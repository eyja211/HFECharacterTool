from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, cast

from hfe_character_tool.export import export_project
from hfe_character_tool.gui_presenter import (
    CharacterForm,
    ItemSlotForm,
    apply_form,
    disabled_item_label,
    focus_target,
    frame_item_edits_from_drafts,
    item_options_by_label,
    item_slots_from_forms,
    next_project_defaults,
    project_rows,
    project_to_form,
    render_export_result,
    render_validation_report,
    selected_item_label,
    texture_labels,
    validation_groups,
)
from hfe_character_tool.hfworkshop_catalog import (
    KEEP_SOURCE_TEXTURE_ROLE_ID,
    CharacterTemplateCatalogEntry,
    ItemCatalog,
    SptActionInfo,
    load_character_template_catalog,
    load_item_catalog,
    load_item_options,
    load_spt_action_options,
    load_spt_frame_item_slots,
    load_texture_parts,
    load_texture_roles,
    selected_texture_role_label,
    texture_role_ids_by_label,
)
from hfe_character_tool.models import (
    DEFAULT_TARGET_GAME,
    CharacterProject,
    ExportResult,
    ItemSpawnSlot,
    TargetGame,
    Template,
    ValidationIssue,
    ValidationReport,
    replace_project,
)
from hfe_character_tool.projects import create_project, load_project, save_project, scan_projects
from hfe_character_tool.randomizer import randomize_project
from hfe_character_tool.runtime import (
    default_target_cache_root,
    is_frozen_app,
    resource_path,
)
from hfe_character_tool.target_cache import (
    TargetCacheEntry,
    TargetCacheError,
    prepare_target_cache,
    target_game_for_source,
)
from hfe_character_tool.templates import list_templates
from hfe_character_tool.tools import check_tools, dependency_summary, discover_tools
from hfe_character_tool.validation import validate_editing

WORKSPACE_MARKERS = (
    Path("vendor") / "original_game" / "HFE v1.0.2.exe",
    Path("vendor") / "projector" / "SA.exe",
)

GLASS_EDGE = "#ffffff"
GLASS_WIDGET_BG = "#ebe7f2"
GLASS_CONTROL_BG = "#eee7f1"
GLASS_CONTROL_ACTIVE_BG = "#fbf7fd"
GLASS_TRACK_BG = "#e9f4ff"
GLASS_THUMB_BG = "#76b8ea"
TEXT_COLOR = "#34445d"
PANEL_MARGIN = 14
PANEL_GAP = 12
SIDEBAR_WIDTH = 280


@dataclass(frozen=True)
class GlassProfile:
    tint_alpha: int = 54
    blur_radius: int = 7
    border_alpha: int = 120
    inner_border_alpha: int = 38


@dataclass(frozen=True)
class NewProjectTemplateChoice:
    label: str
    template_id: str
    role_id: str


def target_template_choices(
    entries: tuple[CharacterTemplateCatalogEntry, ...],
    templates: tuple[Template, ...],
) -> tuple[NewProjectTemplateChoice, ...]:
    templates_by_id = {template.template_id: template for template in templates}
    lucas_template = templates_by_id.get("lucas-basic")
    if lucas_template is None:
        return ()
    choices: list[NewProjectTemplateChoice] = []
    for entry in entries:
        if entry.available:
            choices.append(
                NewProjectTemplateChoice(
                    label=entry.label,
                    template_id=lucas_template.template_id,
                    role_id=entry.role_id,
                )
            )
    return tuple(choices)


def select_template_label_after_refresh(
    current_label: str,
    old_choices: tuple[NewProjectTemplateChoice, ...],
    new_choices: tuple[NewProjectTemplateChoice, ...],
) -> str:
    old_choice = next((choice for choice in old_choices if choice.label == current_label), None)
    if old_choice is not None:
        for choice in new_choices:
            if choice.role_id == old_choice.role_id:
                return choice.label
    return new_choices[0].label if new_choices else ""


