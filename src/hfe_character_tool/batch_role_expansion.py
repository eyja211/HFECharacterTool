from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from hfe_character_tool.swf_binary_symbol_patch import clone_binary_symbols
from hfe_character_tool.swf_bytearray_class_patch import add_bytearray_asset_class_entries
from hfe_character_tool.swf_experience_binding_patch import (
    ExperienceBindingEntry,
    patch_experience_bindings,
)
from hfe_character_tool.swf_global_metadata_patch import (
    GlobalMetadataEntry,
    patch_global_character_metadata_entries,
)
from hfe_character_tool.swf_loadall_patch import patch_loadall_custom_arrays
from hfe_character_tool.swf_probe import SwfProbeReport, probe_swf
from hfe_character_tool.swf_registry_entry_patch import RegistryEntrySpec, patch_registry_entries
from hfe_character_tool.swf_select_char_panel_patch import (
    SelectCharPanelOptionEntry,
    patch_select_char_panel_option_entries,
)
from hfe_character_tool.tools import (
    ToolConfig,
    ToolResult,
    discover_tools,
    extract_projector_swf,
    wrap_with_source_projector,
)

EYJA_ROLES02_OUTPUT_NAME = "HF_Eyja_0.1.7_roles02_candidate.exe"


@dataclass(frozen=True)
class RoleExpansionSpec:
    source_role_id: str
    target_id: str
    display_name: str
    source_spt_class: str
    source_lmi_class: str
    source_lmi_path_id: str
    target_spt_class: str
    target_lmi_class: str
    pow_index: int
    vs_option_value: int
    experience_bound_character_id: str


@dataclass(frozen=True)
class BatchRoleExpansionArtifacts:
    base_swf_path: Path
    patched_swf_path: Path
    output_exe_path: Path
    diagnostics_path: Path
    diagnostics: Mapping[str, Any]


class _PatchStageResult(Protocol):
    @property
    def compile_result(self) -> ToolResult:
        ...

    @property
    def run_result(self) -> ToolResult:
        ...


_ROLE_MAP: tuple[tuple[str, str], ...] = (
    ("lucas", "Lucas02"),
    ("drew", "Drew02"),
    ("shawn", "Shawn02"),
    ("jenny", "Jenny02"),
    ("taylor", "Taylor02"),
    ("leo", "Leo02"),
    ("legge", "Legge02"),
    ("eason", "Eason02"),
    ("eason0", "Eason002"),
    ("vivian", "Vivian02"),
    ("yaga", "Yaga02"),
    ("jason", "Jason02"),
    ("gordon", "Gordon02"),
    ("giggs", "Giggs02"),
    ("titto", "Titto02"),
    ("sinan", "Sinan02"),
    ("iczzy", "Iczzy02"),
    ("heater", "Heater02"),
    ("raye", "Raye02"),
    ("livermore", "Livermore02"),
)


def build_eyja_roles02_specs() -> tuple[RoleExpansionSpec, ...]:
    specs: list[RoleExpansionSpec] = []
    for offset, (source_id, target_id) in enumerate(_ROLE_MAP):
        source_lmi_id = "eason" if source_id == "eason0" else source_id
        specs.append(
            RoleExpansionSpec(
                source_role_id=source_id,
                target_id=target_id,
                display_name=target_id,
                source_spt_class=f"Data.Global_{source_id}Spt",
                source_lmi_class=f"Data.Global_{source_lmi_id}Lmi",
                source_lmi_path_id=source_lmi_id,
                target_spt_class=f"Data.Global_{target_id}Spt",
                target_lmi_class=f"Data.Global_{target_id}Lmi",
                pow_index=21 + offset,
                vs_option_value=20 + offset,
                experience_bound_character_id=source_id,
            )
        )
    return tuple(specs)


def ensure_roles02_targets_absent(
    specs: Sequence[RoleExpansionSpec],
    existing_role_ids: Sequence[str],
    existing_symbol_classes: Sequence[str],
) -> None:
    existing_ids = set(existing_role_ids)
    existing_classes = set(existing_symbol_classes)
    conflicts: list[str] = []
    for spec in specs:
        if spec.target_id in existing_ids:
            conflicts.append(spec.target_id)
        for class_name in (spec.target_spt_class, spec.target_lmi_class):
            if class_name in existing_classes:
                conflicts.append(class_name)
    if conflicts:
        joined = ", ".join(dict.fromkeys(conflicts))
        raise ValueError(f"roles02 target already present in target SWF: {joined}")


