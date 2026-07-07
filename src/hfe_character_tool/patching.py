from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from hfe_character_tool.hfworkshop_catalog import (
    limb_name_replacements,
    load_spt_action_options_from_payload,
    load_texture_parts,
    load_texture_roles,
)
from hfe_character_tool.models import InternalCharacter
from hfe_character_tool.swf_binary_symbol_patch import clone_binary_symbols
from hfe_character_tool.swf_bytearray_class_patch import add_bytearray_asset_classes
from hfe_character_tool.swf_data_extract import extract_data_global_symbol
from hfe_character_tool.swf_experience_binding_patch import patch_experience_binding
from hfe_character_tool.swf_global_metadata_patch import patch_global_character_metadata
from hfe_character_tool.swf_loadall_patch import patch_loadall_custom_arrays
from hfe_character_tool.swf_registry_entry_patch import patch_single_registry_entry
from hfe_character_tool.swf_select_char_panel_patch import patch_select_char_panel_options
from hfe_character_tool.tools import (
    ToolConfig,
    ToolResult,
    extract_projector_swf,
    wrap_with_projector,
)

SAFE_EXPERIENCE_BINDING_ROLE_ID = "lucas"
NEW_CHARACTER_INDEX = -2
NEW_SELECT_OPTION_VALUE = -2


@dataclass(frozen=True)
class ByteArrayClassPlan:
    class_name: str
    data_file: str
    source: str


@dataclass(frozen=True)
class SymbolClassEntry:
    class_name: str
    symbol_id_hint: int | None


@dataclass(frozen=True)
class PatchPlan:
    spt_class: str
    lmi_class: str
    bytearray_classes: tuple[ByteArrayClassPlan, ...]
    symbol_class_entries: tuple[SymbolClassEntry, ...]
    global_id_entries: tuple[str, ...]
    global_mapping_entries: tuple[str, ...]
    global_init_pcode: str
    spt_loader_pcode: str
    lmi_loader_pcode: str
    notes: tuple[str, ...]


@dataclass(frozen=True)
class PatchArtifacts:
    plan_path: Path
    spt_path: Path
    lmi_path: Path
    patched_swf_path: Path
    exe_path: Path
    base_swf_path: Path | None = None
    diagnostics: Mapping[str, object] | None = None


@dataclass(frozen=True)
class PatchedSwfBuildResult:
    path: Path
    diagnostics: Mapping[str, object]


def build_patch_plan(character: InternalCharacter) -> PatchPlan:
    cid = character.character_id
    spt_class = f"Data.Global_{cid}Spt"
    lmi_class = f"Data.Global_{cid}Lmi"
    spt_file = f"{cid}_spt.json"
    lmi_file = f"{cid}_lmi.json"
    return PatchPlan(
        spt_class=spt_class,
        lmi_class=lmi_class,
        bytearray_classes=(
            ByteArrayClassPlan(spt_class, spt_file, generate_bytearray_source(spt_class, spt_file)),
            ByteArrayClassPlan(lmi_class, lmi_file, generate_bytearray_source(lmi_class, lmi_file)),
        ),
        symbol_class_entries=(
            SymbolClassEntry(spt_class, None),
            SymbolClassEntry(lmi_class, None),
        ),
        global_id_entries=(cid,),
        global_mapping_entries=(f"{cid}:{spt_class}", f"{cid}:{lmi_class}"),
        global_init_pcode=generate_global_init_pcode(cid),
        spt_loader_pcode=generate_loader_pcode("spt", 351, "Data.Global_" + cid + "Spt"),
        lmi_loader_pcode=generate_loader_pcode("lmi", 121, "Data.Global_" + cid + "Lmi"),
        notes=(
            "SymbolClass id must be allocated from the target SWF at patch time.",
            "Global arrays/code still require FFDec/P-code implementation and game smoke testing.",
        ),
    )


