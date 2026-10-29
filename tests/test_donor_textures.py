from __future__ import annotations

from typing import Any

from hfe_character_tool.abc_vendor import amf as M
from hfe_character_tool.donor_textures import (
    DonorLimb,
    LmiFile,
    _PictureCopier,
    _slot_values,
    plan_donor_limbs,
)
from hfe_character_tool.hfworkshop_catalog import TexturePart


def _pic(filename: str, embeded: bool = True, bm_ref: int = -1) -> Any:
    return M.AmfObj(
        "Data.LimbPic",
        {
            "filename": filename,
            "embeded": embeded,
            "index": 0.0,
            "refIndex": -1.0,
            "bmRefIndex": float(bm_ref),
            "j": M.AmfEcmaArray(0),
        },
    )


def _limb(name: str, slots: list[int]) -> Any:
    array = M.AmfEcmaArray(len(slots), {str(i): float(v) for i, v in enumerate(slots)})
    return M.AmfObj("Data.Limb", {"name": name, "limbPicIndex": array})


class _Donor:
    def __init__(self, table: dict[str, bytes]) -> None:
        self.table = table

    def png_for_filename(self, filename: str) -> bytes | None:
        return self.table.get(filename)


def test_lmi_round_trips_byte_exact() -> None:
    lmi = LmiFile([_pic("png_a/x.png")], [b"PNG"], [_limb("a_00Head", [0])])
    built = lmi.build()
    assert LmiFile.parse(built).build() == built


def test_copier_keeps_shared_bitmaps_and_borrows_or_drops_pixels() -> None:
    target = LmiFile([_pic("png_Lucas_Limbs/head.png")], [b"L"], [])
    donor = LmiFile(
        [
            _pic("png_Yaga/a.png"),  # 0: owns its pixels
            _pic("png_Yaga/a.png", bm_ref=0),  # 1: shares 0's bitmap
            _pic("png_Yaga/c.png"),  # 2: pixels live in another LMI
            _pic("png_Yaga/gone.png"),  # 3: pixels exist nowhere
        ],
        [b"A", None, None, None],
        [],
    )
    donor_art = _Donor({"png_Yaga/c.png": b"C"})
    copier = _PictureCopier(target, donor_art, "png_codex_tex")  # type: ignore[arg-type]

    slots = [copier.copy("yaga", donor, index) for index in (1, -1, 2, 3)]

    # Picture 1 drags its bmRef target 0 along; the empty slot stays empty.
    assert slots == [1, -1, 3, 4]
    new = target.limb_pics
    assert [p.props["filename"] for p in new[1:]] == [
        "png_codex_tex/yaga/png_Yaga_a.png",
        "png_codex_tex/yaga/png_Yaga_a.png",
        "png_codex_tex/yaga/png_Yaga_c.png",
        "png_codex_tex/yaga/png_Yaga_gone.png",
    ]
    assert new[1].props["bmRefIndex"] == 2.0  # remapped onto the copied picture 0
    assert [p.props["index"] for p in new[1:]] == [1.0, 2.0, 3.0, 4.0]
    # One picture per filename carries the pixels; the shared bitmap is not duplicated.
    assert target.pngs == [b"L", None, b"A", b"C", None]
    # An embedded picture whose pixels never arrive would stall the whole SPT's draw.
    assert new[4].props["embeded"] is False
    assert copier.copied == 4
    # Copying the same donor picture again reuses it.
    assert copier.copy("yaga", donor, 2) == 3


def test_slot_values_treat_null_as_empty() -> None:
    array = M.AmfEcmaArray(3, {"0": 5.0, "1": None, "2": -1.0})
    assert _slot_values(array) == [5, -1, -1]


def test_plan_takes_whatever_the_donor_draws_in_each_skeleton_slot() -> None:
    source: tuple[str, ...] = ("Lucas_00Head", "Lucas_01Chest", "", "", "", "Lucas_05UpperArm")
    source += ("",) * 10 + ("Lucas_16Foot",) + ("",) * 33 + ("Lucas_00Head_hfx",)
    # HF-EX saws draws raye's arms and giggs's feet and has no head overlay in slot 50.
    saws: tuple[str, ...] = ("saws_00Head", "saws_01Chest", "", "", "", "raye_05UpperArm")
    saws += ("",) * 10
    saws += ("giggs_16Foot",)

    plan = plan_donor_limbs(
        {"head": "saws", "arm_upper": "saws", "foot": "saws", "chest": "keep"},
        (
            TexturePart("head", "head", ()),
            TexturePart("arm_upper", "upper arm", ()),
            TexturePart("foot", "foot", ()),
        ),
        source,
        {"saws": saws},
        "lucas",
    )

    assert plan == (
        DonorLimb("Lucas_00Head", "saws", "saws_00Head"),
        DonorLimb("Lucas_00Head_hfx", "saws", ""),
        DonorLimb("Lucas_05UpperArm", "saws", "raye_05UpperArm"),
        DonorLimb("Lucas_16Foot", "saws", "giggs_16Foot"),
    )


def test_plan_keeps_a_base_part_the_donor_does_not_draw() -> None:
    plan = plan_donor_limbs(
        {"foot": "vivian"},
        (TexturePart("foot", "foot", ()),),
        ("",) * 16 + ("Lucas_16Foot",),
        {"vivian": ("Vivian_00Head",)},
        "lucas",
    )

    assert plan == ()
