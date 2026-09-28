"""Melodic phrases: where they start and end, pickups, range, contour, lyrics,
and which phrases repeat or vary each other."""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fractions import Fraction
from typing import List, Optional

from .model import WHOLE, Note, ScoreModel


@dataclass
class Phrase:
    notes: List[Note]
    label: str = ""
    similar_to: Optional[int] = None   # index of the phrase it repeats/varies
    relation: str = ""                 # "repeat", "transposed", "varied", "rhythm"

    @property
    def start(self) -> int:
        return self.notes[0].start

    @property
    def end(self) -> int:
        return self.notes[-1].end

    @property
    def pitches(self) -> List[int]:
        return [n.pitch for n in self.notes]

    def lyric_text(self) -> str:
        out = ""
        for n in self.notes:
            lyr = n.lyric
            if not lyr or not lyr.get("text"):
                continue
            text = lyr["text"]
            joined = out.endswith("-")
            out = (out[:-1] + text) if joined else (out + (" " if out else "") + text)
            if lyr.get("syllabic") in ("begin", "middle"):
                out += "-"
        return out.rstrip("-")


def melody_line(model: ScoreModel, staff: int) -> List[Note]:
    """Top note of each onset on the staff (the audible tune on a keyboard staff)."""
    by_start = {}
    for n in model.notes:
        if n.staff != staff:
            continue
        cur = by_start.get(n.start)
        if cur is None or n.pitch > cur.pitch or (n.lyric and not cur.lyric):
            by_start[n.start] = n
    line = [by_start[t] for t in sorted(by_start)]
    # Drop notes that start while a previous top note is still sounding higher
    result: List[Note] = []
    for n in line:
        if result and n.start < result[-1].end and n.pitch < result[-1].pitch and not n.lyric:
            continue
        result.append(n)
    return result


