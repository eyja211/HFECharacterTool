from __future__ import annotations

import json
from pathlib import Path

import pytest

from hfe_character_tool.hfworkshop_catalog import TexturePart, TextureRole
from hfe_character_tool.models import (
    AssetEntry,
    FrameItemEdit,
    ItemSpawnSlot,
    Severity,
    TargetGame,
    replace_project,
)
from hfe_character_tool.projects import (
    check_project_structure,
    create_project,
    load_project,
    save_project,
    scan_projects,
    update_assets,
)
from hfe_character_tool.target_cache import TargetCacheEntry
from hfe_character_tool.templates import get_template, to_internal_character
from hfe_character_tool.validation import can_export, validate_editing, validate_for_export


def test_create_load_save_and_scan_project(tmp_path: Path) -> None:
    project = create_project(tmp_path, "hero_one", "lucas-basic", "hero_one")
    project_dir = tmp_path / "hero_one"

    assert (project_dir / "character.json").is_file()
    assert (project_dir / "assets").is_dir()
    assert (project_dir / "preview").is_dir()
    assert (project_dir / "exports").is_dir()
    assert check_project_structure(project_dir) == []

    loaded = load_project(project_dir)
    assert loaded.project_name == project.project_name
    assert loaded.created_at

    updated = replace_project(loaded, character_name="测试角色")
    save_project(project_dir, updated)
    assert load_project(project_dir).character_name == "测试角色"

    summaries = scan_projects(tmp_path)
    assert len(summaries) == 1
    assert summaries[0].project_name == "hero_one"
    assert summaries[0].last_export_status == "未导出"


def test_project_json_uses_expected_top_level_sections(tmp_path: Path) -> None:
    create_project(tmp_path, "hero_two", "lucas-basic", "hero_two")
    raw = json.loads((tmp_path / "hero_two" / "character.json").read_text(encoding="utf-8"))

    assert set(raw) == {
        "project",
        "template",
        "character",
        "stats",
        "skills",
        "item_frame_edits",
        "textures",
        "target_game",
        "assets",
        "exports",
    }
    assert raw["template"] == {
        "id": "lucas-basic",
        "version": "1.0",
        "source_role_id": "lucas",
    }
    assert {"hp", "mp", "defense"} <= set(raw["stats"])
    assert raw["item_frame_edits"] == []
    assert raw["target_game"]["source_kind"] == "exe"


def test_create_project_can_store_selected_target_game(tmp_path: Path) -> None:
    target = TargetGame(
        id="target-custom",
        source_path=str(tmp_path / "custom.swf"),
        source_kind="swf",
    )

    create_project(tmp_path, "hero_target", "lucas-basic", "eyja0", target_game=target)

    project = load_project(tmp_path / "hero_target")
    assert project.target_game.source_path == target.source_path
    assert project.target_game.source_kind == "swf"


def test_create_project_can_store_selected_source_role(tmp_path: Path) -> None:
    create_project(
        tmp_path,
        "hero_raye",
        "lucas-basic",
        "ray0",
        source_role_id="raye",
    )

    project = load_project(tmp_path / "hero_raye")
    internal = to_internal_character(project)
    assert project.source_role_id == "raye"
    assert internal.source_role_id == "raye"


def test_old_project_json_migrates_to_builtin_target(tmp_path: Path) -> None:
    create_project(tmp_path, "hero_old", "lucas-basic", "eyja0")
    path = tmp_path / "hero_old" / "character.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("target_game")
    path.write_text(json.dumps(raw), encoding="utf-8")

    project = load_project(tmp_path / "hero_old")

    assert project.target_game.source_path == "vendor/original_game/HFE v1.0.2.exe"
    assert project.target_game.source_kind == "exe"
    assert project.source_role_id == "lucas"


def test_template_conversion_produces_read_only_generation_model(tmp_path: Path) -> None:
    create_project(tmp_path, "hero_three", "lucas-basic", "hero_three")
    project_dir = tmp_path / "hero_three"
    project = update_assets(
        project_dir,
        (
            AssetEntry("0.png", "0", "valid"),
            AssetEntry("1.png", "1", "valid"),
            AssetEntry("2.png", "2", "valid"),
        ),
    )

    internal = to_internal_character(project)

    assert internal.template == get_template("lucas-basic")
    assert internal.character_id == "hero_three"
    assert internal.stats["hp"] == 500
    assert internal.skills[0].skill_id == "rising_slash"
    assert [asset.file_name for asset in internal.assets] == ["0.png", "1.png", "2.png"]


def test_default_project_can_export_without_png_assets(tmp_path: Path) -> None:
    _write_default_target(tmp_path)
    create_project(tmp_path, "hero_four", "lucas-basic", "eyja0")
    project_dir = tmp_path / "hero_four"
    project = load_project(project_dir)

    editing_report = validate_editing(project)
    export_report = validate_for_export(project_dir, project)

    assert not editing_report.has_errors
    assert can_export(export_report)