class GlassProjectList(tk.Canvas):
    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        super().__init__(
            master,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#d8e7f6",
            highlightcolor="#a9cbed",
            background=GLASS_WIDGET_BG,
            **kwargs,
        )
        self._items: list[str] = []
        self._selected_index: int | None = None
        self._row_height = 27
        self._scroll_top = 0
        self._scroll_drag_offset = 0
        self._scroll_dragging = False
        self._scrollbar_width = 18
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Configure>", lambda _event: self._redraw(), add="+")

    def delete(self, first: object, last: object | None = None) -> None:  # type: ignore[override]
        if first == 0 and last in {None, "end"}:
            self._items.clear()
            self._selected_index = None
            self._scroll_top = 0
            self._redraw()
            return
        if last is None:
            super().delete(cast(Any, first))
        else:
            super().delete(cast(Any, first), cast(Any, last))

    def insert(self, index: object, text: str) -> None:
        if index == "end":
            self._items.append(text)
        elif isinstance(index, int):
            self._items.insert(index, text)
        else:
            self._items.append(text)
        self._redraw()

    def curselection(self) -> tuple[int, ...]:
        return () if self._selected_index is None else (self._selected_index,)

    def _on_click(self, event: tk.Event[tk.Misc]) -> None:
        if self._scrollbar_is_visible() and event.x >= self.winfo_width() - self._scrollbar_width:
            top, bottom = self._scrollbar_bounds()
            self._scroll_dragging = True
            if top <= event.y <= bottom:
                self._scroll_drag_offset = event.y - top
                return
            self._scroll_drag_offset = (bottom - top) // 2
            self._move_scrollbar_to_event(event.y)
            return
        index = int((self._scroll_top + event.y) // self._row_height)
        if index < 0 or index >= len(self._items):
            return
        self._selected_index = index
        self._redraw()
        self.event_generate("<<ListboxSelect>>")

    def _on_drag(self, event: tk.Event[tk.Misc]) -> None:
        if not self._scroll_dragging:
            return
        self._move_scrollbar_to_event(event.y)

    def _on_release(self, _event: object) -> None:
        self._scroll_dragging = False

    def _on_mousewheel(self, event: tk.Event[tk.Misc]) -> None:
        if not self._scrollbar_is_visible():
            return
        units = -int(event.delta / 120)
        if units == 0:
            units = -1 if event.delta > 0 else 1
        self._set_scroll_top(self._scroll_top + units * self._row_height * 3)

    def _redraw(self) -> None:
        self.delete("project-list")
        self._clamp_scroll_top()
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        scrollbar_visible = self._scrollbar_is_visible()
        row_width = width - (self._scrollbar_width + 5 if scrollbar_visible else 5)
        first_row = max(int(self._scroll_top // self._row_height), 0)
        last_row = min(
            len(self._items),
            int((self._scroll_top + height) // self._row_height) + 2,
        )
        for index in range(first_row, last_row):
            text = self._items[index]
            top = index * self._row_height - self._scroll_top
            bottom = top + self._row_height
            if self._selected_index == index:
                _rounded_canvas_rect(
                    self,
                    5,
                    top + 3,
                    row_width,
                    bottom - 3,
                    radius=9,
                    fill="#f9fbff",
                    outline="#f8fcff",
                    width=1,
                    stipple="gray50",
                    tags=("project-list",),
                )
            self.create_text(
                10,
                top + self._row_height // 2,
                anchor="w",
                text=text,
                fill=TEXT_COLOR,
                font=("Microsoft YaHei UI", 10),
                tags=("project-list",),
            )
        self._draw_scrollbar(width, height)
        self.tag_raise("project-list")

    def _content_height(self) -> int:
        return len(self._items) * self._row_height

    def _max_scroll_top(self) -> int:
        return max(self._content_height() - max(self.winfo_height(), 1), 0)

    def _clamp_scroll_top(self) -> None:
        self._scroll_top = min(max(self._scroll_top, 0), self._max_scroll_top())

    def _set_scroll_top(self, value: int) -> None:
        self._scroll_top = value
        self._clamp_scroll_top()
        self._redraw()

    def _scrollbar_is_visible(self) -> bool:
        return self._max_scroll_top() > 0

    def _scrollbar_bounds(self) -> tuple[int, int]:
        height = max(self.winfo_height(), 1)
        content_height = max(self._content_height(), 1)
        view_fraction = min(height / content_height, 1.0)
        thumb_height = max(44, int(height * view_fraction))
        max_scroll = max(content_height - height, 1)
        top = int((height - thumb_height) * (self._scroll_top / max_scroll))
        return top, min(height, top + thumb_height)

    def _draw_scrollbar(self, width: int, height: int) -> None:
        if not self._scrollbar_is_visible():
            return
        track_left = width - 13
        track_right = width - 5
        _rounded_canvas_rect(
            self,
            track_left,
            8,
            track_right,
            height - 8,
            radius=5,
            fill=GLASS_TRACK_BG,
            outline="#f8fcff",
            width=1,
            stipple="gray50",
            tags=("project-list",),
        )
        top, bottom = self._scrollbar_bounds()
        _rounded_canvas_rect(
            self,
            track_left - 1,
            top + 2,
            track_right + 1,
            bottom - 2,
            radius=5,
            fill=GLASS_THUMB_BG,
            outline="#f8fcff",
            width=1,
            stipple="gray75",
            tags=("project-list",),
        )

    def _move_scrollbar_to_event(self, event_y: int) -> None:
        top, bottom = self._scrollbar_bounds()
        thumb_height = bottom - top
        travel = max(self.winfo_height() - thumb_height, 1)
        fraction = (event_y - self._scroll_drag_offset) / travel
        max_scroll = self._max_scroll_top()
        self._set_scroll_top(int(min(max(fraction, 0.0), 1.0) * max_scroll))


class GlassVerticalScrollbar(tk.Canvas):
    def __init__(self, master: tk.Misc, command: Any, **kwargs: Any) -> None:
        super().__init__(
            master,
            borderwidth=0,
            highlightthickness=0,
            background=GLASS_WIDGET_BG,
            cursor="sb_v_double_arrow",
            **kwargs,
        )
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._drag_offset = 0
        self.bind("<Configure>", lambda _event: self._redraw(), add="+")
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)

    def set(self, first: float | str, last: float | str) -> None:
        self._first = float(first)
        self._last = float(last)
        self._redraw()

    def _thumb_bounds(self) -> tuple[int, int]:
        height = max(self.winfo_height(), 1)
        span = max(self._last - self._first, 0.08)
        thumb_height = max(42, int(height * span))
        top = int((height - thumb_height) * self._first)
        return top, min(height, top + thumb_height)

    def _redraw(self) -> None:
        self.delete("scrollbar")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        center = width // 2
        self.create_line(
            center,
            10,
            center,
            height - 10,
            fill=GLASS_TRACK_BG,
            width=8,
            stipple="gray50",
            tags=("scrollbar",),
        )
        top, bottom = self._thumb_bounds()
        self.create_rectangle(
            center - 5,
            top + 2,
            center + 5,
            bottom - 2,
            fill=GLASS_THUMB_BG,
            outline="#f8fcff",
            width=1,
            stipple="gray75",
            tags=("scrollbar",),
        )

    def _on_press(self, event: tk.Event[tk.Misc]) -> None:
        top, bottom = self._thumb_bounds()
        if top <= event.y <= bottom:
            self._drag_offset = event.y - top
            return
        self._drag_offset = (bottom - top) // 2
        self._move_to_event(event.y)

    def _on_drag(self, event: tk.Event[tk.Misc]) -> None:
        self._move_to_event(event.y)

    def _move_to_event(self, event_y: int) -> None:
        top, bottom = self._thumb_bounds()
        thumb_height = bottom - top
        travel = max(self.winfo_height() - thumb_height, 1)
        fraction = (event_y - self._drag_offset) / travel
        fraction = min(max(fraction, 0.0), 1.0)
        self._command("moveto", fraction)


class GlassButton(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Any,
        height: int = 42,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            height=height,
            borderwidth=0,
            highlightthickness=0,
            background=GLASS_WIDGET_BG,
            cursor="hand2",
            **kwargs,
        )
        self._text = text
        self._command = command
        self._selected = False
        self._hover = False
        self.bind("<Configure>", lambda _event: self._redraw(), add="+")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._redraw()

    def _on_enter(self, _event: object) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event: object) -> None:
        self._hover = False
        self._redraw()

    def _on_click(self, _event: object) -> None:
        self._command()

    def _redraw(self) -> None:
        self.delete("button")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        fill = GLASS_CONTROL_ACTIVE_BG if self._selected else "#f7f1fb"
        stipple = "gray75" if self._selected else "gray50"
        if self._hover:
            fill = "#fffdfd"
            stipple = "gray75"
        outline = "#f8fcff" if self._selected or self._hover else "#dcecff"
        _rounded_canvas_rect(
            self,
            1,
            1,
            width - 2,
            height - 2,
            radius=13,
            fill=fill,
            outline=outline,
            width=1,
            stipple=stipple,
            tags=("button",),
        )
        self.create_text(
            width // 2,
            height // 2,
            text=self._text,
            fill="#40536d",
            font=("Microsoft YaHei UI", 10),
            tags=("button",),
        )


class HfeCharacterApp(tk.Tk):
    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace
        self.projects_root = workspace / "projects"
        self.current_project_dir: Path | None = None
        self.texture_parts = load_texture_parts(workspace)
        self.texture_roles = load_texture_roles(workspace)
        self.item_options = load_item_options(workspace)
        self.spt_actions = load_spt_action_options(workspace)
        self.action_by_name = {action.action_name: action for action in self.spt_actions}
        self.action_by_label: dict[str, str] = {}
        self.frame_by_label: dict[str, int] = {}
        self._target_cache_entries: dict[tuple[str, str], TargetCacheEntry] = {}
        self._current_source_role_id = "lucas"
        self._current_target_cache: TargetCacheEntry | None = None
        self.item_frame_drafts: dict[tuple[str, int], tuple[ItemSpawnSlot, ...]] = {}
        self._current_item_frame_key: tuple[str, int] | None = None
        self._current_item_base_slot_count = 0
        self._dirty_item_frame_keys: set[tuple[str, int]] = set()
        self._loading_item_slots = False
        self._export_queue: queue.Queue[tuple[str, ExportResult | BaseException]] | None = None
        self._export_thread: threading.Thread | None = None
        self.project_control_widgets: list[tk.Widget] = []
        self._project_controls_enabled = False
        self.texture_vars: dict[str, tk.StringVar] = {}
        self.item_slot_widgets: list[dict[str, tk.Widget]] = []
        self._glass_canvases: list[tk.Canvas] = []
        self._glass_canvas_profiles: dict[tk.Canvas, GlassProfile] = {}
        self._glass_canvas_images: dict[tk.Canvas, object] = {}
        self._glass_canvas_items: dict[tk.Canvas, int] = {}
        self._surface_background_item: int | None = None
        self._left_window: int | None = None
        self._main_window: int | None = None
        self._layout_refresh_pending = False
        self._tab_buttons: dict[tk.Canvas, GlassButton] = {}
        self._active_tab: tk.Canvas | None = None
        self.title("HFE 角色定制工具")
        self.geometry("980x640")
        self.minsize(900, 600)
        self._background_photo: object | None = None
        self._background_source: object | None = None
        self._background_rendered: object | None = None
        self._icon_photo: object | None = None
        self._configure_style()
        self._load_window_icon()
        self._build()
        self.refresh_projects()
        self._set_project_controls_enabled(False)

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.surface = tk.Canvas(self, highlightthickness=0, borderwidth=0, background="#d8ecff")
        self.surface.grid(row=0, column=0, sticky="nsew")
        self._install_background()
        left = self._glass_canvas(self.surface, profile=GlassProfile(tint_alpha=34))
        self._left_window = self.surface.create_window(
            PANEL_MARGIN,
            PANEL_MARGIN,
            anchor="nw",
            window=left,
            width=SIDEBAR_WIDTH,
            height=640,
        )
        self._make_glass_button(left, text="刷新项目", command=self.refresh_projects).pack(
            fill="x", padx=14, pady=(16, 0)
        )
        self._make_glass_button(left, text="新建角色", command=self.new_project).pack(
            fill="x", padx=14, pady=(8, 0)
        )
        self._make_glass_button(left, text="打开项目", command=self.open_project).pack(
            fill="x", padx=14, pady=(8, 0)
        )
        self.project_list = GlassProjectList(left)
        self._register_glass_canvas(
            self.project_list,
            GlassProfile(tint_alpha=8, blur_radius=4, border_alpha=58, inner_border_alpha=14),
        )
        self.project_list.pack(fill="both", expand=True, padx=14, pady=(12, 16))
        self.project_list.bind("<<ListboxSelect>>", self.select_project)

        self.notebook = self._glass_canvas(self.surface, profile=GlassProfile(tint_alpha=32))
        self._main_window = self.surface.create_window(
            PANEL_MARGIN + SIDEBAR_WIDTH + PANEL_GAP,
            PANEL_MARGIN,
            anchor="nw",
            window=self.notebook,
            width=720,
            height=640,
        )
        page_profile = GlassProfile(
            tint_alpha=12,
            blur_radius=4,
            border_alpha=74,
            inner_border_alpha=18,
        )
        self.edit_tab = self._glass_canvas(self.notebook, profile=page_profile)
        self.asset_tab = self._glass_canvas(self.notebook, profile=page_profile)
        self.export_tab = self._glass_canvas(self.notebook, profile=page_profile)
        self._add_tab(self.edit_tab, "编辑角色", 0)
        self._add_tab(self.asset_tab, "贴图与技能", 1)
        self._add_tab(self.export_tab, "校验导出", 2)
        self._build_edit_tab()
        self._build_asset_tab()
        self._build_export_tab()
        self._select_tab(self.edit_tab)
        self.surface.bind("<Configure>", self._refresh_background)
        self.after_idle(self._refresh_background)

    def _build_edit_tab(self) -> None:
        self.id_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.name_zh_var = tk.StringVar()
        self.desc_var = tk.StringVar()
        self.hp_var = tk.StringVar()
        self.mp_var = tk.StringVar()
        self.defense_var = tk.StringVar()
        self.skill_key_var = tk.StringVar()
        self.skill_mp_var = tk.StringVar()
        self.skill_damage_var = tk.StringVar()
        self.skill_speed_var = tk.StringVar()
        self.skill_range_var = tk.StringVar()
        self.target_source_var = tk.StringVar()
        self.target_kind_var = tk.StringVar()
        self.item_action_var = tk.StringVar()
        self.item_frame_var = tk.StringVar()
        self.item_slot_vars: list[dict[str, tk.StringVar]] = []
        self.target_widgets: dict[str, tk.Widget] = {}
        fields = (
            ("角色 ID（按此 ID 导出）", self.id_var, "character.id"),
            ("英文名", self.name_var, "character.name"),
            ("中文名", self.name_zh_var, "character.name_zh"),
            ("角色说明", self.desc_var, "character.description"),
            ("目标游戏 EXE/SWF", self.target_source_var, "target_game.source_path"),
            ("目标类型", self.target_kind_var, "target_game.source_kind"),
            ("HP", self.hp_var, "stats.hp"),
            ("MP", self.mp_var, "stats.mp"),
            ("防御值", self.defense_var, "stats.defense"),
        )
        for row, (label, var, target) in enumerate(fields):
            ttk.Label(self.edit_tab, text=label).grid(
                row=row,
                column=0,
                sticky="w",
                padx=(24, 12),
                pady=((22 if row == 0 else 5), 5),
            )
            entry = ttk.Entry(self.edit_tab, textvariable=var, width=48)
            entry.grid(
                row=row,
                column=1,
                sticky="ew",
                padx=(0, 24),
                pady=((22 if row == 0 else 5), 5),
            )
            self.target_widgets[target] = entry
            self.project_control_widgets.append(entry)
        target_button = ttk.Button(
            self.edit_tab,
            text="选择目标",
            command=self.choose_target_game,
        )
        target_button.grid(row=4, column=2, sticky="w", padx=(0, 24), pady=(5, 5))
        self.project_control_widgets.append(target_button)
        save_button = ttk.Button(self.edit_tab, text="保存并校验", command=self.save_current)
        save_button.grid(
            row=len(fields), column=1, sticky="w", padx=(0, 24), pady=(18, 0)
        )
        self.project_control_widgets.append(save_button)
        random_button = ttk.Button(
            self.edit_tab,
            text="随机生成角色",
            command=self.randomize_current_project,
        )
        random_button.grid(
            row=len(fields), column=1, sticky="e", padx=(0, 24), pady=(18, 0)
        )
        self.project_control_widgets.append(random_button)
        self.edit_tab.columnconfigure(1, weight=1)

    def _glass_canvas(
        self,
        master: tk.Misc,
        profile: GlassProfile | None = None,
    ) -> tk.Canvas:
        canvas = tk.Canvas(
            master,
            borderwidth=0,
            highlightthickness=0,
            background=GLASS_WIDGET_BG,
        )
        self._register_glass_canvas(canvas, profile or GlassProfile())
        return canvas

    def _make_glass_button(
        self,
        master: tk.Misc,
        text: str,
        command: Any,
        height: int = 42,
    ) -> GlassButton:
        button = GlassButton(master, text=text, command=command, height=height)
        self._register_glass_canvas(
            button,
            GlassProfile(tint_alpha=8, blur_radius=4, border_alpha=58, inner_border_alpha=14),
        )
        return button

    def _register_glass_canvas(self, canvas: tk.Canvas, profile: GlassProfile) -> None:
        if canvas not in self._glass_canvases:
            self._glass_canvases.append(canvas)
        self._glass_canvas_profiles[canvas] = profile
        canvas.bind("<Configure>", self._queue_glass_refresh, add="+")

    def _add_tab(self, page: tk.Canvas, text: str, index: int) -> None:
        def select_page(target: tk.Canvas = page) -> None:
            self._select_tab(target)

        button = self._make_glass_button(
            self.notebook,
            text=text,
            command=select_page,
            height=38,
        )
        button.place(x=24 + index * 122, y=18, width=112, height=38)
        page.place(x=18, y=68, relwidth=1, width=-36, relheight=1, height=-86)
        self._tab_buttons[page] = button

    def _select_tab(self, page: tk.Canvas) -> None:
        self._active_tab = page
        page.tk.call("raise", cast(Any, page)._w)
        for tab_page, button in self._tab_buttons.items():
            selected = tab_page is page
            button.set_selected(selected)
        self._queue_glass_refresh()

    def _build_asset_scrollable_content(self) -> tk.Frame:
        scroll_canvas = self._glass_canvas(
            self.asset_tab,
            profile=GlassProfile(
                tint_alpha=6,
                blur_radius=3,
                border_alpha=42,
                inner_border_alpha=8,
            ),
        )
        scrollbar = GlassVerticalScrollbar(
            self.asset_tab,
            command=scroll_canvas.yview,
            width=16,
        )
        self._register_glass_canvas(
            scrollbar,
            GlassProfile(tint_alpha=12, blur_radius=3, border_alpha=34, inner_border_alpha=8),
        )
        scroll_canvas.place(x=18, y=18, relwidth=1, width=-54, relheight=1, height=-36)
        scrollbar.place(relx=1, x=-32, y=18, width=16, relheight=1, height=-36)
        scrollbar.tk.call("raise", cast(Any, scrollbar)._w)
        scroll_canvas.configure(yscrollcommand=self._update_asset_scrollbar)
        content = tk.Frame(scroll_canvas, background=GLASS_WIDGET_BG)
        self.asset_scroll_canvas = scroll_canvas
        self.asset_scroll_content = content
        self.asset_native_scrollbar = scrollbar
        self.asset_scroll_window = scroll_canvas.create_window(0, 0, anchor="nw", window=content)
        self._asset_scroll_first = 0.0
        self._asset_scroll_last = 1.0
        self._asset_scroll_drag_offset = 0
        self.asset_scrollbar_track = tk.Frame(
            scroll_canvas,
            background=GLASS_TRACK_BG,
            cursor="sb_v_double_arrow",
        )
        self.asset_scrollbar_thumb = tk.Frame(
            scroll_canvas,
            background=GLASS_THUMB_BG,
            cursor="sb_v_double_arrow",
        )
        self.asset_scrollbar_track.bind("<Button-1>", self._on_asset_scrollbar_track_press)
        self.asset_scrollbar_thumb.bind("<Button-1>", self._on_asset_scrollbar_thumb_press)
        self.asset_scrollbar_track.bind("<B1-Motion>", self._on_asset_scrollbar_widget_drag)
        self.asset_scrollbar_thumb.bind("<B1-Motion>", self._on_asset_scrollbar_widget_drag)
        content.bind("<Configure>", self._refresh_asset_scrollregion)
        scroll_canvas.bind("<Configure>", self._sync_asset_content_width, add="+")
        scroll_canvas.bind("<Button-1>", self._on_asset_scrollbar_press, add="+")
        scroll_canvas.bind("<B1-Motion>", self._on_asset_scrollbar_drag, add="+")
        for widget in (scroll_canvas, content):
            widget.bind("<Enter>", self._bind_asset_mousewheel, add="+")
            widget.bind("<Leave>", self._unbind_asset_mousewheel, add="+")
        return content

    def _build_asset_tab(self) -> None:
        content = self._build_asset_scrollable_content()
        content.columnconfigure(1, weight=0)
        self.item_catalog_status_var = tk.StringVar(value="道具目录：等待加载项目")
        ttk.Label(content, text="技能动作").grid(
            row=0, column=0, sticky="w", padx=(24, 12), pady=(22, 6)
        )
        action_combo = ttk.Combobox(
            content,
            textvariable=self.item_action_var,
            values=(),
            state="readonly",
            width=28,
        )
        action_combo.bind("<<ComboboxSelected>>", self._on_action_selected)
        action_combo.grid(
            row=0,
            column=1,
            columnspan=3,
            sticky="w",
            padx=(0, 24),
            pady=(22, 6),
        )
        self.action_combo = action_combo
        self.target_widgets["item_frame_edits.0.action_name"] = action_combo
        self.project_control_widgets.append(action_combo)
        ttk.Label(content, textvariable=self.item_catalog_status_var).grid(
            row=0,
            column=4,
            columnspan=4,
            sticky="w",
            padx=(0, 24),
            pady=(22, 6),
        )

        ttk.Label(content, text="动作内帧").grid(
            row=1, column=0, sticky="w", padx=(24, 12), pady=(0, 12)
        )
        frame = ttk.Combobox(
            content,
            textvariable=self.item_frame_var,
            values=(),
            state="readonly",
            width=28,
        )
        frame.bind("<<ComboboxSelected>>", self._on_frame_selected)
        frame.grid(row=1, column=1, columnspan=7, sticky="w", pady=(0, 12))
        self.frame_combo = frame
        self.target_widgets["item_frame_edits.0.action_frame"] = frame
        self.project_control_widgets.append(frame)

        item_labels = (disabled_item_label(), *(option.label for option in self.item_options))
        headers = ("槽", "道具", "x", "y", "z")
        for column, header in enumerate(headers):
            ttk.Label(content, text=header).grid(
                row=2,
                column=column,
                sticky="w",
                padx=((24 if column == 0 else 0), 4),
                pady=(4, 2),
            )
        for column, header in enumerate(("", "", "vx", "vy", "vz")):
            ttk.Label(content, text=header).grid(
                row=3,
                column=column,
                sticky="w",
                padx=((24 if column == 0 else 0), 4),
                pady=(0, 2),
            )
        for row_index in range(3):
            row = 4 + row_index * 2
            vars_for_slot = {
                "item": tk.StringVar(value=disabled_item_label()),
                "x": tk.StringVar(),
                "y": tk.StringVar(),
                "z": tk.StringVar(),
                "vx": tk.StringVar(),
                "vy": tk.StringVar(),
                "vz": tk.StringVar(),
            }
            for slot_var in vars_for_slot.values():
                slot_var.trace_add("write", self._mark_current_item_frame_dirty)
            self.item_slot_vars.append(vars_for_slot)
            widgets_for_slot: dict[str, tk.Widget] = {}
            ttk.Label(content, text=str(row_index + 1)).grid(
                row=row, column=0, rowspan=2, sticky="n", padx=(24, 4), pady=(4, 0)
            )
            combo = ttk.Combobox(
                content,
                textvariable=vars_for_slot["item"],
                values=item_labels,
                state="readonly",
                width=13,
            )
            combo.grid(row=row, column=1, rowspan=2, sticky="nw", padx=(0, 4), pady=(2, 0))
            widgets_for_slot["item"] = combo
            self.target_widgets[f"item_frame_edits.0.slots.{row_index}.item_action_group"] = combo
            self.project_control_widgets.append(combo)
            for column, field in enumerate(("x", "y", "z"), start=2):
                entry = ttk.Entry(content, textvariable=vars_for_slot[field], width=5)
                entry.grid(row=row, column=column, sticky="w", padx=(0, 4), pady=2)
                widgets_for_slot[field] = entry
                self.target_widgets[f"item_frame_edits.0.slots.{row_index}.{field}"] = entry
                self.project_control_widgets.append(entry)
            for column, field in enumerate(("vx", "vy", "vz"), start=2):
                entry = ttk.Entry(content, textvariable=vars_for_slot[field], width=5)
                entry.grid(row=row + 1, column=column, sticky="w", padx=(0, 4), pady=2)
                widgets_for_slot[field] = entry
                self.target_widgets[f"item_frame_edits.0.slots.{row_index}.{field}"] = entry
                self.project_control_widgets.append(entry)
            self.item_slot_widgets.append(widgets_for_slot)

        ttk.Separator(content).grid(
            row=10, column=0, columnspan=5, sticky="ew", padx=24, pady=14
        )
        ttk.Label(content, text="部位贴图").grid(
            row=11, column=0, sticky="w", padx=(24, 12), pady=(0, 8)
        )
        labels = texture_labels(self.texture_parts)
        for offset, part in enumerate(self.texture_parts, start=12):
            var = tk.StringVar()
            self.texture_vars[part.part_id] = var
            ttk.Label(content, text=labels[part.part_id]).grid(
                row=offset, column=0, sticky="w", padx=(24, 12), pady=4
            )
            combo = ttk.Combobox(
                content,
                textvariable=var,
                values=tuple(role.label for role in self.texture_roles),
                state="readonly",
                width=28,
            )
            combo.grid(row=offset, column=1, columnspan=3, sticky="w", padx=(0, 24), pady=4)
            self.target_widgets[f"textures.{part.part_id}"] = combo
            self.project_control_widgets.append(combo)
        tk.Frame(content, height=14, background=GLASS_WIDGET_BG).grid(
            row=12 + len(self.texture_parts),
            column=0,
            columnspan=5,
            sticky="ew",
        )

    def _build_export_tab(self) -> None:
        export_button = ttk.Button(
            self.export_tab, text="执行完整校验并导出", command=self.export_current
        )
        export_button.pack(anchor="w", padx=24, pady=(22, 0))
        self.export_button = export_button
        self.project_control_widgets.append(export_button)
        self.export_status_var = tk.StringVar(value="等待导出。")
        self.export_progress = ttk.Progressbar(
            self.export_tab,
            mode="indeterminate",
            maximum=100,
        )
        self.export_progress.pack(fill="x", padx=24, pady=(10, 0))
        ttk.Label(self.export_tab, textvariable=self.export_status_var).pack(
            anchor="w", padx=24, pady=(6, 0)
        )
        self.validation_tree = ttk.Treeview(
            self.export_tab,
            columns=("target", "suggestion"),
            show="tree headings",
            height=9,
        )
        self.validation_tree.heading("#0", text="问题")
        self.validation_tree.heading("target", text="定位")
        self.validation_tree.heading("suggestion", text="修复建议")
        self.validation_tree.column("#0", width=260, anchor="w")
        self.validation_tree.column("target", width=180, anchor="w")
        self.validation_tree.column("suggestion", width=320, anchor="w")
        self.validation_tree.pack(fill="x", padx=24, pady=(12, 0))
        self.validation_tree.bind("<<TreeviewSelect>>", self.focus_validation_selection)
        self._validation_issue_by_iid: dict[str, ValidationIssue] = {}
        self.result_text = tk.Text(
            self.export_tab,
            height=14,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground="#e9f4ff",
            highlightcolor="#9fc6ed",
            background=GLASS_CONTROL_BG,
            foreground=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
        )
        self.result_text.pack(fill="both", expand=True, padx=24, pady=(12, 24))

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        with suppress(tk.TclError):
            style.theme_use("clam")
        self.configure(bg="#d8ecff")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Glass.TFrame", background=GLASS_WIDGET_BG, borderwidth=0)
        style.configure("TFrame", background=GLASS_WIDGET_BG)
        style.configure("TLabel", background=GLASS_WIDGET_BG, foreground=TEXT_COLOR)
        style.configure("TSeparator", background="#d5e7f7")
        style.configure(
            "TNotebook",
            background=GLASS_WIDGET_BG,
            borderwidth=0,
            tabmargins=(8, 6, 8, 0),
        )
        style.configure(
            "TNotebook.Tab",
            padding=(18, 9),
            background=GLASS_CONTROL_BG,
            foreground="#53647c",
            borderwidth=0,
        )
        style.map("TNotebook.Tab", background=[("selected", GLASS_CONTROL_ACTIVE_BG)])
        style.configure(
            "TButton",
            padding=(12, 8),
            background=GLASS_CONTROL_BG,
            foreground="#43536d",
            borderwidth=0,
            focusthickness=1,
            focuscolor="#b7d8f8",
        )
        style.map(
            "TButton",
            background=[
                ("active", GLASS_CONTROL_ACTIVE_BG),
                ("pressed", GLASS_TRACK_BG),
                ("disabled", "#e4e0ea"),
            ],
            foreground=[("disabled", "#9aa5b5")],
        )
        style.configure(
            "TEntry",
            fieldbackground=GLASS_CONTROL_BG,
            foreground="#2f3a4f",
            bordercolor="#c9ddf2",
            lightcolor=GLASS_CONTROL_BG,
            darkcolor="#c9ddf2",
            insertcolor="#2f3a4f",
            padding=(6, 5),
        )
        style.map(
            "TEntry",
            fieldbackground=[
                ("disabled", "#e3dfe8"),
                ("readonly", GLASS_CONTROL_BG),
            ],
        )
        style.configure(
            "TCombobox",
            fieldbackground=GLASS_CONTROL_BG,
            background=GLASS_TRACK_BG,
            foreground="#2f3a4f",
            arrowcolor="#6c97be",
            bordercolor="#c9ddf2",
            lightcolor=GLASS_CONTROL_BG,
            darkcolor="#c9ddf2",
            padding=(6, 5),
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", GLASS_CONTROL_BG),
                ("disabled", "#e3dfe8"),
            ],
            background=[
                ("active", GLASS_CONTROL_ACTIVE_BG),
                ("pressed", GLASS_TRACK_BG),
                ("disabled", "#e3dfe8"),
            ],
        )
        style.configure(
            "Treeview",
            background=GLASS_CONTROL_BG,
            fieldbackground=GLASS_CONTROL_BG,
            foreground=TEXT_COLOR,
            borderwidth=0,
            relief="flat",
            rowheight=26,
        )
        style.configure(
            "Treeview.Heading",
            background=GLASS_TRACK_BG,
            foreground="#4d5c72",
            borderwidth=0,
            relief="flat",
        )

    def _install_background(self) -> None:
        background_path = resource_path(self.workspace, Path("底图.jpg"))
        source = _open_image(background_path)
        if source is None:
            return
        self._background_source = source
        self._surface_background_item = self.surface.create_image(0, 0, anchor="nw")
        self.surface.tag_lower(self._surface_background_item)

    def _refresh_background(self, _event: object | None = None) -> None:
        width = max(self.surface.winfo_width(), 1)
        height = max(self.surface.winfo_height(), 1)
        self._refresh_layout(width, height)
        if self._background_source is None or self._surface_background_item is None:
            return
        rendered = _cover_image(self._background_source, (width, height))
        photo = _image_to_photo(rendered)
        if rendered is None or photo is None:
            return
        self._background_rendered = rendered
        self._background_photo = photo
        self.surface.itemconfigure(self._surface_background_item, image=photo)
        self.surface.tag_lower(self._surface_background_item)
        self._queue_glass_refresh()

    def _refresh_layout(self, width: int, height: int) -> None:
        panel_height = max(1, height - PANEL_MARGIN * 2)
        main_x = PANEL_MARGIN + SIDEBAR_WIDTH + PANEL_GAP
        main_width = max(1, width - main_x - PANEL_MARGIN)
        if self._left_window is not None:
            self.surface.coords(self._left_window, PANEL_MARGIN, PANEL_MARGIN)
            self.surface.itemconfigure(
                self._left_window, width=SIDEBAR_WIDTH, height=panel_height
            )
        if self._main_window is not None:
            self.surface.coords(self._main_window, main_x, PANEL_MARGIN)
            self.surface.itemconfigure(self._main_window, width=main_width, height=panel_height)

    def _refresh_asset_scrollregion(self, _event: object | None = None) -> None:
        canvas = getattr(self, "asset_scroll_canvas", None)
        if canvas is None:
            return
        canvas.configure(scrollregion=canvas.bbox("all"))
        self._draw_asset_scrollbar()

    def _sync_asset_content_width(self, event: tk.Event[tk.Misc]) -> None:
        canvas = getattr(self, "asset_scroll_canvas", None)
        window = getattr(self, "asset_scroll_window", None)
        if canvas is None or window is None:
            return
        usable_width = max(event.width - 54, 1)
        canvas.itemconfigure(window, width=min(usable_width, 600))
        self._draw_asset_scrollbar()

    def _update_asset_scrollbar(self, first: float | str, last: float | str) -> None:
        self._asset_scroll_first = float(first)
        self._asset_scroll_last = float(last)
        scrollbar = getattr(self, "asset_native_scrollbar", None)
        if scrollbar is not None:
            scrollbar.set(first, last)
        self._draw_asset_scrollbar()

    def _draw_asset_scrollbar(self) -> None:
        track = getattr(self, "asset_scrollbar_track", None)
        thumb = getattr(self, "asset_scrollbar_thumb", None)
        if track is not None:
            track.place_forget()
        if thumb is not None:
            thumb.place_forget()

    def _asset_scrollbar_bounds(self) -> tuple[int, int]:
        canvas = getattr(self, "asset_scroll_canvas", None)
        if canvas is None:
            return 0, 1
        height = max(canvas.winfo_height(), 1)
        span = max(self._asset_scroll_last - self._asset_scroll_first, 0.08)
        thumb_height = max(42, int(height * span))
        top = int((height - thumb_height) * self._asset_scroll_first)
        return top, min(height, top + thumb_height)

    def _on_asset_scrollbar_press(self, event: tk.Event[tk.Misc]) -> None:
        canvas = getattr(self, "asset_scroll_canvas", None)
        if canvas is None or event.x < canvas.winfo_width() - 50:
            return
        top, bottom = self._asset_scrollbar_bounds()
        if top <= event.y <= bottom:
            self._asset_scroll_drag_offset = event.y - top
            return
        self._asset_scroll_drag_offset = (bottom - top) // 2
        self._move_asset_scrollbar_to_event(event.y)

    def _on_asset_scrollbar_drag(self, event: tk.Event[tk.Misc]) -> None:
        canvas = getattr(self, "asset_scroll_canvas", None)
        if canvas is None or event.x < canvas.winfo_width() - 58:
            return
        self._move_asset_scrollbar_to_event(event.y)

    def _on_asset_scrollbar_track_press(self, event: tk.Event[tk.Misc]) -> None:
        top, bottom = self._asset_scrollbar_bounds()
        self._asset_scroll_drag_offset = (bottom - top) // 2
        self._move_asset_scrollbar_to_event(self._asset_canvas_y(event))

    def _on_asset_scrollbar_thumb_press(self, event: tk.Event[tk.Misc]) -> None:
        self._asset_scroll_drag_offset = event.y

    def _on_asset_scrollbar_widget_drag(self, event: tk.Event[tk.Misc]) -> None:
        self._move_asset_scrollbar_to_event(self._asset_canvas_y(event))

    def _asset_canvas_y(self, event: tk.Event[tk.Misc]) -> int:
        canvas = getattr(self, "asset_scroll_canvas", None)
        if canvas is None:
            return 0
        return int(event.y_root - canvas.winfo_rooty())

    def _move_asset_scrollbar_to_event(self, event_y: int) -> None:
        canvas = getattr(self, "asset_scroll_canvas", None)
        if canvas is None:
            return
        top, bottom = self._asset_scrollbar_bounds()
        thumb_height = bottom - top
        travel = max(canvas.winfo_height() - thumb_height, 1)
        fraction = (event_y - self._asset_scroll_drag_offset) / travel
        canvas.yview_moveto(min(max(fraction, 0.0), 1.0))

    def _bind_asset_mousewheel(self, _event: object | None = None) -> None:
        self.bind_all("<MouseWheel>", self._on_asset_mousewheel, add="+")

    def _unbind_asset_mousewheel(self, _event: object | None = None) -> None:
        self.unbind_all("<MouseWheel>")

    def _on_asset_mousewheel(self, event: tk.Event[tk.Misc]) -> None:
        canvas = getattr(self, "asset_scroll_canvas", None)
        if canvas is None or self._active_tab is not self.asset_tab:
            return
        delta = -1 * int(event.delta / 120)
        canvas.yview_scroll(delta, "units")

    def _queue_glass_refresh(self, _event: object | None = None) -> None:
        if self._layout_refresh_pending:
            return
        self._layout_refresh_pending = True
        self.after_idle(self._refresh_glass_canvases)

    def _refresh_glass_canvases(self) -> None:
        self._layout_refresh_pending = False
        if self._background_source is None:
            return
        background = self._background_rendered or self._background_source
        root_x = self.winfo_rootx()
        root_y = self.winfo_rooty()
        for canvas in self._glass_canvases:
            if not canvas.winfo_exists():
                continue
            width = max(canvas.winfo_width(), 1)
            height = max(canvas.winfo_height(), 1)
            x = max(canvas.winfo_rootx() - root_x, 0)
            y = max(canvas.winfo_rooty() - root_y, 0)
            profile = self._glass_canvas_profiles.get(canvas, GlassProfile())
            image = _glass_panel_image(background, (width, height), (x, y), profile)
            photo = _image_to_photo(image)
            if photo is None:
                continue
            item = self._glass_canvas_items.get(canvas)
            if item is None:
                item = canvas.create_image(0, 0, anchor="nw")
                self._glass_canvas_items[canvas] = item
            canvas.itemconfigure(item, image=photo)
            canvas.tag_lower(item)
            self._glass_canvas_images[canvas] = photo

    def _load_window_icon(self) -> None:
        photo = _image_to_photo(
            _open_image(resource_path(self.workspace, Path("图标.jpg"))),
            (64, 64),
        )
        if photo is None:
            return
        self._icon_photo = photo
        self.iconphoto(True, photo)

    def _set_project_controls_enabled(self, enabled: bool) -> None:
        self._project_controls_enabled = enabled
        for widget in self.project_control_widgets:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if enabled else "disabled")
            else:
                cast(Any, widget).configure(state="normal" if enabled else "disabled")
        self._sync_item_slot_controls_state()

    def refresh_frame_options(
        self,
        _event: object | None = None,
        preferred_frame: int | None = None,
    ) -> None:
        action_name = self.action_by_label.get(
            self.item_action_var.get(),
            self.item_action_var.get(),
        )
        action = self.action_by_name.get(action_name)
        if action is None:
            self.frame_by_label = {}
            self.frame_combo.configure(values=())
            self.item_frame_var.set("")
            self._current_item_base_slot_count = 0
            self._sync_item_slot_controls_state()
            return
        selected = preferred_frame
        if selected is None:
            selected = self.frame_by_label.get(self.item_frame_var.get())
        self.frame_by_label = {
            self._frame_label(action, frame): frame
            for frame in range(1, action.frame_count + 1)
        }
        self.frame_combo.configure(values=tuple(self.frame_by_label))
        if selected is None or selected < 1 or selected > action.frame_count:
            selected = action.item_frames[0] if action.item_frames else 1
        self.item_frame_var.set(self._frame_label(action, selected))

    def _on_action_selected(self, event: object | None = None) -> None:
        self._cache_current_item_slots()
        self.refresh_frame_options(event)
        self.refresh_item_slots_for_selected_frame()

    def _on_frame_selected(self, _event: object | None = None) -> None:
        self._cache_current_item_slots()
        self.refresh_item_slots_for_selected_frame()

    def refresh_item_slots_for_selected_frame(self, _event: object | None = None) -> None:
        action_name = self.action_by_label.get(
            self.item_action_var.get(),
            self.item_action_var.get(),
        )
        action_frame = self.frame_by_label.get(self.item_frame_var.get(), 0)
        if not action_name or action_frame < 1:
            self._current_item_frame_key = None
            self._current_item_base_slot_count = 0
            self._set_item_slots_from_slots(())
            return
        key = (action_name, action_frame)
        base_slots = self._base_item_slots(action_name, action_frame)
        self._current_item_base_slot_count = len(self.item_slot_vars)
        draft_slots = self.item_frame_drafts.get(key)
        if draft_slots is not None:
            compatible = self._compatible_item_slots(base_slots, draft_slots)
            if compatible is not None:
                self._current_item_frame_key = key
                self._set_item_slots_from_slots(compatible)
                return
            self.item_frame_drafts.pop(key, None)
            self._dirty_item_frame_keys.discard(key)
        project_slots = self._project_item_slots(action_name, action_frame)
        if project_slots is not None:
            compatible = self._compatible_item_slots(base_slots, project_slots)
            if compatible is not None:
                self._current_item_frame_key = key
                self._set_item_slots_from_slots(compatible)
                return
        self._current_item_frame_key = key
        self._set_item_slots_from_slots(base_slots)

    def _project_item_slots(
        self,
        action_name: str,
        action_frame: int,
    ) -> tuple[ItemSpawnSlot, ...] | None:
        if self.current_project_dir is None:
            return None
        project = load_project(self.current_project_dir)
        for edit in project.item_frame_edits:
            if edit.action_name == action_name and edit.action_frame == action_frame:
                return edit.slots
        return None

    def _set_item_slots_from_slots(self, slots: tuple[ItemSpawnSlot, ...]) -> None:
        forms = [
            ItemSlotForm(
                item_action_group=str(slot.item_action_group) if slot.enabled else "",
                x=_format_number(slot.x),
                y=_format_number(slot.y),
                z=_format_number(slot.z),
                vx=_format_number(slot.vx),
                vy=_format_number(slot.vy),
                vz=_format_number(slot.vz),
            )
            for slot in slots[: self._current_item_base_slot_count]
        ]
        while len(forms) < len(self.item_slot_vars):
            forms.append(
                ItemSlotForm(
                    item_action_group="",
                    x="255",
                    y="-121",
                    z="2",
                    vx="45",
                    vy="0",
                    vz="0",
                )
            )
        self._apply_item_slot_forms(tuple(forms))
        self._sync_item_slot_controls_state()

    def _apply_item_slot_forms(self, forms: tuple[ItemSlotForm, ...]) -> None:
        self._loading_item_slots = True
        try:
            for slot_form, slot_vars in zip(forms, self.item_slot_vars):
                if slot_form.item_action_group:
                    slot_vars["item"].set(
                        selected_item_label(slot_form.item_action_group, self.item_options)
                    )
                else:
                    slot_vars["item"].set(disabled_item_label())
                slot_vars["x"].set(slot_form.x)
                slot_vars["y"].set(slot_form.y)
                slot_vars["z"].set(slot_form.z)
                slot_vars["vx"].set(slot_form.vx)
                slot_vars["vy"].set(slot_form.vy)
                slot_vars["vz"].set(slot_form.vz)
        finally:
            self._loading_item_slots = False

    def _mark_current_item_frame_dirty(self, *_args: object) -> None:
        if self._loading_item_slots or self._current_item_frame_key is None:
            return
        self._dirty_item_frame_keys.add(self._current_item_frame_key)

    def _cache_current_item_slots(self) -> None:
        key = self._current_item_frame_key
        if key is None or key not in self._dirty_item_frame_keys:
            return
        slots = self._current_item_slots()
        base_slots = self._base_item_slots(key[0], key[1])
        if self._item_slot_signature(slots) == self._item_slot_signature(base_slots):
            self.item_frame_drafts.pop(key, None)
        else:
            self.item_frame_drafts[key] = slots
        self._dirty_item_frame_keys.discard(key)

    def _current_item_slot_forms(self) -> tuple[ItemSlotForm, ...]:
        item_groups = item_options_by_label(self.item_options)
        disabled_label = disabled_item_label()
        return tuple(
            ItemSlotForm(
                item_action_group=(
                    ""
                    if slot_vars["item"].get() == disabled_label
                    else str(item_groups[slot_vars["item"].get()])
                    if slot_vars["item"].get() in item_groups
                    else ""
                ),
                x=slot_vars["x"].get(),
                y=slot_vars["y"].get(),
                z=slot_vars["z"].get(),
                vx=slot_vars["vx"].get(),
                vy=slot_vars["vy"].get(),
                vz=slot_vars["vz"].get(),
            )
            for slot_vars in self.item_slot_vars[: self._current_item_base_slot_count]
        )

    def _current_item_slots(self) -> tuple[ItemSpawnSlot, ...]:
        return item_slots_from_forms(self._current_item_slot_forms())

    def _base_item_slots(
        self,
        action_name: str,
        action_frame: int,
    ) -> tuple[ItemSpawnSlot, ...]:
        return load_spt_frame_item_slots(
            self.workspace,
            action_name,
            action_frame,
            self._current_target_cache,
            self._current_source_role_id,
            self._tools_for_gui(self),
        )

    @staticmethod
    def _item_slot_signature(
        slots: tuple[ItemSpawnSlot, ...],
    ) -> tuple[tuple[float, ...], ...]:
        return tuple(
            (
                float(slot.item_action_group),
                slot.x,
                slot.y,
                slot.z,
                slot.vx,
                slot.vy,
                slot.vz,
            )
            for slot in slots
            if slot.enabled
        )

    def _refresh_action_options(self, selected_action: str, selected_frame: int) -> None:
        self.action_by_label = {
            self._action_label(action): action.action_name
            for action in self.spt_actions
        }
        self.action_combo.configure(values=tuple(self.action_by_label))
        action = self.action_by_name.get(selected_action) or self.action_by_name.get("ball")
        if action is None and self.spt_actions:
            action = self.spt_actions[0]
        if action is None:
            self.item_action_var.set("")
            self.refresh_frame_options()
            return
        self.item_action_var.set(self._action_label(action))
        self.frame_by_label = {}
        self.refresh_frame_options(preferred_frame=selected_frame)

    @staticmethod
    def _compatible_item_slots(
        base_slots: tuple[ItemSpawnSlot, ...],
        candidate_slots: tuple[ItemSpawnSlot, ...],
    ) -> tuple[ItemSpawnSlot, ...] | None:
        _ = base_slots
        enabled_slots = tuple(slot for slot in candidate_slots if slot.enabled)
        if len(enabled_slots) > 3:
            return None
        return enabled_slots

    def _sync_item_slot_controls_state(self) -> None:
        active_item_labels = tuple(option.label for option in self.item_options)
        disabled_label = disabled_item_label()
        item_labels = (disabled_label, *active_item_labels)
        loading = self._loading_item_slots
        self._loading_item_slots = True
        try:
            for index, widgets in enumerate(self.item_slot_widgets):
                is_active = (
                    self._project_controls_enabled
                    and index < self._current_item_base_slot_count
                )
                slot_vars = self.item_slot_vars[index]
                combo = widgets["item"]
                if isinstance(combo, ttk.Combobox):
                    if is_active and active_item_labels:
                        combo.configure(values=item_labels, state="readonly")
                        if slot_vars["item"].get() not in item_labels:
                            slot_vars["item"].set(disabled_label)
                    elif (
                        not self._project_controls_enabled
                        and index < self._current_item_base_slot_count
                    ):
                        combo.configure(values=item_labels, state="disabled")
                    else:
                        combo.configure(values=(disabled_label,), state="disabled")
                        slot_vars["item"].set(disabled_label)
                for field in ("x", "y", "z", "vx", "vy", "vz"):
                    cast(Any, widgets[field]).configure(state="normal" if is_active else "disabled")
        finally:
            self._loading_item_slots = loading

    @staticmethod
    def _action_label(action: SptActionInfo) -> str:
        return f"{action.action_name}（{action.frame_count}帧）"

    @staticmethod
    def _frame_label(action: SptActionInfo, frame: int) -> str:
        suffix = "，已有道具" if frame in action.item_frames else ""
        return f"第 {frame} 帧 / 共 {action.frame_count} 帧{suffix}"

    def refresh_projects(self) -> None:
        self.project_list.delete(0, "end")
        self._summaries = scan_projects(self.projects_root)
        for row in project_rows(self._summaries):
            self.project_list.insert("end", row.label)

    def new_project(self) -> None:
        options = self._ask_new_project_options()
        if options is None:
            return
        name, cid, template_id, target_game, source_role_id = options
        self.projects_root.mkdir(exist_ok=True)
        try:
            create_project(
                self.projects_root,
                name,
                template_id,
                cid,
                target_game=target_game,
                source_role_id=source_role_id,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("新建失败", str(exc))
            return
        self.refresh_projects()
        self.load_into_form(self.projects_root / name)
        self._select_tab(self.edit_tab)

    def _ask_new_project_options_legacy(self) -> tuple[str, str, str] | None:
        templates = list_templates()
        default_name, default_cid = next_project_defaults(self._summaries)
        template_labels = {
            f"{template.name}（{template.template_id}）": template.template_id
            for template in templates
        }
        dialog = tk.Toplevel(self)
        dialog.title("新建角色")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        name_var = tk.StringVar(value=default_name)
        cid_var = tk.StringVar(value=default_cid)
        template_var = tk.StringVar(value=next(iter(template_labels)))
        result: list[tuple[str, str, str]] = []

        ttk.Label(dialog, text="项目名称").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=36)
        name_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(10, 4))

        ttk.Label(dialog, text="角色 ID（按此 ID 导出）").grid(
            row=1, column=0, sticky="w", padx=10, pady=4
        )
        ttk.Entry(dialog, textvariable=cid_var, width=36).grid(
            row=1, column=1, sticky="ew", padx=10, pady=4
        )

        ttk.Label(dialog, text="内置模板").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        ttk.Combobox(
            dialog,
            textvariable=template_var,
            values=tuple(template_labels),
            state="readonly",
            width=34,
        ).grid(row=2, column=1, sticky="ew", padx=10, pady=4)

        buttons = ttk.Frame(dialog)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", padx=10, pady=(10, 10))

        def confirm() -> None:
            name = name_var.get().strip()
            cid = cid_var.get().strip()
            template_id = template_labels.get(template_var.get())
            if not name or not cid or template_id is None:
                messagebox.showwarning(
                    "信息不完整",
                    "请填写项目名称、角色 ID，并选择内置模板。",
                    parent=dialog,
                )
                return
            result.append((name, cid, template_id))
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="创建", command=confirm).pack(side="right", padx=(0, 6))
        dialog.columnconfigure(1, weight=1)
        name_entry.focus_set()
        self.wait_window(dialog)
        return result[0] if result else None

    def _ask_new_project_options(self) -> tuple[str, str, str, TargetGame, str] | None:
        templates = list_templates()
        default_name, default_cid = next_project_defaults(self._summaries)
        dialog = tk.Toplevel(self)
        dialog.title("新建角色")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        target_source_var = tk.StringVar(value=DEFAULT_TARGET_GAME.source_path)
        target_kind_var = tk.StringVar(value=DEFAULT_TARGET_GAME.source_kind)
        template_labels: dict[str, NewProjectTemplateChoice] = {}
        name_var = tk.StringVar(value=default_name)
        cid_var = tk.StringVar(value=default_cid)
        template_var = tk.StringVar()
        catalog_status_var = tk.StringVar()
        result: list[tuple[str, str, str, TargetGame, str]] = []

        ttk.Label(dialog, text="目标游戏 EXE/SWF").grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )
        ttk.Entry(dialog, textvariable=target_source_var, width=42).grid(
            row=0, column=1, sticky="ew", padx=(10, 4), pady=(10, 4)
        )

        def selected_target_game() -> TargetGame | None:
            source = target_source_var.get().strip() or DEFAULT_TARGET_GAME.source_path
            try:
                target = target_game_for_source(source)
            except TargetCacheError as exc:
                messagebox.showwarning(exc.summary, exc.detail, parent=dialog)
                return None
            explicit_kind = target_kind_var.get().strip()
            if explicit_kind in {"exe", "swf"}:
                target = dataclass_replace(target, source_kind=explicit_kind)
            if (
                source == DEFAULT_TARGET_GAME.source_path
                and target.source_kind == DEFAULT_TARGET_GAME.source_kind
            ):
                return DEFAULT_TARGET_GAME
            return target

        def refresh_templates_for_target(show_errors: bool = False) -> None:
            nonlocal template_labels
            previous_label = template_var.get()
            previous_choices = tuple(template_labels.values())
            target = selected_target_game()
            if target is None:
                template_labels = {}
                template_combo.configure(values=())
                template_var.set("")
                catalog_status_var.set("目标版本不可读取，无法生成模板列表。")
                return
            target_kind_var.set(target.source_kind)
            cache = self._prepare_target_cache_for_gui(target, dialog, show_errors=show_errors)
            entries = (
                load_character_template_catalog(self.workspace, cache)
                if cache is not None
                else load_character_template_catalog(self.workspace)
            )
            choices = target_template_choices(entries, templates)
            template_labels = {choice.label: choice for choice in choices}
            template_combo.configure(values=tuple(template_labels))
            template_var.set(
                select_template_label_after_refresh(previous_label, previous_choices, choices)
            )
            if template_labels:
                message = (
                    f"已按目标版本发现 {len(entries)} 组角色资源；"
                    f"当前可创建 {len(template_labels)} 个角色模板。"
                )
                _sync_default_id_for_template()
            else:
                message = "此目标版本没有可用的 SPT/LMI 角色模板。"
            catalog_status_var.set(message)

        def _sync_default_id_for_template(_event: object | None = None) -> None:
            choice = template_labels.get(template_var.get())
            if choice is None:
                return
            current = cid_var.get().strip()
            if (
                current
                and current != default_cid
                and len(current.encode("utf-8")) == len(choice.role_id.encode("utf-8"))
            ):
                return
            cid_var.set(_default_character_id_for_source_role(choice.role_id, default_cid))

        def choose_dialog_target() -> None:
            path = filedialog.askopenfilename(
                parent=dialog,
                title="选择目标 HFE EXE/SWF",
                filetypes=(("HFE game", "*.exe *.swf"), ("All files", "*.*")),
            )
            if not path:
                return
            target = target_game_for_source(path)
            target_source_var.set(target.source_path)
            target_kind_var.set(target.source_kind)
            refresh_templates_for_target(show_errors=True)

        ttk.Button(dialog, text="选择目标", command=choose_dialog_target).grid(
            row=0, column=2, sticky="w", padx=(0, 10), pady=(10, 4)
        )

        ttk.Label(dialog, text="目标类型").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        ttk.Combobox(
            dialog,
            textvariable=target_kind_var,
            values=("exe", "swf"),
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky="w", padx=10, pady=4)

        ttk.Label(dialog, text="角色模板").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        template_combo = ttk.Combobox(
            dialog,
            textvariable=template_var,
            values=(),
            state="readonly",
            width=40,
        )
        template_combo.grid(row=2, column=1, columnspan=2, sticky="ew", padx=10, pady=4)
        template_combo.bind("<<ComboboxSelected>>", _sync_default_id_for_template)
        ttk.Label(dialog, textvariable=catalog_status_var, wraplength=460).grid(
            row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8)
        )

        ttk.Label(dialog, text="项目名称").grid(
            row=4, column=0, sticky="w", padx=10, pady=(10, 4)
        )
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=36)
        name_entry.grid(row=4, column=1, columnspan=2, sticky="ew", padx=10, pady=(10, 4))

        ttk.Label(dialog, text="角色 ID（按此 ID 导出）").grid(
            row=5, column=0, sticky="w", padx=10, pady=4
        )
        ttk.Entry(dialog, textvariable=cid_var, width=36).grid(
            row=5, column=1, columnspan=2, sticky="ew", padx=10, pady=4
        )

        buttons = ttk.Frame(dialog)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", padx=10, pady=(10, 10))

        def confirm() -> None:
            target = selected_target_game()
            name = name_var.get().strip()
            cid = cid_var.get().strip()
            choice = template_labels.get(template_var.get())
            if target is None or not name or not cid or choice is None:
                messagebox.showwarning(
                    "信息不完整",
                    "请先选择目标游戏版本，再选择该版本可用的角色模板，并填写项目名称和角色 ID。",
                    parent=dialog,
                )
                return
            result.append((name, cid, choice.template_id, target, choice.role_id))
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="创建", command=confirm).pack(side="right", padx=(0, 6))
        dialog.columnconfigure(1, weight=1)
        refresh_templates_for_target(show_errors=False)
        name_entry.focus_set()
        self.wait_window(dialog)
        return result[0] if result else None

    def open_project(self) -> None:
        path = filedialog.askdirectory(title="选择角色项目文件夹")
        if path:
            self.load_into_form(Path(path))

    def _prepare_target_cache_for_gui(
        self,
        target: TargetGame,
        parent: tk.Misc,
        show_errors: bool = False,
    ) -> TargetCacheEntry | None:
        key = (target.source_path, target.source_kind)
        cached = self._target_cache_entries.get(key)
        if cached is not None:
            return cached
        tools = discover_tools(self.workspace)
        missing = check_tools(tools)
        if missing:
            if show_errors:
                messagebox.showwarning("目标探测不可用", dependency_summary(missing), parent=parent)
            return None
        try:
            entry = prepare_target_cache(
                self.workspace,
                target,
                tools,
                output_root=default_target_cache_root(self.workspace),
                reuse_existing=True,
            )
        except TargetCacheError as exc:
            if show_errors:
                messagebox.showwarning(exc.summary, exc.detail, parent=parent)
            return None
        self._target_cache_entries[key] = entry
        return entry

    def _tools_for_gui(self, parent: tk.Misc, show_errors: bool = False) -> Any | None:
        tools = discover_tools(self.workspace)
        missing = check_tools(tools)
        if missing:
            if show_errors:
                messagebox.showwarning("目标探测不可用", dependency_summary(missing), parent=parent)
            return None
        return tools

    def _refresh_item_options_for_target(
        self,
        target: TargetGame,
        show_errors: bool = False,
    ) -> None:
        cache = self._prepare_target_cache_for_gui(target, self, show_errors=show_errors)
        if cache is None:
            catalog = load_item_catalog(self.workspace)
            self._set_item_catalog(catalog, "道具目录：目标未完成探测，暂用内置目录。")
            return
        catalog = load_item_catalog(self.workspace, cache, self._tools_for_gui(self))
        if catalog.available:
            source = catalog.source if catalog.source is not None else cache.probe_json_path
            self._set_item_catalog(
                catalog,
                f"道具目录：当前目标版本可用，共 {len(catalog.options)} 个；来源 {source}",
            )
            return
        self._set_item_catalog(
            catalog,
            f"道具目录：当前目标版本不可用。{catalog.unavailable_reason}",
        )

    def _set_item_catalog(self, catalog: ItemCatalog, status: str) -> None:
        self.item_options = catalog.options if catalog.available else ()
        if hasattr(self, "item_catalog_status_var"):
            self.item_catalog_status_var.set(status)
        self._sync_item_slot_controls_state()

    def choose_target_game(self) -> None:
        path = filedialog.askopenfilename(
            title="选择目标 HFE EXE/SWF",
            filetypes=(("HFE game", "*.exe *.swf"), ("All files", "*.*")),
        )
        if not path:
            return
        target = target_game_for_source(path)
        self.target_source_var.set(target.source_path)
        self.target_kind_var.set(target.source_kind)
        self._refresh_item_options_for_target(target, show_errors=True)
        self._current_target_cache = self._prepare_target_cache_for_gui(
            target,
            self,
            show_errors=True,
        )
        self.spt_actions = load_spt_action_options(
            self.workspace,
            self._current_target_cache,
            self._current_source_role_id,
            self._tools_for_gui(self),
        )
        self.action_by_name = {action.action_name: action for action in self.spt_actions}
        current_action = self.action_by_label.get(
            self.item_action_var.get(),
            self.item_action_var.get(),
        )
        current_frame = self.frame_by_label.get(self.item_frame_var.get(), 1)
        self._refresh_action_options(current_action, current_frame)
        self.refresh_item_slots_for_selected_frame()

    def select_project(self, _event: object) -> None:
        selected = self.project_list.curselection()
        if selected:
            self.load_into_form(self._summaries[selected[0]].path)

    def load_into_form(self, project_dir: Path) -> None:
        project = load_project(project_dir)
        form = project_to_form(project)
        self.current_project_dir = project_dir
        self._current_source_role_id = project.source_role_id or "lucas"
        self._current_target_cache = self._prepare_target_cache_for_gui(project.target_game, self)
        self.spt_actions = load_spt_action_options(
            self.workspace,
            self._current_target_cache,
            self._current_source_role_id,
            self._tools_for_gui(self),
        )
        self.action_by_name = {action.action_name: action for action in self.spt_actions}
        self.item_frame_drafts = {}
        for edit in project.item_frame_edits:
            base_slots = self._base_item_slots(edit.action_name, edit.action_frame)
            compatible = self._compatible_item_slots(base_slots, edit.slots)
            if compatible is not None and compatible:
                self.item_frame_drafts[(edit.action_name, edit.action_frame)] = compatible
        self._dirty_item_frame_keys.clear()
        self._current_item_frame_key = None
        self.id_var.set(form.character_id)
        self.name_var.set(form.character_name)
        self.name_zh_var.set(form.character_name_zh)
        self.desc_var.set(form.description)
        self.hp_var.set(form.hp)
        self.mp_var.set(form.mp)
        self.defense_var.set(form.defense)
        self.skill_key_var.set(form.skill_key)
        self.skill_mp_var.set(form.skill_mp_cost)
        self.skill_damage_var.set(form.skill_damage)
        self.skill_speed_var.set(form.skill_speed)
        self.skill_range_var.set(form.skill_range)
        self.target_source_var.set(form.target_source_path)
        self.target_kind_var.set(form.target_source_kind)
        self._refresh_item_options_for_target(project.target_game)
        self._refresh_action_options(form.item_action_name, _parse_int(form.item_action_frame, 8))
        self.refresh_item_slots_for_selected_frame()
        selections = {part.part_id: KEEP_SOURCE_TEXTURE_ROLE_ID for part in self.texture_parts}
        selections.update(form.texture_selections)
        for part in self.texture_parts:
            value = selections.get(part.part_id, KEEP_SOURCE_TEXTURE_ROLE_ID)
            self.texture_vars[part.part_id].set(
                selected_texture_role_label(value, self.texture_roles)
            )
        self._set_project_controls_enabled(True)

    def save_current(self) -> None:
        saved = self._save_current_project()
        if saved is None:
            return
        _project, report = saved
        self.show_validation_report(report)
        self.result_text.delete("1.0", "end")
        self.result_text.insert("end", render_validation_report(report))

    def randomize_current_project(self) -> None:
        if self.current_project_dir is None:
            return
        if not self.item_options:
            messagebox.showerror(
                "随机生成失败",
                "当前目标游戏的道具目录不可用，无法随机配置道具。",
            )
            return
        if not self.spt_actions:
            messagebox.showerror(
                "随机生成失败",
                "当前角色模板的动作列表不可用，无法随机配置动作帧。",
            )
            return
        project = load_project(self.current_project_dir)
        randomized = randomize_project(
            project,
            self.spt_actions,
            self.item_options,
            self.texture_parts,
            self.texture_roles,
        )
        save_project(self.current_project_dir, randomized)
        self.load_into_form(self.current_project_dir)
        self.show_validation_report(validate_editing(randomized))
        self.result_text.delete("1.0", "end")
        self.result_text.insert(
            "end",
            "已随机生成 HP/MP/防御、每个动作的随机道具帧，以及随机贴图组合。\n"
            "角色 ID 和目标游戏没有被修改；导出前仍建议点一次“保存并校验”。\n",
        )

    def _save_current_project(self) -> tuple[CharacterProject, ValidationReport] | None:
        if self.current_project_dir is None:
            return None
        self._cache_current_item_slots()
        project = load_project(self.current_project_dir)
        action_name = self.action_by_label.get(
            self.item_action_var.get(),
            self.item_action_var.get(),
        )
        action_frame = self.frame_by_label.get(self.item_frame_var.get(), 0)
        form = CharacterForm(
            character_id=self.id_var.get(),
            character_name=self.name_var.get(),
            character_name_zh=self.name_zh_var.get(),
            description=self.desc_var.get(),
            hp=self.hp_var.get(),
            mp=self.mp_var.get(),
            defense=self.defense_var.get(),
            skill_key=self.skill_key_var.get() or "D>A",
            skill_mp_cost=self.skill_mp_var.get() or "50",
            skill_damage=self.skill_damage_var.get() or "35",
            skill_speed=self.skill_speed_var.get() or "10",
            skill_range=self.skill_range_var.get() or "80",
            target_source_path=self.target_source_var.get(),
            target_source_kind=self.target_kind_var.get(),
            item_action_name=action_name or "ball",
            item_action_frame=str(action_frame or 1),
            item_slots=self._current_item_slot_forms(),
            texture_selections={
                key: texture_role_ids_by_label(self.texture_roles).get(
                    var.get(), KEEP_SOURCE_TEXTURE_ROLE_ID
                )
                for key, var in self.texture_vars.items()
            },
        )
        updated = apply_form(project, form)
        updated = replace_project(
            updated,
            item_frame_edits=frame_item_edits_from_drafts(self.item_frame_drafts),
        )
        save_project(self.current_project_dir, updated)
        self._current_source_role_id = updated.source_role_id or "lucas"
        self._current_target_cache = self._prepare_target_cache_for_gui(updated.target_game, self)
        self.spt_actions = load_spt_action_options(
            self.workspace,
            self._current_target_cache,
            self._current_source_role_id,
            self._tools_for_gui(self),
        )
        self.action_by_name = {action.action_name: action for action in self.spt_actions}
        self._refresh_action_options(action_name or "ball", action_frame or 1)
        self._refresh_item_options_for_target(updated.target_game)
        self.refresh_projects()
        report = validate_editing(updated)
        return updated, report

    def export_current(self) -> None:
        if self.current_project_dir is None:
            return
        if self._export_thread is not None and self._export_thread.is_alive():
            return
        if self._save_current_project() is None:
            return
        project_dir = self.current_project_dir
        result_queue: queue.Queue[tuple[str, ExportResult | BaseException]] = queue.Queue()
        self._export_queue = result_queue
        self.export_status_var.set("正在校验并导出，请稍候...")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("end", "正在校验并导出，请稍候...\n")
        self._set_project_controls_enabled(False)
        self.export_progress.start(12)

        def run_export() -> None:
            try:
                result_queue.put(("result", export_project(project_dir, self.workspace)))
            except BaseException as exc:  # noqa: BLE001
                result_queue.put(("error", exc))

        self._export_thread = threading.Thread(target=run_export, daemon=True)
        self._export_thread.start()
        self.after(120, self._poll_export_result)

    def _poll_export_result(self) -> None:
        result_queue = self._export_queue
        if result_queue is None:
            return
        try:
            kind, payload = result_queue.get_nowait()
        except queue.Empty:
            self.after(120, self._poll_export_result)
            return
        self.export_progress.stop()
        self._set_project_controls_enabled(self.current_project_dir is not None)
        self._export_queue = None
        if kind == "result" and isinstance(payload, ExportResult):
            if payload.status.startswith("success"):
                self.export_status_var.set("导出完成。")
            elif payload.status == "blocked":
                self.export_status_var.set("导出已阻断。")
            else:
                self.export_status_var.set("导出失败。")
            self.show_validation_report(payload.validation_report)
            self.result_text.delete("1.0", "end")
            self.result_text.insert("end", render_export_result(payload))
            self.refresh_projects()
            return
        self.export_status_var.set("导出失败。")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("end", f"导出失败：{payload}\n")

    def show_validation_report(self, report: ValidationReport) -> None:
        self.validation_tree.delete(*self.validation_tree.get_children())
        self._validation_issue_by_iid.clear()
        for group in validation_groups(report):
            if not group.issues:
                continue
            group_iid = f"group:{group.title}"
            self.validation_tree.insert("", "end", iid=group_iid, text=group.title, open=True)
            for index, issue in enumerate(group.issues):
                iid = f"{group.title}:{index}:{issue.target}"
                self._validation_issue_by_iid[iid] = issue
                self.validation_tree.insert(
                    group_iid,
                    "end",
                    iid=iid,
                    text=issue.message,
                    values=(issue.target, issue.suggestion),
                )

    def focus_validation_selection(self, _event: object) -> None:
        selected = self.validation_tree.selection()
        if not selected:
            return
        issue = self._validation_issue_by_iid.get(selected[0])
        if issue is not None:
            self.focus_validation_target(focus_target(issue))

    def focus_validation_target(self, target: str) -> None:
        if target.startswith("textures") or target.startswith("item_frame_edits"):
            self._select_tab(self.asset_tab)
            widget = self.target_widgets.get(target)
            if widget is not None:
                widget.focus_set()
            return
        widget = self.target_widgets.get(target)
        if widget is not None:
            self._select_tab(self.edit_tab)
            widget.focus_set()
            if isinstance(widget, ttk.Entry):
                widget.selection_range(0, "end")
            return
        self._select_tab(self.export_tab)
        self.result_text.focus_set()


