from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_project_factory.core import (  # noqa: E402
    CreateProjectRequest,
    FactoryError,
    checkpoint_project,
    create_project,
    doctor_project,
    export_project,
    inspect_project,
    run_project_command,
    sync_agent_skill,
    sync_agent_skills,
)
import ai_project_factory.core as factory_core  # noqa: E402


def replace_text(path: Path, replacements: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for before, after in replacements.items():
        text = text.replace(before, after)
    path.write_text(text, encoding="utf-8", newline="\n")


def baseline_project(project: Path) -> None:
    replace_text(
        project / "PROJECT_CONTRACT.md",
        {
            "status: DRAFT": "status: BASELINED",
            "contract_revision: 0": "contract_revision: 1",
            "baselined_at: null": "baselined_at: 2026-07-30T00:00:00+08:00",
            "approved_by: null": "approved_by: CSR",
            "[TBD]": "Build and verify a portable demo",
        },
    )
    replace_text(
        project / "ACTIVE_GOAL.md",
        {
            "goal_id: none": "goal_id: G-DEMO-001",
            "status: NONE": "status: ACTIVE",
            "goal_revision: 0": "goal_revision: 1",
            "created_at: null": "created_at: 2026-07-30T00:00:00+08:00",
            "[NONE]": "Create a verified sample artifact",
        },
    )


class FactoryTests(unittest.TestCase):
    def make_project(
        self, parent: Path, name: str = "Demo Project", git: bool = True
    ) -> Path:
        return create_project(
            CreateProjectRequest(
                parent=parent,
                project_name=name,
                profile="general",
                initialize_git=git,
            )
        ).project_path

    def test_create_is_safe_and_starts_in_discussion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            project = self.make_project(parent)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual(state["mode"], "discussion")
            self.assertEqual(state["goal_status"], "none")
            self.assertTrue((project / "PROJECT_CONTRACT.md").is_file())
            self.assertTrue((project / "ACTIVE_GOAL.md").is_file())
            self.assertTrue((project / ".ai" / "project_runtime.py").is_file())
            self.assertTrue((project / ".gitignore").is_file())
            self.assertFalse(any(project.rglob("*.pyc")))
            self.assertFalse(any(project.rglob("__pycache__")))
            self.assertEqual(inspect_project(project).returncode, 0)

            sentinel = project / "user-file.txt"
            sentinel.write_text("keep", encoding="utf-8")
            with self.assertRaises(FactoryError):
                self.make_project(parent)
            self.assertEqual(sentinel.read_text("utf-8"), "keep")

    def test_project_name_is_normalized_and_cannot_inject_template_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(
                Path(temp),
                'Research {{TIMESTAMP}}\nProject "A" \\ portable',
                git=False,
            )
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual(
                state["project_name"],
                'Research {{TIMESTAMP}} Project "A" \\ portable',
            )
            context = (project / "PROJECT_CONTEXT.md").read_text("utf-8")
            self.assertIn("{{TIMESTAMP}}", context)

    def test_draft_contract_cannot_enter_goal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp))
            result = run_project_command(project, ["commit-discussion"])
            self.assertNotEqual(result.returncode, 0)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual(state["mode"], "discussion")
            self.assertEqual(state["goal_status"], "none")

    def test_contract_change_without_revision_cannot_be_bound_by_steering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            baseline_project(project)
            committed = run_project_command(project, ["commit-discussion"])
            self.assertEqual(committed.returncode, 0, committed.stderr)
            contract = project / "PROJECT_CONTRACT.md"
            contract.write_text(
                contract.read_text("utf-8")
                + "\nUnrevisioned material contract change.\n",
                encoding="utf-8",
            )

            steered = run_project_command(project, ["steer", "Continue anyway"])
            self.assertNotEqual(steered.returncode, 0)
            self.assertIn(
                "without incrementing its revision",
                steered.stdout + steered.stderr,
            )
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual((state["mode"], state["goal_status"]), ("goal", "active"))
            self.assertNotEqual(doctor_project(project).returncode, 0)

    def test_goal_steering_pause_resume_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp))
            baseline_project(project)

            committed = run_project_command(
                project, ["commit-discussion", "--updated-by", "test-agent"]
            )
            self.assertEqual(committed.returncode, 0, committed.stderr)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual((state["mode"], state["goal_status"]), ("goal", "active"))
            self.assertIn(
                "Create a verified sample artifact",
                (project / "HANDOFF.md").read_text("utf-8"),
            )

            replace_text(
                project / "PROJECT_CONTRACT.md",
                {
                    "contract_revision: 1": "contract_revision: 2",
                    "- Build and verify a portable demo": (
                        "- Build and verify a portable demo\n"
                        "- Also produce an HTML report"
                    ),
                },
            )
            steered = run_project_command(
                project,
                [
                    "steer",
                    "Also produce an HTML report",
                    "--updated-by",
                    "test-agent",
                ],
            )
            self.assertEqual(steered.returncode, 0, steered.stderr)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual((state["mode"], state["goal_status"]), ("goal", "active"))
            self.assertEqual(state["contract_revision"], 2)
            self.assertIn(
                "Also produce an HTML report",
                (project / "ACTIVE_GOAL.md").read_text("utf-8"),
            )

            paused = run_project_command(
                project,
                [
                    "pause",
                    "--reason",
                    "User explicitly requested discussion",
                    "--updated-by",
                    "test-agent",
                ],
            )
            self.assertEqual(paused.returncode, 0, paused.stderr)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual(
                (state["mode"], state["goal_status"]), ("discussion", "paused")
            )

            resumed = run_project_command(
                project, ["resume", "--updated-by", "test-agent"]
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual((state["mode"], state["goal_status"]), ("goal", "active"))

            completed = run_project_command(
                project,
                [
                    "complete",
                    "--reason",
                    "All acceptance checks passed",
                    "--updated-by",
                    "test-agent",
                ],
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual(
                (state["mode"], state["goal_status"]), ("discussion", "completed")
            )
            handoff = (project / "HANDOFF.md").read_text("utf-8")
            goal = (project / "ACTIVE_GOAL.md").read_text("utf-8")
            self.assertIn("The project is back in Discussion mode", handoff)
            self.assertNotIn("is executing", handoff)
            self.assertIn("No further execution under this Goal", goal)
            self.assertIn("All acceptance checks passed", goal)

    def test_checkpoint_detects_and_repairs_stale_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            (project / "artifact.txt").write_text("v1", encoding="utf-8")
            status = inspect_project(project)
            payload = json.loads(status.stdout)
            self.assertFalse(payload["handoff_fresh"])

            checkpoint = checkpoint_project(project, "test-agent")
            self.assertEqual(checkpoint.returncode, 0, checkpoint.stderr)
            payload = json.loads(inspect_project(project).stdout)
            self.assertTrue(payload["handoff_fresh"])

    def test_filesystem_fingerprint_binds_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = self.make_project(root, git=False)
            external_one = root / "external-one"
            external_two = root / "external-two"
            external_one.mkdir()
            external_two.mkdir()
            link = project / "linked-directory"
            try:
                os.symlink(external_one, link, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")

            memory_path = project / ".ai" / "project_memory.py"
            spec = importlib.util.spec_from_file_location(
                "_factory_symlink_fingerprint_test", memory_path
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            memory = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(memory)
            _, first = memory.compute_fingerprint(project)
            link.unlink()
            os.symlink(external_two, link, target_is_directory=True)
            _, second = memory.compute_fingerprint(project)
            self.assertNotEqual(first, second)

    def test_checkpoint_rolls_back_if_fingerprint_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            runtime_path = project / ".ai" / "project_runtime.py"
            spec = importlib.util.spec_from_file_location(
                "_factory_checkpoint_failure_test", runtime_path
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            runtime = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(runtime)
            before_state = (project / "AI_PROJECT.json").read_bytes()
            before_handoff = (project / "HANDOFF.md").read_bytes()
            with mock.patch.object(
                runtime,
                "current_fingerprints",
                side_effect=OSError("injected fingerprint failure"),
            ):
                with self.assertRaises(OSError):
                    runtime.checkpoint("test-agent")
            self.assertEqual((project / "AI_PROJECT.json").read_bytes(), before_state)
            self.assertEqual((project / "HANDOFF.md").read_bytes(), before_handoff)

    def test_git_commit_requires_checkpoint_before_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=True)
            subprocess.run(
                ["git", "add", "-A"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Factory Test",
                    "-c",
                    "user.email=factory-test@local.invalid",
                    "commit",
                    "-m",
                    "Initial project baseline",
                ],
                cwd=project,
                check=True,
                capture_output=True,
            )
            status = json.loads(inspect_project(project).stdout)
            self.assertFalse(status["handoff_fresh"])
            self.assertIn("base revision differs", status["handoff_stale_reasons"])
            self.assertNotEqual(export_project(project).returncode, 0)
            direct_export = subprocess.run(
                [
                    sys.executable,
                    str(project / ".ai" / "project_memory.py"),
                    "export",
                    str(project),
                ],
                cwd=project,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(direct_export.returncode, 0)

            refreshed = checkpoint_project(project, "test-agent")
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            exported = export_project(project)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            Path(exported.stdout.strip().splitlines()[-1]).unlink(missing_ok=True)

    def test_git_fingerprint_binds_index_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=True)
            script = project / "run.sh"
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-A"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Factory Test",
                    "-c",
                    "user.email=factory-test@local.invalid",
                    "commit",
                    "-m",
                    "Executable mode fixture",
                ],
                cwd=project,
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                checkpoint_project(project, "test-agent").returncode, 0
            )
            self.assertTrue(json.loads(inspect_project(project).stdout)["handoff_fresh"])
            subprocess.run(
                ["git", "update-index", "--chmod=+x", "run.sh"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            self.assertFalse(
                json.loads(inspect_project(project).stdout)["handoff_fresh"]
            )

    def test_git_fingerprint_binds_every_unmerged_index_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=True)
            conflict = project / "conflict.txt"
            conflict.write_text("working tree\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-A"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Factory Test",
                    "-c",
                    "user.email=factory-test@local.invalid",
                    "commit",
                    "-m",
                    "Conflict-stage fixture",
                ],
                cwd=project,
                check=True,
                capture_output=True,
            )

            def write_blob(content: str) -> str:
                result = subprocess.run(
                    ["git", "hash-object", "-w", "--stdin"],
                    cwd=project,
                    input=content,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                return result.stdout.strip()

            base = write_blob("base\n")
            ours = write_blob("ours\n")
            theirs = write_blob("theirs\n")
            changed_ours = write_blob("changed ours\n")

            def set_conflict_stage2(stage2: str) -> None:
                subprocess.run(
                    ["git", "update-index", "--force-remove", "conflict.txt"],
                    cwd=project,
                    check=True,
                    capture_output=True,
                )
                index_info = (
                    f"100644 {base} 1\tconflict.txt\n"
                    f"100644 {stage2} 2\tconflict.txt\n"
                    f"100644 {theirs} 3\tconflict.txt\n"
                )
                subprocess.run(
                    ["git", "update-index", "--index-info"],
                    cwd=project,
                    input=index_info.encode("ascii"),
                    check=True,
                    capture_output=True,
                )

            set_conflict_stage2(ours)
            staged = subprocess.run(
                ["git", "ls-files", "--stage", "--", "conflict.txt"],
                cwd=project,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(len(staged), 3)
            self.assertEqual(
                [line.split("\t", 1)[0].rsplit(" ", 1)[-1] for line in staged],
                ["1", "2", "3"],
            )
            self.assertEqual(
                checkpoint_project(project, "test-agent").returncode, 0
            )
            self.assertTrue(json.loads(inspect_project(project).stdout)["handoff_fresh"])
            set_conflict_stage2(changed_ours)
            self.assertFalse(
                json.loads(inspect_project(project).stdout)["handoff_fresh"]
            )

    def test_export_contains_contract_goal_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            result = export_project(project)
            self.assertEqual(result.returncode, 0, result.stderr)
            bundle = Path(result.stdout.strip().splitlines()[-1])
            try:
                text = bundle.read_text(encoding="utf-8")
                self.assertIn("BEGIN FILE: PROJECT_CONTRACT.md", text)
                self.assertIn("BEGIN FILE: ACTIVE_GOAL.md", text)
                self.assertIn("BEGIN FILE: AI_PROJECT.json", text)
                self.assertFalse(
                    (project / ".ai" / "__pycache__").exists(),
                    "Export must not mutate the project by writing bytecode.",
                )
            finally:
                bundle.unlink(missing_ok=True)

    def test_secret_blocks_export_after_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            contract = project / "PROJECT_CONTRACT.md"
            fake_key = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
            contract.write_text(
                contract.read_text("utf-8")
                + f"\nexample_token = {fake_key}\n",
                encoding="utf-8",
            )
            self.assertEqual(checkpoint_project(project, "test-agent").returncode, 0)
            result = export_project(project)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("credential", (result.stdout + result.stderr).lower())

    def test_invalid_contract_cannot_resume_paused_goal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            baseline_project(project)
            self.assertEqual(
                run_project_command(project, ["commit-discussion"]).returncode, 0
            )
            self.assertEqual(
                run_project_command(
                    project,
                    ["pause", "--reason", "User requested discussion"],
                ).returncode,
                0,
            )
            replace_text(
                project / "PROJECT_CONTRACT.md",
                {
                    "contract_revision: 1": "contract_revision: 0",
                    "approved_by: CSR": "approved_by: null",
                },
            )
            resumed = run_project_command(project, ["resume"])
            self.assertNotEqual(resumed.returncode, 0)
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual(
                (state["mode"], state["goal_status"]),
                ("discussion", "paused"),
            )

    def test_pause_cannot_bind_unrevisioned_active_goal_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            baseline_project(project)
            self.assertEqual(
                run_project_command(project, ["commit-discussion"]).returncode, 0
            )
            goal = project / "ACTIVE_GOAL.md"
            goal.write_text(
                goal.read_text("utf-8")
                + "\nUnrevisioned acceptance change.\n",
                encoding="utf-8",
            )
            paused = run_project_command(
                project,
                ["pause", "--reason", "User requested discussion"],
            )
            self.assertNotEqual(paused.returncode, 0)
            self.assertIn(
                "without incrementing its revision",
                paused.stdout + paused.stderr,
            )
            state = json.loads((project / "AI_PROJECT.json").read_text("utf-8"))
            self.assertEqual((state["mode"], state["goal_status"]), ("goal", "active"))

    def test_interrupted_transition_recovers_from_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            baseline_project(project)
            self.assertEqual(
                run_project_command(project, ["commit-discussion"]).returncode, 0
            )
            runtime_path = project / ".ai" / "project_runtime.py"
            crash_script = f"""
import importlib.util
import os
from pathlib import Path
p = Path({str(runtime_path)!r})
spec = importlib.util.spec_from_file_location("_factory_crash_test", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.sync_handoff_for_exit = lambda *args, **kwargs: os._exit(99)
module.leave_goal("paused", "user_paused", "crash test", "test-agent")
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_script],
                cwd=project,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 99)
            self.assertTrue(
                (project / ".ai" / "lifecycle_transaction.json").is_file()
            )
            self.assertIn(
                "status: PAUSED",
                (project / "ACTIVE_GOAL.md").read_text("utf-8"),
            )

            recovered = inspect_project(project)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            state = json.loads(recovered.stdout)
            self.assertEqual((state["mode"], state["goal_status"]), ("goal", "active"))
            self.assertTrue(state["handoff_fresh"])
            self.assertIn(
                "status: ACTIVE",
                (project / "ACTIVE_GOAL.md").read_text("utf-8"),
            )
            self.assertFalse(
                (project / ".ai" / "lifecycle_transaction.json").exists()
            )

    def test_recovery_refuses_to_overwrite_post_crash_manual_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            baseline_project(project)
            self.assertEqual(
                run_project_command(project, ["commit-discussion"]).returncode, 0
            )
            runtime_path = project / ".ai" / "project_runtime.py"
            crash_script = f"""
import importlib.util
import os
from pathlib import Path
p = Path({str(runtime_path)!r})
spec = importlib.util.spec_from_file_location("_factory_conflict_test", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.sync_handoff_for_exit = lambda *args, **kwargs: os._exit(99)
module.leave_goal("paused", "user_paused", "crash test", "test-agent")
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_script],
                cwd=project,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 99)
            goal = project / "ACTIVE_GOAL.md"
            goal.write_text(
                goal.read_text("utf-8") + "\nMANUAL_EDIT_AFTER_CRASH\n",
                encoding="utf-8",
            )
            status = inspect_project(project)
            self.assertNotEqual(status.returncode, 0)
            self.assertIn(
                "edited after the interrupted transaction",
                status.stdout + status.stderr,
            )
            self.assertIn("MANUAL_EDIT_AFTER_CRASH", goal.read_text("utf-8"))
            self.assertTrue(
                (project / ".ai" / "lifecycle_transaction.json").is_file()
            )

    def test_write_ahead_journal_recovers_crash_before_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            runtime_path = project / ".ai" / "project_runtime.py"
            crash_script = f"""
import importlib.util
import os
from pathlib import Path
p = Path({str(runtime_path)!r})
spec = importlib.util.spec_from_file_location("_factory_wal_test", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.record_transaction_planned_write
def crash_after_wal(path, text):
    original(path, text)
    os._exit(98)
module.record_transaction_planned_write = crash_after_wal
module.checkpoint("test-agent")
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_script],
                cwd=project,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 98)
            journal_path = project / ".ai" / "lifecycle_transaction.json"
            journal = json.loads(journal_path.read_text("utf-8"))
            self.assertTrue(journal["written_fingerprints"]["AI_PROJECT.json"])
            recovered = inspect_project(project)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertTrue(json.loads(recovered.stdout)["handoff_fresh"])
            self.assertFalse(journal_path.exists())

    def test_atomic_write_crash_temp_is_ignored_and_cleaned(self) -> None:
        for crash_target in ("journal", "state"):
            with self.subTest(crash_target=crash_target):
                with tempfile.TemporaryDirectory() as temp:
                    project = self.make_project(Path(temp), git=False)
                    runtime_path = project / ".ai" / "project_runtime.py"
                    crash_script = f"""
import importlib.util
import os
from pathlib import Path
p = Path({str(runtime_path)!r})
spec = importlib.util.spec_from_file_location("_factory_temp_crash_test", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_replace = module.os.replace
target = module.{"JOURNAL_PATH" if crash_target == "journal" else "STATE_PATH"}
def crash_before_replace(source, destination):
    if Path(destination) == target:
        os._exit(95)
    return original_replace(source, destination)
module.os.replace = crash_before_replace
module.checkpoint("test-agent")
"""
                    crashed = subprocess.run(
                        [sys.executable, "-B", "-c", crash_script],
                        cwd=project,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(crashed.returncode, 95)
                    temp_dir = project / ".ai" / "runtime-tmp"
                    self.assertTrue(any(temp_dir.glob(".*.tmp")))

                    recovered = inspect_project(project)
                    self.assertEqual(recovered.returncode, 0, recovered.stderr)
                    self.assertTrue(
                        json.loads(recovered.stdout)["handoff_fresh"]
                    )
                    self.assertFalse(any(temp_dir.glob(".*.tmp")))
                    exported = export_project(project)
                    self.assertEqual(exported.returncode, 0, exported.stderr)
                    Path(exported.stdout.strip().splitlines()[-1]).unlink(
                        missing_ok=True
                    )

    def test_project_lock_serializes_read_during_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            runtime_path = project / ".ai" / "project_runtime.py"
            lock_script = f"""
import importlib.util
import time
from pathlib import Path
p = Path({str(runtime_path)!r})
spec = importlib.util.spec_from_file_location("_factory_lock_test", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.project_lock():
    module.begin_lifecycle_transaction(
        "lock-test", (module.STATE_PATH, module.HANDOFF_PATH)
    )
    print("LOCKED", flush=True)
    time.sleep(1.0)
    module.abort_lifecycle_transaction()
"""
            holder = subprocess.Popen(
                [sys.executable, "-B", "-c", lock_script],
                cwd=project,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(holder.stdout.readline().strip(), "LOCKED")
            started = time.monotonic()
            status = inspect_project(project)
            elapsed = time.monotonic() - started
            stdout, stderr = holder.communicate(timeout=10)
            self.assertEqual(holder.returncode, 0, stdout + stderr)
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertGreaterEqual(elapsed, 0.7)

    def test_direct_memory_export_refuses_interrupted_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            baseline_project(project)
            self.assertEqual(
                run_project_command(project, ["commit-discussion"]).returncode, 0
            )
            runtime_path = project / ".ai" / "project_runtime.py"
            transaction_script = f"""
import importlib.util
import os
import time
from pathlib import Path
p = Path({str(runtime_path)!r})
spec = importlib.util.spec_from_file_location("_factory_export_lock_test", p)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with module.project_lock():
    module.begin_lifecycle_transaction(
        "export-lock-test",
        (module.STATE_PATH, module.GOAL_PATH, module.HANDOFF_PATH),
    )
    module._leave_goal_impl(
        "paused", "user_paused", "export lock test", "test-agent"
    )
    print("READY", flush=True)
    time.sleep(0.8)
    os._exit(99)
"""
            holder = subprocess.Popen(
                [sys.executable, "-B", "-c", transaction_script],
                cwd=project,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(holder.stdout.readline().strip(), "READY")
            checked = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(project / ".ai" / "project_memory.py"),
                    "check",
                    str(project),
                ],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn(
                "lifecycle transaction is incomplete",
                (checked.stdout + checked.stderr).lower(),
            )
            output = Path(temp) / "should-not-exist.md"
            exported = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(project / ".ai" / "project_memory.py"),
                    "export",
                    str(project),
                    "--output",
                    str(output),
                ],
                cwd=project,
                capture_output=True,
                text=True,
                timeout=15,
            )
            remaining_stdout, remaining_stderr = holder.communicate(timeout=10)
            self.assertEqual(holder.returncode, 99)
            self.assertEqual(remaining_stdout + remaining_stderr, "")
            self.assertNotEqual(exported.returncode, 0)
            self.assertIn(
                "transaction is incomplete",
                exported.stdout + exported.stderr,
            )
            self.assertFalse(output.exists())
            recovered = inspect_project(project)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            state = json.loads(recovered.stdout)
            self.assertEqual((state["mode"], state["goal_status"]), ("goal", "active"))

    def test_direct_memory_export_rejects_invalid_factory_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            state_path = project / "AI_PROJECT.json"
            state = json.loads(state_path.read_text("utf-8"))
            state["mode"] = "goal"
            state["goal_status"] = "completed"
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(
                checkpoint_project(project, "test-agent").returncode, 0
            )
            self.assertNotEqual(doctor_project(project).returncode, 0)
            output = Path(temp) / "invalid-lifecycle.md"
            exported = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(project / ".ai" / "project_memory.py"),
                    "export",
                    str(project),
                    "--output",
                    str(output),
                ],
                cwd=project,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(exported.returncode, 0)
            self.assertIn(
                "Factory lifecycle is invalid",
                exported.stdout + exported.stderr,
            )
            self.assertFalse(output.exists())

    def test_doctor_rejects_torn_handoff_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            state_path = project / "AI_PROJECT.json"
            state = json.loads(state_path.read_text("utf-8"))
            state["handoff_revision"] += 1
            state_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            result = doctor_project(project)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "revision does not match",
                result.stdout + result.stderr,
            )

    def test_deep_doctor_passes_for_fresh_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = self.make_project(Path(temp), git=False)
            result = doctor_project(project, deep=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_shared_skill_sync_is_managed_and_refreshable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = sync_agent_skill(root, ROOT)
            bridge = destination / "scripts" / "factory_bridge.py"
            self.assertTrue((destination / ".factory-managed.json").is_file())
            self.assertNotIn("{{FACTORY_ROOT_JSON}}", bridge.read_text("utf-8"))
            self.assertNotIn("{{FACTORY_PYTHON_JSON}}", bridge.read_text("utf-8"))
            self.assertEqual(sync_agent_skill(root, ROOT), destination)

            unmanaged_root = root / "unmanaged"
            unmanaged = unmanaged_root / "ai-project-factory"
            unmanaged.mkdir(parents=True)
            (unmanaged / "keep.txt").write_text("user", encoding="utf-8")
            with self.assertRaises(FactoryError):
                sync_agent_skill(unmanaged_root, ROOT)
            self.assertEqual((unmanaged / "keep.txt").read_text("utf-8"), "user")

            forged_root = root / "forged"
            forged = sync_agent_skill(forged_root, ROOT)
            protected = forged / "user-only.txt"
            protected.write_text("keep", encoding="utf-8")
            (forged / ".factory-managed.json").write_text(
                "{}\n", encoding="utf-8"
            )
            with self.assertRaises(FactoryError):
                sync_agent_skill(forged_root, ROOT)
            self.assertEqual(protected.read_text("utf-8"), "keep")

    def test_tree_hash_has_unambiguous_entry_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "a").write_bytes(b"x")
            (first / "b").write_bytes(b"y")
            (second / "a").write_bytes(
                b"x\0F\0b\0" + b"0\0y"
            )
            self.assertNotEqual(
                factory_core.tree_hash(first),
                factory_core.tree_hash(second),
            )

    def test_multi_skill_sync_preflights_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex_root = root / "codex"
            claude_root = root / "claude"
            codex = sync_agent_skill(codex_root, ROOT)
            protected = codex / "protected.txt"
            protected.write_text("keep", encoding="utf-8")

            unmanaged = claude_root / "ai-project-factory"
            unmanaged.mkdir(parents=True)
            (unmanaged / "user.txt").write_text("user", encoding="utf-8")
            with self.assertRaises(FactoryError):
                sync_agent_skills((codex_root, claude_root), ROOT)
            self.assertEqual(protected.read_text("utf-8"), "keep")

            unmanaged.rename(root / "unmanaged-backup")
            original_sync = sync_agent_skill

            def fail_second(
                destination: Path,
                factory_root: Path | None = None,
                **kwargs,
            ):
                if destination.resolve() == claude_root.resolve():
                    raise FactoryError("injected second-target failure")
                return original_sync(destination, factory_root, **kwargs)

            with mock.patch(
                "ai_project_factory.core.sync_agent_skill",
                side_effect=fail_second,
            ):
                with self.assertRaises(FactoryError):
                    sync_agent_skills((codex_root, claude_root), ROOT)
            self.assertEqual(protected.read_text("utf-8"), "keep")
            self.assertFalse((claude_root / "ai-project-factory").exists())

    def test_multi_skill_sync_recovers_hard_exit_between_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_root = root / "a-first"
            second_root = root / "b-second"
            first = sync_agent_skill(first_root, ROOT)
            second = sync_agent_skill(second_root, ROOT)
            (first / "protected.txt").write_text("first-original", encoding="utf-8")
            (second / "protected.txt").write_text("second-original", encoding="utf-8")

            crash_script = f"""
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "src")!r})
import ai_project_factory.core as core
original = core.sync_agent_skill
calls = 0
def crash_after_first(*args, **kwargs):
    global calls
    result = original(*args, **kwargs)
    calls += 1
    if calls == 1:
        os._exit(94)
    return result
core.sync_agent_skill = crash_after_first
core.sync_agent_skills(
    (Path({str(first_root)!r}), Path({str(second_root)!r})),
    Path({str(ROOT)!r}),
)
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_script],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 94)
            self.assertFalse((first / "protected.txt").exists())
            self.assertTrue((second / "protected.txt").exists())
            self.assertTrue((first_root / factory_core.MULTI_SYNC_JOURNAL).exists())
            self.assertTrue((second_root / factory_core.MULTI_SYNC_JOURNAL).exists())

            ordered = tuple(
                sorted(
                    (first_root.resolve(), second_root.resolve()),
                    key=lambda item: str(item).casefold(),
                )
            )
            with factory_core.skill_sync_locks(ordered):
                self.assertTrue(factory_core.recover_multi_skill_sync(ordered))
            self.assertEqual(
                (first / "protected.txt").read_text("utf-8"),
                "first-original",
            )
            self.assertEqual(
                (second / "protected.txt").read_text("utf-8"),
                "second-original",
            )

            destinations = sync_agent_skills(
                (first_root, second_root),
                ROOT,
            )
            self.assertEqual(
                factory_core.tree_hash(destinations[0]),
                factory_core.tree_hash(destinations[1]),
            )
            for skill_root in (first_root, second_root):
                self.assertFalse(
                    (skill_root / factory_core.MULTI_SYNC_JOURNAL).exists()
                )
                residues = [
                    path
                    for path in skill_root.iterdir()
                    if path.name.startswith(
                        (
                            ".ai-project-factory-transaction-",
                            ".ai-project-factory-staging-",
                            ".ai-project-factory-backup-",
                            ".ai-project-factory-recovery-",
                        )
                    )
                ]
                self.assertEqual(residues, [])

    def test_multi_skill_sync_preserves_manual_edit_after_hard_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_root = root / "a-first"
            second_root = root / "b-second"
            sync_agent_skill(first_root, ROOT)
            sync_agent_skill(second_root, ROOT)
            crash_script = f"""
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "src")!r})
import ai_project_factory.core as core
original = core.sync_agent_skill
calls = 0
def crash_after_first(*args, **kwargs):
    global calls
    result = original(*args, **kwargs)
    calls += 1
    if calls == 1:
        os._exit(92)
    return result
core.sync_agent_skill = crash_after_first
core.sync_agent_skills(
    (Path({str(first_root)!r}), Path({str(second_root)!r})),
    Path({str(ROOT)!r}),
)
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_script],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 92)
            manual = first_root / "ai-project-factory" / "manual-after-crash.txt"
            manual.write_text("preserve me", encoding="utf-8")
            with self.assertRaisesRegex(
                FactoryError,
                "事务之外的修改|人工修改",
            ):
                sync_agent_skills((first_root, second_root), ROOT)
            self.assertEqual(manual.read_text("utf-8"), "preserve me")
            self.assertTrue((first_root / factory_core.MULTI_SYNC_JOURNAL).exists())
            self.assertTrue((second_root / factory_core.MULTI_SYNC_JOURNAL).exists())

    def test_multi_skill_sync_finishes_committed_crash_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_root = root / "a-first"
            second_root = root / "b-second"
            crash_script = f"""
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "src")!r})
import ai_project_factory.core as core
original = core.write_multi_sync_journal
def crash_after_committed(roots, journal, phase):
    original(roots, journal, phase)
    if phase == "committed":
        os._exit(91)
