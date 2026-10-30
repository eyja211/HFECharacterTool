"""Give a new role another role's body-part textures, taken from HF-EX.

A cloned SPT draws every limb with the uz/lz matrices baked against the template role's own
pictures. HF-EX ships every stock role re-cut to Lucas's skeleton, so its pictures fit those
skeletons; the HFE originals are cut differently (yaga's head inside his chest picture, jason's
feet inside his legs, ...) and land detached from the body whatever the matrices say.

Every changed skeleton slot gets a limb private to the new role (the donor's HF-EX pictures,
copied into the new role's own LMI under role-private filenames, since pixels resolve through a
global filename table), and the slot's spriteLimb is renamed to it. Renaming the slot rather
than rewriting the template's limb also covers templates that borrow another role's limb there
(iczzy draws Shawn's fists). Finally the matrices drawing those slots are re-registered for the
HF-EX pictures (see _Registration); the poses themselves do not change.
"""

from __future__ import annotations

import contextlib
import io
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hfe_character_tool.abc_vendor import amf as M
from hfe_character_tool.abc_vendor import swf as S
from hfe_character_tool.hfworkshop_catalog import (
    DONOR_ART_RELATIVE,
    KEEP_SOURCE_TEXTURE_ROLE_ID,
    TexturePart,
)
from hfe_character_tool.runtime import resource_path

# Engine skeleton slots (spriteLimb index) that make up each texture part. Every other body
# slot is an overlay (hair, collar, cape, skirt, hip guard, a spare arm picture, ...) whose part
# is read off its pictures; the effect slots are never touched.
PART_SLOTS: dict[str, tuple[int, ...]] = {
    "head": (0,),
    "chest": (1,),
    "hips": (3,),
    "arm_upper": (5, 10),
    "arm_lower": (6, 11),
    "fist_left": (7, 8),
    "fist_right": (12, 13),
    "leg_upper": (14, 17),
    "leg_lower": (15, 18),
    "foot": (16, 19),
    "sword": (20, 41),
}
EFFECT_SLOTS = frozenset({22, 26, 43, 44})
_PART_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fist_left", ("fist_left", "leftfist")),
    ("fist_right", ("fist_right", "rightfist")),
    ("arm_upper", ("arm_upper", "upperarm", "shoulder")),
    ("arm_lower", ("arm_lower", "lowerarm")),
    ("leg_upper", ("leg_upper", "upperleg")),
    ("leg_lower", ("leg_lower", "lowerleg")),
    ("foot", ("foot",)),
    ("hips", ("hip",)),
    ("chest", ("chest", "collar", "cape", "skirt", "belt", "wing")),
    ("head", ("head", "hair", "face")),
    ("sword", ("sword", "knife", "blade", "bow", "spear", "chain")),
)
# Overlays that are costume rather than body: when the new donor has nothing like them in that
# slot they are hidden instead of left floating (iczzy's skirt next to jason's vest).
_COSTUME_KEYWORDS = ("hair", "collar", "cape", "skirt", "belt", "wing", "shoulder", "guard")


@dataclass(frozen=True)
class SlotSwap:
    slot: int
    donor_role: str
    donor_limb: str  # "" hides the slot


@dataclass(frozen=True)
class DonorTextureResult:
    output_swf: Path
    swapped_slots: tuple[SlotSwap, ...]
    hidden_slots: tuple[int, ...]
    skipped: tuple[str, ...]
    copied_pictures: int
    moved_matrices: int = 0
    aligned_pictures: int = 0
    padded_pictures: int = 0


