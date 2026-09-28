"""The MCP tool layer, with a fake MuseScore client: strict arguments, the params
sent to the plugin, and agreement with the plugin's per-action whitelist."""

import asyncio
import re
from typing import get_args

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.types import PYTHON_ONLY_PARAMS, SEQUENCE_ACTIONS
from tests.conftest import ROOT

TOOLS_MD = ROOT / "skills" / "mcp-musescore" / "references" / "tools.md"

# Valid values for arguments whose type alone doesn't say enough.
SAMPLES = {
    "duration": "1/4",
    "offset": "1/4",
    "beat_unit": "3/8",
    "parts": [{"staff": 0, "notation": "C4:w"}, {"staff": 1, "voice": 1, "events": [{"rest": True, "duration": "1/1"}]}],
    "staves": [0],
    "voices": [0],
    "semitones": 2,
    "expected_version": 1234500,
    "ratio": {"numerator": 3, "denominator": 2},
    "events": [{"pitches": [60], "duration": "1/4", "tie": True}, {"pitches": [60], "duration": "1/4"}],
    "sequence": [{"action": "addNote", "params": {"pitch": 60, "duration": "1/4"}},
                 {"action": "writeVoice", "params": {"notation": "C4:q D4"}},
                 {"action": "replaceSection", "params": {"startMeasure": 2, "parts": [{"staff": 0, "notation": "C4:w"}]}}],
    "lyrics": ["la"],
    "pitch": 60,
    "voice": 1,
    "fifths": 1,
}


def call(app, name, args):
    return asyncio.run(app.call_tool(name, args))


def tools(app):
    return asyncio.run(app.list_tools())


def sample(name, schema):
    if name in SAMPLES:
        return SAMPLES[name]
    for branch in schema.get("anyOf", []):
        if branch.get("type") != "null":
            return sample(name, branch)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    return {"integer": 1, "number": 1.5, "string": "x", "boolean": True}[schema["type"]]


def all_args(tool):
    args = {name: sample(name, prop) for name, prop in tool.inputSchema["properties"].items()}
    if "events" in args and "notation" in args:      # exactly one of them
        del args["notation"]
    if tool.name == "list_instruments":
        args = {"query": "violin"}
    return args


def required_args(tool):
    props = tool.inputSchema["properties"]
    return {name: sample(name, props[name]) for name in tool.inputSchema.get("required", [])}


def test_tool_count_and_every_tool_documented(server):
    app, _ = server
    names = [t.name for t in tools(app)]
    assert len(names) == 71
    assert "write_voice" in names
    doc = TOOLS_MD.read_text(encoding="utf-8")
    assert "currently registers 71 public tools" in doc
    missing = [n for n in names if f"`{n}`" not in doc]
    assert not missing, f"tools.md doesn't mention {missing}"


def test_every_tool_schema_forbids_unknown_arguments(server):
    app, _ = server
    for tool in tools(app):
        assert tool.inputSchema.get("additionalProperties") is False, tool.name


def test_every_tool_rejects_unknown_arguments(server):
    app, client = server
    for tool in tools(app):
        args = required_args(tool)
        args["not_a_param"] = 1
        with pytest.raises(ToolError, match="not_a_param.*\n.*Extra inputs are not permitted"):
            call(app, tool.name, args)
    assert client.sent == []


def test_tie_on_a_tool_without_tie_is_an_error(server):
    app, client = server
    with pytest.raises(ToolError, match="tie"):
        call(app, "add_rest", {"duration": "1/4", "tie": True})
    with pytest.raises(ToolError, match="tied"):
        call(app, "add_note", {"pitch": 60, "tied": True})
    assert client.sent == []


def test_params_sent_are_accepted_by_the_plugin(server, plugin_tables):
    app, client = server
    allowed = plugin_tables["actionParams"]
    for tool in tools(app):
        call(app, tool.name, all_args(tool))
    assert client.sent
    for action, params in client.sent:
        assert action in allowed, action
        # edits (not reads) may carry expectedVersion at the top level
        extra = set() if action in plugin_tables["readOnlyActions"] else {"expectedVersion"}
        unknown = set(params) - set(allowed[action]) - extra
        assert not unknown, f"{action} sends {unknown}, which the plugin rejects"
        if action == "processSequence":
            for step in params["sequence"]:
                assert set(step.get("params", {})) <= set(allowed[step["action"]])


def test_sequence_types_match_the_plugin(plugin_tables):
    assert set(SEQUENCE_ACTIONS) == set(plugin_tables["sequenceCommands"])
    for action, (params_type, _) in SEQUENCE_ACTIONS.items():
        keys = set(params_type.__required_keys__) | set(params_type.__optional_keys__)
        keys -= PYTHON_ONLY_PARAMS.get(action, set())
        assert keys == set(plugin_tables["actionParams"][action]), action


def test_sequence_step_types_forbid_extra_keys():
    for action, (params_type, _) in SEQUENCE_ACTIONS.items():
        assert params_type.__pydantic_config__.get("extra") == "forbid", action


def test_write_voice_sends_normalized_events(server):
    app, client = server
    call(app, "write_voice", {
        "staff": 1, "voice": 2, "measure": 5,
        "events": [
            {"pitches": [60, 64], "duration": {"numerator": 2, "denominator": 8}, "tie": True},
            {"pitches": [60, 64], "duration": "5/8", "tie": [64]},
            {"pitches": [64], "duration": "1/8"},
            {"rest": True, "duration": "1/4"},
        ],
    })
    assert client.sent == [("writeVoice", {
        "events": [
            {"pitches": [60, 64], "duration": "1/4", "tie": [60, 64]},
            {"pitches": [60, 64], "duration": "5/8", "tie": [64]},
            {"pitches": [64], "duration": "1/8"},
            {"rest": True, "duration": "1/4"},
        ],
        "measure": 5, "staff": 1, "voice": 2,
    })]


