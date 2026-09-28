"""TypedDict definitions for MuseScore MCP action sequences and write_voice events.

Position params (all optional): staff (0-based), voice (0-3), measure (1-based),
offset (position inside the measure, a fraction of a whole note like "3/8") and
tick (480 per quarter note). staff and voice stick for later actions.

Every dict here rejects unknown keys (extra="forbid"), matching the plugin's
per-action parameter whitelist (actionParams in musescore-mcp-websocket.qml).
The only additions are PYTHON_ONLY_PARAMS, which the server turns into plugin
params before sending (notation text becomes events).
"""

from typing import Annotated, List, Literal, Union

from pydantic import ConfigDict, Field, with_config
from typing_extensions import NotRequired, TypedDict

from ..notation import NOTE_NAME_PATTERN

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
Offset = Annotated[str, Field(
    pattern=r"^\s*(0|\d+\s*/\s*\d+)\s*$",
    description='Position inside the measure as a fraction of a whole note from the barline: "0", "1/4" (beat 2 in 4/4), "3/8"',
)]
MidiPitch = Annotated[int, Field(ge=0, le=127, description="MIDI pitch, 60 = middle C")]
PitchName = Annotated[str, Field(pattern=NOTE_NAME_PATTERN, description='Note name, C4 = middle C: "F#3", "Bb5" (keeps the spelling)')]
Pitch = Union[MidiPitch, PitchName]
Dynamic = Annotated[str, Field(description="ppp, pp, p, mp, mf, f, ff, fff, fp, sf, sfz, sffz, fz, rf, rfz, ...")]
Articulation = Annotated[str, Field(description="staccato, staccatissimo, tenuto, accent, marcato, portato, "
                                                "accent-staccato, marcato-staccato, stress, unstress, fermata")]


@with_config(STRICT)
class TupletNote(TypedDict):
    """A note/chord or rest inside a tuplet: one plain or dotted note value."""
    pitches: NotRequired[List[Pitch]]
    rest: NotRequired[bool]
    duration: Duration
    tie: NotRequired[Union[bool, List[Pitch]]]
    dynamic: NotRequired[Dynamic]
    articulations: NotRequired[List[Articulation]]
    lyric: NotRequired[str]
    text: NotRequired[str]
    chord: NotRequired[str]


@with_config(STRICT)
class VoiceEvent(TypedDict):
    """One write_voice event: a note/chord (pitches), a rest (rest: true), or a
    tuplet ({"tuplet": "3:2", "events": [...]}). Notes and rests may carry
    markings: dynamic, articulations, lyric, text (expression), chord (symbol)."""
    pitches: NotRequired[List[Pitch]]
    rest: NotRequired[bool]
    duration: NotRequired[Duration]
    tie: NotRequired[Union[bool, List[Pitch]]]
    dynamic: NotRequired[Dynamic]
    articulations: NotRequired[List[Articulation]]
    lyric: NotRequired[str]
    text: NotRequired[str]
    chord: NotRequired[str]
    tuplet: NotRequired[Annotated[str, Field(pattern=r"^\s*\d+\s*:\s*\d+\s*$", description='"3:2" = 3 notes in the time of 2')]]
    events: NotRequired[List[TupletNote]]


@with_config(STRICT)
class EmptyParams(TypedDict):
    pass


@with_config(STRICT)
class StaffVoiceParams(TypedDict):
    staff: NotRequired[int]
    voice: NotRequired[int]


@with_config(STRICT)
class PositionParams(StaffVoiceParams):
    measure: NotRequired[int]
    tick: NotRequired[int]
    offset: NotRequired[Offset]


def _action(name: str, params_type, required: bool = False):
    """A {"action": name, "params": params_type} step type."""
    fields = {"action": Literal[name], "params": params_type if required else NotRequired[params_type]}
    return with_config(STRICT)(TypedDict(f"{name}Action", fields))


# --- Reading -------------------------------------------------------------------

@with_config(STRICT)
class getScoreParams(TypedDict):
    startMeasure: NotRequired[int]
    endMeasure: NotRequired[int]


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
    events: NotRequired[List[VoiceEvent]]
    notation: NotRequired[str]


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
    beatUnit: NotRequired[DurationText]
    text: NotRequired[str]
    measure: NotRequired[int]
    tick: NotRequired[int]
    offset: NotRequired[Offset]


# --- Instruments ---------------------------------------------------------------

@with_config(STRICT)
class addInstrumentParams(TypedDict):
    instrumentId: str
    position: NotRequired[int]


