"""A cell's options to a Fugleramme page, in two halves.

`plan` runs in the widget's fetch(): it asks the detector what Fugleramme's
mode would ask it, recording every answer, works out which plates and faces the
page needs and starts fetching them, and returns a job: the recorded answers,
the names, and the settings, keyed by Fugleramme's own `modes.state_key` (what
the page is a function of). It never draws.

`draw` runs in the page route, once the cell's pixel size is known: it replays
the job's answers into the same vendored mode, which draws exactly the page
Fugleramme would, and nothing in it touches the network. A job's key is the
same for every size, so the route caches one PNG per job and size.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ..fugleramme import languages, modes, names, taxa
from ..fugleramme.api import ApiSource
from ..fugleramme.birdweather import BirdWeatherSource
from ..fugleramme.languages import NONE, SCIENTIFIC, STATION, Namer
from ..fugleramme.render import collage, fonts, packing
from ..fugleramme.render.collage import DEFAULT_MARGIN, DEFAULT_RANKING, NO_LIMIT, RANKINGS
from ..fugleramme.render.page import Edges, blank, day_ordinal
from ..fugleramme.render.paper import PANEL_PAPER, TARGET_PAPER
from ..fugleramme.source import (
    BIRDNET_GO,
    BIRDWEATHER,
    Detection,
    Species,
    station_fault,
    station_pages,
    with_scheme,
)
from . import assets
from .demo import DemoSource

log = logging.getLogger(__name__)

# Bumped when the widget draws a page differently for the same inputs, so a
# cached PNG from before is not served.
RENDER_VERSION = 2

COMMON = "common"  # the detector's own species language (Fugleramme's STATION)
ENGLISH = "en"  # BirdNET v2.4's own English label, bundled
# Every language a page can be named in: the pseudo-languages, English, and each
# BirdNET v2.4 label language, coded as BirdNET-Go's dictionaries code them.
LANGUAGES = (SCIENTIFIC, COMMON, ENGLISH, *assets.LABEL_FILES)
# Nothing is waited for: a page is drawn with what has arrived, and drawn
# again as the rest lands (server.page_png, live_refresh_s).
LABEL_WAIT_S = 0.0

MODES = tuple(modes.MODES)
LOOKBACKS = (0.25, 0.5, 1, 3, 6, 12, 24, 72, 168, 720, 0)
MARGIN_CEILING = 25
LIMIT_CEILING = 500
DETECTOR_TIMEOUT_S = 5.0

# The plate's source key in manifest.json to the people credited for the work,
# as classic/ATTRIBUTION.md names them. Anything else is credited by its key.
ARTISTS = {
    "gould": "John Gould",
    "vonwright": "the von Wright brothers",
    "audubon": "J. J. Audubon",
    "dresser": "H. E. Dresser",
    "fuertes": "L. A. Fuertes",
    "keulemans": "J. G. Keulemans",
    "levaillant": "F. Levaillant",
    "bree": "C. R. Bree",
    "swainson": "W. Swainson",
    "siebold": "P. F. von Siebold",
    "cassin": "J. Cassin",
    "naumann": "J. F. Naumann",
    "temminck": "C. J. Temminck",
    "lear": "Edward Lear",
    "sharpe": "R. B. Sharpe",
    "wolf": "Joseph Wolf",
    "edwards": "George Edwards",
    "finchdavies": "C. G. Finch-Davies",
    "palmstruch": "J. W. Palmstruch",
    "werner": "J. C. Werner",
    "gronvold": "Henrik Grønvold",
    "dekay": "J. E. DeKay",
    "wilson": "Alexander Wilson",
    "catesby": "Mark Catesby",
    "baird": "S. F. Baird",
    "nuttall": "Thomas Nuttall",
    "horsfall": "R. B. Horsfall",
    "brehm": "C. L. Brehm",
}
LICENCE = "CC BY-SA 4.0"


@dataclass(frozen=True)
class Options:
    """A cell's options, coerced the way Fugleramme coerces its settings file."""

    source: str = BIRDNET_GO
    address: str = ""
    password: str = ""
    station: str = ""
    mode: str = modes.DEFAULT_MODE
    lookback_hours: float = 24
    layout: str = packing.DEFAULT_LAYOUT
    spotlight: bool = False
    species_limit: int = 40
    ranking: str = DEFAULT_RANKING
    show_names: bool = True
    name_key: bool = False
    names: str = SCIENTIFIC
    second: str = NONE
    font: str = fonts.DEFAULT_FONT
    label_size: str = fonts.DEFAULT_LABEL_SIZE
    margin: int = round(DEFAULT_MARGIN * 100)
    paper: str = "flat"

    @property
    def demo(self) -> bool:
        return not (self.station if self.source == BIRDWEATHER else self.address).strip()

    @property
    def textured(self) -> bool:
        return self.paper == "textured"


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "on", "yes"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "0", "off", "no", ""):
        return False
    return default


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _choice(value: Any, allowed: Any, default: str) -> str:
    text = str(value if value is not None else "")
    return text if text in allowed else default