@pytest.mark.parametrize("events,message", [
    ([], "events is empty"),
    ([{"pitches": [60], "duration": "1/12"}], "needs a\\s+tuplet"),
    ([{"pitches": [60], "duration": "1/4", "tied": True}], "Extra inputs are not permitted"),
    ([{"pitches": [60], "duration": "1/4", "tie": True}, {"pitches": [62], "duration": "1/4"}], "remove the tie"),
    ([{"pitches": [130], "duration": "1/4"}], "less than or equal to 127"),
    ([{"pitches": [60], "duration": "1/4", "rest": True}], "rest can't have pitches"),
])
def test_write_voice_rejects_bad_events_before_sending(server, events, message):
    app, client = server
    with pytest.raises(ToolError, match=message):
        call(app, "write_voice", {"events": events})
    assert client.sent == []


def test_add_note_params(server):
    app, client = server
    call(app, "add_note", {"pitch": 67, "duration": "5/8", "tie": True, "staff": 1})
    call(app, "add_note", {"pitch": 64, "add_to_chord": True})
    call(app, "add_note", {"pitch": 60})
    call(app, "add_note", {"pitch": 62, "duration": {"numerator": 3, "denominator": 8}})
    assert client.sent == [
        ("addNote", {"pitch": 67, "advanceCursorAfterAction": True, "addToChord": False, "duration": "5/8", "tie": True, "staff": 1}),
        ("addNote", {"pitch": 64, "advanceCursorAfterAction": True, "addToChord": True}),
        ("addNote", {"pitch": 60, "advanceCursorAfterAction": True, "addToChord": False, "duration": "1/4"}),
        ("addNote", {"pitch": 62, "advanceCursorAfterAction": True, "addToChord": False, "duration": "3/8"}),
    ]


@pytest.mark.parametrize("tool,args", [
    ("add_note", {"pitch": 60, "duration": "1/12"}),
    ("add_note", {"pitch": 60, "duration": {"numerator": 1, "denominator": 4, "dots": 1}}),
    ("add_note", {"pitch": 60, "duration": "quarter"}),
    ("add_rest", {"duration": "1/256"}),
    ("add_tuplet", {"ratio": {"numerator": 3, "denominator": 2, "x": 1}}),
])
def test_bad_durations_are_rejected_before_sending(server, tool, args):
    app, client = server
    with pytest.raises(ToolError):
        call(app, tool, args)
    assert client.sent == []


def test_add_tuplet_sends_objects(server):
    app, client = server
    call(app, "add_tuplet", {"duration": "1/4", "ratio": {"numerator": 3, "denominator": 2}})
    assert client.sent[0][1]["duration"] == {"numerator": 1, "denominator": 4}
    assert client.sent[0][1]["ratio"] == {"numerator": 3, "denominator": 2}


def test_process_sequence_normalizes_steps(server):
    app, client = server
    call(app, "process_sequence", {"atomic": True, "sequence": [
        {"action": "setCursor", "params": {"measure": 1}},
        {"action": "addNote", "params": {"pitch": 60, "duration": {"numerator": 2, "denominator": 8}, "tie": True}},
        {"action": "writeVoice", "params": {"events": [{"pitches": [60], "duration": "2/4", "tie": True}, {"pitches": [60], "duration": "1/4"}]}},
        {"action": "getScore"},
    ]})
    assert client.sent == [("processSequence", {"atomic": True, "sequence": [
        {"action": "setCursor", "params": {"measure": 1}},
        {"action": "addNote", "params": {"pitch": 60, "duration": "1/4", "tie": True}},
        {"action": "writeVoice", "params": {"events": [{"pitches": [60], "duration": "1/2", "tie": [60]}, {"pitches": [60], "duration": "1/4"}]}},
        {"action": "getScore"},
    ]})]


@pytest.mark.parametrize("sequence,message", [
    ([{"action": "addNote", "params": {"pitch": 60, "duration": "1/12"}}], "step 0 \\(addNote\\): duration 1/12"),
    ([{"action": "getScore"}, {"action": "writeVoice", "params": {"events": []}}], "step 1 \\(writeVoice\\): events is empty"),
    ([{"action": "addNote", "params": {"pitch": 60, "tie2": True}}], "Extra inputs are not permitted"),
    ([{"action": "addNotes", "params": {}}], "does not match any of the expected tags"),
])
def test_process_sequence_rejects_bad_steps(server, sequence, message):
    app, client = server
    with pytest.raises(ToolError, match=message):
        call(app, "process_sequence", {"sequence": sequence})
    assert client.sent == []


def test_strict_patch_is_idempotent_and_applies_to_new_tools():
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.utilities import func_metadata

    from src.validation import forbid_unknown_tool_arguments

    forbid_unknown_tool_arguments()
    first = func_metadata.ArgModelBase
    forbid_unknown_tool_arguments()
    assert func_metadata.ArgModelBase is first

    app = FastMCP("t")

    @app.tool()
    async def f(x: int = 1):
        return x

    with pytest.raises(ToolError, match="Extra inputs are not permitted"):
        call(app, "f", {"x": 1, "y": 2})
