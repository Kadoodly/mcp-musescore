# Architecture and connection model

```text
Claude Code / Codex / another MCP client
                 │ MCP stdio
                 ▼
server.py ─ FastMCP("MuseScore Assistant")
                 │ src/tools/*
                 ▼
MuseScoreClient ─ WebSocket ws://localhost:8765
                 │ JSON actions
                 ▼
musescore-mcp-websocket.qml ─ MuseScore plugin API
                 ▼
open score in MuseScore Studio
```

## Components

- `server.py` creates the FastMCP server and registers the public Python tools.
- `src/tools/` translates typed MCP calls into camelCase JSON actions.
- `src/client/websocket_client.py` owns the localhost WebSocket connection and retries once after a stale socket.
- `musescore-mcp-websocket.qml` runs inside MuseScore and exposes a WebSocket server on port 8765.
- `src/notation.py` is the compact notation (parsing `notation=` into events; durations and pitch names); `src/score_view.py` renders `get_score`'s compact view in the same notation.
- `src/utils/lilypond_converter.py` converts score data into LilyPond text (`get_score(format="lilypond")`, selections).

## Startup order

1. Start MuseScore Studio and open a score.
2. Enable the plugin once in MuseScore's plugin manager.
3. Run `Plugins > musescore-mcp-websocket`.
4. Let the MCP client launch `server.py` over stdio, or run the skill's start script manually.
5. Call `ping_musescore` before using a score tool.

The Python backend is not the WebSocket server. The QML plugin is the process that listens on port 8765. Starting only `server.py` cannot make MuseScore reachable.

## Wire format

The Python client sends:

```json
{"action": "ping", "params": {}}
```

The QML plugin replies with a success envelope, and the score's version after the request:

```json
{"status": "success", "result": "pong", "version": 1234500}
```

Errors use:

```json
{"status": "error", "message": "...", "version": 1234500}
```

`MuseScoreClient` unwraps the envelope: tools get the `result` object (or `{"error": message}`), with `scoreVersion` added. An edit may carry `expectedVersion` at the top level of its params; the plugin refuses it (and does nothing) if the score's version differs.

## Score versions

MuseScore 4 doesn't tell plugins about edits, so the plugin keeps a digest of every bar. After each of its own edits it re-reads the bars it touched; at `getScore`, `getVersion`, `getChangesSince` and edits with `expectedVersion` it re-reads the score and logs bars that changed without it as edits by the user. Each change raises the version (which starts at a random number when the plugin starts) and is logged with its source and bars (the last 500).

## Files and versions

- Plugin source: `musescore-mcp-websocket.qml`.
- Python entry point: `server.py`.
- Dependency declaration: `requirements.txt`.
- The current source imports `mcp.server.fastmcp`; the tested compatible dependency line is `mcp[cli]==1.29.0` with `websockets==17.0.1`. Do not silently upgrade the MCP package to a major version that removes that import.
- The plugin's API use was checked against the MuseScore v4.7.5 source. The step 1 features were tested live with MuseScore Studio 4.7.5 on Windows (tests/live/test_step1.py); run tests/live/test_all.py to check the rest on your installation. Treat other versions as needing a bridge check.

## Failure diagnosis

- `Not connected to MuseScore`: the plugin is not running, the port is unavailable, or MuseScore closed/reloaded the plugin.
- `Unknown command`: the requested action is not in the QML dispatcher or is using the wrong camelCase spelling (or an old plugin file is installed: copy the new `.qml` and restart MuseScore).
- Empty or stale score data: refresh the plugin/cursor state and call `get_score` again.
- Python import failure: inspect the venv and the installed MCP version; the unpinned upstream requirement may have selected an incompatible major version.
