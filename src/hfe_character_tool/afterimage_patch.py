"""Install or update the runtime afterimage (残影) module in a target SWF.

The mechanic is the user-accepted Drew echo design (``DrewEchoCannon fix7``)
generalised to any role: an ``ObjH`` proxy that is never entered into
``World.obj``/``objZ`` replays its owner's state from N ticks earlier, drawn at
40 % alpha in the owner's Z order. Each configured role carries its own delay
list and two switches: body damage (hits and grabs) and item replay.

Layout of the patch, all additive:

- ``Game.HfeAfterimage``: one readable AS3 class compiled by FFDec's own
  compiler against a carrier SWF; the input constant pools must survive as an
  exact prefix and every original method body must be byte-identical.
- Instance slots ``hfeAi*`` on ``Obj``/``World`` and small native bridges. Every
  module entry point sits behind ``World.hfeAiFault == null`` and a catch that
  paints ``AFTERIMAGE MOD DISABLED: ...`` in red instead of breaking the frame.
- Hooks found by shape, not by offset, so every target version is handled by
  the same code: ``World.Run`` tail, the ``ObjH.Run`` item-emitter increment,
  and the four ``draw_o.Draw/DrawH`` sites plus head/tail of ``World.Draw/DrawH``.

The per-role configuration is one pool string (``HFEAI1|...``). A target that
already carries the module only gets that string rewritten, which is what keeps
cumulative exports (role A, then role B on A's output) working.
"""

from __future__ import annotations

import copy
import os
import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hfe_character_tool.abc_vendor import abcpatch as A
from hfe_character_tool.abc_vendor import swf as S
from hfe_character_tool.abc_vendor.verify_bytecode import verify
from hfe_character_tool.afterimage_as3 import (
    AS3_SOURCE,
    CONFIG_PREFIX,
    HFP_ROUTE_MARKER,
    MODULE_CLASS,
    TS_GATE_MARKER,
)
from hfe_character_tool.models import AFTERIMAGE_MAX_DELAY
from hfe_character_tool.tools import ToolConfig, run_tool

MAX_DELAY = AFTERIMAGE_MAX_DELAY
ALPHA = 0.4
REPORT_PREFIX = "AFTERIMAGE MOD DISABLED: "
ERROR_NAME = "hfeAiError"
OBJ_SLOTS = ("hfeAiEcho", "hfeAiHist", "hfeAiShots", "hfeAiLastShot")
COMPILE_HELPER_CLASS = "HfeAfterimageCompile"
ROLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


class AfterimagePatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class AfterimageRole:
    role_id: str
    delays: tuple[int, ...]
    damage: bool = True
    items: bool = True


@dataclass(frozen=True)
class AfterimagePatchResult:
    output_swf: Path
    mode: str
    roles: tuple[AfterimageRole, ...]
    report: dict[str, Any] = field(default_factory=dict)


def normalize_delays(delays: Sequence[int]) -> tuple[int, ...]:
    out = sorted({int(value) for value in delays})
    for value in out:
        if not 1 <= value <= MAX_DELAY:
            raise ValueError(f"残影延迟必须在 1-{MAX_DELAY} 帧之间：{value}")
    return tuple(out)


def encode_config(roles: Sequence[AfterimageRole]) -> str:
    parts = []
    for role in roles:
        if not ROLE_ID_PATTERN.match(role.role_id):
            raise ValueError(f"角色 ID 不能用于残影配置：{role.role_id!r}")
        delays = normalize_delays(role.delays)
        if not delays:
            continue
        parts.append(
            f"{role.role_id}:{','.join(str(d) for d in delays)}:"
            f"{int(role.damage)}:{int(role.items)}"
        )
    return CONFIG_PREFIX + ";".join(parts)


