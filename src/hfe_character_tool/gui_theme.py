"""Visual theme for the desktop tool: palette, ttk styles and the art backdrop.

The look is "fresh frosted glass over the 底图 art": the art fills the window and
shows through a frosted sidebar, while the editor itself sits on one solid white
panel so every ttk widget lands on a uniform color (no per-label glass seams).
"""

from __future__ import annotations

import tkinter as tk
from contextlib import suppress
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any, Sequence, cast

FONT_FAMILY = "Microsoft YaHei UI"

ACCENT = "#7457e6"
ACCENT_HOVER = "#6343da"
ACCENT_PRESSED = "#5434c4"
ACCENT_SOFT = "#efebfd"
ACCENT_SOFT_2 = "#e2dafb"
ACCENT_TEXT = "#5a3fcf"
PINK = "#e2638f"

CARD = "#ffffff"
PAGE = "#f6f4fa"
FIELD = "#f8f7fc"
FIELD_DISABLED = "#f1eff5"
BORDER = "#e6e1ef"
FIELD_BORDER = "#d8d2e5"
HOVER = "#f3f0fa"

TEXT = "#26233a"
TEXT_MUTED = "#6b6680"
TEXT_SUBTLE = "#9b96ad"
TEXT_DISABLED = "#b4afc2"

SUCCESS = "#178a5c"
SUCCESS_SOFT = "#e2f5ec"
WARNING = "#b8720a"
WARNING_SOFT = "#fdf2de"
DANGER = "#cf3d5a"
DANGER_SOFT = "#fde7ec"
NEUTRAL = "#8a859c"
NEUTRAL_SOFT = "#efedf4"

KIND_COLORS = {
    "success": (SUCCESS, SUCCESS_SOFT),
    "warning": (WARNING, WARNING_SOFT),
    "danger": (DANGER, DANGER_SOFT),
    "neutral": (NEUTRAL, NEUTRAL_SOFT),
    "accent": (ACCENT_TEXT, ACCENT_SOFT),
}

BACKDROP_FALLBACK = "#e9e3f3"
PANEL_RADIUS = 18

_scale = 1.0


def px(value: float) -> int:
    """Scale a 96-dpi pixel size to the current display."""
    return int(round(value * _scale))


def scale() -> float:
    return _scale


def font(size: int = 10, weight: str = "normal") -> tuple[str, int, str]:
    return (FONT_FAMILY, size, weight)


def enable_dpi_awareness() -> None:
    """Render crisp text on scaled Windows displays instead of a blurry bitmap stretch."""
    with suppress(Exception):
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return
        try:
            windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            windll.user32.SetProcessDPIAware()


