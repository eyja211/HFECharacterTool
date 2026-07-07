from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

GLOBAL_PATCH_CLASS_NAME = "HfeGlobalRegistryPatch"


@dataclass(frozen=True)
class GlobalRegistryPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def patch_global_registry_arrays(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    output_exe: Path | None = None,
) -> GlobalRegistryPatchResult:
    source_path = write_global_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "global_patch_classes"
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
        return GlobalRegistryPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "global registry patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(
        ("java", "-cp", classpath, GLOBAL_PATCH_CLASS_NAME, str(input_swf), str(output_swf)),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return GlobalRegistryPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_global_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{GLOBAL_PATCH_CLASS_NAME}.java"
    path.write_text(GLOBAL_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


GLOBAL_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.AVM2Code;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instructions;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.Namespace;
import com.jpexs.decompiler.flash.abc.types.traits.Trait;
import com.jpexs.decompiler.flash.abc.types.traits.TraitSlotConst;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.Tag;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;

public class HfeGlobalRegistryPatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("Usage: HfeGlobalRegistryPatch <in.swf> <out.swf>");
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        PatchTarget target = findGlobalCinit(swf);
        if (target == null) {
            throw new IllegalStateException("Data.Global static initializer not found");
        }
        injectEmptyRegistryArrays(target);
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println("{\"success\":true,\"patched\":\"empty_global_registry_arrays\"}");
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
                    return new PatchTarget(tag, abc, body, ci);
                }
            }
        }
        return null;
    }

    private static void injectEmptyRegistryArrays(PatchTarget target) {
        ABC abc = target.abc;
        MethodBody body = target.body;
        AVM2Code code = body.getCode();
        ensureStaticArraySlots(target);
        int insertAt = findLastReturnVoid(code);
        if (insertAt < 0) {
            throw new IllegalStateException("Data.Global cinit returnvoid not found");
        }
        insertEmptyArray(code, body, insertAt, abc.constants.getPublicQnameId("sptIds", true));
        insertAt += 3;
        insertEmptyArray(code, body, insertAt, abc.constants.getPublicQnameId("sptClasses", true));
        insertAt += 3;
        insertEmptyArray(code, body, insertAt, abc.constants.getPublicQnameId("lmiIds", true));
        insertAt += 3;
        insertEmptyArray(code, body, insertAt, abc.constants.getPublicQnameId("lmiClasses", true));
        int originalInitScopeDepth = body.init_scope_depth;
        int originalMaxScopeDepth = body.max_scope_depth;
        code.markOffsets();
        body.setCode(code);
        body.autoFillStats(abc, abc.findBodyIndex(body.method_info), false);
        body.init_scope_depth = originalInitScopeDepth;
        body.max_scope_depth = originalMaxScopeDepth;
        abc.fireChanged();
        target.tag.setABC(abc);
        ((Tag) target.tag).setModified(true);
    }

    private static void ensureStaticArraySlots(PatchTarget target) {
        ensureStaticArraySlot(target, "sptIds");
        ensureStaticArraySlot(target, "sptClasses");
        ensureStaticArraySlot(target, "lmiIds");
        ensureStaticArraySlot(target, "lmiClasses");
    }

    private static void ensureStaticArraySlot(PatchTarget target, String name) {
        ABC abc = target.abc;
        int nameQName = abc.constants.getPublicQnameId(name, true);
        if (hasStaticTrait(target, nameQName)) {
            return;
        }
        TraitSlotConst slot = new TraitSlotConst();
        slot.name_index = nameQName;
        slot.kindType = Trait.TRAIT_SLOT;
        slot.kindFlags = 0;
        slot.slot_id = nextStaticSlotId(target);
        slot.type_index = abc.constants.getQnameId(
                "Array", Namespace.KIND_PACKAGE, "", true
        );
        slot.value_index = 0;
        slot.value_kind = 0;
        target.abc.class_info.get(target.classIndex).static_traits.addTrait(slot);
    }

    private static boolean hasStaticTrait(PatchTarget target, int nameQName) {
        for (Trait trait : target.abc.class_info.get(target.classIndex).static_traits.traits) {
            if (trait.name_index == nameQName) {
                return true;
            }
        }
        return false;
    }

    private static int nextStaticSlotId(PatchTarget target) {
        int max = 0;
        for (Trait trait : target.abc.class_info.get(target.classIndex).static_traits.traits) {
            if (trait instanceof TraitSlotConst) {
                max = Math.max(max, ((TraitSlotConst) trait).slot_id);
            }
        }
        return max + 1;
    }

    private static void insertEmptyArray(
            AVM2Code code, MethodBody body, int insertAt, int qname
    ) {
        code.insertInstruction(insertAt, ins(AVM2Instructions.FindProperty, qname), body);
        code.insertInstruction(insertAt + 1, ins(AVM2Instructions.NewArray, 0), body);
        code.insertInstruction(insertAt + 2, ins(AVM2Instructions.SetProperty, qname), body);
    }

    private static int findLastReturnVoid(AVM2Code code) {
        for (int i = code.code.size() - 1; i >= 0; i--) {
            if ("returnvoid".equals(code.code.get(i).definition.instructionName)) {
                return i;
            }
        }
        return -1;
    }

    private static AVM2Instruction ins(int opcode, int... operands) {
        return new AVM2Instruction(0, opcode, operands);
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
