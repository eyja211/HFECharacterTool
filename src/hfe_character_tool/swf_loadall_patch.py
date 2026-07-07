from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

LOADALL_PATCH_CLASS_NAME = "HfeLoadAllDataPatch"


@dataclass(frozen=True)
class LoadAllDataPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def patch_loadall_custom_arrays(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    output_exe: Path | None = None,
) -> LoadAllDataPatchResult:
    source_path = write_loadall_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "loadall_patch_classes"
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
        return LoadAllDataPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "LoadAllData patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(
        ("java", "-cp", classpath, LOADALL_PATCH_CLASS_NAME, str(input_swf), str(output_swf)),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return LoadAllDataPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_loadall_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{LOADALL_PATCH_CLASS_NAME}.java"
    path.write_text(LOADALL_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


LOADALL_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.AVM2Code;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instructions;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.Multiname;
import com.jpexs.decompiler.flash.abc.types.Namespace;
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

public class HfeLoadAllDataPatch {
    private static final int LOOP_LOCAL = 14;

    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("Usage: HfeLoadAllDataPatch <in.swf> <out.swf>");
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        MethodRef loadAll = findMethod(swf, "Data.Global", "LoadAllData");
        if (loadAll == null) {
            throw new IllegalStateException("Data.Global.static::LoadAllData not found");
        }
        PatchSummary summary = injectCustomArrayLoaders(loadAll);
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"loadall_custom_array_loops\","
                        + "\"lmi_offset\":" + summary.lmiOffset + ","
                        + "\"spt_offset\":" + summary.sptOffset + ","
                        + "\"custom_loader_style\":\"expanded\","
                        + "\"lmi_added\":" + summary.lmiAdded + ","
                        + "\"spt_added\":" + summary.sptAdded + ","
                        + "\"max_regs\":" + loadAll.body.max_regs + "}"
        );
    }

    private static PatchSummary injectCustomArrayLoaders(MethodRef ref) throws Exception {
        Const c = new Const(ref.abc);
        AVM2Code code = ref.body.getCode();
        if (hasUnsafeRuntimeLoopCustomLoads(code, ref.abc, c)) {
            throw new IllegalStateException(
                    "Existing custom loader uses a runtime jump loop; "
                            + "please rebuild from a clean or stable target package"
            );
        }
        MethodBody cinit = ref.abc.findBody(ref.abc.class_info.get(ref.classIndex).cinit_index);
        AVM2Code registryCode = cinit.getCode();
        List<RegistryEntry> lmiEntries = collectRegistryEntries(
                registryCode, ref.abc, "lmiIds", "lmiClasses", c
        );
        List<RegistryEntry> sptEntries = collectRegistryEntries(
                registryCode, ref.abc, "sptIds", "sptClasses", c
        );
        int existingLmiLoads = customLoadCount(
                code, ref.abc, "LimbInfoFile", "lmiIds", "lmiClasses", c
        );
        int existingSptLoads = customLoadCount(
                code, ref.abc, "Spt", "sptIds", "sptClasses", c
        );
        int missingLmiLoads = Math.max(0, lmiEntries.size() - existingLmiLoads);
        int missingSptLoads = Math.max(0, sptEntries.size() - existingSptLoads);
        if (missingLmiLoads == 0 && missingSptLoads == 0) {
            return new PatchSummary(-1, -1, 0, 0);
        }
        int maxLmiOffset = findMaxLoadOffset(code, ref.abc, c, "lmi_data/");
        int maxSptOffset = findMaxLoadOffset(code, ref.abc, c, "spt_data/");
        if (maxLmiOffset < 0 || maxSptOffset < 0) {
            throw new IllegalStateException(
                    "Could not infer LMI/SPT load offsets from LoadAllData"
            );
        }
        int lmiInsertAt = findStageJumpInsertAt(code, c, maxLmiOffset);
        int sptInsertAt = findStageJumpInsertAt(code, c, maxSptOffset);

        List<AVM2Instruction> lmiCalls = buildExpandedLoaderCalls(
                c.qLimbInfoFileClass,
                c.qLmiIds,
                c.qLmiClasses,
                c.sLmiDir,
                c.sLmiExt,
                lmiEntries,
                existingLmiLoads,
                c
        );
        insertAll(code, ref.body, lmiInsertAt, lmiCalls);
        code.markOffsets();

        int adjustedSptInsertAt = sptInsertAt >= lmiInsertAt
                ? sptInsertAt + lmiCalls.size()
                : sptInsertAt;
        List<AVM2Instruction> sptCalls = buildExpandedLoaderCalls(
                c.qSptClass,
                c.qSptIds,
                c.qSptClasses,
                c.sSptDir,
                c.sSptExt,
                sptEntries,
                existingSptLoads,
                c
        );
        insertAll(code, ref.body, adjustedSptInsertAt, sptCalls);

        finish(ref, code);
        return new PatchSummary(maxLmiOffset, maxSptOffset, missingLmiLoads, missingSptLoads);
    }

    private static List<AVM2Instruction> buildExpandedLoaderCalls(
            int targetLoaderClass,
            int idsQName,
            int classesQName,
            int dirString,
            int extString,
            List<RegistryEntry> entries,
            int existingLoadCount,
            Const c
    ) {
        List<AVM2Instruction> out = new ArrayList<>();
        addExpandedLoaderCalls(
                out,
                targetLoaderClass,
                idsQName,
                classesQName,
                dirString,
                extString,
                entries,
                existingLoadCount,
                c
        );
        return out;
    }

    private static void addExpandedLoaderCalls(
            List<AVM2Instruction> out,
            int targetLoaderClass,
            int idsQName,
            int classesQName,
            int dirString,
            int extString,
            List<RegistryEntry> entries,
            int existingLoadCount,
            Const c
    ) {
        for (int index = 0; index < entries.size(); index++) {
            if (index < existingLoadCount) {
                continue;
            }
            addFixedLoaderCall(
                    out,
                    targetLoaderClass,
                    idsQName,
                    classesQName,
                    dirString,
                    extString,
                    entries.get(index).index,
                    c
            );
        }
    }

    private static void addFixedLoaderCall(
            List<AVM2Instruction> out,
            int targetLoaderClass,
            int idsQName,
            int classesQName,
            int dirString,
            int extString,
            int index,
            Const c
    ) {
        out.add(ins(AVM2Instructions.GetLex, targetLoaderClass));
        out.add(ins(AVM2Instructions.GetLex, classesQName));
        out.add(pushInt(index));
        out.add(ins(AVM2Instructions.GetProperty, c.mAny));
        out.add(ins(AVM2Instructions.GetLex, c.qByteArrayClass));
        out.add(ins(AVM2Instructions.AsTypeLate));
        out.add(ins(AVM2Instructions.PushString, dirString));
        out.add(ins(AVM2Instructions.GetLex, idsQName));
        out.add(pushInt(index));
        out.add(ins(AVM2Instructions.GetProperty, c.mAny));
        out.add(ins(AVM2Instructions.Add));
        out.add(ins(AVM2Instructions.PushString, extString));
        out.add(ins(AVM2Instructions.Add));
        out.add(ins(AVM2Instructions.CallPropVoid, c.qLoadFromCompressedBytes, 2));
    }

    private static boolean alreadyLoadsCustomArrays(AVM2Code code, ABC abc, Const c)
            throws Exception {
        return hasCustomArrayLoadBeforeCall(
                code, abc, "LimbInfoFile", "lmiIds", "lmiClasses", c
        ) && hasCustomArrayLoadBeforeCall(
                code, abc, "Spt", "sptIds", "sptClasses", c
        );
    }

    private static boolean hasCustomArrayLoadBeforeCall(
            AVM2Code code,
            ABC abc,
            String targetLoaderName,
            String idsName,
            String classesName,
            Const c
    ) throws Exception {
        for (int i = 0; i < code.code.size(); i++) {
            if (!isInstruction(code.code.get(i), "callpropvoid", c.qLoadFromCompressedBytes)) {
                continue;
            }
            if (hasCustomArrayOperandsBeforeCall(
                    code, i, abc, targetLoaderName, idsName, classesName
            )) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasCustomArrayOperandsBeforeCall(
            AVM2Code code,
            int callIndex,
            ABC abc,
            String targetLoaderName,
            String idsName,
            String classesName
    ) throws Exception {
        int classesIndex = callIndex - 12;
        int idsIndex = callIndex - 6;
        return classesIndex >= 0
                && idsIndex >= 0
                && isInstructionWithPublicName(
                        abc, code.code.get(classesIndex), "getlex", classesName
                )
                && isInstructionWithPublicName(
                        abc, code.code.get(idsIndex), "getlex", idsName
                );
    }

    private static boolean hasLoopProgressAfterCall(AVM2Code code, int callIndex, Const c) {
        int stop = Math.min(code.code.size(), callIndex + 12);
        boolean hasGetLocal = false;
        boolean hasIncrement = false;
        boolean hasSetLocal = false;
        boolean hasJump = false;
        for (int i = callIndex + 1; i < stop; i++) {
            AVM2Instruction candidate = code.code.get(i);
            hasGetLocal = hasGetLocal
                    || isInstruction(candidate, "getlocal", LOOP_LOCAL);
            hasIncrement = hasIncrement
                    || "increment_i".equals(candidate.definition.instructionName)
                    || "increment".equals(candidate.definition.instructionName);
            hasSetLocal = hasSetLocal
                    || isInstruction(candidate, "setlocal", LOOP_LOCAL);
            hasJump = hasJump || "jump".equals(candidate.definition.instructionName);
        }
        return hasGetLocal && hasIncrement && hasSetLocal && hasJump;
    }

    private static boolean hasUnsafeRuntimeLoopCustomLoads(AVM2Code code, ABC abc, Const c)
            throws Exception {
        return hasRuntimeLoopCustomLoad(
                code, abc, "LimbInfoFile", "lmiIds", "lmiClasses", c
        ) || hasRuntimeLoopCustomLoad(
                code, abc, "Spt", "sptIds", "sptClasses", c
        );
    }

    private static boolean hasRuntimeLoopCustomLoad(
            AVM2Code code,
            ABC abc,
            String targetLoaderName,
            String idsName,
            String classesName,
            Const c
    ) throws Exception {
        for (int i = 0; i < code.code.size(); i++) {
            if (!isInstruction(code.code.get(i), "callpropvoid", c.qLoadFromCompressedBytes)) {
                continue;
            }
            if (hasCustomArrayOperandsBeforeCall(
                    code, i, abc, targetLoaderName, idsName, classesName
            ) && hasLoopProgressAfterCall(code, i, c)) {
                return true;
            }
        }
        return false;
    }

    private static int customLoadCount(
            AVM2Code code,
            ABC abc,
            String targetLoaderName,
            String idsName,
            String classesName,
            Const c
    ) throws Exception {
        int count = 0;
        for (int i = 0; i < code.code.size(); i++) {
            if (!isInstruction(code.code.get(i), "callpropvoid", c.qLoadFromCompressedBytes)) {
                continue;
            }
            if (hasCustomArrayOperandsBeforeCall(
                    code, i, abc, targetLoaderName, idsName, classesName
            )) {
                count++;
            }
        }
        return count;
    }

    private static List<RegistryEntry> collectRegistryEntries(
            AVM2Code code,
            ABC abc,
            String idsName,
            String classesName,
            Const c
    ) throws Exception {
        int count = Math.min(
                countArrayEntries(code, abc, idsName, c),
                countArrayEntries(code, abc, classesName, c)
        );
        List<RegistryEntry> entries = new ArrayList<>();
        for (int index = 0; index < count; index++) {
            entries.add(new RegistryEntry(index));
        }
        return entries;
    }

    private static int countArrayEntries(AVM2Code code, ABC abc, String arrayName, Const c)
            throws Exception {
        int count = 0;
        for (int i = 0; i < code.code.size(); i++) {
            AVM2Instruction ins = code.code.get(i);
            if (isInstructionWithPublicName(abc, ins, "setproperty", arrayName)) {
                int size = newArraySizeBefore(code, i);
                if (size > 0) {
                    count += size;
                }
                continue;
            }
            if (isInstruction(ins, "callpropvoid", c.qPush)
                    && (
                            matchesIdArrayAppend(code, abc, i, arrayName)
                            || matchesClassArrayAppend(code, abc, i, arrayName)
                    )) {
                count++;
            }
        }
        return count;
    }

    private static boolean matchesIdArrayAppend(
            AVM2Code code,
            ABC abc,
            int callIndex,
            String arrayName
    ) throws Exception {
        int arrayIndex = callIndex - 2;
        int valueIndex = callIndex - 1;
        return arrayIndex >= 0
                && valueIndex >= 0
                && isInstructionWithPublicName(
                        abc, code.code.get(arrayIndex), "getlex", arrayName
                )
                && "pushstring".equals(code.code.get(valueIndex).definition.instructionName);
    }

    private static boolean matchesClassArrayAppend(
            AVM2Code code,
            ABC abc,
            int callIndex,
            String arrayName
    ) throws Exception {
        int arrayIndex = callIndex - 3;
        int constructIndex = callIndex - 1;
        return arrayIndex >= 0
                && constructIndex >= 0
                && isInstructionWithPublicName(
                        abc, code.code.get(arrayIndex), "getlex", arrayName
                )
                && "constructprop".equals(
                        code.code.get(constructIndex).definition.instructionName
                );
    }

    private static int newArraySizeBefore(AVM2Code code, int beforeIndex) {
        int start = Math.max(0, beforeIndex - 8);
        for (int i = beforeIndex - 1; i >= start; i--) {
            AVM2Instruction ins = code.code.get(i);
            if ("newarray".equals(ins.definition.instructionName)
                    && ins.operands != null
                    && ins.operands.length > 0) {
                return ins.operands[0];
            }
        }
        return -1;
    }

    private static int findMaxLoadOffset(
            AVM2Code code,
            ABC abc,
            Const c,
            String pathPrefix
    ) throws Exception {
        int maxOffset = -1;
        for (int i = 0; i < code.code.size(); i++) {
            AVM2Instruction ins = code.code.get(i);
            if (!isInstruction(ins, "callpropvoid", c.qLoadFromCompressedBytes)) {
                continue;
            }
            if (!hasPathPrefixBeforeCall(code, abc, i, pathPrefix)) {
                continue;
            }
            int offset = findConditionOffsetBeforeCall(code, c, i);
            if (offset > maxOffset) {
                maxOffset = offset;
            }
        }
        return maxOffset;
    }

    private static boolean hasPathPrefixBeforeCall(
            AVM2Code code,
            ABC abc,
            int callIndex,
            String pathPrefix
    ) throws Exception {
        int start = Math.max(0, callIndex - 12);
        for (int i = start; i < callIndex; i++) {
            String text = code.code.get(i).toStringNoAddress(
                    abc.constants, new ArrayList<DottedChain>()
            );
            if (text.contains("\"" + pathPrefix)) {
                return true;
            }
        }
        return false;
    }

    private static int findConditionOffsetBeforeCall(AVM2Code code, Const c, int callIndex) {
        int start = Math.max(0, callIndex - 20);
        for (int i = callIndex - 4; i >= start; i--) {
            if (matchesLoadCountConditionAt(code, c, i)) {
                return pushIntValue(code.code.get(i + 2));
            }
        }
        return -1;
    }

    private static int findStageJumpInsertAt(AVM2Code code, Const c, int sourceOffset) {
        for (int i = 0; i < code.code.size() - 9; i++) {
            if (!matchesLoadCountConditionAt(code, c, i)) {
                continue;
            }
            int candidateOffset = pushIntValue(code.code.get(i + 2));
            if (candidateOffset < sourceOffset || candidateOffset > sourceOffset + 5) {
                continue;
            }
            int searchStop = Math.min(code.code.size(), i + 90);
            for (int j = i + 5; j + 4 < searchStop; j++) {
                if (isInstruction(code.code.get(j), "findproperty", c.qLoadBinaryFileCount)
                        && isInstruction(code.code.get(j + 1), "getlex", c.qLoadtimeOffset)
                        && isPushInt(code.code.get(j + 2))
                        && "add".equals(code.code.get(j + 3).definition.instructionName)
                        && isInstruction(
                                code.code.get(j + 4), "setproperty", c.qLoadBinaryFileCount
                        )) {
                    return j;
                }
            }
        }
        throw new IllegalStateException(
                "Could not find LoadAllData stage jump after offset " + sourceOffset
        );
    }

    private static boolean matchesLoadCountConditionAt(AVM2Code code, Const c, int i) {
        if (i < 0 || i + 4 >= code.code.size()) {
            return false;
        }
        return isInstruction(code.code.get(i), "getlex", c.qLoadBinaryFileCount)
                && isInstruction(code.code.get(i + 1), "getlex", c.qLoadtimeOffset)
                && isPushInt(code.code.get(i + 2))
                && "add".equals(code.code.get(i + 3).definition.instructionName)
                && "ifne".equals(code.code.get(i + 4).definition.instructionName);
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

    private static void finish(MethodRef ref, AVM2Code code) {
        int originalInitScopeDepth = ref.body.init_scope_depth;
        int originalMaxScopeDepth = ref.body.max_scope_depth;
        code.markOffsets();
        for (PendingBranch branch : PendingBranch.pending) {
            branch.branch.setTargetOffset(
                    (int) (branch.target.getAddress() - (branch.branch.getAddress() + 4))
            );
        }
        PendingBranch.pending.clear();
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

    private static AVM2Instruction pushInt(int value) {
        if (value >= -128 && value <= 127) {
            return ins(AVM2Instructions.PushByte, value);
        }
        return ins(AVM2Instructions.PushShort, value);
    }

    private static boolean isInstruction(AVM2Instruction ins, String name, int operand) {
        return name.equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == operand;
    }

    private static boolean isInstructionWithPublicName(
            ABC abc,
            AVM2Instruction ins,
            String instructionName,
            String publicName
    ) throws Exception {
        if (!instructionName.equals(ins.definition.instructionName)
                || ins.operands == null
                || ins.operands.length == 0) {
            return false;
        }
        String text = ins.toStringNoAddress(abc.constants, new ArrayList<DottedChain>());
        return text.contains("\"" + publicName + "\"");
    }

    private static boolean isPushInt(AVM2Instruction ins) {
        return "pushbyte".equals(ins.definition.instructionName)
                || "pushshort".equals(ins.definition.instructionName);
    }

    private static int pushIntValue(AVM2Instruction ins) {
        if (!isPushInt(ins) || ins.operands == null || ins.operands.length == 0) {
            return -1;
        }
        return ins.operands[0];
    }

    private static MethodRef findMethod(
            SWF swf,
            String className,
            String methodName
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
                for (Trait trait : abc.class_info.get(ci).static_traits.traits) {
                    String actualMethod = abc.constants.getMultiname(trait.name_index)
                            .getName(
                                    abc.constants, new ArrayList<DottedChain>(), true, true
                            ).toString();
                    if (methodName.equals(actualMethod)
                            && trait instanceof TraitMethodGetterSetter) {
                        int methodInfo = ((TraitMethodGetterSetter) trait).method_info;
                        return new MethodRef(tag, abc, ci, abc.findBody(methodInfo));
                    }
                }
            }
        }
        return null;
    }

    private static class Const {
        final int qLoadBinaryFileCount;
        final int qLoadtimeOffset;
        final int qLoadFromCompressedBytes;
        final int qLength;
        final int qSptIds;
        final int qSptClasses;
        final int qLmiIds;
        final int qLmiClasses;
        final int qSptClass;
        final int qLimbInfoFileClass;
        final int qPush;
        final int qByteArrayClass;
        final int mAny;
        final int sSptDir;
        final int sSptExt;
        final int sLmiDir;
        final int sLmiExt;

        Const(ABC abc) {
            qLoadBinaryFileCount = abc.constants.getPublicQnameId("loadBinaryFileCount", true);
            qLoadtimeOffset = abc.constants.getPublicQnameId("loadtimeOffSet", true);
            qLoadFromCompressedBytes = abc.constants.getPublicQnameId(
                    "LoadFromCompressedBytes", true
            );
            qLength = abc.constants.getPublicQnameId("length", true);
            qSptIds = abc.constants.getPublicQnameId("sptIds", true);
            qSptClasses = abc.constants.getPublicQnameId("sptClasses", true);
            qLmiIds = abc.constants.getPublicQnameId("lmiIds", true);
            qLmiClasses = abc.constants.getPublicQnameId("lmiClasses", true);
            qSptClass = abc.constants.getQnameId("Spt", Namespace.KIND_PACKAGE, "Data", true);
            qLimbInfoFileClass = abc.constants.getQnameId(
                    "LimbInfoFile", Namespace.KIND_PACKAGE, "Data", true
            );
            qPush = abc.constants.getPublicQnameId("push", true);
            qByteArrayClass = abc.constants.getQnameId(
                    "ByteArray", Namespace.KIND_PACKAGE, "flash.utils", true
            );
            int nsPublic = abc.constants.getNamespaceId(Namespace.KIND_PACKAGE, "", 0, true);
            int nsSet = abc.constants.getNamespaceSetId(new int[]{nsPublic}, true);
            mAny = abc.constants.getMultinameId(Multiname.createMultinameL(false, nsSet), true);
            sSptDir = abc.constants.getStringId("spt_data/", true);
            sSptExt = abc.constants.getStringId(".spt", true);
            sLmiDir = abc.constants.getStringId("lmi_data/", true);
            sLmiExt = abc.constants.getStringId(".lmi", true);
        }
    }

    private static class PendingBranch {
        static final List<PendingBranch> pending = new ArrayList<>();
        final AVM2Instruction branch;
        final AVM2Instruction target;

        PendingBranch(AVM2Instruction branch, AVM2Instruction target) {
            this.branch = branch;
            this.target = target;
            pending.add(this);
        }
    }

    private static class PatchSummary {
        final int lmiOffset;
        final int sptOffset;
        final int lmiAdded;
        final int sptAdded;

        PatchSummary(int lmiOffset, int sptOffset, int lmiAdded, int sptAdded) {
            this.lmiOffset = lmiOffset;
            this.sptOffset = sptOffset;
            this.lmiAdded = lmiAdded;
            this.sptAdded = sptAdded;
        }
    }

    private static class RegistryEntry {
        final int index;

        RegistryEntry(int index) {
            this.index = index;
        }
    }

    private static class MethodRef {
        final ABCContainerTag tag;
        final ABC abc;
        final int classIndex;
        final MethodBody body;

        MethodRef(ABCContainerTag tag, ABC abc, int classIndex, MethodBody body) {
            this.tag = tag;
            this.abc = abc;
            this.classIndex = classIndex;
            this.body = body;
        }
    }
}
'''
