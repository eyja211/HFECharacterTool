from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hfe_character_tool.tools import (
    ToolConfig,
    ToolResult,
    extract_projector_swf,
    run_tool,
)

PROBE_CLASS_NAME = "HfeSwfProbe"
PROBE_SCHEMA_VERSION = 5


@dataclass(frozen=True)
class SwfProbeReport:
    base_swf_path: Path
    java_source_path: Path
    class_dir: Path
    raw_json: dict[str, Any]
    compile_result: ToolResult
    run_result: ToolResult

    @property
    def can_locate_global(self) -> bool:
        classes = self.raw_json.get("classes", {})
        return isinstance(classes, dict) and bool(classes.get("Data.Global"))

    @property
    def can_locate_loaders(self) -> bool:
        strings = self.raw_json.get("string_constants", {})
        multinames = self.raw_json.get("multiname_constants", {})
        if not isinstance(strings, dict) or not isinstance(multinames, dict):
            return False
        return bool(
            strings.get("loadBinaryFileCount")
            and strings.get("loadtimeOffSet")
            and multinames.get("LoadFromCompressedBytes")
        )

    @property
    def global_method_candidates(self) -> tuple[dict[str, Any], ...]:
        candidates = self.raw_json.get("global_method_candidates", [])
        if not isinstance(candidates, list):
            return ()
        return tuple(item for item in candidates if isinstance(item, dict))

    @property
    def load_offset_hits(self) -> tuple[dict[str, Any], ...]:
        hits = self.raw_json.get("load_offset_hits", [])
        if not isinstance(hits, list):
            return ()
        return tuple(item for item in hits if isinstance(item, dict))

    @property
    def loader_anchor_hits(self) -> tuple[dict[str, Any], ...]:
        hits = self.raw_json.get("loader_anchor_hits", [])
        if not isinstance(hits, list):
            return ()
        return tuple(item for item in hits if isinstance(item, dict))

    @property
    def abc_data_global_classes(self) -> tuple[str, ...]:
        classes = self.raw_json.get("abc_data_global_classes", [])
        if not isinstance(classes, list):
            return ()
        return tuple(item for item in classes if isinstance(item, str))

    @property
    def missing_symbol_abc_classes(self) -> tuple[str, ...]:
        classes = self.raw_json.get("missing_symbol_abc_classes", [])
        if not isinstance(classes, list):
            return ()
        return tuple(item for item in classes if isinstance(item, str))

    @property
    def global_pow_character_ids(self) -> tuple[str, ...]:
        role_ids = self.raw_json.get("global_pow_character_ids", [])
        if not isinstance(role_ids, list):
            return ()
        return tuple(item for item in role_ids if isinstance(item, str))


def probe_original_game(workspace: Path, tools: ToolConfig, output_dir: Path) -> SwfProbeReport:
    if tools.original_game is None:
        raise ValueError("缺少原版 HFE EXE 路径配置。")
    output_dir.mkdir(exist_ok=True)
    base_swf_path = output_dir / "hfe_v1_0_2_probe_base.swf"
    extract_projector_swf(tools.original_game, base_swf_path)
    return probe_swf(base_swf_path, tools, output_dir, workspace)


def probe_swf(
    swf_path: Path,
    tools: ToolConfig,
    output_dir: Path,
    cwd: Path,
) -> SwfProbeReport:
    source_path = write_probe_source(output_dir)
    class_dir = output_dir / "probe_classes"
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
        return SwfProbeReport(
            swf_path,
            source_path,
            class_dir,
            {},
            compile_result,
            ToolResult(False, -1, "", "probe compile failed"),
        )
    classpath = f"{class_dir};{tools.ffdec}"
    run_result = run_tool(("java", "-cp", classpath, PROBE_CLASS_NAME, str(swf_path)), cwd)
    raw = _parse_probe_json(run_result.stdout) if run_result.success else {}
    return SwfProbeReport(swf_path, source_path, class_dir, raw, compile_result, run_result)


def write_probe_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{PROBE_CLASS_NAME}.java"
    path.write_text(PROBE_JAVA_SOURCE, encoding="utf-8")
    return path


