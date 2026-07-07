from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from hfe_character_tool.hfworkshop_catalog import (
    KEEP_SOURCE_TEXTURE_ROLE_ID,
    TexturePart,
    TextureRole,
    _best_donor_limb,
    _limb_suffix,
    limb_name_length_issues,
    limb_name_replacements,
    load_character_template_catalog,
    load_item_catalog,
    load_item_options,
    load_item_options_from_spt_json,
    load_item_options_from_spt_payload_bytes,
    load_spt_action_options,
    load_spt_action_options_from_json,
    load_spt_action_options_from_payload_bytes,
    load_spt_frame_item_slots,
    load_spt_frame_item_slots_from_payload,
    load_texture_parts,
    resolve_action_frame_index,
    resolve_action_frame_index_from_options,
    texture_replacements,
    texture_role_selections,
)
from hfe_character_tool.target_cache import TargetCacheEntry


def test_texture_catalog_groups_lucas_limbpic_filenames(tmp_path: Path) -> None:
    lmi = tmp_path / "vendor" / "HFWorkshop" / "371 - Data.Global_lucasLmi"
    lmi.mkdir(parents=True)
    _write_json(
        lmi / "LimbPic_100.json",
        {"Data.LimbPic": {"filename": "png_Lucas_Limbs/head01.png"}},
    )
    _write_json(
        lmi / "LimbPic_101.json",
        {"Data.LimbPic": {"filename": "png_Lucas_Limbs/head02.png"}},
    )
    _write_json(
        lmi / "LimbPic_12.json",
        {"Data.LimbPic": {"filename": "png_Lucas_Limbs/chest01.png"}},
    )

    parts = load_texture_parts(tmp_path)
    replacements = texture_replacements({"head": "head02.png"}, parts)

    assert [part.part_id for part in parts] == ["head", "chest"]
    assert replacements == {"png_Lucas_Limbs/head01.png": "png_Lucas_Limbs/head02.png"}


def test_texture_role_selections_keep_only_supported_non_default_roles() -> None:
    parts = (TexturePart("head", "头部", ("head01.png",)),)
    roles = (
        TextureRole(KEEP_SOURCE_TEXTURE_ROLE_ID, "keep"),
        TextureRole("lucas", "Lucas"),
        TextureRole("raye", "Raye"),
    )

    selections = texture_role_selections(
        {
            "head": "raye",
            "chest": "raye",
            "hips": "unknown",
            "foot": "lucas",
            "arm": KEEP_SOURCE_TEXTURE_ROLE_ID,
        },
        parts,
        roles,
    )

    assert selections == {"head": "raye"}


def test_texture_role_selections_allow_lucas_for_non_lucas_source() -> None:
    parts = (TexturePart("chest", "body", ("chest01.png",)),)
    roles = (
        TextureRole(KEEP_SOURCE_TEXTURE_ROLE_ID, "keep"),
        TextureRole("lucas", "Lucas"),
        TextureRole("z_iceman", "Iceman"),
    )

    selections = texture_role_selections(
        {"chest": "lucas", "head": "lucas"},
        parts,
        roles,
        default_role_id="z_iceman",
    )

    assert selections == {"chest": "lucas"}


def test_limb_name_matching_prefers_same_byte_length_candidate() -> None:
    target = _best_donor_limb(
        "Lucas_17UpperLeg",
        ("iczzy_17UpperLegRight", "iczzy_14UpperLeg"),
    )

    assert target == "iczzy_14UpperLeg"


def test_limb_name_matching_prefers_selected_role_prefix() -> None:
    target = _best_donor_limb(
        "Iceman_05UpperArm",
        ("jason_05UpperArm", "rudolf_05UpperArm"),
        preferred_role_id="jason",
    )

    assert target == "jason_05UpperArm"


def test_limb_name_matching_handles_role_ids_with_underscores() -> None:
    assert _limb_suffix("z_woman01_06LowerArm") == "06LowerArm"
    assert _limb_suffix("z_villager_03HipsA") == "03HipsA"
    assert _limb_suffix("Lucas_00Head_hfx") == "00Head_hfx"


