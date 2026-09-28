"""The MCP tools end to end: the real Python server talking to the real plugin
code, which runs on the mock MuseScore API in node (tests/js/mock_server.js).
This checks that the pieces fit (notation in, compact view out, versions,
changes); only the live tests show what MuseScore itself does."""

import asyncio
import json

import pytest

from src.notation import parse_notation, pitch_midi
from src.validation import normalize_voice_events
from tests.conftest import needs_node

pytestmark = needs_node


def call(app, name, args=None):
    """A tool's result: text, or the dict the tool returned."""
    content = asyncio.run(app.call_tool(name, args or {}))
    blocks = content[0] if isinstance(content, tuple) else content
    text = "".join(getattr(b, "text", "") for b in blocks)
    try:
        return json.loads(text)
    except ValueError:
        return text


def ok(result):
    assert not (isinstance(result, dict) and result.get("error")), result
    return result


def canon(events):
    """Events with pitches as MIDI numbers and ties as lists, for comparing."""
    out = []
    for ev in normalize_voice_events(events):
        if "tuplet" in ev:
            out.append({"tuplet": ev["tuplet"], "events": canon(ev["events"])})
            continue
        ev = dict(ev)
        if "pitches" in ev:
            ev["pitches"] = sorted(pitch_midi(p) for p in ev["pitches"])
        if "tie" in ev:
            ev["tie"] = sorted(pitch_midi(p) for p in ev["tie"])
        if "articulations" in ev:
            ev["articulations"] = sorted(ev["articulations"])
        out.append(ev)
    return out


def bar_lines(text, bar):
    """The "  sX: ..." lines of one bar in the compact view."""
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l == f"bar {bar}" or l.startswith(f"bar {bar} "))
    out = {}
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        label, _, music = line.strip().partition(": ")
        out[label] = music
    return out


ROUND_TRIPS = [
    "C4:q D4 E4:e F4 G4:q",
    "[C4 E4 G4]:h~ [C4 E4 G4]:q r",
    "{3:2 C5:e B4 A4} G4:q. F4:e E4:q",
    'G4:q(mf staccato "Hel-") A4("lo") B4:h(fermata text="dolce" chord=G7)',
    "[C4~ E4]:h [C4 G4]:h",
    "F#4:q Gb4 A#4 Bb4",
    "r:e C4:e~ C4:q r:h",
    "Eb5:s D5 C5 Bb4 Ab4:e G4 F4:q.(accent tenuto) Eb4:e",
]


@pytest.mark.parametrize("music", ROUND_TRIPS)
def test_what_is_written_reads_back_the_same(mock_server, music):
    app, client = mock_server(nstaves=2, bars=4)
    ok(call(app, "write_voice", {"notation": music, "measure": 2, "staff": 0, "voice": 0}))
    view = call(app, "get_score", {"start_measure": 2, "end_measure": 2})
    line = bar_lines(view, 2)["s0"]
    assert canon(parse_notation(line)) == canon(parse_notation(music)), (music, line)


def test_compact_view_of_a_score(mock_server):
    app, client = mock_server(nstaves=3, bars=6)
    ok(call(app, "set_score_info", {"title": "Nocturne", "composer": "Claude"}))
    ok(call(app, "set_tempo", {"bpm": 72, "text": "Lento", "measure": 1}))
    ok(call(app, "write_voice", {"staff": 0, "voice": 0, "measure": 1,
                                 "notation": 'r:q G4(mf "Hel-") Bb4("lo") Eb5 | Eb5:w'}))
    ok(call(app, "replace_section", {"start_measure": 1, "end_measure": 2, "parts": [
        {"staff": 1, "notation": "[Eb4 G4 Bb4]:h [Eb4 Ab4 C5] | [Eb4 G4 Bb4]:h [Eb4 Ab4 C5]"},
        {"staff": 1, "voice": 1, "notation": "r:h C3 | r:h C3"},
        {"staff": 2, "notation": "Eb2:w | Eb2:w"},
    ]}))
    ok(call(app, "copy_measures", {"start_measure": 1, "end_measure": 2, "to_measure": 3, "insert": False}))
    view = call(app, "get_score")
    lines = view.splitlines()
    assert lines[0].startswith('Score "Nocturne" · by Claude · 6 bars · 4/4')
    assert "version" in lines[0]
    assert lines[1].startswith("Staves: s0 ")
    assert 'bar 1 (4/4) tempo q=72 "Lento"' in lines
    assert bar_lines(view, 1) == {
        "s0": 'r:q G4(mf "Hel-") Bb4("lo") Eb5',
        "s1": "[Eb4 G4 Bb4]:h [Eb4 Ab4 C5]",
        "s1v1": "r:h C3",
        "s2": "Eb2:w",
    }
    assert 'bars 3-4 tempo q=72 "Lento" = bars 1-2' in lines      # the copy took the tempo mark along
    assert "bars 5-6: rests" in lines
    assert lines[-1].startswith("Cursor: ")
    # one staff only, and a range
    part = call(app, "get_score", {"start_measure": 2, "end_measure": 2, "staves": [2]})
    assert bar_lines(part, 2) == {"s2": "Eb2:w"}
    assert "(bars 2-2 of 6)" in part


