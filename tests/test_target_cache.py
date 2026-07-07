from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hfe_character_tool.models import TargetGame
from hfe_character_tool.target_cache import (
    TargetCacheEntry,
    TargetCacheError,
    prepare_target_cache,
    source_kind_from_path,
    target_cache_id,
)
from hfe_character_tool.tools import ToolConfig, ToolResult


def test_prepare_target_cache_extracts_exe_without_overwriting_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path
    swf_bytes = b"FWS" + b"\x01" * 5
    source = workspace / "game.exe"
    source.write_bytes(
        b"PROJECTOR"
        + swf_bytes
        + bytes.fromhex("56 34 12 FA")
        + len(swf_bytes).to_bytes(4, "little")
    )
    original_source = source.read_bytes()
    monkeypatch.setattr("hfe_character_tool.target_cache.probe_swf", _fake_probe)

    entry = prepare_target_cache(workspace, TargetGame(source_path=str(source)), _tools(tmp_path))

    assert entry.cached_swf_path.read_bytes() == swf_bytes
    assert source.read_bytes() == original_source
    assert entry.probe_json_path.is_file()
    assert entry.can_locate_global
    assert entry.can_locate_loaders
    assert entry.item_spt_symbol_name == "Data.Global_itemSpt"
    assert entry.global_pow_character_ids == ("lucas",)
    assert entry.cache_id == target_cache_id(source, "exe")


def test_prepare_target_cache_copies_swf_and_uses_deterministic_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "game.swf"
    source.write_bytes(b"FWSswf-target")
    monkeypatch.setattr("hfe_character_tool.target_cache.probe_swf", _fake_probe)

    first = prepare_target_cache(
        tmp_path, TargetGame(source_path=str(source), source_kind="swf"), _tools(tmp_path)
    )
    second = prepare_target_cache(
        tmp_path, TargetGame(source_path=str(source), source_kind="swf"), _tools(tmp_path)
    )

    assert first.cache_id == second.cache_id
    assert first.cached_swf_path.read_bytes() == b"FWSswf-target"
    assert first.source_kind == "swf"


def test_prepare_target_cache_can_reuse_existing_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "game.swf"
    source.write_bytes(b"FWSswf-target")
    calls = 0

    def fake_probe(*args: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return _fake_probe(*args)

    monkeypatch.setattr("hfe_character_tool.target_cache.probe_swf", fake_probe)
    first = prepare_target_cache(
        tmp_path, TargetGame(source_path=str(source), source_kind="swf"), _tools(tmp_path)
    )
    monkeypatch.setattr(
        "hfe_character_tool.target_cache.probe_swf",
        lambda *_args: pytest.fail("cached GUI lookup should not probe again"),
    )

    second = prepare_target_cache(
        tmp_path,
        TargetGame(source_path=str(source), source_kind="swf"),
        _tools(tmp_path),
        reuse_existing=True,
    )

    assert calls == 1
    assert second.cache_id == first.cache_id
    assert second.raw_probe == first.raw_probe


def test_source_kind_from_path_rejects_unsupported_type(tmp_path: Path) -> None:
    with pytest.raises(TargetCacheError):
        source_kind_from_path(tmp_path / "game.txt")


def test_target_cache_entry_flags_unsafe_runtime_custom_loader(tmp_path: Path) -> None:
    cached_swf = tmp_path / "target.swf"
    cached_swf.write_bytes(b"FWS")
    entry = TargetCacheEntry(
        cache_id="bad-runtime-loader",
        source_path=tmp_path / "bad.exe",
        source_kind="exe",
        cache_dir=tmp_path,
        cached_swf_path=cached_swf,
        probe_json_path=tmp_path / "probe.json",
        raw_probe={
            "custom_loader_reports": [
                {
                    "kind": "custom_lmi",
                    "style": "runtime_loop",
                    "has_loop_progress": False,
                },
                {
                    "kind": "custom_spt",
                    "style": "runtime_loop",
                    "has_loop_progress": True,
                },
            ],
        },
    )

    assert entry.has_unsafe_runtime_custom_loader
    assert entry.unsafe_runtime_custom_loader_kinds == ("custom_spt",)


def _fake_probe(*_args: object) -> SimpleNamespace:
    return SimpleNamespace(
        raw_json={
            "classes": {"Data.Global": True},
            "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
            "multiname_constants": {"LoadFromCompressedBytes": True},
            "probe_schema_version": 5,
            "abc_data_global_classes": ["Data.Global_lucasSpt"],
            "missing_symbol_abc_classes": [],
            "global_pow_character_ids": ["lucas"],
            "custom_loader_reports": [],
            "data_global_symbols": [
                {"id": 185, "name": "Data.Global_itemSpt", "binary_size": 10},
                {"id": 371, "name": "Data.Global_lucasLmi", "binary_size": 10},
                {"id": 442, "name": "Data.Global_lucasSpt", "binary_size": 10},
            ],
        },
        compile_result=ToolResult(True, 0, "", ""),
        run_result=ToolResult(True, 0, "", ""),
    )


def _tools(tmp_path: Path) -> ToolConfig:
    return ToolConfig(
        ffdec=tmp_path / "ffdec.jar",
        hfworkshop=tmp_path / "HFWorkshop.exe",
        projector=tmp_path / "SA.exe",
        playerglobal=tmp_path / "playerglobal.swc",
        original_game=tmp_path / "original.exe",
    )
