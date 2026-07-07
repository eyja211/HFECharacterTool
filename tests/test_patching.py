from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from hfe_character_tool.models import AssetEntry, InternalCharacter, replace_project
from hfe_character_tool.patching import (
    NEW_CHARACTER_INDEX,
    NEW_SELECT_OPTION_VALUE,
    _game_safe_b5_text,
    build_hfe_artifacts,
    build_patch_plan,
    generate_bytearray_source,
    generate_global_init_pcode,
    generate_loader_pcode,
    patch_plan_to_dict,
)
from hfe_character_tool.projects import create_project, save_project, update_assets
from hfe_character_tool.templates import to_internal_character
from hfe_character_tool.tools import ToolConfig, ToolResult


def test_bytearray_source_contains_embed_and_extends_bytearray() -> None:
    source = generate_bytearray_source("Data.Global_demoSpt", "demo_spt.json")

    assert "package Data" in source
    assert "extends ByteArray" in source
    assert '[Embed(source="demo_spt.json"' in source
    assert "writeBytes(bytes)" in source


def test_patch_plan_contains_bytearray_symbol_and_global_steps(tmp_path: Path) -> None:
    character = _internal_character(tmp_path)

    plan = build_patch_plan(character)
    data = patch_plan_to_dict(plan)

    assert plan.spt_class == "Data.Global_plan_heroSpt"
    assert plan.lmi_class == "Data.Global_plan_heroLmi"
    assert [item.class_name for item in plan.bytearray_classes] == [
        "Data.Global_plan_heroSpt",
        "Data.Global_plan_heroLmi",
    ]
    assert data["global_id_entries"] == ["plan_hero"]
    assert 'pushstring "plan_hero"' in plan.global_init_pcode
    assert 'pushstring "spt_data/"' in plan.spt_loader_pcode
    assert 'pushstring "lmi_data/"' in plan.lmi_loader_pcode
    assert "FFDec/P-code" in plan.notes[1]


def test_global_pcode_generation_uses_character_id_and_loader_offsets() -> None:
    init = generate_global_init_pcode("demo")
    spt_loader = generate_loader_pcode("spt", 351, "Data.Global_demoSpt")
    lmi_loader = generate_loader_pcode("lmi", 121, "Data.Global_demoLmi")

    assert 'QName(PackageNamespace(""),"sptIds")' in init
    assert 'Global_demoSpt' in init
    assert "pushshort 351" in spt_loader
    assert "class hint: Data.Global_demoSpt" in spt_loader
    assert "pushshort 121" in lmi_loader
    assert "class hint: Data.Global_demoLmi" in lmi_loader


def test_game_safe_b5_text_falls_back_when_text_cannot_encode_cp950() -> None:
    assert _game_safe_b5_text("測試", "Fallback") == "測試"
    assert _game_safe_b5_text("测试", "Fallback") == "Fallback"
    assert _game_safe_b5_text("", "Fallback") == "Fallback"


def test_build_hfe_artifacts_requires_original_game_for_real_patching(tmp_path: Path) -> None:
    character = _internal_character(tmp_path, character_id="codex")
    config = _fake_tools(tmp_path)

    with pytest.raises(RuntimeError, match="真实 SWF 补丁需要"):
        build_hfe_artifacts(
            character,
            tmp_path / "output",
            "plan_hero",
            config,
            use_real_patching=True,
            allow_placeholder_swf=False,
        )


