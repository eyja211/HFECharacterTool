from __future__ import annotations

import json
from pathlib import Path

from hfe_character_tool.models import (
    AssetEntry,
    CharacterProject,
    ExportRecord,
    ProjectSummary,
    TargetGame,
    now_iso,
    project_from_dict,
    project_to_dict,
    replace_project,
)
from hfe_character_tool.templates import initial_project

PROJECT_FILE = "character.json"
PROJECT_DIRS = ("assets", "preview", "exports")


class ProjectError(Exception):
    def __init__(self, summary: str, detail: str = "") -> None:
        super().__init__(summary)
        self.summary = summary
        self.detail = detail


def create_project(
    root: Path,
    project_name: str,
    template_id: str,
    character_id: str,
    target_game: TargetGame | None = None,
    source_role_id: str = "lucas",
) -> CharacterProject:
    project_dir = root / project_name
    if project_dir.exists():
        raise ProjectError("项目文件夹已存在。", str(project_dir))
    project_dir.mkdir(parents=True)
    for child in PROJECT_DIRS:
        (project_dir / child).mkdir()
    timestamp = now_iso()
    project = replace_project(
        initial_project(project_name, template_id, character_id),
        source_role_id=source_role_id or "lucas",
        created_at=timestamp,
        updated_at=timestamp,
    )
    if target_game is not None:
        project = replace_project(
            project,
            target_game=target_game,
            created_at=timestamp,
            updated_at=timestamp,
        )
    save_project(project_dir, project)
    return project


def load_project(project_dir: Path) -> CharacterProject:
    path = project_dir / PROJECT_FILE
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise ProjectError("缺少 character.json。", str(path)) from exc
    except json.JSONDecodeError as exc:
        raise ProjectError("character.json 不是有效 JSON。", f"{path}: {exc}") from exc
    except OSError as exc:
        raise ProjectError("读取项目文件失败。", f"{path}: {exc}") from exc
    return project_from_dict(data)


def save_project(project_dir: Path, project: CharacterProject) -> None:
    path = project_dir / PROJECT_FILE
    try:
        with path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(project_to_dict(project), file, ensure_ascii=False, indent=2)
            file.write("\n")
    except OSError as exc:
        raise ProjectError("保存项目文件失败。", f"{path}: {exc}") from exc


def check_project_structure(project_dir: Path) -> list[str]:
    missing: list[str] = []
    if not (project_dir / PROJECT_FILE).is_file():
        missing.append(PROJECT_FILE)
    for child in PROJECT_DIRS:
        if not (project_dir / child).is_dir():
            missing.append(f"{child}/")
    return missing


def scan_projects(root: Path) -> tuple[ProjectSummary, ...]:
    summaries: list[ProjectSummary] = []
    if not root.exists():
        return ()
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not (child / PROJECT_FILE).is_file():
            continue
        try:
            project = load_project(child)
        except ProjectError:
            continue
        last_export = project.exports[-1].status if project.exports else "未导出"
        summaries.append(
            ProjectSummary(
                path=child,
                project_name=project.project_name,
                character_id=project.character_id,
                character_name=project.character_name,
                updated_at=project.updated_at,
                last_export_status=last_export,
            )
        )
    return tuple(summaries)


def update_assets(project_dir: Path, assets: tuple[AssetEntry, ...]) -> CharacterProject:
    project = load_project(project_dir).with_assets(assets)
    save_project(project_dir, project)
    return project


def update_export_record(project_dir: Path, record: ExportRecord) -> CharacterProject:
    project = load_project(project_dir).with_export(record)
    save_project(project_dir, project)
    return project