def test_limb_name_replacements_hides_head_variants_without_donor_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hfe_character_tool.hfworkshop_catalog._load_role_limb_names",
        lambda _path: {
            "lucas": ("Lucas_00Head", "Lucas_00Head_hfx"),
            "gordon": ("Gordon_00Head",),
        },
    )
    parts = (TexturePart("head", "head", ()),)
    roles = (
        TextureRole(KEEP_SOURCE_TEXTURE_ROLE_ID, "keep"),
        TextureRole("lucas", "Lucas"),
        TextureRole("gordon", "Gordon"),
    )

    replacements = limb_name_replacements(
        tmp_path,
        {"head": "gordon"},
        parts,
        roles,
        source_role_id="lucas",
    )

    assert replacements == {
        "Lucas_00Head": "Gordon_00Head",
        "Lucas_00Head_hfx": "",
    }


def test_limb_name_replacements_maps_head_variants_when_donor_has_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hfe_character_tool.hfworkshop_catalog._load_role_limb_names",
        lambda _path: {
            "lucas": ("Lucas_00Head", "Lucas_00Head_hfx"),
            "taylor": ("taylor_00Head", "taylor_00Head_hfx"),
        },
    )
    parts = (TexturePart("head", "head", ()),)
    roles = (
        TextureRole(KEEP_SOURCE_TEXTURE_ROLE_ID, "keep"),
        TextureRole("lucas", "Lucas"),
        TextureRole("taylor", "Taylor"),
    )

    replacements = limb_name_replacements(
        tmp_path,
        {"head": "taylor"},
        parts,
        roles,
        source_role_id="lucas",
    )

    assert replacements == {
        "Lucas_00Head": "taylor_00Head",
        "Lucas_00Head_hfx": "taylor_00Head_hfx",
    }


def test_limb_name_replacements_follow_selected_source_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hfe_character_tool.hfworkshop_catalog._load_role_limb_names",
        lambda _path: {
            "z_iceman": (
                "Iceman_00Head",
                "Iceman_00HeadA",
                "Iceman_01Chest",
                "Iceman_12RightFist",
                "Iceman_14UpperLeg",
                "Iceman_16Foot",
            ),
            "lucas": (
                "Lucas_01Chest",
                "Lucas_12RightFist",
                "Lucas_14UpperLeg",
            ),
            "drew": ("Drew_00Head",),
            "legge": ("legge_15Foot",),
        },
    )
    parts = (
        TexturePart("chest", "body", ()),
        TexturePart("fist_right", "right fist", ()),
        TexturePart("leg_upper", "upper leg", ()),
        TexturePart("head", "头部", ()),
        TexturePart("foot", "脚", ()),
    )
    roles = (
        TextureRole(KEEP_SOURCE_TEXTURE_ROLE_ID, "keep"),
        TextureRole("lucas", "Lucas"),
        TextureRole("drew", "Drew"),
        TextureRole("legge", "Legge"),
    )

    replacements = limb_name_replacements(
        tmp_path,
        {
            "head": "drew",
            "chest": "lucas",
            "fist_right": "lucas",
            "leg_upper": "lucas",
            "foot": "legge",
        },
        parts,
        roles,
        source_role_id="z_iceman",
    )

    assert replacements == {
        "Iceman_00Head": "Drew_00Head",
        "Iceman_00HeadA": "Drew_00Head",
        "Iceman_01Chest": "Lucas_01Chest",
        "Iceman_12RightFist": "Lucas_12RightFist",
        "Iceman_14UpperLeg": "Lucas_14UpperLeg",
        "Iceman_16Foot": "legge_15Foot",
    }


def test_limb_name_length_issues_report_unsafe_replacements() -> None:
    issues = limb_name_length_issues(
        {
            "Lucas_00Head": "Jenny_00Head",
            "Lucas_01Chest": "Drew_01Chest",
        }
    )

    assert [(issue.source_limb, issue.target_limb) for issue in issues] == [
        ("Lucas_01Chest", "Drew_01Chest")
    ]


def test_item_catalog_reads_safe_action_groups(tmp_path: Path) -> None:
    item_spt = tmp_path / "vendor" / "HFWorkshop" / "185 - Data.Global_itemSpt" / "Spt.json"
    item_spt.parent.mkdir(parents=True)
    _write_json(
        item_spt,
        {
            "Data.Spt": {
                "actionGroup": {
                    "HFW_ArrayLenXXX": 3,
                    "5": {"name": "lucasB"},
                    "35": {"name": "swordwind"},
                    "999": {"name": "debug_only"},
                }
            }
        },
    )

    options = load_item_options(tmp_path)

    assert [(option.action_group, option.name) for option in options] == [
        (5, "lucasB"),
        (35, "swordwind"),
        (999, "debug_only"),
    ]


