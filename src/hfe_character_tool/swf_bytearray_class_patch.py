from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

BYTEARRAY_CLASS_PATCH_CLASS_NAME = "HfeByteArrayAssetClassPatch"


@dataclass(frozen=True)
class ByteArrayClassPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def add_bytearray_asset_classes(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    anchor_class: str,
    target_spt_class: str,
    target_lmi_class: str,
    output_exe: Path | None = None,
) -> ByteArrayClassPatchResult:
    source_path = write_bytearray_class_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "bytearray_class_patch_classes"
    class_dir.mkdir(exist_ok=True)
    classpath = _ffdec_classpath(tools)
    compile_result = run_tool(
        (
            "javac",
            "-encoding",
            "UTF-8",
            "-cp",
            classpath,
            "-d",
            str(class_dir),
            str(source_path),
        ),
        cwd,
    )
    if not compile_result.success:
        return ByteArrayClassPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "bytearray class patch compile failed"),
        )
    run_result = run_tool(
        (
            "java",
            "-cp",
            f"{class_dir};{classpath}",
            BYTEARRAY_CLASS_PATCH_CLASS_NAME,
            str(input_swf),
            str(output_swf),
            anchor_class,
            target_spt_class,
            target_lmi_class,
        ),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return ByteArrayClassPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_bytearray_class_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{BYTEARRAY_CLASS_PATCH_CLASS_NAME}.java"
    path.write_text(BYTEARRAY_CLASS_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


def _ffdec_classpath(tools: ToolConfig) -> str:
    lib_path = tools.ffdec.parent / "lib" / "ffdec_lib.jar"
    if lib_path.is_file():
        return f"{tools.ffdec};{lib_path}"
    return str(tools.ffdec)


BYTEARRAY_CLASS_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.parser.script.AbcIndexing;
import com.jpexs.decompiler.flash.abc.avm2.parser.script.ActionScript3Parser;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.Tag;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;

public class HfeByteArrayAssetClassPatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 5) {
            throw new IllegalArgumentException(
                    "Usage: HfeByteArrayAssetClassPatch <in.swf> <out.swf> "
                            + "<anchorClass> <targetSptClass> <targetLmiClass>"
            );
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        PatchTarget target = findAbcContainingClass(swf, args[2]);
        if (target == null) {
            throw new IllegalStateException("Anchor ABC class not found: " + args[2]);
        }
        addClassIfMissing(swf, target, args[3]);
        addClassIfMissing(swf, target, args[4]);
        target.abc.fireChanged();
        target.tag.setABC(target.abc);
        ((Tag) target.tag).setModified(true);
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"bytearray_asset_classes\","
                        + "\"target_spt_class\":\"" + jsonEscape(args[3]) + "\","
                        + "\"target_lmi_class\":\"" + jsonEscape(args[4]) + "\"}"
        );
    }

    private static void addClassIfMissing(SWF swf, PatchTarget target, String qualifiedName)
            throws Exception {
        if (hasClass(target.abc, qualifiedName)) {
            return;
        }
        AbcIndexing indexing = new AbcIndexing(swf);
        indexing.selectAbc(target.abc);
        ActionScript3Parser parser = new ActionScript3Parser(indexing);
        parser.addScript(
                byteArrayAssetSource(qualifiedName),
                qualifiedName,
                0,
                0,
                sourceFileName(qualifiedName),
                target.abc
        );
    }

    private static String byteArrayAssetSource(String qualifiedName) {
        int dot = qualifiedName.lastIndexOf('.');
        if (dot <= 0 || dot == qualifiedName.length() - 1) {
            throw new IllegalArgumentException(
                    "Class name must be package-qualified: " + qualifiedName
            );
        }
        String packageName = qualifiedName.substring(0, dot);
        String className = qualifiedName.substring(dot + 1);
        return "package " + packageName + " {"
                + " import mx.core.ByteArrayAsset;"
                + " public class " + className + " extends ByteArrayAsset {"
                + " public function " + className + "() { super(); }"
                + " }"
                + "}";
    }

    private static String sourceFileName(String qualifiedName) {
        return qualifiedName.replace('.', '/') + ".as";
    }

    private static PatchTarget findAbcContainingClass(SWF swf, String className) {
        for (ABCContainerTag tag : swf.getAbcList()) {
            ABC abc = tag.getABC();
            if (hasClass(abc, className)) {
                return new PatchTarget(tag, abc);
            }
        }
        return null;
    }

    private static boolean hasClass(ABC abc, String className) {
        for (int ci = 0; ci < abc.instance_info.size(); ci++) {
            InstanceInfo ii = abc.instance_info.get(ci);
            String actualClass = abc.constants.getMultiname(ii.name_index)
                    .getNameWithNamespace(abc.constants, true).toString();
            if (className.equals(actualClass)) {
                return true;
            }
        }
        return false;
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static class PatchTarget {
        final ABCContainerTag tag;
        final ABC abc;

        PatchTarget(ABCContainerTag tag, ABC abc) {
            this.tag = tag;
            this.abc = abc;
        }
    }
}
'''
