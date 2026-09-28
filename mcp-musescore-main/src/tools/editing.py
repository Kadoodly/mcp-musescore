"""Range editing (transpose, clear, replace a section) and the text, clef,
layout and score-info tools."""

from typing import Any, Dict, List, Literal, Optional

from ..client import MuseScoreClient
from ..types import Offset, SectionPart
from ..validation import voice_events
from .navigation import position_params, with_version

RANGE_DOC = """
            start_measure: First bar (1-based). With end_measure (inclusive; default start_measure),
                the range is whole bars.
            end_measure: Last bar (inclusive).
            start_tick: Or an exact range in ticks (480 per quarter note): start ...
            end_tick: ... and end (exclusive)."""


VERSION_ARG = """
            expected_version: Refuse if the score changed since this version (see write_voice)."""


def append_doc(*texts):
    """Adds shared argument docs to a tool's docstring (before @mcp.tool reads it)."""
    def deco(func):
        func.__doc__ = (func.__doc__ or "").rstrip() + "".join(texts)
        return func
    return deco


def range_params(start_measure, end_measure, start_tick, end_tick) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in (("startMeasure", start_measure), ("endMeasure", end_measure),
                       ("startTick", start_tick), ("endTick", end_tick)):
        if value is not None:
            out[key] = value
    return out


