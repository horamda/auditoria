"""Descubrimiento estático de funcionalidades (SOLO LECTURA).

Nunca escribe en el repositorio analizado. Todo lo encontrado queda con
evidence_level="static": descubierto en código, no observado ni verificado.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "build", ".dart_tool", ".venv", "venv", "__pycache__", ".next", "dist",
    ".idea", ".gradle", "ios", "linux", "macos", "windows", "tmp", "testtmp", "output", "artifacts", ".pytest_cache",
}
MAX_FILE_BYTES = 1_500_000


@dataclass
class Feature:
    layer: str
    kind: str
    name: str
    location: str
    details: dict = field(default_factory=dict)

    @property
    def stable_id(self) -> str:
        # Identificador estable: no depende de la línea, solo de capa+tipo+nombre normalizado
        key = f"{self.layer}|{self.kind}|{re.sub(r'\s+', ' ', self.name.strip().lower())}"
        return hashlib.sha1(key.encode()).hexdigest()[:16]


def _walk(root: Path, exts: tuple[str, ...]) -> Iterator[Path]:
    """Recorre podando directorios excluidos (no entra en node_modules, build, etc.)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(exts):
                p = Path(dirpath, fn)
                try:
                    if p.stat().st_size <= MAX_FILE_BYTES:
                        yield p
                except OSError:
                    continue


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# ---------------- Backend Python ----------------
FLASK_BP = re.compile(r"(\w+)\s*=\s*Blueprint\(\s*['\"][^'\"]+['\"][^)]*?url_prefix\s*=\s*['\"]([^'\"]+)['\"]")
FLASK_ROUTE = re.compile(r"@(\w+)\.route\(\s*['\"]([^'\"]+)['\"](?:[^)]*?methods\s*=\s*\[([^\]]*)\])?", re.S)
FAST_ROUTE = re.compile(r"@(\w+)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]*)['\"]")
FAST_ROUTER = re.compile(r"(\w+)\s*=\s*APIRouter\(([^)]*)\)")
AUTH_HINT = re.compile(r"(jwt_required|login_required|Depends\(\s*(get_current\w*|require\w*|current_user\w*)|roles_required|role_required|admin_required)")
NEXT_ROUTE = re.compile(r"\n@\w+\.(route|get|post|put|patch|delete)\(")
FUNC_DEF =re.compile(r"\n(?:async\s+)?def\s+(\w+)\s*\(")
SQLA_TABLE = re.compile(r"__tablename__\s*=\s*['\"](\w+)['\"]")
FK = re.compile(r"ForeignKey\(\s*['\"](\w+)\.(\w+)['\"]")
SQL_CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?", re.I)
SQL_INDEX = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+`?(\w+)`?\s+ON\s+`?(\w+)`?", re.I)


def discover_python(root: Path, base: Path) -> list[Feature]:
    out: list[Feature] = []
    for p in _walk(root, (".py",)):
        rel = p.relative_to(base).as_posix()
        if "/migrations/" in f"/{rel}" or "/alembic/" in f"/{rel}" or rel.startswith("tests/") or "/tests/" in rel:
            # migraciones se leen como DB; tests como pruebas existentes
            if "test_" in p.name:
                out.append(Feature("backend", "existing_test", rel, rel))
            continue
        text = _read(p)
        prefixes = {m.group(1): m.group(2) for m in FLASK_BP.finditer(text)}
        for m in FAST_ROUTER.finditer(text):
            pm = re.search(r"prefix\s*=\s*['\"]([^'\"]*)['\"]", m.group(2))
            prefixes[m.group(1)] = pm.group(1) if pm else ""
        for m in FLASK_ROUTE.finditer(text):
            methods = [x.strip(" '\"") for x in (m.group(3) or "'GET'").split(",") if x.strip(" '\"")]
            path = prefixes.get(m.group(1), "") + m.group(2)
            out.extend(_endpoint(text, m, rel, path, methods, "flask"))
        for m in FAST_ROUTE.finditer(text):
            path = prefixes.get(m.group(1), "") + m.group(3)
            out.extend(_endpoint(text, m, rel, path or "/", [m.group(2).upper()], "fastapi"))
        for m in SQLA_TABLE.finditer(text):
            fks = sorted({f"{a}.{b}" for a, b in FK.findall(text)})
            out.append(Feature("database", "table", m.group(1), f"{rel}:{_line(text, m.start())}", {"source": "sqlalchemy", "foreign_keys_in_file": fks}))
    return out


