"""Strict validation of tool arguments and write_voice events.

Unknown arguments must be errors, not silently ignored: if Claude sends tie=true
to a tool without a tie parameter, it has to learn that the feature doesn't exist.
"""

import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ConfigDict

from .notation import ARTICULATION_ALIASES, ARTICULATIONS, DYNAMICS, parse_notation, parse_pitch, pitch_midi
from .utils.durations import NOTE_VALUE_TICKS, DurationError, duration_ticks, normalize_duration, ticks_to_text


def forbid_unknown_tool_arguments() -> None:
    """Make every FastMCP tool registered from now on reject unknown arguments.

    FastMCP builds each tool's argument model from the function signature on top of
    ArgModelBase, which ignores extra keys. Swapping in a subclass with
    extra="forbid" makes unknown arguments a validation error (and adds
    "additionalProperties": false to the tool's input schema). Call this before
    the tools are registered.
    """
    from mcp.server.fastmcp.utilities import func_metadata

    base = func_metadata.ArgModelBase
    if base.model_config.get("extra") == "forbid":
        return

    class StrictArgModelBase(base):
        model_config = ConfigDict(**{**base.model_config, "extra": "forbid"})

    func_metadata.ArgModelBase = StrictArgModelBase


def _pitch_list(value: Any, label: str) -> List[Any]:
    """MIDI numbers and/or note names, checked; names in canonical form."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list of MIDI pitches or note names")
    out = [parse_pitch(p, label) for p in value]
    midis = [pitch_midi(p) for p in out]
    if len(set(midis)) != len(midis):
        raise ValueError(f"{label} lists a pitch twice: {value}")
    return out


MAX_VERSES = 20
_NOTE_KEYS = {"pitches", "rest", "duration", "tie", "dynamic", "articulations", "lyric", "text", "chord"}
_TUPLET_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


def _marks(ev: Dict[str, Any], where: str, is_rest: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if ev.get("dynamic") is not None:
        dyn = str(ev["dynamic"]).lower()
        if dyn not in DYNAMICS:
            raise ValueError(f"{where}: unknown dynamic {ev['dynamic']!r} (use {', '.join(DYNAMICS)})")
        out["dynamic"] = dyn
    if ev.get("articulations") is not None:
        arts = ev["articulations"]
        if not isinstance(arts, list):
            raise ValueError(f"{where}: articulations must be a list")
        names = []
        for a in arts:
            name = ARTICULATION_ALIASES.get(str(a).lower(), str(a).lower())
            if name != "fermata" and name not in ARTICULATIONS:
                raise ValueError(f"{where}: unknown articulation {a!r} (use fermata, {', '.join(ARTICULATIONS)})")
            if is_rest and name != "fermata":
                raise ValueError(f"{where}: a rest can only have a fermata, not {name}")
            names.append(name)
        out["articulations"] = names
    if ev.get("lyric") is not None:
        lyric = ev["lyric"]
        if is_rest:
            raise ValueError(f"{where}: a rest can't have a lyric")
        if isinstance(lyric, list):
            # one entry per verse (null: no syllable in that verse)
            if not 1 <= len(lyric) <= MAX_VERSES or not all(v is None or (isinstance(v, str) and v) for v in lyric) \
                    or not any(lyric):
                raise ValueError(f"{where}: lyric must be a non-empty string, or a list with one per verse "
                                 f"(up to {MAX_VERSES}; null or a string for each, at least one string)")
            out["lyric"] = lyric[0] if len(lyric) == 1 else list(lyric)
        elif isinstance(lyric, str) and lyric:
            out["lyric"] = lyric
        else:
            raise ValueError(f"{where}: lyric must be a non-empty string, or a list with one per verse")
    for key in ("text", "chord"):
        if ev.get(key) is not None:
            if not isinstance(ev[key], str) or not ev[key]:
                raise ValueError(f"{where}: {key} must be a non-empty string")
            out[key] = ev[key]
    return out


def _note_event(ev: Any, where: str) -> Dict[str, Any]:
    if not isinstance(ev, dict):
        raise ValueError(f"{where} must be an object")
    unknown = set(ev) - _NOTE_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {sorted(unknown)} (allowed: {', '.join(sorted(_NOTE_KEYS))}, "
                         f"or tuplet + events)")
    if "duration" not in ev:
        raise ValueError(f"{where}: missing duration")
    try:
        duration = normalize_duration(ev["duration"], f"{where} duration")
    except DurationError as e:
        raise ValueError(str(e)) from None

    rest = ev.get("rest")
    if rest is not None and not isinstance(rest, bool):
        raise ValueError(f"{where}: rest must be true or false")
    if rest:
        if "pitches" in ev:
            raise ValueError(f"{where}: a rest can't have pitches")
        if ev.get("tie") not in (None, False):
            raise ValueError(f"{where}: a rest can't be tied")
        out = {"rest": True, "duration": duration}
        out.update(_marks(ev, where, True))
        return out

    if "pitches" not in ev:
        raise ValueError(f'{where}: give "pitches" for a note/chord, or "rest": true for a rest')
    pitches = _pitch_list(ev["pitches"], f"{where} pitches")

    tie = ev.get("tie", False)
    if tie is True:
        tied = list(pitches)
    elif tie is False or tie is None:
        tied = []
    else:
        tied = _pitch_list(tie, f"{where} tie")
        have = [pitch_midi(p) for p in pitches]
        missing = [p for p in tied if pitch_midi(p) not in have]
        if missing:
            raise ValueError(f"{where}: tie lists pitch(es) {missing} that are not in its pitches {pitches}")

    norm: Dict[str, Any] = {"pitches": pitches, "duration": duration}
    if tied:
        norm["tie"] = tied
    norm.update(_marks(ev, where, False))
    return norm


def _tuplet_event(ev: Dict[str, Any], where: str) -> Dict[str, Any]:
    unknown = set(ev) - {"tuplet", "events"}
    if unknown:
        raise ValueError(f"{where}: a tuplet has only tuplet and events, not {sorted(unknown)}")
    m = _TUPLET_RE.match(str(ev["tuplet"]))
    if not m or int(m.group(1)) < 2 or int(m.group(2)) < 1 or m.group(1) == m.group(2):
        raise ValueError(f'{where}: tuplet must be a ratio like "3:2" (3 notes in the time of 2), got {ev["tuplet"]!r}')
    actual, normal = int(m.group(1)), int(m.group(2))
    inner = ev.get("events")
    if not isinstance(inner, list) or not inner:
        raise ValueError(f"{where}: a tuplet needs its notes in events")
    notes = []
    for k, e in enumerate(inner):
        if isinstance(e, dict) and "tuplet" in e:
            raise ValueError(f"{where}: tuplets can't be nested")
        notes.append(_note_event(e, f"{where} note {k}"))
    nominal = 0
    for k, n in enumerate(notes):
        ticks = duration_ticks(n["duration"])
        if ticks not in NOTE_VALUE_TICKS:
            raise ValueError(f"{where} note {k}: inside a tuplet each duration must be one plain or dotted value, got {n['duration']}")
        nominal += ticks
    base = Fraction(nominal, actual)
    if base.denominator != 1 or int(base) not in NOTE_VALUE_TICKS or int(base) * normal not in NOTE_VALUE_TICKS:
        raise ValueError(f"{where}: the notes of a {actual}:{normal} tuplet must add up to {actual} times one note value "
                         f"(e.g. three 1/8 in 3:2), got {ticks_to_text(nominal)}")
    return {"tuplet": f"{actual}:{normal}", "events": notes}


def normalize_voice_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check write_voice events and return them in the plugin's wire format.

    Each event is a note/chord {"pitches": [...], "duration": ..., "tie": ...}, a rest
    {"rest": true, "duration": ...}, or a tuplet {"tuplet": "3:2", "events": [...]}; notes
    and rests may carry markings (dynamic, articulations, lyric, text, chord).
    Durations become "n/d" text; tie=true becomes the explicit list of tied pitches.
    Raises ValueError naming the event.
    """
    if not events:
        raise ValueError("events is empty: give at least one note, chord or rest")

    out: List[Dict[str, Any]] = []
    for i, ev in enumerate(events):
        where = f"event {i}"
        if isinstance(ev, dict) and "tuplet" in ev:
            out.append(_tuplet_event(ev, where))
        else:
            out.append(_note_event(ev, where))

    # A tie continues into the next note (inside or after a tuplet), which must
    # hold the same pitches. A tie on the last one goes to the note already
    # written after the passage; the plugin checks that one.
    flat: List[Tuple[str, Dict[str, Any]]] = []
    for i, ev in enumerate(out):
        if "tuplet" in ev:
            flat.extend((f"event {i} (note {k} of the tuplet)", e) for k, e in enumerate(ev["events"]))
        else:
            flat.append((f"event {i}", ev))
    for j in range(len(flat) - 1):
        (name, ev), (next_name, nxt) = flat[j], flat[j + 1]
        tied = ev.get("tie")
        if not tied:
            continue
        if nxt.get("rest"):
            raise ValueError(f"{name} is tied, but {next_name} is a rest")
        have = [pitch_midi(p) for p in nxt["pitches"]]
        missing = [p for p in tied if pitch_midi(p) not in have]
        if missing:
            common = [p for p in tied if pitch_midi(p) in have]
            hint = f'use "tie": {common} to tie only the continuing pitches' if common else "remove the tie"
            raise ValueError(
                f"{name} ties pitch(es) {missing}, which {next_name} doesn't contain "
                f"(its pitches are {nxt['pitches']}); {hint}"
            )
    return out


def voice_events(events: Optional[Sequence[Dict[str, Any]]], notation: Optional[str], label: str = "") -> List[Dict[str, Any]]:
    """Events from exactly one of events (JSON) or notation (text), checked."""
    prefix = f"{label}: " if label else ""
    if (events is None) == (notation is None):
        raise ValueError(f"{prefix}give either events or notation (exactly one)")
    if notation is not None:
        return normalize_voice_events(parse_notation(notation))
    return normalize_voice_events(events)