def test_other_formats_still_work(mock_server):
    app, client = mock_server()
    ok(call(app, "write_voice", {"notation": "C4:q D4 E4 F4", "measure": 1}))
    raw = call(app, "get_score", {"format": "json"})
    assert raw["analysis"]["measures"][0]["elements"]["staff0"][0]["notes"][0]["pitchName"] == "C"
    assert isinstance(raw["version"], int)
    lily = call(app, "get_score", {"format": "lilypond"})
    assert "[Score]" in lily and lily.startswith("Version ")


def test_versions_expected_version_and_changes(mock_server):
    app, client = mock_server(bars=6)
    v0 = ok(call(app, "get_version"))["version"]
    r = ok(call(app, "write_voice", {"notation": "C5:w", "measure": 1, "expected_version": v0}))
    v1 = r["scoreVersion"]
    assert v1 == v0 + 1
    # the user edits bar 4 in MuseScore
    client.mock("ms.userEdit(e => e.setNoteRest(3 * 1920, 0, 67, 480))")
    refused = call(app, "write_voice", {"notation": "D5:w", "measure": 2, "expected_version": v1})
    assert "changed since version" in refused["error"]
    changes = call(app, "get_changes_since", {"version": v1})
    assert f"Version {v1} -> {v1 + 1}: 1 change(s)." in changes
    assert "user: edited in MuseScore (bars 4)" in changes
    assert "bar 4" in changes and "G4:q" in changes
    everything = call(app, "get_changes_since", {"version": v0})
    assert "mcp: writeVoice (bars 1)" in everything
    assert "Changed bars now: 1, 4" in everything
    assert "No changes since" in call(app, "get_changes_since", {"version": v1 + 1})
    assert "can't say what changed" in call(app, "get_changes_since", {"version": v1 + 99})


def test_process_sequence_with_notation_is_one_undo_step(mock_server):
    app, client = mock_server(nstaves=2, bars=2)
    undo_before = client.mock("ms.undoStack.length")
    ok(call(app, "process_sequence", {"atomic": True, "sequence": [
        {"action": "writeVoice", "params": {"staff": 0, "measure": 1, "notation": "C5:q D5 E5 F5"}},
        {"action": "writeVoice", "params": {"staff": 1, "measure": 1, "voice": 0, "notation": "[C3 G3]:w"}},
        {"action": "addDynamic", "params": {"dynamic": "p", "measure": 1, "staff": 0}},
        {"action": "replaceSection", "params": {"startMeasure": 3, "parts": [{"staff": 0, "notation": "G5:w"}]}},
    ]}))
    assert client.mock("ms.undoStack.length") == undo_before + 1
    view = call(app, "get_score")
    assert bar_lines(view, 1) == {"s0": "C5:q(p) D5 E5 F5", "s1": "[C3 G3]:w"}
    assert bar_lines(view, 3) == {"s0": "G5:w"}
    ok(call(app, "undo"))
    after = call(app, "get_score")
    assert "· 2 bars ·" in after and "bars 1-2 (4/4): rests" in after      # the appended bar went too


def test_range_tools(mock_server):
    app, client = mock_server(nstaves=2, bars=4)
    ok(call(app, "write_voice", {"notation": "C4:q E4 G4 C5 | D4:w", "measure": 1, "staff": 0, "voice": 0}))
    ok(call(app, "transpose", {"semitones": 2, "start_measure": 1, "staves": [0]}))
    assert bar_lines(call(app, "get_score"), 1)["s0"] == "D4:q F#4 A4 D5"
    ok(call(app, "clear_range", {"start_measure": 2}))
    assert "bars 2-4: rests" in call(app, "get_score")
    ok(call(app, "add_chord_symbol", {"text": "D", "measure": 1}))
    ok(call(app, "add_text", {"text": "dolce", "kind": "expression", "measure": 1, "offset": "1/4", "staff": 0}))
    ok(call(app, "add_clef", {"type": "bass", "measure": 3, "staff": 0}))
    view = call(app, "get_score")
    assert bar_lines(view, 1)["s0"] == 'D4:q(chord=D) F#4(text="dolce") A4 D5'
    assert "bars 3-4 s0 clef bass: rests" in view.splitlines()


def test_selection_with_music(mock_server):
    app, client = mock_server(nstaves=2, bars=4)
    ok(call(app, "write_voice", {"notation": "C4:q E4 G4 C5", "measure": 2, "staff": 0, "voice": 0}))
    client.mock("ms.selection = { kind: 'range', els: [], range: { start: 1920, end: 3840, s0: 0, s1: 1 } }")
    sel = ok(call(app, "get_selection"))
    assert sel["fromUser"] is True and sel["startMeasure"] == 2 and sel["endMeasure"] == 2
    assert "s0: C4:q E4 G4 C5" in sel["music"]


def test_list_instruments(mock_server):
    app, client = mock_server()
    text = call(app, "list_instruments", {"query": "clarinet"})
    assert text.splitlines()[0].startswith("clarinet: Clarinet, clef G, sounds 2 semitone(s) lower than written")
    drums = ok(call(app, "list_instruments", {"instrument_id": "drumset"}))
    assert "38: Acoustic Snare (voice 0)" in drums["drums"]
    assert "similar" in call(app, "list_instruments", {"instrument_id": "clarinette"})
    assert "Violin" in call(app, "list_instruments", {"group": "strings", "query": "violin"})
