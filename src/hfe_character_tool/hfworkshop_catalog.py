from __future__ import annotations

import json
import struct
import zlib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, cast

from hfe_character_tool.models import ItemSpawnSlot
from hfe_character_tool.runtime import resource_path
from hfe_character_tool.swf_data_extract import extract_data_global_symbol
from hfe_character_tool.target_cache import TargetCacheEntry
from hfe_character_tool.tools import ToolConfig


@dataclass(frozen=True)
class TexturePart:
    part_id: str
    label: str
    filenames: tuple[str, ...]


@dataclass(frozen=True)
class TextureRole:
    role_id: str
    label: str


@dataclass(frozen=True)
class ItemOption:
    action_group: int
    name: str
    label: str


@dataclass(frozen=True)
class SptActionInfo:
    action_name: str
    first_frame_index: int
    frame_count: int
    item_frames: tuple[int, ...]


@dataclass(frozen=True)
class LimbNameLengthIssue:
    source_limb: str
    target_limb: str
    source_length: int
    target_length: int


@dataclass(frozen=True)
class CharacterTemplateCatalogEntry:
    template_id: str
    role_id: str
    label: str
    spt_class: str
    lmi_class: str
    available: bool = True
    unavailable_reason: str = ""


@dataclass(frozen=True)
class ItemCatalog:
    available: bool
    options: tuple[ItemOption, ...]
    source: Path | None = None
    unavailable_reason: str = ""


LUCAS_LMI_RELATIVE = Path("vendor") / "HFWorkshop" / "371 - Data.Global_lucasLmi"
LUCAS_SPT_RELATIVE = Path("vendor") / "HFWorkshop" / "442 - Data.Global_lucasSpt" / "Spt.json"
ITEM_SPT_RELATIVE = Path("vendor") / "HFWorkshop" / "185 - Data.Global_itemSpt" / "Spt.json"
ORIGINAL_GAME_RELATIVE = Path("vendor") / "original_game" / "HFE v1.0.2.exe"
KEEP_SOURCE_TEXTURE_ROLE_ID = "__source__"
KEEP_SOURCE_TEXTURE_ROLE = TextureRole(
    KEEP_SOURCE_TEXTURE_ROLE_ID, "\u4fdd\u6301\u6a21\u677f\u539f\u6837"
)

_PART_LABELS = {
    "head": "头部",
    "chest": "身体",
    "hips": "腰部",
    "arm_upper": "上臂",
    "arm_lower": "下臂",
    "fist_left": "左拳",
    "fist_right": "右拳",
    "leg_upper": "大腿",
    "leg_lower": "小腿",
    "foot": "脚",
    "sword": "武器",
}

_PART_ORDER = tuple(_PART_LABELS)

_PART_SOURCE_LIMBS = {
    "head": ("Lucas_00Head",),
    "chest": ("Lucas_01Chest",),
    "hips": ("Lucas_03Hips",),
    "arm_upper": ("Lucas_05UpperArm",),
    "arm_lower": ("Lucas_06LowerArm",),
    "fist_left": ("Lucas_07LeftFist", "Lucas_08LeftFistCover"),
    "fist_right": ("Lucas_12RightFist", "Lucas_13RightFistCover"),
    "leg_upper": ("Lucas_14UpperLeg", "Lucas_17UpperLeg"),
    "leg_lower": ("Lucas_15LowerLeg", "Lucas_18LowerLeg"),
    "foot": ("Lucas_16Foot",),
    "sword": ("Lucas_24Sword", "Lucas_41SwordCover"),
}

_DONOR_SUFFIX_CANDIDATES = {
    "16Foot": ("16Foot", "15Foot"),
    "17UpperLeg": ("17UpperLeg", "17UpperLegRight", "14UpperLeg"),
    "18LowerLeg": ("18LowerLeg", "18LowerLegRight", "15LowerLeg"),
    "Lucas_17UpperLeg": ("17UpperLeg", "17UpperLegRight", "14UpperLeg"),
    "Lucas_18LowerLeg": ("18LowerLeg", "18LowerLegRight", "15LowerLeg"),
    "Lucas_41SwordCover": ("41SwordCover", "41Swordcover"),
}

def load_texture_parts(workspace: Path) -> tuple[TexturePart, ...]:
    lmi_dir = resource_path(workspace, LUCAS_LMI_RELATIVE)
    if not lmi_dir.is_dir():
        return fallback_texture_parts()

    names: dict[str, set[str]] = {part_id: set() for part_id in _PART_ORDER}
    for limb_pic in lmi_dir.glob("LimbPic_*.json"):
        raw = _read_json(limb_pic).get("Data.LimbPic")
        if not isinstance(raw, Mapping):
            continue
        basename = Path(str(raw.get("filename", ""))).name
        part_id = _part_id_for_filename(basename)
        if part_id is not None:
            names[part_id].add(basename)

    parts = tuple(
        TexturePart(part_id, _PART_LABELS[part_id], tuple(sorted(names[part_id])))
        for part_id in _PART_ORDER
        if names[part_id]
    )
    return parts or fallback_texture_parts()


def fallback_texture_parts() -> tuple[TexturePart, ...]:
    return (
        TexturePart("head", "头部", ("head01.png",)),
        TexturePart("chest", "身体", ("chest01.png",)),
        TexturePart("sword", "武器", ("sword01.png",)),
    )


