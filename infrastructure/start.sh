#!/bin/sh
set -e
cd /app/backend
alembic upgrade head
python -m app.cli bootstrap-admin
# Crea/sincroniza los perfiles PickearYa y FichaYa (idempotente; no toca suites, entornos ni historial).
# QA_SEED_PROFILES=false lo desactiva.
if [ "${QA_SEED_PROFILES:-true}" = "true" ]; then python -m app.cli seed-profiles || true; fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips "*"