def build_roles02_candidate_exe(
    workspace: Path,
    target_exe: Path,
    output_exe: Path,
    tools: ToolConfig | None = None,
    work_dir: Path | None = None,
    specs: Sequence[RoleExpansionSpec] | None = None,
    preflight_probe: bool = True,
    postflight_probe: bool = True,
) -> BatchRoleExpansionArtifacts:
    resolved_target = target_exe.resolve()
    resolved_output = output_exe.resolve()
    if resolved_target == resolved_output:
        raise ValueError("Refusing to overwrite the source EXE.")
    if not target_exe.is_file():
        raise FileNotFoundError(target_exe)

    actual_tools = tools or discover_tools(workspace)
    actual_specs = tuple(specs or build_eyja_roles02_specs())
    actual_work_dir = work_dir or output_exe.parent / "_work" / output_exe.stem
    actual_work_dir.mkdir(parents=True, exist_ok=True)
    output_exe.parent.mkdir(parents=True, exist_ok=True)

    base_swf = actual_work_dir / "00_base.swf"
    extract_projector_swf(target_exe, base_swf)

    diagnostics: dict[str, Any] = {
        "target_exe": str(target_exe),
        "output_exe": str(output_exe),
        "role_count": len(actual_specs),
        "roles": [asdict(spec) for spec in actual_specs],
    }

    if preflight_probe:
        before = _probe_or_raise(
            base_swf, actual_tools, actual_work_dir / "probe_before", workspace
        )
        _preflight_duplicate_check(before, actual_specs)
        diagnostics["before_probe"] = before.raw_json

    current_swf = base_swf
    binary_reports: list[Mapping[str, Any]] = []
    for index, spec in enumerate(actual_specs, start=1):
        binary_swf = actual_work_dir / f"{index:02d}_{spec.target_id}_binary.swf"
        binary_result = clone_binary_symbols(
            current_swf,
            binary_swf,
            actual_tools,
            actual_work_dir,
            spec.source_spt_class,
            spec.source_lmi_class,
            spec.target_spt_class,
            spec.target_lmi_class,
            target_character_id=spec.target_id,
            resource_id=spec.target_id,
            source_character_id=spec.source_role_id,
            source_lmi_path_id=spec.source_lmi_path_id,
        )
        _ensure_stage_success(f"clone binary symbols for {spec.target_id}", binary_result)
        binary_reports.append(_parse_json_stdout(binary_result.run_result.stdout))

        current_swf = binary_swf

    abc_swf = actual_work_dir / "20_roles02_abc.swf"
    abc_result = add_bytearray_asset_class_entries(
        current_swf,
        abc_swf,
        actual_tools,
        actual_work_dir,
        actual_specs[0].source_spt_class,
        entries=tuple(
            class_name
            for spec in actual_specs
            for class_name in (spec.target_spt_class, spec.target_lmi_class)
        ),
    )
    _ensure_stage_success("add roles02 ByteArray ABC classes", abc_result)
    current_swf = abc_swf

    registry_swf = actual_work_dir / "21_roles02_registry.swf"
    registry_result = patch_registry_entries(
        current_swf,
        registry_swf,
        actual_tools,
        actual_work_dir,
        entries=tuple(
            RegistryEntrySpec(
                character_id=spec.target_id,
                spt_class=spec.target_spt_class,
                lmi_class=spec.target_lmi_class,
                lmi_resource_id=spec.target_id,
            )
            for spec in actual_specs
        ),
    )
    _ensure_stage_success("patch roles02 registry entries", registry_result)

    loader_swf = actual_work_dir / "22_roles02_loader.swf"
    loader_result = patch_loadall_custom_arrays(
        registry_swf, loader_swf, actual_tools, actual_work_dir
    )
    _ensure_stage_success("patch LoadAllData once for roles02", loader_result)
    current_swf = loader_swf

    metadata_swf = actual_work_dir / "23_roles02_metadata.swf"
    metadata_result = patch_global_character_metadata_entries(
        current_swf,
        metadata_swf,
        actual_tools,
        actual_work_dir,
        entries=tuple(
            GlobalMetadataEntry(
                character_id=spec.target_id,
                display_name=spec.display_name,
                display_name_zh=spec.display_name,
                hp=500,
                mp=500,
                stamina=500,
                character_index=spec.pow_index,
                description=spec.display_name,
                source_character_id=spec.source_role_id,
                fallback_character_index=-1,
                add_to_char_list=True,
            )
            for spec in actual_specs
        ),
    )
    _ensure_stage_success("patch roles02 Global.pow metadata", metadata_result)
    current_swf = metadata_swf

    vs_swf = actual_work_dir / "24_roles02_vs.swf"
    vs_result = patch_select_char_panel_option_entries(
        current_swf,
        vs_swf,
        actual_tools,
        actual_work_dir,
        entries=tuple(
            SelectCharPanelOptionEntry(
                character_id=spec.target_id,
                display_name=spec.display_name,
                option_value=spec.vs_option_value,
                source_character_id=spec.source_role_id,
            )
            for spec in actual_specs
        ),
    )
    _ensure_stage_success("patch roles02 VS options", vs_result)
    current_swf = vs_swf

    exp_swf = actual_work_dir / "25_roles02_exp.swf"
    exp_result = patch_experience_bindings(
        current_swf,
        exp_swf,
        actual_tools,
        actual_work_dir,
        entries=tuple(
            ExperienceBindingEntry(
                character_id=spec.target_id,
                bound_character_id=spec.experience_bound_character_id,
            )
            for spec in actual_specs
        ),
    )
    _ensure_stage_success("patch roles02 experience bindings", exp_result)
    current_swf = exp_swf

    final_swf = output_exe.with_suffix(".swf")
    final_swf.write_bytes(current_swf.read_bytes())
    wrap_with_source_projector(target_exe, final_swf, output_exe)

    diagnostics["binary_reports"] = binary_reports
    diagnostics["patched_swf"] = str(final_swf)

    if postflight_probe:
        after = _probe_or_raise(final_swf, actual_tools, actual_work_dir / "probe_after", workspace)
        verify_roles02_probe(after, actual_specs)
        diagnostics["after_probe"] = after.raw_json

    diagnostics_path = output_exe.with_suffix(".diagnostics.json")
    diagnostics_path.write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return BatchRoleExpansionArtifacts(
        base_swf_path=base_swf,
        patched_swf_path=final_swf,
        output_exe_path=output_exe,
        diagnostics_path=diagnostics_path,
        diagnostics=diagnostics,
    )