def options_of(raw: dict[str, Any] | None) -> Options:
    raw = raw or {}
    d = Options()
    hours = _number(raw.get("lookback_hours"), d.lookback_hours)
    return Options(
        source=_choice(raw.get("source"), (BIRDNET_GO, BIRDWEATHER), d.source),
        address=str(raw.get("address") or "").strip(),
        password=str(raw.get("password") or ""),
        station=str(raw.get("station") or "").strip(),
        mode=_choice(raw.get("mode"), MODES, d.mode),
        lookback_hours=hours if hours in LOOKBACKS else d.lookback_hours,
        layout=_choice(raw.get("layout"), packing.LAYOUTS, d.layout),
        spotlight=_bool(raw.get("spotlight"), d.spotlight),
        species_limit=max(
            NO_LIMIT, min(LIMIT_CEILING, int(_number(raw.get("species_limit"), d.species_limit)))
        ),
        ranking=_choice(raw.get("ranking"), RANKINGS, d.ranking),
        show_names=_bool(raw.get("show_names"), d.show_names),
        name_key=_bool(raw.get("name_key"), d.name_key),
        names=_choice(raw.get("names"), LANGUAGES, d.names),
        second=_choice(raw.get("second"), (NONE, *LANGUAGES), d.second),
        font=_choice(raw.get("font"), fonts.FONTS, d.font),
        label_size=_choice(raw.get("label_size"), fonts.LABEL_SIZES, d.label_size),
        margin=max(0, min(MARGIN_CEILING, int(_number(raw.get("margin"), d.margin)))),
        paper=_choice(raw.get("paper"), ("flat", "textured"), d.paper),
    )


# -- sources -----------------------------------------------------------------

_sources: dict[tuple[str, ...], Any] = {}
_sources_lock = threading.Lock()


def source_for(opts: Options) -> Any:
    """The detector a cell names, or the demo garden. Held across renders, so
    the sources' own short caches (and BirdWeather's long one for the whole
    record) work as they do in Fugleramme."""
    if opts.demo:
        return DemoSource()
    if opts.source == BIRDWEATHER:
        pages = station_pages(opts.station)
        fault = station_fault(pages)
        if fault:
            raise BadInput(_STATION_FAULTS.get(fault, fault))
        key: tuple[str, ...] = (BIRDWEATHER, ",".join(pages))
    else:
        key = (BIRDNET_GO, with_scheme(opts.address).rstrip("/"), opts.password)
    with _sources_lock:
        held = _sources.get(key)
        if held is None:
            if key[0] == BIRDWEATHER:
                held = BirdWeatherSource(key[1], timeout=DETECTOR_TIMEOUT_S)
            else:
                held = ApiSource(key[1], password=opts.password, timeout=DETECTOR_TIMEOUT_S)
            if len(_sources) > 16:
                _sources.clear()
            _sources[key] = held
        return held


