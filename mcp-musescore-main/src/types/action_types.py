"""TypedDict definitions for MuseScore MCP action sequences and write_voice events.

Position params (all optional): staff (0-based), voice (0-3), measure (1-based)
and tick (480 per quarter note). staff and voice stick for later actions.

Every dict here rejects unknown keys (extra="forbid"), matching the plugin's
per-action parameter whitelist (actionParams in musescore-mcp-websocket.qml).
"""

from typing import Annotated, List, Literal, Union

from pydantic import ConfigDict, Field, with_config
from typing_extensions import NotRequired, TypedDict

STRICT = ConfigDict(extra="forbid")

DurationText = Annotated[str, Field(
    pattern=r"^\s*\d+\s*/\s*\d+\s*$",
    description='Fraction of a whole note, e.g. "1/4" = quarter, "3/8" = dotted quarter, "5/8" = half tied to eighth',
)]


@with_config(STRICT)
class DurationObject(TypedDict):
    numerator: int
    denominator: int


Duration = Union[DurationText, DurationObject]
Pitch = Annotated[int, Field(ge=0, le=127, description="MIDI pitch, 60 = middle C")]


@with_config(STRICT)
class VoiceEvent(TypedDict):
    """One write_voice event: a note/chord (pitches) or a rest (rest: true)."""
    pitches: NotRequired[List[Pitch]]
    rest: NotRequired[bool]
    duration: Duration
    tie: NotRequired[Union[bool, List[Pitch]]]


@with_config(STRICT)
class EmptyParams(TypedDict):
    pass


@with_config(STRICT)
class PositionParams(TypedDict):
    staff: NotRequired[int]
    voice: NotRequired[int]
    measure: NotRequired[int]
    tick: NotRequired[int]


@with_config(STRICT)
class StaffVoiceParams(TypedDict):
    staff: NotRequired[int]
    voice: NotRequired[int]


def _action(name: str, params_type, required: bool = False):
    """A {"action": name, "params": params_type} step type."""
    fields = {"action": Literal[name], "params": params_type if required else NotRequired[params_type]}
    return with_config(STRICT)(TypedDict(f"{name}Action", fields))


# --- Notes and rests ---------------------------------------------------------

@with_config(STRICT)
class addNoteParams(PositionParams):
    pitch: Pitch
    duration: NotRequired[Duration]
    advanceCursorAfterAction: NotRequired[bool]
    addToChord: NotRequired[bool]
    tie: NotRequired[bool]


@with_config(STRICT)
class addRestParams(PositionParams):
    duration: Duration
    advanceCursorAfterAction: NotRequired[bool]


@with_config(STRICT)
class addTupletParams(PositionParams):
    duration: DurationObject
    ratio: DurationObject
    advanceCursorAfterAction: NotRequired[bool]


@with_config(STRICT)
class writeVoiceParams(PositionParams):
    events: List[VoiceEvent]


@with_config(STRICT)
class addLyricsParams(PositionParams):
    lyrics: List[str]
    verse: NotRequired[int]


@with_config(STRICT)
class addDynamicParams(PositionParams):
    dynamic: str


@with_config(STRICT)
class setTempoParams(TypedDict):
    bpm: float
    text: NotRequired[str]
    measure: NotRequired[int]
    tick: NotRequired[int]


# --- Instruments ---------------------------------------------------------------

@with_config(STRICT)
class addInstrumentParams(TypedDict):
    instrumentId: str


@with_config(STRICT)
class removeInstrumentParams(TypedDict):
    part: NotRequired[int]
    staff: NotRequired[int]


@with_config(STRICT)
class setStaffMuteParams(TypedDict):
    staff: int
    mute: bool


@with_config(STRICT)
class setInstrumentSoundParams(TypedDict):
    staff: int
    instrumentId: str


# --- Measures, navigation, selection -----------------------------------------------

@with_config(STRICT)
class countParams(TypedDict):
    count: NotRequired[int]


@with_config(STRICT)
class insertMeasureParams(TypedDict):
    measure: NotRequired[int]
    count: NotRequired[int]


@with_config(STRICT)
class deleteSelectionParams(TypedDict):
    measure: NotRequired[int]
    staff: NotRequired[int]


@with_config(STRICT)
class goToMeasureParams(StaffVoiceParams):
    measure: int


@with_config(STRICT)
class moveElementParams(TypedDict):
    numElements: NotRequired[int]


@with_config(STRICT)
class selectCurrentMeasureParams(PositionParams):
    allStaves: NotRequired[bool]


@with_config(STRICT)
class selectCustomRangeParams(TypedDict):
    startTick: int
    endTick: int
    startStaff: int
    endStaff: int


@with_config(STRICT)
class setTimeSignatureParams(TypedDict):
    numerator: int
    denominator: int
    measure: NotRequired[int]


