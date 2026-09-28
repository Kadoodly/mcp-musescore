"""TypedDict definitions for MuseScore MCP action sequences.

Position params (all optional): staff (0-based), voice (0-3), measure (1-based)
and tick (480 per quarter note). staff and voice stick for later actions.
"""

from typing import Dict, Any, List, Literal, NotRequired, TypedDict

Duration = Dict[Literal["numerator", "denominator"], int]


class PositionParams(TypedDict):
    staff: NotRequired[int]
    voice: NotRequired[int]
    measure: NotRequired[int]
    tick: NotRequired[int]


class getScoreAction(TypedDict):
    action: Literal["getScore"]
    params: Dict[str, Any]


class addNoteParams(PositionParams):
    pitch: int
    duration: Duration
    advanceCursorAfterAction: NotRequired[bool]
    addToChord: NotRequired[bool]


class addNoteAction(TypedDict):
    action: Literal["addNote"]
    params: addNoteParams


class addRestParams(PositionParams):
    duration: Duration
    advanceCursorAfterAction: NotRequired[bool]


class addRestAction(TypedDict):
    action: Literal["addRest"]
    params: addRestParams


class addTupletParams(PositionParams):
    duration: Duration
    ratio: Duration
    advanceCursorAfterAction: NotRequired[bool]


class addTupletAction(TypedDict):
    action: Literal["addTuplet"]
    params: addTupletParams


class addLyricsParams(PositionParams):
    lyrics: List[str]
    verse: NotRequired[int]


class addLyricsAction(TypedDict):
    action: Literal["addLyrics"]
    params: addLyricsParams


class addDynamicParams(PositionParams):
    dynamic: str


class addDynamicAction(TypedDict):
    action: Literal["addDynamic"]
    params: addDynamicParams


class addFermataAction(TypedDict):
    action: Literal["addFermata"]
    params: PositionParams


class setTempoParams(TypedDict):
    bpm: float
    text: NotRequired[str]
    measure: NotRequired[int]
    tick: NotRequired[int]


class setTempoAction(TypedDict):
    action: Literal["setTempo"]
    params: setTempoParams


class addInstrumentParams(TypedDict):
    instrumentId: str


class addInstrumentAction(TypedDict):
    action: Literal["addInstrument"]
    params: addInstrumentParams


class removeInstrumentParams(TypedDict):
    part: NotRequired[int]
    staff: NotRequired[int]


class removeInstrumentAction(TypedDict):
    action: Literal["removeInstrument"]
    params: removeInstrumentParams


class setStaffMuteParams(TypedDict):
    staff: int
    mute: bool


class setStaffMuteAction(TypedDict):
    action: Literal["setStaffMute"]
    params: setStaffMuteParams


class setInstrumentSoundParams(TypedDict):
    staff: int
    instrumentId: str


class setInstrumentSoundAction(TypedDict):
    action: Literal["setInstrumentSound"]
    params: setInstrumentSoundParams


class appendMeasureAction(TypedDict):
    action: Literal["appendMeasure"]
    params: Dict[str, Any]


class insertMeasureParams(TypedDict):
    measure: NotRequired[int]
    count: NotRequired[int]


class insertMeasureAction(TypedDict):
    action: Literal["insertMeasure"]
    params: insertMeasureParams


class deleteSelectionParams(TypedDict):
    measure: NotRequired[int]
    staff: NotRequired[int]


class deleteSelectionAction(TypedDict):
    action: Literal["deleteSelection"]
    params: deleteSelectionParams


class getCursorInfoAction(TypedDict):
    action: Literal["getCursorInfo"]
    params: Dict[str, Any]


class setCursorAction(TypedDict):
    action: Literal["setCursor"]
    params: PositionParams


class goToMeasureParams(TypedDict):
    measure: int
    staff: NotRequired[int]
    voice: NotRequired[int]


class goToMeasureAction(TypedDict):
    action: Literal["goToMeasure"]
    params: goToMeasureParams


class nextElementAction(TypedDict):
    action: Literal["nextElement"]
    params: Dict[str, Any]


class prevElementAction(TypedDict):
    action: Literal["prevElement"]
    params: Dict[str, Any]


class selectCurrentMeasureAction(TypedDict):
    action: Literal["selectCurrentMeasure"]
    params: Dict[str, Any]


class selectCustomRangeParams(TypedDict):
    startTick: int
    endTick: int
    startStaff: int
    endStaff: int


class selectCustomRangeAction(TypedDict):
    action: Literal["selectCustomRange"]
    params: selectCustomRangeParams


class goToFinalMeasureAction(TypedDict):
    action: Literal["goToFinalMeasure"]
    params: Dict[str, Any]


class goToBeginningOfScoreAction(TypedDict):
    action: Literal["goToBeginningOfScore"]
    params: Dict[str, Any]


class setTimeSignatureParams(TypedDict):
    numerator: int
    denominator: int
    measure: NotRequired[int]


class setTimeSignatureAction(TypedDict):
    action: Literal["setTimeSignature"]
    params: setTimeSignatureParams


class undoAction(TypedDict):
    action: Literal["undo"]
    params: Dict[str, Any]


class nextStaffAction(TypedDict):
    action: Literal["nextStaff"]
    params: Dict[str, Any]


class prevStaffAction(TypedDict):
    action: Literal["prevStaff"]
    params: Dict[str, Any]


class structureEditAction(TypedDict):
    """Round-2 editing actions; params use the camelCase names of the matching tool arguments
    (e.g. addRepeat: startMeasure, endMeasure, times; addMarker: type, measure;
    setKeySignature: fifths, measure, mode, staff; addGradualTempoChange: type, measure, endMeasure)."""
    action: Literal["addRepeat", "removeRepeat", "addMarker", "addJump", "addRehearsalMark",
                    "setKeySignature", "addGradualTempoChange", "removeMarking", "addSlur", "addHairpin",
                    "addArticulation", "deleteMeasures", "copyMeasures"]
    params: Dict[str, Any]


class insertMeasuresParams(TypedDict):
    measure: NotRequired[int]
    count: NotRequired[int]


ActionSequence = List[
    getScoreAction | addNoteAction | addRestAction | addTupletAction |
    addLyricsAction | addDynamicAction | addFermataAction | setTempoAction |
    addInstrumentAction | removeInstrumentAction | setStaffMuteAction |
    setInstrumentSoundAction | appendMeasureAction | insertMeasureAction |
    deleteSelectionAction | getCursorInfoAction | setCursorAction |
    goToMeasureAction | nextElementAction | prevElementAction |
    selectCurrentMeasureAction | selectCustomRangeAction | goToFinalMeasureAction |
    goToBeginningOfScoreAction | setTimeSignatureAction |
    undoAction | nextStaffAction | prevStaffAction | structureEditAction
]
