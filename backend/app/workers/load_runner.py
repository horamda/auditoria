"""Ejecutor de concurrencia/carga con Locust (headless, subproceso aislado).

Definición del caso (suite kind=load):
{
  "users": 10, "spawn_rate": 2, "duration_s": 60,
  "wait_min_s": 1, "wait_max_s": 3,
  "requests": [{"method": "GET", "path": "/api/health", "weight": 3, "name": "health"}],
  "abort_on_error_rate": 0.2,          # criterio de interrupción
  "thresholds": {"p95_ms": 800, "error_rate": 0.01}
}
Los perfiles (10..1000 usuarios) son configuraciones, no garantías de capacidad.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from app.core.config import get_settings
from app.core.guard import PolicyViolation, check_load, check_method, check_url
from app.workers.base import Cancelled, RunContext

LOAD_PROFILES = {f"{n} usuarios": {"users": n, "spawn_rate": max(1, n // 10)} for n in (10, 50, 100, 250, 500, 1000)}

LOCUSTFILE = textwrap.dedent('''
    import json, os, random
    from locust import HttpUser, task, between, events

    CFG = json.loads(os.environ["QA_LOAD_CFG"])
    REQS = CFG["requests"]
    WEIGHTS = [r.get("weight", 1) for r in REQS]
    ABORT = CFG.get("abort_on_error_rate")

    class QAUser(HttpUser):
        host = CFG["host"]
        wait_time = between(CFG.get("wait_min_s", 1), CFG.get("wait_max_s", 3))

        @task
        def op(self):
            r = random.choices(REQS, WEIGHTS)[0]
            # allow_redirects=False: las redirecciones no se siguen hacia destinos no validados
            self.client.request(r.get("method", "GET"), r["path"], name=r.get("name") or r["path"],
                                allow_redirects=False, timeout=CFG.get("timeout_s", 15))

    @events.init.add_listener
    def _(environment, **kw):
        if not ABORT:
            return
        import gevent
        def watchdog():
            while True:
                gevent.sleep(2)
                st = environment.runner.stats.total if environment.runner else None
                if st and st.num_requests >= 20 and st.num_failures / st.num_requests > ABORT:
                    print(f"QA_ABORT error_rate={st.num_failures / st.num_requests:.3f}", flush=True)
                    environment.runner.quit()
                    return
        gevent.spawn(watchdog)
''')


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def run_load_case(definition: dict, ctx: RunContext) -> dict:
    host = (ctx.api_url or ctx.base_url or "").rstrip("/")
    users = int(definition.get("users", 10))
    duration = int(definition.get("duration_s", 30))
    try:
        if not host:
            raise PolicyViolation("El entorno no tiene URL configurada")
        check_load(users, duration, definition.get("target_rps"), ctx.policy)
        check_url(host, ctx.policy)
        reqs = definition.get("requests") or []
        if not reqs:
            raise PolicyViolation("El escenario de carga no define requests")
        for r in reqs:
            check_method(r.get("method", "GET"), ctx.policy, bool(definition.get("approved_write")))
            check_url(host + "/" + r["path"].lstrip("/"), ctx.policy, resolve=False)
    except PolicyViolation as exc:
        return {"status": "blocked", "error": str(exc)}

    out_dir = Path(get_settings().artifacts_dir) / f"run_{ctx.run_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    lf = out_dir / "qa_locustfile.py"
    lf.write_text(LOCUSTFILE, encoding="utf-8")
    prefix = out_dir / "locust"
    cfg = {**definition, "host": host}
    env = {**os.environ, "QA_LOAD_CFG": json.dumps(cfg)}
    cmd = [sys.executable, "-m", "locust", "-f", str(lf), "--headless", "-u", str(users),
           "-r", str(definition.get("spawn_rate", max(1, users // 10))), "-t", f"{duration}s",
           "--csv", str(prefix), "--only-summary", "--stop-timeout", "5"]
    ctx.log(f"Locust: {users} usuarios, {duration}s contra {host}")
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=out_dir)
    hard_limit = duration + 60
    try:
        while proc.poll() is None:
            try:
                ctx.checkpoint()
            except Cancelled:
                proc.kill()
                raise
            if time.perf_counter() - t0 > hard_limit:
                proc.kill()
                return {"status": "error", "error": "Locust excedió el límite duro de tiempo y fue detenido"}
            time.sleep(0.5)
    finally:
        output = proc.stdout.read() if proc.stdout else ""
    (out_dir / "locust_output.log").write_text(output, encoding="utf-8")
    aborted = "QA_ABORT" in output

    stats_file = Path(f"{prefix}_stats.csv")
    if not stats_file.exists():
        return {"status": "error", "error": "Locust no produjo estadísticas", "details": {"output_tail": output[-2000:]}}
    metrics = []
    with stats_file.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            n = int(_f(row.get("Request Count")))
            metrics.append({
                "name": row.get("Name") if row.get("Name") != "Aggregated" else "Aggregated",
                "requests": n, "failures": int(_f(row.get("Failure Count"))),
                "avg_ms": _f(row.get("Average Response Time")), "p50_ms": _f(row.get("50%")),
                "p95_ms": _f(row.get("95%")), "p99_ms": _f(row.get("99%")),
                "rps": _f(row.get("Requests/s")), "failures_per_s": _f(row.get("Failures/s")),
            })
    agg = next((m for m in metrics if m["name"] == "Aggregated"), None) or {"requests": 0, "failures": 0, "p95_ms": 0}
    err_rate = agg["failures"] / agg["requests"] if agg["requests"] else 1.0
    th = definition.get("thresholds", {})
    errors = []
    if agg["requests"] == 0:
        errors.append("No se completó ninguna solicitud")
    if aborted:
        errors.append("Interrumpido por criterio de tasa de error")
    if "error_rate" in th and err_rate > th["error_rate"]:
        errors.append(f"Tasa de error {err_rate:.2%} > {th['error_rate']:.2%}")
    if "p95_ms" in th and agg["p95_ms"] > th["p95_ms"]:
        errors.append(f"p95 {agg['p95_ms']:.0f} ms > {th['p95_ms']} ms")
    return {
        "status": "failed" if errors else "passed",
        "error": "; ".join(errors),
        "duration_ms": (time.perf_counter() - t0) * 1000,
        "details": {"users": users, "duration_s": duration, "error_rate": round(err_rate, 4), "aborted": aborted},
        "metrics": metrics,
        "artifacts": [{"kind": "csv", "path": f"run_{ctx.run_id}/locust_stats.csv"},
                      {"kind": "log", "path": f"run_{ctx.run_id}/locust_output.log"}],
    }