def verify_roles02_probe(report: SwfProbeReport, specs: Sequence[RoleExpansionSpec]) -> None:
    raw = report.raw_json
    classes = set(_string_sequence(raw.get("abc_data_global_classes")))
    symbols = set(_symbol_class_names(raw.get("data_global_symbols")))
    missing_symbol_abc = set(_string_sequence(raw.get("missing_symbol_abc_classes")))
    pow_ids = set(_string_sequence(raw.get("global_pow_character_ids")))
    char_list = set(_string_sequence(raw.get("global_char_list_order")))
    select_options = _object_sequence(raw.get("select_char_options"))
    option_values = {
        str(item.get("id")): int(item["value"])
        for item in select_options
        if "id" in item and isinstance(item.get("value"), int)
    }

    problems: list[str] = []
    for spec in specs:
        for class_name in (spec.target_spt_class, spec.target_lmi_class):
            if class_name not in classes:
                problems.append(f"missing ABC class {class_name}")
            if class_name not in symbols:
                problems.append(f"missing SymbolClass entry {class_name}")
            if class_name in missing_symbol_abc:
                problems.append(f"SymbolClass has no ABC class {class_name}")
        if spec.target_id not in pow_ids:
            problems.append(f"missing Global.pow id {spec.target_id}")
        if spec.target_id not in char_list:
            problems.append(f"missing charListOrder id {spec.target_id}")
        if option_values.get(spec.target_id) != spec.vs_option_value:
            problems.append(f"missing VS option {spec.target_id}={spec.vs_option_value}")

    custom_loader_reports = _object_sequence(raw.get("custom_loader_reports"))
    unsafe_loaders = [
        item for item in custom_loader_reports if item.get("style") == "runtime_loop"
    ]
    if unsafe_loaders:
        problems.append("unsafe runtime custom loader loop detected")

    if problems:
        raise RuntimeError("; ".join(problems))


def _preflight_duplicate_check(
    report: SwfProbeReport, specs: Sequence[RoleExpansionSpec]
) -> None:
    raw = report.raw_json
    existing_role_ids = tuple(
        dict.fromkeys(
            (
                *_string_sequence(raw.get("global_pow_character_ids")),
                *_string_sequence(raw.get("global_char_list_order")),
            )
        )
    )
    existing_symbol_classes = tuple(
        dict.fromkeys(
            (
                *_string_sequence(raw.get("abc_data_global_classes")),
                *_symbol_class_names(raw.get("data_global_symbols")),
            )
        )
    )
    ensure_roles02_targets_absent(specs, existing_role_ids, existing_symbol_classes)


def _probe_or_raise(
    swf_path: Path, tools: ToolConfig, output_dir: Path, workspace: Path
) -> SwfProbeReport:
    report = probe_swf(swf_path, tools, output_dir, workspace)
    if not report.compile_result.success or not report.run_result.success:
        raise RuntimeError(
            "SWF probe failed: "
            f"compile={report.compile_result.stderr} run={report.run_result.stderr}"
        )
    return report


def _ensure_stage_success(stage: str, result: _PatchStageResult) -> None:
    if result.compile_result.success and result.run_result.success:
        return
    raise RuntimeError(
        "\n".join(
            (
                f"stage={stage}",
                f"compile_stdout={result.compile_result.stdout}",
                f"compile_stderr={result.compile_result.stderr}",
                f"run_stdout={result.run_result.stdout}",
                f"run_stderr={result.run_result.stderr}",
            )
        )
    )


def _parse_json_stdout(stdout: str) -> Mapping[str, Any]:
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _string_sequence(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _object_sequence(value: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    items: list[Mapping[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(item)
    return tuple(items)


def _symbol_class_names(value: object) -> tuple[str, ...]:
    names: list[str] = []
    for item in _object_sequence(value):
        name = item.get("name")
        if isinstance(name, str):
            names.append(name)
    return tuple(names)
