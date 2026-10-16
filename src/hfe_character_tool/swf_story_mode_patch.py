from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from hfe_character_tool.swf_bytearray_class_patch import _ffdec_classpath
from hfe_character_tool.tools import (
    ToolConfig,
    ToolResult,
    run_tool,
    wrap_with_source_projector,
)

STORY15_PATCH_CLASS_NAME = "HfeStoryMode15Patch"

_STORY_TITLES: tuple[tuple[str, str, str, str], ...] = (
    ("story01", "story01", "1. First Match", "第一話 初戰"),
    ("story02", "story02b", "2. Trap", "第二話 陷阱"),
    ("story03", "story03a", "3. Defeated", "第三話 潰敗"),
    ("story04", "story04a", "4. Break in", "第四話 潛入"),
    ("story05", "prison2", "5. Prison break", "第五話 逃獄"),
    ("story06", "story06c", "6. Waken", "第六話 醒覺"),
    ("story07", "desert", "7. Promises", "第七話 約定"),
    ("story08", "hill_grass", "8. Training", "第八話 特訓"),
    ("story09", "forest_grass", "9. Betrayal", "第九話 背叛"),
    ("story10", "room1", "10. Death Match", "第十話 死戰"),
    ("story11", "hill_grass", "11. Custom Stage 11", "第十一話 自訂關卡"),
    ("story12", "hill_grass", "12. Custom Stage 12", "第十二話 自訂關卡"),
    ("story13", "hill_grass", "13. Custom Stage 13", "第十三話 自訂關卡"),
    ("story14", "hill_grass", "14. Custom Stage 14", "第十四話 自訂關卡"),
    ("story15", "hill_grass", "15. Custom Stage 15", "第十五話 自訂關卡"),
)


@dataclass(frozen=True)
class StoryModePatchResult:
    java_source_path: Path
    class_dir: Path
    resource_dir: Path
    output_swf_path: Path
    output_exe_path: Path | None
    compile_result: ToolResult
    run_result: ToolResult


def build_story15_storylist_xml() -> str:
    lines = ["<storylist>"]
    for story_id, bg_id, title_en, title_b5 in _STORY_TITLES:
        lines.append(
            "\t<story>"
            f"<storyid>{escape(story_id)}</storyid>"
            f"<bgid>{escape(bg_id)}</bgid>"
            "<storyname>"
            f"<en>{escape(title_en)}</en>"
            f"<b5>{escape(title_b5)}</b5>"
            "</storyname>"
            "</story>"
        )
    lines.append("</storylist>")
    return "\n".join(lines) + "\n"


def story_body_xml(story_id: str) -> str:
    return (
        "<story>\n"
        f"\t<storyid>{escape(story_id)}</storyid>\n"
        "\t<storylevel>1</storylevel>\n"
        "\t<bar>\n"
        "\t\t<bg>\n"
        "\t\t\t<bgid>hill_grass</bgid>\n"
        "\t\t\t<x>0</x>\n"
        "\t\t\t<z>800</z>\n"
        "\t\t\t<y>0</y>\n"
        "\t\t\t<facing>0</facing>\n"
        "\t\t</bg>\n"
        "\t\t<left>0</left>\n"
        "\t\t<right>4000</right>\n"
        "\t\t<effect>showTitle</effect>\n"
        "\t\t<drama><state>start</state></drama>\n"
        "\t</bar>\n"
        "\t<bar>\n"
        "\t\t<drama><state>end</state></drama>\n"
        "\t</bar>\n"
        "</story>\n"
    )


def patch_story_mode_to_15(
    input_swf: Path,
    output_swf: Path,
    tools: ToolConfig,
    cwd: Path,
    output_exe: Path | None = None,
    source_exe: Path | None = None,
) -> StoryModePatchResult:
    source_path = write_story15_patch_source(output_swf.parent)
    class_dir = output_swf.parent / "story15_patch_classes"
    class_dir.mkdir(exist_ok=True)
    resource_dir = output_swf.parent / "story15_resources"
    _write_story_resources(resource_dir)
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
        return StoryModePatchResult(
            source_path,
            class_dir,
            resource_dir,
            output_swf,
            None,
            compile_result,
            ToolResult(False, -1, "", "story15 patch compile failed"),
        )
    run_result = run_tool(
        (
            "java",
            "-cp",
            f"{class_dir};{classpath}",
            STORY15_PATCH_CLASS_NAME,
            str(input_swf),
            str(output_swf),
            str(resource_dir),
        ),
        cwd,
    )
    actual_exe = None
    if run_result.success and output_exe is not None:
        if source_exe is None:
            raise ValueError("source_exe is required when output_exe is provided.")
        wrap_with_source_projector(source_exe, output_swf, output_exe)
        actual_exe = output_exe
    return StoryModePatchResult(
        source_path,
        class_dir,
        resource_dir,
        output_swf,
        actual_exe,
        compile_result,
        run_result,
    )


def write_story15_patch_source(output_dir: Path) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{STORY15_PATCH_CLASS_NAME}.java"
    path.write_text(STORY15_PATCH_JAVA_SOURCE, encoding="utf-8")
    return path