def decode_config(text: str) -> tuple[AfterimageRole, ...]:
    if not text.startswith(CONFIG_PREFIX):
        raise ValueError("not an afterimage config string")
    body = text[len(CONFIG_PREFIX):]
    roles = []
    for part in filter(None, body.split(";")):
        fields = part.split(":")
        if len(fields) < 4:
            continue
        delays = tuple(int(v) for v in fields[1].split(",") if v.strip())
        roles.append(AfterimageRole(fields[0], delays, fields[2] == "1", fields[3] == "1"))
    return tuple(roles)


def merge_roles(
    existing: Sequence[AfterimageRole], new: Sequence[AfterimageRole]
) -> tuple[AfterimageRole, ...]:
    merged = {role.role_id: role for role in existing}
    for role in new:
        merged[role.role_id] = role
    return tuple(role for role in merged.values() if role.delays)


# --------------------------------------------------------------------- SWF I/O


@dataclass
class _Target:
    raw: bytes
    swf: Any
    index: int
    flags: int
    name: str
    abc: Any


def _load_target(path: Path) -> _Target:
    raw = path.read_bytes()
    swf = S.SwfFile.parse(raw)
    for index, tag in swf.find_tags(S.TAG_DOABC):
        flags, name, abc_bytes = S.parse_doabc(tag.body)
        abc = A.AbcFile.parse(abc_bytes)
        if abc.find_instance("Game", "World") is not None:
            if abc.build() != abc_bytes:
                raise AfterimagePatchError("目标主 ABC 无法逐字节往返，已停止残影补丁。")
            return _Target(raw, swf, index, flags, name, abc)
    raise AfterimagePatchError("目标 SWF 中没有找到 Game.World，无法安装残影。")


def _write_swf(target: _Target, abc: Any, output: Path) -> bytes:
    original_tags = [S.emit_tag(t) for t in target.swf.tags]
    target.swf.tags[target.index] = S.make_doabc_tag(
        abc.build(), target.name, bool(target.flags & 1)
    )
    for i, (tag, old) in enumerate(zip(target.swf.tags, original_tags)):
        if i != target.index and S.emit_tag(tag) != old:
            raise AssertionError(f"tag {i} changed outside the main ABC")
    data = bytes(target.swf.build())
    if target.raw[:3] == b"CWS":
        data = b"CWS" + data[3:8] + zlib.compress(data[8:], 9)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return data


# ------------------------------------------------------------------ ABC helpers


def _q(abc: Any, name: str, package: str = "") -> int:
    return int(abc.qname_idx(0x16, package, name))


def _body(abc: Any, cls: str, name: str) -> Any:
    mi, _trait = abc.find_class_method("Game", cls, name, static=False)
    if mi is None:
        raise AfterimagePatchError(f"目标缺少 Game.{cls}.{name}")
    return abc.body_of_method(mi)


def _exact_trait(abc: Any, cls: str, name: str) -> int:
    ci = abc.find_instance("Game", cls)
    found = [t for t in abc.instances[ci]["traits"] if abc.mn_name(t.name) == name]
    if len(found) != 1:
        raise AfterimagePatchError(f"Game.{cls}.{name} 期望 1 个声明，实际 {len(found)} 个")
    return int(found[0].name)


def _has_member(abc: Any, cls: str, name: str) -> bool:
    current = cls
    while True:
        ci = abc.find_instance("Game", current)
        if ci is None:
            return False
        if any(abc.mn_name(t.name) == name for t in abc.instances[ci]["traits"]):
            return True
        parent = abc.mn_name(abc.instances[ci]["super"])
        if parent not in ("Obj", "ObjH") or parent == current:
            return False
        current = parent


def _instructions(code: bytes) -> list[tuple[int, list[int]]]:
    return [(op, operands) for _, op, operands, _ in A.walk_code(code)]


