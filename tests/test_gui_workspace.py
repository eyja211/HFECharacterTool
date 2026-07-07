from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hfe_character_tool.gui import resolve_workspace


def test_resolve_workspace_walks_up_from_packaged_exe_location(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    original = workspace / "vendor" / "original_game" / "HFE v1.0.2.exe"
    projector = workspace / "vendor" / "projector" / "SA.exe"
    original.parent.mkdir(parents=True)
    projector.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    projector.write_bytes(b"projector")
    exe = workspace / "output" / "tool_dist" / "hfe_character_tool.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"exe")

    assert resolve_workspace(start=exe.parent, executable=exe) == workspace


def test_resolve_workspace_falls_back_to_start_when_markers_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    start = tmp_path / "loose_tool"
    start.mkdir()
    cwd = tmp_path / "cwd_without_markers"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert resolve_workspace(start=start) == start


def test_resolve_workspace_uses_executable_directory_for_frozen_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_dir = tmp_path / "portable"
    release_dir.mkdir()
    exe = release_dir / "HFE角色定制工具.exe"
    exe.write_bytes(b"exe")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert resolve_workspace(start=tmp_path, executable=exe) == release_dir


def test_main_starts_app_without_auth_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hfe_character_tool import gui

    calls: list[tuple[str, Path | None]] = []

    class StubApp:
        def __init__(self, workspace: Path) -> None:
            calls.append(("init", workspace))

        def mainloop(self) -> None:
            calls.append(("mainloop", None))

    monkeypatch.setattr(gui, "resolve_workspace", lambda: tmp_path)
    monkeypatch.setattr(gui, "HfeCharacterApp", StubApp)

    assert not hasattr(gui, "prompt" + "_for_access")
    gui.main()

    assert calls == [("init", tmp_path), ("mainloop", None)]