def configure_styles(root: tk.Misc) -> None:
    global _scale
    _scale = max(root.winfo_fpixels("1i") / 96.0, 1.0)
    style = ttk.Style(root)
    with suppress(tk.TclError):
        style.theme_use("clam")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        with suppress(tk.TclError):
            tkfont.nametofont(name).configure(family=FONT_FAMILY, size=10)

    style.configure(
        ".",
        font=font(10),
        background=CARD,
        foreground=TEXT,
        bordercolor=BORDER,
        lightcolor=CARD,
        darkcolor=CARD,
        troughcolor=PAGE,
        focuscolor=ACCENT,
        selectbackground=ACCENT_SOFT_2,
        selectforeground=TEXT,
        insertcolor=TEXT,
    )
    style.configure("TFrame", background=CARD)
    style.configure("Page.TFrame", background=PAGE)
    style.configure("Card.TFrame", background=CARD, bordercolor=BORDER, relief="solid")
    style.configure("TLabel", background=CARD, foreground=TEXT)
    style.configure("Page.TLabel", background=PAGE, foreground=TEXT_MUTED)
    style.configure("Muted.TLabel", background=CARD, foreground=TEXT_MUTED)
    style.configure("Subtle.TLabel", background=CARD, foreground=TEXT_SUBTLE, font=font(9))
    style.configure("Hint.TLabel", background=CARD, foreground=TEXT_SUBTLE, font=font(9))
    style.configure("Error.TLabel", background=CARD, foreground=DANGER, font=font(9))
    style.configure("Warn.TLabel", background=CARD, foreground=WARNING, font=font(9))
    style.configure("Field.TLabel", background=CARD, foreground=TEXT_MUTED, font=font(9))
    style.configure("Title.TLabel", background=CARD, foreground=TEXT, font=font(16, "bold"))
    style.configure("Section.TLabel", background=CARD, foreground=TEXT, font=font(11, "bold"))
    style.configure("Big.TLabel", background=CARD, foreground=TEXT, font=font(20, "bold"))
    style.configure("Strong.TLabel", background=CARD, foreground=TEXT, font=font(10, "bold"))
    style.configure("Accent.TLabel", background=CARD, foreground=ACCENT_TEXT)
    style.configure("WarnText.TLabel", background=CARD, foreground=WARNING)
    style.configure("TSeparator", background=BORDER)

    button_padding = (px(14), px(6))
    style.configure(
        "TButton",
        padding=button_padding,
        background=CARD,
        foreground=TEXT,
        bordercolor=FIELD_BORDER,
        lightcolor=CARD,
        darkcolor=CARD,
        relief="raised",
        focusthickness=0,
        anchor="center",
    )
    style.map(
        "TButton",
        background=[("disabled", FIELD_DISABLED), ("pressed", ACCENT_SOFT_2), ("active", HOVER)],
        foreground=[("disabled", TEXT_DISABLED)],
        bordercolor=[("disabled", BORDER), ("focus", ACCENT), ("active", "#c9c0de")],
        lightcolor=[("disabled", FIELD_DISABLED), ("pressed", ACCENT_SOFT_2), ("active", HOVER)],
        darkcolor=[("disabled", FIELD_DISABLED), ("pressed", ACCENT_SOFT_2), ("active", HOVER)],
    )
    _solid_button(style, "Accent.TButton", ACCENT, ACCENT_HOVER, ACCENT_PRESSED, "#ffffff")
    _solid_button(style, "Danger.TButton", DANGER, "#bb3150", "#a52844", "#ffffff")
    style.configure(
        "Ghost.TButton",
        background=CARD,
        foreground=ACCENT_TEXT,
        bordercolor=CARD,
        lightcolor=CARD,
        darkcolor=CARD,
        padding=(px(8), px(4)),
    )
    style.map(
        "Ghost.TButton",
        background=[("disabled", CARD), ("pressed", ACCENT_SOFT_2), ("active", ACCENT_SOFT)],
        foreground=[("disabled", TEXT_DISABLED)],
        bordercolor=[("disabled", CARD), ("pressed", ACCENT_SOFT_2), ("active", ACCENT_SOFT)],
        lightcolor=[("pressed", ACCENT_SOFT_2), ("active", ACCENT_SOFT)],
        darkcolor=[("pressed", ACCENT_SOFT_2), ("active", ACCENT_SOFT)],
    )
    style.configure("Small.TButton", padding=(px(10), px(3)), font=font(9))
    style.configure("Big.Accent.TButton", padding=(px(26), px(10)), font=font(11, "bold"))

    field_padding = (px(8), px(5))
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(
            widget,
            fieldbackground=FIELD,
            background=FIELD,
            foreground=TEXT,
            bordercolor=FIELD_BORDER,
            lightcolor=FIELD,
            darkcolor=FIELD,
            arrowcolor=TEXT_MUTED,
            padding=field_padding,
            insertwidth=1,
        )
        style.map(
            widget,
            fieldbackground=[("disabled", FIELD_DISABLED), ("focus", CARD)],
            foreground=[("disabled", TEXT_DISABLED)],
            bordercolor=[("disabled", BORDER), ("focus", ACCENT), ("hover", "#bdb3d6")],
            lightcolor=[("disabled", FIELD_DISABLED), ("focus", CARD)],
            darkcolor=[("disabled", FIELD_DISABLED), ("focus", CARD)],
            background=[("disabled", FIELD_DISABLED), ("active", HOVER), ("pressed", HOVER)],
            arrowcolor=[("disabled", TEXT_DISABLED)],
        )
        style.configure(f"Invalid.{widget}", bordercolor=DANGER, lightcolor=DANGER_SOFT)
        style.map(
            f"Invalid.{widget}",
            bordercolor=[("focus", DANGER), ("!disabled", DANGER)],
        )
        style.configure(f"Warn.{widget}", bordercolor=WARNING)
        style.map(f"Warn.{widget}", bordercolor=[("focus", WARNING), ("!disabled", WARNING)])
    style.map(
        "TCombobox",
        fieldbackground=[("disabled", FIELD_DISABLED), ("readonly", FIELD), ("focus", CARD)],
        selectbackground=[("readonly", FIELD), ("readonly", "focus", ACCENT_SOFT)],
        selectforeground=[("readonly", TEXT)],
    )
    style.configure("TSpinbox", arrowsize=px(11))

    for orient in ("Vertical", "Horizontal"):
        # Arrow-less scrollbar: only the trough and the thumb.
        thin_layout: Any = [
            (
                f"{orient}.Scrollbar.trough",
                {
                    "sticky": "ns" if orient == "Vertical" else "ew",
                    "children": [(f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})],
                },
            )
        ]
        style.layout(f"Thin.{orient}.TScrollbar", thin_layout)
        style.configure(
            f"Thin.{orient}.TScrollbar",
            troughcolor=CARD,
            background="#d9d3e6",
            bordercolor=CARD,
            lightcolor="#d9d3e6",
            darkcolor="#d9d3e6",
            gripcount=0,
            arrowsize=px(8),
        )
        style.map(
            f"Thin.{orient}.TScrollbar",
            background=[("pressed", ACCENT), ("active", "#b9aed6")],
            lightcolor=[("pressed", ACCENT), ("active", "#b9aed6")],
            darkcolor=[("pressed", ACCENT), ("active", "#b9aed6")],
        )
    style.configure(
        "Accent.Horizontal.TProgressbar",
        troughcolor=ACCENT_SOFT,
        background=ACCENT,
        bordercolor=ACCENT_SOFT,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=px(6),
    )
    style.configure(
        "Treeview",
        background=CARD,
        fieldbackground=CARD,
        foreground=TEXT,
        bordercolor=BORDER,
        rowheight=px(28),
    )

    root.option_add("*TCombobox*Listbox.font", font(10))
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.borderWidth", 0)
    root.option_add("*Menu.background", CARD)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", ACCENT_SOFT)
    root.option_add("*Menu.activeForeground", ACCENT_TEXT)
    root.option_add("*Menu.font", font(10))
    root.option_add("*Menu.borderWidth", 1)
    root.option_add("*Menu.relief", "flat")


