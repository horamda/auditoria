# Imagen única para Railway: compila el frontend y lo sirve desde la API FastAPI.
# Contexto de build: raíz del repositorio.
FROM node:20-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    QA_ENV=production \
    QA_FRONTEND_DIST=/app/frontend/dist \
    QA_ARTIFACTS_DIR=/data/artifacts \
    QA_REPORTS_DIR=/data/reports \
    QA_CHROMIUM_NO_SANDBOX=true
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && useradd -m qacc && mkdir -p /data/artifacts /data/reports && chown -R qacc /data
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
COPY infrastructure/start.sh /app/start.sh
RUN chmod +x /app/start.sh
USER qacc
EXPOSE 8000
CMD ["/app/start.sh"]
