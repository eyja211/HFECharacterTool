from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_experience_binding_patch import (
    EXPERIENCE_BINDING_PATCH_JAVA_SOURCE,
    write_experience_binding_patch_source,
)


def test_experience_binding_patch_source_syncs_login_and_settlement(
    tmp_path: Path,
) -> None:
    path = write_experience_binding_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == EXPERIENCE_BINDING_PATCH_JAVA_SOURCE
    assert "Data.Global" in source
    assert "LoginStatusMessage" in source
    assert "Game.World" in source
    assert "GenLevelUpMsg" in source
    assert "pow" in source
    assert '"InitPow"' in source
    assert '"c"' in source
    assert "findInitPowCallInsertAt" in source
    assert "patchWorldSettlementBinding" in source
    assert "findEason0SettlementInsertionPoints" in source
    assert "indexForAddress" in source
    assert "getTargetAddress" in source
    assert "tempcharid" in source
    assert '"eason0"' in source
    assert "Expected 2 eason0 settlement branches" in source
    assert "buildTempCharBindingGuard" in source
    assert "AVM2Instructions.IfNe" in source
    assert "AVM2Instructions.InitProperty" in source
    assert "experience_binding" in source
    assert "settlement_branches_patched" in source
    assert "swf.saveTo" in source
