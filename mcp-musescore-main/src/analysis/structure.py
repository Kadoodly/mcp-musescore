"""Repeated passages and a section outline (intro / verse / chorus / bridge ...)."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from . import harmony
from .model import ScoreModel


@dataclass
class Repeat:
    first: int       # first bar of the original
    second: int      # first bar of the repeat
    length: int      # bars
    layer: str       # "everything", "melody", "accompaniment", "chords"


@dataclass
class Section:
    start: int                 # first bar
    end: int                   # last bar (inclusive)
    letter: str = ""
    name: str = ""
    chords: List[str] = field(default_factory=list)
    has_melody: bool = False
    lyrics: str = ""

    @property
    def bars(self) -> int:
        return self.end - self.start + 1


def _bar_fingerprints(model: ScoreModel, staves: Optional[List[int]]) -> Dict[int, tuple]:
    fps: Dict[int, list] = {b.number: [] for b in model.bars}
    for ev in model.events:
        if staves is not None and ev.staff not in staves:
            continue
        bar = model.bar_at(ev.start)
        fps[bar.number].append((ev.staff, ev.voice, ev.start - bar.start, ev.duration, ev.is_rest,
                                tuple(sorted(p for p, _ in ev.pitches))))
    out = {}
    for b, items in fps.items():
        notes = [i for i in items if not i[4]]
        out[b] = tuple(sorted(items)) if notes else None   # silent bars never "match"
    return out


def find_repeats(seq: Dict[int, object], layer: str, min_len: int) -> List[Repeat]:
    bars = sorted(seq)
    found: List[Repeat] = []
    for i, a in enumerate(bars):
        for j in range(i + 1, len(bars)):
            b = bars[j]
            if seq[a] is None or seq[a] != seq[b]:
                continue
            if i > 0 and j > 0 and seq[bars[i - 1]] is not None and seq[bars[i - 1]] == seq[bars[j - 1]] and bars[i - 1] + (b - a) == bars[j - 1]:
                continue   # not maximal on the left
            length = 0
            while j + length < len(bars) and i + length < j and seq[bars[i + length]] is not None \
                    and seq[bars[i + length]] == seq[bars[j + length]]:
                length += 1
            if length >= min_len:
                found.append(Repeat(a, b, length, layer))
    # keep the longest, drop repeats contained in a longer one
    found.sort(key=lambda r: -r.length)
    kept: List[Repeat] = []
    for r in found:
        if any(k.first <= r.first and r.first + r.length <= k.first + k.length and
               k.second <= r.second and r.second + r.length <= k.second + k.length for k in kept):
            continue
        kept.append(r)
    return kept


def all_repeats(model: ScoreModel, chord_slices) -> List[Repeat]:
    melody = model.melody_staff()
    others = [s for s in range(len(model.staves)) if s != melody]
    layers = [("everything", _bar_fingerprints(model, None), 2)]
    if len(model.staves) > 1:
        layers.append(("melody", _bar_fingerprints(model, [melody]), 2))
        layers.append(("accompaniment", _bar_fingerprints(model, others), 4))
    per_bar = harmony.bar_chords(model, chord_slices, simple=True)
    layers.append(("chords", {b: tuple(v) if v != ["—"] else None for b, v in per_bar.items()}, 4))

    result: List[Repeat] = []
    for name, seq, min_len in layers:
        for r in find_repeats(seq, name, min_len):
            # skip a layer's repeat if "everything" already repeats there
            if name != "everything" and any(e.layer == "everything" and e.first <= r.first and
                                            r.first + r.length <= e.first + e.length and
                                            e.second <= r.second for e in result):
                continue
            result.append(r)
    return result


def _melody_bars(model: ScoreModel, staff: int) -> Dict[int, bool]:
    """Bars where the melody sings, counting a pickup as part of the next bar."""
    present = {b.number: False for b in model.bars}
    from .phrases import analyze as phrase_analyze, pickup
    for p in phrase_analyze(model, staff):
        start = p.start
        if pickup(model, p):
            start = model.bar_at(p.start).end
        for b in model.bars:
            if b.end > start and b.start < p.end:
                present[b.number] = True
    return present


def sections(model: ScoreModel, chord_slices) -> List[Section]:
    melody = model.melody_staff()
    per_bar = harmony.bar_chords(model, chord_slices, simple=True)
    # For form, a bar is represented by its main (first) chord
    main_chord = {b: v[:1] for b, v in per_bar.items()}
    sings = _melody_bars(model, melody)
    # A one-bar breath inside the singing doesn't end a section
    numbers = sorted(sings)
    for i in range(1, len(numbers) - 1):
        if not sings[numbers[i]] and sings[numbers[i - 1]] and sings[numbers[i + 1]]:
            sings[numbers[i]] = True

    # 1. Harmonic segmentation: chord loops, and the stretches between them
    segs: List[Tuple[int, int, List[str]]] = []
    covered = set()
    for first, length, k, pattern in harmony.find_loops(main_chord):
        last = first + length * k - 1
        segs.append((first, last, pattern))
        covered.update(range(first, last + 1))
    bar_numbers = [b.number for b in model.bars]
    run: List[int] = []
    for b in bar_numbers + [None]:
        if b is not None and b not in covered:
            run.append(b)
            continue
        if run:
            segs.append((run[0], run[-1], [c for x in run for c in main_chord.get(x, [])]))
            run = []
    segs.sort()

    # 2. Split where the melody starts or stops
    pieces: List[Section] = []
    for first, last, pattern in segs:
        start = first
        for b in range(first + 1, last + 2):
            if b == last + 1 or sings.get(b) != sings.get(start):
                chords = [c for x in range(start, b) for c in main_chord.get(x, [])]
                pieces.append(Section(start, b - 1, chords=chords, has_melody=bool(sings.get(start))))
                start = b

    # 3. Letters: same chord content -> same letter
    letters: Dict[tuple, str] = {}
    for s in pieces:
        key = (tuple(_cycle(s.chords)), s.has_melody)
        if key not in letters:
            letters[key] = chr(ord("A") + len(letters))
        s.letter = letters[key]
        s.lyrics = _lyrics_in(model, melody, s.start, s.end)

    # 4. Names
    counts: Dict[str, int] = {}
    for s in pieces:
        counts[s.letter] = counts.get(s.letter, 0) + 1
    middle = (bar_numbers[0] + bar_numbers[-1]) / 2 if bar_numbers else 0
    for i, s in enumerate(pieces):
        if i == len(pieces) - 1 and s.bars <= 2 and counts[s.letter] == 1:
            s.name = "ending"
        elif not s.has_melody:
            s.name = "intro" if i == 0 else ("outro" if i == len(pieces) - 1 else "instrumental")
        elif counts[s.letter] > 1:
            same = [p for p in pieces if p.letter == s.letter and p.lyrics]
            texts = [p.lyrics.lower() for p in same]
            similar = len(texts) > 1 and all(SequenceMatcher(None, texts[0], t).ratio() > 0.6 for t in texts[1:])
            if len(texts) < 2:
                s.name = "recurring theme"
            else:
                s.name = "chorus (likely)" if similar else "verse (likely)"
        elif s.bars <= 4:
            s.name = "link"
        elif s.start > middle:
            s.name = "bridge (likely)"
        else:
            s.name = "section"
    return pieces


def _cycle(chords: List[str]) -> List[str]:
    """Shortest repeating unit of a chord list (a section may stop mid-cycle),
    rotated to a canonical start so the same loop entered elsewhere compares equal."""
    n = len(chords)
    unit = chords
    for size in range(1, n):
        if n >= 2 * size and (chords[:size] * (n // size + 1))[:n] == chords:
            unit = chords[:size]
            break
    else:
        return chords
    rotations = [unit[i:] + unit[:i] for i in range(len(unit))]
    return min(rotations)


def _lyrics_in(model: ScoreModel, staff: int, first: int, last: int) -> str:
    start, end = model.bar(first).start, model.bar(last).end
    words = []
    for ev in model.events:
        if ev.staff == staff and ev.lyric and start <= ev.start < end:
            text = ev.lyric.get("text", "")
            if words and words[-1].endswith("-"):
                words[-1] = words[-1][:-1] + text
            else:
                words.append(text)
            if ev.lyric.get("syllabic") in ("begin", "middle"):
                words[-1] += "-"
    return " ".join(w.rstrip("-") for w in words)
