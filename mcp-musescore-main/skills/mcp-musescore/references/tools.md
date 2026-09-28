# Public MCP tool reference

This reference describes the tools registered by `server.py` and the JSON actions sent to the MuseScore QML plugin. Use the Python (snake_case) tool names when calling MCP; the camelCase action names only appear inside `process_sequence`.

The server currently registers 71 public tools.

Arguments are strict: an unknown argument (e.g. `tie` on a tool that has no `tie`) or an unknown field inside an event, part or sequence step is an error, never silently ignored. The MuseScore plugin checks the same way (a per-action list of allowed params), so a raw WebSocket call can't slip an unsupported option through either.

## How to work

- **Existing score**: `get_score` first. Its default compact view uses the same notation that `write_voice` and `replace_section` take as `notation`, so what you read can be edited and written back. Big scores: read bar ranges (`start_measure`/`end_measure`) or single staves (`staves`). `analyze_score` and the other `analyze_*` tools give a musical overview.
- **Writing**: `write_voice` (one staff and voice, one passage) or `replace_section` (whole bars for several staves at once; also appends bars at the end). Each call is one undo step and is checked before anything is written. Several different edits as one undo step: `process_sequence` with `atomic=true`.
- **From scratch**: the user creates a score in MuseScore (File → New, with the instruments), or you shape the open one with `add_instrument` / `remove_instrument` / `set_instrument_name` (`list_instruments` gives ids, clefs, transpositions and ranges), then `set_score_info`, `set_key_signature`, `set_time_signature`, `set_tempo`, and write. `open_score` opens a file when no score is open.
- **The user edits too**: every result carries `scoreVersion`. Pass `expected_version` (the version you last read) to the main edit tools; if the score changed in between, nothing is done and `get_changes_since` shows what changed and the music of those bars now.
- `undo` / `redo` work like Ctrl+Z / Ctrl+Y. `get_selection` tells what the user selected in MuseScore.

## The compact notation

Used by `get_score` (reading) and by `notation=` in `write_voice`, `replace_section` parts and `writeVoice` steps (writing).

```
bar 1 (4/4) tempo q=72 "Lento"
  s0: r:q G4(mf "Hel-") Bb4("lo") Eb5~
  s1: [Eb4 G4 Bb4]:h [Eb4 Ab4 C5](chord=Ab/Eb)
  s1v1: r:h C3
bar 2 = bar 1
bars 3-4: rests
```

| Element | Syntax |
|---|---|
| Note | `C4` is middle C (MIDI 60). Accidentals `#` `##` `b` `bb` (`F#3`, `Bb5`); names keep their spelling (`F#4` is not `Gb4`). A MIDI number (`60`) also works. Pitches are concert pitch. |
| Chord / rest | `[C4 E4 G4]` / `r` |
| Duration | After `:`. Letters `w` 1/1, `h` 1/2, `q` 1/4, `e` 1/8, `s` 1/16, `t` 1/32, `x` 1/64 (`b` 2/1) with `.` or `..` for dotted values (`q.` = 3/8), or a fraction (`3/8`, `5/8`). **A duration carries over** to the following notes until another is given. |
| Tie | `~` after a note or chord ties it to the next one (`C4:h~ C4:q`); inside a chord only the marked pitches are tied (`[C4~ E4]:h`). |
| Tuplet | `{3:2 C4:e D4 E4}` = three eighths in the time of two. The notes must add up to 3 × one note value. |
| Markings | In parentheses right after a note or rest: a dynamic (`mf`, `sfz`, ...), articulations (`staccato` `staccatissimo` `tenuto` `accent` `marcato` `portato` `accent-staccato` `marcato-staccato` `stress` `unstress`; short forms `stacc` `ten` `acc` `marc` `>` `^` `-` `.`), ornaments (`trill` `mordent` `short-trill` `turn` `inverted-turn`), `up-bow` `down-bow` `harmonic` `snap-pizzicato` `open` `stopped`, `fermata`, a lyric syllable in quotes (`"Hel-"` continues the word on the next note, `"_"` leaves a note without one), `text="dolce"` (expression text), `chord=Cmaj7` (chord symbol). A rest can only take `fermata` (and `text=`, `chord=`, a dynamic). |
| Bar lines | `\|` is optional. When given, the bars between two `\|` must all have the same length (a check against miscounting). |

