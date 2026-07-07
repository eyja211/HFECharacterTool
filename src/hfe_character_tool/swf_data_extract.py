from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool

DATA_GLOBAL_EXTRACT_CLASS_NAME = "HfeDataGlobalExtract"


@dataclass(frozen=True)
class DataGlobalExtractResult:
    java_source_path: Path
    class_dir: Path
    raw_binary_path: Path
    payload_path: Path
    metadata: Mapping[str, Any]
    compile_result: ToolResult
    run_result: ToolResult


def extract_data_global_symbol(
    input_swf: Path,
    class_name: str,
    tools: ToolConfig,
    output_dir: Path,
    cwd: Path,
) -> DataGlobalExtractResult:
    safe_name = _safe_class_name(class_name)
    raw_binary_path = output_dir / f"{safe_name}.bin"
    payload_path = output_dir / f"{safe_name}.payload.bin"
    source_path = write_data_global_extract_source(output_dir)
    class_dir = output_dir / "data_global_extract_classes"
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
        return DataGlobalExtractResult(
            source_path,
            class_dir,
            raw_binary_path,
            payload_path,
            {},
            compile_result,
            ToolResult(False, -1, "", "data global extract compile failed"),
        )
    run_result = run_tool(
        (
            "java",
            "-cp",
            f"{class_dir};{tools.ffdec}",
            DATA_GLOBAL_EXTRACT_CLASS_NAME,
            str(input_swf),
            class_name,
            str(raw_binary_path),
            str(payload_path),
        ),
        cwd,
    )
    metadata = _parse_json(run_result.stdout) if run_result.success else {}
    return DataGlobalExtractResult(
        source_path,
        class_dir,
        raw_binary_path,
        payload_path,
        metadata,
        compile_result,
        run_result,
    )


def write_data_global_extract_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{DATA_GLOBAL_EXTRACT_CLASS_NAME}.java"
    path.write_text(DATA_GLOBAL_EXTRACT_JAVA_SOURCE, encoding="utf-8")
    return path


def _safe_class_name(class_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in class_name)


def _parse_json(stdout: str) -> Mapping[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, Mapping) else {}


DATA_GLOBAL_EXTRACT_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.tags.DefineBinaryDataTag;
import com.jpexs.decompiler.flash.tags.SymbolClassTag;
import com.jpexs.decompiler.flash.tags.Tag;

import java.io.BufferedInputStream;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.zip.InflaterInputStream;

public class HfeDataGlobalExtract {
    public static void main(String[] args) throws Exception {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                    "Usage: HfeDataGlobalExtract <in.swf> <className> <raw.bin> <payload.bin>"
            );
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        SymbolClassTag symbolTag = symbolClassTagForClass(swf, args[1]);
        if (symbolTag == null) {
            throw new IllegalStateException("SymbolClass tag not found for " + args[1]);
        }
        DefineBinaryDataTag binary = binaryDataForSymbol(swf, symbolTag, args[1]);
        byte[] raw = binary.getDataBytes().getRangeData();
        writeBytes(args[2], raw);

