"""Musical model built from the plugin's getScore analysis.

Ticks: 480 per quarter note. Bars are numbered from 1, beats from 1.
"""

from bisect import bisect_right
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

TPQ = 480
WHOLE = TPQ * 4

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
TPC_NAMES = [
    "Cbb", "Gbb", "Dbb", "Abb", "Ebb", "Bbb", "Fb",
    "Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F",
    "C", "G", "D", "A", "E", "B", "F#",
    "C#", "G#", "D#", "A#", "E#", "B#", "F##",
    "C##", "G##", "D##", "A##", "E##", "B##", "F###",
]

DURATION_NAMES = {
    WHOLE * 2: "breve", WHOLE: "whole", WHOLE * 3 // 4: "dotted half", WHOLE // 2: "half",
    TPQ * 3 // 2: "dotted quarter", TPQ: "quarter", TPQ * 3 // 4: "dotted 8th", TPQ // 2: "8th",
    TPQ * 3 // 8: "dotted 16th", TPQ // 4: "16th", TPQ // 8: "32nd", TPQ // 16: "64th",
}


def tpc_name(tpc: Optional[int]) -> Optional[str]:
    if tpc is None:
        return None
    if tpc == -1:
        return "Fbb"
    if 0 <= tpc < len(TPC_NAMES):
        return TPC_NAMES[tpc]
    return None


def pc_name(pc: int, prefer_flats: bool) -> str:
    return (FLAT_NAMES if prefer_flats else SHARP_NAMES)[pc % 12]


def pitch_name(pitch: int, tpc: Optional[int] = None, prefer_flats: bool = False) -> str:
    """MIDI pitch -> scientific name, e.g. 60 -> C4, spelled by tpc when known."""
    name = tpc_name(tpc) or pc_name(pitch, prefer_flats)
    octave = pitch // 12 - 1
    # B#3 sounds as C4, Cb4 as B3: the written octave follows the letter
    if name.startswith("B") and "#" in name and pitch % 12 in (0, 1):
        octave -= 1
    elif name.startswith("C") and "b" in name and pitch % 12 in (10, 11):
        octave += 1
    return f"{name}{octave}"


def duration_name(ticks: int) -> str:
    if ticks in DURATION_NAMES:
        return DURATION_NAMES[ticks]
    return f"{Fraction(ticks, WHOLE)}"