def _catch_report(abc: Any, a: Any, service: str, catch_local: int, actor: bool) -> int:
    """Catch handler matching the engine's own catch activation shape."""
    handler = len(a.out)
    a.stack = 1
    a.getlocal(0)
    a.pushscope()
    a._op(0x5A, 0, stack_delta=1)
    a.dup()
    a.setlocal(catch_local)
    a.dup()
    a.pushscope()
    a.swap()
    a._op(0x6D, 1, stack_delta=-2)
    a.getlocal(0)
    if actor:
        a.getproperty(_q(abc, "world"))
    a.pushstring(service + ": ")
    a.getlocal(catch_local)
    a._op(0x6C, 1)
    a._op(0x70)
    a.add()
    a.callpropvoid(_q(abc, "hfeAiReport"), 1)
    a._op(0x1D)
    a._op(0x08, catch_local)
    return handler


def _finish_guarded(abc: Any, a: Any, cls: str, name: str, params: list[int],
                    start: int, end: int, catch: int, local_count: int,
                    return_type: str = "void") -> None:
    _, _, body = abc.add_class_method(
        "Game", cls, name, params, return_type=_q(abc, return_type),
        code=a.finish(), max_stack=max(4, a.max_stack), local_count=local_count,
    )
    body.max_scope_depth = body.init_scope_depth + 2
    body.exceptions = [(start, end, catch, 0, _q(abc, ERROR_NAME))]


def _prepare_interfaces(abc: Any) -> None:
    for name in OBJ_SLOTS:
        abc.add_instance_slot("Game", "Obj", name)
    abc.add_instance_slot("Game", "World", "hfeAiState")
    abc.add_instance_slot("Game", "World", "hfeAiFault", type_name="String")

    # World.hfeAiReport(msg): no dependency on the module, so it works even when
    # the verifier rejects the module class.
    a = A.Asm(abc)
    a.getlocal(0)
    a.pushscope()
    a.getlocal(0)
    a.pushstring(REPORT_PREFIX)
    a.getlocal(1)
    a._op(0x70)
    a.add()
    a.coerce_s()
    a.setproperty(_q(abc, "hfeAiFault"))
    a.getlex(_q(abc, "Global", "Data"))
    a.getlocal(0)
    a.getproperty(_q(abc, "hfeAiFault"))
    a.pushstring("FF5050")
    a.callpropvoid(_q(abc, "WriteLevelUpMessageToScreen"), 2)
    a.returnvoid()
    abc.add_class_method("Game", "World", "hfeAiReport", [0], return_type=_q(abc, "void"),
                         code=a.finish(), max_stack=a.max_stack)

    # Private engine functions reached through public bridges.
    obj_t = _q(abc, "Obj", "Game")
    objh_t = _q(abc, "ObjH", "Game")
    for name, native, params, returns_value in (
        ("hfeAiCheck", "CheckAttackBody", [obj_t, obj_t], True),
        ("hfeAiCatch", "CheckCatch", [objh_t, objh_t], True),
        ("hfeAiApply", "ImplementAttack", [obj_t], False),
    ):
        a = A.Asm(abc)
        a.getlocal(0)
        a.pushscope()
        a.getlocal(0)
        for i in range(len(params)):
            a.getlocal(i + 1)
        if returns_value:
            a.callproperty(_exact_trait(abc, "World", native), len(params))
            a.pop()
        else:
            a.callpropvoid(_exact_trait(abc, "World", native), len(params))
        a.returnvoid()
        abc.add_class_method("Game", "World", name, params, return_type=_q(abc, "void"),
                             code=a.finish(), max_stack=a.max_stack)

    a = A.Asm(abc)
    a.getlocal(0)
    a.pushscope()
    for name in ("nameBm", "nameBm_H"):
        a.getlocal(0)
        a.pushnull()
        a.setproperty(_exact_trait(abc, "ObjH", name))
    a.returnvoid()
    abc.add_class_method("Game", "ObjH", "hfeAiHideBars", [], return_type=_q(abc, "void"),
                         code=a.finish(), max_stack=a.max_stack)