def default_texture_selections(parts: tuple[TexturePart, ...]) -> dict[str, str]:
    return {}


def texture_replacements(
    selections: Mapping[str, str],
    parts: tuple[TexturePart, ...],
) -> dict[str, str]:
    replacements: dict[str, str] = {}
    by_part = {part.part_id: part for part in parts}
    for part_id, selected in selections.items():
        part = by_part.get(part_id)
        if part is None or selected not in part.filenames:
            continue
        if selected == part.filenames[0]:
            continue
        for source in part.filenames:
            if source != selected and len(source.encode("utf-8")) == len(selected.encode("utf-8")):
                replacements[f"png_Lucas_Limbs/{source}"] = f"png_Lucas_Limbs/{selected}"
    return replacements


def load_texture_roles(workspace: Path) -> tuple[TextureRole, ...]:
    role_ids = _discover_lmi_role_ids(resource_path(workspace, ORIGINAL_GAME_RELATIVE))
    usable = [role_id for role_id in role_ids if _is_player_texture_role(role_id)]
    if "lucas" not in usable:
        usable.insert(0, "lucas")
    ordered = sorted(set(usable), key=lambda role_id: (role_id != "lucas", role_id))
    return (
        KEEP_SOURCE_TEXTURE_ROLE,
        *tuple(TextureRole(role_id, _role_label(role_id)) for role_id in ordered),
    )


def load_character_template_catalog(
    workspace: Path,
    target_cache: TargetCacheEntry | None = None,
) -> tuple[CharacterTemplateCatalogEntry, ...]:
    if target_cache is None:
        role_ids = _discover_lmi_role_ids(resource_path(workspace, ORIGINAL_GAME_RELATIVE))
        symbols = {
            f"Data.Global_{role_id}Spt": True
            for role_id in role_ids
        }
        symbols.update({f"Data.Global_{role_id}Lmi": True for role_id in role_ids})
    else:
        symbols = {name: True for name in target_cache.data_global_symbol_names}
    spt_by_role = _role_symbols_by_suffix(symbols, "Spt")
    lmi_by_role = _role_symbols_by_suffix(symbols, "Lmi")
    catalog_role_ids = sorted(
        set(spt_by_role) | set(lmi_by_role),
        key=lambda item: (item != "lucas", item),
    )
    entries: list[CharacterTemplateCatalogEntry] = []
    for role_id in catalog_role_ids:
        if role_id in {"item", "global"}:
            continue
        spt_class = spt_by_role.get(role_id, "")
        lmi_class = lmi_by_role.get(role_id, "")
        missing = []
        if not spt_class:
            missing.append("SPT")
        if not lmi_class:
            missing.append("LMI")
        entries.append(
            CharacterTemplateCatalogEntry(
                template_id=f"target:{role_id}",
                role_id=role_id,
                label=_role_label(role_id),
                spt_class=spt_class,
                lmi_class=lmi_class,
                available=not missing,
                unavailable_reason=f"缺少 {'/'.join(missing)}" if missing else "",
            )
        )
    if entries:
        return tuple(entries)
    return (
        CharacterTemplateCatalogEntry(
            template_id="target:lucas",
            role_id="lucas",
            label=_role_label("lucas"),
            spt_class="Data.Global_lucasSpt",
            lmi_class="Data.Global_lucasLmi",
        ),
    )


def texture_role_labels(roles: tuple[TextureRole, ...]) -> dict[str, str]:
    return {role.role_id: role.label for role in roles}


def texture_role_ids_by_label(roles: tuple[TextureRole, ...]) -> dict[str, str]:
    return {role.label: role.role_id for role in roles}


def selected_texture_role_label(role_id: str, roles: tuple[TextureRole, ...]) -> str:
    labels = texture_role_labels(roles)
    if role_id == KEEP_SOURCE_TEXTURE_ROLE_ID:
        return KEEP_SOURCE_TEXTURE_ROLE.label
    return labels.get(role_id, labels.get("lucas", "Lucas (lucas)"))


def texture_role_selections(
    selections: Mapping[str, str],
    parts: tuple[TexturePart, ...],
    roles: tuple[TextureRole, ...],
    default_role_id: str = "lucas",
) -> dict[str, str]:
    part_ids = {part.part_id for part in parts}
    role_ids = {role.role_id for role in roles}
    return {
        part_id: role_id
        for part_id, role_id in selections.items()
        if part_id in part_ids
        and role_id in role_ids
        and role_id not in {KEEP_SOURCE_TEXTURE_ROLE_ID, default_role_id}
    }


def limb_name_replacements(
    workspace: Path,
    selections: Mapping[str, str],
    parts: tuple[TexturePart, ...],
    roles: tuple[TextureRole, ...],
    source_role_id: str = "lucas",
) -> dict[str, str]:
    role_selections = texture_role_selections(
        selections, parts, roles, default_role_id=source_role_id
    )
    if not role_selections:
        return {}
    catalog = _load_role_limb_names(resource_path(workspace, ORIGINAL_GAME_RELATIVE))
    source_names = catalog.get(source_role_id, ()) or catalog.get("lucas", ())
    replacements: dict[str, str] = {}
    for part_id, role_id in role_selections.items():
        donor_names = catalog.get(role_id, ())
        for source_limb in _source_limb_names_for_part(part_id, source_names):
            target = _best_donor_limb(source_limb, donor_names, preferred_role_id=role_id)
            if target is not None and target != source_limb:
                replacements[source_limb] = target
    return replacements


