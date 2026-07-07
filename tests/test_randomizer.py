from __future__ import annotations

import random

from hfe_character_tool.hfworkshop_catalog import (
    KEEP_SOURCE_TEXTURE_ROLE,
    ItemOption,
    SptActionInfo,
    TexturePart,
    TextureRole,
)
from hfe_character_tool.randomizer import randomize_project
from hfe_character_tool.templates import initial_project


def test_randomize_project_keeps_identity_and_uses_safe_ranges() -> None:
    project = initial_project("hero", "lucas-basic", "abcde")
    actions = (
        SptActionInfo("ball", 0, 3, ()),
        SptActionInfo("sing", 3, 2, ()),
    )
    items = (
        ItemOption(5, "lucasB", "5: lucasB"),
        ItemOption(35, "swordwind", "35: swordwind"),
    )
    parts = (
        TexturePart("head", "head", ("head.png",)),
        TexturePart("chest", "chest", ("chest.png",)),
    )
    roles = (
        KEEP_SOURCE_TEXTURE_ROLE,
        TextureRole("lucas", "Lucas"),
        TextureRole("raye", "Raye"),
        TextureRole("gordon", "Gordon"),
    )

    randomized = randomize_project(
        project,
        actions,
        items,
        parts,
        roles,
        rng=random.Random(7),
    )

    assert randomized.character_id == project.character_id
    assert randomized.target_game == project.target_game
    assert 120 <= randomized.stats["hp"] <= 1600
    assert 80 <= randomized.stats["mp"] <= 1200
    assert 0 <= randomized.stats["defense"] <= 450
    assert [edit.action_name for edit in randomized.item_frame_edits] == ["ball", "sing"]
    assert 1 <= randomized.item_frame_edits[0].action_frame <= 3
    assert 1 <= randomized.item_frame_edits[1].action_frame <= 2
    allowed_items = {5, 35}
    for edit in randomized.item_frame_edits:
        assert 1 <= len(edit.slots) <= 3
        for slot in edit.slots:
            assert slot.item_action_group in allowed_items
            assert -9999 <= slot.x <= 9999
            assert -9999 <= slot.y <= 9999
            assert -9999 <= slot.z <= 9999
            assert -9999 <= slot.vx <= 9999
            assert -9999 <= slot.vy <= 9999
            assert -9999 <= slot.vz <= 9999
    assert set(randomized.texture_selections) == {"head", "chest"}
    assert set(randomized.texture_selections.values()) <= {"raye", "gordon"}


def test_randomize_project_skips_items_and_textures_when_catalogs_are_empty() -> None:
    project = initial_project("hero", "lucas-basic", "abcde")

    randomized = randomize_project(
        project,
        (SptActionInfo("ball", 0, 3, ()),),
        (),
        (TexturePart("head", "head", ("head.png",)),),
        (KEEP_SOURCE_TEXTURE_ROLE, TextureRole("lucas", "Lucas")),
        rng=random.Random(1),
    )

    assert randomized.item_frame_edits == ()
    assert randomized.texture_selections == {}