def test_item_catalog_reads_arbitrary_target_spt_json(tmp_path: Path) -> None:
    item_spt = tmp_path / "TargetItemSpt.json"
    _write_json(
        item_spt,
        {
            "Data.Spt": {
                "actionGroup": {
                    "HFW_ArrayLenXXX": 2,
                    "0": {"name": "rock"},
                    "88": {"name": "customOrb"},
                }
            }
        },
    )

    options = load_item_options_from_spt_json(item_spt)

    assert [(option.action_group, option.label) for option in options] == [
        (0, "0: rock"),
        (88, "88: customOrb"),
    ]


def test_target_item_catalog_reports_missing_item_spt(tmp_path: Path) -> None:
    target_cache = _target_cache(
        tmp_path,
        data_global_symbols=("Data.Global_lucasSpt", "Data.Global_lucasLmi"),
    )

    catalog = load_item_catalog(tmp_path, target_cache)

    assert not catalog.available
    assert catalog.options == ()
    assert "Data.Global_itemSpt" in catalog.unavailable_reason


def test_target_item_catalog_reports_unavailable_until_target_item_json_exists(
    tmp_path: Path,
) -> None:
    target_cache = _target_cache(
        tmp_path,
        data_global_symbols=(
            "Data.Global_lucasSpt",
            "Data.Global_lucasLmi",
            "Data.Global_itemSpt",
        ),
    )

    catalog = load_item_catalog(tmp_path, target_cache)

    assert not catalog.available
    assert catalog.options == ()
    assert "自动抽取或解析" in catalog.unavailable_reason


def test_item_catalog_reads_target_item_spt_payload_bytes() -> None:
    payload = (
        _amf_string("actionGroup")
        + b"\x08\x00\x00\x00\x02"
        + _amf_key("0")
        + _typed_object("Data.ActionGroup", _amf_string_field("name", "rock"))
        + _amf_key("88")
        + _typed_object("Data.ActionGroup", _amf_string_field("name", "customOrb"))
    )

    options = load_item_options_from_spt_payload_bytes(payload)

    assert [(option.action_group, option.label) for option in options] == [
        (0, "0: rock"),
        (88, "88: customOrb"),
    ]


def test_target_item_catalog_uses_cached_payload_when_available(tmp_path: Path) -> None:
    target_cache = _target_cache(
        tmp_path,
        data_global_symbols=(
            "Data.Global_lucasSpt",
            "Data.Global_lucasLmi",
            "Data.Global_itemSpt",
        ),
    )
    payload_path = target_cache.cache_dir / "symbols" / "Data_Global_itemSpt.payload.bin"
    payload_path.parent.mkdir()
    payload_path.write_bytes(
        _amf_key("42")
        + _typed_object("Data.ActionGroup", _amf_string_field("name", "targetOnly"))
    )

    catalog = load_item_catalog(tmp_path, target_cache)

    assert catalog.available
    assert catalog.source == payload_path
    assert [(option.action_group, option.name) for option in catalog.options] == [
        (42, "targetOnly")
    ]


def test_spt_action_catalog_resolves_action_local_frames(tmp_path: Path) -> None:
    lucas_spt = tmp_path / "vendor" / "HFWorkshop" / "442 - Data.Global_lucasSpt" / "Spt.json"
    lucas_spt.parent.mkdir(parents=True)
    _write_json(
        lucas_spt,
        {
            "Data.Spt": {
                "frame": {
                    "HFW_ArrayLenXXX": 4,
                    "0": {"name": "idle", "last": True, "cre": None},
                    "1": {"name": "ball", "last": False, "cre": None},
                    "2": {"name": "", "last": False, "cre": {"HFW_ArrayLenXXX": 1}},
                    "3": {"name": "", "last": True, "cre": None},
                }
            }
        },
    )

    actions = load_spt_action_options(tmp_path)

    assert actions[1].action_name == "ball"
    assert actions[1].frame_count == 3
    assert actions[1].item_frames == (2,)
    assert resolve_action_frame_index(tmp_path, "ball", 2) == 2