class BadInput(ValueError):
    """Something typed into the cell that cannot name a detector."""


_STATION_FAULTS = {
    "not a station": "That is not a BirdWeather station. Type its number, or its page's address.",
    "too many stations": "At most five BirdWeather stations can be read together.",
    "stations on two sites": "The stations have to be on the same BirdWeather site.",
}


class Recorder:
    """A source that remembers every answer it passed on, for the job."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.base_url = getattr(inner, "base_url", "")
        self.calls: dict[str, Any] = {}
        self._seen: dict[str, Any] = {}

    def _once(self, key: str, ask: Any, record: Any) -> Any:
        if key not in self._seen:
            answer = ask()
            self._seen[key] = answer
            self.calls[key] = record(answer)
        return self._seen[key]

    def recent(self, limit: int = 20) -> list[Detection]:
        return self._once(
            f"recent:{limit}",
            lambda: self.inner.recent(limit),
            lambda rows: [
                [d.id, d.detected_at.isoformat(), d.scientific_name, d.confidence] for d in rows
            ],
        )

    def latest(self) -> Detection | None:
        rows = self.recent(1)
        return rows[0] if rows else None

    def species_since(self, hours: float = 24) -> list[tuple[str, int]]:
        return self._once(
            f"species_since:{float(hours)!r}",
            lambda: self.inner.species_since(hours),
            lambda rows: [[name, int(n)] for name, n in rows],
        )

    def last_heard(self, hours: float = 24) -> list[str]:
        return self._once(
            f"last_heard:{float(hours)!r}",
            lambda: self.inner.last_heard(hours),
            list,
        )

    def life_list(self) -> list[Species]:
        return self._once(
            "life_list",
            self.inner.life_list,
            lambda rows: [[s.scientific_name, s.first_seen.isoformat()] for s in rows],
        )

    def stats(self) -> dict:
        return {}

    def close(self) -> None:
        """The detector outlives the job."""


class Replay:
    """A job's recorded answers, as a source. `dropped` species are left out of
    every answer: a plate that could not be fetched leaves its bird off the
    page, as a species Fugleramme has no artwork for is."""

    def __init__(self, calls: dict[str, Any], dropped: frozenset[str] = frozenset()):
        self.calls = calls
        self.dropped = dropped
        self.base_url = "replay"

    def _rows(self, key: str) -> list:
        return [row for row in self.calls.get(key) or [] if _name_of(row) not in self.dropped]

    def recent(self, limit: int = 20) -> list[Detection]:
        return [
            Detection(int(i), datetime.fromisoformat(at), name, float(conf), None)
            for i, at, name, conf in self._rows(f"recent:{limit}")
        ]

    def latest(self) -> Detection | None:
        rows = self.recent(1)
        return rows[0] if rows else None

    def species_since(self, hours: float = 24) -> list[tuple[str, int]]:
        return [(name, int(n)) for name, n in self._rows(f"species_since:{float(hours)!r}")]

    def last_heard(self, hours: float = 24) -> list[str]:
        return list(self._rows(f"last_heard:{float(hours)!r}"))

    def life_list(self) -> list[Species]:
        return [Species(name, datetime.fromisoformat(at)) for name, at in self._rows("life_list")]

    def stats(self) -> dict:
        return {}

    def close(self) -> None:
        """Nothing to release."""


def _name_of(row: Any) -> str:
    if isinstance(row, str):
        return row
    if len(row) == 4:  # a detection
        return str(row[2])
    return str(row[0])


# -- names -------------------------------------------------------------------


def _heard(calls: dict[str, Any]) -> set[str]:
    """Every species a job's answers mention."""
    return {_name_of(row) for rows in calls.values() for row in rows or []}


_names_lock = threading.Lock()


