from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

ROUNDTRIP_CLASS_NAME = "HfeSwfRoundTrip"


@dataclass(frozen=True)
class SwfRoundTripResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def roundtrip_swf(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    output_exe: Path | None = None,
) -> SwfRoundTripResult:
    source_path = write_roundtrip_source(output_swf.parent)
    class_dir = output_swf.parent / "roundtrip_classes"
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
        return SwfRoundTripResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "roundtrip compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(
        ("java", "-cp", classpath, ROUNDTRIP_CLASS_NAME, str(input_swf), str(output_swf)),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return SwfRoundTripResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_roundtrip_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{ROUNDTRIP_CLASS_NAME}.java"
    path.write_text(ROUNDTRIP_JAVA_SOURCE, encoding="utf-8")
    return path


ROUNDTRIP_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;

public class HfeSwfRoundTrip {
    public static void main(String[] args) throws Exception {
        if (args.length != 2) {
            throw new IllegalArgumentException("Usage: HfeSwfRoundTrip <in.swf> <out.swf>");
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println("{\"success\":true}");
    }
}
'''

