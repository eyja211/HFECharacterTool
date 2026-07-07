from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, cast

TOOL_VERSION = "0.1.0"
DEFAULT_TARGET_GAME_ID = "builtin-hfe-v1-0-2"
DEFAULT_TARGET_GAME_SOURCE = "vendor/original_game/HFE v1.0.2.exe"
DEFAULT_TARGET_CACHE_DIR = "output/target_cache/builtin-hfe-v1-0-2"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def label(self) -> str:
        return {
            Severity.ERROR: "错误",
            Severity.WARNING: "警告",
            Severity.INFO: "提示",
        }[self]


@dataclass(frozen=True)
class ValidationIssue:
    severity: Severity
    message: str
    suggestion: str
    target: str
    technical_detail: str = ""


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is Severity.ERROR for issue in self.issues)

    def by_severity(self, severity: Severity) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is severity)


@dataclass(frozen=True)
class AssetEntry:
    file_name: str
    purpose: str
    status: str = "registered"


@dataclass(frozen=True)
class ExportRecord:
    exported_at: str
    exe_path: str
    status: str
    summary: str
    validation_report_path: str = ""
    export_log_path: str = ""


@dataclass(frozen=True)
class ItemSpawnSlot:
    enabled: bool = True
    item_action_group: int = 5
    ref: float = -1.0
    x: float = 255.0
    y: float = -121.0
    z: float = 2.0
    vx: float = 45.0
    vy: float = 0.0
    vz: float = 0.0


@dataclass(frozen=True)
class FrameItemEdit:
    action_name: str = "ball"
    action_frame: int = 8
    slots: tuple[ItemSpawnSlot, ...] = ()


DEFAULT_ITEM_FRAME_EDIT = FrameItemEdit()


@dataclass(frozen=True)
class TargetGame:
    id: str = DEFAULT_TARGET_GAME_ID
    source_path: str = DEFAULT_TARGET_GAME_SOURCE
    source_kind: str = "exe"
    detected_version: str = "HFE v1.0.2"
    cache_dir: str = DEFAULT_TARGET_CACHE_DIR
    created_at: str = ""
    updated_at: str = ""


DEFAULT_TARGET_GAME = TargetGame()


