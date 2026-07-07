from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

EXPERIENCE_BINDING_PATCH_CLASS_NAME = "HfeExperienceBindingPatch"


@dataclass(frozen=True)
class ExperienceBindingPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def patch_experience_binding(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    character_id: str,
    bound_character_id: str,
    output_exe: Path | None = None,
) -> ExperienceBindingPatchResult:
    source_path = write_experience_binding_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "experience_binding_patch_classes"
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
        return ExperienceBindingPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "experience binding patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(
        (
            "java",
            "-cp",
            classpath,
            EXPERIENCE_BINDING_PATCH_CLASS_NAME,
            str(input_swf),
            str(output_swf),
            character_id,
            bound_character_id,
        ),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return ExperienceBindingPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_experience_binding_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{EXPERIENCE_BINDING_PATCH_CLASS_NAME}.java"
    path.write_text(EXPERIENCE_BINDING_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


EXPERIENCE_BINDING_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.AVM2Code;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instructions;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.Multiname;
import com.jpexs.decompiler.flash.abc.types.Namespace;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.traits.Trait;
import com.jpexs.decompiler.flash.abc.types.traits.TraitMethodGetterSetter;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.Tag;
import com.jpexs.decompiler.graph.DottedChain;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.List;

public class HfeExperienceBindingPatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "Usage: HfeExperienceBindingPatch <in.swf> <out.swf> "
                            + "<characterId> <boundCharacterId>"
            );
        }
        String characterId = args[2];
        String boundCharacterId = args[3];
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);

        MethodRef login = findMethod(swf, "Data.Global", "LoginStatusMessage", true);
        if (login == null) {
            throw new IllegalStateException("Data.Global.static::LoginStatusMessage not found");
        }
        MethodRef genLevelUp = findMethod(swf, "Game.World", "GenLevelUpMsg", false);
        if (genLevelUp == null) {
            throw new IllegalStateException("Game.World.instance::GenLevelUpMsg not found");
        }

        boolean loginPatched = patchLoginExperienceBinding(
                login, characterId, boundCharacterId
        );
        int worldBranchesPatched = patchWorldSettlementBinding(
                genLevelUp, characterId, boundCharacterId
        );

        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"experience_binding\","
                        + "\"character_id\":\"" + jsonEscape(characterId) + "\","
                        + "\"bound_character_id\":\"" + jsonEscape(boundCharacterId) + "\","
                        + "\"login_patched\":" + loginPatched + ","
                        + "\"settlement_branches_patched\":" + worldBranchesPatched + "}"
        );
    }

    private static boolean patchLoginExperienceBinding(
            MethodRef ref,
            String characterId,
            String boundCharacterId
    ) {
        if (bodyPushesString(ref.abc, ref.body, characterId)) {
            return false;
        }
        Const c = new Const(ref.abc);
        AVM2Code code = ref.body.getCode();
        int insertAt = findInitPowCallInsertAt(code, c);
        List<AVM2Instruction> injected = new ArrayList<>();
        injected.add(ins(AVM2Instructions.GetLex, c.qPow));
        injected.add(ins(AVM2Instructions.PushString, c.string(characterId)));
        injected.add(ins(AVM2Instructions.GetProperty, c.mAny));
        injected.add(ins(AVM2Instructions.GetLex, c.qPow));
        injected.add(ins(AVM2Instructions.PushString, c.string(boundCharacterId)));
        injected.add(ins(AVM2Instructions.GetProperty, c.mAny));
        injected.add(ins(AVM2Instructions.GetProperty, c.qC));
        injected.add(ins(AVM2Instructions.SetProperty, c.qC));
        insertAll(code, ref.body, insertAt, injected);
        finish(ref, code, new ArrayList<PendingBranch>());
        return true;
    }

    private static int patchWorldSettlementBinding(
            MethodRef ref,
            String characterId,
            String boundCharacterId
    ) {
        if (bodyPushesString(ref.abc, ref.body, characterId)) {
            return 0;
        }
        int qTempCharId = findInstanceTraitQName(ref, "tempcharid");
        AVM2Code code = ref.body.getCode();
        code.markOffsets();
        List<Integer> insertions = findEason0SettlementInsertionPoints(
                ref.abc, code, qTempCharId
        );
        if (insertions.size() != 2) {
            throw new IllegalStateException(
                    "Expected 2 eason0 settlement branches in GenLevelUpMsg, found "
                            + insertions.size()
            );
        }
        Const c = new Const(ref.abc);
        List<PendingBranch> branches = new ArrayList<>();
        for (int i = insertions.size() - 1; i >= 0; i--) {
            List<AVM2Instruction> injected = buildTempCharBindingGuard(
                    qTempCharId, c.string(characterId), c.string(boundCharacterId), branches
            );
            insertAll(code, ref.body, insertions.get(i), injected);
        }
        finish(ref, code, branches);
        return insertions.size();
    }

    private static List<AVM2Instruction> buildTempCharBindingGuard(
            int qTempCharId,
            int characterString,
            int boundCharacterString,
            List<PendingBranch> branches
    ) {
        List<AVM2Instruction> out = new ArrayList<>();
        AVM2Instruction ifNotCharacter = ins(AVM2Instructions.IfNe, 0);
        AVM2Instruction after = ins(AVM2Instructions.Nop);
        out.add(ins(AVM2Instructions.GetLocal0));
        out.add(ins(AVM2Instructions.GetProperty, qTempCharId));
        out.add(ins(AVM2Instructions.PushString, characterString));
        out.add(ifNotCharacter);
        out.add(ins(AVM2Instructions.GetLocal0));
        out.add(ins(AVM2Instructions.PushString, boundCharacterString));
        out.add(ins(AVM2Instructions.InitProperty, qTempCharId));
        out.add(after);
        branches.add(new PendingBranch(ifNotCharacter, after));
        return out;
    }

    private static int findInitPowCallInsertAt(AVM2Code code, Const c) {
        for (int i = 0; i < code.code.size() - 1; i++) {
            if (isInstruction(code.code.get(i), "findpropstrict", c.qInitPow)
                    && isInstruction(code.code.get(i + 1), "callpropvoid", c.qInitPow)) {
                return i;
            }
        }
        throw new IllegalStateException("InitPow call not found in LoginStatusMessage");
    }

    private static List<Integer> findEason0SettlementInsertionPoints(
            ABC abc,
            AVM2Code code,
            int qTempCharId
    ) {
        List<Integer> insertions = new ArrayList<>();
        for (int i = 0; i < code.code.size() - 3; i++) {
            if ("getlocal0".equals(code.code.get(i).definition.instructionName)
                    && isInstruction(code.code.get(i + 1), "getproperty", qTempCharId)
                    && isPushString(abc, code.code.get(i + 2), "eason0")
                    && "ifne".equals(code.code.get(i + 3).definition.instructionName)) {
                insertions.add(indexForAddress(code, code.code.get(i + 3).getTargetAddress()));
            }
        }
        return insertions;
    }

    private static int indexForAddress(AVM2Code code, long address) {
        for (int i = 0; i < code.code.size(); i++) {
            if (code.code.get(i).getAddress() == address) {
                return i;
            }
        }
        throw new IllegalStateException("Branch target address not found: " + address);
    }

    private static MethodRef findMethod(
            SWF swf,
            String className,
            String methodName,
            boolean staticMethod
    ) throws Exception {
        for (ABCContainerTag tag : swf.getAbcList()) {
            ABC abc = tag.getABC();
            for (int ci = 0; ci < abc.instance_info.size(); ci++) {
                InstanceInfo ii = abc.instance_info.get(ci);
                String actualClass = abc.constants.getMultiname(ii.name_index)
                        .getNameWithNamespace(abc.constants, true).toString();
                if (!className.equals(actualClass)) {
                    continue;
                }
                List<Trait> traits = staticMethod
                        ? abc.class_info.get(ci).static_traits.traits
                        : abc.instance_info.get(ci).instance_traits.traits;
                for (Trait trait : traits) {
                    String actualMethod = abc.constants.getMultiname(trait.name_index)
                            .getName(
                                    abc.constants, new ArrayList<DottedChain>(), true, true
                            ).toString();
                    if (methodName.equals(actualMethod)
                            && trait instanceof TraitMethodGetterSetter) {
                        int methodInfo = ((TraitMethodGetterSetter) trait).method_info;
                        return new MethodRef(tag, abc, abc.findBody(methodInfo), ci);
                    }
                }
            }
        }
        return null;
    }

    private static int findInstanceTraitQName(MethodRef ref, String traitName) {
        for (Trait trait : ref.abc.instance_info.get(ref.classIndex).instance_traits.traits) {
            String name = ref.abc.constants.getMultiname(trait.name_index)
                    .getName(
                            ref.abc.constants,
                            new ArrayList<DottedChain>(),
                            true,
                            true
                    ).toString();
            if (traitName.equals(name)) {
                return trait.name_index;
            }
        }
        throw new IllegalStateException("Instance trait not found: " + traitName);
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

    private static void finish(
            MethodRef ref,
            AVM2Code code,
            List<PendingBranch> branches
    ) {
        int originalInitScopeDepth = ref.body.init_scope_depth;
        int originalMaxScopeDepth = ref.body.max_scope_depth;
        code.markOffsets();
        for (PendingBranch branch : branches) {
            branch.branch.setTargetOffset(
                    (int) (branch.target.getAddress() - (branch.branch.getAddress() + 4))
            );
        }
        ref.body.setCode(code);
        ref.body.autoFillStats(ref.abc, ref.abc.findBodyIndex(ref.body.method_info), false);
        ref.body.init_scope_depth = originalInitScopeDepth;
        ref.body.max_scope_depth = originalMaxScopeDepth;
        ref.abc.fireChanged();
        ref.tag.setABC(ref.abc);
        ((Tag) ref.tag).setModified(true);
    }

    private static AVM2Instruction ins(int opcode, int... operands) {
        return new AVM2Instruction(0, opcode, operands);
    }

    private static boolean isInstruction(AVM2Instruction ins, String name, int operand) {
        return name.equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == operand;
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static class Const {
        final ABC abc;
        final int qPow;
        final int qInitPow;
        final int qC;
        final int mAny;

        Const(ABC abc) {
            this.abc = abc;
            qPow = abc.constants.getPublicQnameId("pow", true);
            qInitPow = abc.constants.getPublicQnameId("InitPow", true);
            qC = abc.constants.getPublicQnameId("c", true);
            int nsPublic = abc.constants.getNamespaceId(Namespace.KIND_PACKAGE, "", 0, true);
            int nsSet = abc.constants.getNamespaceSetId(new int[]{nsPublic}, true);
            mAny = abc.constants.getMultinameId(Multiname.createMultinameL(false, nsSet), true);
        }

        int string(String value) {
            return abc.constants.getStringId(value, true);
        }
    }

    private static class PendingBranch {
        final AVM2Instruction branch;
        final AVM2Instruction target;

        PendingBranch(AVM2Instruction branch, AVM2Instruction target) {
            this.branch = branch;
            this.target = target;
        }
    }

    private static class MethodRef {
        final ABCContainerTag tag;
        final ABC abc;
        final MethodBody body;
        final int classIndex;

        MethodRef(ABCContainerTag tag, ABC abc, MethodBody body, int classIndex) {
            this.tag = tag;
            this.abc = abc;
            this.body = body;
            this.classIndex = classIndex;
        }
    }
}
'''
