"""A compact text notation for music, used to read scores (get_score's
compact view) and to write them (write_voice / replace_section notation=).

    C4:q D4 E4:e F4 | [C4 E4 G4]:h~ [C4 E4 G4]:q r | {3:2 C5:e B4 A4} G4:q.(mf staccato "la")

Notes      C4 is middle C (MIDI 60); accidentals # ## b bb (F#3, Bb5, C##4);
           or a MIDI number (60). Names keep their spelling (F#4 is not Gb4).
Chords     [C4 E4 G4]
Rests      r
Durations  after ":", as a fraction of a whole note ("1/4", "3/8", "5/8") or
           a letter: w=1/1 h=1/2 q=1/4 e=1/8 s=1/16 t=1/32 x=1/64 (b=2/1),
           with "." or ".." for dotted values (q. = 3/8). A duration carries
           over to the following notes until another is given.
Ties       "~" after a note or chord ties it to the next one (C4:h~ C4:q);
           inside a chord only the marked pitches are tied ([C4~ E4]:h).
Tuplets    {3:2 C4:e D4 E4} = three eighths in the time of two.
Markings   in parentheses right after a note/rest: a dynamic (mf), articulations
           (staccato, accent, tenuto, marcato, ...), ornaments (trill, mordent,
           short-trill, turn, inverted-turn), bowings (up-bow, down-bow), fermata, a lyric syllable
           ("Hel-" continues the word on the next note, "_" leaves a note
           without one), text="dolce", chord=Cmaj7 (chord symbol).
Bar lines  "|" is optional; when given, the bars between two "|" must all have
           the same length (a check against miscounting).

parse_notation() turns such text into the plugin's write_voice events;
format_* functions produce the same syntax from score data.
"""

import re
from fractions import Fraction
from typing import Any, Dict, List, Optional, Tuple

from .utils.durations import DurationError, TICKS_PER_WHOLE, duration_ticks

# ---------------------------------------------------------------------------
# Durations
# ---------------------------------------------------------------------------

DURATION_LETTERS = {"b": 3840, "w": 1920, "h": 960, "q": 480, "e": 240, "s": 120, "t": 60, "x": 30}
LETTER_OF_TICKS = {v: k for k, v in DURATION_LETTERS.items()}


def ticks_fraction(ticks: int) -> str:
    """Ticks as reduced "n/d" of a whole note (480 -> "1/4", 0 -> "0")."""
    if ticks == 0:
        return "0"
    f = Fraction(ticks, TICKS_PER_WHOLE)
    return f"{f.numerator}/{f.denominator}"


def format_duration(ticks: int) -> str:
    """Letters for plain, dotted and double-dotted values (q, q., h..), else "n/d"."""
    for base, letter in LETTER_OF_TICKS.items():
        if ticks == base:
            return letter
        if ticks * 2 == base * 3:
            return letter + "."
        if ticks * 4 == base * 7:
            return letter + ".."
    return ticks_fraction(ticks)


_DURATION_RE = re.compile(r"^(?:([bwhqestx])(\.{0,2})|(\d+)\s*/\s*(\d+))$")


