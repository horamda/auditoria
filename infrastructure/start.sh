#!/bin/sh
set -e
cd /app/backend
alembic upgrade head
python -m app.cli bootstrap-admin
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips "*"