@with_config(STRICT)
class undoParams(TypedDict):
    steps: NotRequired[int]


# --- Structure and expression ------------------------------------------------------

@with_config(STRICT)
class repeatParams(TypedDict):
    startMeasure: int
    endMeasure: int
    times: NotRequired[int]


@with_config(STRICT)
class removeRepeatParams(TypedDict):
    startMeasure: int
    endMeasure: int


@with_config(STRICT)
class markerParams(TypedDict):
    type: str
    measure: int


@with_config(STRICT)
class rehearsalMarkParams(TypedDict):
    text: str
    measure: NotRequired[int]
    tick: NotRequired[int]


@with_config(STRICT)
class setKeySignatureParams(TypedDict):
    fifths: int
    measure: NotRequired[int]
    mode: NotRequired[Literal["major", "minor"]]
    staff: NotRequired[int]


@with_config(STRICT)
class gradualTempoChangeParams(TypedDict):
    type: str
    measure: NotRequired[int]
    tick: NotRequired[int]
    endMeasure: NotRequired[int]
    endTick: NotRequired[int]
    targetBpm: NotRequired[float]
    factor: NotRequired[float]
    aTempo: NotRequired[bool]


@with_config(STRICT)
class removeMarkingParams(TypedDict):
    kind: str
    tick: NotRequired[int]
    measure: NotRequired[int]
    staff: NotRequired[int]


@with_config(STRICT)
class rangeParams(TypedDict):
    startTick: NotRequired[int]
    endTick: NotRequired[int]
    startMeasure: NotRequired[int]
    endMeasure: NotRequired[int]
    staff: NotRequired[int]


@with_config(STRICT)
class typedRangeParams(rangeParams):
    type: str


@with_config(STRICT)
class hairpinParams(rangeParams):
    type: NotRequired[str]


@with_config(STRICT)
class deleteMeasuresParams(TypedDict):
    startMeasure: int
    endMeasure: NotRequired[int]


@with_config(STRICT)
class copyMeasuresParams(TypedDict):
    startMeasure: int
    endMeasure: NotRequired[int]
    toMeasure: int
    insert: NotRequired[bool]
    staff: NotRequired[int]


# action name -> (params type, params required). The plugin accepts exactly these
# actions in processSequence, with exactly these params.
SEQUENCE_ACTIONS = {
    "getScore": (EmptyParams, False),
    "addNote": (addNoteParams, True),
    "addRest": (addRestParams, True),
    "addTuplet": (addTupletParams, True),
    "writeVoice": (writeVoiceParams, True),
    "addLyrics": (addLyricsParams, True),
    "appendMeasure": (countParams, False),
    "insertMeasure": (insertMeasureParams, False),
    "deleteSelection": (deleteSelectionParams, False),
    "getCursorInfo": (EmptyParams, False),
    "setCursor": (PositionParams, False),
    "goToMeasure": (goToMeasureParams, True),
    "goToBeginningOfScore": (StaffVoiceParams, False),
    "goToFinalMeasure": (StaffVoiceParams, False),
    "nextElement": (moveElementParams, False),
    "prevElement": (moveElementParams, False),
    "nextStaff": (countParams, False),
    "prevStaff": (countParams, False),
    "selectCurrentMeasure": (selectCurrentMeasureParams, False),
    "selectCustomRange": (selectCustomRangeParams, True),
    "setTimeSignature": (setTimeSignatureParams, True),
    "setTempo": (setTempoParams, True),
    "addDynamic": (addDynamicParams, True),
    "addFermata": (PositionParams, False),
    "addInstrument": (addInstrumentParams, True),
    "removeInstrument": (removeInstrumentParams, False),
    "setStaffMute": (setStaffMuteParams, True),
    "setInstrumentSound": (setInstrumentSoundParams, True),
    "undo": (undoParams, False),
    "addRepeat": (repeatParams, True),
    "removeRepeat": (removeRepeatParams, True),
    "addMarker": (markerParams, True),
    "addJump": (markerParams, True),
    "addRehearsalMark": (rehearsalMarkParams, True),
    "setKeySignature": (setKeySignatureParams, True),
    "addGradualTempoChange": (gradualTempoChangeParams, True),
    "removeMarking": (removeMarkingParams, True),
    "addSlur": (rangeParams, False),
    "addHairpin": (hairpinParams, False),
    "addArticulation": (typedRangeParams, True),
    "deleteMeasures": (deleteMeasuresParams, True),
    "copyMeasures": (copyMeasuresParams, True),
}

SequenceStep = Annotated[
    Union[tuple(_action(name, params, required) for name, (params, required) in SEQUENCE_ACTIONS.items())],
    Field(discriminator="action"),
]

ActionSequence = Annotated[List[SequenceStep], Field(min_length=1)]
