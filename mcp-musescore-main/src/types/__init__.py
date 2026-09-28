"""Type definitions for MuseScore MCP."""

from .action_types import *

__all__ = [
    "ActionSequence",
    "PositionParams",
    "getScoreAction",
    "addNoteAction",
    "addRestAction",
    "addTupletAction",
    "addLyricsAction",
    "addDynamicAction",
    "addFermataAction",
    "setTempoAction",
    "addInstrumentAction",
    "removeInstrumentAction",
    "setStaffMuteAction",
    "setInstrumentSoundAction",
    "appendMeasureAction",
    "deleteSelectionAction",
    "getCursorInfoAction",
    "setCursorAction",
    "goToMeasureAction",
    "nextElementAction",
    "prevElementAction",
    "selectCurrentMeasureAction",
    "selectCustomRangeAction",
    "insertMeasureAction",
    "goToFinalMeasureAction",
    "goToBeginningOfScoreAction",
    "setTimeSignatureAction",
    "undoAction",
    "nextStaffAction",
    "prevStaffAction"
]
