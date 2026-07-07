from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_roundtrip import ROUNDTRIP_JAVA_SOURCE, write_roundtrip_source


def test_roundtrip_source_loads_and_saves_swf(tmp_path: Path) -> None:
    path = write_roundtrip_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == ROUNDTRIP_JAVA_SOURCE
    assert "new SWF" in source
    assert "swf.saveTo" in source
    assert "FileOutputStream" in source

