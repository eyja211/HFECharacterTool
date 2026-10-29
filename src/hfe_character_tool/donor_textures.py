"""Give a new role another role's body-part textures, taken from HF-EX.

A cloned SPT draws every limb with the uz/lz matrices baked against the source role's own
pictures. HF-EX ships every stock role re-cut to Lucas's skeleton, so its pictures drop into
those matrices; the HFE originals are cut differently (yaga's head inside his chest picture,
jason's feet inside his legs, ...) and land detached from the body. Pointing the SPT at the
target game's donor limb therefore misplaces parts, and no matrix correction can fix art that
is cut differently.

So the SPT is left alone. The donor limb's HF-EX pictures are copied into the new role's own
LMI under role-private filenames (pixels resolve through a global filename table) and the new
role's limb slot table is repointed at them.
"""

from __future__ import annotations

import contextlib
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hfe_character_tool.abc_vendor import amf as M
from hfe_character_tool.abc_vendor import swf as S
from hfe_character_tool.hfworkshop_catalog import (
    DONOR_ART_RELATIVE,
    KEEP_SOURCE_TEXTURE_ROLE_ID,
    TexturePart,
    _limb_suffix,
    _should_hide_unmatched_overlay_suffix,
    _source_limb_names_for_part,
)
from hfe_character_tool.runtime import resource_path


@dataclass(frozen=True)
class DonorLimb:
    source_limb: str
    donor_role: str
    donor_limb: str  # "" hides the limb: a source overlay the donor has no counterpart for


@dataclass(frozen=True)
class DonorTextureResult:
    output_swf: Path
    swapped_limbs: tuple[DonorLimb, ...]
    hidden_limbs: tuple[str, ...]
    skipped: tuple[str, ...]
    copied_pictures: int


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


class DonorArt:
    """Lazily parsed LMIs of the donor-art game (HF-EX)."""

    def __init__(self, exe_path: Path) -> None:
        _prefix, swf_bytes = S.read_projector(str(exe_path))
        self._swf = S.SwfFile.parse(swf_bytes)
        self._lmis: dict[str, LmiFile | None] = {}
        self._png_by_filename: dict[str, bytes] | None = None

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

    def find_limb(self, name: str, donor_role: str) -> tuple[str, LmiFile, Any] | None:
        """The LMI that defines a limb the donor's SPT draws (often another role's)."""
        owner = name.rsplit("_", 1)[0].lower() if "_" in name else ""
        others = [role for role in self.role_ids() if role not in (donor_role, owner)]
        for role_id in (donor_role, *(r for r in self.role_ids() if r.lower() == owner), *others):
            lmi = self.lmi(role_id)
            limb = lmi.limb(name) if lmi else None
            if lmi is not None and limb is not None:
                return role_id, lmi, limb
        return None

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


