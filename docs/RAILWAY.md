# Despliegue en Railway

Un único servicio (imagen del `Dockerfile` raíz: API FastAPI + frontend compilado) y un plugin de base de datos (PostgreSQL recomendado en Railway, o MySQL).

## Pasos
1. Railway → **New Project → Deploy from GitHub repo** → `horamda/auditoria`. Detecta `railway.json` y construye con el `Dockerfile`.
2. En el mismo proyecto: **+ New → Database → PostgreSQL** (o MySQL).
3. En el servicio de la app → **Variables**:

| Variable | Valor |
|---|---|
| `QA_SECRET_KEY` | cadena larga aleatoria: `python -c "import secrets;print(secrets.token_urlsafe(48))"` (obligatoria) |
| `QA_DATABASE_URL` | PostgreSQL: `${{Postgres.DATABASE_URL}}` (se normaliza a `postgresql+psycopg://`). MySQL: `${{MySQL.MYSQL_URL}}` (a `mysql+pymysql://`) |
| `QA_ADMIN_EMAIL` / `QA_ADMIN_PASSWORD` | primer administrador (mín. 10 caracteres). Solo se crea si no hay usuarios; luego se puede borrar la variable de contraseña |
| `QA_CORS_ORIGINS` | no hace falta: frontend y API comparten origen |
| `QA_FICHAYA_STG_USER` etc. | credenciales de cuentas de PRUEBA, solo si se definen escenarios |

4. (Recomendado) **Volume** montado en `/data` y variable `RAILWAY_RUN_UID=0` (los volúmenes montan como root) para que capturas e informes sobrevivan a los redeploys. Sin volumen funciona, pero se pierden al redeployar.
5. **Settings → Networking → Generate Domain** y abrir la URL. Iniciar sesión con el admin del paso 3.
6. El healthcheck es `/api/health`.

## Qué funciona en Railway
API, login/roles, proyectos y entornos, pruebas API, web (Playwright/Chromium con `--no-sandbox` dentro del contenedor), carga (Locust), historial, comparación e informes.

## Qué NO funciona (y por qué)
- Escaneos de seguridad (Semgrep/OSV-Scanner): usan Docker, no disponible dentro del contenedor.
- Android (adb): necesita un emulador/dispositivo local.
- Descubrimiento de código: requiere repos en disco; en Railway no existen. Ejecutarlo desde un QA Control Center local.

## Seguridad
- Es una URL pública: la protegen solo el login, los roles y un límite de intentos en memoria. Usar contraseña larga; no cargar credenciales de producción de otras apps.
- `QA_ENV=production` exige `QA_SECRET_KEY` (la app no arranca sin ella).
- Las ejecuciones en curso se pierden si el servicio se reinicia (ejecutor en proceso).
- Con varias réplicas el kill switch persiste (base), pero el límite de login no se comparte: mantener 1 réplica.

## Base de datos
Suite completa (42 pruebas) y migraciones Alembic validadas sobre PostgreSQL 16 y MySQL 8.4 (esta última antes de la migración de triage; sobre PostgreSQL ya está con todas). Usar una base exclusiva de QA Control Center.
