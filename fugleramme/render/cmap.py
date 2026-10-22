# Tesserae addition, not part of Fugleramme. MIT License (see LICENSE).
"""Which characters a font file maps, read straight from its `cmap` table.

Fugleramme asks fontTools; Tesserae does not ship it and a widget cannot add a
pip dependency, so this reads the two subtable formats that matter for a label
(4, the Basic Multilingual Plane, and 12, everything) out of a TrueType, an
OpenType/CFF or the first face of a collection. None when the file cannot be
read, which `fonts` takes to mean "assume it covers the line" rather than
dropping the name.
"""

from __future__ import annotations

import struct
from pathlib import Path

_UNICODE_SUBTABLES = ((3, 10), (0, 6), (0, 4), (3, 1), (0, 3), (0, 2), (0, 1), (0, 0))


def _table(data: bytes, base: int, tag: bytes) -> int | None:
    count = struct.unpack_from(">H", data, base + 4)[0]
    for k in range(count):
        record = base + 12 + 16 * k
        if data[record : record + 4] == tag:
            return struct.unpack_from(">I", data, record + 8)[0]
    return None


def _format4(data: bytes, at: int) -> set[int]:
    segs = struct.unpack_from(">H", data, at + 6)[0] // 2
    ends_at = at + 14
    starts_at = ends_at + 2 * segs + 2
    deltas_at = starts_at + 2 * segs
    ranges_at = deltas_at + 2 * segs
    found: set[int] = set()
    for s in range(segs):
        end = struct.unpack_from(">H", data, ends_at + 2 * s)[0]
        start = struct.unpack_from(">H", data, starts_at + 2 * s)[0]
        delta = struct.unpack_from(">h", data, deltas_at + 2 * s)[0]
        offset = struct.unpack_from(">H", data, ranges_at + 2 * s)[0]
        for code in range(start, min(end, 0xFFFE) + 1):
            if offset == 0:
                glyph = (code + delta) & 0xFFFF
            else:
                where = ranges_at + 2 * s + offset + 2 * (code - start)
                if where + 2 > len(data):
                    continue
                glyph = struct.unpack_from(">H", data, where)[0]
                if glyph:
                    glyph = (glyph + delta) & 0xFFFF
            if glyph:
                found.add(code)
    return found


def _format12(data: bytes, at: int) -> set[int]:
    groups = struct.unpack_from(">I", data, at + 12)[0]
    found: set[int] = set()
    for g in range(groups):
        start, end, glyph = struct.unpack_from(">III", data, at + 16 + 12 * g)
        if glyph == 0:
            start += 1
        found.update(range(start, end + 1))
    return found


def codepoints(path: str | Path) -> frozenset[int] | None:
    """Every code point the font maps to a glyph, or None if it cannot be read."""
    try:
        data = Path(path).read_bytes()
        base = 0
        if data[:4] == b"ttcf":
            base = struct.unpack_from(">I", data, 12)[0]
        cmap = _table(data, base, b"cmap")
        if cmap is None:
            return None
        count = struct.unpack_from(">H", data, cmap + 2)[0]
        subtables = {}
        for k in range(count):
            platform, encoding, offset = struct.unpack_from(">HHI", data, cmap + 4 + 8 * k)
            subtables[(platform, encoding)] = cmap + offset
        for key in _UNICODE_SUBTABLES:
            at = subtables.get(key)
            if at is None:
                continue
            kind = struct.unpack_from(">H", data, at)[0]
            if kind == 12:
                return frozenset(_format12(data, at))
            if kind == 4:
                return frozenset(_format4(data, at))
        return None
    except (OSError, struct.error, ValueError, TypeError):
        return None
