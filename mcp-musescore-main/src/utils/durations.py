"""Note durations: parsing, validation and splitting into writable note values.

Durations are fractions of a whole note: "1/4" is a quarter, "3/8" a dotted quarter.
MuseScore can only write one plain, dotted or double-dotted value per note, and
silently shortens anything else (5/8 becomes a half note), so a longer or odd
duration is split into such values and the pieces are tied together:

  1. at every barline it crosses,
  2. then greedily into the longest value that still fits (5/8 -> 1/2 + 1/8).

Anything that can't be written that way (1/12 needs a triplet) is an error.
The plugin (musescore-mcp-websocket.qml) implements the same rules; the tests
check both against each other.
"""

import re
from fractions import Fraction
from typing import Any, List, Sequence, Tuple

TICKS_PER_WHOLE = 1920          # 480 per quarter note, as in the plugin
MIN_DURATION = Fraction(1, 128)  # shortest writable value (15 ticks)

# Plain, dotted and double-dotted values from a double-dotted breve down to a
# 128th, in ticks, longest first. Only values that are whole numbers of ticks.
NOTE_VALUE_TICKS: Tuple[int, ...] = tuple(sorted(
    {
        base * mult // 4
        for base in (3840, 1920, 960, 480, 240, 120, 60, 30, 15)
        for mult in (4, 6, 7)       # plain, dotted (x1.5), double-dotted (x1.75)
        if base * mult % 4 == 0
    },
    reverse=True,
))

_FRACTION_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class DurationError(ValueError):
    """A duration that is malformed or can't be written with plain/dotted notes."""


def parse_duration(value: Any, label: str = "duration") -> Fraction:
    """Parse "n/d" or {"numerator": n, "denominator": d} into a positive Fraction."""
    if isinstance(value, Fraction):
        frac = value
    elif isinstance(value, str):
        m = _FRACTION_RE.match(value)
        if not m:
            raise DurationError(f'{label} must look like "1/4" (numerator/denominator), got {value!r}')
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            raise DurationError(f"{label} {value!r} has a zero denominator")
        frac = Fraction(num, den)
    elif isinstance(value, dict):
        if set(value) != {"numerator", "denominator"}:
            raise DurationError(f'{label} object must have exactly "numerator" and "denominator", got {sorted(value)}')
        num, den = value["numerator"], value["denominator"]
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (num, den)):
            raise DurationError(f"{label} numerator and denominator must be integers, got {value!r}")
        if den == 0:
            raise DurationError(f"{label} {num}/{den} has a zero denominator")
        frac = Fraction(num, den)
    else:
        raise DurationError(f'{label} must be "n/d" or {{"numerator": n, "denominator": d}}, got {value!r}')
    if frac <= 0:
        raise DurationError(f"{label} must be greater than zero, got {frac}")
    return frac


def duration_ticks(value: Any, label: str = "duration") -> int:
    """Ticks of a duration that can be written as plain/dotted notes tied together.

    Raises DurationError for durations that need a tuplet (1/12, 1/5, ...) or are
    shorter than a 128th.
    """
    frac = parse_duration(value, label)
    den = frac.denominator
    if den & (den - 1):
        raise DurationError(
            f"{label} {frac} can't be written with plain, dotted or double-dotted notes: it needs a "
            f"tuplet (e.g. 1/12 is an eighth-note triplet). Use add_tuplet and fill it with add_note."
        )
    if frac % MIN_DURATION:
        raise DurationError(f"{label} {frac} is not a multiple of 1/128, the shortest supported value")
    return int(frac * TICKS_PER_WHOLE)


def ticks_to_text(ticks: int) -> str:
    """Ticks -> reduced "n/d" text (480 -> "1/4")."""
    frac = Fraction(ticks, TICKS_PER_WHOLE)
    return f"{frac.numerator}/{frac.denominator}"


def normalize_duration(value: Any, label: str = "duration") -> str:
    """Validate a duration and return it as reduced "n/d" text, the wire format."""
    return ticks_to_text(duration_ticks(value, label))


def is_single_value(ticks: int) -> bool:
    """True if ticks is one plain, dotted or double-dotted note value."""
    return ticks in NOTE_VALUE_TICKS


def split_value(ticks: int) -> List[int]:
    """Greedy split of a length (within one bar) into writable note values."""
    if ticks <= 0 or ticks % NOTE_VALUE_TICKS[-1]:
        raise DurationError(f"{ticks} ticks is not a positive multiple of 1/128")
    pieces = []
    rest = ticks
    while rest:
        value = next(v for v in NOTE_VALUE_TICKS if v <= rest)
        pieces.append(value)
        rest -= value
    return pieces


def plan_pieces(start: int, length: int, bars: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """(tick, ticks) of the notes that write [start, start + length).

    bars are (startTick, endTick) of consecutive bars covering the range. The
    length is split at every barline first, then each part greedily.
    """
    end = start + length
    pieces: List[Tuple[int, int]] = []
    pos = start
    for bar_start, bar_end in bars:
        if bar_end <= pos or bar_start >= end:
            continue
        if bar_start > pos:
            raise DurationError(f"bars leave a gap at tick {pos}")
        part_end = min(bar_end, end)
        for value in split_value(part_end - pos):
            pieces.append((pos, value))
            pos += value
        if pos >= end:
            break
    if pos != end:
        raise DurationError(f"bars end at tick {pos}, before the duration ends at {end}")
    return pieces