def _parse_probe_json(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


PROBE_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.traits.Trait;
import com.jpexs.decompiler.flash.abc.types.traits.TraitMethodGetterSetter;
import com.jpexs.decompiler.flash.abc.avm2.AVM2Code;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.DefineBinaryDataTag;
import com.jpexs.decompiler.flash.tags.SymbolClassTag;
import com.jpexs.decompiler.flash.tags.Tag;
import com.jpexs.decompiler.graph.DottedChain;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.LinkedHashMap;
import java.util.Map;

public class HfeSwfProbe {
    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("Usage: HfeSwfProbe <in.swf>");
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        Map<String, Boolean> classes = new LinkedHashMap<>();
        classes.put("Data.Global", false);
        classes.put("Data.Spt", false);
        classes.put("Data.LimbInfoFile", false);
        classes.put("Data.Global_lucasSpt", false);
        classes.put("Data.Global_lucasLmi", false);
        classes.put("Data.Global_codexSpt", false);
        classes.put("Data.Global_codexLmi", false);
        classes.put("Data.Global_codexcloneSpt", false);
        classes.put("Data.Global_codexcloneLmi", false);

        Map<String, Boolean> strings = new LinkedHashMap<>();
        strings.put("loadBinaryFileCount", false);
        strings.put("loadtimeOffSet", false);
        strings.put("sptIds", false);
        strings.put("sptClasses", false);
        strings.put("lmiIds", false);
        strings.put("lmiClasses", false);

        Map<String, Boolean> multinames = new LinkedHashMap<>();
        multinames.put("LoadFromCompressedBytes", false);
        multinames.put("loadBinaryFileCount", false);
        multinames.put("loadtimeOffSet", false);
        multinames.put("sptIds", false);
        multinames.put("lmiIds", false);

        int abcCount = 0;
        List<String> globalMethodCandidates = new ArrayList<>();
        List<String> loadOffsetHits = new ArrayList<>();
        List<String> loaderAnchorHits = new ArrayList<>();
        List<String> globalRegistryAssignments = new ArrayList<>();
        List<String> globalPowCharacterIds = new ArrayList<>();
        List<String> globalPowEntries = new ArrayList<>();
        List<String> globalCharListOrder = new ArrayList<>();
        List<String> selectCharOptions = new ArrayList<>();
        List<String> customLoaderReports = new ArrayList<>();
        List<String> abcDataGlobalClasses = new ArrayList<>();
        for (ABCContainerTag tag : swf.getAbcList()) {
            abcCount++;
            ABC abc = tag.getABC();
            for (int ci = 0; ci < abc.instance_info.size(); ci++) {
                InstanceInfo ii = abc.instance_info.get(ci);
                String className = abc.constants.getMultiname(ii.name_index)
                        .getNameWithNamespace(abc.constants, true).toString();
                if (classes.containsKey(className)) {
                    classes.put(className, true);
                }
                if (className.startsWith("Data.Global_")) {
                    abcDataGlobalClasses.add(className);
                }
                if ("Data.Global".equals(className)) {
                    collectGlobalMethods(
                            globalMethodCandidates,
                            loadOffsetHits,
                            loaderAnchorHits,
                            globalRegistryAssignments,
                            globalPowCharacterIds,
                            globalPowEntries,
                            globalCharListOrder,
                            customLoaderReports,
                            abc,
                            ci
                    );
                }
                if ("Web_misc.SelectCharPanel".equals(className)) {
                    collectSelectCharOptions(selectCharOptions, abc, ci);
                }
            }
            for (int i = 1; i < abc.constants.getStringCount(); i++) {
                String s = abc.constants.getString(i);
                if (strings.containsKey(s)) {
                    strings.put(s, true);
                }
            }
            for (int i = 1; i < abc.constants.getMultinameCount(); i++) {
                String text = abc.constants.multinameToString(i);
                for (String key : new ArrayList<>(multinames.keySet())) {
                    if (text.contains(key)) {
                        multinames.put(key, true);
                    }
                }
            }
        }

        int symbolClassCount = 0;
        int maxSymbolId = 0;
        List<String> dataGlobalSymbols = new ArrayList<>();
        List<String> missingSymbolAbcClasses = new ArrayList<>();
        for (Tag tag : swf.getTags()) {
            if (tag instanceof SymbolClassTag) {
                SymbolClassTag symbol = (SymbolClassTag) tag;
                symbolClassCount += symbol.tags.size();
                for (int i = 0; i < symbol.tags.size(); i++) {
                    Integer id = symbol.tags.get(i);
                    if (id != null && id > maxSymbolId) {
                        maxSymbolId = id;
                    }
                    String name = i < symbol.names.size() ? symbol.names.get(i) : "";
                    if (id != null && name.startsWith("Data.Global_")) {
                        int binarySize = binaryDataSize(swf, id);
                        if (!abcDataGlobalClasses.contains(name)) {
                            missingSymbolAbcClasses.add(name);
                        }
                        StringBuilder item = new StringBuilder();
                        item.append("{");
                        item.append("\"id\":").append(id).append(",");
                        item.append("\"name\":\"").append(jsonEscape(name)).append("\",");
                        item.append("\"binary_size\":").append(binarySize);
                        item.append("}");
                        dataGlobalSymbols.add(item.toString());
                    }
                }
            }
        }

        StringBuilder out = new StringBuilder();
        out.append("{");
        out.append("\"probe_schema_version\":5,");
        out.append("\"abc_count\":").append(abcCount).append(",");
        out.append("\"symbol_class_count\":").append(symbolClassCount).append(",");
        out.append("\"max_symbol_id\":").append(maxSymbolId).append(",");
        appendMap(out, "classes", classes);
        out.append(",");
        appendMap(out, "string_constants", strings);
        out.append(",");
        appendMap(out, "multiname_constants", multinames);
        out.append(",");
        appendStringArray(out, "global_method_candidates", globalMethodCandidates);
        out.append(",");
        appendStringArray(out, "load_offset_hits", loadOffsetHits);
        out.append(",");
        appendStringArray(out, "loader_anchor_hits", loaderAnchorHits);
        out.append(",");
        appendStringArray(out, "global_registry_assignments", globalRegistryAssignments);
        out.append(",");
        appendPlainStringArray(out, "global_pow_character_ids", globalPowCharacterIds);
        out.append(",");
        appendStringArray(out, "global_pow_entries", globalPowEntries);
        out.append(",");
        appendPlainStringArray(out, "global_char_list_order", globalCharListOrder);
        out.append(",");
        appendStringArray(out, "select_char_options", selectCharOptions);
        out.append(",");
        appendStringArray(out, "custom_loader_reports", customLoaderReports);
        out.append(",");
        appendPlainStringArray(out, "abc_data_global_classes", abcDataGlobalClasses);
        out.append(",");
        appendPlainStringArray(out, "missing_symbol_abc_classes", missingSymbolAbcClasses);
        out.append(",");
        appendStringArray(out, "data_global_symbols", dataGlobalSymbols);
        out.append("}");
        System.out.println(out.toString());
    }

    private static int binaryDataSize(SWF swf, int id) {
        for (Tag tag : swf.getTags()) {
            if (tag instanceof DefineBinaryDataTag) {
                DefineBinaryDataTag binary = (DefineBinaryDataTag) tag;
                if (binary.getCharacterId() == id) {
                    return binary.getDataBytes().getLength();
                }
            }
        }
        return -1;
    }

    private static void appendMap(StringBuilder out, String name, Map<String, Boolean> values) {
        out.append("\"").append(name).append("\":{");
        boolean first = true;
        for (Map.Entry<String, Boolean> entry : values.entrySet()) {
            if (!first) {
                out.append(",");
            }
            first = false;
            out.append("\"").append(jsonEscape(entry.getKey())).append("\":").append(entry.getValue());
        }
        out.append("}");
    }

    private static void appendStringArray(StringBuilder out, String name, List<String> values) {
        out.append("\"").append(name).append("\":[");
        boolean first = true;
        for (String value : values) {
            if (!first) {
                out.append(",");
            }
            first = false;
            out.append(value);
        }
        out.append("]");
    }

    private static void appendPlainStringArray(
            StringBuilder out,
            String name,
            List<String> values
    ) {
        out.append("\"").append(name).append("\":[");
        boolean first = true;
        for (String value : values) {
            if (!first) {
                out.append(",");
            }
            first = false;
            out.append("\"").append(jsonEscape(value)).append("\"");
        }
        out.append("]");
    }

    private static void collectGlobalMethods(
            List<String> out,
            List<String> loadOffsetHits,
            List<String> loaderAnchorHits,
            List<String> globalRegistryAssignments,
            List<String> globalPowCharacterIds,
            List<String> globalPowEntries,
            List<String> globalCharListOrder,
            List<String> customLoaderReports,
            ABC abc,
            int classIndex
    ) throws Exception {
        MethodBody cinit = abc.findBody(abc.class_info.get(classIndex).cinit_index);
        collectGlobalMetadata(
                globalPowCharacterIds,
                globalPowEntries,
                globalCharListOrder,
                abc,
                cinit
        );
        addMethodCandidate(
                out,
                loadOffsetHits,
                loaderAnchorHits,
                globalRegistryAssignments,
                customLoaderReports,
                abc,
                "static::<cinit>",
                cinit
        );
        MethodBody iinit = abc.findBody(abc.instance_info.get(classIndex).iinit_index);
        addMethodCandidate(
                out,
                loadOffsetHits,
                loaderAnchorHits,
                globalRegistryAssignments,
                customLoaderReports,
                abc,
                "instance::<iinit>",
                iinit
        );
        for (Trait trait : abc.instance_info.get(classIndex).instance_traits.traits) {
            addTraitCandidate(
                    out,
                    loadOffsetHits,
                    loaderAnchorHits,
                    globalRegistryAssignments,
                    customLoaderReports,
                    abc,
                    "instance",
                    trait
            );
        }
        for (Trait trait : abc.class_info.get(classIndex).static_traits.traits) {
            addTraitCandidate(
                    out,
                    loadOffsetHits,
                    loaderAnchorHits,
                    globalRegistryAssignments,
                    customLoaderReports,
                    abc,
                    "static",
                    trait
            );
        }
    }

    private static void collectGlobalMetadata(
            List<String> powIds,
            List<String> powEntries,
            List<String> charListOrder,
            ABC abc,
            MethodBody body
    ) throws Exception {
        if (body == null) {
            return;
        }
        int qPow = abc.constants.getPublicQnameId("pow", true);
        int qCharListOrder = abc.constants.getPublicQnameId("charListOrder", true);
        AVM2Code code = body.getCode();
        collectCharListOrder(charListOrder, abc, code, qCharListOrder);
        for (int i = 0; i < code.code.size(); i++) {
            String roleId = pushedString(abc, code.code.get(i));
            if (!isRoleIdCandidate(roleId)) {
                continue;
            }
            if (!isPowReferenceNear(code, i, qPow)) {
                continue;
            }
            int objectEnd = findPowObjectEnd(code, i + 1);
            if (hasPowObjectFields(abc, code, i + 1, objectEnd)) {
                int index = readObjectPairInt(abc, code, i + 1, objectEnd, "i");
                if (!powIds.contains(roleId)) {
                    powIds.add(roleId);
                }
                appendPowEntry(powEntries, roleId, index);
            }
        }
    }

    private static void collectCharListOrder(
            List<String> out,
            ABC abc,
            AVM2Code code,
            int qCharListOrder
    ) {
        for (int i = 0; i < code.code.size(); i++) {
            if (!isPropertyAccess(code.code.get(i), qCharListOrder)) {
                continue;
            }
            int stop = Math.min(code.code.size(), i + 160);
            for (int j = i + 1; j < stop; j++) {
                String roleId = pushedString(abc, code.code.get(j));
                if (isRoleIdCandidate(roleId) && !out.contains(roleId)) {
                    out.add(roleId);
                }
                String name = code.code.get(j).definition.instructionName;
                if ("setproperty".equals(name) || "initproperty".equals(name)
                        || "callpropvoid".equals(name)) {
                    break;
                }
            }
        }
    }

    private static void appendPowEntry(List<String> out, String roleId, int index) {
        for (String item : out) {
            if (item.contains("\"id\":\"" + jsonEscape(roleId) + "\"")) {
                return;
            }
        }
        StringBuilder entry = new StringBuilder();
        entry.append("{");
        entry.append("\"id\":\"").append(jsonEscape(roleId)).append("\",");
        entry.append("\"index\":").append(index);
        entry.append("}");
        out.add(entry.toString());
    }

    private static void collectSelectCharOptions(
            List<String> out,
            ABC abc,
            int classIndex
    ) throws Exception {
        MethodBody cinit = abc.findBody(abc.class_info.get(classIndex).cinit_index);
        if (cinit == null) {
            return;
        }
        int charOptionQName = findStaticTraitQName(abc, classIndex, "charOption");
        AVM2Code code = cinit.getCode();
        int start = firstPropertyAccess(code, charOptionQName);
        if (start < 0) {
            start = 0;
        }
        for (int i = start; i < code.code.size(); i++) {
            if (!isPushString(abc, code.code.get(i), "id")) {
                continue;
            }
            String id = findPushedStringAfter(abc, code, i + 1, Math.min(i + 8, code.code.size()));
            if (!isRoleIdCandidate(id)) {
                continue;
            }
            int value = findNearestValuePairBefore(abc, code, i);
            String name = findNearestStringPairBefore(abc, code, i, "name");
            if (value < 0 || name == null) {
                continue;
            }
            appendSelectCharOption(out, id, name, value);
        }
    }

    private static int findStaticTraitQName(ABC abc, int classIndex, String traitName) {
        for (Trait trait : abc.class_info.get(classIndex).static_traits.traits) {
            String name = abc.constants.getMultiname(trait.name_index)
                    .getName(
                            abc.constants,
                            new ArrayList<DottedChain>(),
                            true,
                            true
                    ).toString();
            if (traitName.equals(name)) {
                return trait.name_index;
            }
        }
        return -1;
    }

    private static int firstPropertyAccess(AVM2Code code, int qname) {
        if (qname < 0) {
            return -1;
        }
        for (int i = 0; i < code.code.size(); i++) {
            if (isPropertyAccess(code.code.get(i), qname)) {
                return i;
            }
        }
        return -1;
    }

    private static void appendSelectCharOption(
            List<String> out,
            String id,
            String name,
            int value
    ) {
        for (String item : out) {
            if (item.contains("\"id\":\"" + jsonEscape(id) + "\"")) {
                return;
            }
        }
        StringBuilder entry = new StringBuilder();
        entry.append("{");
        entry.append("\"id\":\"").append(jsonEscape(id)).append("\",");
        entry.append("\"name\":\"").append(jsonEscape(name)).append("\",");
        entry.append("\"value\":").append(value);
        entry.append("}");
        out.add(entry.toString());
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

    private static String findNearestStringPairBefore(
            ABC abc,
            AVM2Code code,
            int beforeIndex,
            String key
    ) {
        int start = Math.max(0, beforeIndex - 80);
        for (int i = beforeIndex - 1; i >= start; i--) {
            if (isPushString(abc, code.code.get(i), key)) {
                String value = findPushedStringAfter(abc, code, i + 1, beforeIndex);
                if (value != null) {
                    return value;
                }
            }
        }
        return null;
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

    private static String findPushedStringAfter(
            ABC abc,
            AVM2Code code,
            int start,
            int stopExclusive
    ) {
        int stop = Math.min(stopExclusive, code.code.size());
        for (int i = start; i < stop; i++) {
            String value = pushedString(abc, code.code.get(i));
            if (value != null) {
                return value;
            }
        }
        return null;
    }

    private static int findPowObjectEnd(AVM2Code code, int start) {
        int stop = Math.min(code.code.size(), start + 120);
        for (int i = start; i < stop; i++) {
            if ("newobject".equals(code.code.get(i).definition.instructionName)
                    && newObjectPairCount(code.code.get(i)) >= 10
                    && (i + 1 >= code.code.size()
                            || "setproperty".equals(
                                    code.code.get(i + 1).definition.instructionName))) {
                return i;
            }
        }
        return stop;
    }

    private static boolean hasPowObjectFields(
            ABC abc,
            AVM2Code code,
            int start,
            int stopExclusive
    ) {
        return hasObjectKey(abc, code, start, stopExclusive, "i")
                && hasObjectKey(abc, code, start, stopExclusive, "hp0")
                && hasObjectKey(abc, code, start, stopExclusive, "mp0")
                && hasObjectKey(abc, code, start, stopExclusive, "str0")
                && hasObjectKey(abc, code, start, stopExclusive, "state");
    }

    private static boolean hasObjectKey(
            ABC abc,
            AVM2Code code,
            int start,
            int stopExclusive,
            String key
    ) {
        int stop = Math.min(stopExclusive, code.code.size());
        for (int i = start; i < stop; i++) {
            if (isPushString(abc, code.code.get(i), key)) {
                return true;
            }
        }
        return false;
    }

    private static int readObjectPairInt(
            ABC abc,
            AVM2Code code,
            int start,
            int stopExclusive,
            String key
    ) {
        int stop = Math.min(stopExclusive, code.code.size());
        for (int i = start; i < stop; i++) {
            if (!isPushString(abc, code.code.get(i), key)) {
                continue;
            }
            int value = i + 1 < stop ? pushedInt(code.code.get(i + 1)) : -1;
            if (value >= 0) {
                return value;
            }
        }
        return -1;
    }

    private static boolean isRoleIdCandidate(String value) {
        if (value == null || value.isEmpty()) {
            return false;
        }
        if (isPowObjectKey(value)) {
            return false;
        }
        for (int i = 0; i < value.length(); i++) {
            char ch = value.charAt(i);
            boolean ok = (ch >= 'a' && ch <= 'z')
                    || (ch >= 'A' && ch <= 'Z')
                    || (ch >= '0' && ch <= '9')
                    || ch == '_';
            if (!ok) {
                return false;
            }
        }
        return true;
    }

    private static boolean isPowObjectKey(String value) {
        return "i".equals(value)
                || "hp0".equals(value)
                || "mp0".equals(value)
                || "str0".equals(value)
                || "lv".equals(value)
                || "hp".equals(value)
                || "mp".equals(value)
                || "str".equals(value)
                || "exp".equals(value)
                || "state".equals(value)
                || "hp_".equals(value)
                || "mp_".equals(value)
                || "str_".equals(value)
                || "lv_".equals(value)
                || "exp_".equals(value)
                || "c".equals(value)
                || "fake_hp".equals(value);
    }

    private static boolean isPowReferenceNear(AVM2Code code, int beforeIndex, int qname) {
        int start = Math.max(0, beforeIndex - 8);
        for (int i = beforeIndex - 1; i >= start; i--) {
            AVM2Instruction ins = code.code.get(i);
            if (isPowAccess(ins, qname)) {
                return true;
            }
            String name = ins.definition.instructionName;
            if ("newarray".equals(name) || "newobject".equals(name) || "setproperty".equals(name)) {
                return false;
            }
        }
        return false;
    }

    private static void addTraitCandidate(
            List<String> out,
            List<String> loadOffsetHits,
            List<String> loaderAnchorHits,
            List<String> globalRegistryAssignments,
            List<String> customLoaderReports,
            ABC abc,
            String scope,
            Trait trait
    ) throws Exception {
        if (!(trait instanceof TraitMethodGetterSetter)) {
            return;
        }
        TraitMethodGetterSetter methodTrait = (TraitMethodGetterSetter) trait;
        String name = abc.constants.getMultiname(trait.name_index)
                .getName(abc.constants, new ArrayList<DottedChain>(), true, true).toString();
        addMethodCandidate(
                out,
                loadOffsetHits,
                loaderAnchorHits,
                globalRegistryAssignments,
                customLoaderReports,
                abc,
                scope + "::" + name,
                abc.findBody(methodTrait.method_info)
        );
    }

    private static void addMethodCandidate(
            List<String> out,
            List<String> loadOffsetHits,
            List<String> loaderAnchorHits,
            List<String> globalRegistryAssignments,
            List<String> customLoaderReports,
            ABC abc,
            String name,
            MethodBody body
    ) throws Exception {
        if (body == null) {
            return;
        }
        AVM2Code code = body.getCode();
        int instructionCount = code.code.size();
        boolean hasLoadBinaryFileCount = false;
        boolean hasLoadtimeOffSet = false;
        boolean hasLoadFromCompressedBytes = false;
        boolean hasSpt = false;
        boolean hasLmi = false;
        boolean hasSptIds = false;
        boolean hasSptClasses = false;
        boolean hasLmiIds = false;
        boolean hasLmiClasses = false;
        List<String> instructionTexts = new ArrayList<>();
        for (int i = 0; i < code.code.size(); i++) {
            AVM2Instruction ins = code.code.get(i);
            String text = ins.toStringNoAddress(abc.constants, new ArrayList<DottedChain>());
            instructionTexts.add(text);
            if (text.contains("loadBinaryFileCount")) {
                hasLoadBinaryFileCount = true;
                addInstructionWindowHit(
                        loaderAnchorHits, name, "loadBinaryFileCount", i, instructionTexts, code
                );
            }
            if (text.contains("loadtimeOffSet")) {
                hasLoadtimeOffSet = true;
                addInstructionWindowHit(
                        loaderAnchorHits, name, "loadtimeOffSet", i, instructionTexts, code
                );
            }
            if (text.contains("LoadFromCompressedBytes")) {
                hasLoadFromCompressedBytes = true;
                addInstructionWindowHit(
                        loaderAnchorHits, name, "LoadFromCompressedBytes", i, instructionTexts, code
                );
            }
            if (text.contains("Spt")) {
                hasSpt = true;
            }
            if (text.contains("LimbInfoFile")) {
                hasLmi = true;
            }
            if (text.contains("sptIds")) {
                hasSptIds = true;
            }
            if (text.contains("sptClasses")) {
                hasSptClasses = true;
            }
            if (text.contains("lmiIds")) {
                hasLmiIds = true;
            }
            if (text.contains("lmiClasses")) {
                hasLmiClasses = true;
            }
            String registryField = registryFieldName(text);
            if ("static::<cinit>".equals(name)
                    && registryField != null
                    && text.contains("setproperty")) {
                addInstructionWindowHit(
                        globalRegistryAssignments,
                        name,
                        registryField,
                        i,
                        instructionTexts,
                        code
                );
            }
            if (isPushShort(ins, 121)) {
                addLoadOffsetHit(loadOffsetHits, name, 121, i, text, instructionTexts, code);
            }
            if (isPushShort(ins, 351)) {
                addLoadOffsetHit(loadOffsetHits, name, 351, i, text, instructionTexts, code);
            }
        }
        collectCustomLoaderReports(customLoaderReports, abc, name, code);
        boolean interesting = hasLoadBinaryFileCount || hasLoadtimeOffSet
                || hasLoadFromCompressedBytes || hasSpt || hasLmi;
        if (!interesting) {
            return;
        }
        StringBuilder item = new StringBuilder();
        item.append("{");
        item.append("\"name\":\"").append(jsonEscape(name)).append("\",");
        item.append("\"max_regs\":").append(body.max_regs).append(",");
        item.append("\"instruction_count\":").append(instructionCount).append(",");
        item.append("\"has_loadBinaryFileCount\":").append(hasLoadBinaryFileCount).append(",");
        item.append("\"has_loadtimeOffSet\":").append(hasLoadtimeOffSet).append(",");
        item.append("\"has_LoadFromCompressedBytes\":").append(hasLoadFromCompressedBytes).append(",");
        item.append("\"has_Spt\":").append(hasSpt).append(",");
        item.append("\"has_LimbInfoFile\":").append(hasLmi).append(",");
        item.append("\"has_sptIds\":").append(hasSptIds).append(",");
        item.append("\"has_sptClasses\":").append(hasSptClasses).append(",");
        item.append("\"has_lmiIds\":").append(hasLmiIds).append(",");
        item.append("\"has_lmiClasses\":").append(hasLmiClasses);
        item.append("}");
        out.add(item.toString());
    }

    private static void collectCustomLoaderReports(
            List<String> out,
            ABC abc,
            String methodName,
            AVM2Code code
    ) {
        for (int i = 0; i < code.code.size(); i++) {
            AVM2Instruction ins = code.code.get(i);
            if (!isCallPropVoid(abc, ins, "LoadFromCompressedBytes")) {
                continue;
            }
            boolean hasSptIds = hasGetLexBeforeCall(abc, code, i, "sptIds");
            boolean hasSptClasses = hasGetLexBeforeCall(abc, code, i, "sptClasses");
            boolean hasLmiIds = hasGetLexBeforeCall(abc, code, i, "lmiIds");
            boolean hasLmiClasses = hasGetLexBeforeCall(abc, code, i, "lmiClasses");
            String kind = "";
            if (hasSptIds && hasSptClasses) {
                kind = "custom_spt";
            } else if (hasLmiIds && hasLmiClasses) {
                kind = "custom_lmi";
            }
            if (kind.isEmpty()) {
                continue;
            }
            boolean hasLoopProgress = hasLoopProgressAfterCall(code, i);
            String style = customLoaderStyle(abc, code, i, hasLoopProgress);
            StringBuilder item = new StringBuilder();
            item.append("{");
            item.append("\"kind\":\"").append(kind).append("\",");
            item.append("\"style\":\"").append(style).append("\",");
            item.append("\"method\":\"").append(jsonEscape(methodName)).append("\",");
            item.append("\"call_index\":").append(i).append(",");
            item.append("\"has_loop_progress\":").append(hasLoopProgress);
            item.append("}");
            out.add(item.toString());
        }
    }

    private static String customLoaderStyle(
            ABC abc,
            AVM2Code code,
            int callIndex,
            boolean hasLoopProgress
    ) {
        if (hasLoopProgress) {
            return "runtime_loop";
        }
        int start = Math.max(0, callIndex - 25);
        for (int i = start; i < callIndex; i++) {
            if ("getlocal".equals(code.code.get(i).definition.instructionName)) {
                return "legacy_single";
            }
        }
        return "expanded";
    }

    private static boolean hasGetLexBeforeCall(
            ABC abc,
            AVM2Code code,
            int callIndex,
            String qname
    ) {
        int start = Math.max(0, callIndex - 25);
        for (int i = start; i < callIndex; i++) {
            if (isGetLex(abc, code.code.get(i), qname)) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasLoopProgressAfterCall(AVM2Code code, int callIndex) {
        int stop = Math.min(code.code.size(), callIndex + 12);
        boolean hasGetLocal = false;
        boolean hasIncrement = false;
        boolean hasSetLocal = false;
        boolean hasJump = false;
        for (int i = callIndex + 1; i < stop; i++) {
            AVM2Instruction candidate = code.code.get(i);
            String name = candidate.definition.instructionName;
            hasGetLocal = hasGetLocal || "getlocal".equals(name);
            hasIncrement = hasIncrement || "increment_i".equals(name) || "increment".equals(name);
            hasSetLocal = hasSetLocal || "setlocal".equals(name);
            hasJump = hasJump || "jump".equals(name);
        }
        return hasGetLocal && hasIncrement && hasSetLocal && hasJump;
    }

    private static boolean isGetLex(ABC abc, AVM2Instruction ins, String qname) {
        return isInstructionWithPublicName(abc, ins, "getlex", qname);
    }

    private static boolean isCallPropVoid(ABC abc, AVM2Instruction ins, String qname) {
        return isInstructionWithPublicName(abc, ins, "callpropvoid", qname);
    }

    private static boolean isInstructionWithPublicName(
            ABC abc,
            AVM2Instruction ins,
            String instructionName,
            String qname
    ) {
        if (!instructionName.equals(ins.definition.instructionName)
                || ins.operands == null
                || ins.operands.length == 0) {
            return false;
        }
        String text = ins.toStringNoAddress(abc.constants, new ArrayList<DottedChain>());
        return text.contains("\"" + qname + "\"");
    }

    private static String registryFieldName(String text) {
        if (text.contains("\"sptIds\"")) {
            return "sptIds";
        }
        if (text.contains("\"sptClasses\"")) {
            return "sptClasses";
        }
        if (text.contains("\"lmiIds\"")) {
            return "lmiIds";
        }
        if (text.contains("\"lmiClasses\"")) {
            return "lmiClasses";
        }
        return null;
    }

    private static void addLoadOffsetHit(
            List<String> out,
            String methodName,
            int offset,
            int instructionIndex,
            String matchedText,
            List<String> instructionTexts,
            AVM2Code code
    ) {
        int start = Math.max(0, instructionIndex - 4);
        int end = Math.min(code.code.size(), instructionIndex + 14);
        StringBuilder window = new StringBuilder();
        for (int i = start; i < end; i++) {
            if (i > start) {
                window.append(" | ");
            }
            String text = i < instructionTexts.size()
                    ? instructionTexts.get(i)
                    : code.code.get(i).toString();
            window.append(i).append(":").append(text);
        }
        StringBuilder item = new StringBuilder();
        item.append("{");
        item.append("\"method\":\"").append(jsonEscape(methodName)).append("\",");
        item.append("\"offset\":").append(offset).append(",");
        item.append("\"instruction_index\":").append(instructionIndex).append(",");
        item.append("\"window\":\"").append(jsonEscape(window.toString())).append("\"");
        item.append("}");
        out.add(item.toString());
    }

    private static void addInstructionWindowHit(
            List<String> out,
            String methodName,
            String anchor,
            int instructionIndex,
            List<String> instructionTexts,
            AVM2Code code
    ) {
        int start = Math.max(0, instructionIndex - 6);
        int end = Math.min(code.code.size(), instructionIndex + 16);
        StringBuilder window = new StringBuilder();
        for (int i = start; i < end; i++) {
            if (i > start) {
                window.append(" | ");
            }
            String text = i < instructionTexts.size()
                    ? instructionTexts.get(i)
                    : code.code.get(i).toString();
            window.append(i).append(":").append(text);
        }
        StringBuilder item = new StringBuilder();
        item.append("{");
        item.append("\"method\":\"").append(jsonEscape(methodName)).append("\",");
        item.append("\"anchor\":\"").append(jsonEscape(anchor)).append("\",");
        item.append("\"instruction_index\":").append(instructionIndex).append(",");
        item.append("\"window\":\"").append(jsonEscape(window.toString())).append("\"");
        item.append("}");
        out.add(item.toString());
    }

    private static boolean isPushShort(AVM2Instruction ins, int value) {
        return "pushshort".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == value;
    }

    private static boolean isGetLex(AVM2Instruction ins, int qname) {
        return "getlex".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static boolean isPropertyAccess(AVM2Instruction ins, int qname) {
        String name = ins.definition.instructionName;
        return ("getlex".equals(name)
                || "getproperty".equals(name)
                || "findproperty".equals(name)
                || "findpropertystrict".equals(name)
                || "setproperty".equals(name)
                || "initproperty".equals(name))
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static boolean isPowAccess(AVM2Instruction ins, int qname) {
        String name = ins.definition.instructionName;
        return ("getlex".equals(name)
                || "getproperty".equals(name)
                || "findproperty".equals(name)
                || "findpropertystrict".equals(name))
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static boolean isPushString(ABC abc, AVM2Instruction ins, String value) {
        return "pushstring".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && value.equals(abc.constants.getString(ins.operands[0]));
    }

    private static int newObjectPairCount(AVM2Instruction ins) {
        if (!"newobject".equals(ins.definition.instructionName)
                || ins.operands == null
                || ins.operands.length == 0) {
            return -1;
        }
        return ins.operands[0];
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

    private static String pushedString(ABC abc, AVM2Instruction ins) {
        if (!"pushstring".equals(ins.definition.instructionName)
                || ins.operands == null
                || ins.operands.length == 0) {
            return null;
        }
        return abc.constants.getString(ins.operands[0]);
    }

    private static String jsonEscape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
'''
