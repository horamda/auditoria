"""Servicio de prueba (sistema bajo prueba sintético). Nunca apunta a sistemas reales."""
from __future__ import annotations

import threading

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

app = FastAPI()
STATE = {"items": [], "broken": False, "writes": 0}
_lock = threading.Lock()

LOGIN = """<!doctype html><html><body>
<h1>Panel Demo</h1>
<form id="f" onsubmit="event.preventDefault(); if(document.querySelector('#user').value==='qa'){location.href='/panel'}else{document.querySelector('#msg').textContent='Error de login'}">
<input id="user" data-testid="user"><input id="pass" type="password"><button type="submit">Ingresar</button></form>
<div id="msg"></div></body></html>"""

PANEL = """<!doctype html><html><body><h1>Bienvenido al panel</h1>
<input id="buscar" placeholder="Buscar"><button id="eliminar">Eliminar todo</button>
<table><tr><td>Fila 1</td></tr></table></body></html>"""


@app.get("/", response_class=HTMLResponse)
def login_page():
    return LOGIN


@app.get("/panel", response_class=HTMLResponse)
def panel():
    return PANEL


@app.get("/api/health")
def health():
    if STATE["broken"]:
        return {"status": "degraded"}
    return {"status": "ok", "version": "1.0"}


@app.get("/api/items")
def items():
    return {"items": STATE["items"]}


@app.post("/api/items", status_code=201)
async def create_item(req: Request):
    body = await req.json()
    with _lock:
        STATE["writes"] += 1
        STATE["items"].append(body)
    return body


@app.get("/api/redirect-out")
def redirect_out():
    return RedirectResponse("http://169.254.169.254/latest/meta-data/")
