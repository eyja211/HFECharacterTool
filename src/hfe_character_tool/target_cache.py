from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from hfe_character_tool.models import DEFAULT_TARGET_GAME, TargetGame, now_iso
from hfe_character_tool.runtime import default_target_cache_root, resource_path
from hfe_character_tool.swf_probe import PROBE_SCHEMA_VERSION, probe_swf
from hfe_character_tool.tools import ToolConfig, extract_projector_swf

TARGET_CACHE_ROOT = Path("output") / "target_cache"
TARGET_SWF_NAME = "target.swf"
PROBE_JSON_NAME = "probe.json"
MANIFEST_JSON_NAME = "manifest.json"


class TargetCacheError(Exception):
    def __init__(self, summary: str, detail: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail


@dataclass(frozen=True)
class TargetCacheEntry:
    cache_id: str
    source_path: Path
    source_kind: str
    cache_dir: Path
    cached_swf_path: Path
    probe_json_path: Path
    raw_probe: Mapping[str, Any]

    @property
    def can_locate_global(self) -> bool:
        classes = self.raw_probe.get("classes", {})
        return isinstance(classes, Mapping) and bool(classes.get("Data.Global"))

    @property
    def can_locate_loaders(self) -> bool:
        strings = self.raw_probe.get("string_constants", {})
        multinames = self.raw_probe.get("multiname_constants", {})
        return (
            isinstance(strings, Mapping)
            and isinstance(multinames, Mapping)
            and bool(strings.get("loadBinaryFileCount"))
            and bool(strings.get("loadtimeOffSet"))
            and bool(multinames.get("LoadFromCompressedBytes"))
        )

    @property
    def abc_data_global_classes(self) -> tuple[str, ...]:
        return _string_tuple(self.raw_probe.get("abc_data_global_classes"))

    @property
    def missing_symbol_abc_classes(self) -> tuple[str, ...]:
        return _string_tuple(self.raw_probe.get("missing_symbol_abc_classes"))

    @property
    def has_global_pow_character_ids(self) -> bool:
        return isinstance(self.raw_probe.get("global_pow_character_ids"), list)

    @property
    def global_pow_character_ids(self) -> tuple[str, ...]:
        return _string_tuple(self.raw_probe.get("global_pow_character_ids"))

    @property
    def global_char_list_order(self) -> tuple[str, ...]:
        return _string_tuple(self.raw_probe.get("global_char_list_order"))

    @property
    def select_char_option_ids(self) -> tuple[str, ...]:
        options = self.raw_probe.get("select_char_options", [])
        if not isinstance(options, list):
            return ()
        ids: list[str] = []
        for item in options:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                ids.append(item["id"])
        return tuple(ids)

    @property
    def custom_loader_reports(self) -> tuple[Mapping[str, Any], ...]:
        reports = self.raw_probe.get("custom_loader_reports", [])
        if not isinstance(reports, list):
            return ()
        return tuple(item for item in reports if isinstance(item, Mapping))

    @property
    def unsafe_runtime_custom_loader_kinds(self) -> tuple[str, ...]:
        kinds: list[str] = []
        for report in self.custom_loader_reports:
            if report.get("style") != "runtime_loop":
                continue
            if report.get("has_loop_progress") is not True:
                continue
            kind = report.get("kind")
            if isinstance(kind, str) and kind not in kinds:
                kinds.append(kind)
        return tuple(kinds)

    @property
    def has_unsafe_runtime_custom_loader(self) -> bool:
        return bool(self.unsafe_runtime_custom_loader_kinds)

    @property
    def data_global_symbol_names(self) -> tuple[str, ...]:
        symbols = self.raw_probe.get("data_global_symbols", [])
        if not isinstance(symbols, list):
            return ()
        names: list[str] = []
        for item in symbols:
            if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                names.append(item["name"])
        return tuple(names)

    @property
    def spt_symbol_names(self) -> tuple[str, ...]:
        return tuple(name for name in self.data_global_symbol_names if name.endswith("Spt"))

    @property
    def lmi_symbol_names(self) -> tuple[str, ...]:
        return tuple(name for name in self.data_global_symbol_names if name.endswith("Lmi"))

    @property
    def item_spt_symbol_name(self) -> str:
        for name in self.data_global_symbol_names:
            if name == "Data.Global_itemSpt":
                return name
        return ""

    @property
    def item_lmi_symbol_name(self) -> str:
        for name in self.data_global_symbol_names:
            if name == "Data.Global_itemLmi":
                return name
        return ""

    @property
    def global_dat_symbol_name(self) -> str:
        for name in self.data_global_symbol_names:
            if name == "Data.Global_globalDat":
                return name
        return ""


def target_game_for_source(source_path: str) -> TargetGame:
    source_kind = source_kind_from_path(Path(source_path))
    source_id = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:12]
    timestamp = now_iso()
    return TargetGame(
        id=f"target-{source_id}",
        source_path=source_path,
        source_kind=source_kind,
        detected_version="",
        cache_dir="",
        created_at=timestamp,
        updated_at=timestamp,
    )


