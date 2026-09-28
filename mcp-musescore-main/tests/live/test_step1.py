"""Live test of step 1 against a running MuseScore Studio 4.7.x.

Covers write_voice (100+ notes in one call, chords, rests, dotted notes, 5/8 and
9/32, notes crossing barlines, empty voices, several voices and staves), ties
(within a bar, across barlines, repeated, chords, several voices, with odd
durations, into an existing note), add_note tie=true, one undo step per write,
strict validation (bad durations, bad and unknown parameters, at the MCP layer
and in the plugin itself) and timing of batched writes.

Safety
  * It refuses to run unless every MuseScore window's title contains "mcp test".
  * It only writes into bars it appends after the last bar, and checks at the end
    that the score's original bars are unchanged.

How to run
  1. In MuseScore create a new score, e.g. Piano (two staves, so the multi-staff
     test runs), and save it as "mcp test.mscz" so the window title shows it.
     Close other MuseScore windows.
  2. Copy the new musescore-mcp-websocket.qml into the plugins folder, restart
     MuseScore, open "mcp test" and run Plugins > musescore-mcp-websocket.
  3. From the repository folder (where server.py is), with the venv:
         python tests/live/test_step1.py
     Options: --cleanup deletes the appended bars at the end (default: keep them,
     so you can look at the ties and save the file); --bars N appends N bars.
  4. Look at the bars it lists (ties, split notes) and save the file.

Exit code 0 = everything passed. Each check prints PASS/FAIL with details.
"""

import argparse
import asyncio
import json
import logging
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

