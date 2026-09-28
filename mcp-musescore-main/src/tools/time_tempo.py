"""Time signature, tempo and marking tools for MuseScore MCP."""

from typing import Optional

from ..client import MuseScoreClient
from ..types import Offset
from .navigation import position_params


def setup_time_tempo_tools(mcp, client: MuseScoreClient):
    """Setup time signature, tempo and marking tools."""

    @mcp.tool()
    async def set_time_signature(numerator: int = 4, denominator: int = 4, measure: Optional[int] = None):
        """Set the time signature from a measure onwards. MuseScore re-bars the music after it: the notes
        stay, but the bars after it are cut anew, so their number and numbering can change (e.g. 8 bars of
        3/4 become 6 bars of 4/4). Read the score again afterwards.

        Args:
            numerator: Top number of time signature (beats per measure)
            denominator: Bottom number of time signature (note value that gets the beat)
            measure: Measure (1-based) where it starts. Default: the cursor's measure.
        """
        params = {"numerator": numerator, "denominator": denominator}
        params.update(position_params(measure=measure))
        return await client.send_command("setTimeSignature", params)

    @mcp.tool()
    async def set_tempo(
        bpm: float,
        beat_unit: Optional[str] = None,
        text: Optional[str] = None,
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
    ):
        """Add a tempo marking (it changes playback), replacing one already at that position.

        Args:
            bpm: Beats per minute of beat_unit.
            beat_unit: The beat as a fraction of a whole note: "1/4" (default), "3/8" (dotted quarter, for
                6/8), "1/2" (for 2/2), "1/8". Shown in the metronome mark.
            text: Optional tempo word shown before the metronome mark, e.g. "Allegro".
            measure: Measure (1-based) to place it at. Default: the cursor position.
            offset: With measure: position inside it ("1/2" = halfway through a 4/4 bar).
            tick: Absolute tick instead of measure.
        """
        params = {"bpm": bpm}
        if beat_unit:
            params["beatUnit"] = beat_unit
        if text:
            params["text"] = text
        params.update(position_params(measure, tick, offset=offset))
        return await client.send_command("setTempo", params)

    @mcp.tool()
    async def add_dynamic(
        dynamic: str,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
    ):
        """Add a dynamic marking under the note/rest at the cursor (or the given position).
        Replaces a dynamic already there. Does not move the cursor's position in time.

        Args:
            dynamic: One of ppp, pp, p, mp, mf, f, ff, fff (up to 6 letters), fp, pf, sf, sfz,
                sff, sffz, sfp, sfpp, fz, rf, rfz.
            staff: Staff index (0-based). Default: the cursor's staff.
            voice: Voice 0-3. Default: the cursor's voice.
            measure: Place in this measure (1-based), at its start or at offset.
            offset: With measure: position inside it ("1/4" = beat 2 in 4/4).
            tick: Place at this absolute tick.
        """
        params = {"dynamic": dynamic}
        params.update(position_params(measure, tick, staff, voice, offset))
        return await client.send_command("addDynamic", params)

    @mcp.tool()
    async def add_fermata(
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
    ):
        """Add a fermata on the note/rest at the cursor (or the given position).

        With the default advancing cursor, the cursor is already past the last note written;
        pass its tick (from the add_note result, or use prev_element first).

        Args:
            staff: Staff index (0-based). Default: the cursor's staff.
            voice: Voice 0-3. Default: the cursor's voice.
            measure: Place in this measure (1-based), at its start or at offset.
            offset: With measure: position inside it.
            tick: Place at this absolute tick.
        """
        return await client.send_command("addFermata", position_params(measure, tick, staff, voice, offset))
