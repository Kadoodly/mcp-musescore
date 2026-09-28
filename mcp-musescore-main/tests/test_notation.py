"""The compact notation: parsing (write_voice notation=) and formatting pieces."""

import pytest

from src.notation import format_duration, note_name, parse_notation, parse_pitch, ticks_fraction


def test_parse_notes_chords_rests_and_carried_durations():
    assert parse_notation("C4:q D4 E4:e F4 [C4 E4 G4]:h r:q") == [
        {"pitches": ["C4"], "duration": "1/4"},
        {"pitches": ["D4"], "duration": "1/4"},
        {"pitches": ["E4"], "duration": "1/8"},
        {"pitches": ["F4"], "duration": "1/8"},
        {"pitches": ["C4", "E4", "G4"], "duration": "1/2"},
        {"rest": True, "duration": "1/4"},
    ]


@pytest.mark.parametrize("text,ticks", [
    ("w", "1/1"), ("h.", "3/4"), ("q..", "7/16"), ("e", "1/8"), ("s.", "3/32"), ("t", "1/32"), ("x.", "3/128"),
    ("b", "2/1"), ("5/8", "5/8"), ("3 / 8", "3/8"), ("2/4", "1/2"),
])
def test_durations(text, ticks):
    assert parse_notation(f"C4:{text.replace(' ', '')}")[0]["duration"] == ticks


def test_ties_tuplets_markings_and_numbers():
    assert parse_notation('60:1/4~ 60 [C4~ E4]:h {3:2 C5:e B4 A4} G4:q.(mf staccato "la" text="dolce espr." chord=G7/B) r(fermata)') == [
        {"pitches": [60], "duration": "1/4", "tie": True},
        {"pitches": [60], "duration": "1/4"},
        {"pitches": ["C4", "E4"], "duration": "1/2", "tie": ["C4"]},
        {"tuplet": "3:2", "events": [{"pitches": ["C5"], "duration": "1/8"}, {"pitches": ["B4"], "duration": "1/8"},
                                     {"pitches": ["A4"], "duration": "1/8"}]},
        {"pitches": ["G4"], "duration": "3/8", "dynamic": "mf", "articulations": ["staccato"], "lyric": "la",
         "text": "dolce espr.", "chord": "G7/B"},
        {"rest": True, "duration": "3/8", "articulations": ["fermata"]},
    ]


def test_accidentals_and_aliases():
    assert [e["pitches"][0] for e in parse_notation("c4:q f#3 Bb5 C##4 Ebb2 F♯4 B♭3")] == ["C4", "F#3", "Bb5", "C##4", "Ebb2", "F#4", "Bb3"]
    assert parse_notation("C4:q(> ^ - . acc marc ten stacc)")[0]["articulations"] == \
        ["accent", "marcato", "tenuto", "staccato", "accent", "marcato", "tenuto", "staccato"]


def test_bar_lines_check_bar_lengths():
    parse_notation("C4:w | D4:h E4 | F4:w | G4:q")         # first and last may be partial
    parse_notation("r:q | C4:q D4 E4 F4 | {3:2 C4:e D4 E4} F4:h. |")
    with pytest.raises(ValueError, match=r"bar check failed.*\(1/1, 7/8\)"):
        parse_notation("| C4:w | D4:h E4:q E4:e |")


@pytest.mark.parametrize("text,message", [
    ("C4 D4", "needs a duration"),
    ("H4:q", "not a MIDI pitch or a note name"),
    ("C4:q(loud)", "unknown marking 'loud'"),
    ("r:q~", "a rest can't be tied"),
    ("[C4 C4]:q", "lists C4 twice"),
    ("[C4 E4:q", "go after the \\]"),
    ("[C4 E4", "missing its closing ]"),
    ("{3:2 C4:e {3:2 D4:e}}", "can't be nested"),
    ("{C4:e D4 E4}", "starts with its ratio"),
    ("{3:2 C4:e D4 E4", "missing its closing }"),
    ("C4:q }", "} without a tuplet"),
    ("r:q(staccato)", "a rest can only have a fermata"),
    ('r:q("la")', "a rest can't have a lyric"),
    ("C4:q:q", "not a duration"),
    ("C4:3", "not a duration"),
    ("C4:1/12", "needs a\\s+tuplet"),
    ("C4:q(mf", "missing their closing \\)"),
    ('C4:q("la)', "missing their closing \\)"),
    ("C4:q(color=red)", "unknown marking color="),
    ("C4:qD4", "not a duration"),
    ("", "notation is empty"),
    ("128:q", "not a MIDI pitch"),
    ("C4:q @1/2 D4", "is a position from get_score's view"),
])
def test_errors(text, message):
    with pytest.raises(ValueError, match=message):
        parse_notation(text)


def test_formatting_helpers():
    assert [format_duration(t) for t in (1920, 1440, 1680, 480, 720, 240, 60, 160, 1200, 3840)] == \
        ["w", "h.", "h..", "q", "q.", "e", "t", "1/12", "5/8", "b"]
    assert [ticks_fraction(t) for t in (0, 480, 720, 1920, 160)] == ["0", "1/4", "3/8", "1/1", "1/12"]
    assert note_name(60) == "C4" and note_name(61) == "C#4" and note_name(70) == "Bb4"
    assert note_name(66, 8) == "Gb4" and note_name(60, 26) == "B#3" and note_name(71, 7) == "Cb5"
    assert note_name(62, 14) == "D4"      # a spelling that doesn't fit the pitch is ignored
    assert parse_pitch("a#4", "p") == "A#4" and parse_pitch(60, "p") == 60 and parse_pitch("60", "p") == 60
