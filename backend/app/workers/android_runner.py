"""Ejecutor de pruebas Android vía adb.

Definición del caso (suite kind=android):
{
  "apk_path": "C:/.../app-debug.apk",     # se copia a artifacts; el original no se toca
  "package": "com.ejemplo.app", "activity": ".MainActivity",
  "device_kind": "emulator" | "physical",  # por defecto emulator; physical exige allow_physical
  "serial": "emulator-5554",               # opcional
  "grant_permissions": ["android.permission.RECORD_AUDIO"],
  "checks": ["launch", "no_crash", "memory", "cpu", "rotation", "font_scale", "recovery"],
  "settle_s": 8, "max_pss_mb": 600
}
El tipo de dispositivo (emulador/físico) se detecta por propiedades del sistema y queda en los resultados.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from app.core.config import get_settings
from app.workers.base import RunContext


class AndroidError(Exception):
    pass


def adb_path() -> str:
    cands = [shutil.which("adb"),
             os.path.join(os.environ.get("ANDROID_HOME", ""), "platform-tools", "adb.exe"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    raise AndroidError("adb no encontrado (definir ANDROID_HOME o agregar platform-tools al PATH)")


def adb(serial: str, *args: str, timeout: int = 60, check: bool = True) -> str:
    r = subprocess.run([adb_path(), "-s", serial, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if check and r.returncode != 0:
        raise AndroidError(f"adb {' '.join(args[:3])} falló: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def classify(serial: str) -> str:
    """emulator | physical, según propiedades del sistema (no solo el nombre del serial)."""
    qemu = adb(serial, "shell", "getprop", "ro.kernel.qemu", check=False).strip()
    boot_qemu = adb(serial, "shell", "getprop", "ro.boot.qemu", check=False).strip()
    return "emulator" if serial.startswith("emulator-") or qemu == "1" or boot_qemu == "1" else "physical"


def list_devices() -> list[dict]:
    out = subprocess.run([adb_path(), "devices"], capture_output=True, text=True, timeout=30).stdout
    devs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            devs.append({"serial": parts[0], "kind": classify(parts[0])})
    return devs


def device_info(serial: str) -> dict:
    def g(prop: str) -> str:
        return adb(serial, "shell", "getprop", prop, check=False).strip()
    return {"serial": serial, "kind": classify(serial), "model": g("ro.product.model"),
            "android": g("ro.build.version.release"), "sdk": g("ro.build.version.sdk")}


# ---------------- métricas ----------------
def pss_mb(serial: str, package: str) -> float | None:
    out = adb(serial, "shell", "dumpsys", "meminfo", package, check=False)
    m = re.search(r"TOTAL PSS:\s*([\d,]+)", out) or re.search(r"^\s*TOTAL\s+([\d,]+)", out, re.M)
    return round(int(m.group(1).replace(",", "")) / 1024, 1) if m else None


def cpu_pct(serial: str, package: str) -> float | None:
    """Instantánea de CPU (una muestra de `top`): orientativa, no un promedio."""
    out = adb(serial, "shell", "top", "-b", "-n", "1", check=False)
    for line in out.splitlines():
        if package in line:
            for col in line.split():
                if "." in col:
                    try:
                        v = float(col)
                    except ValueError:
                        continue
                    if 0 <= v <= 800:
                        return v
    return None


def screen_is_blank(serial: str) -> bool:
    """True si el framebuffer es de un solo color (p. ej. todo negro). Muestrea píxeles sin dependencias."""
    r = subprocess.run([adb_path(), "-s", serial, "exec-out", "screencap"], capture_output=True, timeout=60)
    raw = r.stdout
    if r.returncode != 0 or len(raw) < 64:
        return False  # no se pudo capturar: no se afirma nada
    w = int.from_bytes(raw[0:4], "little")
    h = int.from_bytes(raw[4:8], "little")
    header = 16 if len(raw) >= 16 + w * h * 4 else 12  # Android 9+ agrega el campo de espacio de color
    px = raw[header:]
    if not w or not h or len(px) < w * h * 4:
        return False
    step = max(4, (w * h // 4000)) * 4
    first = px[0:3]
    return all(px[i:i + 3] == first for i in range(0, len(px) - 3, step))


def pid_of(serial: str, package: str) -> str:
    return adb(serial, "shell", "pidof", package, check=False).strip()


CRASH = re.compile(r"(FATAL EXCEPTION|ANR in |Process .* has died|signal \d+ \(SIG)")


def crash_lines(serial: str, package: str) -> list[str]:
    out = adb(serial, "logcat", "-d", "-b", "crash,main", "-v", "brief", check=False, timeout=60)
    hits = [ln.strip()[:200] for ln in out.splitlines()
            if CRASH.search(ln) and (package in ln or "FATAL" in ln or "ANR" in ln)]
    return hits[:20]


def _result(status: str, error: str = "", **details) -> dict:
    return {"status": status, "error": error, "details": details}


def _component(pkg: str, act: str) -> str:
    return f"{pkg}/{act if act.startswith('.') or '/' in act else '.' + act}"


def run_android_case(definition: dict, ctx: RunContext, case_slug: str) -> dict:
    t0 = time.perf_counter()
    try:
        devs = list_devices()
    except (AndroidError, subprocess.SubprocessError) as exc:
        return _result("skipped", f"adb no disponible: {exc}")
    want = definition.get("device_kind", "emulator")
    wanted_serial = definition.get("serial")
    cand = [d for d in devs if (not wanted_serial or d["serial"] == wanted_serial) and d["kind"] == want]
    if not cand:
        found = ", ".join(f"{d['serial']}({d['kind']})" for d in devs) or "ninguno"
        return _result("skipped", f"No hay dispositivo '{want}' disponible (detectados: {found})")
    if want == "physical" and not definition.get("allow_physical"):
        return _result("blocked", "Dispositivo físico: el caso debe declarar allow_physical=true")
    serial = cand[0]["serial"]
    info = device_info(serial)
    pkg, act = definition.get("package", ""), definition.get("activity", ".MainActivity")
    if not pkg:
        return _result("error", "El caso no define 'package'", device=info)
    checks = definition.get("checks", ["launch", "no_crash", "memory", "cpu", "rotation", "font_scale", "recovery"])
    settle, max_pss = definition.get("settle_s", 8), definition.get("max_pss_mb", 600)
    errors: list[str] = []
    measured: dict = {"device": info}
    artifacts: list[dict] = []
    art_dir = Path(get_settings().artifacts_dir) / f"run_{ctx.run_id}"
    art_dir.mkdir(parents=True, exist_ok=True)

    def shot(name: str) -> None:
        fn = f"{case_slug}_{name}_{int(time.time() * 1000)}.png"
        r = subprocess.run([adb_path(), "-s", serial, "exec-out", "screencap", "-p"], capture_output=True, timeout=60)
        if r.returncode == 0 and r.stdout[:4] == b"\x89PNG":
            (art_dir / fn).write_bytes(r.stdout)
            artifacts.append({"kind": "screenshot", "path": f"run_{ctx.run_id}/{fn}"})

    try:
        apk = definition.get("apk_path")
        if apk:
            src = Path(apk)
            if not src.is_file():
                return _result("error", f"APK inexistente: {apk}", **measured)
            dst = art_dir / f"{case_slug}.apk"
            shutil.copy2(src, dst)  # se instala la copia; el original no se toca
            ctx.log(f"Instalando {dst.name} en {serial}")
            adb(serial, "install", "-r", "-t", str(dst), timeout=240)
        for perm in definition.get("grant_permissions", []):
            adb(serial, "shell", "pm", "grant", pkg, perm, check=False)
        adb(serial, "logcat", "-c", check=False)
        ctx.checkpoint()

        if "launch" in checks:
            adb(serial, "shell", "am", "force-stop", pkg, check=False)
            ts = time.perf_counter()
            out = adb(serial, "shell", "am", "start", "-W", "-n", _component(pkg, act))
            wait = re.search(r"TotalTime:\s*(\d+)", out)
            measured["startup_ms"] = int(wait.group(1)) if wait else round((time.perf_counter() - ts) * 1000)
            if "Error" in out:
                errors.append(f"No se pudo iniciar la actividad: {out.strip()[:200]}")
            time.sleep(settle)
            ctx.checkpoint()
            if not pid_of(serial, pkg):
                errors.append("La aplicación no está en ejecución tras iniciar")
            if screen_is_blank(serial):
                errors.append("Pantalla en negro/uniforme tras iniciar (posible falla de render o emulador degradado)")
            shot("inicio")

        if "memory" in checks and pid_of(serial, pkg):
            measured["pss_mb"] = pss_mb(serial, pkg)
            if measured["pss_mb"] and measured["pss_mb"] > max_pss:
                errors.append(f"Memoria {measured['pss_mb']} MB supera el máximo {max_pss} MB")
        if "cpu" in checks and pid_of(serial, pkg):
            measured["cpu_pct_snapshot"] = cpu_pct(serial, pkg)

        if "rotation" in checks:
            adb(serial, "shell", "settings", "put", "system", "accelerometer_rotation", "0", check=False)
            for rot, label in ((1, "horizontal"), (0, "vertical")):
                adb(serial, "shell", "settings", "put", "system", "user_rotation", str(rot), check=False)
                time.sleep(2)
                ctx.checkpoint()
                if not pid_of(serial, pkg):
                    errors.append(f"La aplicación murió al rotar a {label}")
                    break
                if screen_is_blank(serial):
                    errors.append(f"Pantalla en negro/uniforme tras rotar a {label} (app o emulador sin repintar)")
                if rot == 1:
                    shot("horizontal")
            adb(serial, "shell", "settings", "put", "system", "accelerometer_rotation", "1", check=False)

        if "font_scale" in checks:
            orig = adb(serial, "shell", "settings", "get", "system", "font_scale", check=False).strip()
            try:
                adb(serial, "shell", "settings", "put", "system", "font_scale", "1.5", check=False)
                time.sleep(3)
                if not pid_of(serial, pkg):
                    errors.append("La aplicación murió con escala de texto 1.5")
                shot("texto_grande")
            finally:
                restore = orig if orig not in ("", "null") else "1.0"
                adb(serial, "shell", "settings", "put", "system", "font_scale", restore, check=False)

        if "recovery" in checks:
            adb(serial, "shell", "am", "force-stop", pkg, check=False)
            adb(serial, "shell", "am", "start", "-n", _component(pkg, act), check=False)
            time.sleep(settle / 2)
            if not pid_of(serial, pkg):
                errors.append("La aplicación no se recuperó tras cerrarla y volver a abrirla")

        if "no_crash" in checks:
            crashes = crash_lines(serial, pkg)
            measured["crash_log"] = crashes
            if crashes:
                errors.append(f"{len(crashes)} cierres inesperados/ANR en logcat")
        log = adb(serial, "logcat", "-d", "-b", "main,crash", "-v", "brief", check=False, timeout=60)
        log_name = f"{case_slug}_logcat.txt"
        (art_dir / log_name).write_text(log[-200_000:], encoding="utf-8")
        artifacts.append({"kind": "log", "path": f"run_{ctx.run_id}/{log_name}"})
    except AndroidError as exc:
        return {"status": "error", "error": str(exc), "duration_ms": (time.perf_counter() - t0) * 1000,
                "details": measured, "artifacts": artifacts}
    except subprocess.TimeoutExpired as exc:
        return {"status": "error", "error": f"Timeout ejecutando adb: {exc.cmd[-3:]}",
                "duration_ms": (time.perf_counter() - t0) * 1000, "details": measured, "artifacts": artifacts}

    return {"status": "failed" if errors else "passed", "error": "; ".join(errors),
            "duration_ms": (time.perf_counter() - t0) * 1000, "details": measured, "artifacts": artifacts}