def test_spt_frame_item_slots_follow_selected_action_frame(tmp_path: Path) -> None:
    lucas_spt = tmp_path / "vendor" / "HFWorkshop" / "442 - Data.Global_lucasSpt" / "Spt.json"
    lucas_spt.parent.mkdir(parents=True)
    _write_json(
        lucas_spt,
        {
            "Data.Spt": {
                "frame": {
                    "HFW_ArrayLenXXX": 3,
                    "0": {"name": "ball", "last": False, "cre": None},
                    "1": {
                        "name": "",
                        "last": False,
                        "cre": {
                            "HFW_ArrayLenXXX": 1,
                            "0": {
                                "int1": 5.0,
                                "ref": 33.0,
                                "x": 255.0,
                                "y": -121.0,
                                "z": 2.0,
                                "vx": 45.0,
                                "vy": 0.0,
                                "vz": 0.0,
                            },
                        },
                    },
                    "2": {"name": "", "last": True, "cre": None},
                }
            }
        },
    )

    assert load_spt_frame_item_slots(tmp_path, "ball", 1) == ()
    slots = load_spt_frame_item_slots(tmp_path, "ball", 2)

    assert len(slots) == 1
    assert slots[0].item_action_group == 5
    assert slots[0].ref == 33.0
    assert slots[0].x == 255.0


def test_spt_action_catalog_hides_hfworkshop_error_markers(tmp_path: Path) -> None:
    lucas_spt = tmp_path / "vendor" / "HFWorkshop" / "442 - Data.Global_lucasSpt" / "Spt.json"
    lucas_spt.parent.mkdir(parents=True)
    _write_json(
        lucas_spt,
        {
            "Data.Spt": {
                "frame": {
                    "HFW_ArrayLenXXX": 4,
                    "0": {"name": "walk", "last": True, "cre": None},
                    "1": {"name": "//<Err>Exists@5-walk", "last": False, "cre": None},
                    "2": {"name": "", "last": True, "cre": None},
                    "3": {"name": "ball", "last": True, "cre": None},
                }
            }
        },
    )

    actions = load_spt_action_options(tmp_path)

    assert [action.action_name for action in actions] == ["walk", "ball"]


def test_spt_action_catalog_from_non_lucas_json_filters_error_markers(tmp_path: Path) -> None:
    spt = tmp_path / "raye_spt.json"
    _write_json(
        spt,
        {
            "Data.Spt": {
                "frame": {
                    "HFW_ArrayLenXXX": 3,
                    "0": {"name": "//<Err>bad", "last": True, "cre": None},
                    "1": {"name": "cast", "last": False, "cre": None},
                    "2": {"name": "", "last": True, "cre": {"HFW_ArrayLenXXX": 1}},
                }
            }
        },
    )

    actions = load_spt_action_options_from_json(spt)

    assert [action.action_name for action in actions] == ["cast"]
    assert actions[0].item_frames == (2,)


def test_spt_action_catalog_reads_binary_payload_frames() -> None:
    payload = (
        _frame("idle", last=True)
        + _frame("blast", cre_null=True)
        + _frame("", cre_null=False)
        + _frame("", last=True, cre_null=True)
    )

    actions = load_spt_action_options_from_payload_bytes(payload)

    assert [action.action_name for action in actions] == ["idle", "blast"]
    assert actions[1].first_frame_index == 1
    assert actions[1].frame_count == 3
    assert actions[1].item_frames == (2,)
    assert resolve_action_frame_index_from_options(actions, "blast", 2) == 2


def test_spt_frame_item_slots_read_binary_payload(tmp_path: Path) -> None:
    payload = (
        _frame("blast", cre_null=True)
        + _frame(
            "",
            cre_null=False,
            pt=_pt_object({"int1": 88.0, "ref": -1.0, "x": 12.0, "vx": 3.0}),
        )
        + _frame("", last=True, cre_null=True)
    )
    path = tmp_path / "source.payload.bin"
    path.write_bytes(payload)

    slots = load_spt_frame_item_slots_from_payload(path, "blast", 2)

    assert len(slots) == 1
    assert slots[0].item_action_group == 88
    assert slots[0].ref == -1.0
    assert slots[0].x == 12.0
    assert slots[0].vx == 3.0


