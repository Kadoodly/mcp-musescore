"""Checks on file paths before MuseScore gets them.

The server talks to the plugin on localhost, so it runs on the computer running MuseScore and sees
the same files. It checks here what MuseScore would otherwise ask about in a dialog: while a dialog
waits for the user, the plugin can't answer (MuseScore 4.7.5, ExportProjectScenario::doExportLoop
asks to retry when the file can't be written, and whether to replace existing page images;
opening a missing file shows an error).
"""

import re
from pathlib import Path
from typing import List

# Formats MuseScore writes in one go, without a progress dialog: format -> description
EXPORT_FORMATS = {
    "pdf": "PDF",
    "png": "PNG images, one per page",
    "svg": "SVG images, one per page",
    "mid": "MIDI", "midi": "MIDI",
    "musicxml": "MusicXML", "xml": "MusicXML", "mxl": "compressed MusicXML",
    "mei": "MEI",
    "mscz": "MuseScore file (a copy; the open score keeps its own file)",
    "mscx": "uncompressed MuseScore file",
}
# One file per page when the score has more than one (name-1.png, name-2.png, ...)
PER_PAGE_FORMATS = {"png", "svg"}
# Rendered through the audio engine with a progress dialog: not from a plugin request
AUDIO_VIDEO_FORMATS = {"mp3", "wav", "ogg", "flac", "aac", "mp4"}


def _absolute(path: str, what: str) -> Path:
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"{what} must be a file path")
    p = Path(path.strip()).expanduser()
    if not p.is_absolute():
        raise ValueError(f"{what} must be a full path (e.g. C:/Users/me/Music/song), not {path!r}")
    return p


def export_target(path: str, fmt: str, overwrite: bool = False) -> str:
    """The file MuseScore will write for export_score(path, fmt), after checking that it can
    write it without asking. Raises ValueError with the reason otherwise."""
    fmt = str(fmt).lower().lstrip(".")
    if fmt in AUDIO_VIDEO_FORMATS:
        raise ValueError(f"{fmt} can't be exported from the plugin (MuseScore renders audio and video with a "
                         f"progress dialog); ask the user to use File > Export in MuseScore")
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"unknown format {fmt!r} (use {', '.join(EXPORT_FORMATS)})")
    p = _absolute(path, "path")
    # EngravingPluginAPIHelper::writeScore adds "." + ext unless the name already ends with it
    target = p if str(p).endswith(fmt) else Path(str(p) + "." + fmt)
    folder = target.parent
    if not folder.is_dir():
        raise ValueError(f"the folder {str(folder)!r} doesn't exist")
    if target.is_dir():
        raise ValueError(f"{str(target)!r} is a folder")
    existing: List[Path] = [target] if target.exists() else []
    if fmt in PER_PAGE_FORMATS:
        # completeExportPath: <folder>/<name>-<page>.<ext>
        stem = target.name[:-len(fmt)].rstrip(".")
        pages = re.compile(re.escape(stem) + r"-\d+\." + re.escape(fmt) + "$")
        existing += sorted(f for f in folder.iterdir() if pages.match(f.name))
        if existing:
            # MuseScore asks about each existing page file: choose another name instead
            raise ValueError(f"{', '.join(repr(f.name) for f in existing[:3])}{' ...' if len(existing) > 3 else ''} "
                             f"already exist(s) in {str(folder)!r}; use another name (MuseScore would ask about each "
                             f"page file)")
    elif existing and not overwrite:
        raise ValueError(f"{str(target)!r} already exists; pass overwrite=true to replace it")
    return str(target)


def open_target(path: str) -> str:
    """The score file to open, checked to exist."""
    p = _absolute(path, "path")
    if not p.is_file():
        raise ValueError(f"no file at {str(p)!r}")
    return str(p)
