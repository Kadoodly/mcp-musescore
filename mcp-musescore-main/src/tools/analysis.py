"""Music analysis tools: key, harmony, phrases, form, rhythm and tempo."""

from typing import Literal, Optional

from ..analysis import reports
from ..analysis.model import ScoreModel
from ..client import MuseScoreClient


def setup_analysis_tools(mcp, client: MuseScoreClient):
    """Setup music analysis tools."""

    async def _model():
        res = await client.send_command("getScore")
        if not res.get("success") or "analysis" not in res:
            return None, res
        return ScoreModel(res["analysis"]), None

    @mcp.tool()
    async def analyze_score():
        """Musical overview of the open score: key and mode, meter, tempo (with suspicious
        tempo marks flagged), form (intro/verse/chorus/bridge guesses with bar ranges), main chord
        progressions with Roman numerals, melody range/phrasing, rhythm traits and dynamics.

        Start here before editing or writing music; then use the analyze_* tools for detail.
        """
        model, err = await _model()
        return err if model is None else reports.overview_report(model)

    @mcp.tool()
    async def analyze_harmony(
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        resolution: Literal["beat", "half", "bar"] = "beat",
        include_melody: bool = True,
    ):
        """Chords bar by bar, with Roman numerals in the detected key, repeating progressions
        (chord loops), most-used chords and harmonic rhythm. Chords are computed from every sounding
        note, including notes held from earlier, and shown with their bass note (F/A = F over A).

        Args:
            start_measure: First bar (1-based). Default: start of the score.
            end_measure: Last bar (inclusive). Default: end of the score.
            resolution: How finely to look for chord changes: every beat (default), half bar, or bar.
            include_melody: Set False to name chords from the accompaniment only (ignores melody passing notes).
        """
        model, err = await _model()
        if model is None:
            return err
        return reports.harmony_report(model, start_measure, end_measure, resolution, include_melody)

    @mcp.tool()
    async def analyze_phrases(
        staff: Optional[int] = None,
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
    ):
        """Melodic phrases: where each starts and ends (bar.beat), pickups, length, range, contour,
        the note and chord it ends on, its lyrics, and which phrases repeat or vary each other
        (letters: A, A' = variation, B ...).

        Args:
            staff: Staff to analyse. Default: the staff with the most lyrics, else the top staff.
            start_measure: Only list phrases from this bar on.
            end_measure: Only list phrases up to this bar.
        """
        model, err = await _model()
        return err if model is None else reports.phrases_report(model, staff, start_measure, end_measure)

    @mcp.tool()
    async def analyze_structure():
        """Form of the piece: sections with bar ranges and start times (intro, verse, chorus, bridge,
        link, ending, as best guesses from chord loops, melody entries and lyric repetition), the
        section pattern (A B C B ...), repeated passages (identical, same melody, same accompaniment
        or same chords), and repeat signs, voltas, D.C./D.S./Coda markers and rehearsal marks.
        """
        model, err = await _model()
        return err if model is None else reports.structure_report(model)

    @mcp.tool()
    async def analyze_rhythm(
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        staff: Optional[int] = None,
    ):
        """Meter and rhythm: time signatures with beat grouping (e.g. 6/8 = 2 dotted-quarter beats),
        pickup and irregular bars, swing, and per staff the note values used, how many attacks fall
        on/off the beat, tuplets, notes held across barlines, articulations, syncopations and
        anticipations (with positions), and where melody phrases enter relative to the beat.

        Args:
            start_measure: First bar (1-based). Default: start of the score.
            end_measure: Last bar (inclusive). Default: end of the score.
            staff: Only this staff. Default: all staves.
        """
        model, err = await _model()
        return err if model is None else reports.rhythm_report(model, start_measure, end_measure, staff)

    @mcp.tool()
    async def get_tempo_map():
        """Tempo: every tempo marking (quarter-note BPM and the felt beat, e.g. dotted quarter in 6/8),
        flags for marks that are off the rhythmic grid, redundant or stacked (typical MIDI-import
        leftovers), gradual changes (rit./accel.), the effective tempo bar by bar, fermatas, breath
        marks, and the total playing time.
        """
        model, err = await _model()
        return err if model is None else reports.tempo_report(model)
