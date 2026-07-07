from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_probe import (
    PROBE_JAVA_SOURCE,
    PROBE_SCHEMA_VERSION,
    _parse_probe_json,
    write_probe_source,
)


def test_probe_source_contains_read_only_anchors(tmp_path: Path) -> None:
    path = write_probe_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == PROBE_JAVA_SOURCE
    assert "Data.Global" in source
    assert "LoadFromCompressedBytes" in source
    assert "SymbolClassTag" in source
    assert "DefineBinaryDataTag" in source
    assert "global_method_candidates" in source
    assert "load_offset_hits" in source
    assert "loader_anchor_hits" in source
    assert "global_registry_assignments" in source
    assert "probe_schema_version" in source
    assert "global_pow_character_ids" in source
    assert "global_pow_entries" in source
    assert "global_char_list_order" in source
    assert "select_char_options" in source
    assert "collectSelectCharOptions" in source
    assert "Web_misc.SelectCharPanel" in source
    assert "custom_loader_reports" in source
    assert "collectCustomLoaderReports" in source
    assert "hasLoopProgressAfterCall" in source
    assert "customLoaderStyle" in source
    assert '\\"style\\"' in source
    assert "runtime_loop" in source
    assert "expanded" in source
    assert "custom_spt" in source
    assert "custom_lmi" in source
    assert "abc_data_global_classes" in source
    assert "missing_symbol_abc_classes" in source
    assert "data_global_symbols" in source
    assert "Data.Global_codexSpt" in source
    assert "Data.Global_codexLmi" in source
    assert "Data.Global_codexcloneSpt" in source
    assert "has_sptIds" in source
    assert "has_lmiClasses" in source
    assert "appendPlainStringArray" in source
    assert "registryFieldName" in source
    assert "collectGlobalMetadata" in source
    assert "isPowReferenceNear" in source
    assert "hasPowObjectFields" in source
    assert "binaryDataSize" in source
    assert "isPushShort(ins, 351)" in source
    assert "isPushShort(ins, 121)" in source
    assert "swf.saveTo" not in source


def test_parse_probe_json_returns_dict() -> None:
    parsed = _parse_probe_json('{"classes":{"Data.Global":true}}')

    assert parsed == {"classes": {"Data.Global": True}}
    assert _parse_probe_json("not-json") == {}


def test_probe_schema_version_requires_custom_loader_style() -> None:
    assert PROBE_SCHEMA_VERSION == 5
    assert '\\"probe_schema_version\\":5' in PROBE_JAVA_SOURCE
