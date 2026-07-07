from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

BYTEARRAY_CLASS_CLONE_PATCH_CLASS_NAME = "HfeByteArrayAssetClassClonePatch"


@dataclass(frozen=True)
class ByteArrayClassClonePatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def clone_bytearray_asset_classes(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    source_spt_class: str,
    source_lmi_class: str,
    target_spt_class: str,
    target_lmi_class: str,
    output_exe: Path | None = None,
) -> ByteArrayClassClonePatchResult:
    source_path = write_bytearray_class_clone_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "bytearray_class_clone_patch_classes"
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
        return ByteArrayClassClonePatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "bytearray class clone patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(
        (
            "java",
            "-cp",
            classpath,
            BYTEARRAY_CLASS_CLONE_PATCH_CLASS_NAME,
            str(input_swf),
            str(output_swf),
            source_spt_class,
            source_lmi_class,
            target_spt_class,
            target_lmi_class,
        ),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return ByteArrayClassClonePatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_bytearray_class_clone_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{BYTEARRAY_CLASS_CLONE_PATCH_CLASS_NAME}.java"
    path.write_text(BYTEARRAY_CLASS_CLONE_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


BYTEARRAY_CLASS_CLONE_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.types.ClassInfo;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.Namespace;
import com.jpexs.decompiler.flash.abc.types.ScriptInfo;
import com.jpexs.decompiler.flash.abc.types.traits.Trait;
import com.jpexs.decompiler.flash.abc.types.traits.TraitClass;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.Tag;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.util.Arrays;

public class HfeByteArrayAssetClassClonePatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 6) {
            throw new IllegalArgumentException(
                    "Usage: HfeByteArrayAssetClassClonePatch <in.swf> <out.swf> "
                            + "<sourceSptClass> <sourceLmiClass> "
                            + "<targetSptClass> <targetLmiClass>"
            );
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        CloneSummary spt = cloneClass(swf, args[2], args[4]);
        CloneSummary lmi = cloneClass(swf, args[3], args[5]);
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"bytearray_asset_class_clone\","
                        + "\"spt_class\":\"" + jsonEscape(args[4]) + "\","
                        + "\"lmi_class\":\"" + jsonEscape(args[5]) + "\","
                        + "\"spt_class_index\":" + spt.classIndex + ","
                        + "\"lmi_class_index\":" + lmi.classIndex + "}"
        );
    }

    private static CloneSummary cloneClass(SWF swf, String sourceClass, String targetClass) {
        ClassRef source = findClass(swf, sourceClass);
        if (source == null) {
            throw new IllegalStateException("Source class not found: " + sourceClass);
        }
        if (hasClass(source.abc, targetClass)) {
            return new CloneSummary(source.abc.findClassByName(targetClass));
        }
        int targetQName = qualifiedClassQName(source.abc, targetClass);

        InstanceInfo sourceInstance = source.abc.instance_info.get(source.classIndex);
        InstanceInfo targetInstance = new InstanceInfo(sourceInstance.instance_traits.clone());
        targetInstance.name_index = targetQName;
        targetInstance.super_index = sourceInstance.super_index;
        targetInstance.flags = sourceInstance.flags;
        targetInstance.protectedNS = sourceInstance.protectedNS;
        targetInstance.interfaces = Arrays.copyOf(
                sourceInstance.interfaces,
                sourceInstance.interfaces.length
        );
        targetInstance.iinit_index = sourceInstance.iinit_index;

        ClassInfo sourceClassInfo = source.abc.class_info.get(source.classIndex);
        ClassInfo targetClassInfo = new ClassInfo(sourceClassInfo.static_traits.clone());
        targetClassInfo.cinit_index = sourceClassInfo.cinit_index;
        targetClassInfo.lastDispId = sourceClassInfo.lastDispId;

        int newClassIndex = source.abc.class_info.size();
        source.abc.addClass(targetClassInfo, targetInstance, source.scriptIndex);
        updateScriptTrait(source.abc, source.scriptIndex, newClassIndex, targetQName);
        source.abc.fireChanged();
        source.tag.setABC(source.abc);
        ((Tag) source.tag).setModified(true);
        return new CloneSummary(newClassIndex);
    }

    private static void updateScriptTrait(
            ABC abc,
            int scriptIndex,
            int classIndex,
            int targetQName
    ) {
        ScriptInfo script = abc.script_info.get(scriptIndex);
        for (Trait trait : script.traits.traits) {
            if (trait instanceof TraitClass) {
                TraitClass traitClass = (TraitClass) trait;
                if (traitClass.class_info == classIndex) {
                    traitClass.name_index = targetQName;
                    return;
                }
            }
        }
        throw new IllegalStateException("New script class trait not found");
    }

    private static ClassRef findClass(SWF swf, String className) {
        for (ABCContainerTag tag : swf.getAbcList()) {
            ABC abc = tag.getABC();
            for (int ci = 0; ci < abc.instance_info.size(); ci++) {
                String actualClass = classNameAt(abc, ci);
                if (!className.equals(actualClass)) {
                    continue;
                }
                int scriptIndex = findScriptIndexForClass(abc, ci);
                if (scriptIndex < 0) {
                    throw new IllegalStateException("Script trait not found for " + className);
                }
                return new ClassRef(tag, abc, ci, scriptIndex);
            }
        }
        return null;
    }

    private static int findScriptIndexForClass(ABC abc, int classIndex) {
        for (int si = 0; si < abc.script_info.size(); si++) {
            ScriptInfo script = abc.script_info.get(si);
            for (Trait trait : script.traits.traits) {
                if (trait instanceof TraitClass
                        && ((TraitClass) trait).class_info == classIndex) {
                    return si;
                }
            }
        }
        return -1;
    }

    private static boolean hasClass(ABC abc, String className) {
        for (int ci = 0; ci < abc.instance_info.size(); ci++) {
            if (className.equals(classNameAt(abc, ci))) {
                return true;
            }
        }
        return false;
    }

    private static String classNameAt(ABC abc, int classIndex) {
        InstanceInfo ii = abc.instance_info.get(classIndex);
        return abc.constants.getMultiname(ii.name_index)
                .getNameWithNamespace(abc.constants, true).toString();
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

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static class ClassRef {
        final ABCContainerTag tag;
        final ABC abc;
        final int classIndex;
        final int scriptIndex;

        ClassRef(ABCContainerTag tag, ABC abc, int classIndex, int scriptIndex) {
            this.tag = tag;
            this.abc = abc;
            this.classIndex = classIndex;
            this.scriptIndex = scriptIndex;
        }
    }

    private static class CloneSummary {
        final int classIndex;

        CloneSummary(int classIndex) {
            this.classIndex = classIndex;
        }
    }
}
'''
