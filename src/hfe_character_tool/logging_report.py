from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hfe_character_tool.models import Severity, ValidationReport, now_iso


@dataclass(frozen=True)
class LogEvent:
    timestamp: str
    level: Severity
    module: str
    stage: str
    message: str
    technical_detail: str = ""


class EventLog:
    def __init__(self) -> None:
        self._events: list[LogEvent] = []

    @property
    def events(self) -> tuple[LogEvent, ...]:
        return tuple(self._events)

    def info(self, module: str, stage: str, message: str, technical_detail: str = "") -> None:
        self.record(Severity.INFO, module, stage, message, technical_detail)

    def warning(self, module: str, stage: str, message: str, technical_detail: str = "") -> None:
        self.record(Severity.WARNING, module, stage, message, technical_detail)

    def error(self, module: str, stage: str, message: str, technical_detail: str = "") -> None:
        self.record(Severity.ERROR, module, stage, message, technical_detail)

    def record(
        self,
        level: Severity,
        module: str,
        stage: str,
        message: str,
        technical_detail: str = "",
    ) -> None:
        self._events.append(LogEvent(now_iso(), level, module, stage, message, technical_detail))


def user_summary(events: tuple[LogEvent, ...]) -> str:
    failures = [event.message for event in events if event.level is Severity.ERROR]
    if failures:
        return "；".join(failures)
    warnings = [event.message for event in events if event.level is Severity.WARNING]
    if warnings:
        return "已完成，但有警告：" + "；".join(warnings[:3])
    return "操作完成。"


def developer_log(events: tuple[LogEvent, ...]) -> str:
    lines = ["# 导出开发者日志", ""]
    for event in events:
        header = f"{event.level.label} {event.module}/{event.stage}: {event.message}"
        lines.append(
            f"- [{event.timestamp}] {header}"
        )
        if event.technical_detail:
            lines.append(f"  detail: {event.technical_detail}")
    return "\n".join(lines) + "\n"


def validation_report_text(report: ValidationReport) -> str:
    lines = ["# 校验报告", ""]
    if not report.issues:
        lines.append("未发现问题。")
    for issue in report.issues:
        lines.append(f"- {issue.severity.label}：{issue.message}")
        lines.append(f"  修复建议：{issue.suggestion}")
        lines.append(f"  定位：{issue.target}")
        if issue.technical_detail:
            lines.append(f"  技术细节：{issue.technical_detail}")
    return "\n".join(lines) + "\n"


def write_export_log(output_dir: Path, stem: str, events: tuple[LogEvent, ...]) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{stem}_export_log.md"
    path.write_text(developer_log(events), encoding="utf-8")
    return path


def write_validation_report(output_dir: Path, stem: str, report: ValidationReport) -> Path:
    output_dir.mkdir(exist_ok=True)
    path = output_dir / f"{stem}_validation_report.md"
    path.write_text(validation_report_text(report), encoding="utf-8")
    return path
