import pytest
from pydantic import TypeAdapter, ValidationError

from src.types import ActionSequence, VoiceEvent
from src.validation import normalize_voice_events


def note(pitches, duration="1/4", **kw):
    return {"pitches": pitches, "duration": duration, **kw}


def test_events_are_normalized_to_the_wire_format():
    events = [
        note([60], "2/8"),
        note([60, 64, 67], {"numerator": 1, "denominator": 2}, tie=True),
        note([60, 64, 67], "1/2", tie=[60]),
        note([60], "1/4", tie=False),
        {"rest": True, "duration": "1/4"},
        {"pitches": [62], "rest": False, "duration": "1/8"},
    ]
    assert normalize_voice_events(events) == [
        {"pitches": [60], "duration": "1/4"},
        {"pitches": [60, 64, 67], "duration": "1/2", "tie": [60, 64, 67]},
        {"pitches": [60, 64, 67], "duration": "1/2", "tie": [60]},
        {"pitches": [60], "duration": "1/4"},
        {"rest": True, "duration": "1/4"},
        {"pitches": [62], "duration": "1/8"},
    ]


def test_names_markings_and_tuplets():
    events = [
        note(["F#4", "a#4", 73], "1/4", tie=["F#4"], dynamic="MF", articulations=["stacc", "fermata"], lyric="la"),
        note(["F#4"], "1/8", text="dolce", chord="D7/F#"),
        {"tuplet": " 3 : 2 ", "events": [note([60], "1/8"), {"rest": True, "duration": "1/8"}, note(["C4"], "1/8")]},
    ]
    assert normalize_voice_events(events) == [
        {"pitches": ["F#4", "A#4", 73], "duration": "1/4", "tie": ["F#4"], "dynamic": "mf",
         "articulations": ["staccato", "fermata"], "lyric": "la"},
        {"pitches": ["F#4"], "duration": "1/8", "text": "dolce", "chord": "D7/F#"},
        {"tuplet": "3:2", "events": [{"pitches": [60], "duration": "1/8"}, {"rest": True, "duration": "1/8"},
                                     {"pitches": ["C4"], "duration": "1/8"}]},
    ]


def test_ties_match_names_and_numbers():
    assert normalize_voice_events([note(["C4"], tie=True), note([60])])[0]["tie"] == ["C4"]
    assert normalize_voice_events([note([60], tie=True), note(["B#3"])])[0]["tie"] == [60]


def test_a_tie_on_the_last_event_is_left_to_the_plugin():
    assert normalize_voice_events([note([65], tie=True)]) == [{"pitches": [65], "duration": "1/4", "tie": [65]}]


