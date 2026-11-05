"""A running Fugleramme as the cell's source: its own page, as its admin sets it.

Fugleramme serves the page it shows on its kiosk at /collage.png, and a cheap
/state token that changes whenever that page would (docs: Screens). Neither
needs a sign-in. fetch() asks /state first and downloads /collage.png only when
the token is new, keeping the PNG in the widget's data folder; the cell then
loads it from this widget's own /remote.png route. Panels and the editor never
have to reach the frame's LAN address themselves, and a frame that is briefly
away leaves the last page it drew on the cell rather than an error.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

from ..fugleramme.source import with_scheme

SERVER = "server"
FITS = ("contain", "cover")

STATE_TIMEOUT_S = 5.0
# A Pi Zero 2 W packs a large collage slowly (Fugleramme's docs say so), and
# /collage.png is drawn on request.
PAGE_TIMEOUT_S = 60.0
MAX_BYTES = 40_000_000
PAGES_KEPT = 24
USER_AGENT = "tesserae-fugleramme (+https://github.com/dmellok/tesserae-widget-fugleramme)"

_KEY = re.compile(r"[0-9a-f]{20}")
_lock = threading.Lock()


class Unreachable(Exception):
    """The frame did not give us a page; the message is for the cell."""


def base_of(typed: str) -> str:
    """The frame's address as typed, with a scheme and without a trailing slash
    or a page path pasted along with it."""
    base = with_scheme(typed).rstrip("/")
    for tail in ("/collage.png", "/frame.png", "/admin", "/index.html"):
        if base.endswith(tail):
            base = base[: -len(tail)]
    return base.rstrip("/")


def valid_key(key: str) -> bool:
    return bool(_KEY.fullmatch(key or ""))


def folder(data_dir: Path) -> Path:
    return Path(data_dir) / "remote"


def page_path(data_dir: Path, key: str) -> Path:
    return folder(data_dir) / f"{key}.png"


def _key(base: str, token: str) -> str:
    return hashlib.blake2b(f"{base}\n{token}".encode(), digest_size=10).hexdigest()


def _get(url: str, timeout: float, etag: str = "") -> tuple[int, bytes, str]:
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (the user's own frame)
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise Unreachable("Fugleramme's page is larger than this widget will take.")
            return response.status, body, response.headers.get("ETag", "")
    except urllib.error.HTTPError as error:
        return error.code, b"", ""


def _token(base: str) -> str:
    """The frame's /state token, or "" when this Fugleramme has none to give."""
    try:
        status, body, _ = _get(f"{base}/state", STATE_TIMEOUT_S)
    except (OSError, ValueError):
        return ""
    if status != 200:
        return ""
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        return str(json.loads(body).get("token") or "")
    return ""


def _index(data_dir: Path) -> dict[str, Any]:
    with contextlib.suppress(OSError, ValueError):
        return json.loads((folder(data_dir) / "index.json").read_text())
    return {}


def _remember(data_dir: Path, base: str, entry: dict[str, Any]) -> None:
    index = _index(data_dir)
    index[base] = entry
    if len(index) > PAGES_KEPT:
        for old in list(index)[: len(index) - PAGES_KEPT]:
            index.pop(old, None)
    path = folder(data_dir) / "index.json"
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(index))
    tmp.replace(path)
    keep = {f"{e.get('key')}.png" for e in index.values()}
    for png in folder(data_dir).glob("*.png"):
        if png.name not in keep:
            png.unlink(missing_ok=True)


def _describe(body: bytes) -> tuple[int, int, str]:
    """The page's size and its paper: the colour at its corner, which the cell
    shows round a page that does not fill it."""
    with Image.open(io.BytesIO(body)) as image:
        w, h = image.size
        r, g, b = image.convert("RGB").getpixel((min(2, w - 1), min(2, h - 1)))[:3]
    return w, h, f"#{r:02x}{g:02x}{b:02x}"


def page(typed: str, data_dir: Path) -> dict[str, Any]:
    """The frame's page, kept on disk: {key, w, h, paper, stale}. Raises
    Unreachable only when there is no earlier page to fall back on."""
    base = base_of(typed)
    if not base or "://" not in base:
        raise Unreachable(
            "Type the address Fugleramme opens at, such as http://fugleramme.local:8080."
        )
    folder(data_dir).mkdir(parents=True, exist_ok=True)
    with _lock:
        held = _index(data_dir).get(base)
    token = _token(base)
    if held and token and held.get("token") == token and page_path(data_dir, held["key"]).is_file():
        return {**held, "stale": False}
    try:
        status, body, etag = _get(
            f"{base}/collage.png", PAGE_TIMEOUT_S, (held or {}).get("etag", "")
        )
    except Unreachable:
        raise
    except (OSError, ValueError) as error:
        return _fallback(held, data_dir, f"Couldn't reach Fugleramme at {base}.", error)
    if status == 304 and held and page_path(data_dir, held["key"]).is_file():
        return {**held, "stale": False}
    if status != 200 or not body:
        reason = {
            503: "Fugleramme can't reach its detector right now.",
            404: f"Nothing at {base} answers as Fugleramme.",
        }.get(status, f"Fugleramme at {base} answered {status}.")
        return _fallback(held, data_dir, reason, None)
    try:
        w, h, paper = _describe(body)
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        return _fallback(held, data_dir, f"{base}/collage.png is not a picture.", error)
    entry = {"key": _key(base, token or etag or hashlib.sha1(body).hexdigest()), "token": token,
             "etag": etag, "w": w, "h": h, "paper": paper}  # fmt: skip
    path = page_path(data_dir, entry["key"])
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_bytes(body)
    tmp.replace(path)
    with _lock:
        _remember(data_dir, base, entry)
    return {**entry, "stale": False}


def _fallback(
    held: dict | None, data_dir: Path, reason: str, error: Exception | None
) -> dict[str, Any]:
    if held and page_path(data_dir, held["key"]).is_file():
        return {**held, "stale": True, "reason": reason}
    raise Unreachable(reason) from error