def _endpoint(text: str, m: re.Match, rel: str, path: str, methods: list[str], fw: str) -> list[Feature]:
    tail = text[m.end(): m.end() + 1200]
    fn = FUNC_DEF.search("\n" + tail)
    nxt = NEXT_ROUTE.search(tail)
    fn_body = tail[: nxt.start()] if nxt else tail
    auth = bool(AUTH_HINT.search(text[max(0, m.start() - 300): m.end()] + fn_body))
    feats = []
    for meth in methods:
        meth = meth.upper()
        feats.append(Feature(
            "backend", "endpoint", f"{meth} {path}", f"{rel}:{_line(text, m.start())}",
            {"framework": fw, "method": meth, "path": path, "handler": fn.group(1) if fn else None,
             "auth_detected": auth, "operation": "read" if meth in ("GET", "HEAD", "OPTIONS") else "write"},
        ))
    return feats


def discover_sql(root: Path, base: Path) -> list[Feature]:
    out = []
    for p in _walk(root, (".sql",)):
        text, rel = _read(p), p.relative_to(base).as_posix()
        for m in SQL_CREATE.finditer(text):
            out.append(Feature("database", "table", m.group(1), f"{rel}:{_line(text, m.start())}", {"source": "sql"}))
        for m in SQL_INDEX.finditer(text):
            out.append(Feature("database", "index", f"{m.group(2)}.{m.group(1)}", f"{rel}:{_line(text, m.start())}"))
    return out


# ---------------- Flutter ----------------
WIDGET_CLASS = re.compile(r"class\s+(\w+)\s+extends\s+(StatelessWidget|StatefulWidget|ConsumerWidget|ConsumerStatefulWidget|HookWidget)")
NAMED_ROUTE = re.compile(r"['\"](/[\w\-/:]*)['\"]\s*:\s*\(")
GO_ROUTE = re.compile(r"GoRoute\(\s*(?:name\s*:\s*['\"][^'\"]*['\"]\s*,\s*)?path\s*:\s*['\"]([^'\"]+)['\"]")
HTTP_CALL = re.compile(r"\b(?:http|_?dio|client|_client|_http|api|_api)\.(get|post|put|patch|delete)\s*\(\s*(?:Uri\.parse\()?\s*['\"$]?([^,)\n]{0,120})", re.I)
FORM = re.compile(r"\b(Form|TextFormField)\(")
KEY = re.compile(r"Key\(\s*['\"]([^'\"]+)['\"]\s*\)")
STATE_MGMT = {"provider": "Provider", "flutter_riverpod": "Riverpod", "riverpod": "Riverpod", "flutter_bloc": "Bloc", "get": "GetX", "mobx": "MobX"}
PERMISSION = re.compile(r"<uses-permission[^>]*android:name=\"([^\"]+)\"")
PUB_DEP = re.compile(r"^  ([a-z0-9_]+):", re.M)