@pytest.mark.parametrize("events,message", [
    ([], "events is empty"),
    ([note([60], "1/12")], "event 0 duration 1/12 can't be written.*tuplet"),
    ([note([60], "1/256")], "multiple of 1/128"),
    ([note([128])], "not a MIDI pitch"),
    ([note([-1])], "not a MIDI pitch"),
    ([note([True])], "not a pitch"),
    ([note(["H4"])], "not a MIDI pitch or a note name"),
    ([note(["C4", 60])], "lists a pitch twice"),
    ([note([60], dynamic="loud")], "unknown dynamic 'loud'"),
    ([note([60], articulations=["wobble"])], "unknown articulation 'wobble'"),
    ([{"rest": True, "duration": "1/4", "articulations": ["staccato"]}], "a rest can only have a fermata"),
    ([{"rest": True, "duration": "1/4", "lyric": "la"}], "a rest can't have a lyric"),
    ([note([60], lyric="")], "lyric must be a non-empty string"),
    ([{"tuplet": "3:2", "events": [note([60], "1/8")] * 2}], "must add up to 3 times one note value"),
    ([{"tuplet": "3:2", "events": [note([60], "3/16"), note([60], "5/16"), note([60], "1/8")]}], "one plain or dotted value"),
    ([{"tuplet": "3", "events": [note([60], "1/8")] * 3}], 'tuplet must be a ratio like "3:2"'),
    ([{"tuplet": "3:2", "events": [{"tuplet": "3:2", "events": []}]}], "can't be nested"),
    ([{"tuplet": "3:2", "events": [note([60], "1/8")] * 3, "duration": "1/4"}], "only tuplet and events"),
    ([{"tuplet": "3:2", "events": [note([60], "1/8"), note([60], "1/8"), note([60], "1/8", tie=True)]},
      {"rest": True, "duration": "1/4"}], r"event 0 \(note 2 of the tuplet\) is tied, but event 1 is a rest"),
    ([note([])], "non-empty list"),
    ([note([60, 60])], "lists a pitch twice"),
    ([{"duration": "1/4"}], 'give "pitches"'),
    ([{"pitches": [60]}], "missing duration"),
    ([{"rest": True, "pitches": [60], "duration": "1/4"}], "rest can't have pitches"),
    ([{"rest": True, "duration": "1/4", "tie": True}], "rest can't be tied"),
    ([{"rest": "yes", "duration": "1/4"}], "rest must be true or false"),
    ([note([60], tied=True)], r"unknown field\(s\) \['tied'\]"),
    ([note([60], tie=[62])], r"tie lists pitch\(es\) \[62\]"),
    ([note([60], tie=True), {"rest": True, "duration": "1/4"}], "event 0 is tied, but event 1 is a rest"),
    ([note([60, 64], tie=True), note([60, 65])], r'ties pitch\(es\) \[64\].*use "tie": \[60\]'),
    ([note([60], tie=True), note([62])], r"ties pitch\(es\) \[60\].*remove the tie"),
])
def test_bad_events_are_errors(events, message):
    with pytest.raises(ValueError, match=message):
        normalize_voice_events(events)


def test_event_type_rejects_unknown_fields():
    adapter = TypeAdapter(VoiceEvent)
    adapter.validate_python(note([60], tie=[60]))
    adapter.validate_python(note(["Bb3", 60], articulations=["staccato"], dynamic="p"))
    adapter.validate_python({"tuplet": "3:2", "events": [note([60], "1/8")] * 3})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        adapter.validate_python(note([60], articulation="staccato"))
    with pytest.raises(ValidationError):
        adapter.validate_python({"tuplet": "3:2", "events": [{"tuplet": "3:2", "events": []}]})
    with pytest.raises(ValidationError):
        adapter.validate_python(note(["H4"]))
    with pytest.raises(ValidationError):
        adapter.validate_python(note([60], {"numerator": 1, "denominator": 4, "dots": 1}))
    with pytest.raises(ValidationError):
        adapter.validate_python(note([200]))


def test_sequence_types_are_strict():
    adapter = TypeAdapter(ActionSequence)
    adapter.validate_python([
        {"action": "addNote", "params": {"pitch": 60, "duration": "1/4", "tie": True}},
        {"action": "writeVoice", "params": {"events": [note([60])], "staff": 1}},
        {"action": "getScore"},
        {"action": "goToFinalMeasure", "params": {"staff": 1}},
        {"action": "writeVoice", "params": {"notation": "C4:q D4", "measure": 2, "offset": "1/4"}},
        {"action": "replaceSection", "params": {"startMeasure": 1, "parts": [{"staff": 0, "notation": "C4:w"}]}},
        {"action": "transpose", "params": {"semitones": 2, "startMeasure": 1, "staves": [0]}},
    ])
    for bad in (
        [{"action": "addNote", "params": {"pitch": 60, "tied": True}}],
        [{"action": "addRest", "params": {"duration": "1/4", "tie": True}}],
        [{"action": "writeVoice", "params": {"events": [note([60], x=1)]}}],
        [{"action": "getScore", "params": {"format": "json"}}],
        [{"action": "ping"}],
        [{"action": "addNote", "params": {"pitch": 60}, "atomic": True}],
        [{"action": "addNote", "params": {"pitch": 60, "offset": "beat 2"}}],
        [{"action": "replaceSection", "params": {"startMeasure": 1, "parts": [{"staff": 0, "notes": "C4:w"}]}}],
        [{"action": "getVersion"}],
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