def _label_names(code: str, store: assets.Store, species: set[str], wait: float) -> dict:
    """A language's names from BirdNET's label list, under the scientific name as
    the list has it and as the detector reports it now (`names.canonical`)."""
    if code == ENGLISH:
        return {name: common for name in species if (common := taxa.common_of(name))}
    path = store.labels(code, wait=wait)
    if path is None:
        return {}
    found: dict[str, str] = {}
    with contextlib.suppress(OSError, UnicodeDecodeError):
        for line in path.read_text(encoding="utf-8").splitlines():
            sci, _, common = line.partition("_")
            sci, common = sci.strip(), common.strip()
            if not common or common.lower() == sci.lower():
                continue
            for key in (sci, names.canonical(sci)):
                if key in species:
                    found.setdefault(key, common)
    return found


def language_codes(choice: str, source: Any) -> str:
    """The Namer's code for an option's language. The detector's own language is
    Fugleramme's STATION on a BirdNET-Go, and English on BirdWeather (each
    species' own commonName) and in the demo garden."""
    if choice == COMMON:
        return STATION if isinstance(source, ApiSource) else ENGLISH
    return choice


def names_for(
    primary: str, secondary: str, source: Any, species: set[str], store: assets.Store, wait: float
) -> tuple[dict[str, dict[str, str]], str]:
    """{language: {scientific name: common name}} for the languages a page reads,
    and the station's locale when the page is in it.

    The detector names a bird as Fugleramme asks it to (`languages.dictionary`:
    BirdNET-Go's dictionary for the language, its species list in its own
    language, or BirdWeather's names). Tesserae: BirdNET's label list in the same
    language fills in a bird the detector has no name for, and names the demo
    garden, where Fugleramme would show the scientific name."""
    real = not isinstance(source, DemoSource)
    table: dict[str, dict[str, str]] = {}
    locale = ""
    with _names_lock:
        languages.use(source if real else None)
        try:
            for code in dict.fromkeys((primary, secondary)):
                if code in (SCIENTIFIC, NONE):
                    continue
                heard: dict[str, str] = {}
                if real:
                    said, _version = languages.dictionary(code, store.data_dir)
                    heard = {names.canonical(sci): common for sci, common in said.items()}
                    heard.update(said)
                filled = {} if code == STATION else _label_names(code, store, species, wait)
                table[code] = {
                    name: common
                    for name in species
                    if (common := heard.get(name) or filled.get(name))
                }
            if primary == STATION:
                locale = languages.station_locale()
        finally:
            languages.use(None)
    return table, locale


def namer_for(job: dict[str, Any]) -> Namer:
    table = job["table"]
    digest = hashlib.blake2b(json.dumps(table, sort_keys=True).encode(), digest_size=6).hexdigest()
    return Namer(
        job["primary"],
        job["secondary"],
        table,
        ((job["primary"], job["secondary"], job["locale"], digest),),
        job["locale"],
    )


def context_of(job: dict[str, Any], source: Any, store: assets.Store, size=(0, 0)):
    """Fugleramme's mode Context for a job, as `modes.context` builds it from the
    settings file."""
    keyed = job["show_names"] and job["name_key"]
    limit = job["species_limit"]
    return modes.Context(
        mode=job["mode"],
        source=source,
        images_dir=store.artwork,
        style=assets.STYLE,
        day=job["day"],
        namer=namer_for(job),
        resolution=tuple(size),
        show_names=job["show_names"],
        name_key=keyed,
        lookback_hours=job["lookback_hours"],
        font_key=job["font"],
        label_size=job["label_size"],
        species_limit=modes._key_limit(limit) if keyed else limit,
        ranking=job["ranking"],
        layout=job["layout"],
        spotlight=job["spotlight"],
        margin=Edges.even(job["margin"] / 100),
        textured=job["textured"],
    )


def _subjects(ctx: Any) -> list[str]:
    """The species the page draws, before anything is fetched."""
    if ctx.mode == "collage":
        return modes._selected(ctx)
    if ctx.mode == "latest":
        holder = modes._holder(ctx)
        return [holder[0]] if holder else []
    arrival = modes._arrival(ctx)
    return [arrival.scientific_name] if arrival else []


