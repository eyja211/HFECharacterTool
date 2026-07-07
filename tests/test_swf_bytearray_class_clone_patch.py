from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_bytearray_class_clone_patch import (
    BYTEARRAY_CLASS_CLONE_PATCH_JAVA_SOURCE,
    write_bytearray_class_clone_patch_source,
)


def test_bytearray_class_clone_patch_source_clones_existing_class_shape(
    tmp_path: Path,
) -> None:
    path = write_bytearray_class_clone_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == BYTEARRAY_CLASS_CLONE_PATCH_JAVA_SOURCE
    assert "sourceInstance.instance_traits.clone()" in source
    assert "sourceClassInfo.static_traits.clone()" in source
    assert "abc.addClass" in source
    assert "updateScriptTrait" in source
    assert "bytearray_asset_class_clone" in source
    assert "swf.saveTo" in source