@dataclass
class LmiFile:
    limb_pics: list[Any]
    pngs: list[bytes | None]
    limbs: list[Any]

    @classmethod
    def parse(cls, stream: bytes) -> LmiFile:
        reader = M.StreamReader(stream)
        if reader.utf() != "limbInfo":
            raise ValueError("bad LMI header")
        count = reader.i32()
        pics = [M.amf0_decode(reader.obj_bytes()) for _ in range(count)]
        pngs = [reader.obj_bytes() for _ in range(count)]
        limbs = [M.amf0_decode(reader.obj_bytes()) for _ in range(reader.i32())]
        if not reader.eof():
            raise ValueError("trailing bytes in LMI stream")
        return cls(pics, pngs, limbs)

    def build(self) -> bytes:
        writer = M.StreamWriter()
        writer.utf("limbInfo")
        writer.i32(len(self.limb_pics))
        for pic in self.limb_pics:
            writer.obj_bytes(M.amf0_encode(pic))
        for png in self.pngs:
            writer.obj_bytes(png)
        writer.i32(len(self.limbs))
        for limb in self.limbs:
            writer.obj_bytes(M.amf0_encode(limb))
        return bytes(writer.out)

    def limb(self, name: str) -> Any | None:
        for limb in self.limbs:
            if limb.props.get("name") == name:
                return limb
        return None


class GameData:
    """Lazily parsed LMIs and SPT slot tables of one game (HF-EX, the HFE reference, a target)."""

    def __init__(self, swf: Any) -> None:
        self._swf = swf
        self._lmis: dict[str, LmiFile | None] = {}
        self._png_by_filename: dict[str, bytes] | None = None

    @classmethod
    def from_exe(cls, exe_path: Path) -> GameData:
        _prefix, swf_bytes = S.read_projector(str(exe_path))
        return cls(S.SwfFile.parse(swf_bytes))

    def lmi(self, role_id: str) -> LmiFile | None:
        if role_id not in self._lmis:
            try:
                payload = self._swf.binary_payload(f"Data.Global_{role_id}Lmi")
            except KeyError:
                self._lmis[role_id] = None
            else:
                self._lmis[role_id] = LmiFile.parse(M.unwrap_payload(payload))
        return self._lmis[role_id]

    def role_ids(self) -> list[str]:
        prefix, suffix = "Data.Global_", "Lmi"
        return sorted(
            name[len(prefix) : -len(suffix)]
            for name in self._swf.class_to_char_id()
            if name.startswith(prefix) and name.endswith(suffix)
        )

    def find_limb(
        self, name: str, donor_role: str, search_all: bool = True
    ) -> tuple[str, LmiFile, Any] | None:
        """The LMI that defines a limb the donor's SPT draws (often another role's)."""
        owner = name.rsplit("_", 1)[0].lower() if "_" in name else ""
        owners = [role for role in self.role_ids() if role.lower() == owner]
        others = [role for role in self.role_ids() if role not in (donor_role, *owners)]
        for role_id in (donor_role, *owners, *(others if search_all else ())):
            lmi = self.lmi(role_id)
            limb = lmi.limb(name) if lmi else None
            if lmi is not None and limb is not None:
                return role_id, lmi, limb
        return None

    def limb_texts(self, name: str, donor_role: str) -> tuple[str, ...]:
        found = self.find_limb(name, donor_role)
        return (name, *(_limb_filenames(found[1], found[2]) if found else ()))

    def sprite_slots(self, role_id: str) -> tuple[str, ...]:
        try:
            payload = self._swf.binary_payload(f"Data.Global_{role_id}Spt")
        except KeyError:
            return ()
        return sprite_limb_names(M.unwrap_payload(payload))

    def png_for_filename(self, filename: str) -> bytes | None:
        # Pixels are global by filename in the engine, so a picture may borrow them from
        # any LMI of the game it was authored in.
        if self._png_by_filename is None:
            table: dict[str, bytes] = {}
            for class_name in self._swf.class_to_char_id():
                if class_name.startswith("Data.Global_") and class_name.endswith("Lmi"):
                    lmi = self.lmi(class_name[len("Data.Global_") : -len("Lmi")])
                    for pic, png in zip(lmi.limb_pics, lmi.pngs) if lmi else ():
                        if png is not None and pic.props.get("embeded"):
                            table[_norm(pic.props.get("filename"))] = png
            self._png_by_filename = table
        return self._png_by_filename.get(filename)


