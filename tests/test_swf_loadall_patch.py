from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_loadall_patch import (
    LOADALL_PATCH_JAVA_SOURCE,
    write_loadall_patch_source,
)


def test_loadall_patch_source_injects_custom_array_loops(tmp_path: Path) -> None:
    path = write_loadall_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == LOADALL_PATCH_JAVA_SOURCE
    assert "Data.Global" in source
    assert "LoadAllData" in source
    assert "sptIds" in source
    assert "sptClasses" in source
    assert "lmiIds" in source
    assert "lmiClasses" in source
    assert "alreadyLoadsCustomArrays" in source
    assert "hasCustomArrayLoadBeforeCall" in source
    assert "findMaxLoadOffset" in source
    assert "findStageJumpInsertAt" in source
    assert "originalInitScopeDepth" in source
    assert "originalMaxScopeDepth" in source
    assert "addExpandedLoaderCalls" in source
    assert "collectRegistryEntries" in source
    assert "RegistryEntry" in source
    assert "private static AVM2Instruction pushInt" in source
    assert 'getPublicQnameId("push", true)' in source
    assert "for (int index = 0; index < entries.size(); index++)" in source
    assert "custom_loader_style" in source
    assert "jumpLoop" not in source
    assert "AVM2Instructions.IncrementI" not in source
    assert "new PendingBranch(jumpLoop, loopStart)" not in source
    assert "hasLoopProgressAfterCall" in source
    assert "hasCustomArrayOperandsBeforeCall" in source
    assert "findStageJumpInsertAt(code, c, maxLmiOffset)" in source
    assert "int searchStop = Math.min(code.code.size(), i + 90)" in source
    assert "max_regs" in source
    assert "swf.saveTo" in source


def test_loadall_patch_matches_existing_custom_loaders_by_public_name() -> None:
    source = LOADALL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert "isInstructionWithPublicName" in source
    assert "customLoadCount(code,ref.abc,\"Spt\",\"sptIds\",\"sptClasses\",c)" in compact
    assert (
        "hasCustomArrayOperandsBeforeCall("
        "code,i,abc,targetLoaderName,idsName,classesName"
    ) in compact
    assert 'text.contains("\\"" + publicName + "\\"")' in source
    assert "intclassesIndex=callIndex-12;" in compact
    assert "intidsIndex=callIndex-6;" in compact
    assert "matchesIdArrayAppend(code,abc,i,arrayName)" in compact
    assert "matchesClassArrayAppend(code,abc,i,arrayName)" in compact
    assert "customLoadCount(code,c.qSptClass,c.qSptIds,c.qSptClasses,c)" not in compact
