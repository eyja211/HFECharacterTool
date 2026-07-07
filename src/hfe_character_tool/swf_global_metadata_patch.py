from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

GLOBAL_METADATA_PATCH_CLASS_NAME = "HfeGlobalMetadataPatch"


@dataclass(frozen=True)
class GlobalMetadataPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def patch_global_character_metadata(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    character_id: str,
    display_name: str,
    display_name_zh: str,
    hp: int,
    mp: int,
    stamina: int,
    character_index: int,
    description: str | None = None,
    source_character_id: str = "",
    fallback_character_index: int = -1,
    add_to_char_list: bool = True,
    output_exe: Path | None = None,
) -> GlobalMetadataPatchResult:
    source_path = write_global_metadata_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "global_metadata_patch_classes"
    class_dir.mkdir(exist_ok=True)
    compile_result = run_tool(
        (
            "javac",
            "-encoding",
            "UTF-8",
            "-cp",
            str(tools.ffdec),
            "-d",
            str(class_dir),
            str(source_path),
        ),
        cwd,
    )
    if not compile_result.success:
        return GlobalMetadataPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "global metadata patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(
        (
            "java",
            "-cp",
            classpath,
            GLOBAL_METADATA_PATCH_CLASS_NAME,
            str(input_swf),
            str(output_swf),
            character_id,
            display_name,
            display_name_zh,
            str(hp),
            str(mp),
            str(stamina),
            str(character_index),
            source_character_id,
            description or display_name,
            str(fallback_character_index),
            str(add_to_char_list).lower(),
        ),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return GlobalMetadataPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_global_metadata_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{GLOBAL_METADATA_PATCH_CLASS_NAME}.java"
    path.write_text(GLOBAL_METADATA_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


GLOBAL_METADATA_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.AVM2Code;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instructions;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.Multiname;
import com.jpexs.decompiler.flash.abc.types.Namespace;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.Tag;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

public class HfeGlobalMetadataPatch {
    public static void main(String[] args) throws Exception {
        if (args.length < 9 || args.length > 13) {
            throw new IllegalArgumentException(
                "Usage: HfeGlobalMetadataPatch <in.swf> <out.swf> <id> <name> <nameB5> "
                            + "<hp> <mp> <stamina> <index> [sourceCharacterId] [description] "
                            + "[fallbackIndex] [addToCharList]"
            );
        }
        String characterId = args[2];
        String displayName = args[3];
        String displayNameB5 = args[4];
        int hp = Integer.parseInt(args[5]);
        int mp = Integer.parseInt(args[6]);
        int stamina = Integer.parseInt(args[7]);
        int characterIndex = Integer.parseInt(args[8]);
        String sourceCharacterId = "";
        String description = displayName;
        int fallbackCharacterIndex = -1;
        boolean addToCharList = true;
        if (args.length == 10) {
            description = args[9];
        } else if (args.length >= 11) {
            sourceCharacterId = args[9];
            description = args[10];
            fallbackCharacterIndex = args.length >= 12 ? Integer.parseInt(args[11]) : -1;
            addToCharList = args.length >= 13 ? Boolean.parseBoolean(args[12]) : true;
        }

        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        PatchTarget target = findGlobalCinit(swf);
        if (target == null) {
            throw new IllegalStateException("Data.Global static initializer not found");
        }
        boolean sourcePowFound = false;
        boolean usedFallbackIndex = false;
        if (characterIndex == -2) {
            characterIndex = findNextPowIndex(target);
        }
        if (characterIndex < 0 && !sourceCharacterId.isEmpty()) {
            int sourceIndex = findSourcePowIndex(target, sourceCharacterId);
            if (sourceIndex >= 0) {
                characterIndex = sourceIndex;
                sourcePowFound = true;
            }
        }
        if (characterIndex < 0 && fallbackCharacterIndex >= 0) {
            characterIndex = fallbackCharacterIndex;
            usedFallbackIndex = true;
        }
        if (characterIndex < 0) {
            throw new IllegalStateException(
                    "Source character pow.i not found: " + sourceCharacterId
            );
        }
        injectMetadata(
                target, characterId, displayName, displayNameB5, description,
                hp, mp, stamina, characterIndex, addToCharList
        );
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"global_character_metadata\","
                        + "\"character_id\":\"" + jsonEscape(characterId) + "\","
                        + "\"source_character_id\":\"" + jsonEscape(sourceCharacterId) + "\","
                        + "\"index\":" + characterIndex + ","
                        + "\"source_pow_found\":" + sourcePowFound + ","
                        + "\"used_fallback_index\":" + usedFallbackIndex + ","
                        + "\"add_to_char_list\":" + addToCharList + ","
                        + "\"hp\":" + hp + ",\"mp\":" + mp + ",\"stamina\":" + stamina + "}"
        );
    }

    private static PatchTarget findGlobalCinit(SWF swf) {
        for (ABCContainerTag tag : swf.getAbcList()) {
            ABC abc = tag.getABC();
            for (int ci = 0; ci < abc.instance_info.size(); ci++) {
                InstanceInfo ii = abc.instance_info.get(ci);
                String className = abc.constants.getMultiname(ii.name_index)
                        .getNameWithNamespace(abc.constants, true).toString();
                if ("Data.Global".equals(className)) {
                    MethodBody body = abc.findBody(abc.class_info.get(ci).cinit_index);
                    return new PatchTarget(tag, abc, body);
                }
            }
        }
        return null;
    }

    private static int findSourcePowIndex(PatchTarget target, String sourceCharacterId) {
        Const c = new Const(target.abc);
        AVM2Code code = target.body.getCode();
        for (int i = 0; i < code.code.size(); i++) {
            if (!isPushString(target.abc, code.code.get(i), sourceCharacterId)) {
                continue;
            }
            if (!isPowReferenceNear(code, i, c.qPow)) {
                continue;
            }
            int objectEnd = findPowObjectEnd(code, i + 1);
            if (!hasPowObjectFields(target.abc, code, i + 1, objectEnd)) {
                continue;
            }
            int value = readObjectPairInt(target.abc, code, i + 1, objectEnd, "i");
            if (value >= 0) {
                return value;
            }
        }
        return -1;
    }

    private static int findNextPowIndex(PatchTarget target) {
        Const c = new Const(target.abc);
        AVM2Code code = target.body.getCode();
        int max = -1;
        for (int i = 0; i < code.code.size(); i++) {
            String roleId = pushedString(target.abc, code.code.get(i));
            if (!isRoleIdCandidate(roleId)) {
                continue;
            }
            if (!isPowReferenceNear(code, i, c.qPow)) {
                continue;
            }
            int objectEnd = findPowObjectEnd(code, i + 1);
            if (!hasPowObjectFields(target.abc, code, i + 1, objectEnd)) {
                continue;
            }
            int value = readObjectPairInt(target.abc, code, i + 1, objectEnd, "i");
            if (value > max) {
                max = value;
            }
        }
        return max + 1;
    }

    private static int findPowObjectEnd(AVM2Code code, int start) {
        int stop = Math.min(code.code.size(), start + 120);
        for (int i = start; i < stop; i++) {
            if ("newobject".equals(code.code.get(i).definition.instructionName)
                    && newObjectPairCount(code.code.get(i)) >= 10
                    && (i + 1 >= code.code.size() || isSetProperty(code.code.get(i + 1)))) {
                return i;
            }
        }
        return stop;
    }

    private static boolean hasPowObjectFields(
            ABC abc,
            AVM2Code code,
            int start,
            int stopExclusive
    ) {
        return hasObjectKey(abc, code, start, stopExclusive, "i")
                && hasObjectKey(abc, code, start, stopExclusive, "hp0")
                && hasObjectKey(abc, code, start, stopExclusive, "mp0")
                && hasObjectKey(abc, code, start, stopExclusive, "str0")
                && hasObjectKey(abc, code, start, stopExclusive, "state");
    }

    private static boolean hasObjectKey(
            ABC abc,
            AVM2Code code,
            int start,
            int stopExclusive,
            String key
    ) {
        int stop = Math.min(stopExclusive, code.code.size());
        for (int i = start; i < stop; i++) {
            if (isPushString(abc, code.code.get(i), key)) {
                return true;
            }
        }
        return false;
    }

    private static int readObjectPairInt(
            ABC abc,
            AVM2Code code,
            int start,
            int stopExclusive,
            String key
    ) {
        int stop = Math.min(stopExclusive, code.code.size());
        for (int i = start; i < stop; i++) {
            if (!isPushString(abc, code.code.get(i), key)) {
                continue;
            }
            int value = i + 1 < stop ? pushedInt(code.code.get(i + 1)) : -1;
            if (value >= 0) {
                return value;
            }
        }
        return -1;
    }

    private static boolean isPushString(ABC abc, AVM2Instruction ins, String value) {
        return "pushstring".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && value.equals(abc.constants.getString(ins.operands[0]));
    }

    private static String pushedString(ABC abc, AVM2Instruction ins) {
        if (!"pushstring".equals(ins.definition.instructionName)
                || ins.operands == null
                || ins.operands.length == 0) {
            return null;
        }
        return abc.constants.getString(ins.operands[0]);
    }

    private static boolean isRoleIdCandidate(String value) {
        if (value == null || value.isEmpty()) {
            return false;
        }
        if (isPowObjectKey(value)) {
            return false;
        }
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            boolean ok = (ch >= 'a' && ch <= 'z')
                    || (ch >= 'A' && ch <= 'Z')
                    || (ch >= '0' && ch <= '9')
                    || ch == '_';
            if (!ok) {
                return false;
            }
        }
        return true;
    }

    private static boolean isPowObjectKey(String value) {
        return "i".equals(value)
                || "hp0".equals(value)
                || "mp0".equals(value)
                || "str0".equals(value)
                || "lv".equals(value)
                || "hp".equals(value)
                || "mp".equals(value)
                || "str".equals(value)
                || "exp".equals(value)
                || "state".equals(value)
                || "hp_".equals(value)
                || "mp_".equals(value)
                || "str_".equals(value)
                || "lv_".equals(value)
                || "exp_".equals(value)
                || "c".equals(value)
                || "fake_hp".equals(value);
    }

    private static boolean isGetLex(AVM2Instruction ins, int qname) {
        return "getlex".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static boolean isPowReferenceNear(AVM2Code code, int beforeIndex, int qname) {
        int start = Math.max(0, beforeIndex - 8);
        for (int i = beforeIndex - 1; i >= start; i--) {
            AVM2Instruction ins = code.code.get(i);
            if (isPowAccess(ins, qname)) {
                return true;
            }
            String name = ins.definition.instructionName;
            if ("newarray".equals(name) || "newobject".equals(name) || "setproperty".equals(name)) {
                return false;
            }
        }
        return false;
    }

    private static boolean isPowAccess(AVM2Instruction ins, int qname) {
        String name = ins.definition.instructionName;
        return ("getlex".equals(name)
                || "getproperty".equals(name)
                || "findproperty".equals(name)
                || "findpropertystrict".equals(name))
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static boolean isSetProperty(AVM2Instruction ins) {
        return "setproperty".equals(ins.definition.instructionName);
    }

    private static void injectMetadata(
            PatchTarget target,
            String characterId,
            String displayName,
            String displayNameB5,
            String description,
            int hp,
            int mp,
            int stamina,
            int characterIndex,
            boolean addToCharList
    ) {
        Const c = new Const(target.abc);
        MethodBody body = target.body;
        AVM2Code code = body.getCode();
        int insertAt = findLastReturnVoid(code);
        if (insertAt < 0) {
            throw new IllegalStateException("Data.Global cinit returnvoid not found");
        }
        List<AVM2Instruction> injected = new ArrayList<>();
        if (addToCharList) {
            addCharListOrderPush(injected, c, characterId);
        }
        addPowEntry(injected, c, characterId, hp, mp, stamina, characterIndex);
        addLangWordsEntry(injected, c, characterId, displayName, displayNameB5);
        addLangWordsEntry(injected, c, characterId + "_desc", description, description);
        insertAll(code, body, insertAt, injected);
        finish(target, code);
    }

    private static void addCharListOrderPush(
            List<AVM2Instruction> out,
            Const c,
            String characterId
    ) {
        out.add(ins(AVM2Instructions.GetLex, c.qCharListOrder));
        out.add(ins(AVM2Instructions.PushString, c.string(characterId)));
        out.add(ins(AVM2Instructions.CallPropVoid, c.qPush, 1));
    }

    private static void addPowEntry(
            List<AVM2Instruction> out,
            Const c,
            String characterId,
            int hp,
            int mp,
            int stamina,
            int characterIndex
    ) {
        out.add(ins(AVM2Instructions.GetLex, c.qPow));
        out.add(ins(AVM2Instructions.PushString, c.string(characterId)));
        addPair(out, c, "i", characterIndex);
        addPair(out, c, "hp0", hp);
        addPair(out, c, "mp0", mp);
        addPair(out, c, "str0", stamina);
        addPair(out, c, "lv", 1);
        addPair(out, c, "hp", hp);
        addPair(out, c, "mp", mp);
        addPair(out, c, "str", stamina);
        addPair(out, c, "exp", 0);
        addPair(out, c, "state", "a");
        addPair(out, c, "hp_", hp);
        addPair(out, c, "mp_", mp);
        addPair(out, c, "str_", stamina);
        addPair(out, c, "lv_", 1);
        addPair(out, c, "exp_", 0);
        addPair(out, c, "c", 0);
        addPair(out, c, "fake_hp", 0);
        out.add(ins(AVM2Instructions.NewObject, 17));
        out.add(ins(AVM2Instructions.SetProperty, c.mAny));
    }

    private static void addLangWordsEntry(
            List<AVM2Instruction> out,
            Const c,
            String characterId,
            String displayName,
            String displayNameB5
    ) {
        out.add(ins(AVM2Instructions.GetLex, c.qLangWords));
        out.add(ins(AVM2Instructions.PushString, c.string(characterId)));
        addPair(out, c, "en", displayName);
        addPair(out, c, "b5", displayNameB5);
        out.add(ins(AVM2Instructions.NewObject, 2));
        out.add(ins(AVM2Instructions.SetProperty, c.mAny));
    }

    private static void addPair(List<AVM2Instruction> out, Const c, String key, int value) {
        out.add(ins(AVM2Instructions.PushString, c.string(key)));
        out.add(pushInt(value));
    }

    private static void addPair(List<AVM2Instruction> out, Const c, String key, String value) {
        out.add(ins(AVM2Instructions.PushString, c.string(key)));
        out.add(ins(AVM2Instructions.PushString, c.string(value)));
    }

    private static AVM2Instruction pushInt(int value) {
        if (value >= -128 && value <= 127) {
            return ins(AVM2Instructions.PushByte, value);
        }
        return ins(AVM2Instructions.PushShort, value);
    }

    private static int pushedInt(AVM2Instruction ins) {
        if (ins.operands == null || ins.operands.length == 0) {
            return -1;
        }
        if ("pushbyte".equals(ins.definition.instructionName)
                || "pushshort".equals(ins.definition.instructionName)
                || "pushint".equals(ins.definition.instructionName)) {
            return ins.operands[0];
        }
        return -1;
    }

    private static int newObjectPairCount(AVM2Instruction ins) {
        if (!"newobject".equals(ins.definition.instructionName)
                || ins.operands == null
                || ins.operands.length == 0) {
            return -1;
        }
        return ins.operands[0];
    }

    private static int findLastReturnVoid(AVM2Code code) {
        for (int i = code.code.size() - 1; i >= 0; i--) {
            if ("returnvoid".equals(code.code.get(i).definition.instructionName)) {
                return i;
            }
        }
        return -1;
    }

    private static void insertAll(
            AVM2Code code,
            MethodBody body,
            int insertAt,
            List<AVM2Instruction> injected
    ) {
        for (int i = 0; i < injected.size(); i++) {
            code.insertInstruction(insertAt + i, injected.get(i), body);
        }
    }

    private static void finish(PatchTarget target, AVM2Code code) {
        int originalInitScopeDepth = target.body.init_scope_depth;
        int originalMaxScopeDepth = target.body.max_scope_depth;
        code.markOffsets();
        target.body.setCode(code);
        target.body.autoFillStats(
                target.abc, target.abc.findBodyIndex(target.body.method_info), false
        );
        target.body.init_scope_depth = originalInitScopeDepth;
        target.body.max_scope_depth = originalMaxScopeDepth;
        target.abc.fireChanged();
        target.tag.setABC(target.abc);
        ((Tag) target.tag).setModified(true);
    }

    private static AVM2Instruction ins(int opcode, int... operands) {
        return new AVM2Instruction(0, opcode, operands);
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static class Const {
        final ABC abc;
        final int qCharListOrder;
        final int qPow;
        final int qLangWords;
        final int qPush;
        final int mAny;

        Const(ABC abc) {
            this.abc = abc;
            qCharListOrder = abc.constants.getPublicQnameId("charListOrder", true);
            qPow = abc.constants.getPublicQnameId("pow", true);
            qLangWords = abc.constants.getPublicQnameId("langWords", true);
            qPush = abc.constants.getPublicQnameId("push", true);
            int nsPublic = abc.constants.getNamespaceId(Namespace.KIND_PACKAGE, "", 0, true);
            int nsSet = abc.constants.getNamespaceSetId(new int[]{nsPublic}, true);
            mAny = abc.constants.getMultinameId(Multiname.createMultinameL(false, nsSet), true);
        }

        int string(String value) {
            return abc.constants.getStringId(value, true);
        }
    }

    private static class PatchTarget {
        final ABCContainerTag tag;
        final ABC abc;
        final MethodBody body;

        PatchTarget(ABCContainerTag tag, ABC abc, MethodBody body) {
            this.tag = tag;
            this.abc = abc;
            this.body = body;
        }
    }
}
'''
