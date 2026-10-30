from __future__ import annotations

import io
from typing import Any

import pytest

from hfe_character_tool.abc_vendor import amf as M
from hfe_character_tool.donor_textures import (
    LmiFile,
    SlotSwap,
    _PicInfo,
    _PictureCopier,
    _Registration,
    _reregister_frames,
    _slot_values,
    classify_limb,
    is_costume,
    plan_slot_swaps,
)


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


def _slots(entries: dict[int, str]) -> tuple[str, ...]:
    return tuple(entries.get(slot, "") for slot in range(max(entries) + 1))


FILES = {
    "iczzy_23Collar": ("collar01.png", "collar02.png"),
    "Iczzy_29Skirt": ("skirt01.png",),
    "iczzy_34HipGuard": ("hips_guard.png",),
    "Lucas_28Cape": ("arm_lower02.png", "arm_lower03.png", "arm_lower04.png"),
    "Lucas_00Head_hfx": ("head01.png", "head03.png"),
    "heater_28Cape": ("cape01.png", "cape02.png"),
}


def _plan(
    selections: dict[str, str], source: dict[int, str], donors: dict[str, dict[int, str]]
) -> tuple[SlotSwap, ...]:
    slots = _slots(source)
    return plan_slot_swaps(
        selections,
        slots,
        lambda slot: (slots[slot], *FILES.get(slots[slot], ())),
        {role: _slots(entries) for role, entries in donors.items()},
        lambda _role, limb: (limb, *FILES.get(limb, ())),
        "iczzy",
    )


def test_plan_hides_template_costume_and_swaps_borrowed_fists() -> None:
    # iczzy wears a collar, skirt and hip guard, and draws Shawn's fists.
    source = {
        0: "iczzy_00Head",
        1: "iczzy_01Chest",
        3: "iczzy_03Hips",
        7: "Shawn_07LeftFist",
        22: "icefire",
        23: "iczzy_23Collar",
        29: "Iczzy_29Skirt",
        34: "iczzy_34HipGuard",
    }
    donors = {
        "jason": {1: "jason_01Chest", 27: "jason_28Cape"},
        "heater": {3: "heater_03Hips"},
        "jenny": {7: "Jenny_07LeftFist"},
    }

    plan = _plan({"chest": "jason", "hips": "heater", "fist_left": "jenny"}, source, donors)

    assert plan == (
        SlotSwap(1, "jason", "jason_01Chest"),
        SlotSwap(3, "heater", "heater_03Hips"),
        SlotSwap(7, "jenny", "Jenny_07LeftFist"),
        SlotSwap(23, "jason", ""),
        SlotSwap(29, "jason", ""),
        SlotSwap(34, "heater", ""),
    )


def test_plan_keeps_a_body_overlay_the_donor_fills_with_something_else() -> None:
    # Lucas's slot 28 is a spare lower-arm picture; heater's slot 28 is a cape.
    source = {6: "Lucas_06LowerArm", 28: "Lucas_28Cape", 50: "Lucas_00Head_hfx"}
    donors = {"heater": {6: "heater_06LowerArm", 28: "heater_28Cape"}}

    plan = _plan({"arm_lower": "heater", "head": "heater"}, source, donors)

    # The head glow overlay goes with the head; the arm picture is not turned into a cape.
    assert plan == (
        SlotSwap(6, "heater", "heater_06LowerArm"),
        SlotSwap(50, "heater", ""),
    )


def test_plan_keeps_a_base_part_the_donor_does_not_draw() -> None:
    assert _plan({"foot": "vivian"}, {16: "Lucas_16Foot"}, {"vivian": {0: "Vivian_00Head"}}) == ()


def test_classify_limb_votes_over_name_and_pictures() -> None:
    assert classify_limb(("Lucas_28Cape", *FILES["Lucas_28Cape"])) == "arm_lower"
    assert classify_limb(("iczzy_34HipGuard", "hips_guard.png")) == "hips"
    assert classify_limb(("taylor_28Cape", "taylorhfev1hfx-NO.128.png")) == "chest"
    assert classify_limb(("raye_35jump_ball", "ball01.png")) is None
    assert is_costume(("Iczzy_29Skirt", "skirt01.png"))
    assert is_costume(("Lucas_00Head_hfx", "head01.png"))
    assert not is_costume(("Lucas_28Cape", *FILES["Lucas_28Cape"]))