In the compact view: `s1:` is staff 1 voice 0, `s1v1:` staff 1 voice 1. Staves with only rests in a bar are left out. `@3/8` is a position in the bar as a fraction of a whole note (the same unit as the `offset` argument): inside a voice line it means the next note starts there (the voice is empty before it; when writing, use a rest or start at that offset instead); in `marks:` lines it places markings that are not on a note, and lines such as slurs (`slur s0@1/4-bar 3@1/2`). A bar with the same music as an earlier listed bar is shown as `bar 9 = bar 1` (runs: `bars 9-12 = bars 1-4`); runs of empty bars as `bars 5-8: rests`. The bar header shows time signature changes, `|:` / `:|` repeats, markers and jumps, rehearsal marks `[A]`, key and clef changes, and visible tempo marks. Grace notes are reported (`+2 grace`) but can't be written.

## Positions: staff, voice, measure, offset, tick

The plugin keeps its own write cursor: a tick (480 ticks per quarter note), a staff (0-based) and a voice (0-3; 0 is MuseScore's voice 1). Write tools accept these optional position arguments:

| Argument | Meaning |
|---|---|
| `staff` | Staff index, 0-based (see `get_score`). Sticks: later calls without `staff` keep writing there. |
| `voice` | Voice 0-3. Sticks like `staff`. |
| `measure` | Measure number, **1-based**. |
| `offset` | With `measure`: the position inside it, as a fraction of a whole note from the barline (`"0"`, `"1/4"` = beat 2 in 4/4, `"3/8"`). |
| `tick` | Absolute tick instead of `measure`/`offset`. |

Writing can start anywhere: a note or rest held across the start is shortened to end there (the part before stays; a note is never re-struck; the result's `warnings` say so), and an empty voice 1-3 is filled with rests up to it. Texts, chord symbols, clefs, tempo marks and dynamics placed inside a rest split the rest; inside a sounding note they are refused (that would change the music). Writing past the end of the score appends bars. Writing into the middle of an existing tuplet isn't supported (clear it first).

Every edit result includes `cursor` (tick, measure, beat, staff, staffName, voice, element at the cursor) and `scoreVersion`. Clicking in MuseScore moves the cursor to the clicked note or selected range. The plugin cursor and MuseScore's visible selection are kept apart: edits only move the plugin cursor, and the selection is moved to it once at the end of each call or batch.

## Durations and ties

Durations are fractions of a whole note: in events as text `"1/4"` or `{"numerator": 1, "denominator": 4}`; in notation also as letters (see above).

MuseScore writes one plain, dotted or double-dotted value per note (and silently shortens anything else), so a duration that isn't one such value, or that crosses a barline, is **split and tied**: first at every barline, then greedily into the longest values that fit. `"5/8"` from a bar start becomes 1/2 tied to 1/8; a half note starting on beat 4 of 4/4 becomes two tied quarters. The pieces always add up exactly, and the result lists every split (`split`). Rests are split the same way, without ties. Durations that need a tuplet (e.g. `"1/12"`) are errors outside tuplets.

A tie joins a note to the **next note of the same pitch in the same staff and voice**. It is made with MuseScore's own tie command after all notes of the call (or atomic batch) are written, and checked afterwards; if a tie can't be made exactly (no such next note, a rest follows, a repeat barline is in between), nothing from the call is kept and the error says why.

## Score versions

Every change to the score raises its version: your edits and the user's edits in MuseScore. MuseScore doesn't tell plugins about edits, so the plugin compares a digest of every bar when it reads the score (at `get_score`, `get_version`, `get_changes_since`, and edits with `expected_version`) and logs which bars changed and who changed them (`mcp` or `user`). The log keeps the last 500 changes; versions start at a random number each time the plugin starts, so a version from an earlier run is never mistaken for a current one. Tempo marks change the playback time of later bars but only count as a change of their own bar.

## Reading the score

| MCP tool | Parameters | What it does |
|---|---|---|
| `get_score` | `format: compact\|lilypond\|json = compact`, `start_measure`, `end_measure`, `staves` | The score: title, instruments (staff numbers, clefs, transposition, ranges), key, time signatures, tempo, the music bar by bar, the version and the cursor. Compact: the notation above (at most 200 bars per call; the result says how to read on). `lilypond`: LilyPond. `json`: the raw data (ticks, voices, every note and marking). |
| `get_version` | none | The current version. |
| `get_changes_since` | `version*`, `show_music = true` | The changes since a version (who, which action, which bars) and the music of the changed bars now. Says so when the log can't cover that version (too old, another plugin run, another score opened): read the score again. |
| `get_selection` | `show_music = true` | What is selected in MuseScore: a range (bars, staves, with its music) or elements (type, tick, bar, staff, voice, pitch). `fromUser` is false when the selection only shows the plugin's cursor. |
| `check_score` | none | Bars whose voices don't add up (corrupted), per staff. |
| `list_instruments` | `query`, `group`, `instrument_id` | MuseScore 4.7.5's instruments: id (for `add_instrument`), name, clef(s), transposition, comfortable and full range (sounding MIDI pitches). With `instrument_id`: details and, for drum kits, the drum map (MIDI pitch → drum, voice). |
| `open_score` | `path*` | Opens a file (mscz, MusicXML, MIDI, ...) when no score is open. With a score open it is refused: MuseScore 4 would open the file in a new window, which the plugin can't reach. |
| `connect_to_musescore` | none | Opens the WebSocket connection to `ws://localhost:8765`. |
| `ping_musescore` | none | A healthy plugin answers `"pong"`. |

## Music analysis

All return readable text. Positions are bar.beat, with '+1/16' meaning a 16th after the beat.

| MCP tool | Parameters | What it does |
|---|---|---|
| `analyze_score` | none | Overview: key/mode, meter, tempo (flags suspicious marks), form, main chord loops with Roman numerals, melody range and phrasing, rhythm traits, dynamics. |
| `analyze_harmony` | `start_measure`, `end_measure`, `resolution: beat\|half\|bar = beat`, `include_melody = true` | Chords per bar/beat from all sounding notes, bass notes, Roman numerals, loops, chord vocabulary, harmonic rhythm, written chord symbols. |
| `analyze_phrases` | `staff`, `start_measure`, `end_measure` | Melodic phrases: span, pickup, length, range, contour, final note and chord, lyrics, repeat/variation letters. |
| `analyze_structure` | none | Sections with bar ranges and times, section pattern, repeated passages, repeat signs, voltas, jumps/markers, rehearsal marks. |
| `analyze_rhythm` | `start_measure`, `end_measure`, `staff` | Meter/beat grouping, irregular bars, swing, note values, on/off-beat attacks, tuplets, ties over barlines, articulations, syncopations. |
| `get_tempo_map` | none | Tempo marks, off-grid/redundant marks, rit./accel. spans, tempo per bar, fermatas, breath marks, total time. |

Section names and the key's mode are inferred; treat them as informed guesses.

## Writing music

| MCP tool | Parameters | What it does |
|---|---|---|
| `write_voice` | `notation` or `events`, position, `expected_version` | Writes a passage into one staff and voice from the start position, in one call and **one undo step**. Events: `{"pitches": [60], "duration": "1/8"}`, `{"pitches": ["C4", "E4", "G4"], "duration": "1/2"}`, `{"rest": true, "duration": "1/4"}`, `"tie": true` or `"tie": [60]`, `{"tuplet": "3:2", "events": [...]}`, markings `"dynamic"`, `"articulations"`, `"lyric"`, `"text"`, `"chord"`. A tie on the last note ties into the note already written right after the passage. Result: `startTick`, `endTick`, `startMeasure`, `endMeasure`, `written`, `ties`, `split`, `warnings`. The cursor ends after the passage. |
| `replace_section` | `start_measure*`, `parts*`, `end_measure`, `clear_other_voices = true`, `expected_version` | New music for whole bars, any number of staves and voices, one undo step. Each part: `{"staff": 0, "voice": 0, "notation": "..."}` (or `events`), filling the bars exactly. Other voices of those staves are cleared unless `clear_other_voices` is false; other staves are untouched. `start_measure` may be (number of bars + 1): the bars are appended. |
| `add_note` | `pitch = 64` (MIDI or name), `duration = "1/4"`, `advance_cursor_after_action = true`, `add_to_chord = false`, `tie = false`, position, `expected_version` | One note (split and tied if needed). `add_to_chord=true` adds the pitch to the chord just written (omit `duration`). `tie=true` ties it to the next note of the same pitch, which must exist when the call ends (in an atomic sequence: when the sequence ends). |
| `add_rest` | `duration = "1/4"`, `advance_cursor_after_action = true`, position, `expected_version` | One rest (split if needed). |
| `add_tuplet` | `duration` (total), `ratio = 3/2`, `advance_cursor_after_action = false`, position | An empty tuplet; the next `add_note` calls fill it. (`write_voice` writes tuplets directly.) |
| `add_lyrics` | `lyrics: list[str]`, `verse = 0`, position, `expected_version` | Syllables on consecutive notes. `"Hel-"` hyphenates; `"_"` skips a note; chords tied over are skipped; an existing lyric in the verse is replaced. |
| `undo` / `redo` | `steps = 1` | Like Ctrl+Z / Ctrl+Y; the cursor follows. A new edit clears what can be redone. |

## Changing what is there

| MCP tool | Parameters | What it does |
|---|---|---|
| `transpose` | `semitones*`, range, `staves`, `voices`, `chord_symbols = true`, `key_signatures = false`, `expected_version` | Chromatic transposition, one undo step, notes respelled (D major +2 → E major). Notes tied into/out of the range move with it; chord symbols move too. `key_signatures=true` (whole bars) also moves the key signatures and restores the old key after the range: use it to change the key of a piece or section. |
| `clear_range` | range, `staves`, `voices`, `markings = true`, `expected_version` | Empties a range: voice 0 becomes rests (whole bars get one bar rest), voices 1-3 disappear. A note held into the range keeps its part before; one that starts inside and lasts past the end is removed (reported). `markings` also removes dynamics, texts, chord symbols, fermatas. The bars stay. |
| `copy_measures` | `start_measure*`, `end_measure*`, `to_measure*`, `insert = true`, `staff`, `to_staff`, `transpose`, `expected_version` | Copies bars (notes, lyrics, markings) via MuseScore's clipboard, into inserted bars or over existing ones; `to_staff` pastes onto other staves; `transpose` then moves the copy. Undo steps: one per inserted bar, one for the paste, one for the transposition (`undoSteps`). |
| `delete_measures` | `start_measure*`, `end_measure`, `expected_version` | Removes whole bars. |
| `insert_measure` | `measure`, `count = 1`, `expected_version` | Inserts empty bars (one undo step each). |
| `append_measure` | `count = 1` | Appends bars. |
| `delete_selection` | `measure`, `staff` | Deletes the selection; with `measure`, clears that bar. `clear_range` is more precise. |

A range is `start_measure`/`end_measure` (whole bars, inclusive; `end_measure` defaults to `start_measure`) or `start_tick`/`end_tick` (end exclusive).

## Markings, text and layout

| MCP tool | Parameters | What it does |
|---|---|---|
| `add_dynamic` | `dynamic*`, position | pp, p, mp, mf, f, ff, sfz, fp, ...; replaces one at the same place. |
| `add_fermata` | position | A fermata on the note/rest. |
| `add_text` | `text*`, `kind: staff\|system\|expression = staff`, `staff`, position | Staff text ("pizz.", "solo"), system text ("Tutti"), expression ("dolce"). |
| `add_chord_symbol` | `text*`, `staff`, position | A chord symbol ("Cmaj7", "F#m7b5", "G7/B"), replacing one at the same place. |
| `add_pedal_marks` | range, `staff` | "Ped." at the start and the release at the end. Symbols only: they don't sustain on playback (the plugin API can't add pedal lines). |
| `add_clef` | `type*`, `staff`, position | treble, bass, alto, tenor, soprano, mezzo-soprano, baritone, treble 8vb/8va/15ma, bass 8vb/8va, percussion. |
| `set_tempo` | `bpm*`, `beat_unit = "1/4"`, `text`, position | A tempo mark (changes playback), replacing one at the same place; `beat_unit` "3/8" for a dotted-quarter beat in 6/8. |
| `add_tempo_change` | `type*`, start, `end_measure`/`end_tick`, `target_bpm`, `factor`, `a_tempo` | rit./accel.: a visible marking plus hidden tempo marks that change playback smoothly (one undo step). |
| `set_time_signature` | `numerator = 4`, `denominator = 4`, `measure` | From that bar on. |
| `set_key_signature` | `fifths*`, `measure`, `mode`, `staff` | From a bar on (concert key; transposing instruments get their written key). |
| `add_slur`, `add_hairpin`, `add_articulation` | range, `staff`, `type` | Selection-based MuseScore actions; each is one undo step. (`write_voice` markings are the easier way to add articulations.) |
| `add_repeat` / `remove_repeat` | `start_measure*`, `end_measure*`, `times = 2` | Repeat barlines. |
| `add_marker` | `type*`, `measure*` | segno, coda, fine, to coda, varsegno, varcoda. |
| `add_jump` | `type*`, `measure*` | D.C., D.C. al Fine, D.C. al Coda, D.S., D.S. al Fine, D.S. al Coda. |
| `add_section_label` | `text*`, position | A rehearsal mark ("Verse", "A"). |
| `remove_marking` | `kind*`, position, `staff` | Removes a marking at a position (tempo, dynamic, fermata, text, rehearsalMark, chordSymbol, breath; lines by their start: slur, hairpin, volta, gradualTempoChange). |
| `add_layout_break` | `type: line\|page\|section`, `measure*` | A break after the bar. |
| `set_measures_per_system` | `count*` | Every system holds `count` bars (system locks); 0 back to automatic. Not in atomic sequences. |

Voltas and pedal lines can't be added through the plugin API.

## Score, instruments, files

| MCP tool | Parameters | What it does |
|---|---|---|
| `set_score_info` | `title`, `subtitle`, `composer`, `lyricist` | The texts at the top of the first page (replacing what is there) and the score properties. Undo restores the texts, not the file properties. |
| `add_instrument` | `instrument_id*`, `position` | Adds an instrument (part) at the bottom or at a part index. |
| `set_instrument_name` | `name`, `short_name`, `staff` or `part` | Renames a part ("Violin I" / "Vln. I"). |
| `remove_instrument` | `part` or `staff` | Removes a whole part (not the only one). |
| `set_instrument_sound` | `staff*`, `instrument_id*` | Replaces the instrument of the staff's part. |
| `set_staff_mute` | `staff*`, `mute*` | Not reliable in MuseScore 4; prefer the mixer. |
| `export_score` | `path*`, `format = pdf` | pdf, png, svg, mid, musicxml, mxl, mp3, wav, ogg, flac, mscz, ... to a path on the computer running MuseScore (the folder must exist). |
| `save_score` | none | Like Ctrl+S (a never-saved score opens the Save dialog). |

## Navigation and selection (low level)

| MCP tool | Parameters | What it does |
|---|---|---|
| `get_cursor_info` | none | Cursor position and the note/rest at it. |
| `set_cursor` | `measure`, `offset`, `tick`, `staff`, `voice` | Moves the cursor. |
| `go_to_measure` | `measure*`, `offset`, `staff`, `voice` | Moves to a bar. |
| `go_to_final_measure` / `go_to_beginning_of_score` | none | Moves to the last bar / tick 0. |
| `next_element` / `prev_element` | `num_elements = 1` | Moves by notes/rests in the current staff and voice. |
| `next_staff` / `prev_staff` | none | Moves one staff down/up. |
| `select_current_measure` | `all_staves = false` | Selects the cursor's bar. |
| `select_custom_range` | `start_tick*`, `end_tick*`, `start_staff*`, `end_staff*` | Selects a range (staff bounds inclusive); returns its elements and LilyPond. |

## Batch execution

| MCP tool | Parameters | What it does |
|---|---|---|
| `process_sequence` | `sequence*`, `atomic = false`, `expected_version` | Runs camelCase actions in one round trip; every step is checked before any runs. Each step is its own undo step and the sequence stops at the first failing step (reporting which). With `atomic=true` the whole sequence is one undo step and nothing is kept if a step fails; `addNote` ties are made at the end. MuseScore's view is updated once, at the end. |

Steps are `{"action": "writeVoice", "params": {...}}` with the tool's parameters in camelCase (`writeVoice` also takes `notation`; `replaceSection` parts may use `notation`). The tool description lists every action with its params. Actions that change the selection can't be in an atomic sequence: undo, redo, deleteSelection, insertMeasure, selectCurrentMeasure, selectCustomRange, addSlur, addHairpin, addArticulation, deleteMeasures, copyMeasures, setMeasuresPerSystem.

```json
{
  "atomic": true,
  "expected_version": 1234500,
  "sequence": [
    {"action": "writeVoice", "params": {"staff": 0, "measure": 5, "notation": "C5:q D5 E5 F5 | G5:w"}},
    {"action": "writeVoice", "params": {"staff": 1, "measure": 5, "voice": 0, "notation": "[C3 G3]:w | [B2 G3]:w"}},
    {"action": "addDynamic", "params": {"dynamic": "p", "measure": 5, "staff": 0}},
    {"action": "addChordSymbol", "params": {"text": "G7", "measure": 6}}
  ]
}
```
