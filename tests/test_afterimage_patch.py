from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from afterimage_vm import UNDEFINED, EmittedVM

from hfe_character_tool.abc_vendor import abcpatch as A
from hfe_character_tool.abc_vendor import swf as S
from hfe_character_tool.afterimage_as3 import AS3_SOURCE, CONFIG_PREFIX, MODULE_CLASS
from hfe_character_tool.afterimage_patch import (
    AfterimagePatchError,
    AfterimageRole,
    apply_afterimage,
    decode_config,
    encode_config,
    merge_roles,
    normalize_delays,
    read_installed_roles,
)
from hfe_character_tool.tools import discover_tools, extract_projector_swf

ROOT = Path(__file__).resolve().parents[1]
STOCK_EXE = ROOT / "vendor" / "original_game" / "HeroFighter v0.7 (1).exe"


def test_config_round_trip_and_merge() -> None:
    roles = (
        AfterimageRole("shaw0", (3, 1, 2, 2), True, False),
        AfterimageRole("z_x", (90,), False, True),
    )
    text = encode_config(roles)
    assert text == CONFIG_PREFIX + "shaw0:1,2,3:1:0;z_x:90:0:1"
    decoded = decode_config(text)
    assert decoded == (
        AfterimageRole("shaw0", (1, 2, 3), True, False),
        AfterimageRole("z_x", (90,), False, True),
    )
    merged = merge_roles(
        decoded, (AfterimageRole("z_x", (5,), True, True), AfterimageRole("new0", (1,)))
    )
    assert [r.role_id for r in merged] == ["shaw0", "z_x", "new0"]
    assert merged[1].delays == (5,)


def test_normalize_delays_rejects_out_of_range() -> None:
    assert normalize_delays([5, 1, 5]) == (1, 5)
    with pytest.raises(ValueError):
        normalize_delays([0])
    with pytest.raises(ValueError):
        normalize_delays([91])


def test_source_has_no_block_comments_or_compact_loops() -> None:
    assert "/*" not in AS3_SOURCE
    # FFDec 21.1 compiles a compact `i<xs.length` header into a read of `lengthxs`.
    for line in AS3_SOURCE.splitlines():
        if "for (" in line:
            assert " < " in line or " >= " in line, line


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if not STOCK_EXE.is_file():
        pytest.skip("stock HF v0.7 EXE not available")
    tools = discover_tools(ROOT)
    if tools.java is None or tools.javac is None:
        pytest.skip("java/javac not available")
    work = tmp_path_factory.mktemp("afterimage")
    swf_in = work / "in.swf"
    extract_projector_swf(STOCK_EXE, swf_in)
    out = work / "out.swf"
    roles = (AfterimageRole("hero", (1, 3), True, True), AfterimageRole("calm", (2,), False, False))
    result = apply_afterimage(swf_in, out, roles, tools, work / "compile")
    return {"tools": tools, "work": work, "in": swf_in, "out": out, "result": result}


def _main_abc(path: Path) -> tuple[Any, Any, int]:
    swf = S.SwfFile.parse(path.read_bytes())
    for index, tag in swf.find_tags(S.TAG_DOABC):
        _flags, _name, data = S.parse_doabc(tag.body)
        abc = A.AbcFile.parse(data)
        if abc.find_instance("Game", "World") is not None:
            return abc, swf, index
    raise AssertionError("no main ABC")


def test_install_report_and_only_main_abc_changes(built: dict[str, Any]) -> None:
    result = built["result"]
    assert result.mode == "installed"
    assert len(result.report["hooks"]) == 4
    assert result.report["modified_engine_methods"] == 4
    assert result.report["timestop_gate"] is False
    before_abc, before, before_index = _main_abc(built["in"])
    after_abc, after, after_index = _main_abc(built["out"])
    assert before_index == after_index
    assert len(before.tags) == len(after.tags)
    changed = [i for i, (x, y) in enumerate(zip(before.tags, after.tags))
               if S.emit_tag(x) != S.emit_tag(y)]
    assert changed == [before_index]
    # Additive: original pools are a prefix, untouched bodies stay byte-identical.
    assert after_abc.strings[:len(before_abc.strings)] == before_abc.strings
    differing = [b.method for b in before_abc.bodies
                 if after_abc.body_of_method(b.method).code != b.code]
    assert len(differing) == 4
    assert read_installed_roles(built["out"]) == result.roles


