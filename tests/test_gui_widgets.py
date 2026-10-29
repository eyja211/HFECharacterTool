from __future__ import annotations

import tkinter as tk
from collections.abc import Iterator

import pytest

from hfe_character_tool import gui_theme as theme
from hfe_character_tool.gui_widgets import CanvasButton


@pytest.fixture(scope="module")
def root() -> Iterator[tk.Tk]:
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - headless machines
        pytest.skip(f"Tk unavailable: {exc}")
    window.withdraw()
    yield window
    window.destroy()


@pytest.fixture()
def canvas(root: tk.Tk) -> Iterator[tk.Canvas]:
    widget = tk.Canvas(root)
    yield widget
    widget.destroy()


def _fill(canvas: tk.Canvas, item: int) -> str:
    return str(canvas.tk.call(str(canvas), "itemcget", item, "-fill"))


def test_canvas_button_hover_recolours_without_recreating_items(canvas: tk.Canvas) -> None:
    # Recreating the item under the pointer re-fires <Leave>/<Enter> forever (frozen window).
    button = CanvasButton(canvas, "btn", "新建", lambda: None, kind="accent")
    button.place(10, 10, 110, 40)
    items = canvas.find_withtag("btn")
    assert len(items) == 2

    button._set_hover(True)
    assert canvas.find_withtag("btn") == items
    assert _fill(canvas, items[0]) == theme.ACCENT_HOVER

    button._set_hover(False)
    button.set_enabled(False)
    assert canvas.find_withtag("btn") == items
    assert _fill(canvas, items[1]) == theme.TEXT_DISABLED


def test_canvas_button_place_only_rebuilds_when_box_changes(canvas: tk.Canvas) -> None:
    button = CanvasButton(canvas, "btn", "刷新", lambda: None)
    button.place(0, 0, 80, 30)
    items = canvas.find_withtag("btn")
    button.place(0, 0, 80, 30)
    assert canvas.find_withtag("btn") == items
    button.place(0, 0, 90, 30)
    assert canvas.find_withtag("btn") != items
    assert len(canvas.find_withtag("btn")) == 2
