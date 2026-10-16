from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Tuple, cast

import pytest

from hfe_character_tool.batch_role_expansion import (
    EYJA_ROLES02_OUTPUT_NAME,
    build_eyja_roles02_specs,
    build_roles02_candidate_exe,
    ensure_roles02_targets_absent,
)
from hfe_character_tool.swf_binary_symbol_patch import BINARY_SYMBOL_PATCH_JAVA_SOURCE
from hfe_character_tool.swf_global_metadata_patch import (
    GLOBAL_METADATA_PATCH_JAVA_SOURCE,
    GlobalMetadataEntry,
)
from hfe_character_tool.swf_select_char_panel_patch import SelectCharPanelOptionEntry
from hfe_character_tool.tools import ToolConfig, ToolResult


def test_eyja_roles02_spec_matches_requested_twenty_roles() -> None:
    specs = build_eyja_roles02_specs()

    assert [(spec.source_role_id, spec.target_id, spec.display_name) for spec in specs] == [
        ("lucas", "Lucas02", "Lucas02"),
        ("drew", "Drew02", "Drew02"),
        ("shawn", "Shawn02", "Shawn02"),
        ("jenny", "Jenny02", "Jenny02"),
        ("taylor", "Taylor02", "Taylor02"),
        ("leo", "Leo02", "Leo02"),
        ("legge", "Legge02", "Legge02"),
        ("eason", "Eason02", "Eason02"),
        ("eason0", "Eason002", "Eason002"),
        ("vivian", "Vivian02", "Vivian02"),
        ("yaga", "Yaga02", "Yaga02"),
        ("jason", "Jason02", "Jason02"),
        ("gordon", "Gordon02", "Gordon02"),
        ("giggs", "Giggs02", "Giggs02"),
        ("titto", "Titto02", "Titto02"),
        ("sinan", "Sinan02", "Sinan02"),
        ("iczzy", "Iczzy02", "Iczzy02"),
        ("heater", "Heater02", "Heater02"),
        ("raye", "Raye02", "Raye02"),
        ("livermore", "Livermore02", "Livermore02"),
    ]
    assert [spec.pow_index for spec in specs] == list(range(21, 41))
    assert [spec.vs_option_value for spec in specs] == list(range(20, 40))
    assert EYJA_ROLES02_OUTPUT_NAME == "HF_Eyja_0.1.7_roles02_candidate.exe"


def test_eyja_roles02_spec_separates_eason0_spt_from_eason_lmi_donor() -> None:
    specs = build_eyja_roles02_specs()
    by_source = {spec.source_role_id: spec for spec in specs}

    for source_id, spec in by_source.items():
        assert spec.source_spt_class == f"Data.Global_{source_id}Spt"
        assert spec.target_spt_class == f"Data.Global_{spec.target_id}Spt"
        assert spec.target_lmi_class == f"Data.Global_{spec.target_id}Lmi"
        assert spec.experience_bound_character_id == source_id

    eason0 = by_source["eason0"]
    assert eason0.target_id == "Eason002"
    assert eason0.source_lmi_class == "Data.Global_easonLmi"
    assert eason0.source_lmi_path_id == "eason"

    for source_id, spec in by_source.items():
        if source_id != "eason0":
            assert spec.source_lmi_class == f"Data.Global_{source_id}Lmi"
            assert spec.source_lmi_path_id == source_id


def test_eyja_roles02_duplicate_prevention_checks_ids_and_classes() -> None:
    specs = build_eyja_roles02_specs()

    with pytest.raises(ValueError, match="Lucas02"):
        ensure_roles02_targets_absent(
            specs,
            existing_role_ids=("lucas", "Lucas02"),
            existing_symbol_classes=(),
        )

    with pytest.raises(ValueError, match="Data.Global_Eason002Lmi"):
        ensure_roles02_targets_absent(
            specs,
            existing_role_ids=("lucas",),
            existing_symbol_classes=("Data.Global_Eason002Lmi",),
        )


def test_binary_symbol_patch_supports_distinct_source_lmi_path_id() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE

    assert "sourceLmiPathId" in source
    assert '"lmi_data/" + sourceLmiPathId + ".lmi"' in source
    assert "validateResourceId(" not in source
    assert "same byte length as the selected source role" not in source


def test_global_metadata_patch_clones_source_pow_shape_when_available() -> None:
    source = GLOBAL_METADATA_PATCH_JAVA_SOURCE

    assert "cloneSourcePowEntry" in source
    assert "findSourcePowRoleIdIndex" in source
    assert "addPowEntryFromSource" in source
    assert "replaceClonedPowIndex" in source