def _world_bridge(abc: Any, name: str, service: str, extra_params: tuple[int, ...] = (),
                  gate_state: bool = False, half: bool | None = None) -> None:
    params = list(extra_params)
    a = A.Asm(abc)
    a.getlocal(0)
    a.pushscope()
    a.getlocal(0)
    a.getproperty(_q(abc, "hfeAiFault"))
    a.pushnull()
    a.ifne("done")
    if gate_state:
        a.getlocal(0)
        a.getproperty(_q(abc, "hfeAiState"))
        a.pushnull()
        a.ifeq("done")
    start = len(a.out)
    a.getlex(_q(abc, MODULE_CLASS, "Game"))
    a.getlocal(0)
    for i in range(len(params)):
        a.getlocal(i + 1)
    if half is not None:
        if half:
            a.pushtrue()
        else:
            a.pushfalse()
    a.callpropvoid(_q(abc, service), 1 + len(params) + (half is not None))
    end = len(a.out)
    a.jump("done")
    catch_local = len(params) + 1
    catch = _catch_report(abc, a, service, catch_local, actor=False)
    a.label("done")
    a.returnvoid()
    _finish_guarded(abc, a, "World", name, params, start, end, catch, catch_local + 1)


def _record_bridge(abc: Any) -> None:
    """ObjH.hfeAiRecordShot(): only owners with a history resolve the module."""
    a = A.Asm(abc)
    a.getlocal(0)
    a.pushscope()
    a.getlocal(0)
    a.getproperty(_q(abc, "world"))
    a.pushnull()
    a.ifeq("done")
    a.getlocal(0)
    a.getproperty(_q(abc, "world"))
    a.getproperty(_q(abc, "hfeAiFault"))
    a.pushnull()
    a.ifne("done")
    a.getlocal(0)
    a.getproperty(_q(abc, "hfeAiHist"))
    a.pushnull()
    a.ifeq("done")
    start = len(a.out)
    a.getlex(_q(abc, MODULE_CLASS, "Game"))
    a.getlocal(0)
    a.getlocal(0)
    a.getproperty(_exact_trait(abc, "ObjH", "newObjI"))
    a.callpropvoid(_q(abc, "recordShot"), 2)
    end = len(a.out)
    a.jump("done")
    catch = _catch_report(abc, a, "recordShot", 1, actor=True)
    a.label("done")
    a.returnvoid()
    _finish_guarded(abc, a, "ObjH", "hfeAiRecordShot", [], start, end, catch, 2)


def _draw_bridge(abc: Any, name: str, native: str, half: bool) -> None:
    """Obj.hfeAiDraw(bmp): guarded module pre-draw, then the native virtual Draw."""
    a = A.Asm(abc)
    a.getlocal(0)
    a.pushscope()
    a.getlocal(0)
    a.getproperty(_q(abc, "world"))
    a.pushnull()
    a.ifeq("native")
    a.getlocal(0)
    a.getproperty(_q(abc, "world"))
    a.getproperty(_q(abc, "hfeAiFault"))
    a.pushnull()
    a.ifne("native")
    a.getlocal(0)
    a.getproperty(_q(abc, "world"))
    a.getproperty(_q(abc, "hfeAiState"))
    a.pushnull()
    a.ifeq("native")
    start = len(a.out)
    a.getlex(_q(abc, MODULE_CLASS, "Game"))
    a.getlocal(0)
    a.getlocal(1)
    if half:
        a.pushtrue()
    else:
        a.pushfalse()
    a.callpropvoid(_q(abc, "drawBefore"), 3)
    end = len(a.out)
    a.jump("native")
    catch = _catch_report(abc, a, "drawBefore", 2, actor=True)
    a.label("native")
    a.getlocal(0)
    a.getlocal(1)
    a.callpropvoid(_q(abc, native), 1)
    a.returnvoid()
    _finish_guarded(abc, a, "Obj", name, [_q(abc, "BitmapData", "flash.display")],
                    start, end, catch, 3)


def _call_code(abc: Any, name: str, locals_: tuple[int, ...] = (0,)) -> tuple[bytes, int]:
    a = A.Asm(abc)
    for index in locals_:
        a.getlocal(index)
    a.callpropvoid(_q(abc, name), len(locals_) - 1)
    return a.finish(), a.max_stack


