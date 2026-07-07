from __future__ import annotations

from pathlib import Path

from hfe_character_tool.gui_presenter import (
    CharacterForm,
    ItemSlotForm,
    apply_form,
    disabled_item_label,
    frame_item_edits_from_drafts,
    item_slots_from_forms,
    next_project_defaults,
    project_rows,
    project_to_form,
    render_export_result,
    render_validation_report,
    selected_item_label,
    validation_groups,
)
from hfe_character_tool.hfworkshop_catalog import KEEP_SOURCE_TEXTURE_ROLE_ID, ItemOption
from hfe_character_tool.models import (
    ExportResult,
    ItemSpawnSlot,
    ProjectSummary,
    Severity,
    ValidationIssue,
    ValidationReport,
)
from hfe_character_tool.templates import initial_project


def test_project_rows_include_status_and_updated_time() -> None:
    rows = project_rows(
        (
            ProjectSummary(
                path=Path("hero"),
                project_name="hero",
                character_id="hero_id",
                character_name="英雄",
                updated_at="2026-05-27T00:00:00Z",
                last_export_status="未导出",
            ),
        )
    )

    assert rows[0].label == "hero / hero_id / 未导出"
    assert "最近修改" in rows[0].tooltip


def test_next_project_defaults_avoid_existing_name_and_id() -> None:
    existing = (
        ProjectSummary(
            path=Path("hero"),
            project_name="Eyja",
            character_id="eyja",
            character_name="Eyja",
            updated_at="",
            last_export_status="未导出",
        ),
        ProjectSummary(
            path=Path("hero2"),
            project_name="Eyja2",
            character_id="eyj2",
            character_name="Eyja2",
            updated_at="",
            last_export_status="未导出",
        ),
    )

    assert next_project_defaults(existing) == ("Eyja3", "eyj03")


def test_next_project_defaults_use_five_byte_ids_after_default_is_taken() -> None:
    existing = (
        ProjectSummary(
            path=Path("hero"),
            project_name="Eyja",
            character_id="eyja0",
            character_name="Eyja",
            updated_at="",
            last_export_status="未导出",
        ),
        ProjectSummary(
            path=Path("hero2"),
            project_name="Eyja2",
            character_id="eyj02",
            character_name="Eyja2",
            updated_at="",
            last_export_status="未导出",
        ),
    )

    assert next_project_defaults(existing) == ("Eyja3", "eyj03")


def test_project_to_form_and_apply_form_round_trip() -> None:
    project = initial_project("hero", "lucas-basic", "hero_id")
    form = project_to_form(project)

    updated = apply_form(
        project,
        CharacterForm(
            character_id=" new_id ",
            character_name=" 新角色 ",
            character_name_zh=" 艾雅 ",
            description=" 描述 ",
            hp="600",
            mp="bad-number",
            defense="100",
            skill_key="D>A",
            skill_mp_cost="25",
            skill_damage="40",
            skill_speed="12",
            skill_range="90",
            target_source_path="custom.swf",
            target_source_kind="swf",
            item_action_name="ball",
            item_action_frame="8",
            item_slots=(
                ItemSlotForm("74", "255", "-121", "2", "45", "0", "0"),
                ItemSlotForm("35", "260", "-121", "2", "50", "0", "0"),
                ItemSlotForm("", "255", "-121", "2", "45", "0", "0"),
            ),
            texture_selections={"head": "raye", "chest": KEEP_SOURCE_TEXTURE_ROLE_ID},
        ),
    )

    assert form.skill_key == "D>A"
    assert updated.character_id == "new_id"
    assert updated.character_name == "新角色"
    assert updated.stats["hp"] == 600
    assert updated.stats["mp"] == 0
    assert updated.stats["defense"] == 100
    assert updated.skills["rising_slash"]["range"] == 90
    assert updated.target_game.source_path == "custom.swf"
    assert updated.target_game.source_kind == "swf"
    assert updated.item_frame_edits[0].action_name == "ball"
    assert updated.item_frame_edits[0].action_frame == 8
    assert [slot.item_action_group for slot in updated.item_frame_edits[0].slots] == [74, 35]
    assert updated.item_frame_edits[0].slots[1].x == 260
    assert updated.texture_selections["head"] == "raye"
    assert "chest" not in updated.texture_selections


def test_frame_item_edits_from_drafts_keeps_multiple_frames() -> None:
    edits = frame_item_edits_from_drafts(
        {
            ("ball", 8): (ItemSpawnSlot(item_action_group=74),),
            ("ball", 9): (ItemSpawnSlot(item_action_group=35),),
        }
    )

    assert [(edit.action_name, edit.action_frame) for edit in edits] == [
        ("ball", 8),
        ("ball", 9),
    ]
    assert [edit.slots[0].item_action_group for edit in edits] == [74, 35]


def test_selected_item_label_distinguishes_empty_from_explicit_zero() -> None:
    options = (
        ItemOption(0, "rock", "0: rock"),
        ItemOption(35, "swordwind", "35: swordwind"),
    )

    assert selected_item_label("", options) == disabled_item_label()
    assert selected_item_label("0", options) == "0: rock"


def test_item_slots_from_forms_skips_empty_and_keeps_explicit_zero() -> None:
    slots = item_slots_from_forms(
        (
            ItemSlotForm("", "255", "-121", "2", "45", "0", "0"),
            ItemSlotForm("0", "255", "-121", "2", "45", "0", "0"),
            ItemSlotForm("35", "260", "-121", "2", "50", "0", "0"),
        )
    )

    assert [slot.item_action_group for slot in slots] == [0, 35]


def test_render_validation_report_groups_by_severity() -> None:
    report = ValidationReport(
        (
            ValidationIssue(Severity.INFO, "提示消息", "继续观察。", "assets"),
            ValidationIssue(Severity.ERROR, "错误消息", "请修复。", "character.id"),
            ValidationIssue(Severity.WARNING, "警告消息", "建议修复。", "stats.hp"),
        )
    )

    rendered = render_validation_report(report)

    assert rendered.index("[错误]") < rendered.index("[警告]") < rendered.index("[提示]")
    assert "定位：character.id" in rendered
    assert "修复：请修复。" in rendered


def test_validation_groups_keep_targets_for_click_to_focus() -> None:
    issue = ValidationIssue(Severity.ERROR, "错误消息", "请修复。", "stats.hp")
    report = ValidationReport((issue,))

    groups = validation_groups(report)

    assert groups[0].title == "错误"
    assert groups[0].issues == (issue,)
    assert groups[0].issues[0].target == "stats.hp"


def test_render_export_result_includes_logs_and_checklist() -> None:
    result = ExportResult(
        status="success_placeholder",
        summary="已完成但需要验证。",
        exe_path=Path("output/hero.exe"),
        validation_report=ValidationReport(),
        log_path=Path("output/hero_log.md"),
        validation_report_path=Path("output/hero_validation.md"),
        test_checklist=("进入 VS 模式。", "选择新角色。"),
    )

    rendered = render_export_result(result)

    assert "输出：output\\hero.exe" in rendered or "输出：output/hero.exe" in rendered
    assert "导出日志" in rendered
    assert "游戏内测试清单" in rendered
    assert "- 选择新角色。" in rendered
