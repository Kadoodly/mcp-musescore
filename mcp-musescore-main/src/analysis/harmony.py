"""Chord recognition, key/mode detection, Roman numerals and chord loops."""

from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .model import ScoreModel, pc_name, tpc_name

# (symbol suffix, intervals above the root). Order = preference on ties.
TEMPLATES: List[Tuple[str, Tuple[int, ...]]] = [
    ("", (0, 4, 7)),
    ("m", (0, 3, 7)),
    ("7", (0, 4, 7, 10)),
    ("m7", (0, 3, 7, 10)),
    ("maj7", (0, 4, 7, 11)),
    ("5", (0, 7)),
    ("sus4", (0, 5, 7)),
    ("sus2", (0, 2, 7)),
    ("dim", (0, 3, 6)),
    ("aug", (0, 4, 8)),
    ("6", (0, 4, 7, 9)),
    ("m6", (0, 3, 7, 9)),
    ("add9", (0, 2, 4, 7)),
    ("m(add9)", (0, 2, 3, 7)),
    ("7sus4", (0, 5, 7, 10)),
    ("m7b5", (0, 3, 6, 10)),
    ("dim7", (0, 3, 6, 9)),
    ("m(maj7)", (0, 3, 7, 11)),
    ("9", (0, 2, 4, 7, 10)),
    ("maj9", (0, 2, 4, 7, 11)),
    ("m9", (0, 2, 3, 7, 10)),
    ("6/9", (0, 2, 4, 7, 9)),
    ("7b9", (0, 1, 4, 7, 10)),
    ("7#9", (0, 3, 4, 7, 10)),
    ("maj7#11", (0, 4, 6, 7, 11)),
    ("m11", (0, 2, 3, 5, 7, 10)),
    ("11", (0, 2, 4, 5, 7, 10)),
    ("13", (0, 2, 4, 7, 9, 10)),
]

MINOR_QUALITIES = {"m", "m7", "m6", "m(add9)", "m9", "m11", "m(maj7)"}
DIM_QUALITIES = {"dim", "dim7"}
HALF_DIM = {"m7b5"}


@dataclass
class Chord:
    root: int                # pitch class
    suffix: str              # "", "m", "maj7", ...
    bass: int                # pitch class of the lowest note
    root_name: str
    bass_name: str
    confidence: float        # share of the sounding weight explained by the chord

    @property
    def symbol(self) -> str:
        s = self.root_name + self.suffix
        if self.bass != self.root:
            s += "/" + self.bass_name
        return s

    @property
    def is_minor(self) -> bool:
        return self.suffix in MINOR_QUALITIES

    @property
    def tones(self) -> Tuple[int, ...]:
        return dict(TEMPLATES)[self.suffix]


@dataclass
class Key:
    tonic: int
    mode: str               # "major" | "minor"
    name: str
    evidence: str

    def degree_name(self, chord: Chord) -> str:
        return roman(chord, self)


@dataclass
class Slice:
    start: int
    end: int
    chord: Optional[Chord]


def _spelling(model: ScoreModel, notes, pc: int) -> str:
    """Name a pitch class the way the score spells it, if a note has it."""
    names = Counter(tpc_name(n.tpc) for n in notes if n.pitch % 12 == pc and tpc_name(n.tpc))
    if names:
        return names.most_common(1)[0][0]
    return pc_name(pc, model.prefer_flats)


def recognize(model: ScoreModel, start: int, end: int, staves: Optional[List[int]] = None,
              melody_staff: Optional[int] = None) -> Optional[Chord]:
    """Best-fitting chord for the notes sounding in [start, end)."""
    notes = model.notes_in(start, end, staves)
    if not notes:
        return None
    weights = [0.0] * 12
    span = max(1, end - start)
    for n in notes:
        overlap = min(n.end, end) - max(n.start, start)
        w = overlap / span
        if n.start >= start:
            w *= 1.15          # struck inside the slice
        if melody_staff is not None and n.staff == melody_staff:
            w *= 0.5           # melody notes are often non-chord tones
        weights[n.pitch % 12] += w

    # Bass: lowest note sounding at the slice start (else lowest overall)
    at_start = [n for n in notes if n.start <= start < n.end] or notes
    bass_pc = min(at_start, key=lambda n: n.pitch).pitch % 12
    weights[bass_pc] += 0.35

    total = sum(weights)
    top = max(weights)
    best: Optional[Tuple[float, int, str, float]] = None
    for root in range(12):
        if weights[root] <= 0:
            continue
        for suffix, ivs in TEMPLATES:
            tones = {(root + i) % 12 for i in ivs}
            covered = sum(weights[t] for t in tones)
            extra = total - covered
            missing = 0.0
            for i in ivs:
                if weights[(root + i) % 12] <= 0:
                    missing += 0.35 if i == 7 else 1.0
            score = (covered - 1.3 * extra - missing * 0.6 * top
                     - 0.12 * total * max(0, len(ivs) - 3)
                     + (0.25 * total if bass_pc == root else 0))
            if best is None or score > best[0] + 1e-9:
                best = (score, root, suffix, covered / total if total else 0)
    if best is None:
        return None
    _, root, suffix, conf = best
    return Chord(root, suffix, bass_pc, _spelling(model, notes, root), _spelling(model, notes, bass_pc), round(conf, 2))