def parse_duration_text(text: str, where: str) -> Tuple[int, str]:
    """(ticks, "n/d") of a duration token: a letter with dots or "n/d"."""
    m = _DURATION_RE.match(text.strip())
    if not m:
        raise ValueError(f"{where}: {text!r} is not a duration (use a letter w h q e s t x with dots, or a fraction like 3/8)")
    if m.group(1):
        base = DURATION_LETTERS[m.group(1)]
        dots = len(m.group(2))
        ticks = base + (base // 2 if dots >= 1 else 0) + (base // 4 if dots == 2 else 0)
        if base // 4 * 4 != base and dots == 2:
            raise ValueError(f"{where}: {text} is shorter than the shortest supported value")
        return ticks, ticks_fraction(ticks)
    value = f"{m.group(3)}/{m.group(4)}"
    try:
        ticks = duration_ticks(value, f"{where} duration")
    except DurationError as e:
        raise ValueError(str(e)) from None
    return ticks, ticks_fraction(ticks)


# ---------------------------------------------------------------------------
# Pitches
# ---------------------------------------------------------------------------

NOTE_NAME_PATTERN = r"^([A-Ga-g])(##|#|bb|b)?(-1|[0-9])$"
_NOTE_RE = re.compile(NOTE_NAME_PATTERN)
_STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_ALTER = {"": 0, "#": 1, "##": 2, "b": -1, "bb": -2}


def parse_pitch(token: Any, where: str):
    """A MIDI number (0-127) or a note name like C4, F#3, Bb5; returns it in the
    plugin's form: the int, or the name with an upper-case letter."""
    if isinstance(token, bool):
        raise ValueError(f"{where}: {token!r} is not a pitch")
    if isinstance(token, int):
        if not 0 <= token <= 127:
            raise ValueError(f"{where}: {token} is not a MIDI pitch 0-127")
        return token
    text = str(token).strip().replace("♯", "#").replace("♭", "b")
    if text.isdigit():
        return parse_pitch(int(text), where)
    m = _NOTE_RE.match(text)
    if not m:
        raise ValueError(f"{where}: {token!r} is not a MIDI pitch or a note name like C4, F#3, Bb5")
    midi = (int(m.group(3)) + 1) * 12 + _STEPS[m.group(1).upper()] + _ALTER[m.group(2) or ""]
    if not 0 <= midi <= 127:
        raise ValueError(f"{where}: {text} is outside MIDI 0-127")
    return m.group(1).upper() + (m.group(2) or "") + m.group(3)


def pitch_midi(value) -> int:
    """MIDI number of a parse_pitch() result."""
    if isinstance(value, int):
        return value
    m = _NOTE_RE.match(value)
    return (int(m.group(3)) + 1) * 12 + _STEPS[m.group(1).upper()] + _ALTER[m.group(2) or ""]


_TPC_NAMES = ["Fbb", "Cbb", "Gbb", "Dbb", "Abb", "Ebb", "Bbb", "Fb", "Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F",
              "C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "E#", "B#", "F##", "C##", "G##", "D##",
              "A##", "E##", "B##"]
_SHARP_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]


def note_name(midi: int, tpc: Optional[int] = None) -> str:
    """Name with octave (C4 = 60), spelled by tpc when known (tpc -1..33)."""
    if tpc is not None and -1 <= tpc <= 33:
        name = _TPC_NAMES[tpc + 1]
        alter = name.count("#") - name.count("b")
        octave = (midi - alter) // 12 - 1
        # a spelling that doesn't match the pitch class would print a wrong note
        if (_STEPS[name[0]] + alter - midi) % 12 == 0:
            return f"{name}{octave}"
    return f"{_SHARP_NAMES[midi % 12]}{midi // 12 - 1}"


# ---------------------------------------------------------------------------
# Markings
# ---------------------------------------------------------------------------

DYNAMICS = ("pppppp", "ppppp", "pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff", "fffff", "ffffff",
            "fp", "pf", "sf", "sfz", "sff", "sffz", "sfff", "sfffz", "sfp", "sfpp", "rfz", "rf", "fz")

# name -> SMuFL symbol, as the plugin's articulationSymbols
ARTICULATIONS = {
    "staccato": "articStaccato", "staccatissimo": "articStaccatissimo", "tenuto": "articTenuto",
    "accent": "articAccent", "marcato": "articMarcato", "portato": "articTenutoStaccato",
    "accent-staccato": "articAccentStaccato", "marcato-staccato": "articMarcatoStaccato",
    "stress": "articStress", "unstress": "articUnstress",
    "up-bow": "stringsUpBow", "down-bow": "stringsDownBow", "harmonic": "stringsHarmonic",
    "snap-pizzicato": "pluckedSnapPizzicato", "open": "brassMuteOpen", "stopped": "brassMuteClosed",
    # ornaments
    "trill": "ornamentTrill", "mordent": "ornamentMordent", "short-trill": "ornamentShortTrill",
    "turn": "ornamentTurn", "inverted-turn": "ornamentTurnInverted",
}
ARTICULATION_ALIASES = {"stacc": "staccato", "stac": "staccato", "ten": "tenuto", "acc": "accent", "marc": "marcato",
                        "staccatiss": "staccatissimo", ">": "accent", "^": "marcato", "-": "tenuto", ".": "staccato",
                        "tr": "trill", "upbow": "up-bow", "downbow": "down-bow", "prall": "short-trill",
                        "inverted-mordent": "short-trill", "snap-pizz": "snap-pizzicato"}
_SYMBOL_TO_NAME = {sym: name for name, sym in ARTICULATIONS.items()}


def articulation_name(symbol: str) -> str:
    """Our name for a MuseScore articulation symbol (articStaccatoBelow -> staccato)."""
    base = re.sub(r"(Above|Below)$", "", symbol or "")
    return _SYMBOL_TO_NAME.get(base, symbol or "?")


