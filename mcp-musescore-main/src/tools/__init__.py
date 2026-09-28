"""MCP tools for MuseScore operations."""

from .analysis import setup_analysis_tools
from .connection import setup_connection_tools
from .editing import setup_editing_tools
from .navigation import setup_navigation_tools
from .notes_measures import setup_notes_measures_tools
from .score_state import setup_score_state_tools
from .sequences import setup_sequence_tools
from .staff_instruments import setup_staff_instruments_tools
from .structure_edit import setup_structure_edit_tools
from .time_tempo import setup_time_tempo_tools

__all__ = [
    "setup_analysis_tools",
    "setup_connection_tools",
    "setup_editing_tools",
    "setup_navigation_tools",
    "setup_notes_measures_tools",
    "setup_score_state_tools",
    "setup_sequence_tools",
    "setup_staff_instruments_tools",
    "setup_structure_edit_tools",
    "setup_time_tempo_tools",
]