def limb_name_length_issues(
    replacements: Mapping[str, str],
) -> tuple[LimbNameLengthIssue, ...]:
    return tuple(
        LimbNameLengthIssue(
            source_limb=source,
            target_limb=target,
            source_length=len(source.encode("utf-8")),
            target_length=len(target.encode("utf-8")),
        )
        for source, target in sorted(replacements.items())
        if len(source.encode("utf-8")) != len(target.encode("utf-8"))
    )


def load_item_options(workspace: Path) -> tuple[ItemOption, ...]:
    path = resource_path(workspace, ITEM_SPT_RELATIVE)
    if not path.is_file():
        return fallback_item_options()
    return load_item_options_from_spt_json(path) or fallback_item_options()


def load_item_options_from_spt_json(path: Path) -> tuple[ItemOption, ...]:
    data = _read_json(path).get("Data.Spt")
    if not isinstance(data, Mapping):
        return ()
    action_group = data.get("actionGroup")
    if not isinstance(action_group, Mapping):
        return ()

    options: list[ItemOption] = []
    for key, value in action_group.items():
        if key == "HFW_ArrayLenXXX" or not str(key).isdigit() or not isinstance(value, Mapping):
            continue
        name = str(value.get("name", ""))
        if not name.strip():
            continue
        action_group_id = int(key)
        options.append(ItemOption(action_group_id, name, f"{action_group_id}: {name}"))
    return tuple(sorted(options, key=lambda item: item.action_group))


def load_item_options_from_spt_payload(path: Path) -> tuple[ItemOption, ...]:
    return load_item_options_from_spt_payload_bytes(path.read_bytes())


def load_item_options_from_spt_payload_bytes(data: bytes) -> tuple[ItemOption, ...]:
    options: list[ItemOption] = []
    seen: set[int] = set()
    starts = _object_starts(data, "Data.ActionGroup")
    for index, start in enumerate(starts):
        action_group_id = _numeric_key_before(data, start)
        if action_group_id is None or action_group_id in seen:
            continue
        end = starts[index + 1] if index + 1 < len(starts) else len(data)
        name = _last_named_string(data[start:end], "name")
        if not name:
            continue
        seen.add(action_group_id)
        options.append(ItemOption(action_group_id, name, f"{action_group_id}: {name}"))
    return tuple(sorted(options, key=lambda item: item.action_group))


def load_item_catalog(
    workspace: Path,
    target_cache: TargetCacheEntry | None = None,
    tools: ToolConfig | None = None,
) -> ItemCatalog:
    builtin_path = resource_path(workspace, ITEM_SPT_RELATIVE)
    if target_cache is None:
        options = load_item_options(workspace)
        return ItemCatalog(bool(options), options, builtin_path if builtin_path.is_file() else None)
    if (
        _same_path(target_cache.source_path, resource_path(workspace, ORIGINAL_GAME_RELATIVE))
        and builtin_path.is_file()
    ):
        options = load_item_options_from_spt_json(builtin_path)
        return ItemCatalog(True, options, builtin_path)
    if not target_cache.item_spt_symbol_name:
        return ItemCatalog(
            False,
            (),
            target_cache.probe_json_path,
            "目标 SWF 未发现 Data.Global_itemSpt，无法提供道具目录。",
        )
    payload_path = ensure_data_global_payload(
        workspace,
        target_cache,
        target_cache.item_spt_symbol_name,
        tools,
    )
    if payload_path is not None:
        options = load_item_options_from_spt_payload(payload_path)
        if options:
            return ItemCatalog(True, options, payload_path)
    return ItemCatalog(
        False,
        (),
        target_cache.probe_json_path,
        "目标 itemSpt 已定位，但自动抽取或解析 actionGroup 目录失败。",
    )


def fallback_item_options() -> tuple[ItemOption, ...]:
    return (
        ItemOption(5, "lucasB", "5: lucasB"),
        ItemOption(35, "swordwind", "35: swordwind"),
        ItemOption(74, "easonball", "74: easonball"),
    )


def item_label(action_group: int, options: tuple[ItemOption, ...]) -> str:
    for option in options:
        if option.action_group == action_group:
            return option.label
    return str(action_group)


def load_spt_action_options(
    workspace: Path,
    target_cache: TargetCacheEntry | None = None,
    role_id: str = "lucas",
    tools: ToolConfig | None = None,
) -> tuple[SptActionInfo, ...]:
    if target_cache is not None:
        class_name = f"Data.Global_{role_id or 'lucas'}Spt"
        payload_path = ensure_data_global_payload(workspace, target_cache, class_name, tools)
        if payload_path is not None:
            actions = load_spt_action_options_from_payload(payload_path)
            if actions:
                return actions
        if role_id != "lucas":
            return ()
    path = resource_path(workspace, LUCAS_SPT_RELATIVE)
    if not path.is_file():
        return fallback_spt_action_options()
    return load_spt_action_options_from_json(path) or fallback_spt_action_options()