def _art(ctx: Any, species: list[str], store: assets.Store) -> dict[str, str]:
    """Each species' plate for the day, as a path under assets/."""
    found: dict[str, str] = {}
    for name in species:
        path = names.image_for(name, ctx.images_dir, ctx.style, ctx.day)
        if path is not None:
            found[name] = store.rel(path)
    return found


def _perch(ctx: Any, store: assets.Store) -> str | None:
    perches = ctx.perches()
    return store.rel(perches[ctx.day % len(perches)]) if perches else None


def _font_files(
    job: dict[str, Any], labels: list[str], store: assets.Store, wait: float
) -> list[str]:
    """The label face, and Fugleramme's fallback faces when a line has a
    character the face lacks (fetched only then: they are 2.4 MB together)."""
    if not job["show_names"]:
        return []
    _label, main = fonts.FONTS.get(job["font"], fonts.FONTS[fonts.DEFAULT_FONT])
    files = [f"fonts/{main}"]
    store.ensure(files, wait=wait)
    lines = [line for label in labels for line in label.split("\n") if line]
    if store.have(files[0]):
        short = any(not fonts._covers(str(store.path(files[0])), line) for line in lines)
    else:  # until the face is here, anything past Latin is taken to need a fallback
        short = any(ord(c) >= 0x250 for line in lines for c in line)
    if short:
        files += [f"fonts/{f.file}" for f in fonts.FALLBACKS]
    return files


def plan(opts: Options, source: Any, store: assets.Store, wait: float = 0.0) -> dict[str, Any]:
    """The job for a cell: what Fugleramme's mode asked the detector, and what
    the page needs fetched. Raises the source's Unavailable."""
    fonts.FONTS_DIR = store.fonts
    recorder = Recorder(source)
    job: dict[str, Any] = {
        "v": RENDER_VERSION,
        "pin": assets.PIN,
        "mode": opts.mode,
        "day": day_ordinal(),
        "show_names": opts.show_names,
        "name_key": opts.name_key,
        "names": opts.names,
        "second": opts.second if opts.second != opts.names else NONE,
        "lookback_hours": opts.lookback_hours,
        "font": opts.font,
        "label_size": opts.label_size,
        "species_limit": opts.species_limit,
        "ranking": opts.ranking,
        "layout": opts.layout,
        "spotlight": opts.spotlight,
        "margin": opts.margin,
        "textured": opts.textured,
        "table": {},
        "primary": language_codes(opts.names, source),
        "secondary": NONE,
        "locale": "",
        "demo": opts.demo,
    }
    second = language_codes(opts.second, source)
    job["secondary"] = NONE if second == job["primary"] else second
    ctx = context_of(job, recorder, store)
    # Everything the page will ask the detector, asked now: the mode's own key
    # and the species it is about read the same answers its render does.
    species = _subjects(ctx)
    modes.state_key(ctx)
    job["table"], job["locale"] = names_for(
        job["primary"], job["secondary"], source, _heard(recorder.calls), store, LABEL_WAIT_S
    )
    ctx = replace(ctx, namer=namer_for(job))
    job["calls"] = recorder.calls
    art = _art(ctx, species, store)
    job["art"] = art
    job["perch"] = None if art else _perch(ctx, store)
    # The plates start first, then the faces; nothing is waited for beyond
    # `wait` (none in the widget: the page draws with what has arrived).
    plates = [*art.values(), *([job["perch"]] if job["perch"] else [])]
    store.ensure(plates)
    deadline = time.monotonic() + wait
    labels = [ctx.namer.label(name) for name in species]
    # The single-plate pages write a date in the page's language too.
    if job["mode"] != "collage" and species:
        now = datetime.now().astimezone()
        labels += [ctx.namer.moment(now), ctx.namer.date(now)]
    job["fonts"] = _font_files(job, labels, store, wait)
    store.ensure([*job["fonts"], *plates], wait=max(0.0, deadline - time.monotonic()))
    job["key"] = _key(modes.state_key(ctx), job)
    job["credit"] = credit(art.values(), store)
    return job