def test_build_hfe_artifacts_writes_diagnostic_plan_and_placeholder_exe(
    tmp_path: Path,
) -> None:
    character = _internal_character(tmp_path)
    config = _fake_tools(tmp_path)
    base_swf = b"FWS" + b"\x01" * 5
    original_game = tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe"
    original_game.parent.mkdir(parents=True)
    original_game.write_bytes(
        b"PROJECTOR" + base_swf + bytes.fromhex("56 34 12 FA") + len(base_swf).to_bytes(4, "little")
    )

    artifacts = build_hfe_artifacts(character, tmp_path / "output", "plan_hero", config)

    plan_data = json.loads(artifacts.plan_path.read_text(encoding="utf-8"))
    assert plan_data["bytearray_classes"][0]["source"]
    assert artifacts.spt_path.is_file()
    assert artifacts.lmi_path.is_file()
    assert artifacts.patched_swf_path.read_bytes().startswith(b"FWS")
    assert artifacts.base_swf_path is not None
    assert artifacts.base_swf_path.read_bytes() == base_swf
    assert artifacts.exe_path.read_bytes().endswith(
        bytes.fromhex("56 34 12 FA")
        + artifacts.patched_swf_path.stat().st_size.to_bytes(4, "little")
    )


def test_build_hfe_artifacts_creates_nested_output_parent_dirs(tmp_path: Path) -> None:
    character = _internal_character(tmp_path)
    config = _fake_tools(tmp_path)

    artifacts = build_hfe_artifacts(
        character,
        tmp_path / "output" / "_work" / "run1",
        "run1",
        config,
    )

    assert artifacts.exe_path.is_file()