def split_phrases(model: ScoreModel, line: List[Note]) -> List[Phrase]:
    phrases: List[Phrase] = []
    current: List[Note] = []
    for i, n in enumerate(line):
        if current:
            prev = current[-1]
            gap = n.start - prev.end
            beat = model.bar_at(n.start).meter.beat_ticks
            prev_text = (prev.lyric or {}).get("text", "")
            next_text = (n.lyric or {}).get("text", "")
            sentence_end = prev_text.endswith((".", "?", "!"))
            clause_end = prev_text.endswith((",", ";", ":"))
            new_line = bool(next_text[:1].isupper()) and next_text not in ("I", "I'm", "I'll", "I've", "I'd")
            long_note = prev.duration >= beat
            if (gap >= beat // 2
                    or (sentence_end and gap > 0)
                    or (sentence_end and long_note)
                    or (clause_end and gap >= beat // 4)
                    or (new_line and gap > 0 and long_note)):
                phrases.append(Phrase(current))
                current = []
        current.append(n)
    if current:
        phrases.append(Phrase(current))

    # Continuous singing can run many bars without a rest: split long phrases
    # at their strongest internal boundary until each is at most ~4 bars.
    result: List[Phrase] = []
    for p in phrases:
        result.extend(_split_long(model, p.notes))
    return result


def _boundary_score(model: ScoreModel, prev: Note, nxt: Note) -> float:
    beat = model.bar_at(nxt.start).meter.beat_ticks
    prev_text = (prev.lyric or {}).get("text", "")
    next_text = (nxt.lyric or {}).get("text", "")
    score = 2.0 * (nxt.start - prev.end) / beat + 0.8 * min(prev.duration / beat, 2.0)
    if prev_text.endswith((".", "?", "!")):
        score += 1.0
    elif prev_text.endswith((",", ";", ":")):
        score += 0.6
    if next_text[:1].isupper() and next_text not in ("I", "I'm", "I'll", "I've", "I'd"):
        score += 0.4
    return score


def _split_long(model: ScoreModel, notes: List[Note]) -> List[Phrase]:
    bar_len = model.bar_at(notes[0].start).nominal.bar_ticks
    length = notes[-1].end - notes[0].start
    if length <= 4.5 * bar_len or len(notes) < 6:
        return [Phrase(notes)]
    best_k, best_score = None, -1.0
    for k in range(1, len(notes)):
        left = notes[k - 1].end - notes[0].start
        right = notes[-1].end - notes[k].start
        if left < bar_len or right < bar_len:
            continue
        score = _boundary_score(model, notes[k - 1], notes[k])
        # prefer halves of 2 or 4 bars
        for half in (left, right):
            bars = half / bar_len
            if min(abs(bars - 2), abs(bars - 4)) < 0.6:
                score += 0.3
        if score > best_score:
            best_k, best_score = k, score
    if best_k is None:
        return [Phrase(notes)]
    return _split_long(model, notes[:best_k]) + _split_long(model, notes[best_k:])


def _signature(p: Phrase):
    first = p.notes[0]
    rhythm = tuple((n.start - first.start, n.duration) for n in p.notes)
    intervals = tuple(b.pitch - a.pitch for a, b in zip(p.notes, p.notes[1:]))
    return rhythm, intervals


def label_phrases(phrases: List[Phrase]) -> None:
    """Letter each phrase; repeats and variations share a letter (A, A', A'')."""
    next_letter = 0
    for i, p in enumerate(phrases):
        rhythm, intervals = _signature(p)
        best = None
        for j in range(i):
            q = phrases[j]
            r2, iv2 = _signature(q)
            if p.pitches == q.pitches and rhythm == r2:
                best = (j, "repeat", 1.0)
                break
            if intervals == iv2 and rhythm == r2:
                cand = (j, "transposed", 0.95)
            else:
                pitch_sim = SequenceMatcher(None, intervals, iv2).ratio() if intervals and iv2 else 0
                rhythm_sim = SequenceMatcher(None, rhythm, r2).ratio()
                score = 0.6 * pitch_sim + 0.4 * rhythm_sim
                if score >= 0.7 and len(p.notes) >= 3:
                    cand = (j, "varied", score)
                elif rhythm_sim >= 0.9 and len(p.notes) >= 4:
                    cand = (j, "rhythm", 0.5 + rhythm_sim / 10)
                else:
                    continue
            if best is None or cand[2] > best[2]:
                best = cand
        if best is None:
            p.label = chr(ord("A") + next_letter % 26) + ("" if next_letter < 26 else str(next_letter // 26))
            next_letter += 1
        else:
            j, relation, _ = best
            base = phrases[j].label.rstrip("'")
            p.similar_to, p.relation = j, relation
            if relation == "repeat":
                p.label = phrases[j].label
            else:
                primes = max((len(q.label) - len(q.label.rstrip("'")) for q in phrases[:i] if q.label.rstrip("'") == base), default=0)
                p.label = base + "'" * (primes + 1)


def contour(p: Phrase) -> str:
    ps = p.pitches
    if len(ps) < 3:
        return "short"
    first, last = ps[0], ps[-1]
    hi, lo = max(ps), min(ps)
    peak_at = ps.index(hi) / (len(ps) - 1)
    low_at = ps.index(lo) / (len(ps) - 1)
    if hi - lo <= 2:
        return "flat (around one note)"
    if 0.2 < peak_at < 0.8 and hi - max(first, last) >= 3:
        return "arch (rises then falls)"
    if 0.2 < low_at < 0.8 and min(first, last) - lo >= 3:
        return "dip (falls then rises)"
    if last - first >= 3:
        return "rising"
    if first - last >= 3:
        return "falling"
    return "level, ends near where it started"


def pickup(model: ScoreModel, p: Phrase) -> Optional[str]:
    """Anacrusis: notes before the phrase's first downbeat."""
    start = p.start
    bar = model.bar_at(start)
    if start == bar.start:
        return None
    downbeat = bar.end
    if p.end <= downbeat:
        return None
    return str(Fraction(downbeat - start, WHOLE))


def analyze(model: ScoreModel, staff: Optional[int] = None) -> List[Phrase]:
    staff = model.melody_staff() if staff is None else staff
    line = melody_line(model, staff)
    phrases = split_phrases(model, line)
    label_phrases(phrases)
    return phrases


def scale_degree(pitch: int, tonic: int, mode: str) -> str:
    names_major = {0: "1", 1: "b2", 2: "2", 3: "b3", 4: "3", 5: "4", 6: "#4", 7: "5", 8: "b6", 9: "6", 10: "b7", 11: "7"}
    names_minor = {0: "1", 1: "b2", 2: "2", 3: "3", 4: "#3", 5: "4", 6: "#4", 7: "5", 8: "6", 9: "#6", 10: "7", 11: "#7"}
    return (names_minor if mode == "minor" else names_major)[(pitch - tonic) % 12]
