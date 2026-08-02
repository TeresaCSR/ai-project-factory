"""Small Windows-friendly GUI over the single deterministic Factory Core."""

from __future__ import annotations

import json
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .core import (
    FACTORY_VERSION,
    PROFILES,
    REPO_ROOT,
    CreateProjectRequest,
    FactoryError,
    ProjectSummary,
    build_agent_prompt,
    checkpoint_project,
    create_project,
    discover_projects,
    doctor_project,
    export_project,
    inspect_project,
    launch_codex_project,
    open_in_file_manager,
    sync_agent_skills,
)


BACKGROUND = "#f4f7fa"
SURFACE = "#ffffff"
NAVY = "#102a43"
SIDEBAR = "#132f46"
ACCENT = "#34789a"
ACCENT_DARK = "#255b78"
MUTED = "#627487"
LINE = "#dbe4ea"
SOFT_BLUE = "#eaf3f7"
SUCCESS = "#247057"
ERROR = "#a64040"
PROFILE_LABELS = {
    "通用项目（general）": "general",
    "软件开发（software）": "software",
    "科研 / 数据（research）": "research",
}


class FactoryApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"AI Project Factory · {FACTORY_VERSION}")
        initial_height = min(700, max(620, self.winfo_screenheight() - 120))
        self.geometry(f"940x{initial_height}")
        self.minsize(840, 620)
        self.configure(bg=BACKGROUND)
        self.last_project: Path | None = None
        self.status_var = tk.StringVar(value="就绪")
        self._current_page = "create"
        self._project_rows: dict[str, ProjectSummary] = {}
        self._busy = False
        self._closing = False
        self._saved_widget_states: dict[tk.Widget, str] = {}
        self._configure_styles()
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Page.TFrame", background=BACKGROUND)
        style.configure(
            "Title.TLabel",
            background=BACKGROUND,
            foreground=NAVY,
            font=("Microsoft YaHei UI", 21, "bold"),
        )
        style.configure(
            "Subtitle.TLabel", background=BACKGROUND, foreground=MUTED
        )
        style.configure(
            "CardTitle.TLabel",
            background=SURFACE,
            foreground=NAVY,
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        style.configure("Card.TLabel", background=SURFACE, foreground=NAVY)
        style.configure(
            "Primary.TButton",
            background=ACCENT,
            foreground="#ffffff",
            padding=(18, 10),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", ACCENT_DARK), ("disabled", "#9bb2bf")],
            foreground=[("disabled", "#eef3f6")],
        )
        style.configure(
            "Secondary.TButton",
            background=SOFT_BLUE,
            foreground=NAVY,
            padding=(16, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#d9eaf1")],
        )
        style.configure("TButton", padding=(12, 8))
        style.configure("Compact.TButton", padding=(10, 6))
        style.configure(
            "Factory.Treeview",
            background=SURFACE,
            fieldbackground=SURFACE,
            foreground=NAVY,
            rowheight=31,
            borderwidth=0,
        )
        style.configure(
            "Factory.Treeview.Heading",
            background="#edf2f5",
            foreground=MUTED,
            font=("Microsoft YaHei UI", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Factory.Treeview",
            background=[("selected", "#dcecf3")],
            foreground=[("selected", NAVY)],
        )
        style.configure(
            "Badge.TLabel",
            background=SOFT_BLUE,
            foreground=NAVY,
            padding=(10, 5),
            font=("Microsoft YaHei UI", 9, "bold"),
        )

    def _build(self) -> None:
        shell = tk.Frame(self, bg=BACKGROUND)
        shell.pack(fill="both", expand=True)

        sidebar = tk.Frame(shell, bg=SIDEBAR, width=220)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        mark = tk.Canvas(
            sidebar,
            width=42,
            height=42,
            bg=SIDEBAR,
            highlightthickness=0,
        )
        mark.pack(anchor="w", padx=22, pady=(28, 8))
        mark.create_rectangle(4, 4, 38, 38, outline="#7fb3c9", width=2)
        mark.create_line(12, 12, 12, 30, fill="#ffffff", width=3)
        mark.create_line(30, 12, 30, 30, fill="#ffffff", width=3)
        mark.create_line(12, 21, 30, 21, fill="#ffffff", width=3)
        mark.create_oval(27, 7, 33, 13, fill="#7fb3c9", outline="")

        tk.Label(
            sidebar,
            text="AI Project\nFactory",
            bg=SIDEBAR,
            fg="#ffffff",
            justify="left",
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w", padx=22)
        tk.Label(
            sidebar,
            text=f"Portable workspace · v{FACTORY_VERSION}",
            bg=SIDEBAR,
            fg="#9eb4c4",
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w", padx=22, pady=(5, 28))

        self.nav_buttons: dict[str, tk.Button] = {}
        for key, label, command in (
            ("create", "＋  新建项目", lambda: self._show_page("create")),
            ("manage", "▣  项目控制台", lambda: self._show_page("manage")),
        ):
            button = tk.Button(
                sidebar,
                text=label,
                command=command,
                anchor="w",
                relief="flat",
                borderwidth=0,
                padx=20,
                pady=12,
                bg=SIDEBAR,
                fg="#cbd8e1",
                activebackground="#1c425e",
                activeforeground="#ffffff",
                font=("Microsoft YaHei UI", 10, "bold"),
                cursor="hand2",
            )
            button.pack(fill="x", pady=2)
            self.nav_buttons[key] = button

        tk.Frame(sidebar, bg="#27475f", height=1).pack(
            side="bottom", fill="x", padx=20, pady=(0, 14)
        )
        self.status_label = tk.Label(
            sidebar,
            textvariable=self.status_var,
            bg=SIDEBAR,
            fg="#b9c9d4",
            justify="left",
            wraplength=174,
            font=("Microsoft YaHei UI", 9),
        )
        self.status_label.pack(side="bottom", anchor="w", padx=22, pady=(8, 8))
        tk.Label(
            sidebar,
            text="本地优先 · 普通 Markdown · 可核验",
            bg=SIDEBAR,
            fg="#718b9d",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="bottom", anchor="w", padx=22)

        content = ttk.Frame(shell, style="Page.TFrame")
        content.pack(side="left", fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.create_tab = ttk.Frame(
            content, padding=(30, 24), style="Page.TFrame"
        )
        self.manage_tab = ttk.Frame(
            content, padding=(30, 24), style="Page.TFrame"
        )
        for page in (self.create_tab, self.manage_tab):
            page.grid(row=0, column=0, sticky="nsew")
        self._build_create(self.create_tab)
        self._build_manage(self.manage_tab)
        self._show_page("create")

    def _field(
        self,
        parent: tk.Misc,
        row: int,
        label: str,
        variable: tk.StringVar,
        browse: Callable[[], None] | None = None,
    ) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky="w", pady=8
        )
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(14, 8), pady=8
        )
        if browse:
            ttk.Button(parent, text="浏览…", command=browse).grid(
                row=row, column=2, sticky="ew", pady=8
            )

    def _build_create(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        ttk.Label(tab, text="新建可迁移项目", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            tab,
            text="从一份可核验的本地事实层开始，再让 Codex 和你完成启动访谈。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 18))

        flow = ttk.Frame(tab, style="Page.TFrame")
        flow.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        for column in range(3):
            flow.columnconfigure(column, weight=1)
        for index, (number, title, detail) in enumerate(
            (
                ("01", "创建地基", "生成事实层与本地 Git"),
                ("02", "讨论定方案", "访谈、研究与 pushback"),
                ("03", "Goal 执行", "确认后持续做到完成"),
            )
        ):
            card = tk.Frame(
                flow,
                bg=SURFACE,
                highlightbackground=LINE,
                highlightthickness=1,
            )
            card.grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 6, 0 if index == 2 else 6),
            )
            tk.Label(
                card,
                text=number,
                bg=SURFACE,
                fg=ACCENT,
                font=("Cascadia Mono", 10, "bold"),
            ).pack(anchor="w", padx=14, pady=(10, 2))
            tk.Label(
                card,
                text=title,
                bg=SURFACE,
                fg=NAVY,
                font=("Microsoft YaHei UI", 10, "bold"),
            ).pack(anchor="w", padx=14)
            tk.Label(
                card,
                text=detail,
                bg=SURFACE,
                fg=MUTED,
                font=("Microsoft YaHei UI", 8),
            ).pack(anchor="w", padx=14, pady=(2, 10))

        self.name_var = tk.StringVar()
        self.parent_var = tk.StringVar(
            value=str(Path.home() / "Documents" / "AI Projects")
        )
        self.profile_var = tk.StringVar(value="通用项目（general）")
        self.git_var = tk.BooleanVar(value=True)

        form = tk.Frame(
            tab,
            bg=SURFACE,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=20,
            pady=14,
        )
        form.grid(row=3, column=0, sticky="nsew")
        form.columnconfigure(1, weight=1)
        self._field(form, 0, "项目名称", self.name_var)
        self._field(form, 1, "保存位置", self.parent_var, self._choose_parent)

        ttk.Label(form, text="项目类型", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=8
        )
        ttk.Combobox(
            form,
            textvariable=self.profile_var,
            values=tuple(PROFILE_LABELS),
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", padx=(14, 8), pady=8)
        ttk.Checkbutton(
            form,
            text="初始化本地 Git（不会提交或上传）",
            variable=self.git_var,
        ).grid(row=2, column=2, sticky="w", pady=8)

        ttk.Label(
            form,
            text="初始想法（可选）",
            style="Card.TLabel",
        ).grid(row=3, column=0, sticky="nw", pady=8)
        self.idea_text = tk.Text(
            form,
            height=3,
            wrap="word",
            bg="#f8fafb",
            fg=NAVY,
            insertbackground=NAVY,
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 9),
            padx=8,
            pady=6,
        )
        self.idea_text.grid(
            row=3,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(14, 0),
            pady=8,
        )
        ttk.Label(
            form,
            text=(
                "它只作为启动访谈输入，不会被视为已经批准的 Contract。"
                "留空也可以。"
            ),
            style="Card.TLabel",
        ).grid(row=4, column=1, columnspan=2, sticky="w", padx=(14, 0))

        action_bar = ttk.Frame(tab, style="Page.TFrame")
        action_bar.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        action_bar.columnconfigure(0, weight=1)
        ttk.Label(
            action_bar,
            text="从 Discussion 开始，确认后进入 Goal。",
            style="Subtitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            action_bar,
            text="只创建项目",
            style="Secondary.TButton",
            command=lambda: self._create(start_codex=False),
        ).grid(row=0, column=1, padx=(12, 8))
        self.create_and_start_button = ttk.Button(
            action_bar,
            text="创建并启动 Codex 讨论",
            style="Primary.TButton",
            command=lambda: self._create(start_codex=True),
        )
        self.create_and_start_button.grid(row=0, column=2)

    def _build_manage(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(8, weight=1)
        ttk.Label(tab, text="项目控制台", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            tab,
            text="选择项目，查看状态、启动真实 Codex 任务或准备迁移。",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 14))

        recent_card = tk.Frame(
            tab,
            bg=SURFACE,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=14,
            pady=10,
        )
        recent_card.grid(row=2, column=0, sticky="ew")
        recent_card.columnconfigure(0, weight=1)
        ttk.Label(
            recent_card,
            text="最近项目",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            recent_card,
            text="刷新",
            command=self._refresh_projects,
        ).grid(row=0, column=1, sticky="e")
        self.project_tree = ttk.Treeview(
            recent_card,
            columns=("state", "handoff", "updated"),
            show="tree headings",
            height=2,
            style="Factory.Treeview",
            selectmode="browse",
        )
        self.project_tree.heading("#0", text="项目")
        self.project_tree.heading("state", text="模式 / Goal")
        self.project_tree.heading("handoff", text="Handoff")
        self.project_tree.heading("updated", text="最近更新")
        self.project_tree.column("#0", width=230, minwidth=170)
        self.project_tree.column("state", width=155, minwidth=120)
        self.project_tree.column("handoff", width=90, minwidth=75)
        self.project_tree.column("updated", width=135, minwidth=110)
        self.project_tree.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        self.project_tree.bind("<<TreeviewSelect>>", self._select_recent)

        self.project_var = tk.StringVar()
        path_row = ttk.Frame(tab, style="Page.TFrame")
        path_row.grid(row=3, column=0, sticky="ew", pady=(10, 8))
        path_row.columnconfigure(1, weight=1)
        self._field(
            path_row, 0, "当前项目", self.project_var, self._choose_project
        )

        badges = ttk.Frame(tab, style="Page.TFrame")
        badges.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.mode_badge_var = tk.StringVar(value="模式：未选择")
        self.goal_badge_var = tk.StringVar(value="Goal：未选择")
        self.handoff_badge_var = tk.StringVar(value="Handoff：未检查")
        self.version_badge_var = tk.StringVar(value="Factory：未知")
        for index, variable in enumerate(
            (
                self.mode_badge_var,
                self.goal_badge_var,
                self.handoff_badge_var,
                self.version_badge_var,
            )
        ):
            ttk.Label(
                badges,
                textvariable=variable,
                style="Badge.TLabel",
            ).grid(row=0, column=index, padx=(0, 7), sticky="w")

        self.codex_launch_button = ttk.Button(
            tab,
            text="启动 / 继续 Codex 任务",
            command=self._launch_codex,
            style="Primary.TButton",
        )
        self.codex_launch_button.grid(
            row=5, column=0, sticky="ew", pady=(2, 10)
        )
        self.start_prompt_button = self.codex_launch_button

        actions = ttk.Frame(tab, style="Page.TFrame")
        actions.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        for column in range(3):
            actions.columnconfigure(column, weight=1)
        buttons = (
            ("查看项目状态", self._status),
            ("运行完整验证", self._doctor),
            ("刷新交接检查点", self._checkpoint),
            ("导出网页 / API 包", self._export),
            ("复制准备切换提示", lambda: self._copy_prompt("prepare")),
            ("打开项目目录", self._open_project),
        )
        for index, (label, command) in enumerate(buttons):
            ttk.Button(
                actions,
                text=label,
                command=command,
                style="Compact.TButton",
            ).grid(
                row=index // 3,
                column=index % 3,
                padx=4,
                pady=3,
                sticky="ew",
            )

        migration = ttk.Frame(tab, style="Page.TFrame")
        migration.grid(row=7, column=0, sticky="ew", pady=(0, 8))
        for column in range(3):
            migration.columnconfigure(column, weight=1)
        for index, (label, command) in enumerate(
            (
                ("复制启动 / 继续提示", lambda: self._copy_prompt("start")),
                ("复制新 Agent 接管提示", lambda: self._copy_prompt("takeover")),
                ("安装本机 Agent 集成", self._sync_adapters),
            )
        ):
            ttk.Button(
                migration,
                text=label,
                command=command,
                style="Compact.TButton",
            ).grid(row=0, column=index, padx=4, sticky="ew")

        self.output = tk.Text(
            tab,
            wrap="word",
            height=4,
            bg="#f8fafb",
            fg=NAVY,
            relief="solid",
            borderwidth=0,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=12,
            pady=10,
            font=("Cascadia Mono", 9),
        )
        self.output.grid(row=8, column=0, sticky="nsew")
        self.output.configure(state="disabled")
        self._set_output(
            "选择最近项目，或用“浏览”指定其他 Factory 项目。\n\n"
            "主按钮会创建一个真实可见的启动确认轮次：首条用户消息包含完整"
            "项目输入，Codex 只生成一张简短启动确认，不读取文件或调用工具。"
            "任务和首轮创建后会立即打开；启动卡通常约 10–20 秒生成完成。"
            "完成后回复“继续”，再开始真实访谈。只有"
            " App Server 不可用或确认超时，才退回需要手动发送的预填草稿。"
        )

    def _show_page(self, name: str) -> None:
        if name not in {"create", "manage"}:
            return
        self._current_page = name
        page = self.create_tab if name == "create" else self.manage_tab
        page.tkraise()
        for key, button in self.nav_buttons.items():
            selected = key == name
            button.configure(
                bg="#1f4d6b" if selected else SIDEBAR,
                fg="#ffffff" if selected else "#cbd8e1",
            )
        if name == "manage" and hasattr(self, "project_tree"):
            self._refresh_projects()

    def _refresh_projects(self) -> None:
        if not hasattr(self, "project_tree") or self._busy:
            return
        raw = self.parent_var.get().strip() if hasattr(self, "parent_var") else ""
        if not raw:
            return
        summaries = discover_projects(Path(raw))
        self._project_rows.clear()
        for item in self.project_tree.get_children():
            self.project_tree.delete(item)
        for index, summary in enumerate(summaries):
            iid = f"project-{index}"
            self._project_rows[iid] = summary
            updated = summary.updated_at.replace("T", " ")[:16]
            self.project_tree.insert(
                "",
                "end",
                iid=iid,
                text=summary.project_name,
                values=(
                    f"{summary.mode} / {summary.goal_status}",
                    f"r{summary.handoff_revision}",
                    updated or "unknown",
                ),
            )

    def _select_recent(self, _event: object | None = None) -> None:
        selection = self.project_tree.selection()
        if not selection:
            return
        summary = self._project_rows.get(selection[0])
        if summary is None:
            return
        self.project_var.set(str(summary.project_path))
        self._set_summary_badges(summary)
        self._set_output(
            f"已选择：{summary.project_name}\n"
            f"目录：{summary.project_path}\n"
            f"状态：{summary.mode} / {summary.goal_status}\n"
            f"Handoff revision：{summary.handoff_revision}\n\n"
            "可以直接启动 Codex，或先运行“查看项目状态 / 完整验证”。"
        )

    def _set_summary_badges(self, summary: ProjectSummary) -> None:
        self.mode_badge_var.set(f"模式：{summary.mode}")
        self.goal_badge_var.set(f"Goal：{summary.goal_status}")
        self.handoff_badge_var.set(f"Handoff：r{summary.handoff_revision}")
        self.version_badge_var.set(f"Factory：{summary.factory_version}")

    def _choose_parent(self) -> None:
        selected = filedialog.askdirectory(title="选择新项目的父目录")
        if selected:
            self.parent_var.set(selected)
            self._refresh_projects()

    def _choose_project(self) -> None:
        selected = filedialog.askdirectory(title="选择 Factory 项目目录")
        if selected:
            self.project_var.set(selected)
            self.status_var.set("已选择项目，可查看状态或启动 Codex")

    def _project(self) -> Path:
        raw = self.project_var.get().strip()
        if not raw:
            raise FactoryError("请先选择项目目录。")
        return Path(raw)

    def _selected_project(self) -> Path | None:
        try:
            return self._project()
        except Exception as exc:
            self._failed(exc)
            return None

    def _set_output(self, text: str, error: bool = False) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.tag_add("all", "1.0", "end")
        self.output.tag_config("all", foreground=ERROR if error else NAVY)
        self.output.configure(state="disabled")

    def _interactive_widgets(self, parent: tk.Misc) -> list[tk.Widget]:
        widgets: list[tk.Widget] = []
        for child in parent.winfo_children():
            if isinstance(
                child,
                (
                    tk.Button,
                    tk.Text,
                    ttk.Button,
                    ttk.Entry,
                    ttk.Combobox,
                    ttk.Checkbutton,
                    ttk.Treeview,
                ),
            ):
                widgets.append(child)
            widgets.extend(self._interactive_widgets(child))
        return widgets

    def _set_busy(self, busy: bool) -> None:
        if busy == self._busy:
            return
        self._busy = busy
        if busy:
            self._saved_widget_states = {}
            for widget in self._interactive_widgets(self):
                try:
                    state = str(widget.cget("state"))
                    self._saved_widget_states[widget] = state
                    widget.configure(state="disabled")
                except tk.TclError:
                    continue
        else:
            for widget, state in self._saved_widget_states.items():
                try:
                    if widget.winfo_exists():
                        widget.configure(state=state)
                except tk.TclError:
                    continue
            self._saved_widget_states = {}

    def _post_to_ui(self, callback: Callable[[], None]) -> None:
        if self._closing:
            return
        try:
            self.after(0, callback)
        except (tk.TclError, RuntimeError):
            return

    def _background(
        self,
        label: str,
        action: Callable[[], object],
        success: Callable[[object], None],
    ) -> None:
        if self._busy:
            self.bell()
            self.status_var.set("已有操作正在进行，请等待完成")
            return
        self._set_busy(True)
        self.status_var.set(label)

        def deliver_failure(error: Exception) -> None:
            if self._closing:
                return
            self._set_busy(False)
            self._failed(error)

        def deliver_success(result: object) -> None:
            if self._closing:
                return
            self._set_busy(False)
            try:
                success(result)
            except Exception as exc:
                self._failed(exc)

        def worker() -> None:
            try:
                result = action()
            except Exception as exc:
                self._post_to_ui(
                    lambda exc=exc: deliver_failure(exc)
                )
            else:
                self._post_to_ui(
                    lambda result=result: deliver_success(result)
                )

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self) -> None:
        if self._busy:
            messagebox.showinfo(
                "操作仍在进行",
                "当前操作尚未结束。为避免留下半创建项目或中断正在启动的 Codex "
                "任务，Factory 会在这段短操作完成后再允许关闭。",
            )
            return
        self._closing = True
        self.destroy()

    def _failed(self, exc: Exception) -> None:
        self.status_var.set("操作失败")
        message = str(exc)
        if hasattr(self, "output"):
            self._set_output(message, error=True)
        messagebox.showerror("AI Project Factory", message)

    def _create(self, start_codex: bool = True) -> None:
        profile = PROFILE_LABELS.get(self.profile_var.get())
        if profile not in PROFILES:
            self._failed(FactoryError("请选择有效的项目类型。"))
            return
        request = CreateProjectRequest(
            parent=Path(self.parent_var.get()),
            project_name=self.name_var.get(),
            profile=profile,
            initialize_git=self.git_var.get(),
        )

        def done(raw: object) -> None:
            result = raw
            self.last_project = result.project_path  # type: ignore[attr-defined]
            self.project_var.set(str(self.last_project))
            self._show_page("manage")
            self._refresh_projects()
            if start_codex:
                self._launch_codex_path(
                    self.last_project,
                    created=True,
                    initial_context=self.idea_text.get("1.0", "end").strip(),
                )
            else:
                self.status_var.set("项目已创建")
                self._set_output(
                    f"项目已安全创建：\n{self.last_project}\n\n"
                    "当前状态为 Discussion / none。"
                    "需要时点击“启动 / 继续 Codex 任务”。"
                )

        self._background("正在原子创建并校验项目…", lambda: create_project(request), done)

    def _relay(self, raw: object, label: str) -> None:
        result = raw
        text = (result.stdout + result.stderr).strip()  # type: ignore[attr-defined]
        self._set_output(text or label, error=not result.ok)  # type: ignore[attr-defined]
        self.status_var.set(label if result.ok else "操作失败")  # type: ignore[attr-defined]

    def _status(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        self._background(
            "正在读取状态…",
            lambda: inspect_project(project),
            self._show_status,
        )

    def _show_status(self, raw: object) -> None:
        result = raw
        if not result.ok:  # type: ignore[attr-defined]
            self._relay(result, "状态读取失败")
            return
        try:
            state = json.loads(result.stdout)  # type: ignore[attr-defined]
        except (TypeError, json.JSONDecodeError):
            self._relay(result, "状态已刷新")
            return
        mode = {
            "discussion": "Discussion（可讨论、调整、pushback）",
            "goal": "Goal（按已确认目标持续执行）",
        }.get(str(state.get("mode")), str(state.get("mode")))
        goal_status = {
            "none": "尚未建立 Goal",
            "active": "执行中",
            "paused": "已暂停",
            "blocked": "被真实阻塞",
            "completed": "已完成",
            "needs_revision": "Contract 需要重议",
        }.get(str(state.get("goal_status")), str(state.get("goal_status")))
        fresh = bool(state.get("handoff_fresh"))
        self.mode_badge_var.set(f"模式：{state.get('mode', 'unknown')}")
        self.goal_badge_var.set(f"Goal：{state.get('goal_status', 'unknown')}")
        self.handoff_badge_var.set(
            "Handoff：最新" if fresh else "Handoff：需更新"
        )
        self.version_badge_var.set(
            f"Factory：{state.get('factory_version', 'unknown')}"
        )
        next_hint = (
            "可以切换聊天或 Agent。"
            if fresh
            else "先让当前 Agent 更新 HANDOFF，再刷新交接检查点。"
        )
        self._set_output(
            "\n".join(
                (
                    f"项目：{state.get('project_name', 'unknown')}",
                    f"当前模式：{mode}",
                    f"Goal 状态：{goal_status}",
                    f"Contract revision：{state.get('contract_revision', 'unknown')}",
                    f"Goal revision：{state.get('active_goal_revision', 'unknown')}",
                    f"Handoff：{'已验证为最新' if fresh else '需要更新'}",
                    "",
                    f"建议：{next_hint}",
                )
            )
        )
        self.status_var.set("状态已刷新")

    def _doctor(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        self._background(
            "正在执行完整验证…",
            lambda: doctor_project(project, deep=True),
            lambda result: self._relay(result, "验证完成"),
        )

    def _checkpoint(self) -> None:
        if not messagebox.askokcancel(
            "刷新交接检查点",
            "请确认当前 Agent 已把语义状态写入 HANDOFF.md。\n\n"
            "Factory 将刷新 revision 和 fingerprints，但不会读取聊天补写摘要。",
        ):
            return
        project = self._selected_project()
        if project is None:
            return
        self._background(
            "正在刷新交接检查点…",
            lambda: checkpoint_project(
                project, updated_by="factory-gui", status="ready_for_compact"
            ),
            lambda result: self._relay(result, "交接检查点已刷新"),
        )

    def _export(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        selected = filedialog.asksaveasfilename(
            title="保存给网页或 API 模型的 Context Bundle",
            initialdir=str(project.parent),
            initialfile=f"{project.name}_CONTEXT_BUNDLE.md",
            defaultextension=".md",
            filetypes=(("Markdown", "*.md"), ("All files", "*.*")),
        )
        if not selected:
            return
        output = Path(selected)

        def done(raw: object) -> None:
            self._relay(raw, "网页/API 迁移包已导出")
            result = raw
            if result.ok:  # type: ignore[attr-defined]
                messagebox.showinfo(
                    "导出完成",
                    f"Context Bundle 已保存：\n{output}\n\n"
                    "请只额外上传目标模型当前任务真正需要的附件。",
                )

        self._background(
            "正在验证并导出迁移包…",
            lambda: export_project(project, output),
            done,
        )

    def _open_project(self) -> None:
        try:
            open_in_file_manager(self._project())
        except Exception as exc:
            self._failed(exc)

    def _launch_codex_path(
        self,
        project: Path,
        created: bool = False,
        initial_context: str | None = None,
    ) -> None:
        def started(thread_id: str, _deep_link: str) -> None:
            def update() -> None:
                self.status_var.set("Codex 已打开，正在生成可见启动确认")
                self._set_output(
                    f"真实 Codex 任务和首轮已创建，Codex 已打开并正在生成"
                    f"可见启动确认。\n\n"
                    f"项目：{project}\n"
                    f"任务 ID：{thread_id}\n\n"
                    "这一步只让模型输出固定启动卡，不读取文件，也不调用"
                    " Token Bridge、连接器或其他工具，通常约 10–20 秒。"
                    "你可以直接在 Codex 中看到生成过程。"
                )

            self._post_to_ui(update)

        def action() -> object:
            return launch_codex_project(
                project,
                "start",
                initial_context,
                on_started=started,
                turn_timeout=45.0,
            )

        def done(raw: object) -> None:
            result = raw
            if result.method == "app-server":  # type: ignore[attr-defined]
                detail = (
                    f"\n\n提示：{result.detail}"  # type: ignore[attr-defined]
                    if result.detail  # type: ignore[attr-defined]
                    else ""
                )
                prefix = (
                    "项目已创建，真实 Codex 任务也已启动。"
                    if created
                    else "真实 Codex 任务已启动。"
                )
                self._set_output(
                    f"{prefix}\n\n"
                    f"项目：{result.project_path}\n"  # type: ignore[attr-defined]
                    f"任务 ID：{result.thread_id}\n"  # type: ignore[attr-defined]
                    f"交接状态：{result.turn_status}"  # type: ignore[attr-defined]
                    f"{detail}\n\n"
                    "初始想法已经作为真实首轮用户消息显示，Codex 的启动确认"
                    "不是项目研究结论。现在请在已打开的 Codex 任务中回复"
                    "“继续”或直接补充要求，真实访谈和 Token Bridge 等动作"
                    "会在可见任务中执行。"
                )
                self.status_var.set("Codex 已打开，回复“继续”开始访谈")
                return

            self.clipboard_clear()
            self.clipboard_append(result.prompt)  # type: ignore[attr-defined]
            self.update()
            self._set_output(
                "Codex App Server 未能创建真实任务，已安全退回预填草稿。\n\n"
                f"项目：{result.project_path}\n\n"  # type: ignore[attr-defined]
                f"原因：{result.detail}\n\n"  # type: ignore[attr-defined]
                "请在 Codex 中检查提示后手动发送；提示已复制到剪贴板。"
            )
            self.status_var.set("Codex 草稿已打开，等待手动发送")

        self._background("正在创建并发送 Codex 任务…", action, done)

    def _launch_codex(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        self._launch_codex_path(project)

    def _copy_prompt(self, kind: str) -> None:
        project = self._selected_project()
        if project is None:
            return
        try:
            prompt = build_agent_prompt(project, kind)
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.update()
            self._set_output(
                f"已复制提示，可粘贴给在此目录工作的 Agent：\n\n{prompt}"
            )
            self.status_var.set("Agent 提示已复制")
        except Exception as exc:
            self._failed(exc)

    def _sync_adapters(self) -> None:
        if not messagebox.askyesno(
            "安装本机 Agent 集成",
            "这会在当前用户目录中安装或刷新由 Factory 管理的 Codex 与"
            " Claude Code Skill。\n\n"
            "它不会传输项目，不会影响云端/Cowork，也不会覆盖非 Factory 管理"
            "的同名 Skill。已安装 Skill 仍会调用当前这份 Factory；移动或删除"
            " Factory 后需要重新同步。\n\n如果某个项目另有同名 Skill，Codex"
            " 与 Claude Code 的优先/展示行为不同，需要你自行确认实际加载版本。"
            "\n\n继续吗？",
        ):
            return

        def action() -> str:
            home = Path.home()
            codex, claude = sync_agent_skills(
                (
                    home / ".agents" / "skills",
                    home / ".claude" / "skills",
                ),
                REPO_ROOT,
            )
            return json.dumps(
                {"codex": str(codex), "claude": str(claude)},
                ensure_ascii=False,
                indent=2,
            )

        def done(raw: object) -> None:
            self._set_output(
                "本机 Codex 与 Claude Code 集成已安装：\n"
                + str(raw)
                + "\n\n请新建或重新打开 Agent 会话，让运行时重新发现 Skill。"
            )
            self.status_var.set("本机 Agent 集成已安装")

        self._background("正在安装本机 Agent 集成…", action, done)


def launch_gui(smoke_test: bool = False) -> None:
    app = FactoryApp()
    if smoke_test:
        app.withdraw()
        app.after(80, app.destroy)
    app.mainloop()
