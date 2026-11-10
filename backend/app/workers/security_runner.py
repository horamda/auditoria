"""Análisis de seguridad estático (SOLO LECTURA sobre el código analizado).

- Semgrep y OSV-Scanner corren en contenedores Docker con el repositorio montado `:ro`,
  límites de memoria/CPU, nombre propio (para poder matarlos) y timeout.
- pip-audit corre con `--disable-pip --no-deps`: no instala ni ejecuta paquetes; solo
  audita dependencias con versión fijada (`==`). Las no fijadas se informan como limitación.
- Los fragmentos de código (`lines`) se descartan para no copiar secretos a las evidencias.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

SEMGREP_IMAGE = "semgrep/semgrep:1.95.0"
OSV_IMAGE = "ghcr.io/google/osv-scanner:v1.9.1"
DEFAULT_SEMGREP_CONFIGS = ["p/security-audit", "p/secrets", "p/owasp-top-ten"]
ALL_TOOLS = ("semgrep", "osv-scanner", "pip-audit")
SEV_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class ToolUnavailable(Exception):
    pass


def fingerprint(*parts: str | None) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode()).hexdigest()[:20]


def _cvss_to_sev(score: str | float | None) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "medium"
    return "critical" if s >= 9 else "high" if s >= 7 else "medium" if s >= 4 else "low"


def _run(cmd: list[str], timeout_s: int, checkpoint: Callable[[], None], container: str | None = None) -> tuple[int, str, str]:
    """Ejecuta con cancelación cooperativa; mata el contenedor si corresponde."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    t0 = time.monotonic()
    try:
        while True:
            try:
                out, err = proc.communicate(timeout=1)
                return proc.returncode, out, err
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() - t0 > timeout_s:
                raise TimeoutError(f"Timeout de {timeout_s}s")
            checkpoint()
    except BaseException:
        if container:
            subprocess.run(["docker", "kill", container], capture_output=True, timeout=30)
        proc.kill()
        proc.communicate()
        raise


def _docker_cmd(image: str, mount: Path, args: list[str], name: str) -> list[str]:
    if not shutil.which("docker"):
        raise ToolUnavailable("Docker no está disponible en el worker")
    return ["docker", "run", "--rm", "--name", name, "--memory", "2g", "--cpus", "2", "--pids-limit", "512",
            "--security-opt", "no-new-privileges", "-v", f"{mount.as_posix()}:/src:ro", image, *args]


def _rel(path: str, scan_rel: str) -> str:
    p = path.removeprefix("/src/").removeprefix("/src")
    return f"{scan_rel}/{p}".strip("/") if scan_rel not in ("", ".") else p


# ---------------- Semgrep ----------------
def run_semgrep(scan: Path, scan_rel: str, timeout_s: int, checkpoint, configs: list[str] | None = None) -> list[dict]:
    args = ["semgrep", "scan", "--metrics=off", "--json", "--quiet", "--timeout", "30",
            "--exclude", "node_modules", "--exclude", "build", "--exclude", ".dart_tool", "--exclude", "*.min.js"]
    for c in configs or DEFAULT_SEMGREP_CONFIGS:
        args += ["--config", c]
    name = f"qacc-semgrep-{uuid.uuid4().hex[:8]}"
    code, out, err = _run(_docker_cmd(SEMGREP_IMAGE, scan, [*args, "/src"], name), timeout_s, checkpoint, name)
    if code not in (0, 1) or not out.strip():
        raise RuntimeError(f"Semgrep terminó con código {code}: {err[-500:]}")
    data = json.loads(out)
    sev = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    findings = []
    for r in data.get("results", []):
        meta = r.get("extra", {}).get("metadata", {})
        file = _rel(r["path"], scan_rel)
        cwe = meta.get("cwe")
        findings.append({
            "tool": "semgrep", "rule_id": r["check_id"], "category": "codigo",
            "severity": sev.get(r.get("extra", {}).get("severity", ""), "medium"),
            "title": r["check_id"].rsplit(".", 1)[-1].replace("-", " "),
            "message": r.get("extra", {}).get("message", "")[:2000],
            "file": file, "line": r.get("start", {}).get("line"),
            "cwe": (cwe[0] if isinstance(cwe, list) and cwe else cwe or "")[:200],
            "confidence": meta.get("confidence", ""),
            # sin 'lines': el fragmento podría contener un secreto
        })
    # Huella estable ante desplazamientos de línea: regla + archivo + n.º de ocurrencia en ese archivo
    seen: dict[tuple[str, str], int] = {}
    for f in sorted(findings, key=lambda x: (x["rule_id"], x["file"], x["line"] or 0)):
        k = (f["rule_id"], f["file"])
        seen[k] = seen.get(k, 0) + 1
        f["fingerprint"] = fingerprint("semgrep", f["rule_id"], f["file"], str(seen[k]))
    return findings


