# Vendored from Fugleramme v0.29.0 (https://github.com/arnegiacomo/fugleramme).
# Copyright (c) 2026 Arne Giacomo Munthe-Kaas. MIT License, see LICENSE.
# Tesserae: trimmed to the Namer and its dates; see the module docstring.
"""Species names in the reader's language.

Tesserae: trimmed to the `Namer` and the dates it writes. Fugleramme reads its
name dictionaries from BirdNET-Go's API and BirdWeather's translations; the
widget hands the `Namer` the common names its detector already sent with the
detections, and the English names in BirdNET's own label list, instead
(glue.plan). babel is optional here as it is upstream: without it, dates are
written in figures.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

log = logging.getLogger(__name__)

# Pseudo-language: the scientific name, the only one needing no BirdNET-Go.
SCIENTIFIC = "sci"
NONE = ""  # secondary language unset
# Pseudo-language: BirdNET-Go's own species language (birdnet.locale).
STATION = "station"


def station_locale() -> str:
    """Tesserae: no station language is read, so there is none to name."""
    return ""


try:
    from babel import Locale
    from babel.core import UnknownLocaleError
    from babel.dates import format_date, format_skeleton, format_time
except ImportError:  # a half-finished self-update: numeric dates still read fine
    Locale = format_date = format_skeleton = format_time = None  # type: ignore[assignment,misc]
    UnknownLocaleError = ValueError  # type: ignore[assignment,misc]


# English puts a narrow no-break space before AM/PM. Five of the seven label
# faces have no glyph for one and draw a box instead.
_PLAIN_SPACES = str.maketrans({"\u202f": " ", "\u00a0": " "})


def english_name(locale: str) -> str:
    """English name: "Arabic" for "ar", "Portuguese" for "pt-br", "" if babel lacks it."""
    if not locale or Locale is None:
        return ""
    try:
        return str(Locale.parse(locale.split("-")[0]).english_name or "")
    except (UnknownLocaleError, ValueError):
        return ""


def _spelled(code: str, local: datetime, clock: bool) -> str | None:
    """The date in the language's own words, or None when babel cannot."""
    if code in (SCIENTIFIC, NONE) or format_date is None:
        return None
    locale = code.replace("-", "_")  # babel's spelling of "pt-br"
    try:
        if not clock:
            # "long" keeps the month spelled out; asking for day-month-year
            # shortens it to "16. aug. 2026".
            return format_date(local, "long", locale=locale)
        day = str(format_skeleton("MMMMd", local, locale=locale))
        # Some languages put a comma between the day and the time, some a space.
        join = str(Locale.parse(locale).datetime_formats["short"])
        return join.format(format_time(local, "short", locale=locale), day)
    except (UnknownLocaleError, ValueError, KeyError):
        log.warning("No date format for %s; using numeric", code)
        return None


def _written(code: str, when: datetime, clock: bool) -> str:
    """A date the way the language writes it - more than the month's name, since
    Hungarian and Latvian put the year first and Spanish joins with "de"."""
    written = _spelled(code, when.astimezone(), clock) or numeric(when, clock)
    return written.translate(_PLAIN_SPACES)


def numeric(when: datetime, clock: bool) -> str:
    """A date in figures alone, which any face can set. Tesserae: the day is
    written by hand, since Windows' strftime has no "%-d"."""
    local = when.astimezone()
    return f"{local.day}." + local.strftime("%m %H:%M" if clock else "%m.%Y")


def _capitalized(name: str) -> str:
    """Norwegian names come lowercase, English titled; a standalone label reads
    better capitalized, and a mixed dictionary evenly."""
    return name[:1].upper() + name[1:]


# (language code, text) -> whether the page can draw it; a name it cannot falls
# back to the scientific one, a date to `numeric`.
Drawable = Callable[[str, str], bool]


def _anything(_code: str, _text: str) -> bool:
    return True


class Namer:
    """Renders a scientific name into the configured language(s). Built by `namer`."""

    def __init__(
        self,
        primary: str,
        secondary: str,
        names: dict[str, dict[str, str]],
        version: tuple,
        station_locale: str = "",
    ):
        self.primary = primary
        self.secondary = secondary
        self._names = names
        # STATION's dates follow birdnet.locale ("pt-br" as babel's "pt"), and are
        # numeric while it is unknown.
        self._dates = station_locale.split("-")[0] if primary == STATION else primary
        # Cache key: the names change with the dictionaries, not just the setting.
        self.key = (primary, secondary, version)

    def _one(self, code: str, scientific: str, drawable: Drawable) -> str:
        if code in (SCIENTIFIC, NONE):
            return scientific
        common = self._names.get(code, {}).get(scientific)
        name = _capitalized(common) if common else scientific
        return name if drawable(code, name) else scientific

    def parts(self, scientific: str, drawable: Drawable = _anything) -> tuple[str, ...]:
        """The primary name, plus the second language's when it differs."""
        primary = self._one(self.primary, scientific, drawable)
        if self.secondary == NONE:
            return (primary,)
        secondary = self._one(self.secondary, scientific, drawable)
        return (primary,) if secondary == primary else (primary, secondary)

    def label(self, scientific: str, drawable: Drawable = _anything) -> str:
        """Collage label: the second language on its own line, in parentheses."""
        parts = self.parts(scientific, drawable)
        return parts[0] if len(parts) == 1 else f"{parts[0]}\n({parts[1]})"

    def inline(self, scientific: str) -> str:
        """One line, for the admin listings."""
        return self.label(scientific).replace("\n", " ")

    def date(self, when: datetime, drawable: Drawable = _anything) -> str:
        """A day, with its year: the newest arrival's can be months back."""
        return self._dated(when, False, drawable)

    def moment(self, when: datetime, drawable: Drawable = _anything) -> str:
        """A day and a clock time, for the bird holding the page now."""
        return self._dated(when, True, drawable)

    def _dated(self, when: datetime, clock: bool, drawable: Drawable) -> str:
        written = _written(self._dates, when, clock)
        return written if drawable(self._dates, written) else numeric(when, clock)
