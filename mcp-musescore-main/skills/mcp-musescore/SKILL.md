---
name: mcp-musescore
description: Use the mcp-musescore MCP server to read, compose, arrange and edit the score open in MuseScore Studio 4 through its QML WebSocket plugin.
metadata:
  short-description: Read and write the score open in MuseScore
---

# MuseScore MCP

Use this skill when a user asks to read, analyze, compose, arrange, transpose or edit a score in MuseScore through the configured `mcp-musescore` server.

## Read the right detail

- [references/tools.md](references/tools.md): the source of truth for the 70 public MCP tools, the compact notation, positions, durations and ties, score versions, and batches. Read it before constructing a `process_sequence` or a long `notation`.
- [references/architecture.md](references/architecture.md): connection, plugin, port, response envelope, MuseScore version problems.
- [references/client-configuration.md](references/client-configuration.md): stdio configuration for a client.
- [references/runtime.md](references/runtime.md): installing, the Python runtime, the repository's CLI helpers.

## Preconditions

- MuseScore Studio 4.7+ is open, with the `musescore-mcp-websocket` plugin running (Plugins menu). It listens on `ws://localhost:8765`.
- A score is open in that window (or none, and the user gives you a file for `open_score`).
- If a tool reports `Not connected to MuseScore`, don't retry edits blindly: have the user start or reload the plugin, call `ping_musescore`, then continue.

## Working with a score

1. **Read before writing.** `get_score` gives the instruments (staff numbers, clefs, ranges), key, meter, tempo and the music in the compact notation, plus the score's version. Long scores: read bar ranges or single staves. `analyze_score` and the `analyze_*` tools summarise harmony, form, phrases and rhythm.
2. **Write whole passages.** `write_voice` with `notation` (e.g. `"C4:q D4 E4:e F4 | [C4 E4 G4]:h~ [C4 E4 G4]:q r"`) writes one staff/voice in one undo step. `replace_section` rewrites whole bars for several staves at once (and appends bars at the end). `process_sequence(atomic=true)` groups different edits into one undo step. `add_note`/`add_rest` are for single corrections.
3. **Use the notation you read.** The compact view and `notation=` are the same language: copy a bar's line, change it, write it back with `write_voice` (at `measure`, same `staff`/`voice`) or `replace_section`.
4. **Positions**: `measure` (1-based) + `offset` inside the bar (`"3/8"`, a fraction of a whole note), staves 0-based, voices 0-3. `staff` and `voice` stick between calls: pass them explicitly when switching.
5. **Guard against concurrent edits.** The user may edit in MuseScore while you work. Pass `expected_version` (from the last read or result's `scoreVersion`) to edits; if refused, `get_changes_since(version)` shows what changed, then redo your edit on the new state.
6. **Verify.** Read the bars you changed (`get_score` with a range) and report what is there, including any `warnings` or `split` in the results.
7. **From scratch**: ask the user to create the score in MuseScore (File → New, with the instruments), or shape the open one: `list_instruments` → `add_instrument` / `remove_instrument` / `set_instrument_name`, then `set_score_info`, `set_time_signature`, `set_key_signature`, `set_tempo`, and write. Keep parts inside each instrument's range.

## Guardrails

- Don't claim an edit succeeded without reading it back from MuseScore.
- Preserve the user's music outside the requested change: prefer `write_voice`/`replace_section` on exact bars over clearing large ranges; `transpose(key_signatures=true)` only when the key should change.
- Unknown arguments and fields are errors. If one is refused, the feature doesn't exist in that form: use the documented tools instead of guessing variants.
- Some things the plugin API can't do: voltas, pedal lines (pedal marks are symbols only), grace notes, pickup (anacrusis) bars, opening a second score window, muting or soloing (MuseScore 4's mixer is not reachable from plugins). Say so instead of improvising.
- `undo` takes back your last edit (a `copy_measures` call can be several undo steps: see `undoSteps`).
- `save_score` and `export_score` write files on the user's computer: only when asked.

## Portability

This folder is a normal `SKILL.md` skill for Claude Code and compatible loaders. Codex also uses `agents/openai.yaml` for UI metadata. The skill does not embed secrets, API keys, or machine-specific absolute paths.
