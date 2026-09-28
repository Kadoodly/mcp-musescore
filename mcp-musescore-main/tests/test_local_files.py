"""Path checks made before MuseScore sees a path (so it never has to ask in a dialog)."""

import pytest

from src.local_files import export_target, open_target


def test_export_adds_the_extension_and_needs_the_folder(tmp_path):
    assert export_target(str(tmp_path / "song"), "PDF") == str(tmp_path / "song.pdf")
    assert export_target(str(tmp_path / "song.mid"), ".mid") == str(tmp_path / "song.mid")
    with pytest.raises(ValueError, match="doesn't exist"):
        export_target(str(tmp_path / "missing" / "song"), "pdf")
    with pytest.raises(ValueError, match="full path"):
        export_target("song", "pdf")


def test_export_refuses_audio_and_unknown_formats(tmp_path):
    with pytest.raises(ValueError, match="File > Export"):
        export_target(str(tmp_path / "song"), "mp3")
    with pytest.raises(ValueError, match="unknown format"):
        export_target(str(tmp_path / "song"), "docx")


def test_existing_files(tmp_path):
    (tmp_path / "song.pdf").write_text("x")
    with pytest.raises(ValueError, match="overwrite=true"):
        export_target(str(tmp_path / "song"), "pdf")
    assert export_target(str(tmp_path / "song"), "pdf", overwrite=True) == str(tmp_path / "song.pdf")
    # page images: MuseScore would ask about each one, even with overwrite
    (tmp_path / "song-2.png").write_text("x")
    with pytest.raises(ValueError, match="use another name"):
        export_target(str(tmp_path / "song"), "png", overwrite=True)
    (tmp_path / "song-cover.png").write_text("x")      # not a page file
    assert export_target(str(tmp_path / "other"), "png") == str(tmp_path / "other.png")


def test_open_needs_an_existing_file(tmp_path):
    f = tmp_path / "a.mscz"
    with pytest.raises(ValueError, match="no file"):
        open_target(str(f))
    f.write_text("x")
    assert open_target(str(f)) == str(f)