def _write_story_resources(resource_dir: Path) -> None:
    resource_dir.mkdir(exist_ok=True)
    (resource_dir / "storylist.xml").write_text(
        build_story15_storylist_xml(), encoding="utf-8"
    )
    for index in range(8, 16):
        story_id = f"story{index:02d}"
        (resource_dir / f"{story_id}.xml").write_text(
            story_body_xml(story_id), encoding="utf-8"
        )


STORY15_PATCH_JAVA_SOURCE = r'''
import com.jpexs.decompiler.flash.SWF;
import com.jpexs.decompiler.flash.abc.ABC;
import com.jpexs.decompiler.flash.abc.avm2.AVM2Code;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instruction;
import com.jpexs.decompiler.flash.abc.avm2.instructions.AVM2Instructions;
import com.jpexs.decompiler.flash.abc.avm2.parser.script.AbcIndexing;
import com.jpexs.decompiler.flash.abc.avm2.parser.script.ActionScript3Parser;
import com.jpexs.decompiler.flash.abc.types.InstanceInfo;
import com.jpexs.decompiler.flash.abc.types.MethodBody;
import com.jpexs.decompiler.flash.abc.types.Namespace;
import com.jpexs.decompiler.flash.abc.types.traits.Trait;
import com.jpexs.decompiler.flash.abc.types.traits.TraitMethodGetterSetter;
import com.jpexs.decompiler.flash.abc.types.traits.TraitSlotConst;
import com.jpexs.decompiler.flash.tags.ABCContainerTag;
import com.jpexs.decompiler.flash.tags.DefineBinaryDataTag;
import com.jpexs.decompiler.flash.tags.SymbolClassTag;
import com.jpexs.decompiler.flash.tags.Tag;
import com.jpexs.decompiler.graph.DottedChain;
import com.jpexs.helpers.ByteArrayRange;

import java.io.BufferedInputStream;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;

public class HfeStoryMode15Patch {
    private static final int FIRST_PAGE_OFFSET = 0;
    private static final int SECOND_PAGE_OFFSET = 5;
    private static final int STORY_UNLOCK_MAX = 14;
    private static final String PAGE_OFFSET = "storyMenuPageOffset";
    private static final String[] BODY_STORIES = {
            "story08", "story09", "story10", "story11",
            "story12", "story13", "story14", "story15"
    };

    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException(
                    "Usage: HfeStoryMode15Patch <in.swf> <out.swf> <resourceDir>"
            );
        }
        SWF swf = new SWF(new BufferedInputStream(new FileInputStream(args[0])), false);
        Path resources = Paths.get(args[2]);
        PatchTarget global = findClass(swf, "Data.Global");
        if (global == null) {
            throw new IllegalStateException("Data.Global not found");
        }
        patchStoryResources(swf, global, resources);
        ensurePageOffsetSlot(global);
        patchGlobalStoryUnlock(global);
        patchLoadStories(global);
        patchStoryMenu(swf);
        swf.setModified(true);
        try (OutputStream os = new FileOutputStream(args[1])) {
            swf.saveTo(os);
        }
        System.out.println(
                "{\"success\":true,\"patched\":\"story_mode_15\","
                        + "\"story_count\":15,\"body_resources\":8,"
                        + "\"page_offsets\":\"0,5\"}"
        );
    }

    private static void patchStoryResources(
            SWF swf,
            PatchTarget global,
            Path resourceDir
    ) throws Exception {
        SymbolClassTag symbol = symbolClassTagForClass(swf, "Data.Global_storylist_xml");
        if (symbol == null) {
            throw new IllegalStateException("Data.Global_storylist_xml SymbolClass not found");
        }
        upsertBinaryResource(
                swf,
                symbol,
                "Data.Global_storylist_xml",
                Files.readAllBytes(resourceDir.resolve("storylist.xml")),
                true
        );
        for (String storyId : BODY_STORIES) {
            String className = "Data.Global_" + storyId + "_xml";
            ensureByteArrayClass(swf, global, className);
            upsertBinaryResource(
                swf,
                symbol,
                className,
                storyBodyFromStory07(swf, symbol, storyId),
                false
            );
        }
        global.abc.fireChanged();
        global.tag.setABC(global.abc);
        ((Tag) global.tag).setModified(true);
    }

    private static void upsertBinaryResource(
            SWF swf,
            SymbolClassTag symbol,
            String className,
            byte[] bytes,
            boolean replaceExisting
    ) {
        DefineBinaryDataTag existing = binaryDataForClassOrNull(swf, symbol, className);
        if (existing != null) {
            if (replaceExisting) {
                existing.setDataBytes(new ByteArrayRange(bytes));
                existing.setModified(true);
            }
            return;
        }
        DefineBinaryDataTag tag = new DefineBinaryDataTag(swf);
        tag.setCharacterId(swf.getNextCharacterId());
        tag.reserved = 0;
        tag.setDataBytes(new ByteArrayRange(bytes));
        tag.addClassName(className);
        tag.setModified(true);
        int insertAt = swf.indexOfTag(symbol);
        swf.addTag(insertAt, tag);
        symbol.tags.add(tag.getCharacterId());
        symbol.names.add(className);
        symbol.setModified(true);
    }

    private static byte[] storyBodyFromStory07(
            SWF swf,
            SymbolClassTag symbol,
            String storyId
    ) {
        DefineBinaryDataTag source = binaryDataForClassOrNull(
                swf, symbol, "Data.Global_story07_xml"
        );
        if (source == null) {
            throw new IllegalStateException("Data.Global_story07_xml source not found");
        }
        String sourceXml = new String(source.getDataBytes().getRangeData(), StandardCharsets.UTF_8);
        String sourceId = "<storyid>story07</storyid>";
        if (!sourceXml.contains(sourceId)) {
            throw new IllegalStateException("story07 body does not contain its story id");
        }
        return sourceXml.replace(
                sourceId, "<storyid>" + storyId + "</storyid>"
        ).getBytes(StandardCharsets.UTF_8);
    }

    private static void ensureByteArrayClass(
            SWF swf,
            PatchTarget target,
            String qualifiedName
    ) throws Exception {
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
                qualifiedName.replace('.', '/') + ".as",
                target.abc
        );
    }

    private static String byteArrayAssetSource(String qualifiedName) {
        int dot = qualifiedName.lastIndexOf('.');
        if (dot <= 0 || dot == qualifiedName.length() - 1) {
            throw new IllegalArgumentException("Class must be package-qualified");
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

    private static void ensurePageOffsetSlot(PatchTarget target) {
        int qPageOffset = target.abc.constants.getPublicQnameId(PAGE_OFFSET, true);
        if (!hasStaticTrait(target, qPageOffset)) {
            TraitSlotConst slot = new TraitSlotConst();
            slot.name_index = qPageOffset;
            slot.kindType = Trait.TRAIT_SLOT;
            slot.kindFlags = 0;
            slot.slot_id = nextStaticSlotId(target);
            slot.type_index = target.abc.constants.getQnameId(
                    "int", Namespace.KIND_PACKAGE, "", true
            );
            slot.value_index = 0;
            slot.value_kind = 0;
            target.abc.class_info.get(target.classIndex).static_traits.addTrait(slot);
        }
        MethodBody body = target.abc.findBody(
                target.abc.class_info.get(target.classIndex).cinit_index
        );
        AVM2Code code = body.getCode();
        if (methodSetsProperty(code, qPageOffset)) {
            finish(target, body, code);
            return;
        }
        int insertAt = findLastReturnVoid(code);
        code.insertInstruction(insertAt, ins(AVM2Instructions.FindProperty, qPageOffset), body);
        code.insertInstruction(insertAt + 1, pushInt(FIRST_PAGE_OFFSET), body);
        code.insertInstruction(
                insertAt + 2, ins(AVM2Instructions.SetProperty, qPageOffset), body
        );
        finish(target, body, code);
    }

    private static void patchGlobalStoryUnlock(PatchTarget target) {
        MethodBody body = target.abc.findBody(
                target.abc.class_info.get(target.classIndex).cinit_index
        );
        AVM2Code code = body.getCode();
        Const c = new Const(target.abc);
        if (methodSetsStoryUnlock(code, c)) {
            finish(target, body, code);
            return;
        }
        int insertAt = findLastReturnVoid(code);
        code.insertInstruction(
                insertAt, ins(AVM2Instructions.FindProperty, c.qStoryChosenMax), body
        );
        code.insertInstruction(insertAt + 1, pushInt(STORY_UNLOCK_MAX), body);
        code.insertInstruction(
                insertAt + 2, ins(AVM2Instructions.SetProperty, c.qStoryChosenMax), body
        );
        finish(target, body, code);
    }

    private static void patchLoadStories(PatchTarget target) throws Exception {
        MethodBody body = findMethodBody(target, "LoadStories", true);
        AVM2Code code = body.getCode();
        Const c = new Const(target.abc);
        if (methodPushesString(target.abc, body, "story15")) {
            finish(target, body, code);
            return;
        }
        int insertAt = findLoadStoryInsertionPoint(target.abc, code, c);
        List<AVM2Instruction> injected = new ArrayList<>();
        for (String storyId : BODY_STORIES) {
            injected.add(ins(AVM2Instructions.FindPropertyStrict, c.qLoadStory));
            injected.add(
                    ins(AVM2Instructions.GetLex, dataClassQName(target.abc, storyClass(storyId)))
            );
            injected.add(ins(AVM2Instructions.PushString, target.abc.constants.getStringId(
                    storyId, true
            )));
            injected.add(ins(AVM2Instructions.CallPropVoid, c.qLoadStory, 2));
        }
        insertAll(code, body, insertAt, injected);
        finish(target, body, code);
    }

    private static int findLoadStoryInsertionPoint(ABC abc, AVM2Code code, Const c) {
        for (int i = 0; i < code.code.size(); i++) {
            if (!isCallPropVoid(code.code.get(i), c.qLoadStory)) {
                continue;
            }
            if (i >= 1 && isPushString(abc, code.code.get(i - 1), "story07")) {
                return i + 1;
            }
        }
        throw new IllegalStateException("Could not find story07 LoadStory call");
    }

    private static void patchStoryMenu(SWF swf) throws Exception {
        PatchTarget menu = findClass(swf, "Page.StoryMenu");
        if (menu == null) {
            throw new IllegalStateException("Page.StoryMenu not found");
        }
        patchStoryUnlock(menu);
        patchRefreshLang(menu);
        patchRefreshButtons(menu);
        patchOnClickBut(menu);
        patchPageLabels(menu);
        patchPageNextButton(menu);
        patchPagePreviousButton(menu);
    }

    private static void patchStoryUnlock(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "RefreshButtons", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        int insertAt = findFirstPushScope(code) + 1;
        if (startsWithStoryUnlock(code, insertAt, c)) {
            finish(menu, body, code);
            return;
        }
        code.insertInstruction(insertAt, ins(AVM2Instructions.GetLex, c.qGlobalClass), body);
        code.insertInstruction(insertAt + 1, pushInt(STORY_UNLOCK_MAX), body);
        code.insertInstruction(
                insertAt + 2, ins(AVM2Instructions.SetProperty, c.qStoryChosenMax), body
        );
        finish(menu, body, code);
    }

    private static void patchPageLabels(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "RefreshLang", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        if (methodPushesString(menu.abc, body, "NEXT PAGE >")) {
            finish(menu, body, code);
            return;
        }
        int insertAt = findLastReturnVoid(code);
        AVM2Instruction originalReturn = code.code.get(insertAt);
        AVM2Instruction secondPageStart = ins(AVM2Instructions.GetLocal0);
        AVM2Instruction ifSecondPage = ins(AVM2Instructions.IfNe, 0);
        AVM2Instruction jumpEnd = ins(AVM2Instructions.Jump, 0);
        List<AVM2Instruction> injected = new ArrayList<>();
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qPageOffset));
        injected.add(pushInt(FIRST_PAGE_OFFSET));
        injected.add(ifSecondPage);
        injected.add(ins(AVM2Instructions.GetLocal0));
        injected.add(ins(AVM2Instructions.GetProperty, c.qText9));
        injected.add(ins(AVM2Instructions.PushString,
                menu.abc.constants.getStringId("NEXT PAGE >", true)));
        injected.add(ins(AVM2Instructions.SetProperty, c.qText));
        injected.add(jumpEnd);
        injected.add(secondPageStart);
        injected.add(ins(AVM2Instructions.GetProperty, c.qText0));
        injected.add(ins(AVM2Instructions.PushString,
                menu.abc.constants.getStringId("< PREV PAGE", true)));
        injected.add(ins(AVM2Instructions.SetProperty, c.qText));
        PendingBranch.add(ifSecondPage, secondPageStart);
        PendingBranch.add(jumpEnd, originalReturn);
        insertAll(code, body, insertAt, injected);
        finish(menu, body, code);
    }

    private static void patchPageNextButton(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "__glass9_click", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        if (methodContainsPublicName(menu.abc, body, PAGE_OFFSET)) {
            finish(menu, body, code);
            return;
        }
        int insertAt = findFirstPushScope(code) + 1;
        AVM2Instruction originalContinue = code.code.get(insertAt);
        AVM2Instruction ifConnecting = ins(AVM2Instructions.IfTrue, 0);
        AVM2Instruction ifSecondPage = ins(AVM2Instructions.IfNe, 0);
        List<AVM2Instruction> injected = new ArrayList<>();
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qConnecting));
        injected.add(ifConnecting);
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qPageOffset));
        injected.add(pushInt(FIRST_PAGE_OFFSET));
        injected.add(ifSecondPage);
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(pushInt(SECOND_PAGE_OFFSET));
        injected.add(ins(AVM2Instructions.SetProperty, c.qPageOffset));
        injected.add(ins(AVM2Instructions.GetLocal0));
        injected.add(ins(AVM2Instructions.CallPropVoid, c.qRefreshLang, 0));
        injected.add(ins(AVM2Instructions.ReturnVoid));
        PendingBranch.add(ifConnecting, originalContinue);
        PendingBranch.add(ifSecondPage, originalContinue);
        insertAll(code, body, insertAt, injected);
        finish(menu, body, code);
    }

    private static void patchPagePreviousButton(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "__glass0_click", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        if (methodContainsPublicName(menu.abc, body, PAGE_OFFSET)) {
            finish(menu, body, code);
            return;
        }
        int insertAt = findFirstPushScope(code) + 1;
        AVM2Instruction originalContinue = code.code.get(insertAt);
        AVM2Instruction resetPage = ins(AVM2Instructions.GetLex, c.qGlobalClass);
        AVM2Instruction ifConnecting = ins(AVM2Instructions.IfTrue, 0);
        AVM2Instruction ifSecondPage = ins(AVM2Instructions.IfNe, 0);
        AVM2Instruction jumpOriginal = ins(AVM2Instructions.Jump, 0);
        List<AVM2Instruction> injected = new ArrayList<>();
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qConnecting));
        injected.add(ifConnecting);
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qPageOffset));
        injected.add(pushInt(FIRST_PAGE_OFFSET));
        injected.add(ifSecondPage);
        injected.add(jumpOriginal);
        injected.add(resetPage);
        injected.add(pushInt(FIRST_PAGE_OFFSET));
        injected.add(ins(AVM2Instructions.SetProperty, c.qPageOffset));
        injected.add(ins(AVM2Instructions.GetLocal0));
        injected.add(ins(AVM2Instructions.CallPropVoid, c.qRefreshLang, 0));
        injected.add(ins(AVM2Instructions.ReturnVoid));
        PendingBranch.add(ifConnecting, originalContinue);
        PendingBranch.add(ifSecondPage, resetPage);
        PendingBranch.add(jumpOriginal, originalContinue);
        insertAll(code, body, insertAt, injected);
        finish(menu, body, code);
    }

    private static void patchPageControls(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(
                menu, "___StoryMenu_PageWeb1_creationComplete", false
        );
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        if (methodContainsPublicName(menu.abc, body, "[1/2]")) {
            finish(menu, body, code);
            return;
        }
        int insertAt = findLastReturnVoid(code);
        insertAll(code, body, insertAt, pageControlInstructions(menu.abc, c, FIRST_PAGE_OFFSET));
        finish(menu, body, code);
    }

    private static void patchOnClickNext(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "OnClickNext", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        if (methodContainsPublicName(menu.abc, body, PAGE_OFFSET)) {
            finish(menu, body, code);
            return;
        }
        int insertAt = findFirstPushScope(code) + 1;
        AVM2Instruction originalContinue = code.code.get(insertAt);
        AVM2Instruction ifConnecting = ins(AVM2Instructions.IfTrue, 0);
        AVM2Instruction ifSecondPage = ins(AVM2Instructions.IfNe, 0);
        List<AVM2Instruction> injected = new ArrayList<>();
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qConnecting));
        injected.add(ifConnecting);
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qPageOffset));
        injected.add(pushInt(FIRST_PAGE_OFFSET));
        injected.add(ifSecondPage);
        injected.addAll(pageControlInstructions(menu.abc, c, SECOND_PAGE_OFFSET));
        injected.add(ins(AVM2Instructions.ReturnVoid));
        PendingBranch.add(ifConnecting, originalContinue);
        PendingBranch.add(ifSecondPage, originalContinue);
        insertAll(code, body, insertAt, injected);
        finish(menu, body, code);
    }

    private static void patchOnClickBack(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "OnClickBack", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        if (methodContainsPublicName(menu.abc, body, PAGE_OFFSET)) {
            finish(menu, body, code);
            return;
        }
        int insertAt = findFirstPushScope(code) + 1;
        AVM2Instruction originalContinue = code.code.get(insertAt);
        AVM2Instruction resetPage = ins(AVM2Instructions.GetLex, c.qGlobalClass);
        AVM2Instruction ifConnecting = ins(AVM2Instructions.IfTrue, 0);
        AVM2Instruction ifSecondPage = ins(AVM2Instructions.IfNe, 0);
        AVM2Instruction jumpOriginal = ins(AVM2Instructions.Jump, 0);
        List<AVM2Instruction> pageOne = pageControlInstructions(
                menu.abc, c, FIRST_PAGE_OFFSET
        );
        List<AVM2Instruction> injected = new ArrayList<>();
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qConnecting));
        injected.add(ifConnecting);
        injected.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        injected.add(ins(AVM2Instructions.GetProperty, c.qPageOffset));
        injected.add(pushInt(FIRST_PAGE_OFFSET));
        injected.add(ifSecondPage);
        injected.add(jumpOriginal);
        injected.add(resetPage);
        injected.addAll(pageOne.subList(1, pageOne.size()));
        injected.add(ins(AVM2Instructions.ReturnVoid));
        PendingBranch.add(ifConnecting, originalContinue);
        PendingBranch.add(ifSecondPage, resetPage);
        PendingBranch.add(jumpOriginal, originalContinue);
        insertAll(code, body, insertAt, injected);
        finish(menu, body, code);
    }

    private static List<AVM2Instruction> pageControlInstructions(
            ABC abc,
            Const c,
            int offset
    ) {
        String next = offset == FIRST_PAGE_OFFSET ? "下一页" : "下一步";
        String back = offset == FIRST_PAGE_OFFSET ? "返回" : "上一页";
        String title = offset == FIRST_PAGE_OFFSET ? "请选择故事 [1/2]" : "请选择故事 [2/2]";
        List<AVM2Instruction> result = new ArrayList<>();
        result.add(ins(AVM2Instructions.GetLex, c.qGlobalClass));
        result.add(pushInt(offset));
        result.add(ins(AVM2Instructions.SetProperty, c.qPageOffset));
        result.add(ins(AVM2Instructions.GetLocal0));
        result.add(ins(AVM2Instructions.CallPropVoid, c.qRefreshLang, 0));
        result.add(ins(AVM2Instructions.GetLocal0));
        result.add(ins(AVM2Instructions.GetProperty, c.qNextText));
        result.add(ins(AVM2Instructions.PushString, abc.constants.getStringId(next, true)));
        result.add(ins(AVM2Instructions.SetProperty, c.qText));
        result.add(ins(AVM2Instructions.GetLocal0));
        result.add(ins(AVM2Instructions.GetProperty, c.qBackText));
        result.add(ins(AVM2Instructions.PushString, abc.constants.getStringId(back, true)));
        result.add(ins(AVM2Instructions.SetProperty, c.qText));
        result.add(ins(AVM2Instructions.GetLocal0));
        result.add(ins(AVM2Instructions.GetProperty, c.qSelectEpisodeText));
        result.add(ins(AVM2Instructions.PushString, abc.constants.getStringId(title, true)));
        result.add(ins(AVM2Instructions.SetProperty, c.qText));
        return result;
    }

    private static void patchRefreshLang(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "RefreshLang", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        int patched = 0;
        for (int i = 0; i + 3 < code.code.size(); i++) {
            if (!isGetProperty(code.code.get(i), c.qStory)
                    || !"getlocal1".equals(code.code.get(i + 1).definition.instructionName)
                    || isPageOffsetReadAt(code, i + 2, c)) {
                continue;
            }
            code.insertInstruction(i + 2, ins(AVM2Instructions.GetLex, c.qGlobalClass), body);
            code.insertInstruction(i + 3, ins(AVM2Instructions.GetProperty, c.qPageOffset), body);
            code.insertInstruction(i + 4, ins(AVM2Instructions.Add), body);
            patched++;
            i += 4;
        }
        if (patched == 0 && !methodContainsPublicName(menu.abc, body, PAGE_OFFSET)) {
            throw new IllegalStateException("RefreshLang story indexes were not patched");
        }
        finish(menu, body, code);
    }

    private static void patchRefreshButtons(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "RefreshButtons", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        patchStoryIndexFromInstanceLoop(menu.abc, body, code, c);
        patchSelectedStoryComparison(menu.abc, body, code, c);
        finish(menu, body, code);
    }

    private static void patchStoryIndexFromInstanceLoop(
            ABC abc,
            MethodBody body,
            AVM2Code code,
            Const c
    ) {
        int patched = 0;
        for (int i = 0; i + 4 < code.code.size(); i++) {
            if (!isGetProperty(code.code.get(i), c.qStory)) {
                continue;
            }
            if (!"getlocal0".equals(code.code.get(i + 1).definition.instructionName)
                    || !isPrivateName(abc, code.code.get(i + 2), "i")
                    || isPageOffsetReadAt(code, i + 3, c)) {
                continue;
            }
            code.insertInstruction(i + 3, ins(AVM2Instructions.GetLex, c.qGlobalClass), body);
            code.insertInstruction(i + 4, ins(AVM2Instructions.GetProperty, c.qPageOffset), body);
            code.insertInstruction(i + 5, ins(AVM2Instructions.Add), body);
            patched++;
            i += 5;
        }
        if (patched == 0 && !methodContainsPublicName(abc, body, PAGE_OFFSET)) {
            throw new IllegalStateException("RefreshButtons story index was not patched");
        }
    }

    private static void patchSelectedStoryComparison(
            ABC abc,
            MethodBody body,
            AVM2Code code,
            Const c
    ) {
        for (int i = 0; i + 8 < code.code.size(); i++) {
            if (!isGetProperty(code.code.get(i), c.qStoryChosenI)) {
                continue;
            }
            int localIndex = findPrivateIBeforeBranch(abc, code, i + 1, i + 10, c);
            if (localIndex < 0 || isPageOffsetReadAt(code, localIndex + 1, c)) {
                continue;
            }
            code.insertInstruction(
                    localIndex + 1, ins(AVM2Instructions.GetLex, c.qGlobalClass), body
            );
            code.insertInstruction(
                    localIndex + 2, ins(AVM2Instructions.GetProperty, c.qPageOffset), body
            );
            code.insertInstruction(localIndex + 3, ins(AVM2Instructions.Add), body);
            return;
        }
    }

    private static int findPrivateIBeforeBranch(
            ABC abc,
            AVM2Code code,
            int start,
            int stop,
            Const c
    ) {
        int limit = Math.min(stop, code.code.size() - 1);
        for (int i = start; i < limit; i++) {
            if (!"getlocal0".equals(code.code.get(i).definition.instructionName)
                    || !isPrivateName(abc, code.code.get(i + 1), "i")) {
                continue;
            }
            for (int j = i + 2; j < limit; j++) {
                if ("ifne".equals(code.code.get(j).definition.instructionName)) {
                    return i + 1;
                }
            }
        }
        return -1;
    }

    private static void patchOnClickBut(PatchTarget menu) throws Exception {
        MethodBody body = findMethodBody(menu, "OnClickBut", false);
        AVM2Code code = body.getCode();
        Const c = new Const(menu.abc);
        if (methodContainsPublicName(menu.abc, body, PAGE_OFFSET)) {
            finish(menu, body, code);
            return;
        }
        int insertAt = findFirstPushScope(code) + 1;
        code.insertInstruction(insertAt, ins(AVM2Instructions.GetLocal1), body);
        code.insertInstruction(insertAt + 1, ins(AVM2Instructions.GetLex, c.qGlobalClass), body);
        code.insertInstruction(
                insertAt + 2, ins(AVM2Instructions.GetProperty, c.qPageOffset), body
        );
        code.insertInstruction(insertAt + 3, ins(AVM2Instructions.Add), body);
        code.insertInstruction(insertAt + 4, ins(AVM2Instructions.SetLocal1), body);
        finish(menu, body, code);
    }

    private static void insertAll(
            AVM2Code code,
            MethodBody body,
            int insertAt,
            List<AVM2Instruction> instructions
    ) {
        for (int i = 0; i < instructions.size(); i++) {
            code.insertInstruction(insertAt + i, instructions.get(i), body);
        }
    }

    private static void finish(PatchTarget target, MethodBody body, AVM2Code code) {
        int originalInitScopeDepth = body.init_scope_depth;
        int originalMaxScopeDepth = body.max_scope_depth;
        code.markOffsets();
        for (PendingBranch branch : PendingBranch.pending) {
            branch.branch.setTargetOffset(
                    (int) (branch.target.getAddress() - (branch.branch.getAddress() + 4))
            );
        }
        PendingBranch.pending.clear();
        body.setCode(code);
        body.autoFillStats(target.abc, target.abc.findBodyIndex(body.method_info), false);
        body.init_scope_depth = originalInitScopeDepth;
        body.max_scope_depth = originalMaxScopeDepth;
        target.abc.fireChanged();
        target.tag.setABC(target.abc);
        ((Tag) target.tag).setModified(true);
    }

    private static PatchTarget findClass(SWF swf, String className) {
        for (ABCContainerTag tag : swf.getAbcList()) {
            ABC abc = tag.getABC();
            for (int ci = 0; ci < abc.instance_info.size(); ci++) {
                InstanceInfo ii = abc.instance_info.get(ci);
                String actualClass = abc.constants.getMultiname(ii.name_index)
                        .getNameWithNamespace(abc.constants, true).toString();
                if (className.equals(actualClass)) {
                    return new PatchTarget(tag, abc, ci);
                }
            }
        }
        return null;
    }

    private static MethodBody findMethodBody(
            PatchTarget target,
            String methodName,
            boolean isStatic
    ) throws Exception {
        List<Trait> traits = isStatic
                ? target.abc.class_info.get(target.classIndex).static_traits.traits
                : target.abc.instance_info.get(target.classIndex).instance_traits.traits;
        for (Trait trait : traits) {
            if (!(trait instanceof TraitMethodGetterSetter)) {
                continue;
            }
            String actualMethod = target.abc.constants.getMultiname(trait.name_index)
                    .getName(
                            target.abc.constants,
                            new ArrayList<DottedChain>(),
                            true,
                            true
                    ).toString();
            if (methodName.equals(actualMethod)) {
                return target.abc.findBody(
                        ((TraitMethodGetterSetter) trait).method_info
                );
            }
        }
        throw new IllegalStateException("Method not found: " + methodName);
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

    private static DefineBinaryDataTag binaryDataForClassOrNull(
            SWF swf,
            SymbolClassTag symbol,
            String className
    ) {
        int id = symbolIdForClassOrMinusOne(symbol, className);
        if (id < 0) {
            return null;
        }
        for (Tag tag : swf.getTags()) {
            if (tag instanceof DefineBinaryDataTag) {
                DefineBinaryDataTag binary = (DefineBinaryDataTag) tag;
                if (binary.getCharacterId() == id) {
                    return binary;
                }
            }
        }
        return null;
    }

    private static int symbolIdForClassOrMinusOne(SymbolClassTag symbol, String className) {
        for (int i = 0; i < symbol.names.size(); i++) {
            if (className.equals(symbol.names.get(i))) {
                return symbol.tags.get(i);
            }
        }
        return -1;
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

    private static boolean hasStaticTrait(PatchTarget target, int qname) {
        for (Trait trait : target.abc.class_info.get(target.classIndex).static_traits.traits) {
            if (trait.name_index == qname) {
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

    private static int findFirstPushScope(AVM2Code code) {
        for (int i = 0; i < code.code.size(); i++) {
            if ("pushscope".equals(code.code.get(i).definition.instructionName)) {
                return i;
            }
        }
        throw new IllegalStateException("pushscope not found");
    }

    private static int findLastReturnVoid(AVM2Code code) {
        for (int i = code.code.size() - 1; i >= 0; i--) {
            if ("returnvoid".equals(code.code.get(i).definition.instructionName)) {
                return i;
            }
        }
        return -1;
    }

    private static boolean methodSetsProperty(AVM2Code code, int qname) {
        for (AVM2Instruction ins : code.code) {
            if (isSetProperty(ins, qname)) {
                return true;
            }
        }
        return false;
    }

    private static boolean methodPushesString(ABC abc, MethodBody body, String value) {
        AVM2Code code = body.getCode();
        for (AVM2Instruction ins : code.code) {
            if (isPushString(abc, ins, value)) {
                return true;
            }
        }
        return false;
    }

    private static boolean methodContainsPublicName(ABC abc, MethodBody body, String name) {
        AVM2Code code = body.getCode();
        for (AVM2Instruction ins : code.code) {
            String text = ins.toStringNoAddress(abc.constants, new ArrayList<DottedChain>());
            if (text.contains("\"" + name + "\"")) {
                return true;
            }
        }
        return false;
    }

    private static boolean startsWithStoryUnlock(AVM2Code code, int index, Const c) {
        return index + 2 < code.code.size()
                && isGetLex(code.code.get(index), c.qGlobalClass)
                && isPushInt(code.code.get(index + 1), STORY_UNLOCK_MAX)
                && isSetProperty(code.code.get(index + 2), c.qStoryChosenMax);
    }

    private static boolean methodSetsStoryUnlock(AVM2Code code, Const c) {
        for (int i = 0; i + 1 < code.code.size(); i++) {
            if (!isPushInt(code.code.get(i), STORY_UNLOCK_MAX)) {
                continue;
            }
            for (int j = i + 1; j < Math.min(i + 4, code.code.size()); j++) {
                if (isSetProperty(code.code.get(j), c.qStoryChosenMax)) {
                    return true;
                }
            }
        }
        return false;
    }

    private static boolean methodReferencesQName(AVM2Code code, int qname) {
        for (AVM2Instruction ins : code.code) {
            if (ins.operands != null && ins.operands.length > 0 && ins.operands[0] == qname) {
                return true;
            }
        }
        return false;
    }

    private static boolean isPageOffsetReadAt(AVM2Code code, int index, Const c) {
        if (index + 2 >= code.code.size()) {
            return false;
        }
        return isGetLex(code.code.get(index), c.qGlobalClass)
                && isGetProperty(code.code.get(index + 1), c.qPageOffset)
                && "add".equals(code.code.get(index + 2).definition.instructionName);
    }

    private static boolean isPrivateName(ABC abc, AVM2Instruction ins, String name) {
        if (!"getproperty".equals(ins.definition.instructionName)
                || ins.operands == null
                || ins.operands.length == 0) {
            return false;
        }
        String text = ins.toStringNoAddress(abc.constants, new ArrayList<DottedChain>());
        return text.contains("\"" + name + "\"") && text.contains("PrivateNamespace");
    }

    private static boolean isPushString(ABC abc, AVM2Instruction ins, String value) {
        return "pushstring".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && value.equals(abc.constants.getString(ins.operands[0]));
    }

    private static boolean isPushInt(AVM2Instruction ins, int value) {
        String name = ins.definition.instructionName;
        return ("pushbyte".equals(name) || "pushshort".equals(name) || "pushint".equals(name))
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

    private static boolean isGetProperty(AVM2Instruction ins, int qname) {
        return "getproperty".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static boolean isSetProperty(AVM2Instruction ins, int qname) {
        return "setproperty".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static boolean isCallPropVoid(AVM2Instruction ins, int qname) {
        return "callpropvoid".equals(ins.definition.instructionName)
                && ins.operands != null
                && ins.operands.length > 0
                && ins.operands[0] == qname;
    }

    private static int dataClassQName(ABC abc, String className) {
        return abc.constants.getQnameId(className, Namespace.KIND_PACKAGE, "Data", true);
    }

    private static String storyClass(String storyId) {
        return "Global_" + storyId + "_xml";
    }

    private static AVM2Instruction pushInt(int value) {
        if (value >= -128 && value <= 127) {
            return ins(AVM2Instructions.PushByte, value);
        }
        return ins(AVM2Instructions.PushShort, value);
    }

    private static AVM2Instruction ins(int opcode, int... operands) {
        return new AVM2Instruction(0, opcode, operands);
    }

    private static class Const {
        final int qGlobalClass;
        final int qPageOffset;
        final int qConnecting;
        final int qStory;
        final int qStoryChosenI;
        final int qStoryChosenMax;
        final int qLoadStory;
        final int qRefreshLang;
        final int qNextText;
        final int qBackText;
        final int qSelectEpisodeText;
        final int qText0;
        final int qText9;
        final int qText;

        Const(ABC abc) {
            qGlobalClass = abc.constants.getQnameId(
                    "Global", Namespace.KIND_PACKAGE, "Data", true
            );
            qPageOffset = abc.constants.getPublicQnameId(PAGE_OFFSET, true);
            qConnecting = abc.constants.getPublicQnameId("connecting", true);
            qStory = abc.constants.getPublicQnameId("story", true);
            qStoryChosenI = abc.constants.getPublicQnameId("storyChosen_i", true);
            qStoryChosenMax = abc.constants.getPublicQnameId("storyChosen_max", true);
            qLoadStory = abc.constants.getPublicQnameId("LoadStory", true);
            qRefreshLang = abc.constants.getPublicQnameId("RefreshLang", true);
            qNextText = abc.constants.getPublicQnameId("next_text", true);
            qBackText = abc.constants.getPublicQnameId("back_text", true);
            qSelectEpisodeText = abc.constants.getPublicQnameId("select_episode_text", true);
            qText0 = abc.constants.getPublicQnameId("text0", true);
            qText9 = abc.constants.getPublicQnameId("text9", true);
            qText = abc.constants.getPublicQnameId("text", true);
        }
    }

    private static class PendingBranch {
        static final List<PendingBranch> pending = new ArrayList<>();
        final AVM2Instruction branch;
        final AVM2Instruction target;

        PendingBranch(AVM2Instruction branch, AVM2Instruction target) {
            this.branch = branch;
            this.target = target;
        }

        static void add(AVM2Instruction branch, AVM2Instruction target) {
            pending.add(new PendingBranch(branch, target));
        }
    }

    private static class PatchTarget {
        final ABCContainerTag tag;
        final ABC abc;
        final int classIndex;

        PatchTarget(ABCContainerTag tag, ABC abc, int classIndex) {
            this.tag = tag;
            this.abc = abc;
            this.classIndex = classIndex;
        }
    }
}
'''
