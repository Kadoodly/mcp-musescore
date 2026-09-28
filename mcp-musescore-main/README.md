# MuseScore MCP Server

A Model Context Protocol (MCP) server that provides programmatic control over MuseScore, via a WebSocket-based plugin system. This allows AI assistants like Claude to compose music, add lyrics, navigate scores, and control MuseScore directly.

![Demo GIF](./assets/mcp-muse.gif)

## Prerequisites

- MuseScore Studio 4.7 or newer (the plugin uses `removeParts`, which was added to the plugin API in 4.7)
- Python 3.11+
- Claude Desktop or compatible MCP client

## Setup

### 1. Install the MuseScore Plugin

Copy `musescore-mcp-websocket.qml` into your MuseScore plugins folder:

**Windows**: `%USERPROFILE%\Documents\MuseScore4\Plugins\` (if OneDrive syncs your Documents folder: `%USERPROFILE%\OneDrive\Documents\MuseScore4\Plugins\`)
**macOS**: `~/Documents/MuseScore4/Plugins/`
**Linux**: `~/Documents/MuseScore4/Plugins/`

If unsure, MuseScore shows the folder under **Edit → Preferences → Folders → Plugins** (macOS: **MuseScore Studio → Preferences**).

### 2. Enable the Plugin in MuseScore

1. Restart MuseScore (it only scans the plugins folder at startup)
2. Go to **Plugins → Manage plugins…**
3. Find **musescore-mcp-websocket** and enable it

MuseScore 4 lists the plugin under its file name, **musescore-mcp-websocket**, not "MuseScore API Server".

### 3. Setup Python Environment

The folder is `mcp-musescore` when cloned, or `mcp-musescore-main` when downloaded as a ZIP from GitHub.

**Windows (PowerShell):**
```powershell
git clone https://github.com/ghchen99/mcp-musescore.git
cd mcp-musescore
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**macOS / Linux:**
```bash
git clone https://github.com/ghchen99/mcp-musescore.git
cd mcp-musescore
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

`requirements.txt` pins `mcp[cli]<2`: the server imports `mcp.server.fastmcp`, which was removed in mcp 2.x. The separate `fastmcp` package is not needed.

### 4. Configure Claude Desktop

Add to your Claude Desktop configuration file:

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Windows (note the doubled backslashes in JSON):
```json
{
  "mcpServers": {
    "musescore": {
      "command": "C:\\Users\\YOU\\Downloads\\mcp-musescore-main\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\Users\\YOU\\Downloads\\mcp-musescore-main\\server.py"
      ]
    }
  }
}
```

macOS / Linux:
```json
{
  "mcpServers": {
    "musescore": {
      "command": "/path/to/mcp-musescore/.venv/bin/python",
      "args": [
        "/path/to/mcp-musescore/server.py"
      ]
    }
  }
}
```

**Note**: Update the paths to match your actual project location.

## Running the System

### Order of Operations

1. **Start MuseScore** and open (or create) a score
2. **Run the plugin**: **Plugins → musescore-mcp-websocket**. Nothing visible happens; it starts listening on port 8765
3. **Start (or restart) Claude Desktop**

### After changing the code

- **Plugin (`.qml`) changes**: copy the file into the plugins folder again, quit and restart MuseScore, then run **Plugins → musescore-mcp-websocket** again.
- **Python changes**: fully quit Claude Desktop (on Windows, also right-click its tray icon → **Quit**; closing the window is not enough) and reopen it.

### Development and Testing

For development, use the MCP development tools:

```bash
# Test your server
mcp dev server.py
```

Offline tests (no MuseScore needed; the plugin tests need [node](https://nodejs.org)):

```bash
pip install -r requirements-dev.txt
python -m pytest            # Python logic, strict validation, and the plugin's JavaScript
node syntax_check.js        # syntax check of the plugin (QML converted to JS, node --check)
```

The plugin's JavaScript is also run against a small mock of MuseScore's plugin API (`tests/js/`). That tests the plugin's own logic, not MuseScore: the live test does that. To run it, open a score saved as `mcp test` (a piano score, so there are two staves), start the plugin, and run:

```bash
python tests/live/test_step1.py            # keeps its bars so you can check them; --cleanup deletes them
```

It refuses to run unless every MuseScore window title contains "mcp test", only writes into bars it appends after the last bar, and checks that the original bars are unchanged. See the docstring of `tests/live/test_step1.py` for details.

### Viewing Console Output

To see MuseScore plugin console output, run MuseScore from terminal:

**macOS**:
```bash
/Applications/MuseScore\ 4.app/Contents/MacOS/mscore
```

**Windows**:
```powershell
& "C:\Program Files\MuseScore 4\bin\MuseScore4.exe"
```

**Linux**:
```bash
musescore4
```

## Features

This MCP server provides comprehensive MuseScore control.

**🌟 NEW in this fork:** Built-in automatic, flawless multi-voice Polyphony & Temporal layout mapping to LilyPond!

### The cursor: staff, voice and position

The plugin keeps its own write cursor: a tick position (480 ticks per quarter note), a staff (0-based) and a voice (0–3; 0 is MuseScore's voice 1). Every write tool writes at the cursor, or at an explicit position:

- `staff` / `voice`: write on that staff/voice. The choice sticks, so later calls keep writing there.
- `measure` (1-based) or `tick`: write at that position instead of at the cursor.

Clicking a note or selecting a range in MuseScore moves the cursor there. Measures are appended automatically when writing past the end of the score.

The score and MuseScore's visible selection are kept apart: edits change the score and move the plugin cursor; the selection is moved to the cursor once, at the end of a call or batch, not after every note.

### Strict arguments

Unknown arguments are errors, at the MCP layer and inside the plugin (which has a list of allowed params per action). If Claude sends `tie=true` to a tool that has no `tie`, the call fails instead of silently doing something else.

### **Navigation & Cursor Control**
- `get_cursor_info()` - Cursor position (measure, beat, tick, staff, voice) and the note/rest there
- `set_cursor(measure, tick, staff, voice)` - Move the cursor; every argument optional
- `go_to_measure(measure, staff, voice)` - Navigate to a measure (1-based)
- `go_to_beginning_of_score()` / `go_to_final_measure()` - Navigate to start/end
- `next_element(n)` / `prev_element(n)` - Move by notes/rests in the current staff and voice
- `next_staff()` / `prev_staff()` - Move between staves
- `select_current_measure(all_staves)` - Select the cursor's measure
- `select_custom_range(start_tick, end_tick, start_staff, end_staff)` - Select a tick range; both staff bounds inclusive

### **Polyphony & LilyPond Integration**
- **Temporal Rhythm Padding**: Voices with gaps or rests automatically receive LilyPond spacer sequences (`s4.`) to hold their mathematical place accurately.
- **Concurrent Voice Rendering**: Full 4-voice (`\voiceOne`, `\voiceTwo`, etc.) arrays correctly structured and sharded per staff for advanced Agent processing.

### **Note & Rest Creation**
All of these accept optional `staff`, `voice`, `measure` and `tick`.
- `write_voice(events)` - **The main way to write music**: a whole passage of notes, chords, rests and ties in one staff and voice, in one call and one undo step. Events look like `{"pitches": [60], "duration": "1/8"}`, `{"pitches": [48, 52, 55], "duration": "1/2", "tie": true}` or `{"rest": true, "duration": "1/4"}`.
- `add_note(pitch, duration, advance_cursor_after_action, add_to_chord, tie)` - Add a note with MIDI pitch, for small corrections. Sequential notes write a melody; `add_to_chord=True` stacks a pitch on the chord just written; `tie=True` ties it to the next note of the same pitch in that voice.
- `add_rest(duration, advance_cursor_after_action)` - Add rests
- `add_tuplet(duration, ratio)` - Create a tuplet; the cursor stays at its start so the next `add_note` calls fill it

Durations are fractions of a whole note (`"1/4"`, `"3/8"`, ...). MuseScore only writes one plain, dotted or double-dotted value per note, so any other duration, or one crossing a barline, is split (at barlines first, then into the longest values that fit) and the pieces are tied: `"5/8"` becomes a half tied to an eighth. Nothing is shortened silently; durations that need a tuplet (`"1/12"`) are an error.

### **Markings**
- `add_dynamic(dynamic)` - pp, p, mp, mf, f, ff, sfz, fp, …
- `add_fermata()` - Fermata on the note/rest at the cursor
- `set_tempo(bpm, text)` - Tempo marking, e.g. `set_tempo(96, "Allegretto")`
- `add_tempo_change(type, measure, end_measure, target_bpm, factor, a_tempo)` - rit./rall./accel. with real playback change (visible marking + hidden tempo steps)
- `set_time_signature(numerator, denominator, measure)` - Change time signature
- `add_slur(...)`, `add_hairpin(type, ...)`, `add_articulation(type, ...)` - Over a tick range or whole bars on one staff
- `remove_marking(kind, tick/measure, staff)` - Remove a tempo mark, dynamic, fermata, text, label, chord symbol, slur, hairpin, …

### **Measure Management & Form**
- `insert_measure(measure, count)` - Insert empty measures before a measure
- `append_measure(count)` - Add measures to end of score
- `delete_selection(measure, staff)` - Delete the current selection, or clear a measure
- `delete_measures(start_measure, end_measure)` - Remove whole bars
- `copy_measures(start_measure, end_measure, to_measure, insert, staff)` - Copy a passage (e.g. write out a reprise), into new bars or over existing ones
- `add_repeat(start_measure, end_measure, times)` / `remove_repeat(...)` - Repeat barlines
- `add_marker(type, measure)` - Segno, Coda, Fine, To Coda
- `add_jump(type, measure)` - D.C., D.S., al Fine, al Coda
- `add_section_label(text, measure)` - Rehearsal mark such as "Chorus"
- `set_key_signature(fifths, measure, mode, staff)` - Key change

### **Lyrics**
- `add_lyrics(lyrics, verse)` - Add syllables to consecutive notes. End a syllable with `-` to hyphenate it with the next (`["Twin-", "kle"]`); `_` skips a note. Re-adding replaces existing lyrics in that verse instead of duplicating them.

### **Instruments**
- `add_instrument(instrument_id)` - Add an instrument, e.g. `"violin"`, `"flute"`, `"piano"`
- `remove_instrument(part | staff)` - Remove an instrument by part index or any of its staff indices
- `set_instrument_sound(staff, instrument_id)` - Replace the instrument on a staff's part

### **Music Analysis**
These read the score and describe it musically, so Claude has reliable facts before it edits or writes. Positions are given as bar.beat (`4.2+3/16` = bar 4, beat 2, plus three 16ths).
- `analyze_score()` - Overview: key and mode, meter, tempo, form, main progressions, melody and rhythm traits, dynamics
- `analyze_harmony(start_measure, end_measure, resolution, include_melody)` - Chords per beat with bass notes and Roman numerals, chord loops, harmonic rhythm, written chord symbols
- `analyze_phrases(staff, start_measure, end_measure)` - Melodic phrases with pickups, range, contour, ending note/chord, lyrics, and which phrases repeat or vary each other
- `analyze_structure()` - Sections (intro/verse/chorus/bridge guesses), repeated passages, repeat signs, voltas, D.C./D.S./Coda, rehearsal marks
- `analyze_rhythm(start_measure, end_measure, staff)` - Meter and beat grouping, pickup/irregular bars, note values, on/off-beat attacks, tuplets, syncopations and anticipations, swing
- `get_tempo_map()` - Tempo marks (quarter and felt beat), misplaced/redundant marks, rit./accel., tempo per bar, fermatas, total time

### **Score Information**
- `get_score(format, start_measure, end_measure)` - Title, key signature, time signatures, tempos, the instrument on each staff, the cursor, and the music as LilyPond (or `format="json"` for the full raw data, including lyric verse/syllabic and markings)
- `ping_musescore()` - Test connection to MuseScore
- `connect_to_musescore()` - Establish WebSocket connection

### **Utilities**
- `undo(steps)` - Undo like Ctrl+Z; the cursor returns to where it was
- `processSequence(sequence, atomic)` - Execute multiple commands in batch; every step is checked first, and it stops at the first failing step. With `atomic=True` the batch is one undo step and nothing is kept if a step fails. The view is updated once at the end, not per step

## Sample Music

Check out the `/examples` folder for sample MuseScore files demonstrating various musical styles:

- **Asian Instrumental** - Traditional Asian-inspired instrumental piece
- **String Quartet** - Classical string quartet arrangement

Each example includes:
- `.mscz` - MuseScore file (editable)
- `.pdf` - Sheet music
- `.mp3` - Audio preview

## Usage Examples

### Creating a Simple Melody

```python
await go_to_measure(1, staff=0)