def classify_limb(texts: tuple[str, ...]) -> str | None:
    """Texture part a limb belongs to, by majority over its name and picture filenames."""
    votes: dict[str, int] = {}
    for text in texts:
        lowered = text.lower()
        for part, keywords in _PART_KEYWORDS:
            if any(keyword in lowered for keyword in keywords):
                votes[part] = votes.get(part, 0) + 1
                break
    return max(votes, key=lambda part: votes[part]) if votes else None


def is_costume(texts: tuple[str, ...]) -> bool:
    """Name first, then picture filenames; the pictures decide (Lucas_28Cape holds arms)."""
    name, pictures = (texts[0].lower(), texts[1:]) if texts else ("", ())
    if name.endswith("_hfx"):
        return True
    judged = pictures or (name,)
    hits = sum(1 for text in judged if any(word in text.lower() for word in _COSTUME_KEYWORDS))
    return hits * 2 > len(judged)


def plan_slot_swaps(
    selections: Mapping[str, str],
    source_slots: tuple[str, ...],
    source_texts: Callable[[int], tuple[str, ...]],
    donor_slots: Mapping[str, tuple[str, ...]],
    donor_texts: Callable[[str, str], tuple[str, ...]],
    source_role_id: str,
) -> tuple[SlotSwap, ...]:
    """Which skeleton slots of the template take which donor limb.

    A slot takes whatever limb the donor's own HF-EX SPT draws in that slot: many roles borrow
    arms, fists or feet from another role (saws draws raye's arms, jason giggs's feet), so this
    is what the donor really looks like. An overlay slot only takes a donor limb of the same
    part (Lucas's slot 28 is a spare arm picture, heater's is a cape); otherwise a costume
    overlay is hidden and a body overlay kept.
    """
    primary = {slot: part for part, slots in PART_SLOTS.items() for slot in slots}
    swaps: list[SlotSwap] = []
    for slot, name in enumerate(source_slots):
        if not name or slot in EFFECT_SLOTS:
            continue
        part = primary[slot] if slot in primary else classify_limb(source_texts(slot))
        role = selections.get(part or "")
        if part is None or role is None or role in {KEEP_SOURCE_TEXTURE_ROLE_ID, source_role_id}:
            continue
        slots = donor_slots.get(role, ())
        donor = slots[slot] if slot < len(slots) else ""
        if donor and (slot in primary or classify_limb(donor_texts(role, donor)) == part):
            swaps.append(SlotSwap(slot, role, donor))
        elif slot not in primary and is_costume(source_texts(slot)):
            swaps.append(SlotSwap(slot, role, ""))
    return tuple(swaps)


_SLOT_CATALOG: dict[tuple[str, int, int], dict[str, tuple[str, ...]]] = {}


def _catalog_key(workspace: Path) -> tuple[str, int, int] | None:
    path = resource_path(workspace, DONOR_ART_RELATIVE)
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def donor_slot_catalog(workspace: Path) -> dict[str, tuple[str, ...]]:
    """Role -> the limb its HF-EX SPT draws in each skeleton slot. Parsing takes seconds."""
    key = _catalog_key(workspace)
    if key is None:
        return {}
    cached = _SLOT_CATALOG.get(key)
    if cached is None:
        donor = GameData.from_exe(Path(key[0]))
        cached = {role_id: donor.sprite_slots(role_id) for role_id in donor.role_ids()}
        _SLOT_CATALOG[key] = cached
    return cached


def warm_donor_catalog(workspace: Path) -> None:
    # On failure the editor just skips the "role has no such part" hints.
    with contextlib.suppress(OSError, ValueError, zlib.error):
        donor_slot_catalog(workspace)


