"""Fugleramme for Tesserae: the birds a BirdNET-Go or a BirdWeather station heard,
drawn by Fugleramme's own renderer (https://github.com/arnegiacomo/fugleramme)
as hand-coloured 1800s plates packed onto one page.

fetch() asks the detector and plans the page (glue.plan); client.js then loads
the page as a PNG from this widget's /page.png route at the cell's own pixel
size, which draws it once per job and size and keeps it. The plates and the
label faces are fetched from Fugleramme at a pinned commit on first use
(glue.assets).
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import logging
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_PACKAGE = "_tesserae_plugins.fugleramme.widget"


def _package() -> types.ModuleType:
    """The widget folder as a package, so the vendored renderer (fugleramme/)
    and the glue round it (glue/) import each other relatively. Loaded by path
    under a name of the widget's own, as Tesserae loads server.py."""
    # Python resolves `from . import x` through every parent, so the two above
    # the widget's own have to exist; empty, they shadow nothing.
    for parent in ("_tesserae_plugins", "_tesserae_plugins.fugleramme"):
        if parent not in sys.modules:
            stub = types.ModuleType(parent)
            stub.__path__ = []  # type: ignore[attr-defined]
            sys.modules[parent] = stub
    held = sys.modules.get(_PACKAGE)
    if held is None:
        held = types.ModuleType(_PACKAGE)
        held.__path__ = [str(_HERE)]  # type: ignore[attr-defined]
        held.__package__ = _PACKAGE
        sys.modules[_PACKAGE] = held
    return held


_package()
assets = importlib.import_module(f"{_PACKAGE}.glue.assets")
plan = importlib.import_module(f"{_PACKAGE}.glue.plan")
_source = importlib.import_module(f"{_PACKAGE}.fugleramme.source")

# Nothing waits on a download: fetch() starts them and the page is drawn with
# what has arrived, then drawn again as the rest lands. Until then fetch() asks
# the editor and a scheduled send's warm to come back this often.
LIVE_REFRESH_S = 4
FIRST_WAIT_S = 3.0
FIRST_WANT = 3
MAX_SIDE = 4096
PAGES_KEPT = 120

# The page route only reads a job fetch() wrote and the files it fetched, so a
# loopback render may load it with no session.
RENDER_SAFE_ENDPOINTS: tuple[str, ...] = ("fugleramme_page.page",)

_drawing = threading.BoundedSemaphore(2)
_page_locks: dict[str, threading.Lock] = {}
_page_locks_lock = threading.Lock()
_data_dir: Path | None = None


def _friendly(error: Exception, opts: Any) -> str:
    text = str(error)
    if isinstance(error, _source.NoSuchStation):
        return f"BirdWeather has no station {opts.station}."
    if opts.source == _source.BIRDWEATHER:
        return "Couldn't reach BirdWeather right now."
    if "answered 401" in text:
        if opts.password:
            return "BirdNET-Go refused the password."
        return "BirdNET-Go asks for a password. Fill in the BirdNET-Go password field."
    if "did not answer within" in text:
        return f"BirdNET-Go at {opts.address} took too long to answer."
    return f"Couldn't reach BirdNET-Go at {opts.address}."


def strip_px(w: int, h: int) -> int:
    """The height of the credit line under the page."""
    return max(12, min(22, round(min(w, h) * 0.026)))


def fetch(options: dict, settings: dict, *, ctx: dict) -> dict:
    global _data_dir
    _data_dir = Path(ctx["data_dir"])
    store = assets.Store(_data_dir)
    opts = plan.options_of(options)
    try:
        source = plan.source_for(opts)
        # One tray card for everything this page starts fetching.
        with assets.batch():
            job = plan.plan(opts, source, store)
    except plan.BadInput as error:
        return {"error": str(error)}
    except _source.Unavailable as error:
        log.info("Fugleramme detector: %s", error)
        return {"error": _friendly(error, opts)}
    plan.save(job, store)
    cell_w = int(ctx.get("cell_w") or 0)
    cell_h = int(ctx.get("cell_h") or 0)
    birds = len(job["art"])
    pending = assets.in_flight(_wanted(job, store))
    hints = {"live_refresh_s": LIVE_REFRESH_S, "live_pending": pending} if pending else {}
    return {
        **hints,
        "job": job["key"],
        "mode": job["mode"],
        "demo": job["demo"],
        "birds": birds,
        "credit": plan.credit_line(job["credit"]["artists"]),
        "credit_short": plan.credit_line([]),
        "paper": plan.paper_of(job["textured"]),
        "cell_w": cell_w,
        "cell_h": cell_h,
        "strip": strip_px(cell_w, cell_h) if cell_w and cell_h else 0,
    }


