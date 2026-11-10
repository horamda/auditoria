from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, projects, runs
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="QA Control Center",
    version="0.1.0",
    description="Plataforma de análisis, pruebas y monitoreo de aplicaciones Flutter, paneles web, APIs y bases de datos.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(runs.router)


@app.get("/api/health", tags=["meta"])
def health():
    return {"status": "ok"}


# Frontend compilado servido por la misma API (un solo servicio, mismo origen: sin CORS).
_dist = Path(settings.frontend_dist).resolve() if settings.frontend_dist else None
if _dist and (_dist / "index.html").is_file():
    if (_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        target = (_dist / path).resolve()
        if path and target.is_file() and _dist in target.parents:
            return FileResponse(target)
        return FileResponse(_dist / "index.html")
