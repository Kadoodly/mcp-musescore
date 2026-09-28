"""Staff and instrument tools for MuseScore MCP."""

from typing import Optional

from ..client import MuseScoreClient


def setup_staff_instruments_tools(mcp, client: MuseScoreClient):
    """Setup staff and instrument tools."""

    @mcp.tool()
    async def add_instrument(instrument_id: str):
        """Add a new instrument (part) at the bottom of the score.

        Args:
            instrument_id: MuseScore instrument id, e.g. "piano", "violin", "flute", "cello",
                "soprano", "acoustic-guitar", "drumset". The result reports the new part's staves;
                it warns if the id was unknown and MuseScore substituted another instrument.
        """
        return await client.send_command("addInstrument", {
            "instrumentId": instrument_id
        })

    @mcp.tool()
    async def remove_instrument(part: Optional[int] = None, staff: Optional[int] = None):
        """Remove a whole instrument (part, including all its staves) from the score.

        Give either the part index or the index of any of its staves (see get_score).
        Undo restores it.

        Args:
            part: Part index (0-based).
            staff: Staff index (0-based) of any staff belonging to the instrument.
        """
        params = {}
        if part is not None:
            params["part"] = part
        if staff is not None:
            params["staff"] = staff
        return await client.send_command("removeInstrument", params)

    @mcp.tool()
    async def set_staff_mute(staff: int, mute: bool):
        """Mute or unmute a staff.

        Args:
            staff: Staff number (0-based)
            mute: True to mute, False to unmute
        """
        return await client.send_command("setStaffMute", {
            "staff": staff,
            "mute": mute
        })

    @mcp.tool()
    async def set_instrument_sound(staff: int, instrument_id: str):
        """Replace the instrument of the part containing a staff (e.g. turn a violin into a viola).

        Args:
            staff: Staff number (0-based)
            instrument_id: MuseScore instrument id of the new instrument
        """
        return await client.send_command("setInstrumentSound", {
            "staff": staff,
            "instrumentId": instrument_id
        })