def _insert_after_pushscope(abc: Any, body: Any, name: str) -> None:
    code, stack = _call_code(abc, name)
    ins = {off: (op, args, nxt) for off, op, args, nxt in A.walk_code(body.code)}
    offset = 0
    seen = set()
    # Follow the obfuscator's always-taken prologue branch instead of a dead pushscope.
    while True:
        if offset in seen or offset not in ins:
            raise AfterimagePatchError("找不到方法入口的 pushscope")
        seen.add(offset)
        op, args, nxt = ins[offset]
        if op == 0x30:
            break
        always_taken = (offset == 5 and op == 0x14) or op == 0x10
        offset = nxt + args[0] if always_taken else nxt
    A.rewrite_code(body, before={nxt: _instructions(code)})
    body.max_stack = max(body.max_stack, stack)


def _install_hooks(abc: Any) -> list[str]:
    changes: list[str] = []
    _world_bridge(abc, "hfeAiTick", "tick")
    _record_bridge(abc)

    world_run = _body(abc, "World", "Run")
    code, stack = _call_code(abc, "hfeAiTick")
    A.append_tail(abc, world_run, code, stack)
    changes.append("World.Run: tail tick (after settlement and the ax/az flush)")

    target = _body(abc, "ObjH", "Run")
    instructions = list(A.walk_code(target.code))
    cre_i = _exact_trait(abc, "ObjH", "creI")
    sites = []
    for i, (off, op, args, _nxt) in enumerate(instructions):
        if op not in (0x61, 0x68) or args != [cre_i]:
            continue
        window = instructions[max(0, i - 25):i]
        for j, (_o, op2, args2, _n) in enumerate(window):
            if op2 == 0x66 and args2 == [cre_i] and any(
                op3 in (0x91, 0xC0) for _o3, op3, _a3, _n3 in window[j + 1:j + 4]
            ):
                sites.append(off)
                break
    if len(sites) != 1:
        raise AfterimagePatchError(f"ObjH.Run 物品生成循环自增点期望 1 处，实际 {len(sites)} 处")
    code, stack = _call_code(abc, "hfeAiRecordShot")
    A.rewrite_code(target, after={sites[0]: _instructions(code)})
    target.max_stack = max(target.max_stack, stack + 2)
    changes.append("ObjH.Run: record each emitted item at the creI increment")

    _world_bridge(abc, "hfeAiBeginDraw", "beginDraw", gate_state=True)
    bitmap = _q(abc, "BitmapData", "flash.display")
    draw_o = _exact_trait(abc, "World", "draw_o")
    for wrapper, native, half in (("hfeAiDraw", "Draw", False), ("hfeAiDrawH", "DrawH", True)):
        _draw_bridge(abc, wrapper, native, half)
        tail_name = "hfeAiEndDrawH" if half else "hfeAiEndDraw"
        _world_bridge(abc, tail_name, "endDraw", extra_params=(bitmap,),
                      gate_state=True, half=half)
        body = _body(abc, "World", native)
        draw_ins = list(A.walk_code(body.code))
        replacements = {}
        native_q = _q(abc, native)
        for i, (off, op, args, _nxt) in enumerate(draw_ins):
            if op == 0x4F and args == [native_q, 1]:
                context = draw_ins[max(0, i - 18):i]
                if any(p == 0x66 and ar == [draw_o] for _o, p, ar, _n in context):
                    replacements[off] = [(op, [_q(abc, wrapper), 1])]
        if len(replacements) != 4:
            raise AfterimagePatchError(
                f"World.{native} 的 draw_o 绘制点期望 4 处，实际 {len(replacements)} 处"
            )
        A.rewrite_code(body, replace=replacements)
        _insert_after_pushscope(abc, body, "hfeAiBeginDraw")
        code, stack = _call_code(abc, tail_name, (0, 1))
        A.append_tail(abc, body, code, stack)
        changes.append(f"World.{native}: Z-ordered translucent echo drawing (4 sites, guarded)")
    return changes