def generate_global_init_pcode(character_id: str) -> str:
    return "\n".join(
        (
            'findproperty QName(PackageNamespace(""),"sptIds")',
            f'pushstring "{character_id}"',
            "newarray 1",
            'setproperty QName(PackageNamespace(""),"sptIds")',
            'findproperty QName(PackageNamespace(""),"sptClasses")',
            f'findpropstrict QName(PackageNamespace("Data"),"Global_{character_id}Spt")',
            f'constructprop QName(PackageNamespace("Data"),"Global_{character_id}Spt"), 0',
            "newarray 1",
            'setproperty QName(PackageNamespace(""),"sptClasses")',
            'findproperty QName(PackageNamespace(""),"lmiIds")',
            f'pushstring "{character_id}"',
            "newarray 1",
            'setproperty QName(PackageNamespace(""),"lmiIds")',
            'findproperty QName(PackageNamespace(""),"lmiClasses")',
            f'findpropstrict QName(PackageNamespace("Data"),"Global_{character_id}Lmi")',
            f'constructprop QName(PackageNamespace("Data"),"Global_{character_id}Lmi"), 0',
            "newarray 1",
            'setproperty QName(PackageNamespace(""),"lmiClasses")',
        )
    )


def generate_loader_pcode(kind: str, offset: int, class_name: str) -> str:
    if kind not in {"spt", "lmi"}:
        raise ValueError("kind must be 'spt' or 'lmi'")
    id_var = f"{kind}Ids"
    class_var = f"{kind}Classes"
    target_class = "Spt" if kind == "spt" else "LimbInfoFile"
    data_dir = "spt_data" if kind == "spt" else "lmi_data"
    extension = kind
    return "\n".join(
        (
            'getlex QName(PackageNamespace(""),"loadBinaryFileCount")',
            'getlex QName(PackageNamespace(""),"loadtimeOffSet")',
            f"pushshort {offset}",
            "add",
            f"ifne after_{kind}_custom_load",
            "pushbyte 0",
            "setlocal 14",
            f"loop_{kind}_custom_load:",
            "label",
            "getlocal 14",
            f'getlex QName(PackageNamespace(""),"{id_var}")',
            'getproperty QName(PackageNamespace(""),"length")',
            f"iflt body_{kind}_custom_load",
            f"jump after_{kind}_custom_load",
            f"body_{kind}_custom_load:",
            f'getlex QName(PackageNamespace("Data"),"{target_class}")',
            f'getlex QName(PackageNamespace(""),"{class_var}")',
            "getlocal 14",
            "getproperty MultinameL([...])",
            'getlex QName(PackageNamespace("flash.utils"),"ByteArray")',
            "astypelate",
            f'pushstring "{data_dir}/"',
            f'getlex QName(PackageNamespace(""),"{id_var}")',
            "getlocal 14",
            "getproperty MultinameL([...])",
            "add",
            f'pushstring ".{extension}"',
            "add",
            'callpropvoid QName(PackageNamespace(""),"LoadFromCompressedBytes"), 2',
            "getlocal 14",
            "increment",
            "setlocal 14",
            f"jump loop_{kind}_custom_load",
            f"after_{kind}_custom_load:",
            f"; class hint: {class_name}",
        )
    )


def generate_bytearray_source(qualified_class_name: str, data_file: str) -> str:
    package_name, class_name = qualified_class_name.rsplit(".", 1)
    return "\n".join(
        (
            f"package {package_name} {{",
            "    import flash.utils.ByteArray;",
            "",
            f"    public class {class_name} extends ByteArray {{",
            f'        [Embed(source="{data_file}", mimeType="application/octet-stream")]',
            "        private static const EmbeddedData:Class;",
            "",
            f"        public function {class_name}() {{",
            "            super();",
            "            var bytes:ByteArray = new EmbeddedData() as ByteArray;",
            "            writeBytes(bytes);",
            "            position = 0;",
            "        }",
            "    }",
            "}",
        )
    )


def generate_spt_data(character: InternalCharacter) -> dict[str, object]:
    return {
        "kind": "spt",
        "character_id": character.character_id,
        "stats": dict(character.stats),
        "skills": [skill.__dict__ for skill in character.skills],
        "item_frame_edits": [
            {
                "action_name": edit.action_name,
                "action_frame": edit.action_frame,
                "slots": [slot.__dict__ for slot in edit.slots if slot.enabled],
            }
            for edit in character.item_frame_edits
        ],
        "texture_selections": dict(character.texture_selections),
    }


def generate_lmi_data(character: InternalCharacter) -> dict[str, object]:
    return {
        "kind": "lmi",
        "character_id": character.character_id,
        "assets": [asset.file_name for asset in character.assets],
        "texture_selections": dict(character.texture_selections),
    }


