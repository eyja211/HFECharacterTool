from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_bytearray_class_patch import (
    BYTEARRAY_CLASS_PATCH_JAVA_SOURCE,
    _ffdec_classpath,
    write_bytearray_class_patch_source,
)
from hfe_character_tool.tools import ToolConfig


def test_bytearray_class_patch_source_adds_real_abc_classes(tmp_path: Path) -> None:
    path = write_bytearray_class_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == BYTEARRAY_CLASS_PATCH_JAVA_SOURCE
    assert "ActionScript3Parser" in source
    assert "AbcIndexing" in source
    assert "parser.addScript" in source
    assert "mx.core.ByteArrayAsset" in source
    assert "findAbcContainingClass" in source
    assert "bytearray_asset_classes" in source
    assert "swf.saveTo" in source


def test_ffdec_classpath_includes_lib_when_present(tmp_path: Path) -> None:
    ffdec = tmp_path / "ffdec.jar"
    lib = tmp_path / "lib"
    ffdec.write_text("jar", encoding="utf-8")
    lib.mkdir()
    (lib / "ffdec_lib.jar").write_text("lib", encoding="utf-8")
    config = ToolConfig(
        ffdec=ffdec,
        hfworkshop=tmp_path / "HFWorkshop.exe",
        projector=tmp_path / "SA.exe",
        playerglobal=tmp_path / "playerglobal.swc",
    )

    assert _ffdec_classpath(config) == f"{ffdec};{lib / 'ffdec_lib.jar'}"