def _matrix(tx: float = 0.0, ty: float = 0.0, scale: float = 1.0) -> Any:
    return M.AmfObj(
        "flash.geom.Matrix",
        {"a": scale, "b": 0.0, "c": 0.0, "d": scale, "tx": tx, "ty": ty},
    )


def _info(cx: float, cy: float, joint: tuple[float, float], size: tuple[int, int]) -> _PicInfo:
    png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + size[0].to_bytes(4, "big")
    png += size[1].to_bytes(4, "big")
    return _PicInfo("png_x/head02.png", 1.0, cx, cy, (joint,), png)


def test_registration_pins_the_donor_pivot_onto_the_template_pivot() -> None:
    registration = _Registration(None)
    template = _info(53, 40, (100, 121), (105, 99))  # pivot pixel (47, 81)
    donor = _info(41, 37, (100, 121), (139, 130))  # pivot pixel (59, 84)

    scale, tx, ty = registration.correction(template, donor, "giggs_00Head", "giggs", 1)

    assert (scale, tx, ty) == (1.0, -12.0, -3.0)
    assert (59 * scale + tx, 84 * scale + ty) == (47, 81)


def test_registration_falls_back_to_padding_when_joints_are_garbage() -> None:
    registration = _Registration(None)
    template = _info(53, 40, (100, 121), (105, 99))
    junk = _info(30, 0, (131, 400), (51, 69))  # default joint data: pivot far below the picture

    assert registration.correction(template, junk, "rudolf_16Foot", "jason", 2) == (1.0, 0.0, 0.0)


def test_reregister_frames_moves_each_matrix_once() -> None:
    shared = _matrix(10, 20, 2.0)
    frame = M.AmfObj(
        "Data.Frame",
        {
            "uz": M.AmfEcmaArray(
                3,
                {
                    "0": M.AmfObj("Data.LZ", {"i": 0.0, "p": 1.0, "m": shared}),
                    "1": M.AmfObj("Data.LZ", {"i": 0.0, "p": 1.0, "m": shared}),
                    "2": M.AmfObj("Data.LZ", {"i": 5.0, "p": 0.0, "m": _matrix(1, 1)}),
                },
            ),
            "lz": None,
        },
    )
    spt = M.AmfObj("Data.Spt", {"frame": M.AmfEcmaArray(1, {"0": frame})})

    moved = _reregister_frames(spt, lambda slot, p: (1.0, -3.0, 4.0) if slot == 0 else None)

    # m o K: the shift goes through the matrix's own scale; slot 5 is not swapped.
    assert moved == 1
    assert (shared.props["tx"], shared.props["ty"]) == (4.0, 28.0)
    assert frame.props["uz"].props["2"].props["m"].props["tx"] == 1


def _png(size: tuple[int, int], box: tuple[int, int, int, int]) -> bytes:
    image_module = pytest.importorskip("PIL.Image")
    image = image_module.new("RGBA", size, (0, 0, 0, 0))
    image.paste((90, 60, 30, 255), box)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_registration_lays_the_donor_drawing_over_the_template_drawing() -> None:
    # Template thigh: drawing at x 5..15, y 2..18, pivot (hip) top-centre at (10, 2).
    template = _PicInfo("t.png", 1.0, 0.0, 0.0, ((10.0, 2.0),), _png((20, 20), (5, 2, 15, 18)))
    # Donor thigh padded like HF-EX: same drawing at x 25..35, y 20..36, junk pivot record.
    donor = _PicInfo("d.png", 1.0, 30.0, 0.0, ((131.0, 400.0),), _png((40, 40), (25, 20, 35, 36)))

    scale, tx, ty = _Registration(None).correction(template, donor, "giggs_14UpperLeg", "giggs", 2)

    assert (scale, tx, ty) == (1.0, -20.0, -18.0)
    assert (25 + tx, 20 + ty, 35 + tx, 36 + ty) == (5, 2, 15, 18)
