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
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

try:  # Tesserae's progress tray; an older host has none
    from app import jobs
except Exception:
    jobs = None

# Fugleramme v0.29.0. Bumping it re-fetches everything into a new folder, so the
# bundled manifest and geometry must come from the same commit.
PIN = "03046c764748a74bd6ffddf5ed526ceb0c199436"
VERSION = "0.29.0"
REPO = "https://github.com/arnegiacomo/fugleramme"
RAW = "https://raw.githubusercontent.com/arnegiacomo/fugleramme/{pin}/assets/{path}"
STYLE = "classic"

# BirdNET v2.4's label lists in other languages, at the BirdNET-Analyzer release
# Fugleramme's tools/vendor_fonts.py reads them from. They name the birds when no
# detector does: the demo garden, and species a detector has no name for.
LABELS = (
    "https://raw.githubusercontent.com/birdnet-team/BirdNET-Analyzer/v2.4.0/"
    "birdnet_analyzer/labels/V2.4/BirdNET_GLOBAL_6K_V2.4_Labels_{}.txt"
)
# Language code, as Fugleramme and BirdNET-Go's dictionaries spell it, to the
# label file's own suffix. English is the bundled list.
LABEL_FILES = {
    "af": "af", "ar": "ar", "bg": "bg", "ca": "ca", "cs": "cs", "da": "da",
    "de": "de", "el": "el", "es": "es", "fi": "fi", "fr": "fr", "he": "he",
    "hr": "hr", "hu": "hu", "id": "in", "is": "is", "it": "it", "ja": "ja",
    "ko": "ko", "lt": "lt", "ml": "ml", "nl": "nl", "nb": "no", "pl": "pl",
    "pt-br": "pt_BR", "pt-pt": "pt_PT", "ro": "ro", "ru": "ru", "sk": "sk",
    "sl": "sl", "sr": "sr", "sv": "sv", "th": "th", "tr": "tr", "uk": "uk",
    "zh": "zh",
}  # fmt: skip

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
        with batch():
            futures = [f for rel in dict.fromkeys(rels) if (f := self._submit(rel)) is not None]
        if futures and wait > 0:
            concurrent.futures.wait(futures, timeout=wait)
        return [rel for rel in dict.fromkeys(rels) if not self.have(rel)]

    def _submit(self, rel: str) -> concurrent.futures.Future[bool] | None:
        return submit(url_for(rel), self.path(rel))

    def label_path(self, code: str) -> Path | None:
        suffix = LABEL_FILES.get(code)
        return None if suffix is None else self.data_dir / "labels" / f"{suffix}.txt"

    def labels(self, code: str, wait: float = 0.0) -> Path | None:
        """BirdNET v2.4's label list in a language, fetched from BirdNET-Analyzer
        where Fugleramme's own tools read it, or None while it is not here."""
        suffix = LABEL_FILES.get(code)
        if suffix is None:
            return None
        target = self.data_dir / "labels" / f"{suffix}.txt"
        with batch():
            future = submit(LABELS.format(suffix), target)
        if future is not None and wait > 0:
            concurrent.futures.wait([future], timeout=wait)
        return target if target.is_file() else None


def submit(url: str, target: Path) -> concurrent.futures.Future[bool] | None:
    """Start fetching `url` into `target` unless it is there, on its way, or
    failed within `RETRY_S`. The fetch in flight, if there is one."""
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
        future = _pool.submit(_download, url, target)
        _inflight[target] = future
        _batch["total"] += 1
        _batch["kinds"][_kind(target)] += 1
    future.add_done_callback(lambda f, t=target: _done(t, f))
    return future


def in_flight(targets: Iterable[Path]) -> int:
    """How many of `targets` are being fetched now."""
    with _lock:
        return sum(1 for target in targets if target in _inflight)


# The downloads since the tray last went quiet: one card for all of them.
_batch: dict = {"total": 0, "done": 0, "failed": 0, "kinds": Counter(), "opening": 0}


@contextlib.contextmanager
def batch():
    """While a page's files are being handed out, the card is not over even if
    the ones handed out so far have all landed: one card per page, not per file."""
    with _lock:
        _batch["opening"] += 1
    try:
        yield
    finally:
        with _lock:
            _batch["opening"] -= 1
        _report()


SOURCE, KEY, LABEL = "fugleramme", "assets", "Fugleramme"
TITLE = "Fetching plates from Fugleramme"


def _kind(target: Path) -> str:
    if target.suffix == ".txt":
        return "name list"
    if target.suffix in (".ttf", ".otf"):
        return "typeface"
    return "plate"


def _count(n: int, kind: str) -> str:
    return f"{n} {kind}" + ("" if n == 1 else "s")


def _summary(kinds: Counter) -> str:
    """ "17 plates and 1 typeface" """
    parts = [_count(kinds[k], k) for k in ("plate", "typeface", "name list") if kinds[k]]
    return " and ".join(parts) if len(parts) < 3 else ", ".join(parts[:-1]) + " and " + parts[-1]


def _done(target: Path, future: concurrent.futures.Future[bool]) -> None:
    try:
        ok = bool(future.result())
    except Exception:
        ok = False
    with _lock:
        _inflight.pop(target, None)
        _batch["done"] += 1
        _batch["failed"] += 0 if ok else 1
    _report()


def _report() -> None:
    """The tray's card for the downloads in hand, finished or failed once the
    last of them is in. Every call is guarded: the tray is a nicety."""
    with _lock:
        total, done, failed = _batch["total"], _batch["done"], _batch["failed"]
        kinds = Counter(_batch["kinds"])
        over = total > 0 and done >= total and not _inflight and not _batch["opening"]
        if over:
            _batch.update(total=0, done=0, failed=0, kinds=Counter())
    if jobs is None or total == 0:
        return
    try:
        if not over:
            plates = kinds["plate"]
            what = (
                f"{min(done, total)} of {_summary(kinds)}"
                if plates != total
                else (f"{min(done, total)} of {_count(total, 'plate')}")
            )
            jobs.report(SOURCE, KEY, TITLE, progress=done / total, detail=what, label=LABEL)
        elif failed:
            jobs.fail(
                SOURCE,
                KEY,
                f"{_count(failed, 'file')} could not be fetched; trying again within the hour",
            )
        else:
            jobs.finish(SOURCE, KEY, _summary(kinds))
    except Exception:
        log.debug("Fugleramme could not report to the tray", exc_info=True)


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
    if suffix == ".txt":  # a label list: "Genus species_Common name" lines
        try:
            first = body.decode("utf-8").split("\n", 1)[0]
        except UnicodeDecodeError:
            return False
        return "_" in first and "<" not in first
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


def first_arrivals(targets: list[Path], want: int, wait: float) -> int:
    """Wait up to `wait` seconds until `want` of `targets` are here, returning
    as soon as they are. Only for a page none of whose plates has arrived: a
    bare sheet tells nobody anything, and the first plates of a cold cache
    land within a second or two."""
    deadline = time.monotonic() + wait
    while True:
        here = sum(1 for target in targets if target.is_file())
        left = deadline - time.monotonic()
        if here >= want or left <= 0:
            return here
        with _lock:
            running = [f for target in targets if (f := _inflight.get(target)) is not None]
        if not running:
            return here
        concurrent.futures.wait(
            running, timeout=left, return_when=concurrent.futures.FIRST_COMPLETED
        )