@dataclass(frozen=True)
class CharacterProject:
    project_name: str
    template_id: str
    template_version: str
    source_role_id: str
    character_id: str
    character_name: str
    description: str
    character_name_zh: str = ""
    stats: Mapping[str, int] = field(default_factory=dict)
    skills: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    item_frame_edits: tuple[FrameItemEdit, ...] = (DEFAULT_ITEM_FRAME_EDIT,)
    texture_selections: Mapping[str, str] = field(default_factory=dict)
    target_game: TargetGame = DEFAULT_TARGET_GAME
    assets: tuple[AssetEntry, ...] = ()
    exports: tuple[ExportRecord, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    tool_version: str = TOOL_VERSION

    def with_assets(self, assets: tuple[AssetEntry, ...]) -> CharacterProject:
        return replace_project(self, assets=assets)

    def with_export(self, export: ExportRecord) -> CharacterProject:
        return replace_project(self, exports=(*self.exports, export))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def replace_project(project: CharacterProject, **changes: object) -> CharacterProject:
    merged = dict(changes)
    if "updated_at" not in merged:
        merged["updated_at"] = now_iso()
    return dataclass_replace(project, **cast(Any, merged))


def project_to_dict(project: CharacterProject) -> dict[str, Any]:
    return {
        "project": {
            "name": project.project_name,
            "created_at": project.created_at,
            "updated_at": project.updated_at,
            "tool_version": project.tool_version,
        },
        "template": {
            "id": project.template_id,
            "version": project.template_version,
            "source_role_id": project.source_role_id,
        },
        "character": {
            "id": project.character_id,
            "name": project.character_name,
            "name_zh": project.character_name_zh,
            "description": project.description,
        },
        "stats": dict(project.stats),
        "skills": {name: dict(value) for name, value in project.skills.items()},
        "item_frame_edits": [_frame_item_edit_to_dict(edit) for edit in project.item_frame_edits],
        "textures": dict(project.texture_selections),
        "target_game": _target_game_to_dict(project.target_game),
        "assets": [
            {"file_name": asset.file_name, "purpose": asset.purpose, "status": asset.status}
            for asset in project.assets
        ],
        "exports": [
            {
                "exported_at": record.exported_at,
                "exe_path": record.exe_path,
                "status": record.status,
                "summary": record.summary,
                "validation_report_path": record.validation_report_path,
                "export_log_path": record.export_log_path,
            }
            for record in project.exports
        ],
    }


def project_from_dict(data: Mapping[str, Any]) -> CharacterProject:
    project = _mapping(data.get("project"))
    template = _mapping(data.get("template"))
    character = _mapping(data.get("character"))
    raw_skills = _mapping(data.get("skills"))
    raw_item_frame_edits = data.get("item_frame_edits")
    if isinstance(raw_item_frame_edits, list):
        item_frame_edits = tuple(
            _frame_item_edit_from_mapping(_mapping(edit))
            for edit in raw_item_frame_edits
        )
    else:
        item_frame_edits = _legacy_item_frame_edits(raw_skills)
    return CharacterProject(
        project_name=str(project.get("name", "")),
        template_id=str(template.get("id", "")),
        template_version=str(template.get("version", "")),
        source_role_id=str(template.get("source_role_id", "lucas")) or "lucas",
        character_id=str(character.get("id", "")),
        character_name=str(character.get("name", "")),
        character_name_zh=str(character.get("name_zh", "")),
        description=str(character.get("description", "")),
        stats=MappingProxyType(_stats_from_mapping(_mapping(data.get("stats")))),
        skills=MappingProxyType(
            {
                str(name): MappingProxyType(_skill_from_mapping(_mapping(value)))
                for name, value in raw_skills.items()
            }
        ),
        item_frame_edits=item_frame_edits,
        texture_selections=MappingProxyType(
            {str(k): str(v) for k, v in _mapping(data.get("textures")).items()}
        ),
        target_game=_target_game_from_mapping(_mapping(data.get("target_game"))),
        assets=tuple(
            AssetEntry(
                file_name=str(_mapping(asset).get("file_name", "")),
                purpose=str(_mapping(asset).get("purpose", "")),
                status=str(_mapping(asset).get("status", "registered")),
            )
            for asset in _sequence(data.get("assets"))
        ),
        exports=tuple(
            ExportRecord(
                exported_at=str(_mapping(record).get("exported_at", "")),
                exe_path=str(_mapping(record).get("exe_path", "")),
                status=str(_mapping(record).get("status", "")),
                summary=str(_mapping(record).get("summary", "")),
                validation_report_path=str(_mapping(record).get("validation_report_path", "")),
                export_log_path=str(_mapping(record).get("export_log_path", "")),
            )
            for record in _sequence(data.get("exports"))
        ),
        created_at=str(project.get("created_at", "")),
        updated_at=str(project.get("updated_at", "")),
        tool_version=str(project.get("tool_version", TOOL_VERSION)),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _sequence(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return []


def _stats_from_mapping(value: Mapping[str, Any]) -> dict[str, int]:
    stats: dict[str, int] = {}
    for key, raw in value.items():
        name = str(key)
        if name == "stamina":
            continue
        stats[name] = int(raw)
    stats.setdefault("hp", 500)
    stats.setdefault("mp", 500)
    stats.setdefault("defense", 0)
    return stats


def _skill_from_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): raw
        for key, raw in value.items()
        if str(key) not in {"item_action_group", "item_quantity"}
    }


def _frame_item_edit_to_dict(edit: FrameItemEdit) -> dict[str, Any]:
    return {
        "action_name": edit.action_name,
        "action_frame": edit.action_frame,
        "slots": [_item_spawn_slot_to_dict(slot) for slot in edit.slots if slot.enabled],
    }


def _item_spawn_slot_to_dict(slot: ItemSpawnSlot) -> dict[str, Any]:
    return {
        "item_action_group": slot.item_action_group,
        "ref": slot.ref,
        "x": slot.x,
        "y": slot.y,
        "z": slot.z,
        "vx": slot.vx,
        "vy": slot.vy,
        "vz": slot.vz,
    }


def _target_game_to_dict(target: TargetGame) -> dict[str, str]:
    return {
        "id": target.id,
        "source_path": target.source_path,
        "source_kind": target.source_kind,
        "detected_version": target.detected_version,
        "cache_dir": target.cache_dir,
        "created_at": target.created_at,
        "updated_at": target.updated_at,
    }


def _target_game_from_mapping(value: Mapping[str, Any]) -> TargetGame:
    if not value:
        return DEFAULT_TARGET_GAME
    return TargetGame(
        id=str(value.get("id", DEFAULT_TARGET_GAME.id)) or DEFAULT_TARGET_GAME.id,
        source_path=str(value.get("source_path", DEFAULT_TARGET_GAME.source_path))
        or DEFAULT_TARGET_GAME.source_path,
        source_kind=str(value.get("source_kind", DEFAULT_TARGET_GAME.source_kind))
        or DEFAULT_TARGET_GAME.source_kind,
        detected_version=str(value.get("detected_version", DEFAULT_TARGET_GAME.detected_version)),
        cache_dir=str(value.get("cache_dir", DEFAULT_TARGET_GAME.cache_dir)),
        created_at=str(value.get("created_at", "")),
        updated_at=str(value.get("updated_at", "")),
    )


def _frame_item_edit_from_mapping(value: Mapping[str, Any]) -> FrameItemEdit:
    slots = tuple(
        _item_spawn_slot_from_mapping(_mapping(slot))
        for slot in _sequence(value.get("slots"))
    )
    return FrameItemEdit(
        action_name=str(value.get("action_name", "ball")).strip() or "ball",
        action_frame=int(value.get("action_frame", 8)),
        slots=slots if "slots" in value else DEFAULT_ITEM_FRAME_EDIT.slots,
    )


def _item_spawn_slot_from_mapping(value: Mapping[str, Any]) -> ItemSpawnSlot:
    return ItemSpawnSlot(
        enabled=bool(value.get("enabled", True)),
        item_action_group=int(value.get("item_action_group", 5)),
        ref=float(value.get("ref", -1.0)),
        x=float(value.get("x", 255.0)),
        y=float(value.get("y", -121.0)),
        z=float(value.get("z", 2.0)),
        vx=float(value.get("vx", 45.0)),
        vy=float(value.get("vy", 0.0)),
        vz=float(value.get("vz", 0.0)),
    )


def _legacy_item_frame_edits(skills: Mapping[str, Any]) -> tuple[FrameItemEdit, ...]:
    skill = _mapping(skills.get("rising_slash"))
    item_action_group = int(skill.get("item_action_group", 5))
    item_quantity = int(skill.get("item_quantity", 1))
    quantity = max(1, min(item_quantity, 3))
    slots = tuple(
        ItemSpawnSlot(item_action_group=item_action_group)
        for _ in range(quantity)
    )
    return (FrameItemEdit(action_name="ball", action_frame=8, slots=slots),)


@dataclass(frozen=True)
class ProjectSummary:
    path: Path
    project_name: str
    character_id: str
    character_name: str
    updated_at: str
    last_export_status: str


@dataclass(frozen=True)
class Template:
    template_id: str
    name: str
    version: str
    required_assets: tuple[str, ...]
    default_stats: Mapping[str, int]
    skill_defaults: Mapping[str, Mapping[str, Any]]
    editable_fields: tuple[str, ...]
    mapping_note: str


@dataclass(frozen=True)
class InternalSkill:
    skill_id: str
    key: str
    mp_cost: int
    damage: int
    speed: int
    range: int


@dataclass(frozen=True)
class InternalCharacter:
    template: Template
    source_role_id: str
    character_id: str
    character_name: str
    character_name_zh: str
    description: str
    stats: Mapping[str, int]
    skills: tuple[InternalSkill, ...]
    item_frame_edits: tuple[FrameItemEdit, ...]
    assets: tuple[AssetEntry, ...]
    texture_selections: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PreviewResult:
    status: str
    primary_asset: str
    message: str


@dataclass(frozen=True)
class ExportResult:
    status: str
    summary: str
    exe_path: Path | None
    validation_report: ValidationReport
    log_path: Path | None
    validation_report_path: Path | None
    test_checklist: tuple[str, ...]
    diagnostics_path: Path | None = None
