from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hfe_character_tool.assets import build_static_preview, import_assets, scan_asset_folder
from hfe_character_tool.export import export_project
from hfe_character_tool.logging_report import EventLog, developer_log, user_summary
from hfe_character_tool.models import InternalCharacter, Severity
from hfe_character_tool.patching import PatchArtifacts
from hfe_character_tool.projects import create_project
from hfe_character_tool.target_cache import TargetCacheEntry
from hfe_character_tool.tools import (
    ToolConfig,
    check_tools,
    dependency_summary,
    wrap_with_projector,
)


def test_scan_asset_folder_identifies_png_and_non_png(tmp_path: Path) -> None:
    (tmp_path / "0.png").write_bytes(b"png")
    (tmp_path / "readme.txt").write_text("note", encoding="utf-8")

    assets, issues = scan_asset_folder(tmp_path)

    assert [asset.file_name for asset in assets] == ["0.png"]
    assert len(issues) == 1
    assert issues[0].severity is Severity.WARNING
    assert "忽略非 PNG 文件" in issues[0].message


def test_import_assets_updates_project_and_preview(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    create_project(projects_root, "asset_hero", "lucas-basic", "asset_hero")
    project_dir = projects_root / "asset_hero"
    source = tmp_path / "source_assets"
    source.mkdir()
    for name in ("0.png", "1.png", "2.png"):
        (source / name).write_bytes(b"fake-png")

    assets, issues = import_assets(project_dir, source)
    preview = build_static_preview(project_dir)

    assert [asset.file_name for asset in assets] == ["0.png", "1.png", "2.png"]
    assert not [issue for issue in issues if issue.severity is Severity.ERROR]
    assert (project_dir / "assets" / "0.png").is_file()
    assert preview.status == "static"
    assert preview.primary_asset.endswith("0.png")


def test_tool_checks_and_projector_wrapping_rule(tmp_path: Path) -> None:
    projector = tmp_path / "SA.exe"
    swf = tmp_path / "patched.swf"
    out = tmp_path / "custom.exe"
    projector.write_bytes(b"PROJECTOR")
    swf.write_bytes(b"SWF_BYTES")

    wrap_with_projector(projector, swf, out)
    data = out.read_bytes()

    assert data == b"PROJECTORSWF_BYTES" + bytes.fromhex("56 34 12 FA") + (9).to_bytes(4, "little")


def test_missing_tool_summary_is_user_readable(tmp_path: Path) -> None:
    config = ToolConfig(
        ffdec=tmp_path / "missing_ffdec.jar",
        hfworkshop=tmp_path / "missing_hfworkshop.exe",
        projector=tmp_path / "missing_sa.exe",
        playerglobal=tmp_path / "missing_playerglobal.swc",
    )

    missing = check_tools(config)

    assert set(missing) == {
        "FFDec",
        "HFWorkshop",
        "SA.exe",
        "playerglobal.swc",
        "java",
        "javac",
    }
    assert "缺少依赖" in dependency_summary(missing)


def test_user_summary_does_not_dump_large_technical_detail() -> None:
    log = EventLog()
    log.error("tools", "run", "外部工具执行失败。", "STDOUT: very long raw output")

    assert user_summary(log.events) == "外部工具执行失败。"
    assert "STDOUT" in developer_log(log.events)
    assert "STDOUT" not in user_summary(log.events)


def test_export_blocks_when_validation_has_errors(tmp_path: Path) -> None:
    workspace = tmp_path
    projects_root = workspace / "projects"
    create_project(projects_root, "blocked_hero", "lucas-basic", "blocked_hero")

    result = export_project(projects_root / "blocked_hero", workspace)

    assert result.status == "blocked"
    assert result.exe_path is None
    assert result.validation_report.has_errors
    assert result.log_path is not None
    assert result.validation_report_path is not None


def test_export_accepts_nested_output_directory(tmp_path: Path) -> None:
    workspace = tmp_path
    projects_root = workspace / "projects"
    create_project(projects_root, "nested_output_hero", "lucas-basic", "nested_output_hero")

    result = export_project(
        projects_root / "nested_output_hero",
        workspace,
        output_dir=workspace / "output" / "nested" / "step1",
    )

    assert result.status == "blocked"
    assert result.log_path is not None
    assert result.log_path.parent == workspace / "output" / "nested" / "step1"


def test_export_blocks_when_default_target_game_is_missing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path
    projects_root = workspace / "projects"
    create_project(projects_root, "export_hero", "lucas-basic", "codex")
    project_dir = projects_root / "export_hero"
    source = tmp_path / "source_assets"
    source.mkdir()
    for name in ("0.png", "1.png", "2.png"):
        (source / name).write_bytes(b"fake-png")
    import_assets(project_dir, source)
    _create_fake_vendor_tools(workspace)
    original_projector = (workspace / "vendor" / "projector" / "SA.exe").read_bytes()

    result = export_project(project_dir, workspace)

    assert result.status == "blocked"
    assert result.exe_path is None
    assert (workspace / "vendor" / "projector" / "SA.exe").read_bytes() == original_projector
    assert result.validation_report.has_errors
    assert any(
        issue.target == "target_game.source_path" for issue in result.validation_report.issues
    )


def test_export_uses_real_patching_when_original_game_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path
    projects_root = workspace / "projects"
    create_project(projects_root, "real_hero", "lucas-basic", "codex")
    project_dir = projects_root / "real_hero"
    source = tmp_path / "source_assets"
    source.mkdir()
    for name in ("0.png", "1.png", "2.png"):
        (source / name).write_bytes(b"fake-png")
    import_assets(project_dir, source)
    _create_fake_vendor_tools(workspace)
    original_game = workspace / "vendor" / "original_game" / "HFE v1.0.2.exe"
    original_game.parent.mkdir(parents=True)
    original_game.write_bytes(b"FAKE_ORIGINAL")
    cached_swf = workspace / "output" / "target_cache" / "abc123" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWScached")
    probe_json = cached_swf.parent / "probe.json"
    probe_json.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_build_hfe_artifacts(
        character: InternalCharacter,
        output_dir: Path,
        stem: str,
        tools: ToolConfig,
        use_real_patching: bool = False,
        allow_placeholder_swf: bool = True,
        target_swf_path: Path | None = None,
        target_source_kind: str = "exe",
        source_has_pow_metadata: bool | None = None,
        workspace: Path | None = None,
    ) -> PatchArtifacts:
        captured["character_id"] = character.character_id
        captured["use_real_patching"] = use_real_patching
        captured["allow_placeholder_swf"] = allow_placeholder_swf
        captured["original_game"] = tools.original_game
        captured["target_swf_path"] = target_swf_path
        captured["target_source_kind"] = target_source_kind
        captured["source_has_pow_metadata"] = source_has_pow_metadata
        captured["output_dir"] = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        plan_path = output_dir / f"{stem}_patch_plan.json"
        spt_path = output_dir / f"{stem}_spt.json"
        lmi_path = output_dir / f"{stem}_lmi.json"
        swf_path = output_dir / f"{stem}_patched.swf"
        exe_path = output_dir / f"{stem}.exe"
        captured["work_plan_path"] = plan_path
        captured["work_swf_path"] = swf_path
        captured["work_exe_path"] = exe_path
        for path in (plan_path, spt_path, lmi_path):
            path.write_text("{}", encoding="utf-8")
        swf_path.write_bytes(b"FWSreal")
        exe_path.write_bytes(b"EXEreal")
        return PatchArtifacts(
            plan_path,
            spt_path,
            lmi_path,
            swf_path,
            exe_path,
            diagnostics={
                "source_role_id": "appa",
                "character_id": "app0",
                "resource_id": "app0",
                "target_limb_prefix": "a0_",
                "spt_limb_name_replacements": 13,
                "lmi_limb_name_replacements": 14,
                "fallback_pt_template_uses": 0,
            },
        )

    def fake_prepare_target_cache(
        workspace_path: Path, *_args: object, **_kwargs: object
    ) -> TargetCacheEntry:
        return TargetCacheEntry(
            cache_id="abc123",
            source_path=original_game,
            source_kind="exe",
            cache_dir=cached_swf.parent,
            cached_swf_path=cached_swf,
            probe_json_path=probe_json,
            raw_probe=_valid_probe_json(),
        )

    monkeypatch.setattr("hfe_character_tool.export.build_hfe_artifacts", fake_build_hfe_artifacts)
    monkeypatch.setattr("hfe_character_tool.export.prepare_target_cache", fake_prepare_target_cache)

    result = export_project(project_dir, workspace)

    assert result.status == "success_playable_candidate"
    assert result.exe_path is not None
    assert result.exe_path.parent == workspace / "output"
    assert result.exe_path.read_bytes() == b"EXEreal"
    work_dir = captured["output_dir"]
    work_plan_path = captured["work_plan_path"]
    work_swf_path = captured["work_swf_path"]
    work_exe_path = captured["work_exe_path"]
    assert isinstance(work_dir, Path)
    assert isinstance(work_plan_path, Path)
    assert isinstance(work_swf_path, Path)
    assert isinstance(work_exe_path, Path)
    assert work_dir != workspace / "output"
    assert work_dir.parent == workspace / "output" / "_work"
    assert not work_plan_path.exists()
    assert not work_swf_path.exists()
    assert not work_exe_path.exists()
    assert not (workspace / "output" / f"{result.exe_path.stem}_patch_plan.json").exists()
    assert not (workspace / "output" / f"{result.exe_path.stem}_patched.swf").exists()
    assert result.diagnostics_path is None
    assert captured["character_id"] == "codex"
    assert captured["use_real_patching"] is True
    assert captured["original_game"] == original_game
    assert captured["target_swf_path"] == cached_swf
    assert captured["target_source_kind"] == "exe"
    assert captured["source_has_pow_metadata"] is True
    assert result.log_path is not None
    log_text = result.log_path.read_text(encoding="utf-8")
    assert "cache_id=abc123" in log_text
    assert "patching/diagnostics" in log_text
    assert "target_limb_prefix=a0_" in log_text
    assert "spt_limb_name_replacements=13" in log_text
    assert "fallback_pt_template_uses=0" in log_text
    assert "真实 HFE 可玩候选" in log_text
    assert "占位 SWF/EXE" not in log_text


def test_export_keeps_work_directory_as_diagnostics_when_real_patching_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path
    projects_root = workspace / "projects"
    create_project(projects_root, "broken_hero", "lucas-basic", "codex")
    project_dir = projects_root / "broken_hero"
    source = tmp_path / "source_assets"
    source.mkdir()
    for name in ("0.png", "1.png", "2.png"):
        (source / name).write_bytes(b"fake-png")
    import_assets(project_dir, source)
    _create_fake_vendor_tools(workspace)
    original_game = workspace / "vendor" / "original_game" / "HFE v1.0.2.exe"
    original_game.parent.mkdir(parents=True)
    original_game.write_bytes(b"FAKE_ORIGINAL")
    cached_swf = workspace / "output" / "target_cache" / "abc123" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWScached")
    probe_json = cached_swf.parent / "probe.json"
    probe_json.write_text("{}", encoding="utf-8")

    def fake_build_hfe_artifacts(
        character: InternalCharacter,
        output_dir: Path,
        stem: str,
        tools: ToolConfig,
        use_real_patching: bool = False,
        allow_placeholder_swf: bool = True,
        target_swf_path: Path | None = None,
        target_source_kind: str = "exe",
        source_has_pow_metadata: bool | None = None,
        workspace: Path | None = None,
    ) -> PatchArtifacts:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{stem}_patch_plan.json").write_text("{}", encoding="utf-8")
        raise RuntimeError("boom")

    def fake_prepare_target_cache(
        workspace_path: Path, *_args: object, **_kwargs: object
    ) -> TargetCacheEntry:
        return TargetCacheEntry(
            cache_id="abc123",
            source_path=original_game,
            source_kind="exe",
            cache_dir=cached_swf.parent,
            cached_swf_path=cached_swf,
            probe_json_path=probe_json,
            raw_probe=_valid_probe_json(),
        )

    monkeypatch.setattr("hfe_character_tool.export.build_hfe_artifacts", fake_build_hfe_artifacts)
    monkeypatch.setattr("hfe_character_tool.export.prepare_target_cache", fake_prepare_target_cache)

    result = export_project(project_dir, workspace)

    assert result.status == "failed"
    assert result.exe_path is None
    assert result.diagnostics_path is not None
    assert result.diagnostics_path.parent == workspace / "output" / "_work"
    assert any(result.diagnostics_path.glob("*_patch_plan.json"))


def _create_fake_vendor_tools(workspace: Path) -> None:
    files = (
        workspace / "vendor" / "FFDec" / "ffdec.jar",
        workspace / "vendor" / "HFWorkshop" / "HFWorkshop.exe",
        workspace / "vendor" / "projector" / "SA.exe",
        workspace / "vendor" / "playerGlobal" / "playerglobal.swc",
        workspace / "runtime" / "jdk" / "bin" / _java_exe_name("java"),
        workspace / "runtime" / "jdk" / "bin" / _java_exe_name("javac"),
    )
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"FAKE_TOOL")


def _java_exe_name(tool: str) -> str:
    return f"{tool}.exe" if sys.platform == "win32" else tool


def _valid_probe_json() -> dict[str, object]:
    return {
        "classes": {"Data.Global": True},
        "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
        "multiname_constants": {"LoadFromCompressedBytes": True},
        "abc_data_global_classes": ["Data.Global_lucasSpt"],
        "missing_symbol_abc_classes": [],
        "probe_schema_version": 4,
        "global_pow_character_ids": ["lucas"],
        "global_char_list_order": ["lucas"],
        "select_char_options": [{"id": "lucas", "name": "Lucas", "value": 0}],
        "data_global_symbols": [
            {"id": 371, "name": "Data.Global_lucasLmi", "binary_size": 10},
            {"id": 442, "name": "Data.Global_lucasSpt", "binary_size": 10},
        ],
    }
