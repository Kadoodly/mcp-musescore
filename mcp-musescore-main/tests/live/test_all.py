"""Live test of everything added after step 1, against a running MuseScore
Studio 4.7.x: the compact notation (read and write, round trip), writing at an
offset and into held notes, voices 2-4 in the middle of a bar, tuplets, markings
(dynamics, articulations, ornaments, lyrics, text, chord symbols, fermatas),
replace_section (several staves, one undo step, appending bars), transpose
(spelling, ties, chord symbols, key signatures), clear_range, copy_measures to
another staff transposed, texts, clefs, pedal marks, tempo with beat units,
layout breaks, score versions (expected_version, get_changes_since), redo, the
selection, check_score, export, and timings.

Safety
  * It refuses to run unless every MuseScore window's title contains "mcp test".
  * It only writes into bars it appends after the last bar, and checks at the end
    that the score's original bars are unchanged.
  * Tools that change the whole score (title, instruments, system locks) only run
    with --global. They put back what they change, except system locks: the
    test removes all system locks at the end.

How to run
  1. In MuseScore create a new score, e.g. Piano (two staves), and save it as
     "mcp test.mscz" so the window title shows it. Close other MuseScore windows.
  2. Copy the new musescore-mcp-websocket.qml into the plugins folder, restart
     MuseScore, open "mcp test" and run Plugins > musescore-mcp-websocket.
  3. From the repository folder (where server.py is), with the venv:
         python tests/live/test_all.py
     Options: --cleanup deletes the appended bars at the end (default: keep them,
     so you can look at them); --bars N appends N bars (default 80); --global also
     tests title/instrument/system-lock tools; --interactive also checks that edits
     you make in MuseScore are noticed (it asks you to change a note).
  4. Look at the bars it lists and save (or discard) the file.

Exit code 0 = everything passed. Each check prints PASS/FAIL/SKIP with details.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

if "pytest" in sys.modules:   # a script, not a pytest module: never run it by accident
    import pytest
    pytest.skip("live MuseScore test: run it as a script", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_step1 import Live, ToolFailed, check_window_title  # noqa: E402

from src.notation import parse_notation, pitch_midi  # noqa: E402
from src.validation import normalize_voice_events  # noqa: E402

WHOLE = 1920


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


def bar_lines(view, bar):
    """{"s0": "...", "s1v1": "..."} of one bar in the compact view ({} for rests)."""
    lines = view.splitlines()
    for i, line in enumerate(lines):
        if line == f"bar {bar}" or line.startswith(f"bar {bar} ") or line.startswith(f"bar {bar}:"):
            out = {}
            for more in lines[i + 1:]:
                if not more.startswith("  "):
                    break
                label, _, music = more.strip().partition(": ")
                out[label] = music
            return out
    raise AssertionError(f"bar {bar} not in the view:\n{view}")


# ---------------------------------------------------------------------------

class AllFeatures:
    def __init__(self, live, bars_to_add, cleanup, global_tools, interactive):
        self.live = live
        self.bars_to_add = bars_to_add
        self.cleanup = cleanup
        self.global_tools = global_tools
        self.interactive = interactive
        self.timings = {}

    async def setup(self):
        lv = self.live
        ping = await lv.tool("ping_musescore")
        assert ping.get("result") == "pong", ping
        a = await lv.analysis()
        self.original_bars = a["numMeasures"]
        self.staves = a["numStaves"]
        self.original = json.dumps(a["measures"], sort_keys=True)
        self.header = {k: v for k, v in a.items() if k != "measures"}
        print(f"Score: {self.original_bars} bars, {self.staves} staves. Appending {self.bars_to_add} test bars.")
        await lv.tool("append_measure", count=self.bars_to_add)
        a = await lv.analysis(self.original_bars + 1)
        self.bars = {m["measure"]: (m["startTick"], m["endTick"]) for m in a["measures"]}
        lengths = {e - s for s, e in self.bars.values()}
        if lengths != {WHOLE}:
            raise AssertionError("the last bar of the score must be in 4/4 for these tests (the appended bars take its meter)")
        self.next_bar = self.original_bars + 1

    def take(self, count=1):
        """Reserves `count` fresh bars (and a spare one after them); returns the first."""
        first = self.next_bar
        self.next_bar += count + 1
        if self.next_bar > self.original_bars + self.bars_to_add:
            raise AssertionError("out of test bars: rerun with a larger --bars")
        return first

    async def view(self, first, last=None, staves=None):
        args = {"start_measure": first, "end_measure": last or first}
        if staves is not None:
            args["staves"] = staves
        return await self.live.tool("get_score", **args)

    async def elements(self, bar, staff=0, voice=None):
        a = await self.live.analysis(bar, bar)
        els = a["measures"][0]["elements"].get(f"staff{staff}", [])
        return [e for e in els if voice is None or e["voice"] == voice]

    # --- the notation, both ways -------------------------------------------------

    async def test_notation_round_trip(self):
        passages = [
            "C4:q D4 E4:e F4 G4:q",
            "[C4 E4 G4]:h~ [C4 E4 G4]:q r",
            "{3:2 C5:e B4 A4} G4:q. F4:e E4:q",
            '{5:4 C5:s D5 E5 F5 G5} A5:q.(fermata) r:e B4:q',
            'G4:q(mf staccato "Hel-") A4("lo") B4:h(text="dolce" chord=G7)',
            "[C4~ E4]:h [C4 G4]:h",
            "F#4:q Gb4 A#4 Bb4",
            "Cb5:q B#3 E#4 Fb4",
            "C5:q(trill) D5(up-bow down-bow) E5(turn short-trill) F5(mordent accent-staccato)",
            "C4:e. D4:s E4:e.. F4:t G4:h(p tenuto portato)",
        ]
        mismatches = []
        for music in passages:
            bar = self.take()
            await self.live.tool("write_voice", notation=music, measure=bar, staff=0, voice=0)
            line = bar_lines(await self.view(bar, staves=[0]), bar).get("s0", "")
            if canon(parse_notation(line)) != canon(parse_notation(music)):
                mismatches.append(f"bar {bar}: wrote {music!r}, read {line!r}")
        assert not mismatches, "\n  ".join(mismatches)
        return f"{len(passages)} passages read back the same"

    async def test_notation_timing(self):
        bar = self.take(8)
        music = " | ".join(" ".join(f"{n}:s" if i == 0 else n for i, n in
                                    enumerate(["C5", "D5", "E5", "F5", "G5", "A5", "B5", "C6"] * 2)) for _ in range(8))
        t0 = time.perf_counter()
        res = await self.live.tool("write_voice", notation=music, measure=bar, staff=0, voice=0)
        self.timings["write_voice notation, 128 notes"] = time.perf_counter() - t0
        assert res["written"] == 128, res
        t0 = time.perf_counter()
        await self.live.tool("get_score")
        self.timings["get_score compact, whole score"] = time.perf_counter() - t0
        return ", ".join(f"{k}: {v * 1000:.0f} ms" for k, v in self.timings.items())

    # --- writing anywhere ----------------------------------------------------------

    async def test_offset_and_held_note(self):
        lv = self.live
        bar = self.take()
        await lv.tool("write_voice", notation="E4:w", measure=bar, staff=0, voice=0)
        res = await lv.tool("write_voice", notation="G4:e", measure=bar, offset="5/8", staff=0, voice=0)
        assert any("Split the note" in w for w in res.get("warnings", [])), res
        line = bar_lines(await self.view(bar, staves=[0]), bar)["s0"]
        assert line == "E4:h~ E4:e G4 r:q", line
        return f"bar {bar}: {line}"

    async def test_second_voice_mid_bar(self):
        lv = self.live
        bar = self.take()
        await lv.tool("write_voice", notation="C5:w", measure=bar, staff=0, voice=0)
        await lv.tool("write_voice", notation="E4:q F4", measure=bar, offset="1/2", staff=0, voice=1)
        lines = bar_lines(await self.view(bar, staves=[0]), bar)
        assert lines.get("s0") == "C5:w", lines
        v1 = lines.get("s0v1", "")
        assert v1.endswith("E4:q F4"), lines
        return f"bar {bar}: s0v1 = {v1}"

    async def test_write_into_rest_and_texts_in_rests(self):
        lv = self.live
        bar = self.take()
        await lv.tool("add_text", text="solo", kind="staff", measure=bar, offset="3/8", staff=0)
        await lv.tool("add_chord_symbol", text="Dm7", measure=bar, offset="1/2", staff=0)
        a = await lv.analysis(bar, bar)
        marks = {(m["type"], m.get("text")) for m in a["measures"][0]["markings"]}
        assert ("text", "solo") in marks and ("chordSymbol", "Dm7") in marks, marks
        await lv.tool("write_voice", notation="C5:w", measure=bar + 1, staff=0, voice=0)
        await lv.expect_error(lv.tool("add_text", text="x", measure=bar + 1, offset="1/4", staff=0), "still sounding")
        return f"bar {bar}"

    # --- several staves ---------------------------------------------------------------

    async def test_replace_section_one_undo_step(self):
        if self.staves < 2:
            return "SKIP: the score has one staff"
        lv = self.live
        bar = self.take(2)
        before = await lv.snapshot(bar, bar + 1)
        await lv.tool("replace_section", start_measure=bar, end_measure=bar + 1, parts=[
            {"staff": 0, "notation": 'E5:q(mf) D5 C5:h | {3:2 D5:e E5 D5} C5:q G4:h'},
            {"staff": 0, "voice": 1, "notation": "r:h G4 | E4:w"},
            {"staff": 1, "notation": "[C3 G3]:w | [G2 D3]:h [C3 G3]"},
        ])
        view = await self.view(bar, bar + 1)
        assert bar_lines(view, bar) == {"s0": "E5:q(mf) D5 C5:h", "s0v1": "r:h G4", "s1": "[C3 G3]:w"}, view
        await lv.tool("undo")
        assert await lv.snapshot(bar, bar + 1) == before, "one undo didn't take back the whole replace_section"
        await lv.tool("redo")
        assert bar_lines(await self.view(bar), bar).get("s1") == "[C3 G3]:w", "redo didn't bring it back"
        return f"bars {bar}-{bar + 1}"

    async def test_process_sequence_atomic_notation(self):
        lv = self.live
        bar = self.take()
        undo_steps = 0
        await lv.tool("process_sequence", atomic=True, sequence=[
            {"action": "writeVoice", "params": {"staff": 0, "voice": 0, "measure": bar, "notation": "C5:q D5 E5 F5"}},
            {"action": "addDynamic", "params": {"dynamic": "pp", "measure": bar, "staff": 0}},
            {"action": "addChordSymbol", "params": {"text": "F", "measure": bar, "offset": "3/4"}},
        ])
        line = bar_lines(await self.view(bar, staves=[0]), bar)["s0"]
        assert line == "C5:q(pp) D5 E5 F5(chord=F)", line
        await lv.tool("undo")
        undo_steps += 1
        assert bar_lines(await self.view(bar, staves=[0]), bar) == {}, "one undo didn't take back the whole sequence"
        await lv.tool("redo")
        return f"bar {bar}: {line}"

    # --- changing what is there ---------------------------------------------------------

    async def test_transpose_spelling_ties_chords(self):
        lv = self.live
        bar = self.take(2)
        await lv.tool("write_voice", notation="[C4 E4 G4]:h(chord=C) r:q D4~ | D4:w", measure=bar, staff=0, voice=0)
        res = await lv.tool("transpose", semitones=2, start_measure=bar, staves=[0])
        assert any("moved too" in w for w in res.get("warnings", [])), res
        view = await self.view(bar, bar + 1, staves=[0])
        assert bar_lines(view, bar)["s0"] == "[D4 F#4 A4]:h(chord=D) r:q E4~", view
        assert bar_lines(view, bar + 1)["s0"] == "E4:w", view
        return f"bars {bar}-{bar + 1}"

    async def test_transpose_key_signatures(self):
        lv = self.live
        bar = self.take(2)
        key_before = (await lv.analysis(bar + 2, bar + 2))["measures"][0]["keyFifths"]
        await lv.tool("write_voice", notation="C4:q E4 G4 C5 | B4:w", measure=bar, staff=0, voice=0)
        await lv.tool("transpose", semitones=7, start_measure=bar, end_measure=bar + 1, key_signatures=True)
        a = await lv.analysis(bar, bar + 2)
        keys = [m["keyFifths"] for m in a["measures"]]
        assert keys[0] == keys[1] == key_before + 1 and keys[2] == key_before, keys
        return f"bars {bar}-{bar + 1}: key {key_before} -> {keys[0]}, restored after"

    async def test_clear_range(self):
        lv = self.live
        bar = self.take()
        await lv.tool("write_voice", notation="C5:q(f) D5 E5 F5", measure=bar, staff=0, voice=0)
        await lv.tool("write_voice", notation="A4:w", measure=bar, staff=0, voice=1)
        await lv.tool("clear_range", start_measure=bar, staves=[0])
        a = await lv.analysis(bar, bar)
        m = a["measures"][0]
        els = m["elements"]["staff0"]
        assert [(e["name"], e["durationTicks"]) for e in els] == [("Rest", WHOLE)], els
        assert not [x for x in m["markings"] if x.get("staff") == 0 and x["type"] == "dynamic"], m["markings"]
        return f"bar {bar}"

    async def test_copy_to_other_staff_transposed(self):
        if self.staves < 2:
            return "SKIP: the score has one staff"
        lv = self.live
        src = self.take()
        dst = self.take()
        await lv.tool("write_voice", notation="C5:q E5 G5:h", measure=src, staff=0, voice=0)
        res = await lv.tool("copy_measures", start_measure=src, end_measure=src, to_measure=dst, insert=False,
                            staff=0, to_staff=1, transpose=-24)
        assert res["undoSteps"] == 2, res
        line = bar_lines(await self.view(dst, staves=[1]), dst).get("s1")
        assert line == "C3:q E3 G3:h", line
        return f"bar {src} (staff 0) -> bar {dst} (staff 1): {line}"

    # --- markings and layout ------------------------------------------------------------

    async def test_clef_pedal_tempo_layout(self):
        lv = self.live
        bar = self.take(2)
        await lv.tool("add_clef", type="bass", measure=bar, staff=0)
        await lv.tool("add_clef", type="treble", measure=bar + 2, staff=0)
        await lv.tool("add_pedal_marks", start_measure=bar, end_measure=bar, staff=min(1, self.staves - 1))
        await lv.tool("set_tempo", bpm=60, beat_unit="3/8", text="Andante", measure=bar)
        await lv.tool("set_tempo", bpm=120, measure=bar + 2)
        await lv.tool("add_layout_break", type="line", measure=bar + 1)
        a = await lv.analysis()
        clefs = [c for c in a["staves"][0].get("clefChanges", []) if bar <= c["measure"] <= bar + 2]
        assert clefs[:1] == [{"measure": bar, "clef": "bass"}], a["staves"][0].get("clefChanges")
        tempos = [(t["measure"], t["bpm"]) for t in a["tempos"] if bar <= t["measure"] <= bar + 2]
        assert (bar, 90) in tempos and (bar + 2, 120) in tempos, tempos
        view = await self.view(bar)
        assert "tempo q=90" in view and "clef bass" in view, view
        return f"bars {bar}-{bar + 2}: clefs {clefs}, tempos {tempos}"

    # --- versions, selection, check, export -----------------------------------------------

    async def test_versions(self):
        lv = self.live
        bar = self.take()
        v0 = (await lv.tool("get_version"))["version"]
        res = await lv.tool("write_voice", notation="C5:w", measure=bar, staff=0, voice=0, expected_version=v0)
        v1 = res["scoreVersion"]
        assert v1 == v0 + 1, (v0, v1)
        await lv.expect_error(lv.tool("write_voice", notation="D5:w", measure=bar, staff=0, voice=0, expected_version=v0),
                              "changed since version")
        changes = await lv.tool("get_changes_since", version=v0)
        assert f"bars {bar}" in changes and "mcp: writeVoice" in changes, changes
        detail = f"v{v0} -> v{v1}, stale expected_version refused"
        if self.interactive:
            print(f"\n  >>> In MuseScore, change the note in bar {bar} (staff 1) to another pitch, then press Enter here.")
            await asyncio.get_running_loop().run_in_executor(None, input)
            changes = await lv.tool("get_changes_since", version=v1)
            assert "user: edited in MuseScore" in changes and f"bars {bar}" in changes, changes
            detail += "; your edit was noticed"
        return detail

    async def test_selection_and_check(self):
        lv = self.live
        bar = self.take()
        await lv.tool("write_voice", notation="G4:h A4", measure=bar, staff=0, voice=0)
        start, end = self.bars[bar]
        await lv.tool("select_custom_range", start_tick=start, end_tick=end, start_staff=0, end_staff=0)
        sel = await lv.tool("get_selection")
        assert sel["kind"] == "range" and sel["startMeasure"] == bar and sel["endMeasure"] == bar, sel
        assert "G4:h A4" in sel.get("music", ""), sel
        check = await lv.tool("check_score")
        assert check["ok"], check
        return f"selection bar {sel['startMeasure']}, fromUser={sel['fromUser']}; no corrupted bars"

    async def test_export(self):
        lv = self.live
        folder = Path(tempfile.mkdtemp(prefix="mcp-musescore-"))
        made = []
        for fmt in ("musicxml", "pdf", "mid"):
            res = await lv.tool("export_score", path=str(folder / "export-test"), format=fmt)
            f = Path(res["path"])
            assert f.exists() and f.stat().st_size > 0, f"{fmt}: {f} missing"
            made.append(f"{fmt} {f.stat().st_size} bytes")
            f.unlink()
        folder.rmdir()
        return ", ".join(made)

    async def test_open_score_refused(self):
        await self.live.expect_error(self.live.tool("open_score", path=str(ROOT / "examples" / "x.mscz")), "already open")
        return "refused while a score is open"

    # --- whole-score tools (--global) --------------------------------------------------

    async def test_global_score_info(self):
        if not self.global_tools:
            return "SKIP: run with --global"
        lv = self.live
        old_title = self.header.get("title") or ""
        await lv.tool("set_score_info", title="MCP live test", subtitle="temporary")
        a = await lv.analysis(1, 1)
        assert a["title"] == "MCP live test", a["title"]
        await lv.tool("set_score_info", title=old_title, subtitle="")
        return f"title set and restored to {old_title!r}"

    async def test_global_instruments(self):
        if not self.global_tools:
            return "SKIP: run with --global"
        lv = self.live
        before = (await lv.analysis(1, 1))["numStaves"]
        res = await lv.tool("add_instrument", instrument_id="flute", position=0)
        staff = res["part"]["staves"][0]
        a = await lv.analysis(1, 1)
        assert a["numStaves"] == before + 1 and a["staves"][0]["instrumentId"] == "flute", a["staves"][0]
        await lv.tool("set_instrument_name", staff=0, name="Flute I", short_name="Fl. I")
        a = await lv.analysis(1, 1)
        assert a["staves"][0]["instrument"] == "Flute I", a["staves"][0]
        await lv.tool("remove_instrument", staff=staff)
        assert (await lv.analysis(1, 1))["numStaves"] == before
        return "flute inserted at the top, renamed, removed"

    async def test_global_system_locks(self):
        if not self.global_tools:
            return "SKIP: run with --global"
        await self.live.tool("set_measures_per_system", count=4)
        await self.live.tool("set_measures_per_system", count=0)
        return "locked to 4 bars per system, then unlocked (check the layout while it runs if you want)"

    # --------------------------------------------------------------------------------------

    async def finish(self):
        lv = self.live
        a = await lv.analysis(1, self.original_bars)
        assert json.dumps(a["measures"], sort_keys=True) == self.original, "THE ORIGINAL BARS CHANGED"
        if self.cleanup:
            total = (await lv.analysis(1, 1))["numMeasures"]
            await lv.tool("delete_measures", start_measure=self.original_bars + 1, end_measure=total)
            print("Deleted the test bars.")
        return "original bars unchanged"

    async def run(self):
        await self.setup()
        checks = [n for n in dir(self) if n.startswith("test_")]
        checks.sort(key=lambda n: getattr(AllFeatures, n).__code__.co_firstlineno)
        failed = 0
        for name in checks + ["finish"]:
            try:
                detail = await getattr(self, name)()
                status = "SKIP" if str(detail).startswith("SKIP") else "PASS"
            except (AssertionError, ToolFailed, KeyError) as e:
                failed += 1
                status, detail = "FAIL", f"{type(e).__name__}: {e}"
            print(f"{status}  {name}: {detail}")
        print(f"\n{len(checks) + 1 - failed}/{len(checks) + 1} passed. Test bars: {self.original_bars + 1}-"
              f"{self.original_bars + self.bars_to_add}" + ("" if self.cleanup else " (kept: have a look, then save or discard)"))
        return failed


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bars", type=int, default=80, help="bars to append for the tests (default 80)")
    parser.add_argument("--cleanup", action="store_true", help="delete the appended bars at the end")
    parser.add_argument("--global", dest="global_tools", action="store_true",
                        help="also test title, instrument and system-lock tools (they restore what they change, "
                             "except that all system locks are removed)")
    parser.add_argument("--interactive", action="store_true", help="also check that your edits in MuseScore are noticed")
    args = parser.parse_args()

    check_window_title()
    import server
    logging.getLogger("MuseScoreMCP.Client").setLevel(logging.WARNING)
    logging.getLogger("MuseScoreMCP").setLevel(logging.WARNING)
    failed = await AllFeatures(Live(server), args.bars, args.cleanup, args.global_tools, args.interactive).run()
    await server.client.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