# --------------------------------------------------------------- compile step

COMPILE_HELPER_SOURCE = r"""
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.parser.script.AbcIndexing;
import com.jpexs.decompiler.flash.abc.avm2.parser.script.ActionScript3Parser;
import com.jpexs.decompiler.flash.configuration.Configuration;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

public final class HfeAfterimageCompile {
    public static void main(String[] args) throws Exception {
        Configuration.playerLibLocation.set(args[3]);
        Configuration.parallelSpeedUp.set(false);
        Configuration.cacheOnDisk.set(false);
        SWF.initPlayer();
        SWF swf;
        try (InputStream in = Files.newInputStream(Paths.get(args[0]))) {
            swf = new SWF(in, false);
        }
        ABC target = null;
        for (ABCContainerTag tag : swf.getAbcList()) {
            if (tag.getABC().findClassByName("Game.World") >= 0) {
                target = tag.getABC();
                break;
            }
        }
        if (target == null) {
            throw new IllegalStateException("Game.World ABC not found");
        }
        int classes = target.instance_info.size();
        int scripts = target.script_info.size();
        if (args.length < 5 || !args[4].equals("noop")) {
            AbcIndexing index = swf.getAbcIndex();
            index.selectAbc(target);
            String source = new String(
                    Files.readAllBytes(Paths.get(args[1])), StandardCharsets.UTF_8);
            new ActionScript3Parser(index).addScript(
                    source, args[1], classes, scripts, null, target);
        }
        try (OutputStream out = Files.newOutputStream(Paths.get(args[2]))) {
            target.saveToStream(out);
        }
        System.out.println("classes " + classes + " -> " + target.instance_info.size());
    }
}
"""


def _carrier(target: _Target, abc_bytes: bytes) -> bytes:
    """Only ABC / runtime metadata are needed for compiler symbol resolution."""
    tags = []
    for i, tag in enumerate(target.swf.tags):
        if tag.type not in (S.TAG_DOABC, 69, S.TAG_SHOWFRAME):
            continue
        if i == target.index:
            tag = S.make_doabc_tag(abc_bytes, target.name, bool(target.flags & 1))
        tags.append(tag)
    tags.append(S.Tag(S.TAG_END, b"", False))
    carrier = S.SwfFile(target.swf.sig, target.swf.version, target.swf.header_rest, tags)
    return bytes(carrier.build())


def _playerglobal(tools: ToolConfig) -> Path:
    bundled = tools.ffdec.parent / "flashlib" / "playerglobal32_0.swc"
    return bundled if bundled.is_file() else tools.playerglobal


def _compile(target: _Target, abc: Any, source: str, tools: ToolConfig, work: Path) -> Any:
    work.mkdir(parents=True, exist_ok=True)
    helper = work / f"{COMPILE_HELPER_CLASS}.java"
    helper.write_text(COMPILE_HELPER_SOURCE, encoding="utf-8")
    classpath = str(tools.ffdec)
    compiled = run_tool(
        ("javac", "-encoding", "UTF-8", "-cp", classpath, "-d", str(work), str(helper)), work
    )
    if not compiled.success:
        raise AfterimagePatchError(f"编译残影辅助程序失败：{compiled.stderr or compiled.stdout}")
    carrier = work / "afterimage_carrier.swf"
    carrier.write_bytes(_carrier(target, abc.build()))
    source_path = work / f"{MODULE_CLASS}.as"
    source_path.write_text(source, encoding="utf-8")
    output = work / "afterimage_compiled.abc"
    result = run_tool(
        ("java", "-Djava.awt.headless=true", "-Xmx2g", "-cp", f"{work}{os.pathsep}{classpath}",
         COMPILE_HELPER_CLASS, str(carrier), str(source_path), str(output),
         str(_playerglobal(tools))),
        work,
    )
    if not result.success or not output.is_file():
        raise AfterimagePatchError(f"编译残影模块失败：{result.stderr or result.stdout}")
    compiled_abc = A.AbcFile.parse(output.read_bytes())
    for attr in ("ints", "uints", "strings", "namespaces", "ns_sets", "multinames"):
        old = getattr(abc, attr)
        new = getattr(compiled_abc, attr)
        if new[:len(old)] != old:
            raise AfterimagePatchError(f"编译器改动了已有常量池 {attr}，已停止。")
    for old_body in abc.bodies:
        new_body = compiled_abc.body_of_method(old_body.method)
        if (new_body is None or old_body.code != new_body.code
                or old_body.exceptions != new_body.exceptions):
            raise AfterimagePatchError(f"编译器改动了原有方法 {old_body.method}，已停止。")
    if compiled_abc.find_instance("Game", MODULE_CLASS) is None:
        raise AfterimagePatchError("编译结果缺少残影模块类。")
    if len(compiled_abc.instances) != len(abc.instances) + 1:
        raise AfterimagePatchError("编译器添加了意外的类。")
    return compiled_abc