def target_source_path(workspace: Path, target: TargetGame = DEFAULT_TARGET_GAME) -> Path:
    source = Path(target.source_path)
    if source.is_absolute():
        return source
    return resource_path(workspace, source)


def source_kind_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".exe":
        return "exe"
    if suffix == ".swf":
        return "swf"
    raise TargetCacheError(
        "不支持的目标游戏文件类型。",
        f"source={path}; supported=.exe,.swf",
    )


def resolve_source_kind(source_path: Path, explicit_kind: str) -> str:
    if explicit_kind in {"exe", "swf"}:
        return explicit_kind
    return source_kind_from_path(source_path)


def target_cache_id(source_path: Path, source_kind: str) -> str:
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise TargetCacheError("目标游戏文件不存在或不可读取。", str(source_path)) from exc
    identity = "|".join(
        (
            str(source_path.resolve()).lower(),
            source_kind,
            str(stat.st_mtime_ns),
            str(stat.st_size),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def prepare_target_cache(
    workspace: Path,
    target: TargetGame,
    tools: ToolConfig,
    output_root: Path | None = None,
    reuse_existing: bool = False,
) -> TargetCacheEntry:
    source_path = target_source_path(workspace, target)
    source_kind = resolve_source_kind(source_path, target.source_kind)
    if not source_path.is_file():
        raise TargetCacheError("目标游戏文件不存在。", str(source_path))
    cache_id = target_cache_id(source_path, source_kind)
    cache_root = output_root or default_target_cache_root(workspace)
    cache_dir = cache_root / cache_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_swf_path = cache_dir / TARGET_SWF_NAME
    probe_json_path = cache_dir / PROBE_JSON_NAME
    manifest_path = cache_dir / MANIFEST_JSON_NAME
    if reuse_existing:
        existing = _existing_target_cache_entry(
            cache_id,
            source_path,
            source_kind,
            cache_dir,
            cached_swf_path,
            probe_json_path,
            manifest_path,
        )
        if existing is not None:
            return existing
    if source_kind == "exe":
        extract_projector_swf(source_path, cached_swf_path)
    elif source_kind == "swf":
        shutil.copyfile(source_path, cached_swf_path)
    else:
        raise TargetCacheError("不支持的目标游戏文件类型。", source_kind)

    probe = probe_swf(cached_swf_path, tools, cache_dir, workspace)
    probe_json_path.write_text(
        json.dumps(probe.raw_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "cache_id": cache_id,
                "source_path": str(source_path),
                "source_kind": source_kind,
                "cached_swf_path": str(cached_swf_path),
                "probe_json_path": str(probe_json_path),
                "compile_returncode": probe.compile_result.returncode,
                "run_returncode": probe.run_result.returncode,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return TargetCacheEntry(
        cache_id=cache_id,
        source_path=source_path,
        source_kind=source_kind,
        cache_dir=cache_dir,
        cached_swf_path=cached_swf_path,
        probe_json_path=probe_json_path,
        raw_probe=probe.raw_json,
    )


def _existing_target_cache_entry(
    cache_id: str,
    source_path: Path,
    source_kind: str,
    cache_dir: Path,
    cached_swf_path: Path,
    probe_json_path: Path,
    manifest_path: Path,
) -> TargetCacheEntry | None:
    if not (
        cached_swf_path.is_file()
        and probe_json_path.is_file()
        and manifest_path.is_file()
    ):
        return None
    try:
        raw = json.loads(probe_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, Mapping) or not raw:
        return None
    if raw.get("probe_schema_version") != PROBE_SCHEMA_VERSION:
        return None
    if "global_pow_character_ids" not in raw:
        return None
    return TargetCacheEntry(
        cache_id=cache_id,
        source_path=source_path,
        source_kind=source_kind,
        cache_dir=cache_dir,
        cached_swf_path=cached_swf_path,
        probe_json_path=probe_json_path,
        raw_probe=raw,
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))
