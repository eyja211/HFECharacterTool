"""Tiny interpreter for the compiled ``Game.HfeAfterimage`` bytecode.

It runs the module's real emitted instructions over dict-backed objects with
spies for engine calls. It is not Flash: there is no verifier and no engine.
Unsupported opcodes and unmodeled calls raise, so a test can never pass by
silently skipping the code under test. Adapted from the Drew echo project's
``emitted_vm.py``.
"""

from __future__ import annotations

import math
from typing import Any

from hfe_character_tool.abc_vendor import abcpatch as A
from hfe_character_tool.afterimage_as3 import MODULE_CLASS

UNDEFINED = object()


class AVMPropertyError(RuntimeError):
    pass


def equal(left: Any, right: Any) -> bool:
    if left is None or left is UNDEFINED:
        return right is None or right is UNDEFINED
    if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
        return left is right
    if isinstance(left, object) and type(left) is object:
        return left is right
    return bool(left == right)


def text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if value is None:
        return "null"
    return str(value)


def avm_add(x: Any, y: Any) -> Any:
    if isinstance(x, str) or isinstance(y, str):
        return text(x) + text(y)
    return x + y


def truth(value: Any) -> bool:
    if value is None or value is UNDEFINED:
        return False
    if isinstance(value, (dict, list)) or type(value) is object:
        return True
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def to_int(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if math.isnan(number) or math.isinf(number):
        return 0
    return int(number)


def to_number(value: Any) -> float:
    if value is None:
        return 0.0
    if value is UNDEFINED:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


BINARY_OPS = {"equals", "strictequals", "lessthan", "lessequals", "greaterthan",
              "greaterequals", "add", "subtract", "multiply", "divide", "modulo",
              "add_i", "subtract_i", "multiply_i"}
UNARY_OPS = {"not", "increment", "decrement", "negate", "increment_i", "decrement_i",
             "negate_i"}


def binary(name: str, x: Any, y: Any) -> Any:
    if name in ("equals", "strictequals"):
        return equal(x, y)
    if name == "add":
        return avm_add(x, y)
    if name == "modulo":
        if isinstance(x, float) or isinstance(y, float):
            return math.fmod(x, y)
        return (abs(x) % abs(y)) * (1 if x >= 0 else -1)
    if name.endswith("_i"):
        return int(binary(name[:-2], x, y))
    table = {
        "lessthan": lambda a, b: a < b, "lessequals": lambda a, b: a <= b,
        "greaterthan": lambda a, b: a > b, "greaterequals": lambda a, b: a >= b,
        "subtract": lambda a, b: a - b, "multiply": lambda a, b: a * b,
        "divide": lambda a, b: a / b,
    }
    return table[name](x, y)


def unary(name: str, x: Any) -> Any:
    if name == "not":
        return not truth(x)
    if name == "negate":
        return -x
    if name == "negate_i":
        return -to_int(x)
    base = to_int(x) if name.endswith("_i") else to_number(x)
    return base + 1 if name.startswith("increment") else base - 1


def compare(name: str, x: Any, y: Any) -> bool:
    table = {
        "ifeq": lambda: equal(x, y), "ifne": lambda: not equal(x, y),
        "ifstricteq": lambda: equal(x, y), "ifstrictne": lambda: not equal(x, y),
        "iflt": lambda: x < y, "ifge": lambda: x >= y, "ifgt": lambda: x > y,
        "ifle": lambda: x <= y, "ifngt": lambda: not x > y,
        "ifnlt": lambda: not x < y, "ifnle": lambda: not x <= y,
        "ifnge": lambda: not x >= y,
    }
    return bool(table[name]())


class EmittedVM:
    def __init__(self, abc: Any, constructors: dict[str, Any] | None = None) -> None:
        self.abc = abc
        self.globals: dict[str, Any] = {
            "Math": {"abs": abs, "min": min, "max": max, "floor": math.floor,
                     "ceil": math.ceil, "sqrt": math.sqrt},
            "Number": {"MAX_VALUE": 1.7976931348623157e308},
            "Array": {"NUMERIC": 16},
            "Global": {"sptHash": {}},
            "C": {"_nor": 0, "_basic": 0, "_stand": 0, "_fall1": 6},
            "ObjH": {"_avm_class": "Game.ObjH"},
        }
        self.constructors: dict[str, Any] = {
            "Array": lambda *vals: ([UNDEFINED] * int(vals[0])
                                    if len(vals) == 1 and isinstance(vals[0], (int, float))
                                    else list(vals)),
        }
        self.constructors.update(constructors or {})
        self.builtins: dict[str, Any] = {
            "int": to_int, "Number": to_number, "Boolean": truth, "String": text,
            "isNaN": lambda v: math.isnan(to_number(v)),
        }
        ci = abc.find_instance("Game", MODULE_CLASS)
        if ci is None:
            raise AssertionError("compiled module class missing")
        self.methods: dict[str, int] = {}
        self.constants: dict[str, Any] = {}
        self.statics: dict[str, Any] = {}
        for trait in abc.classes[ci][1]:
            name = abc.mn_name(trait.name)
            kind = trait.kind & 15
            if kind in (1, 2, 3):
                self.methods[name] = trait.data[1]
            elif kind in (0, 6):
                _, _, idx, vkind = trait.data
                pools = {3: abc.ints, 4: abc.uints, 6: abc.doubles, 1: abc.strings}
                value = (None if idx == 0 else False if vkind == 10 else True
                         if vkind == 11 else pools[vkind][idx] if vkind in pools else None)
                (self.constants if kind == 6 else self.statics)[name] = value
        self.calls = 0

    def get(self, obj: Any, name: Any) -> Any:
        if obj is None or obj is UNDEFINED:
            raise AVMPropertyError(f"Error #1009/#1010 reading {name} on null")
        if obj is self.statics:
            if name in self.statics:
                return self.statics[name]
            if name in self.constants:
                return self.constants[name]
            raise AssertionError(f"unknown static {name}")
        if isinstance(obj, str):
            if name == "length":
                return len(obj)
            if name == "split":
                return lambda sep: obj.split(sep)
            if name == "substr":
                return lambda start, length=None: (obj[int(start):] if length is None
                                                   else obj[int(start):int(start) + int(length)])
            raise AssertionError(f"Unmodeled String.{name}")
        if isinstance(obj, list):
            if name == "length":
                return len(obj)
            if isinstance(name, (int, float)) or str(name).lstrip("-").isdigit():
                idx = int(name)
                return obj[idx] if 0 <= idx < len(obj) else UNDEFINED
            raise AssertionError(f"Unmodeled Array.{name}")
        if isinstance(obj, dict):
            return obj.get(name, UNDEFINED)
        raise AssertionError(f"Unsupported property {type(obj).__name__}.{name}")

    def call_list(self, obj: list[Any], member: str, vals: list[Any]) -> Any:
        if member == "indexOf":
            return next((i for i, x in enumerate(obj) if equal(x, vals[0])), -1)
        if member == "push":
            obj.extend(vals)
            return len(obj)
        if member == "concat":
            out = obj.copy()
            for extra in vals:
                out.extend(extra)
            return out
        if member == "sortOn":
            obj.sort(key=lambda x: x[vals[0]])
            return obj
        if member == "splice":
            start, length = int(vals[0]), int(vals[1])
            removed = obj[start:start + length]
            del obj[start:start + length]
            return removed
        raise AssertionError(f"Unmodeled Array.{member}")

    def run(self, method: str, *params: Any, budget: int = 200000) -> Any:
        self.calls += 1
        body = self.abc.body_of_method(self.methods[method])
        code = {off: (A.OPCODES[op][0], args, nxt) for off, op, args, nxt in A.walk_code(body.code)}
        local: list[Any] = [UNDEFINED] * max(body.local_count, len(params) + 1)
        local[0] = self.statics
        local[1:len(params) + 1] = params
        stack: list[Any] = []
        pc = 0

        def popn(n: int) -> list[Any]:
            vals = stack[-n:] if n else []
            if n:
                del stack[-n:]
            return vals

        for _ in range(budget):
            name, args, nxt = code[pc]
            if name in ("debug", "debugline", "debugfile", "label", "nop", "coerce",
                        "coerce_a", "coerce_s"):
                pass
            elif name.startswith("getlocal"):
                stack.append(local[args[0] if name == "getlocal" else int(name[-1])])
            elif name.startswith("setlocal"):
                local[args[0] if name == "setlocal" else int(name[-1])] = stack.pop()
            elif name == "kill":
                local[args[0]] = UNDEFINED
            elif name in ("inclocal_i", "declocal_i", "inclocal", "declocal"):
                local[args[0]] = to_number(local[args[0]]) + (1 if "inc" in name else -1)
                if name.endswith("_i"):
                    local[args[0]] = int(local[args[0]])
            elif name == "pushscope":
                stack.pop()
            elif name == "pushbyte":
                stack.append(args[0] if args[0] < 128 else args[0] - 256)
            elif name == "pushshort":
                value = args[0] & 65535
                stack.append(value if value < 32768 else value - 65536)
            elif name in ("pushint", "pushdouble", "pushstring", "pushuint"):
                pool = {"pushint": "ints", "pushdouble": "doubles", "pushstring": "strings",
                        "pushuint": "uints"}[name]
                stack.append(getattr(self.abc, pool)[args[0]])
            elif name in ("pushnull", "pushtrue", "pushfalse", "pushundefined", "pushnan"):
                stack.append({"pushnull": None, "pushtrue": True, "pushfalse": False,
                              "pushundefined": UNDEFINED, "pushnan": float("nan")}[name])
            elif name == "dup":
                stack.append(stack[-1])
            elif name == "pop":
                stack.pop()
            elif name == "swap":
                stack[-2:] = reversed(stack[-2:])
            elif name == "newarray":
                stack.append(popn(args[0]))
            elif name == "newobject":
                pairs = popn(args[0] * 2)
                stack.append(dict(zip(pairs[::2], pairs[1::2])))
            elif name in ("getproperty", "setproperty", "initproperty"):
                member = self.abc.mn_name(args[0])
                value = stack.pop() if name != "getproperty" else None
                if member is None:
                    member = stack.pop()
                obj = stack.pop()
                if name == "getproperty":
                    stack.append(self.get(obj, member))
                elif obj is self.statics:
                    self.statics[member] = value
                elif isinstance(obj, list):
                    idx = int(member)
                    while len(obj) <= idx:
                        obj.append(UNDEFINED)
                    obj[idx] = value
                elif isinstance(obj, dict):
                    obj[member] = value
                else:
                    raise AVMPropertyError(f"cannot write {member} on {obj!r}")
            elif name == "getlex":
                member = self.abc.mn_name(args[0])
                if member in self.constants:
                    stack.append(self.constants[member])
                elif member in self.statics:
                    stack.append(self.statics[member])
                elif member in self.globals:
                    stack.append(self.globals[member])
                else:
                    raise AssertionError(f"Unmodeled getlex {member}")
            elif name in ("findpropstrict", "findproperty"):
                member = self.abc.mn_name(args[0])
                if member in self.builtins:
                    stack.append(self.builtins)
                elif member in self.methods or member in self.statics or member in self.constants:
                    stack.append(self.statics)
                elif member in self.constructors or member in self.globals:
                    stack.append(self.globals.get(member, {"_avm_class": member}))
                else:
                    raise AssertionError(f"Unmodeled function {member}")
            elif name == "constructprop":
                vals = popn(args[1])
                stack.pop()
                member = self.abc.mn_name(args[0])
                if member not in self.constructors:
                    raise AssertionError(f"Unmodeled constructor {member}")
                stack.append(self.constructors[member](*vals))
            elif name in ("callproperty", "callpropvoid", "callproplex"):
                vals = popn(args[1])
                member = self.abc.mn_name(args[0])
                obj = stack.pop()
                if obj is self.statics:
                    result = self.run(member, *vals, budget=budget)
                elif obj is self.builtins:
                    result = self.builtins[member](*vals)
                elif isinstance(obj, list):
                    result = self.call_list(obj, member, vals)
                else:
                    fn = self.get(obj, member)
                    if not callable(fn):
                        raise AVMPropertyError(f"#1006 {member} is not a function")
                    result = fn(*vals)
                if name != "callpropvoid":
                    stack.append(result)
            elif name == "convert_i":
                stack.append(to_int(stack.pop()))
            elif name == "convert_d":
                stack.append(to_number(stack.pop()))
            elif name == "convert_b":
                stack.append(truth(stack.pop()))
            elif name == "convert_s":
                stack.append(text(stack.pop()))
            elif name == "istypelate":
                obj, cls = popn(2)
                stack.append(isinstance(obj, dict) and obj.get("_avm_type") == cls["_avm_class"])
            elif name in BINARY_OPS:
                x, y = popn(2)
                stack.append(binary(name, x, y))
            elif name in UNARY_OPS:
                stack.append(unary(name, stack.pop()))
            elif name == "jump":
                nxt += args[0]
            elif name in ("iftrue", "iffalse"):
                if truth(stack.pop()) == (name == "iftrue"):
                    nxt += args[0]
            elif name.startswith("if"):
                x, y = popn(2)
                if compare(name, x, y):
                    nxt += args[0]
            elif name == "returnvalue":
                return stack.pop()
            elif name == "returnvoid":
                return None
            else:
                raise AssertionError((method, pc, name))
            pc = nxt
        raise AssertionError(f"{method} instruction budget exhausted")