def donor_catalog_ready(workspace: Path) -> bool:
    key = _catalog_key(workspace)
    return key is not None and key in _SLOT_CATALOG


def texture_parts_without_donor(
    workspace: Path,
    selections: Mapping[str, str],
    parts: tuple[TexturePart, ...],
    source_role_id: str = "lucas",
) -> tuple[str, ...]:
    """Parts whose chosen role draws nothing in the part's slots, so they keep the template."""
    catalog = donor_slot_catalog(workspace)
    source_slots = catalog.get(source_role_id) or catalog.get("lucas", ())
    part_ids = {part.part_id for part in parts}
    missing: list[str] = []
    for part_id, role_id in selections.items():
        if part_id not in part_ids or role_id in {KEEP_SOURCE_TEXTURE_ROLE_ID, source_role_id}:
            continue
        donor_slots = catalog.get(role_id, ())
        if not any(
            slot < len(source_slots)
            and source_slots[slot]
            and slot < len(donor_slots)
            and donor_slots[slot]
            for slot in PART_SLOTS.get(part_id, ())
        ):
            missing.append(part_id)
    return tuple(missing)


def sprite_limb_names(spt_stream: bytes) -> tuple[str, ...]:
    """spriteLimb[i].limbName of an inflated SPT stream, without decoding its frames."""
    reader = M.StreamReader(spt_stream)
    if reader.utf() != "Spt":
        raise ValueError("bad SPT header")
    body = reader.obj_bytes()
    amf = M.Amf0Reader(body)
    if amf.u8() == 0x10:
        amf.utf()
        amf.refs.append(None)
        if amf.utf() == "spriteLimb":
            return _limb_names(amf.read())
    return _limb_names(M.amf0_decode(body).props.get("spriteLimb"))


def _limb_names(sprite_limbs: Any) -> tuple[str, ...]:
    return tuple(
        str(limb.props.get("limbName") or "") if limb is not None else ""
        for limb in _array_items(sprite_limbs)
    )