def test_update_mode_merges_roles_without_new_code(built: dict[str, Any], tmp_path: Path) -> None:
    out2 = tmp_path / "out2.swf"
    second = apply_afterimage(built["out"], out2, (AfterimageRole("next0", (1, 2), True, True),),
                              built["tools"], tmp_path / "compile")
    assert second.mode == "updated"
    assert [r.role_id for r in second.roles] == ["hero", "calm", "next0"]
    first_abc, _, _ = _main_abc(built["out"])
    second_abc, _, _ = _main_abc(out2)
    assert [b.code for b in first_abc.bodies] == [b.code for b in second_abc.bodies]
    changed = [i for i, (x, y) in enumerate(zip(first_abc.strings, second_abc.strings)) if x != y]
    assert len(changed) == 1
    assert second_abc.strings[changed[0]].endswith("next0:1,2:1:1")


def test_new_bridges_have_no_backward_branch(built: dict[str, Any]) -> None:
    before_abc, _, _ = _main_abc(built["in"])
    after_abc, _, _ = _main_abc(built["out"])
    ci = after_abc.find_instance("Game", MODULE_CLASS)
    module = {t.data[1] for t in after_abc.classes[ci][1] if t.kind & 15 in (1, 2, 3)}
    module |= {after_abc.instances[ci]["iinit"], after_abc.classes[ci][0], after_abc.scripts[-1][0]}
    bridges = [b for b in after_abc.bodies
               if b.method >= len(before_abc.methods) and b.method not in module]
    assert len(bridges) >= 12
    for body in bridges:
        for _off, op, args, _nxt in A.walk_code(body.code):
            if A.OPCODES[op][1] == ["s24"]:
                assert args[0] >= 0, body.method
    # Each bridge that calls the module carries a catch boundary.
    calls_module = [b for b in bridges if after_abc.qname_idx(0x16, "Game", MODULE_CLASS, add=False)
                    in [a[0] for _o, op, a, _n in A.walk_code(b.code) if op == 0x60]]
    assert calls_module and all(b.exceptions for b in calls_module)


# ---------------------------------------------------------------- behaviour


def frame(**kw: Any) -> dict[str, Any]:
    base = {"onGround": True, "footY": 0, "bx1": -50, "bx2": 50, "by1": -150, "by2": 0,
            "bz1": -20, "bz2": 20, "newAttack": False, "attack": [], "cat": None,
            "body": [], "dbx1": -60, "dbx2": 60, "dby1": -200, "dby2": 10}
    base.update(kw)
    return base