def test_lucas_template_warns_then_blocks_non_five_byte_id(tmp_path: Path) -> None:
    create_project(tmp_path, "hero_short", "lucas-basic", "eyja")
    project_dir = tmp_path / "hero_short"
    project = load_project(project_dir)

    editing_report = validate_editing(project)
    export_report = validate_for_export(project_dir, project)

    assert not editing_report.has_errors
    assert any(issue.target == "character.id" for issue in editing_report.issues)
    assert not can_export(export_report)


def test_source_role_length_rule_follows_selected_template_role(tmp_path: Path) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(
        projects_root,
        "hero_raye",
        "lucas-basic",
        "ray0",
        source_role_id="raye",
    )
    project_dir = projects_root / "hero_raye"
    project = load_project(project_dir)

    assert can_export(validate_for_export(project_dir, project))

    blocked = replace_project(project, character_id="raye0")
    report = validate_for_export(project_dir, blocked)

    assert any(issue.target == "character.id" for issue in report.issues)


def test_validation_checks_id_stats_and_skill_ranges(tmp_path: Path) -> None:
    create_project(tmp_path, "hero_five", "lucas-basic", "hero_five")
    project_dir = tmp_path / "hero_five"
    project = replace_project(
        load_project(project_dir),
        character_id="Lucas",
        character_name="",
        stats={"hp": 0, "mp": 500, "defense": -1},
        skills={
            "rising_slash": {
                "key": "RAW_SPT_EDIT",
                "mp_cost": 999,
                "damage": 35,
                "speed": 10,
                "range": 80,
            }
        },
    )

    report = validate_editing(project)
    messages = [issue.message for issue in report.issues]

    assert "角色 ID 格式不正确。" in messages
    assert "角色名称不能为空。" in messages
    assert "HP 必须在 1 到 9999 之间。" in messages
    assert "技能按键不在安全列表中。" in messages
    assert any(issue.target == "skills.rising_slash.mp_cost" for issue in report.issues)