def load_spt_action_options_from_json(path: Path) -> tuple[SptActionInfo, ...]:
    data = _read_json(path).get("Data.Spt")
    if not isinstance(data, Mapping):
        return ()
    frames = data.get("frame")
    if not isinstance(frames, Mapping):
        return ()
    count = int(frames.get("HFW_ArrayLenXXX", 0))

    actions: list[SptActionInfo] = []
    current_name = ""
    current_start = 0
    current_count = 0
    item_frames: list[int] = []
    for frame_index in range(count):
        frame = frames.get(str(frame_index))
        if not isinstance(frame, Mapping):
            continue
        name = str(frame.get("name", ""))
        if name and not _is_user_visible_action_name(name):
            if current_name:
                actions.append(
                    SptActionInfo(
                        current_name,
                        current_start,
                        current_count,
                        tuple(item_frames),
                    )
                )
                current_name = ""
                current_start = 0
                current_count = 0
                item_frames = []
            continue
        if name:
            if current_name:
                actions.append(
                    SptActionInfo(
                        current_name,
                        current_start,
                        current_count,
                        tuple(item_frames),
                    )
                )
            current_name = name
            current_start = frame_index
            current_count = 0
            item_frames = []
        if not current_name:
            continue
        local_frame = current_count + 1
        if frame.get("cre") is not None:
            item_frames.append(local_frame)
        current_count += 1
        if bool(frame.get("last", False)):
            actions.append(
                SptActionInfo(current_name, current_start, current_count, tuple(item_frames))
            )
            current_name = ""
            current_start = 0
            current_count = 0
            item_frames = []
    if current_name:
        actions.append(
            SptActionInfo(current_name, current_start, current_count, tuple(item_frames))
        )
    return tuple(actions)


def load_spt_action_options_from_payload(path: Path) -> tuple[SptActionInfo, ...]:
    return load_spt_action_options_from_payload_bytes(path.read_bytes())


def load_spt_action_options_from_payload_bytes(data: bytes) -> tuple[SptActionInfo, ...]:
    starts = _object_starts(data, "Data.Frame")
    actions: list[SptActionInfo] = []
    current_name = ""
    current_start = 0
    current_count = 0
    item_frames: list[int] = []
    for frame_index, start in enumerate(starts):
        end = starts[frame_index + 1] if frame_index + 1 < len(starts) else len(data)
        frame = data[start:end]
        name = _first_named_string(frame, "name")
        if name and not _is_user_visible_action_name(name):
            if current_name:
                actions.append(
                    SptActionInfo(
                        current_name,
                        current_start,
                        current_count,
                        tuple(item_frames),
                    )
                )
                current_name = ""
                current_start = 0
                current_count = 0
                item_frames = []
            continue
        if name:
            if current_name:
                actions.append(
                    SptActionInfo(
                        current_name,
                        current_start,
                        current_count,
                        tuple(item_frames),
                    )
                )
            current_name = name
            current_start = frame_index
            current_count = 0
            item_frames = []
        if not current_name:
            continue
        local_frame = current_count + 1
        if _frame_has_item_cre(frame):
            item_frames.append(local_frame)
        current_count += 1
        if _bool_field(frame, "last"):
            actions.append(
                SptActionInfo(current_name, current_start, current_count, tuple(item_frames))
            )
            current_name = ""
            current_start = 0
            current_count = 0
            item_frames = []
    if current_name:
        actions.append(
            SptActionInfo(current_name, current_start, current_count, tuple(item_frames))
        )
    return tuple(actions)


def load_spt_frame_item_slots(
    workspace: Path,
    action_name: str,
    action_frame: int,
    target_cache: TargetCacheEntry | None = None,
    role_id: str = "lucas",
    tools: ToolConfig | None = None,
) -> tuple[ItemSpawnSlot, ...]:
    if target_cache is not None:
        class_name = f"Data.Global_{role_id or 'lucas'}Spt"
        payload_path = ensure_data_global_payload(workspace, target_cache, class_name, tools)
        if payload_path is not None:
            return load_spt_frame_item_slots_from_payload(
                payload_path,
                action_name,
                action_frame,
            )
        if role_id != "lucas":
            return ()
    path = resource_path(workspace, LUCAS_SPT_RELATIVE)
    if not path.is_file():
        return _fallback_frame_item_slots(action_name, action_frame)
    data = _read_json(path).get("Data.Spt")
    if not isinstance(data, Mapping):
        return _fallback_frame_item_slots(action_name, action_frame)
    frames = data.get("frame")
    if not isinstance(frames, Mapping):
        return _fallback_frame_item_slots(action_name, action_frame)
    try:
        frame_index = resolve_action_frame_index(workspace, action_name, action_frame)
    except ValueError:
        return ()
    frame = frames.get(str(frame_index))
    if not isinstance(frame, Mapping):
        return ()
    return _cre_item_slots(frame.get("cre"))


def load_spt_frame_item_slots_from_payload(
    path: Path,
    action_name: str,
    action_frame: int,
) -> tuple[ItemSpawnSlot, ...]:
    data = path.read_bytes()
    actions = load_spt_action_options_from_payload_bytes(data)
    try:
        frame_index = resolve_action_frame_index_from_options(actions, action_name, action_frame)
    except ValueError:
        return ()
    starts = _object_starts(data, "Data.Frame")
    if frame_index < 0 or frame_index >= len(starts):
        return ()
    end = starts[frame_index + 1] if frame_index + 1 < len(starts) else len(data)
    return _cre_item_slots_from_binary_frame(data[starts[frame_index]:end])


