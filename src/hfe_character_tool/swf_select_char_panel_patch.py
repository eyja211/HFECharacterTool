from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

SELECT_CHAR_PANEL_PATCH_CLASS_NAME = "HfeSelectCharPanelPatch"


@dataclass(frozen=True)
class SelectCharPanelPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def patch_select_char_panel_options(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    character_id: str,
    display_name: str,
    option_value: int,
    source_character_id: str = "",
    output_exe: Path | None = None,
) -> SelectCharPanelPatchResult:
    source_path = write_select_char_panel_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "select_char_panel_patch_classes"
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
        return SelectCharPanelPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "select char panel patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(
        (
            "java",
            "-cp",
            classpath,
            SELECT_CHAR_PANEL_PATCH_CLASS_NAME,
            str(input_swf),
            str(output_swf),
            character_id,
            display_name,
            str(option_value),
            source_character_id,
        ),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return SelectCharPanelPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_select_char_panel_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{SELECT_CHAR_PANEL_PATCH_CLASS_NAME}.java"
    path.write_text(SELECT_CHAR_PANEL_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


SELECT_CHAR_PANEL_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.AVM2Code;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instructions;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.traits.Trait;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.Tag;
import com.jpexs.decompiler.graph.DottedChain;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

public class HfeSelectCharPanelPatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 5 && args.length != 6) {
            throw new IllegalArgumentException(
                    "Usage: HfeSelectCharPanelPatch <in.swf> <out.swf> "
                            + "<id> <displayName> <optionValue> [sourceCharacterId]"
            );
        }
        String characterId = args[2];
        String displayName = args[3];
        int optionValue = Integer.parseInt(args[4]);
        String sourceCharacterId = args.length == 6 ? args[5] : "";

        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        PatchTarget target = findSelectCharPanelCinit(swf);
        if (target == null) {
            throw new IllegalStateException(
                    "Web_misc.SelectCharPanel static initializer not found"
            );
        }
        int charOptionQName = findStaticTraitQName(target, "charOption");
        boolean sourceOptionFound = false;
        boolean usedFallbackOptionValue = false;
        if (optionValue == -2) {
            optionValue = findNextCharOptionValue(target, charOptionQName);
            usedFallbackOptionValue = true;
        }
        if (optionValue < 0 && !sourceCharacterId.isEmpty()) {
            int sourceOptionValue = findSourceOptionValue(target, sourceCharacterId);
            if (sourceOptionValue >= 0) {
                optionValue = sourceOptionValue;
                sourceOptionFound = true;
            }
        }
        if (optionValue < 0) {
            optionValue = findNextCharOptionValue(target, charOptionQName);
            usedFallbackOptionValue = true;
        }
        boolean alreadyPresent = bodyPushesString(target.abc, target.body, characterId);
        if (!alreadyPresent) {
            injectCharOptionPush(target, charOptionQName, characterId, displayName, optionValue);
        }
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"select_char_panel_option\","
                        + "\"character_id\":\"" + jsonEscape(characterId) + "\","
                        + "\"display_name\":\"" + jsonEscape(displayName) + "\","
                        + "\"option_value\":" + optionValue + ","
                        + "\"source_character_id\":\"" + jsonEscape(sourceCharacterId) + "\","
                        + "\"source_option_found\":" + sourceOptionFound + ","
                        + "\"used_fallback_option_value\":" + usedFallbackOptionValue + ","
                        + "\"already_present\":" + alreadyPresent + "}"
        );
    }

    private static PatchTarget findSelectCharPanelCinit(SWF swf) {
        for (ABCContainerTag tag : swf.getAbcList()) {
            ABC abc = tag.getABC();
            for (int ci = 0; ci < abc.instance_info.size(); ci++) {
                InstanceInfo ii = abc.instance_info.get(ci);
                String className = abc.constants.getMultiname(ii.name_index)
                        .getNameWithNamespace(abc.constants, true).toString();
                if ("Web_misc.SelectCharPanel".equals(className)) {
                    MethodBody body = abc.findBody(abc.class_info.get(ci).cinit_index);
                    return new PatchTarget(tag, abc, body, ci);
                }
            }
        }
        return null;
    }

    private static int findStaticTraitQName(PatchTarget target, String traitName) {
        for (Trait trait : target.abc.class_info.get(target.classIndex).static_traits.traits) {
            String name = target.abc.constants.getMultiname(trait.name_index)
                    .getName(
                            target.abc.constants,
                            new ArrayList<DottedChain>(),
                            true,
                            true
                    ).toString();
            if (traitName.equals(name)) {
                return trait.name_index;
            }
        }
        throw new IllegalStateException(
                "Static trait not found on Web_misc.SelectCharPanel: " + traitName
        );
    }

    private static boolean bodyPushesString(ABC abc, MethodBody body, String value) {
        AVM2Code code = body.getCode();
        for (AVM2Instruction ins : code.code) {
            if (isPushString(abc, ins, value)) {
                return true;
            }
        }
        return false;
    }

    private static boolean isPushString(ABC abc, AVM2Instruction ins, String value) {
        return "pushstring".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && value.equals(abc.constants.getString(ins.operands[0]));
    }

    private static void injectCharOptionPush(
            PatchTarget target,
            int charOptionQName,
            String characterId,
            String displayName,
            int optionValue
    ) {
        Const c = new Const(target.abc);
        AVM2Code code = target.body.getCode();
        int insertAt = findLastReturnVoid(code);
        if (insertAt < 0) {
            throw new IllegalStateException("Web_misc.SelectCharPanel cinit returnvoid not found");
        }
        List<AVM2Instruction> injected = new ArrayList<>();
        injected.add(ins(AVM2Instructions.GetLex, charOptionQName));
        addPair(injected, c, "value", optionValue);
        addPair(injected, c, "name", displayName);
        addPair(injected, c, "id", characterId);
        injected.add(ins(AVM2Instructions.NewObject, 3));
        injected.add(ins(AVM2Instructions.CallPropVoid, c.qPush, 1));
        insertAll(code, target.body, insertAt, injected);
        finish(target, code);
    }

    private static int findSourceOptionValue(PatchTarget target, String sourceCharacterId) {
        AVM2Code code = target.body.getCode();
        for (int i = 0; i < code.code.size(); i++) {
            if (!isPushString(target.abc, code.code.get(i), sourceCharacterId)) {
                continue;
            }
            int value = findNearestValuePairBefore(target.abc, code, i);
            if (value >= 0) {
                return value;
            }
        }
        return -1;
    }

    private static int findNextCharOptionValue(PatchTarget target, int charOptionQName) {
        AVM2Code code = target.body.getCode();
        int max = -1;
        for (int i = 0; i < code.code.size(); i++) {
            if (!isPushString(target.abc, code.code.get(i), "id")) {
                continue;
            }
            int value = findNearestValuePairBefore(target.abc, code, i);
            if (value > max) {
                max = value;
            }
        }
        return max + 1;
    }

    private static int findNearestValuePairBefore(ABC abc, AVM2Code code, int beforeIndex) {
        int start = Math.max(0, beforeIndex - 80);
        for (int i = beforeIndex - 1; i >= start; i--) {
            if (isPushString(abc, code.code.get(i), "value")) {
                int value = findPushedIntAfter(code, i + 1, beforeIndex);
                if (value >= 0) {
                    return value;
                }
            }
        }
        return -1;
    }

    private static int findPushedIntAfter(AVM2Code code, int start, int stopExclusive) {
        int stop = Math.min(stopExclusive, code.code.size());
        for (int i = start; i < stop; i++) {
            int value = pushedInt(code.code.get(i));
            if (value >= 0) {
                return value;
            }
        }
        return -1;
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
        final int qPush;

        Const(ABC abc) {
            this.abc = abc;
            qPush = abc.constants.getPublicQnameId("push", true);
        }

        int string(String value) {
            return abc.constants.getStringId(value, true);
        }
    }

    private static class PatchTarget {
        final ABCContainerTag tag;
        final ABC abc;
        final MethodBody body;
        final int classIndex;

        PatchTarget(ABCContainerTag tag, ABC abc, MethodBody body, int classIndex) {
            this.tag = tag;
            this.abc = abc;
            this.body = body;
            this.classIndex = classIndex;
        }
    }
}
'''