core.write_multi_sync_journal = crash_after_committed
core.sync_agent_skills(
    (Path({str(first_root)!r}), Path({str(second_root)!r})),
    Path({str(ROOT)!r}),
)
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_script],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 91)
            self.assertTrue(
                (first_root / "ai-project-factory").is_dir()
            )
            self.assertTrue(
                (second_root / "ai-project-factory").is_dir()
            )
            self.assertTrue((first_root / factory_core.MULTI_SYNC_JOURNAL).exists())
            self.assertTrue((second_root / factory_core.MULTI_SYNC_JOURNAL).exists())

            destinations = sync_agent_skills(
                (first_root, second_root),
                ROOT,
            )
            self.assertEqual(
                factory_core.tree_hash(destinations[0]),
                factory_core.tree_hash(destinations[1]),
            )
            for skill_root in (first_root, second_root):
                self.assertFalse(
                    (skill_root / factory_core.MULTI_SYNC_JOURNAL).exists()
                )
                self.assertFalse(
                    any(
                        path.name.startswith(
                            (
                                ".ai-project-factory-transaction-",
                                ".ai-project-factory-staging-",
                                ".ai-project-factory-backup-",
                                ".ai-project-factory-recovery-",
                            )
                        )
                        for path in skill_root.iterdir()
                    )
                )

    def test_multi_skill_sync_rejects_malformed_journal_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "skills").resolve()
            root.mkdir(parents=True)
            transaction_id = "0123456789"
            journal = {
                "schema_version": factory_core.MULTI_SYNC_SCHEMA,
                "transaction_id": transaction_id,
                "revision": "not-an-integer",
                "phase": "preparing",
                "expected_hash": "0" * 64,
                "targets": [
                    {
                        "root": str(root),
                        "destination": str(root / "ai-project-factory"),
                        "snapshot": str(
                            root
                            / f".ai-project-factory-transaction-{transaction_id}"
                        ),
                        "staging": str(
                            root
                            / f".ai-project-factory-staging-{transaction_id}"
                        ),
                        "backup": str(
                            root
                            / f".ai-project-factory-backup-{transaction_id}"
                        ),
                        "recovery": str(
                            root
                            / f".ai-project-factory-recovery-{transaction_id}"
                        ),
                        "original_present": False,
                        "original_hash": None,
                    }
                ],
            }
            (root / factory_core.MULTI_SYNC_JOURNAL).write_text(
                json.dumps(journal),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(FactoryError, "revision"):
                factory_core.recover_multi_skill_sync((root,))

    def test_multi_skill_sync_recovers_skewed_phase_after_second_crash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_root = root / "a-first"
            second_root = root / "b-second"
            crash_commit = f"""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "src")!r})
import ai_project_factory.core as core
original = core.write_multi_sync_journal
def stop_after_first_committed(roots, journal, phase):
    if phase != "committed":
        return original(roots, journal, phase)
    journal["revision"] = int(journal.get("revision", 0)) + 1
    journal["phase"] = phase
    journal["updated_at"] = core.timestamp()
    text = json.dumps(journal, ensure_ascii=False, indent=2) + "\\n"
    core.atomic_write_text(roots[0] / core.MULTI_SYNC_JOURNAL, text)
    os._exit(90)
core.write_multi_sync_journal = stop_after_first_committed
core.sync_agent_skills(
    (Path({str(first_root)!r}), Path({str(second_root)!r})),
    Path({str(ROOT)!r}),
)
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_commit],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 90)
            first_journal = json.loads(
                (first_root / factory_core.MULTI_SYNC_JOURNAL).read_text("utf-8")
            )
            second_journal = json.loads(
                (second_root / factory_core.MULTI_SYNC_JOURNAL).read_text("utf-8")
            )
            self.assertEqual(first_journal["phase"], "committed")
            self.assertEqual(second_journal["phase"], "prepared")

            crash_cleanup = f"""
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "src")!r})
import ai_project_factory.core as core
roots = tuple(sorted(
    (Path({str(first_root)!r}).resolve(), Path({str(second_root)!r}).resolve()),
    key=core.skill_root_sort_key,
))
def stop_after_first_journal(roots):
    (roots[0] / core.MULTI_SYNC_JOURNAL).unlink(missing_ok=True)
    os._exit(89)
