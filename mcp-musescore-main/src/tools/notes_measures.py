"""Notes and measures tools for MuseScore MCP."""

from fractions import Fraction
from typing import List, Optional

from ..client import MuseScoreClient
from ..types import Duration, DurationObject, VoiceEvent
from ..utils.durations import DurationError, normalize_duration, parse_duration
from ..validation import normalize_voice_events
from .navigation import position_params

POSITION_ARGS_DOC = """
            staff: Staff index to write on (0-based). Default: the cursor's staff. The choice sticks:
                later calls without staff keep writing there.
            voice: Voice 0-3 (0 is MuseScore's voice 1). Default: the cursor's voice. Also sticks.
            measure: Write at the start of this measure (1-based) instead of at the cursor.
            tick: Write at this absolute tick (480 per quarter note) instead of at the cursor."""


DURATION_DOC = """
            Durations are fractions of a whole note: "1/4" quarter, "3/8" dotted quarter, "1/16" 16th
            (or {"numerator": 1, "denominator": 4}). A duration that isn't one plain, dotted or
            double-dotted value, or that crosses a barline, is written as several notes tied together
            (5/8 -> 1/2 tied to 1/8); the result lists these splits. Durations needing a tuplet (1/12)
            are an error. MuseScore never gets a duration it would shorten."""


def with_position_doc(func):
    """Append the shared staff/voice/measure/tick argument docs to a tool's docstring."""
    func.__doc__ = (func.__doc__ or "").rstrip() + POSITION_ARGS_DOC
    if func.__name__ in ("add_note", "add_rest", "write_voice"):
        func.__doc__ += DURATION_DOC
    return func


def wire_duration(value, label: str = "duration") -> str:
    """Validate a writable duration and return it as "n/d" text for the plugin."""
    try:
        return normalize_duration(value, label)
    except DurationError as e:
        raise ValueError(str(e)) from None


def duration_object(value, label: str = "duration") -> dict:
    """A positive duration as {"numerator", "denominator"} (tuplets take any fraction)."""
    try:
        frac: Fraction = parse_duration(value, label)
    except DurationError as e:
        raise ValueError(str(e)) from None
    return {"numerator": frac.numerator, "denominator": frac.denominator}