def _key(state: tuple, job: dict[str, Any]) -> str:
    """Fugleramme's state key, with what a Tesserae page adds to it."""
    extra = (job["textured"], job["margin"], job["pin"], RENDER_VERSION)
    return hashlib.blake2b(repr((state, extra)).encode(), digest_size=10).hexdigest()


def credit(paths: Any, store: assets.Store) -> dict[str, Any]:
    """Who the plates on a page are after, for the credit line."""
    keys: list[str] = []
    for rel in paths:
        source = names.source_of(store.path(rel))
        if source and source not in keys:
            keys.append(source)
    artists = [ARTISTS.get(key, key.title()) for key in keys]
    return {"artists": artists, "licence": LICENCE, "from": "Fugleramme"}


def credit_line(artists: list[str]) -> str:
    """One line for the foot of the page."""
    if not artists:
        return f"Plates from Fugleramme, {LICENCE}"
    shown = artists[:3]
    if len(artists) > 3:
        names_ = ", ".join(shown) + " and others"
    elif len(shown) > 1:
        names_ = ", ".join(shown[:-1]) + " and " + shown[-1]
    else:
        names_ = shown[0]
    return f"Plates after {names_}, from Fugleramme, {LICENCE}"


# -- jobs on disk --------------------------------------------------------------

_KEY = re.compile(r"[0-9a-f]{20}")
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_JOBS_KEPT = 200


def valid_key(key: str) -> bool:
    return bool(_KEY.fullmatch(key or ""))


def save(job: dict[str, Any], store: assets.Store) -> None:
    with _jobs_lock:
        _jobs[job["key"]] = job
        if len(_jobs) > _JOBS_KEPT:
            for old in list(_jobs)[: len(_jobs) - _JOBS_KEPT]:
                _jobs.pop(old, None)
    path = store.jobs / f"{job['key']}.json"
    if path.exists():
        return
    with contextlib.suppress(OSError):
        store.jobs.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job), encoding="utf-8")
        tmp.replace(path)
        _prune(store.jobs, "*.json", _JOBS_KEPT)


def load(key: str, store: assets.Store) -> dict[str, Any] | None:
    if not valid_key(key):
        return None
    with _jobs_lock:
        held = _jobs.get(key)
    if held is not None:
        return held
    try:
        return json.loads((store.jobs / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _prune(folder: Path, pattern: str, keep: int) -> None:
    with contextlib.suppress(OSError):
        files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            old.unlink(missing_ok=True)


# -- drawing -------------------------------------------------------------------


def paper_of(textured: bool) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(TARGET_PAPER if textured else PANEL_PAPER))


def draw(job: dict[str, Any], size: tuple[int, int], store: assets.Store) -> Image.Image:
    """The page at `size`, by Fugleramme's own mode. A bird whose plate is
    missing is left off; a page that cannot be drawn at all is bare paper."""
    fonts.FONTS_DIR = store.fonts
    dropped: set[str] = {name for name, rel in job["art"].items() if not store.have(rel)}
    for _ in range(4):
        ctx = context_of(job, Replay(job["calls"], frozenset(dropped)), store, size)
        missing = {
            name for name, rel in _art(ctx, _subjects(ctx), store).items() if not store.have(rel)
        }
        if not missing:
            break
        dropped |= missing
    stand_in = any(not store.have(rel) for rel in job["fonts"])
    try:
        image = modes.render(ctx).convert("RGB")
        if stand_in:
            # The packing reserved room for names set in a stand-in face; the
            # real face measures differently, so it must not reuse it.
            with collage._layouts_lock:
                collage._layouts.clear()
        return image
    except (OSError, ValueError) as error:
        log.warning("Fugleramme page could not be drawn: %s", error)
        return blank(size, job["textured"])
