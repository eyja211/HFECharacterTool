from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from contextlib import suppress
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable, TypeVar, cast

from hfe_character_tool import gui_theme as theme
from hfe_character_tool.action_names import describe_action, family_label, infer_family
from hfe_character_tool.donor_textures import (
    donor_catalog_ready,
    texture_parts_without_donor,
    warm_donor_catalog,
)
from hfe_character_tool.export import export_project
from hfe_character_tool.gui_presenter import (
    CharacterForm,
    ItemSlotForm,
    action_row_text,
    add_afterimage_delay,
    apply_form,
    character_id_hint,
    character_id_problem,
    compress_delays,
    disabled_item_label,
    export_status_display,
    format_elapsed,
    frame_edit_label,
    frame_item_edits_from_drafts,
    item_options_by_label,
    item_slots_from_forms,
    next_project_defaults,
    project_list_rows,
    project_to_form,
    quick_add_afterimage_delays,
    quick_add_preview,
    selected_item_label,
    suggest_character_id,
    target_display_name,
    validation_chip,
    validation_counts,
    validation_groups,
)
from hfe_character_tool.gui_widgets import (
    CanvasButton,
    DelayTimeline,
    FrameStrip,
    ListboxTooltip,
    Pill,
    ProjectList,
    ScrollFrame,
    SearchCombobox,
    TabBar,
    ToggleSwitch,
    Tooltip,
    add_placeholder,
    make_card,
)
from hfe_character_tool.hfworkshop_catalog import (
    KEEP_SOURCE_TEXTURE_ROLE_ID,
    CharacterTemplateCatalogEntry,
    ItemCatalog,
    SptActionInfo,
    load_character_template_catalog,
    load_item_catalog,
    load_spt_action_options,
    load_spt_frame_item_slots,
    load_texture_parts,
    load_texture_roles,
    selected_texture_role_label,
    texture_role_ids_by_label,
)
from hfe_character_tool.models import (
    AFTERIMAGE_MAX_DELAY,
    DEFAULT_TARGET_GAME,
    AfterimageSettings,
    CharacterProject,
    ExportResult,
    ItemSpawnSlot,
    Severity,
    TargetGame,
    Template,
    ValidationIssue,
    ValidationReport,
    replace_project,
)
from hfe_character_tool.projects import (
    PROJECT_FILE,
    TRASH_DIR_NAME,
    ProjectError,
    create_project,
    duplicate_project,
    load_project,
    move_project_to_trash,
    save_project,
    scan_projects,
)
from hfe_character_tool.randomizer import randomize_project
from hfe_character_tool.runtime import default_target_cache_root, is_frozen_app, resource_path
from hfe_character_tool.target_cache import (
    TargetCacheEntry,
    TargetCacheError,
    prepare_target_cache,
    target_game_for_source,
)
from hfe_character_tool.templates import list_templates
from hfe_character_tool.tools import ToolConfig, check_tools, dependency_summary, discover_tools
from hfe_character_tool.validation import validate_against_target, validate_editing

WORKSPACE_MARKERS = (
    Path("vendor") / "original_game" / "HFE v1.0.2.exe",
    Path("vendor") / "projector" / "SA.exe",
)
APP_TITLE = "HFE 角色定制工具"
PAGES = (
    ("basic", "基本信息"),
    ("textures", "外观贴图"),
    ("skills", "技能道具"),
    ("afterimage", "残影"),
    ("export", "校验导出"),
)
SLOT_FIELDS = ("x", "y", "z", "vx", "vy", "vz")
EMPTY_SLOT = ItemSlotForm(item_action_group="", x="255", y="-121", z="2", vx="45", vy="0", vz="0")
MARGIN = 14
SIDEBAR_WIDTH = 272
GAP = 14
PANEL_INSET = 6
AFTERIMAGE_PRESETS = (
    ("紧随 ×5（1–5 帧）", "5", "1", "1"),
    ("拖尾 ×10（2–20 帧）", "10", "2", "2"),
    ("每秒一个（30/60/90 帧）", "3", "30", "30"),
)

T = TypeVar("T")


@dataclass(frozen=True)
class NewProjectTemplateChoice:
    label: str
    template_id: str
    role_id: str


@dataclass(frozen=True)
class TargetContext:
    """Everything the editor reads from one target game (computed off the UI thread)."""

    target: TargetGame
    cache: TargetCacheEntry | None
    tools: ToolConfig | None
    problem: tuple[str, str] | None
    actions: tuple[SptActionInfo, ...]
    item_catalog: ItemCatalog
    item_status: str


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


def compute_target_context(
    workspace: Path,
    target: TargetGame,
    role_id: str,
    cached: TargetCacheEntry | None,
) -> TargetContext:
    """Probe/cached-read a target and load its action and item catalogs. Thread-safe."""
    tools = discover_tools(workspace)
    missing = check_tools(tools)
    usable_tools = None if missing else tools
    cache = cached
    problem: tuple[str, str] | None = None
    if cache is None:
        if missing:
            problem = ("目标探测不可用", dependency_summary(missing))
        else:
            try:
                cache = prepare_target_cache(
                    workspace,
                    target,
                    tools,
                    output_root=default_target_cache_root(workspace),
                    reuse_existing=True,
                )
            except TargetCacheError as exc:
                problem = (exc.summary, exc.detail)
    actions = load_spt_action_options(workspace, cache, role_id, usable_tools)
    if cache is None:
        catalog = load_item_catalog(workspace)
        status = "目标未完成读取，暂用内置道具目录。"
    else:
        catalog = load_item_catalog(workspace, cache, usable_tools)
        status = (
            f"当前目标版本共 {len(catalog.options)} 种道具。"
            if catalog.available
            else f"当前目标版本的道具目录不可用：{catalog.unavailable_reason}"
        )
    return TargetContext(target, cache, usable_tools, problem, actions, catalog, status)


def run_in_thread(
    widget: tk.Misc,
    work: Callable[[], T],
    done: Callable[[T], None],
    failed: Callable[[BaseException], None],
) -> None:
    """Run `work` on a worker thread and deliver the result on the Tk thread."""
    results: queue.Queue[tuple[bool, Any]] = queue.Queue()

    def runner() -> None:
        try:
            results.put((True, work()))
        except BaseException as exc:  # noqa: BLE001
            results.put((False, exc))

    def poll() -> None:
        try:
            ok, payload = results.get_nowait()
        except queue.Empty:
            widget.after(40, poll)
            return
        if ok:
            done(payload)
        else:
            failed(payload)

    threading.Thread(target=runner, daemon=True).start()
    widget.after(40, poll)


def open_in_explorer(path: Path) -> None:
    if path.is_file():
        subprocess.Popen(["explorer", "/select,", str(path)])  # noqa: S603,S607
    elif hasattr(os, "startfile"):
        os.startfile(str(path))


