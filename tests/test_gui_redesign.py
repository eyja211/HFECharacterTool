from __future__ import annotations

from pathlib import Path

import pytest

from hfe_character_tool.gui_presenter import (
    character_id_hint,
    character_id_problem,
    compress_delays,
    export_status_display,
    format_elapsed,
    project_list_rows,
    quick_add_preview,
    suggest_character_id,
    target_display_name,
    validation_chip,
)
from hfe_character_tool.hfworkshop_catalog import SptActionInfo
from hfe_character_tool.models import (
    DEFAULT_TARGET_GAME,
    CharacterProject,
    FrameItemEdit,
    ItemSpawnSlot,
    ProjectSummary,
    Severity,
    TargetGame,
    ValidationIssue,
    ValidationReport,
    replace_project,
)
from hfe_character_tool.projects import (
    TRASH_DIR_NAME,
    ProjectError,
    create_project,
    duplicate_project,
    load_project,
    move_project_to_trash,
    scan_projects,
)
from hfe_character_tool.target_cache import TargetCacheEntry
from hfe_character_tool.templates import initial_project
from hfe_character_tool.validation import (
    non_game_text_characters,
    validate_against_target,
    validate_editing,
)


def _summary(name: str, cid: str, updated: str, status: str = "未导出") -> ProjectSummary:
    return ProjectSummary(
        path=Path(name),
        project_name=name,
        character_id=cid,
        character_name=name.upper(),
        updated_at=updated,
        last_export_status=status,
    )


def test_compress_delays_collapses_runs_of_three_or_more() -> None:
    assert compress_delays((1, 2, 3, 5, 6, 30)) == "1–3、5、6、30"
    assert compress_delays((30, 60, 90)) == "30、60、90"
    assert compress_delays(()) == ""


def test_quick_add_preview_reports_result_or_error() -> None:
    assert quick_add_preview("3", "30", "30") == "将添加延迟 30、60、90 帧的 3 个残影。"
    assert "1-90" in quick_add_preview("5", "88", "1")
    assert "整数" in quick_add_preview("x", "1", "1")


def test_project_list_rows_sorts_recent_first_and_filters() -> None:
    summaries = (
        _summary("Old", "old00", "2026-01-01T00:00:00Z"),
        _summary("New", "new00", "2026-09-01T00:00:00Z", "success_playable_candidate"),
        _summary("Mid", "mid00", "2026-05-01T00:00:00Z", "blocked"),
    )
    rows = project_list_rows(summaries)
    assert [row.title for row in rows] == ["New", "Mid", "Old"]
    assert rows[0].subtitle == "new00 · 已导出"
    assert [row.status_kind for row in rows] == ["success", "warning", "neutral"]
    assert [row.title for row in project_list_rows(summaries, "MID0")] == ["Mid"]
    assert project_list_rows(summaries, "nothing") == ()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success_playable_candidate", ("已导出", "success")),
        ("blocked", ("导出被阻断", "warning")),
        ("failed", ("导出失败", "danger")),
        ("未导出", ("未导出", "neutral")),
        ("", ("未导出", "neutral")),
    ],
)
def test_export_status_display(status: str, expected: tuple[str, str]) -> None:
    assert export_status_display(status) == expected


def test_character_id_problem_matches_export_rules() -> None:
    assert character_id_problem("eyj02", "lucas") == ""
    assert character_id_problem("", "lucas") == "请填写角色 ID。"
    assert "小写字母开头" in character_id_problem("Eyj02", "lucas")
    assert "重复" in character_id_problem("lucas", "lucas")
    assert "重复" in character_id_problem("drew", "drew")
    assert "需要 4 个字符" in character_id_problem("dre00", "drew")
    assert character_id_problem("dre0", "drew") == ""
    assert "4 个字符" in character_id_hint("drew")


def test_target_display_name() -> None:
    assert target_display_name(DEFAULT_TARGET_GAME) == "HFE v1.0.2（内置原版）"
    custom = TargetGame(
        id="t",
        source_path=r"D:\games\HF_Eyja_0.1.8.exe",
        source_kind="exe",
        detected_version="",
        cache_dir="",
    )
    assert target_display_name(custom) == "HF_Eyja_0.1.8.exe"


