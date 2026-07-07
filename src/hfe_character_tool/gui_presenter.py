from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from hfe_character_tool.hfworkshop_catalog import (
    KEEP_SOURCE_TEXTURE_ROLE_ID,
    ItemOption,
    TexturePart,
    item_label,
)
from hfe_character_tool.models import (
    DEFAULT_ITEM_FRAME_EDIT,
    CharacterProject,
    ExportResult,
    FrameItemEdit,
    ItemSpawnSlot,
    PreviewResult,
    ProjectSummary,
    Severity,
    TargetGame,
    ValidationIssue,
    ValidationReport,
    replace_project,
)

DEFAULT_PROJECT_NAME = "Eyja"
DEFAULT_CHARACTER_ID = "eyja0"
DEFAULT_CHARACTER_NAME_ZH = "艾雅法拉"
MAX_ITEM_SLOTS = 3


@dataclass(frozen=True)
class ItemSlotForm:
    item_action_group: str
    x: str
    y: str
    z: str
    vx: str
    vy: str
    vz: str


@dataclass(frozen=True)
class ProjectRow:
    label: str
    tooltip: str


@dataclass(frozen=True)
class CharacterForm:
    character_id: str
    character_name: str
    character_name_zh: str
    description: str
    hp: str
    mp: str
    defense: str
    skill_key: str
    skill_mp_cost: str
    skill_damage: str
    skill_speed: str
    skill_range: str
    target_source_path: str
    target_source_kind: str
    item_action_name: str
    item_action_frame: str
    item_slots: tuple[ItemSlotForm, ...]
    texture_selections: dict[str, str]


@dataclass(frozen=True)
class ValidationGroup:
    title: str
    issues: tuple[ValidationIssue, ...]


def next_project_defaults(existing: tuple[ProjectSummary, ...]) -> tuple[str, str]:
    used_names = {summary.project_name for summary in existing}
    used_ids = {summary.character_id for summary in existing}
    if DEFAULT_PROJECT_NAME not in used_names and DEFAULT_CHARACTER_ID not in used_ids:
        return DEFAULT_PROJECT_NAME, DEFAULT_CHARACTER_ID
    index = 2
    while True:
        name = f"{DEFAULT_PROJECT_NAME}{index}"
        character_id = _numbered_character_id(index)
        if name not in used_names and character_id not in used_ids:
            return name, character_id
        index += 1


def project_rows(summaries: tuple[ProjectSummary, ...]) -> tuple[ProjectRow, ...]:
    rows: list[ProjectRow] = []
    for summary in summaries:
        label = (
            f"{summary.project_name} / {summary.character_id} / "
            f"{summary.last_export_status}"
        )
        tooltip = f"最近修改：{summary.updated_at or '未知'}"
        rows.append(ProjectRow(label, tooltip))
    return tuple(rows)


def project_to_form(project: CharacterProject) -> CharacterForm:
    skill = next(iter(project.skills.values()), {})
    item_edit = project.item_frame_edits[0] if project.item_frame_edits else DEFAULT_ITEM_FRAME_EDIT
    return CharacterForm(
        character_id=project.character_id,
        character_name=project.character_name,
        character_name_zh=project.character_name_zh,
        description=project.description,
        hp=str(project.stats.get("hp", "")),
        mp=str(project.stats.get("mp", "")),
        defense=str(project.stats.get("defense", "")),
        skill_key=str(skill.get("key", "")),
        skill_mp_cost=str(skill.get("mp_cost", "")),
        skill_damage=str(skill.get("damage", "")),
        skill_speed=str(skill.get("speed", "")),
        skill_range=str(skill.get("range", "")),
        target_source_path=project.target_game.source_path,
        target_source_kind=project.target_game.source_kind,
        item_action_name=item_edit.action_name,
        item_action_frame=str(item_edit.action_frame),
        item_slots=_slot_forms(item_edit.slots),
        texture_selections=dict(project.texture_selections),
    )


def apply_form(project: CharacterProject, form: CharacterForm) -> CharacterProject:
    return replace_project(
        project,
        character_id=form.character_id.strip(),
        character_name=form.character_name.strip(),
        character_name_zh=form.character_name_zh.strip(),
        description=form.description.strip(),
        stats={
            "hp": _parse_int(form.hp),
            "mp": _parse_int(form.mp),
            "defense": _parse_int(form.defense),
        },
        skills={
            "rising_slash": {
                "key": form.skill_key.strip(),
                "mp_cost": _parse_int(form.skill_mp_cost),
                "damage": _parse_int(form.skill_damage),
                "speed": _parse_int(form.skill_speed),
                "range": _parse_int(form.skill_range),
            }
        },
        target_game=_target_game_from_form(project.target_game, form),
        item_frame_edits=(
            FrameItemEdit(
                action_name=form.item_action_name.strip() or "ball",
                action_frame=_parse_int(form.item_action_frame),
                slots=_item_slots_from_form(form.item_slots),
            ),
        ),
        texture_selections={
            key: value.strip()
            for key, value in form.texture_selections.items()
            if key.strip() and value.strip() and value.strip() != KEEP_SOURCE_TEXTURE_ROLE_ID
        },
    )


def texture_labels(parts: tuple[TexturePart, ...]) -> dict[str, str]:
    return {part.part_id: part.label for part in parts}


def item_options_by_label(options: tuple[ItemOption, ...]) -> dict[str, int]:
    return {option.label: option.action_group for option in options}


