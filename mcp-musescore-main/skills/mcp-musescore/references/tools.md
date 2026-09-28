# Public MCP tool reference

This reference describes the tools currently registered by `server.py` and the JSON actions sent to the MuseScore QML plugin. Use the Python function names when calling MCP. Use the camelCase action names only inside `processSequence`.

The server currently registers 51 public tools.

Arguments are strict: an unknown argument (e.g. `tie` on a tool that has no `tie`) or an unknown field inside an event or sequence step is an error, never silently ignored. The MuseScore plugin checks the same way (a per-action list of allowed params), so a raw WebSocket call can't slip an unsupported option through either.

**To write music, use `write_voice`**: a whole passage (notes, chords, rests, ties) in one call and one undo step. `add_note` and `add_rest` are for small corrections.

## Positions: staff, voice, measure, tick

The plugin keeps its own write cursor: a tick (480 ticks per quarter note), a staff (0-based) and a voice (0-3; 0 is MuseScore's voice 1). Write tools accept these optional position arguments:

| Argument | Meaning |
|---|---|
| `staff` | Staff index, 0-based. Sticks: later calls without `staff` keep writing there. |
| `voice` | Voice 0-3. Sticks like `staff`. |
| `measure` | Measure number, **1-based**. Writes at the start of that measure instead of at the cursor. |
| `tick` | Absolute tick. Takes precedence over `measure`. Must be the start of an existing note/rest. |

Every result includes `cursor` (tick, measure, beat, staff, staffName, voice, element at the cursor). Clicking in MuseScore moves the cursor to the clicked note or selected range. Writing past the end of the score appends measures automatically.

The plugin cursor (where the next write goes) and MuseScore's visible selection are kept apart: edits only move the plugin cursor, and the selection is moved to it once at the end of each call or batch, never after each note.

## Durations and ties

Durations are fractions of a whole note, as text `"1/4"` or as `{"numerator": 1, "denominator": 4}`: `"1/1"` whole, `"1/2"` half, `"1/4"` quarter, `"1/8"` eighth, `"3/8"` dotted quarter, `"7/16"` double-dotted quarter.

MuseScore writes one plain, dotted or double-dotted value per note (and silently shortens anything else), so a duration that isn't one such value, or that crosses a barline, is **split and tied**: first at every barline, then greedily into the longest values that fit. `"5/8"` from a bar start becomes 1/2 tied to 1/8; `"9/32"` becomes 1/4 tied to 1/32; a half note starting on beat 4 of 4/4 becomes two tied quarters. The pieces always add up exactly to the requested duration, and the result lists every split (`split`). Rests are split the same way, without ties. Durations that need a tuplet (e.g. `"1/12"`) or are shorter than 1/128 are errors.

A tie joins a note to the **next note of the same pitch in the same staff and voice**. It is made with MuseScore's own tie command after all notes of the call (or atomic batch) are written, and checked afterwards; if a tie can't be made exactly (no such next note, a rest follows, a repeat barline is in between), nothing from the call is kept and the error says why.

## Connection and score inspection

| MCP tool | Parameters | What it does |
|---|---|---|
| `connect_to_musescore` | none | Opens the WebSocket client connection to `ws://localhost:8765`. Returns `{success: bool}`. |
| `ping_musescore` | none | Sends the `ping` action. A healthy plugin returns `result: "pong"`. |
| `get_score` | `format: "lilypond" \| "json" = "lilypond"`, `start_measure: int \| null`, `end_measure: int \| null` | Title, key signature, time signatures, tempos, instrument per staff, cursor, and the music as LilyPond. `format="json"` returns the raw analysis: per-measure elements (ticks, voices, pitches, lyrics with verse and syllabic) and markings (dynamics, tempo, fermatas, text). |

Call `ping_musescore` before a mutation. Call `get_score` before and after a complex edit; use the measure range on long scores.

## Music analysis

All return readable text. Positions are bar.beat, with '+1/16' meaning a 16th after the beat.

| MCP tool | Parameters | What it does |
|---|---|---|
| `analyze_score` | none | Overview: key/mode, meter, tempo (flags suspicious marks), form, main chord loops with Roman numerals, melody range and phrasing, rhythm traits, dynamics. Call it first when asked about a piece. |
| `analyze_harmony` | `start_measure`, `end_measure`, `resolution: beat\|half\|bar = beat`, `include_melody = true` | Chords per bar/beat from all sounding notes (held notes included), bass notes, Roman numerals, loops, chord vocabulary, harmonic rhythm, written chord symbols. |
| `analyze_phrases` | `staff`, `start_measure`, `end_measure` | Melodic phrases: span, pickup, length, range, contour, final note (scale degree) and chord, lyrics, repeat/variation letters. |
| `analyze_structure` | none | Sections with bar ranges and times, section pattern, repeated passages by layer, repeat signs, voltas, jumps/markers, rehearsal marks. |
| `analyze_rhythm` | `start_measure`, `end_measure`, `staff` | Meter/beat grouping, irregular bars, swing, note values, on/off-beat attacks, tuplets, ties over barlines, articulations, syncopations/anticipations, phrase entry points. |
| `get_tempo_map` | none | Tempo marks (quarter and felt-beat BPM), off-grid/redundant marks, rit./accel. spans, tempo per bar, fermatas, breath marks, total time. |

Section names (verse, chorus, bridge) and the key's mode are inferred; treat them as informed guesses and confirm with the user before large structural edits.

## Navigation and selection

| MCP tool | Parameters | What it does |
|---|---|---|
| `get_cursor_info` | none | Cursor position and the note/rest at it. |
| `set_cursor` | `measure`, `tick`, `staff`, `voice` (all optional) | Moves the cursor. |
| `go_to_measure` | `measure: int` (1-based), `staff`, `voice` | Moves to the start of a measure. |
| `go_to_final_measure` | none | Moves to the start of the last measure. |
| `go_to_beginning_of_score` | none | Moves to tick 0 (keeps staff and voice). |
| `next_element` / `prev_element` | `num_elements: int = 1` | Moves by notes/rests in the current staff and voice. `next_element` from the last element moves to the end of the score. |
| `next_staff` / `prev_staff` | none | Moves one staff down/up at the same tick. |
| `select_current_measure` | `all_staves: bool = false` | Selects the cursor's measure (for `delete_selection`). |
| `select_custom_range` | `start_tick`, `end_tick`, `start_staff`, `end_staff` | Selects a tick range; both staff bounds inclusive. Returns elements and LilyPond. |

## Notes, rests, lyrics, and measures

Durations are `"1/4"`-style fractions (or `{"numerator": 1, "denominator": 4}`); see "Durations and ties" above. MIDI pitch 60 is middle C (C4); valid MIDI pitch values are 0-127. All tools in this table except the measure tools also take the position arguments.

| MCP tool | Parameters | What it does |
|---|---|---|
| `write_voice` | `events: list`, position | Writes a passage into one staff and voice from the start position, in one call and **one undo step**; the view is updated once. Events: `{"pitches": [60], "duration": "1/8"}` note, `{"pitches": [48, 52, 55], "duration": "1/2"}` chord, `{"rest": true, "duration": "1/4"}` rest; `"tie": true` ties every pitch to the same pitch in the next event, `"tie": [60]` only the listed pitches. A tie on the last event ties into the note already written right after the passage. Overwrites what is there; appends bars if needed; an empty voice 1-3 (MuseScore's voices 2-4) is filled with rests. The cursor ends after the passage. Can't yet start inside a held note or overwrite tuplets. Result: `startTick`, `endTick`, `startMeasure`, `endMeasure`, `written`, `ties`, `split`, `warnings` (e.g. a held note after the passage was cut to a rest). |
| `add_note` | `pitch: int = 64`, `duration = "1/4"`, `advance_cursor_after_action: bool = true`, `add_to_chord: bool = false`, `tie: bool = false` | Writes a note (split and tied if needed). `add_to_chord=true` adds the pitch to the chord just written (the cursor doesn't move; omit `duration`, or give the chord's own). `tie=true` ties it to the next note of the same pitch in the same voice, which must exist when the call ends (inside an atomic `processSequence`, when the sequence ends). |
| `add_rest` | `duration = "1/4"`, `advance_cursor_after_action: bool = true` | Writes a rest (split if needed). |
| `add_tuplet` | `duration` (total), `ratio = 3/2`, `advance_cursor_after_action: bool = false` | Creates a tuplet filled with rests; the cursor stays at its start so the next `add_note` calls (with the base duration, e.g. 1/8 for an eighth triplet) fill it. Inside a tuplet each note must be one plain or dotted value. |
| `add_lyrics` | `lyrics: list[str]`, `verse: int = 0` | Adds syllables to consecutive notes. `"Hel-"` hyphenates to the next syllable; `"_"` skips a note; chords whose notes are all tied over are skipped (a chord where a new note starts still takes a syllable); an existing lyric in the same verse is replaced. |
| `insert_measure` | `measure: int \| null` | Inserts an empty measure before the given (or the cursor's) measure. |
| `append_measure` | `count: int = 1` | Appends measures to the end of the score. |
| `delete_selection` | `measure: int \| null`, `staff: int \| null` | Deletes the current selection; with `measure`, clears that measure on `staff` or on all staves. Destructive: verify immediately. |
| `undo` | `steps: int = 1` | Undoes like Ctrl+Z and restores the cursor. |

## Markings, time and tempo

| MCP tool | Parameters | What it does |
|---|---|---|
| `add_dynamic` | `dynamic: str` + position | Adds pp, p, mp, mf, f, ff, sfz, fp, … under the note/rest; replaces an existing one there. |
| `add_fermata` | position | Adds a fermata to the note/rest. |
| `set_tempo` | `bpm: float`, `text: str \| null`, `measure`, `tick` | Adds a tempo marking (quarter = bpm) on the top staff; replaces one at the same position. |
| `set_time_signature` | `numerator = 4`, `denominator = 4`, `measure: int \| null` | Sets the time signature from that (or the cursor's) measure onwards. |

## Form and structure editing

| MCP tool | Parameters | What it does |
|---|---|---|
| `copy_measures` | `start_measure`, `end_measure`, `to_measure`, `insert = true`, `staff` | Copies bars (notes, lyrics, markings) via MuseScore's clipboard, into inserted bars or over existing ones. Undo steps = inserted bars + 1 (reported as `undoSteps`). |
| `delete_measures` | `start_measure`, `end_measure` | Removes whole bars. |
| `insert_measure` | `measure`, `count = 1` | Inserts empty bars (one undo step each). |
| `add_repeat` / `remove_repeat` | `start_measure`, `end_measure`, `times = 2` | Repeat barlines. |
| `add_marker` | `type: segno\|coda\|fine\|to coda\|varsegno\|varcoda`, `measure` | Navigation marker. |
| `add_jump` | `type: D.C.\|D.C. al Fine\|D.C. al Coda\|D.S.\|D.S. al Fine\|D.S. al Coda`, `measure` | Jump at the end of the bar. |
| `add_section_label` | `text`, `measure`, `tick` | Rehearsal mark. |
| `set_key_signature` | `fifths`, `measure`, `mode`, `staff` | Key change from a bar on. |
| `add_tempo_change` | `type`, `measure`/`tick`, `end_measure`/`end_tick`, `target_bpm`, `factor`, `a_tempo` | rit./accel. as a visible marking plus hidden per-beat tempo marks (one undo step). |
| `add_slur`, `add_hairpin`, `add_articulation` | range (`start_tick`/`end_tick` or `start_measure`/`end_measure`), `staff`, `type` | Selection-based MuseScore actions; each is one undo step. |
| `remove_marking` | `kind`, `tick`/`measure`, `staff` | Removes a marking at a position (lines by start). |

Voltas can't be added through the plugin API; add them from the palette or write the passage out with `copy_measures`.
`processSequence(..., atomic=True)` groups steps into one undo step (not allowed for selection-based actions).

## Staff and instruments

| MCP tool | Parameters | What it does |
|---|---|---|
| `add_instrument` | `instrument_id: str` | Appends an instrument (e.g. `violin`, `flute`, `piano`). Warns if the id was unknown and MuseScore substituted another. |
| `remove_instrument` | `part: int \| null` or `staff: int \| null` | Removes a whole part. Refuses to remove the only part. Undo restores it. Requires MuseScore 4.7+. |
| `set_staff_mute` | `staff: int`, `mute: bool` | Not reliable in MuseScore 4; prefer the mixer. |
| `set_instrument_sound` | `staff: int`, `instrument_id: str` | Replaces the instrument of the staff's part. |

## Batch execution

| MCP tool | Parameters | What it does |
|---|---|---|
| `processSequence` | `sequence: list[object]`, `atomic: bool = false` | Runs camelCase actions in order in one round trip; every step is checked before any runs. Each step is its own undo step; the sequence stops at the first failing step and reports its index. With `atomic=true` the whole sequence is one undo step and nothing is kept if a step fails; `addNote` ties are made at the end, so the next note may come from a later step. Either way steps do no UI work: MuseScore's view is updated once, at the end. |

The accepted inner actions are:

```text
getScore, addNote, addRest, addTuplet, writeVoice, addLyrics, appendMeasure,
insertMeasure, deleteSelection, getCursorInfo, setCursor, goToMeasure,
goToBeginningOfScore, goToFinalMeasure, nextElement, prevElement, nextStaff,
prevStaff, selectCurrentMeasure, selectCustomRange, setTimeSignature, setTempo,
addDynamic, addFermata, addInstrument, removeInstrument, setStaffMute,
setInstrumentSound, undo, addRepeat, removeRepeat, addMarker, addJump,
addRehearsalMark, setKeySignature, addGradualTempoChange, removeMarking,
addSlur, addHairpin, addArticulation, deleteMeasures, copyMeasures
```

Each action uses a `params` object with the camelCase names of the matching tool arguments (`writeVoice` takes `events` plus position, `addNote` takes `pitch`, `duration`, `tie`, `addToChord`, ...). Unknown params are errors. Example:

```json
{
  "sequence": [
    {"action": "setCursor", "params": {"measure": 1, "staff": 2}},
    {"action": "writeVoice", "params": {"events": [
      {"pitches": [60], "duration": "1/4", "tie": true},
      {"pitches": [60], "duration": "1/8"},
      {"pitches": [62], "duration": "1/8"},
      {"rest": true, "duration": "1/2"}
    ]}},
    {"action": "addNote", "params": {"pitch": 64, "duration": "1/4", "measure": 2}},
    {"action": "addNote", "params": {"pitch": 67, "addToChord": true}}
  ]
}
```