def section_parts(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """replace_section parts in the plugin's form: notation becomes events."""
    out = []
    for i, part in enumerate(parts):
        p = {"staff": part["staff"]}
        if part.get("voice") is not None:
            p["voice"] = part["voice"]
        p["events"] = voice_events(part.get("events"), part.get("notation"), f"part {i}")
        out.append(p)
    return out


def setup_editing_tools(mcp, client: MuseScoreClient):
    """Setup range editing, text, clef, layout and score-info tools."""

    @mcp.tool()
    async def replace_section(
        start_measure: int,
        parts: List[SectionPart],
        end_measure: Optional[int] = None,
        clear_other_voices: bool = True,
        expected_version: Optional[int] = None,
    ):
        """Replace whole bars with new music for any number of staves/voices, in ONE undo step: the
        way to rewrite a passage, or to add new bars for several instruments at once (bars past the
        end of the score are appended).

        Each part is {"staff": 0, "voice": 0, "notation": "C4:q D4 E4 F4 | G4:w"} (or "events" as in
        write_voice) and must fill bars start_measure..end_measure exactly (use rests). The other
        voices of the staves you give are cleared unless clear_other_voices is false; staves you don't
        give are left alone. Everything is checked before anything is written.

        Args:
            start_measure: First bar (1-based); up to (number of bars + 1) to add at the end.
            parts: The music, per staff and voice (voice defaults to 0).
            end_measure: Last bar (inclusive). Default: start_measure.
            clear_other_voices: Clear voices of those staves that no part gives (default true).
            expected_version: Refuse if the score changed since this version (see write_voice).
        """
        params: Dict[str, Any] = {"startMeasure": start_measure, "parts": section_parts(parts),
                                  "clearOtherVoices": clear_other_voices}
        if end_measure is not None:
            params["endMeasure"] = end_measure
        return await client.send_command("replaceSection", with_version(params, expected_version))

    @mcp.tool()
    @append_doc(RANGE_DOC, VERSION_ARG)
    async def transpose(
        semitones: int,
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        start_tick: Optional[int] = None,
        end_tick: Optional[int] = None,
        staves: Optional[List[int]] = None,
        voices: Optional[List[int]] = None,
        chord_symbols: bool = True,
        key_signatures: bool = False,
        expected_version: Optional[int] = None,
    ):
        """Transpose a range chromatically by a number of semitones (one undo step), respelling the
        notes (D major up 2 = E major). Notes tied into or out of the range move with it; chord symbols
        move too. To change the key of a piece or section, set key_signatures=true (whole bars): the key
        signatures in the range move as well, and the old key comes back after the range.

        Args:
            semitones: -48..48, e.g. 12 = an octave up, -3 = a minor third down, 5 = a fourth up.
            staves: Staff indices (default: all staves).
            voices: Voices 0-3 (default: all).
            chord_symbols: Transpose the chord symbols in the range (default true).
            key_signatures: Also transpose the key signatures (default false: for moving a phrase)."""
        params: Dict[str, Any] = {"semitones": semitones}
        params.update(range_params(start_measure, end_measure, start_tick, end_tick))
        if staves is not None:
            params["staves"] = staves
        if voices is not None:
            params["voices"] = voices
        if not chord_symbols:
            params["chordSymbols"] = False
        if key_signatures:
            params["keySignatures"] = True
        return await client.send_command("transpose", with_version(params, expected_version))

    @mcp.tool()
    @append_doc(RANGE_DOC, VERSION_ARG)
    async def clear_range(
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        start_tick: Optional[int] = None,
        end_tick: Optional[int] = None,
        staves: Optional[List[int]] = None,
        voices: Optional[List[int]] = None,
        markings: bool = True,
        expected_version: Optional[int] = None,
    ):
        """Empty a range (one undo step): notes become rests in voice 0 and disappear in voices 1-3;
        whole bars get a single bar rest. A note held into the range from before keeps the part
        before it; a note that starts inside and lasts beyond the end is removed entirely (the
        result says so). The bars themselves stay (delete_measures removes bars).

        Args:
            staves: Staff indices (default: all staves).
            voices: Voices 0-3 (default: all).
            markings: Also remove dynamics, texts, chord symbols and fermatas in the range (default true)."""
        params: Dict[str, Any] = range_params(start_measure, end_measure, start_tick, end_tick)
        if staves is not None:
            params["staves"] = staves
        if voices is not None:
            params["voices"] = voices
        params["markings"] = markings
        return await client.send_command("clearRange", with_version(params, expected_version))

    @mcp.tool()
    async def add_text(
        text: str,
        kind: Literal["staff", "system", "expression"] = "staff",
        staff: Optional[int] = None,
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
    ):
        """Add text to the score: staff text (e.g. "pizz.", "solo", "con sord."), system text (applies
        to all staves, e.g. "Tutti") or an expression ("dolce", "espressivo", "cresc."). It is placed at
        a note/rest; inside a rest it splits the rest there.

        Args:
            text: The text.
            kind: staff (default), system or expression.
            staff: Staff index (default: the cursor's).
            measure: Bar (1-based); default the cursor position.
            offset: With measure: position inside the bar.
            tick: Absolute tick instead of measure."""
        params: Dict[str, Any] = {"text": text, "kind": kind}
        params.update(position_params(measure, tick, staff, None, offset))
        return await client.send_command("addText", params)

    @mcp.tool()
    async def add_chord_symbol(
        text: str,
        staff: Optional[int] = None,
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
    ):
        """Add a chord symbol (e.g. "Cmaj7", "F#m7b5", "G7/B", "Bbsus4") above a staff, replacing one at
        the same position. write_voice can also add them per note (chord=Cmaj7).

        Args:
            text: The chord symbol.
            staff: Staff index (default: the cursor's).
            measure: Bar (1-based); default the cursor position.
            offset: With measure: position inside the bar ("1/2" = beat 3 in 4/4).
            tick: Absolute tick instead of measure."""
        params: Dict[str, Any] = {"text": text}
        params.update(position_params(measure, tick, staff, None, offset))
        return await client.send_command("addChordSymbol", params)

    @mcp.tool()
    @append_doc(RANGE_DOC)
    async def add_pedal_marks(
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        start_tick: Optional[int] = None,
        end_tick: Optional[int] = None,
        staff: Optional[int] = None,
    ):
        """Add piano pedal marks: "Ped." at the start of the range and the release sign at its end.
        MuseScore's plugin interface can't add pedal lines, so these are symbols only: they don't
        sustain on playback. The release must be before the end of the score.

        Args:
            staff: Staff index (default: the cursor's; usually the lower piano staff)."""
        params: Dict[str, Any] = range_params(start_measure, end_measure, start_tick, end_tick)
        if staff is not None:
            params["staff"] = staff
        return await client.send_command("addPedalMarks", params)

    @mcp.tool()
    async def add_clef(
        type: Literal["treble", "bass", "alto", "tenor", "soprano", "mezzo-soprano", "baritone", "treble 8vb",
                      "treble 8va", "treble 15ma", "bass 8vb", "bass 8va", "percussion"],
        staff: Optional[int] = None,
        measure: Optional[int] = None,
        offset: Optional[Offset] = None,
        tick: Optional[int] = None,
    ):
        """Change the clef of a staff from a position on (e.g. a bass clef for a low passage in the
        right hand). Pitches don't change, only how they are shown.

        Args:
            type: The clef.
            staff: Staff index (default: the cursor's).
            measure: Bar (1-based); default the cursor position.
            offset: With measure: position inside the bar.
            tick: Absolute tick instead of measure."""
        params: Dict[str, Any] = {"type": type}
        params.update(position_params(measure, tick, staff, None, offset))
        return await client.send_command("addClef", params)

    @mcp.tool()
    async def add_layout_break(type: Literal["line", "page", "section"], measure: int):
        """Start a new system (line), page or section after a bar.

        Args:
            type: line, page or section.
            measure: The bar after which the break comes (1-based)."""
        return await client.send_command("addLayoutBreak", {"type": type, "measure": measure})

    @mcp.tool()
    async def set_measures_per_system(count: int):
        """Lay the score out with `count` bars on every system (line), using MuseScore's system locks.
        0 removes the locks. Changes the selection, so it can't be part of an atomic sequence.

        Args:
            count: Bars per system (1-64), or 0 to go back to automatic layout."""
        return await client.send_command("setMeasuresPerSystem", {"count": count})

    @mcp.tool()
    async def set_score_info(
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        composer: Optional[str] = None,
        lyricist: Optional[str] = None,
    ):
        """Set the title, subtitle, composer and/or lyricist: the text at the top of the first page
        (replacing what is there) and the score's properties. An empty string removes it. Undo
        restores the texts on the page, not the file properties.

        Args:
            title: Title.
            subtitle: Subtitle.
            composer: Composer.
            lyricist: Lyricist."""
        params = {k: v for k, v in (("title", title), ("subtitle", subtitle), ("composer", composer),
                                    ("lyricist", lyricist)) if v is not None}
        return await client.send_command("setScoreInfo", params)

    @mcp.tool()
    async def export_score(path: str, format: str = "pdf"):
        """Export the open score to a file, e.g. to share or listen to it.

        Args:
            path: File path on the computer running MuseScore, e.g. "C:/Users/me/Music/song". The
                extension is added if missing. The folder must exist.
            format: pdf, png, svg, mid (MIDI), musicxml, mxl (compressed MusicXML), mp3, wav, ogg, flac,
                mscz, mscx, brf."""
        return await client.send_command("exportScore", {"path": path, "format": format})

    @mcp.tool()
    async def save_score():
        """Save the score in MuseScore (like Ctrl+S). A score that was never saved opens the Save dialog
        in MuseScore, where the user picks the file."""
        return await client.send_command("saveScore")
