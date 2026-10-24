from __future__ import annotations

from hfe_character_tool.action_names import (
    action_parts,
    describe_action,
    family_label,
    infer_family,
)
from hfe_character_tool.gui_presenter import action_row_text, frame_edit_label

LUCAS = ("stand", "walk", "attack1", "sing", "sing2", "singR", "ball", "ball2", "ballJ", "ballRb")
# In HFE v1.0.2 the "sinan" slot carries Lincoln's move set.
SINAN_SLOT = ("stand", "sing", "ball", "cat_sing", "spin_sky", "spin_land", "jump_big_ball")
VIVIAN = ("stand", "ball", "heal", "supersword", "supersword2", "move", "rid_ball", "rid_heal")
SORCERER = ("stand", "attack1, attack3", "ball", "heal", "rid_ball", "rid_heal")


def test_family_is_recognised_from_the_move_set_not_the_role_id() -> None:
    assert infer_family(LUCAS) == "lucas"
    assert infer_family(SINAN_SLOT) == "lincoln"
    assert infer_family(VIVIAN) == "vivian"
    assert infer_family(SORCERER) == "z_sorcerer"
    assert infer_family(("stand", "walk", "attack1", "hit1")) is None
    assert family_label("lincoln") == "林肯（Lincoln）"


def test_same_name_means_different_moves_per_character() -> None:
    assert describe_action("ball", "lucas") == "飞龙斩 动作1"
    assert describe_action("ball", "vivian") == "二连"
    assert describe_action("ball", "z_sorcerer") == "追踪波"
    assert describe_action("attack4", "iczzy") == "冷冻波1"
    assert describe_action("attack4", None) == "普攻第4下"


def test_spt_name_variants() -> None:
    assert describe_action("singR", "lucas") == "骑马升龙"
    assert describe_action("\\_gua_stand", None) == "防御站立"
    assert action_parts("_cat_forward_attack, _hea_attack1") == (
        "_cat_forward_attack",
        "_hea_attack1",
    )
    assert describe_action("_cat_forward_attack, _hea_attack1", None) == "抓人扔出 / 丢出重物"
    assert describe_action("hit1_back, hit2_back", None) == "背后受击1 / 背后受击2"
    assert describe_action("_old__rid_attack1", "legge") == "骑乘普攻第1下（旧版）"
    assert describe_action("_old_cat_forward_attack", "legge") == "抓人扔出（旧版）"
    assert describe_action("attack1end", "legge") == "普攻第1下收手"


def test_unknown_actions_stay_blank_instead_of_guessed() -> None:
    assert describe_action("_rid_pull", "lucas") == ""
    assert describe_action("wind_attack", "lincoln") == ""


def test_row_and_summary_text() -> None:
    assert action_row_text("ball", "飞龙斩 动作1", edited=True) == "● 飞龙斩 动作1  ·  ball"
    assert action_row_text("_rid_pull", "", edited=False) == "   _rid_pull"
    assert frame_edit_label("ball", 8, 2, "飞龙斩 动作1") == "飞龙斩 动作1 第 8 帧 · 2 个道具"
    assert frame_edit_label("ball", 8, 2) == "ball 第 8 帧 · 2 个道具"
