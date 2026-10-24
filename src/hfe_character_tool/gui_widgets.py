"""Small custom widgets for the desktop tool (all plain tkinter, no extra deps)."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any, Callable, Sequence, cast

from hfe_character_tool import gui_theme as theme
from hfe_character_tool.gui_presenter import ProjectListRow
from hfe_character_tool.models import AFTERIMAGE_MAX_DELAY

WheelHandler = Callable[[int], None]


class CardFrame(tk.Frame):
    """A white bordered section; `header` holds the title row when there is one."""

    header: ttk.Frame


def make_card(
    master: tk.Misc,
    title: str | None = None,
    subtitle: str | None = None,
    padding: int = 18,
) -> tuple[CardFrame, ttk.Frame]:
    """Returns (outer, body); grid/pack the outer, fill the body."""
    outer = CardFrame(
        master,
        background=theme.CARD,
        highlightthickness=1,
        highlightbackground=theme.BORDER,
        highlightcolor=theme.BORDER,
    )
    pad = theme.px(padding)
    outer.header = ttk.Frame(outer)
    if title:
        outer.header.pack(fill="x", padx=pad, pady=(pad, 0))
        ttk.Label(outer.header, text=title, style="Section.TLabel").pack(side="left")
        if subtitle:
            ttk.Label(outer, text=subtitle, style="Muted.TLabel", wraplength=theme.px(640)).pack(
                fill="x", padx=pad, pady=(theme.px(4), 0)
            )
    body = ttk.Frame(outer)
    body.pack(fill="both", expand=True, padx=pad, pady=(theme.px(12) if title else pad, pad))
    return outer, body


class Tooltip:
    def __init__(self, widget: tk.Misc, text: str | Callable[[], str], delay: int = 450) -> None:
        self._widget = widget
        self._text = text
        self._delay = delay
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._after = self._widget.after(self._delay, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            self._widget.after_cancel(self._after)
            self._after = None

    def _show(self) -> None:
        text = self._text() if callable(self._text) else self._text
        if not text:
            return
        tip = tk.Toplevel(self._widget)
        tip.wm_overrideredirect(True)
        tip.configure(background=theme.TEXT)
        tk.Label(
            tip,
            text=text,
            justify="left",
            background=theme.TEXT,
            foreground="#ffffff",
            font=theme.font(9),
            padx=theme.px(10),
            pady=theme.px(6),
            wraplength=theme.px(360),
        ).pack()
        x = self._widget.winfo_rootx() + theme.px(4)
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + theme.px(6)
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def add_placeholder(entry: ttk.Entry, text: str) -> None:
    """Grey hint text shown inside an empty, unfocused entry."""
    label = tk.Label(
        entry,
        text=text,
        background=theme.FIELD,
        foreground=theme.TEXT_SUBTLE,
        font=theme.font(10),
        cursor="xterm",
    )

    def refresh(_event: object = None) -> None:
        if entry.get() or entry.focus_get() is entry:
            label.place_forget()
        else:
            label.place(x=theme.px(9), rely=0.5, anchor="w")

    label.bind("<Button-1>", lambda _event: entry.focus_set())
    entry.bind("<FocusIn>", refresh, add="+")
    entry.bind("<FocusOut>", refresh, add="+")
    entry.bind("<KeyRelease>", refresh, add="+")
    entry.after_idle(refresh)


class ListboxTooltip:
    """Per-row tooltip for a Listbox; `text_for(index)` returns "" for no tooltip."""

    def __init__(self, listbox: tk.Listbox, text_for: Callable[[int], str]) -> None:
        self._listbox = listbox
        self._text_for = text_for
        self._index: int | None = None
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        listbox.bind("<Motion>", self._on_motion, add="+")
        listbox.bind("<Leave>", lambda _event: self._reset(), add="+")
        listbox.bind("<ButtonPress>", lambda _event: self._reset(), add="+")

    def _on_motion(self, event: tk.Event[tk.Misc]) -> None:
        if self._listbox.size() == 0:
            return
        index = int(cast(Any, self._listbox).nearest(event.y))
        bbox = self._listbox.bbox(index)
        if not bbox or not bbox[1] <= event.y <= bbox[1] + bbox[3]:
            self._reset()
            return
        if index != self._index:
            self._reset()
            self._index = index
            self._after = self._listbox.after(350, self._show)

    def _show(self) -> None:
        self._after = None
        if self._index is None:
            return
        text = self._text_for(self._index)
        if not text:
            return
        bbox = self._listbox.bbox(self._index)
        tip = tk.Toplevel(self._listbox)
        tip.wm_overrideredirect(True)
        tk.Label(
            tip,
            text=text,
            justify="left",
            background=theme.TEXT,
            foreground="#ffffff",
            font=theme.font(9),
            padx=theme.px(10),
            pady=theme.px(6),
            wraplength=theme.px(360),
        ).pack()
        x = self._listbox.winfo_rootx() + self._listbox.winfo_width() + theme.px(6)
        y = self._listbox.winfo_rooty() + (bbox[1] if bbox else 0)
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _reset(self) -> None:
        if self._after is not None:
            self._listbox.after_cancel(self._after)
            self._after = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        self._index = None


class ScrollFrame(tk.Frame):
    """Vertically scrolling page; put children into `body`."""

    def __init__(self, master: tk.Misc, background: str = theme.PAGE) -> None:
        super().__init__(master, background=background)
        self._canvas = tk.Canvas(self, background=background, highlightthickness=0, borderwidth=0)
        self._scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self._canvas.yview, style="Thin.Vertical.TScrollbar"
        )
        self.body = tk.Frame(self._canvas, background=background)
        self._window = self._canvas.create_window(0, 0, anchor="nw", window=self.body)
        self._canvas.configure(yscrollcommand=self._on_yscroll)
        self._canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_body_configure(self, _event: object) -> None:
        self._canvas.configure(scrollregion=(0, 0, self.body.winfo_reqwidth(), self._content_h()))

    def _content_h(self) -> int:
        return max(self.body.winfo_reqheight(), self._canvas.winfo_height())

    def _on_canvas_configure(self, event: tk.Event[tk.Misc]) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)
        self._on_body_configure(event)

    def _on_yscroll(self, first: float | str, last: float | str) -> None:
        self._scrollbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scrollbar.pack_forget()
        elif not self._scrollbar.winfo_ismapped():
            self._scrollbar.pack(side="right", fill="y", padx=(0, theme.px(3)), pady=theme.px(4))

    def scroll_wheel(self, units: int) -> None:
        if self.body.winfo_reqheight() > self._canvas.winfo_height():
            self._canvas.yview_scroll(units, "units")

    def scroll_to_top(self) -> None:
        self._canvas.yview_moveto(0)

    def ensure_visible(self, widget: tk.Misc) -> None:
        self.update_idletasks()
        top = widget.winfo_rooty() - self.body.winfo_rooty()
        height = self.body.winfo_reqheight()
        view = self._canvas.winfo_height()
        if height <= view:
            return
        first, last = (float(value) for value in self._canvas.yview())
        if top < first * height or top + widget.winfo_height() > last * height:
            self._canvas.yview_moveto(max(0.0, (top - theme.px(40)) / height))


class CanvasButton:
    """A button drawn as items on an existing canvas, so it sits seamlessly on the art."""

    def __init__(
        self,
        canvas: tk.Canvas,
        tag: str,
        text: str,
        command: Callable[[], None],
        kind: str = "glass",
    ) -> None:
        self._canvas = canvas
        self._tag = tag
        self._text = text
        self._command = command
        self._kind = kind
        self._box = (0.0, 0.0, 0.0, 0.0)
        self._hover = False
        self._enabled = True
        self._font = tkfont.Font(
            family=theme.FONT_FAMILY, size=10, weight="bold" if kind == "accent" else "normal"
        )
        canvas.tag_bind(tag, "<Enter>", lambda _event: self._set_hover(True))
        canvas.tag_bind(tag, "<Leave>", lambda _event: self._set_hover(False))
        canvas.tag_bind(tag, "<Button-1>", lambda _event: self._click())

    def place(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self._box = (x1, y1, x2, y2)
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._redraw()

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        self._canvas.configure(cursor="hand2" if hover and self._enabled else "")
        self._redraw()

    def _click(self) -> None:
        if self._enabled:
            self._command()

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete(self._tag)
        x1, y1, x2, y2 = self._box
        if x2 <= x1:
            return
        if self._kind == "accent":
            fill = theme.ACCENT_HOVER if self._hover else theme.ACCENT
            outline, color = fill, "#ffffff"
        else:
            fill = "#ffffff" if self._hover else "#fbf9fe"
            outline = theme.ACCENT_SOFT_2 if self._hover else "#e6def2"
            color = theme.ACCENT_TEXT if self._hover else theme.TEXT
        if not self._enabled:
            fill, outline, color = "#ece8f3", "#e3ddee", theme.TEXT_DISABLED
        theme.rounded_rect(
            canvas, x1, y1, x2, y2, theme.px(11), fill=fill, outline=outline, tags=(self._tag,)
        )
        canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text=self._text,
            fill=color,
            font=self._font,
            tags=(self._tag,),
        )


class Pill(tk.Canvas):
    """Rounded status chip; optionally clickable."""

    def __init__(
        self,
        master: tk.Misc,
        background: str = theme.CARD,
        command: Callable[[], None] | None = None,
        size: int = 9,
    ) -> None:
        super().__init__(master, highlightthickness=0, borderwidth=0, background=background)
        self._font = tkfont.Font(family=theme.FONT_FAMILY, size=size, weight="bold")
        self._command = command
        self._text = ""
        self._kind = "neutral"
        self._dot = True
        if command is not None:
            self.configure(cursor="hand2")
            self.bind("<Button-1>", lambda _event: command())

    def set(self, text: str, kind: str = "neutral", dot: bool = True) -> None:
        self._text, self._kind, self._dot = text, kind, dot
        self.delete("all")
        if not text:
            self.configure(width=1, height=1)
            return
        fg, bg = theme.KIND_COLORS.get(kind, theme.KIND_COLORS["neutral"])
        pad = theme.px(9)
        dot_w = theme.px(12) if dot else 0
        width = self._font.measure(text) + pad * 2 + dot_w
        height = self._font.metrics("linespace") + theme.px(6)
        self.configure(width=width, height=height)
        theme.rounded_rect(self, 0, 0, width, height, height / 2, fill=bg, outline=bg)
        if dot:
            r = theme.px(3)
            cx, cy = pad + r, height / 2
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fg, outline=fg)
        self.create_text(pad + dot_w, height / 2, text=text, anchor="w", fill=fg, font=self._font)


class TabBar(tk.Canvas):
    def __init__(self, master: tk.Misc, on_select: Callable[[int], None]) -> None:
        super().__init__(
            master, height=theme.px(44), highlightthickness=0, borderwidth=0, background=theme.CARD
        )
        self._on_select = on_select
        self._labels: list[str] = []
        self._badges: dict[int, tuple[str, str]] = {}
        self._selected = 0
        self._hover: int | None = None
        self._hit: list[tuple[int, int]] = []
        self._font = tkfont.Font(family=theme.FONT_FAMILY, size=10)
        self._bold = tkfont.Font(family=theme.FONT_FAMILY, size=10, weight="bold")
        self._badge_font = tkfont.Font(family=theme.FONT_FAMILY, size=8, weight="bold")
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _event: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)

    def set_tabs(self, labels: Sequence[str]) -> None:
        self._labels = list(labels)
        self._redraw()

    def set_badge(self, index: int, text: str, kind: str = "neutral") -> None:
        if text:
            self._badges[index] = (text, kind)
        else:
            self._badges.pop(index, None)
        self._redraw()

    def select(self, index: int) -> None:
        self._selected = index
        self._redraw()

    def _index_at(self, x: int) -> int | None:
        for index, (left, right) in enumerate(self._hit):
            if left <= x <= right:
                return index
        return None

    def _on_motion(self, event: tk.Event[tk.Misc]) -> None:
        self._set_hover(self._index_at(event.x))

    def _set_hover(self, index: int | None) -> None:
        if index != self._hover:
            self._hover = index
            self.configure(cursor="hand2" if index is not None else "")
            self._redraw()

    def _on_click(self, event: tk.Event[tk.Misc]) -> None:
        index = self._index_at(event.x)
        if index is not None:
            self._on_select(index)

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        self.create_line(0, height - 1, width, height - 1, fill=theme.BORDER)
        x = theme.px(22)
        gap = theme.px(26)
        self._hit = []
        for index, label in enumerate(self._labels):
            selected = index == self._selected
            label_font = self._bold if selected else self._font
            color = (
                theme.ACCENT_TEXT
                if selected
                else theme.TEXT
                if index == self._hover
                else theme.TEXT_MUTED
            )
            text_w = label_font.measure(label)
            self.create_text(x, height / 2 - 1, text=label, anchor="w", fill=color, font=label_font)
            right = x + text_w
            badge = self._badges.get(index)
            if badge is not None:
                text, kind = badge
                fg, bg = theme.KIND_COLORS.get(kind, theme.KIND_COLORS["neutral"])
                bw = self._badge_font.measure(text) + theme.px(12)
                bh = theme.px(17)
                bx = right + theme.px(6)
                by = height / 2 - bh / 2 - 1
                theme.rounded_rect(self, bx, by, bx + bw, by + bh, bh / 2, fill=bg, outline=bg)
                self.create_text(
                    bx + bw / 2, by + bh / 2, text=text, fill=fg, font=self._badge_font
                )
                right = bx + bw
            if selected:
                theme.rounded_rect(
                    self,
                    x - 2,
                    height - theme.px(3),
                    right + 2,
                    height + 2,
                    2,
                    fill=theme.ACCENT,
                    outline=theme.ACCENT,
                )
            self._hit.append((x - gap // 2, right + gap // 2))
            x = right + gap


class ProjectList(tk.Canvas):
    """Two-line project rows drawn over the frosted sidebar background."""

    def __init__(
        self,
        master: tk.Misc,
        on_open: Callable[[Path], None],
        on_context: Callable[[Path, int, int], None],
    ) -> None:
        super().__init__(master, highlightthickness=0, borderwidth=0, background="#f3eef8")
        self._on_open = on_open
        self._on_context = on_context
        self._rows: tuple[ProjectListRow, ...] = ()
        self._current: Path | None = None
        self._hover: int | None = None
        self._focus_index: int | None = None
        self._top = 0
        self._row_h = theme.px(50)
        self._drag_offset: int | None = None
        self._background: Any | None = None
        self._empty_text = "还没有项目"
        self._title_font = tkfont.Font(family=theme.FONT_FAMILY, size=10, weight="bold")
        self._sub_font = tkfont.Font(family=theme.FONT_FAMILY, size=9)
        self.wheel_handler: WheelHandler = self._scroll_units
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _event: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda _event: setattr(self, "_drag_offset", None))
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<Up>", lambda _event: self._move_focus(-1))
        self.bind("<Down>", lambda _event: self._move_focus(1))
        self.bind("<Return>", self._open_focused)
        self.configure(takefocus=1)

    def set_background(self, photo: Any | None) -> None:
        self._background = photo
        self._redraw()

    def set_rows(self, rows: tuple[ProjectListRow, ...], empty_text: str) -> None:
        self._rows = rows
        self._empty_text = empty_text
        self._hover = None
        self._focus_index = None
        self._clamp()
        self._redraw()

    def set_current(self, path: Path | None) -> None:
        self._current = path
        self._redraw()

    def _content_h(self) -> int:
        return len(self._rows) * self._row_h

    def _max_top(self) -> int:
        return max(self._content_h() - self.winfo_height(), 0)

    def _clamp(self) -> None:
        self._top = min(max(self._top, 0), self._max_top())

    def _scroll_units(self, units: int) -> None:
        self._top += units * theme.px(40)
        self._clamp()
        self._redraw()

    def _index_at(self, y: int) -> int | None:
        index = (self._top + y) // self._row_h
        return index if 0 <= index < len(self._rows) else None

    def _on_motion(self, event: tk.Event[tk.Misc]) -> None:
        self._set_hover(None if self._in_scrollbar(event.x) else self._index_at(event.y))

    def _set_hover(self, index: int | None) -> None:
        if index != self._hover:
            self._hover = index
            self.configure(cursor="hand2" if index is not None else "")
            self._redraw()

    def _in_scrollbar(self, x: int) -> bool:
        return self._max_top() > 0 and x >= self.winfo_width() - theme.px(12)

    def _thumb(self) -> tuple[int, int]:
        height = max(self.winfo_height(), 1)
        content = max(self._content_h(), 1)
        thumb = max(theme.px(36), int(height * height / content))
        top = int((height - thumb) * self._top / max(self._max_top(), 1))
        return top, top + thumb

    def _on_click(self, event: tk.Event[tk.Misc]) -> None:
        self.focus_set()
        if self._in_scrollbar(event.x):
            top, bottom = self._thumb()
            self._drag_offset = event.y - top if top <= event.y <= bottom else (bottom - top) // 2
            self._on_drag(event)
            return
        index = self._index_at(event.y)
        if index is not None:
            self._focus_index = index
            self._on_open(self._rows[index].path)

    def _on_drag(self, event: tk.Event[tk.Misc]) -> None:
        if self._drag_offset is None:
            return
        top, bottom = self._thumb()
        travel = max(self.winfo_height() - (bottom - top), 1)
        self._top = int((event.y - self._drag_offset) / travel * self._max_top())
        self._clamp()
        self._redraw()

    def _on_right_click(self, event: tk.Event[tk.Misc]) -> None:
        index = self._index_at(event.y)
        if index is not None:
            self._on_context(self._rows[index].path, event.x_root, event.y_root)

    def _move_focus(self, step: int) -> None:
        if not self._rows:
            return
        current = self._focus_index
        if current is None:
            paths = [row.path for row in self._rows]
            current = paths.index(self._current) if self._current in paths else -step
        self._focus_index = min(max(current + step, 0), len(self._rows) - 1)
        row_top = self._focus_index * self._row_h
        if row_top < self._top:
            self._top = row_top
        elif row_top + self._row_h > self._top + self.winfo_height():
            self._top = row_top + self._row_h - self.winfo_height()
        self._clamp()
        self._redraw()

    def _open_focused(self, _event: object) -> None:
        if self._focus_index is not None and self._focus_index < len(self._rows):
            self._on_open(self._rows[self._focus_index].path)

    def _redraw(self) -> None:
        self.delete("all")
        width = self.winfo_width()
        height = self.winfo_height()
        if self._background is not None:
            self.create_image(0, 0, anchor="nw", image=self._background)
        if not self._rows:
            self.create_text(
                width / 2,
                theme.px(40),
                text=self._empty_text,
                fill=theme.TEXT_MUTED,
                font=self._sub_font,
                width=width - theme.px(24),
                justify="center",
            )
            return
        right = width - (theme.px(14) if self._max_top() > 0 else theme.px(4))
        first = self._top // self._row_h
        last = min(len(self._rows), (self._top + height) // self._row_h + 1)
        for index in range(first, last):
            row = self._rows[index]
            top = index * self._row_h - self._top
            current = row.path == self._current
            if current or index == self._hover or index == self._focus_index:
                fill = theme.CARD if current else "#faf7fe"
                outline = theme.ACCENT_SOFT_2 if current else "#ebe5f6"
                theme.rounded_rect(
                    self,
                    theme.px(2),
                    top + theme.px(3),
                    right,
                    top + self._row_h - theme.px(3),
                    theme.px(10),
                    fill=fill,
                    outline=outline,
                )
            if current:
                theme.rounded_rect(
                    self,
                    theme.px(2),
                    top + theme.px(13),
                    theme.px(6),
                    top + self._row_h - theme.px(13),
                    2,
                    fill=theme.ACCENT,
                    outline=theme.ACCENT,
                )
            color = theme.KIND_COLORS.get(row.status_kind, theme.KIND_COLORS["neutral"])[0]
            r = theme.px(3.5)
            cx = right - theme.px(14)
            cy = top + self._row_h / 2
            self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline=color)
            text_w = max(cx - theme.px(32), 10)
            self.create_text(
                theme.px(16),
                top + theme.px(17),
                text=_fit(row.title, self._title_font, text_w),
                anchor="w",
                fill=theme.ACCENT_TEXT if current else theme.TEXT,
                font=self._title_font,
            )
            self.create_text(
                theme.px(16),
                top + theme.px(35),
                text=_fit(row.subtitle, self._sub_font, text_w),
                anchor="w",
                fill=theme.TEXT_MUTED,
                font=self._sub_font,
            )
        if self._max_top() > 0:
            top_px, bottom_px = self._thumb()
            x = width - theme.px(6)
            self.create_line(
                x,
                top_px + theme.px(4),
                x,
                bottom_px - theme.px(4),
                fill="#c7bddb",
                width=theme.px(4),
                capstyle="round",
            )


def _fit(text: str, text_font: tkfont.Font, width: float) -> str:
    if text_font.measure(text) <= width:
        return text
    while text and text_font.measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


class FrameStrip(tk.Canvas):
    """Clickable frame chips: selected, edited (has a draft) and originally-has-items."""

    def __init__(self, master: tk.Misc, on_select: Callable[[int], None]) -> None:
        super().__init__(master, highlightthickness=0, borderwidth=0, background=theme.CARD)
        self._on_select = on_select
        self._count = 0
        self._original: set[int] = set()
        self._edited: set[int] = set()
        self._selected = 0
        self._hover: int | None = None
        self._enabled = True
        self._chip = theme.px(34)
        self._gap = theme.px(6)
        self._font = tkfont.Font(family=theme.FONT_FAMILY, size=10, weight="bold")
        self.configure(height=self._chip + theme.px(4), takefocus=1)
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _event: self._set_hover(None))
        self.bind("<Button-1>", self._on_click)
        self.bind("<Left>", lambda _event: self._step(-1))
        self.bind("<Right>", lambda _event: self._step(1))

    def set_frames(
        self, count: int, original: Sequence[int], edited: Sequence[int], selected: int
    ) -> None:
        self._count = count
        self._original = set(original)
        self._edited = set(edited)
        self._selected = selected
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._redraw()

    def _positions(self) -> list[tuple[int, int]]:
        width = max(self.winfo_width(), self._chip)
        per_row = max(1, (width + self._gap) // (self._chip + self._gap))
        return [
            (
                (index % per_row) * (self._chip + self._gap),
                (index // per_row) * (self._chip + self._gap),
            )
            for index in range(self._count)
        ]

    def _frame_at(self, x: int, y: int) -> int | None:
        for index, (cx, cy) in enumerate(self._positions()):
            if cx <= x <= cx + self._chip and cy <= y <= cy + self._chip:
                return index + 1
        return None

    def _on_motion(self, event: tk.Event[tk.Misc]) -> None:
        self._set_hover(self._frame_at(event.x, event.y) if self._enabled else None)

    def _set_hover(self, frame: int | None) -> None:
        if frame != self._hover:
            self._hover = frame
            self.configure(cursor="hand2" if frame is not None else "")
            self._redraw()

    def _on_click(self, event: tk.Event[tk.Misc]) -> None:
        if not self._enabled:
            return
        self.focus_set()
        frame = self._frame_at(event.x, event.y)
        if frame is not None and frame != self._selected:
            self._on_select(frame)

    def _step(self, delta: int) -> None:
        if self._enabled and self._count:
            frame = min(max(self._selected + delta, 1), self._count)
            if frame != self._selected:
                self._on_select(frame)

    def _redraw(self) -> None:
        self.delete("all")
        positions = self._positions()
        rows = (positions[-1][1] // (self._chip + self._gap) + 1) if positions else 1
        wanted = rows * (self._chip + self._gap)
        if int(self.cget("height")) != wanted:
            self.configure(height=wanted)
        for index, (x, y) in enumerate(positions):
            frame = index + 1
            selected = frame == self._selected
            edited = frame in self._edited
            if not self._enabled:
                fill, outline, color = theme.FIELD_DISABLED, theme.BORDER, theme.TEXT_DISABLED
            elif selected:
                fill, outline, color = theme.ACCENT, theme.ACCENT, "#ffffff"
            elif edited:
                fill, outline, color = theme.ACCENT_SOFT, theme.ACCENT_SOFT_2, theme.ACCENT_TEXT
            elif frame == self._hover:
                fill, outline, color = theme.HOVER, "#cfc5e6", theme.TEXT
            else:
                fill, outline, color = theme.FIELD, theme.FIELD_BORDER, theme.TEXT_MUTED
            size = self._chip
            theme.rounded_rect(
                self,
                x + 1,
                y + 1,
                x + size - 1,
                y + size - 1,
                theme.px(9),
                fill=fill,
                outline=outline,
            )
            self.create_text(
                x + size / 2, y + size / 2 - 1, text=str(frame), fill=color, font=self._font
            )
            if frame in self._original:
                r = theme.px(2.5)
                cx, cy = x + size / 2, y + size - theme.px(7)
                dot = "#ffffff" if selected else theme.TEXT_SUBTLE
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=dot, outline=dot)
            if edited:
                r = theme.px(4)
                cx, cy = x + size - theme.px(7), y + theme.px(7)
                self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=theme.PINK, outline=fill)


class DelayTimeline(tk.Canvas):
    """1..90 frame ruler; click or drag to add/remove afterimages."""

    def __init__(self, master: tk.Misc, on_change: Callable[[tuple[int, ...]], None]) -> None:
        super().__init__(
            master,
            height=theme.px(96),
            highlightthickness=0,
            borderwidth=0,
            background=theme.CARD,
            cursor="hand2",
        )
        self._on_change = on_change
        self._delays: set[int] = set()
        self._hover: int | None = None
        self._paint_add: bool | None = None
        self._enabled = True
        self._font = tkfont.Font(family=theme.FONT_FAMILY, size=8)
        self._tip_font = tkfont.Font(family=theme.FONT_FAMILY, size=9, weight="bold")
        self.bind("<Configure>", lambda _event: self._redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _event: self._set_hover(None))
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set_delays(self, delays: Sequence[int]) -> None:
        self._delays = set(delays)
        self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "")
        self._redraw()

    def _bounds(self) -> tuple[float, float]:
        return theme.px(14), max(self.winfo_width() - theme.px(14), theme.px(60))

    def _x(self, delay: int) -> float:
        left, right = self._bounds()
        return left + (delay - 1) * (right - left) / (AFTERIMAGE_MAX_DELAY - 1)

    def _delay_at(self, x: int) -> int:
        left, right = self._bounds()
        ratio = (x - left) / max(right - left, 1)
        return min(max(round(ratio * (AFTERIMAGE_MAX_DELAY - 1)) + 1, 1), AFTERIMAGE_MAX_DELAY)

    def _on_motion(self, event: tk.Event[tk.Misc]) -> None:
        self._set_hover(self._delay_at(event.x) if self._enabled else None)

    def _set_hover(self, delay: int | None) -> None:
        if delay != self._hover:
            self._hover = delay
            self._redraw()

    def _on_press(self, event: tk.Event[tk.Misc]) -> None:
        if not self._enabled:
            return
        delay = self._delay_at(event.x)
        self._paint_add = delay not in self._delays
        self._apply(delay)

    def _on_drag(self, event: tk.Event[tk.Misc]) -> None:
        if self._paint_add is None:
            return
        delay = self._delay_at(event.x)
        self._hover = delay
        self._apply(delay)

    def _on_release(self, _event: object) -> None:
        if self._paint_add is not None:
            self._paint_add = None
            self._on_change(tuple(sorted(self._delays)))

    def _apply(self, delay: int) -> None:
        if self._paint_add:
            self._delays.add(delay)
        else:
            self._delays.discard(delay)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        left, right = self._bounds()
        base = theme.px(62)
        track_top = theme.px(30)
        theme.rounded_rect(
            self,
            left - theme.px(8),
            track_top - theme.px(4),
            right + theme.px(8),
            base + theme.px(4),
            theme.px(10),
            fill=theme.FIELD,
            outline=theme.BORDER,
        )
        for delay in range(1, AFTERIMAGE_MAX_DELAY + 1):
            x = self._x(delay)
            if delay % 30 == 0:
                self.create_line(x, track_top, x, base, fill="#d5cde6", dash=(2, 3))
            if delay == 1 or delay % 10 == 0:
                self.create_text(
                    x, base + theme.px(15), text=str(delay), fill=theme.TEXT_SUBTLE, font=self._font
                )
        for second in (1, 2, 3):
            self.create_text(
                self._x(second * 30),
                base + theme.px(28),
                text=f"{second} 秒",
                fill=theme.TEXT_MUTED,
                font=self._font,
            )
        if not self._delays and self._hover is None:
            self.create_text(
                (left + right) / 2,
                (track_top + base) / 2,
                text="在这里点击添加残影，按住拖动可以连续添加",
                fill=theme.TEXT_SUBTLE,
                font=self._tip_font,
            )
        bar_w = max(theme.px(2), (right - left) / AFTERIMAGE_MAX_DELAY * 0.62)
        active = theme.ACCENT if self._enabled else theme.TEXT_DISABLED
        for delay in sorted(self._delays):
            x = self._x(delay)
            fade = 1.0 - (delay - 1) / (AFTERIMAGE_MAX_DELAY * 1.6)
            height = (base - track_top - theme.px(6)) * (0.55 + 0.45 * fade)
            self.create_rectangle(
                x - bar_w / 2,
                base - theme.px(3) - height,
                x + bar_w / 2,
                base - theme.px(3),
                fill=active,
                outline="",
            )
        if self._hover is not None:
            x = self._x(self._hover)
            exists = self._hover in self._delays
            color = theme.DANGER if exists else theme.PINK
            self.create_line(x, track_top - theme.px(2), x, base + theme.px(2), fill=color, width=2)
            action = "点击删除" if exists else "点击添加"
            text = f"延迟 {self._hover} 帧 · {self._hover / 30:.2f} 秒 · {action}"
            tw = self._tip_font.measure(text) + theme.px(16)
            tx = min(max(x - tw / 2, 0), self.winfo_width() - tw)
            theme.rounded_rect(
                self, tx, 0, tx + tw, theme.px(22), theme.px(8), fill=theme.TEXT, outline=theme.TEXT
            )
            self.create_text(
                tx + tw / 2, theme.px(11), text=text, fill="#ffffff", font=self._tip_font
            )


class ToggleSwitch(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
        background: str = theme.CARD,
    ) -> None:
        super().__init__(
            master,
            width=theme.px(42),
            height=theme.px(24),
            highlightthickness=0,
            borderwidth=0,
            background=background,
            cursor="hand2",
            takefocus=1,
        )
        self._variable = variable
        self._command = command
        self._enabled = True
        self.bind("<Button-1>", self.click)
        self.bind("<space>", self.click)
        variable.trace_add("write", lambda *_args: self._redraw())
        self._redraw()

    def click(self, _event: object = None) -> None:
        self.toggle()

    def toggle(self) -> None:
        if not self._enabled:
            return
        self._variable.set(not self._variable.get())
        if self._command is not None:
            self._command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "")
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        on = bool(self._variable.get())
        w, h = theme.px(42), theme.px(24)
        if not self._enabled:
            track = theme.ACCENT_SOFT_2 if on else theme.FIELD_DISABLED
        else:
            track = theme.ACCENT if on else "#d7d1e4"
        theme.rounded_rect(self, 1, 1, w - 1, h - 1, h / 2, fill=track, outline=track)
        r = h / 2 - theme.px(3)
        cx = w - h / 2 if on else h / 2
        self.create_oval(cx - r, h / 2 - r, cx + r, h / 2 + r, fill="#ffffff", outline="#ffffff")


class SearchCombobox(ttk.Combobox):
    """Combobox you can type into to filter a long list; the bound variable only ever
    receives an exact value from the list."""

    def __init__(
        self, master: tk.Misc, variable: tk.StringVar, values: Sequence[str] = (), **kwargs: Any
    ) -> None:
        self._text = tk.StringVar(value=variable.get())
        super().__init__(master, textvariable=self._text, **kwargs)
        self._variable = variable
        self._all: tuple[str, ...] = tuple(values)
        self._enabled = True
        super().configure(values=self._all, height=14)
        variable.trace_add("write", lambda *_args: self._sync_from_variable())
        self.bind("<<ComboboxSelected>>", lambda _event: self._commit(self._text.get()))
        self.bind("<KeyRelease>", self._on_key)
        self.bind("<Return>", lambda _event: self._commit_best())
        self.bind("<FocusOut>", lambda _event: self._commit_best())
        self.bind("<FocusIn>", lambda _event: self.select_range(0, "end"), add="+")
        self.bind("<Escape>", lambda _event: self._sync_from_variable())

    def set_values(self, values: Sequence[str]) -> None:
        self._all = tuple(values)
        super().configure(values=self._all)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        super().configure(state="normal" if enabled else "disabled")

    def _sync_from_variable(self) -> None:
        if self._text.get() != self._variable.get():
            self._text.set(self._variable.get())
        super().configure(values=self._all)

    def _on_key(self, event: tk.Event[tk.Misc]) -> None:
        if event.keysym in {"Return", "Escape", "Tab", "Up", "Down", "Left", "Right"}:
            return
        needle = self._text.get().strip().lower()
        matches = [value for value in self._all if needle in value.lower()] if needle else []
        super().configure(values=tuple(matches) if needle else self._all)

    def _commit_best(self) -> None:
        text = self._text.get().strip()
        if text in self._all:
            self._commit(text)
            return
        needle = text.lower()
        matches = [value for value in self._all if needle and needle in value.lower()]
        if len(matches) == 1:
            self._commit(matches[0])
        else:
            self._sync_from_variable()

    def _commit(self, value: str) -> None:
        if value in self._all and self._variable.get() != value:
            self._variable.set(value)
        self._sync_from_variable()