def apply_donor_textures(
    input_swf: Path,
    output_swf: Path,
    donor_exe: Path,
    selections: Mapping[str, str],
    source_spt_class: str,
    target_spt_class: str,
    target_lmi_class: str,
    source_role_id: str,
    resource_id: str,
    reference_exe: Path,
) -> DonorTextureResult:
    data = input_swf.read_bytes()
    if not selections:
        output_swf.write_bytes(data)
        return DonorTextureResult(output_swf, (), (), (), 0)
    swf = S.SwfFile.parse(data)
    target_lmi = LmiFile.parse(M.unwrap_payload(swf.binary_payload(target_lmi_class)))
    spt_stream = M.unwrap_payload(swf.binary_payload(target_spt_class))
    spt = SptFile.parse(spt_stream)
    if spt.build() != spt_stream:
        raise ValueError(f"{target_spt_class} does not re-encode byte for byte; not editing it")
    sprite_limbs = _array_items(spt.obj.props.get("spriteLimb"))
    cloned_slots = _limb_names(spt.obj.props.get("spriteLimb"))
    source_slots = sprite_limb_names(M.unwrap_payload(swf.binary_payload(source_spt_class)))

    def source_texts(slot: int) -> tuple[str, ...]:
        limb = target_lmi.limb(cloned_slots[slot]) if slot < len(cloned_slots) else None
        name = source_slots[slot]
        return (name, *(_limb_filenames(target_lmi, limb) if limb is not None else ()))

    donor = GameData.from_exe(donor_exe)
    target = GameData(swf)
    reference = GameData.from_exe(reference_exe) if reference_exe.is_file() else None
    donor_slots = {role_id: donor.sprite_slots(role_id) for role_id in set(selections.values())}
    plan = plan_slot_swaps(
        selections, source_slots, source_texts, donor_slots, donor.limb_texts, source_role_id
    )

    swapped: list[SlotSwap] = []
    hidden: list[int] = []
    skipped: list[str] = []
    private: dict[str, str] = {}
    donor_home: dict[str, tuple[str, LmiFile, Any]] = {}
    copier = _PictureCopier(target_lmi, donor, f"png_{resource_id}_tex")
    for swap in plan:
        if swap.donor_limb == "":
            _rename_slot(spt.obj, sprite_limbs, swap.slot, "")
            hidden.append(swap.slot)
            continue
        name = private.get(swap.donor_limb)
        if name is None:
            found = donor.find_limb(swap.donor_limb, swap.donor_role)
            if found is None:
                skipped.append(f"slot {swap.slot}: no LMI defines {swap.donor_limb}")
                continue
            home_role, donor_lmi, donor_limb = found
            donor_home[swap.donor_limb] = found
            name = f"{resource_id}_tex_{swap.donor_limb}"
            limb = M.deep_clone(donor_limb)
            limb.props["name"] = name
            limb.props["index"] = float(len(target_lmi.limbs))
            _set_slots(
                limb.props["limbPicIndex"],
                [
                    copier.copy(home_role, donor_lmi, index)
                    for index in _slot_values(donor_limb.props["limbPicIndex"])
                ],
            )
            target_lmi.limbs.append(limb)
            private[swap.donor_limb] = name
        _rename_slot(spt.obj, sprite_limbs, swap.slot, name)
        swapped.append(swap)

    registration = _Registration(reference)

    def template_pic(slot: int, p: int) -> _PicInfo | None:
        name = cloned_slots[slot] if slot < len(cloned_slots) else ""
        limb = target_lmi.limb(name)
        if limb is not None:
            return _pic_info(target_lmi, _slot_at(limb, p))
        found = target.find_limb(name, "", search_all=False) if name else None
        return _pic_info(found[1], _slot_at(found[2], p)) if found else None

    slot_donor = {swap.slot: swap.donor_limb for swap in swapped}
    corrections: dict[tuple[int, int], tuple[float, float, float]] = {}

    def correction(slot: int, p: int) -> tuple[float, float, float] | None:
        donor_limb = slot_donor.get(slot)
        if donor_limb is None:
            return None
        if (slot, p) not in corrections:
            home_role, donor_lmi, limb = donor_home[donor_limb]
            corrections[(slot, p)] = registration.correction(
                template_pic(slot, p),
                _pic_info(donor_lmi, _slot_at(limb, p)),
                donor_limb,
                home_role,
                p,
            )
        return corrections[(slot, p)]

    moved = _reregister_frames(spt.obj, correction)

    if swapped or hidden:
        swf.replace_binary(target_spt_class, M.wrap_payload(spt.build()))
        swf.replace_binary(target_lmi_class, M.wrap_payload(target_lmi.build()))
        built = swf.build()
        output_swf.write_bytes(b"CWS" + built[3:8] + zlib.compress(built[8:], 9))
    else:
        output_swf.write_bytes(data)
    return DonorTextureResult(
        output_swf,
        tuple(swapped),
        tuple(hidden),
        tuple(skipped),
        copier.copied,
        moved,
        registration.aligned,
        registration.padded,
    )


@dataclass
class SptFile:
    obj: Any

    @classmethod
    def parse(cls, stream: bytes) -> SptFile:
        reader = M.StreamReader(stream)
        if reader.utf() != "Spt":
            raise ValueError("bad SPT header")
        obj = M.amf0_decode(reader.obj_bytes())
        if not reader.eof():
            raise ValueError("trailing bytes in SPT stream")
        return cls(obj)

    def build(self) -> bytes:
        writer = M.StreamWriter()
        writer.utf("Spt")
        writer.obj_bytes(M.amf0_encode(self.obj))
        return bytes(writer.out)


