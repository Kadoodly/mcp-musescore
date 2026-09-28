"""Sequence processing tools for MuseScore MCP."""

from ..client import MuseScoreClient
from ..types import ActionSequence


def setup_sequence_tools(mcp, client: MuseScoreClient):
    """Setup sequence processing tools."""

    @mcp.tool()
    async def processSequence(sequence: ActionSequence, atomic: bool = False):
        """Process a sequence of commands in one round trip.

        Actions use camelCase names and params (e.g. {"action": "addNote", "params": {"pitch": 60,
        "duration": {"numerator": 1, "denominator": 4}, "staff": 2}}). Write actions accept optional
        staff, voice, measure (1-based) and tick. Each step is its own undo step; the sequence stops
        at the first failing step and reports which one failed.

        With atomic=True the whole sequence is ONE undo step, and if any step fails nothing is kept.
        Atomic sequences can't contain actions that change the selection (undo, deleteSelection,
        insertMeasure, select*, addSlur, addHairpin, addArticulation, deleteMeasures, copyMeasures).
        """
        return await client.send_command("processSequence", {"sequence": sequence, "atomic": atomic})