def fallback_spt_action_options() -> tuple[SptActionInfo, ...]:
    return (
        SptActionInfo("singR", 280, 2, (1,)),
        SptActionInfo("sing", 282, 2, (1,)),
        SptActionInfo("ball", 316, 15, (8,)),
        SptActionInfo("ball2", 331, 13, (7,)),
        SptActionInfo("ballR", 344, 15, (8,)),
        SptActionInfo("ball2R", 359, 13, (7,)),
        SptActionInfo("ballRb", 372, 13, (7, 8, 9, 10, 11)),
        SptActionInfo("ballJ", 395, 9, (1, 6)),
        SptActionInfo("ball2J", 404, 6, (5,)),
    )


def _fallback_frame_item_slots(action_name: str, action_frame: int) -> tuple[ItemSpawnSlot, ...]:
    for action in fallback_spt_action_options():
        if action.action_name == action_name and action_frame in action.item_frames:
            return (ItemSpawnSlot(),)
    return ()


def _cre_item_slots(raw_cre: object) -> tuple[ItemSpawnSlot, ...]:
    if not isinstance(raw_cre, Mapping):
        return ()
    slots: list[ItemSpawnSlot] = []
    keys = sorted(
        (key for key in raw_cre if str(key).isdigit()),
        key=lambda key: int(str(key)),
    )
    for key in keys:
        raw_slot = raw_cre.get(key)
        if not isinstance(raw_slot, Mapping):
            continue
        action_group = int(_as_float(raw_slot.get("int1"), 0.0))
        if action_group < 0:
            continue
        slots.append(
            ItemSpawnSlot(
                enabled=True,
                item_action_group=action_group,
                ref=_as_float(raw_slot.get("ref"), -1.0),
                x=_as_float(raw_slot.get("x"), 255.0),
                y=_as_float(raw_slot.get("y"), -121.0),
                z=_as_float(raw_slot.get("z"), 2.0),
                vx=_as_float(raw_slot.get("vx"), 45.0),
                vy=_as_float(raw_slot.get("vy"), 0.0),
                vz=_as_float(raw_slot.get("vz"), 0.0),
            )
        )
    return tuple(slots)


def _as_float(value: object, default: float) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError):
        return default


def action_labels(actions: tuple[SptActionInfo, ...]) -> tuple[str, ...]:
    return tuple(action.action_name for action in actions)


def _is_user_visible_action_name(action_name: str) -> bool:
    stripped = action_name.strip()
    return bool(stripped) and not stripped.startswith("//") and "<Err>" not in stripped


def resolve_action_frame_index(
    workspace: Path,
    action_name: str,
    action_frame: int,
) -> int:
    return resolve_action_frame_index_from_options(
        load_spt_action_options(workspace),
        action_name,
        action_frame,
    )


def resolve_action_frame_index_from_options(
    actions: tuple[SptActionInfo, ...],
    action_name: str,
    action_frame: int,
) -> int:
    for action in actions:
        if action.action_name != action_name:
            continue
        if action_frame < 1 or action_frame > action.frame_count:
            raise ValueError(
                f"Action {action_name} has {action.frame_count} frames, got {action_frame}"
            )
        return action.first_frame_index + action_frame - 1
    raise ValueError(f"Unknown SPT action: {action_name}")


def ensure_data_global_payload(
    workspace: Path,
    target_cache: TargetCacheEntry,
    class_name: str,
    tools: ToolConfig | None = None,
) -> Path | None:
    output_dir = target_cache.cache_dir / "symbols"
    payload_path = output_dir / f"{_safe_symbol_filename(class_name)}.payload.bin"
    if payload_path.is_file():
        return payload_path
    if tools is None:
        return None
    result = extract_data_global_symbol(
        target_cache.cached_swf_path,
        class_name,
        tools,
        output_dir,
        workspace,
    )
    if (
        result.compile_result.success
        and result.run_result.success
        and result.payload_path.is_file()
    ):
        return result.payload_path
    return None


def _role_symbols_by_suffix(symbols: Mapping[str, bool], suffix: str) -> dict[str, str]:
    prefix = "Data.Global_"
    result: dict[str, str] = {}
    for class_name in symbols:
        if not class_name.startswith(prefix) or not class_name.endswith(suffix):
            continue
        role_id = class_name[len(prefix) : -len(suffix)]
        if role_id:
            result[role_id] = class_name
    return result


def _object_starts(data: bytes, class_name: str) -> tuple[int, ...]:
    marker = _typed_object_marker(class_name)
    starts: list[int] = []
    offset = 0
    while offset <= len(data) - len(marker):
        found = data.find(marker, offset)
        if found < 0:
            break
        starts.append(found)
        offset = found + len(marker)
    return tuple(starts)


def _typed_object_marker(class_name: str) -> bytes:
    encoded = class_name.encode("utf-8")
    if len(encoded) > 65535:
        raise ValueError("class name is too long")
    return b"\x10" + len(encoded).to_bytes(2, "big") + encoded


