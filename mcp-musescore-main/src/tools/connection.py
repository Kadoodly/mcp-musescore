"""Connection and utility tools for MuseScore MCP."""

from typing import Any, Dict, Literal, Optional

from ..client import MuseScoreClient
from ..utils.lilypond_converter import json_to_lilypond


def format_cursor(cursor: Optional[Dict[str, Any]]) -> str:
    if not cursor:
        return "unknown"
    where = "end of score" if cursor.get("atEndOfScore") else f"measure {cursor.get('measure')}, beat {cursor.get('beat')}"
    return (f"{where} (tick {cursor.get('tick')}), staff {cursor.get('staff')} "
            f"({cursor.get('staffName')}), voice {cursor.get('voice')}")


def format_score_header(analysis: Dict[str, Any], cursor: Optional[Dict[str, Any]] = None) -> str:
    lines = []
    title = analysis.get("title") or "(untitled)"
    lines.append(f"Title: {title} | Measures: {analysis.get('numMeasures')} | Staves: {analysis.get('numStaves')}")

    key = analysis.get("keySignature")
    if key:
        fifths = key.get("fifths", 0)
        accidentals = f"{abs(fifths)} {'sharp' if fifths > 0 else 'flat'}{'s' if abs(fifths) != 1 else ''}" if fifths else "no sharps/flats"
        lines.append(f"Key signature: {key.get('name')} ({accidentals})")

    time_sigs = analysis.get("timeSignatures", [])
    if time_sigs:
        lines.append("Time signature: " + ", ".join(
            f"{t['numerator']}/{t['denominator']} (from m{t['measure']})" for t in time_sigs))

    # Visible tempo marks only (hidden ones are the steps of a written-out rit./accel.)
    hidden = {mk.get("tick") for m in analysis.get("measures", []) for mk in m.get("markings", [])
              if mk.get("type") == "tempo" and mk.get("visible") is False}
    tempos = [t for t in analysis.get("tempos", []) if t.get("tick") not in hidden]
    if tempos:
        lines.append("Tempo: " + ", ".join(
            f"{t['bpm']} BPM" + (f" \"{t['text']}\"" if t.get("text") else "") + f" (m{t['measure']})" for t in tempos))
    else:
        lines.append(f"Tempo: {analysis.get('initialTempoBpm')} BPM (no tempo marking)")

    lines.append("Staves:")
    for st in analysis.get("staves", []):
        key_note = ""
        if key and st.get("keySignature") and st["keySignature"].get("fifths") != key.get("fifths"):
            key_note = f", written key {st['keySignature'].get('name')}"
        lines.append(f"  staff {st.get('index')}: {st.get('instrument')} [{st.get('instrumentId')}], part {st.get('part')}{key_note}")

    if cursor:
        lines.append(f"Cursor: {format_cursor(cursor)}")
    return "\n".join(lines)


def setup_connection_tools(mcp, client: MuseScoreClient):
    """Setup connection and utility tools."""

    @mcp.tool()
    async def connect_to_musescore():
        """Connect to the MuseScore WebSocket API."""
        result = await client.connect()
        return {"success": result}

    @mcp.tool()
    async def ping_musescore():
        """Ping the MuseScore WebSocket API to check connection."""
        return await client.send_command("ping")

    @mcp.tool()
    async def get_score(
        format: Literal["lilypond", "json"] = "lilypond",
        start_measure: Optional[int] = None,
        end_measure: Optional[int] = None,
    ):
        """Get the current score: title, key and time signatures, tempo, the instrument on
        each staff, the cursor position, and the music itself.

        Args:
            format: "lilypond" (default) gives a readable summary plus LilyPond notation.
                "json" gives the full raw analysis (per-measure elements with ticks, voices,
                pitches, lyrics with verse and syllabic, dynamics/tempo/fermata markings).
            start_measure: First measure to include (1-based). Default: first measure.
            end_measure: Last measure to include (1-based, inclusive). Default: last measure.
        """
        res = await client.send_command("getScore")
        if not res.get("success") or "analysis" not in res:
            return res

        analysis = res["analysis"]
        if format == "json":
            if start_measure or end_measure:
                lo, hi = start_measure or 1, end_measure or analysis.get("numMeasures", 0)
                analysis["measures"] = [m for m in analysis.get("measures", []) if lo <= m.get("measure", 0) <= hi]
            return {"analysis": analysis, "cursor": res.get("cursor")}

        header = format_score_header(analysis, res.get("cursor"))
        lily = json_to_lilypond(analysis, start_measure, end_measure)
        return f"{header}\n[Score]\n{lily}"