def _parse_marks(text: str, where: str, is_rest: bool) -> Dict[str, Any]:
    marks: Dict[str, Any] = {}
    i, n = 0, len(text)
    items: List[Tuple[str, Optional[str], bool]] = []   # (key, value, quoted)
    while i < n:
        c = text[i]
        if c in " \t\n,;":
            i += 1
            continue
        if c in "\"'":
            j = text.find(c, i + 1)
            if j < 0:
                raise ValueError(f"{where}: unclosed quote in markings ({text})")
            items.append(("lyric", text[i + 1:j], True))
            i = j + 1
            continue
        j = i
        while j < n and text[j] not in " \t\n,;=\"'":
            j += 1
        word = text[i:j]
        if j < n and text[j] == "=":
            j += 1
            if j < n and text[j] in "\"'":
                k = text.find(text[j], j + 1)
                if k < 0:
                    raise ValueError(f"{where}: unclosed quote in markings ({text})")
                items.append((word.lower(), text[j + 1:k], True))
                i = k + 1
            else:
                k = j
                while k < n and text[k] not in " \t\n,;":
                    k += 1
                items.append((word.lower(), text[j:k], False))
                i = k
            continue
        items.append((word, None, False))
        i = j
    for key, value, _ in items:
        if value is not None:
            if key in ("lyric", "text", "chord"):
                if not value:
                    raise ValueError(f"{where}: {key} is empty")
                if key in marks:
                    raise ValueError(f"{where}: two {key} markings")
                marks[key] = value
            elif key in ("dynamic", "dyn"):
                marks["dynamic"] = value
            else:
                raise ValueError(f"{where}: unknown marking {key}= (use text=, chord=, lyric=)")
            continue
        word = key
        low = word.lower()
        if low in DYNAMICS:
            if "dynamic" in marks:
                raise ValueError(f"{where}: two dynamics")
            marks["dynamic"] = low
        elif low == "fermata" or low in ARTICULATIONS or low in ARTICULATION_ALIASES:
            name = ARTICULATION_ALIASES.get(low, low)
            marks.setdefault("articulations", []).append(name)
        else:
            raise ValueError(f"{where}: unknown marking {word!r} (dynamics like mf, articulations "
                             f"{', '.join(ARTICULATIONS)}, fermata, \"lyric\", text=\"...\", chord=Cmaj7)")
    if is_rest:
        bad = [a for a in marks.get("articulations", []) if a != "fermata"]
        if bad:
            raise ValueError(f"{where}: a rest can only have a fermata, not {bad[0]}")
        if "lyric" in marks:
            raise ValueError(f"{where}: a rest can't have a lyric")
    return marks


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_SEPARATORS = " \t\r\n,;"


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.last_ticks: Optional[int] = None
        self.last_text: Optional[str] = None
        self.count = 0
        self.bar_marks: List[int] = []    # tick positions of "|" marks
        self.pos = 0                       # ticks written so far

    def error(self, message: str):
        snippet = self.s[max(0, self.i - 20):self.i + 20].replace("\n", " ")
        raise ValueError(f"notation: {message} (near ...{snippet}...)")

    def skip(self):
        while self.i < len(self.s) and self.s[self.i] in _SEPARATORS:
            self.i += 1

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def items(self, tuplet: Optional[Tuple[int, int]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        while True:
            self.skip()
            c = self.peek()
            if not c:
                if tuplet:
                    self.error("a tuplet is missing its closing }")
                return out
            if c == "}":
                if not tuplet:
                    self.error("} without a tuplet")
                self.i += 1
                return out
            if c == "|":
                if tuplet:
                    self.error("a barline can't be inside a tuplet")
                self.bar_marks.append(self.pos)
                self.i += 1
                continue
            if c == "{":
                if tuplet:
                    self.error("tuplets can't be nested")
                self.i += 1
                self.skip()
                m = re.compile(r"(\d+)\s*:\s*(\d+)").match(self.s, self.i)
                if not m:
                    self.error("a tuplet starts with its ratio, e.g. {3:2 C4:e D4 E4}")
                self.i = m.end()
                ratio = (int(m.group(1)), int(m.group(2)))
                if ratio[0] < 2 or ratio[1] < 1 or ratio[0] == ratio[1]:
                    self.error(f"{ratio[0]}:{ratio[1]} is not a tuplet ratio")
                start = self.pos
                inner = self.items(ratio)
                if not inner:
                    self.error("empty tuplet")
                nominal = sum(e["_ticks"] for e in inner)
                self.pos = start + nominal * ratio[1] // ratio[0]
                for e in inner:
                    e.pop("_ticks")
                out.append({"tuplet": f"{ratio[0]}:{ratio[1]}", "events": inner})
                continue
            ev = self.event()
            if tuplet:
                ev["_ticks"] = self.last_ticks
            else:
                self.pos += self.last_ticks
            out.append(ev)

    def word(self, stop: str) -> str:
        j = self.i
        while j < len(self.s) and self.s[j] not in _SEPARATORS + stop:
            j += 1
        w = self.s[self.i:j]
        self.i = j
        return w

    def event(self) -> Dict[str, Any]:
        self.count += 1
        where = f"note {self.count}"
        pitches: List[Any] = []
        tied: List[Any] = []
        rest = False
        if self.peek() == "[":
            self.i += 1
            while True:
                self.skip()
                c = self.peek()
                if not c:
                    self.error("a chord is missing its closing ]")
                if c == "]":
                    self.i += 1
                    break
                token = self.word("]~:(")
                if not token:
                    if c in ":(":
                        self.error(f"{where}: a chord's duration and markings go after the ], e.g. [C4 E4]:q(mf)")
                    self.error(f"unexpected {c!r} in a chord")
                p = parse_pitch(token, where)
                if pitch_midi(p) in [pitch_midi(x) for x in pitches]:
                    self.error(f"{where}: the chord lists {token} twice")
                pitches.append(p)
                if self.peek() == "~":
                    self.i += 1
                    tied.append(p)
            if not pitches:
                self.error(f"{where}: empty chord []")
        else:
            token = self.word(":~([]{}|")
            if not token:
                self.error(f"unexpected {self.peek()!r}")
            if token.startswith("@"):
                self.error(f"{token} is a position from get_score's view; to write, fill the gap with a rest "
                           f"(r:h) or start the passage there (measure + offset)")
            if token.lower() == "r":
                rest = True
            else:
                pitches.append(parse_pitch(token, where))
        if self.peek() == ":":
            self.i += 1
            text = self.word("~(")
            self.last_ticks, self.last_text = parse_duration_text(text, where)
        elif self.last_ticks is None:
            self.error(f"{where} needs a duration (e.g. C4:q or C4:1/4); later notes may leave it out")
        if self.peek() == "~":
            self.i += 1
            if rest:
                self.error(f"{where}: a rest can't be tied")
            tied = list(pitches)
        marks: Dict[str, Any] = {}
        if self.peek() == "(":
            j = self.i + 1
            depth_quote = None
            while j < len(self.s):
                ch = self.s[j]
                if depth_quote:
                    if ch == depth_quote:
                        depth_quote = None
                elif ch in "\"'":
                    depth_quote = ch
                elif ch == ")":
                    break
                j += 1
            if j >= len(self.s):
                self.error(f"{where}: markings are missing their closing )")
            marks = _parse_marks(self.s[self.i + 1:j], where, rest)
            self.i = j + 1
        nxt = self.peek()
        if nxt and nxt not in _SEPARATORS + "[]{}|":
            self.error(f"{where}: unexpected {nxt!r} after the note (separate notes with spaces)")
        if rest:
            ev: Dict[str, Any] = {"rest": True, "duration": self.last_text}
        else:
            ev = {"pitches": pitches, "duration": self.last_text}
            if tied:
                ev["tie"] = True if len(tied) == len(pitches) else tied
        ev.update(marks)
        return ev


def parse_notation(text: str) -> List[Dict[str, Any]]:
    """write_voice events (the plugin's wire format) from notation text.
    Raises ValueError with the position of the problem."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("notation is empty")
    p = _Parser(text)
    events = p.items(None)
    if not events:
        raise ValueError("notation has no notes or rests")
    marks = p.bar_marks
    lengths = [b - a for a, b in zip(marks, marks[1:]) if b > a]
    if len(set(lengths)) > 1:
        shown = ", ".join(ticks_fraction(x) for x in lengths)
        raise ValueError(f"notation: bar check failed: the bars between | marks have different lengths ({shown}). "
                         f"Check the durations, or leave out the | marks")
    return events


def events_ticks(events: List[Dict[str, Any]]) -> int:
    """Total length in ticks of wire-format events."""
    total = 0
    for ev in events:
        if "tuplet" in ev:
            a, n = (int(x) for x in ev["tuplet"].split(":"))
            total += sum(duration_ticks(e["duration"]) for e in ev["events"]) * n // a
        else:
            total += duration_ticks(ev["duration"])
    return total
