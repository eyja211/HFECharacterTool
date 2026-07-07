from __future__ import annotations

from pathlib import Path

from hfe_character_tool.swf_binary_symbol_patch import (
    BINARY_SYMBOL_PATCH_JAVA_SOURCE,
    write_binary_symbol_patch_source,
)


def test_binary_symbol_patch_source_clones_define_binary_data_and_symbols(
    tmp_path: Path,
) -> None:
    path = write_binary_symbol_patch_source(tmp_path)
    source = path.read_text(encoding="utf-8")

    assert source == BINARY_SYMBOL_PATCH_JAVA_SOURCE
    assert "DefineBinaryDataTag" in source
    assert "SymbolClassTag" in source
    assert "symbolClassTagForClass" in source
    assert "getNextCharacterId" in source
    assert "ByteArrayRange" in source
    assert "patchCharacterPayload" in source
    assert "validateResourceId" in source
    assert "readSptContainer" in source
    assert "writeSptContainer" in source
    assert "applyFrameItemEdits" in source
    assert "replaceCreInFrame" in source
    assert "patchExistingCreSlots" in source
    assert "Binary-safe item editing must keep the existing cre slot count" in source
    assert "patchTopLevelDoubleField" in source
    assert "isProjectilePtTemplate" in source
    assert "isEditablePtTemplate" in source
    assert "findFallbackPtObjectTemplate" in source
    assert "fallback_pt_template_uses" in source
    assert "writeNamedDoubleIfPresent" in source
    assert "Could not find editable Data.Pt template" in source
    assert "brk0" in source
    assert "applyLimbNameReplacements" in source
    assert "applyTextureReplacements" in source
    assert "DONOR_LIMB" in source
    assert "same byte length as the selected source role" in source
    assert "readAmf3ByteArray" in source
    assert "replaceFirstSptId" in source
    assert "replacePrefixUtfStrings" in source
    assert "replaceLimbUtfStrings" in source
    assert "selectLimbNamesForReplacement" in source
    assert "aliasExternalLmiLimbNames" in source
    assert "source_lmi_limb_names" in source
    assert "renamed_limb_names" in source
    assert "external_lmi_limb_names" in source
    assert "external_lmi_limb_aliases" in source
    assert "Embedded AMF string replacement must keep byte length" in source
    assert "writeAmf3ByteArray" in source
    assert "detectLimbNames" in source
    assert "sourceSptLimbNames" in source
    assert "sourceLmiLimbNames" in source
    assert '"lmi_data/" + sourceCharacterId + ".lmi"' in source
    assert '"lmi_data/" + resourceId + ".lmi"' in source
    assert "source_character_id" in source
    assert "spt_limb_name_replacements" in source
    assert "spt_donor_limb_name_replacements" in source
    assert "spt_lmi_path_replacements" in source
    assert "frame_item_edits" in source
    assert "generated_cre_slots" in source
    assert "lmi_limb_name_replacements" in source
    assert "InflaterInputStream" in source
    assert "DeflaterOutputStream" in source
    assert "setDataBytes" in source
    assert "symbolTag.tags.add" in source
    assert "symbolTag.names.add" in source
    assert "swf.addTag(symbolIndex, clonedSpt)" in source
    assert "swf.addTag(symbolIndex + 1, clonedLmi)" in source
    assert "binary_symbol_clone" in source
    assert "swf.saveTo" in source


def test_binary_symbol_patch_uses_lmi_limb_definitions_as_source_of_truth() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE

    assert "String[] sourceLmiLimbNames = detectLimbNames(sourceLmiPayload);" in source
    assert "String[] replacementLimbNames = selectLimbNamesForReplacement(" in source
    assert "sourceLmiLimbNames," in source
    assert "detectLimbNames(spt, sourceCharacterId)" not in source
    assert "detectLimbNames(patched, sourceCharacterId)" not in source
    assert "private static String[] detectLimbNames(byte[] source)" in source
    assert "private static boolean isLimbName(String value)" in source
    assert "private static int limbNameSeparator(String value)" in source
    assert "prefixId.equals(expected)" not in source


