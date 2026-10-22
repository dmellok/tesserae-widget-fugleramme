"""The artwork and the faces, fetched from Fugleramme at a pinned commit.

The classic plates are some 220 MB and the label faces 4.8 MB, far more than a
catalog widget may ship, so only the small records travel with the widget
(assets/artwork/classic/manifest.json, geometry.json, ATTRIBUTION.md). A plate,
a perch or a face is fetched from raw.githubusercontent.com at `PIN` the first
time a page needs it and kept in the widget's data folder, laid out the way
Fugleramme's own assets/ is, so the vendored renderer reads it unchanged.

A fetch that fails is remembered for `RETRY_S`, so a dead network costs one
attempt per file rather than one per render; the page is drawn without what is
missing (glue.plan).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

# Fugleramme v0.29.0. Bumping it re-fetches everything into a new folder, so the
# bundled manifest and geometry must come from the same commit.
PIN = "03046c764748a74bd6ffddf5ed526ceb0c199436"
VERSION = "0.29.0"
REPO = "https://github.com/arnegiacomo/fugleramme"
RAW = "https://raw.githubusercontent.com/arnegiacomo/fugleramme/{pin}/assets/{path}"
STYLE = "classic"

HERE = Path(__file__).resolve().parents[1]
BUNDLED_STYLE = HERE / "assets" / "artwork" / STYLE
# Copied next to the fetched plates: the vendored names/sizes modules read a
# style's records from the folder its plates sit in.
STYLE_RECORDS = ("manifest.json", "geometry.json", "ATTRIBUTION.md")

USER_AGENT = "tesserae-fugleramme/0.1 (+https://github.com/dmellok/tesserae-widget-fugleramme)"
TIMEOUT_S = 20
MAX_BYTES = 8_000_000
RETRY_S = 3600
_WORKERS = 6

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="fugleramme")
_inflight: dict[Path, concurrent.futures.Future[bool]] = {}
# Re-entrant: a fetch that is over before its callback is added runs the
# callback at once, on the thread still holding the lock.
_lock = threading.RLock()


def url_for(rel: str, pin: str = PIN) -> str:
    """Where a file under Fugleramme's assets/ is served at the pin."""
    return RAW.format(pin=pin, path=rel.lstrip("/"))


class Store:
    """The widget's copy of Fugleramme's assets/, under its data folder."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / f"assets-{PIN[:12]}"
        self.artwork = self.root / "artwork"
        self.fonts = self.root / "fonts"
        self.style_dir = self.artwork / STYLE
        self.pages = self.data_dir / "pages"
        self.jobs = self.data_dir / "jobs"
        self._records()

    def _records(self) -> None:
        """The style's manifest and geometry beside where its plates land."""
        self.style_dir.mkdir(parents=True, exist_ok=True)
        for name in STYLE_RECORDS:
            src, dst = BUNDLED_STYLE / name, self.style_dir / name
            with contextlib.suppress(OSError):
                if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                    shutil.copyfile(src, dst)

    def path(self, rel: str) -> Path:
        """The local copy of a file under assets/ ("artwork/classic/birds/x.webp")."""
        return self.root / rel

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def have(self, rel: str) -> bool:
        return self.path(rel).is_file()

    def ensure(self, rels: Iterable[str], wait: float = 0.0) -> list[str]:
        """Start fetching whatever of `rels` is missing and wait up to `wait`
        seconds for it. Returns the ones still missing."""
        futures = [f for rel in dict.fromkeys(rels) if (f := self._submit(rel)) is not None]
        if futures and wait > 0:
            concurrent.futures.wait(futures, timeout=wait)
        return [rel for rel in dict.fromkeys(rels) if not self.have(rel)]

    def _submit(self, rel: str) -> concurrent.futures.Future[bool] | None:
        target = self.path(rel)
        if target.is_file():
            return None
        with _lock:
            running = _inflight.get(target)
            if running is not None:
                return running
            miss = target.with_name(target.name + ".miss")
            with contextlib.suppress(OSError):
                if time.time() - miss.stat().st_mtime < RETRY_S:
                    return None
            future = _pool.submit(_download, url_for(rel), target)
            _inflight[target] = future
            future.add_done_callback(lambda _f, t=target: _done(t))
            return future


def _done(target: Path) -> None:
    with _lock:
        _inflight.pop(target, None)


def _valid(target: Path, body: bytes) -> bool:
    """Whether the bytes are what the file claims to be, so a captive portal's
    page or a truncated answer is never kept as a plate or a face."""
    suffix = target.suffix.lower()
    if suffix == ".webp":
        return body[:4] == b"RIFF" and body[8:12] == b"WEBP"
    if suffix == ".png":
        return body[:8] == b"\x89PNG\r\n\x1a\n"
    if suffix in (".ttf", ".otf"):
        return body[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")
    return bool(body)


def _download(url: str, target: Path) -> bool:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            body = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        log.warning("Could not fetch %s: %s", url, error)
        _missed(target)
        return False
    if len(body) > MAX_BYTES or not _valid(target, body):
        log.warning("Fetched %s but it is not a usable file", url)
        _missed(target)
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(body)
        os.replace(tmp, target)
    except OSError as error:
        log.warning("Could not keep %s: %s", target, error)
        return False
    with contextlib.suppress(OSError):
        target.with_name(target.name + ".miss").unlink()
    return True


def _missed(target: Path) -> None:
    with contextlib.suppress(OSError):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_name(target.name + ".miss").touch()


def pending(rels: Iterable[str], store: Store, wait: float) -> list[str]:
    """Wait up to `wait` seconds for any of `rels` still being fetched, without
    starting a fetch of its own. The page route uses this: the fetches belong to
    fetch(), which runs first."""
    deadline = time.monotonic() + wait
    with _lock:
        running = [f for rel in rels if (f := _inflight.get(store.path(rel))) is not None]
    if running:
        concurrent.futures.wait(running, timeout=max(0.0, deadline - time.monotonic()))
    return [rel for rel in rels if not store.have(rel)]
