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


def test_a_tie_on_the_last_event_is_left_to_the_plugin():
    assert normalize_voice_events([note([65], tie=True)]) == [{"pitches": [65], "duration": "1/4", "tie": [65]}]


@pytest.mark.parametrize("events,message", [
    ([], "events is empty"),
    ([note([60], "1/12")], "event 0 duration 1/12 can't be written.*tuplet"),
    ([note([60], "1/256")], "multiple of 1/128"),
    ([note([128])], "not a MIDI pitch"),
    ([note([-1])], "not a MIDI pitch"),
    ([note([True])], "not a MIDI pitch"),
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
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        adapter.validate_python(note([60], articulation="staccato"))
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
    ])
    for bad in (
        [{"action": "addNote", "params": {"pitch": 60, "tied": True}}],
        [{"action": "addRest", "params": {"duration": "1/4", "tie": True}}],
        [{"action": "writeVoice", "params": {"events": [note([60], x=1)]}}],
        [{"action": "getScore", "params": {"format": "json"}}],
        [{"action": "ping"}],
        [{"action": "addNote", "params": {"pitch": 60}, "atomic": True}],
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
