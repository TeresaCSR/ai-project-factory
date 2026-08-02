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
UI_FONT = "Segoe UI"
MONO_FONT = "Cascadia Mono"
PROFILE_LABELS = {
    "General project": "general",
    "Software development": "software",
    "Research / data": "research",
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
        self.status_var = tk.StringVar(value="Ready")
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
        style.configure(".", font=(UI_FONT, 10))
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Surface.TFrame", background=SURFACE)
        style.configure("Page.TFrame", background=BACKGROUND)
        style.configure(
            "Title.TLabel",
            background=BACKGROUND,
            foreground=NAVY,
            font=(UI_FONT, 21, "bold"),
        )
        style.configure(
            "Subtitle.TLabel", background=BACKGROUND, foreground=MUTED
        )
        style.configure(
            "CardTitle.TLabel",
            background=SURFACE,
            foreground=NAVY,
            font=(UI_FONT, 12, "bold"),
        )
        style.configure("Card.TLabel", background=SURFACE, foreground=NAVY)
        style.configure(
            "Primary.TButton",
            background=ACCENT,
            foreground="#ffffff",
            padding=(18, 10),
            font=(UI_FONT, 10, "bold"),
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
            font=(UI_FONT, 10, "bold"),
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
            font=(UI_FONT, 9, "bold"),
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
            font=(UI_FONT, 9, "bold"),
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
            font=(UI_FONT, 17, "bold"),
        ).pack(anchor="w", padx=22)
        tk.Label(
            sidebar,
            text=f"Portable workspace · v{FACTORY_VERSION}",
            bg=SIDEBAR,
            fg="#9eb4c4",
            font=(UI_FONT, 8),
        ).pack(anchor="w", padx=22, pady=(5, 28))

        self.nav_buttons: dict[str, tk.Button] = {}
        for key, label, command in (
            ("create", "＋  New project", lambda: self._show_page("create")),
            ("manage", "▣  Project console", lambda: self._show_page("manage")),
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
                font=(UI_FONT, 10, "bold"),
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
            font=(UI_FONT, 9),
        )
        self.status_label.pack(side="bottom", anchor="w", padx=22, pady=(8, 8))
        tk.Label(
            sidebar,
            text="Local first · plain Markdown · verifiable",
            bg=SIDEBAR,
            fg="#718b9d",
            justify="left",
            wraplength=176,
            font=(UI_FONT, 8),
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
            ttk.Button(parent, text="Browse…", command=browse).grid(
                row=row, column=2, sticky="ew", pady=8
            )

    def _build_create(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        ttk.Label(tab, text="New portable project", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            tab,
            text=(
                "Start from a fact layer you can check, then let the agent "
                "interview you."
            ),
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 18))

        flow = ttk.Frame(tab, style="Page.TFrame")
        flow.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        for column in range(3):
            flow.columnconfigure(column, weight=1)
        for index, (number, title, detail) in enumerate(
            (
                ("01", "Lay the ground", "Fact layer plus a local Git repo"),
                ("02", "Discuss", "Interview, research, push back"),
                ("03", "Execute", "Run to completion once approved"),
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
                font=(MONO_FONT, 10, "bold"),
            ).pack(anchor="w", padx=14, pady=(10, 2))
            tk.Label(
                card,
                text=title,
                bg=SURFACE,
                fg=NAVY,
                font=(UI_FONT, 10, "bold"),
            ).pack(anchor="w", padx=14)
            tk.Label(
                card,
                text=detail,
                bg=SURFACE,
                fg=MUTED,
                font=(UI_FONT, 8),
            ).pack(anchor="w", padx=14, pady=(2, 10))

        self.name_var = tk.StringVar()
        self.parent_var = tk.StringVar(
            value=str(Path.home() / "Documents" / "AI Projects")
        )
        self.profile_var = tk.StringVar(value="General project")
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
        self._field(form, 0, "Project name", self.name_var)
        self._field(form, 1, "Location", self.parent_var, self._choose_parent)

        ttk.Label(form, text="Project type", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", pady=8
        )
        ttk.Combobox(
            form,
            textvariable=self.profile_var,
            values=tuple(PROFILE_LABELS),
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", padx=(14, 8), pady=8)
        # Its own row rather than beside the combobox: the label is long
        # enough that sharing a column would squeeze the path field.
        ttk.Checkbutton(
            form,
            text="Initialise a local Git repo (no commit, nothing uploaded)",
            variable=self.git_var,
        ).grid(row=3, column=1, columnspan=2, sticky="w", padx=(14, 0))

        ttk.Label(
            form,
            text="Initial idea (optional)",
            style="Card.TLabel",
        ).grid(row=4, column=0, sticky="nw", pady=8)
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
            font=(UI_FONT, 9),
            padx=8,
            pady=6,
        )
        self.idea_text.grid(
            row=4,
            column=1,
            columnspan=2,
            sticky="ew",
            padx=(14, 0),
            pady=8,
        )
        ttk.Label(
            form,
            text=(
                "Seeds the opening interview. It is not treated as an "
                "approved contract, and leaving it empty is fine."
            ),
            style="Card.TLabel",
            wraplength=440,
            justify="left",
        ).grid(row=5, column=1, columnspan=2, sticky="w", padx=(14, 0))

        action_bar = ttk.Frame(tab, style="Page.TFrame")
        action_bar.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        action_bar.columnconfigure(0, weight=1)
        ttk.Label(
            action_bar,
            text="Starts in Discussion.\nMoves to Goal once you approve.",
            style="Subtitle.TLabel",
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            action_bar,
            text="Create only",
            style="Secondary.TButton",
            command=lambda: self._create(start_codex=False),
        ).grid(row=0, column=1, padx=(12, 8))
        self.create_and_start_button = ttk.Button(
            action_bar,
            text="Create and open a Codex discussion",
            style="Primary.TButton",
            command=lambda: self._create(start_codex=True),
        )
        self.create_and_start_button.grid(row=0, column=2)

    def _build_manage(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(8, weight=1)
        ttk.Label(tab, text="Project console", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            tab,
            text=(
                "Pick a project to read its state, start a real Codex task, "
                "or prepare a handoff."
            ),
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
            text="Recent projects",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            recent_card,
            text="Refresh",
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
        self.project_tree.heading("#0", text="Project")
        self.project_tree.heading("state", text="Mode / Goal")
        self.project_tree.heading("handoff", text="Handoff")
        self.project_tree.heading("updated", text="Updated")
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
            path_row, 0, "Current project", self.project_var, self._choose_project
        )

        badges = ttk.Frame(tab, style="Page.TFrame")
        badges.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self.mode_badge_var = tk.StringVar(value="Mode: none selected")
        self.goal_badge_var = tk.StringVar(value="Goal: none selected")
        self.handoff_badge_var = tk.StringVar(value="Handoff: not checked")
        self.version_badge_var = tk.StringVar(value="Factory: unknown")
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
            text="Start / continue the Codex task",
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
            ("Project state", self._status),
            ("Full validation", self._doctor),
            ("Refresh handoff checkpoint", self._checkpoint),
            ("Export web / API bundle", self._export),
            ("Copy: prepare to switch", lambda: self._copy_prompt("prepare")),
            ("Open project folder", self._open_project),
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
                ("Copy: start / continue", lambda: self._copy_prompt("start")),
                ("Copy: new agent takeover", lambda: self._copy_prompt("takeover")),
                ("Install local agent integration", self._sync_adapters),
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
            font=(MONO_FONT, 9),
        )
        self.output.grid(row=8, column=0, sticky="nsew")
        self.output.configure(state="disabled")
        self._set_output(
            "Pick a recent project, or use Browse to point at another "
            "Factory project.\n\n"
            "The main button creates a real, visible opening turn: the first "
            "user message carries your full input, and Codex answers with one "
            "short start card without reading files or calling tools. The "
            "task opens as soon as it exists; the card usually takes 10-20 "
            'seconds. Reply "continue" to begin the real interview.'
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
            f"Selected: {summary.project_name}\n"
            f"Folder: {summary.project_path}\n"
            f"State: {summary.mode} / {summary.goal_status}\n"
            f"Handoff revision: {summary.handoff_revision}\n\n"
            "You can start Codex now, or read the project state and run the "
            "full validation first."
        )

    def _set_summary_badges(self, summary: ProjectSummary) -> None:
        self.mode_badge_var.set(f"Mode: {summary.mode}")
        self.goal_badge_var.set(f"Goal: {summary.goal_status}")
        self.handoff_badge_var.set(f"Handoff: r{summary.handoff_revision}")
        self.version_badge_var.set(f"Factory: {summary.factory_version}")

    def _choose_parent(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose the parent folder for the new project"
        )
        if selected:
            self.parent_var.set(selected)
            self._refresh_projects()

    def _choose_project(self) -> None:
        selected = filedialog.askdirectory(title="Choose a Factory project folder")
        if selected:
            self.project_var.set(selected)
            self.status_var.set("Project selected")

    def _project(self) -> Path:
        raw = self.project_var.get().strip()
        if not raw:
            raise FactoryError("Choose a project folder first.")
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
            self.status_var.set("Another operation is running; please wait")
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
                "Operation still running",
                "The current operation has not finished. To avoid leaving a "
                "half-created project or interrupting a Codex task that is "
                "starting, Factory will allow closing once this short "
                "operation completes.",
            )
            return
        self._closing = True
        self.destroy()

    def _failed(self, exc: Exception) -> None:
        self.status_var.set("Operation failed")
        message = str(exc)
        if hasattr(self, "output"):
            self._set_output(message, error=True)
        messagebox.showerror("AI Project Factory", message)

    def _create(self, start_codex: bool = True) -> None:
        profile = PROFILE_LABELS.get(self.profile_var.get())
        if profile not in PROFILES:
            self._failed(FactoryError("Choose a valid project type."))
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
                self.status_var.set("Project created")
                self._set_output(
                    f"Project created safely:\n{self.last_project}\n\n"
                    "It starts in Discussion / none. Use "
                    '"Start / continue the Codex task" when you are ready.'
                )

        self._background(
            "Creating and validating the project atomically…",
            lambda: create_project(request),
            done,
        )

    def _relay(self, raw: object, label: str) -> None:
        result = raw
        text = (result.stdout + result.stderr).strip()  # type: ignore[attr-defined]
        self._set_output(text or label, error=not result.ok)  # type: ignore[attr-defined]
        self.status_var.set(label if result.ok else "Operation failed")  # type: ignore[attr-defined]

    def _status(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        self._background(
            "Reading state…",
            lambda: inspect_project(project),
            self._show_status,
        )

    def _show_status(self, raw: object) -> None:
        result = raw
        if not result.ok:  # type: ignore[attr-defined]
            self._relay(result, "Could not read the state")
            return
        try:
            state = json.loads(result.stdout)  # type: ignore[attr-defined]
        except (TypeError, json.JSONDecodeError):
            self._relay(result, "State refreshed")
            return
        mode = {
            "discussion": "Discussion (open to debate, changes, pushback)",
            "goal": "Goal (executing an approved objective)",
        }.get(str(state.get("mode")), str(state.get("mode")))
        goal_status = {
            "none": "no goal yet",
            "active": "in progress",
            "paused": "paused",
            "blocked": "genuinely blocked",
            "completed": "completed",
            "needs_revision": "contract needs revisiting",
        }.get(str(state.get("goal_status")), str(state.get("goal_status")))
        fresh = bool(state.get("handoff_fresh"))
        self.mode_badge_var.set(f"Mode: {state.get('mode', 'unknown')}")
        self.goal_badge_var.set(f"Goal: {state.get('goal_status', 'unknown')}")
        self.handoff_badge_var.set(
            "Handoff: current" if fresh else "Handoff: needs update"
        )
        self.version_badge_var.set(
            f"Factory: {state.get('factory_version', 'unknown')}"
        )
        next_hint = (
            "Safe to switch chats or agents."
            if fresh
            else "Have the current agent update HANDOFF, then refresh the "
            "checkpoint."
        )
        self._set_output(
            "\n".join(
                (
                    f"Project: {state.get('project_name', 'unknown')}",
                    f"Mode: {mode}",
                    f"Goal status: {goal_status}",
                    f"Contract revision: {state.get('contract_revision', 'unknown')}",
                    f"Goal revision: {state.get('active_goal_revision', 'unknown')}",
                    f"Handoff: {'verified current' if fresh else 'needs update'}",
                    "",
                    f"Suggested next step: {next_hint}",
                )
            )
        )
        self.status_var.set("State refreshed")

    def _doctor(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        self._background(
            "Running the full validation…",
            lambda: doctor_project(project, deep=True),
            lambda result: self._relay(result, "Validation finished"),
        )

    def _checkpoint(self) -> None:
        if not messagebox.askokcancel(
            "Refresh handoff checkpoint",
            "Confirm that the current agent has written the semantic state "
            "into HANDOFF.md.\n\n"
            "Factory refreshes revisions and fingerprints. It does not read "
            "your chat, and will not invent a summary.",
        ):
            return
        project = self._selected_project()
        if project is None:
            return
        self._background(
            "Refreshing the handoff checkpoint…",
            lambda: checkpoint_project(
                project, updated_by="factory-gui", status="ready_for_compact"
            ),
            lambda result: self._relay(result, "Handoff checkpoint refreshed"),
        )

    def _export(self) -> None:
        project = self._selected_project()
        if project is None:
            return
        selected = filedialog.asksaveasfilename(
            title="Save a context bundle for a web or API model",
            initialdir=str(project.parent),
            initialfile=f"{project.name}_CONTEXT_BUNDLE.md",
            defaultextension=".md",
            filetypes=(("Markdown", "*.md"), ("All files", "*.*")),
        )
        if not selected:
            return
        output = Path(selected)

        def done(raw: object) -> None:
            self._relay(raw, "Context bundle exported")
            result = raw
            if result.ok:  # type: ignore[attr-defined]
                messagebox.showinfo(
                    "Export complete",
                    f"Context bundle saved:\n{output}\n\n"
                    "Upload only the extra attachments the target model "
                    "genuinely needs for the task at hand.",
                )

        self._background(
            "Validating and exporting the bundle…",
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
                self.status_var.set("Codex is open, writing the start card")
                self._set_output(
                    "A real Codex task and its first turn exist, and Codex is "
                    "open and writing the visible start card.\n\n"
                    f"Project: {project}\n"
                    f"Task ID: {thread_id}\n\n"
                    "This turn only prints a fixed start card. It reads no "
                    "files and calls no tools, so it usually takes 10-20 "
                    "seconds, and you can watch it happen in Codex."
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
                    f"\n\nNote: {result.detail}"  # type: ignore[attr-defined]
                    if result.detail  # type: ignore[attr-defined]
                    else ""
                )
                prefix = (
                    "Project created, and a real Codex task started."
                    if created
                    else "Real Codex task started."
                )
                self._set_output(
                    f"{prefix}\n\n"
                    f"Project: {result.project_path}\n"  # type: ignore[attr-defined]
                    f"Task ID: {result.thread_id}\n"  # type: ignore[attr-defined]
                    f"Turn status: {result.turn_status}"  # type: ignore[attr-defined]
                    f"{detail}\n\n"
                    "Your initial idea is visible as a real first user "
                    "message, and the start card is an acknowledgement rather "
                    'than research. Reply "continue" in the open Codex task, '
                    "or add requirements directly; the real interview happens "
                    "there where you can see it."
                )
                self.status_var.set('Codex open — reply "continue" to begin')
                return

            self.clipboard_clear()
            self.clipboard_append(result.prompt)  # type: ignore[attr-defined]
            self.update()
            self._set_output(
                "The Codex App Server could not create a real task, so "
                "Factory fell back to a prefilled draft.\n\n"
                f"Project: {result.project_path}\n\n"  # type: ignore[attr-defined]
                f"Reason: {result.detail}\n\n"  # type: ignore[attr-defined]
                "Check the prompt in Codex and send it yourself. It is also "
                "on your clipboard."
            )
            self.status_var.set("Codex draft open, waiting for you to send")

        self._background("Creating and sending the Codex task…", action, done)

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
                "Copied. Paste this to an agent working in this folder:\n\n"
                f"{prompt}"
            )
            self.status_var.set("Agent prompt copied")
        except Exception as exc:
            self._failed(exc)

    def _sync_adapters(self) -> None:
        if not messagebox.askyesno(
            "Install local agent integration",
            "This installs or refreshes the Factory-managed Codex and Claude "
            "Code skills in your user profile.\n\n"
            "It uploads nothing, does not affect cloud sessions, and will not "
            "overwrite a skill of the same name that Factory does not manage. "
            "The installed skill calls this copy of Factory, so re-run it if "
            "you move or delete this folder.\n\n"
            "If a project defines a skill with the same name, Codex and "
            "Claude Code resolve the conflict differently, so check which one "
            "actually loads.\n\nContinue?",
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
                "Local Codex and Claude Code integration installed:\n"
                + str(raw)
                + "\n\nStart a new agent session, or reopen the current one, "
                "so the runtime rediscovers the skill."
            )
            self.status_var.set("Local agent integration installed")

        self._background("Installing the local agent integration…", action, done)


def launch_gui(smoke_test: bool = False) -> None:
    app = FactoryApp()
    if smoke_test:
        app.withdraw()
        app.after(80, app.destroy)
    app.mainloop()