def test_binary_symbol_patch_allows_lmi_limb_prefix_length_to_change() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert "replacementLimbNames,targetLimbPrefix,stats" in compact
    assert "ReplacementKind.LIMB_NAME,true" in compact


def test_binary_symbol_patch_derives_length_preserving_limb_prefix() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert (
        "StringtargetLimbPrefix=targetCharacterId==null?null:"
        "limbPrefixFor(resourceId,replacementLimbNames);"
    ) in compact
    assert "targetLimbPrefix,sourceCharacterId" in compact
    assert "private static String limbPrefixFor(" in source
    assert "sameLengthLimbPrefixCandidate" in source
    assert 'sourcePrefix.endsWith("_")?sourcePrefix.length()-1' in compact
    assert "sourcePrefix.lastIndexOf('_')" in source
    assert "sourcePrefix.equals(candidate)" in source


def test_binary_symbol_patch_handles_role_ids_with_underscores_in_limb_names() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert "intunderscore=limbNameSeparator(sourceLimbName);" in compact
    assert "intunderscore=limbNameSeparator(value);" in compact
    assert "value.lastIndexOf('_')" in source


def test_binary_symbol_patch_limits_composite_lmi_limb_replacements() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert "selectLimbNamesForReplacement(sourceLmiLimbNames,sourceCharacterId)" in compact
    assert "subtractLimbNames(sourceLmiLimbNames,replacementLimbNames)" in compact
    assert 'limbNamesWithPrefix(sourceLmiLimbNames,sourceCharacterId+"_")' in compact
    assert "soleLimbPrefix(sourceLmiLimbNames)" in compact
    assert "dominantLimbPrefix(sourceLmiLimbNames)" in compact
    assert "private static String limbPrefix(String limbName)" in source


def test_binary_symbol_patch_aliases_external_lmi_limb_names() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert (
        "aliasExternalLmiLimbNames("
        "patched,externalLmiLimbNames,resourceId,false,stats)"
    ) in compact
    assert (
        "aliasExternalLmiLimbNames("
        "spt,externalLmiLimbNames,resourceId,true,stats)"
    ) in compact
    assert "ReplacementKind.EXTERNAL_LMI_ALIAS" in source
    assert "externalLmiLimbAlias" in source
    assert "compactAliasPrefix" in source
    assert "External LMI alias length mismatch" in source
    assert "sanitizeLimbAliasToken" in source


def test_binary_symbol_patch_aliases_bare_lmi_limb_names_narrowly() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert "detectDataLimbNameValues(source)" in compact
    assert "isPotentialLmiLimbName(value)" in compact
    assert "compactBareLimbAlias" in source
    assert "Bare LMI alias length mismatch" in source
    assert (
        'replaceNamedUtfStringValue(patched,sptPayload?"limbName":"name",'
        "sourceLimbName,alias,stats,ReplacementKind.EXTERNAL_LMI_ALIAS,false)"
    ) in compact


def test_binary_symbol_patch_can_clear_unmatched_donor_limb_names() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE
    compact = "".join(source.split())

    assert "if(parts[1].isEmpty())" in compact
    assert (
        'replaceNamedUtfStringValue(patched,"limbName",parts[0],"",'
        "stats,ReplacementKind.DONOR_LIMB,true)"
    ) in compact


def test_binary_symbol_patch_preserves_source_lmi_image_namespaces() -> None:
    source = BINARY_SYMBOL_PATCH_JAVA_SOURCE

    assert "sourceImageNamespaces" not in source
    assert '"png_" + resourceId + "_Limbs/"' not in source
    assert "ReplacementKind.IMAGE_NAMESPACE" not in source
    assert "lmi_image_namespace_replacements" not in source
    assert "imageNamespaceReplacements" not in source