# A melody in one call (MIDI pitch: 60=C, 62=D, 64=E, etc.), one undo step
await write_voice([
    {"pitches": [60], "duration": "1/4"},
    {"pitches": [64], "duration": "1/4"},
    {"pitches": [67], "duration": "1/4"},
    {"pitches": [72], "duration": "1/4", "tie": True},   # tied over the barline
    {"pitches": [72], "duration": "1/2"},
    {"rest": True, "duration": "1/2"},
], staff=0, voice=0, measure=1)

# Chords on the second staff
await write_voice([
    {"pitches": [48, 52, 55], "duration": "1/1"},
    {"pitches": [43, 50, 53], "duration": "5/8"},        # written as 1/2 tied to 1/8
    {"pitches": [48, 52, 55], "duration": "3/8"},
], staff=1, measure=1)

# A small correction
await add_note(62, "1/4", measure=1)

# Lyrics on the melody, dynamics and tempo
await add_lyrics(["Do", "mi", "sol", "do"], staff=0, measure=1)
await add_dynamic("mf", staff=0, measure=1)
await set_tempo(96, "Moderato", measure=1)
```

### Batch Operations

```python
# Add multiple lyrics at once (a trailing "-" hyphenates to the next syllable)
await add_lyrics(["Twin-", "kle", "twin-", "kle", "lit-", "tle", "star"])

