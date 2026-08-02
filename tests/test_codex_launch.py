from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_project_factory.core import (  # noqa: E402
    CodexAppServerClient,
    FactoryError,
    build_agent_prompt,
    build_codex_bootstrap_turn_prompt,
    build_codex_quick_start_card,
    build_codex_deep_link,
    discover_projects,
    launch_codex_project,
    launch_codex_task,
    minimal_codex_app_server_command,
    open_codex_deep_link,
    run_command,
)


class NonClosingStringIO(io.StringIO):
    def close(self) -> None:
        self.flush()


class FakeAppServerProcess:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.stdin = NonClosingStringIO()
        self.stdout = io.StringIO(
            "".join(json.dumps(message) + "\n" for message in messages)
        )
        self.stderr = io.StringIO("")
        self.returncode = 0
        self.wait_calls = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class CodexLaunchTests(unittest.TestCase):
    def make_project(self, parent: Path, name: str = "中文 Project") -> Path:
        project = parent / name
        (project / ".ai").mkdir(parents=True)
        (project / ".ai" / "project_runtime.py").write_text(
            "# test runtime\n", encoding="utf-8"
        )
        (project / "AI_START_HERE.md").write_text(
            "# Start\n", encoding="utf-8"
        )
        return project

    def test_deep_link_binds_path_and_prefills_start_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp))
            result = build_codex_deep_link(project)
            parsed = urlparse(result.deep_link)
            query = parse_qs(parsed.query)

            self.assertEqual(parsed.scheme, "codex")
            self.assertEqual(parsed.netloc, "threads")
            self.assertEqual(parsed.path, "/new")
            self.assertEqual(query["path"], [str(project.resolve())])
            self.assertEqual(query["prompt"], [result.prompt])
            self.assertIn("AI_START_HERE.md", result.prompt)
            self.assertIn("Discussion", result.prompt)
            self.assertIn("Goal", result.prompt)

    def test_launch_opens_exact_generated_deep_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp))
            with mock.patch(
                "ai_project_factory.core.launch_codex_task",
                side_effect=FactoryError("app server unavailable"),
            ), mock.patch(
                "ai_project_factory.core.open_codex_deep_link"
            ) as open_link:
                result = launch_codex_project(project)
            self.assertEqual(result.method, "draft")
            open_link.assert_called_once_with(result.deep_link)

    def test_initial_idea_is_marked_as_unapproved_interview_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp))
            prompt = build_agent_prompt(
                project,
                initial_context="做一个帮助整理实验数据的工具。",
            )
            self.assertIn("启动访谈的主题", prompt)
            self.assertIn("不是已经批准的 Contract", prompt)
            self.assertIn("整理实验数据", prompt)
            self.assertIn("不得忽略后再泛化询问", prompt)
            self.assertIn("最多提出 3 个", prompt)
            self.assertIn("可选的全局记忆文件", prompt)
            self.assertIn("Token Bridge", prompt)
            self.assertIn("正常 Codex 宿主执行", prompt)
            self.assertIn("用户回复“继续”", prompt)

    @unittest.skipIf(
        os.name != "nt" and sys.version_info < (3, 12),
        "this patches os.name to 'nt' globally, and before 3.12 pathlib "
        "then refuses to construct a path on a POSIX host",
    )
    def test_windows_commands_hide_console_subprocesses(self) -> None:
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with (
            mock.patch("ai_project_factory.core.os.name", "nt"),
            mock.patch.object(
                subprocess,
                "CREATE_NEW_CONSOLE",
                0x00000010,
                create=True,
            ),
            mock.patch(
                "ai_project_factory.core.subprocess.run",
                return_value=completed,
            ) as runner,
        ):
            result = run_command(["git", "status"], Path("C:/work"))

        self.assertTrue(result.ok)
        self.assertEqual(
            runner.call_args.kwargs["creationflags"],
            0x00000010,
        )

    @unittest.skipUnless(
        sys.platform == "win32",
        "startupinfo is a Windows-only subprocess concept",
    )
    def test_app_server_uses_hidden_shared_console(self) -> None:
        fake = FakeAppServerProcess([])
        with (
            mock.patch("ai_project_factory.core.os.name", "nt"),
            mock.patch.object(
                subprocess,
                "CREATE_NEW_CONSOLE",
                0x00000010,
                create=True,
            ),
            mock.patch(
                "ai_project_factory.core.minimal_codex_app_server_command",
                return_value=["codex", "app-server", "--listen", "stdio://"],
            ),
            mock.patch(
                "ai_project_factory.core.subprocess.Popen",
                return_value=fake,
            ) as launcher,
        ):
            client = CodexAppServerClient("codex", Path("C:/work"))
            client.close()

        self.assertEqual(
            launcher.call_args.kwargs["creationflags"],
            0x00000010,
        )
        self.assertIsNotNone(launcher.call_args.kwargs["startupinfo"])

    @unittest.skipUnless(
        sys.platform == "win32",
        "startupinfo is a Windows-only subprocess concept",
    )
    def test_nested_memory_git_helpers_hide_windows_console(self) -> None:
        from ai_project_factory.runtime import project_memory

        completed = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with (
            mock.patch.object(project_memory.os, "name", "nt"),
            mock.patch.object(
                project_memory.subprocess,
                "CREATE_NEW_CONSOLE",
                0x00000010,
                create=True,
            ),
            mock.patch(
                "ai_project_factory.runtime.project_memory.subprocess.run",
                return_value=completed,
            ) as runner,
        ):
            project_memory.run_git(Path("C:/work"), "status")

        self.assertEqual(
            runner.call_args.kwargs["creationflags"],
            0x00000010,
        )
        self.assertIsNotNone(runner.call_args.kwargs["startupinfo"])

    def test_app_server_creates_visible_bootstrap_turn_before_opening(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), "真实启动")
            fake = FakeAppServerProcess(
                [
                    {"id": 1, "result": {"userAgent": "test"}},
                    {
                        "id": 2,
                        "result": {
                            "thread": {
                                "id": "thr_factory_123",
                                "ephemeral": False,
                            }
                        },
                    },
                    {"id": 3, "result": {}},
                    {
                        "id": 4,
                        "result": {
                            "turn": {
                                "id": "turn_bootstrap_123",
                                "status": "inProgress",
                            }
                        },
                    },
                    {
                        "method": "turn/completed",
                        "params": {
                            "turn": {
                                "id": "turn_bootstrap_123",
                                "status": "completed",
                            }
                        },
                    },
                    {"id": 91, "result": {}},
                ]
            )

            def open_after_turn_starts(_deep_link: str) -> None:
                self.assertEqual(fake.wait_calls, 0)
                methods = [
                    json.loads(line).get("method")
                    for line in fake.stdin.getvalue().splitlines()
                    if line.strip()
                ]
                self.assertIn("turn/start", methods)

            with (
                mock.patch(
                    "ai_project_factory.core.find_codex_executable",
                    return_value="codex",
                ),
                mock.patch(
                    "ai_project_factory.core.minimal_codex_app_server_command",
                    return_value=["codex", "app-server", "--listen", "stdio://"],
                ),
                mock.patch(
                    "ai_project_factory.core.subprocess.Popen",
                    return_value=fake,
                ),
                mock.patch(
                    "ai_project_factory.core.open_codex_deep_link",
                    side_effect=open_after_turn_starts,
                ) as open_link,
            ):
                result = launch_codex_task(project)

            self.assertEqual(result.method, "app-server")
            self.assertEqual(result.thread_id, "thr_factory_123")
            self.assertEqual(result.turn_id, "turn_bootstrap_123")
            self.assertEqual(result.turn_status, "completed")
            self.assertGreater(fake.wait_calls, 0)
            open_link.assert_called_once_with(
                "codex://threads/thr_factory_123"
            )
            sent = [
                json.loads(line)
                for line in fake.stdin.getvalue().splitlines()
                if line.strip()
            ]
            self.assertEqual(
                [message.get("method") for message in sent],
                [
                    "initialize",
                    "initialized",
                    "thread/start",
                    "thread/name/set",
                    "turn/start",
                    "thread/unsubscribe",
                ],
            )
            start_params = sent[2]["params"]
            self.assertEqual(start_params["cwd"], str(project.resolve()))
            self.assertEqual(start_params["approvalPolicy"], "never")
            self.assertEqual(start_params["sandbox"], "workspace-write")
            turn_params = sent[4]["params"]
            bootstrap_text = turn_params["input"][0]["text"]
            self.assertIn(
                "AI_START_HERE.md",
                bootstrap_text,
            )
            self.assertIn(
                "AI Project Factory 启动确认",
                bootstrap_text,
            )
            self.assertIn(
                "<PROJECT_TASK>",
                bootstrap_text,
            )
            self.assertIn(
                "当前只输出 STARTUP_CARD",
                bootstrap_text,
            )
            self.assertEqual(turn_params["approvalPolicy"], "never")
            self.assertFalse(
                turn_params["sandboxPolicy"]["networkAccess"]
            )
            self.assertEqual(
                sent[3]["params"]["name"],
                "真实启动 · 启动讨论",
            )

    def test_quick_start_card_is_honest_and_requests_visible_continue(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), "快速项目")
            (project / "AI_PROJECT.json").write_text(
                json.dumps(
                    {"mode": "discussion", "goal_status": "none"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            card = build_codex_quick_start_card(project, "start")

        self.assertIn("不是项目研究结论", card)
        self.assertIn("discussion / none", card)
        self.assertIn("没有读取项目文件或调用工具", card)
        self.assertIn("回复“继续”", card)

    def test_bootstrap_turn_wraps_real_task_without_executing_it(
        self,
    ) -> None:
        wrapped = build_codex_bootstrap_turn_prompt(
            "REAL PROJECT INPUT",
            "VISIBLE STARTUP CARD",
        )

        self.assertIn("<PROJECT_TASK>\nREAL PROJECT INPUT", wrapped)
        self.assertIn("<STARTUP_CARD>\nVISIBLE STARTUP CARD", wrapped)
        self.assertIn("不要读取任何文件", wrapped)
        self.assertIn("当前只输出 STARTUP_CARD", wrapped)

    def test_minimal_app_server_never_spawns_mcp_listing_process(
        self,
    ) -> None:
        with mock.patch(
            "ai_project_factory.core.run_command"
        ) as runner:
            command = minimal_codex_app_server_command(
                "codex",
                Path("C:/work"),
            )

        runner.assert_not_called()
        self.assertIn("--disable", command)
        self.assertIn("plugins", command)
        self.assertIn("apps", command)
        self.assertIn("shell_tool", command)
        self.assertIn("code_mode_host", command)
        self.assertIn("in_app_browser", command)
        self.assertIn(
            "mcp_servers.node_repl.enabled=false",
            command,
        )
        self.assertIn(
            "mcp_servers.openaiDeveloperDocs.enabled=false",
            command,
        )

    def test_project_discovery_skips_non_factory_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.make_project(root, "Valid")
            (project / "AI_PROJECT.json").write_text(
                json.dumps(
                    {
                        "project_name": "Valid",
                        "profile": "software",
                        "mode": "goal",
                        "goal_status": "active",
                        "handoff_revision": 4,
                        "factory_version": "0.5.0",
                        "updated_at": "2026-08-01T10:00:00+08:00",
                    }
                ),
                encoding="utf-8",
            )
            (root / "Not a project").mkdir()

            found = discover_projects(root)

            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].project_name, "Valid")
            self.assertEqual(found[0].goal_status, "active")

    def test_failed_bootstrap_turn_deletes_persisted_empty_thread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), "Cleanup")
            fake = FakeAppServerProcess(
                [
                    {"id": 1, "result": {}},
                    {
                        "id": 2,
                        "result": {
                            "thread": {
                                "id": "thr_cleanup",
                                "ephemeral": False,
                            }
                        },
                    },
                    {"id": 3, "result": {}},
                    {
                        "id": 4,
                        "error": {
                            "code": -32000,
                            "message": "bootstrap turn failure",
                        },
                    },
                    {"id": 90, "result": {}},
                ]
            )
            with (
                mock.patch(
                    "ai_project_factory.core.find_codex_executable",
                    return_value="codex",
                ),
                mock.patch(
                    "ai_project_factory.core.minimal_codex_app_server_command",
                    return_value=["codex", "app-server", "--listen", "stdio://"],
                ),
                mock.patch(
                    "ai_project_factory.core.subprocess.Popen",
                    return_value=fake,
                ),
                self.assertRaisesRegex(FactoryError, "bootstrap turn failure"),
            ):
                launch_codex_task(project)

            sent = [
                json.loads(line)
                for line in fake.stdin.getvalue().splitlines()
                if line.strip()
            ]
            self.assertEqual(sent[-1]["method"], "thread/delete")
            self.assertEqual(
                sent[-1]["params"],
                {"threadId": "thr_cleanup"},
            )

    def test_prompt_rejects_non_factory_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(FactoryError, "有效的 Factory 项目"):
                build_agent_prompt(Path(temp))

    @mock.patch(
        "ai_project_factory.core.codex_protocol_registered",
        return_value=False,
    )
    def test_windows_launch_reports_missing_codex_protocol(
        self, _registered: mock.Mock
    ) -> None:
        with (
            mock.patch("ai_project_factory.core.os.name", "nt"),
            self.assertRaisesRegex(FactoryError, "codex://"),
        ):
            open_codex_deep_link("codex://new?path=C%3A%5CTest")


if __name__ == "__main__":
    unittest.main()