def _numeric_key_before(data: bytes, offset: int) -> int | None:
    for length in range(1, 8):
        key_offset = offset - 2 - length
        if key_offset < 0:
            continue
        if data[key_offset : key_offset + 2] != length.to_bytes(2, "big"):
            continue
        raw = data[key_offset + 2 : offset]
        if raw.isdigit():
            return int(raw.decode("ascii"))
    return None


def _first_named_string(data: bytes, field_name: str) -> str:
    values = _named_string_values(data, field_name)
    return values[0] if values else ""


def _last_named_string(data: bytes, field_name: str) -> str:
    values = _named_string_values(data, field_name)
    return values[-1] if values else ""


def _named_string_values(data: bytes, field_name: str) -> tuple[str, ...]:
    name = field_name.encode("utf-8")
    pattern = len(name).to_bytes(2, "big") + name + b"\x02"
    values: list[str] = []
    offset = 0
    while offset <= len(data) - len(pattern) - 2:
        found = data.find(pattern, offset)
        if found < 0:
            break
        length_offset = found + len(pattern)
        length = int.from_bytes(data[length_offset : length_offset + 2], "big")
        value_offset = length_offset + 2
        value_end = value_offset + length
        if value_end > len(data):
            offset = found + 1
            continue
        with suppress(UnicodeDecodeError):
            values.append(data[value_offset:value_end].decode("utf-8"))
        offset = value_end
    return tuple(values)


def _bool_field(data: bytes, field_name: str) -> bool:
    name = field_name.encode("utf-8")
    pattern = len(name).to_bytes(2, "big") + name + b"\x01"
    found = data.find(pattern)
    return found >= 0 and found + len(pattern) < len(data) and data[found + len(pattern)] == 1


def _frame_has_item_cre(data: bytes) -> bool:
    cre_offset = _field_offset(data, "cre")
    return cre_offset >= 0 and cre_offset + 5 < len(data) and data[cre_offset + 5] != 0x05


def _cre_item_slots_from_binary_frame(data: bytes) -> tuple[ItemSpawnSlot, ...]:
    cre_offset = _field_offset(data, "cre")
    if cre_offset < 0 or cre_offset + 5 >= len(data) or data[cre_offset + 5] != 0x08:
        return ()
    slots: list[ItemSpawnSlot] = []
    pt_starts = _object_starts(data[cre_offset:], "Data.Pt")
    for relative_start in pt_starts[:3]:
        start = cre_offset + relative_start
        end = data.find(b"\x00\x00\x09", start)
        if end < 0:
            continue
        pt_object = data[start : end + 3]
        action_group = int(_double_field(pt_object, "int1", -1.0))
        if action_group < 0:
            continue
        slots.append(
            ItemSpawnSlot(
                enabled=True,
                item_action_group=action_group,
                ref=_double_field(pt_object, "ref", -1.0),
                x=_double_field(pt_object, "x", 255.0),
                y=_double_field(pt_object, "y", -121.0),
                z=_double_field(pt_object, "z", 2.0),
                vx=_double_field(pt_object, "vx", 45.0),
                vy=_double_field(pt_object, "vy", 0.0),
                vz=_double_field(pt_object, "vz", 0.0),
            )
        )
    return tuple(slots)


def _field_offset(data: bytes, field_name: str) -> int:
    name = field_name.encode("utf-8")
    return data.find(len(name).to_bytes(2, "big") + name)


def _double_field(data: bytes, field_name: str, default: float) -> float:
    name = field_name.encode("utf-8")
    pattern = len(name).to_bytes(2, "big") + name + b"\x00"
    offset = data.find(pattern)
    value_offset = offset + len(pattern)
    if offset < 0 or value_offset + 8 > len(data):
        return default
    return float(struct.unpack(">d", data[value_offset : value_offset + 8])[0])


def _safe_symbol_filename(class_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in class_name)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _read_json(path: Path) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], json.loads(path.read_text(encoding="utf-8-sig")))


def _part_id_for_filename(filename: str) -> str | None:
    stem = filename.rsplit(".", 1)[0]
    if stem.startswith("swordcover"):
        return "sword"
    for part_id in sorted(_PART_ORDER, key=len, reverse=True):
        if stem.startswith(part_id):
            return part_id
    return None


def _discover_lmi_role_ids(exe_path: Path) -> tuple[str, ...]:
    if not exe_path.is_file():
        return ("lucas",)
    try:
        swf = _extract_projector_swf_bytes(exe_path)
        body = _decompress_swf_body(swf)
        names = _symbol_class_names(body)
    except (OSError, ValueError, zlib.error):
        return ("lucas",)
    prefix = "Data.Global_"
    suffix = "Lmi"
    role_ids = []
    for name in names:
        if name.startswith(prefix) and name.endswith(suffix):
            role_id = name[len(prefix) : -len(suffix)]
            if role_id:
                role_ids.append(role_id)
    return tuple(role_ids) or ("lucas",)


def _load_role_limb_names(exe_path: Path) -> dict[str, tuple[str, ...]]:
    if not exe_path.is_file():
        return {}
    try:
        swf = _extract_projector_swf_bytes(exe_path)
        body = _decompress_swf_body(swf)
        symbols = _symbol_class_map(body)
        binaries = _binary_data_by_id(body)
    except (OSError, ValueError, zlib.error):
        return {}
    role_names: dict[str, tuple[str, ...]] = {}
    prefix = "Data.Global_"
    suffix = "Lmi"
    for class_name, symbol_id in symbols.items():
        if not class_name.startswith(prefix) or not class_name.endswith(suffix):
            continue
        data = binaries.get(symbol_id)
        if data is None:
            continue
        role_id = class_name[len(prefix) : -len(suffix)]
        role_names[role_id] = _limb_names_from_binary_data(data)
    return role_names