def generate_patched_swf_placeholder(output_dir: Path, stem: str, plan: PatchPlan) -> Path:
    path = output_dir / f"{stem}_patched.swf"
    payload = {
        "warning": "placeholder patched SWF plan, not verified as playable",
        "plan": patch_plan_to_dict(plan),
    }
    path.write_bytes(b"FWS" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return path


def patch_plan_to_dict(plan: PatchPlan) -> dict[str, object]:
    return {
        "spt_class": plan.spt_class,
        "lmi_class": plan.lmi_class,
        "bytearray_classes": [
            {"class_name": item.class_name, "data_file": item.data_file, "source": item.source}
            for item in plan.bytearray_classes
        ],
        "symbol_class_entries": [
            {"class_name": item.class_name, "symbol_id_hint": item.symbol_id_hint}
            for item in plan.symbol_class_entries
        ],
        "global_id_entries": list(plan.global_id_entries),
        "global_mapping_entries": list(plan.global_mapping_entries),
        "global_init_pcode": plan.global_init_pcode,
        "spt_loader_pcode": plan.spt_loader_pcode,
        "lmi_loader_pcode": plan.lmi_loader_pcode,
        "notes": list(plan.notes),
    }


def build_hfe_artifacts(
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
    plan = build_patch_plan(character)
    plan_path = output_dir / f"{stem}_patch_plan.json"
    spt_path = output_dir / f"{stem}_spt.json"
    lmi_path = output_dir / f"{stem}_lmi.json"
    base_swf_path = None
    if target_swf_path is not None and target_swf_path.is_file():
        base_swf_path = output_dir / f"{stem}_base.swf"
        base_swf_path.write_bytes(target_swf_path.read_bytes())
    elif tools.original_game is not None and tools.original_game.is_file():
        base_swf_path = output_dir / f"{stem}_base.swf"
        extract_projector_swf(tools.original_game, base_swf_path)
    plan_path.write_text(
        json.dumps(patch_plan_to_dict(plan), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    spt_path.write_text(
        json.dumps(generate_spt_data(character), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lmi_path.write_text(
        json.dumps(generate_lmi_data(character), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if use_real_patching:
        if base_swf_path is None:
            raise RuntimeError("真实 SWF 补丁需要 vendor/original_game/HFE v1.0.2.exe。")
        swf_result = generate_patched_swf(
            output_dir,
            stem,
            character,
            plan,
            tools,
            base_swf_path,
            source_has_pow_metadata=source_has_pow_metadata,
            workspace=workspace,
        )
        if isinstance(swf_result, Path):
            swf_path = swf_result
            diagnostics: Mapping[str, object] | None = None
        else:
            swf_path = swf_result.path
            diagnostics = swf_result.diagnostics
        if target_source_kind == "swf":
            exe_path = swf_path
        else:
            exe_path = output_dir / f"{stem}.exe"
            wrap_with_projector(tools.projector, swf_path, exe_path)
        return PatchArtifacts(
            plan_path,
            spt_path,
            lmi_path,
            swf_path,
            exe_path,
            base_swf_path,
            diagnostics,
        )
    if not allow_placeholder_swf:
        raise RuntimeError("必须显式启用 use_real_patching 才能生成真实 patched SWF。")
    swf_path = generate_patched_swf_placeholder(output_dir, stem, plan)
    exe_path = output_dir / f"{stem}.exe"
    wrap_with_projector(tools.projector, swf_path, exe_path)
    return PatchArtifacts(plan_path, spt_path, lmi_path, swf_path, exe_path, base_swf_path)


def generate_patched_swf(
    output_dir: Path,
    stem: str,
    character: InternalCharacter,
    plan: PatchPlan,
    tools: ToolConfig,
    base_swf_path: Path,
    source_has_pow_metadata: bool | None = None,
    workspace: Path | None = None,
) -> Path | PatchedSwfBuildResult:
    cid = character.character_id
    source_role_id = character.source_role_id or "lucas"
    source_role_byte_length = len(source_role_id.encode("utf-8"))
    if not cid.isascii() or len(cid.encode("utf-8")) != source_role_byte_length:
        raise ValueError(
            "当前源模板只能导出与源角色 ID 字节长度一致的 ASCII 角色 ID；"
            f"源角色 {source_role_id} 需要 {source_role_byte_length} 个字节。"
        )
    resource_id = cid
    defense_brk0 = -abs(int(character.stats.get("defense", 0)))
    replacements = limb_name_replacements(
        workspace or output_dir.parent,
        character.texture_selections,
        load_texture_parts(workspace or output_dir.parent),
        load_texture_roles(workspace or output_dir.parent),
        source_role_id,
    )
    source_spt_class = f"Data.Global_{source_role_id}Spt"
    source_lmi_class = f"Data.Global_{source_role_id}Lmi"
    source_spt_payload = _extract_source_spt_payload(
        output_dir,
        stem,
        base_swf_path,
        source_spt_class,
        tools,
        workspace,
    )
    source_actions = load_spt_action_options_from_payload(source_spt_payload)
    binary_swf = output_dir / f"{stem}_01_binary.swf"
    abc_swf = output_dir / f"{stem}_02_abc.swf"
    registry_swf = output_dir / f"{stem}_03_registry.swf"
    loader_swf = output_dir / f"{stem}_04_loader.swf"
    metadata_swf = output_dir / f"{stem}_05_metadata.swf"
    vsselect_swf = output_dir / f"{stem}_06_vsselect.swf"
    final_swf = output_dir / f"{stem}_patched.swf"
    game_display_name = _game_safe_text(character.character_name, cid)
    game_display_name_b5 = _game_safe_text(character.character_name_zh, game_display_name)
    game_description = _game_safe_text(
        character.description or game_display_name,
        game_display_name,
    )
    source_uses_fallback_metadata = source_has_pow_metadata is False
    add_to_char_list = (
        source_has_pow_metadata is not False and not source_role_id.startswith("z_")
    )
    fallback_character_index = -1
    experience_bound_character_id = (
        SAFE_EXPERIENCE_BINDING_ROLE_ID if source_uses_fallback_metadata else source_role_id
    )

    binary_result = clone_binary_symbols(
        base_swf_path,
        binary_swf,
        tools,
        output_dir,
        source_spt_class,
        source_lmi_class,
        plan.spt_class,
        plan.lmi_class,
        target_character_id=cid,
        resource_id=resource_id,
        defense_brk0=defense_brk0,
        item_frame_edits=character.item_frame_edits,
        texture_replacements=replacements,
        workspace=workspace or output_dir.parent,
        source_character_id=source_role_id,
        spt_actions=source_actions,
    )
    _ensure_stage_success(
        f"clone {source_role_id} SPT/LMI binary symbols",
        binary_result.compile_result,
        binary_result.run_result,
    )

    abc_result = add_bytearray_asset_classes(
        binary_swf,
        abc_swf,
        tools,
        output_dir,
        source_spt_class,
        plan.spt_class,
        plan.lmi_class,
    )
    _ensure_stage_success(
        "add ByteArrayAsset ABC classes",
        abc_result.compile_result,
        abc_result.run_result,
    )

    registry_result = patch_single_registry_entry(
        abc_swf,
        registry_swf,
        tools,
        output_dir,
        cid,
        plan.spt_class,
        plan.lmi_class,
        lmi_resource_id=resource_id,
    )
    _ensure_stage_success(
        "patch Data.Global registry entry",
        registry_result.compile_result,
        registry_result.run_result,
    )

    loader_result = patch_loadall_custom_arrays(registry_swf, loader_swf, tools, output_dir)
    _ensure_stage_success(
        "patch LoadAllData custom loader",
        loader_result.compile_result,
        loader_result.run_result,
    )

    metadata_result = patch_global_character_metadata(
        loader_swf,
        metadata_swf,
        tools,
        output_dir,
        cid,
        game_display_name,
        game_display_name_b5,
        int(character.stats.get("hp", 500)),
        int(character.stats.get("mp", 500)),
        500,
        character_index=NEW_CHARACTER_INDEX,
        description=game_description,
        source_character_id=source_role_id,
        fallback_character_index=-1,
        add_to_char_list=add_to_char_list,
    )
    _ensure_stage_success(
        "patch role-list metadata",
        metadata_result.compile_result,
        metadata_result.run_result,
    )

    vsselect_result = patch_select_char_panel_options(
        metadata_swf,
        vsselect_swf,
        tools,
        output_dir,
        cid,
        game_display_name,
        option_value=NEW_SELECT_OPTION_VALUE,
        source_character_id=source_role_id,
    )
    _ensure_stage_success(
        "patch VS character select panel",
        vsselect_result.compile_result,
        vsselect_result.run_result,
    )

    exp_result = patch_experience_binding(
        vsselect_swf,
        final_swf,
        tools,
        output_dir,
        cid,
        bound_character_id=experience_bound_character_id,
    )
    _ensure_stage_success(
        "patch source-role experience binding",
        exp_result.compile_result,
        exp_result.run_result,
    )
    diagnostics = dict(
        _binary_patch_diagnostics(
            binary_result.run_result.stdout,
            source_role_id,
            cid,
            resource_id,
        )
    )
    diagnostics["texture_limb_replacements_requested"] = len(replacements)
    metadata_stdout = _parse_json_stdout(metadata_result.run_result.stdout)
    vsselect_stdout = _parse_json_stdout(vsselect_result.run_result.stdout)
    exp_stdout = _parse_json_stdout(exp_result.run_result.stdout)
    diagnostics.update(
        {
            "source_has_pow_metadata": source_has_pow_metadata,
            "add_to_char_list": add_to_char_list,
            "fallback_character_index": fallback_character_index,
            "experience_bound_character_id": experience_bound_character_id,
        }
    )
    for key in (
        "index",
        "source_pow_found",
        "used_fallback_index",
    ):
        if key in metadata_stdout:
            diagnostics[f"metadata_{key}"] = metadata_stdout[key]
    for key in (
        "option_value",
        "source_option_found",
        "used_fallback_option_value",
    ):
        if key in vsselect_stdout:
            diagnostics[f"vs_{key}"] = vsselect_stdout[key]
    if "bound_character_id" in exp_stdout:
        diagnostics["exp_bound_character_id"] = exp_stdout["bound_character_id"]
    return PatchedSwfBuildResult(final_swf, diagnostics)


def _binary_patch_diagnostics(
    stdout: str,
    source_role_id: str,
    character_id: str,
    resource_id: str,
) -> Mapping[str, object]:
    raw = _parse_json_stdout(stdout)
    diagnostics: dict[str, object] = {
        "source_role_id": source_role_id,
        "character_id": character_id,
        "resource_id": resource_id,
    }
    for key in (
        "source_character_id",
        "target_character_id",
        "target_limb_prefix",
        "spt_id_replacements",
        "spt_limb_name_replacements",
        "spt_lmi_path_replacements",
        "lmi_limb_name_replacements",
        "brk0_patches",
        "frame_item_edits",
        "generated_cre_slots",
        "fallback_pt_template_uses",
        "spt_donor_limb_name_replacements",
        "texture_replacements",
        "source_lmi_limb_names",
        "renamed_limb_names",
        "external_lmi_limb_names",
        "external_lmi_limb_aliases",
        "spt_id",
        "lmi_id",
        "spt_bytes",
        "lmi_bytes",
    ):
        if key in raw:
            diagnostics[key] = raw[key]
    return diagnostics


def _parse_json_stdout(stdout: str) -> dict[str, object]:
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


def _resource_id_for_lucas_payload(character_id: str) -> str:
    return character_id


def _game_safe_b5_text(text: str, fallback: str) -> str:
    return _game_safe_text(text, fallback)


def _game_safe_text(text: str, fallback: str) -> str:
    stripped = text.strip()
    if not stripped:
        return fallback
    try:
        stripped.encode("cp950")
    except UnicodeEncodeError:
        return fallback
    return stripped


def _extract_source_spt_payload(
    output_dir: Path,
    stem: str,
    base_swf_path: Path,
    source_spt_class: str,
    tools: ToolConfig,
    workspace: Path | None = None,
) -> Path:
    result = extract_data_global_symbol(
        base_swf_path,
        source_spt_class,
        tools,
        output_dir / f"{stem}_source_symbols",
        workspace or output_dir.parent,
    )
    _ensure_stage_success(
        "extract source SPT payload",
        result.compile_result,
        result.run_result,
    )
    if not result.payload_path.is_file():
        raise RuntimeError(f"源 SPT payload 未生成：{source_spt_class}")
    return result.payload_path


def _ensure_stage_success(stage: str, compile_result: ToolResult, run_result: ToolResult) -> None:
    if compile_result.success and run_result.success:
        return
    detail = "\n".join(
        item
        for item in (
            f"stage={stage}",
            f"compile_stderr={compile_result.stderr}",
            f"compile_stdout={compile_result.stdout}",
            f"run_stderr={run_result.stderr}",
            f"run_stdout={run_result.stdout}",
        )
        if not item.endswith("=")
    )
    raise RuntimeError(f"真实 SWF 补丁阶段失败：{detail}")
