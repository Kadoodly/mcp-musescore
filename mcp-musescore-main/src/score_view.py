"""The compact text view of a score (get_score's default format).

It uses the same notation as write_voice's notation= parameter (see
src/notation.py), so what Claude reads it can write back:

    Score "Nocturne" · 8 bars · 4/4 · version 1234500
    Staves: s0 Flute [flute] treble, range C4-A6 | s1 Piano [piano] treble | s2 Piano [piano] bass
    Key: Eb major / C minor (-3)
    bar 1 (4/4) tempo q=72 "Lento"
      s0: r:q G4(mf "dolce") Bb4 Eb5
      s1: [Eb4 G4 Bb4]:h~ [Eb4 G4 Bb4]
      s2: Eb2:w
    bar 2 = bar 1
    bars 3-4: rests

Bars are listed with one line per staff and voice ("s1:" is staff 1 voice
0, "s1v1:" staff 1 voice 1). Staves without notes in a bar are left out.
A bar with the same music as an earlier listed bar is shown as "= bar N".
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import instruments
from .notation import articulation_name, format_duration, note_name, read_only_mark, ticks_fraction

LEGEND = ("Notation: C4 = middle C, concert pitch; durations w=1/1 h=1/2 q=1/4 e=1/8 s=1/16 t=1/32, \".\" dotted; "
          "a duration carries over to the next notes until another is given; ~ = tied to the next note; "
          "{3:2 ...} = tuplet; (...) = markings of that note (!grace2, !name = read-only: grace notes and markings "
          "that can't be written); @3/8 = position in the bar as a fraction of a whole "
          "note (the offset parameter). s1v1 = staff 1 voice 1. Staves missing from a bar have only rests there.")

_METRONOME = re.compile(r"\s*[♩♪𝅗𝅥𝅝.]+\s*=\s*[\d.]+\s*$")


def _quote(text: str) -> str:
    return f"'{text}'" if '"' in text and "'" not in text else f'"{text}"'


def _offset(tick: int, bar_start: int) -> str:
    return "@" + ticks_fraction(tick - bar_start)


class BarMap:
    """Bar numbers and start ticks for every bar of the score, from the time
    signature list (exact: each change of the actual bar length is listed)."""

    def __init__(self, analysis: Dict[str, Any]):
        self.starts: List[Tuple[int, int, int]] = []    # (bar, start, end)
        known = {m["measure"]: (m["startTick"], m["endTick"]) for m in analysis.get("measures", [])}
        sigs = sorted(analysis.get("timeSignatures", []), key=lambda t: t["measure"])
        total = analysis.get("numMeasures", 0)
        pos = 0
        for bar in range(1, total + 1):
            if bar in known:
                start, end = known[bar]
            else:
                sig = None
                for t in sigs:
                    if t["measure"] <= bar:
                        sig = t
                if sig and sig["measure"] == bar:
                    pos = sig["tick"]
                length = 1920 * sig["numerator"] // sig["denominator"] if sig else 1920
                start, end = pos, pos + length
            self.starts.append((bar, start, end))
            pos = end

    def position(self, tick: int) -> Tuple[int, int]:
        """(bar number, bar start tick) of the bar holding tick."""
        for bar, start, end in self.starts:
            if start <= tick < end:
                return bar, start
        if self.starts:
            bar, start, end = self.starts[-1]
            return (bar, start) if tick < end else (bar + 1, end)
        return 1, 0

    def text(self, tick: int, current_bar: Optional[int] = None) -> str:
        bar, start = self.position(tick)
        off = _offset(tick, start)
        return off if bar == current_bar else f"bar {bar}{off}"


# ---------------------------------------------------------------------------
# One voice of one bar
# ---------------------------------------------------------------------------

def _lyric_text(lyr: Dict[str, Any]) -> str:
    text = lyr.get("text", "")
    if lyr.get("syllabic") in ("begin", "middle"):
        text += "-"
    verse = lyr.get("verse", 0) or 0      # 0-based in MuseScore; v2"..." is the second verse
    return (f"v{verse + 1}" if verse else "") + _quote(text)


def _element_token(el: Dict[str, Any], duration: Optional[str], extra_marks: Sequence[str]) -> str:
    marks: List[str] = list(extra_marks)
    for art in el.get("articulations", []) or []:
        marks.append(articulation_name(art))
    for lyr in sorted(el.get("lyrics", []) or [], key=lambda l: l.get("verse", 0) or 0):
        marks.append(_lyric_text(lyr))
    if el.get("graceNotes"):
        marks.append(read_only_mark(f"grace{el['graceNotes']}"))
    dur = f":{duration}" if duration else ""
    if el.get("name") == "Rest":
        head, tie = "r", ""
    else:
        notes = sorted(el.get("notes", []), key=lambda n: n.get("pitchMidi", 0))
        names = [note_name(n["pitchMidi"], n.get("tpc")) for n in notes]
        tied = [bool(n.get("tiedForward")) for n in notes]
        if len(notes) == 1:
            head, tie = names[0], "~" if tied[0] else ""
        elif all(tied):
            head, tie = "[" + " ".join(names) + "]", "~"
        else:
            head, tie = "[" + " ".join(n + ("~" if t else "") for n, t in zip(names, tied)) + "]", ""
    return head + dur + tie + (f"({' '.join(marks)})" if marks else "")


def format_voice(elements: List[Dict[str, Any]], bar_start: int, inline: Dict[int, List[str]]) -> str:
    """Notation for one voice's chords/rests in a bar (sorted by tick).
    inline: tick -> markings to show on the element starting there."""
    tokens: List[str] = []
    last: Optional[int] = None
    expected = bar_start
    i = 0
    while i < len(elements):
        el = elements[i]
        tick = el["startTick"]
        if tick != expected:
            tokens.append(_offset(tick, bar_start))
        tup = el.get("tuplet") if el.get("isTuplet") else None
        if tup:
            group = [el]
            key = (tup.get("startTick"), tup.get("actual"), tup.get("normal"))
            j = i + 1
            while j < len(elements):
                t2 = elements[j].get("tuplet") if elements[j].get("isTuplet") else None
                if not t2 or (t2.get("startTick"), t2.get("actual"), t2.get("normal")) != key or key[0] is None:
                    break
                group.append(elements[j])
                j += 1
            inner, inner_last = [], None
            for g in group:
                nominal = g.get("nominalTicks") or round(g["durationTicks"] * tup["actual"] / tup["normal"])
                inner.append(_element_token(g, None if nominal == inner_last else format_duration(nominal),
                                            inline.pop(g["startTick"], [])))
                inner_last = nominal
            tokens.append("{" + f"{tup['actual']}:{tup['normal']} " + " ".join(inner) + "}")
            last = None
            expected = group[-1]["startTick"] + group[-1]["durationTicks"]
            i = j
            continue
        ticks = el["durationTicks"]
        tokens.append(_element_token(el, None if ticks == last else format_duration(ticks), inline.pop(tick, [])))
        last = ticks
        expected = tick + ticks
        i += 1
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Bars
# ---------------------------------------------------------------------------

def _marking_text(mk: Dict[str, Any]) -> Optional[Tuple[str, bool]]:
    """(text, inline?) of a staff marking; None for markings shown elsewhere."""
    kind = mk.get("type")
    if kind == "dynamic":
        return mk.get("value", ""), True
    if kind == "text":
        return "text=" + _quote(mk.get("text", "")), True
    if kind == "chordSymbol":
        return "chord=" + mk.get("text", "").replace(" ", ""), True
    if kind == "fermata":
        return "fermata", True
    if kind == "breath":
        return "breath", False
    if kind == "tripletFeel":
        return "triplet feel " + _quote(mk.get("text", "")), False
    return None


def _tempo_text(mk: Dict[str, Any]) -> str:
    words = _METRONOME.sub("", mk.get("text", "") or "").strip()
    return f"tempo q={mk.get('bpm')}" + (f" {_quote(words)}" if words else "")


class _Bar:
    def __init__(self, number: int):
        self.number = number
        self.head: List[str] = []       # shown after "bar N", not part of the music
        self.lines: List[str] = []      # the music (compared between bars)


def _bar_blocks(analysis: Dict[str, Any], staves: Optional[Set[int]], barmap: BarMap) -> List[_Bar]:
    header_staves = analysis.get("staves", [])
    clef_changes = {(c["measure"], st["index"]): c["clef"] for st in header_staves for c in st.get("clefChanges", [])}
    key_changes = {}
    for st in header_staves:
        for k in st.get("keyChanges", []):
            key_changes.setdefault(k["measure"], set()).add((k["fifths"], k["name"]))
    spanners = analysis.get("spanners", [])
    prev_sig = None
    out = []
    measures = analysis.get("measures", [])
    for idx, m in enumerate(measures):
        bar = _Bar(m["measure"])
        start, end = m["startTick"], m["endTick"]
        sig = m.get("timeSignature")
        if sig != prev_sig:
            bar.head.append(f"({sig})")
            prev_sig = sig
        if m.get("repeatStart"):
            bar.head.append("|:")
        for mk in m.get("marks", []) or []:
            bar.head.append(_quote(mk.get("text") or mk.get("name") or mk.get("type")))
        for fifths, name in sorted(key_changes.get(m["measure"], ())):
            bar.head.append(f"key {name} ({fifths})")
        for (bn, s), clef in sorted(clef_changes.items()):
            if bn == m["measure"] and (staves is None or s in staves):
                bar.head.append(f"s{s} clef {clef}")

        inline: Dict[int, Dict[int, List[str]]] = {}    # staff -> tick -> marks
        loose: List[str] = []
        by_staff = m.get("elements", {})
        for mk in m.get("markings", []) or []:
            kind = mk.get("type")
            if kind == "tempo":
                if mk.get("visible") is not False:
                    bar.head.append(_tempo_text(mk) + (_offset(mk["tick"], start) if mk["tick"] != start else ""))
                continue
            if kind == "rehearsalMark":
                bar.head.append(f"[{mk.get('text', '')}]" + (_offset(mk["tick"], start) if mk["tick"] != start else ""))
                continue
            st = mk.get("staff")
            if staves is not None and st not in staves:
                continue
            got = _marking_text(mk)
            if not got:
                continue
            text, can_inline = got
            starts_here = [e for e in by_staff.get(f"staff{st}", []) if e["startTick"] == mk["tick"]]
            if can_inline and starts_here:
                inline.setdefault(st, {}).setdefault(mk["tick"], []).append(text)
            else:
                loose.append(f"s{st} {text}{_offset(mk['tick'], start)}")

        for staff_key in sorted(by_staff, key=lambda k: int(k[5:])):
            s = int(staff_key[5:])
            if staves is not None and s not in staves:
                continue
            els = by_staff[staff_key]
            by_voice: Dict[int, List[Dict[str, Any]]] = {}
            for e in sorted(els, key=lambda e: e["startTick"]):
                by_voice.setdefault(e.get("voice", 0), []).append(e)
            # a voice is listed if it has notes (rests alone are left out)
            shown = [v for v in sorted(by_voice) if any(e.get("name") == "Chord" for e in by_voice[v])]
            # a marking goes on the lowest listed voice with a note/rest starting there
            staff_inline = inline.get(s, {})
            target: Dict[int, int] = {}
            for t in staff_inline:
                for v in shown:
                    if any(e["startTick"] == t for e in by_voice[v]):
                        target[t] = v
                        break
            for v in shown:
                mine = {t: staff_inline[t] for t, tv in target.items() if tv == v}
                label = f"s{s}" + (f"v{v}" if v else "")
                bar.lines.append(f"  {label}: {format_voice(by_voice[v], start, mine)}")
            for t, texts in staff_inline.items():
                if t not in target:
                    loose.extend(f"s{s} {x}{_offset(t, start)}" for x in texts)

        for sp in spanners:
            if sp.get("startTick") is None or not (start <= sp["startTick"] < end):
                continue
            if staves is not None and sp.get("staff") not in staves:
                continue
            kind = sp["type"]
            name = sp.get("name") or ""
            label = kind + (f"({name})" if name and name.lower() != kind.lower() else "")
            if sp.get("text"):
                label += " " + _quote(sp["text"])
            if kind == "volta" and sp.get("endings"):
                label += f" {sp['endings']}"
            stop = barmap.text(sp["endTick"], m["measure"]) if sp.get("endTick") is not None else "?"
            staff = f" s{sp['staff']}" if sp.get("staff") is not None and kind not in ("volta", "gradualTempoChange") else ""
            loose.append(f"{label}{staff}{_offset(sp['startTick'], start)}-{stop}")
        if loose:
            bar.lines.append("  marks: " + "; ".join(loose))
        if m.get("repeatEnd"):
            count = m.get("repeatCount") or 2
            bar.head.append(":|" + (f" x{count}" if count != 2 else ""))
        out.append(bar)
    return out


def format_bars(analysis: Dict[str, Any], staves: Optional[Iterable[int]] = None,
                only_bars: Optional[Iterable[int]] = None) -> List[str]:
    """The bar lines of the compact view. only_bars: list just these bars."""
    staff_set = set(staves) if staves is not None else None
    wanted = set(only_bars) if only_bars is not None else None
    barmap = BarMap(analysis)
    blocks = [b for b in _bar_blocks(analysis, staff_set, barmap) if wanted is None or b.number in wanted]
    first_seen: Dict[Tuple[str, ...], int] = {}
    lines: List[str] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        head = (" " + " ".join(b.head)) if b.head else ""
        key = tuple(b.lines)
        if not b.lines:
            j = i
            while j + 1 < len(blocks) and not blocks[j + 1].lines and not blocks[j + 1].head and \
                    blocks[j + 1].number == blocks[j].number + 1:
                j += 1
            label = f"bar {b.number}" if j == i else f"bars {b.number}-{blocks[j].number}"
            lines.append(f"{label}{head}: rests")
            i = j + 1
            continue
        if key in first_seen:
            src = first_seen[key]
            j = i
            # extend a run: bar k+1 = bar src+1 ...
            while j + 1 < len(blocks):
                nb = blocks[j + 1]
                want = src + (j + 1 - i)
                if nb.head or nb.number != blocks[j].number + 1 or not nb.lines or first_seen.get(tuple(nb.lines)) != want:
                    break
                j += 1
            if j == i:
                lines.append(f"bar {b.number}{head} = bar {src}")
            else:
                lines.append(f"bars {b.number}-{blocks[j].number}{head} = bars {src}-{src + j - i}")
            i = j + 1
            continue
        first_seen[key] = b.number
        lines.append(f"bar {b.number}{head}")
        lines.extend(b.lines)
        i += 1
    return lines


def format_header(analysis: Dict[str, Any], version: Optional[int] = None) -> List[str]:
    title = analysis.get("title") or "(untitled)"
    first = [f"Score {_quote(title)}"]
    if analysis.get("composer"):
        first.append(f"by {analysis['composer']}")
    first.append(f"{analysis.get('numMeasures')} bars")
    sigs = analysis.get("timeSignatures", [])
    if sigs:
        first.append(", ".join(f"{t['numerator']}/{t['denominator']}" + (f" from bar {t['measure']}" if i else "")
                               for i, t in enumerate(sigs[:6])) + (" ..." if len(sigs) > 6 else ""))
    if analysis.get("durationSeconds"):
        secs = int(analysis["durationSeconds"])
        first.append(f"{secs // 60}:{secs % 60:02d} long")
    if version is not None:
        first.append(f"version {version}")
    lines = [" · ".join(first)]

    staff_texts = []
    for st in analysis.get("staves", []):
        text = f"s{st['index']} {st.get('instrument') or '?'} [{st.get('instrumentId') or '?'}]"
        if st.get("clef"):
            text += f" {st['clef']}"
        tr = st.get("transposition")
        if tr and tr.get("chromatic"):
            text += f", written {abs(tr['chromatic'])} semitone(s) {'higher' if tr['chromatic'] < 0 else 'lower'} than it sounds"
        inst = instruments.find(st.get("instrumentId"))
        if inst and inst.get("range") and not inst.get("unpitched"):
            text += f", range {instruments.range_text(inst)}"
        if st.get("visible") is False:
            text += ", hidden"
        staff_texts.append(text)
    lines.append("Staves: " + " | ".join(staff_texts))

    key = analysis.get("keySignature")
    if key:
        lines.append(f"Key: {key.get('name')} ({key.get('fifths')})")
    tempos = [t for t in analysis.get("tempos", [])]
    if not tempos and analysis.get("initialTempoBpm"):
        lines.append(f"Tempo: q={analysis['initialTempoBpm']} (no tempo mark)")
    return lines


def format_score(analysis: Dict[str, Any], version: Optional[int] = None, cursor: Optional[Dict[str, Any]] = None,
                 staves: Optional[Iterable[int]] = None, only_bars: Optional[Iterable[int]] = None,
                 legend: bool = True) -> str:
    lines = format_header(analysis, version)
    if legend:
        lines.append(LEGEND)
    first, last = analysis.get("firstMeasure"), analysis.get("lastMeasure")
    if first and last and (first != 1 or last != analysis.get("numMeasures")):
        lines.append(f"(bars {first}-{last} of {analysis.get('numMeasures')})")
    lines.extend(format_bars(analysis, staves, only_bars))
    if cursor:
        lines.append(format_cursor_line(cursor))
    return "\n".join(lines)


def format_cursor_line(cursor: Dict[str, Any]) -> str:
    where = "end of the score" if cursor.get("atEndOfScore") else f"bar {cursor.get('measure')} beat {cursor.get('beat')}"
    return f"Cursor: {where} (tick {cursor.get('tick')}), s{cursor.get('staff')} v{cursor.get('voice')}"
