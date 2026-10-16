from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from pytest import MonkeyPatch

from hfe_character_tool.swf_story_mode_patch import (
    STORY15_PATCH_JAVA_SOURCE,
    StoryModePatchResult,
    build_story15_storylist_xml,
    patch_story_mode_to_15,
    story_body_xml,
    write_story15_patch_source,
)
from hfe_character_tool.tools import ToolConfig, ToolResult


def test_story15_storylist_contains_exact_fifteen_story_ids() -> None:
    root = ET.fromstring(build_story15_storylist_xml())
    story_ids = [node.findtext("storyid") for node in root.findall("story")]
    eighth_name = root.findall("story")[7].find("storyname/en")
    fifteenth_name = root.findall("story")[14].find("storyname/en")

    assert story_ids == [f"story{i:02d}" for i in range(1, 16)]
    assert eighth_name is not None
    assert eighth_name.text == "8. Training"
    assert fifteenth_name is not None
    assert fifteenth_name.text == "15. Custom Stage 15"


def test_story_body_template_sets_matching_story_id_and_minimal_bar() -> None:
    xml = story_body_xml("story15")
    root = ET.fromstring(xml)
    bgid = root.find("bar/bg/bgid")
    left = root.find("bar/left")
    right = root.find("bar/right")

    assert root.findtext("storyid") == "story15"
    assert root.findtext("storylevel") == "1"
    assert bgid is not None
    assert bgid.text == "hill_grass"
    assert left is not None
    assert left.text == "0"
    assert right is not None
    assert right.text == "4000"


def test_story_patch_source_registers_resources_and_loads_story15(
    tmp_path: Path,
) -> None:
    path = write_story15_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == STORY15_PATCH_JAVA_SOURCE
    assert "Data.Global_storylist_xml" in source
    assert '"story08"' in source
    assert '"story15"' in source
    assert '"Data.Global_" + storyId + "_xml"' in source
    assert "DefineBinaryDataTag" in source
    assert "SymbolClassTag" in source
    assert "ActionScript3Parser" in source
    assert "LoadStories" in source
    assert '"story15"' in source
    assert "getNextCharacterId" in source


def test_story_patch_source_adds_two_page_menu_offset() -> None:
    source = STORY15_PATCH_JAVA_SOURCE

    assert "storyMenuPageOffset" in source
    assert "patchRefreshLang" in source
    assert "patchRefreshButtons" in source
    assert "patchOnClickBut" in source
    assert "patchStoryUnlock" in source
    assert "patchGlobalStoryUnlock" in source
    assert "patchPageLabels" in source
    assert "patchPageNextButton" in source
    assert "patchPagePreviousButton" in source
    assert "HfeStoryPager" not in source
    assert "PAGE_OFFSET" in source
    assert "FIRST_PAGE_OFFSET" in source
    assert "SECOND_PAGE_OFFSET" in source
    assert "STORY_UNLOCK_MAX = 14" in source
    assert "qPageOffset" in source
    assert "qStoryChosenMax" in source
    assert "AVM2Instructions.Add" in source
    assert "storyChosen_max" in source
    assert "NEXT PAGE >" in source
    assert "< PREV PAGE" in source
    assert "Data.Global_story07_xml" in source
    assert "storyBodyFromStory07" in source
    assert "StandardCharsets.UTF_8" in source
    assert "PendingBranch.add(ifSecondPage, originalContinue)" in source


def test_patch_story_mode_to_15_wraps_with_source_projector(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    tools = ToolConfig(
        ffdec=tmp_path / "vendor" / "FFDec" / "ffdec.jar",
        hfworkshop=tmp_path / "vendor" / "HFWorkshop.exe",
        projector=tmp_path / "vendor" / "projector" / "SA.exe",
        playerglobal=tmp_path / "vendor" / "playerGlobal" / "playerglobal.swc",
    )
    input_swf = tmp_path / "input.swf"
    output_swf = tmp_path / "out" / "story15.swf"
    source_exe = tmp_path / "source.exe"
    output_exe = tmp_path / "out" / "story15.exe"
    input_swf.write_bytes(b"FWSinput")
    source_exe.write_bytes(b"PROJECTOR")
    calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_run_tool(command: tuple[str, ...], cwd: Path) -> ToolResult:
        calls.append(("run", command))
        if command[0] == "java":
            output_swf.parent.mkdir(parents=True, exist_ok=True)
            output_swf.write_bytes(b"FWSstory15")
        return ToolResult(True, 0, '{"success":true}', "")

    def fake_wrap(source: Path, swf: Path, exe: Path) -> None:
        calls.append(("wrap", (source, swf, exe)))
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"EXE")

    monkeypatch.setattr("hfe_character_tool.swf_story_mode_patch.run_tool", fake_run_tool)
    monkeypatch.setattr(
        "hfe_character_tool.swf_story_mode_patch.wrap_with_source_projector", fake_wrap
    )

    result = patch_story_mode_to_15(
        input_swf,
        output_swf,
        tools,
        tmp_path,
        output_exe=output_exe,
        source_exe=source_exe,
    )

    assert isinstance(result, StoryModePatchResult)
    assert output_swf.read_bytes() == b"FWSstory15"
    assert output_exe.read_bytes() == b"EXE"
    assert calls[-1] == ("wrap", (source_exe, output_swf, output_exe))


def _success_stage_result() -> SimpleNamespace:
    result = ToolResult(True, 0, "", "")
    return SimpleNamespace(compile_result=result, run_result=result)