# Use sequence processing for several operations in one round trip (atomic: one undo step)
sequence = [
    {"action": "writeVoice", "params": {"measure": 1, "staff": 0, "events": [
        {"pitches": [60], "duration": "1/2"}, {"pitches": [64], "duration": "1/2"}]}},
    {"action": "writeVoice", "params": {"measure": 1, "staff": 1, "events": [
        {"pitches": [48], "duration": "1/1"}]}},
    {"action": "addDynamic", "params": {"dynamic": "mf", "staff": 0, "measure": 1}},
]
await processSequence(sequence, atomic=True)
```

## Star History

<a href="https://www.star-history.com/?repos=ghchen99%2Fmcp-musescore&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ghchen99/mcp-musescore&type=date&theme=dark&legend=top-left&sealed_token=odyuE8YGdrYwqllb44_ZA_4jszOPlz9weSOVIc87yiR8SUSbUGaQoLxlylc2J4ZujJBLXPSCBh9dxSpTn9rD1wNfri9JkxySZuf93zfduRtC2cFbRbi0d3REHTcU3W6U0tKKmxu13NwAIo2teYBM3WjkiqauMf04gdfk8t5LM6MQ1oZFcJoY2ZVydzEg" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ghchen99/mcp-musescore&type=date&legend=top-left&sealed_token=odyuE8YGdrYwqllb44_ZA_4jszOPlz9weSOVIc87yiR8SUSbUGaQoLxlylc2J4ZujJBLXPSCBh9dxSpTn9rD1wNfri9JkxySZuf93zfduRtC2cFbRbi0d3REHTcU3W6U0tKKmxu13NwAIo2teYBM3WjkiqauMf04gdfk8t5LM6MQ1oZFcJoY2ZVydzEg" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ghchen99/mcp-musescore&type=date&legend=top-left&sealed_token=odyuE8YGdrYwqllb44_ZA_4jszOPlz9weSOVIc87yiR8SUSbUGaQoLxlylc2J4ZujJBLXPSCBh9dxSpTn9rD1wNfri9JkxySZuf93zfduRtC2cFbRbi0d3REHTcU3W6U0tKKmxu13NwAIo2teYBM3WjkiqauMf04gdfk8t5LM6MQ1oZFcJoY2ZVydzEg" />
 </picture>
</a>

## Troubleshooting

### Connection Issues
- **"Not connected to MuseScore"**: 
  - Ensure MuseScore is running with a score open
  - Run the MuseScore plugin (Plugins → musescore-mcp-websocket)
  - Check that port 8765 isn't blocked by firewall

### Plugin Issues
- **Plugin not appearing**: Check the `.qml` file is in the plugins folder shown under Preferences → Folders, then restart MuseScore and enable it in Plugins → Manage plugins
- **Plugin missing after replacing the file**: MuseScore may disable a changed plugin; re-enable it in Plugins → Manage plugins
- **No console output**: Run MuseScore from terminal to see debug messages

### Python Server Issues
- **`ModuleNotFoundError: mcp.server.fastmcp`**: mcp 2.x is installed; run `pip install -r requirements.txt` to get `mcp<2`
- **Tool changes not showing up in Claude**: Claude Desktop must be fully quit (tray icon → Quit) and reopened
- **"No server object found"**: The server object must be named `mcp`, `server`, or `app` at module level
- **WebSocket errors**: Make sure MuseScore plugin is running before starting Python server
- **Connection timeout**: The MuseScore plugin must be actively running, not just enabled

### API Limitations
- **Undo cursor tracking**: `undo` restores the plugin's cursor for actions done through the plugin; undoing in MuseScore itself (Ctrl+Z) doesn't move the cursor
- **`set_staff_mute`**: not reliable in MuseScore 4; use the mixer instead
- **Voltas (1st/2nd endings)** and real rit./accel. lines can't be added through MuseScore 4.7's plugin API; add voltas from the palette. `add_tempo_change` writes the tempo change as hidden tempo marks instead
- **Selection-based edits** (insert/delete bars, copy, slurs, hairpins, articulations) are MuseScore's own actions: each is its own undo step, and `copy_measures` uses the clipboard
- **Selection**: write actions select the note/rest at the cursor (once per call or batch) so you can see where it is
- **`write_voice`** can't yet start inside a held note or write over tuplets (use `add_tuplet` + `add_note` for tuplets); writing over part of a held note turns the rest of it into a rest (reported in `warnings`)
- **Ties** are made with MuseScore's own tie command, so the note to tie to (same pitch, same voice, right after) must exist by the end of the call or atomic batch; ties can't cross a repeat barline. Making a tie leaves note-input mode

## File Structure

```
mcp-musescore/
├── .venv/
├── server.py                           # Python MCP server entry point
├── musescore-mcp-websocket.qml         # MuseScore plugin
├── syntax_check.js                     # node syntax check of the plugin
├── requirements.txt
├── requirements-dev.txt                # + pytest
├── README.md
├── tests/                              # pytest; js/ = plugin tests on a mock API; live/ = against MuseScore
└── src/                                # Source code modules
    ├── __init__.py
    ├── client/                         # WebSocket client functionality
    │   ├── __init__.py
    │   └── websocket_client.py
    ├── tools/                          # MCP tool implementations
    │   ├── __init__.py
    │   ├── connection.py               # Connection management tools
    │   ├── navigation.py               # Score navigation tools
    │   ├── notes_measures.py           # Note and measure manipulation
    │   ├── sequences.py                # Batch operation tools
    │   ├── staff_instruments.py        # Staff and instrument tools
    │   └── time_tempo.py               # Time signature, tempo and markings
    ├── analysis/                       # Harmony, phrases, form, rhythm, tempo
    ├── types/                          # Type definitions
    │   ├── __init__.py
    │   └── action_types.py             # WebSocket action type definitions (strict)
    ├── validation.py                   # Strict tool arguments, write_voice events
    └── utils/
        ├── durations.py                # Duration parsing and splitting into tied notes
        └── lilypond_converter.py       # Score JSON → LilyPond
```

## MIDI Pitch Reference

Common MIDI pitch values for reference:
- **Middle C**: 60
- **C Major Scale**: 60, 62, 64, 65, 67, 69, 71, 72
- **Chromatic**: C=60, C#=61, D=62, D#=63, E=64, F=65, F#=66, G=67, G#=68, A=69, A#=70, B=71

## Duration Reference

Duration format: `"numerator/denominator"` (or `{"numerator": int, "denominator": int}`)
- **Whole note**: `"1/1"`
- **Half note**: `"1/2"`
- **Quarter note**: `"1/4"`
- **Eighth note**: `"1/8"`
- **Dotted quarter**: `"3/8"`; double-dotted quarter: `"7/16"`
- **Anything else** (`"5/8"`, `"9/32"`, a note crossing a barline) is written as tied notes that add up exactly; `"1/12"` (a triplet value) is an error
