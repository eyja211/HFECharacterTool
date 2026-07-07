from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_select_char_panel_patch import (
    SELECT_CHAR_PANEL_PATCH_JAVA_SOURCE,
    write_select_char_panel_patch_source,
)


def test_select_char_panel_patch_source_pushes_codex_into_char_option(
    tmp_path: Path,
) -> None:
    path = write_select_char_panel_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == SELECT_CHAR_PANEL_PATCH_JAVA_SOURCE
    assert "Web_misc.SelectCharPanel" in source
    assert '"charOption"' in source
    assert "findStaticTraitQName" in source
    assert "findSourceOptionValue" in source
    assert "findNextCharOptionValue" in source
    assert "optionValue == -2" in source
    assert "used_fallback_option_value" in source
    assert 'isPushString(target.abc, code.code.get(i), "id")' in source
    assert "findCharOptionSetPropertyIndex" not in source
    assert "findPushedIntAfter" in source
    assert "GetLex, charOptionQName" in source
    assert 'addPair(injected, c, "value", optionValue)' in source
    assert 'addPair(injected, c, "name", displayName)' in source
    assert 'addPair(injected, c, "id", characterId)' in source
    assert "CallPropVoid, c.qPush, 1" in source
    assert "originalInitScopeDepth" in source
    assert "select_char_panel_option" in source
    assert "source_character_id" in source
    assert "already_present" in source
    assert "swf.saveTo" in source
