from fractions import Fraction

import pytest

from src.utils.durations import (
    NOTE_VALUE_TICKS,
    DurationError,
    duration_ticks,
    is_single_value,
    normalize_duration,
    parse_duration,
    plan_pieces,
    split_value,
    ticks_to_text,
)

Q = 480
BAR = 1920
FOUR_BARS = [(i * BAR, (i + 1) * BAR) for i in range(4)]


def test_note_values_are_plain_dotted_and_double_dotted():
    assert NOTE_VALUE_TICKS[0] == 6720          # double-dotted breve
    assert NOTE_VALUE_TICKS[-1] == 15           # 128th
    assert list(NOTE_VALUE_TICKS) == sorted(NOTE_VALUE_TICKS, reverse=True)
    for plain in (1920, 960, 480, 240, 120, 60):
        assert plain in NOTE_VALUE_TICKS
        assert plain * 3 // 2 in NOTE_VALUE_TICKS
        assert plain * 7 // 4 in NOTE_VALUE_TICKS
    assert 30 * 7 / 4 not in NOTE_VALUE_TICKS     # not a whole number of ticks
    assert 1920 * 15 // 8 not in NOTE_VALUE_TICKS  # triple dots aren't used


@pytest.mark.parametrize("value,ticks", [
    ("1/4", 480), (" 3 / 8 ", 720), ("2/8", 480), ("5/8", 1200), ("9/32", 540), ("1/128", 15), ("2/1", 3840),
    ({"numerator": 1, "denominator": 4}, 480), ({"numerator": 7, "denominator": 16}, 840), (Fraction(1, 2), 960),
])
def test_duration_ticks(value, ticks):
    assert duration_ticks(value) == ticks


@pytest.mark.parametrize("value,message", [
    ("1/12", "needs a tuplet"), ("1/6", "needs a tuplet"), ("5/24", "needs a tuplet"), ("1/5", "needs a tuplet"),
    ("1/256", "multiple of 1/128"), ("3/512", "multiple of 1/128"),
    ("0/4", "greater than zero"), ("1/0", "zero denominator"), ("-1/4", 'must look like "1/4"'), ("1.5/4", "must look like"),
    ("quarter", "must look like"), (0.25, 'must be "n/d"'), (None, 'must be "n/d"'),
    ({"numerator": 1, "denominator": 4, "dots": 1}, 'exactly "numerator" and "denominator"'),
    ({"numerator": 1}, 'exactly "numerator" and "denominator"'),
    ({"numerator": 1.0, "denominator": 4}, "must be integers"), ({"numerator": True, "denominator": 4}, "must be integers"),
    ({"numerator": -1, "denominator": 4}, "greater than zero"),
])
def test_bad_durations_are_errors(value, message):
    with pytest.raises(DurationError, match=message):
        duration_ticks(value, "duration")


def test_parse_keeps_fraction_and_normalize_reduces():
    assert parse_duration("2/8") == Fraction(1, 4)
    assert normalize_duration({"numerator": 2, "denominator": 8}) == "1/4"
    assert normalize_duration("10/16") == "5/8"
    assert ticks_to_text(1200) == "5/8"
    assert ticks_to_text(3840) == "2/1"


def test_single_values():
    assert is_single_value(720) and is_single_value(840) and is_single_value(15)
    assert not is_single_value(1200) and not is_single_value(540)


@pytest.mark.parametrize("ticks,pieces", [
    (1200, [960, 240]),        # 5/8 = 1/2 + 1/8
    (540, [480, 60]),          # 9/32 = 1/4 + 1/32
    (1680, [1680]),            # 7/8 = double-dotted half
    (1800, [1680, 120]),       # 15/16
    (2400, [1920, 480]),       # a whole plus a quarter (only fits in a longer bar)
    (15, [15]),
    (45, [45]),                # dotted 64th
    (75, [60, 15]),
])
def test_split_value_is_greedy(ticks, pieces):
    assert split_value(ticks) == pieces
    assert sum(split_value(ticks)) == ticks


def test_split_value_rejects_non_multiples():
    with pytest.raises(DurationError):
        split_value(10)
    with pytest.raises(DurationError):
        split_value(0)


def test_every_multiple_of_a_128th_splits_exactly():
    for ticks in range(15, 4 * BAR, 15):
        pieces = split_value(ticks)
        assert sum(pieces) == ticks
        assert all(p in NOTE_VALUE_TICKS for p in pieces)
        assert pieces == sorted(pieces, reverse=True)


@pytest.mark.parametrize("start,length,pieces", [
    (0, 1200, [(0, 960), (960, 240)]),
    (3 * Q, 2 * Q, [(3 * Q, Q), (BAR, Q)]),                        # half note from beat 4: split at the barline
    (2 * Q, 1440, [(2 * Q, 960), (BAR, 480)]),                     # dotted half from beat 3
    (Q, 1440, [(Q, 1440)]),                                        # dotted half from beat 2 fits
    (0, 3 * BAR, [(0, BAR), (BAR, BAR), (2 * BAR, BAR)]),          # three whole bars
    (1740, 540, [(1740, 180), (BAR, 360)]),                        # 9/32 across a barline
])
def test_plan_pieces(start, length, pieces):
    assert plan_pieces(start, length, FOUR_BARS) == pieces


def test_plan_pieces_in_other_meters():
    three_four = [(0, 1440), (1440, 2880)]
    assert plan_pieces(960, 960, three_four) == [(960, 480), (1440, 480)]
    six_eight = [(0, 1440), (1440, 2880)]
    assert plan_pieces(0, 2880, six_eight) == [(0, 1440), (1440, 1440)]
    pickup = [(0, 480), (480, 2400)]   # a quarter-note pickup bar, then 4/4
    assert plan_pieces(0, 960, pickup) == [(0, 480), (480, 480)]


def test_plan_pieces_needs_bars_for_the_whole_range():
    with pytest.raises(DurationError):
        plan_pieces(0, 3 * BAR, FOUR_BARS[:2])
    with pytest.raises(DurationError):
        plan_pieces(0, BAR, [(480, BAR)])
