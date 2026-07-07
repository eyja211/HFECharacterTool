from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from hfe_character_tool.hfworkshop_catalog import (
    SptActionInfo,
    resolve_action_frame_index,
    resolve_action_frame_index_from_options,
)
from hfe_character_tool.models import FrameItemEdit
from hfe_character_tool.tools import ToolConfig, ToolResult, run_tool, wrap_with_projector

BINARY_SYMBOL_PATCH_CLASS_NAME = "HfeBinarySymbolClonePatch"


@dataclass(frozen=True)
class BinarySymbolPatchResult:
    java_source_path: Path
    class_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def clone_binary_symbols(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    source_spt_class: str,
    source_lmi_class: str,
    target_spt_class: str,
    target_lmi_class: str,
    target_character_id: str | None = None,
    resource_id: str | None = None,
    defense_brk0: int = 0,
    item_frame_edits: tuple[FrameItemEdit, ...] = (),
    texture_replacements: Mapping[str, str] | None = None,
    workspace: Path | None = None,
    source_character_id: str = "lucas",
    spt_actions: tuple[SptActionInfo, ...] | None = None,
    output_exe: Path | None = None,
) -> BinarySymbolPatchResult:
    source_path = write_binary_symbol_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "binary_symbol_patch_classes"
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
        return BinarySymbolPatchResult(
            source_path,
            class_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "binary symbol patch compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    command: tuple[str, ...] = (
        "java",
        "-cp",
        classpath,
        BINARY_SYMBOL_PATCH_CLASS_NAME,
        str(input_swf),
        str(output_swf),
        source_spt_class,
        source_lmi_class,
        target_spt_class,
        target_lmi_class,
    )
    if target_character_id is not None:
        command = (
            *command,
            target_character_id,
            resource_id or target_character_id,
            str(defense_brk0),
            _encode_frame_item_edits(item_frame_edits, workspace or cwd, spt_actions),
            _encode_texture_replacements(texture_replacements or {}),
            source_character_id,
        )
    run_result = run_tool(command, cwd)
    actual_exe = None
    if run_result.success and output_exe is not None:
        wrap_with_projector(tools.projector, output_swf, output_exe)
        actual_exe = output_exe
    return BinarySymbolPatchResult(
        source_path,
        class_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def _encode_texture_replacements(replacements: Mapping[str, str]) -> str:
    return "|".join(f"{source}=>{target}" for source, target in sorted(replacements.items()))


def _encode_frame_item_edits(
    edits: tuple[FrameItemEdit, ...],
    workspace: Path,
    spt_actions: tuple[SptActionInfo, ...] | None = None,
) -> str:
    encoded_edits: list[str] = []
    for edit in edits:
        enabled_slots = tuple(slot for slot in edit.slots if slot.enabled)
        if not enabled_slots:
            continue
        frame_index = (
            resolve_action_frame_index_from_options(
                spt_actions,
                edit.action_name,
                edit.action_frame,
            )
            if spt_actions is not None
            else resolve_action_frame_index(workspace, edit.action_name, edit.action_frame)
        )
        slots = ";".join(
            ",".join(
                (
                    str(slot.item_action_group),
                    _format_float(slot.ref),
                    _format_float(slot.x),
                    _format_float(slot.y),
                    _format_float(slot.z),
                    _format_float(slot.vx),
                    _format_float(slot.vy),
                    _format_float(slot.vz),
                )
            )
            for slot in enabled_slots
        )
        encoded_edits.append(f"{frame_index}:{slots}")
    return "|".join(encoded_edits)


def _format_float(value: float) -> str:
    return repr(float(value))


def write_binary_symbol_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{BINARY_SYMBOL_PATCH_CLASS_NAME}.java"
    path.write_text(BINARY_SYMBOL_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


BINARY_SYMBOL_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.tags.DefineBinaryDataTag;
import com.jpexs.decompiler.flash.tags.SymbolClassTag;
import com.jpexs.decompiler.flash.tags.Tag;
import com.jpexs.helpers.ByteArrayRange;

import java.io.BufferedInputStream;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Set;
import java.util.zip.DeflaterOutputStream;
import java.util.zip.InflaterInputStream;

public class HfeBinarySymbolClonePatch {
    public static void main(String[] args) throws Exception {
        if (args.length != 6 && args.length != 11 && args.length != 12) {
            throw new IllegalArgumentException(
                    "Usage: HfeBinarySymbolClonePatch <in.swf> <out.swf> "
                            + "<sourceSptClass> <sourceLmiClass> <targetSptClass> "
                            + "<targetLmiClass> [targetCharacterId resourceId defenseBrk0 "
                            + "frameItemSpec textureReplacements sourceCharacterId]"
            );
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        SymbolClassTag symbolTag = symbolClassTagForClass(swf, args[2]);
        if (symbolTag == null) {
            throw new IllegalStateException("SymbolClass tag not found for " + args[2]);
        }
        DefineBinaryDataTag sourceSpt = binaryDataForSymbol(swf, symbolTag, args[2]);
        DefineBinaryDataTag sourceLmi = binaryDataForSymbol(swf, symbolTag, args[3]);
        String targetCharacterId = args.length >= 11 ? args[6] : null;
        String resourceId = args.length >= 11 ? args[7] : targetCharacterId;
        int defenseBrk0 = args.length >= 11 ? Integer.parseInt(args[8]) : 0;
        String frameItemSpec = args.length >= 11 ? args[9] : "";
        String textureSpec = args.length >= 11 ? args[10] : "";
        String sourceCharacterId = args.length == 12 ? args[11] : "lucas";
        byte[] sourceLmiPayload = inflateCharacterPayload(sourceLmi.getDataBytes().getRangeData());
        String[] sourceLmiLimbNames = detectLimbNames(sourceLmiPayload);
        String[] replacementLimbNames = selectLimbNamesForReplacement(
                sourceLmiLimbNames, sourceCharacterId
        );
        String[] externalLmiLimbNames = subtractLimbNames(sourceLmiLimbNames, replacementLimbNames);
        byte[] fallbackPtTemplate = frameItemSpec == null || frameItemSpec.isEmpty()
                ? null
                : findFallbackPtObjectTemplate(swf, symbolTag, args[2]);
        String targetLimbPrefix = targetCharacterId == null
                ? null
                : limbPrefixFor(resourceId, replacementLimbNames);
        int symbolIndex = swf.indexOfTag(symbolTag);
        PatchStats sptPatchStats = new PatchStats();
        PatchStats lmiPatchStats = new PatchStats();
        DefineBinaryDataTag clonedSpt = cloneBinaryData(
                swf, sourceSpt, args[4], targetCharacterId, resourceId, targetLimbPrefix,
                sourceCharacterId, true, replacementLimbNames, defenseBrk0, frameItemSpec,
                textureSpec, fallbackPtTemplate, externalLmiLimbNames, sptPatchStats
        );
        swf.addTag(symbolIndex, clonedSpt);
        DefineBinaryDataTag clonedLmi = cloneBinaryData(
                swf, sourceLmi, args[5], targetCharacterId, resourceId, targetLimbPrefix,
                sourceCharacterId, false, replacementLimbNames, defenseBrk0, frameItemSpec,
                textureSpec, fallbackPtTemplate, externalLmiLimbNames, lmiPatchStats
        );
        swf.addTag(symbolIndex + 1, clonedLmi);
        symbolTag.tags.add(clonedSpt.getCharacterId());
        symbolTag.names.add(args[4]);
        symbolTag.tags.add(clonedLmi.getCharacterId());
        symbolTag.names.add(args[5]);
        symbolTag.setModified(true);

        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"binary_symbol_clone\","
                        + "\"spt_id\":" + clonedSpt.getCharacterId() + ","
                        + "\"lmi_id\":" + clonedLmi.getCharacterId() + ","
                        + "\"spt_bytes\":" + clonedSpt.getDataBytes().getLength() + ","
                        + "\"lmi_bytes\":" + clonedLmi.getDataBytes().getLength() + ","
                        + "\"spt_class\":\"" + jsonEscape(args[4]) + "\","
                        + "\"lmi_class\":\"" + jsonEscape(args[5]) + "\","
                        + "\"target_character_id\":"
                        + (targetCharacterId == null
                                ? "null"
                                : "\"" + jsonEscape(targetCharacterId) + "\"")
                        + ",\"source_character_id\":\"" + jsonEscape(sourceCharacterId) + "\""
                        + ",\"spt_id_replacements\":"
                        + sptPatchStats.sptIdReplacements
                        + ",\"spt_limb_name_replacements\":"
                        + sptPatchStats.limbNameReplacements
                        + ",\"spt_lmi_path_replacements\":"
                        + sptPatchStats.lmiPathReplacements
                        + ",\"lmi_limb_name_replacements\":"
                        + lmiPatchStats.limbNameReplacements
                        + ",\"target_limb_prefix\":"
                        + (targetCharacterId == null
                                ? "null"
                                : "\"" + jsonEscape(targetLimbPrefix) + "\"")
                        + ",\"brk0_patches\":"
                        + sptPatchStats.brk0Patches
                        + ",\"frame_item_edits\":"
                        + sptPatchStats.frameItemEdits
                        + ",\"generated_cre_slots\":"
                        + sptPatchStats.generatedCreSlots
                        + ",\"fallback_pt_template_uses\":"
                        + sptPatchStats.fallbackPtTemplateUses
                        + ",\"spt_donor_limb_name_replacements\":"
                        + sptPatchStats.donorLimbNameReplacements
                        + ",\"texture_replacements\":"
                        + lmiPatchStats.textureReplacements
                        + ",\"source_lmi_limb_names\":"
                        + sourceLmiLimbNames.length
                        + ",\"renamed_limb_names\":"
                        + replacementLimbNames.length
                        + ",\"external_lmi_limb_names\":"
                        + externalLmiLimbNames.length
                        + ",\"external_lmi_limb_aliases\":"
                        + lmiPatchStats.externalLmiLimbAliases
                        + "}"
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

    private static DefineBinaryDataTag cloneBinaryData(
            SWF swf,
            DefineBinaryDataTag source,
            String targetClass,
            String targetCharacterId,
            String resourceId,
            String targetLimbPrefix,
            String sourceCharacterId,
            boolean patchSptId,
            String[] replacementLimbNames,
            int defenseBrk0,
            String frameItemSpec,
            String textureSpec,
            byte[] fallbackPtTemplate,
            String[] externalLmiLimbNames,
            PatchStats stats
    ) {
        DefineBinaryDataTag cloned = new DefineBinaryDataTag(swf);
        cloned.setCharacterId(swf.getNextCharacterId());
        cloned.reserved = source.reserved;
        byte[] data = source.getDataBytes().getRangeData();
        if (targetCharacterId != null) {
            data = patchCharacterPayload(
                    data, targetCharacterId, resourceId, targetLimbPrefix, sourceCharacterId,
                    patchSptId, replacementLimbNames, defenseBrk0, frameItemSpec, textureSpec,
                    fallbackPtTemplate, externalLmiLimbNames, stats
            );
        }
        cloned.setDataBytes(new ByteArrayRange(data));
        cloned.addClassName(targetClass);
        cloned.setModified(true);
        return cloned;
    }

    private static byte[] patchCharacterPayload(
            byte[] source,
            String targetCharacterId,
            String resourceId,
            String targetLimbPrefix,
            String sourceCharacterId,
            boolean patchSptId,
            String[] replacementLimbNames,
            int defenseBrk0,
            String frameItemSpec,
            String textureSpec,
            byte[] fallbackPtTemplate,
            String[] externalLmiLimbNames,
            PatchStats stats
    ) {
        validateResourceId(resourceId, sourceCharacterId);
        byte[] inflated = inflateCharacterPayload(source);
        byte[] patched = inflated;
        if (patchSptId) {
            SptContainer sptContainer = readSptContainer(inflated);
            byte[] spt = sptContainer.bytes;
            String[] sourceSptLimbNames = replacementLimbNames;
            spt = replaceFirstSptId(spt, targetCharacterId, stats);
            spt = replaceExactUtfStrings(
                    spt,
                    "lmi_data/" + sourceCharacterId + ".lmi",
                    "lmi_data/" + resourceId + ".lmi",
                    stats,
                    ReplacementKind.LMI_PATH,
                    true
            );
            if (defenseBrk0 != 0) {
                spt = patchTopLevelDoubleField(spt, "brk0", (double) defenseBrk0, stats);
            }
            if (frameItemSpec != null && !frameItemSpec.isEmpty()) {
                spt = applyFrameItemEdits(spt, frameItemSpec, fallbackPtTemplate, stats);
            }
            spt = applyLimbNameReplacements(spt, textureSpec, stats);
            spt = aliasExternalLmiLimbNames(
                    spt, externalLmiLimbNames, resourceId, true, stats
            );
            spt = replaceLimbUtfStrings(
                    spt,
                    sourceSptLimbNames,
                    targetLimbPrefix,
                    stats,
                    ReplacementKind.LIMB_NAME,
                    true
            );
            patched = writeSptContainer(sptContainer.fileType, spt);
        } else {
            patched = replaceLimbUtfStrings(
                    patched,
                    replacementLimbNames,
                    targetLimbPrefix,
                    stats,
                    ReplacementKind.LIMB_NAME,
                    true
            );
            patched = aliasExternalLmiLimbNames(
                    patched, externalLmiLimbNames, resourceId, false, stats
            );
        }
        byte[] deflated = deflate(patched);
        return writeAmf3ByteArray(deflated);
    }

    private static byte[] inflateCharacterPayload(byte[] source) {
        Amf3ByteArray payload = readAmf3ByteArray(source);
        return inflate(payload.bytes);
    }

    private static SptContainer readSptContainer(byte[] inflated) {
        RawString fileType = readRawAmf0String(inflated, 0);
        if (!"Spt".equals(fileType.value)) {
            throw new IllegalStateException("Expected Spt container, got " + fileType.value);
        }
        Amf3ByteArray inner = readAmf3ByteArray(inflated, fileType.nextOffset);
        if (inner.nextOffset != inflated.length) {
            throw new IllegalStateException("Unexpected trailing bytes in Spt container");
        }
        return new SptContainer(fileType.value, inner.bytes);
    }

    private static byte[] writeSptContainer(String fileType, byte[] sptBytes) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(sptBytes.length + 16);
        writeRawAmf0String(out, fileType);
        byte[] inner = writeAmf3ByteArray(sptBytes);
        out.write(inner, 0, inner.length);
        return out.toByteArray();
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

    private static void writeRawAmf0String(ByteArrayOutputStream out, String value) {
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        if (bytes.length > 65535) {
            throw new IllegalArgumentException("raw AMF0 string is too long");
        }
        out.write((bytes.length >> 8) & 0xff);
        out.write(bytes.length & 0xff);
        out.write(bytes, 0, bytes.length);
    }

    private static void validateResourceId(String resourceId, String sourceCharacterId) {
        int targetLength = resourceId.getBytes(StandardCharsets.UTF_8).length;
        int templateLength = sourceCharacterId.getBytes(StandardCharsets.UTF_8).length;
        if (targetLength != templateLength) {
            throw new IllegalArgumentException(
                    "Binary SPT/LMI resource namespace currently requires an id with the "
                            + "same byte length as the selected source role."
            );
        }
    }

    private static byte[] replaceFirstSptId(
            byte[] source,
            String targetCharacterId,
            PatchStats stats
    ) {
        byte[] key = new byte[]{0, 2, 'i', 'd', 2};
        byte[] replacement = targetCharacterId.getBytes(StandardCharsets.UTF_8);
        if (replacement.length > 65535) {
            throw new IllegalArgumentException("target character id is too long");
        }
        for (int i = 0; i <= source.length - key.length - 2; i++) {
            boolean matched = true;
            for (int j = 0; j < key.length; j++) {
                if (source[i + j] != key[j]) {
                    matched = false;
                    break;
                }
            }
            if (!matched) {
                continue;
            }
            int oldLenOffset = i + key.length;
            int oldLen = ((source[oldLenOffset] & 0xff) << 8)
                    | (source[oldLenOffset + 1] & 0xff);
            int oldValueOffset = oldLenOffset + 2;
            if (oldValueOffset + oldLen > source.length) {
                continue;
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            out.write(source, 0, oldLenOffset);
            out.write((replacement.length >> 8) & 0xff);
            out.write(replacement.length & 0xff);
            out.write(replacement, 0, replacement.length);
            out.write(
                    source,
                    oldValueOffset + oldLen,
                    source.length - oldValueOffset - oldLen
            );
            stats.sptIdReplacements++;
            return out.toByteArray();
        }
        throw new IllegalStateException("SPT id field not found in compressed binary data");
    }

    private static byte[] replaceExactUtfStrings(
            byte[] source,
            String sourceValue,
            String targetValue,
            PatchStats stats,
            ReplacementKind kind
    ) {
        return replaceExactUtfStrings(source, sourceValue, targetValue, stats, kind, false);
    }

    private static byte[] replaceExactUtfStrings(
            byte[] source,
            String sourceValue,
            String targetValue,
            PatchStats stats,
            ReplacementKind kind,
            boolean allowLengthChange
    ) {
        byte[] sourceBytes = sourceValue.getBytes(StandardCharsets.UTF_8);
        byte[] targetBytes = targetValue.getBytes(StandardCharsets.UTF_8);
        return replaceUtfStrings(source, stats, kind, new UtfReplacement() {
            public byte[] replacementFor(byte[] value, int length) {
                if (length != sourceBytes.length) {
                    return null;
                }
                for (int i = 0; i < sourceBytes.length; i++) {
                    if (value[i] != sourceBytes[i]) {
                        return null;
                    }
                }
                return targetBytes;
            }
        }, allowLengthChange);
    }

    private static byte[] replaceNamedUtfStringValue(
            byte[] source,
            String fieldName,
            String sourceValue,
            String targetValue,
            PatchStats stats,
            ReplacementKind kind,
            boolean allowLengthChange
    ) {
        byte[] fieldBytes = fieldName.getBytes(StandardCharsets.UTF_8);
        byte[] sourceBytes = sourceValue.getBytes(StandardCharsets.UTF_8);
        byte[] targetBytes = targetValue.getBytes(StandardCharsets.UTF_8);
        ByteArrayOutputStream out = new ByteArrayOutputStream(source.length);
        int offset = 0;
        while (offset < source.length) {
            if (offset + 2 + fieldBytes.length + 3 <= source.length) {
                int fieldLength = ((source[offset] & 0xff) << 8)
                        | (source[offset + 1] & 0xff);
                int fieldOffset = offset + 2;
                int markerOffset = fieldOffset + fieldLength;
                if (fieldLength == fieldBytes.length
                        && markerOffset + 3 <= source.length
                        && startsWith(source, fieldOffset, fieldLength, fieldBytes)
                        && source[markerOffset] == 0x02) {
                    int oldLen = ((source[markerOffset + 1] & 0xff) << 8)
                            | (source[markerOffset + 2] & 0xff);
                    int oldValueOffset = markerOffset + 3;
                    if (oldValueOffset + oldLen <= source.length
                            && oldLen == sourceBytes.length
                            && startsWith(source, oldValueOffset, oldLen, sourceBytes)) {
                        if (targetBytes.length > 65535) {
                            throw new IllegalArgumentException(
                                    "replacement string is too long"
                            );
                        }
                        if (!allowLengthChange && targetBytes.length != oldLen) {
                            throw new IllegalArgumentException(
                                    "Embedded AMF string replacement must keep byte length"
                            );
                        }
                        out.write(source, offset, markerOffset + 1 - offset);
                        out.write((targetBytes.length >> 8) & 0xff);
                        out.write(targetBytes.length & 0xff);
                        out.write(targetBytes, 0, targetBytes.length);
                        offset = oldValueOffset + oldLen;
                        stats.count(kind);
                        continue;
                    }
                }
            }
            out.write(source[offset] & 0xff);
            offset++;
        }
        return out.toByteArray();
    }

    private static byte[] replacePrefixUtfStrings(
            byte[] source,
            String sourcePrefix,
            String targetPrefix,
            PatchStats stats,
            ReplacementKind kind
    ) {
        return replacePrefixUtfStrings(source, sourcePrefix, targetPrefix, stats, kind, false);
    }

    private static byte[] replacePrefixUtfStrings(
            byte[] source,
            String sourcePrefix,
            String targetPrefix,
            PatchStats stats,
            ReplacementKind kind,
            boolean allowLengthChange
    ) {
        byte[] sourcePrefixBytes = sourcePrefix.getBytes(StandardCharsets.UTF_8);
        return replaceUtfStrings(source, stats, kind, new UtfReplacement() {
            public byte[] replacementFor(byte[] value, int length) {
                if (!startsWith(value, 0, length, sourcePrefixBytes)) {
                    return null;
                }
                String oldValue = new String(value, 0, length, StandardCharsets.UTF_8);
                if (!oldValue.startsWith(sourcePrefix)) {
                    return null;
                }
                return (targetPrefix + oldValue.substring(sourcePrefix.length()))
                        .getBytes(StandardCharsets.UTF_8);
            }
        }, allowLengthChange);
    }

    private static byte[] replacePrefixUtfStrings(
            byte[] source,
            String[] sourcePrefixes,
            String targetPrefix,
            PatchStats stats,
            ReplacementKind kind,
            boolean allowLengthChange
    ) {
        byte[] patched = source;
        for (String sourcePrefix : sourcePrefixes) {
            if (sourcePrefix.equals(targetPrefix)) {
                continue;
            }
            patched = replacePrefixUtfStrings(
                    patched,
                    sourcePrefix,
                    targetPrefix,
                    stats,
                    kind,
                    allowLengthChange
            );
        }
        return patched;
    }

    private static byte[] replaceLimbUtfStrings(
            byte[] source,
            String[] sourceLimbNames,
            String targetPrefix,
            PatchStats stats,
            ReplacementKind kind,
            boolean allowLengthChange
    ) {
        byte[] patched = source;
        for (String sourceLimbName : sourceLimbNames) {
            int underscore = limbNameSeparator(sourceLimbName);
            if (underscore <= 0 || underscore + 1 >= sourceLimbName.length()) {
                continue;
            }
            String targetValue = targetPrefix + sourceLimbName.substring(underscore + 1);
            if (sourceLimbName.equals(targetValue)) {
                continue;
            }
            patched = replaceExactUtfStrings(
                    patched,
                    sourceLimbName,
                    targetValue,
                    stats,
                    kind,
                    allowLengthChange
            );
        }
        return patched;
    }

    private static String[] detectLimbNames(byte[] source) {
        Set<String> names = new LinkedHashSet<>();
        for (String value : utfStrings(source)) {
            if (isLimbName(value)) {
                names.add(value);
            }
        }
        for (String value : detectDataLimbNameValues(source)) {
            if (isPotentialLmiLimbName(value)) {
                names.add(value);
            }
        }
        return names.toArray(new String[0]);
    }

    private static String[] detectDataLimbNameValues(byte[] source) {
        Set<String> names = new LinkedHashSet<>();
        byte[] field = new byte[]{0, 4, 'n', 'a', 'm', 'e', 2};
        for (int offset = 0; offset <= source.length - field.length - 2; offset++) {
            boolean matched = true;
            for (int i = 0; i < field.length; i++) {
                if (source[offset + i] != field[i]) {
                    matched = false;
                    break;
                }
            }
            if (!matched) {
                continue;
            }
            int lengthOffset = offset + field.length;
            int length = ((source[lengthOffset] & 0xff) << 8)
                    | (source[lengthOffset + 1] & 0xff);
            int valueOffset = lengthOffset + 2;
            if (valueOffset + length > source.length) {
                continue;
            }
            names.add(new String(source, valueOffset, length, StandardCharsets.UTF_8));
        }
        return names.toArray(new String[0]);
    }

    private static boolean isPotentialLmiLimbName(String value) {
        if (value == null || value.isEmpty()) {
            return false;
        }
        if (value.startsWith("png_") || value.indexOf('/') >= 0) {
            return false;
        }
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if (!(Character.isLetterOrDigit(ch) || ch == '_')) {
                return false;
            }
        }
        return true;
    }

    private static boolean isLimbName(String value) {
        int underscore = limbNameSeparator(value);
        if (underscore <= 0 || underscore + 3 >= value.length()) {
            return false;
        }
        if (value.startsWith("png_") || value.indexOf('/') >= 0) {
            return false;
        }
        String suffix = value.substring(underscore + 1);
        return Character.isDigit(suffix.charAt(0)) && Character.isDigit(suffix.charAt(1));
    }

    private static int limbNameSeparator(String value) {
        for (int i = value.lastIndexOf('_'); i > 0; i = value.lastIndexOf('_', i - 1)) {
            if (i + 2 < value.length()
                    && Character.isDigit(value.charAt(i + 1))
                    && Character.isDigit(value.charAt(i + 2))) {
                return i;
            }
        }
        return -1;
    }

    private static String[] selectLimbNamesForReplacement(
            String[] sourceLmiLimbNames,
            String sourceCharacterId
    ) {
        if (sourceLmiLimbNames.length == 0) {
            return sourceLmiLimbNames;
        }
        if (sourceCharacterId != null && !sourceCharacterId.isEmpty()) {
            String[] owned = limbNamesWithPrefix(sourceLmiLimbNames, sourceCharacterId + "_");
            if (owned.length > 0) {
                return owned;
            }
        }
        String solePrefix = soleLimbPrefix(sourceLmiLimbNames);
        if (solePrefix != null) {
            return sourceLmiLimbNames;
        }
        String dominantPrefix = dominantLimbPrefix(sourceLmiLimbNames);
        if (dominantPrefix == null) {
            return sourceLmiLimbNames;
        }
        return limbNamesWithPrefix(sourceLmiLimbNames, dominantPrefix);
    }

    private static String[] subtractLimbNames(String[] values, String[] excluded) {
        String[] selected = new String[values.length];
        int count = 0;
        for (String value : values) {
            boolean isExcluded = false;
            for (String excludedValue : excluded) {
                if (value.equals(excludedValue)) {
                    isExcluded = true;
                    break;
                }
            }
            if (!isExcluded) {
                selected[count++] = value;
            }
        }
        return Arrays.copyOf(selected, count);
    }

    private static String[] limbNamesWithPrefix(String[] sourceLmiLimbNames, String prefix) {
        String[] selected = new String[sourceLmiLimbNames.length];
        int count = 0;
        for (String name : sourceLmiLimbNames) {
            if (name.startsWith(prefix)) {
                selected[count++] = name;
            }
        }
        return Arrays.copyOf(selected, count);
    }

    private static String soleLimbPrefix(String[] sourceLmiLimbNames) {
        String prefix = null;
        for (String name : sourceLmiLimbNames) {
            String current = limbPrefix(name);
            if (current == null) {
                continue;
            }
            if (prefix == null) {
                prefix = current;
            } else if (!prefix.equals(current)) {
                return null;
            }
        }
        return prefix;
    }

    private static String dominantLimbPrefix(String[] sourceLmiLimbNames) {
        String[] prefixes = new String[sourceLmiLimbNames.length];
        int[] counts = new int[sourceLmiLimbNames.length];
        int prefixCount = 0;
        for (String name : sourceLmiLimbNames) {
            String prefix = limbPrefix(name);
            if (prefix == null) {
                continue;
            }
            int index = -1;
            for (int i = 0; i < prefixCount; i++) {
                if (prefixes[i].equals(prefix)) {
                    index = i;
                    break;
                }
            }
            if (index < 0) {
                index = prefixCount;
                prefixes[prefixCount++] = prefix;
            }
            counts[index]++;
        }
        String bestPrefix = null;
        int bestCount = 0;
        for (int i = 0; i < prefixCount; i++) {
            if (counts[i] > bestCount) {
                bestPrefix = prefixes[i];
                bestCount = counts[i];
            }
        }
        return bestPrefix;
    }

    private static String limbPrefix(String limbName) {
        int underscore = limbNameSeparator(limbName);
        if (underscore <= 0) {
            return null;
        }
        return limbName.substring(0, underscore + 1);
    }

    private static byte[] aliasExternalLmiLimbNames(
            byte[] source,
            String[] externalLmiLimbNames,
            String resourceId,
            boolean sptPayload,
            PatchStats stats
    ) {
        byte[] patched = source;
        for (int i = 0; i < externalLmiLimbNames.length; i++) {
            String sourceLimbName = externalLmiLimbNames[i];
            String alias = externalLmiLimbAlias(resourceId, sourceLimbName, i);
            if (limbNameSeparator(sourceLimbName) <= 0) {
                patched = replaceNamedUtfStringValue(
                        patched,
                        sptPayload ? "limbName" : "name",
                        sourceLimbName,
                        alias,
                        stats,
                        ReplacementKind.EXTERNAL_LMI_ALIAS,
                        false
                );
            } else {
                patched = replaceExactUtfStrings(
                        patched,
                        sourceLimbName,
                        alias,
                        stats,
                        ReplacementKind.EXTERNAL_LMI_ALIAS,
                        false
                );
            }
        }
        return patched;
    }

    private static String externalLmiLimbAlias(
            String resourceId,
            String sourceLimbName,
            int index
    ) {
        int underscore = limbNameSeparator(sourceLimbName);
        String sourcePrefix = underscore > 0
                ? sourceLimbName.substring(0, underscore)
                : sourceLimbName;
        String suffix = underscore > 0
                ? sourceLimbName.substring(underscore + 1)
                : String.format("%02dExternal", index);
        int sourceByteLength = sourceLimbName.getBytes(StandardCharsets.UTF_8).length;
        if (underscore <= 0) {
            return compactBareLimbAlias(resourceId, sourceLimbName, index, sourceByteLength);
        }
        int suffixByteLength = suffix.getBytes(StandardCharsets.UTF_8).length;
        int prefixByteLength = sourceByteLength - suffixByteLength - 1;
        if (prefixByteLength <= 0) {
            return sourceLimbName;
        }
        String alias = compactAliasPrefix(resourceId, sourcePrefix, index, prefixByteLength)
                + "_"
                + suffix;
        if (alias.getBytes(StandardCharsets.UTF_8).length != sourceByteLength) {
            throw new IllegalStateException("External LMI alias length mismatch");
        }
        return alias;
    }

    private static String compactBareLimbAlias(
            String resourceId,
            String sourceLimbName,
            int index,
            int byteLength
    ) {
        if (byteLength <= 0) {
            return sourceLimbName;
        }
        String seed = sanitizeLimbAliasToken(resourceId) + Integer.toString(index, 36);
        if (seed.length() == 0) {
            seed = "x";
        }
        String alias = fitLimbPrefixStem(seed, byteLength);
        if (alias.equals(sourceLimbName)) {
            String alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
            for (int i = 0; i < alphabet.length(); i++) {
                String candidate = alias.substring(0, Math.max(0, alias.length() - 1))
                        + alphabet.charAt(i);
                if (!candidate.equals(sourceLimbName)) {
                    alias = candidate;
                    break;
                }
            }
        }
        if (alias.getBytes(StandardCharsets.UTF_8).length != byteLength) {
            throw new IllegalStateException("Bare LMI alias length mismatch");
        }
        return alias;
    }

    private static String compactAliasPrefix(
            String resourceId,
            String sourcePrefix,
            int index,
            int prefixLength
    ) {
        String seed = sanitizeLimbAliasToken(resourceId);
        if (seed.length() == 0) {
            seed = sanitizeLimbAliasToken(sourcePrefix);
        }
        if (seed.length() == 0) {
            seed = "x";
        }
        String indexToken = Integer.toString(index, 36);
        if (prefixLength <= indexToken.length()) {
            return indexToken.substring(indexToken.length() - prefixLength);
        }
        int stemLength = prefixLength - indexToken.length();
        StringBuilder stem = new StringBuilder();
        while (stem.length() < stemLength) {
            stem.append(seed);
        }
        return stem.substring(0, stemLength) + indexToken;
    }

    private static String sanitizeLimbAliasToken(String value) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            if (Character.isLetterOrDigit(ch)) {
                out.append(ch);
            }
        }
        if (out.length() == 0) {
            return "external";
        }
        return out.toString();
    }

    private static String[] utfStrings(byte[] source) {
        String[] values = new String[64];
        int count = 0;
        for (int offset = 0; offset < source.length; offset++) {
            if (source[offset] != 0x02 || offset + 3 > source.length) {
                continue;
            }
            int length = ((source[offset + 1] & 0xff) << 8)
                    | (source[offset + 2] & 0xff);
            int valueOffset = offset + 3;
            if (valueOffset + length > source.length) {
                offset++;
                continue;
            }
            if (count == values.length) {
                values = Arrays.copyOf(values, values.length * 2);
            }
            values[count++] = new String(
                    source,
                    valueOffset,
                    length,
                    StandardCharsets.UTF_8
            );
        }
        return Arrays.copyOf(values, count);
    }

    private static byte[] replaceUtfStrings(
            byte[] source,
            PatchStats stats,
            ReplacementKind kind,
            UtfReplacement replacement,
            boolean allowLengthChange
    ) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(source.length);
        int offset = 0;
        while (offset < source.length) {
            if (source[offset] == 0x02 && offset + 3 <= source.length) {
                int oldLen = ((source[offset + 1] & 0xff) << 8)
                        | (source[offset + 2] & 0xff);
                int oldValueOffset = offset + 3;
                if (oldValueOffset + oldLen <= source.length) {
                    byte[] oldValue = Arrays.copyOfRange(
                            source,
                            oldValueOffset,
                            oldValueOffset + oldLen
                    );
                    byte[] newValue = replacement.replacementFor(oldValue, oldLen);
                    if (newValue != null) {
                        if (newValue.length > 65535) {
                            throw new IllegalArgumentException("replacement string is too long");
                        }
                        if (!allowLengthChange && newValue.length != oldLen) {
                            throw new IllegalArgumentException(
                                    "Embedded AMF string replacement must keep byte length"
                            );
                        }
                        out.write(source[offset] & 0xff);
                        out.write((newValue.length >> 8) & 0xff);
                        out.write(newValue.length & 0xff);
                        out.write(newValue, 0, newValue.length);
                        offset = oldValueOffset + oldLen;
                        stats.count(kind);
                        continue;
                    }
                }
            }
            out.write(source[offset] & 0xff);
            offset++;
        }
        return out.toByteArray();
    }

    private static boolean startsWith(
            byte[] source,
            int offset,
            int length,
            byte[] prefix
    ) {
        if (length < prefix.length) {
            return false;
        }
        for (int i = 0; i < prefix.length; i++) {
            if (source[offset + i] != prefix[i]) {
                return false;
            }
        }
        return true;
    }

    private static String limbPrefixFor(String resourceId, String[] sourceLimbNames) {
        String sourcePrefix = firstSourceLimbPrefix(sourceLimbNames);
        String direct = resourceId + "_";
        if (sourcePrefix == null) {
            return direct;
        }
        if (direct.getBytes(StandardCharsets.UTF_8).length
                == sourcePrefix.getBytes(StandardCharsets.UTF_8).length
                && !sourcePrefix.equals(direct)) {
            return direct;
        }
        return sameLengthLimbPrefixCandidate(resourceId, sourcePrefix);
    }

    private static String firstSourceLimbPrefix(String[] sourceLimbNames) {
        for (String sourceLimbName : sourceLimbNames) {
            String prefix = limbPrefix(sourceLimbName);
            if (prefix != null) {
                return prefix;
            }
        }
        return null;
    }

    private static String sameLengthLimbPrefixCandidate(String resourceId, String sourcePrefix) {
        int stemLength = sourcePrefix.endsWith("_")
                ? sourcePrefix.length() - 1
                : sourcePrefix.lastIndexOf('_');
        if (stemLength <= 0) {
            return resourceId + "_";
        }
        String seed = resourceId.replaceAll("[^A-Za-z0-9]", "");
        if (seed.isEmpty()) {
            seed = "x";
        }
        String[] candidates = new String[]{
                fitLimbPrefixStem(seed, stemLength),
                edgeLimbPrefixStem(seed, stemLength),
                fitLimbPrefixStem(reverse(seed), stemLength)
        };
        for (String candidateStem : candidates) {
            String candidate = candidateStem + "_";
            if (isUsableTargetLimbPrefix(candidate, sourcePrefix)) {
                return candidate;
            }
        }
        String base = fitLimbPrefixStem(seed, stemLength);
        for (char suffix = '0'; suffix <= '9'; suffix++) {
            String candidateStem = base.substring(0, stemLength - 1) + suffix;
            String candidate = candidateStem + "_";
            if (isUsableTargetLimbPrefix(candidate, sourcePrefix)) {
                return candidate;
            }
        }
        for (char suffix = 'a'; suffix <= 'z'; suffix++) {
            String candidateStem = base.substring(0, stemLength - 1) + suffix;
            String candidate = candidateStem + "_";
            if (isUsableTargetLimbPrefix(candidate, sourcePrefix)) {
                return candidate;
            }
        }
        throw new IllegalArgumentException(
                "Could not derive a unique limb prefix for " + resourceId
        );
    }

    private static boolean isUsableTargetLimbPrefix(String candidate, String sourcePrefix) {
        return !sourcePrefix.equals(candidate)
                && candidate.getBytes(StandardCharsets.UTF_8).length
                == sourcePrefix.getBytes(StandardCharsets.UTF_8).length;
    }

    private static String fitLimbPrefixStem(String seed, int stemLength) {
        StringBuilder out = new StringBuilder(stemLength);
        while (out.length() < stemLength) {
            out.append(seed);
        }
        return out.substring(0, stemLength);
    }

    private static String edgeLimbPrefixStem(String seed, int stemLength) {
        if (stemLength <= 1 || seed.length() <= stemLength) {
            return fitLimbPrefixStem(seed, stemLength);
        }
        return seed.substring(0, 1) + seed.substring(seed.length() - (stemLength - 1));
    }

    private static String reverse(String value) {
        return new StringBuilder(value).reverse().toString();
    }

    private static byte[] patchTopLevelDoubleField(
            byte[] source,
            String fieldName,
            double value,
            PatchStats stats
    ) {
        byte[] patched = Arrays.copyOf(source, source.length);
        byte[] pattern = doubleFieldPattern(fieldName);
        int offset = indexOf(patched, pattern, 0);
        if (offset < 0) {
            throw new IllegalStateException("Could not find SPT numeric field: " + fieldName);
        }
        writeDouble(patched, offset + pattern.length, value);
        if ("brk0".equals(fieldName)) {
            stats.brk0Patches++;
        }
        return patched;
    }

    private static byte[] applyFrameItemEdits(
            byte[] source,
            String frameItemSpec,
            byte[] fallbackPtTemplate,
            PatchStats stats
    ) {
        byte[] patched = source;
        byte[] ptTemplate = extractPtObjectTemplateOrNull(source);
        if (ptTemplate == null && fallbackPtTemplate != null) {
            ptTemplate = fallbackPtTemplate;
            stats.fallbackPtTemplateUses++;
        }
        if (ptTemplate == null) {
            throw new IllegalStateException("Could not find editable Data.Pt template");
        }
        String[] editSpecs = frameItemSpec.split("\\|");
        for (String editSpec : editSpecs) {
            if (editSpec.isEmpty()) {
                continue;
            }
            FrameItemEdit edit = parseFrameItemEdit(editSpec);
            patched = replaceCreInFrame(patched, edit, ptTemplate, stats);
        }
        return patched;
    }

    private static FrameItemEdit parseFrameItemEdit(String editSpec) {
        String[] parts = editSpec.split(":", 2);
        if (parts.length != 2) {
            throw new IllegalArgumentException("Invalid frame item edit: " + editSpec);
        }
        int frameIndex = Integer.parseInt(parts[0]);
        String[] slotSpecs = parts[1].split(";");
        PtEdit[] slots = new PtEdit[slotSpecs.length];
        for (int i = 0; i < slotSpecs.length; i++) {
            slots[i] = parsePtEdit(slotSpecs[i]);
        }
        return new FrameItemEdit(frameIndex, slots);
    }

    private static PtEdit parsePtEdit(String slotSpec) {
        String[] values = slotSpec.split(",", -1);
        if (values.length != 8) {
            throw new IllegalArgumentException("Invalid item slot: " + slotSpec);
        }
        return new PtEdit(
                Integer.parseInt(values[0]),
                Double.parseDouble(values[1]),
                Double.parseDouble(values[2]),
                Double.parseDouble(values[3]),
                Double.parseDouble(values[4]),
                Double.parseDouble(values[5]),
                Double.parseDouble(values[6]),
                Double.parseDouble(values[7])
        );
    }

    private static byte[] replaceCreInFrame(
            byte[] source,
            FrameItemEdit edit,
            byte[] ptTemplate,
            PatchStats stats
    ) {
        if (edit.slots.length < 1 || edit.slots.length > 9) {
            throw new IllegalArgumentException("Frame item slot count must be between 1 and 9");
        }
        int[] frameStarts = findFrameStarts(source);
        if (edit.frameIndex < 0 || edit.frameIndex >= frameStarts.length) {
            throw new IllegalArgumentException("Frame index out of range: " + edit.frameIndex);
        }
        int frameStart = frameStarts[edit.frameIndex];
        int frameEnd = edit.frameIndex + 1 < frameStarts.length
                ? frameStarts[edit.frameIndex + 1]
                : source.length;
        byte[] creKey = new byte[]{0, 3, 'c', 'r', 'e'};
        int creOffset = indexOf(source, creKey, frameStart);
        if (creOffset < 0 || creOffset >= frameEnd) {
            throw new IllegalStateException("Could not find cre field in frame " + edit.frameIndex);
        }
        int creEnd;
        int valueType = source[creOffset + creKey.length] & 0xff;
        if (valueType == 0x05) {
            creEnd = creOffset + creKey.length + 1;
        } else if (valueType == 0x08) {
            byte[] objectAndArrayEnd = new byte[]{0, 0, 9, 0, 0, 9};
            int endOffset = indexOf(source, objectAndArrayEnd, creOffset + creKey.length + 5);
            if (endOffset < 0 || endOffset >= frameEnd) {
                throw new IllegalStateException(
                        "Could not find end of cre array in frame " + edit.frameIndex
                );
            }
            creEnd = endOffset + objectAndArrayEnd.length;
        } else {
            throw new IllegalStateException(
                    "Unsupported cre value type " + valueType + " in frame " + edit.frameIndex
            );
        }
        byte[] generatedCre = generateCreArray(ptTemplate, edit.slots, stats);
        ByteArrayOutputStream out = new ByteArrayOutputStream(
                source.length - (creEnd - creOffset) + generatedCre.length
        );
        out.write(source, 0, creOffset);
        out.write(generatedCre, 0, generatedCre.length);
        out.write(source, creEnd, source.length - creEnd);
        stats.frameItemEdits++;
        return out.toByteArray();
    }

    private static byte[] patchExistingCreSlots(
            byte[] source,
            int creOffset,
            int creEnd,
            FrameItemEdit edit,
            PatchStats stats
    ) {
        int[] ptStarts = findPtObjectStarts(source, creOffset, creEnd);
        if (ptStarts.length != edit.slots.length) {
            throw new IllegalArgumentException(
                    "Binary-safe item editing must keep the existing cre slot count: existing="
                            + ptStarts.length + ", edited=" + edit.slots.length
            );
        }
        byte[] out = Arrays.copyOf(source, source.length);
        byte[] objectEnd = new byte[]{0, 0, 9};
        byte[] marker = new byte[]{16, 0, 7, 'D', 'a', 't', 'a', '.', 'P', 't'};
        for (int i = 0; i < ptStarts.length; i++) {
            int start = ptStarts[i];
            int end = indexOf(source, objectEnd, start + marker.length);
            if (end < 0 || end + objectEnd.length > creEnd) {
                throw new IllegalStateException("Could not find Data.Pt object end");
            }
            applyPtEditInRange(out, start, end + objectEnd.length, edit.slots[i]);
            stats.generatedCreSlots++;
        }
        return out;
    }

    private static int[] findPtObjectStarts(byte[] source, int startOffset, int endOffset) {
        byte[] marker = new byte[]{16, 0, 7, 'D', 'a', 't', 'a', '.', 'P', 't'};
        int[] starts = new int[16];
        int count = 0;
        int offset = startOffset;
        while (offset <= endOffset - marker.length) {
            int found = indexOf(source, marker, offset);
            if (found < 0 || found >= endOffset) {
                break;
            }
            if (count == starts.length) {
                starts = Arrays.copyOf(starts, starts.length * 2);
            }
            starts[count++] = found;
            offset = found + marker.length;
        }
        return Arrays.copyOf(starts, count);
    }

    private static int[] findFrameStarts(byte[] source) {
        byte[] marker = new byte[]{
                16, 0, 10, 'D', 'a', 't', 'a', '.', 'F', 'r', 'a', 'm', 'e'
        };
        int[] starts = new int[512];
        int count = 0;
        int offset = 0;
        while (offset <= source.length - marker.length) {
            int found = indexOf(source, marker, offset);
            if (found < 0) {
                break;
            }
            if (count == starts.length) {
                starts = Arrays.copyOf(starts, starts.length * 2);
            }
            starts[count++] = found;
            offset = found + marker.length;
        }
        if (count == 0) {
            throw new IllegalStateException("No Data.Frame entries found in SPT payload");
        }
        return Arrays.copyOf(starts, count);
    }

    private static byte[] generateCreArray(byte[] template, PtEdit[] slots, PatchStats stats) {
        ByteArrayOutputStream out = new ByteArrayOutputStream(template.length * slots.length + 16);
        out.write(0);
        out.write(3);
        out.write('c');
        out.write('r');
        out.write('e');
        out.write(8);
        out.write(0);
        out.write(0);
        out.write(0);
        out.write(slots.length);
        for (int i = 0; i < slots.length; i++) {
            out.write(0);
            out.write(1);
            out.write((byte) ('0' + i));
            byte[] object = applyPtEdit(template, slots[i]);
            out.write(object, 0, object.length);
            stats.generatedCreSlots++;
        }
        out.write(0);
        out.write(0);
        out.write(9);
        return out.toByteArray();
    }

    private static byte[] extractPtObjectTemplate(byte[] source) {
        byte[] template = extractPtObjectTemplateOrNull(source);
        if (template != null) {
            return template;
        }
        throw new IllegalStateException("Could not find editable Data.Pt template");
    }

    private static byte[] extractPtObjectTemplateOrNull(byte[] source) {
        byte[] marker = new byte[]{16, 0, 7, 'D', 'a', 't', 'a', '.', 'P', 't'};
        int offset = 0;
        byte[] fallback = null;
        while (offset <= source.length - marker.length) {
            int start = indexOf(source, marker, offset);
            if (start < 0) {
                break;
            }
            byte[] objectEnd = new byte[]{0, 0, 9};
            int end = indexOf(source, objectEnd, start + marker.length);
            if (end < 0) {
                throw new IllegalStateException("Could not find Data.Pt object end");
            }
            byte[] candidate = Arrays.copyOfRange(source, start, end + objectEnd.length);
            if (isProjectilePtTemplate(candidate)) {
                return candidate;
            }
            if (fallback == null && isEditablePtTemplate(candidate)) {
                fallback = candidate;
            }
            offset = start + marker.length;
        }
        if (fallback != null) {
            return fallback;
        }
        return null;
    }

    private static byte[] findFallbackPtObjectTemplate(
            SWF swf,
            SymbolClassTag symbolTag,
            String sourceSptClass
    ) {
        String[] donors = new String[]{
                sourceSptClass,
                "Data.Global_lucasSpt",
                "Data.Global_rayeSpt",
                "Data.Global_drewSpt"
        };
        for (String donor : donors) {
            DefineBinaryDataTag tag = binaryDataForSymbolOrNull(swf, symbolTag, donor);
            if (tag == null) {
                continue;
            }
            byte[] template = extractPtTemplateFromSptBinary(tag);
            if (template != null) {
                return template;
            }
        }
        return null;
    }

    private static byte[] extractPtTemplateFromSptBinary(DefineBinaryDataTag tag) {
        try {
            byte[] inflated = inflateCharacterPayload(tag.getDataBytes().getRangeData());
            SptContainer container = readSptContainer(inflated);
            return extractPtObjectTemplateOrNull(container.bytes);
        } catch (RuntimeException ex) {
            return null;
        }
    }

    private static DefineBinaryDataTag binaryDataForSymbolOrNull(
            SWF swf,
            SymbolClassTag symbolTag,
            String className
    ) {
        try {
            return binaryDataForSymbol(swf, symbolTag, className);
        } catch (RuntimeException ex) {
            return null;
        }
    }

    private static boolean isProjectilePtTemplate(byte[] candidate) {
        return hasNamedDouble(candidate, "sx", 160.0)
                && hasNamedDouble(candidate, "sy", -10.0)
                && hasNamedDouble(candidate, "ref", 33.0)
                && hasNamedDouble(candidate, "int1", 5.0)
                && hasNamedDouble(candidate, "ai", 2.0);
    }

    private static boolean isEditablePtTemplate(byte[] candidate) {
        return hasNamedDoubleField(candidate, "int1")
                && hasNamedDoubleField(candidate, "ref")
                && hasNamedDoubleField(candidate, "x")
                && hasNamedDoubleField(candidate, "y")
                && hasNamedDoubleField(candidate, "z")
                && hasNamedDoubleField(candidate, "vx")
                && hasNamedDoubleField(candidate, "vy")
                && hasNamedDoubleField(candidate, "vz");
    }

    private static byte[] applyPtEdit(byte[] template, PtEdit edit) {
        byte[] out = Arrays.copyOf(template, template.length);
        writeNamedDoubleIfPresent(out, "sx", 160.0);
        writeNamedDoubleIfPresent(out, "sy", -10.0);
        writeNamedDoubleIfPresent(out, "ai", 2.0);
        writeNamedDouble(out, "int1", (double) edit.itemActionGroup);
        writeNamedDouble(out, "ref", edit.ref);
        writeNamedDouble(out, "x", edit.x);
        writeNamedDouble(out, "y", edit.y);
        writeNamedDouble(out, "z", edit.z);
        writeNamedDouble(out, "vx", edit.vx);
        writeNamedDouble(out, "vy", edit.vy);
        writeNamedDouble(out, "vz", edit.vz);
        return out;
    }

    private static void applyPtEditInRange(
            byte[] source,
            int start,
            int end,
            PtEdit edit
    ) {
        writeNamedDoubleIfPresent(source, start, end, "sx", 160.0);
        writeNamedDoubleIfPresent(source, start, end, "sy", -10.0);
        writeNamedDoubleIfPresent(source, start, end, "ai", 2.0);
        writeNamedDouble(source, start, end, "int1", (double) edit.itemActionGroup);
        writeNamedDouble(source, start, end, "ref", edit.ref);
        writeNamedDouble(source, start, end, "x", edit.x);
        writeNamedDouble(source, start, end, "y", edit.y);
        writeNamedDouble(source, start, end, "z", edit.z);
        writeNamedDouble(source, start, end, "vx", edit.vx);
        writeNamedDouble(source, start, end, "vy", edit.vy);
        writeNamedDouble(source, start, end, "vz", edit.vz);
    }

    private static void writeNamedDouble(byte[] source, String fieldName, double value) {
        byte[] pattern = doubleFieldPattern(fieldName);
        int offset = indexOf(source, pattern, 0);
        if (offset < 0) {
            throw new IllegalStateException("Could not find Data.Pt field: " + fieldName);
        }
        writeDouble(source, offset + pattern.length, value);
    }

    private static void writeNamedDouble(
            byte[] source,
            int start,
            int end,
            String fieldName,
            double value
    ) {
        byte[] pattern = doubleFieldPattern(fieldName);
        int offset = indexOf(source, pattern, start, end);
        if (offset < 0) {
            throw new IllegalStateException("Could not find Data.Pt field: " + fieldName);
        }
        writeDouble(source, offset + pattern.length, value);
    }

    private static boolean hasNamedDouble(byte[] source, String fieldName, double value) {
        byte[] pattern = doubleFieldPattern(fieldName);
        int offset = indexOf(source, pattern, 0);
        if (offset < 0) {
            return false;
        }
        byte[] expected = doubleBytes(value);
        return matches(source, offset + pattern.length, expected);
    }

    private static boolean hasNamedDoubleField(byte[] source, String fieldName) {
        return indexOf(source, doubleFieldPattern(fieldName), 0) >= 0;
    }

    private static byte[] doubleFieldPattern(String fieldName) {
        byte[] name = fieldName.getBytes(StandardCharsets.UTF_8);
        if (name.length > 65535) {
            throw new IllegalArgumentException("field name is too long");
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream(name.length + 3);
        out.write((name.length >> 8) & 0xff);
        out.write(name.length & 0xff);
        out.write(name, 0, name.length);
        out.write(0);
        return out.toByteArray();
    }

    private static void writeNamedDoubleIfPresent(byte[] source, String fieldName, double value) {
        byte[] pattern = doubleFieldPattern(fieldName);
        int offset = indexOf(source, pattern, 0);
        if (offset >= 0) {
            writeDouble(source, offset + pattern.length, value);
        }
    }

    private static void writeNamedDoubleIfPresent(
            byte[] source,
            int start,
            int end,
            String fieldName,
            double value
    ) {
        byte[] pattern = doubleFieldPattern(fieldName);
        int offset = indexOf(source, pattern, start, end);
        if (offset >= 0) {
            writeDouble(source, offset + pattern.length, value);
        }
    }

    private static byte[] applyLimbNameReplacements(
            byte[] source,
            String replacementSpec,
            PatchStats stats
    ) {
        byte[] patched = source;
        if (replacementSpec == null || replacementSpec.isEmpty()) {
            return patched;
        }
        String[] pairs = replacementSpec.split("\\|");
        for (String pair : pairs) {
            if (pair.isEmpty()) {
                continue;
            }
            String[] parts = pair.split("=>", 2);
            if (parts.length != 2) {
                throw new IllegalArgumentException("Invalid limb replacement: " + pair);
            }
            if (parts[1].isEmpty()) {
                patched = replaceNamedUtfStringValue(
                        patched,
                        "limbName",
                        parts[0],
                        "",
                        stats,
                        ReplacementKind.DONOR_LIMB,
                        true
                );
            } else {
                patched = replaceExactUtfStrings(
                        patched,
                        parts[0],
                        parts[1],
                        stats,
                        ReplacementKind.DONOR_LIMB,
                        true
                );
            }
        }
        return patched;
    }

    private static byte[] applyTextureReplacements(
            byte[] source,
            String textureSpec,
            PatchStats stats
    ) {
        byte[] patched = source;
        if (textureSpec == null || textureSpec.isEmpty()) {
            return patched;
        }
        String[] pairs = textureSpec.split("\\|");
        for (String pair : pairs) {
            if (pair.isEmpty()) {
                continue;
            }
            String[] parts = pair.split("=>", 2);
            if (parts.length != 2) {
                throw new IllegalArgumentException("Invalid texture replacement: " + pair);
            }
            patched = replaceExactUtfStrings(
                    patched,
                    parts[0],
                    parts[1],
                    stats,
                    ReplacementKind.TEXTURE
            );
        }
        return patched;
    }

    private static byte[] doubleBytes(double value) {
        long bits = Double.doubleToLongBits(value);
        byte[] out = new byte[8];
        for (int i = 7; i >= 0; i--) {
            out[i] = (byte) (bits & 0xff);
            bits >>>= 8;
        }
        return out;
    }

    private static void writeDouble(byte[] source, int offset, double value) {
        byte[] bytes = doubleBytes(value);
        for (int i = 0; i < bytes.length; i++) {
            source[offset + i] = bytes[i];
        }
    }

    private static byte[] concat(byte[] left, byte[] right) {
        byte[] out = new byte[left.length + right.length];
        System.arraycopy(left, 0, out, 0, left.length);
        System.arraycopy(right, 0, out, left.length, right.length);
        return out;
    }

    private static boolean matches(byte[] source, int offset, byte[] needle) {
        if (offset < 0 || offset + needle.length > source.length) {
            return false;
        }
        for (int i = 0; i < needle.length; i++) {
            if (source[offset + i] != needle[i]) {
                return false;
            }
        }
        return true;
    }

    private static int indexOf(byte[] source, byte[] needle, int start) {
        for (int i = start; i <= source.length - needle.length; i++) {
            if (matches(source, i, needle)) {
                return i;
            }
        }
        return -1;
    }

    private static int indexOf(byte[] source, byte[] needle, int start, int end) {
        int stop = Math.min(end, source.length) - needle.length;
        for (int i = start; i <= stop; i++) {
            if (matches(source, i, needle)) {
                return i;
            }
        }
        return -1;
    }

    private static int lastIndexOf(byte[] source, byte[] needle, int start, int end) {
        for (int i = end - needle.length; i >= start; i--) {
            if (matches(source, i, needle)) {
                return i;
            }
        }
        return -1;
    }

    private interface UtfReplacement {
        byte[] replacementFor(byte[] value, int length);
    }

    private enum ReplacementKind {
        LIMB_NAME,
        LMI_PATH,
        TEXTURE,
        DONOR_LIMB,
        EXTERNAL_LMI_ALIAS
    }

    private static Amf3ByteArray readAmf3ByteArray(byte[] source) {
        return readAmf3ByteArray(source, 0);
    }

    private static Amf3ByteArray readAmf3ByteArray(byte[] source, int offset) {
        if (source.length < offset + 2 || source[offset] != 0x0c) {
            throw new IllegalStateException("Expected AMF3 ByteArray payload");
        }
        U29Result result = readU29(source, offset + 1);
        if ((result.value & 1) == 0) {
            throw new IllegalStateException("AMF3 ByteArray reference payload is not supported");
        }
        int length = result.value >> 1;
        if (result.nextOffset + length > source.length) {
            throw new IllegalStateException("AMF3 ByteArray length exceeds payload size");
        }
        return new Amf3ByteArray(
                Arrays.copyOfRange(source, result.nextOffset, result.nextOffset + length),
                result.nextOffset + length
        );
    }

    private static byte[] writeAmf3ByteArray(byte[] bytes) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        out.write(0x0c);
        writeU29(out, (bytes.length << 1) | 1);
        out.write(bytes, 0, bytes.length);
        return out.toByteArray();
    }

    private static U29Result readU29(byte[] source, int offset) {
        int value = 0;
        int current = source[offset++] & 0xff;
        if (current < 128) {
            return new U29Result(current, offset);
        }
        value = (current & 0x7f) << 7;
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

    private static void writeU29(ByteArrayOutputStream out, int value) {
        if (value < 0x80) {
            out.write(value);
        } else if (value < 0x4000) {
            out.write(((value >> 7) & 0x7f) | 0x80);
            out.write(value & 0x7f);
        } else if (value < 0x200000) {
            out.write(((value >> 14) & 0x7f) | 0x80);
            out.write(((value >> 7) & 0x7f) | 0x80);
            out.write(value & 0x7f);
        } else {
            out.write(((value >> 22) & 0x7f) | 0x80);
            out.write(((value >> 15) & 0x7f) | 0x80);
            out.write(((value >> 8) & 0x7f) | 0x80);
            out.write(value & 0xff);
        }
    }

    private static byte[] inflate(byte[] bytes) {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            try (InflaterInputStream inflater = new InflaterInputStream(
                    new ByteArrayInputStream(bytes)
            )) {
                byte[] buffer = new byte[8192];
                int read;
                while ((read = inflater.read(buffer)) != -1) {
                    out.write(buffer, 0, read);
                }
            }
            return out.toByteArray();
        } catch (Exception ex) {
            throw new IllegalStateException("Could not inflate SPT binary data", ex);
        }
    }

    private static byte[] deflate(byte[] bytes) {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            try (DeflaterOutputStream deflater = new DeflaterOutputStream(out)) {
                deflater.write(bytes);
            }
            return out.toByteArray();
        } catch (Exception ex) {
            throw new IllegalStateException("Could not deflate SPT binary data", ex);
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

    private static class RawString {
        final String value;
        final int nextOffset;

        RawString(String value, int nextOffset) {
            this.value = value;
            this.nextOffset = nextOffset;
        }
    }

    private static class SptContainer {
        final String fileType;
        final byte[] bytes;

        SptContainer(String fileType, byte[] bytes) {
            this.fileType = fileType;
            this.bytes = bytes;
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

    private static class FrameItemEdit {
        final int frameIndex;
        final PtEdit[] slots;

        FrameItemEdit(int frameIndex, PtEdit[] slots) {
            this.frameIndex = frameIndex;
            this.slots = slots;
        }
    }

    private static class PtEdit {
        final int itemActionGroup;
        final double ref;
        final double x;
        final double y;
        final double z;
        final double vx;
        final double vy;
        final double vz;

        PtEdit(
                int itemActionGroup,
                double ref,
                double x,
                double y,
                double z,
                double vx,
                double vy,
                double vz
        ) {
            this.itemActionGroup = itemActionGroup;
            this.ref = ref;
            this.x = x;
            this.y = y;
            this.z = z;
            this.vx = vx;
            this.vy = vy;
            this.vz = vz;
        }
    }

    private static class PatchStats {
        int sptIdReplacements = 0;
        int limbNameReplacements = 0;
        int lmiPathReplacements = 0;
        int brk0Patches = 0;
        int frameItemEdits = 0;
        int generatedCreSlots = 0;
        int fallbackPtTemplateUses = 0;
        int textureReplacements = 0;
        int donorLimbNameReplacements = 0;
        int externalLmiLimbAliases = 0;

        void count(ReplacementKind kind) {
            if (kind == ReplacementKind.LIMB_NAME) {
                limbNameReplacements++;
            } else if (kind == ReplacementKind.LMI_PATH) {
                lmiPathReplacements++;
            } else if (kind == ReplacementKind.TEXTURE) {
                textureReplacements++;
            } else if (kind == ReplacementKind.DONOR_LIMB) {
                donorLimbNameReplacements++;
            } else if (kind == ReplacementKind.EXTERNAL_LMI_ALIAS) {
                externalLmiLimbAliases++;
            }
        }
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
'''