def test_roles02_candidate_pipeline_batches_registry_and_loader_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _fake_tools(tmp_path)
    target_exe = tmp_path / "HF Eyja 0.1.7.exe"
    target_exe.write_bytes(b"PROJECTOR")
    output_exe = tmp_path / "output" / EYJA_ROLES02_OUTPUT_NAME
    calls: list[tuple[str, str, tuple[object, ...], dict[str, object]]] = []

    def fake_extract_projector_swf(exe_path: Path, swf_path: Path) -> None:
        calls.append(("extract", swf_path.name, (exe_path,), {}))
        swf_path.parent.mkdir(parents=True, exist_ok=True)
        swf_path.write_bytes(b"FWSbase")

    def fake_stage(name: str) -> object:
        def stage(*args: object, **kwargs: object) -> SimpleNamespace:
            output_swf = args[1]
            assert isinstance(output_swf, Path)
            calls.append((name, output_swf.name, args, kwargs))
            output_swf.write_bytes(f"FWS{name}".encode("ascii"))
            stdout = ""
            if name == "binary":
                stdout = json.dumps(
                    {
                        "source_character_id": kwargs["source_character_id"],
                        "target_character_id": kwargs["target_character_id"],
                    }
                )
            return _success_stage_result(stdout)

        return stage

    def fake_batch(name: str, expected_entries: int) -> object:
        def stage(*args: object, **kwargs: object) -> SimpleNamespace:
            entries = kwargs["entries"]
            assert isinstance(entries, tuple)
            assert len(entries) == expected_entries
            output_swf = args[1]
            assert isinstance(output_swf, Path)
            calls.append((name, output_swf.name, args, kwargs))
            output_swf.write_bytes(f"FWS{name}".encode("ascii"))
            return _success_stage_result()

        return stage

    def fake_wrap_with_source_projector(
        _source_exe: Path, swf_path: Path, exe_path: Path
    ) -> None:
        calls.append(("wrap", exe_path.name, (swf_path,), {}))
        exe_path.parent.mkdir(parents=True, exist_ok=True)
        exe_path.write_bytes(b"EXE")

    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.extract_projector_swf",
        fake_extract_projector_swf,
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.clone_binary_symbols",
        fake_stage("binary"),
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.add_bytearray_asset_class_entries",
        fake_batch("abc_batch", 40),
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.patch_registry_entries",
        fake_batch("registry_batch", 20),
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.patch_loadall_custom_arrays",
        fake_stage("loader"),
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.patch_global_character_metadata_entries",
        fake_batch("metadata_batch", 20),
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.patch_select_char_panel_option_entries",
        fake_batch("vs_batch", 20),
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.patch_experience_bindings",
        fake_batch("exp_batch", 20),
    )
    monkeypatch.setattr(
        "hfe_character_tool.batch_role_expansion.wrap_with_source_projector",
        fake_wrap_with_source_projector,
    )

    artifacts = build_roles02_candidate_exe(
        tmp_path,
        target_exe,
        output_exe,
        tools,
        preflight_probe=False,
        postflight_probe=False,
    )

    assert artifacts.output_exe_path == output_exe
    assert output_exe.read_bytes() == b"EXE"
    assert [call[0] for call in calls].count("abc_batch") == 1
    assert [call[0] for call in calls].count("registry_batch") == 1
    assert [call[0] for call in calls].count("metadata_batch") == 1
    assert [call[0] for call in calls].count("vs_batch") == 1
    assert [call[0] for call in calls].count("exp_batch") == 1
    assert [call[0] for call in calls].count("loader") == 1
    assert [call[0] for call in calls].count("binary") == 20
    assert [call[0] for call in calls].count("abc") == 0
    assert [call[0] for call in calls].count("metadata") == 0
    assert [call[0] for call in calls].count("vsselect") == 0
    assert [call[0] for call in calls].count("expbind") == 0

    binary_calls = [call for call in calls if call[0] == "binary"]
    eason0_call = binary_calls[8]
    assert eason0_call[3]["source_character_id"] == "eason0"
    assert eason0_call[3]["source_lmi_path_id"] == "eason"
    assert eason0_call[2][4] == "Data.Global_eason0Spt"
    assert eason0_call[2][5] == "Data.Global_easonLmi"

    raw_metadata_entries = [
        call[3]["entries"] for call in calls if call[0] == "metadata_batch"
    ][0]
    metadata_entries = cast(
        Tuple[GlobalMetadataEntry, ...],
        raw_metadata_entries,
    )
    assert [entry.character_index for entry in metadata_entries] == list(range(21, 41))
    raw_vs_entries = [call[3]["entries"] for call in calls if call[0] == "vs_batch"][0]
    vs_entries = cast(
        Tuple[SelectCharPanelOptionEntry, ...],
        raw_vs_entries,
    )
    assert [entry.option_value for entry in vs_entries] == list(range(20, 40))


def _success_stage_result(stdout: str = "") -> SimpleNamespace:
    result = ToolResult(True, 0, stdout, "")
    return SimpleNamespace(compile_result=result, run_result=result)


def _fake_tools(tmp_path: Path) -> ToolConfig:
    projector = tmp_path / "vendor" / "projector" / "SA.exe"
    projector.parent.mkdir(parents=True)
    projector.write_bytes(b"PROJECTOR")
    return ToolConfig(
        ffdec=tmp_path / "vendor" / "FFDec" / "ffdec.jar",
        hfworkshop=tmp_path / "vendor" / "HFWorkshop" / "HFWorkshop.exe",
        projector=projector,
        playerglobal=tmp_path / "vendor" / "playerGlobal" / "playerglobal.swc",
        original_game=None,
    )
