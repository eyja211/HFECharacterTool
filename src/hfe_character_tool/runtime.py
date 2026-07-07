from __future__ import annotations

import sys
from pathlib import Path

RESOURCE_MARKERS = (
    Path("vendor") / "original_game" / "HFE v1.0.2.exe",
    Path("vendor") / "projector" / "SA.exe",
)


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_resource_root() -> Path | None:
    raw = getattr(sys, "_MEIPASS", None)
    if not raw:
        return None
    root = Path(str(raw)).resolve()
    if _has_resource_markers(root):
        return root
    return None


def resource_root(workspace: Path) -> Path:
    workspace = workspace.resolve()
    if _has_resource_markers(workspace):
        return workspace
    bundled = bundled_resource_root()
    if bundled is not None:
        return bundled
    for candidate in workspace.parents:
        if _has_resource_markers(candidate):
            return candidate
    return workspace


def resource_path(workspace: Path, relative_path: Path) -> Path:
    if relative_path.is_absolute():
        return relative_path
    workspace_path = workspace.resolve() / relative_path
    if workspace_path.exists():
        return workspace_path
    return resource_root(workspace) / relative_path


def default_export_dir(workspace: Path) -> Path:
    if is_frozen_app():
        return workspace
    return workspace / "output"


def default_target_cache_root(workspace: Path) -> Path:
    return default_export_dir(workspace) / "target_cache"


def _has_resource_markers(root: Path) -> bool:
    return all((root / marker).is_file() for marker in RESOURCE_MARKERS)