if "pytest" in sys.modules:   # a script, not a pytest module: never run it by accident
    import pytest
    pytest.skip("live MuseScore test: run it as a script", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

REQUIRED_TITLE = "mcp test"


# ---------------------------------------------------------------------------
# Window title guard
# ---------------------------------------------------------------------------

def _windows_titles():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    titles = []

    def process_name(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
            return ""
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length and "musescore" in process_name(hwnd).lower():
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(callback, 0)
    return titles


def _macos_titles():
    script = '''
tell application "System Events"
    set out to ""
    repeat with p in (every process whose name contains "MuseScore" or name is "mscore")
        repeat with w in (every window of p)
            set out to out & (name of w) & linefeed
        end repeat
    end repeat
    return out
end tell'''
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)
    if res.returncode != 0:
        raise RuntimeError("osascript failed (allow your terminal under Privacy > Accessibility): " + res.stderr.strip())
    return [t for t in res.stdout.splitlines() if t.strip()]


def _linux_titles():
    try:
        res = subprocess.run(["wmctrl", "-lp"], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        raise RuntimeError("install wmctrl so the MuseScore window title can be checked")
    titles = []
    for line in res.stdout.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            comm = Path(f"/proc/{parts[2]}/comm").read_text().strip().lower()
        except OSError:
            comm = ""
        if "musescore" in comm or "mscore" in comm:
            titles.append(parts[4])
    return titles


def check_window_title():
    system = platform.system()
    try:
        titles = {"Windows": _windows_titles, "Darwin": _macos_titles}.get(system, _linux_titles)()
    except Exception as e:
        sys.exit(f"REFUSING TO RUN: can't read the MuseScore window title ({e}).")
    if not titles:
        sys.exit("REFUSING TO RUN: no MuseScore window found.")
    # Main windows name MuseScore in their title; floating panels ("Palettes") don't.
    main = [t for t in titles if "musescore" in t.lower()] or titles
    wrong = [t for t in main if REQUIRED_TITLE not in t.lower()]
    if wrong:
        sys.exit(f'REFUSING TO RUN: every MuseScore window title must contain "{REQUIRED_TITLE}"; found {wrong}.')
    print(f"MuseScore window: {main}")


# ---------------------------------------------------------------------------
# Calling the server
# ---------------------------------------------------------------------------

class ToolFailed(Exception):
    pass


def note(pitches, duration, tie=None):
    ev = {"pitches": pitches, "duration": duration}
    if tie is not None:
        ev["tie"] = tie
    return ev


def rest(duration):
    return {"rest": True, "duration": duration}


class Live:
    def __init__(self, server_module):
        from mcp.server.fastmcp.exceptions import ToolError

        self.server = server_module
        self.ToolError = ToolError
        self.results = []

    async def tool(self, name, **args):
        """Calls an MCP tool as Claude would; returns its dict result or raises ToolFailed."""
        try:
            out = await self.server.mcp.call_tool(name, args)
        except self.ToolError as e:
            raise ToolFailed(str(e)) from None
        if isinstance(out, tuple):
            out = out[0]
        text = out[0].text if out else "{}"
        try:
            data = json.loads(text)
        except ValueError:
            return text
        if isinstance(data, dict) and data.get("error"):
            raise ToolFailed(data["error"])
        return data

    async def raw(self, action, params):
        """Sends a command straight to the plugin (no Python validation)."""
        return await self.server.client.send_command(action, params)

    async def expect_error(self, coro, contains=None):
        try:
            res = await coro
        except ToolFailed as e:
            msg = str(e)
        else:
            if isinstance(res, dict) and res.get("error"):
                msg = res["error"]
            else:
                raise AssertionError(f"expected an error, got success: {str(res)[:300]}")
        if contains and contains.lower() not in msg.lower():
            raise AssertionError(f"error {msg!r} doesn't mention {contains!r}")
        return msg

    async def analysis(self, start=None, end=None):
        args = {"format": "json"}
        if start:
            args["start_measure"] = start
        if end:
            args["end_measure"] = end
        return (await self.tool("get_score", **args))["analysis"]

    async def snapshot(self, start=None, end=None):
        a = await self.analysis(start, end)
        return json.dumps(a["measures"], sort_keys=True)

    async def track(self, staff, voice, start_tick, end_tick, first_bar, last_bar):
        """[(tick, ticks, 'rest' | pitches, tiedForward, tiedBack)] of one voice."""
        rows = []
        for m in (await self.analysis(first_bar, last_bar))["measures"]:
            for el in m["elements"].get(f"staff{staff}", []):
                if el["voice"] != voice or not start_tick <= el["startTick"] < end_tick:
                    continue
                if el.get("isTuplet"):
                    rows.append((el["startTick"], "tuplet"))
                    continue
                if el["name"] == "Rest":
                    rows.append((el["startTick"], el["durationTicks"], "rest", [], []))
                else:
                    ns = el["notes"]
                    rows.append((el["startTick"], el["durationTicks"], sorted(n["pitchMidi"] for n in ns),
                                 sorted(n["pitchMidi"] for n in ns if n["tiedForward"]),
                                 sorted(n["pitchMidi"] for n in ns if n["tiedBack"])))
        return sorted(rows)


def expected_rows(events, start_tick, bars, tie_in=()):
    """The rows a passage should produce: pieces split at barlines then greedily,
    tied within a split note and where an event asks for it."""
    from src.utils.durations import duration_ticks, plan_pieces

    rows, pos, back = [], start_tick, sorted(tie_in)
    for i, ev in enumerate(events):
        length = duration_ticks(ev["duration"])
        pieces = plan_pieces(pos, length, bars)
        if ev.get("rest"):
            rows += [(t, n, "rest", [], []) for t, n in pieces]
            back = []
        else:
            pitches = sorted(ev["pitches"])
            tie = ev.get("tie") or []
            tied = pitches if tie is True else sorted(tie)
            for k, (t, n) in enumerate(pieces):
                fwd = pitches if k + 1 < len(pieces) else tied
                rows.append((t, n, pitches, fwd, back))
                back = fwd
        pos += length
    return rows


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

class Step1:
    def __init__(self, live, bars_to_add, cleanup):
        self.live = live
        self.bars_to_add = bars_to_add
        self.cleanup = cleanup
        self.timings = {}

    async def setup(self):
        lv = self.live
        ping = await lv.tool("ping_musescore")
        assert ping.get("result") == "pong", ping
        a = await lv.analysis()
        self.original_bars = a["numMeasures"]
        self.staves = a["numStaves"]
        self.original = json.dumps(a["measures"], sort_keys=True)
        print(f"Score: {self.original_bars} bars, {self.staves} staves. Appending {self.bars_to_add} test bars.")
        await lv.tool("append_measure", count=self.bars_to_add)
        a = await lv.analysis()
        assert a["numMeasures"] == self.original_bars + self.bars_to_add, "append_measure didn't append"
        self.bars = [(m["startTick"], m["endTick"]) for m in a["measures"]]
        self.next_bar = self.original_bars + 1          # first free test bar (1-based)
        for m in a["measures"][self.original_bars:]:
            assert all(el["name"] == "Rest" for els in m["elements"].values() for el in els), "appended bars aren't empty"

    def region(self, ticks):
        """Reserves whole fresh bars for `ticks` of music, plus one spare bar."""
        first = self.next_bar
        if first > len(self.bars):
            raise AssertionError("out of test bars: rerun with a larger --bars")
        start = self.bars[first - 1][0]
        last = first
        while last <= len(self.bars) and self.bars[last - 1][1] < start + ticks:
            last += 1
        self.next_bar = last + 2
        if self.next_bar > len(self.bars) + 1:
            raise AssertionError("out of test bars: rerun with a larger --bars")
        return first, last, start

    def bar_length(self):
        """Length of the next free bar (all appended bars share the last meter)."""
        start, end = self.bars[self.next_bar - 1]
        return end - start

    async def write_and_check(self, events, staff=0, voice=0, tie_in=(), region=None, label=""):
        from src.utils.durations import duration_ticks

        total = sum(duration_ticks(e["duration"]) for e in events)
        first, last, start = region or self.region(total)
        t0 = time.perf_counter()
        res = await self.live.tool("write_voice", events=events, staff=staff, voice=voice, measure=first)
        elapsed = time.perf_counter() - t0
        assert res["startTick"] == start and res["endTick"] == start + total, res
        want = expected_rows(events, start, self.bars, tie_in)
        got = await self.live.track(staff, voice, start, start + total, first, last + 1)
        assert got == want, f"{label} staff {staff} voice {voice}:\n  expected {want}\n  got      {got}"
        return res, elapsed, (first, last, start)

    # --- write_voice ---------------------------------------------------------------

    async def test_many_notes_one_call(self):
        scale = [60, 62, 64, 65, 67, 69, 71, 72, 74, 72, 71, 69, 67, 65, 64, 62]
        events = [note([scale[i % 16]], "1/16") for i in range(128)]
        res, elapsed, (first, last, _) = await self.write_and_check(events)
        self.timings["write_voice, 128 notes"] = elapsed
        assert res["written"] == 128
        return f"bars {first}-{last}, {elapsed * 1000:.0f} ms"

    async def test_chords_rests_dotted(self):
        events = [note([48, 52, 55], "3/8"), note([50], "1/8"), rest("1/4"), note([60, 64, 67], "7/16"), note([62], "1/16"),
                  note([60], "3/16"), rest("1/16"), note([72, 76], "1/4")]
        _, _, (first, last, _) = await self.write_and_check(events)
        return f"bars {first}-{last}"

    async def test_odd_durations_split_and_tied(self):
        events = [note([60], "5/8"), note([62], "9/32"), note([64], "3/32"), note([65], "5/8"), note([67], "3/8")]
        res, _, (first, last, _) = await self.write_and_check(events)
        assert any(s["duration"] == "5/8" for s in res.get("split", [])), res
        return f"bars {first}-{last}: " + "; ".join(f"{s['duration']} = {' + '.join(s['writtenAs'])}" for s in res["split"])

    async def test_notes_crossing_barlines(self):
        bar = self.bar_length()
        if bar <= 720:
            return "SKIP: the bars are too short for this test"
        events = [rest(ticks_text(bar - 480)), note([67], "1/2"),            # starts a quarter before the barline
                  rest(ticks_text(bar - 720)), note([60, 64], "3/8", True),  # dotted quarter across, tied on
                  note([60, 64], ticks_text(bar + 240))]                       # longer than a bar
        _, _, (first, last, _) = await self.write_and_check(events)
        return f"bars {first}-{last}"

    async def test_ties(self):
        events = [
            note([60], "1/4", True), note([60], "1/4", True), note([60], "1/4", True), note([60], "1/4", True),  # C~C~C~C~
            note([60, 64, 67], "1/2", [60, 64]), note([60, 64, 69], "1/2"),                                     # partial chord tie
            note([72], "1/8", True), note([72], "3/8"),                                                           # within a bar
            note([65, 69], "1/2", True), note([65, 69], "1/2"),                                                   # full chord tie
            note([62], "5/8", True), note([62], "9/32", True), note([62], "3/32"),                                # with odd durations
        ]
        _, _, (first, last, _) = await self.write_and_check(events)
        return f"bars {first}-{last}"

    async def test_two_voices_with_ties(self):
        v0 = [note([72], "1/2", True), note([72], "1/2"), note([74], "3/4", True), note([74], "1/2"), note([76], "3/4")]
        v1 = [note([48], "3/4", True), note([48], "1/2"), note([43], "3/4", True), note([43], "1/4"), rest("1/2")]
        region = self.region(3 * 1920)
        await self.write_and_check(v0, voice=0, region=region, label="voice 1")
        await self.write_and_check(v1, voice=1, region=region, label="voice 2")
        # voice 1 unchanged by the voice-2 write
        await self.write_and_check_readonly(v0, 0, 0, region)
        return f"bars {region[0]}-{region[1]}"

    async def write_and_check_readonly(self, events, staff, voice, region):
        from src.utils.durations import duration_ticks

        first, last, start = region
        total = sum(duration_ticks(e["duration"]) for e in events)
        got = await self.live.track(staff, voice, start, start + total, first, last + 1)
        assert got == expected_rows(events, start, self.bars), f"staff {staff} voice {voice} changed:\n  {got}"

    async def test_empty_voices(self):
        region = self.region(1920)
        await self.write_and_check([rest("1/4"), note([55], "1/4", True), note([55], "1/2")], voice=2, region=region, label="voice 3")
        await self.write_and_check([note([43], "3/8"), rest("5/8")], voice=3, region=region, label="voice 4")
        return f"bar {region[0]}, voices 3 and 4"

    async def test_second_staff(self):
        if self.staves < 2:
            return "SKIP: the score has one staff"
        events = [note([36, 43], "1/2", True), note([36, 43], "1/2", True), note([36, 43], "1/4"), note([38], "3/4", True), note([38], "1/4")]
        _, _, (first, last, _) = await self.write_and_check(events, staff=1)
        await self.write_and_check([note([60], "1/2", True), note([60], "1/1")], staff=1, voice=1,
                                   region=(first, last + 1, self.bars[first - 1][0]))
        return f"staff 2, bars {first}-{last}"

    async def test_tie_into_existing_note(self):
        bar = self.bar_length()
        if bar <= 480:
            return "SKIP: the bars are too short for this test"
        first, _, start = self.region(2 * bar)
        second = self.bars[first][0]
        await self.live.tool("write_voice", staff=0, voice=0, measure=first + 1, events=[note([65, 69], "1/4"), note([67], "1/4")])
        bar1 = [note([60], ticks_text(bar - 480)), note([65], "1/4", True)]
        await self.write_and_check(bar1, region=(first, first, start))
        got = await self.live.track(0, 0, second, second + 1, first + 1, first + 1)
        assert got and got[0][4] == [65], f"the existing note isn't tied into: {got}"
        return f"bars {first}-{first + 1}"

    # --- add_note tie and undo -----------------------------------------------------

    async def test_add_note_tie(self):
        lv = self.live
        first, _, start = self.region(1920)
        before = await lv.snapshot(first, first)
        msg = await lv.expect_error(lv.tool("add_note", pitch=67, duration="1/4", tie=True, staff=0, voice=0, measure=first),
                                    "tie on pitch 67")
        assert await lv.snapshot(first, first) == before, "a failed add_note tie changed the score"
        await lv.tool("process_sequence", atomic=True, sequence=[
            {"action": "setCursor", "params": {"measure": first, "staff": 0, "voice": 0}},
            {"action": "addNote", "params": {"pitch": 67, "duration": "1/4", "tie": True}},
            {"action": "addNote", "params": {"pitch": 67, "duration": "1/2", "tie": True}},
            {"action": "addNote", "params": {"pitch": 67, "duration": "1/4"}},
        ])
        want = expected_rows([note([67], "1/4", True), note([67], "1/2", True), note([67], "1/4")], start, self.bars)
        got = await lv.track(0, 0, start, want[-1][0] + 1, first, first + 1)
        assert got == want, f"\n  expected {want}\n  got      {got}"
        return f"bar {first}; standalone tie with no next note: {msg[:90]}"

    async def test_one_undo_step(self):
        lv = self.live
        first, last, start = self.region(1920 * 2)
        before = await lv.snapshot(first, last)
        events = [note([60], "5/8", True), note([60], "3/8"), note([62, 65], "3/4", True), note([62, 65], "1/4")]
        await lv.tool("write_voice", events=events, staff=0, voice=0, measure=first)
        assert await lv.snapshot(first, last) != before
        await lv.tool("undo", steps=1)
        assert await lv.snapshot(first, last) == before, "one undo didn't remove the whole write_voice"
        return f"bars {first}-{last}"

    # --- validation ------------------------------------------------------------------

    async def test_invalid_input_changes_nothing(self):
        lv = self.live
        first, _, start = self.region(1920)
        before = await lv.snapshot()
        cases = [
            ("1/12 duration", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[note([60], "1/12")]), "tuplet"),
            ("1/256 duration", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[note([60], "1/256")]), "1/128"),
            ("text duration", lv.tool("add_note", pitch=60, duration="quarter", staff=0, voice=0, measure=first), "pattern"),
            ("pitch 128", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[note([128], "1/4")]), "127"),
            ("voice 4", lv.tool("write_voice", staff=0, measure=first, voice=4, events=[note([60], "1/4")]), "voice"),
            ("staff 99", lv.tool("write_voice", voice=0, measure=first, staff=99, events=[note([60], "1/4")]), "staff"),
            ("rest with pitches", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[{"rest": True, "pitches": [60], "duration": "1/4"}]), "rest"),
            ("tie to another pitch", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[note([60], "1/4", True), note([62], "1/4")]), "tie"),
            ("tie into a rest", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[note([60], "1/4", True)]), "rest follows"),
            ("empty events", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[]), "empty"),
            ("tick past the end", lv.tool("write_voice", staff=0, voice=0, tick=10 ** 9, events=[note([60], "1/4")]), "tick"),
            # unknown parameters, MCP layer
            ("unknown tool arg", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[note([60], "1/4")], tied=True), "extra inputs"),
            ("tie on add_rest", lv.tool("add_rest", duration="1/4", tie=True, staff=0, voice=0, measure=first), "extra inputs"),
            ("unknown event field", lv.tool("write_voice", staff=0, voice=0, measure=first, events=[{"pitches": [60], "duration": "1/4", "articulation": "staccato"}]), "extra inputs"),
            ("unknown sequence param", lv.tool("process_sequence", sequence=[{"action": "addNote", "params": {"pitch": 60, "duration": "1/4", "tied": True}}]), "extra inputs"),
            # the plugin itself, bypassing Python validation
            ("plugin: unknown param", lv.raw("addNote", {"pitch": 60, "duration": "1/4", "tie2": True, "staff": 0, "voice": 0, "measure": first}), "unknown parameter 'tie2'"),
            ("plugin: unknown event field", lv.raw("writeVoice", {"staff": 0, "voice": 0, "measure": first, "events": [{"pitches": [60], "duration": "1/4", "x": 1}]}), "unknown parameter 'x'"),
            ("plugin: 1/12", lv.raw("writeVoice", {"staff": 0, "voice": 0, "measure": first, "events": [note([60], "1/12")]}), "tuplet"),
            ("plugin: 9/32 as object with dots", lv.raw("addNote", {"pitch": 60, "duration": {"numerator": 9, "denominator": 32, "dots": 0}, "staff": 0, "voice": 0, "measure": first}), "dots"),
            ("plugin: unknown action", lv.raw("writeVoices", {}), "unknown command"),
            ("plugin: tie on addRest", lv.raw("addRest", {"duration": "1/4", "tie": True, "staff": 0, "voice": 0, "measure": first}), "unknown parameter 'tie'"),
        ]
        failures = []
        for label, coro, contains in cases:
            try:
                await lv.expect_error(coro, contains)
            except AssertionError as e:
                failures.append(f"{label}: {e}")
        assert not failures, "\n  ".join(failures)
        assert await lv.snapshot() == before, "an invalid call changed the score"
        return f"{len(cases)} invalid calls rejected, score unchanged"

    # --- performance -----------------------------------------------------------------

    async def test_batch_timing(self):
        lv = self.live
        first, last, start = self.region(128 * 240)
        seq = [{"action": "setCursor", "params": {"measure": first, "staff": 0, "voice": 0}}]
        seq += [{"action": "addNote", "params": {"pitch": 60 + i % 12, "duration": "1/8"}} for i in range(128)]
        t0 = time.perf_counter()
        await lv.tool("process_sequence", atomic=True, sequence=seq)
        self.timings["atomic process_sequence, 128 addNote"] = time.perf_counter() - t0
        got = await lv.track(0, 0, start, start + 128 * 240, first, last)
        assert [r[2] for r in got] == [[60 + i % 12] for i in range(128)], "atomic batch wrote the wrong notes"

        first, last, start = self.region(32 * 240)
        await lv.tool("set_cursor", measure=first, staff=0, voice=0)
        t0 = time.perf_counter()
        for i in range(32):
            await lv.tool("add_note", pitch=60 + i % 12, duration="1/8")
        self.timings["32 separate add_note calls"] = time.perf_counter() - t0
        return ", ".join(f"{k}: {v * 1000:.0f} ms" for k, v in self.timings.items())

    async def finish(self):
        lv = self.live
        a = await lv.analysis(1, self.original_bars)
        assert json.dumps(a["measures"], sort_keys=True) == self.original, "THE ORIGINAL BARS CHANGED"
        if self.cleanup:
            await lv.tool("delete_measures", start_measure=self.original_bars + 1, end_measure=self.original_bars + self.bars_to_add)
            print("Deleted the test bars.")
        return "original bars unchanged"

    async def run(self):
        await self.setup()
        checks = [n for n in dir(self) if n.startswith("test_")]
        checks.sort(key=lambda n: getattr(Step1, n).__code__.co_firstlineno)
        failed = 0
        for name in checks + ["finish"]:
            try:
                detail = await getattr(self, name)()
                status = "SKIP" if str(detail).startswith("SKIP") else "PASS"
            except (AssertionError, ToolFailed) as e:
                failed += 1
                status, detail = "FAIL", str(e)
            print(f"{status}  {name}: {detail}")
        print(f"\n{len(checks) + 1 - failed}/{len(checks) + 1} passed. Test bars: {self.original_bars + 1}-{self.original_bars + self.bars_to_add}"
              + ("" if self.cleanup else " (kept: check the ties in MuseScore, then save)"))
        return failed


def ticks_text(ticks):
    from src.utils.durations import ticks_to_text
    return ticks_to_text(ticks)


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bars", type=int, default=120, help="bars to append for the tests (default 120)")
    parser.add_argument("--cleanup", action="store_true", help="delete the appended bars at the end")
    args = parser.parse_args()

    check_window_title()
    import server
    logging.getLogger("MuseScoreMCP.Client").setLevel(logging.WARNING)
    logging.getLogger("MuseScoreMCP").setLevel(logging.WARNING)
    failed = await Step1(Live(server), args.bars, args.cleanup).run()
    await server.client.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