def _rename_slot(spt: Any, sprite_limbs: list[Any], slot: int, name: str) -> None:
    sprite_limb = sprite_limbs[slot]
    if sum(1 for other in sprite_limbs if other is sprite_limb) > 1:
        # One SpriteLimb object referenced from several slots: give this slot its own copy.
        sprite_limb = M.deep_clone(sprite_limb)
        sprite_limbs[slot] = sprite_limb
        array = spt.props["spriteLimb"]
        if isinstance(array, M.AmfStrictArray):
            array.items[slot] = sprite_limb
        else:
            array.props[str(slot)] = sprite_limb
    sprite_limb.props["limbName"] = name


def _limb_filenames(lmi: LmiFile, limb: Any) -> tuple[str, ...]:
    pics = (
        lmi.limb_pics[index]
        for index in _slot_values(limb.props.get("limbPicIndex"))
        if 0 <= index < len(lmi.limb_pics)
    )
    return tuple(_norm(pic.props.get("filename")).rsplit("/", 1)[-1] for pic in pics)


@dataclass(frozen=True)
class _PicInfo:
    filename: str
    r: float
    cx: float
    cy: float
    joints: tuple[tuple[float, float], ...]
    png: bytes | None

    def pivot(self) -> tuple[float, float] | None:
        """Pixel of the picture that SetLimbPose pins to the parent joint (joint 0)."""
        if not self.joints:
            return None
        return (self.r * (self.joints[0][0] - self.cx), self.r * (self.joints[0][1] - self.cy))

    def pivot_inside(self) -> bool:
        # Default joint data like (131, 400) puts the pivot far outside the picture.
        pivot, size = self.pivot(), _png_size(self.png)
        if pivot is None or size is None:
            return False
        width, height = size
        return -width / 2 <= pivot[0] <= width * 1.5 and -height / 2 <= pivot[1] <= height * 1.5


class _Registration:
    """Where an HF-EX picture must go in the template's frames: m' = m o K, K(q) = s*q + t.

    The frames were baked for the template's own pictures, and the cx/cy/joint records cannot
    say where another picture belongs: HF-EX pads most pictures on the left and top without
    updating them, and even HFE's own records disagree with the art (giggs's thigh pivot sits in
    the empty corner, Lucas's beside the thigh). So the donor's drawing is placed over the
    template's drawing instead: the point of the donor's alpha bounding box at the same relative
    position as the template's pivot in the template's box is pinned onto that pivot (the neck
    for a head, the hip for a thigh). Only without both drawings does it fall back to the
    records: HF-EX padding measured against the HFE reference, then the joint pivots.
    """

    LIMIT = 80.0  # a larger pivot correction means joint data that cannot be trusted

    def __init__(self, reference: GameData | None) -> None:
        self._reference = reference
        self.aligned = 0
        self.padded = 0

    def correction(
        self,
        template: _PicInfo | None,
        donor: _PicInfo | None,
        donor_limb: str,
        home_role: str,
        p: int,
    ) -> tuple[float, float, float]:
        if donor is None:
            return (1.0, 0.0, 0.0)
        scale = template.r / donor.r if template is not None else 1.0
        template_box = _alpha_box(template.png) if template is not None else None
        donor_box = _alpha_box(donor.png)
        if template is not None and template_box is not None and donor_box is not None:
            self.aligned += 1
            pivot = template.pivot()
            shift = []
            for axis in (0, 1):
                low, high = template_box[axis], template_box[axis + 2]
                anchor = pivot[axis] if pivot is not None else (low + high) / 2
                u = min(max((anchor - low) / (high - low), 0.0), 1.0) if high > low else 0.5
                goal = low + u * (high - low)
                d_low, d_high = donor_box[axis], donor_box[axis + 2]
                shift.append(goal - scale * (d_low + u * (d_high - d_low)))
            return (scale, shift[0], shift[1])
        pad = self._padding(donor, donor_limb, home_role, p)
        if pad != (0, 0):
            self.padded += 1
        unpadded = _PicInfo(
            donor.filename,
            donor.r,
            donor.cx - pad[0] / donor.r,
            donor.cy - pad[1] / donor.r,
            donor.joints,
            donor.png,
        )
        if template is not None and template.pivot_inside() and unpadded.pivot_inside():
            target, source = template.pivot(), unpadded.pivot()
            assert target is not None and source is not None
            moved = (target[0] - scale * source[0], target[1] - scale * source[1])
            if abs(moved[0]) <= self.LIMIT and abs(moved[1]) <= self.LIMIT:
                return (scale, moved[0], moved[1])
        return (1.0, float(-pad[0]), float(-pad[1]))

    def _padding(self, donor: _PicInfo, donor_limb: str, home_role: str, p: int) -> tuple[int, int]:
        if self._reference is None:
            return (0, 0)
        found = self._reference.find_limb(donor_limb, home_role, search_all=False)
        hfe = _pic_info(found[1], _slot_at(found[2], p)) if found else None
        if (
            hfe is None
            or hfe.filename.rsplit("/", 1)[-1] != donor.filename.rsplit("/", 1)[-1]
            or hfe.joints != donor.joints
        ):
            return (0, 0)
        ex_box, hfe_box = _alpha_box(donor.png), _alpha_box(hfe.png)
        if ex_box is None or hfe_box is None:
            return (0, 0)
        if any(
            abs((ex_box[i + 2] - ex_box[i]) - (hfe_box[i + 2] - hfe_box[i])) > 2 for i in (0, 1)
        ):
            return (0, 0)  # a different drawing, not a padded copy
        return (ex_box[0] - hfe_box[0], ex_box[1] - hfe_box[1])