class HfeCharacterApp(tk.Tk):
    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace
        self.projects_root = workspace / "projects"
        self.withdraw()
        theme.configure_styles(self)
        self.title(APP_TITLE)
        width = min(theme.px(1180), int(self.winfo_screenwidth() * 0.92))
        height = min(theme.px(760), int(self.winfo_screenheight() * 0.86))
        self.geometry(f"{width}x{height}")
        self.minsize(min(theme.px(1000), width), min(theme.px(640), height))
        self.configure(background=theme.BACKDROP_FALLBACK)

        self.texture_parts = load_texture_parts(workspace)
        self.texture_roles = load_texture_roles(workspace)
        self.item_options: tuple[Any, ...] = ()
        self.spt_actions: tuple[SptActionInfo, ...] = ()
        self.action_by_name: dict[str, SptActionInfo] = {}
        self._summaries: tuple[Any, ...] = ()
        self.current_project_dir: Path | None = None
        self._project: CharacterProject | None = None
        self._target_game: TargetGame = DEFAULT_TARGET_GAME
        self._context: TargetContext | None = None
        self._target_cache_entries: dict[tuple[str, str], TargetCacheEntry] = {}
        self._source_role_id = "lucas"
        self._hidden_skill = ("D>A", "50", "35", "10", "80")
        self.item_frame_drafts: dict[tuple[str, int], tuple[ItemSpawnSlot, ...]] = {}
        self._dirty_item_frame_keys: set[tuple[str, int]] = set()
        self._current_item_frame_key: tuple[str, int] | None = None
        self._loading_item_slots = False
        self._base_slot_cache: dict[tuple[str, int], tuple[ItemSpawnSlot, ...]] = {}
        self._selected_action: str | None = None
        self._selected_frame = 0
        self._visible_actions: list[str] = []
        self.afterimage_delays: tuple[int, ...] = ()
        self._dirty = False
        self._populating = False
        self._busy = False
        self._busy_after: str | None = None
        self._exporting = False
        self._export_started = 0.0
        self._refresh_after: str | None = None
        self._toast_after: str | None = None
        self._layout_after: str | None = None
        self._hero_after: str | None = None
        self._validation_report = ValidationReport()
        self._showing_export_report = False
        self._field_messages: dict[str, tuple[tk.Widget, ttk.Label, str]] = {}
        self.target_widgets: dict[str, tk.Widget] = {}
        self._page_frames: dict[str, ScrollFrame] = {}
        self._active_page = "basic"
        self._slot_clipboard: tuple[ItemSpawnSlot, ...] | None = None
        self._texture_warnings: dict[str, str] = {}
        self._action_family: str | None = None
        self._art = theme.open_image(resource_path(workspace, Path("底图.jpg")))
        self._photos: dict[str, Any] = {}

        self._build_shell()
        self._build_welcome_page()
        self._build_basic_page()
        self._build_textures_page()
        self._build_skills_page()
        self._build_afterimage_page()
        self._build_export_page()
        self._build_busy_overlay()
        self._bind_shortcuts()
        self._load_window_icon()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh_projects()
        self._show_welcome()
        self.deiconify()
        # Texture limb-name checks need the parsed game EXE (seconds); parse it once early.
        threading.Thread(target=warm_donor_catalog, args=(workspace,), daemon=True).start()
        self.after(500, self._wait_for_limb_names)

    def _wait_for_limb_names(self) -> None:
        if not donor_catalog_ready(self.workspace):
            self.after(500, self._wait_for_limb_names)
        elif self._project is not None:
            self._refresh_derived()

    # ------------------------------------------------------------------ shell

    def _build_shell(self) -> None:
        self.surface = tk.Canvas(
            self, highlightthickness=0, borderwidth=0, background=theme.BACKDROP_FALLBACK
        )
        self.surface.pack(fill="both", expand=True)
        self._backdrop_item = self.surface.create_image(0, 0, anchor="nw")
        self._brand_icon_item = self.surface.create_image(0, 0, anchor="nw")
        self._brand_title = self.surface.create_text(
            0, 0, text=APP_TITLE, anchor="w", fill=theme.TEXT, font=theme.font(12, "bold")
        )
        self._brand_sub = self.surface.create_text(
            0,
            0,
            text="原创角色 · 一键导出游戏",
            anchor="w",
            fill=theme.TEXT_MUTED,
            font=theme.font(9),
        )
        self.new_button = CanvasButton(
            self.surface, "btn-new", "＋  新建角色", self.new_project, kind="accent"
        )
        self.open_button = CanvasButton(
            self.surface, "btn-open", "打开项目文件夹", self.open_project
        )
        self.refresh_button = CanvasButton(
            self.surface, "btn-refresh", "刷新", self.refresh_projects
        )
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self._refresh_project_rows())
        self.search_entry = ttk.Entry(self.surface, textvariable=self.search_var)
        add_placeholder(self.search_entry, "搜索项目名或角色 ID")
        self._search_window = self.surface.create_window(
            0, 0, anchor="nw", window=self.search_entry
        )
        self._list_caption = self.surface.create_text(
            0, 0, text="", anchor="w", fill=theme.TEXT_MUTED, font=theme.font(9)
        )
        self.project_list = ProjectList(self.surface, self._on_project_clicked, self._project_menu)
        self._list_window = self.surface.create_window(0, 0, anchor="nw", window=self.project_list)
        self._sidebar_fallback = self.surface.create_rectangle(0, 0, 0, 0, outline="", fill="")
        self._main_fallback = self.surface.create_rectangle(0, 0, 0, 0, outline="", fill="")

        self.main = tk.Frame(self.surface, background=theme.CARD)
        self._main_window = self.surface.create_window(0, 0, anchor="nw", window=self.main)
        self.main.columnconfigure(0, weight=1)
        self.main.rowconfigure(2, weight=1)
        self._build_header()
        self.tabbar = TabBar(self.main, lambda index: self._select_page(PAGES[index][0]))
        self.tabbar.set_tabs([label for _key, label in PAGES])
        self.content = tk.Frame(self.main, background=theme.PAGE)
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self._build_status_bar()
        self.surface.bind("<Configure>", self._schedule_layout)
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_class("TLabel", "<Configure>", _autowrap, add="+")
        self.bind_class("Label", "<Configure>", _autowrap, add="+")
        for widget_class in ("TCombobox", "TSpinbox", "Listbox", "Text"):
            self.bind_class(widget_class, "<MouseWheel>", "")

    def _build_header(self) -> None:
        header = ttk.Frame(self.main)
        header.grid(row=0, column=0, sticky="ew", padx=theme.px(24), pady=(theme.px(16), 0))
        header.columnconfigure(0, weight=1)
        titles = ttk.Frame(header)
        titles.grid(row=0, column=0, sticky="ew", padx=(0, theme.px(16)))
        self.header_title = ttk.Label(titles, text="欢迎", style="Title.TLabel")
        self.header_title.pack(side="left")
        self.dirty_label = Pill(titles)
        self.dirty_label.set("未保存", "accent")
        self.header_meta = ttk.Label(
            header, text="", style="Muted.TLabel", wraplength=theme.px(700)
        )
        self.header_meta.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(theme.px(2), 0))
        actions = ttk.Frame(header)
        actions.grid(row=0, column=1, sticky="e")
        self.discard_button = ttk.Button(
            actions, text="放弃修改", style="Ghost.TButton", command=self.discard_changes
        )
        self.save_button = ttk.Button(actions, text="保存", command=self.save_current)
        self.export_button = ttk.Button(
            actions, text="导出游戏", style="Accent.TButton", command=self.export_current
        )
        self.discard_button.pack(side="left", padx=(0, theme.px(6)))
        self.save_button.pack(side="left", padx=(0, theme.px(8)))
        self.export_button.pack(side="left")
        Tooltip(self.save_button, "保存当前项目（Ctrl+S）")
        Tooltip(self.export_button, "保存并导出可以直接运行的游戏 EXE（Ctrl+E）")

    def _build_status_bar(self) -> None:
        bar = tk.Frame(self.main, background=theme.CARD)
        bar.grid(row=3, column=0, sticky="ew")
        tk.Frame(bar, height=1, background=theme.BORDER).pack(fill="x", side="top")
        inner = ttk.Frame(bar)
        inner.pack(fill="x", padx=theme.px(20), pady=theme.px(7))
        self.status_icon = ttk.Label(inner, text="●", style="Subtle.TLabel")
        self.status_icon.pack(side="left")
        self.status_label = ttk.Label(inner, text="就绪", style="Muted.TLabel")
        self.status_label.pack(side="left", padx=(theme.px(6), 0))
        self.status_progress = ttk.Progressbar(
            inner,
            mode="indeterminate",
            length=theme.px(120),
            style="Accent.Horizontal.TProgressbar",
        )
        self.validation_pill = Pill(inner, command=lambda: self._select_page("export"))
        self.validation_pill.pack(side="right")
        Tooltip(self.validation_pill, "点击查看校验详情")

    def _schedule_layout(self, _event: object = None) -> None:
        self._layout_now()
        if self._layout_after is not None:
            self.after_cancel(self._layout_after)
        self._layout_after = self.after(90, self._render_backdrop)

    def _boxes(self) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        width = max(self.surface.winfo_width(), 1)
        height = max(self.surface.winfo_height(), 1)
        margin, sidebar, gap = theme.px(MARGIN), theme.px(SIDEBAR_WIDTH), theme.px(GAP)
        side = (margin, margin, margin + sidebar, height - margin)
        main = (margin + sidebar + gap, margin, width - margin, height - margin)
        return side, main

    def _layout_now(self) -> None:
        side, main = self._boxes()
        sx1, sy1, sx2, sy2 = side
        pad = theme.px(14)
        self.surface.coords(self._brand_icon_item, sx1 + pad + theme.px(2), sy1 + theme.px(16))
        self.surface.coords(self._brand_title, sx1 + pad + theme.px(52), sy1 + theme.px(28))
        self.surface.coords(self._brand_sub, sx1 + pad + theme.px(52), sy1 + theme.px(48))
        top = sy1 + theme.px(78)
        self.new_button.place(sx1 + pad, top, sx2 - pad, top + theme.px(42))
        top += theme.px(52)
        refresh_w = theme.px(64)
        self.open_button.place(
            sx1 + pad, top, sx2 - pad - refresh_w - theme.px(8), top + theme.px(34)
        )
        self.refresh_button.place(sx2 - pad - refresh_w, top, sx2 - pad, top + theme.px(34))
        top += theme.px(48)
        self.surface.coords(self._search_window, sx1 + pad, top)
        self.surface.itemconfigure(self._search_window, width=sx2 - sx1 - pad * 2)
        top += self.search_entry.winfo_reqheight() + theme.px(14)
        self.surface.coords(self._list_caption, sx1 + pad + theme.px(4), top)
        top += theme.px(12)
        self._list_box = (sx1 + pad - theme.px(4), top, sx2 - pad + theme.px(4), sy2 - pad)
        lx1, ly1, lx2, ly2 = self._list_box
        self.surface.coords(self._list_window, lx1, ly1)
        self.surface.itemconfigure(self._list_window, width=lx2 - lx1, height=max(ly2 - ly1, 1))
        inset = theme.px(PANEL_INSET)
        mx1, my1, mx2, my2 = main
        self.surface.coords(self._main_window, mx1 + inset, my1 + inset)
        self.surface.itemconfigure(
            self._main_window,
            width=max(mx2 - mx1 - inset * 2, 1),
            height=max(my2 - my1 - inset * 2, 1),
        )
        if self._art is None:
            self.surface.coords(self._sidebar_fallback, *side)
            self.surface.itemconfigure(self._sidebar_fallback, fill="#f6f2fa")
            self.surface.coords(self._main_fallback, *main)
            self.surface.itemconfigure(self._main_fallback, fill=theme.CARD)
            # The panels are created after the brand text; keep them underneath it.
            self.surface.tag_lower(self._main_fallback)
            self.surface.tag_lower(self._sidebar_fallback)
            self.project_list.configure(background="#f6f2fa")

    def _render_backdrop(self) -> None:
        self._layout_after = None
        if self._art is None:
            return
        side, main = self._boxes()
        width = max(self.surface.winfo_width(), 1)
        height = max(self.surface.winfo_height(), 1)
        try:
            image = theme.render_backdrop(
                self._art,
                (width, height),
                (theme.PanelSpec(side, "frosted"), theme.PanelSpec(main, "solid")),
            )
        except Exception:  # noqa: BLE001 - the backdrop is decoration only
            self._art = None
            self._layout_now()
            return
        photo = theme.to_photo(image)
        if photo is None:
            return
        self._photos["backdrop"] = photo
        self.surface.itemconfigure(self._backdrop_item, image=photo)
        self.surface.tag_lower(self._backdrop_item)
        list_photo = theme.to_photo(image.crop(self._list_box))
        self._photos["list"] = list_photo
        self.project_list.set_background(list_photo)
        if self.current_project_dir is None:
            self._render_hero()

    def _load_window_icon(self) -> None:
        icon = theme.open_image(resource_path(self.workspace, Path("图标.jpg")))
        if icon is None:
            return
        size = theme.px(40)
        brand = theme.rounded_image(theme.cover_image(icon, (size, size)), theme.px(10), "#f4eff9")
        self._photos["brand"] = theme.to_photo(brand)
        self.surface.itemconfigure(self._brand_icon_item, image=self._photos["brand"])
        window_icon = theme.to_photo(theme.cover_image(icon, (64, 64)))
        if window_icon is not None:
            self._photos["window"] = window_icon
            self.iconphoto(True, window_icon)

    def _build_busy_overlay(self) -> None:
        overlay = tk.Frame(self.content, background=theme.PAGE)
        box = ttk.Frame(overlay, padding=theme.px(28))
        box.place(relx=0.5, rely=0.42, anchor="center")
        self.busy_label = ttk.Label(box, text="", style="Section.TLabel")
        self.busy_label.pack()
        ttk.Label(
            box,
            text="第一次读取某个游戏版本需要解包分析，可能要几分钟；之后会直接使用缓存。",
            style="Muted.TLabel",
        ).pack(pady=(theme.px(6), theme.px(14)))
        self.busy_progress = ttk.Progressbar(
            box,
            mode="indeterminate",
            length=theme.px(320),
            style="Accent.Horizontal.TProgressbar",
        )
        self.busy_progress.pack()
        self.busy_overlay = overlay

    def _bind_shortcuts(self) -> None:
        self.bind_all("<Control-s>", lambda _event: self.save_current())
        self.bind_all("<Control-n>", lambda _event: self.new_project())
        self.bind_all("<Control-o>", lambda _event: self.open_project())
        self.bind_all("<Control-e>", lambda _event: self.export_current())
        self.bind_all("<F5>", lambda _event: self.refresh_projects())
        self.bind_all("<Control-f>", lambda _event: self.search_entry.focus_set())
        for index, (key, _label) in enumerate(PAGES, start=1):
            self.bind_all(f"<Control-Key-{index}>", partial(self._on_page_shortcut, key))

    def _on_page_shortcut(self, page: str, _event: object) -> None:
        self._select_page(page)

    def _on_mousewheel(self, event: tk.Event[tk.Misc]) -> None:
        units = -1 if event.delta > 0 else 1
        try:
            widget: Any = self.winfo_containing(event.x_root, event.y_root)
        except KeyError:  # Tcl-only widgets such as a combobox popdown
            return
        while widget is not None:
            handler = getattr(widget, "wheel_handler", None)
            if handler is not None:
                handler(units)
                return
            if isinstance(widget, (tk.Listbox, tk.Text)):
                widget.yview_scroll(units * 2, "units")
                return
            if isinstance(widget, ScrollFrame):
                widget.scroll_wheel(units * 2)
                return
            widget = widget.master

    # ------------------------------------------------------------------ pages

    def _page(self, key: str) -> tk.Frame:
        page = ScrollFrame(self.content)
        page.grid(row=0, column=0, sticky="nsew")
        self._page_frames[key] = page
        body = page.body
        body.columnconfigure(0, weight=1)
        return body

    def _card(self, body: tk.Misc, row: int, title: str, subtitle: str = "") -> ttk.Frame:
        outer, inner = make_card(body, title, subtitle or None)
        outer.grid(
            row=row,
            column=0,
            sticky="ew",
            padx=theme.px(24),
            pady=(theme.px(20) if row == 0 else theme.px(14), 0),
        )
        return inner

    def _build_welcome_page(self) -> None:
        page = ScrollFrame(self.content, background=theme.CARD)
        page.grid(row=0, column=0, sticky="nsew")
        self.welcome_page = page
        body = page.body
        body.columnconfigure(0, weight=1)
        self.hero_label = tk.Label(body, background=theme.CARD, borderwidth=0)
        self.hero_label.grid(row=0, column=0, sticky="ew", padx=theme.px(24), pady=(theme.px(8), 0))
        text = ttk.Frame(body)
        text.grid(row=1, column=0, sticky="ew", padx=theme.px(32), pady=(theme.px(22), 0))
        ttk.Label(text, text="开始制作你的原创角色", style="Big.TLabel").pack(anchor="w")
        ttk.Label(
            text,
            text="以游戏里已有的角色为底子，换上别的角色的部位贴图、给技能加道具、加上残影，"
            "然后一键导出可以直接运行的游戏 EXE。",
            style="Muted.TLabel",
            wraplength=theme.px(720),
        ).pack(fill="x", pady=(theme.px(6), theme.px(16)))
        buttons = ttk.Frame(text)
        buttons.pack(anchor="w")
        ttk.Button(
            buttons, text="＋  新建角色", style="Big.Accent.TButton", command=self.new_project
        ).pack(side="left")
        ttk.Button(buttons, text="打开项目文件夹…", command=self.open_project).pack(
            side="left", padx=(theme.px(10), 0), ipady=theme.px(4)
        )
        steps = ttk.Frame(body)
        steps.grid(row=2, column=0, sticky="ew", padx=theme.px(32), pady=(theme.px(26), 0))
        for column, (number, title, desc) in enumerate(
            (
                ("1", "选游戏和模板", "先选要改的游戏版本，再选一个现有角色当底子。"),
                ("2", "编辑角色", "名字、属性、部位贴图、技能道具、残影，想改哪项改哪项。"),
                ("3", "校验并导出", "工具会先检查问题，再生成新的游戏 EXE，原游戏不会被改动。"),
            )
        ):
            steps.columnconfigure(column, weight=1, uniform="steps")
            card = tk.Frame(steps, background=theme.PAGE)
            card.grid(
                row=0, column=column, sticky="nsew", padx=(0 if column == 0 else theme.px(12), 0)
            )
            badge = tk.Label(
                card,
                text=number,
                background=theme.ACCENT,
                foreground="#ffffff",
                font=theme.font(10, "bold"),
                width=2,
            )
            badge.pack(anchor="w", padx=theme.px(16), pady=(theme.px(16), theme.px(8)))
            tk.Label(
                card,
                text=title,
                background=theme.PAGE,
                foreground=theme.TEXT,
                font=theme.font(11, "bold"),
            ).pack(anchor="w", padx=theme.px(16))
            tk.Label(
                card,
                text=desc,
                background=theme.PAGE,
                foreground=theme.TEXT_MUTED,
                font=theme.font(9),
                wraplength=theme.px(220),
                justify="left",
                anchor="w",
            ).pack(fill="x", padx=theme.px(16), pady=(theme.px(4), theme.px(16)))
        recent = ttk.Frame(body)
        recent.grid(
            row=3, column=0, sticky="ew", padx=theme.px(32), pady=(theme.px(26), theme.px(24))
        )
        ttk.Label(recent, text="最近的项目", style="Section.TLabel").pack(anchor="w")
        self.recent_box = ttk.Frame(recent)
        self.recent_box.pack(fill="x", pady=(theme.px(6), 0))
        body.bind("<Configure>", lambda _event: self._schedule_hero(), add="+")

    def _schedule_hero(self) -> None:
        if self._hero_after is not None:
            self.after_cancel(self._hero_after)
        self._hero_after = self.after(120, self._render_hero)

    def _render_hero(self) -> None:
        self._hero_after = None
        if self._art is None:
            self.hero_label.grid_remove()
            return
        width = self.welcome_page.body.winfo_width() - theme.px(48)
        if width < 100 or self._photos.get("hero_width") == width:
            return
        height = max(theme.px(200), min(theme.px(280), int(width * 0.3)))
        image = theme.rounded_image(
            theme.cover_image(self._art, (width, height), focus_y=0.3), theme.px(16)
        )
        self._photos["hero"] = theme.to_photo(image)
        self._photos["hero_width"] = width
        self.hero_label.configure(image=self._photos["hero"])

    def _refresh_recent(self) -> None:
        for child in self.recent_box.winfo_children():
            child.destroy()
        rows = project_list_rows(self._summaries)[:5]
        if not rows:
            ttk.Label(
                self.recent_box, text="还没有项目，点上面的“新建角色”开始。", style="Muted.TLabel"
            ).pack(anchor="w")
        for row in rows:
            line = ttk.Frame(self.recent_box)
            line.pack(fill="x")
            button = ttk.Button(
                line,
                text=row.title,
                style="Ghost.TButton",
                command=partial(self._on_project_clicked, row.path),
            )
            button.pack(side="left")
            ttk.Label(line, text=row.subtitle, style="Muted.TLabel").pack(
                side="left", padx=(theme.px(6), 0)
            )

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        target: str,
        hint: str = "",
        kind: str = "entry",
        columnspan: int = 1,
    ) -> tk.Widget:
        cell = ttk.Frame(parent)
        cell.grid(
            row=row,
            column=column,
            columnspan=columnspan,
            sticky="new",
            padx=(0 if column == 0 else theme.px(18), 0),
            pady=(0, theme.px(6)),
        )
        cell.columnconfigure(0, weight=1)
        ttk.Label(cell, text=label, style="Field.TLabel").grid(row=0, column=0, sticky="w")
        widget: tk.Widget
        if kind == "spin":
            widget = ttk.Spinbox(cell, textvariable=variable, from_=0, to=9999, increment=10)
        else:
            widget = ttk.Entry(cell, textvariable=variable)
        widget.grid(row=1, column=0, sticky="ew", pady=(theme.px(3), 0))
        message = ttk.Label(cell, text=hint, style="Hint.TLabel", wraplength=theme.px(380))
        message.grid(row=2, column=0, sticky="ew", pady=(theme.px(2), 0))
        self.target_widgets[target] = widget
        self._field_messages[target] = (widget, message, hint)
        variable.trace_add("write", self._on_form_changed)
        return widget

    def _build_basic_page(self) -> None:
        body = self._page("basic")
        self.id_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.name_zh_var = tk.StringVar()
        self.desc_var = tk.StringVar()
        self.hp_var = tk.StringVar()
        self.mp_var = tk.StringVar()
        self.defense_var = tk.StringVar()

        identity = self._card(
            body,
            0,
            "角色身份",
            "角色 ID 是导出后在游戏里注册用的内部名字；名字和说明是玩家看到的。",
        )
        identity.columnconfigure(0, weight=1, uniform="identity")
        identity.columnconfigure(1, weight=1, uniform="identity")
        self._field(
            identity, 0, 0, "角色 ID", self.id_var, "character.id", character_id_hint("lucas")
        )
        self._field(
            identity, 0, 1, "英文名", self.name_var, "character.name", "游戏英文界面显示的名字。"
        )
        self._field(
            identity,
            1,
            0,
            "中文名",
            self.name_zh_var,
            "character.name_zh",
            "游戏中文界面显示的名字。游戏用繁体字（Big5）显示，请用繁体字。",
        )
        self._field(
            identity,
            1,
            1,
            "角色说明",
            self.desc_var,
            "character.description",
            "选人界面里的角色介绍，同样需要繁体字或英文。",
        )

        stats = self._card(body, 1, "基础属性")
        for column, (label, var, target, hint) in enumerate(
            (
                ("HP（生命）", self.hp_var, "stats.hp", "范围 1–9999"),
                ("MP（气）", self.mp_var, "stats.mp", "范围 1–9999"),
                ("防御值", self.defense_var, "stats.defense", "0–9999，越大越难被打破防御"),
            )
        ):
            stats.columnconfigure(column, weight=1, uniform="stats")
            self._field(stats, 0, column, label, var, target, hint, kind="spin")

        target_card = self._card(
            body, 2, "目标游戏", "导出时以这个游戏为底包生成新的 EXE，原来的游戏文件不会被修改。"
        )
        target_card.columnconfigure(0, weight=1)
        self.target_name_label = ttk.Label(target_card, text="", style="Strong.TLabel")
        self.target_name_label.grid(row=0, column=0, sticky="w")
        self.target_path_label = ttk.Label(target_card, text="", style="Hint.TLabel")
        self.target_path_label.grid(row=1, column=0, sticky="w", pady=(theme.px(2), 0))
        self.target_notice = ttk.Label(
            target_card, text="", style="Warn.TLabel", wraplength=theme.px(620)
        )
        self.target_notice.grid(row=2, column=0, columnspan=2, sticky="ew")
        change = ttk.Button(target_card, text="更换目标游戏…", command=self.choose_target_game)
        change.grid(row=0, column=1, rowspan=2, sticky="e")
        self.target_widgets["target_game.source_path"] = change
        self._field_messages["target_game.source_path"] = (change, self.target_notice, "")

        random_card = self._card(
            body,
            3,
            "随机生成",
            "随机设置 HP/MP/防御、技能道具帧和部位贴图，角色 ID 和目标游戏保持不变。"
            "生成后不满意，可以点右上角的“放弃修改”。",
        )
        ttk.Button(
            random_card, text="🎲  随机生成一个角色", command=self.randomize_current_project
        ).pack(anchor="w")
        tk.Frame(body, height=theme.px(24), background=theme.PAGE).grid(row=9, column=0)

    def _build_textures_page(self) -> None:
        body = self._page("textures")
        card = self._card(
            body,
            0,
            "部位贴图",
            "每个部位都可以换成其他角色同一部位的贴图（取自 HF-EX 按 Lucas 骨骼对齐的版本，"
            "导出到哪个游戏都不会错位）；“保持模板原样”表示这个部位不改。",
        )
        toolbar = ttk.Frame(card)
        toolbar.pack(fill="x", pady=(0, theme.px(8)))
        self.texture_summary = ttk.Label(toolbar, text="", style="Accent.TLabel")
        self.texture_summary.pack(side="left")
        role_labels = tuple(role.label for role in self.texture_roles)
        ttk.Button(
            toolbar, text="全部恢复原样", style="Ghost.TButton", command=self._reset_textures
        ).pack(side="right")
        bulk = ttk.Frame(card)
        bulk.pack(fill="x", pady=(0, theme.px(14)))
        ttk.Label(bulk, text="把全部部位换成", style="Muted.TLabel").pack(side="left")
        self.texture_all_var = tk.StringVar(value=role_labels[1] if len(role_labels) > 1 else "")
        ttk.Combobox(
            bulk,
            textvariable=self.texture_all_var,
            values=role_labels,
            state="readonly",
            width=18,
        ).pack(side="left", padx=theme.px(6))
        ttk.Button(
            bulk, text="应用到全部", style="Small.TButton", command=self._apply_texture_to_all
        ).pack(side="left")
        grid = ttk.Frame(card)
        grid.pack(fill="x")
        self.texture_vars: dict[str, tk.StringVar] = {}
        self.texture_name_labels: dict[str, ttk.Label] = {}
        for index, part in enumerate(self.texture_parts):
            row, column = divmod(index, 2)
            grid.columnconfigure(column * 2 + 1, weight=1, uniform="tex")
            label = ttk.Label(grid, text=part.label, width=8)
            label.grid(
                row=row,
                column=column * 2,
                sticky="w",
                padx=(theme.px(18) if column else 0, theme.px(10)),
                pady=theme.px(5),
            )
            var = tk.StringVar()
            var.trace_add("write", self._on_form_changed)
            combo = ttk.Combobox(grid, textvariable=var, values=role_labels, state="readonly")
            combo.grid(row=row, column=column * 2 + 1, sticky="ew", pady=theme.px(5))
            self.texture_vars[part.part_id] = var
            self.texture_name_labels[part.part_id] = label
            Tooltip(label, partial(self._texture_tip, part.part_id))
            self.target_widgets[f"textures.{part.part_id}"] = combo

    def _build_skills_page(self) -> None:
        body = self._page("skills")
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=1)
        left_outer, left = make_card(body, "动作", padding=14)
        left_outer.grid(
            row=0,
            column=0,
            rowspan=4,
            sticky="nsew",
            padx=(theme.px(24), 0),
            pady=(theme.px(20), theme.px(24)),
        )
        self.action_filter_var = tk.StringVar()
        self.action_filter_var.trace_add("write", lambda *_args: self._refresh_action_list())
        search = ttk.Entry(left, textvariable=self.action_filter_var, width=24)
        search.pack(fill="x")
        add_placeholder(search, "搜索动作名或中文招式名")
        self.action_family_label = ttk.Label(
            left, text="", style="Hint.TLabel", wraplength=theme.px(240)
        )
        self.action_family_label.pack(fill="x", pady=(theme.px(6), 0))
        only_row = ttk.Frame(left)
        only_row.pack(fill="x", pady=(theme.px(8), 0))
        self.action_item_only_var = tk.BooleanVar(value=False)
        self.action_item_only_var.trace_add("write", lambda *_args: self._refresh_action_list())
        only_switch = ToggleSwitch(only_row, self.action_item_only_var)
        only_switch.pack(side="left")
        only_label = ttk.Label(only_row, text="只看会生成道具的动作", cursor="hand2")
        only_label.pack(side="left", padx=(theme.px(8), 0))
        only_label.bind("<Button-1>", only_switch.click)
        Tooltip(only_label, "原版有某一帧会生成道具的动作，一般就是放飞行道具、召唤物的技能。")
        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True, pady=(theme.px(8), 0))
        self.action_list = tk.Listbox(list_frame, height=20, width=28, exportselection=False)
        theme.style_listbox(self.action_list)
        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.action_list.yview,
            style="Thin.Vertical.TScrollbar",
        )
        self.action_list.configure(yscrollcommand=scrollbar.set)
        self.action_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.action_list.bind("<<ListboxSelect>>", self._on_action_list_select)
        ListboxTooltip(self.action_list, self._action_tooltip)
        ttk.Label(left, text="紫色 = 已改过道具的动作", style="Hint.TLabel").pack(
            anchor="w", pady=(theme.px(8), 0)
        )
        self.target_widgets["item_frame_edits.action_name"] = self.action_list

        right = tk.Frame(body, background=theme.PAGE)
        right.grid(row=0, column=1, sticky="nsew", padx=(theme.px(14), theme.px(24)))
        right.columnconfigure(0, weight=1)
        frames_outer, frames = make_card(right, "选择帧", padding=16)
        frames_outer.grid(row=0, column=0, sticky="ew", pady=(theme.px(20), 0))
        self.frame_caption = ttk.Label(frames_outer.header, text="", style="Muted.TLabel")
        self.frame_caption.pack(side="left", padx=(theme.px(10), 0))
        self.frame_strip = FrameStrip(frames, self._select_frame)
        self.frame_strip.pack(fill="x")
        legend = ttk.Frame(frames)
        legend.pack(fill="x", pady=(theme.px(8), 0))
        ttk.Label(
            legend,
            text="粉点＝已修改　灰点＝原版这帧就会生成道具",
            style="Hint.TLabel",
            wraplength=theme.px(400),
        ).pack(fill="x")
        Tooltip(self.frame_strip, "点击选择帧；选中后可以用键盘 ← → 切换。")

        slots_outer, slots = make_card(right, "这一帧生成的道具", padding=16)
        slots_outer.grid(row=1, column=0, sticky="ew", pady=(theme.px(14), 0))
        self.restore_frame_button = ttk.Button(
            slots_outer.header,
            text="恢复本帧原样",
            style="Ghost.TButton",
            command=self._restore_current_frame,
        )
        self.restore_frame_button.pack(side="right")
        slot_tools = ttk.Frame(slots)
        slot_tools.pack(fill="x", pady=(0, theme.px(6)))
        self.paste_frame_button = ttk.Button(
            slot_tools, text="粘贴到本帧", style="Small.TButton", command=self._paste_frame
        )
        self.paste_frame_button.pack(side="right")
        self.copy_frame_button = ttk.Button(
            slot_tools, text="复制本帧", style="Small.TButton", command=self._copy_frame
        )
        self.copy_frame_button.pack(side="right", padx=(0, theme.px(6)))
        Tooltip(self.copy_frame_button, "复制这一帧的全部道具设置，再到别的帧点“粘贴到本帧”。")
        self.item_catalog_status = ttk.Label(
            slot_tools, text="", style="Hint.TLabel", wraplength=theme.px(200)
        )
        self.item_catalog_status.pack(side="left", fill="x", expand=True)
        self.item_slot_vars: list[dict[str, tk.StringVar]] = []
        self.item_slot_widgets: list[dict[str, Any]] = []
        for index in range(3):
            self._build_slot_row(slots, index)

        summary_outer, summary = make_card(
            right, "已修改的帧", "点击可以跳过去继续编辑。", padding=16
        )
        summary_outer.grid(row=2, column=0, sticky="ew", pady=(theme.px(14), theme.px(24)))
        self.edits_box = ttk.Frame(summary)
        self.edits_box.pack(fill="x")

    def _build_slot_row(self, parent: ttk.Frame, index: int) -> None:
        row = tk.Frame(parent, background=theme.CARD)
        row.pack(fill="x", pady=(theme.px(10) if index else 0, 0))
        if index:
            tk.Frame(row, height=1, background=theme.BORDER).pack(fill="x", pady=(0, theme.px(10)))
        top = ttk.Frame(row)
        top.pack(fill="x")
        ttk.Label(top, text=f"道具 {index + 1}", style="Strong.TLabel", width=7).pack(side="left")
        vars_for_slot = {"item": tk.StringVar(value=disabled_item_label())}
        for field in SLOT_FIELDS:
            vars_for_slot[field] = tk.StringVar()
        combo = SearchCombobox(top, vars_for_slot["item"], width=34)
        combo.pack(side="left", fill="x", expand=True)
        Tooltip(combo, "可以直接输入道具编号或名字筛选，选“无”表示这个槽不生成道具。")
        clear = ttk.Button(
            top,
            text="清空",
            style="Ghost.TButton",
            command=partial(vars_for_slot["item"].set, disabled_item_label()),
        )
        clear.pack(side="left", padx=(theme.px(6), 0))
        coords = ttk.Frame(row)
        coords.pack(fill="x", pady=(theme.px(4), 0))
        for column in (2, 4, 6):
            coords.columnconfigure(column, weight=1, uniform="coord")
        widgets: dict[str, Any] = {"item": combo, "clear": clear}
        groups = (
            (
                "出现位置",
                ("x", "y", "z"),
                "相对角色的位置：x 越大越靠前，y 负数越靠上，z 是前后层次。",
            ),
            (
                "初速度",
                ("vx", "vy", "vz"),
                "道具出现时的速度：vx 正数向前飞，vy 负数向上，vz 前后方向。",
            ),
        )
        for group_index, (title, fields, tip) in enumerate(groups):
            caption = ttk.Label(coords, text=title, style="Field.TLabel", width=7)
            caption.grid(row=group_index, column=0, sticky="w", pady=(theme.px(4), 0))
            Tooltip(caption, tip)
            for field_index, field in enumerate(fields):
                ttk.Label(coords, text=field, style="Subtle.TLabel").grid(
                    row=group_index,
                    column=1 + field_index * 2,
                    sticky="e",
                    padx=(theme.px(10) if field_index else theme.px(4), theme.px(4)),
                    pady=(theme.px(4), 0),
                )
                entry = ttk.Entry(coords, textvariable=vars_for_slot[field], width=6)
                entry.grid(
                    row=group_index, column=2 + field_index * 2, sticky="ew", pady=(theme.px(4), 0)
                )
                widgets[field] = entry
                self.target_widgets[f"slot.{index}.{field}"] = entry
        self.target_widgets[f"slot.{index}.item_action_group"] = combo
        for var in vars_for_slot.values():
            var.trace_add("write", self._on_slot_changed)
        self.item_slot_vars.append(vars_for_slot)
        self.item_slot_widgets.append(widgets)

    def _build_afterimage_page(self) -> None:
        body = self._page("afterimage")
        timeline = self._card(
            body,
            0,
            "残影时间轴",
            "残影会重演角色若干帧之前的动作和走位（30帧约1秒），"
            "半透明显示，不能被攻击、不能被抓。"
            "在时间轴上点击或拖动即可添加 / 删除残影。",
        )
        self.delay_timeline = DelayTimeline(timeline, self._on_timeline_changed)
        self.delay_timeline.pack(fill="x")
        summary_row = ttk.Frame(timeline)
        summary_row.pack(fill="x", pady=(theme.px(10), 0))
        self.afterimage_summary_label = ttk.Label(
            summary_row, text="", style="Strong.TLabel", wraplength=theme.px(620)
        )
        self.afterimage_summary_label.pack(side="left", fill="x", expand=True)
        self.afterimage_clear_button = ttk.Button(
            summary_row, text="全部删除", style="Ghost.TButton", command=self.clear_afterimages
        )
        self.afterimage_clear_button.pack(side="right")
        self.afterimage_warning = ttk.Label(timeline, text="", style="Warn.TLabel")
        self.afterimage_warning.pack(anchor="w")
        self.target_widgets["afterimage.delays"] = self.delay_timeline

        quick = self._card(body, 1, "快速添加")
        self.afterimage_single_var = tk.StringVar(value="10")
        self.afterimage_count_var = tk.StringVar(value="20")
        self.afterimage_start_var = tk.StringVar(value="1")
        self.afterimage_step_var = tk.StringVar(value="1")
        single = ttk.Frame(quick)
        single.pack(fill="x")
        ttk.Label(single, text="单个", style="Strong.TLabel", width=6).pack(side="left")
        ttk.Label(single, text="延迟").pack(side="left")
        ttk.Spinbox(
            single,
            textvariable=self.afterimage_single_var,
            from_=1,
            to=AFTERIMAGE_MAX_DELAY,
            width=5,
        ).pack(side="left", padx=theme.px(6))
        ttk.Label(single, text="帧").pack(side="left")
        ttk.Button(
            single, text="添加", style="Small.TButton", command=self.add_single_afterimage
        ).pack(side="left", padx=(theme.px(12), 0))
        batch = ttk.Frame(quick)
        batch.pack(fill="x", pady=(theme.px(12), 0))
        ttk.Label(batch, text="批量", style="Strong.TLabel", width=6).pack(side="left")
        for text, var in (
            ("", self.afterimage_count_var),
            ("个，从第", self.afterimage_start_var),
            ("帧开始，每个间隔", self.afterimage_step_var),
        ):
            if text:
                ttk.Label(batch, text=text).pack(side="left")
            ttk.Spinbox(batch, textvariable=var, from_=1, to=AFTERIMAGE_MAX_DELAY, width=4).pack(
                side="left", padx=theme.px(6)
            )
            var.trace_add("write", lambda *_args: self._refresh_quick_preview())
        ttk.Label(batch, text="帧").pack(side="left")
        ttk.Button(
            batch, text="批量添加", style="Small.TButton", command=self.quick_add_afterimages
        ).pack(side="left", padx=(theme.px(12), 0))
        self.quick_preview = ttk.Label(quick, text="", style="Hint.TLabel")
        self.quick_preview.pack(anchor="w", padx=(theme.px(56), 0), pady=(theme.px(4), 0))
        presets = ttk.Frame(quick)
        presets.pack(fill="x", pady=(theme.px(12), 0))
        ttk.Label(presets, text="常用", style="Strong.TLabel", width=6).pack(side="left")
        for label, count, start, step in AFTERIMAGE_PRESETS:
            ttk.Button(
                presets,
                text=label,
                style="Small.TButton",
                command=partial(self._apply_afterimage_preset, count, start, step),
            ).pack(side="left", padx=(0, theme.px(8)))

        behavior = self._card(body, 2, "残影行为")
        self.afterimage_damage_var = tk.BooleanVar(value=True)
        self.afterimage_items_var = tk.BooleanVar(value=True)
        self._toggles: list[ToggleSwitch] = []
        for row_index, (toggle_var, title, desc) in enumerate(
            (
                (
                    self.afterimage_damage_var,
                    "残影可以造成伤害",
                    "拳脚、技能命中和抓投都会真实生效，伤害和击杀算在本体头上。关闭后残影只是画面效果，碰不到任何人。",
                ),
                (
                    self.afterimage_items_var,
                    "残影可以发射道具",
                    "本体发出的飞行道具、召唤物会在延迟之后由残影再发一次，命中算本体的。",
                ),
            )
        ):
            line = ttk.Frame(behavior)
            line.pack(fill="x", pady=(theme.px(14) if row_index else 0, 0))
            switch = ToggleSwitch(line, toggle_var)
            switch.pack(side="left", anchor="n", pady=(theme.px(2), 0))
            texts = ttk.Frame(line)
            texts.pack(side="left", fill="x", expand=True, padx=(theme.px(14), 0))
            title_label = ttk.Label(texts, text=title, style="Strong.TLabel", cursor="hand2")
            title_label.pack(anchor="w")
            title_label.bind("<Button-1>", switch.click)
            ttk.Label(texts, text=desc, style="Muted.TLabel", wraplength=theme.px(600)).pack(
                fill="x"
            )
            toggle_var.trace_add("write", self._on_form_changed)
            self._toggles.append(switch)
        tk.Frame(body, height=theme.px(24), background=theme.PAGE).grid(row=9, column=0)
        self._refresh_quick_preview()

    def _build_export_page(self) -> None:
        body = self._page("export")
        export = self._card(body, 0, "导出游戏")
        self.export_intro = ttk.Label(
            export, text="", style="Muted.TLabel", wraplength=theme.px(680)
        )
        self.export_intro.pack(fill="x")
        row = ttk.Frame(export)
        row.pack(fill="x", pady=(theme.px(14), 0))
        self.export_start_button = ttk.Button(
            row, text="开始导出", style="Big.Accent.TButton", command=self.export_current
        )
        self.export_start_button.pack(side="left")
        status = ttk.Frame(row)
        status.pack(side="left", fill="x", expand=True, padx=(theme.px(18), 0))
        self.export_status = ttk.Label(status, text="", style="Strong.TLabel")
        self.export_status.pack(anchor="w")
        self.export_progress = ttk.Progressbar(
            status, mode="indeterminate", style="Accent.Horizontal.TProgressbar"
        )

        issues = self._card(body, 1, "校验结果")
        self.issues_caption = ttk.Label(issues, text="", style="Hint.TLabel")
        self.issues_caption.pack(anchor="w")
        self.issues_box = ttk.Frame(issues)
        self.issues_box.pack(fill="x")

        self.result_outer, result = make_card(body, "导出结果")
        self.result_box = result
        history_outer, history = make_card(body, "导出记录", "这个项目以前导出过的游戏。")
        history_outer.grid(
            row=3, column=0, sticky="ew", padx=theme.px(24), pady=(theme.px(14), theme.px(24))
        )
        self.history_box = history

    # ------------------------------------------------------------------ navigation

    def _select_page(self, key: str) -> None:
        if self.current_project_dir is None or key not in self._page_frames:
            return
        self._active_page = key
        self._page_frames[key].tkraise()
        self.tabbar.select([page for page, _label in PAGES].index(key))
        if self._busy:
            self.busy_overlay.tkraise()

    def _show_welcome(self) -> None:
        self.tabbar.grid_remove()
        self.welcome_page.tkraise()
        self.header_title.configure(text="欢迎使用")
        self.header_meta.configure(text="新建一个角色，或者从左侧打开已有项目。")
        for button in (self.save_button, self.export_button):
            button.state(["disabled"])
        self._set_dirty(False)
        self.validation_pill.set("")
        self._refresh_recent()
        self._schedule_hero()

    def _show_editor(self) -> None:
        self.tabbar.grid(row=1, column=0, sticky="ew", pady=(theme.px(8), 0))
        self._select_page(self._active_page)
        self._sync_header_buttons()

    def _sync_header_buttons(self) -> None:
        has_project = self.current_project_dir is not None
        idle = not self._busy and not self._exporting
        for button in (self.save_button, self.export_button, self.export_start_button):
            button.state(["!disabled"] if has_project and idle else ["disabled"])
        self.discard_button.state(["!disabled"] if idle else ["disabled"])
        for canvas_button in (self.new_button, self.open_button, self.refresh_button):
            canvas_button.set_enabled(idle)

    # ------------------------------------------------------------------ background work

    def _run_background(
        self, message: str, work: Callable[[], T], done: Callable[[T], None]
    ) -> None:
        self._busy = True
        self._sync_header_buttons()
        self._status(message, "busy")
        self.busy_label.configure(text=message)
        self._busy_after = self.after(250, self._show_busy_overlay)

        def finish(result: T) -> None:
            self._end_busy()
            done(result)

        def failed(exc: BaseException) -> None:
            self._end_busy()
            self._status("操作失败", "danger")
            messagebox.showerror("操作失败", f"{type(exc).__name__}: {exc}", parent=self)

        run_in_thread(self, work, finish, failed)

    def _show_busy_overlay(self) -> None:
        self._busy_after = None
        if self._busy:
            self.busy_overlay.grid(row=0, column=0, sticky="nsew")
            self.busy_overlay.tkraise()
            self.busy_progress.start(12)

    def _end_busy(self) -> None:
        self._busy = False
        if self._busy_after is not None:
            self.after_cancel(self._busy_after)
            self._busy_after = None
        self.busy_progress.stop()
        self.busy_overlay.grid_remove()
        self._status("就绪")
        self._sync_header_buttons()

    def _load_context_async(
        self, target: TargetGame, role_id: str, message: str, done: Callable[[TargetContext], None]
    ) -> None:
        cached = self._target_cache_entries.get((target.source_path, target.source_kind))
        workspace = self.workspace
        self._run_background(
            message, lambda: compute_target_context(workspace, target, role_id, cached), done
        )

    def _apply_context(self, context: TargetContext) -> None:
        self._context = context
        if context.cache is not None:
            key = (context.target.source_path, context.target.source_kind)
            self._target_cache_entries[key] = context.cache
        self.spt_actions = context.actions
        self.action_by_name = {action.action_name: action for action in context.actions}
        self._action_family = infer_family(action.action_name for action in context.actions)
        family = family_label(self._action_family)
        self.action_family_label.configure(
            text=f"中文招式名按「{family}」的招式表显示。"
            if family
            else "没认出这个模板是哪个角色，只显示通用动作的中文名。"
        )
        catalog = context.item_catalog
        self.item_options = catalog.options if catalog.available else ()
        self.item_catalog_status.configure(text=context.item_status)
        labels = (disabled_item_label(), *(option.label for option in self.item_options))
        for widgets in self.item_slot_widgets:
            widgets["item"].set_values(labels)
        self._base_slot_cache.clear()
        notice = ""
        if context.problem is not None:
            notice = f"⚠ {context.problem[0]}：{context.problem[1]}"[:400]
        self.target_notice.configure(text=notice)
        self._field_messages["target_game.source_path"] = (
            self._field_messages["target_game.source_path"][0],
            self.target_notice,
            notice,
        )

    # ------------------------------------------------------------------ projects

    def refresh_projects(self) -> None:
        if self._busy:
            return
        self._summaries = scan_projects(self.projects_root)
        self._refresh_project_rows()
        if self.current_project_dir is None:
            self._refresh_recent()

    def _refresh_project_rows(self) -> None:
        query = self.search_var.get()
        rows = project_list_rows(self._summaries, query)
        empty = "没有匹配的项目" if query.strip() else "还没有项目，点上面的“新建角色”开始"
        self.project_list.set_rows(rows, empty)
        self.project_list.set_current(self.current_project_dir)
        total = len(self._summaries)
        caption = f"项目  {len(rows)} / {total}" if query.strip() else f"项目  {total}"
        self.surface.itemconfigure(self._list_caption, text=caption)

    def _on_project_clicked(self, path: Path) -> None:
        if self._busy or self._exporting:
            return
        if self.current_project_dir is not None and path == self.current_project_dir:
            return
        if self._confirm_leave():
            self.load_into_form(path)

    def _project_menu(self, path: Path, x: int, y: int) -> None:
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="打开", command=lambda: self._on_project_clicked(path))
        menu.add_command(label="在资源管理器中显示", command=lambda: open_in_explorer(path))
        menu.add_command(label="复制项目路径", command=lambda: self._copy(str(path)))
        menu.add_separator()
        menu.add_command(label="复制为新项目…", command=lambda: self.duplicate_project(path))
        menu.add_command(label="删除项目…", command=lambda: self.delete_project(path))
        menu.tk_popup(x, y)

    def duplicate_project(self, path: Path) -> None:
        if self._busy or self._exporting:
            return
        try:
            source = load_project(path)
        except ProjectError as exc:
            messagebox.showerror("无法复制", f"{exc.summary}\n{exc.detail}", parent=self)
            return
        used = {summary.character_id for summary in self._summaries}
        dialog = DuplicateProjectDialog(self, source, used)
        self.wait_window(dialog)
        if dialog.result is None or not self._confirm_leave():
            return
        name, cid = dialog.result
        try:
            duplicate_project(self.projects_root, path, name, cid)
        except (ProjectError, OSError) as exc:
            messagebox.showerror("复制失败", str(exc), parent=self)
            return
        self.refresh_projects()
        self.load_into_form(self.projects_root / name)

    def delete_project(self, path: Path) -> None:
        if self._busy or self._exporting:
            return
        if path.parent.resolve() != self.projects_root.resolve():
            messagebox.showinfo("不能删除", "只能删除工具 projects 文件夹里的项目。", parent=self)
            return
        if not messagebox.askyesno(
            "删除项目",
            f"把「{path.name}」移到 projects\\{TRASH_DIR_NAME} 文件夹？\n\n"
            "导出过的游戏不会被删除；以后想恢复，把它从这个文件夹拖回 projects 即可。",
            parent=self,
        ):
            return
        try:
            move_project_to_trash(self.projects_root, path)
        except OSError as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)
            return
        if self.current_project_dir is not None and path == self.current_project_dir:
            self.current_project_dir = None
            self._project = None
            self._dirty = False
            self._show_welcome()
        self.refresh_projects()
        self._toast(f"已把「{path.name}」移到 {TRASH_DIR_NAME}", "neutral")

    def _copy(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self._toast("已复制到剪贴板", "success")

    def new_project(self, initial_target: TargetGame | None = None) -> None:
        if self._busy or self._exporting or not self._confirm_leave():
            return
        dialog = NewProjectDialog(self, initial_target)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        name, cid, template_id, target_game, source_role_id = dialog.result
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
        except ProjectError as exc:
            messagebox.showerror("新建失败", f"{exc.summary}\n{exc.detail}", parent=self)
            return
        self.refresh_projects()
        self._active_page = "basic"
        self.load_into_form(self.projects_root / name)

    def open_project(self) -> None:
        if self._busy or self._exporting or not self._confirm_leave():
            return
        path = filedialog.askdirectory(
            title="选择角色项目文件夹", initialdir=str(self.projects_root), parent=self
        )
        if not path:
            return
        if not (Path(path) / PROJECT_FILE).is_file():
            messagebox.showwarning(
                "不是角色项目",
                f"这个文件夹里没有 {PROJECT_FILE}，请选择一个角色项目文件夹。",
                parent=self,
            )
            return
        self.load_into_form(Path(path))

    def load_into_form(self, project_dir: Path) -> None:
        try:
            project = load_project(project_dir)
        except ProjectError as exc:
            messagebox.showerror("无法打开项目", f"{exc.summary}\n{exc.detail}", parent=self)
            return
        role = project.source_role_id or "lucas"

        def done(context: TargetContext) -> None:
            self.current_project_dir = project_dir
            self._project = project
            self._source_role_id = role
            self._apply_context(context)
            self._populate(project)
            self._show_editor()
            self.project_list.set_current(project_dir)
            self._toast(f"已打开「{project.project_name}」", "success")

        self._load_context_async(
            project.target_game, role, f"正在打开「{project.project_name}」…", done
        )

    def _confirm_leave(self) -> bool:
        if not self._dirty or self._project is None:
            return True
        answer = messagebox.askyesnocancel(
            "保存修改？",
            f"「{self._project.project_name}」有未保存的修改，要先保存吗？",
            parent=self,
        )
        if answer is None:
            return False
        if answer:
            return self.save_current()
        return True

    def _on_close(self) -> None:
        if self._exporting and not messagebox.askyesno(
            "导出还没完成", "现在退出会中断导出，确定退出吗？", parent=self
        ):
            return
        if self._confirm_leave():
            self.destroy()

    # ------------------------------------------------------------------ form <-> project

    def _populate(self, project: CharacterProject) -> None:
        form = project_to_form(project)
        self._populating = True
        try:
            self.id_var.set(form.character_id)
            self.name_var.set(form.character_name)
            self.name_zh_var.set(form.character_name_zh)
            self.desc_var.set(form.description)
            self.hp_var.set(form.hp)
            self.mp_var.set(form.mp)
            self.defense_var.set(form.defense)
            self._hidden_skill = (
                form.skill_key or "D>A",
                form.skill_mp_cost or "50",
                form.skill_damage or "35",
                form.skill_speed or "10",
                form.skill_range or "80",
            )
            self._target_game = project.target_game
            self.item_frame_drafts = {}
            for edit in project.item_frame_edits:
                enabled = tuple(slot for slot in edit.slots if slot.enabled)
                if enabled and len(enabled) <= 3:
                    self.item_frame_drafts[(edit.action_name, edit.action_frame)] = enabled
            self._dirty_item_frame_keys.clear()
            self._current_item_frame_key = None
            selections = {part.part_id: KEEP_SOURCE_TEXTURE_ROLE_ID for part in self.texture_parts}
            selections.update(form.texture_selections)
            for part in self.texture_parts:
                self.texture_vars[part.part_id].set(
                    selected_texture_role_label(
                        selections.get(part.part_id, KEEP_SOURCE_TEXTURE_ROLE_ID),
                        self.texture_roles,
                    )
                )
            self._set_afterimage_delays(project.afterimage.delays, user=False)
            self.afterimage_damage_var.set(project.afterimage.damage)
            self.afterimage_items_var.set(project.afterimage.items)
            self.action_filter_var.set("")
            self._selected_action = None
            self._select_action(form.item_action_name, _parse_int(form.item_action_frame, 8))
        finally:
            self._populating = False
        self._showing_export_report = False
        self._set_dirty(False)
        self._refresh_target_card()
        self._refresh_history()
        self.result_outer.grid_remove()
        self.export_status.configure(text="")
        self._refresh_derived()

    def _project_from_form(self) -> CharacterProject:
        assert self._project is not None
        self._cache_current_item_slots()
        role_ids = texture_role_ids_by_label(self.texture_roles)
        form = CharacterForm(
            character_id=self.id_var.get(),
            character_name=self.name_var.get(),
            character_name_zh=self.name_zh_var.get(),
            description=self.desc_var.get(),
            hp=self.hp_var.get(),
            mp=self.mp_var.get(),
            defense=self.defense_var.get(),
            skill_key=self._hidden_skill[0],
            skill_mp_cost=self._hidden_skill[1],
            skill_damage=self._hidden_skill[2],
            skill_speed=self._hidden_skill[3],
            skill_range=self._hidden_skill[4],
            target_source_path=self._target_game.source_path,
            target_source_kind=self._target_game.source_kind,
            item_action_name=self._selected_action or "ball",
            item_action_frame=str(self._selected_frame or 1),
            item_slots=self._current_item_slot_forms(),
            texture_selections={
                key: role_ids.get(var.get(), KEEP_SOURCE_TEXTURE_ROLE_ID)
                for key, var in self.texture_vars.items()
            },
        )
        updated = apply_form(self._project, form)
        return replace_project(
            updated,
            item_frame_edits=frame_item_edits_from_drafts(self.item_frame_drafts),
            afterimage=AfterimageSettings(
                delays=self.afterimage_delays,
                damage=bool(self.afterimage_damage_var.get()),
                items=bool(self.afterimage_items_var.get()),
            ),
        )

    def save_current(self, quiet: bool = False) -> bool:
        if (
            self._project is None
            or self.current_project_dir is None
            or self._busy
            or self._exporting
        ):
            return False
        project = self._project_from_form()
        try:
            save_project(self.current_project_dir, project)
        except ProjectError as exc:
            messagebox.showerror("保存失败", f"{exc.summary}\n{exc.detail}", parent=self)
            return False
        self._project = project
        self._set_dirty(False)
        self._summaries = scan_projects(self.projects_root)
        self._refresh_project_rows()
        self._refresh_derived()
        if not quiet:
            self._toast(f"已保存 {time.strftime('%H:%M')}", "success")
        return True

    def discard_changes(self) -> None:
        if self._project is None or self.current_project_dir is None or not self._dirty:
            return
        if not messagebox.askyesno(
            "放弃修改", "放弃所有未保存的修改，回到上次保存的状态？", parent=self
        ):
            return
        saved_target = self._project.target_game
        if (saved_target.source_path, saved_target.source_kind) != (
            self._target_game.source_path,
            self._target_game.source_kind,
        ):
            self._dirty = False
            self.load_into_form(self.current_project_dir)
            return
        self._populate(self._project)
        self._toast("已恢复到上次保存的状态", "neutral")

    def randomize_current_project(self) -> None:
        if self._project is None:
            return
        if not self.item_options:
            messagebox.showerror(
                "随机生成失败", "当前目标游戏的道具目录不可用，无法随机配置道具。", parent=self
            )
            return
        if not self.spt_actions:
            messagebox.showerror(
                "随机生成失败", "当前角色模板的动作列表不可用，无法随机配置动作帧。", parent=self
            )
            return
        randomized = randomize_project(
            self._project_from_form(),
            self.spt_actions,
            self.item_options,
            self.texture_parts,
            self.texture_roles,
        )
        self._populate(randomized)
        self._set_dirty(True)
        self._toast("已随机生成。满意就保存，不满意点“放弃修改”。", "accent")

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        if dirty:
            self.discard_button.pack(side="left", padx=(0, theme.px(6)), before=self.save_button)
            self.dirty_label.pack(side="left", padx=(theme.px(12), 0), pady=(theme.px(4), 0))
        else:
            self.dirty_label.pack_forget()
            self.discard_button.pack_forget()
        name = self._project.project_name if self._project is not None else ""
        suffix = f" — {name}{' *' if dirty else ''}" if name else ""
        self.title(APP_TITLE + suffix)

    def _on_form_changed(self, *_args: object) -> None:
        if self._populating or self._project is None:
            return
        if not self._dirty:
            self._set_dirty(True)
        if self._showing_export_report:
            self._showing_export_report = False
        if self._refresh_after is not None:
            self.after_cancel(self._refresh_after)
        self._refresh_after = self.after(250, self._refresh_derived)

    def _refresh_derived(self) -> None:
        self._refresh_after = None
        if self._project is None:
            return
        project = self._project_from_form()
        title = project.character_name_zh or project.character_name or project.project_name
        self.header_title.configure(text=title)
        self.header_meta.configure(
            text=f"{project.project_name}  ·  ID {project.character_id}  ·  "
            f"{self._source_role_id} 模板  ·  {target_display_name(self._target_game)}"
        )
        unchanged = {KEEP_SOURCE_TEXTURE_ROLE_ID, self._source_role_id}
        replaced = sum(1 for value in project.texture_selections.values() if value not in unchanged)
        self._refresh_skill_markers()
        edited = len(frame_item_edits_from_drafts(self.item_frame_drafts))
        self.tabbar.set_badge(1, str(replaced) if replaced else "", "accent")
        self.tabbar.set_badge(2, str(edited) if edited else "", "accent")
        self.tabbar.set_badge(
            3, str(len(self.afterimage_delays)) if self.afterimage_delays else "", "accent"
        )
        report = self._live_report(project)
        self._validation_report = report
        self._refresh_texture_labels(replaced)
        text, kind = validation_chip(report)
        self.validation_pill.set(text, kind)
        errors, warnings, _infos = validation_counts(report)
        self.tabbar.set_badge(
            4,
            str(errors) if errors else str(warnings) if warnings else "",
            "danger" if errors else "warning",
        )
        self._refresh_field_messages(report)
        if not self._showing_export_report:
            self._render_issues(report, "随修改实时检查。导出时还会针对目标游戏做完整检查。")
        name = project.character_name_zh or project.character_name
        self.export_intro.configure(
            text=f"将以 {target_display_name(self._target_game)} 为底包，生成包含「{name}」"
            f"（ID {project.character_id}）的新游戏 EXE，保存到 output 文件夹。"
            "原游戏文件不会被修改。"
        )

    def _live_report(self, project: CharacterProject) -> ValidationReport:
        issues = list(validate_editing(project).issues)
        context = self._context
        if context is not None and context.cache is not None:
            groups = (
                frozenset(option.action_group for option in self.item_options)
                if context.item_catalog.available
                else None
            )
            issues.extend(validate_against_target(project, context.cache, self.spt_actions, groups))
        self._texture_warnings = self._texture_donor_warnings(project)
        labels = {part.part_id: part.label for part in self.texture_parts}
        for part_id, detail in self._texture_warnings.items():
            issues.append(
                ValidationIssue(
                    Severity.WARNING,
                    f"{labels.get(part_id, part_id)}选的来源角色没有这个部件，会保持模板原样。",
                    "想换掉这个部位，请换一个有该部件的来源角色。",
                    f"textures.{part_id}",
                    detail,
                )
            )
        return ValidationReport(tuple(issues))

    def _texture_donor_warnings(self, project: CharacterProject) -> dict[str, str]:
        if not donor_catalog_ready(self.workspace):
            return {}
        missing = texture_parts_without_donor(
            self.workspace,
            project.texture_selections,
            self.texture_parts,
            self._source_role_id,
        )
        return {
            part_id: f"来源角色：{project.texture_selections.get(part_id, '')}"
            for part_id in missing
        }

    def _texture_tip(self, part_id: str) -> str:
        detail = self._texture_warnings.get(part_id)
        if not detail:
            return ""
        return "来源角色没有这个部件，导出后保持模板原样。\n" + detail

    def _refresh_target_card(self) -> None:
        self.target_name_label.configure(text=target_display_name(self._target_game))
        self.target_path_label.configure(text=self._target_game.source_path)
        hint = character_id_hint(self._source_role_id)
        widget, label, _old = self._field_messages["character.id"]
        self._field_messages["character.id"] = (widget, label, hint)

    def _refresh_field_messages(self, report: ValidationReport) -> None:
        worst: dict[str, ValidationIssue] = {}
        rank = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        for issue in report.issues:
            current = worst.get(issue.target)
            if current is None or rank[issue.severity] < rank[current.severity]:
                worst[issue.target] = issue
        for target, (widget, label, hint) in self._field_messages.items():
            found = worst.get(target)
            base_style = widget.winfo_class()
            if found is not None and found.severity is not Severity.INFO:
                prefix = "Invalid" if found.severity is Severity.ERROR else "Warn"
                if base_style in {"TEntry", "TSpinbox", "TCombobox"}:
                    cast_widget: Any = widget
                    cast_widget.configure(style=f"{prefix}.{base_style}")
                label.configure(
                    text=f"{found.message} {found.suggestion}".strip(),
                    style="Error.TLabel" if found.severity is Severity.ERROR else "Warn.TLabel",
                )
            else:
                if base_style in {"TEntry", "TSpinbox", "TCombobox"}:
                    cast_widget = widget
                    cast_widget.configure(style=base_style)
                label.configure(
                    text=hint,
                    style="Warn.TLabel" if target == "target_game.source_path" else "Hint.TLabel",
                )

    def choose_target_game(self) -> None:
        if self._project is None or self._busy:
            return
        path = filedialog.askopenfilename(
            title="选择目标 HFE 游戏（EXE 或 SWF）",
            filetypes=(("HFE 游戏", "*.exe *.swf"), ("所有文件", "*.*")),
            parent=self,
        )
        if not path:
            return
        try:
            target = target_game_for_source(path)
        except TargetCacheError as exc:
            messagebox.showwarning(exc.summary, exc.detail, parent=self)
            return

        def done(context: TargetContext) -> None:
            self._target_game = target
            self._apply_context(context)
            current = self._selected_action
            self._selected_action = None
            self._select_action(current or "ball", self._selected_frame or 1)
            self._refresh_target_card()
            self._on_form_changed()
            if context.problem is None:
                self._toast(f"已切换到 {target_display_name(target)}，记得保存。", "accent")

        self._load_context_async(target, self._source_role_id, "正在读取目标游戏…", done)

    # ------------------------------------------------------------------ textures

    def _refresh_texture_labels(self, replaced: int) -> None:
        role_ids = texture_role_ids_by_label(self.texture_roles)
        for part in self.texture_parts:
            changed = role_ids.get(
                self.texture_vars[part.part_id].get(), KEEP_SOURCE_TEXTURE_ROLE_ID
            ) not in {KEEP_SOURCE_TEXTURE_ROLE_ID, self._source_role_id}
            warned = part.part_id in self._texture_warnings
            self.texture_name_labels[part.part_id].configure(
                style="WarnText.TLabel" if warned else "Accent.TLabel" if changed else "TLabel",
                text=f"⚠ {part.label}" if warned else f"● {part.label}" if changed else part.label,
            )
        self.texture_summary.configure(
            text=f"已替换 {replaced} / {len(self.texture_parts)} 个部位"
            if replaced
            else "所有部位都保持模板原样"
        )

    def _reset_textures(self) -> None:
        keep = selected_texture_role_label(KEEP_SOURCE_TEXTURE_ROLE_ID, self.texture_roles)
        for var in self.texture_vars.values():
            var.set(keep)

    def _apply_texture_to_all(self) -> None:
        value = self.texture_all_var.get()
        if value:
            for var in self.texture_vars.values():
                var.set(value)

    # ------------------------------------------------------------------ skills / item frames

    def _refresh_action_list(self) -> None:
        needle = self.action_filter_var.get().strip().lower()
        self._visible_actions = [
            action.action_name
            for action in self.spt_actions
            if (
                not needle
                or needle in action.action_name.lower()
                or needle in self._action_description(action.action_name)
            )
            and (
                not self.action_item_only_var.get()
                or action.item_frames
                or action.action_name in {name for name, _frame in self.item_frame_drafts}
            )
        ]
        self.action_list.delete(0, "end")
        edited_actions = {name for name, _frame in self.item_frame_drafts}
        for index, name in enumerate(self._visible_actions):
            self.action_list.insert("end", self._action_row(name, name in edited_actions))
            if name in edited_actions:
                self.action_list.itemconfigure(index, foreground=theme.ACCENT_TEXT)
        if self._selected_action in self._visible_actions:
            index = self._visible_actions.index(self._selected_action)
            self.action_list.selection_set(index)
            self.action_list.see(index)

    def _action_description(self, action_name: str) -> str:
        return describe_action(action_name, self._action_family)

    def _action_row(self, action_name: str, edited: bool) -> str:
        return action_row_text(action_name, self._action_description(action_name), edited)

    def _action_tooltip(self, index: int) -> str:
        if index >= len(self._visible_actions):
            return ""
        name = self._visible_actions[index]
        action = self.action_by_name[name]
        description = self._action_description(name) or "（参考表里没有这个动作的中文名）"
        items = (
            f"\n原版第 {', '.join(str(f) for f in action.item_frames)} 帧会生成道具"
            if action.item_frames
            else ""
        )
        return f"{description}\n动作代码：{name} · 共 {action.frame_count} 帧{items}"

    def _on_action_list_select(self, _event: object) -> None:
        selection = _listbox_selection(self.action_list)
        if selection and selection[0] < len(self._visible_actions):
            name = self._visible_actions[selection[0]]
            if name != self._selected_action:
                self._select_action(name)

    def _select_action(self, action_name: str, preferred_frame: int | None = None) -> None:
        self._cache_current_item_slots()
        action = self.action_by_name.get(action_name) or self.action_by_name.get("ball")
        if action is None and self.spt_actions:
            action = self.spt_actions[0]
        if action is None:
            self._selected_action = None
            self._selected_frame = 0
            self._current_item_frame_key = None
            self._show_slots(())
            self._refresh_skill_markers()
            return
        self._selected_action = action.action_name
        frame = preferred_frame
        if frame is None:
            edited = sorted(f for name, f in self.item_frame_drafts if name == action.action_name)
            frame = edited[0] if edited else action.item_frames[0] if action.item_frames else 1
        frame = min(max(frame, 1), action.frame_count)
        self._refresh_action_list()
        self._select_frame(frame)

    def _select_frame(self, frame: int) -> None:
        self._cache_current_item_slots()
        if self._selected_action is None:
            return
        self._selected_frame = frame
        key = (self._selected_action, frame)
        self._current_item_frame_key = key
        draft = self.item_frame_drafts.get(key)
        self._show_slots(draft if draft is not None else self._base_item_slots(*key))
        self._refresh_skill_markers()

    def _show_slots(self, slots: tuple[ItemSpawnSlot, ...]) -> None:
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
            for slot in slots[:3]
        ]
        while len(forms) < 3:
            forms.append(EMPTY_SLOT)
        self._loading_item_slots = True
        try:
            for slot_form, slot_vars in zip(forms, self.item_slot_vars):
                slot_vars["item"].set(
                    selected_item_label(slot_form.item_action_group, self.item_options)
                    if slot_form.item_action_group
                    else disabled_item_label()
                )
                for field in SLOT_FIELDS:
                    slot_vars[field].set(getattr(slot_form, field))
        finally:
            self._loading_item_slots = False
        self._sync_slot_states()

    def _sync_slot_states(self) -> None:
        active = self._current_item_frame_key is not None and self._project is not None
        known = {disabled_item_label(), *(option.label for option in self.item_options)}
        for slot_vars, widgets in zip(self.item_slot_vars, self.item_slot_widgets):
            if slot_vars["item"].get() not in known:
                self._loading_item_slots = True
                slot_vars["item"].set(disabled_item_label())
                self._loading_item_slots = False
            has_item = slot_vars["item"].get() != disabled_item_label()
            widgets["item"].set_enabled(active and bool(self.item_options))
            widgets["clear"].state(["!disabled"] if active and has_item else ["disabled"])
            for field in SLOT_FIELDS:
                entry = widgets[field]
                entry.state(["!disabled"] if active and has_item else ["disabled"])
                entry.configure(
                    style="TEntry" if _is_number(slot_vars[field].get()) else "Invalid.TEntry"
                )
        self.copy_frame_button.state(["!disabled"] if active else ["disabled"])
        self.paste_frame_button.state(
            ["!disabled"] if active and self._slot_clipboard is not None else ["disabled"]
        )
        self.restore_frame_button.state(
            ["!disabled"]
            if active and self._current_item_frame_key in self.item_frame_drafts
            else ["disabled"]
        )

    def _on_slot_changed(self, *_args: object) -> None:
        if self._loading_item_slots:
            return
        if self._current_item_frame_key is not None:
            self._dirty_item_frame_keys.add(self._current_item_frame_key)
        self._sync_slot_states()
        self._on_form_changed()

    def _cache_current_item_slots(self) -> None:
        key = self._current_item_frame_key
        if key is None or key not in self._dirty_item_frame_keys:
            return
        slots = item_slots_from_forms(self._current_item_slot_forms())
        if _slot_signature(slots) == _slot_signature(self._base_item_slots(*key)):
            self.item_frame_drafts.pop(key, None)
        else:
            self.item_frame_drafts[key] = slots
        self._dirty_item_frame_keys.discard(key)

    def _current_item_slot_forms(self) -> tuple[ItemSlotForm, ...]:
        if self._current_item_frame_key is None:
            return ()
        groups = item_options_by_label(self.item_options)
        return tuple(
            ItemSlotForm(
                item_action_group=str(groups[slot_vars["item"].get()])
                if slot_vars["item"].get() in groups
                else "",
                x=slot_vars["x"].get(),
                y=slot_vars["y"].get(),
                z=slot_vars["z"].get(),
                vx=slot_vars["vx"].get(),
                vy=slot_vars["vy"].get(),
                vz=slot_vars["vz"].get(),
            )
            for slot_vars in self.item_slot_vars
        )

    def _base_item_slots(self, action_name: str, action_frame: int) -> tuple[ItemSpawnSlot, ...]:
        key = (action_name, action_frame)
        cached = self._base_slot_cache.get(key)
        if cached is None:
            context = self._context
            cached = load_spt_frame_item_slots(
                self.workspace,
                action_name,
                action_frame,
                context.cache if context is not None else None,
                self._source_role_id,
                context.tools if context is not None else None,
            )
            self._base_slot_cache[key] = cached
        return cached

    def _copy_frame(self) -> None:
        if self._current_item_frame_key is None:
            return
        self._slot_clipboard = item_slots_from_forms(self._current_item_slot_forms())
        action, frame = self._current_item_frame_key
        self._toast(f"已复制 {action} 第 {frame} 帧的 {len(self._slot_clipboard)} 个道具", "accent")
        self._sync_slot_states()

    def _paste_frame(self) -> None:
        key = self._current_item_frame_key
        if key is None or self._slot_clipboard is None:
            return
        self._show_slots(self._slot_clipboard)
        self._dirty_item_frame_keys.add(key)
        self._on_form_changed()

    def _restore_current_frame(self) -> None:
        key = self._current_item_frame_key
        if key is None or key not in self.item_frame_drafts:
            return
        self.item_frame_drafts.pop(key, None)
        self._dirty_item_frame_keys.discard(key)
        self._show_slots(self._base_item_slots(*key))
        self._on_form_changed()
        self._refresh_skill_markers()

    def _refresh_skill_markers(self) -> None:
        action = self.action_by_name.get(self._selected_action or "")
        has_project = self._project is not None and action is not None
        if action is None:
            self.frame_strip.set_frames(0, (), (), 0)
            self.frame_caption.configure(text="")
        else:
            edited = [f for name, f in self.item_frame_drafts if name == action.action_name]
            self.frame_strip.set_frames(
                action.frame_count, action.item_frames, edited, self._selected_frame
            )
            description = self._action_description(action.action_name)
            title = f"{action.action_name}（{description}）" if description else action.action_name
            self.frame_caption.configure(
                text=f"{title} · 第 {self._selected_frame}/{action.frame_count} 帧"
            )
        self.frame_strip.set_enabled(has_project)
        edited_actions = {name for name, _frame in self.item_frame_drafts}
        for index, name in enumerate(self._visible_actions):
            text = self._action_row(name, name in edited_actions)
            if self.action_list.get(index) != text:
                selected = index in _listbox_selection(self.action_list)
                self.action_list.delete(index)
                self.action_list.insert(index, text)
                if selected:
                    self.action_list.selection_set(index)
            self.action_list.itemconfigure(
                index, foreground=theme.ACCENT_TEXT if name in edited_actions else theme.TEXT
            )
        for child in self.edits_box.winfo_children():
            child.destroy()
        edits = frame_item_edits_from_drafts(self.item_frame_drafts)
        if not edits:
            ttk.Label(self.edits_box, text="还没有修改任何帧。", style="Muted.TLabel").pack(
                anchor="w"
            )
        flow = ttk.Frame(self.edits_box)
        flow.pack(fill="x")
        for index, edit in enumerate(edits):
            button = ttk.Button(
                flow,
                text=frame_edit_label(
                    edit.action_name,
                    edit.action_frame,
                    len(edit.slots),
                    self._action_description(edit.action_name),
                ),
                style="Small.TButton",
                command=partial(self._jump_to_frame, edit.action_name, edit.action_frame),
            )
            button.grid(
                row=index // 3,
                column=index % 3,
                sticky="w",
                padx=(0, theme.px(8)),
                pady=(0, theme.px(6)),
            )
        self._sync_slot_states()

    def _jump_to_frame(self, action_name: str, frame: int) -> None:
        self._select_page("skills")
        if action_name != self._selected_action:
            self.action_filter_var.set("")
            self._select_action(action_name, frame)
        else:
            self._select_frame(frame)

    # ------------------------------------------------------------------ afterimages

    def _set_afterimage_delays(self, delays: tuple[int, ...], user: bool = True) -> None:
        self.afterimage_delays = tuple(sorted(set(delays)))
        self.delay_timeline.set_delays(self.afterimage_delays)
        count = len(self.afterimage_delays)
        if count:
            longest = self.afterimage_delays[-1]
            self.afterimage_summary_label.configure(
                text=f"共 {count} 个残影：延迟 {compress_delays(self.afterimage_delays)} 帧"
                f"（最长约 {longest / 30:.1f} 秒）"
            )
        else:
            self.afterimage_summary_label.configure(
                text="还没有残影。在上面的时间轴点一下就能添加。"
            )
        self.afterimage_warning.configure(
            text="残影超过 30 个会比较吃性能，游戏里可能掉帧。" if count > 30 else ""
        )
        self.afterimage_clear_button.state(["!disabled"] if count else ["disabled"])
        if user:
            self._on_form_changed()

    def _on_timeline_changed(self, delays: tuple[int, ...]) -> None:
        self._set_afterimage_delays(delays)

    def add_single_afterimage(self) -> None:
        try:
            delays = add_afterimage_delay(self.afterimage_delays, self.afterimage_single_var.get())
        except ValueError as exc:
            self._toast(str(exc), "danger")
            return
        self._set_afterimage_delays(delays)

    def quick_add_afterimages(self) -> None:
        try:
            delays = quick_add_afterimage_delays(
                self.afterimage_delays,
                self.afterimage_count_var.get(),
                self.afterimage_start_var.get(),
                self.afterimage_step_var.get(),
            )
        except ValueError as exc:
            self._toast(str(exc), "danger")
            return
        self._set_afterimage_delays(delays)

    def _apply_afterimage_preset(self, count: str, start: str, step: str) -> None:
        self.afterimage_count_var.set(count)
        self.afterimage_start_var.set(start)
        self.afterimage_step_var.set(step)
        self.quick_add_afterimages()

    def _refresh_quick_preview(self) -> None:
        self.quick_preview.configure(
            text=quick_add_preview(
                self.afterimage_count_var.get(),
                self.afterimage_start_var.get(),
                self.afterimage_step_var.get(),
            )
        )

    def clear_afterimages(self) -> None:
        if self.afterimage_delays and messagebox.askyesno(
            "全部删除", "删除这个角色的全部残影？", parent=self
        ):
            self._set_afterimage_delays(())

    # ------------------------------------------------------------------ validation / export

    def _render_issues(self, report: ValidationReport, caption: str) -> None:
        for child in self.issues_box.winfo_children():
            child.destroy()
        self.issues_caption.configure(text=caption)
        if not report.issues:
            ok = Pill(self.issues_box)
            ok.set("没有发现问题，可以导出", "success")
            ok.pack(anchor="w", pady=(theme.px(8), 0))
            return
        for group in validation_groups(report):
            kind = {"错误": "danger", "警告": "warning", "提示": "neutral"}[group.title]
            for issue in group.issues:
                row = ttk.Frame(self.issues_box)
                row.pack(fill="x", pady=(theme.px(10), 0))
                pill = Pill(row)
                pill.set(group.title, kind)
                pill.pack(side="left", anchor="n", pady=(theme.px(1), 0))
                if self._issue_page(issue.target) is not None:
                    ttk.Button(
                        row,
                        text="去修改 →",
                        style="Ghost.TButton",
                        command=partial(self.focus_validation_target, issue.target),
                    ).pack(side="right", anchor="n")
                texts = ttk.Frame(row)
                texts.pack(side="left", fill="x", expand=True, padx=(theme.px(12), 0))
                ttk.Label(
                    texts, text=issue.message, style="Strong.TLabel", wraplength=theme.px(560)
                ).pack(fill="x")
                if issue.suggestion:
                    ttk.Label(
                        texts, text=issue.suggestion, style="Muted.TLabel", wraplength=theme.px(560)
                    ).pack(fill="x")

    def _issue_page(self, target: str) -> str | None:
        if target.startswith(("character.", "stats.", "target_game.source_path")):
            return "basic"
        if target.startswith("textures."):
            return "textures"
        if target.startswith("item_frame_edits"):
            return "skills"
        if target.startswith("afterimage"):
            return "afterimage"
        return None

    def focus_validation_target(self, target: str) -> None:
        page = self._issue_page(target)
        if page is None:
            self._select_page("export")
            return
        widget: tk.Widget | None = self.target_widgets.get(target)
        if page == "skills":
            parts = target.split(".")
            edits = frame_item_edits_from_drafts(self.item_frame_drafts)
            if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) < len(edits):
                edit = edits[int(parts[1])]
                self._jump_to_frame(edit.action_name, edit.action_frame)
                if len(parts) >= 5 and parts[2] == "slots":
                    widget = self.target_widgets.get(f"slot.{parts[3]}.{parts[4]}")
            widget = widget or self.action_list
        self._select_page(page)
        if widget is not None:
            self._page_frames[page].ensure_visible(widget)
            widget.focus_set()
            if isinstance(widget, ttk.Entry):
                widget.selection_range(0, "end")

    def export_current(self) -> None:
        if self._project is None or self.current_project_dir is None:
            return
        if self._busy or self._exporting:
            return
        self._select_page("export")
        if self._dirty and not self.save_current(quiet=True):
            return
        errors, _warnings, _infos = validation_counts(self._validation_report)
        if errors:
            self.export_status.configure(
                text=f"还有 {errors} 个错误，修正后才能导出。", style="Error.TLabel"
            )
            self._toast(f"还有 {errors} 个错误需要先修正", "danger")
            return
        project_dir = self.current_project_dir
        workspace = self.workspace
        self._exporting = True
        self._export_started = time.monotonic()
        self._sync_header_buttons()
        self.result_outer.grid_remove()
        self.export_progress.pack(fill="x", pady=(theme.px(8), 0))
        self.export_progress.start(12)
        self._tick_export()
        self._status("正在导出…", "busy")

        def done(result: ExportResult) -> None:
            self._finish_export()
            self._show_export_result(result)

        def failed(exc: BaseException) -> None:
            self._finish_export()
            self.export_status.configure(text="导出失败。", style="Error.TLabel")
            self._status("导出失败", "danger")
            self._render_result_banner("导出失败", f"{type(exc).__name__}: {exc}", "danger")

        run_in_thread(self, lambda: export_project(project_dir, workspace), done, failed)

    def _tick_export(self) -> None:
        if not self._exporting:
            return
        elapsed = format_elapsed(time.monotonic() - self._export_started)
        self.export_status.configure(
            text=f"正在校验、修补并打包游戏…  已用时 {elapsed}（通常需要 1–3 分钟）",
            style="Strong.TLabel",
        )
        self.after(500, self._tick_export)

    def _finish_export(self) -> None:
        self._exporting = False
        self.export_progress.stop()
        self.export_progress.pack_forget()
        self._sync_header_buttons()
        self._status("就绪")

    def _show_export_result(self, result: ExportResult) -> None:
        elapsed = format_elapsed(time.monotonic() - self._export_started)
        label, kind = export_status_display(result.status)
        self.export_status.configure(
            text=f"{label}（用时 {elapsed}）",
            style="Strong.TLabel" if kind == "success" else "Error.TLabel",
        )
        self._showing_export_report = True
        self._render_issues(result.validation_report, "这是导出时针对目标游戏做的完整检查。")
        title = {
            "success": "导出成功，可以去游戏里测试了",
            "warning": "导出被阻断：请先修正上面的错误",
            "danger": "导出失败",
        }.get(kind, label)
        self._render_result_banner(title, result.summary, kind, result)
        self._summaries = scan_projects(self.projects_root)
        self._refresh_project_rows()
        if self.current_project_dir is not None:
            # export_project appended an export record on disk; keep it as the saved state
            # so the next save does not write the old export history back.
            with suppress(ProjectError):
                self._project = load_project(self.current_project_dir)
        self._refresh_history()
        self._status(label, "success" if kind == "success" else "danger")

    def _render_result_banner(
        self, title: str, summary: str, kind: str, result: ExportResult | None = None
    ) -> None:
        for child in self.result_box.winfo_children():
            child.destroy()
        self.result_outer.grid(
            row=2, column=0, sticky="ew", padx=theme.px(24), pady=(theme.px(14), 0)
        )
        fg, bg = theme.KIND_COLORS.get(kind, theme.KIND_COLORS["neutral"])
        banner = tk.Frame(self.result_box, background=bg)
        banner.pack(fill="x")
        tk.Label(
            banner,
            text=title,
            background=bg,
            foreground=fg,
            font=theme.font(11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=theme.px(14), pady=(theme.px(10), 0))
        tk.Label(
            banner,
            text=summary,
            background=bg,
            foreground=theme.TEXT,
            font=theme.font(9),
            anchor="w",
            justify="left",
            wraplength=theme.px(640),
        ).pack(fill="x", padx=theme.px(14), pady=(theme.px(2), theme.px(10)))
        if result is None:
            return
        if result.exe_path is not None:
            exe = Path(result.exe_path)
            path_row = ttk.Frame(self.result_box)
            path_row.pack(fill="x", pady=(theme.px(12), 0))
            ttk.Label(path_row, text="游戏文件", style="Field.TLabel").pack(anchor="w")
            ttk.Label(
                path_row, text=str(exe), style="Strong.TLabel", wraplength=theme.px(640)
            ).pack(fill="x")
            buttons = ttk.Frame(self.result_box)
            buttons.pack(fill="x", pady=(theme.px(10), 0))
            ttk.Button(
                buttons,
                text="▶  启动游戏",
                style="Accent.TButton",
                command=lambda: self._launch(exe),
            ).pack(side="left")
            ttk.Button(buttons, text="打开所在文件夹", command=lambda: open_in_explorer(exe)).pack(
                side="left", padx=(theme.px(8), 0)
            )
            ttk.Button(
                buttons,
                text="复制路径",
                style="Ghost.TButton",
                command=lambda: self._copy(str(exe)),
            ).pack(side="left", padx=(theme.px(8), 0))
            more = ttk.Frame(self.result_box)
            more.pack(fill="x", pady=(theme.px(10), 0))
            ttk.Button(
                more,
                text="＋  在这个游戏里继续加角色",
                command=partial(self._continue_in_game, exe),
            ).pack(side="left")
            ttk.Label(
                more,
                text="以刚导出的游戏为底包新建角色，多个原创角色和各自的残影都会在同一个游戏里。",
                style="Hint.TLabel",
                wraplength=theme.px(420),
            ).pack(side="left", fill="x", expand=True, padx=(theme.px(10), 0))
        links = ttk.Frame(self.result_box)
        links.pack(fill="x", pady=(theme.px(8), 0))
        for text, path in (
            ("导出日志", result.log_path),
            ("校验报告", result.validation_report_path),
            ("诊断目录", result.diagnostics_path),
        ):
            if path is not None:
                ttk.Button(
                    links,
                    text=text,
                    style="Ghost.TButton",
                    command=partial(open_in_explorer, Path(path)),
                ).pack(side="left")
        if result.test_checklist:
            ttk.Label(self.result_box, text="游戏内测试清单", style="Section.TLabel").pack(
                anchor="w", pady=(theme.px(14), theme.px(4))
            )
            for item in result.test_checklist:
                var = tk.BooleanVar(value=False)
                line = ttk.Frame(self.result_box)
                line.pack(fill="x", pady=(theme.px(3), 0))
                box = tk.Label(
                    line,
                    text="☐",
                    background=theme.CARD,
                    foreground=theme.TEXT_MUTED,
                    font=theme.font(11),
                    cursor="hand2",
                )
                box.pack(side="left", anchor="n")
                text_label = ttk.Label(line, text=item, wraplength=theme.px(620), cursor="hand2")
                text_label.pack(side="left", fill="x", expand=True, padx=(theme.px(6), 0))

                def toggle(
                    _event: object,
                    v: tk.BooleanVar = var,
                    b: tk.Label = box,
                    t: ttk.Label = text_label,
                ) -> None:
                    v.set(not v.get())
                    b.configure(
                        text="☑" if v.get() else "☐",
                        foreground=theme.SUCCESS if v.get() else theme.TEXT_MUTED,
                    )
                    t.configure(style="Muted.TLabel" if v.get() else "TLabel")

                box.bind("<Button-1>", toggle)
                text_label.bind("<Button-1>", toggle)

    def _continue_in_game(self, exe: Path) -> None:
        # Exporting into a previously exported game keeps its added roles and afterimages.
        if not exe.is_file():
            self._toast("找不到这个导出的游戏文件了", "danger")
            return
        try:
            target = target_game_for_source(str(exe))
        except TargetCacheError as exc:
            messagebox.showwarning(exc.summary, exc.detail, parent=self)
            return
        self.new_project(target)

    def _launch(self, exe: Path) -> None:
        try:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))  # noqa: S603
        except OSError as exc:
            messagebox.showerror("无法启动游戏", str(exc), parent=self)
            return
        self._toast("游戏已启动", "success")

    def _refresh_history(self) -> None:
        for child in self.history_box.winfo_children():
            child.destroy()
        exports = self._project.exports if self._project is not None else ()
        if not exports:
            ttk.Label(self.history_box, text="还没有导出过。", style="Muted.TLabel").pack(
                anchor="w"
            )
            return
        for record in reversed(exports[-6:]):
            row = ttk.Frame(self.history_box)
            row.pack(fill="x", pady=(0, theme.px(6)))
            exe = Path(record.exe_path) if record.exe_path else None
            if exe is not None:
                # Buttons first: pack gives leftover space to what is packed last.
                ttk.Button(
                    row,
                    text="打开文件夹",
                    style="Ghost.TButton",
                    command=partial(open_in_explorer, exe),
                ).pack(side="right")
                ttk.Button(
                    row,
                    text="继续加角色",
                    style="Ghost.TButton",
                    command=partial(self._continue_in_game, exe),
                ).pack(side="right")
            label, kind = export_status_display(record.status)
            pill = Pill(row)
            pill.set(label, kind)
            pill.pack(side="left")
            when = record.exported_at.replace("T", " ")[:16]
            ttk.Label(row, text=when, style="Muted.TLabel").pack(
                side="left", padx=(theme.px(10), 0)
            )
            if exe is not None:
                ttk.Label(row, text=exe.name, style="TLabel").pack(
                    side="left", padx=(theme.px(10), 0)
                )

    # ------------------------------------------------------------------ status bar

    def _status(self, text: str, kind: str = "neutral") -> None:
        colors = {
            "busy": theme.ACCENT,
            "success": theme.SUCCESS,
            "danger": theme.DANGER,
            "warning": theme.WARNING,
            "accent": theme.ACCENT,
        }
        self.status_icon.configure(foreground=colors.get(kind, theme.TEXT_SUBTLE))
        self.status_label.configure(text=text)
        if kind == "busy":
            self.status_progress.pack(side="left", padx=(theme.px(12), 0))
            self.status_progress.start(12)
        else:
            self.status_progress.stop()
            self.status_progress.pack_forget()

    def _toast(self, text: str, kind: str = "neutral") -> None:
        if self._busy or self._exporting:
            return
        self._status(text, kind)
        if self._toast_after is not None:
            self.after_cancel(self._toast_after)
        self._toast_after = self.after(6000, self._clear_toast)

    def _clear_toast(self) -> None:
        self._toast_after = None
        if not self._busy and not self._exporting:
            self._status("就绪")


