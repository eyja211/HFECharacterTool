from __future__ import annotations

import shutil
from collections import Counter
from collections.abc import Collection
from pathlib import Path

from hfe_character_tool.models import AssetEntry, PreviewResult, Severity, ValidationIssue
from hfe_character_tool.projects import load_project, update_assets
from hfe_character_tool.templates import get_template


def scan_asset_folder(
    folder: Path, required_assets: Collection[str] | None = None
) -> tuple[tuple[AssetEntry, ...], tuple[ValidationIssue, ...]]:
    files = [path for path in folder.iterdir() if path.is_file()]
    png_files = [path for path in files if path.suffix.lower() == ".png"]
    issues: list[ValidationIssue] = [
        ValidationIssue(
            Severity.WARNING,
            f"忽略非 PNG 文件：{path.name}",
            "素材文件夹中只保留 PNG 会更清晰。",
            f"assets.{path.name}",
        )
        for path in files
        if path.suffix.lower() != ".png"
    ]
    lowered = Counter(path.name.lower() for path in png_files)
    for name, count in lowered.items():
        if count > 1:
            issues.append(
                ValidationIssue(
                    Severity.ERROR,
                    f"存在重复或大小写冲突的素材名：{name}",
                    "请保留一个明确文件名。",
                    f"assets.{name}",
                )
            )
    valid_png_files = png_files
    if required_assets is not None:
        required_names = set(required_assets)
        valid_png_files = [path for path in png_files if path.name in required_names]
        for path in png_files:
            if path.name not in required_names:
                issues.append(
                    ValidationIssue(
                        Severity.WARNING,
                        f"额外 PNG 素材未登记：{path.name}",
                        "请使用当前模板要求的精确 PNG 文件名；额外素材不会进入项目清单。",
                        f"assets.{path.name}",
                    )
                )
        valid_names = {path.name for path in valid_png_files}
        for required in required_assets:
            if required not in valid_names:
                issues.append(
                    ValidationIssue(
                        Severity.ERROR,
                        f"缺少模板必需素材：{required}",
                        "请补齐缺失 PNG 后重新导入。",
                        f"assets.{required}",
                    )
                )
    assets = tuple(
        AssetEntry(path.name, _purpose_from_name(path.name), "valid") for path in valid_png_files
    )
    return assets, tuple(issues)


def import_assets(
    project_dir: Path, source_folder: Path
) -> tuple[tuple[AssetEntry, ...], tuple[ValidationIssue, ...]]:
    project = load_project(project_dir)
    template = get_template(project.template_id)
    required_assets = template.required_assets or None
    assets, issues = scan_asset_folder(source_folder, required_assets)
    target = project_dir / "assets"
    target.mkdir(exist_ok=True)
    for asset in assets:
        shutil.copy2(source_folder / asset.file_name, target / asset.file_name)
    update_assets(project_dir, assets)
    return assets, issues


def build_static_preview(project_dir: Path) -> PreviewResult:
    project = load_project(project_dir)
    if not project.assets:
        return PreviewResult("placeholder", "", "尚未导入素材，显示占位预览。")
    first = project.assets[0]
    path = project_dir / "assets" / first.file_name
    if not path.is_file():
        return PreviewResult("placeholder", "", f"预览素材不存在：{first.file_name}")
    return PreviewResult("static", str(path), f"静态预览素材：{first.file_name}")


def _purpose_from_name(file_name: str) -> str:
    return Path(file_name).stem
