# MuseScore MCP

Let Claude read, compose, arrange and edit the score open in **MuseScore Studio 4.7+**. A MuseScore plugin exposes the score over a local WebSocket, and an MCP server gives Claude (Claude Desktop or any MCP client) 70 tools to work with it while you keep editing in MuseScore.

The code, full documentation and tests are in [`mcp-musescore-main/`](mcp-musescore-main/). The full setup guide and tool list are in [its README](mcp-musescore-main/README.md) and [tools.md](mcp-musescore-main/skills/mcp-musescore/references/tools.md).

## What Claude can do

- **Read any score**: instruments, key, meter, tempo, and the music itself in a compact notation (`C4:q D4 [C4 E4 G4]:h~ {3:2 C5:e B4 A4}`), whole or bar by bar. There are also analysis tools for harmony, form, phrases and rhythm.
- **Write whole passages** in the same notation: notes, chords, rests, ties, tuplets, dynamics, articulations, ornaments, lyrics (several verses), expression text and chord symbols. One call writes one undo step, anywhere in the score, and it can span several staves at once.
- **Edit ranges**: transpose (spelling, chord symbols and key signatures included), clear, replace or copy bars (to another staff, transposed), insert or delete bars.
- **Shape the score**: add instruments (at any position), rename, replace or remove them; set time and key signatures, tempo (including rit./accel.), clefs, repeats, markers and jumps, section labels, line and page breaks, bars per system, and the title texts.
- **Files**: export to PDF, PNG, SVG, MIDI, MusicXML and MuseScore files; save; open a score file when none is open.
- **Work alongside you**: every change raises the score's version, so Claude notices edits you make in MuseScore and never overwrites them by accident (`expected_version`, `get_changes_since`).

It can't do voltas, pedal lines, grace notes, pickup bars, muting and soloing (the mixer), or audio export. The MuseScore 4 plugin API doesn't offer these.

## Quick start (Windows)

1. **Get the code**
   ```powershell
   git clone https://github.com/Kadoodly/mcp-musescore.git
   cd mcp-musescore\mcp-musescore-main
   py -3 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. **Install the plugin**: copy `musescore-mcp-websocket.qml` to `Documents\MuseScore4\Plugins\` (with OneDrive: `OneDrive\Documents\MuseScore4\Plugins\`). Restart MuseScore, then enable **musescore-mcp-websocket** under **Plugins → Manage plugins…**.
3. **Connect Claude Desktop**: in `%APPDATA%\Claude\claude_desktop_config.json`, add a server whose `command` is the full path of `.venv\Scripts\python.exe` and whose `args` is the full path of `server.py`. [The full README](mcp-musescore-main/README.md) has the exact JSON, plus macOS and Linux steps.
4. **Use it**: open a score in MuseScore, run the plugin from the **Plugins** menu, restart Claude Desktop, and ask, for example, *"Read the score and add a flute counter-melody in bars 9-16"* or *"Transpose the song to G major"*.

To update later, run `git pull` in the repository folder and copy the `.qml` file to the Plugins folder again.

## Status

The plugin was checked against the MuseScore 4.7.5 source, and there are offline tests for the plugin (on a mock of the plugin API) and the server. The live test scripts in `mcp-musescore-main/tests/live/` passed in MuseScore 4.7.5. They only run on a score whose window title contains "mcp test", and they only write into bars they append.

## Credits

A fork of [ghchen99/mcp-musescore](https://github.com/ghchen99/mcp-musescore), extended with the compact notation, score versions, range editing and the tests. MIT License (see [LICENSE](mcp-musescore-main/LICENSE)).
