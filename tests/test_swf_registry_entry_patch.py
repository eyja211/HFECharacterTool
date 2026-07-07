from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_registry_entry_patch import (
    REGISTRY_ENTRY_PATCH_JAVA_SOURCE,
    write_registry_entry_patch_source,
)


def test_registry_entry_patch_source_appends_to_existing_registration_arrays(
    tmp_path: Path,
) -> None:
    path = write_registry_entry_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == REGISTRY_ENTRY_PATCH_JAVA_SOURCE
    assert "Data.Global" in source
    assert "sptIds" in source
    assert "sptClasses" in source
    assert "lmiIds" in source
    assert "lmiClasses" in source
    assert "PushString" in source
    assert "FindPropertyStrict" in source
    assert "ConstructProp" in source
    assert "ensureStaticArraySlots" in source
    assert "RegistrySlots" in source
    assert "CallPropVoid" in source
    assert "qPush" in source
    assert "appendIdArray" in source
    assert "appendClassArray" in source
    assert "initIdArray" in source
    assert "initClassArray" in source
    assert "TraitSlotConst" in source
    assert "originalInitScopeDepth" in source
    assert "[lmiClass]" in source
    assert "initEmptyArray" in source
    assert "qualifiedClassQName" in source
    assert "single_registry_entry" in source
    assert "swf.saveTo" in source