core.remove_multi_sync_journals = stop_after_first_journal
with core.skill_sync_locks(roots):
    core.recover_multi_skill_sync(roots)
"""
            crashed_again = subprocess.run(
                [sys.executable, "-B", "-c", crash_cleanup],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed_again.returncode, 89)
            for skill_root in (first_root, second_root):
                self.assertTrue((skill_root / "ai-project-factory").is_dir())

            ordered = tuple(
                sorted(
                    (first_root.resolve(), second_root.resolve()),
                    key=factory_core.skill_root_sort_key,
                )
            )
            with factory_core.skill_sync_locks(ordered):
                self.assertTrue(factory_core.recover_multi_skill_sync(ordered))
            for skill_root in (first_root, second_root):
                self.assertTrue((skill_root / "ai-project-factory").is_dir())
                self.assertFalse(
                    (skill_root / factory_core.MULTI_SYNC_JOURNAL).exists()
                )

    def test_multi_skill_sync_retries_half_deleted_terminal_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_root = root / "a-first"
            second_root = root / "b-second"
            sync_agent_skill(first_root, ROOT)
            sync_agent_skill(second_root, ROOT)
            crash_script = f"""
import os
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT / "src")!r})
import ai_project_factory.core as core
original = core.remove_owned_tree_without_following_links
def stop_during_snapshot_cleanup(path):
    if path.name.startswith(".ai-project-factory-transaction-"):
        marker = path / ".factory-managed.json"
        marker.unlink(missing_ok=True)
        os._exit(88)
    return original(path)