def test_validation_chip_prefers_errors_then_warnings() -> None:
    error = ValidationIssue(Severity.ERROR, "e", "", "character.id")
    warning = ValidationIssue(Severity.WARNING, "w", "", "character.name")
    assert validation_chip(ValidationReport()) == ("校验通过", "success")
    assert validation_chip(ValidationReport((warning,))) == ("1 个警告", "warning")
    assert validation_chip(ValidationReport((warning, error))) == ("1 个错误", "danger")


def test_format_elapsed() -> None:
    assert format_elapsed(0) == "00:00"
    assert format_elapsed(125.7) == "02:05"



# --- domain checks surfaced while editing ---------------------------------------------


def _cache(tmp_path: Path, symbols: tuple[str, ...], pow_ids: tuple[str, ...]) -> TargetCacheEntry:
    return TargetCacheEntry(
        cache_id="fake",
        source_path=tmp_path / "target.swf",
        source_kind="swf",
        cache_dir=tmp_path,
        cached_swf_path=tmp_path / "target.swf",
        probe_json_path=tmp_path / "probe.json",
        raw_probe={
            "data_global_symbols": [
                {"id": index, "name": name, "binary_size": 10}
                for index, name in enumerate(symbols)
            ],
            "global_pow_character_ids": list(pow_ids),
        },
    )


def _project(**changes: object) -> CharacterProject:
    return replace_project(initial_project("P", "lucas-basic", "eyj02"), **changes)


def test_game_text_check_lists_characters_big5_cannot_encode() -> None:
    assert non_game_text_characters("艾雅法拉 Eyja") == ""
    assert non_game_text_characters("残影们残") == "残们"
    report = validate_editing(_project(character_name_zh="残影少女", description="叛叛的主人"))
    issues = [issue for issue in report.issues if issue.target == "character.name_zh"]
    assert len(issues) == 1 and issues[0].severity is Severity.WARNING
    assert "残" in issues[0].message and "英文名" in issues[0].suggestion
    assert not [issue for issue in report.issues if issue.target == "character.description"]


def test_validate_against_target_reports_target_specific_problems(tmp_path: Path) -> None:
    cache = _cache(
        tmp_path,
        ("Data.Global_lucasSpt", "Data.Global_lucasLmi", "Data.Global_eyj02Spt"),
        ("lucas", "eyj02"),
    )
    actions = (SptActionInfo("ball", 0, 15, (8,)),)
    project = _project(
        item_frame_edits=(
            FrameItemEdit("ball", 8, (ItemSpawnSlot(item_action_group=5),)),
            FrameItemEdit("ball", 20, (ItemSpawnSlot(item_action_group=999),)),
        )
    )
    issues = validate_against_target(project, cache, actions, frozenset({5}))
    targets = {issue.target for issue in issues}
    assert "character.id" in targets  # eyj02 is already registered in this game
    assert "item_frame_edits.1.action_frame" in targets  # ball has only 15 frames
    assert "item_frame_edits.1.slots.0.item_action_group" in targets  # 999 not in itemSpt
    assert "item_frame_edits.0.slots.0.item_action_group" not in targets
    assert all(issue.message.isascii() is False for issue in issues)
    assert validate_against_target(project, cache, actions, None) == tuple(
        issue for issue in issues if "slots" not in issue.target
    )


def test_suggest_character_id_keeps_length_and_skips_used() -> None:
    assert suggest_character_id("eyj02", {"eyj01", "eyj02"}) == "eyj03"
    assert suggest_character_id("eyja", set()) == "eyj1"
    assert suggest_character_id("dre0", {"dre1"}) == "dre2"


def test_duplicate_and_trash_project(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    create_project(root, "Orig", "lucas-basic", "eyj02")
    (root / "Orig" / "assets" / "a.png").write_bytes(b"png")
    copy_dir = duplicate_project(root, root / "Orig", "Copy", "eyj03")
    copied = load_project(copy_dir)
    assert (copied.project_name, copied.character_id, copied.exports) == ("Copy", "eyj03", ())
    assert (copy_dir / "assets" / "a.png").is_file() and (copy_dir / "exports").is_dir()
    with pytest.raises(ProjectError):
        duplicate_project(root, root / "Orig", "Copy", "eyj04")
    moved = move_project_to_trash(root, root / "Orig")
    assert not (root / "Orig").exists() and (moved / "character.json").is_file()
    assert moved.parent.name == TRASH_DIR_NAME
    assert [summary.project_name for summary in scan_projects(root)] == ["Copy"]
