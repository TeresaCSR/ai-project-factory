from __future__ import annotations

import sys
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_project_factory.core import FactoryError  # noqa: E402
from ai_project_factory.gui import SIDEBAR, FactoryApp  # noqa: E402


class GuiRegressionTests(unittest.TestCase):
    def make_app(self) -> FactoryApp:
        try:
            app = FactoryApp()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        app.withdraw()
        return app

    def test_background_failure_is_delivered_and_unlocks_ui(self) -> None:
        app = self.make_app()
        try:
            with mock.patch(
                "ai_project_factory.gui.messagebox.showerror"
            ) as showerror:
                def fail() -> object:
                    raise FactoryError("injected GUI failure")

                app._background("测试中", fail, lambda _result: None)
                deadline = time.monotonic() + 5

                def finish_when_delivered() -> None:
                    if not app._busy or time.monotonic() >= deadline:
                        app.quit()
                    else:
                        app.after(10, finish_when_delivered)

                app.after(10, finish_when_delivered)
                app.mainloop()

                self.assertFalse(app._busy)
                self.assertEqual(app.status_var.get(), "操作失败")
                self.assertIn(
                    "injected GUI failure",
                    app.output.get("1.0", "end"),
                )
                showerror.assert_called_once()
        finally:
            app._closing = True
            app.destroy()

    def test_status_remains_mapped_at_minimum_height(self) -> None:
        app = self.make_app()
        try:
            app.deiconify()
            app.geometry("780x600")
            app.update()
            self.assertTrue(app.status_label.winfo_ismapped())
            bottom = (
                app.status_label.winfo_rooty()
                - app.winfo_rooty()
                + app.status_label.winfo_height()
            )
            self.assertLessEqual(bottom, app.winfo_height())
        finally:
            app._closing = True
            app.destroy()

    def test_sidebar_navigation_has_unambiguous_selected_page(self) -> None:
        app = self.make_app()
        try:
            app.deiconify()
            app.update()
            self.assertEqual(app._current_page, "create")
            self.assertNotEqual(
                app.nav_buttons["create"].cget("bg"),
                app.nav_buttons["manage"].cget("bg"),
            )
            self.assertEqual(app.nav_buttons["manage"].cget("bg"), SIDEBAR)
            app._show_page("manage")
            app.update()
            self.assertEqual(app._current_page, "manage")
            self.assertNotEqual(
                app.nav_buttons["manage"].cget("bg"),
                app.nav_buttons["create"].cget("bg"),
            )
        finally:
            app._closing = True
            app.destroy()

    def test_create_success_moves_to_highlighted_start_step(self) -> None:
        app = self.make_app()
        try:
            created = Path("C:/AI Projects/Factory Trial")
            app.name_var.set("Factory Trial")
            app.parent_var.set(str(created.parent))

            def run_now(
                _label: str,
                action: object,
                success: object,
            ) -> None:
                success(action())  # type: ignore[operator]

            with (
                mock.patch(
                    "ai_project_factory.gui.create_project",
                    return_value=SimpleNamespace(project_path=created),
                ),
                mock.patch(
                    "ai_project_factory.gui.launch_codex_project",
                    return_value=SimpleNamespace(
                        project_path=created,
                        prompt="启动提示",
                        deep_link="codex://threads/test",
                        method="app-server",
                        thread_id="test-thread",
                        turn_id="test-turn",
                        turn_status="completed",
                        detail="",
                    ),
                ) as launch_codex,
                mock.patch.object(app, "_background", side_effect=run_now),
            ):
                app._create()

            self.assertEqual(app.project_var.get(), str(created))
            self.assertEqual(app._current_page, "manage")
            self.assertEqual(
                app.start_prompt_button.cget("style"),
                "Primary.TButton",
            )
            launch_codex.assert_called_once()
            self.assertEqual(
                launch_codex.call_args.args[:2],
                (created, "start"),
            )
            self.assertIn("真实 Codex 任务", app.output.get("1.0", "end"))
            self.assertIn("Token Bridge", app.output.get("1.0", "end"))
            self.assertIn("真实首轮用户消息", app.output.get("1.0", "end"))
            self.assertIn("启动确认", app.output.get("1.0", "end"))
        finally:
            app._closing = True
            app.destroy()

    def test_manage_page_explains_real_task_and_safe_fallback(self) -> None:
        app = self.make_app()
        try:
            guidance = app.output.get("1.0", "end")
            self.assertIn("真实可见的启动确认轮次", guidance)
            self.assertIn("10–20 秒", guidance)
            self.assertIn("预填草稿", guidance)
        finally:
            app._closing = True
            app.destroy()

    def test_copied_agent_prompt_includes_selected_project_path(self) -> None:
        app = self.make_app()
        try:
            project = Path("C:/AI Projects/Factory Trial")
            app.project_var.set(str(project))
            expected = (
                f"本地项目目录（当前 Agent 可访问）：{project}\n\n"
                "请读取 AI_START_HERE.md。"
            )
            with mock.patch(
                "ai_project_factory.gui.build_agent_prompt",
                return_value=expected,
            ) as build_prompt:
                app._copy_prompt("start")
            prompt = app.clipboard_get()
            self.assertIn(str(project), prompt)
            self.assertIn("AI_START_HERE.md", prompt)
            self.assertIn("当前 Agent 可访问", prompt)
            build_prompt.assert_called_once_with(project, "start")
        finally:
            app._closing = True
            app.destroy()

    def test_codex_button_opens_bound_task_and_keeps_prompt_fallback(self) -> None:
        app = self.make_app()
        try:
            project = Path("C:/AI Projects/Factory Trial")
            app.project_var.set(str(project))
            result = SimpleNamespace(
                project_path=project,
                prompt="请读取 AI_START_HERE.md。",
                deep_link="codex://threads/test-thread",
                method="draft",
                thread_id=None,
                turn_id=None,
                turn_status="awaiting_user_send",
                detail="injected app server failure",
            )
            def run_now(
                _label: str,
                action: object,
                success: object,
            ) -> None:
                success(action())  # type: ignore[operator]

            with (
                mock.patch(
                    "ai_project_factory.gui.launch_codex_project",
                    return_value=result,
                ) as launch_codex,
                mock.patch.object(app, "_background", side_effect=run_now),
            ):
                app._launch_codex()
            launch_codex.assert_called_once()
            self.assertEqual(app.clipboard_get(), result.prompt)
            self.assertIn("预填草稿", app.output.get("1.0", "end"))
            self.assertEqual(
                app.status_var.get(),
                "Codex 草稿已打开，等待手动发送",
            )
        finally:
            app._closing = True
            app.destroy()

    def test_busy_window_cannot_close_mid_transaction(self) -> None:
        app = self.make_app()
        try:
            app._busy = True
            with mock.patch(
                "ai_project_factory.gui.messagebox.showinfo"
            ) as showinfo:
                app._on_close()
            self.assertFalse(app._closing)
            self.assertTrue(app.winfo_exists())
            showinfo.assert_called_once()
        finally:
            app._busy = False
            app._closing = True
            app.destroy()

    def test_launcher_avoids_parse_time_errorlevel_expansion(self) -> None:
        launcher = (ROOT / "AI Project Factory.cmd").read_text("utf-8")
        self.assertNotIn("%errorlevel%==", launcher.lower())
        self.assertIn("if errorlevel 1 goto try_python", launcher.lower())


if __name__ == "__main__":
    unittest.main()
