"""Staff and instrument tools for MuseScore MCP."""

from typing import Optional

from ..client import MuseScoreClient


def setup_staff_instruments_tools(mcp, client: MuseScoreClient):
    """Setup staff and instrument tools."""

    @mcp.tool()
    async def add_instrument(instrument_id: str, position: Optional[int] = None):
        """Add an instrument (part, with all its staves) to the score. list_instruments gives the ids,
        ranges and clefs.

        Args:
            instrument_id: MuseScore instrument id, e.g. "piano", "violin", "flute", "cello", "soprano",
                "guitar-steel", "drumset". The result reports the new part's staves; with the default
                position it warns if the id was unknown and MuseScore substituted another instrument.
            position: Part index to insert it at (0 = top). Default: at the bottom.
        """
        params = {"instrumentId": instrument_id}
        if position is not None:
            params["position"] = position
        return await client.send_command("addInstrument", params)

    @mcp.tool()
    async def set_instrument_name(
        name: Optional[str] = None,
        short_name: Optional[str] = None,
        staff: Optional[int] = None,
        part: Optional[int] = None,
    ):
        """Rename an instrument (part), e.g. "Violin I" / "Vln. I", or "Soprano" for a vocal line: the
        name before the first system and the short name before the others.

        Args:
            name: The full name.
            short_name: The short name.
            staff: Any staff of the part (default: the cursor's staff).
            part: Or the part index.
        """
        params = {k: v for k, v in (("name", name), ("shortName", short_name), ("staff", staff), ("part", part))
                  if v is not None}
        return await client.send_command("setInstrumentName", params)

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