# ---------------- OSV-Scanner ----------------
def run_osv(scan: Path, scan_rel: str, timeout_s: int, checkpoint) -> list[dict]:
    name = f"qacc-osv-{uuid.uuid4().hex[:8]}"
    args = ["--format", "json", "-r", "/src"]
    code, out, err = _run(_docker_cmd(OSV_IMAGE, scan, args, name), timeout_s, checkpoint, name)
    if code not in (0, 1) or not out.strip():
        if "No package sources found" in err:
            return []
        raise RuntimeError(f"OSV-Scanner terminó con código {code}: {err[-500:]}")
    data = json.loads(out)
    findings = []
    for res in data.get("results", []):
        src = _rel(res["source"]["path"], scan_rel)
        for pkg in res.get("packages", []):
            p = pkg["package"]
            vulns = {v["id"]: v for v in pkg.get("vulnerabilities", [])}
            for g in pkg.get("groups", []) or [{"ids": [i]} for i in vulns]:
                ids = g.get("ids", [])
                v = vulns.get(ids[0], {}) if ids else {}
                fixed = sorted({e["fixed"] for a in v.get("affected", []) if a.get("package", {}).get("name", "").lower() == p["name"].lower()
                                for rg in a.get("ranges", []) for e in rg.get("events", []) if "fixed" in e})
                aliases = sorted(set(ids) | set(g.get("aliases", [])))
                findings.append({
                    "tool": "osv-scanner", "rule_id": ids[0] if ids else "", "category": "dependencia",
                    "severity": _cvss_to_sev(g.get("max_severity")),
                    "title": f"{p['name']} {p.get('version', '')}: {v.get('summary') or (ids[0] if ids else 'vulnerabilidad')}"[:300],
                    "message": (v.get("details") or "")[:2000],
                    "file": src, "line": None, "package": p["name"], "installed_version": p.get("version", ""),
                    "fixed_version": ", ".join(fixed[:3]), "ecosystem": p.get("ecosystem", ""), "vuln_ids": aliases[:20],
                    "fingerprint": fingerprint("osv", p["ecosystem"] if "ecosystem" in p else "", p["name"], p.get("version"), ids[0] if ids else ""),
                })
    return findings


# ---------------- pip-audit ----------------
PINNED = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)\s*==\s*([A-Za-z0-9_.+\-]+)\s*(;.*)?$")
SKIP_REQ_DIRS = {"node_modules", ".venv", "venv", "build", ".git"}


def run_pip_audit(scan: Path, base: Path, workdir: Path, timeout_s: int, checkpoint) -> tuple[list[dict], dict]:
    req_files = [p for p in scan.rglob("requirements*.txt") if not (set(p.relative_to(scan).parts) & SKIP_REQ_DIRS)]
    findings, stats = [], {"files": 0, "pinned": 0, "unpinned_skipped": 0}
    workdir.mkdir(parents=True, exist_ok=True)
    for rf in req_files:
        rel = rf.relative_to(base).as_posix()
        pinned, unpinned = [], 0
        for line in rf.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.split("#", 1)[0].strip()
            if not s or s.startswith(("-", "git+", "http")):
                continue
            m = PINNED.match(s)
            if m:
                pinned.append(f"{m.group(1)}=={m.group(2)}")
            else:
                unpinned += 1
        stats["files"] += 1
        stats["pinned"] += len(pinned)
        stats["unpinned_skipped"] += unpinned
        if not pinned:
            continue
        tmp = workdir / f"pinned_{fingerprint(rel)}.txt"  # copia fuera del repositorio analizado
        tmp.write_text("\n".join(pinned) + "\n", encoding="utf-8")
        cmd = [sys.executable, "-m", "pip_audit", "-r", str(tmp), "--disable-pip", "--no-deps", "-f", "json", "--progress-spinner", "off"]
        code, out, err = _run(cmd, timeout_s, checkpoint)
        if not out.strip():
            raise RuntimeError(f"pip-audit falló en {rel}: {err[-400:]}")
        for dep in json.loads(out).get("dependencies", []):
            for v in dep.get("vulns", []):
                findings.append({
                    "tool": "pip-audit", "rule_id": v["id"], "category": "dependencia", "severity": "medium",
                    "title": f"{dep['name']} {dep.get('version', '')}: {v['id']}",
                    "message": (v.get("description") or "")[:2000], "file": rel, "line": None,
                    "package": dep["name"], "installed_version": dep.get("version", ""),
                    "fixed_version": ", ".join(v.get("fix_versions", [])[:3]), "ecosystem": "PyPI",
                    "vuln_ids": sorted({v["id"], *v.get("aliases", [])})[:20],
                    "fingerprint": fingerprint("osv", "PyPI", dep["name"], dep.get("version"), v["id"]),
                })
    return findings, stats


def merge_dependency_duplicates(findings: list[dict]) -> list[dict]:
    """OSV y pip-audit pueden informar la misma vulnerabilidad: se unifican por alias compartido."""
    out: list[dict] = []
    index: dict[tuple[str, str, str], dict] = {}
    for f in findings:
        if f["category"] != "dependencia":
            out.append(f)
            continue
        keys = [(f["package"].lower(), f["installed_version"], a) for a in f.get("vuln_ids", [])]
        hit = next((index[k] for k in keys if k in index), None)
        if hit:
            hit["tools"] = sorted(set(hit.get("tools", [hit["tool"]])) | {f["tool"]})
            if SEV_ORDER[f["severity"]] > SEV_ORDER[hit["severity"]]:
                hit["severity"] = f["severity"]
            hit["fixed_version"] = hit["fixed_version"] or f["fixed_version"]
            continue
        f["tools"] = [f["tool"]]
        out.append(f)
        for k in keys:
            index[k] = f
    return out