def _best_donor_limb(
    source_limb: str,
    donor_names: tuple[str, ...],
    preferred_role_id: str = "",
) -> str | None:
    source_suffix = _limb_suffix(source_limb)
    primary_candidates = _dedupe_strings(
        (
            *_DONOR_SUFFIX_CANDIDATES.get(source_limb, ()),
            *_DONOR_SUFFIX_CANDIDATES.get(source_suffix, ()),
            source_suffix,
        )
    )
    fallback_candidates = _base_suffix_candidates(source_suffix)
    by_suffix: dict[str, list[str]] = {}
    for name in donor_names:
        by_suffix.setdefault(_limb_suffix(name).lower(), []).append(name)

    target = _find_donor_limb_candidate(
        source_limb, primary_candidates, by_suffix, preferred_role_id
    )
    if target is not None:
        return target
    if _should_hide_unmatched_overlay_suffix(source_suffix):
        return ""
    return _find_donor_limb_candidate(
        source_limb, fallback_candidates, by_suffix, preferred_role_id
    )


def _find_donor_limb_candidate(
    source_limb: str,
    candidates: tuple[str, ...],
    by_suffix: Mapping[str, list[str]],
    preferred_role_id: str = "",
) -> str | None:
    fallback: str | None = None
    for candidate in candidates:
        targets = by_suffix.get(candidate.lower(), [])
        preferred = _preferred_role_limb(targets, preferred_role_id)
        if preferred is not None:
            return preferred
        if fallback is None and targets:
            fallback = targets[0]
        for target in targets:
            if len(target.encode("utf-8")) == len(source_limb.encode("utf-8")):
                return target
    return fallback


def _should_hide_unmatched_overlay_suffix(source_suffix: str) -> bool:
    return source_suffix.lower().endswith("_hfx")


def _preferred_role_limb(targets: list[str], preferred_role_id: str) -> str | None:
    if not preferred_role_id:
        return None
    prefix = f"{preferred_role_id}_".lower()
    for target in targets:
        if target.lower().startswith(prefix):
            return target
    return None


def _base_suffix_candidates(source_suffix: str) -> tuple[str, ...]:
    candidates: list[str] = []
    if "_" in source_suffix and len(source_suffix) >= 3 and source_suffix[:2].isdigit():
        base = source_suffix.split("_", 1)[0]
        candidates.append(base)
        candidates.extend(_DONOR_SUFFIX_CANDIDATES.get(base, ()))
    current = source_suffix
    while len(current) > 3 and (current[-1].isdigit() or current[-1].isupper()):
        current = current[:-1]
        if len(current) >= 3 and current[:2].isdigit():
            candidates.append(current)
            candidates.extend(_DONOR_SUFFIX_CANDIDATES.get(current, ()))
    return tuple(candidates)