def _reregister_frames(
    spt: Any, correction: Callable[[int, int], tuple[float, float, float] | None]
) -> int:
    """Apply each swapped slot's correction to every uz/lz matrix that draws it."""
    moved = 0
    done: dict[int, tuple[float, float, float]] = {}
    for frame in _array_items(spt.props.get("frame")):
        if frame is None:
            continue
        for key in ("uz", "lz"):
            for entry in _array_items(frame.props.get(key)):
                if entry is None:
                    continue
                k = correction(_int(entry.props.get("i")), _int(entry.props.get("p")))
                matrix = entry.props.get("m")
                if k is None or k == (1.0, 0.0, 0.0) or matrix is None:
                    continue
                if id(matrix) in done:
                    if done[id(matrix)] == k:
                        continue
                    # A matrix object shared with a differently corrected entry: split it off.
                    matrix = M.deep_clone(matrix)
                    entry.props["m"] = matrix
                m = matrix.props
                a, b, c, d = (float(m[name]) for name in ("a", "b", "c", "d"))
                scale, kx, ky = k
                m["tx"] = float(m["tx"]) + a * kx + c * ky
                m["ty"] = float(m["ty"]) + b * kx + d * ky
                m["a"], m["b"], m["c"], m["d"] = a * scale, b * scale, c * scale, d * scale
                done[id(matrix)] = k
                moved += 1
    return moved


def _slot_at(limb: Any, p: int) -> int:
    values = _slot_values(limb.props.get("limbPicIndex"))
    return values[p] if 0 <= p < len(values) else -1


def _pic_info(lmi: LmiFile, index: int) -> _PicInfo | None:
    if index < 0 or index >= len(lmi.limb_pics):
        return None
    props = lmi.limb_pics[index].props
    filename = _norm(props.get("filename"))
    png = lmi.pngs[index]
    if png is None:
        for other, other_png in zip(lmi.limb_pics, lmi.pngs):
            if other_png is not None and _norm(other.props.get("filename")) == filename:
                png = other_png
                break
    joints = tuple(
        (float(joint.props.get("x", 0.0)), float(joint.props.get("y", 0.0)))
        for joint in _array_items(props.get("j"))
        if joint is not None
    )
    return _PicInfo(
        filename,
        float(props.get("r") or 1.0),
        float(props.get("cx") or 0.0),
        float(props.get("cy") or 0.0),
        joints,
        png,
    )


