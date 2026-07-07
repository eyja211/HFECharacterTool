from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

REGISTRY_ENTRY_PATCH_CLASS_NAME = "HfeRegistryEntryPatch"


@dataclass(frozen=True)
class RegistryEntryPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def patch_single_registry_entry(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    character_id: str,
    spt_class: str,
    lmi_class: str | None,
    lmi_resource_id: str | None = None,
    output_exe: Path | None = None,
) -> RegistryEntryPatchResult:
    source_path = write_registry_entry_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "registry_entry_patch_classes"
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
        return RegistryEntryPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "registry entry patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    command: tuple[str, ...] = (
        "java",
        "-cp",
        classpath,
        REGISTRY_ENTRY_PATCH_CLASS_NAME,
        str(input_swf),
        str(output_swf),
        character_id,
        spt_class,
    )
    if lmi_class is not None:
        command = (*command, lmi_class)
        if lmi_resource_id is not None:
            command = (*command, lmi_resource_id)
    run_result = run_tool(command, cwd)
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return RegistryEntryPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_registry_entry_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{REGISTRY_ENTRY_PATCH_CLASS_NAME}.java"
    path.write_text(REGISTRY_ENTRY_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


REGISTRY_ENTRY_PATCH_JAVA_SOURCE = r'''
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

public class HfeRegistryEntryPatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 4 && args.length != 5 && args.length != 6) {
            throw new IllegalArgumentException(
                    "Usage: HfeRegistryEntryPatch <in.swf> <out.swf> <id> "
                            + "<sptClass> [lmiClass] [lmiResourceId]"
            );
        }
        String lmiClass = args.length >= 5 ? args[4] : null;
        String lmiResourceId = args.length == 6 ? args[5] : args[2];
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        PatchTarget target = findGlobalCinit(swf);
        if (target == null) {
            throw new IllegalStateException("Data.Global static initializer not found");
        }
        injectSingleEntryArrays(target, args[2], args[3], lmiClass, lmiResourceId);
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"single_registry_entry\","
                        + "\"character_id\":\"" + jsonEscape(args[2]) + "\","
                        + "\"spt_class\":\"" + jsonEscape(args[3]) + "\","
                        + "\"lmi_class\":"
                        + (lmiClass == null ? "null" : "\"" + jsonEscape(lmiClass) + "\"")
                        + "}"
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
                    return new PatchTarget(tag, abc, body, ci);
                }
            }
        }
        return null;
    }

    private static void injectSingleEntryArrays(
            PatchTarget target,
            String characterId,
            String sptClass,
            String lmiClass,
            String lmiResourceId
    ) {
        ABC abc = target.abc;
        MethodBody body = target.body;
        AVM2Code code = body.getCode();
        RegistrySlots slots = ensureStaticArraySlots(target);
        int insertAt = findLastReturnVoid(code);
        if (insertAt < 0) {
            throw new IllegalStateException("Data.Global cinit returnvoid not found");
        }
        int sptClassQName = qualifiedClassQName(abc, sptClass);
        int lmiClassQName = lmiClass == null ? -1 : qualifiedClassQName(abc, lmiClass);
        int characterString = abc.constants.getStringId(characterId, true);
        int lmiResourceString = abc.constants.getStringId(lmiResourceId, true);
        int sptIds = abc.constants.getPublicQnameId("sptIds", true);
        int sptClasses = abc.constants.getPublicQnameId("sptClasses", true);
        int lmiIds = abc.constants.getPublicQnameId("lmiIds", true);
        int lmiClasses = abc.constants.getPublicQnameId("lmiClasses", true);
        int qPush = abc.constants.getPublicQnameId("push", true);

        if (slots.sptIdsExisted) {
            appendIdArray(code, body, insertAt, sptIds, qPush, characterString);
            insertAt += 3;
        } else {
            initIdArray(code, body, insertAt, sptIds, characterString);
            insertAt += 4;
        }
        if (slots.sptClassesExisted) {
            appendClassArray(code, body, insertAt, sptClasses, qPush, sptClassQName);
            insertAt += 4;
        } else {
            initClassArray(code, body, insertAt, sptClasses, sptClassQName);
            insertAt += 5;
        }
        if (lmiClass == null) {
            if (!slots.lmiIdsExisted) {
                initEmptyArray(code, body, insertAt, lmiIds);
                insertAt += 3;
            }
            if (!slots.lmiClassesExisted) {
                initEmptyArray(code, body, insertAt, lmiClasses);
            }
        } else {
            if (slots.lmiIdsExisted) {
                appendIdArray(code, body, insertAt, lmiIds, qPush, lmiResourceString);
                insertAt += 3;
            } else {
                initIdArray(code, body, insertAt, lmiIds, lmiResourceString);
                insertAt += 4;
            }
            if (slots.lmiClassesExisted) {
                appendClassArray(code, body, insertAt, lmiClasses, qPush, lmiClassQName);
            } else {
                initClassArray(code, body, insertAt, lmiClasses, lmiClassQName);
            }
        }
        finish(target, code);
    }

    private static RegistrySlots ensureStaticArraySlots(PatchTarget target) {
        return new RegistrySlots(
                ensureStaticArraySlot(target, "sptIds"),
                ensureStaticArraySlot(target, "sptClasses"),
                ensureStaticArraySlot(target, "lmiIds"),
                ensureStaticArraySlot(target, "lmiClasses")
        );
    }

    private static boolean ensureStaticArraySlot(PatchTarget target, String name) {
        ABC abc = target.abc;
        int nameQName = abc.constants.getPublicQnameId(name, true);
        if (hasStaticTrait(target, nameQName)) {
            return true;
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
        return false;
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

    private static void initIdArray(
            AVM2Code code,
            MethodBody body,
            int insertAt,
            int arrayQName,
            int characterString
    ) {
        code.insertInstruction(insertAt, ins(AVM2Instructions.FindProperty, arrayQName), body);
        code.insertInstruction(
                insertAt + 1, ins(AVM2Instructions.PushString, characterString), body
        );
        code.insertInstruction(insertAt + 2, ins(AVM2Instructions.NewArray, 1), body);
        code.insertInstruction(insertAt + 3, ins(AVM2Instructions.SetProperty, arrayQName), body);
    }

    private static void initClassArray(
            AVM2Code code,
            MethodBody body,
            int insertAt,
            int arrayQName,
            int classQName
    ) {
        code.insertInstruction(insertAt, ins(AVM2Instructions.FindProperty, arrayQName), body);
        code.insertInstruction(
                insertAt + 1, ins(AVM2Instructions.FindPropertyStrict, classQName), body
        );
        code.insertInstruction(
                insertAt + 2, ins(AVM2Instructions.ConstructProp, classQName, 0), body
        );
        code.insertInstruction(insertAt + 3, ins(AVM2Instructions.NewArray, 1), body);
        code.insertInstruction(insertAt + 4, ins(AVM2Instructions.SetProperty, arrayQName), body);
    }

    private static void initEmptyArray(
            AVM2Code code,
            MethodBody body,
            int insertAt,
            int arrayQName
    ) {
        code.insertInstruction(insertAt, ins(AVM2Instructions.FindProperty, arrayQName), body);
        code.insertInstruction(insertAt + 1, ins(AVM2Instructions.NewArray, 0), body);
        code.insertInstruction(insertAt + 2, ins(AVM2Instructions.SetProperty, arrayQName), body);
    }

    private static void appendIdArray(
            AVM2Code code,
            MethodBody body,
            int insertAt,
            int arrayQName,
            int qPush,
            int characterString
    ) {
        code.insertInstruction(insertAt, ins(AVM2Instructions.GetLex, arrayQName), body);
        code.insertInstruction(
                insertAt + 1, ins(AVM2Instructions.PushString, characterString), body
        );
        code.insertInstruction(insertAt + 2, ins(AVM2Instructions.CallPropVoid, qPush, 1), body);
    }

    private static void appendClassArray(
            AVM2Code code,
            MethodBody body,
            int insertAt,
            int arrayQName,
            int qPush,
            int classQName
    ) {
        code.insertInstruction(insertAt, ins(AVM2Instructions.GetLex, arrayQName), body);
        code.insertInstruction(
                insertAt + 1, ins(AVM2Instructions.FindPropertyStrict, classQName), body
        );
        code.insertInstruction(
                insertAt + 2, ins(AVM2Instructions.ConstructProp, classQName, 0), body
        );
        code.insertInstruction(insertAt + 3, ins(AVM2Instructions.CallPropVoid, qPush, 1), body);
    }

    private static int qualifiedClassQName(ABC abc, String qualifiedName) {
        int dot = qualifiedName.lastIndexOf('.');
        if (dot <= 0 || dot == qualifiedName.length() - 1) {
            throw new IllegalArgumentException(
                    "Class name must be package-qualified: " + qualifiedName
            );
        }
        String packageName = qualifiedName.substring(0, dot);
        String localName = qualifiedName.substring(dot + 1);
        return abc.constants.getQnameId(localName, Namespace.KIND_PACKAGE, packageName, true);
    }

    private static int findLastReturnVoid(AVM2Code code) {
        for (int i = code.code.size() - 1; i >= 0; i--) {
            if ("returnvoid".equals(code.code.get(i).definition.instructionName)) {
                return i;
            }
        }
        return -1;
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

    private static class RegistrySlots {
        final boolean sptIdsExisted;
        final boolean sptClassesExisted;
        final boolean lmiIdsExisted;
        final boolean lmiClassesExisted;

        RegistrySlots(
                boolean sptIdsExisted,
                boolean sptClassesExisted,
                boolean lmiIdsExisted,
                boolean lmiClassesExisted
        ) {
            this.sptIdsExisted = sptIdsExisted;
            this.sptClassesExisted = sptClassesExisted;
            this.lmiIdsExisted = lmiIdsExisted;
            this.lmiClassesExisted = lmiClassesExisted;
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