class Fixture:
    def __init__(self, abc: Any) -> None:
        self.created: list[dict[str, Any]] = []
        self.checks: list[tuple[Any, Any]] = []
        self.catches: list[tuple[Any, Any]] = []
        self.applied: list[Any] = []
        self.spawned: list[tuple[Any, ...]] = []
        self.item_spt = object()
        self.vm = EmittedVM(abc, constructors={"ObjH": self.obj_h, "Rectangle": self.rect,
                                               "ColorTransform": lambda *a: ("ct", a)})
        self.frames = [frame() for _ in range(4)]
        self.hero = {"actionGroup": [{"action": [[{}], [{}]]}], "frame": self.frames}
        self.calm = {"actionGroup": [{"action": [[{}], [{}]]}], "frame": self.frames}
        self.vm.globals["Global"]["sptHash"] = {"hero": self.hero, "calm": self.calm}
        self.w: dict[str, Any] = {
            "bg": object(), "hfeAiState": None, "playingDrama": False, "obj": [],
            "scale": 0.5, "sclX": 0, "sclY": 0,
            "hfeAiCheck": lambda v, e: self.checks.append((v, e)),
            "hfeAiCatch": lambda e, v: self.catches.append((e, v)),
            "hfeAiApply": lambda e: self.applied.append(e),
            "AddObjI": self.add_obj_i,
        }

    def rect(self, x: float, y: float, w: float, h: float) -> dict[str, Any]:
        return {"x": x, "y": y, "width": w, "height": h}

    def unit(self, id_: str, spt: Any, team: int = 0, **kw: Any) -> dict[str, Any]:
        u: dict[str, Any] = {
            "_avm_type": "Game.ObjH", "id": id_, "world": self.w, "spt": spt, "hp": 500,
            "die": False, "deleted": False, "recy": False, "hfeAiEcho": None,
            "hfeAiHist": None, "hfeAiShots": None, "hfeAiLastShot": None, "con": 0,
            "team": team, "inObj": True, "x": 0, "y": 0, "z": 300, "vx": 0, "vy": 0, "vz": 0,
            "facing": 0, "agi": 0, "ati": 0, "ai": 0, "fi": 0, "dur": 0, "o": 0, "landY": 0,
            "str": 1, "lv": 1, "brk": 0, "brkin": 0, "pU": False, "pD": False, "pL": False,
            "pR": False, "keyS1": "", "cat": None, "cau": None, "app": "0", "bt_team": -1,
            "mHp": 500, "mMp": 100, "mSt": 100, "f": self.frames[0],
        }
        u.update(kw)
        return u

    def obj_h(self, id_: str, world: Any, spt: Any, name: str, team: int, con: int,
              cid: int) -> dict[str, Any]:
        echo = self.unit(id_, spt, team, con=con, aList=[], app="0")
        echo["released"] = 0
        echo["SetA"] = lambda *a: None
        echo["SetApp"] = lambda app: echo.__setitem__("app", app)
        echo["hfeAiHideBars"] = lambda: None
        echo["ReleaseAll"] = lambda: echo.__setitem__("released", echo["released"] + 1)
        echo["PosCatchee"] = lambda: None
        self.created.append(echo)
        return echo

    def add_obj_i(self, *args: Any) -> dict[str, Any]:
        self.spawned.append(args)
        return {"spt": self.item_spt, "f": {"body": []}, "ab": {}}

    def tick(self) -> None:
        self.vm.run("tick", self.w)

    def echoes(self, owner: dict[str, Any]) -> list[Any]:
        hist = owner["hfeAiHist"]
        return [s["echo"] for s in hist["slots"]] if hist not in (None, UNDEFINED) else []


@pytest.fixture()
def fx(built: dict[str, Any]) -> Fixture:
    abc, _, _ = _main_abc(built["out"])
    return Fixture(abc)


def test_each_echo_replays_exactly_its_delay(fx: Fixture) -> None:
    d = fx.unit("1", fx.hero)
    fx.w["obj"] = [d]
    for t in range(10):
        d["x"] = 100 * t
        fx.tick()
        one, three = fx.echoes(d)
        assert (one is None) == (t < 1), t
        assert (three is None) == (t < 3), t
        if one is not None:
            assert one["x"] == 100 * (t - 1)
            assert one["ix"] == 100 * (t - 1)
            assert one["hfeAiEcho"] is d and one["aOwner"] is d
            assert one["hitable"] is False and one["repelable"] is False
        if three is not None:
            assert three["x"] == 100 * (t - 3)
    assert len(fx.created) == 2


def test_damage_switch_controls_hit_and_grab_checks(fx: Fixture) -> None:
    d = fx.unit("1", fx.hero, team=0)
    calm = fx.unit("2", fx.calm, team=0)
    enemy = fx.unit("3", object(), team=1, x=0, z=300)
    fx.w["obj"] = [d, calm, enemy]
    for _ in range(4):
        fx.tick()
    hero_echoes = set(map(id, fx.echoes(d)))
    calm_echoes = set(map(id, fx.echoes(calm)))
    assert calm_echoes and hero_echoes
    checked_attackers = {id(e) for _v, e in fx.checks}
    assert checked_attackers == hero_echoes
    assert all(v is not d for v, _e in fx.checks)
    assert all(id(e) not in calm_echoes for e, _v in fx.catches)


def test_hits_are_applied_once_per_victim(fx: Fixture) -> None:
    d = fx.unit("1", fx.hero)
    fx.w["obj"] = [d]
    fx.tick()
    fx.tick()
    echo = fx.echoes(d)[0]
    victim = fx.unit("9", object(), team=1)
    echo["aList"] = [{"o": victim}, {"o": victim}, {"o": None}]
    fx.tick()
    assert fx.applied == [echo]
    assert echo["aList"] == []