def setup_notes_measures_tools(mcp, client: MuseScoreClient):
    """Setup notes and measures tools."""

    @mcp.tool()
    @with_position_doc
    async def add_note(
        pitch: int = 64,
        duration: Optional[Duration] = None,
        advance_cursor_after_action: bool = True,
        add_to_chord: bool = False,
        tie: bool = False,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add a note at the cursor (or at the given position) with the specified pitch and duration.

        For whole passages use write_voice instead: one call, one undo step. add_note is for small
        corrections.

        Sequential notes write a melody. Set add_to_chord=True to stack a pitch on the chord that
        was just written (e.g. add_note(60), add_note(64, add_to_chord=True), add_note(67,
        add_to_chord=True) writes a C major triad, and the next plain add_note continues after it).
        Measures are appended automatically when writing past the end of the score.

        Args:
            pitch: MIDI pitch value (0-127, where 60 is middle C)
            duration: Default "1/4". With add_to_chord, omit it (the pitch takes the chord's duration).
            advance_cursor_after_action: Whether to move cursor to next position after adding note
            add_to_chord: If True, add this pitch to the last written chord instead of writing the next melody note
            tie: Tie this note to the next note of the same pitch in the same staff and voice. That
                next note must already exist when the call ends; inside processSequence(atomic=True)
                it may be written by a later step. Otherwise nothing is written and you get an error.
                To write tied notes from scratch, use write_voice."""
        params = {
            "pitch": pitch,
            "advanceCursorAfterAction": advance_cursor_after_action,
            "addToChord": add_to_chord,
        }
        if duration is not None:
            params["duration"] = wire_duration(duration)
        elif not add_to_chord:
            params["duration"] = "1/4"
        if tie:
            params["tie"] = True
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addNote", params)

    @mcp.tool()
    @with_position_doc
    async def add_rest(
        duration: Duration = "1/4",
        advance_cursor_after_action: bool = True,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add a rest at the cursor (or at the given position).

        Args:
            duration: e.g. "1/4" for a quarter rest
            advance_cursor_after_action: Whether to move cursor to next position after adding rest"""
        params = {
            "duration": wire_duration(duration),
            "advanceCursorAfterAction": advance_cursor_after_action,
        }
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addRest", params)

    @mcp.tool()
    @with_position_doc
    async def add_tuplet(
        duration: Duration = "1/4",
        ratio: DurationObject = {"numerator": 3, "denominator": 2},
        advance_cursor_after_action: bool = False,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Create an empty tuplet (filled with rests) at the cursor.

        By default the cursor stays at the start of the tuplet, so the following add_note calls
        fill it: for an eighth-note triplet use duration 1/4, ratio 3/2, then three add_note calls
        with duration 1/8.

        Args:
            duration: Total duration of the tuplet, e.g. "1/4"
            ratio: Tuplet ratio as {"numerator": int, "denominator": int} (e.g., {"numerator": 3, "denominator": 2} for triplet)
            advance_cursor_after_action: If True, move the cursor past the whole tuplet"""
        params = {
            "duration": duration_object(duration),
            "ratio": duration_object(ratio, "ratio"),
            "advanceCursorAfterAction": advance_cursor_after_action,
        }
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addTuplet", params)

    @mcp.tool()
    @with_position_doc
    async def write_voice(
        events: List[VoiceEvent],
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Write a whole passage (notes, chords, rests, ties) into one staff and voice in ONE call.

        This is the main way to write music: the passage is one MuseScore undo step, it is checked
        before anything is written, and MuseScore's view is only updated once at the end. The
        events are written one after another from the start position (default: the cursor),
        overwriting what is there; bars are appended when writing past the end of the score. The
        cursor ends after the passage, so another write_voice continues where this one ended.

        events: a list of
            {"pitches": [60], "duration": "1/8"}                  a note
            {"pitches": [48, 52, 55], "duration": "1/2"}          a chord
            {"rest": true, "duration": "1/4"}                     a rest
            {"pitches": [65], "duration": "1/4", "tie": true}     tied to the next event
            {"pitches": [60, 64], "duration": "1/4", "tie": [60]} only C is tied (64 is re-struck)
        tie: true ties every pitch of the event to the same pitch in the next event, which must
        contain it. A tie on the last event ties into the note already written right after the
        passage (same staff and voice, same pitch), which must exist.

        Unknown fields are errors. Nothing is written if any event is invalid. The passage can't
        start inside a held note or overwrite tuplets yet (use add_tuplet + add_note for tuplets).
        Voices 1-3 may be empty: they are filled with rests as needed.

        Args:
            events: The notes, chords and rests, in order."""
        params = {"events": normalize_voice_events(events)}
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("writeVoice", params)

    @mcp.tool()
    @with_position_doc
    async def add_lyrics(
        lyrics: List[str],
        verse: int = 0,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add lyrics to consecutive notes starting from the cursor (or the given position).

        End a syllable with "-" when the word continues on the next note, so the syllables are
        hyphenated: ["Twin-", "kle", "twin-", "kle", "lit-", "tle", "star"]. Use "_" to leave a note
        without a syllable (melisma). Chords whose notes are all tied over from the previous chord are
        skipped (a chord where a new note starts still gets a syllable), and a lyric
        already on a note in the same verse is replaced (not duplicated).

        Args:
            lyrics: List of lyric syllables to add
            verse: Verse number (0-based, default is 0 for first verse)"""
        params = {"lyrics": lyrics, "verse": verse}
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addLyrics", params)

    @mcp.tool()
    async def insert_measure(measure: Optional[int] = None, count: int = 1):
        """Insert empty measures before the given measure (1-based), or before the cursor's measure.
        Each inserted measure is its own undo step."""
        params = position_params(measure=measure)
        params["count"] = count
        return await client.send_command("insertMeasure", params)

    @mcp.tool()
    async def append_measure(count: int = 1):
        """Append measures to the end of the score."""
        return await client.send_command("appendMeasure", {"count": count})

    @mcp.tool()
    async def delete_selection(measure: Optional[int] = None, staff: Optional[int] = None):
        """Delete the current selection (by default, the note/rest at the cursor).

        With measure (1-based), clears that whole measure instead: on the given staff only, or on
        all staves if staff is omitted. Notes become rests; the measure itself stays.
        """
        params = {}
        if measure is not None:
            params["measure"] = measure
        if staff is not None:
            params["staff"] = staff
        return await client.send_command("deleteSelection", params)

    @mcp.tool()
    async def undo(steps: int = 1):
        """Undo the last action(s), like Ctrl+Z in MuseScore. The cursor returns to where it was."""
        return await client.send_command("undo", {"steps": steps})
