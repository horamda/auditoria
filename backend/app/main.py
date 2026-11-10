from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
