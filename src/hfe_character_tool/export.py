from __future__ import annotations

import shutil
from pathlib import Path
from typing import Mapping

from hfe_character_tool.logging_report import (
    EventLog,
    user_summary,
    write_export_log,
    write_validation_report,
)
from hfe_character_tool.models import ExportRecord, ExportResult, now_iso
from hfe_character_tool.patching import PatchArtifacts, build_hfe_artifacts
from hfe_character_tool.projects import load_project, update_export_record
from hfe_character_tool.runtime import default_export_dir, default_target_cache_root
from hfe_character_tool.target_cache import TargetCacheError, prepare_target_cache
from hfe_character_tool.templates import to_internal_character
from hfe_character_tool.tools import check_tools, dependency_summary, discover_tools
from hfe_character_tool.validation import can_export, validate_for_export

TEST_CHECKLIST = (
    "打开导出的 EXE，确认不黑屏、不闪退、不停在加载画面。",
    "进入游戏内“角色一览”第 2 页，确认新增角色正常显示。",
    "进入 VS/选角色界面，翻角色，确认能看到并选择新增角色。",
    "选中新增角色后进入战斗，确认加载、移动、普通攻击和技能正常。",
    "登录账号，用新增角色打完一局，确认结算后没有 hack/封号/账号异常提示。",
    "记录任何崩溃、缺图、技能异常、数值异常或账号结算异常。",
)


