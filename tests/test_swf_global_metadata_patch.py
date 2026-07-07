from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_global_metadata_patch import (
    GLOBAL_METADATA_PATCH_JAVA_SOURCE,
    write_global_metadata_patch_source,
)


def test_global_metadata_patch_source_adds_pow_char_list_and_lang_words(
    tmp_path: Path,
) -> None:
    path = write_global_metadata_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == GLOBAL_METADATA_PATCH_JAVA_SOURCE
    assert "charListOrder" in source
    assert "CallPropVoid, c.qPush, 1" in source
    assert "qPow" in source
    assert "qLangWords" in source
    assert "source_character_id" in source
    assert "findSourcePowIndex" in source
    assert "findNextPowIndex" in source
    assert "fallbackIndex" in source
    assert "used_fallback_index" in source
    assert "add_to_char_list" in source
    assert "if (addToCharList)" in source
    assert "isGetLex" in source
    assert "isPowReferenceNear" in source
    assert "isPowAccess" in source
    assert "isSetProperty" in source
    assert "findPowObjectEnd" in source
    assert "hasPowObjectFields" in source
    assert "readObjectPairInt" in source
    assert 'characterId + "_desc"' in source
    assert '"hp0"' in source
    assert '"mp0"' in source
    assert '"str0"' in source
    assert '"fake_hp"' in source
    assert "NewObject, 17" in source
    assert "originalInitScopeDepth" in source
    assert "global_character_metadata" in source
    assert "swf.saveTo" in source