def _solid_button(
    style: ttk.Style, name: str, base: str, hover: str, pressed: str, text: str
) -> None:
    style.configure(
        name,
        background=base,
        foreground=text,
        bordercolor=base,
        lightcolor=base,
        darkcolor=base,
        font=font(10, "bold"),
    )
    style.map(
        name,
        background=[("disabled", "#cfc8e3"), ("pressed", pressed), ("active", hover)],
        foreground=[("disabled", "#f6f4fb")],
        bordercolor=[("disabled", "#cfc8e3"), ("pressed", pressed), ("active", hover)],
        lightcolor=[("disabled", "#cfc8e3"), ("pressed", pressed), ("active", hover)],
        darkcolor=[("disabled", "#cfc8e3"), ("pressed", pressed), ("active", hover)],
    )


def style_listbox(listbox: tk.Listbox) -> None:
    listbox.configure(
        background=CARD,
        foreground=TEXT,
        selectbackground=ACCENT_SOFT_2,
        selectforeground=ACCENT_TEXT,
        highlightthickness=0,
        borderwidth=0,
        activestyle="none",
        font=font(10),
        relief="flat",
    )


def rounded_rect(
    canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs: Any
) -> int:
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = (
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    )
    return int(canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs))


# --- Backdrop rendering (Pillow is optional; without it the window is a flat color) ---


@dataclass(frozen=True)
class PanelSpec:
    box: tuple[int, int, int, int]
    kind: str  # "solid" | "frosted"


def open_image(path: Any) -> Any | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        image: Any = Image.open(path)
        image.load()
    except (OSError, ValueError):
        return None
    return image.convert("RGB")


def cover_image(image: Any, size: tuple[int, int], focus_y: float = 0.5) -> Any:
    from PIL import Image

    source_width, source_height = image.size
    target_width, target_height = max(size[0], 1), max(size[1], 1)
    ratio = max(target_width / source_width, target_height / source_height)
    resized = image.resize(
        (max(1, round(source_width * ratio)), max(1, round(source_height * ratio))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.size[0] - target_width) // 2)
    top = max(
        0, min(resized.size[1] - target_height, int(resized.size[1] * focus_y) - target_height // 2)
    )
    return resized.crop((left, top, left + target_width, top + target_height))


def render_backdrop(art: Any, size: tuple[int, int], panels: Sequence[PanelSpec]) -> Any:
    """Art + soft wash, frosted panels, and solid panels with a soft shadow."""
    from PIL import Image, ImageDraw, ImageFilter

    width, height = size
    base = cover_image(art, size, focus_y=0.42).filter(ImageFilter.GaussianBlur(px(2)))
    canvas = Image.alpha_composite(
        base.convert("RGBA"), Image.new("RGBA", size, (250, 247, 255, 70))
    )
    radius = px(PANEL_RADIUS)
    for panel in panels:
        x1, y1, x2, y2 = panel.box
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(panel.box, radius=radius, fill=255)
        if panel.kind == "frosted":
            frosted = base.filter(ImageFilter.GaussianBlur(px(22))).convert("RGBA")
            frosted = Image.alpha_composite(frosted, Image.new("RGBA", size, (255, 255, 255, 168)))
            canvas.paste(frosted, (0, 0), mask)
            ImageDraw.Draw(canvas).rounded_rectangle(
                panel.box, radius=radius, outline=(255, 255, 255, 230), width=1
            )
            continue
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (x1, y1 + px(4), x2, y2 + px(6)), radius=radius, fill=(62, 40, 96, 60)
        )
        canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(px(10))))
        ImageDraw.Draw(canvas).rounded_rectangle(panel.box, radius=radius, fill=CARD)
    return canvas.convert("RGB")


def rounded_image(image: Any, radius: int, background: str = CARD) -> Any:
    from PIL import Image, ImageDraw

    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *image.size), radius=radius, fill=255)
    result = Image.new("RGB", image.size, background)
    result.paste(image, (0, 0), mask)
    return result


def to_photo(image: Any | None) -> Any | None:
    if image is None:
        return None
    try:
        from PIL import ImageTk
    except ImportError:
        return None
    return cast(Any, ImageTk.PhotoImage)(image)
