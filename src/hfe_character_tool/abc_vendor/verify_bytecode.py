"""Offline AVM2 control-flow checks with constant-branch pruning for this HF."""
from __future__ import annotations

from collections import deque
import math

from hfe_character_tool.abc_vendor import abcpatch as A

UNKNOWN = ("unknown",)


def runtime_parts(abc, mn):
    kind = abc.multinames[mn][0]
    return {0x0F: 1, 0x10: 1, 0x11: 2, 0x12: 2, 0x1B: 1, 0x1C: 1}.get(kind, 0)


def verify(abc, body):
    ins = {off: (op, args, nxt) for off, op, args, nxt in A.walk_code(body.code)}
    work = deque([(0, (), body.init_scope_depth)])
    states = {}
    errors = []
    peak = 0
    scope_peak = body.init_scope_depth
    while work:
        off, stack_, scope = work.popleft()
        if off not in ins:
            errors.append(f"invalid target {off}")
            continue
        previous = states.get(off)
        if previous is not None:
            if len(previous[0]) != len(stack_) or previous[1] != scope:
                errors.append(f"merge {off}: {len(previous[0])}/{previous[1]} vs {len(stack_)}/{scope}")
                continue
            merged = tuple(a if a == b else UNKNOWN for a, b in zip(previous[0], stack_))
            if merged == previous[0]:
                continue
            stack_ = merged
        states[off] = (stack_, scope)
        op, args, nxt = ins[off]
        for start, end, handler, _, _ in body.exceptions:
            if start <= off < end:
                work.append((handler,(UNKNOWN,),body.init_scope_depth))
        name = A.OPCODES[op][0]
        stack = list(stack_)
        successors = [nxt]

        def take(n=1):
            if len(stack) < n:
                raise ValueError(f"underflow: need {n}, have {len(stack)}")
            values = stack[len(stack)-n:] if n else []
            if n:
                del stack[-n:]
            return values

        def output(n, value=UNKNOWN):
            take(n)
            stack.append(value)

        try:
            if name in ("nop", "label", "debug", "debugline", "debugfile", "bkpt",
                        "bkptline", "kill", "inclocal", "declocal", "inclocal_i", "declocal_i"):
                pass
            elif name == "pushbyte":
                stack.append(args[0] if args[0] < 128 else args[0]-256)
            elif name == "pushshort":
                v=args[0]&65535
                stack.append(v if v<32768 else v-65536)
            elif name in ("pushint", "pushuint", "pushdouble"):
                stack.append(getattr(abc, {"pushint":"ints","pushuint":"uints","pushdouble":"doubles"}[name])[args[0]])
            elif name in ("pushtrue", "pushfalse", "pushnull", "pushundefined", "pushnan"):
                stack.append({"pushtrue":True, "pushfalse":False, "pushnull":None,
                              "pushundefined":UNKNOWN, "pushnan":float("nan")}[name])
            elif name == "pushstring":
                stack.append(abc.strings[args[0]])
            elif name in ("getlocal0","getlocal1","getlocal2","getlocal3","getlocal",
                          "getglobalscope","getscopeobject","getlex","pushnamespace",
                          "newactivation","newfunction","newcatch","finddef","getglobalslot"):
                stack.append(UNKNOWN)
            elif name.startswith("setlocal") or name in ("pop", "setglobalslot"):
                take()
            elif name == "dup":
                stack.append(stack[-1])
            elif name == "swap":
                stack[-2:] = reversed(stack[-2:])
            elif name in ("pushscope","pushwith"):
                take(); scope += 1
            elif name == "popscope":
                scope -= 1
            elif name in ("findproperty","findpropstrict"):
                output(runtime_parts(abc,args[0]))
            elif name in ("getproperty","getsuper","getdescendants"):
                output(1+runtime_parts(abc,args[0]))
            elif name in ("setproperty","initproperty","setsuper"):
                take(2+runtime_parts(abc,args[0]))
            elif name == "deleteproperty":
                output(1+runtime_parts(abc,args[0]))
            elif name == "getslot":
                output(1)
            elif name == "setslot":
                take(2)
            elif name == "newobject":
                output(args[0]*2)
            elif name == "newarray":
                output(args[0])
            elif name in ("callproperty","callproplex","callsuper","constructprop","callpropvoid","callsupervoid"):
                n=1+args[1]+runtime_parts(abc,args[0])
                if name.endswith("void"): take(n)
                else: output(n)
            elif name in ("callstatic","callmethod"):
                output(1+args[1])
            elif name == "call":
                output(2+args[0])
            elif name == "construct":
                output(1+args[0])
            elif name == "constructsuper":
                take(1+args[0])
            elif name == "newclass":
                output(1)
            elif name == "applytype":
                output(1+args[0])
            elif name == "hasnext2":
                stack.append(UNKNOWN)
            elif name in ("nextname","nextvalue","hasnext","astypelate","istypelate",
                          "instanceof","in"):
                output(2)
            elif name in ("coerce","coerce_a","coerce_s","astype","convert_i","convert_u",
                          "convert_d","convert_b","convert_s","convert_o","checkfilter",
                          "typeof","negate","negate_i","increment","increment_i","decrement",
                          "decrement_i","bitnot","istype","sxi1","sxi8","sxi16",
                          "esc_xelem","esc_xattr"):
                output(1)
            elif name == "not":
                value=take()[0]; stack.append(not value if value!=UNKNOWN else UNKNOWN)
            elif name in ("add","subtract","multiply","divide","modulo","lshift","rshift","urshift",
                          "bitand","bitor","bitxor","equals","strictequals","lessthan","lessequals",
                          "greaterthan","greaterequals","add_i","subtract_i","multiply_i"):
                x,y=take(2); value=UNKNOWN
                if x!=UNKNOWN and y!=UNKNOWN:
                    ops={"equals":lambda:x==y,"strictequals":lambda:type(x)==type(y) and x==y,
                         "lessthan":lambda:x<y,"lessequals":lambda:x<=y,
                         "greaterthan":lambda:x>y,"greaterequals":lambda:x>=y,
                         "add":lambda:x+y,"subtract":lambda:x-y,"multiply":lambda:x*y}
                    if name in ops:
                        try: value=ops[name]()
                        except (TypeError,ValueError,OverflowError): pass
                stack.append(value)
            elif name in ("returnvoid","returnvalue","throw"):
                if name != "returnvoid": take()
                if stack: errors.append(f"nonempty return stack {off}: {len(stack)}")
                successors=[]
            elif name == "jump":
                successors=[nxt+args[0]]
            elif name in ("iftrue","iffalse"):
                value=take()[0]
                if value==UNKNOWN: successors=[nxt,nxt+args[0]]
                else:
                    branch=bool(value) if name=="iftrue" else not bool(value)
                    successors=[nxt+args[0] if branch else nxt]
            elif name.startswith("if"):
                x,y=take(2); branch=UNKNOWN
                if x!=UNKNOWN and y!=UNKNOWN:
                    ops={"ifeq":lambda:x==y,"ifne":lambda:x!=y,
                         "ifstricteq":lambda:type(x)==type(y) and x==y,
                         "ifstrictne":lambda:type(x)!=type(y) or x!=y,
                         "iflt":lambda:x<y,"ifle":lambda:x<=y,
                         "ifgt":lambda:x>y,"ifge":lambda:x>=y,
                         "ifnlt":lambda:not x<y,"ifnle":lambda:not x<=y,
                         "ifngt":lambda:not x>y,"ifnge":lambda:not x>=y}
                    try: branch=ops[name]()
                    except (TypeError,ValueError): pass
                successors=[nxt,nxt+args[0]] if branch==UNKNOWN else [nxt+args[0] if branch else nxt]
            elif name=="lookupswitch":
                take(); successors=[off+n for n in args[:-1]]
            else:
                raise ValueError(f"unsupported {name}")
            peak=max(peak,len(stack));scope_peak=max(scope_peak,scope)
            if scope<body.init_scope_depth: errors.append(f"scope underflow {off}")
            for successor in successors:
                work.append((successor,tuple(stack),scope))
        except (ValueError,IndexError) as e:
            errors.append(f"{off} {name}: {e}")
    if peak>body.max_stack:errors.append(f"stack peak {peak} exceeds {body.max_stack}")
    if scope_peak>body.max_scope_depth:errors.append(f"scope peak {scope_peak} exceeds {body.max_scope_depth}")
    return {"method":body.method,"reachable":len(states),"peak":peak,
            "scope_peak":scope_peak,"errors":sorted(set(errors))}
