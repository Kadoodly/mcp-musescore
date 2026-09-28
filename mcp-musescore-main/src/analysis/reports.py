"""Readable text reports built from the analysis modules."""

from collections import Counter, defaultdict
from fractions import Fraction
from typing import List, Optional

from . import harmony, phrases as phr, rhythm, structure
from .model import WHOLE, ScoreModel

POSITION_NOTE = ("Positions are bar.beat; '+1/16' means a 16th after that beat "
                 "(e.g. 4.2+3/16 = bar 4, beat 2, plus three 16ths).")


def _key(model: ScoreModel, chord_slices) -> harmony.Key:
    return harmony.detect_key(model, chord_slices)


def _staves_line(model: ScoreModel) -> str:
    return "; ".join(model.staff_label(s["index"]) for s in model.staves)


# ---------------------------------------------------------------------------
# Harmony
# ---------------------------------------------------------------------------

def harmony_report(model: ScoreModel, start_bar: Optional[int] = None, end_bar: Optional[int] = None,
                   resolution: str = "beat", include_melody: bool = True) -> str:
    all_slices = harmony.slices(model, resolution, include_melody=include_melody)
    key = _key(model, all_slices)
    chord_slices = [s for s in all_slices if (start_bar is None or model.bar_at(s.end - 1).number >= start_bar)
                    and (end_bar is None or model.bar_at(s.start).number <= end_bar)]
    lines = [f"Key: {key.name} ({key.evidence}). Key signature: {model.raw.get('keySignature', {}).get('name', '?')}."]
    key_changes = [(b.number, b.key_fifths) for i, b in enumerate(model.bars) if i and b.key_fifths != model.bars[i - 1].key_fifths]
    if key_changes:
        lines.append("Key signature changes: " + ", ".join(f"bar {n} ({f:+d})" for n, f in key_changes))
    lines.append("Chords are recognised from all sounding notes (held notes included); "
                 "confidence = share of the notes the chord explains.")
    lines.append("")

    # Per bar
    per_bar = defaultdict(list)
    for s in chord_slices:
        per_bar[model.bar_at(s.start).number].append(s)
    for bar in model.bars_in(start_bar, end_bar):
        items = per_bar.get(bar.number, [])
        # a chord held over from the previous bar
        if not items or items[0].start > bar.start:
            held = next((s for s in all_slices if s.start < bar.start < s.end), None)
            if held:
                items = [held] + items
        parts = []
        for s in items:
            if not s.chord:
                parts.append("(no notes)")
                continue
            at = "" if s.start <= bar.start else f" @{model.fmt_pos(s.start)}"
            low = " ?" if s.chord.confidence < 0.6 else ""
            parts.append(f"{s.chord.symbol} [{harmony.roman(s.chord, key)}]{at}{low}")
        lines.append(f"bar {bar.number}: " + " · ".join(parts))

    # Loops and vocabulary
    simple = harmony.bar_chords(model, all_slices, simple=True)
    loops = harmony.find_loops({b: v for b, v in simple.items()
                                if (start_bar is None or b >= start_bar) and (end_bar is None or b <= end_bar)})
    if loops:
        lines.append("")
        lines.append("Repeating progressions (triad level):")
        for first, length, k, pattern in loops:
            numerals = []
            for sym in pattern:
                sl = next((s for s in all_slices if s.chord and harmony.simple_symbol(s.chord) == sym), None)
                numerals.append(harmony.simple_roman(sl.chord, key) if sl else "?")
            lines.append(f"  bars {first}–{first + length * k - 1}: {' – '.join(pattern)}  "
                         f"({' – '.join(numerals)}), {length}-bar cycle ×{k}")

    weights = Counter()
    for s in chord_slices:
        if s.chord:
            weights[s.chord.symbol] += s.end - s.start
    total = sum(weights.values()) or 1
    if weights:
        lines.append("")
        lines.append("Most used chords (by time): " + ", ".join(
            f"{c} {round(100 * w / total)}%" for c, w in weights.most_common(8)))
        changes = len([s for s in chord_slices if s.chord])
        bars_n = len(model.bars_in(start_bar, end_bar)) or 1
        lines.append(f"Harmonic rhythm: {changes} chord changes in {bars_n} bars (~{changes / bars_n:.1f} per bar).")

    symbols = [(model.fmt_pos(m.get("tick", b.start)), m.get("text")) for b in model.bars_in(start_bar, end_bar)
               for m in b.markings if m.get("type") == "chordSymbol"]
    if symbols:
        lines.append("Chord symbols written in the score: " + ", ".join(f"{t} @{p}" for p, t in symbols[:40]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phrases
# ---------------------------------------------------------------------------

def phrases_report(model: ScoreModel, staff: Optional[int] = None, start_bar: Optional[int] = None,
                   end_bar: Optional[int] = None) -> str:
    staff = model.melody_staff() if staff is None else staff
    all_phrases = phr.analyze(model, staff)
    slices = harmony.slices(model, "beat")
    key = _key(model, slices)
    lo = model.bar(start_bar).start if start_bar else 0
    hi = model.bar(end_bar).end if end_bar else model.end_tick
    shown = [(i, p) for i, p in enumerate(all_phrases) if p.end > lo and p.start < hi]

    lines = [f"Melody: {model.staff_label(staff)}, {len(all_phrases)} phrases. Key {key.name}.",
             "Letters group phrases: same letter = same tune (A' = varied, e.g. new pitches on the same rhythm).",
             POSITION_NOTE, ""]
    for i, p in shown:
        bar_len = model.bar_at(p.start).nominal.bar_ticks
        length = (p.end - p.start) / bar_len
        pitches = [n.pitch for n in p.notes]
        hi_n = max(p.notes, key=lambda n: n.pitch)
        lo_n = min(p.notes, key=lambda n: n.pitch)
        last = p.notes[-1]
        chord = next((s.chord for s in slices if s.start <= last.start < s.end and s.chord), None)
        pick = phr.pickup(model, p)
        rel = ""
        if p.similar_to is not None:
            rel = {"repeat": "repeats", "transposed": "transposes", "varied": "varies",
                   "rhythm": "same rhythm as"}[p.relation] + f" #{p.similar_to + 1}"
        text = p.lyric_text()
        lines.append(
            f"#{i + 1} {p.label}  {model.fmt_span(p.start, p.end)}  ({length:.1f} bars, {len(p.notes)} notes)"
            + (f", pickup {pick}" if pick else ", starts on the beat" if model.is_on_beat(p.start) else ", starts off the beat")
        )
        detail = (f"    range {model.name(lo_n.pitch, lo_n.tpc)}–{model.name(hi_n.pitch, hi_n.tpc)}, "
                  f"{phr.contour(p)}, ends on {model.name(last.pitch, last.tpc)} "
                  f"(scale degree {phr.scale_degree(last.pitch, key.tonic, key.mode)})")
        if chord:
            detail += f" over {chord.symbol} [{harmony.roman(chord, key)}]"
        if rel:
            detail += f"; {rel}"
        lines.append(detail)
        if text:
            lines.append(f'    "{text}"')

    groups = defaultdict(list)
    for i, p in enumerate(all_phrases):
        groups[p.label.rstrip("'")].append(i + 1)
    recurring = {k: v for k, v in groups.items() if len(v) > 1}
    if recurring:
        lines.append("")
        lines.append("Recurring phrase shapes: " + "; ".join(f"{k}: #{', #'.join(map(str, v))}" for k, v in recurring.items()))

    lengths = [(p.end - p.start) / model.bar_at(p.start).nominal.bar_ticks for p in all_phrases]
    picks = Counter(phr.pickup(model, p) for p in all_phrases)
    if lengths:
        lines.append(f"Typical phrase length: {sorted(lengths)[len(lengths) // 2]:.1f} bars. "
                     f"Phrase starts: " + ", ".join(
                         f"{c}× {'on a downbeat/beat' if k is None else 'pickup of ' + k}" for k, c in picks.most_common()))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def structure_report(model: ScoreModel) -> str:
    slices = harmony.slices(model, "beat")
    secs = structure.sections(model, slices)
    lines = ["Form (sections from chord loops, melody entries and repetition; names are best guesses):"]
    for s in secs:
        t = model.bar(s.start).time_seconds
        first_cycle = s.chords[:len(structure._cycle(s.chords))] if s.chords else []
        lines.append(f"  bars {s.start}–{s.end} ({s.bars}) {s.letter}: {s.name}"
                     + (f" @ {rhythm.fmt_time(t)}" if t is not None else "")
                     + (f" | chords: {' '.join(first_cycle)}" if first_cycle else "")
                     + (f' | "{" ".join(s.lyrics.split()[:8])}…"' if s.lyrics else ""))
    lines.append("  Pattern: " + " ".join(s.letter for s in secs))

    reps = structure.all_repeats(model, slices)
    if reps:
        lines.append("")
        lines.append("Repeated passages:")
        what = {"everything": "identical in all staves", "melody": "same melody",
                "accompaniment": "same accompaniment", "chords": "same chords"}
        for r in reps[:20]:
            lines.append(f"  bars {r.first}–{r.first + r.length - 1} = bars {r.second}–{r.second + r.length - 1} "
                         f"({r.length} bars, {what[r.layer]})")

    marks = []
    for b in model.bars:
        if b.repeat_start:
            marks.append(f"bar {b.number}: start repeat")
        if b.repeat_end:
            marks.append(f"bar {b.number}: end repeat ×{b.repeat_count or 2}")
        for m in b.marks:
            name, text = m.get("name") or m.get("type"), (m.get("text") or "").strip()
            marks.append(f"bar {b.number}: {name}" + (f' "{text}"' if text and text.lower() != name.lower() else ""))
        for m in b.markings:
            if m.get("type") == "rehearsalMark":
                marks.append(f"bar {b.number}: rehearsal mark {m.get('text')}")
    voltas = [s for s in model.spanners if s.get("type") == "volta"]
    for v in voltas:
        marks.append(f"{model.fmt_pos(v['startTick'])}: volta (ending {v.get('endings') or '?'})")
    lines.append("")
    lines.append("Repeat signs / jumps / rehearsal marks: " + ("; ".join(marks) if marks else "none"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rhythm & meter
# ---------------------------------------------------------------------------

def rhythm_report(model: ScoreModel, start_bar: Optional[int] = None, end_bar: Optional[int] = None,
                  staff: Optional[int] = None, max_examples: int = 12) -> str:
    lines = ["Meter:"]
    for first, last, meter in rhythm.meter_runs(model):
        lines.append(f"  bars {first}–{last}: {meter} — {meter.describe()}, beat groups "
                     f"{'+'.join(map(str, meter.groups))} × {Fraction(meter.unit, WHOLE)}")
    irr = rhythm.irregular_bars(model)
    lines.append("  Irregular bars: " + ("; ".join(f"bar {n} {d}" for n, d in irr) if irr else "none"))
    if model.swing:
        lines.append("  Swing: " + ", ".join(f"staff {s['staff']} unit {s['unit']} ratio {s['ratio']}%" for s in model.swing))
    lines.append(POSITION_NOTE)

    staves = [staff] if staff is not None else [s["index"] for s in model.staves]
    melody = model.melody_staff()
    for st in staves:
        r = rhythm.staff_rhythm(model, st, start_bar, end_bar)
        if not r or not r["notes"]:
            continue
        lines.append("")
        lines.append(f"{model.staff_label(st)}{' — melody' if st == melody else ''}: {r['notes']} notes, "
                     f"{r['onsets_per_bar']} attacks per bar")
        lines.append("  Durations: " + ", ".join(f"{n} {p}%" for n, p in r["durations"]))
        lines.append(f"  Attacks on the downbeat {r['on_downbeat_pct']}%, on any beat {r['on_beat_pct']}%, "
                     f"off the beat {100 - r['on_beat_pct']}%")
        if r["tuplets"]:
            lines.append(f"  Tuplets: {r['tuplets']} in bars {', '.join(map(str, r['tuplet_bars'][:20]))}")
        if r["tied_across_barline"]:
            lines.append(f"  Notes tied/held across a barline: {r['tied_across_barline']}")
        if r["articulations"]:
            lines.append(f"  Articulations: {r['articulations']}")
        sync = r["syncopations"]
        if sync:
            ant = [s for s in sync if s.kind == "anticipation"]
            lines.append(f"  Syncopation: {len(sync)} notes struck off the beat and held across it "
                         f"({len(ant)} anticipations, i.e. pushed just ahead of the beat)")
            for s in sync[:max_examples]:
                n = s.note
                lines.append(f"    {model.fmt_pos(n.start)} {model.name(n.pitch, n.tpc)} "
                             f"held over {model.fmt_pos(s.beat_tick)} ({s.kind})")
            if len(sync) > max_examples:
                lines.append(f"    … {len(sync) - max_examples} more")

    if staff is None or staff == melody:
        ph = phr.analyze(model, melody)
        ph = [p for p in ph if (start_bar is None or model.bar_at(p.start).number >= start_bar)
              and (end_bar is None or model.bar_at(p.start).number <= end_bar)]
        if ph:
            entries = Counter()
            for p in ph:
                bar, beat, _, rem = model.beat_of(p.start)
                entries[f"beat {beat}" + (f"+{Fraction(rem, WHOLE)}" if rem else "")] += 1
            lines.append("")
            lines.append("Phrase entry points in the melody: " + ", ".join(f"{k} ×{v}" for k, v in entries.most_common()))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------

def tempo_report(model: ScoreModel) -> str:
    lines = []
    marks = rhythm.tempo_marks(model)
    if model.duration_seconds:
        lines.append(f"Length: {len(model.bars)} bars, about {rhythm.fmt_time(model.duration_seconds)} of playback.")
    lines.append("Tempo marks (BPM is per quarter note, as MuseScore stores it; felt beat shown for the meter):")
    if not marks:
        lines.append(f"  none — default ♩ = {model.raw.get('initialTempoBpm')}")
    i = 0
    while i < len(marks):
        mk = marks[i]
        # A visible mark followed by hidden ones = a written-out gradual change
        hidden = []
        j = i + 1
        while j < len(marks) and not marks[j].visible:
            hidden.append(marks[j])
            j += 1
        if hidden and mk.visible:
            lines.append(f"  {model.fmt_pos(mk.tick)}–{model.fmt_pos(hidden[-1].tick)}: "
                         f"\"{mk.text or 'tempo change'}\" ♩ = {mk.bpm:g} → {hidden[-1].bpm:g} "
                         f"({len(hidden)} hidden tempo steps)")
            i = j
            continue
        i += 1
        meter = model.bar_at(mk.tick).meter
        felt, unit = rhythm.felt_bpm(mk.bpm, meter)
        felt_txt = f", felt beat ({unit}) = {felt}" if unit != "quarter" else ""
        lines.append(f"  {model.fmt_pos(mk.tick)} (tick {mk.tick}): ♩ = {mk.bpm:g}{felt_txt}"
                     + (f' "{mk.text}"' if mk.text else "")
                     + (f"  ⚠ {'; '.join(mk.flags)}" if mk.flags else ""))

    gradual = [s for s in model.spanners if s.get("type") == "gradualTempoChange"]
    written_out = any(not mk.visible for mk in marks)
    if gradual or not written_out:
        lines.append("Gradual tempo lines (rit./accel./rall.): " + ("" if gradual else "none"))
    for g in gradual:
        lines.append(f"  {g.get('name') or g.get('text') or 'tempo change'} from {model.fmt_pos(g['startTick'])} "
                     f"to {model.fmt_pos(g['endTick'])}")

    ranges = rhythm.tempo_ranges(model)
    if ranges:
        lines.append("Effective tempo by bar (includes gradual changes):")
        for first, last, a, b in ranges:
            lines.append(f"  bars {first}–{last}: ♩ = {a:g}" + (f" → {b:g}" if abs(a - b) > 0.01 else ""))

    ferm = [(model.fmt_pos(m.get("tick", b.start)), m.get("staff")) for b in model.bars for m in b.markings
            if m.get("type") == "fermata"]
    lines.append("Fermatas: " + (", ".join(f"{p} (staff {s})" for p, s in ferm) if ferm else "none"))
    breaths = [model.fmt_pos(m.get("tick", b.start)) for b in model.bars for m in b.markings if m.get("type") == "breath"]
    if breaths:
        lines.append("Breath marks / caesuras: " + ", ".join(breaths))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Expression
# ---------------------------------------------------------------------------

def expression_summary(model: ScoreModel) -> List[str]:
    dyn = [(model.fmt_pos(m.get("tick", b.start)), m.get("value")) for b in model.bars for m in b.markings
           if m.get("type") == "dynamic"]
    kinds = Counter(s.get("type") for s in model.spanners)
    hairpins = [s for s in model.spanners if s.get("type") == "hairpin"]
    out = ["Dynamics: " + (", ".join(f"{v} @{p}" for p, v in dyn[:20]) if dyn else "none")]
    if hairpins:
        out.append("Hairpins: " + ", ".join(f"{h.get('name') or 'hairpin'} {model.fmt_span(h['startTick'], h['endTick'])}"
                                            for h in hairpins[:15]))
    other = {k: v for k, v in kinds.items() if k not in ("hairpin", "gradualTempoChange", "volta")}
    if other:
        out.append("Lines: " + ", ".join(f"{k} ×{v}" for k, v in other.items()))
    return out


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

def overview_report(model: ScoreModel) -> str:
    slices = harmony.slices(model, "beat")
    key = _key(model, slices)
    lines = [f"{model.title or '(untitled)'} — {len(model.bars)} bars"
             + (f", about {rhythm.fmt_time(model.duration_seconds)}" if model.duration_seconds else ""),
             f"Staves: {_staves_line(model)}",
             f"Key: {key.name} ({key.evidence}); signature {model.raw.get('keySignature', {}).get('name', '?')}"]

    runs = rhythm.meter_runs(model)
    lines.append("Meter: " + "; ".join(f"bars {a}–{b} {m} ({m.describe()})" for a, b, m in runs))
    marks = rhythm.tempo_marks(model)
    if marks:
        m0 = marks[0]
        felt, unit = rhythm.felt_bpm(m0.bpm, model.bar_at(m0.tick).meter)
        tempo = f"♩ = {m0.bpm:g}" + (f" (felt {unit} = {felt})" if unit != "quarter" else "")
        changes = [mk for mk in marks[1:] if mk.visible]
        if changes:
            tempo += "; then " + ", ".join(f"♩ = {mk.bpm:g} @{model.fmt_pos(mk.tick)}" for mk in changes)
        flagged = sum(1 for mk in marks if mk.flags)
        if flagged:
            tempo += f" — {flagged} mark(s) look misplaced or redundant (see get_tempo_map)"
        lines.append("Tempo: " + tempo)

    secs = structure.sections(model, slices)
    lines.append("Form: " + " | ".join(f"{s.name.replace(' (likely)', '')} {s.start}–{s.end}" for s in secs))

    simple = harmony.bar_chords(model, slices, simple=True)
    loops = harmony.find_loops(simple)
    seen = set()
    loop_txt = []
    for first, length, k, pattern in loops:
        sig = tuple(pattern)
        if sig in seen:
            continue
        seen.add(sig)
        numerals = []
        for sym in pattern:
            sl = next((s for s in slices if s.chord and harmony.simple_symbol(s.chord) == sym), None)
            numerals.append(harmony.simple_roman(sl.chord, key) if sl else "?")
        loop_txt.append(f"{' – '.join(pattern)} ({' – '.join(numerals)}) from bar {first}")
    if loop_txt:
        lines.append("Main progressions: " + "; ".join(loop_txt))

    if model.has_lyrics() or model.notes:
        melody = model.melody_staff()
        ph = phr.analyze(model, melody)
        if ph:
            line_notes = [n for p in ph for n in p.notes]
            lo = min(line_notes, key=lambda n: n.pitch)
            hi = max(line_notes, key=lambda n: n.pitch)
            picks = Counter(phr.pickup(model, p) is not None for p in ph)
            lines.append(f"Melody ({model.staff_label(melody)}): range {model.name(lo.pitch, lo.tpc)}–"
                         f"{model.name(hi.pitch, hi.tpc)}, {len(ph)} phrases, {picks[True]} begin with a pickup")
        r = rhythm.staff_rhythm(model, melody)
        if r and r["notes"]:
            lines.append(f"Melody rhythm: mostly {', '.join(n for n, _ in r['durations'][:3])}; "
                         f"{100 - r['on_beat_pct']}% of attacks off the beat; {len(r['syncopations'])} syncopated notes")
    lines.extend(expression_summary(model))
    lines.append("")
    lines.append("More detail: analyze_harmony, analyze_phrases, analyze_structure, analyze_rhythm, get_tempo_map.")
    return "\n".join(lines)