def discover_flutter(root: Path, base: Path) -> list[Feature]:
    out: list[Feature] = []
    for pub in [p for p in _walk(root, (".yaml",)) if p.name == "pubspec.yaml"]:
        app_root = pub.parent
        rel_app = app_root.relative_to(base).as_posix() or "."
        ptext = _read(pub)
        dep_block = ptext.split("dependencies:", 1)[1].split("dev_dependencies:", 1)[0] if "dependencies:" in ptext else ""
        deps = PUB_DEP.findall(dep_block)
        for d in deps:
            out.append(Feature("flutter", "dependency", f"{rel_app}:{d}", f"{rel_app}/pubspec.yaml"))
        for d in deps:
            if d in STATE_MGMT:
                out.append(Feature("flutter", "state_management", f"{rel_app}:{STATE_MGMT[d]}", f"{rel_app}/pubspec.yaml"))
        manifest = app_root / "android/app/src/main/AndroidManifest.xml"
        if manifest.exists():
            for perm in sorted(set(PERMISSION.findall(_read(manifest)))):
                out.append(Feature("flutter", "android_permission", f"{rel_app}:{perm}", manifest.relative_to(base).as_posix()))
        lib = app_root / "lib"
        if lib.is_dir():
            for p in _walk(lib, (".dart",)):
                text, rel = _read(p), p.relative_to(base).as_posix()
                for m in WIDGET_CLASS.finditer(text):
                    name = m.group(1)
                    if re.search(r"(Screen|Page|View)$", name):
                        has_form = bool(FORM.search(text))
                        out.append(Feature("flutter", "screen", f"{rel_app}:{name}", f"{rel}:{_line(text, m.start())}",
                                           {"widget": m.group(2), "has_form": has_form, "test_keys": sorted(set(KEY.findall(text)))[:30]}))
                for m in NAMED_ROUTE.finditer(text):
                    out.append(Feature("flutter", "route", f"{rel_app}:{m.group(1)}", f"{rel}:{_line(text, m.start())}"))
                for m in GO_ROUTE.finditer(text):
                    out.append(Feature("flutter", "route", f"{rel_app}:{m.group(1)}", f"{rel}:{_line(text, m.start())}"))
                for m in HTTP_CALL.finditer(text):
                    target = m.group(2).strip().strip("'\"")
                    out.append(Feature("flutter", "http_call", f"{rel_app}:{m.group(1).upper()} {target}", f"{rel}:{_line(text, m.start())}"))
                if re.search(r"(Repository|Service|Api)\b", p.stem.title().replace("_", "")) or re.search(r"class\s+\w+(Repository|Service)\b", text):
                    for cm in re.finditer(r"class\s+(\w+(?:Repository|Service|Api))\b", text):
                        out.append(Feature("flutter", "data_service", f"{rel_app}:{cm.group(1)}", f"{rel}:{_line(text, cm.start())}"))
        for tdir in ("test", "integration_test"):
            d = app_root / tdir
            if d.is_dir():
                for p in _walk(d, (".dart",)):
                    if p.name.endswith("_test.dart"):
                        out.append(Feature("flutter", "existing_test", p.relative_to(base).as_posix(), p.relative_to(base).as_posix(), {"type": tdir}))
    return out


# ---------------- Paneles web ----------------
REACT_ROUTE = re.compile(r"<Route[^>]*\bpath\s*=\s*['\"{]+([^'\"}]+)")
ROUTE_OBJ = re.compile(r"\bpath\s*:\s*['\"](/[^'\"]*)['\"]")
FETCH = re.compile(r"\b(?:fetch|axios\.(?:get|post|put|patch|delete)|api\.(?:get|post|put|patch|delete))\s*\(\s*[`'\"]([^`'\"]{1,150})")
BUTTON = re.compile(r"<button[^>]*>\s*([^<{]{2,60})<", re.I)
FORM_HTML = re.compile(r"<form\b[^>]*?(?:action\s*=\s*['\"]([^'\"]*)['\"])?", re.I)
TABLE = re.compile(r"<table\b", re.I)