def test_export_validation_rejects_item_action_group_missing_from_item_spt(
    tmp_path: Path,
) -> None:
    _write_default_target(tmp_path)
    item_spt = tmp_path / "vendor" / "HFWorkshop" / "185 - Data.Global_itemSpt" / "Spt.json"
    item_spt.parent.mkdir(parents=True)
    item_spt.write_text(
        json.dumps(
            {
                "Data.Spt": {
                    "actionGroup": {
                        "HFW_ArrayLenXXX": 1,
                        "5": {"name": "lucasB"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    projects_root = tmp_path / "projects"
    create_project(projects_root, "hero_six", "lucas-basic", "eyja")
    project_dir = projects_root / "hero_six"
    project = replace_project(
        load_project(project_dir),
        item_frame_edits=(
            FrameItemEdit(
                action_name="ball",
                action_frame=8,
                slots=(ItemSpawnSlot(item_action_group=999),),
            ),
        ),
    )

    cached_swf = tmp_path / "output" / "target_cache" / "builtin" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWS")
    target_cache = TargetCacheEntry(
        cache_id="builtin",
        source_path=tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe",
        source_kind="exe",
        cache_dir=cached_swf.parent,
        cached_swf_path=cached_swf,
        probe_json_path=cached_swf.parent / "probe.json",
        raw_probe={
            "classes": {"Data.Global": True},
            "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
            "multiname_constants": {"LoadFromCompressedBytes": True},
            "data_global_symbols": [
                {"id": 1, "name": "Data.Global_lucasSpt", "binary_size": 10},
                {"id": 2, "name": "Data.Global_lucasLmi", "binary_size": 10},
                {"id": 3, "name": "Data.Global_itemSpt", "binary_size": 10},
            ],
        },
    )

    report = validate_for_export(project_dir, project, target_cache=target_cache)

    assert any(
        issue.target == "item_frame_edits.0.slots.0.item_action_group"
        for issue in report.issues
    )


def test_export_validation_defers_item_catalog_check_until_target_cache_exists(
    tmp_path: Path,
) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(
        projects_root,
        "hero_hfep_item",
        "lucas-basic",
        "app0",
        source_role_id="appa",
    )
    project_dir = projects_root / "hero_hfep_item"
    project = replace_project(
        load_project(project_dir),
        item_frame_edits=(
            FrameItemEdit(
                action_name="attack1",
                action_frame=1,
                slots=(ItemSpawnSlot(item_action_group=582),),
            ),
        ),
    )

    report = validate_for_export(project_dir, project)

    assert not any(
        issue.target == "item_frame_edits.0.slots.0.item_action_group"
        for issue in report.issues
    )


def test_export_validation_allows_item_slot_count_changes(tmp_path: Path) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(projects_root, "hero_empty_frame", "lucas-basic", "eyja0")
    project_dir = projects_root / "hero_empty_frame"
    project = replace_project(
        load_project(project_dir),
        item_frame_edits=(
            FrameItemEdit(
                action_name="ball",
                action_frame=7,
                slots=(ItemSpawnSlot(item_action_group=5),),
            ),
        ),
    )

    report = validate_for_export(project_dir, project)

    assert can_export(report)
    assert not any(issue.target == "item_frame_edits.0.slots" for issue in report.issues)


def test_export_validation_warns_on_texture_limb_name_length_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(projects_root, "hero_texture", "lucas-basic", "eyja0")
    project_dir = projects_root / "hero_texture"
    project = replace_project(
        load_project(project_dir),
        texture_selections={"head": "raye"},
    )
    monkeypatch.setattr(
        "hfe_character_tool.validation.load_texture_parts",
        lambda _workspace: (TexturePart("head", "head", ()),),
    )
    monkeypatch.setattr(
        "hfe_character_tool.validation.load_texture_roles",
        lambda _workspace: (TextureRole("lucas", "Lucas"), TextureRole("raye", "Raye")),
    )
    monkeypatch.setattr(
        "hfe_character_tool.validation.limb_name_replacements",
        lambda *_args: {"Lucas_00Head": "raye_00Head"},
    )

    report = validate_for_export(project_dir, project)

    assert can_export(report)
    assert any(
        issue.target == "textures" and issue.severity is Severity.WARNING
        for issue in report.issues
    )


def test_export_validation_blocks_missing_target_source(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    create_project(projects_root, "hero_missing_target", "lucas-basic", "eyja0")
    project_dir = projects_root / "hero_missing_target"
    project = load_project(project_dir)

    report = validate_for_export(project_dir, project)

    assert any(issue.target == "target_game.source_path" for issue in report.issues)


def test_export_validation_blocks_unsupported_target_probe(tmp_path: Path) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(projects_root, "hero_bad_probe", "lucas-basic", "eyja0")
    project_dir = projects_root / "hero_bad_probe"
    project = load_project(project_dir)
    cached_swf = tmp_path / "output" / "target_cache" / "bad" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWSbad")
    target_cache = TargetCacheEntry(
        cache_id="bad",
        source_path=tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe",
        source_kind="exe",
        cache_dir=cached_swf.parent,
        cached_swf_path=cached_swf,
        probe_json_path=cached_swf.parent / "probe.json",
        raw_probe={
            "classes": {"Data.Global": False},
            "string_constants": {},
            "multiname_constants": {},
        },
    )

    report = validate_for_export(project_dir, project, target_cache=target_cache)

    assert any(issue.target == "target_game.probe.Data.Global" for issue in report.issues)
    assert any(issue.target == "target_game.probe.loaders" for issue in report.issues)


def test_export_validation_blocks_unsafe_runtime_custom_loader_target(tmp_path: Path) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(projects_root, "hero_bad_runtime_loader", "lucas-basic", "eyja0")
    project_dir = projects_root / "hero_bad_runtime_loader"
    project = load_project(project_dir)
    cached_swf = tmp_path / "output" / "target_cache" / "runtime_bad" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWS")
    target_cache = TargetCacheEntry(
        cache_id="runtime_bad",
        source_path=tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe",
        source_kind="exe",
        cache_dir=cached_swf.parent,
        cached_swf_path=cached_swf,
        probe_json_path=cached_swf.parent / "probe.json",
        raw_probe={
            "classes": {"Data.Global": True},
            "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
            "multiname_constants": {"LoadFromCompressedBytes": True},
            "custom_loader_reports": [
                {
                    "kind": "custom_lmi",
                    "style": "runtime_loop",
                    "has_loop_progress": False,
                },
                {
                    "kind": "custom_spt",
                    "style": "runtime_loop",
                    "has_loop_progress": True,
                },
            ],
            "data_global_symbols": [
                {"id": 1, "name": "Data.Global_lucasSpt", "binary_size": 10},
                {"id": 2, "name": "Data.Global_lucasLmi", "binary_size": 10},
            ],
        },
    )

    report = validate_for_export(project_dir, project, target_cache=target_cache)

    issue = next(
        issue for issue in report.issues if issue.target == "target_game.probe.custom_loader"
    )
    assert issue.severity is Severity.ERROR
    assert "运行时循环加载器" in issue.message
    assert "会闪退" in issue.suggestion
    assert "custom_spt" in issue.technical_detail
    assert not can_export(report)


def test_export_validation_blocks_source_role_missing_from_target_cache(tmp_path: Path) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(
        projects_root,
        "hero_raye_missing",
        "lucas-basic",
        "ray0",
        source_role_id="raye",
    )
    project_dir = projects_root / "hero_raye_missing"
    project = load_project(project_dir)
    cached_swf = tmp_path / "output" / "target_cache" / "target" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWS")
    target_cache = TargetCacheEntry(
        cache_id="target",
        source_path=tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe",
        source_kind="exe",
        cache_dir=cached_swf.parent,
        cached_swf_path=cached_swf,
        probe_json_path=cached_swf.parent / "probe.json",
        raw_probe={
            "classes": {"Data.Global": True},
            "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
            "multiname_constants": {"LoadFromCompressedBytes": True},
            "data_global_symbols": [
                {"id": 1, "name": "Data.Global_lucasSpt", "binary_size": 10},
                {"id": 2, "name": "Data.Global_lucasLmi", "binary_size": 10},
            ],
        },
    )

    report = validate_for_export(project_dir, project, target_cache=target_cache)

    assert any(issue.target == "template.source_role_id" for issue in report.issues)


def test_export_validation_warns_for_source_role_without_pow_metadata(tmp_path: Path) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(
        projects_root,
        "hero_npc_template",
        "lucas-basic",
        "z_woman00",
        source_role_id="z_woman01",
    )
    project_dir = projects_root / "hero_npc_template"
    project = load_project(project_dir)
    cached_swf = tmp_path / "output" / "target_cache" / "target" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWS")
    target_cache = TargetCacheEntry(
        cache_id="target",
        source_path=tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe",
        source_kind="exe",
        cache_dir=cached_swf.parent,
        cached_swf_path=cached_swf,
        probe_json_path=cached_swf.parent / "probe.json",
        raw_probe={
            "classes": {"Data.Global": True},
            "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
            "multiname_constants": {"LoadFromCompressedBytes": True},
            "probe_schema_version": 5,
            "global_pow_character_ids": ["lucas"],
            "data_global_symbols": [
                {"id": 1, "name": "Data.Global_z_woman01Spt", "binary_size": 10},
                {"id": 2, "name": "Data.Global_z_woman01Lmi", "binary_size": 10},
                {"id": 3, "name": "Data.Global_itemSpt", "binary_size": 10},
            ],
        },
    )

    report = validate_for_export(project_dir, project, target_cache=target_cache)

    issue = next(issue for issue in report.issues if issue.target == "template.source_role_id")
    assert issue.severity is Severity.WARNING
    assert "小兵/NPC" in issue.message
    assert "Global.pow" in issue.technical_detail
    assert not any(
        issue.severity is Severity.ERROR and issue.target == "template.source_role_id"
        for issue in report.issues
    )


def test_export_validation_blocks_character_id_already_present_in_target(
    tmp_path: Path,
) -> None:
    _write_default_target(tmp_path)
    projects_root = tmp_path / "projects"
    create_project(projects_root, "duplicate_hero", "lucas-basic", "eyja0")
    project_dir = projects_root / "duplicate_hero"
    project = load_project(project_dir)
    cached_swf = tmp_path / "output" / "target_cache" / "target" / "target.swf"
    cached_swf.parent.mkdir(parents=True)
    cached_swf.write_bytes(b"FWS")
    target_cache = TargetCacheEntry(
        cache_id="target",
        source_path=tmp_path / "vendor" / "original_game" / "HFE v1.0.2.exe",
        source_kind="exe",
        cache_dir=cached_swf.parent,
        cached_swf_path=cached_swf,
        probe_json_path=cached_swf.parent / "probe.json",
        raw_probe={
            "classes": {"Data.Global": True},
            "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
            "multiname_constants": {"LoadFromCompressedBytes": True},
            "probe_schema_version": 5,
            "global_pow_character_ids": ["lucas", "eyja0"],
            "global_char_list_order": ["lucas", "eyja0"],
            "select_char_options": [{"id": "eyja0", "name": "Eyja", "value": 20}],
            "data_global_symbols": [
                {"id": 1, "name": "Data.Global_lucasSpt", "binary_size": 10},
                {"id": 2, "name": "Data.Global_lucasLmi", "binary_size": 10},
                {"id": 470, "name": "Data.Global_eyja0Spt", "binary_size": 10},
                {"id": 471, "name": "Data.Global_eyja0Lmi", "binary_size": 10},
            ],
        },
    )

    report = validate_for_export(project_dir, project, target_cache=target_cache)

    issue = next(issue for issue in report.issues if issue.target == "character.id")
    assert issue.severity is Severity.ERROR
    assert "eyja0" in issue.technical_detail


def _write_default_target(workspace: Path) -> None:
    target = workspace / "vendor" / "original_game" / "HFE v1.0.2.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"placeholder target")
