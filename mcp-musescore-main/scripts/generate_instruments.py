"""Builds src/data/instruments.json from MuseScore's instrument list.

    python scripts/generate_instruments.py path/to/MuseScore/share/instruments/instruments.xml

The file shipped here was made from MuseScore v4.7.5
(share/instruments/instruments.xml). For each instrument: its id (what
add_instrument / set_instrument_sound take), name, group, number of staves,
clefs, transposition and playable ranges.

Pitch ranges in instruments.xml are concert (sounding) MIDI pitches:
"amateur" is a comfortable range, "professional" the full one.
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "data" / "instruments.json"

# The fields that an <init> reference passes on
FIELDS = ("family", "trackName", "longName", "description", "trait", "staves", "clef", "clef2", "concertClef", "transposingClef",
          "aPitchRange", "pPitchRange", "transposeChromatic", "transposeDiatonic", "genres", "drumset", "tablature", "drums")


def read_instrument(el):
    d = {}
    drums = []
    for drum in el.findall("Drum"):
        name = (drum.findtext("name") or "").strip()
        if name:
            voice = drum.findtext("voice")
            drums.append([int(drum.get("pitch")), name, int(voice) if voice and voice.strip().isdigit() else 0])
    if drums:
        d["drums"] = sorted(drums)
    for child in el:
        tag, text = child.tag, (child.text or "").strip()
        if tag == "clef":
            d["clef2" if child.get("staff") == "2" else "clef"] = text
        elif tag == "genre":
            d.setdefault("genres", []).append(text)
        elif tag == "drumset":
            d["drumset"] = text == "1"
        elif tag == "traitName" and child.get("type") == "transposition":
            d["trait"] = text
        elif tag == "stafftype":
            d["tablature"] = text == "tablature"
        elif tag in FIELDS:
            d[tag] = text
    return d


def midi_range(text):
    if not text or "-" not in text:
        return None
    lo, hi = text.split("-", 1)
    return [int(lo), int(hi)]


def main(xml_path):
    root = ET.parse(xml_path).getroot()
    raw, order = {}, []
    for group in root.iter("InstrumentGroup"):
        group_name = group.findtext("name") or group.get("id")
        for el in group.iter("Instrument"):
            d = read_instrument(el)
            d["group"] = group_name
            init = el.findtext("init")
            raw[el.get("id")] = (d, init.strip() if init else None)
            order.append(el.get("id"))

    def resolved(iid, seen=()):
        d, init = raw[iid]
        if not init or init not in raw or init in seen:
            return dict(d)
        base = resolved(init, seen + (iid,))
        base.update(d)
        return base

    out = []
    for iid in order:
        d = resolved(iid)
        clef = d.get("concertClef") or d.get("clef") or "G"
        name = d.get("longName") or d.get("trackName") or iid.replace("-", " ").title()
        if d.get("trait") and d["trait"] not in name:
            name += " in " + d["trait"]
        item = {
            "id": iid,
            "name": name,
            "group": d["group"],
            "family": d.get("family", ""),
            "staves": int(d.get("staves", "1")),
            "clef": clef,
        }
        if d.get("trackName") and d["trackName"] not in name:
            item["trackName"] = d["trackName"]
        if d.get("description"):
            item["description"] = d["description"]
        if d.get("clef2"):
            item["clef2"] = d["clef2"]
        if d.get("transposingClef") and d.get("transposingClef") != clef:
            item["writtenClef"] = d["transposingClef"]
        chrom = int(d.get("transposeChromatic", "0") or 0)
        if chrom:
            item["transposition"] = chrom   # sounding pitch = written pitch + transposition (Bb clarinet: -2)
        for key, name in (("aPitchRange", "range"), ("pPitchRange", "fullRange")):
            r = midi_range(d.get(key))
            if r:
                item[name] = r
        if d.get("genres"):
            item["genres"] = d["genres"]
        if d.get("drumset"):
            item["unpitched"] = True
        if d.get("drums"):
            item["drums"] = d["drums"]      # [MIDI pitch, drum name, voice MuseScore uses (0-3)]
        if d.get("tablature"):
            item["tablature"] = True
        out.append(item)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "MuseScore v4.7.5 share/instruments/instruments.xml", "instruments": out},
                              ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(out)} instruments -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "instruments.xml")