def selected_item_label(action_group: str, options: tuple[ItemOption, ...]) -> str:
    parsed = _parse_optional_int(action_group)
    if parsed is None:
        return disabled_item_label()
    return item_label(parsed, options)


def item_slots_from_forms(slots: tuple[ItemSlotForm, ...]) -> tuple[ItemSpawnSlot, ...]:
    return _item_slots_from_form(slots)


def frame_item_edits_from_drafts(
    drafts: Mapping[tuple[str, int], tuple[ItemSpawnSlot, ...]],
) -> tuple[FrameItemEdit, ...]:
    edits: list[FrameItemEdit] = []
    for (action_name, action_frame), slots in drafts.items():
        enabled_slots = tuple(slot for slot in slots if slot.enabled)
        if enabled_slots:
            edits.append(
                FrameItemEdit(
                    action_name=action_name,
                    action_frame=action_frame,
                    slots=enabled_slots,
                )
            )
    return tuple(edits)


def disabled_item_label() -> str:
    return "无"


def validation_groups(report: ValidationReport) -> tuple[ValidationGroup, ...]:
    return (
        ValidationGroup("错误", report.by_severity(Severity.ERROR)),
        ValidationGroup("警告", report.by_severity(Severity.WARNING)),
        ValidationGroup("提示", report.by_severity(Severity.INFO)),
    )


def render_validation_report(report: ValidationReport) -> str:
    lines: list[str] = []
    for group in validation_groups(report):
        if not group.issues:
            continue
        lines.append(f"[{group.title}]")
        for issue in group.issues:
            lines.append(f"- {issue.message}")
            lines.append(f"  定位：{issue.target}")
            lines.append(f"  修复：{issue.suggestion}")
    if not lines:
        return "未发现问题。\n"
    return "\n".join(lines) + "\n"


def render_preview(preview: PreviewResult, issues: tuple[ValidationIssue, ...]) -> str:
    lines = [preview.message]
    for issue in issues:
        lines.append(f"{issue.severity.label}: {issue.message}")
        lines.append(f"  定位：{issue.target}")
    return "\n".join(lines) + "\n"


def render_export_result(result: ExportResult) -> str:
    lines = [result.summary]
    if result.exe_path is not None:
        lines.append(f"输出：{result.exe_path}")
    if result.validation_report_path is not None:
        lines.append(f"校验报告：{result.validation_report_path}")
    if result.log_path is not None:
        lines.append(f"导出日志：{result.log_path}")
    if result.diagnostics_path is not None:
        lines.append(f"诊断目录：{result.diagnostics_path}")
    if result.test_checklist:
        lines.append("游戏内测试清单：")
        lines.extend(f"- {item}" for item in result.test_checklist)
    return "\n".join(lines) + "\n"


def focus_target(issue: ValidationIssue) -> str:
    return issue.target


def _parse_int(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0


def _parse_optional_int(value: str) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def _parse_float(value: str) -> float:
    try:
        return float(value.strip())
    except ValueError:
        return 0.0


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _target_game_from_form(current: TargetGame, form: CharacterForm) -> TargetGame:
    source_path = form.target_source_path.strip() or current.source_path
    source_kind = form.target_source_kind.strip() or _target_kind_from_source(source_path)
    if source_path == current.source_path and source_kind == current.source_kind:
        return current
    return TargetGame(
        id=current.id if source_path == current.source_path else "",
        source_path=source_path,
        source_kind=source_kind,
        detected_version=current.detected_version if source_path == current.source_path else "",
        cache_dir=current.cache_dir if source_path == current.source_path else "",
        created_at=current.created_at,
    )


def _target_kind_from_source(source_path: str) -> str:
    lower = source_path.lower()
    if lower.endswith(".swf"):
        return "swf"
    return "exe"


def _slot_forms(slots: tuple[ItemSpawnSlot, ...]) -> tuple[ItemSlotForm, ...]:
    forms = [
        ItemSlotForm(
            item_action_group=str(slot.item_action_group) if slot.enabled else "",
            x=_format_number(slot.x),
            y=_format_number(slot.y),
            z=_format_number(slot.z),
            vx=_format_number(slot.vx),
            vy=_format_number(slot.vy),
            vz=_format_number(slot.vz),
        )
        for slot in slots[:MAX_ITEM_SLOTS]
    ]
    while len(forms) < MAX_ITEM_SLOTS:
        forms.append(
            ItemSlotForm(
                item_action_group="",
                x="255",
                y="-121",
                z="2",
                vx="45",
                vy="0",
                vz="0",
            )
        )
    return tuple(forms)


def _item_slots_from_form(slots: tuple[ItemSlotForm, ...]) -> tuple[ItemSpawnSlot, ...]:
    parsed: list[ItemSpawnSlot] = []
    for slot in slots[:MAX_ITEM_SLOTS]:
        action_group = _parse_optional_int(slot.item_action_group)
        if action_group is None or action_group < 0:
            continue
        parsed.append(
            ItemSpawnSlot(
                item_action_group=action_group,
                ref=-1.0,
                x=_parse_float(slot.x),
                y=_parse_float(slot.y),
                z=_parse_float(slot.z),
                vx=_parse_float(slot.vx),
                vy=_parse_float(slot.vy),
                vz=_parse_float(slot.vz),
            )
        )
    return tuple(parsed)


def _numbered_character_id(index: int) -> str:
    if index <= 99:
        return f"eyj{index:02d}"
    return f"e{index:04d}"[-5:]