def discover_web(root: Path, base: Path) -> list[Feature]:
    out: list[Feature] = []
    for p in _walk(root, (".tsx", ".jsx", ".ts", ".js", ".html", ".vue")):
        rel = p.relative_to(base).as_posix()
        if rel.endswith((".min.js", ".d.ts")) or "/static/vendor" in rel:
            continue
        text = _read(p)
        # Next.js app router: src/app/**/page.tsx
        m_next = re.search(r"(?:^|/)app/(.*?)/?page\.(tsx|jsx|ts|js)$", rel)
        if m_next:
            route = "/" + "/".join(s for s in m_next.group(1).split("/") if s and not s.startswith("("))
            out.append(Feature("web", "route", route, rel, {"framework": "nextjs"}))
        for m in REACT_ROUTE.finditer(text):
            out.append(Feature("web", "route", m.group(1), f"{rel}:{_line(text, m.start())}"))
        if p.suffix in (".tsx", ".jsx", ".ts", ".js") and ("router" in text.lower() or "routes" in p.stem.lower()):
            for m in ROUTE_OBJ.finditer(text):
                out.append(Feature("web", "route", m.group(1), f"{rel}:{_line(text, m.start())}"))
        for m in FETCH.finditer(text):
            out.append(Feature("web", "api_call", m.group(1), f"{rel}:{_line(text, m.start())}"))
        for m in BUTTON.finditer(text):
            label = m.group(1).strip()
            if label:
                out.append(Feature("web", "button", f"{rel}:{label}", f"{rel}:{_line(text, m.start())}"))
        for m in FORM_HTML.finditer(text):
            out.append(Feature("web", "form", f"{rel}:{m.group(1) or 'form'}", f"{rel}:{_line(text, m.start())}"))
        if TABLE.search(text):
            out.append(Feature("web", "table", rel, rel))
    return out


# ---------------- Android nativo (Java/Kotlin) ----------------
ACTIVITY = re.compile(r"<(activity|service|receiver)\b[^>]*android:name=\"([^\"]+)\"", re.S)
ANDROID_TEST = re.compile(r"@Test\b")


def discover_android(root: Path, base: Path) -> list[Feature]:
    out: list[Feature] = []
    for mf in [p for p in _walk(root, (".xml",)) if p.name == "AndroidManifest.xml" and "/src/main/" in p.as_posix()]:
        # Las apps Flutter ya se cubren en discover_flutter
        if any((par / "pubspec.yaml").exists() for par in mf.parents if base in par.parents or par == base):
            continue
        app_dir = mf.parents[2]  # .../app/src/main/AndroidManifest.xml -> .../app
        rel_app = app_dir.relative_to(base).as_posix()
        text, rel = _read(mf), mf.relative_to(base).as_posix()
        for perm in sorted(set(PERMISSION.findall(text))):
            out.append(Feature("android", "android_permission", f"{rel_app}:{perm}", rel))
        for m in ACTIVITY.finditer(text):
            out.append(Feature("android", m.group(1), f"{rel_app}:{m.group(2)}", f"{rel}:{_line(text, m.start())}"))
        for tdir in ("src/androidTest", "src/test"):
            d = app_dir / tdir
            if d.is_dir():
                for p in _walk(d, (".java", ".kt")):
                    if ANDROID_TEST.search(_read(p)):
                        out.append(Feature("android", "existing_test", p.relative_to(base).as_posix(), p.relative_to(base).as_posix(),
                                           {"type": "instrumented" if "androidTest" in tdir else "unit"}))
    return out


def discover(root: Path, subpaths: list[str] | None = None) -> list[Feature]:
    """Analiza `root` completo o solo las subcarpetas de los componentes; rutas siempre relativas a `root`."""
    root = root.resolve()
    scans = []
    for sp in subpaths or []:
        d = (root / sp).resolve()
        if d != root and root not in d.parents:
            raise ValueError(f"Subcarpeta fuera del proyecto: {sp}")
        if d.is_dir():
            scans.append(d)
    scans = scans or [root]
    feats: list[Feature] = []
    for scan in scans:
        for fn in (discover_python, discover_sql, discover_flutter, discover_web, discover_android):
            feats += fn(scan, root)
    # Deduplicar por stable_id conservando la primera ubicación
    seen: dict[str, Feature] = {}
    for f in feats:
        seen.setdefault(f.stable_id, f)
    return list(seen.values())
