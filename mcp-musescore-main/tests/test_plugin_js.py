"""The plugin's JavaScript, run with node: a syntax check of the converted QML,
the mock-API tests in tests/js/, and the plugin's duration rules compared with
src/utils/durations.py. None of this runs MuseScore (see tests/live/)."""

import json
import random

import pytest

from src.utils.durations import NOTE_VALUE_TICKS, DurationError, duration_ticks, plan_pieces
from tests.conftest import needs_node, run_node

pytestmark = needs_node


def test_plugin_javascript_syntax():
    res = run_node("syntax_check.js")
    assert res.returncode == 0, res.stdout + res.stderr


def test_plugin_against_mock_api():
    res = run_node("tests/js/test_plugin.js")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "FAIL" not in res.stdout


def test_note_values_match(plugin_tables):
    assert tuple(plugin_tables["noteValueTicks"]) == NOTE_VALUE_TICKS


def plugin_plans(cases):
    res = run_node("tests/js/test_plugin.js", "--plans", stdin=json.dumps(cases))
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def python_result(case):
    try:
        if case["kind"] == "parse":
            return {"ticks": duration_ticks(case["value"])}
        bars = [(b["startTick"], b["endTick"]) for b in case["bars"]]
        return {"pieces": [list(p) for p in plan_pieces(case["start"], case["length"], bars)]}
    except DurationError as e:
        return {"error": str(e)}


def test_duration_parsing_matches_python():
    values = ["1/4", "3/8", "5/8", "9/32", "7/16", "2/1", "1/128", "10/16", " 1 / 2 ", "1/12", "1/6", "5/24", "1/256",
              "0/4", "1/0", "abc", "1.5/4", "-1/4",
              {"numerator": 1, "denominator": 4}, {"numerator": 3, "denominator": 16},
              {"numerator": 1, "denominator": 4, "x": 1}, {"numerator": 0, "denominator": 4}]
    for num in range(1, 40):
        for den in (1, 2, 3, 4, 5, 6, 8, 12, 16, 24, 32, 64, 128, 256):
            values.append(f"{num}/{den}")
    cases = [{"kind": "parse", "value": v} for v in values]
    for case, js in zip(cases, plugin_plans(cases)):
        py = python_result(case)
        assert ("error" in js) == ("error" in py), (case, js, py)
        if "error" not in py:
            assert js == py, case
        elif "tuplet" in py["error"]:
            assert "tuplet" in js["error"], (case, js)


def random_bars(rng):
    """Consecutive bars in assorted meters, sometimes starting with a pickup."""
    meters = [(4, 4), (3, 4), (2, 4), (6, 8), (5, 8), (7, 8), (12, 8), (2, 2), (3, 2), (7, 16), (9, 16)]
    bars, pos = [], 0
    if rng.random() < 0.3:
        pickup = rng.choice([240, 480, 720, 960])
        bars.append({"startTick": 0, "endTick": pickup})
        pos = pickup
    for _ in range(6):
        num, den = rng.choice(meters)
        length = 1920 * num // den
        bars.append({"startTick": pos, "endTick": pos + length})
        pos += length
    return bars


def test_piece_planning_matches_python():
    rng = random.Random(1234)
    cases = []
    for _ in range(600):
        bars = random_bars(rng)
        end = bars[-1]["endTick"]
        start = rng.randrange(0, bars[2]["endTick"], 15)
        length = rng.randrange(15, min(3 * 1920, end - start) + 1, 15)
        cases.append({"kind": "plan", "start": start, "length": length, "bars": bars})
    for case, js in zip(cases, plugin_plans(cases)):
        py = python_result(case)
        assert js == py, case
        # pieces add up exactly, never cross a barline, and are single note values
        pieces = py["pieces"]
        assert sum(t for _, t in pieces) == case["length"]
        for tick, ticks in pieces:
            assert ticks in NOTE_VALUE_TICKS
            bar = next(b for b in case["bars"] if b["startTick"] <= tick < b["endTick"])
            assert tick + ticks <= bar["endTick"]
