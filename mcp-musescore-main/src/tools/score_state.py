"""Reading the score and keeping track of it: the compact score view,
versions and change log, the user's selection, a consistency check, and
MuseScore's instrument list."""

from typing import Any, Dict, List, Literal, Optional

from .. import instruments as inst_data
from ..client import MuseScoreClient
from ..score_view import format_bars, format_score
from ..utils.lilypond_converter import json_to_lilypond
from .connection import format_score_header

MAX_BARS = 200


def _bar_list(ranges: List[List[int]]) -> str:
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in ranges)


def setup_score_state_tools(mcp, client: MuseScoreClient):
    """Setup score reading, version and instrument tools."""

    @mcp.tool()
    async def get_score(
        format: Literal["compact", "lilypond", "json"] = "compact",
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
        staves: Optional[List[int]] = None,
    ):
        """Read the open score: title, instruments (staff numbers, clefs, ranges), key, time signatures,
        tempo, and the music bar by bar, plus the score version and the cursor.

        Start here with an existing score. The default compact format uses the same notation as
        write_voice's notation parameter, so you can edit what you read directly:
            bar 1 (4/4) tempo q=72 "Lento"
              s0: r:q G4(mf "Hel-") Bb4 Eb5~
              s1: [Eb4 G4 Bb4]:h [Eb4 Ab4 C5](chord=Ab/Eb)
            bar 2 = bar 1
            bars 3-4: rests
        s1 = staff 1 (voice 0); s1v1 = staff 1 voice 1. @3/8 = a position in the bar, as for offset=.

        Args:
            format: "compact" (default); "lilypond" (LilyPond notation); "json" (the raw data: ticks,
                voices, every note and marking).
            start_measure: First bar to read (1-based). Default: the first. Big scores: read in chunks.
            end_measure: Last bar (inclusive). Default: the last (at most 200 bars per call in compact).
            staves: Only these staves (compact format). Default: all.
        """
        params: Dict[str, Any] = {}
        if start_measure is not None:
            params["startMeasure"] = start_measure
        if end_measure is not None:
            params["endMeasure"] = end_measure
        res = await client.send_command("getScore", params)
        if not res.get("success") or "analysis" not in res:
            return res
        analysis = res["analysis"]
        version = res.get("version", res.get("scoreVersion"))
        if format == "json":
            return {"analysis": analysis, "cursor": res.get("cursor"), "version": version}
        if format == "lilypond":
            header = format_score_header(analysis, res.get("cursor"))
            return f"Version {version}\n{header}\n[Score]\n{json_to_lilypond(analysis, start_measure, end_measure)}"
        note = ""
        measures = analysis.get("measures", [])
        if end_measure is None and len(measures) > MAX_BARS:
            first = measures[0]["measure"]
            analysis["measures"] = measures[:MAX_BARS]
            analysis["lastMeasure"] = first + MAX_BARS - 1
            note = (f"\n(Showing bars {first}-{first + MAX_BARS - 1} of {analysis.get('numMeasures')}; "
                    f"read more with start_measure={first + MAX_BARS}.)")
        return format_score(analysis, version, res.get("cursor"), staves) + note

    @mcp.tool()
    async def get_version():
        """The score's current version. Every change raises it: your edits, and edits made in MuseScore
        (noticed when the plugin next looks at the score). Pass it as expected_version to an edit to make
        sure nobody changed the score in between, or to get_changes_since later."""
        return await client.send_command("getVersion")

    @mcp.tool()
    async def get_changes_since(version: int, show_music: bool = True):
        """What changed in the score since a version: which bars were edited, by you (mcp) or in
        MuseScore (user), and the music of those bars now, in get_score's compact notation. Use it
        after an edit was refused for expected_version, or to see what the user did.

        Args:
            version: A version from earlier (get_score, get_version, or scoreVersion in a result).
            show_music: Include the changed bars' music (default true).
        """
        res = await client.send_command("getChangesSince", {"version": version})
        if "error" in res:
            return res
        now = res.get("version")
        if not res.get("complete"):
            return (f"The score is at version {now}. The change log can't say what changed since version {version} "
                    f"(too old, from an earlier run of the plugin, or another score was opened). "
                    f"Read the score again with get_score.")
        changes = res.get("changes", [])
        if not changes:
            return f"No changes since version {version} (the score is at version {now})."
        lines = [f"Version {version} -> {now}: {len(changes)} change(s)."]
        for c in changes:
            where = f"bars {_bar_list(c['bars'])}" if c.get("bars") else "no bar contents (layout, lines or settings)"
            lines.append(f"  v{c['version']} {c['source']}: {c['action']} ({where})")
        bars = res.get("changedBars", [])
        if bars:
            lines.append(f"Changed bars now: {_bar_list(bars)} (the score has {res.get('numMeasures')} bars)")
        if show_music and bars:
            wanted = [b for a, z in bars for b in range(a, z + 1)]
            score = await client.send_command("getScore", {"startMeasure": min(wanted), "endMeasure": max(wanted)})
            if score.get("success") and "analysis" in score:
                lines.extend(format_bars(score["analysis"], only_bars=wanted))
                if score.get("version") != now:
                    lines.append(f"(The score changed again while reading: now version {score.get('version')}.)")
        return "\n".join(lines)

    @mcp.tool()
    async def get_selection(show_music: bool = True):
        """What is selected in MuseScore right now, e.g. when the user says "change these bars" or
        "this note": a range (bars and staves) or single elements (with pitch and position).
        fromUser tells whether the user selected it (false: it only shows the plugin's cursor).

        Args:
            show_music: For a range, include its music in get_score's compact notation (default true).
        """
        sel = await client.send_command("getSelection")
        if "error" in sel or not show_music or sel.get("kind") != "range":
            return sel
        score = await client.send_command("getScore", {"startMeasure": sel["startMeasure"], "endMeasure": sel["endMeasure"]})
        if score.get("success") and "analysis" in score:
            staves = list(range(sel["startStaff"], sel["endStaff"] + 1))
            sel["music"] = "\n".join(format_bars(score["analysis"], staves=staves))
        return sel

    @mcp.tool()
    async def open_score(path: str):
        """Open a score file in MuseScore: .mscz/.mscx, MusicXML (.musicxml/.xml/.mxl), MIDI (.mid),
        and the other formats MuseScore imports. Only works while no score is open in the MuseScore
        window the plugin runs in (MuseScore 4 opens a second file in a new window, which the plugin
        can't reach); otherwise ask the user to open the file, or to close the current score first.
        Importing may show MuseScore's import dialog, which the user has to confirm.

        Args:
            path: The file, on the computer running MuseScore (e.g. "C:/Users/me/Music/song.mscz")."""
        return await client.send_command("openScore", {"path": path})

    @mcp.tool()
    async def check_score():
        """Check the score for corrupted bars (voices whose durations don't add up to the bar), which
        MuseScore warns about on saving. Returns the bar and staff of each."""
        return await client.send_command("checkScore")

    @mcp.tool()
    async def list_instruments(query: str = "", group: str = "", instrument_id: Optional[str] = None):
        """MuseScore's instruments: the ids that add_instrument and set_instrument_sound take, with
        clefs, transposition and ranges (sounding MIDI pitches: comfortable range, then the full one).
        Write parts within the range. From MuseScore 4.7.5's instrument list.

        Args:
            query: Words to look for in names/descriptions, e.g. "clarinet", "alto sax", "drum".
            group: Only this group: woodwinds, brass, percussion, vocals, keyboards, strings, electronic,
                free reed, plucked.
            instrument_id: Details of one instrument, including the drum map (MIDI pitch = drum) for
                drum kits and other unpitched percussion.
        """
        if instrument_id:
            inst = inst_data.find(instrument_id)
            if not inst:
                close = inst_data.search(instrument_id.replace("-", " "), limit=8)
                return {"error": f"No instrument with id {instrument_id!r}",
                        "similar": [inst_data.describe(i) for i in close]}
            out = {"instrument": inst_data.describe(inst)}
            if inst.get("description"):
                out["description"] = inst["description"]
            if inst.get("drums"):
                out["drums"] = [f"{p}: {name} (voice {v})" for p, name, v in inst["drums"]]
            return out
        hits = inst_data.search(query, group, limit=60)
        if not hits:
            return {"error": f"No instrument matches {query!r}" + (f" in group {group!r}" if group else "")}
        return "\n".join(inst_data.describe(i) for i in hits)
