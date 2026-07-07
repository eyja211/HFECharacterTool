from __future__ import annotations

import re
from pathlib import Path

from hfe_character_tool.hfworkshop_catalog import (
    limb_name_length_issues,
    limb_name_replacements,
    load_item_catalog,
    load_spt_action_options,
    load_texture_parts,
    load_texture_roles,
    resolve_action_frame_index_from_options,
)
from hfe_character_tool.models import CharacterProject, Severity, ValidationIssue, ValidationReport
from hfe_character_tool.projects import check_project_structure
from hfe_character_tool.target_cache import (
    TargetCacheEntry,
    TargetCacheError,
    resolve_source_kind,
    target_source_path,
)
from hfe_character_tool.templates import check_template_version, get_template
from hfe_character_tool.tools import ToolConfig

CHARACTER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
ALLOWED_KEYS = {"A", "J", "D>A", "D>J", "U>A", "U>J", "D^A", "D^J"}
OCCUPIED_IDS = {"lucas", "raye", "livermore", "eason", "drew", "jenny"}


def validate_project_structure(project_dir: Path) -> ValidationReport:
    issues = [
        ValidationIssue(
            severity=Severity.ERROR,
            message=f"项目缺少必要项目：{item}",
            suggestion="请重新创建项目或恢复缺失文件。",
            target=f"project.{item}",
            technical_detail=str(project_dir / item.rstrip("/")),
        )
        for item in check_project_structure(project_dir)
    ]
    return ValidationReport(tuple(issues))


def validate_editing(project: CharacterProject) -> ValidationReport:
    return _validate(project, project_dir=None, full=False)


def validate_for_export(
    project_dir: Path,
    project: CharacterProject,
    target_cache: TargetCacheEntry | None = None,
    tools: ToolConfig | None = None,
) -> ValidationReport:
    structure = validate_project_structure(project_dir).issues
    full = _validate(
        project,
        project_dir=project_dir,
        full=True,
        target_cache=target_cache,
        tools=tools,
    ).issues
    return ValidationReport((*structure, *full))


def can_export(report: ValidationReport) -> bool:
    return not report.has_errors


