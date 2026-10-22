"""A made-up garden, for a cell with no detector set.

Answers the same questions as a BirdNET-Go or a BirdWeather station (Fugleramme's
`source.Source`) from detections generated out of the hour, so every mode and
layout has something to show: a busy window for the collage, a run of calls for
the latest bird, and one species first heard a few hours ago for the newest
arrival and the newcomer's mark. The garden only changes on the hour, so a page
of it is drawn once an hour at most.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from ..fugleramme.source import Detection, Species

BASE_URL = "demo"

# Garden birds of northern Europe, as Fugleramme's own fake detector hears, the
# commonest first. Coloeus monedula is BirdNET's Corvus monedula, renamed.
SPECIES = (
    "Turdus merula",
    "Parus major",
    "Cyanistes caeruleus",
    "Passer domesticus",
    "Fringilla coelebs",
    "Erithacus rubecula",
    "Pica pica",
    "Sturnus vulgaris",
    "Columba palumbus",
    "Corvus cornix",
    "Troglodytes troglodytes",
    "Chloris chloris",
    "Carduelis carduelis",
    "Sitta europaea",
    "Dendrocopos major",
    "Aegithalos caudatus",
    "Pyrrhula pyrrhula",
    "Coloeus monedula",
    "Garrulus glandarius",
    "Turdus philomelos",
    "Prunella modularis",
    "Phylloscopus collybita",
    "Sylvia atricapilla",
    "Motacilla alba",
    "Regulus regulus",
    "Certhia familiaris",
    "Hirundo rustica",
    "Picus viridis",
    "Bombycilla garrulus",
    "Buteo buteo",
    "Strix aluco",
)
# First heard a few hours ago: the newest arrival, marked as new.
NEWCOMER = "Accipiter nisus"

_DETECTIONS = 1600
_NEWEST = timedelta(minutes=3)
_OLDEST = timedelta(days=90)


class DemoSource:
    """A `source.Source` over the made-up garden, fixed for the hour."""

    kind = "demo"

    def __init__(self, now: datetime | None = None):
        moment = (now or datetime.now(UTC)).astimezone(UTC)
        self.now = moment.replace(minute=0, second=0, microsecond=0)
        self.base_url = BASE_URL
        self.station = f"{BASE_URL}#{self.now.isoformat()}"
        self._rows = _generate(self.now)

    def latest(self) -> Detection | None:
        return self._rows[0] if self._rows else None

    def recent(self, limit: int = 20) -> list[Detection]:
        return self._rows[:limit]

    def _since(self, hours: float) -> list[Detection]:
        if hours <= 0:
            return self._rows
        cutoff = self.now - timedelta(hours=hours)
        return [row for row in self._rows if row.detected_at >= cutoff]

    def species_since(self, hours: float = 24) -> list[tuple[str, int]]:
        counts = Counter(row.scientific_name for row in self._since(hours))
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    def last_heard(self, hours: float = 24) -> list[str]:
        order: dict[str, int] = {}
        for row in self._since(hours):
            order.setdefault(row.scientific_name, len(order))
        return list(order)

    def life_list(self) -> list[Species]:
        first: dict[str, datetime] = {}
        for row in self._rows:  # newest first, so the last write is the earliest
            first[row.scientific_name] = row.detected_at
        return sorted(
            (Species(name, at) for name, at in first.items()),
            key=lambda s: (s.first_seen, s.scientific_name),
        )

    def stats(self) -> dict:
        ever = self.species_since(0)
        return {
            "total": sum(n for _name, n in ever),
            "species": len(ever),
            "last_24h": sum(n for _name, n in self.species_since(24)),
            "top": [{"scientific_name": name, "n": n, "best": None} for name, n in ever[:10]],
        }

    def close(self) -> None:
        """Nothing to release."""


def _generate(now: datetime) -> list[Detection]:
    """Detections newest first, spread geometrically from minutes to months back
    so every lookback has birds, the commoner species heard more often. The run
    of calls at the top is the bird holding the Latest bird page."""
    rng = random.Random(int(now.timestamp()) // 3600)
    weights = [1 / (rank + 2) ** 0.8 for rank in range(len(SPECIES))]
    span = _OLDEST / _NEWEST
    rows: list[Detection] = []
    holder = rng.choice(SPECIES[:8])
    for i in range(_DETECTIONS):
        at = now - _NEWEST * span ** (i / (_DETECTIONS - 1))
        name = holder if i < 4 else rng.choices(SPECIES, weights)[0]
        rows.append(Detection(_DETECTIONS - i, at, name, round(rng.uniform(0.7, 0.97), 2), None))
    # The newcomer: a handful of calls, all in the last few hours.
    for k in range(5):
        at = now - timedelta(minutes=40 + 37 * k)
        rows.append(Detection(_DETECTIONS + 1 + k, at, NEWCOMER, 0.9, None))
    return sorted(rows, key=lambda row: row.detected_at, reverse=True)