def resolve_workspace(start: Path | None = None, executable: Path | None = None) -> Path:
    if is_frozen_app():
        if executable is not None:
            return executable.resolve().parent
        return Path(sys.executable).resolve().parent
    starts: list[Path] = []
    if start is not None:
        starts.append(start)
    starts.append(Path.cwd())
    if executable is not None:
        starts.append(executable.parent)
    elif getattr(sys, "frozen", False):
        starts.append(Path(sys.executable).resolve().parent)

    for root in starts:
        resolved = root.resolve()
        for candidate in (resolved, *resolved.parents):
            if all((candidate / marker).is_file() for marker in WORKSPACE_MARKERS):
                return candidate
    return (start or Path.cwd()).resolve()


def _rounded_canvas_rect(
    canvas: tk.Canvas,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    radius: int,
    **kwargs: Any,
) -> int:
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
    return int(canvas.create_polygon(points, smooth=True, splinesteps=16, **kwargs))


def _parse_int(value: str, default: int) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return default


def _format_number(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)


def _default_character_id_for_source_role(source_role_id: str, fallback: str) -> str:
    desired_length = len(source_role_id.encode("utf-8"))
    if source_role_id == "lucas" and len(fallback.encode("utf-8")) == desired_length:
        return fallback
    if desired_length < 2:
        return fallback
    cleaned = "".join(
        ch.lower() if ch.isascii() and (ch.isalnum() or ch == "_") else ""
        for ch in source_role_id
    )
    if not cleaned or not cleaned[0].isalpha():
        cleaned = "c" + cleaned
    if len(cleaned) >= desired_length:
        candidate = cleaned[:desired_length]
    else:
        candidate = cleaned + ("0" * (desired_length - len(cleaned)))
    if candidate == source_role_id.lower():
        candidate = candidate[:-1] + "0"
    return candidate


