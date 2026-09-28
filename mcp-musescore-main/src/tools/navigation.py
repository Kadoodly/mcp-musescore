"""Cursor and navigation tools for MuseScore MCP."""

from typing import Any, Dict, Optional

from ..client import MuseScoreClient
from ..types import Offset
from ..utils.lilypond_converter import json_to_lilypond


def position_params(
    measure: Optional[int] = None,
    tick: Optional[int] = None,
    staff: Optional[int] = None,
    voice: Optional[int] = None,
    offset: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the optional position params understood by the plugin."""
    params: Dict[str, Any] = {}
    for key, value in (("measure", measure), ("tick", tick), ("staff", staff), ("voice", voice), ("offset", offset)):
        if value is not None:
            params[key] = value
    return params


def with_version(params: Dict[str, Any], expected_version: Optional[int]) -> Dict[str, Any]:
    """Adds expectedVersion: the plugin refuses the edit if the score changed since."""
    if expected_version is not None:
        params["expectedVersion"] = expected_version
    return params


def setup_navigation_tools(mcp, client: MuseScoreClient):
    """Setup cursor and navigation tools."""

    async def _with_selection_lilypond(action: str, params: Dict[str, Any]):
        res = await client.send_command(action, params)
        sel = res.get("currentSelection")
        if res.get("success") and sel:
            res["lilypond"] = json_to_lilypond(sel)
        return res

    @mcp.tool()
    async def get_cursor_info():
        """Get the cursor position (measure, beat, tick, staff, voice) and the note/rest at it.

        If you click a note or select a range in MuseScore, the cursor moves there."""
        return await client.send_command("getCursorInfo")

    @mcp.tool()
    async def set_cursor(
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
        staff: Optional[int] = None,
        voice: Optional[int] = None,
    ):
        """Move the write cursor. Every argument is optional; omitted ones keep their current value.

        Args:
            measure: Measure number (1-based); moves to the start of that measure.
            offset: With measure: position inside it, as a fraction of a whole note ("1/4" = beat 2 in 4/4).
            tick: Absolute position in ticks (480 per quarter note), instead of measure.
            staff: Staff index (0-based, see get_score for which instrument is on which staff).
            voice: Voice 0-3 (0 is MuseScore's voice 1).
        """
        return await client.send_command("setCursor", position_params(measure, tick, staff, voice, offset))

    @mcp.tool()
    async def go_to_measure(measure: int, offset: Optional[Offset] = None, staff: Optional[int] = None,
                            voice: Optional[int] = None):
        """Move the cursor to a measure (1-based), optionally also changing staff/voice.

        Args:
            measure: Measure number, starting at 1.
            offset: Position inside the measure ("1/4" = beat 2 in 4/4). Default: its start.
            staff: Staff index (0-based). Default: keep the current staff.
            voice: Voice 0-3. Default: keep the current voice.
        """
        return await client.send_command("goToMeasure", position_params(measure, None, staff, voice, offset))

    @mcp.tool()
    async def go_to_final_measure():
        """Move the cursor to the start of the final measure (keeps staff and voice)."""
        return await client.send_command("goToFinalMeasure")

    @mcp.tool()
    async def go_to_beginning_of_score():
        """Move the cursor to the beginning of the score (keeps staff and voice)."""
        return await client.send_command("goToBeginningOfScore")

    @mcp.tool()
    async def next_element(num_elements: int = 1):
        """Move the cursor forward by notes/rests in the current staff and voice."""
        return await client.send_command("nextElement", {"numElements": num_elements})

    @mcp.tool()
    async def prev_element(num_elements: int = 1):
        """Move the cursor back by notes/rests in the current staff and voice."""
        return await client.send_command("prevElement", {"numElements": num_elements})

    @mcp.tool()
    async def next_staff():
        """Move the cursor down one staff (same position in time)."""
        return await client.send_command("nextStaff")

    @mcp.tool()
    async def prev_staff():
        """Move the cursor up one staff (same position in time)."""
        return await client.send_command("prevStaff")

    @mcp.tool()
    async def select_current_measure(all_staves: bool = False):
        """Select the measure containing the cursor (on the cursor's staff, or on all staves).

        The selection is what delete_selection acts on."""
        return await _with_selection_lilypond("selectCurrentMeasure", {"allStaves": all_staves})

    @mcp.tool()
    async def select_custom_range(start_tick: int, end_tick: int, start_staff: int, end_staff: int):
        """
        Select a custom range of ticks across staves, and move the cursor to its start.
        start_staff and end_staff are both inclusive: (0, 1920, 2, 2) selects only staff 2.
        This provides high surgical precision for retrieving continuous phrasing that spans measure bounds.
        """
        params = {
            "startTick": start_tick,
            "endTick": end_tick,
            "startStaff": start_staff,
            "endStaff": end_staff
        }
        return await _with_selection_lilypond("selectCustomRange", params)
