from __future__ import annotations

import random
from collections.abc import Sequence

from hfe_character_tool.hfworkshop_catalog import (
    KEEP_SOURCE_TEXTURE_ROLE_ID,
    ItemOption,
    SptActionInfo,
    TexturePart,
    TextureRole,
)
from hfe_character_tool.models import (
    CharacterProject,
    FrameItemEdit,
    ItemSpawnSlot,
    replace_project,
)

MAX_RANDOM_SLOTS_PER_FRAME = 3


def randomize_project(
    project: CharacterProject,
    actions: Sequence[SptActionInfo],
    item_options: Sequence[ItemOption],
    texture_parts: Sequence[TexturePart],
    texture_roles: Sequence[TextureRole],
    *,
    rng: random.Random | None = None,
) -> CharacterProject:
    generator = rng or random.Random()
    source_role_id = project.source_role_id or "lucas"
    return replace_project(
        project,
        stats=_random_stats(generator),
        item_frame_edits=_random_item_frame_edits(generator, actions, item_options),
        texture_selections=_random_texture_selections(
            generator,
            texture_parts,
            texture_roles,
            source_role_id,
        ),
    )


def _random_stats(rng: random.Random) -> dict[str, int]:
    return {
        "hp": rng.randint(120, 1600),
        "mp": rng.randint(80, 1200),
        "defense": rng.randint(0, 450),
    }


def _random_item_frame_edits(
    rng: random.Random,
    actions: Sequence[SptActionInfo],
    item_options: Sequence[ItemOption],
) -> tuple[FrameItemEdit, ...]:
    if not item_options:
        return ()
    edits: list[FrameItemEdit] = []
    for action in actions:
        if action.frame_count < 1:
            continue
        frame = rng.randint(1, action.frame_count)
        slot_count = rng.randint(1, MAX_RANDOM_SLOTS_PER_FRAME)
        edits.append(
            FrameItemEdit(
                action_name=action.action_name,
                action_frame=frame,
                slots=tuple(_random_slot(rng, item_options) for _ in range(slot_count)),
            )
        )
    return tuple(edits)


def _random_slot(rng: random.Random, item_options: Sequence[ItemOption]) -> ItemSpawnSlot:
    item = rng.choice(tuple(item_options))
    return ItemSpawnSlot(
        item_action_group=item.action_group,
        ref=-1.0,
        x=float(rng.randint(-260, 360)),
        y=float(rng.randint(-260, 160)),
        z=float(rng.randint(-40, 80)),
        vx=float(rng.randint(-180, 180)),
        vy=float(rng.randint(-130, 130)),
        vz=float(rng.randint(-90, 120)),
    )


def _random_texture_selections(
    rng: random.Random,
    texture_parts: Sequence[TexturePart],
    texture_roles: Sequence[TextureRole],
    source_role_id: str,
) -> dict[str, str]:
    selectable_roles = tuple(
        role.role_id
        for role in texture_roles
        if role.role_id not in {KEEP_SOURCE_TEXTURE_ROLE_ID, source_role_id}
    )
    if not selectable_roles:
        return {}
    return {
        part.part_id: rng.choice(selectable_roles)
        for part in texture_parts
        if part.part_id
    }