def _validate(
    project: CharacterProject,
    project_dir: Path | None,
    full: bool,
    target_cache: TargetCacheEntry | None = None,
    tools: ToolConfig | None = None,
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    try:
        template = get_template(project.template_id)
    except KeyError:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "项目引用了未知模板。",
                "请重新选择一个内置模板。",
                "template.id",
                project.template_id,
            )
        )
        return ValidationReport(tuple(issues))

    if not check_template_version(project):
        issues.append(
            ValidationIssue(
                Severity.WARNING,
                "项目模板版本与当前内置模板不一致。",
                "建议新建项目或迁移配置后再导出。",
                "template.version",
                f"project={project.template_version}, builtin={template.version}",
            )
        )

    if full and project_dir is not None:
        workspace = _workspace_for_project_dir(project_dir)
        target_path = target_source_path(workspace, project.target_game)
        try:
            resolve_source_kind(target_path, project.target_game.source_kind)
        except TargetCacheError as exc:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    exc.summary,
                    "请选择 .exe 或 .swf 目标游戏文件。",
                    "target_game.source_path",
                    exc.detail,
                )
            )
        if not target_path.is_file():
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "目标游戏文件不存在。",
                    "请重新选择可读取的 HFE .exe 或 .swf 文件。",
                    "target_game.source_path",
                    str(target_path),
                )
            )
        if target_cache is not None:
            issues.extend(_validate_target_cache(target_cache))
            issues.extend(_validate_source_role(project.source_role_id, target_cache))
            issues.extend(_validate_character_id_available(project.character_id, target_cache))

    if not CHARACTER_ID_PATTERN.fullmatch(project.character_id):
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "角色 ID 格式不正确。",
                "请使用小写字母开头，只包含小写字母、数字和下划线，长度 2 到 32。",
                "character.id",
                project.character_id,
            )
        )
    if project.character_id in OCCUPIED_IDS:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "角色 ID 与已知原版角色冲突。",
                "请换一个不与原版角色重复的角色 ID。",
                "character.id",
                project.character_id,
            )
        )
    id_byte_length = len(project.character_id.encode("utf-8"))
    source_role_id = project.source_role_id or "lucas"
    source_role_byte_length = len(source_role_id.encode("utf-8"))
    if template.template_id == "lucas-basic" and (
        not project.character_id.isascii() or id_byte_length != source_role_byte_length
    ):
        issues.append(
            ValidationIssue(
                Severity.ERROR if full else Severity.WARNING,
                "当前二进制导出路线要求角色 ID 与所选源模板 ID 字节长度一致。",
                (
                    f"当前源模板为 {source_role_id}，需要 "
                    f"{source_role_byte_length} 个 ASCII 字节；"
                    "这是为了保持 SPT/LMI 内嵌资源命名空间稳定。"
                ),
                "character.id",
                project.character_id,
            )
        )
    if not project.character_name.strip():
        issues.append(
            ValidationIssue(
                Severity.ERROR, "角色名称不能为空。", "请输入角色名称。", "character.name"
            )
        )
    if len(project.character_name) > 24:
        issues.append(
            ValidationIssue(
                Severity.WARNING,
                "角色名称较长，游戏内可能显示拥挤。",
                "建议控制在 24 个字符以内。",
                "character.name",
            )
        )

    for stat_name in ("hp", "mp"):
        value = int(project.stats.get(stat_name, 0))
        if value < 1 or value > 9999:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    f"{_field_label(stat_name)} 必须在 1 到 9999 之间。",
                    "请调整为安全范围内的数值。",
                    f"stats.{stat_name}",
                    str(value),
                )
            )
    defense = int(project.stats.get("defense", 0))
    if defense < 0 or defense > 9999:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                f"{_field_label('defense')} 必须在 0 到 9999 之间。",
                "请调整为安全范围内的数值。",
                "stats.defense",
                str(defense),
            )
        )

    for skill_name, skill in project.skills.items():
        if skill_name not in template.skill_defaults:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "技能模板不存在。",
                    "请选择当前模板提供的技能。",
                    f"skills.{skill_name}",
                )
            )
        key = str(skill.get("key", ""))
        if key not in ALLOWED_KEYS:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "技能按键不在安全列表中。",
                    "请从工具提供的按键选项中选择。",
                    f"skills.{skill_name}.key",
                    key,
                )
            )
        for field, minimum, maximum in (
            ("mp_cost", 0, 500),
            ("damage", 0, 999),
            ("speed", 1, 100),
            ("range", 1, 500),
        ):
            value = int(skill.get(field, -1))
            if value < minimum or value > maximum:
                issues.append(
                    ValidationIssue(
                        Severity.ERROR,
                        f"技能参数 {field} 超出安全范围。",
                        f"请调整到 {minimum} 到 {maximum} 之间。",
                        f"skills.{skill_name}.{field}",
                        str(value),
                    )
                )

    for edit_index, edit in enumerate(project.item_frame_edits):
        target = f"item_frame_edits.{edit_index}"
        if not edit.action_name.strip():
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "道具动作名格式不正确。",
                    "请使用 Lucas SPT 中存在的动作名，例如 ball。",
                    f"{target}.action_name",
                    edit.action_name,
                )
            )
        if edit.action_frame < 1 or edit.action_frame > 999:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "动作内帧数超出安全范围。",
                    "请填写从 1 开始的动作内帧数。",
                    f"{target}.action_frame",
                    str(edit.action_frame),
                )
            )
        if full and project_dir is not None:
            workspace = _workspace_for_project_dir(project_dir)
            if target_cache is not None:
                actions = load_spt_action_options(
                    workspace,
                    target_cache,
                    project.source_role_id,
                    tools,
                )
                try:
                    resolve_action_frame_index_from_options(
                        actions,
                        edit.action_name,
                        edit.action_frame,
                    )
                except ValueError as exc:
                    issues.append(
                        ValidationIssue(
                            Severity.ERROR,
                            "动作或帧数不在当前模板中。",
                            "请从工具的动作和帧数下拉选项中重新选择。",
                            f"{target}.action_frame",
                            str(exc),
                        )
                    )
        enabled_slots = tuple(slot for slot in edit.slots if slot.enabled)
        if len(enabled_slots) > 3:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    "同一帧最多先支持 3 个道具。",
                    "请减少这一帧的道具槽数量。",
                    f"{target}.slots",
                    str(len(enabled_slots)),
                )
            )
        for slot_index, slot in enumerate(enabled_slots):
            slot_target = f"{target}.slots.{slot_index}"
            if slot.item_action_group < 0 or slot.item_action_group > 999:
                issues.append(
                    ValidationIssue(
                        Severity.ERROR,
                        "道具 actionGroup 超出安全范围。",
                        "请从工具提供的道具列表中选择。",
                        f"{slot_target}.item_action_group",
                        str(slot.item_action_group),
                    )
                )
            if full and project_dir is not None and target_cache is not None:
                workspace = _workspace_for_project_dir(project_dir)
                item_catalog = load_item_catalog(workspace, target_cache, tools)
                if not item_catalog.available:
                    issues.append(
                        ValidationIssue(
                            Severity.ERROR,
                            "目标道具目录不可用。",
                            "请更换包含可解析 item SPT 的目标，或先清空该帧道具编辑。",
                            f"{slot_target}.item_action_group",
                            item_catalog.unavailable_reason,
                        )
                    )
                elif slot.item_action_group not in {
                    option.action_group for option in item_catalog.options
                }:
                    issues.append(
                        ValidationIssue(
                            Severity.ERROR,
                            "道具 actionGroup 不在当前 item SPT 中。",
                            "请从工具提供的道具列表中重新选择。",
                            f"{slot_target}.item_action_group",
                            str(slot.item_action_group),
                        )
                    )
            for coord_field, coord_value in (
                ("x", slot.x),
                ("y", slot.y),
                ("z", slot.z),
                ("vx", slot.vx),
                ("vy", slot.vy),
                ("vz", slot.vz),
            ):
                if coord_value < -9999 or coord_value > 9999:
                    issues.append(
                        ValidationIssue(
                            Severity.ERROR,
                            f"道具 {coord_field} 超出安全范围。",
                            "请填写 -9999 到 9999 之间的数字。",
                            f"{slot_target}.{coord_field}",
                            str(coord_value),
                        )
                    )

    if full and project_dir is not None and project.texture_selections:
        workspace = _workspace_for_project_dir(project_dir)
        unsafe_limb_names = limb_name_length_issues(
            limb_name_replacements(
                workspace,
                project.texture_selections,
                load_texture_parts(workspace),
                load_texture_roles(workspace),
                project.source_role_id or "lucas",
            )
        )
        if unsafe_limb_names:
            sample = unsafe_limb_names[0]
            issues.append(
                ValidationIssue(
                    Severity.WARNING,
                    "贴图替换会改变 limbName 字节长度，导出会继续，但游戏内可能错位。",
                    (
                        "已允许导出，请在游戏内验证身体部位对齐情况；"
                        "精确 uz 偏移微调属于后续功能。"
                    ),
                    "textures",
                    (
                        f"{sample.source_limb}({sample.source_length}) -> "
                        f"{sample.target_limb}({sample.target_length})"
                    ),
                )
            )

    asset_names = {asset.file_name for asset in project.assets}
    for required in template.required_assets:
        if required not in asset_names:
            issues.append(
                ValidationIssue(
                    Severity.ERROR if full else Severity.WARNING,
                    f"缺少必需 PNG 素材：{required}",
                    "请导入模板要求的完整 PNG 素材。",
                    f"assets.{required}",
                )
            )
        elif project_dir is not None and not (project_dir / "assets" / required).is_file():
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    f"素材清单中的文件不存在：{required}",
                    "请重新导入素材或修正素材清单。",
                    f"assets.{required}",
                    str(project_dir / "assets" / required),
                )
            )

    if project.assets:
        issues.append(
            ValidationIssue(
                Severity.INFO,
                "素材清单已登记，可进行静态预览。",
                "导出前仍需确认游戏内表现。",
                "assets",
            )
        )

    return ValidationReport(tuple(issues))


