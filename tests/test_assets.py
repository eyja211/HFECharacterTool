from __future__ import annotations

from pathlib import Path
from typing import Iterable

from hfe_character_tool.assets import import_assets, scan_asset_folder
from hfe_character_tool.models import Severity, ValidationIssue
from hfe_character_tool.projects import create_project, load_project


def test_scan_asset_folder_applies_template_names(tmp_path: Path) -> None:
    for name in ("0.png", "extra.png", "note.txt"):
        (tmp_path / name).write_bytes(b"x")

    assets, issues = scan_asset_folder(tmp_path, ("0.png", "1.png"))

    assert [asset.file_name for asset in assets] == ["0.png"]
    assert _issue(issues, Severity.WARNING, "assets.extra.png")
    assert _issue(issues, Severity.ERROR, "assets.1.png")
    assert _issue(issues, Severity.WARNING, "assets.note.txt")


def test_import_assets_registers_pngs_when_template_has_no_required_assets(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    create_project(projects_root, "asset_named", "lucas-basic", "asset_named")
    project_dir = projects_root / "asset_named"
    source = tmp_path / "source"
    source.mkdir()
    for name in ("0.png", "1.png", "2.png", "extra.png"):
        (source / name).write_bytes(b"x")

    assets, issues = import_assets(project_dir, source)
    project = load_project(project_dir)

    assert [asset.file_name for asset in assets] == ["0.png", "1.png", "2.png", "extra.png"]
    assert [asset.file_name for asset in project.assets] == ["0.png", "1.png", "2.png", "extra.png"]
    assert (project_dir / "assets" / "0.png").is_file()
    assert (project_dir / "assets" / "1.png").is_file()
    assert (project_dir / "assets" / "2.png").is_file()
    assert (project_dir / "assets" / "extra.png").is_file()
    assert not issues


def test_import_assets_keeps_non_png_warning_without_required_assets(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    create_project(projects_root, "asset_issues", "lucas-basic", "asset_issues")
    project_dir = projects_root / "asset_issues"
    source = tmp_path / "source"
    source.mkdir()
    for name in ("0.png", "extra.png", "readme.txt"):
        (source / name).write_bytes(b"x")

    assets, issues = import_assets(project_dir, source)
    project = load_project(project_dir)

    assert [asset.file_name for asset in assets] == ["0.png", "extra.png"]
    assert [asset.file_name for asset in project.assets] == ["0.png", "extra.png"]
    assert (project_dir / "assets" / "0.png").is_file()
    assert (project_dir / "assets" / "extra.png").is_file()
    assert _issue(issues, Severity.WARNING, "assets.readme.txt")


def test_scan_asset_folder_reports_case_conflicts() -> None:
    folder = _FakeFolder(
        _FakePath("0.png"),
        _FakePath("0.PNG"),
        _FakePath("1.png"),
    )

    assets, issues = scan_asset_folder(folder, ("0.png", "1.png"))  # type: ignore[arg-type]

    assert [asset.file_name for asset in assets] == ["0.png", "1.png"]
    assert _issue(issues, Severity.ERROR, "assets.0.png")


def _issue(issues: Iterable[ValidationIssue], severity: Severity, target: str) -> bool:
    return any(
        getattr(issue, "severity", None) is severity and getattr(issue, "target", "") == target
        for issue in issues
    )


class _FakeFolder:
    def __init__(self, *paths: _FakePath) -> None:
        self._paths = paths

    def iterdir(self) -> tuple[_FakePath, ...]:
        return self._paths


class _FakePath:
    def __init__(self, name: str) -> None:
        self.name = name
        self.suffix = Path(name).suffix

    def is_file(self) -> bool:
        return True