@with_config(STRICT)
class setInstrumentNameParams(TypedDict):
    staff: NotRequired[int]
    part: NotRequired[int]
    name: NotRequired[str]
    shortName: NotRequired[str]


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
class requiredCountParams(TypedDict):
    count: int


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
    offset: NotRequired[Offset]


@with_config(STRICT)
class moveElementParams(TypedDict):
    numElements: NotRequired[int]


@with_config(STRICT)
class selectCurrentMeasureParams(StaffVoiceParams):
    allStaves: NotRequired[bool]
    measure: NotRequired[int]
    tick: NotRequired[int]


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
class stepsParams(TypedDict):
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
    offset: NotRequired[Offset]


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
    offset: NotRequired[Offset]
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
    offset: NotRequired[Offset]
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
    toStaff: NotRequired[int]
    transpose: NotRequired[int]


# --- Range operations ----------------------------------------------------------

@with_config(STRICT)
class tickOrBarRange(TypedDict):
    startMeasure: NotRequired[int]
    endMeasure: NotRequired[int]
    startTick: NotRequired[int]
    endTick: NotRequired[int]


@with_config(STRICT)
class transposeParams(tickOrBarRange):
    semitones: int
    staves: NotRequired[List[int]]
    voices: NotRequired[List[int]]
    chordSymbols: NotRequired[bool]
    keySignatures: NotRequired[bool]


@with_config(STRICT)
class clearRangeParams(tickOrBarRange):
    staves: NotRequired[List[int]]
    voices: NotRequired[List[int]]
    markings: NotRequired[bool]


@with_config(STRICT)
class SectionPart(TypedDict):
    """The music of one staff/voice for replace_section: events or notation."""
    staff: int
    voice: NotRequired[int]
    events: NotRequired[List[VoiceEvent]]
    notation: NotRequired[str]


@with_config(STRICT)
class replaceSectionParams(TypedDict):
    startMeasure: int
    endMeasure: NotRequired[int]
    parts: List[SectionPart]
    clearOtherVoices: NotRequired[bool]


# --- Text, clefs, layout, score info -------------------------------------------------

@with_config(STRICT)
class addTextParams(PositionParams):
    text: str
    kind: NotRequired[Literal["staff", "system", "expression"]]


@with_config(STRICT)
class addChordSymbolParams(TypedDict):
    text: str
    staff: NotRequired[int]
    measure: NotRequired[int]
    tick: NotRequired[int]
    offset: NotRequired[Offset]


@with_config(STRICT)
class addPedalMarksParams(tickOrBarRange):
    staff: NotRequired[int]


@with_config(STRICT)
class addClefParams(TypedDict):
    type: str
    staff: NotRequired[int]
    measure: NotRequired[int]
    tick: NotRequired[int]
    offset: NotRequired[Offset]


@with_config(STRICT)
class addLayoutBreakParams(TypedDict):
    type: Literal["line", "page", "section"]
    measure: int


@with_config(STRICT)
class setScoreInfoParams(TypedDict):
    title: NotRequired[str]
    subtitle: NotRequired[str]
    composer: NotRequired[str]
    lyricist: NotRequired[str]


# action name -> (params type, params required). The plugin accepts exactly these
# actions in processSequence, with exactly these params (plus PYTHON_ONLY_PARAMS).
SEQUENCE_ACTIONS = {
    "getScore": (getScoreParams, False),
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
    "setInstrumentName": (setInstrumentNameParams, True),
    "undo": (stepsParams, False),
    "redo": (stepsParams, False),
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
    "transpose": (transposeParams, True),
    "clearRange": (clearRangeParams, True),
    "replaceSection": (replaceSectionParams, True),
    "addText": (addTextParams, True),
    "addChordSymbol": (addChordSymbolParams, True),
    "addPedalMarks": (addPedalMarksParams, True),
    "addClef": (addClefParams, True),
    "addLayoutBreak": (addLayoutBreakParams, True),
    "setMeasuresPerSystem": (requiredCountParams, True),
    "setScoreInfo": (setScoreInfoParams, True),
}

# Params that exist only on the Python side and are converted before sending.
PYTHON_ONLY_PARAMS = {"writeVoice": {"notation"}}

SequenceStep = Annotated[
    Union[tuple(_action(name, params, required) for name, (params, required) in SEQUENCE_ACTIONS.items())],
    Field(discriminator="action"),
]

ActionSequence = Annotated[List[SequenceStep], Field(min_length=1)]
