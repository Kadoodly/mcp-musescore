from mcp.server.fastmcp import FastMCP
import sys
import logging

# Import modular components
from src.validation import forbid_unknown_tool_arguments
from src.client import MuseScoreClient
from src.tools import (
    setup_connection_tools,
    setup_navigation_tools,
    setup_notes_measures_tools,
    setup_staff_instruments_tools,
    setup_time_tempo_tools,
    setup_sequence_tools,
    setup_analysis_tools,
    setup_structure_edit_tools
)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("MuseScoreMCP")


def create_server(client) -> FastMCP:
    """The MCP app with every tool registered, talking to MuseScore through `client`."""
    # Unknown tool arguments are errors (e.g. tie=true on a tool without a tie
    # parameter), so Claude always knows whether a feature exists. Must run
    # before the tools are registered.
    forbid_unknown_tool_arguments()

    app = FastMCP("MuseScore Assistant")
    setup_connection_tools(app, client)
    setup_navigation_tools(app, client)
    setup_notes_measures_tools(app, client)
    setup_staff_instruments_tools(app, client)
    setup_time_tempo_tools(app, client)
    setup_sequence_tools(app, client)
    setup_analysis_tools(app, client)
    setup_structure_edit_tools(app, client)
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