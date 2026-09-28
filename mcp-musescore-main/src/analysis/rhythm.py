"""Meter, rhythm, syncopation and tempo."""

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from .model import TPQ, WHOLE, Meter, Note, ScoreModel, duration_name


# ---------------------------------------------------------------------------
# Meter
# ---------------------------------------------------------------------------

def meter_runs(model: ScoreModel) -> List[Tuple[int, int, Meter]]:
    """Consecutive bars sharing a (nominal) time signature: (first, last, meter)."""
    runs: List[Tuple[int, int, Meter]] = []
    for b in model.bars:
        if runs and runs[-1][2] == b.nominal:
            runs[-1] = (runs[-1][0], b.number, b.nominal)
        else:
            runs.append((b.number, b.number, b.nominal))
    return runs


def irregular_bars(model: ScoreModel) -> List[Tuple[int, str]]:
    out = []
    for b in model.bars:
        if b.irregular:
            kind = "pickup bar" if b.number == 1 and b.length < b.nominal.bar_ticks else (
                "shortened" if b.length < b.nominal.bar_ticks else "lengthened")
            out.append((b.number, f"{kind}: {Fraction(b.length, WHOLE)} instead of {b.nominal}"))
    return out


# ---------------------------------------------------------------------------
# Rhythm per staff
# ---------------------------------------------------------------------------

@dataclass
class Syncopation:
    note: Note
    kind: str   # "anticipation" (pushed ahead of the beat) or "syncopation"
    beat_tick: int


def syncopations(model: ScoreModel, notes: List[Note]) -> List[Syncopation]:
    """Notes struck off the beat that sustain across the next beat."""
    out = []
    for n in notes:
        bar, beat, beat_start, rem = model.beat_of(n.start)
        if rem == 0:
            continue
        meter, shift = model.beat_grid(bar)
        starts = [bar.start - shift + s for s in meter.beat_starts] + [bar.end]
        nxt = next((s for s in starts if s > n.start), bar.end)
        if n.end <= nxt:
            continue
        beat_len = meter.beat_ticks
        kind = "anticipation" if nxt - n.start <= beat_len // 3 else "syncopation"
        out.append(Syncopation(n, kind, nxt))
    return out


def staff_rhythm(model: ScoreModel, staff: int, first_bar: Optional[int] = None,
                 last_bar: Optional[int] = None) -> Dict[str, object]:
    bars = model.bars_in(first_bar, last_bar)
    if not bars:
        return {}
    lo, hi = bars[0].start, bars[-1].end
    events = [e for e in model.events if e.staff == staff and lo <= e.start < hi]
    notes = [n for n in model.notes if n.staff == staff and lo <= n.start < hi]
    chords = [e for e in events if not e.is_rest]
    onsets = sorted({n.start for n in notes})

    durations = Counter(e.duration for e in chords)
    total = sum(durations.values()) or 1
    on_downbeat = sum(1 for t in onsets if model.bar_at(t).start == t)
    on_beat = sum(1 for t in onsets if model.is_on_beat(t))
    tuplets = Counter(e.tuplet for e in chords if e.tuplet)
    tuplet_bars = sorted({model.bar_at(e.start).number for e in events if e.tuplet})
    across_bar = [n for n in notes if model.bar_at(n.start).end < n.end]
    sync = syncopations(model, notes)
    articulations = Counter(a for e in chords for a in e.articulations)

    return {
        "staff": staff,
        "notes": len(notes),
        "onsets": len(onsets),
        "durations": [(duration_name(d), round(100 * c / total)) for d, c in durations.most_common(5)],
        "on_downbeat_pct": round(100 * on_downbeat / len(onsets)) if onsets else 0,
        "on_beat_pct": round(100 * on_beat / len(onsets)) if onsets else 0,
        "tuplets": {f"{a}:{b}": c for (a, b), c in tuplets.items()},
        "tuplet_bars": tuplet_bars,
        "tied_across_barline": len(across_bar),
        "syncopations": sync,
        "articulations": dict(articulations),
        "onsets_per_bar": round(len(onsets) / len(bars), 1),
    }


# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------

@dataclass
class TempoMark:
    tick: int
    bpm: float
    text: str
    flags: List[str]
    visible: bool = True


def tempo_marks(model: ScoreModel) -> List[TempoMark]:
    marks: List[TempoMark] = []
    current = None
    for bar in model.bars:
        for mk in bar.markings:
            if mk.get("type") != "tempo":
                continue
            tick = mk.get("tick", bar.start)
            flags = []
            visible = mk.get("visible", True)
            if tick % (TPQ // 4):
                flags.append(f"off the rhythmic grid ({tick % (TPQ // 4)} tick(s) from a 16th); probably an import artifact")
            elif not model.is_on_beat(tick):
                flags.append("placed off the beat")
            if visible and current is not None and abs(current - mk["bpm"]) < 0.01 and "tempo" not in (mk.get("text") or "") \
                    and not (mk.get("text") or "").strip().endswith((".", "rit", "accel")):
                flags.append("repeats the tempo already in effect")
            if marks and tick - marks[-1].tick < TPQ // 4:
                flags.append(f"{tick - marks[-1].tick} tick(s) after the previous tempo mark")
            marks.append(TempoMark(tick, mk["bpm"], mk.get("text", ""), flags, visible))
            current = mk["bpm"]
    return marks


def felt_bpm(bpm_quarter: float, meter: Meter) -> Tuple[float, str]:
    """Tempo in the meter's felt beat, e.g. 6/8 at quarter=67 -> dotted quarter=44.7."""
    beat = meter.beat_ticks
    return round(bpm_quarter * TPQ / beat, 1), duration_name(beat)


def tempo_ranges(model: ScoreModel) -> List[Tuple[int, int, float, float]]:
    """(first bar, last bar, bpm at start, bpm at end) for stretches of steady or changing tempo."""
    runs: List[Tuple[int, int, float, float]] = []
    for b in model.bars:
        if b.tempo_bpm is None:
            continue
        if runs and abs(runs[-1][3] - b.tempo_bpm) < 0.01:
            runs[-1] = (runs[-1][0], b.number, runs[-1][2], b.tempo_bpm)
        else:
            runs.append((b.number, b.number, b.tempo_bpm, b.tempo_bpm))
    return runs


def fmt_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"
