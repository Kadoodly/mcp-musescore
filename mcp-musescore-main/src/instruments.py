"""MuseScore's instrument list (src/data/instruments.json, made by
scripts/generate_instruments.py from MuseScore 4.7.5): ids for add_instrument,
clefs, transpositions and ranges."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from .notation import note_name

DATA = Path(__file__).resolve().parent / "data" / "instruments.json"


@lru_cache(maxsize=1)
def instruments() -> List[Dict[str, Any]]:
    return json.loads(DATA.read_text(encoding="utf-8"))["instruments"]


@lru_cache(maxsize=1)
def _by_id() -> Dict[str, Dict[str, Any]]:
    return {i["id"]: i for i in instruments()}


def find(instrument_id: Optional[str]) -> Optional[Dict[str, Any]]:
    return _by_id().get(instrument_id or "")


def range_text(inst: Dict[str, Any], full: bool = False) -> str:
    r = inst.get("fullRange" if full else "range")
    return f"{note_name(r[0])}-{note_name(r[1])}" if r else ""


def search(query: str = "", group: str = "", limit: int = 40) -> List[Dict[str, Any]]:
    """Instruments whose id, name, family or description contain every word of
    `query` (case-insensitive), common ones first."""
    words = [w for w in query.lower().split() if w]
    group = group.lower()
    hits = []
    for inst in instruments():
        if group and group not in inst["group"].lower():
            continue
        text = " ".join([inst["id"], inst["name"], inst.get("trackName", ""), inst.get("family", ""),
                         inst.get("description", ""), inst["group"]]).lower()
        if all(w in text for w in words):
            q = " ".join(words)
            rank = 0 if q == inst["id"] else 1 if q in (inst["name"].lower(), inst["id"].replace("-", " ")) else 2
            common = "common" in inst.get("genres", [])
            hits.append((rank, 0 if common else 1, inst))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[:limit]]


def describe(inst: Dict[str, Any]) -> str:
    """One line: id, name, staves/clefs, transposition, ranges."""
    parts = [f"{inst['id']}: {inst['name']}"]
    clefs = inst["clef"] + ("+" + inst["clef2"] if inst.get("clef2") else "")
    parts.append(f"{inst['staves']} staves ({clefs})" if inst["staves"] > 1 else f"clef {clefs}")
    if inst.get("transposition"):
        t = inst["transposition"]
        parts.append(f"sounds {abs(t)} semitone(s) {'lower' if t < 0 else 'higher'} than written")
    if inst.get("unpitched"):
        parts.append("unpitched (drum map: MIDI pitch = drum)")
    elif inst.get("range"):
        full = range_text(inst, True)
        parts.append(f"range {range_text(inst)}" + (f" (up to {full})" if full and full != range_text(inst) else ""))
    if inst.get("tablature"):
        parts.append("tablature")
    return ", ".join(parts) + f" [{inst['group']}]"