class NewProjectDialog(tk.Toplevel):
    """Target game first, then a template from that target, then name and ID."""

    def __init__(self, app: HfeCharacterApp, initial_target: TargetGame | None = None) -> None:
        super().__init__(app)
        self.app = app
        self.result: tuple[str, str, str, TargetGame, str] | None = None
        self.title("新建角色")
        self.configure(background=theme.CARD)
        self.transient(app)
        self.resizable(False, False)
        self._templates = list_templates()
        self._default_name, self._default_cid = next_project_defaults(app._summaries)
        self._target = initial_target or DEFAULT_TARGET_GAME
        self._choices: tuple[NewProjectTemplateChoice, ...] = ()
        self._visible: list[NewProjectTemplateChoice] = []
        self._selected: NewProjectTemplateChoice | None = None
        self._loading = False
        self._cid_touched = False
        self._setting_cid = False
        self.name_var = tk.StringVar(value=self._default_name)
        self.cid_var = tk.StringVar(value=self._default_cid)
        self.filter_var = tk.StringVar()
        self._build()
        self.name_var.trace_add("write", lambda *_args: self._validate())
        self.cid_var.trace_add("write", self._on_cid_typed)
        self.filter_var.trace_add("write", lambda *_args: self._refresh_list())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self._confirm())
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + max((app.winfo_height() - self.winfo_reqheight()) // 2, 0)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self._load_target(self._target)

    def _step(self, parent: ttk.Frame, number: str, title: str) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(theme.px(18), theme.px(8)))
        tk.Label(
            row,
            text=number,
            background=theme.ACCENT,
            foreground="#ffffff",
            font=theme.font(9, "bold"),
            width=2,
        ).pack(side="left")
        ttk.Label(row, text=title, style="Section.TLabel").pack(side="left", padx=(theme.px(10), 0))

    def _build(self) -> None:
        body = ttk.Frame(self, padding=(theme.px(28), theme.px(22), theme.px(28), theme.px(22)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="新建角色", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="不同游戏版本里的角色和道具不完全一样，所以先选游戏，再选角色模板。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(theme.px(4), 0))

        self._step(body, "1", "目标游戏")
        target_row = ttk.Frame(body)
        target_row.pack(fill="x")
        self.target_row = target_row
        texts = ttk.Frame(target_row)
        texts.pack(side="left", fill="x", expand=True)
        self.target_name = ttk.Label(texts, text="", style="Strong.TLabel")
        self.target_name.pack(anchor="w")
        self.target_status = ttk.Label(
            texts, text="", style="Hint.TLabel", wraplength=theme.px(420)
        )
        self.target_status.pack(fill="x")
        self.change_button = ttk.Button(target_row, text="更换…", command=self._choose_target)
        self.change_button.pack(side="right")
        self.progress = ttk.Progressbar(
            body, mode="indeterminate", style="Accent.Horizontal.TProgressbar"
        )

        self._step(body, "2", "角色模板（以哪个角色为底子）")
        search = ttk.Entry(body, textvariable=self.filter_var)
        search.pack(fill="x")
        add_placeholder(search, "搜索角色")
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="x", pady=(theme.px(6), 0))
        self.template_list = tk.Listbox(list_frame, height=8, exportselection=False)
        theme.style_listbox(self.template_list)
        self.template_list.configure(
            background=theme.FIELD, highlightthickness=1, highlightbackground=theme.FIELD_BORDER
        )
        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.template_list.yview,
            style="Thin.Vertical.TScrollbar",
        )
        self.template_list.configure(yscrollcommand=scrollbar.set)
        self.template_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.template_list.bind("<<ListboxSelect>>", self._on_template_selected)

        self._step(body, "3", "项目信息")
        grid = ttk.Frame(body)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="info")
        grid.columnconfigure(1, weight=1, uniform="info")
        ttk.Label(grid, text="项目名称", style="Field.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(grid, text="角色 ID", style="Field.TLabel").grid(
            row=0, column=1, sticky="w", padx=(theme.px(14), 0)
        )
        self.name_entry = ttk.Entry(grid, textvariable=self.name_var)
        self.name_entry.grid(row=1, column=0, sticky="ew", pady=(theme.px(3), 0))
        self.cid_entry = ttk.Entry(grid, textvariable=self.cid_var)
        self.cid_entry.grid(
            row=1, column=1, sticky="ew", padx=(theme.px(14), 0), pady=(theme.px(3), 0)
        )
        self.name_message = ttk.Label(grid, text="", style="Hint.TLabel", wraplength=theme.px(220))
        self.name_message.grid(row=2, column=0, sticky="new", pady=(theme.px(2), 0))
        self.cid_message = ttk.Label(grid, text="", style="Hint.TLabel", wraplength=theme.px(220))
        self.cid_message.grid(
            row=2, column=1, sticky="new", padx=(theme.px(14), 0), pady=(theme.px(2), 0)
        )

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(theme.px(22), 0))
        self.create_button = ttk.Button(
            buttons, text="创建角色", style="Accent.TButton", command=self._confirm
        )
        self.create_button.pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(
            side="right", padx=(0, theme.px(8))
        )
        self.name_entry.focus_set()

    def _choose_target(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择目标 HFE 游戏（EXE 或 SWF）",
            filetypes=(("HFE 游戏", "*.exe *.swf"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            target = target_game_for_source(path)
        except TargetCacheError as exc:
            messagebox.showwarning(exc.summary, exc.detail, parent=self)
            return
        self._load_target(target)

    def _load_target(self, target: TargetGame) -> None:
        self._target = target
        self._loading = True
        self.target_name.configure(text=target_display_name(target))
        self.target_status.configure(
            text="正在读取这个游戏里的角色…（第一次读取新版本可能需要几分钟）", style="Hint.TLabel"
        )
        self.progress.pack(fill="x", pady=(theme.px(8), 0), after=self.target_row)
        self.progress.start(12)
        self.change_button.state(["disabled"])
        self._validate()
        cached = self.app._target_cache_entries.get((target.source_path, target.source_kind))
        workspace = self.app.workspace

        def work() -> (
            tuple[TargetCacheEntry | None, tuple[CharacterTemplateCatalogEntry, ...], str]
        ):
            context_cache = cached
            problem = ""
            if context_cache is None:
                tools = discover_tools(workspace)
                missing = check_tools(tools)
                if missing:
                    problem = dependency_summary(missing)
                else:
                    try:
                        context_cache = prepare_target_cache(
                            workspace,
                            target,
                            tools,
                            output_root=default_target_cache_root(workspace),
                            reuse_existing=True,
                        )
                    except TargetCacheError as exc:
                        problem = f"{exc.summary}：{exc.detail}"
            entries = (
                load_character_template_catalog(workspace, context_cache)
                if context_cache is not None
                else load_character_template_catalog(workspace)
            )
            return context_cache, entries, problem

        def done(
            result: tuple[TargetCacheEntry | None, tuple[CharacterTemplateCatalogEntry, ...], str],
        ) -> None:
            if not self.winfo_exists():
                return
            cache, entries, problem = result
            if cache is not None:
                self.app._target_cache_entries[(target.source_path, target.source_kind)] = cache
            previous_label = self._selected.label if self._selected else ""
            old_choices = self._choices
            self._choices = target_template_choices(entries, self._templates)
            self._finish_loading()
            if problem:
                self.target_status.configure(
                    text=f"⚠ 读取失败：{problem}"[:300], style="Warn.TLabel"
                )
            elif self._choices:
                self.target_status.configure(
                    text=f"✓ 已读取，可以用 {len(self._choices)} 个角色当模板。",
                    style="Hint.TLabel",
                )
            else:
                self.target_status.configure(
                    text="这个游戏里没有可用的角色模板。", style="Warn.TLabel"
                )
            wanted = select_template_label_after_refresh(previous_label, old_choices, self._choices)
            self._selected = next((c for c in self._choices if c.label == wanted), None)
            self._refresh_list()
            self._sync_default_id()

        def failed(exc: BaseException) -> None:
            if self.winfo_exists():
                self._finish_loading()
                self.target_status.configure(text=f"⚠ 读取失败：{exc}", style="Warn.TLabel")

        run_in_thread(self, work, done, failed)

    def _finish_loading(self) -> None:
        self._loading = False
        self.progress.stop()
        self.progress.pack_forget()
        self.change_button.state(["!disabled"])
        self._validate()

    def _refresh_list(self) -> None:
        needle = self.filter_var.get().strip().lower()
        self._visible = [c for c in self._choices if not needle or needle in c.label.lower()]
        self.template_list.delete(0, "end")
        for choice in self._visible:
            self.template_list.insert("end", f"  {choice.label}")
        if self._selected in self._visible:
            index = self._visible.index(self._selected)
            self.template_list.selection_set(index)
            self.template_list.see(index)
        self._validate()

    def _on_template_selected(self, _event: object) -> None:
        selection = _listbox_selection(self.template_list)
        if selection and selection[0] < len(self._visible):
            self._selected = self._visible[selection[0]]
            self._sync_default_id()

    def _on_cid_typed(self, *_args: object) -> None:
        if not self._setting_cid:
            self._cid_touched = True
        self._validate()

    def _sync_default_id(self) -> None:
        if self._selected is None:
            return
        current = self.cid_var.get().strip()
        role = self._selected.role_id
        if self._cid_touched and current and not character_id_problem(current, role):
            self._validate()
            return
        self._setting_cid = True
        try:
            self.cid_var.set(_default_character_id_for_source_role(role, self._default_cid))
        finally:
            self._setting_cid = False
        self._validate()

    def _validate(self) -> bool:
        name = self.name_var.get().strip()
        name_problem = ""
        if not name:
            name_problem = "请填写项目名称。"
        elif any(ch in name for ch in '\\/:*?"<>|'):
            name_problem = '项目名称不能包含 \\ / : * ? " < > |'
        elif (self.app.projects_root / name).exists():
            name_problem = "已经有同名项目了，换一个名字。"
        self.name_message.configure(
            text=name_problem or "项目文件夹的名字，只在工具里用。",
            style="Error.TLabel" if name_problem else "Hint.TLabel",
        )
        self.name_entry.configure(style="Invalid.TEntry" if name_problem else "TEntry")
        role = self._selected.role_id if self._selected else "lucas"
        cid_problem = character_id_problem(self.cid_var.get(), role)
        self.cid_message.configure(
            text=cid_problem or character_id_hint(role),
            style="Error.TLabel" if cid_problem else "Hint.TLabel",
        )
        self.cid_entry.configure(style="Invalid.TEntry" if cid_problem else "TEntry")
        ok = (
            not name_problem
            and not cid_problem
            and self._selected is not None
            and not self._loading
        )
        self.create_button.state(["!disabled"] if ok else ["disabled"])
        return ok

    def _confirm(self) -> None:
        if not self._validate() or self._selected is None:
            return
        self.result = (
            self.name_var.get().strip(),
            self.cid_var.get().strip(),
            self._selected.template_id,
            self._target,
            self._selected.role_id,
        )
        self.destroy()


class DuplicateProjectDialog(tk.Toplevel):
    """New project name and character ID for a copy of an existing project."""

    def __init__(self, app: HfeCharacterApp, source: CharacterProject, used_ids: set[str]) -> None:
        super().__init__(app)
        self.app = app
        self.result: tuple[str, str] | None = None
        self._role = source.source_role_id or "lucas"
        self.title("复制为新项目")
        self.configure(background=theme.CARD)
        self.transient(app)
        self.resizable(False, False)
        self.name_var = tk.StringVar(value=f"{source.project_name}_副本")
        self.cid_var = tk.StringVar(value=suggest_character_id(source.character_id, used_ids))
        body = ttk.Frame(self, padding=(theme.px(28), theme.px(22)))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="复制为新项目", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text=f"复制「{source.project_name}」的全部设置（不含导出记录）。"
            "新项目要用不同的角色 ID，才能和原角色放进同一个游戏。",
            style="Muted.TLabel",
            wraplength=theme.px(420),
        ).pack(fill="x", pady=(theme.px(4), theme.px(14)))
        self.name_entry, self.name_message = self._row(body, "项目名称", self.name_var)
        self.cid_entry, self.cid_message = self._row(body, "角色 ID", self.cid_var)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(theme.px(18), 0))
        self.ok_button = ttk.Button(buttons, text="复制", style="Accent.TButton", command=self._ok)
        self.ok_button.pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(
            side="right", padx=(0, theme.px(8))
        )
        for var in (self.name_var, self.cid_var):
            var.trace_add("write", lambda *_args: self._validate())
        self.bind("<Return>", lambda _event: self._ok())
        self.bind("<Escape>", lambda _event: self.destroy())
        self._validate()
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + max((app.winfo_height() - self.winfo_reqheight()) // 3, 0)
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.grab_set()
        self.name_entry.focus_set()
        self.name_entry.selection_range(0, "end")

    def _row(self, parent: ttk.Frame, label: str, var: tk.StringVar) -> tuple[ttk.Entry, ttk.Label]:
        ttk.Label(parent, text=label, style="Field.TLabel").pack(anchor="w")
        entry = ttk.Entry(parent, textvariable=var, width=40)
        entry.pack(fill="x", pady=(theme.px(3), 0))
        message = ttk.Label(parent, text="", style="Hint.TLabel", wraplength=theme.px(420))
        message.pack(fill="x", pady=(theme.px(2), theme.px(8)))
        return entry, message

    def _validate(self) -> bool:
        name = self.name_var.get().strip()
        name_problem = ""
        if not name:
            name_problem = "请填写项目名称。"
        elif any(ch in name for ch in '\\/:*?"<>|'):
            name_problem = '项目名称不能包含 \\ / : * ? " < > |'
        elif (self.app.projects_root / name).exists():
            name_problem = "已经有同名项目了，换一个名字。"
        cid_problem = character_id_problem(self.cid_var.get(), self._role)
        for entry, message, problem, hint in (
            (self.name_entry, self.name_message, name_problem, "项目文件夹的名字。"),
            (self.cid_entry, self.cid_message, cid_problem, character_id_hint(self._role)),
        ):
            entry.configure(style="Invalid.TEntry" if problem else "TEntry")
            message.configure(
                text=problem or hint, style="Error.TLabel" if problem else "Hint.TLabel"
            )
        ok = not name_problem and not cid_problem
        self.ok_button.state(["!disabled"] if ok else ["disabled"])
        return ok

    def _ok(self) -> None:
        if self._validate():
            self.result = (self.name_var.get().strip(), self.cid_var.get().strip())
            self.destroy()


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


def _autowrap(event: tk.Event[tk.Misc]) -> None:
    """Labels created with a wraplength re-wrap to the width they are actually given."""
    widget: Any = event.widget
    try:
        current = int(float(str(widget.cget("wraplength")) or 0))
    except (tk.TclError, ValueError):
        return
    width = event.width - 4
    if current > 0 and width > 40 and abs(current - width) > 3:
        widget.configure(wraplength=width)


def _listbox_selection(listbox: tk.Listbox) -> tuple[int, ...]:
    return tuple(int(index) for index in cast(Any, listbox).curselection())


def _slot_signature(slots: tuple[ItemSpawnSlot, ...]) -> tuple[tuple[float, ...], ...]:
    return tuple(
        (float(slot.item_action_group), slot.x, slot.y, slot.z, slot.vx, slot.vy, slot.vz)
        for slot in slots
        if slot.enabled
    )


def _is_number(value: str) -> bool:
    try:
        float(value.strip())
    except ValueError:
        return False
    return True


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
        ch.lower() if ch.isascii() and (ch.isalnum() or ch == "_") else "" for ch in source_role_id
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


def main() -> None:
    theme.enable_dpi_awareness()
    workspace = resolve_workspace()
    app = HfeCharacterApp(workspace)
    app.mainloop()
