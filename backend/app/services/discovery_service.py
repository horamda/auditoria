from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.guard import PolicyViolation
from app.models import DiscoveredFeature, Project, utcnow
from app.workers.discovery import discover


def resolve_project_root(project: Project) -> Path:
    """Solo se analizan rutas dentro de QA_ALLOWED_REPO_ROOTS o clones propios de la plataforma."""
    s = get_settings()
    if project.local_path:
        root = Path(project.local_path).resolve()
        if not any(root == r or r in root.parents for r in s.repo_roots):
            raise PolicyViolation(f"Ruta no autorizada para análisis: {root}. Agregarla a QA_ALLOWED_REPO_ROOTS.")
        if not root.is_dir():
            raise PolicyViolation(f"La ruta no existe: {root}")
        return root
    if project.repo_url:
        if not project.repo_url.startswith("https://github.com/"):
            raise PolicyViolation("Solo se admiten repositorios https://github.com/")
        dest = Path(s.artifacts_dir) / "repos" / f"project_{project.id}"
        if dest.exists():
            subprocess.run(["git", "-C", str(dest), "pull", "--ff-only", "-q"], check=True, timeout=300)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Clon superficial, de solo lectura para la plataforma. Token (si hace falta) vía credential helper de git, nunca en la URL.
            subprocess.run(["git", "clone", "--depth", "1", "-q", project.repo_url, str(dest)], check=True, timeout=600)
        return dest
    raise PolicyViolation("El proyecto no tiene ruta local ni repositorio configurado")


def run_discovery(db: Session, project: Project) -> dict:
    root = resolve_project_root(project)
    subpaths = sorted({c.path for c in project.components if c.path})
    try:
        feats = discover(root, subpaths)
    except ValueError as exc:
        raise PolicyViolation(str(exc)) from exc
    existing = {f.stable_id: f for f in db.scalars(select(DiscoveredFeature).where(DiscoveredFeature.project_id == project.id))}
    now, new, seen = utcnow(), 0, set()
    for f in feats:
        seen.add(f.stable_id)
        row = existing.get(f.stable_id)
        if row:
            row.location, row.details, row.last_seen, row.present = f.location, f.details, now, True
        else:
            new += 1
            db.add(DiscoveredFeature(project_id=project.id, stable_id=f.stable_id, layer=f.layer, kind=f.kind,
                                     name=f.name[:300], location=f.location[:500], details=f.details))
    removed = 0
    for sid, row in existing.items():
        if sid not in seen and row.present:
            row.present, removed = False, removed + 1
    db.commit()
    by_kind: dict[str, int] = {}
    for f in feats:
        k = f"{f.layer}.{f.kind}"
        by_kind[k] = by_kind.get(k, 0) + 1
    return {"root": str(root), "scanned": subpaths or ["."], "total": len(feats), "new": new, "no_longer_present": removed, "by_kind": dict(sorted(by_kind.items()))}