def slices(model: ScoreModel, resolution: str = "beat", start_bar: Optional[int] = None,
           end_bar: Optional[int] = None, staves: Optional[List[int]] = None,
           include_melody: bool = True) -> List[Slice]:
    """Chords per beat (or per bar / half bar), with repeats merged."""
    melody = model.melody_staff() if model.has_lyrics() else None
    if staves is None and not include_melody and melody is not None:
        staves = [s for s in range(len(model.staves)) if s != melody]
    out: List[Slice] = []
    for bar in model.bars_in(start_bar, end_bar):
        meter, shift = model.beat_grid(bar)
        if resolution == "bar":
            bounds = [bar.start, bar.end]
        else:
            starts = [bar.start - shift + s for s in meter.beat_starts]
            starts = [s for s in starts if bar.start <= s < bar.end] or [bar.start]
            if starts[0] != bar.start:
                starts.insert(0, bar.start)
            if resolution == "half" and len(starts) >= 4 and len(starts) % 2 == 0:
                starts = starts[::2]
            bounds = starts + [bar.end]
        for a, b in zip(bounds, bounds[1:]):
            ch = recognize(model, a, b, staves, melody)
            if out and out[-1].end == a and _same(out[-1].chord, ch):
                out[-1].end = b
            else:
                out.append(Slice(a, b, ch))
    return out


def _same(a: Optional[Chord], b: Optional[Chord]) -> bool:
    if a is None or b is None:
        return a is b
    return a.symbol == b.symbol


# ---------------------------------------------------------------------------
# Key and Roman numerals
# ---------------------------------------------------------------------------

def detect_key(model: ScoreModel, chord_slices: List[Slice], fifths: Optional[int] = None) -> Key:
    """Major key of the signature vs. its relative minor, from the harmony."""
    f = model.key_fifths if fifths is None else fifths
    major_tonic = (f * 7) % 12
    minor_tonic = (major_tonic + 9) % 12
    major_score = minor_score = 0.0
    reasons = []
    timed = [s for s in chord_slices if s.chord]
    for s in timed:
        d = s.end - s.start
        c = s.chord
        if c.root == major_tonic and not c.is_minor and c.suffix not in DIM_QUALITIES:
            major_score += d
        if c.root == minor_tonic and c.is_minor:
            minor_score += d
        # Dominant of the relative minor (raised leading tone) points to minor
        if c.root == (minor_tonic + 7) % 12 and c.suffix in ("", "7", "7b9", "7#9"):
            minor_score += d * 0.5
    total = major_score + minor_score or 1
    if timed:
        first, last = timed[0].chord, timed[-1].chord
        for label, c, w in (("first", first, 0.15), ("last", last, 0.25)):
            if c.root == minor_tonic and c.is_minor:
                minor_score += total * w
                reasons.append(f"{label} chord is the minor tonic")
            elif c.root == major_tonic and not c.is_minor:
                major_score += total * w
                reasons.append(f"{label} chord is the major tonic")
    prefer_flats = f < 0
    if minor_score > major_score:
        name = f"{pc_name(minor_tonic, prefer_flats)} minor"
        share = minor_score / (major_score + minor_score)
        return Key(minor_tonic, "minor", name, f"tonic-chord weight {share:.0%} minor vs major" + (f"; {', '.join(reasons)}" if reasons else ""))
    name = f"{pc_name(major_tonic, prefer_flats)} major"
    share = major_score / (major_score + minor_score) if (major_score + minor_score) else 1
    return Key(major_tonic, "major", name, f"tonic-chord weight {share:.0%} major vs minor" + (f"; {', '.join(reasons)}" if reasons else ""))