        byte[] inflated = inflate(readAmf3ByteArray(raw).bytes);
        byte[] payload = inflated;
        String container = "";
        try {
            RawString fileType = readRawAmf0String(inflated, 0);
            Amf3ByteArray inner = readAmf3ByteArray(inflated, fileType.nextOffset);
            if (inner.nextOffset == inflated.length) {
                container = fileType.value;
                payload = inner.bytes;
            }
        } catch (RuntimeException ignored) {
            container = "";
        }
        writeBytes(args[3], payload);
        System.out.println(
                "{\"success\":true,"
                        + "\"class_name\":\"" + jsonEscape(args[1]) + "\","
                        + "\"symbol_id\":" + binary.getCharacterId() + ","
                        + "\"raw_size\":" + raw.length + ","
                        + "\"inflated_size\":" + inflated.length + ","
                        + "\"payload_size\":" + payload.length + ","
                        + "\"container\":\"" + jsonEscape(container) + "\"}"
        );
    }

    private static SymbolClassTag symbolClassTagForClass(SWF swf, String className) {
        for (Tag tag : swf.getTags()) {
            if (tag instanceof SymbolClassTag) {
                SymbolClassTag symbol = (SymbolClassTag) tag;
                for (String name : symbol.names) {
                    if (className.equals(name)) {
                        return symbol;
                    }
                }
            }
        }
        return null;
    }

    private static DefineBinaryDataTag binaryDataForSymbol(
            SWF swf,
            SymbolClassTag symbolTag,
            String className
    ) {
        int sourceId = symbolIdForClass(symbolTag, className);
        for (Tag tag : swf.getTags()) {
            if (tag instanceof DefineBinaryDataTag) {
                DefineBinaryDataTag binary = (DefineBinaryDataTag) tag;
                if (binary.getCharacterId() == sourceId) {
                    return binary;
                }
            }
        }
        throw new IllegalStateException("DefineBinaryData not found for class " + className);
    }

    private static int symbolIdForClass(SymbolClassTag symbolTag, String className) {
        for (int i = 0; i < symbolTag.names.size(); i++) {
            if (className.equals(symbolTag.names.get(i))) {
                return symbolTag.tags.get(i);
            }
        }
        throw new IllegalStateException("SymbolClass entry not found: " + className);
    }

    private static void writeBytes(String path, byte[] data) throws Exception {
        try (OutputStream os = new FileOutputStream(path)) {
            os.write(data);
        }
    }

    private static RawString readRawAmf0String(byte[] source, int offset) {
        if (offset + 2 > source.length) {
            throw new IllegalStateException("Raw AMF0 string length exceeds payload size");
        }
        int length = ((source[offset] & 0xff) << 8) | (source[offset + 1] & 0xff);
        int valueOffset = offset + 2;
        if (valueOffset + length > source.length) {
            throw new IllegalStateException("Raw AMF0 string exceeds payload size");
        }
        String value = new String(source, valueOffset, length, StandardCharsets.UTF_8);
        return new RawString(value, valueOffset + length);
    }

    private static Amf3ByteArray readAmf3ByteArray(byte[] source) {
        return readAmf3ByteArray(source, 0);
    }

    private static Amf3ByteArray readAmf3ByteArray(byte[] source, int offset) {
        if (source.length < offset + 2 || source[offset] != 0x0c) {
            throw new IllegalStateException("Expected AMF3 ByteArray marker");
        }
        U29Result result = readU29(source, offset + 1);
        if ((result.value & 1) == 0) {
            throw new IllegalStateException("ByteArray reference is not supported");
        }
        int length = result.value >> 1;
        if (result.nextOffset + length > source.length) {
            throw new IllegalStateException("ByteArray length exceeds payload");
        }
        return new Amf3ByteArray(slice(source, result.nextOffset, result.nextOffset + length),
                result.nextOffset + length);
    }

    private static U29Result readU29(byte[] source, int offset) {
        int current = source[offset++] & 0xff;
        if (current < 128) {
            return new U29Result(current, offset);
        }
        int value = (current & 0x7f) << 7;
        current = source[offset++] & 0xff;
        if (current < 128) {
            return new U29Result(value | current, offset);
        }
        value = (value | (current & 0x7f)) << 7;
        current = source[offset++] & 0xff;
        if (current < 128) {
            return new U29Result(value | current, offset);
        }
        value = (value | (current & 0x7f)) << 8;
        current = source[offset++] & 0xff;
        return new U29Result(value | current, offset);
    }

    private static byte[] inflate(byte[] source) {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            InflaterInputStream inflater = new InflaterInputStream(
                    new ByteArrayInputStream(source)
            );
            byte[] buffer = new byte[8192];
            int read;
            while ((read = inflater.read(buffer)) >= 0) {
                out.write(buffer, 0, read);
            }
            return out.toByteArray();
        } catch (Exception ex) {
            throw new IllegalStateException("zlib inflate failed", ex);
        }
    }

    private static byte[] slice(byte[] source, int start, int end) {
        byte[] out = new byte[end - start];
        System.arraycopy(source, start, out, 0, out.length);
        return out;
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static class RawString {
        final String value;
        final int nextOffset;

        RawString(String value, int nextOffset) {
            this.value = value;
            this.nextOffset = nextOffset;
        }
    }

    private static class Amf3ByteArray {
        final byte[] bytes;
        final int nextOffset;

        Amf3ByteArray(byte[] bytes, int nextOffset) {
            this.bytes = bytes;
            this.nextOffset = nextOffset;
        }
    }

    private static class U29Result {
        final int value;
        final int nextOffset;

        U29Result(int value, int nextOffset) {
            this.value = value;
            this.nextOffset = nextOffset;
        }
    }
}
'''
