"""Time signature, tempo and marking tools for MuseScore MCP."""

from typing import Optional

from ..client import MuseScoreClient
from .navigation import position_params


def setup_time_tempo_tools(mcp, client: MuseScoreClient):
    """Setup time signature, tempo and marking tools."""

    @mcp.tool()
    async def set_time_signature(numerator: int = 4, denominator: int = 4, measure: Optional[int] = None):
        """Set the time signature from a measure onwards.

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
        text: Optional[str] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add a tempo marking (quarter note = bpm), replacing one already at that position.

        Args:
            bpm: Quarter-note beats per minute.
            text: Optional tempo word shown before the metronome mark, e.g. "Allegro".
            measure: Measure (1-based) to place it at. Default: the cursor position.
            tick: Absolute tick to place it at (must be the start of a note/rest).
        """
        params = {"bpm": bpm}
        if text:
            params["text"] = text
        params.update(position_params(measure, tick))
        return await client.send_command("setTempo", params)

    @mcp.tool()
    async def add_dynamic(
        dynamic: str,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add a dynamic marking under the note/rest at the cursor (or the given position).
        Replaces a dynamic already there. Does not move the cursor's position in time.

        Args:
            dynamic: One of ppp, pp, p, mp, mf, f, ff, fff (up to 6 letters), fp, pf, sf, sfz,
                sff, sffz, sfp, sfpp, fz, rf, rfz.
            staff: Staff index (0-based). Default: the cursor's staff.
            voice: Voice 0-3. Default: the cursor's voice.
            measure: Place at the start of this measure (1-based).
            tick: Place at this absolute tick.
        """
        params = {"dynamic": dynamic}
        params.update(position_params(measure, tick, staff, voice))
        return await client.send_command("addDynamic", params)

    @mcp.tool()
    async def add_fermata(
        staff: Optional[int] = None,
        voice: Optional[int] = None,
        measure: Optional[int] = None,
        tick: Optional[int] = None,
    ):
        """Add a fermata on the note/rest at the cursor (or the given position).

        With the default advancing cursor, the cursor is already past the last note written;
        pass its tick (from the add_note result, or use prev_element first).

        Args:
            staff: Staff index (0-based). Default: the cursor's staff.
            voice: Voice 0-3. Default: the cursor's voice.
            measure: Place at the start of this measure (1-based).
            tick: Place at this absolute tick.
        """
        return await client.send_command("addFermata", position_params(measure, tick, staff, voice))