def _png_size(png: bytes | None) -> tuple[int, int] | None:
    start = png.find(b"\x89PNG") if png else -1
    if png is None or start < 0 or len(png) < start + 24:
        return None
    return int.from_bytes(png[start + 16 : start + 20], "big"), int.from_bytes(
        png[start + 20 : start + 24], "big"
    )


def _alpha_box(png: bytes | None) -> tuple[int, int, int, int] | None:
    start = png.find(b"\x89PNG") if png else -1
    if png is None or start < 0:
        return None
    try:
        from PIL import Image  # optional: without Pillow pictures are taken as unpadded
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(png[start:])) as image:
            alpha = image.convert("RGBA").getchannel("A")
            box = alpha.point(lambda value: 255 if value > 40 else 0).getbbox()
    except (OSError, ValueError):
        return None
    return (box[0], box[1], box[2], box[3]) if box else None


class _PictureCopier:
    """Appends donor pictures to an LMI once each, keeping ref/bmRef links and pixels."""

    def __init__(self, lmi: LmiFile, donor: GameData, folder: str) -> None:
        self._lmi = lmi
        self._donor = donor
        self._folder = folder
        self._copied: dict[tuple[str, int], int] = {}
        self._filenames_with_pixels = {
            _norm(pic.props.get("filename"))
            for pic, png in zip(lmi.limb_pics, lmi.pngs)
            if png is not None and pic.props.get("embeded")
        }
        self.copied = 0

    def copy(self, role_id: str, donor_lmi: LmiFile, index: int) -> int:
        if index < 0 or index >= len(donor_lmi.limb_pics):
            return -1
        key = (role_id, index)
        if key in self._copied:
            return self._copied[key]
        new_index = len(self._lmi.limb_pics)
        self._copied[key] = new_index
        pic = M.deep_clone(donor_lmi.limb_pics[index])
        old_filename = _norm(pic.props.get("filename"))
        filename = f"{self._folder}/{role_id}/{old_filename.replace('/', '_')}"
        pic.props["filename"] = filename
        pic.props["index"] = float(new_index)
        self._lmi.limb_pics.append(pic)
        self._lmi.pngs.append(None)
        self.copied += 1
        for link in ("refIndex", "bmRefIndex"):
            target = _int(pic.props.get(link))
            if target >= 0 and target != index:
                pic.props[link] = float(self.copy(role_id, donor_lmi, target))
        if pic.props.get("embeded") and filename not in self._filenames_with_pixels:
            png = donor_lmi.pngs[index]
            if png is None:
                png = self._donor.png_for_filename(old_filename)
            if png is None:
                # An embedded picture whose pixels never arrive stalls the whole SPT's draw.
                pic.props["embeded"] = False
            else:
                self._lmi.pngs[new_index] = png
                self._filenames_with_pixels.add(filename)
        return new_index


def _array_items(value: Any) -> list[Any]:
    if value is None or value is M.UNDEFINED:
        return []
    if isinstance(value, M.AmfStrictArray):
        return list(value.items)
    keys = [key for key in value.props if key.lstrip("-").isdigit()]
    return [value.props[key] for key in sorted(keys, key=int)]


def _slot_values(slots: Any) -> list[int]:
    return [_int(value) for value in _array_items(slots)]


def _set_slots(slots: Any, values: list[int]) -> None:
    # Keep the array's shape (ECMA count, key set); only the slot values change.
    if isinstance(slots, M.AmfStrictArray):
        slots.items = [float(value) for value in values]
        return
    keys = sorted((key for key in slots.props if key.lstrip("-").isdigit()), key=int)
    for key, value in zip(keys, values):
        slots.props[key] = float(value)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else -1


def _norm(filename: Any) -> str:
    return str(filename or "").replace("\\", "/")
