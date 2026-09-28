import logging
import re
from fractions import Fraction
from typing import Dict, Any, List, Optional

logger = logging.getLogger("LilyPondConverter")

TICKS_PER_QUARTER = 480

LILY_DYNAMICS = {
    "ppppp", "pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "fffff",
    "fp", "sf", "sff", "sfz", "sp", "spp", "rfz", "rf", "fz", "sfp", "sffz",
}

# Circle-of-fifths position (-7..7) -> LilyPond tonic of the major key.
MAJOR_KEYS = ["ces", "ges", "des", "as", "es", "bes", "f", "c", "g", "d", "a", "e", "b", "fis", "cis"]


def midi_to_lilypond_pitch(midi_pitch: int, tpc: Optional[int] = None) -> str:
    """
    Convert a MIDI pitch to LilyPond pitch syntax.
    If TPC (Tonal Pitch Class) is provided, uses it to determine enharmonic spelling (flats vs sharps).
    Example: 60 -> c', 67 -> g', 74 -> d''
    """
    try:
        # Fallback names if no TPC provided
        pitch_names = ['c', 'cis', 'd', 'dis', 'e', 'f', 'fis', 'g', 'gis', 'a', 'ais', 'b']

        if tpc is not None:
            # MuseScore TPC map (circle of fifths where 14 = C, 15 = G)
            tpc_map = {
                6: 'fes', 7: 'ces', 8: 'ges', 9: 'des', 10: 'as', 11: 'es', 12: 'bes', 13: 'f',
                14: 'c', 15: 'g', 16: 'd', 17: 'a', 18: 'e', 19: 'b', 20: 'fis',
                21: 'cis', 22: 'gis', 23: 'dis', 24: 'ais', 25: 'eis', 26: 'bis',
                27: 'fisis', 28: 'cisis', 29: 'gisis', 30: 'disis', 31: 'aisis', 32: 'eisis', 33: 'bisis',
                -1: 'feses', 0: 'ceses', 1: 'geses', 2: 'deses', 3: 'ases', 4: 'eses', 5: 'beses'
            }
            base_note = tpc_map.get(tpc, pitch_names[midi_pitch % 12])
        else:
            base_note = pitch_names[midi_pitch % 12]

        # Octave of the written letter: B#3 sounds as MIDI 60 but is written in
        # octave 3, Cb4 sounds as MIDI 59 but is written in octave 4.
        octave = (midi_pitch // 12) - 1
        if base_note.startswith('b') and base_note.endswith(('is', 'isis')) and midi_pitch % 12 in (0, 1):
            octave -= 1
        elif base_note.startswith('c') and base_note.endswith(('es', 'eses')) and midi_pitch % 12 in (10, 11):
            octave += 1

        # LilyPond: c (no mark) is C3, c' is C4 (MIDI 60)
        if octave >= 3:
            octave_mark = "'" * (octave - 3)
        else:
            octave_mark = "," * (3 - octave)

        return f"{base_note}{octave_mark}"
    except Exception as e:
        logger.error(f"Error converting MIDI pitch {midi_pitch}: {e}")
        return "c'"  # Safe fallback


def ticks_to_lilypond_duration(ticks: int) -> str:
    """
    Convert MuseScore ticks (480 per quarter) to a LilyPond duration.
    Plain and dotted values map directly; anything else (e.g. tuplet notes)
    becomes a scaled duration such as "8*2/3".
    """
    if ticks <= 0:
        return "4"
    for base in (1, 2, 4, 8, 16, 32, 64, 128):
        base_ticks = TICKS_PER_QUARTER * 4 // base
        if ticks == base_ticks:
            return str(base)
        if ticks * 2 == base_ticks * 3:
            return f"{base}."
        if ticks * 4 == base_ticks * 7:
            return f"{base}.."
    if ticks == TICKS_PER_QUARTER * 8:
        return "\\breve"
    # Scale the nearest shorter plain duration, e.g. 160 ticks -> 4*1/3
    for base in (1, 2, 4, 8, 16, 32, 64, 128):
        base_ticks = TICKS_PER_QUARTER * 4 // base
        if base_ticks <= ticks * 2:
            ratio = Fraction(ticks, base_ticks)
            return f"{base}*{ratio.numerator}/{ratio.denominator}"
    return "4"


def ticks_to_spacers(ticks: int) -> List[str]:
    """
    Greedily consume temporal gap into valid Lilypond spacer rests.
    """
    if ticks <= 0:
        return []

    spacers = []
    # Using standard valid LilyPond rhythm sizes sorted by size descending
    mapping = [
        (1920, "1"), (1440, "2."), (960, "2"), (720, "4."),
        (480, "4"), (360, "8."), (240, "8"), (180, "16."),
        (120, "16"), (60, "32"), (30, "64")
    ]

    remaining = ticks
    for tick_val, duration_str in mapping:
        while remaining >= tick_val:
            spacers.append(f"s{duration_str}")
            remaining -= tick_val

    if remaining > 0:
        spacers.append(f"s{ticks_to_lilypond_duration(remaining)}")

    return spacers


def _quote(text: str) -> str:
    return '"' + str(text).replace('\\', '').replace('"', "'") + '"'


def _lyric_markup(lyrics: List[Dict[str, Any]]) -> str:
    parts = []
    for lyr in sorted(lyrics, key=lambda l: l.get("verse", 0)):
        text = lyr.get("text", "")
        if not text:
            continue
        # Show the hyphen for syllables that continue on the next note.
        if lyr.get("syllabic") in ("begin", "middle"):
            text += " --"
        verse = lyr.get("verse", 0)
        parts.append(f"{verse + 1}. {text}" if verse else text)
    if not parts:
        return ""
    return "_" + _quote(" / ".join(parts))


def _marking_suffix(marking: Dict[str, Any]) -> str:
    kind = marking.get("type")
    if kind == "dynamic":
        value = (marking.get("value") or "").strip()
        return f"\\{value}" if value in LILY_DYNAMICS else ("_" + _quote(value) if value else "")
    if kind == "fermata":
        return "\\fermata"
    if kind == "text" and marking.get("text"):
        return "^" + _quote(marking["text"])
    return ""


def process_element(element: Dict[str, Any], suffix: str = "") -> str:
    """
    Process a single JSON element dictionary into LilyPond syntax.
    Handles 'Chord' (with notes) and 'Rest'.
    """
    try:
        elem_name = element.get("name", "")
        duration_ticks = element.get("durationTicks", 480)
        lily_duration = ticks_to_lilypond_duration(duration_ticks)

        if elem_name == "Rest":
            return f"r{lily_duration}{suffix}"

        elif elem_name == "Chord":
            tie = "~" if element.get("isTie") else ""
            lyric_str = _lyric_markup(element.get("lyrics", []))
            lily_notes = [
                midi_to_lilypond_pitch(n["pitchMidi"], n.get("tpc"))
                for n in element.get("notes", []) if n.get("pitchMidi") is not None
            ]

            if not lily_notes:
                return f"r{lily_duration}{suffix}{lyric_str}"
            elif len(lily_notes) == 1:
                return f"{lily_notes[0]}{lily_duration}{tie}{suffix}{lyric_str}"
            else:
                joined_notes = " ".join(lily_notes)
                return f"<{joined_notes}>{lily_duration}{tie}{suffix}{lyric_str}"
        else:
            return ""  # Ignore other elements without crashing
    except Exception as e:
        logger.error(f"Error parsing element: {e}")
        return ""


def _render_staff(
    staff_elements: List[Dict[str, Any]],
    base_tick: int,
    measure_starts: List[int],
    markings: Dict[int, List[Dict[str, Any]]],
    prefix: str,
) -> List[str]:
    """Render one staff's elements (any number of voices) as LilyPond lines."""
    voices: Dict[int, List[Dict[str, Any]]] = {}
    for elem in staff_elements:
        voices.setdefault(elem.get("voice", 0) + 1, []).append(elem)

    voice_commands = {1: "\\voiceOne", 2: "\\voiceTwo", 3: "\\voiceThree", 4: "\\voiceFour"}
    multi_voice = len(voices) > 1
    voice_strings = []

    for v_idx in sorted(voices.keys()):
        v_elems = sorted(voices[v_idx], key=lambda x: x.get("startTick", 0))
        formatted = [prefix] if prefix and v_idx == min(voices) else []
        current_tick = base_tick
        boundaries = [t for t in measure_starts if t > base_tick]

        for e in v_elems:
            e_tick = e.get("startTick", current_tick)

            # Fill gaps with spacers, split at barlines, with a bar check at
            # each measure boundary reached
            while boundaries and boundaries[0] <= e_tick:
                if boundaries[0] > current_tick:
                    formatted.extend(ticks_to_spacers(boundaries[0] - current_tick))
                    current_tick = boundaries[0]
                formatted.append("|")
                boundaries.pop(0)
            if e_tick > current_tick:
                formatted.extend(ticks_to_spacers(e_tick - current_tick))
                current_tick = e_tick

            suffix = "".join(_marking_suffix(m) for m in markings.get(e_tick, []) if v_idx == min(voices))
            processed = process_element(e, suffix)
            if processed:
                formatted.append(processed)
            current_tick = e_tick + e.get("durationTicks", 480)

        body = " ".join(formatted)
        if multi_voice:
            voice_strings.append(f"      \\new Voice {{ {voice_commands.get(v_idx, '')} {body} }}")
        else:
            voice_strings.append(f"      {body}")

    if multi_voice:
        return ["    <<", " \\\\\n".join(voice_strings), "    >>"]
    return voice_strings


def json_to_lilypond(
    score_data: Dict[str, Any],
    start_measure: Optional[int] = None,
    end_measure: Optional[int] = None,
) -> str:
    """
    Convert a getScore analysis (or a selection with an elements map per staff)
    into a LilyPond tree. With a score analysis, all measures are rendered
    unless start_measure/end_measure (1-based, inclusive) limit the range.
    """
    try:
        staves_info = score_data.get("staves", [])
        measure_starts: List[int] = []
        markings_by_staff: Dict[int, Dict[int, List[Dict[str, Any]]]] = {}
        tempos: Dict[int, List[Dict[str, Any]]] = {}

        if "measures" in score_data:
            measures = score_data.get("measures", [])
            if start_measure or end_measure:
                lo = start_measure or 1
                hi = end_measure or len(measures)
                measures = [m for m in measures if lo <= m.get("measure", 0) <= hi]

            elements_by_staff: Dict[str, List[Dict[str, Any]]] = {}
            for m in measures:
                measure_starts.append(m.get("startTick", 0))
                for staff_name, elems in m.get("elements", {}).items():
                    elements_by_staff.setdefault(staff_name, []).extend(elems)
                for mk in m.get("markings", []):
                    if mk.get("type") == "tempo":
                        tempos.setdefault(mk.get("tick", 0), []).append(mk)
                    elif mk.get("staff") is not None:
                        markings_by_staff.setdefault(mk["staff"], {}).setdefault(mk.get("tick", 0), []).append(mk)
            base_tick = measure_starts[0] if measure_starts else 0
        else:
            # A selection: {"elements": {"staff0": [...], ...}, "startTick": ...}
            elements_by_staff = score_data.get("elements", {}) or {}
            if isinstance(elements_by_staff, list):
                elements_by_staff = {f"staff{score_data.get('startStaff', 0)}": elements_by_staff}
            base_tick = score_data.get("startTick", 0)

        staff_names = sorted(elements_by_staff.keys(), key=lambda x: int(x.replace("staff", "") or 0))

        lily_parts = ["<<"]
        for staff in staff_names:
            staff_elements = [e for e in elements_by_staff.get(staff, []) if e]
            if not staff_elements:
                continue
            idx = int(staff.replace("staff", "") or 0)
            info = staves_info[idx] if idx < len(staves_info) else {}

            prefix_parts = []
            if info.get("keySignature") and "measures" in score_data:
                fifths = info["keySignature"].get("fifths", 0)
                if -7 <= fifths <= 7:
                    prefix_parts.append(f"\\key {MAJOR_KEYS[fifths + 7]} \\major")
            if "measures" in score_data and measure_starts:
                first = next((m for m in score_data["measures"] if m.get("startTick") == base_tick), None)
                if first and first.get("timeSignature"):
                    prefix_parts.append(f"\\time {first['timeSignature']}")
            if idx == 0:
                for tick in sorted(tempos):
                    if tick == base_tick:
                        for t in tempos[tick]:
                            # "Andante ♩ = 90" -> "Andante"; the metronome mark is rebuilt from bpm
                            word = re.sub(r"\S*\s*=\s*[\d.]+\s*$", "", t.get("text") or "").strip()
                            label = f"{_quote(word)} " if word else ""
                            prefix_parts.append(f"\\tempo {label}4 = {t.get('bpm')}")

            name = info.get("instrument")
            header = f"  \\new Staff \\with {{ instrumentName = {_quote(name)} }} {{" if name else "  \\new Staff {"
            lily_parts.append(f"  % {staff}")
            lily_parts.append(header)
            lily_parts.extend(_render_staff(
                staff_elements, base_tick, measure_starts,
                markings_by_staff.get(idx, {}), " ".join(prefix_parts),
            ))
            lily_parts.append("  }")

        lily_parts.append(">>")
        return "\n".join(lily_parts)

    except Exception as e:
        logger.error(f"Failed to convert JSON to LilyPond: {e}")
        return "<< >>"
