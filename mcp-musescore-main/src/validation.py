"""Strict validation of tool arguments and write_voice events.

Unknown arguments must be errors, not silently ignored: if Claude sends tie=true
to a tool without a tie parameter, it has to learn that the feature doesn't exist.
"""

from typing import Any, Dict, List, Sequence

from pydantic import ConfigDict

from .utils.durations import DurationError, normalize_duration


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


def _pitch_list(value: Any, label: str) -> List[int]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list of MIDI pitches")
    for p in value:
        if not isinstance(p, int) or isinstance(p, bool) or not 0 <= p <= 127:
            raise ValueError(f"{label}: {p!r} is not a MIDI pitch 0-127")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} lists a pitch twice: {value}")
    return list(value)


def normalize_voice_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check write_voice events and return them in the plugin's wire format.

    Each event is a note/chord {"pitches": [...], "duration": ..., "tie": ...} or a
    rest {"rest": true, "duration": ...}. Durations become "n/d" text; tie=true
    becomes the explicit list of tied pitches. Raises ValueError naming the event.
    """
    if not events:
        raise ValueError("events is empty: give at least one note, chord or rest")

    out: List[Dict[str, Any]] = []
    for i, ev in enumerate(events):
        where = f"event {i}"
        unknown = set(ev) - {"pitches", "rest", "duration", "tie"}
        if unknown:
            raise ValueError(f"{where}: unknown field(s) {sorted(unknown)} (allowed: pitches, rest, duration, tie)")
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
            out.append({"rest": True, "duration": duration})
            continue

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
            missing = [p for p in tied if p not in pitches]
            if missing:
                raise ValueError(f"{where}: tie lists pitch(es) {missing} that are not in its pitches {pitches}")

        norm: Dict[str, Any] = {"pitches": pitches, "duration": duration}
        if tied:
            norm["tie"] = tied
        out.append(norm)

    # A tie continues into the next event, so that event must hold the same pitches.
    # A tie on the last event goes to the note already written after the passage;
    # the plugin checks that one.
    for i in range(len(out) - 1):
        tied = out[i].get("tie")
        if not tied:
            continue
        nxt = out[i + 1]
        if nxt.get("rest"):
            raise ValueError(f"event {i} is tied, but event {i + 1} is a rest")
        missing = [p for p in tied if p not in nxt["pitches"]]
        if missing:
            common = [p for p in tied if p in nxt["pitches"]]
            hint = f'use "tie": {common} to tie only the continuing pitches' if common else "remove the tie"
            raise ValueError(
                f"event {i} ties pitch(es) {missing}, which event {i + 1} doesn't contain "
                f"(its pitches are {nxt['pitches']}); {hint}"
            )
    return out
