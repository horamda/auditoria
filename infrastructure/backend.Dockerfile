FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && useradd -m qacc && mkdir -p /data/artifacts && chown qacc /data/artifacts
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY . .
USER qacc
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