def _validate_target_cache(target_cache: TargetCacheEntry) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not target_cache.cached_swf_path.is_file():
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "目标 SWF 缓存不存在。",
                "请重新探测目标游戏文件后再导出。",
                "target_game.cache",
                str(target_cache.cached_swf_path),
            )
        )
    if not target_cache.can_locate_global:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "目标 SWF 中未定位到 Data.Global。",
                "该目标版本暂不支持自动 patch，请更换兼容目标或记录为后续适配。",
                "target_game.probe.Data.Global",
                f"cache_id={target_cache.cache_id}; probe={target_cache.probe_json_path}",
            )
        )
    if not target_cache.can_locate_loaders:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "目标 SWF 中未定位到加载器锚点。",
                "该目标版本的字节码布局暂不支持自动 patch。",
                "target_game.probe.loaders",
                f"cache_id={target_cache.cache_id}; probe={target_cache.probe_json_path}",
            )
        )
    if target_cache.has_unsafe_runtime_custom_loader:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "目标包包含旧版不安全的运行时循环加载器。",
                "请改用原版或重新导出的稳定目标包；这种旧坏包继续追加角色会闪退。",
                "target_game.probe.custom_loader",
                (
                    f"cache_id={target_cache.cache_id}; "
                    f"kinds={', '.join(target_cache.unsafe_runtime_custom_loader_kinds)}; "
                    f"probe={target_cache.probe_json_path}"
                ),
            )
        )
    if target_cache.missing_symbol_abc_classes:
        issues.append(
            ValidationIssue(
                Severity.ERROR,
                "目标 SWF 存在 SymbolClass 与 ABC 类定义不一致。",
                "请查看 probe 报告，确认目标版本资源结构后再导出。",
                "target_game.probe.SymbolClass",
                ", ".join(target_cache.missing_symbol_abc_classes),
            )
        )
    return issues