def module_source(abc: Any, roles: Sequence[AfterimageRole]) -> str:
    """AS3 source specialised for this target and role table."""
    source = AS3_SOURCE.replace('"@@CONFIG@@"', '"' + encode_config(roles) + '"')
    if "/*" in source:
        raise AssertionError("FFDec parses block comments as regex literals")
    lines = []
    for line in source.split("\n"):
        if TS_GATE_MARKER in line:
            if _has_member(abc, "World", "tsFrames") and _has_member(abc, "World", "tsTeam"):
                line = ("            return !w.playingDrama && "
                        "(w.tsFrames <= 0 || w.tsTeam == team);")
            else:
                line = "            return !w.playingDrama;"
        elif HFP_ROUTE_MARKER in line:
            if _has_member(abc, "World", "hfpRoute"):
                line = ('            w.hfpRoute = p.spt == Global.sptHash["ex_item"] '
                        '? "ex" : null;')
            else:
                continue
        lines.append(line)
    return "\n".join(lines)


def check_compiled_member_names(abc: Any, source: str) -> int:
    """Reject compiler-created names absent from the source (FFDec ``lengthxs``)."""
    identifiers = set(re.findall(r"[A-Za-z_$][\w$]*", source))
    ci = abc.find_instance("Game", MODULE_CLASS)
    traits = abc.instances[ci]["traits"] + abc.classes[ci][1]
    method_ids = {t.data[1] for t in traits if t.kind & 15 in (1, 2, 3)}
    method_ids.update((abc.instances[ci]["iinit"], abc.classes[ci][0]))
    errors = []
    checked = 0
    for mi in sorted(method_ids):
        body = abc.body_of_method(mi)
        if body is None:
            continue
        for off, op, args, _ in A.walk_code(body.code):
            if op not in (0x60, 0x66, 0x61, 0x68, 0x5D, 0x5E, 0x4A, 0x4F, 0x46):
                continue
            name = abc.mn_name(args[0])
            if name is None:
                continue
            checked += 1
            if name not in identifiers:
                errors.append((mi, off, name))
    if errors:
        raise AfterimagePatchError(f"编译结果出现源码里没有的成员名：{errors}")
    return checked


def _module_method_ids(abc: Any) -> set[int]:
    ci = abc.find_instance("Game", MODULE_CLASS)
    traits = abc.instances[ci]["traits"] + abc.classes[ci][1]
    ids = {t.data[1] for t in traits if t.kind & 15 in (1, 2, 3)}
    ids.update((abc.instances[ci]["iinit"], abc.classes[ci][0]))
    # The compiler appends one script whose init method declares the class.
    script_init = abc.scripts[-1][0]
    return ids | {script_init}


