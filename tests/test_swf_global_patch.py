from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_global_patch import (
    GLOBAL_PATCH_JAVA_SOURCE,
    write_global_patch_source,
)


def test_global_patch_source_adds_empty_registry_arrays(tmp_path: Path) -> None:
    path = write_global_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == GLOBAL_PATCH_JAVA_SOURCE
    assert "Data.Global" in source
    assert "sptIds" in source
    assert "sptClasses" in source
    assert "lmiIds" in source
    assert "lmiClasses" in source
    assert "ensureStaticArraySlots" in source
    assert "TraitSlotConst" in source
    assert "originalInitScopeDepth" in source
    assert "NewArray, 0" in source
    assert "swf.saveTo" in source