def _validate_source_role(
    source_role_id: str,
    target_cache: TargetCacheEntry,
) -> list[ValidationIssue]:
    role_id = source_role_id or "lucas"
    required_spt = f"Data.Global_{role_id}Spt"
    required_lmi = f"Data.Global_{role_id}Lmi"
    symbols = set(target_cache.data_global_symbol_names)
    missing = [name for name in (required_spt, required_lmi) if name not in symbols]
    if missing:
        return [
            ValidationIssue(
                Severity.ERROR,
                "目标版本中不存在所选源角色模板。",
                "请在新建角色时先选择目标版本，再选择该版本实际包含的角色模板。",
                "template.source_role_id",
                ", ".join(missing),
            )
        ]
    if (
        target_cache.has_global_pow_character_ids
        and role_id not in target_cache.global_pow_character_ids
    ):
        return [
            ValidationIssue(
                Severity.WARNING,
                "所选源角色缺少完整玩家角色 metadata，将按小兵/NPC 模板导出。",
                (
                    "导出会使用可选角色 fallback 写入新角色 metadata 与 VS 选人入口；"
                    "新角色可能不会出现在角色一览中。"
                ),
                "template.source_role_id",
                f"{role_id} 缺少 Global.pow 角色 metadata；SPT/LMI 仍可作为模板",
            )
        ]
    return []


def _validate_character_id_available(
    character_id: str,
    target_cache: TargetCacheEntry,
) -> list[ValidationIssue]:
    occupied_locations: list[str] = []
    if character_id in target_cache.global_pow_character_ids:
        occupied_locations.append("Data.Global.pow")
    if character_id in target_cache.global_char_list_order:
        occupied_locations.append("Data.Global.charListOrder")
    if character_id in target_cache.select_char_option_ids:
        occupied_locations.append("Web_misc.SelectCharPanel.charOption")
    symbols = set(target_cache.data_global_symbol_names)
    for class_name in (
        f"Data.Global_{character_id}Spt",
        f"Data.Global_{character_id}Lmi",
    ):
        if class_name in symbols:
            occupied_locations.append(class_name)
    if not occupied_locations:
        return []
    return [
        ValidationIssue(
            Severity.ERROR,
            "Character ID already exists in the target game.",
            "Use a new unused ID before exporting again.",
            "character.id",
            f"{character_id}: {', '.join(dict.fromkeys(occupied_locations))}",
        )
    ]


def _workspace_for_project_dir(project_dir: Path) -> Path:
    if project_dir.parent.name == "projects":
        return project_dir.parent.parent
    return project_dir.parent


def _field_label(field: str) -> str:
    return {"hp": "HP", "mp": "MP", "defense": "防御值"}.get(field, field)