core.remove_owned_tree_without_following_links = stop_during_snapshot_cleanup
core.sync_agent_skills(
    (Path({str(first_root)!r}), Path({str(second_root)!r})),
    Path({str(ROOT)!r}),
)
"""
            crashed = subprocess.run(
                [sys.executable, "-B", "-c", crash_script],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(crashed.returncode, 88)
            ordered = tuple(
                sorted(
                    (first_root.resolve(), second_root.resolve()),
                    key=factory_core.skill_root_sort_key,
                )
            )
            with factory_core.skill_sync_locks(ordered):
                self.assertTrue(factory_core.recover_multi_skill_sync(ordered))
            for skill_root in (first_root, second_root):
                self.assertTrue((skill_root / "ai-project-factory").is_dir())
                self.assertFalse(
                    (skill_root / factory_core.MULTI_SYNC_JOURNAL).exists()
                )

    def test_multi_skill_sync_cleanup_does_not_follow_snapshot_symlink(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "skills").resolve()
            root.mkdir(parents=True)
            valuable = root / "valuable-skill"
            valuable.mkdir()
            protected = valuable / "keep.txt"
            protected.write_text("keep", encoding="utf-8")
            transaction_id = "abcdef0123"
            snapshot = root / (
                f".ai-project-factory-transaction-{transaction_id}"
            )
            try:
                snapshot.symlink_to(valuable, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            journal = {
                "schema_version": factory_core.MULTI_SYNC_SCHEMA,
                "transaction_id": transaction_id,
                "revision": 1,
                "phase": "preparing",
                "expected_hash": "0" * 64,
                "targets": [
                    {
                        "root": str(root),
                        "destination": str(root / "ai-project-factory"),
                        "snapshot": str(snapshot),
                        "staging": str(
                            root
                            / f".ai-project-factory-staging-{transaction_id}"
                        ),
                        "backup": str(
                            root
                            / f".ai-project-factory-backup-{transaction_id}"
                        ),
                        "recovery": str(
                            root
                            / f".ai-project-factory-recovery-{transaction_id}"
                        ),
                        "original_present": False,
                        "original_hash": None,
                    }
                ],
            }
            (root / factory_core.MULTI_SYNC_JOURNAL).write_text(
                json.dumps(journal),
                encoding="utf-8",
            )

            with factory_core.skill_sync_locks((root,)):
                self.assertTrue(
                    factory_core.recover_multi_skill_sync((root,))
                )
            self.assertEqual(protected.read_text("utf-8"), "keep")
            self.assertFalse(snapshot.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_multi_skill_sync_cleanup_does_not_follow_snapshot_junction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = (Path(temp) / "skills").resolve()
            root.mkdir(parents=True)
            valuable = root / "valuable-skill"
            valuable.mkdir()
            protected = valuable / "keep.txt"
            protected.write_text("keep", encoding="utf-8")
            transaction_id = "fedcba9876"
            snapshot = root / (
                f".ai-project-factory-transaction-{transaction_id}"
            )
            linked = subprocess.run(
                [
                    "cmd",
                    "/c",
                    "mklink",
                    "/J",
                    str(snapshot),
                    str(valuable),
                ],
                capture_output=True,
                text=True,
            )
            if linked.returncode != 0:
                self.skipTest(
                    "directory junction unavailable: "
                    + linked.stdout
                    + linked.stderr
                )
            journal = {
                "schema_version": factory_core.MULTI_SYNC_SCHEMA,
                "transaction_id": transaction_id,
                "revision": 1,
                "phase": "preparing",
                "expected_hash": "0" * 64,
                "targets": [
                    {
                        "root": str(root),
                        "destination": str(root / "ai-project-factory"),
                        "snapshot": str(snapshot),
                        "staging": str(
                            root
                            / f".ai-project-factory-staging-{transaction_id}"
                        ),
                        "backup": str(
                            root
                            / f".ai-project-factory-backup-{transaction_id}"
                        ),
                        "recovery": str(
                            root
                            / f".ai-project-factory-recovery-{transaction_id}"
                        ),
                        "original_present": False,
                        "original_hash": None,
                    }
                ],
            }
            (root / factory_core.MULTI_SYNC_JOURNAL).write_text(
                json.dumps(journal),
                encoding="utf-8",
            )

            with factory_core.skill_sync_locks((root,)):
                self.assertTrue(
                    factory_core.recover_multi_skill_sync((root,))
                )
            self.assertEqual(protected.read_text("utf-8"), "keep")
            self.assertFalse(snapshot.exists())

    def test_multi_skill_sync_rejects_nested_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            nested = root / "nested"
            with self.assertRaisesRegex(FactoryError, "不能互相嵌套"):
                sync_agent_skills((root, nested), ROOT)

    def test_skill_sync_rejects_source_containment(self) -> None:
        source = (
            ROOT
            / "src"
            / "ai_project_factory"
            / "resources"
            / "agent-skills"
            / "ai-project-factory"
        )
        nested = source / "unsafe-destination"
        with self.assertRaises(FactoryError):
            sync_agent_skill(nested, ROOT)
        self.assertFalse(nested.exists())

    def test_skill_sync_recovers_interrupted_swap_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = sync_agent_skill(root, ROOT)
            backup = root / ".ai-project-factory-backup-crash"
            destination.rename(backup)
            self.assertFalse(destination.exists())
            restored = sync_agent_skill(root, ROOT)
            self.assertEqual(restored, destination)
            self.assertTrue((restored / ".factory-managed.json").is_file())
            self.assertFalse(backup.exists())


if __name__ == "__main__":
    unittest.main()