def test_item_switch_and_replay_timing(fx: Fixture) -> None:
    d = fx.unit("1", fx.hero)
    calm = fx.unit("2", fx.calm)
    fx.w["obj"] = [d, calm]
    fx.tick()
    item = {"agi": 5, "ai": 0, "spt": fx.item_spt, "x": 10, "y": -50, "z": 300, "facing": 0,
            "vx": 20, "vy": 0, "vz": 0, "o": 0, "ab": {"ref": 7}, "useAb": True, "str": 1}
    fx.vm.run("recordShot", d, item)
    fx.vm.run("recordShot", d, item)
    fx.vm.run("recordShot", calm, dict(item))
    assert len(d["hfeAiShots"]) == 1
    assert calm["hfeAiShots"] in (None, UNDEFINED)
    spawned_at = []
    for t in range(1, 6):
        before = len(fx.spawned)
        fx.tick()
        spawned_at.extend([t] * (len(fx.spawned) - before))
    # recorded during tick 1; delays 1 and 3 replay it on ticks 2 and 4.
    assert spawned_at == [2, 4]
    assert all(args[0] == 5 and args[6] == d["team"] for args in fx.spawned)


def test_owner_death_clears_echoes(fx: Fixture) -> None:
    d = fx.unit("1", fx.hero)
    fx.w["obj"] = [d]
    for _ in range(4):
        fx.tick()
    echoes = fx.echoes(d)
    assert all(e is not None for e in echoes)
    d["die"] = True
    fx.tick()
    assert d["hfeAiHist"] is None
    assert all(e["deleted"] is True and e["released"] == 1 for e in echoes)


def test_other_roles_and_drama_are_ignored(fx: Fixture) -> None:
    other = fx.unit("5", object())
    d = fx.unit("1", fx.hero)
    fx.w["obj"] = [other, d]
    fx.w["playingDrama"] = True
    fx.tick()
    assert d["hfeAiHist"] in (None, UNDEFINED)
    fx.w["playingDrama"] = False
    fx.tick()
    assert other["hfeAiHist"] in (None, UNDEFINED)
    assert d["hfeAiHist"] not in (None, UNDEFINED)


def test_new_world_starts_fresh_history(fx: Fixture) -> None:
    d = fx.unit("1", fx.hero)
    fx.w["obj"] = [d]
    for _ in range(4):
        fx.tick()
    old = fx.echoes(d)
    fx.w["bg"] = object()
    fx.tick()
    assert all(e["deleted"] is True for e in old)
    assert fx.echoes(d) == [None, None]


def test_draw_region_is_local_and_clipped(fx: Fixture) -> None:
    d = fx.unit("1", fx.hero)
    fx.w["obj"] = [d]
    bmp = {"width": 960, "height": 540, "rect": {"x": 0, "y": 0, "width": 960, "height": 540}}
    echo = fx.unit("e", fx.hero, x=400, y=0, z=300)
    rect = fx.vm.run("region", fx.w, echo, bmp, False)
    assert rect["width"] < 200 and rect["height"] < 300
    assert rect["x"] == (400 - 60 - 80) * 0.5
    half = fx.vm.run("region", fx.w, echo, bmp, True)
    assert half["x"] == (400 - 60 - 80) * 0.25
    far = fx.unit("f", fx.hero, x=99999)
    assert fx.vm.run("region", fx.w, far, bmp, False) is None


def test_existing_module_without_hooks_is_rejected(built: dict[str, Any], tmp_path: Path) -> None:
    abc, swf, index = _main_abc(built["out"])
    ci = abc.find_instance("Game", "World")
    abc.instances[ci]["traits"] = [t for t in abc.instances[ci]["traits"]
                                   if abc.mn_name(t.name) != "hfeAiTick"]
    flags, name, _ = S.parse_doabc(swf.tags[index].body)
    swf.tags[index] = S.make_doabc_tag(abc.build(), name, bool(flags & 1))
    broken = tmp_path / "broken.swf"
    broken.write_bytes(swf.build())
    with pytest.raises(AfterimagePatchError):
        apply_afterimage(broken, tmp_path / "x.swf", (AfterimageRole("a", (1,)),),
                         built["tools"], tmp_path / "c")