def test_build_hfe_artifacts_real_patching_runs_verified_stage_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    character = _internal_character(tmp_path, character_id="codex")
    config = _fake_tools(tmp_path)
    original_game = tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe"
    original_game.parent.mkdir(parents=True)
    original_game.write_bytes(b"PROJECTOR")
    calls: list[str] = []
    texture_replacements = {"Lucas_00Head": "raye_00Head"}

    def fake_extract_projector_swf(exe_path: Path, swf_path: Path) -> None:
        calls.append("extract")
        assert exe_path == original_game
        swf_path.write_bytes(b"FWSbase")

    def fake_stage(name: str) -> Callable[..., SimpleNamespace]:
        def stage(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append(name)
            output_swf = args[1]
            assert isinstance(output_swf, Path)
            output_swf.write_bytes(f"FWS{name}".encode("ascii"))
            if name == "binary":
                assert kwargs["target_character_id"] == "codex"
                assert kwargs["texture_replacements"] == texture_replacements
                assert kwargs["source_character_id"] == "lucas"
            if name == "metadata":
                assert kwargs["character_index"] == NEW_CHARACTER_INDEX
                assert kwargs["source_character_id"] == "lucas"
            if name == "vsselect":
                assert kwargs["option_value"] == NEW_SELECT_OPTION_VALUE
                assert kwargs["source_character_id"] == "lucas"
            if name == "expbind":
                assert kwargs["bound_character_id"] == "lucas"
            stdout = (
                json.dumps(
                    {
                        "source_character_id": "lucas",
                        "target_character_id": "codex",
                        "target_limb_prefix": "codex_",
                        "spt_limb_name_replacements": 22,
                        "lmi_limb_name_replacements": 19,
                        "frame_item_edits": 1,
                        "generated_cre_slots": 3,
                        "fallback_pt_template_uses": 0,
                    }
                )
                if name == "binary"
                else ""
            )
            return _success_stage_result(stdout)

        return stage

    def fake_wrap_with_projector(projector_exe: Path, swf_path: Path, output_exe: Path) -> None:
        calls.append("wrap")
        assert projector_exe == config.projector
        assert swf_path.name == "codex_plan_patched.swf"
        output_exe.write_bytes(b"EXE")

    monkeypatch.setattr(
        "hfe_character_tool.patching.extract_projector_swf", fake_extract_projector_swf
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.limb_name_replacements",
        lambda *_args: texture_replacements,
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching._extract_source_spt_payload",
        lambda output_dir, *_args: _empty_payload(output_dir),
    )
    monkeypatch.setattr("hfe_character_tool.patching.clone_binary_symbols", fake_stage("binary"))
    monkeypatch.setattr(
        "hfe_character_tool.patching.add_bytearray_asset_classes", fake_stage("abc")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_single_registry_entry", fake_stage("registry")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_loadall_custom_arrays", fake_stage("loader")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_global_character_metadata", fake_stage("metadata")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_select_char_panel_options", fake_stage("vsselect")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_experience_binding", fake_stage("expbind")
    )
    monkeypatch.setattr("hfe_character_tool.patching.wrap_with_projector", fake_wrap_with_projector)

    artifacts = build_hfe_artifacts(
        character,
        tmp_path / "output",
        "codex_plan",
        config,
        use_real_patching=True,
    )

    assert calls == [
        "extract",
        "binary",
        "abc",
        "registry",
        "loader",
        "metadata",
        "vsselect",
        "expbind",
        "wrap",
    ]
    assert artifacts.patched_swf_path.name == "codex_plan_patched.swf"
    assert artifacts.exe_path.read_bytes() == b"EXE"
    assert artifacts.diagnostics is not None
    assert artifacts.diagnostics["source_role_id"] == "lucas"
    assert artifacts.diagnostics["character_id"] == "codex"
    assert artifacts.diagnostics["resource_id"] == "codex"
    assert artifacts.diagnostics["target_limb_prefix"] == "codex_"
    assert artifacts.diagnostics["spt_limb_name_replacements"] == 22
    assert artifacts.diagnostics["lmi_limb_name_replacements"] == 19
    assert artifacts.diagnostics["fallback_pt_template_uses"] == 0


def test_build_hfe_artifacts_uses_source_metadata_for_non_lucas_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    character = _internal_character(
        tmp_path,
        character_id="ray0",
        source_role_id="raye",
        character_name="Raye模板测试",
        character_name_zh="Raye模板测试",
        description="Raye模板测试",
    )
    config = _fake_tools(tmp_path)
    original_game = tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe"
    original_game.parent.mkdir(parents=True)
    original_game.write_bytes(b"PROJECTOR")
    calls: list[str] = []

    def fake_extract_projector_swf(_exe_path: Path, swf_path: Path) -> None:
        calls.append("extract")
        swf_path.write_bytes(b"FWSbase")

    def fake_stage(name: str) -> Callable[..., SimpleNamespace]:
        def stage(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append(name)
            output_swf = args[1]
            assert isinstance(output_swf, Path)
            output_swf.write_bytes(f"FWS{name}".encode("ascii"))
            if name == "binary":
                assert kwargs["source_character_id"] == "raye"
            if name == "metadata":
                assert args[5] == "ray0"
                assert args[6] == "ray0"
                assert kwargs["character_index"] == NEW_CHARACTER_INDEX
                assert kwargs["description"] == "ray0"
                assert kwargs["source_character_id"] == "raye"
            if name == "vsselect":
                assert args[5] == "ray0"
                assert kwargs["option_value"] == NEW_SELECT_OPTION_VALUE
                assert kwargs["source_character_id"] == "raye"
            if name == "expbind":
                assert kwargs["bound_character_id"] == "raye"
            return _success_stage_result()

        return stage

    def fake_wrap_with_projector(_projector_exe: Path, _swf_path: Path, output_exe: Path) -> None:
        calls.append("wrap")
        output_exe.write_bytes(b"EXE")

    monkeypatch.setattr(
        "hfe_character_tool.patching.extract_projector_swf", fake_extract_projector_swf
    )
    monkeypatch.setattr("hfe_character_tool.patching.limb_name_replacements", lambda *_args: {})
    monkeypatch.setattr(
        "hfe_character_tool.patching._extract_source_spt_payload",
        lambda output_dir, *_args: _empty_payload(output_dir),
    )
    monkeypatch.setattr("hfe_character_tool.patching.clone_binary_symbols", fake_stage("binary"))
    monkeypatch.setattr(
        "hfe_character_tool.patching.add_bytearray_asset_classes", fake_stage("abc")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_single_registry_entry", fake_stage("registry")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_loadall_custom_arrays", fake_stage("loader")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_global_character_metadata", fake_stage("metadata")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_select_char_panel_options", fake_stage("vsselect")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_experience_binding", fake_stage("expbind")
    )
    monkeypatch.setattr("hfe_character_tool.patching.wrap_with_projector", fake_wrap_with_projector)

    artifacts = build_hfe_artifacts(
        character,
        tmp_path / "output",
        "ray0_plan",
        config,
        use_real_patching=True,
        source_has_pow_metadata=True,
    )

    assert calls == [
        "extract",
        "binary",
        "abc",
        "registry",
        "loader",
        "metadata",
        "vsselect",
        "expbind",
        "wrap",
    ]
    assert artifacts.exe_path.read_bytes() == b"EXE"


def test_build_hfe_artifacts_uses_hidden_list_metadata_for_spt_only_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    character = _internal_character(
        tmp_path,
        character_id="z_woman00",
        source_role_id="z_woman01",
    )
    config = _fake_tools(tmp_path)
    original_game = tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe"
    original_game.parent.mkdir(parents=True)
    original_game.write_bytes(b"PROJECTOR")
    calls: list[str] = []

    def fake_extract_projector_swf(_exe_path: Path, swf_path: Path) -> None:
        calls.append("extract")
        swf_path.write_bytes(b"FWSbase")

    def fake_stage(name: str) -> Callable[..., SimpleNamespace]:
        def stage(*args: object, **kwargs: object) -> SimpleNamespace:
            calls.append(name)
            output_swf = args[1]
            assert isinstance(output_swf, Path)
            output_swf.write_bytes(f"FWS{name}".encode("ascii"))
            if name == "metadata":
                assert kwargs["character_index"] == NEW_CHARACTER_INDEX
                assert kwargs["source_character_id"] == "z_woman01"
                assert kwargs["fallback_character_index"] == -1
                assert kwargs["add_to_char_list"] is False
            if name == "vsselect":
                assert kwargs["option_value"] == NEW_SELECT_OPTION_VALUE
                assert kwargs["source_character_id"] == "z_woman01"
            if name == "expbind":
                assert kwargs["bound_character_id"] == "lucas"
            return _success_stage_result()

        return stage

    def fake_wrap_with_projector(_projector_exe: Path, _swf_path: Path, output_exe: Path) -> None:
        calls.append("wrap")
        output_exe.write_bytes(b"EXE")

    monkeypatch.setattr(
        "hfe_character_tool.patching.extract_projector_swf", fake_extract_projector_swf
    )
    monkeypatch.setattr("hfe_character_tool.patching.limb_name_replacements", lambda *_args: {})
    monkeypatch.setattr(
        "hfe_character_tool.patching._extract_source_spt_payload",
        lambda output_dir, *_args: _empty_payload(output_dir),
    )
    monkeypatch.setattr("hfe_character_tool.patching.clone_binary_symbols", fake_stage("binary"))
    monkeypatch.setattr(
        "hfe_character_tool.patching.add_bytearray_asset_classes", fake_stage("abc")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_single_registry_entry", fake_stage("registry")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_loadall_custom_arrays", fake_stage("loader")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_global_character_metadata", fake_stage("metadata")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_select_char_panel_options", fake_stage("vsselect")
    )
    monkeypatch.setattr(
        "hfe_character_tool.patching.patch_experience_binding", fake_stage("expbind")
    )
    monkeypatch.setattr("hfe_character_tool.patching.wrap_with_projector", fake_wrap_with_projector)

    artifacts = build_hfe_artifacts(
        character,
        tmp_path / "output",
        "z_woman00_plan",
        config,
        use_real_patching=True,
        source_has_pow_metadata=False,
    )

    assert calls == [
        "extract",
        "binary",
        "abc",
        "registry",
        "loader",
        "metadata",
        "vsselect",
        "expbind",
        "wrap",
    ]
    assert artifacts.exe_path.read_bytes() == b"EXE"
    assert artifacts.diagnostics is not None
    assert artifacts.diagnostics["source_has_pow_metadata"] is False
    assert artifacts.diagnostics["add_to_char_list"] is False
    assert artifacts.diagnostics["fallback_character_index"] == -1
    assert artifacts.diagnostics["experience_bound_character_id"] == "lucas"


def test_build_hfe_artifacts_uses_cached_swf_for_swf_target_without_wrapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    character = _internal_character(tmp_path, character_id="codex")
    config = _fake_tools(tmp_path)
    cached_swf = tmp_path / "cache" / "target.swf"
    cached_swf.parent.mkdir()
    cached_swf.write_bytes(b"FWScached")

    def fail_extract(*_args: object) -> None:
        raise AssertionError("cached target SWF should not be extracted from original_game")

    def fail_wrap(*_args: object) -> None:
        raise AssertionError("SWF targets should return patched SWF without projector wrapping")

    def fake_generate_patched_swf(
        output_dir: Path,
        stem: str,
        *_args: object,
        **_kwargs: object,
    ) -> Path:
        path = output_dir / f"{stem}_patched.swf"
        path.write_bytes(b"FWSpatched")
        return path

    monkeypatch.setattr("hfe_character_tool.patching.extract_projector_swf", fail_extract)
    monkeypatch.setattr("hfe_character_tool.patching.wrap_with_projector", fail_wrap)
    monkeypatch.setattr(
        "hfe_character_tool.patching.generate_patched_swf", fake_generate_patched_swf
    )

    artifacts = build_hfe_artifacts(
        character,
        tmp_path / "output",
        "codex_plan",
        config,
        use_real_patching=True,
        target_swf_path=cached_swf,
        target_source_kind="swf",
    )

    assert artifacts.base_swf_path is not None
    assert artifacts.base_swf_path.read_bytes() == b"FWScached"
    assert artifacts.exe_path == artifacts.patched_swf_path
    assert artifacts.exe_path.read_bytes() == b"FWSpatched"


def _internal_character(
    tmp_path: Path,
    character_id: str = "plan_hero",
    source_role_id: str = "lucas",
    character_name: str | None = None,
    character_name_zh: str | None = None,
    description: str | None = None,
) -> InternalCharacter:
    projects_root = tmp_path / "projects"
    create_project(
        projects_root,
        "plan_hero",
        "lucas-basic",
        character_id,
        source_role_id=source_role_id,
    )
    project = update_assets(
        projects_root / "plan_hero",
        (
            AssetEntry("0.png", "0", "valid"),
            AssetEntry("1.png", "1", "valid"),
            AssetEntry("2.png", "2", "valid"),
        ),
    )
    if character_name is not None or character_name_zh is not None or description is not None:
        project = replace_project(
            project,
            **{
                key: value
                for key, value in {
                    "character_name": character_name,
                    "character_name_zh": character_name_zh,
                    "description": description,
                }.items()
                if value is not None
            },
        )
        save_project(projects_root / "plan_hero", project)
    return to_internal_character(project)


def _success_stage_result(stdout: str = "") -> SimpleNamespace:
    result = ToolResult(True, 0, stdout, "")
    return SimpleNamespace(compile_result=result, run_result=result)


def _empty_payload(output_dir: Path) -> Path:
    path = output_dir / "source.payload.bin"
    path.write_bytes(b"")
    return path


def _fake_tools(tmp_path: Path) -> ToolConfig:
    projector = tmp_path / "vendor" / "projector" / "SA.exe"
    projector.parent.mkdir(parents=True)
    projector.write_bytes(b"PROJECTOR")
    return ToolConfig(
        ffdec=tmp_path / "vendor" / "FFDec" / "ffdec.jar",
        hfworkshop=tmp_path / "vendor" / "HFWorkshop" / "HFWorkshop.exe",
        projector=projector,
        playerglobal=tmp_path / "vendor" / "playerGlobal" / "playerglobal.swc",
        original_game=tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe",
    )
