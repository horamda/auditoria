"""CLI de operación.

  python -m app.cli seed-roles
  python -m app.cli create-admin --email admin@empresa.com   (contraseña desde QA_ADMIN_PASSWORD o prompt)
  python -m app.cli seed-profiles                            (crea o sincroniza perfiles; sin URLs ni credenciales)
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import Project, ProjectComponent, Role, User

ROLES = {"admin": "Administración total", "qa": "Crear y ejecutar pruebas", "viewer": "Solo lectura"}

PROFILES = [
    {"name": "PickearYa", "description": "Picking por voz, control de cargas (1er y 2do control), canchas, faltantes, "
     "autorizaciones, activos retornables y liberación de camiones. Módulos a confirmar mediante descubrimiento.",
     "app_type": "mixed", "framework": "Flask + React + Android nativo + FastAPI (control de cargas)",
     "language": "Python/JavaScript/Java-Kotlin",
     "components": [("backend", "backend", "backend", "flask"), ("web_admin", "frontend", "frontend", "react"),
                    ("android_native", "android (picking por voz)", "android", "android"),
                    ("module", "control_cargas_mvp", "control_cargas_mvp", "fastapi"),
                    ("module", "voz", "voz", "python"), ("postgresql", "PostgreSQL (Railway)", None, "postgresql")]},
    {"name": "FichaYa", "description": "Fichadas, ingreso/salida, horarios, reuniones QR, permisos, reconocimiento facial "
     "configurable, panel administrativo y banco de horas. Módulos a confirmar mediante descubrimiento.",
     "app_type": "mixed", "framework": "Flask + Flutter", "language": "Python/Dart",
     "components": [("backend", "backend", "backend", "flask"), ("flutter_android", "frontend_flutter", "frontend_flutter", "flutter"),
                    ("mysql", "MySQL", None, "mysql")]},
]


def seed_roles(db) -> None:
    for name, desc in ROLES.items():
        if not db.scalar(select(Role).where(Role.name == name)):
            db.add(Role(name=name, description=desc))
    db.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="qa-cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed-roles")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    sp = sub.add_parser("seed-profiles")
    sp.add_argument("--pickearya-path", default="")
    sp.add_argument("--fichaya-path", default="")
    args = ap.parse_args(argv)

    with SessionLocal() as db:
        seed_roles(db)
        if args.cmd == "create-admin":
            pwd = os.environ.get("QA_ADMIN_PASSWORD") or getpass.getpass("Contraseña: ")
            if len(pwd) < 10:
                print("La contraseña debe tener al menos 10 caracteres", file=sys.stderr)
                return 1
            role = db.scalar(select(Role).where(Role.name == "admin"))
            user = db.scalar(select(User).where(User.email == args.email.lower()))
            if user:
                user.password_hash, user.role_id = hash_password(pwd), role.id
            else:
                db.add(User(email=args.email.lower(), full_name="Administrador", password_hash=hash_password(pwd), role_id=role.id))
            db.commit()
            print(f"Administrador listo: {args.email.lower()}")
        elif args.cmd == "seed-profiles":
            paths = {"PickearYa": args.pickearya_path or None, "FichaYa": args.fichaya_path or None}
            for prof in PROFILES:
                existing = db.scalar(select(Project).where(Project.name == prof["name"]))
                if existing:
                    # Sincroniza el perfil (no toca suites, entornos ni historial)
                    existing.description, existing.framework, existing.language = prof["description"], prof["framework"], prof["language"]
                    if paths[prof["name"]]:
                        existing.local_path = paths[prof["name"]]
                    existing.components = [ProjectComponent(kind=k, name=n, path=pa, framework=f) for k, n, pa, f in prof["components"]]
                    print(f"Perfil actualizado: {prof['name']}")
                    continue
                p = Project(name=prof["name"], description=prof["description"], app_type=prof["app_type"],
                            framework=prof["framework"], language=prof["language"], local_path=paths[prof["name"]],
                            status="en análisis")
                p.components = [ProjectComponent(kind=k, name=n, path=pa, framework=f) for k, n, pa, f in prof["components"]]
                db.add(p)
                print(f"Perfil creado: {prof['name']}")
            db.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