def test_character_template_catalog_pairs_target_spt_and_lmi_symbols(tmp_path: Path) -> None:
    target_cache = _target_cache(
        tmp_path,
        data_global_symbols=(
            "Data.Global_lucasSpt",
            "Data.Global_lucasLmi",
            "Data.Global_rayeSpt",
        ),
    )

    entries = load_character_template_catalog(tmp_path, target_cache)
    by_role = {entry.role_id: entry for entry in entries}

    assert by_role["lucas"].available
    assert not by_role["raye"].available
    assert by_role["raye"].unavailable_reason == "缺少 LMI"


def test_character_template_catalog_allows_spt_lmi_roles_without_pow_metadata(
    tmp_path: Path,
) -> None:
    target_cache = _target_cache(
        tmp_path,
        data_global_symbols=(
            "Data.Global_lucasSpt",
            "Data.Global_lucasLmi",
            "Data.Global_z_woman01Spt",
            "Data.Global_z_woman01Lmi",
        ),
        global_pow_character_ids=("lucas",),
    )

    entries = load_character_template_catalog(tmp_path, target_cache)
    by_role = {entry.role_id: entry for entry in entries}

    assert by_role["lucas"].available
    assert by_role["z_woman01"].available
    assert by_role["z_woman01"].unavailable_reason == ""


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _typed_object(class_name: str, payload: bytes = b"") -> bytes:
    encoded = class_name.encode("utf-8")
    return b"\x10" + len(encoded).to_bytes(2, "big") + encoded + payload


def _amf_key(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(2, "big") + encoded


def _amf_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return b"\x02" + len(encoded).to_bytes(2, "big") + encoded


def _amf_string_field(name: str, value: str) -> bytes:
    return _amf_key(name) + _amf_string(value)


def _amf_bool_field(name: str, value: bool) -> bytes:
    return _amf_key(name) + b"\x01" + (b"\x01" if value else b"\x00")


def _amf_double_field(name: str, value: float) -> bytes:
    return _amf_key(name) + b"\x00" + struct.pack(">d", value)


def _frame(
    name: str,
    *,
    last: bool = False,
    cre_null: bool = True,
    pt: bytes = b"",
) -> bytes:
    cre_value = (
        b"\x05"
        if cre_null
        else b"\x08\x00\x00\x00\x01" + _amf_key("0") + pt + b"\x00\x00\x09"
    )
    cre = _amf_key("cre") + cre_value
    return _typed_object(
        "Data.Frame",
        _amf_string_field("name", name) + _amf_bool_field("last", last) + cre,
    )


def _pt_object(fields: dict[str, float]) -> bytes:
    defaults = {
        "int1": 5.0,
        "ref": -1.0,
        "x": 255.0,
        "y": -121.0,
        "z": 2.0,
        "vx": 45.0,
        "vy": 0.0,
        "vz": 0.0,
    }
    defaults.update(fields)
    payload = b"".join(_amf_double_field(name, value) for name, value in defaults.items())
    return _typed_object("Data.Pt", payload + b"\x00\x00\x09")


def _target_cache(
    tmp_path: Path,
    data_global_symbols: tuple[str, ...],
    global_pow_character_ids: tuple[str, ...] = ("lucas",),
) -> TargetCacheEntry:
    cache_dir = tmp_path / "output" / "target_cache" / "fake"
    cached_swf = cache_dir / "target.swf"
    probe_json = cache_dir / "probe.json"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_swf.write_bytes(b"FWSfake")
    probe_json.write_text("{}", encoding="utf-8")
    return TargetCacheEntry(
        cache_id="fake",
        source_path=tmp_path / "target.swf",
        source_kind="swf",
        cache_dir=cache_dir,
        cached_swf_path=cached_swf,
        probe_json_path=probe_json,
        raw_probe={
            "classes": {"Data.Global": True},
            "string_constants": {"loadBinaryFileCount": True, "loadtimeOffSet": True},
            "multiname_constants": {"LoadFromCompressedBytes": True},
            "probe_schema_version": 4,
            "data_global_symbols": [
                {"id": index, "name": name, "binary_size": 10}
                for index, name in enumerate(data_global_symbols)
            ],
            "global_pow_character_ids": list(global_pow_character_ids),
        },
    )