def _files(job: dict) -> list[str]:
    """What the job's page is drawn from, under assets/."""
    return [*job["fonts"], *job["art"].values(), *([job["perch"]] if job["perch"] else [])]


def _wanted(job: dict, store: Any) -> list[Path]:
    """Everything the page waits on: its files and its name lists."""
    lists = [store.label_path(code) for code in (job["primary"], job["secondary"])]
    return [*(store.path(rel) for rel in _files(job)), *(p for p in lists if p)]


def _store() -> Any:
    """The data folder fetch() was handed, or the plugin registry's."""
    if _data_dir is not None:
        return assets.Store(_data_dir)
    from flask import current_app

    plugin = current_app.config["PLUGIN_REGISTRY"].get("fugleramme")
    return assets.Store(plugin.data_dir)


def _lock_for(name: str) -> threading.Lock:
    with _page_locks_lock:
        if len(_page_locks) > 256:
            _page_locks.clear()
        return _page_locks.setdefault(name, threading.Lock())


def page_png(key: str, w: int, h: int, store: Any) -> bytes | None:
    """The job's page at w x h as PNG, drawn once and kept. None for an unknown job."""
    job = plan.load(key, store)
    if job is None:
        return None
    w, h = max(16, min(MAX_SIDE, w)), max(16, min(MAX_SIDE, h))
    # Drawn with what has arrived: a bird whose plate is still on its way is
    # left off, a face still on its way stands in with a system one. Only a page
    # with no plate at all yet gives the first few a moment (FIRST_WAIT_S, well
    # inside Tesserae's render budget) rather than drawing a bare sheet.
    plates = [store.path(rel) for rel in job["art"].values()]
    if plates and not any(path.is_file() for path in plates):
        assets.first_arrivals(plates, min(FIRST_WANT, len(plates)), FIRST_WAIT_S)
    missing = [rel for rel in _files(job) if not store.have(rel)]
    # A page drawn without some of its files is kept under a name of its own,
    # so it is drawn again once they arrive rather than held for good.
    gap = "_" + hashlib.blake2b("|".join(missing).encode(), digest_size=4).hexdigest()
    name = f"{key}_{w}x{h}{gap if missing else ''}.png"
    path = store.pages / name
    with _lock_for(name):
        with contextlib.suppress(OSError):
            return path.read_bytes()
        started = time.monotonic()
        with _drawing:
            image = plan.draw(job, (w, h), store)
        buffer = io.BytesIO()
        image.save(buffer, "PNG", compress_level=6)
        body = buffer.getvalue()
        log.debug("Fugleramme page %s drawn in %.1fs", name, time.monotonic() - started)
        with contextlib.suppress(OSError):
            store.pages.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
            tmp.write_bytes(body)
            tmp.replace(path)
            plan._prune(store.pages, "*.png", PAGES_KEPT)
        return body


def blueprint():
    from flask import Blueprint, Response, abort, request

    bp = Blueprint("fugleramme_page", __name__)

    @bp.get("/page.png")
    def page() -> Response:
        key = request.args.get("job", "")
        if not plan.valid_key(key):
            abort(404)
        try:
            w = int(request.args.get("w", "0"))
            h = int(request.args.get("h", "0"))
        except ValueError:
            abort(400)
        if w <= 0 or h <= 0:
            abort(400)
        body = page_png(key, w, h, _store())
        if body is None:
            abort(404)
        response = Response(body, mimetype="image/png")
        response.headers["Cache-Control"] = "private, max-age=86400"
        return response

    return bp
