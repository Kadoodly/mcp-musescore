"""Sequence processing tools for MuseScore MCP."""

from typing import Any, Dict, List

from ..client import MuseScoreClient
from ..types import ActionSequence
from ..utils.durations import DurationError, normalize_duration
from ..validation import normalize_voice_events


def normalize_sequence(sequence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check the parts of each step that the types can't (writable durations, write_voice
    events) and return the steps in the plugin's wire format. Raises ValueError naming the step."""
    out = []
    for i, step in enumerate(sequence):
        step = dict(step)
        params = dict(step.get("params") or {})
        try:
            if step["action"] in ("addNote", "addRest") and "duration" in params:
                params["duration"] = normalize_duration(params["duration"])
            elif step["action"] == "writeVoice":
                params["events"] = normalize_voice_events(params["events"])
        except (ValueError, DurationError) as e:
            raise ValueError(f"step {i} ({step['action']}): {e}") from None
        if "params" in step or params:
            step["params"] = params
        out.append(step)
    return out


def setup_sequence_tools(mcp, client: MuseScoreClient):
    """Setup sequence processing tools."""

    @mcp.tool()
    async def processSequence(sequence: ActionSequence, atomic: bool = False):
        """Process a sequence of commands in one round trip.

        Actions use camelCase names and params (e.g. {"action": "addNote", "params": {"pitch": 60,
        "duration": "1/4", "staff": 2}}). Write actions accept optional staff, voice, measure
        (1-based) and tick. Unknown actions or params are errors. writeVoice takes the same events
        as the write_voice tool. Each step is its own undo step; the sequence stops at the first
        failing step and reports which one failed. MuseScore's view (cursor/selection) is updated
        once, at the end.

        With atomic=True the whole sequence is ONE undo step, and if any step fails nothing is kept.
        Ties requested with addNote tie=true are made at the end of an atomic sequence, so the
        following note may be written by a later step.
        Atomic sequences can't contain actions that change the selection (undo, deleteSelection,
        insertMeasure, select*, addSlur, addHairpin, addArticulation, deleteMeasures, copyMeasures).
        """
        return await client.send_command("processSequence", {"sequence": normalize_sequence(sequence), "atomic": atomic})
