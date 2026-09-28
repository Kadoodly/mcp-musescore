"""Structure and expression editing tools: repeats, jumps, sections, keys,
tempo changes, slurs, hairpins, articulations, copying and deleting bars."""

from typing import Any, Dict, List, Literal, Optional

from ..client import MuseScoreClient
from ..types import Offset


def _params(**kwargs) -> Dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def setup_structure_edit_tools(mcp, client: MuseScoreClient):
    """Setup structure and expression editing tools."""

    @mcp.tool()
    async def copy_measures(
        start_measure: int,
        end_measure: int,
        to_measure: int,
        insert: bool = True,
        staff: Optional[int] = None,
        to_staff: Optional[int] = None,
        transpose: Optional[int] = None,
        expected_version: Optional[int] = None,
    ):
        """Copy bars start_measure..end_measure (1-based, inclusive) to to_measure, e.g. to write out a
        repeated chorus, double a melody on another instrument, or repeat a phrase a step higher.
        Copies everything in those bars: notes, lyrics, dynamics, articulations.

        Args:
            start_measure: First bar to copy.
            end_measure: Last bar to copy.
            to_measure: Bar where the copy starts. Use (number of bars + 1) to add it at the end.
            insert: True (default) inserts new bars for the copy, pushing later music back.
                False overwrites the bars at to_measure.
            staff: Copy only this staff (default: all staves).
            to_staff: Paste onto this staff instead (with staff: e.g. copy the right hand to a flute).
            transpose: Then transpose the copy by this many semitones (e.g. 12 = an octave up).
            expected_version: Refuse if the score changed since this version (see write_voice).

        Uses MuseScore's clipboard. Undo steps: one per inserted bar, one for the paste and one for
        the transposition (reported as undoSteps in the result).
        """
        params = _params(startMeasure=start_measure, endMeasure=end_measure, toMeasure=to_measure, insert=insert,
                         staff=staff, toStaff=to_staff, transpose=transpose, expectedVersion=expected_version)
        return await client.send_command("copyMeasures", params)

    @mcp.tool()
    async def delete_measures(start_measure: int, end_measure: Optional[int] = None, expected_version: Optional[int] = None):
        """Delete whole bars (the bars disappear, later music moves up). To only empty bars, use
        clear_range instead.

        Args:
            start_measure: First bar to delete (1-based).
            end_measure: Last bar to delete (inclusive). Default: just start_measure.
            expected_version: Refuse if the score changed since this version (see write_voice).
        """
        return await client.send_command("deleteMeasures", _params(startMeasure=start_measure, endMeasure=end_measure,
                                                                   expectedVersion=expected_version))

    @mcp.tool()
    async def add_repeat(start_measure: int, end_measure: int, times: int = 2):
        """Add repeat barlines: start repeat at start_measure, end repeat at end_measure.

        Args:
            start_measure: First bar of the repeated passage (1-based).
            end_measure: Last bar of the repeated passage.
            times: How many times the passage is played in total (default 2).
        """
        return await client.send_command("addRepeat", _params(startMeasure=start_measure, endMeasure=end_measure, times=times))

    @mcp.tool()
    async def remove_repeat(start_measure: int, end_measure: int):
        """Remove the start-repeat barline at start_measure and the end-repeat barline at end_measure."""
        return await client.send_command("removeRepeat", _params(startMeasure=start_measure, endMeasure=end_measure))

    @mcp.tool()
    async def add_marker(
        type: Literal["segno", "coda", "fine", "to coda", "varsegno", "varcoda"],
        measure: int,
    ):
        """Add a navigation marker to a bar: Segno or Coda (shown at the start of the bar),
        Fine or To Coda (shown at the end of the bar, where the music stops or jumps).

        Args:
            type: The marker.
            measure: The bar (1-based).
        """
        return await client.send_command("addMarker", {"type": type, "measure": measure})

    @mcp.tool()
    async def add_jump(
        type: Literal["D.C.", "D.C. al Fine", "D.C. al Coda", "D.S.", "D.S. al Fine", "D.S. al Coda"],
        measure: int,
    ):
        """Add a jump instruction at the end of a bar (D.C. = back to the start, D.S. = back to the Segno;
        "al Fine" stops at Fine, "al Coda" jumps from To Coda to the Coda). Add the matching markers
        with add_marker.

        Args:
            type: The jump.
            measure: The bar at whose end the jump happens (1-based).
        """
        return await client.send_command("addJump", {"type": type, "measure": measure})

    @mcp.tool()
    async def add_section_label(text: str, measure: Optional[int] = None, offset: Optional[Offset] = None,
                                tick: Optional[int] = None):
        """Add a section label (rehearsal mark) such as "Verse 1", "Chorus", "Bridge" or "A" above the
        score. Replaces a label already at that position.

        Args:
            text: The label.
            measure: Bar where the section starts (1-based). Default: the cursor position.
            offset: With measure: position inside the bar.
            tick: Exact position instead of a bar.
        """
        return await client.send_command("addRehearsalMark", _params(text=text, measure=measure, offset=offset, tick=tick))

    @mcp.tool()
    async def set_key_signature(
        fifths: int,
        measure: Optional[int] = None,
        mode: Optional[Literal["major", "minor"]] = None,
        staff: Optional[int] = None,
    ):
        """Change the key signature from a bar onwards.

        Args:
            fifths: Sharps (positive) or flats (negative), -7..7. E.g. 0 = C major/A minor,
                -2 = Bb major/G minor, 1 = G major/E minor, 3 = A major/F# minor.
            measure: First bar of the new key (1-based). Default: the cursor's bar.
            mode: Optionally mark the key as major or minor.
            staff: Only this staff (default: all staves). Transposing instruments get the same
                written key; check them afterwards.
        """
        return await client.send_command("setKeySignature", _params(fifths=fifths, measure=measure, mode=mode, staff=staff))

    @mcp.tool()
    async def add_tempo_change(
        type: Literal["rit.", "rall.", "accel.", "allarg.", "string.", "smorz.", "morendo"],
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
        end_measure: Optional[int] = None,
        end_tick: Optional[int] = None,
        target_bpm: Optional[float] = None,
        factor: Optional[float] = None,
        a_tempo: bool = False,
    ):
        """Gradual tempo change (rit., accel., ...) that also changes playback: the marking is shown at
        the start, and hidden tempo marks on the notes played inside the range (at most one per 8th in
        6/8, per quarter in 4/4) move the tempo smoothly, reaching the target on the last of them.
        Tempo marks already inside the range are replaced. One undo step.

        Args:
            type: The kind of change.
            measure: Bar where it starts (1-based). Default: the cursor position.
            offset: With measure: where in the bar it starts.
            tick: Exact start instead of a bar.
            end_measure: Last bar it covers (inclusive). Default: to the end of the start bar.
            end_tick: Exact end instead of a bar.
            target_bpm: Quarter-note tempo reached at the end.
            factor: Or: end tempo relative to the start (0.75 = 25% slower). Default 0.75 for rit./rall.,
                1.25 for accel.
            a_tempo: Put "a tempo" (back to the original tempo) right after the change.
        """
        return await client.send_command("addGradualTempoChange", _params(
            type=type, measure=measure, offset=offset, tick=tick, endMeasure=end_measure, endTick=end_tick,
            targetBpm=target_bpm, factor=factor, aTempo=a_tempo or None))

    @mcp.tool()
    async def remove_marking(
        kind: Literal["tempo", "dynamic", "fermata", "text", "rehearsalMark", "chordSymbol", "breath",
                      "slur", "hairpin", "volta", "gradualTempoChange"],
        tick: Optional[int] = None,
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        staff: Optional[int] = None,
    ):
        """Remove a marking at an exact position (as get_score shows it: a bar and @offset, or a tick).
        Lines (slur, hairpin, volta, gradualTempoChange) are matched by where they start.
        "text" removes staff, system and expression text (pedal marks are staff text).

        Args:
            kind: What to remove.
            tick: Exact position.
            measure: Or this bar, at its start or at offset.
            offset: With measure: position inside the bar.
            staff: Only on this staff (default: any staff).
        """
        return await client.send_command("removeMarking", _params(kind=kind, tick=tick, measure=measure, offset=offset,
                                                                  staff=staff))

    @mcp.tool()
    async def add_slur(
        start_tick: Optional[int] = None,
        end_tick: Optional[int] = None,
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        staff: Optional[int] = None,
    ):
        """Add a slur over the notes in a range on one staff (ticks, or whole bars).

        Args:
            start_tick: Start of the first note.
            end_tick: End of the last note (the tick after it).
            start_measure: Or: first bar.
            end_measure: Or: last bar (inclusive).
            staff: Staff (default: the cursor's staff).
        """
        return await client.send_command("addSlur", _params(
            startTick=start_tick, endTick=end_tick, startMeasure=start_measure, endMeasure=end_measure, staff=staff))

    @mcp.tool()
    async def add_hairpin(
        type: Literal["crescendo", "diminuendo"] = "crescendo",
        start_tick: Optional[int] = None,
        end_tick: Optional[int] = None,
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        staff: Optional[int] = None,
    ):
        """Add a crescendo or diminuendo hairpin over a range on one staff (ticks, or whole bars)."""
        return await client.send_command("addHairpin", _params(
            type=type, startTick=start_tick, endTick=end_tick, startMeasure=start_measure, endMeasure=end_measure, staff=staff))

    @mcp.tool()
    async def add_articulation(
        type: Literal["staccato", "tenuto", "accent", "marcato"],
        start_tick: Optional[int] = None,
        end_tick: Optional[int] = None,
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        staff: Optional[int] = None,
    ):
        """Add an articulation to every note in a range on one staff. With only start_tick, applies to
        the single note/chord starting there."""
        return await client.send_command("addArticulation", _params(
            type=type, startTick=start_tick, endTick=end_tick, startMeasure=start_measure, endMeasure=end_measure, staff=staff))