def _open_image(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image.open(path)


def _image_to_photo(image: Any | None, size: tuple[int, int] | None = None) -> Any | None:
    if image is None:
        return None
    try:
        from PIL import ImageTk
    except ImportError:
        return None
    rendered = _cover_image(image, size) if size is not None else image
    if rendered is None:
        return None
    photo_factory = cast(Any, ImageTk.PhotoImage)
    return photo_factory(rendered)


def _cover_image(image: Any | None, size: tuple[int, int] | None) -> Any | None:
    if image is None or size is None:
        return image
    try:
        from PIL import Image
    except ImportError:
        return None
    source_width, source_height = image.size
    target_width, target_height = size
    scale = max(target_width / source_width, target_height / source_height)
    resized = image.resize(
        (max(1, int(source_width * scale)), max(1, int(source_height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.size[0] - target_width) // 2)
    top = max(0, (resized.size[1] - target_height) // 2)
    return resized.crop((left, top, left + target_width, top + target_height))


def _glass_panel_image(
    image: Any | None,
    size: tuple[int, int],
    origin: tuple[int, int],
    profile: GlassProfile,
) -> Any | None:
    if image is None:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        return None
    width, height = size
    x, y = origin
    image_width, image_height = image.size
    if image_width < x + width or image_height < y + height:
        image = _cover_image(image, (x + width + PANEL_MARGIN, y + height + PANEL_MARGIN))
        if image is None:
            return None
    panel = image.crop((x, y, x + width, y + height)).convert("RGBA")
    panel = panel.filter(ImageFilter.GaussianBlur(radius=profile.blur_radius))
    tint = Image.new("RGBA", (width, height), (255, 255, 255, profile.tint_alpha))
    panel = Image.alpha_composite(panel, tint)
    if width < 8 or height < 8:
        return panel
    glow = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(glow)
    draw.rounded_rectangle(
        (1, 1, width - 2, height - 2),
        radius=18,
        outline=(255, 255, 255, profile.border_alpha),
        width=2,
    )
    draw.rounded_rectangle(
        (3, 3, width - 4, height - 4),
        radius=16,
        outline=(130, 180, 225, profile.inner_border_alpha),
        width=1,
    )
    return Image.alpha_composite(panel, glow)


def main() -> None:
    workspace = resolve_workspace()
    app = HfeCharacterApp(workspace)
    app.mainloop()