def _check_new_methods(abc: Any, first_new_method: int) -> int:
    """Native bridges and hooks must be straight-line and stack-valid.

    The compiled module class is excluded: it is a new class whose loops are
    ordinary compiler output (the backward-branch ban applies to code spliced
    into or added beside engine methods).
    """
    module = _module_method_ids(abc)
    checked = 0
    for body in abc.bodies:
        if body.method < first_new_method or body.method in module:
            continue
        instructions = list(A.walk_code(body.code))
        bounds = {off for off, _, _, _ in instructions} | {len(body.code)}
        for _off, op, args, nxt in instructions:
            if A.OPCODES[op][1] == ["s24"]:
                if args[0] < 0:
                    raise AfterimagePatchError(f"新增方法 {body.method} 含向后跳转")
                if nxt + args[0] not in bounds:
                    raise AfterimagePatchError(f"新增方法 {body.method} 跳转目标非法")
        result = verify(abc, body)
        if result["errors"]:
            raise AfterimagePatchError(f"新增方法 {body.method} 栈检查失败：{result['errors']}")
        checked += 1
    return checked


def _config_string_index(abc: Any) -> int:
    hits = [
        i for i, s in enumerate(abc.strings)
        if isinstance(s, str) and s.startswith(CONFIG_PREFIX)
    ]
    if len(hits) != 1:
        raise AfterimagePatchError(f"残影配置字符串期望 1 个，实际 {len(hits)} 个")
    return hits[0]


def read_installed_roles(path: Path) -> tuple[AfterimageRole, ...]:
    target = _load_target(path)
    if target.abc.find_instance("Game", MODULE_CLASS) is None:
        return ()
    return decode_config(target.abc.strings[_config_string_index(target.abc)])


def apply_afterimage(
    input_swf: Path,
    output_swf: Path,
    roles: Sequence[AfterimageRole],
    tools: ToolConfig,
    work_dir: Path,
) -> AfterimagePatchResult:
    target = _load_target(input_swf)
    original = target.abc
    if original.find_instance("Game", MODULE_CLASS) is not None:
        index = _config_string_index(original)
        existing = decode_config(original.strings[index])
        merged = merge_roles(existing, roles)
        abc = copy.deepcopy(original)
        abc.strings[index] = encode_config(merged)
        for name in ("hfeAiTick", "hfeAiBeginDraw", "hfeAiReport"):
            if not _has_member(abc, "World", name):
                raise AfterimagePatchError(f"目标已有残影模块但缺少挂点 {name}")
        _write_swf(target, abc, output_swf)
        return AfterimagePatchResult(
            output_swf, "updated", merged,
            {"previous_roles": [r.role_id for r in existing], "config": encode_config(merged)},
        )

    merged = merge_roles((), roles)
    if not merged:
        raise AfterimagePatchError("没有需要安装的残影。")
    for cls, name in (("Obj", "hfeAiEcho"), ("World", "hfeAiState")):
        if _has_member(original, cls, name):
            raise AfterimagePatchError(f"目标已声明 {cls}.{name}，但没有残影模块类")
    abc = copy.deepcopy(original)
    _prepare_interfaces(abc)
    source = module_source(abc, merged)
    compiled = _compile(target, abc, source, tools, work_dir)
    audited = check_compiled_member_names(compiled, source)
    changes = _install_hooks(compiled)
    checked_new = _check_new_methods(compiled, len(original.methods))
    modified = 0
    for old_body in original.bodies:
        new_body = compiled.body_of_method(old_body.method)
        if old_body.code == new_body.code:
            continue
        result = verify(compiled, new_body)
        if result["errors"]:
            raise AfterimagePatchError(
                f"被挂钩的原方法 {old_body.method} 校验失败：{result['errors']}"
            )
        modified += 1
    _write_swf(target, compiled, output_swf)
    report = {
        "config": encode_config(merged),
        "hooks": changes,
        "modified_engine_methods": modified,
        "new_bridge_methods_checked": checked_new,
        "compiled_member_accesses_audited": audited,
        "timestop_gate": _has_member(original, "World", "tsFrames"),
        "porter_route": _has_member(original, "World", "hfpRoute"),
        "alpha": ALPHA,
    }
    return AfterimagePatchResult(output_swf, "installed", merged, report)