def export_project(
    project_dir: Path, workspace: Path, output_dir: Path | None = None
) -> ExportResult:
    output = output_dir or default_export_dir(workspace)
    log = EventLog()
    project = load_project(project_dir)
    stem = _unique_stem(output, project.character_id)
    log.info("export", "load", "项目已加载。", str(project_dir))
    report = validate_for_export(project_dir, project)
    validation_path = write_validation_report(output, stem, report)
    if not can_export(report):
        log.error("export", "validation", "校验存在错误，已停止导出。")
        log_path = write_export_log(output, stem, log.events)
        return ExportResult(
            status="blocked",
            summary=user_summary(log.events),
            exe_path=None,
            validation_report=report,
            log_path=log_path,
            validation_report_path=validation_path,
            test_checklist=(),
        )
    tools = discover_tools(workspace)
    missing = check_tools(tools)
    if missing:
        log.error("tools", "check", dependency_summary(missing), repr(missing))
        log_path = write_export_log(output, stem, log.events)
        return ExportResult(
            status="failed",
            summary=user_summary(log.events),
            exe_path=None,
            validation_report=report,
            log_path=log_path,
            validation_report_path=validation_path,
            test_checklist=(),
        )
    log.info("tools", "check", "外部工具路径检查通过。")
    try:
        target_cache = prepare_target_cache(
            workspace,
            project.target_game,
            tools,
            output_root=default_target_cache_root(workspace),
        )
    except TargetCacheError as exc:
        log.error("target", "cache", exc.summary, exc.detail)
        log_path = write_export_log(output, stem, log.events)
        return ExportResult(
            "failed", user_summary(log.events), None, report, log_path, validation_path, ()
        )
    log.info(
        "target",
        "resolve",
        "已选择目标游戏版本。",
        (
            f"source={target_cache.source_path}; kind={target_cache.source_kind}; "
            f"cache_id={target_cache.cache_id}; cached_swf={target_cache.cached_swf_path}"
        ),
    )
    log.info(
        "target",
        "probe",
        "目标 SWF 探测信息已加载。",
        (
            f"cache_id={target_cache.cache_id}; probe_json={target_cache.probe_json_path}; "
            f"can_locate_global={target_cache.can_locate_global}; "
            f"can_locate_loaders={target_cache.can_locate_loaders}; "
            f"abc_data_global_classes={len(target_cache.abc_data_global_classes)}; "
            f"missing_symbol_abc_classes={target_cache.missing_symbol_abc_classes}"
        ),
    )
    report = validate_for_export(project_dir, project, target_cache=target_cache, tools=tools)
    validation_path = write_validation_report(output, stem, report)
    if not can_export(report):
        log.error("export", "validation", "目标兼容性校验存在错误，已停止导出。")
        log_path = write_export_log(output, stem, log.events)
        return ExportResult(
            status="blocked",
            summary=user_summary(log.events),
            exe_path=None,
            validation_report=report,
            log_path=log_path,
            validation_report_path=validation_path,
            test_checklist=(),
        )
    character = to_internal_character(project)
    use_real_patching = target_cache.cached_swf_path.is_file()
    source_role_id = project.source_role_id or "lucas"
    source_has_pow_metadata = (
        source_role_id in target_cache.global_pow_character_ids
        if target_cache.has_global_pow_character_ids
        else None
    )
    work_dir = output / "_work" / stem
    try:
        artifacts = build_hfe_artifacts(
            character,
            work_dir,
            stem,
            tools,
            use_real_patching=use_real_patching,
            target_swf_path=target_cache.cached_swf_path,
            target_source_kind=target_cache.source_kind,
            source_has_pow_metadata=source_has_pow_metadata,
            workspace=workspace,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("patching", "build", "生成导出产物失败。", str(exc))
        log_path = write_export_log(output, stem, log.events)
        return ExportResult(
            "failed",
            user_summary(log.events),
            None,
            report,
            log_path,
            validation_path,
            (),
            work_dir if work_dir.exists() else None,
        )
    published_path = _publish_artifact(artifacts, output, stem)
    status = "success_playable_candidate" if use_real_patching else "success_placeholder"
    if use_real_patching:
        log.info(
            "patching",
            "real",
            "已生成真实 HFE 可玩候选 EXE，仍需按清单进行游戏内验收。",
            str(published_path),
        )
        if artifacts.diagnostics:
            log.info(
                "patching",
                "diagnostics",
                "已记录二进制 patch 诊断信息。",
                _format_patch_diagnostics(artifacts.diagnostics),
            )
    else:
        log.warning(
            "patching",
            "placeholder",
            "已生成可诊断的补丁计划和占位 SWF/EXE，尚未完成真实游戏内验证。",
            str(artifacts.plan_path),
        )
    removed, cleanup_errors = _cleanup_artifact_files(artifacts)
    if cleanup_errors:
        log.warning(
            "export",
            "cleanup",
            "导出已成功，但部分临时诊断文件未能清理。",
            "; ".join(cleanup_errors),
        )
    elif removed:
        log.info(
            "export",
            "cleanup",
            "已清理本次导出的临时工作文件。",
            f"files={removed}; work_dir={work_dir}",
        )
    log.info("export", "write", "导出产物已写入 output。", str(published_path))
    log_path = write_export_log(output, stem, log.events)
    record = ExportRecord(
        exported_at=now_iso(),
        exe_path=str(published_path),
        status=status,
        summary=(
            "已生成真实 HFE 可玩候选；仍需按清单完成游戏内验收。"
            if use_real_patching
            else "已生成导出产物；真实游戏内可玩性仍需后续验证。"
        ),
        validation_report_path=str(validation_path),
        export_log_path=str(log_path),
    )
    update_export_record(project_dir, record)
    return ExportResult(
        status=status,
        summary=user_summary(log.events),
        exe_path=published_path,
        validation_report=report,
        log_path=log_path,
        validation_report_path=validation_path,
        test_checklist=TEST_CHECKLIST,
    )


def _unique_stem(output_dir: Path, character_id: str) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    base = f"{character_id}_{now_iso().replace(':', '').replace('+', 'Z')}"
    stem = base
    index = 2
    while (output_dir / f"{stem}.exe").exists() or (output_dir / f"{stem}.swf").exists():
        stem = f"{base}_{index}"
        index += 1
    return stem


def _publish_artifact(artifacts: PatchArtifacts, output_dir: Path, stem: str) -> Path:
    suffix = artifacts.exe_path.suffix or ".exe"
    published_path = output_dir / f"{stem}{suffix}"
    if artifacts.exe_path.resolve() != published_path.resolve():
        published_path.parent.mkdir(exist_ok=True)
        shutil.copy2(artifacts.exe_path, published_path)
    return published_path


def _cleanup_artifact_files(artifacts: PatchArtifacts) -> tuple[int, tuple[str, ...]]:
    removed = 0
    errors: list[str] = []
    paths = (
        artifacts.plan_path,
        artifacts.spt_path,
        artifacts.lmi_path,
        artifacts.base_swf_path,
        artifacts.patched_swf_path,
        artifacts.exe_path,
    )
    seen: set[Path] = set()
    for path in paths:
        if path is None:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return removed, tuple(errors)


def _format_patch_diagnostics(diagnostics: Mapping[str, object]) -> str:
    return "; ".join(f"{key}={diagnostics[key]}" for key in sorted(diagnostics))
