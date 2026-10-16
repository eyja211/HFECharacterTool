from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hfe_character_tool.gui_presenter import (
    add_afterimage_delay,
    afterimage_summary,
    quick_add_afterimage_delays,
    remove_afterimage_delays,
)
from hfe_character_tool.models import AfterimageSettings, replace_project
from hfe_character_tool.projects import create_project, load_project, save_project
from hfe_character_tool.swf_loadall_patch import write_loadall_patch_source
from hfe_character_tool.swf_probe import PROBE_JAVA_SOURCE
from hfe_character_tool.target_cache import TargetCacheEntry
from hfe_character_tool.validation import validate_editing


def test_quick_add_matches_the_users_example() -> None:
    # “添加 20 个分别延迟 1-20 帧的残影”
    assert quick_add_afterimage_delays((), "20", "1", "1") == tuple(range(1, 21))
    assert quick_add_afterimage_delays((5,), "3", "10", "5") == (5, 10, 15, 20)


def test_single_add_dedups_and_rejects_out_of_range() -> None:
    assert add_afterimage_delay((3, 7), "5") == (3, 5, 7)
    assert add_afterimage_delay((3,), "3") == (3,)
    for bad in ("0", "91", "abc", ""):
        with pytest.raises(ValueError):
            add_afterimage_delay((), bad)
    with pytest.raises(ValueError):
        quick_add_afterimage_delays((), "10", "85", "1")


def test_remove_and_summary() -> None:
    assert remove_afterimage_delays((1, 2, 3, 4), (2, 4)) == (1, 3)
    assert afterimage_summary(()) == "当前没有残影。"
    assert "2 个残影" in afterimage_summary((1, 30))


def test_project_round_trips_afterimage(tmp_path: Path) -> None:
    create_project(tmp_path, "p", "lucas-basic", "hero_two")
    project = load_project(tmp_path / "p")
    assert not project.afterimage.enabled
    save_project(
        tmp_path / "p",
        replace_project(project, afterimage=AfterimageSettings((10, 1, 5), False, True)),
    )
    loaded = load_project(tmp_path / "p")
    assert loaded.afterimage == AfterimageSettings((1, 5, 10), False, True)


def test_many_afterimages_only_warn(tmp_path: Path) -> None:
    create_project(tmp_path, "p", "lucas-basic", "hero_two")
    project = replace_project(
        load_project(tmp_path / "p"), afterimage=AfterimageSettings(tuple(range(1, 41)))
    )
    issues = [i for i in validate_editing(project).issues if i.target == "afterimage.delays"]
    assert [i.severity.value for i in issues] == ["warning"]


def test_native_flex_loader_loop_is_not_reported_unsafe() -> None:
    def kinds(style: str) -> tuple[str, ...]:
        reports = ({"kind": "custom_spt", "style": style, "has_loop_progress": True},)
        fake = SimpleNamespace(custom_loader_reports=reports)
        prop: Any = TargetCacheEntry.unsafe_runtime_custom_loader_kinds
        return tuple(prop.fget(fake))

    assert kinds("native_loop") == ()
    assert kinds("runtime_loop") == ("custom_spt",)


def test_loader_patch_and_probe_detect_native_label_loops(tmp_path: Path) -> None:
    source = write_loadall_patch_source(tmp_path).read_text(encoding="utf-8")
    assert "isNativeLabelLoop(code, i)" in source
    assert "return Integer.MAX_VALUE;" in source
    expected = 'isNativeLabelLoop(code, callIndex) ? "native_loop" : "runtime_loop"'
    assert expected in PROBE_JAVA_SOURCE
