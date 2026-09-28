"""Notes and measures tools for MuseScore MCP."""

from typing import List, Optional
from ..client import MuseScoreClient
from .navigation import position_params

POSITION_ARGS_DOC = """
            staff: Staff index to write on (0-based). Default: the cursor's staff. The choice sticks:
                later calls without staff keep writing there.
            voice: Voice 0-3 (0 is MuseScore's voice 1). Default: the cursor's voice. Also sticks.
            measure: Write at the start of this measure (1-based) instead of at the cursor.
            tick: Write at this absolute tick (480 per quarter note) instead of at the cursor."""


def with_position_doc(func):
    """Append the shared staff/voice/measure/tick argument docs to a tool's docstring."""
    func.__doc__ = (func.__doc__ or "").rstrip() + POSITION_ARGS_DOC
    return func


def setup_notes_measures_tools(mcp, client: MuseScoreClient):
    """Setup notes and measures tools."""

    @mcp.tool()
    @with_position_doc
    async def add_note(
        pitch: int = 64,
        duration: dict = {"numerator": 1, "denominator": 4},
        advance_cursor_after_action: bool = True,
        add_to_chord: bool = False,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add a note at the cursor (or at the given position) with the specified pitch and duration.

        Sequential notes write a melody. Set add_to_chord=True to stack a pitch on the chord that
        was just written (e.g. add_note(60), add_note(64, add_to_chord=True), add_note(67,
        add_to_chord=True) writes a C major triad, and the next plain add_note continues after it).
        Measures are appended automatically when writing past the end of the score.

        Args:
            pitch: MIDI pitch value (0-127, where 60 is middle C)
            duration: Duration as {"numerator": int, "denominator": int} (e.g., {"numerator": 1, "denominator": 4} for quarter note)
            advance_cursor_after_action: Whether to move cursor to next position after adding note
            add_to_chord: If True, add this pitch to the last written chord instead of writing the next melody note"""
        params = {
            "pitch": pitch,
            "duration": duration,
            "advanceCursorAfterAction": advance_cursor_after_action,
            "addToChord": add_to_chord,
        }
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addNote", params)

    @mcp.tool()
    @with_position_doc
    async def add_rest(
        duration: dict = {"numerator": 1, "denominator": 4},
        advance_cursor_after_action: bool = True,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add a rest at the cursor (or at the given position).

        Args:
            duration: Duration as {"numerator": int, "denominator": int} (e.g., {"numerator": 1, "denominator": 4} for quarter rest)
            advance_cursor_after_action: Whether to move cursor to next position after adding rest"""
        params = {
            "duration": duration,
            "advanceCursorAfterAction": advance_cursor_after_action,
        }
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addRest", params)

    @mcp.tool()
    @with_position_doc
    async def add_tuplet(
        duration: dict = {"numerator": 1, "denominator": 4},
        ratio: dict = {"numerator": 3, "denominator": 2},
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
            duration: Total duration of the tuplet as {"numerator": int, "denominator": int}
            ratio: Tuplet ratio as {"numerator": int, "denominator": int} (e.g., {"numerator": 3, "denominator": 2} for triplet)
            advance_cursor_after_action: If True, move the cursor past the whole tuplet"""
        params = {
            "duration": duration,
            "ratio": ratio,
            "advanceCursorAfterAction": advance_cursor_after_action,
        }
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addTuplet", params)

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
