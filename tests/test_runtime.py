from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hfe_character_tool.runtime import (
    default_export_dir,
    resource_path,
    resource_root,
)


def test_resource_path_uses_bundled_resources_when_workspace_is_portable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "tool_dir"
    bundle = tmp_path / "bundle"
    workspace.mkdir()
    original = bundle / "vendor" / "original_game" / "HFE v1.0.2.exe"
    projector = bundle / "vendor" / "projector" / "SA.exe"
    original.parent.mkdir(parents=True)
    projector.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    projector.write_bytes(b"projector")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert resource_root(workspace) == bundle
    assert resource_path(workspace, Path("vendor/original_game/HFE v1.0.2.exe")) == original


def test_default_export_dir_is_workspace_for_frozen_app(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert default_export_dir(tmp_path) == tmp_path