def _dedupe_strings(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _source_limb_names_for_part(part_id: str, source_names: tuple[str, ...]) -> tuple[str, ...]:
    suffixes = _part_suffixes(part_id)
    if not suffixes:
        return ()
    matches = [
        name
        for name in source_names
        if any(_suffix_matches_part(_limb_suffix(name), suffix) for suffix in suffixes)
    ]
    if matches:
        return tuple(matches)
    return _PART_SOURCE_LIMBS.get(part_id, ())


def _part_suffixes(part_id: str) -> tuple[str, ...]:
    suffixes: list[str] = []
    for source_limb in _PART_SOURCE_LIMBS.get(part_id, ()):
        suffix = _limb_suffix(source_limb)
        suffixes.append(suffix)
        suffixes.extend(_DONOR_SUFFIX_CANDIDATES.get(source_limb, ()))
        suffixes.extend(_DONOR_SUFFIX_CANDIDATES.get(suffix, ()))
    return tuple(dict.fromkeys(suffixes))


def _suffix_matches_part(source_suffix: str, part_suffix: str) -> bool:
    if source_suffix == part_suffix:
        return True
    if not source_suffix.startswith(part_suffix):
        return False
    extra = source_suffix[len(part_suffix) :]
    return bool(extra) and all(ch.isalnum() or ch == "_" for ch in extra)


def _limb_suffix(limb_name: str) -> str:
    separator = _limb_name_separator(limb_name)
    if separator < 0:
        return limb_name
    return limb_name[separator + 1 :]


def _limb_name_separator(limb_name: str) -> int:
    for index in range(len(limb_name) - 1, 0, -1):
        if (
            limb_name[index] == "_"
            and index + 2 < len(limb_name)
            and limb_name[index + 1].isdigit()
            and limb_name[index + 2].isdigit()
        ):
            return index
    return -1


def _is_player_texture_role(role_id: str) -> bool:
    return role_id not in {"animal", "bg", "global", "item", "model0"} and not role_id.startswith(
        "z_"
    )


def _role_label(role_id: str) -> str:
    if role_id == "lucas":
        return "Lucas（默认）"
    return f"{role_id[:1].upper() + role_id[1:]}（{role_id}）"


def _extract_projector_swf_bytes(exe_path: Path) -> bytes:
    marker = bytes.fromhex("56 34 12 FA")
    data = exe_path.read_bytes()
    if len(data) < 8:
        raise ValueError("projector file is too small")
    length = int.from_bytes(data[-4:], "little")
    marker_start = len(data) - 8
    swf_start = marker_start - length
    if swf_start < 0 or data[marker_start : marker_start + 4] != marker:
        raise ValueError("projector marker not found")
    swf = data[swf_start:marker_start]
    if not swf.startswith((b"FWS", b"CWS", b"ZWS")):
        raise ValueError("embedded payload is not a SWF")
    return swf


def _decompress_swf_body(swf: bytes) -> bytes:
    if swf.startswith(b"CWS"):
        return swf[:8] + zlib.decompress(swf[8:])
    if swf.startswith(b"FWS"):
        return swf
    raise ValueError("unsupported SWF compression")


def _symbol_class_names(swf_body: bytes) -> tuple[str, ...]:
    return tuple(_symbol_class_map(swf_body))


def _symbol_class_map(swf_body: bytes) -> dict[str, int]:
    symbols: dict[str, int] = {}
    for tag_code, payload in _swf_tags(swf_body):
        if tag_code == 76:
            symbols.update(_read_symbol_class_map(payload))
    return symbols


def _binary_data_by_id(swf_body: bytes) -> dict[int, bytes]:
    binaries: dict[int, bytes] = {}
    for tag_code, payload in _swf_tags(swf_body):
        if tag_code == 87 and len(payload) >= 6:
            symbol_id = int.from_bytes(payload[0:2], "little")
            binaries[symbol_id] = payload[6:]
    return binaries


def _swf_tags(swf_body: bytes) -> tuple[tuple[int, bytes], ...]:
    tags: list[tuple[int, bytes]] = []
    pos = 8
    rect_bits = 5 + 4 * (swf_body[pos] >> 3)
    pos += (rect_bits + 7) // 8 + 4
    while pos < len(swf_body):
        header = int.from_bytes(swf_body[pos : pos + 2], "little")
        pos += 2
        tag_code = header >> 6
        tag_len = header & 0x3F
        if tag_len == 0x3F:
            tag_len = int.from_bytes(swf_body[pos : pos + 4], "little")
            pos += 4
        payload = swf_body[pos : pos + tag_len]
        pos += tag_len
        tags.append((tag_code, payload))
        if tag_code == 0:
            break
    return tuple(tags)


def _read_symbol_class_map(payload: bytes) -> dict[str, int]:
    count = int.from_bytes(payload[0:2], "little")
    offset = 2
    symbols: dict[str, int] = {}
    for _ in range(count):
        symbol_id = int.from_bytes(payload[offset : offset + 2], "little")
        offset += 2
        end = payload.index(0, offset)
        symbols[payload[offset:end].decode("utf-8", errors="replace")] = symbol_id
        offset = end + 1
    return symbols


def _read_symbol_class_names(payload: bytes) -> tuple[str, ...]:
    count = int.from_bytes(payload[0:2], "little")
    offset = 2
    names: list[str] = []
    for _ in range(count):
        offset += 2
        end = payload.index(0, offset)
        names.append(payload[offset:end].decode("utf-8", errors="replace"))
        offset = end + 1
    return tuple(names)


def _limb_names_from_binary_data(data: bytes) -> tuple[str, ...]:
    try:
        inflated = zlib.decompress(_read_amf3_byte_array(data))
    except (ValueError, zlib.error):
        return ()
    return tuple(
        value
        for value in _utf_strings(inflated)
        if _looks_like_limb_name(value)
    )


def _read_amf3_byte_array(data: bytes) -> bytes:
    if len(data) < 2 or data[0] != 0x0C:
        raise ValueError("expected AMF3 ByteArray")
    value, offset = _read_u29(data, 1)
    if (value & 1) == 0:
        raise ValueError("AMF3 ByteArray references are not supported")
    length = value >> 1
    return data[offset : offset + length]


def _read_u29(data: bytes, offset: int) -> tuple[int, int]:
    current = data[offset]
    offset += 1
    if current < 128:
        return current, offset
    value = (current & 0x7F) << 7
    current = data[offset]
    offset += 1
    if current < 128:
        return value | current, offset
    value = (value | (current & 0x7F)) << 7
    current = data[offset]
    offset += 1
    if current < 128:
        return value | current, offset
    value = (value | (current & 0x7F)) << 8
    current = data[offset]
    offset += 1
    return value | current, offset


def _utf_strings(data: bytes) -> tuple[str, ...]:
    values: list[str] = []
    offset = 0
    while offset <= len(data) - 3:
        if data[offset] != 0x02:
            offset += 1
            continue
        length = (data[offset + 1] << 8) | data[offset + 2]
        start = offset + 3
        end = start + length
        if end > len(data):
            offset += 1
            continue
        try:
            value = data[start:end].decode("utf-8")
        except UnicodeDecodeError:
            offset += 1
            continue
        values.append(value)
        offset = end
    return tuple(values)


def _looks_like_limb_name(value: str) -> bool:
    if "_" not in value:
        return False
    suffix = _limb_suffix(value)
    return len(suffix) >= 3 and suffix[:2].isdigit()