def plan_donor_limbs(
    selections: Mapping[str, str],
    parts: tuple[TexturePart, ...],
    source_slots: tuple[str, ...],
    donor_slots: Mapping[str, tuple[str, ...]],
    source_role_id: str,
) -> tuple[DonorLimb, ...]:
    """Which source limb takes which donor limb.

    A part is matched by skeleton slot (spriteLimb index: 0 head, 1 chest, 5 upper arm, ...),
    taking whatever limb the donor's own SPT draws there. Many HF-EX roles borrow arms, fists
    or feet from another role (saws draws raye's arms, jason giggs's feet), so this is what
    the donor really looks like, where matching limb names finds nothing.
    """
    part_ids = {part.part_id for part in parts}
    names = tuple(dict.fromkeys(name for name in source_slots if name))
    plan: dict[str, DonorLimb] = {}
    for part_id, role_id in selections.items():
        if part_id not in part_ids or role_id in {KEEP_SOURCE_TEXTURE_ROLE_ID, source_role_id}:
            continue
        slots = donor_slots.get(role_id, ())
        for source_limb in _source_limb_names_for_part(part_id, names):
            if source_limb not in names:
                continue
            slot = source_slots.index(source_limb)
            donor_limb = slots[slot] if slot < len(slots) else ""
            if donor_limb:
                plan[source_limb] = DonorLimb(source_limb, role_id, donor_limb)
            elif _should_hide_unmatched_overlay_suffix(_limb_suffix(source_limb)):
                plan[source_limb] = DonorLimb(source_limb, role_id, "")
    return tuple(plan.values())


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
        donor = DonorArt(Path(key[0]))
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
    """Parts whose chosen role draws nothing in that slot, so they keep the template's art."""
    catalog = donor_slot_catalog(workspace)
    source_slots = catalog.get(source_role_id) or catalog.get("lucas", ())
    missing: list[str] = []
    for part_id, role_id in selections.items():
        plan = plan_donor_limbs({part_id: role_id}, parts, source_slots, catalog, source_role_id)
        if (
            role_id != KEEP_SOURCE_TEXTURE_ROLE_ID
            and role_id != source_role_id
            and not any(entry.donor_limb for entry in plan)
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
    parts: tuple[TexturePart, ...],
    source_spt_class: str,
    source_lmi_class: str,
    target_spt_class: str,
    target_lmi_class: str,
    source_role_id: str,
    resource_id: str,
) -> DonorTextureResult:
    data = input_swf.read_bytes()
    if not selections:
        output_swf.write_bytes(data)
        return DonorTextureResult(output_swf, (), (), (), 0)
    swf = S.SwfFile.parse(data)
    target_lmi = LmiFile.parse(M.unwrap_payload(swf.binary_payload(target_lmi_class)))
    source_slots = sprite_limb_names(M.unwrap_payload(swf.binary_payload(source_spt_class)))
    target_slots = sprite_limb_names(M.unwrap_payload(swf.binary_payload(target_spt_class)))
    cloned_name: dict[str, str] = {}
    for source_name, target_name in zip(source_slots, target_slots):
        if source_name and target_name:
            cloned_name.setdefault(source_name, target_name)
    donor = DonorArt(donor_exe)
    donor_slots = {role_id: donor.sprite_slots(role_id) for role_id in set(selections.values())}
    plan = plan_donor_limbs(selections, parts, source_slots, donor_slots, source_role_id)

    swapped: list[DonorLimb] = []
    hidden: list[str] = []
    skipped: list[str] = []
    copier = _PictureCopier(target_lmi, donor, f"png_{resource_id}_tex")
    for entry in plan:
        limb = target_lmi.limb(cloned_name.get(entry.source_limb, ""))
        if limb is None:
            skipped.append(f"{entry.source_limb}: not drawn by the cloned SPT")
            continue
        slots = limb.props["limbPicIndex"]
        if entry.donor_limb == "":
            _set_slots(slots, [-1] * len(_slot_values(slots)))
            hidden.append(entry.source_limb)
            continue
        found = donor.find_limb(entry.donor_limb, entry.donor_role)
        if found is None:
            skipped.append(f"{entry.source_limb}: no LMI defines {entry.donor_limb}")
            continue
        home_role, donor_lmi, donor_limb = found
        new_slots = [
            copier.copy(home_role, donor_lmi, index)
            for index in _slot_values(donor_limb.props["limbPicIndex"])
        ]
        limb.props["limbPicIndex"] = M.deep_clone(donor_limb.props["limbPicIndex"])
        _set_slots(limb.props["limbPicIndex"], new_slots)
        swapped.append(entry)

    if swapped or hidden:
        swf.replace_binary(target_lmi_class, M.wrap_payload(target_lmi.build()))
        built = swf.build()
        output_swf.write_bytes(b"CWS" + built[3:8] + zlib.compress(built[8:], 9))
    else:
        output_swf.write_bytes(data)
    return DonorTextureResult(
        output_swf, tuple(swapped), tuple(hidden), tuple(skipped), copier.copied
    )


class _PictureCopier:
    """Appends donor pictures to an LMI once each, keeping ref/bmRef links and pixels."""

    def __init__(self, lmi: LmiFile, donor: DonorArt, folder: str) -> None:
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
