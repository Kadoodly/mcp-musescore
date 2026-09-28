from mcp.server.fastmcp import FastMCP
import sys
import logging

# Import modular components
from src.validation import forbid_unknown_tool_arguments
from src.client import MuseScoreClient
from src.tools import (
    setup_analysis_tools,
    setup_connection_tools,
    setup_editing_tools,
    setup_navigation_tools,
    setup_notes_measures_tools,
    setup_score_state_tools,
    setup_sequence_tools,
    setup_staff_instruments_tools,
    setup_structure_edit_tools,
    setup_time_tempo_tools,
)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("MuseScoreMCP")

# Sent to the MCP client when it connects: how to work with this server.
INSTRUCTIONS = """Edits the score open in MuseScore Studio 4 (through the MuseScore API Server plugin).

Workflow:
- Existing score: get_score first. Its compact notation (C4:q D4 [C4 E4 G4]:h~ r ...) is the same one
  write_voice and replace_section take as notation=, so you can rewrite what you read. Big scores: read
  ranges with start_measure/end_measure. analyze_score, analyze_harmony, analyze_structure give a
  musical overview.
- New music: write_voice (one staff/voice, one passage) or replace_section (whole bars, several staves at
  once; also appends bars at the end). Each call is one undo step, checked before anything is written.
  Several edits as one undo step: process_sequence with atomic=true.
- Positions: measure (1-based) + offset inside the bar ("3/8" = fraction of a whole note), or tick
  (480 per quarter). Staves are 0-based (see get_score), voices 0-3.
- From scratch: the user opens a new score in MuseScore with the instruments (or use add_instrument /
  remove_instrument; list_instruments gives ids and ranges), then set_score_info, set_key_signature,
  set_tempo, and write.
- The user may edit in MuseScore at the same time. Every result carries scoreVersion; pass
  expected_version=<version you last read> to edits to make sure you are editing what you read. If it
  was refused, get_changes_since(version) shows what changed.
- undo / redo work like Ctrl+Z / Ctrl+Y. Durations are fractions of a whole note ("1/4" = quarter).
"""


def create_server(client) -> FastMCP:
    """The MCP app with every tool registered, talking to MuseScore through `client`."""
    # Unknown tool arguments are errors (e.g. tie=true on a tool without a tie
    # parameter), so Claude always knows whether a feature exists. Must run
    # before the tools are registered.
    forbid_unknown_tool_arguments()

    app = FastMCP("MuseScore Assistant", instructions=INSTRUCTIONS)
    setup_connection_tools(app, client)
    setup_score_state_tools(app, client)
    setup_navigation_tools(app, client)
    setup_notes_measures_tools(app, client)
    setup_staff_instruments_tools(app, client)
    setup_time_tempo_tools(app, client)
    setup_sequence_tools(app, client)
    setup_analysis_tools(app, client)
    setup_structure_edit_tools(app, client)
    setup_editing_tools(app, client)
    return app


# Create the MCP app and client
client = MuseScoreClient()
mcp = create_server(client)

# Main entry point
if __name__ == "__main__":
    sys.stderr.write("MuseScore MCP Server starting up...\n")
    sys.stderr.flush()
    logger.info("MuseScore MCP Server is running")
    mcp.run()