MAJOR_DEGREES = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV", 6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
MINOR_DEGREES = {0: "I", 1: "bII", 2: "II", 3: "III", 4: "#III", 5: "IV", 6: "#IV", 7: "V", 8: "VI", 9: "#VI", 10: "VII", 11: "#VII"}


def roman(chord: Chord, key: Key) -> str:
    degrees = MINOR_DEGREES if key.mode == "minor" else MAJOR_DEGREES
    numeral = degrees[(chord.root - key.tonic) % 12]
    accidental = numeral[0] if numeral[0] in "b#" else ""
    body = numeral[len(accidental):]
    if chord.is_minor or chord.suffix in DIM_QUALITIES or chord.suffix in HALF_DIM:
        body = body.lower()
    suffix = chord.suffix
    if suffix in DIM_QUALITIES:
        body += "°" + ("7" if suffix == "dim7" else "")
        suffix = ""
    elif suffix in HALF_DIM:
        body += "ø7"
        suffix = ""
    elif suffix == "aug":
        body += "+"
        suffix = ""
    elif suffix.startswith("m") and not suffix.startswith("maj"):
        suffix = suffix[1:]
    out = accidental + body + suffix
    if chord.bass != chord.root:
        ivs = {(chord.root + i) % 12: i for i in chord.tones}
        inv = ivs.get(chord.bass)
        if inv in (3, 4):
            out += " (1st inv)"
        elif inv == 7:
            out += " (2nd inv)"
        elif inv in (10, 11):
            out += " (3rd inv)"
        else:
            out += f" over {pc_name(chord.bass, key.name.find('b') > 0)}"
    return out


def simple_roman(chord: Chord, key: Key) -> str:
    """Roman numeral of the triad (extensions dropped), without inversion text."""
    if chord.suffix in DIM_QUALITIES or chord.suffix in HALF_DIM:
        suffix = "dim"
    elif chord.suffix in ("aug", "5") or chord.suffix.startswith("sus") or chord.suffix == "7sus4":
        suffix = "aug" if chord.suffix == "aug" else ("sus4" if "sus" in chord.suffix else chord.suffix)
    else:
        suffix = "m" if chord.is_minor else ""
    triad = Chord(chord.root, suffix, chord.root, chord.root_name, chord.root_name, chord.confidence)
    return roman(triad, key)


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------

def simple_symbol(chord: Optional[Chord]) -> str:
    """Triad-level name: extensions and added tones dropped (Fadd9/A -> F/A, Bbmaj9 -> Bb)."""
    if chord is None:
        return "—"
    if chord.suffix in DIM_QUALITIES or chord.suffix in HALF_DIM:
        quality = "dim"
    elif chord.suffix == "aug":
        quality = "aug"
    elif chord.suffix.startswith("sus") or chord.suffix == "7sus4":
        quality = "sus"
    elif chord.suffix == "5":
        quality = "5"
    else:
        quality = "m" if chord.is_minor else ""
    s = chord.root_name + quality
    if chord.bass != chord.root:
        s += "/" + chord.bass_name
    return s


def bar_chords(model: ScoreModel, chord_slices: List[Slice], simple: bool = False) -> Dict[int, List[str]]:
    per_bar: Dict[int, List[str]] = {}
    for s in chord_slices:
        tick = s.start
        while tick < s.end:
            bar = model.bar_at(tick)
            label = simple_symbol(s.chord) if simple else (s.chord.symbol if s.chord else "—")
            lst = per_bar.setdefault(bar.number, [])
            if not lst or lst[-1] != label:
                lst.append(label)
            tick = bar.end
    return per_bar


def find_loops(per_bar: Dict[int, List[str]], min_repeats: int = 2) -> List[Tuple[int, int, int, List[str]]]:
    """Consecutive repeats of a chord pattern: (first bar, bars per cycle, repeats, pattern)."""
    bars = sorted(per_bar)
    seq = [tuple(per_bar[b]) for b in bars]
    loops = []
    i = 0
    while i < len(seq):
        best = None
        for length in range(1, 9):
            if i + length * min_repeats > len(seq):
                break
            pattern = seq[i:i + length]
            if all(p == ("—",) for p in pattern):
                continue
            k = 1
            while seq[i + k * length:i + (k + 1) * length] == pattern:
                k += 1
            # prefer the pattern covering the most bars; shorter on ties
            if k >= min_repeats and (best is None or k * length > best[1] * best[0]):
                best = (length, k)
        if best and not (best[0] == 1 and best[1] < 3):
            length, k = best
            flat = [c for bar in seq[i:i + length] for c in bar]
            loops.append((bars[i], length, k, flat))
            i += length * k
        else:
            i += 1
    return loops