# ---------------------------------------------------------------------------
# Meter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Meter:
    numerator: int
    denominator: int

    @property
    def unit(self) -> int:
        return WHOLE // self.denominator

    @property
    def bar_ticks(self) -> int:
        return self.unit * self.numerator

    @property
    def groups(self) -> List[int]:
        """Beat grouping in denominator units, e.g. 6/8 -> [3, 3], 7/8 -> [2, 2, 3]."""
        n, d = self.numerator, self.denominator
        if d >= 8 and n % 3 == 0 and n >= 6:
            return [3] * (n // 3)
        if d >= 8 and n == 3:
            return [3]
        if d >= 8 and n in (5, 7, 8, 10, 11, 13):
            fixed = {5: [3, 2], 7: [2, 2, 3], 8: [3, 3, 2], 10: [3, 3, 2, 2], 11: [3, 3, 3, 2], 13: [3, 3, 3, 2, 2]}
            return fixed[n]
        return [1] * n

    @property
    def is_compound(self) -> bool:
        return self.denominator >= 8 and all(g == 3 for g in self.groups) and self.numerator >= 6

    @property
    def is_irregular(self) -> bool:
        return len(set(self.groups)) > 1

    @property
    def beat_starts(self) -> List[int]:
        starts, pos = [], 0
        for g in self.groups:
            starts.append(pos)
            pos += g * self.unit
        return starts

    @property
    def beat_ticks(self) -> int:
        """Length of the felt beat (the most common group)."""
        return max(set(self.groups), key=self.groups.count) * self.unit

    def describe(self) -> str:
        count = len(self.groups)
        size = {1: "single", 2: "duple", 3: "triple", 4: "quadruple"}.get(count, f"{count}-beat")
        if self.is_irregular:
            return f"irregular ({'+'.join(map(str, self.groups))})"
        if self.is_compound:
            return f"compound {size}: {count} dotted-{duration_name(self.unit * 2).replace('dotted ', '')} beats"
        if self.numerator == 3 and self.denominator >= 8:
            return f"compound single: 1 beat of {duration_name(self.unit * 3)}"
        return f"simple {size}: {count} {duration_name(self.unit)} beats"

    def __str__(self) -> str:
        return f"{self.numerator}/{self.denominator}"


def parse_meter(text: str) -> Meter:
    num, den = text.split("/")
    return Meter(int(num), int(den))


# ---------------------------------------------------------------------------
# Score model
# ---------------------------------------------------------------------------

@dataclass
class Bar:
    number: int
    start: int
    end: int
    meter: Meter
    nominal: Meter
    key_fifths: int = 0
    tempo_bpm: Optional[float] = None
    time_seconds: Optional[float] = None
    repeat_start: bool = False
    repeat_end: bool = False
    repeat_count: Optional[int] = None
    marks: List[Dict[str, Any]] = field(default_factory=list)
    markings: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def irregular(self) -> bool:
        return self.length != self.nominal.bar_ticks


@dataclass
class Event:
    """A chord or rest as written (one per staff/voice/onset)."""
    staff: int
    voice: int
    start: int
    duration: int
    is_rest: bool
    pitches: List[Tuple[int, Optional[int]]]  # (midi, tpc)
    tied_forward: bool = False
    tied_back: bool = False
    tuplet: Optional[Tuple[int, int]] = None
    articulations: List[str] = field(default_factory=list)
    lyric: Optional[Dict[str, Any]] = None  # verse 0 lyric

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass
class Note:
    """A sounding note: tied notes are merged into one."""
    staff: int
    voice: int
    start: int
    end: int
    pitch: int
    tpc: Optional[int]
    lyric: Optional[Dict[str, Any]] = None

    @property
    def duration(self) -> int:
        return self.end - self.start


class ScoreModel:
    def __init__(self, analysis: Dict[str, Any]):
        self.raw = analysis
        self.title = analysis.get("title") or ""
        self.staves = analysis.get("staves", [])
        key = analysis.get("keySignature") or {}
        self.key_fifths = key.get("fifths", 0)
        self.prefer_flats = self.key_fifths < 0
        self.spanners = analysis.get("spanners", []) or []
        self.swing = analysis.get("swing", []) or []
        self.duration_seconds = analysis.get("durationSeconds")

        self.bars: List[Bar] = []
        self.events: List[Event] = []
        for m in analysis.get("measures", []):
            meter = parse_meter(m.get("timeSignature", "4/4"))
            nominal = parse_meter(m.get("nominalTimeSignature") or m.get("timeSignature", "4/4"))
            bar = Bar(
                number=m["measure"], start=m["startTick"], end=m["endTick"], meter=meter, nominal=nominal,
                key_fifths=m.get("keyFifths", self.key_fifths), tempo_bpm=m.get("tempoBpm"),
                time_seconds=m.get("timeSeconds"), repeat_start=m.get("repeatStart", False),
                repeat_end=m.get("repeatEnd", False), repeat_count=m.get("repeatCount"),
                marks=m.get("marks", []), markings=m.get("markings", []),
            )
            self.bars.append(bar)
            for staff_key, elements in m.get("elements", {}).items():
                staff = int(staff_key.replace("staff", ""))
                for e in elements:
                    self.events.append(self._event(staff, e))
        self.events.sort(key=lambda e: (e.staff, e.voice, e.start))
        self._has_tie_back = any("isTiedBack" in e for m in analysis.get("measures", [])
                                 for els in m.get("elements", {}).values() for e in els)
        self._bar_starts = [b.start for b in self.bars]
        self.end_tick = self.bars[-1].end if self.bars else 0
        self.notes = self._merge_ties()

    @staticmethod
    def _event(staff: int, e: Dict[str, Any]) -> Event:
        notes = e.get("notes", [])
        lyric = None
        for lyr in e.get("lyrics", []) or []:
            if lyr.get("verse", 0) == 0:
                lyric = lyr
                break
        tup = e.get("tuplet")
        return Event(
            staff=staff, voice=e.get("voice", 0), start=e["startTick"], duration=e.get("durationTicks", 0),
            is_rest=e.get("name") != "Chord",
            pitches=[(n["pitchMidi"], n.get("tpc")) for n in notes],
            tied_forward=bool(e.get("isTie")), tied_back=bool(e.get("isTiedBack")),
            tuplet=(tup["actual"], tup["normal"]) if tup else ((3, 2) if e.get("isTuplet") else None),
            articulations=e.get("articulations", []) or [], lyric=lyric,
        )

    def _merge_ties(self) -> List[Note]:
        # Per-note tie flags come from the raw data when the plugin provides them.
        per_note: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = {}
        for m in self.raw.get("measures", []):
            for staff_key, elements in m.get("elements", {}).items():
                staff = int(staff_key.replace("staff", ""))
                for e in elements:
                    if e.get("name") == "Chord":
                        per_note[(staff, e.get("voice", 0), e["startTick"])] = e.get("notes", [])

        result: List[Note] = []
        open_notes: Dict[Tuple[int, int, int], Note] = {}  # (staff, voice, pitch) -> note
        for ev in self.events:
            if ev.is_rest:
                continue
            flags = per_note.get((ev.staff, ev.voice, ev.start), [])
            for i, (pitch, tpc) in enumerate(ev.pitches):
                f = flags[i] if i < len(flags) else {}
                # Older plugin data has no tie-back flags: a note continues a
                # tie if the same pitch was tied forward into this onset.
                tied_back = f.get("tiedBack", ev.tied_back if self._has_tie_back else True)
                tied_fwd = f.get("tiedForward", ev.tied_forward)
                key = (ev.staff, ev.voice, pitch)
                prev = open_notes.get(key)
                if tied_back and prev is not None and prev.end == ev.start:
                    prev.end = ev.end
                    note = prev
                else:
                    note = Note(ev.staff, ev.voice, ev.start, ev.end, pitch, tpc, ev.lyric)
                    result.append(note)
                if tied_fwd:
                    open_notes[key] = note
                else:
                    open_notes.pop(key, None)
        result.sort(key=lambda n: (n.start, n.staff, n.pitch))
        return result

    # ---------------------------------------------------------------- lookup

    def bar_at(self, tick: int) -> Bar:
        i = bisect_right(self._bar_starts, tick) - 1
        return self.bars[max(0, min(i, len(self.bars) - 1))]

    def bar(self, number: int) -> Bar:
        return self.bars[number - 1]

    def bars_in(self, start: Optional[int], end: Optional[int]) -> List[Bar]:
        lo = start or 1
        hi = end or len(self.bars)
        return [b for b in self.bars if lo <= b.number <= hi]

    def beat_grid(self, bar: Bar) -> Tuple[Meter, int]:
        """Meter to count beats with, and the offset of the bar start within it.

        A short first bar (pickup) is counted from the end of the nominal bar.
        """
        if bar.irregular and bar.number == 1 and bar.length < bar.nominal.bar_ticks:
            return bar.nominal, bar.nominal.bar_ticks - bar.length
        if bar.irregular:
            return bar.nominal, 0
        return bar.meter, 0

    def beat_of(self, tick: int) -> Tuple[Bar, int, int, int]:
        """(bar, beat number, beat start tick, offset into the beat)."""
        bar = self.bar_at(tick)
        meter, shift = self.beat_grid(bar)
        off = tick - bar.start + shift
        starts = meter.beat_starts
        idx = max(0, bisect_right(starts, off) - 1)
        beat_start = bar.start - shift + starts[idx]
        return bar, idx + 1, beat_start, off - starts[idx]

    def is_on_beat(self, tick: int) -> bool:
        return self.beat_of(tick)[3] == 0

    def fmt_pos(self, tick: int) -> str:
        """Position as bar.beat, plus the distance past the beat as a note value.

        "4.2+5/16" = bar 4, beat 2, plus five 16ths.
        """
        if tick >= self.end_tick:
            return f"end ({len(self.bars) + 1}.1)"
        bar, beat, _, rem = self.beat_of(tick)
        base = f"{bar.number}.{beat}"
        return base if rem == 0 else f"{base}+{Fraction(rem, WHOLE)}"

    def fmt_span(self, start: int, end: int) -> str:
        return f"{self.fmt_pos(start)}–{self.fmt_pos(end)}"

    def name(self, pitch: int, tpc: Optional[int] = None) -> str:
        return pitch_name(pitch, tpc, self.prefer_flats)

    def staff_label(self, staff: int) -> str:
        if 0 <= staff < len(self.staves):
            inst = self.staves[staff].get("instrument") or f"staff {staff}"
            same = [s for s in self.staves if s.get("part") == self.staves[staff].get("part")]
            if len(same) > 1:
                pos = [s["index"] for s in same].index(staff)
                hand = {0: "upper", 1: "lower"}.get(pos, f"#{pos + 1}")
                return f"staff {staff} ({inst}, {hand})"
            return f"staff {staff} ({inst})"
        return f"staff {staff}"

    def notes_in(self, start: int, end: int, staves: Optional[List[int]] = None) -> List[Note]:
        """Notes sounding at any point in [start, end)."""
        return [n for n in self.notes if n.start < end and n.end > start and (staves is None or n.staff in staves)]

    def melody_staff(self) -> int:
        """The staff carrying the tune: most lyrics, else the top staff with notes."""
        lyric_counts: Dict[int, int] = {}
        for ev in self.events:
            if ev.lyric:
                lyric_counts[ev.staff] = lyric_counts.get(ev.staff, 0) + 1
        if lyric_counts:
            return max(lyric_counts, key=lyric_counts.get)
        staves_with_notes = sorted({n.staff for n in self.notes})
        return staves_with_notes[0] if staves_with_notes else 0

    def has_lyrics(self) -> bool:
        return any(ev.lyric for ev in self.events)
