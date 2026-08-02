from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import venv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_portable_release.py"
SPEC = importlib.util.spec_from_file_location("_factory_release_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_builder)

WHEEL_SCRIPT = ROOT / "scripts" / "build_wheel_release.py"
WHEEL_SPEC = importlib.util.spec_from_file_location(
    "_factory_wheel_builder",
    WHEEL_SCRIPT,
)
assert WHEEL_SPEC is not None and WHEEL_SPEC.loader is not None
wheel_builder = importlib.util.module_from_spec(WHEEL_SPEC)
WHEEL_SPEC.loader.exec_module(wheel_builder)


HAS_SETUPTOOLS = importlib.util.find_spec("setuptools") is not None


class PortableReleaseTests(unittest.TestCase):
    def test_package_and_factory_versions_are_synchronized(self) -> None:
        package_version = release_builder.project_version()
        core_text = (
            ROOT / "src" / "ai_project_factory" / "core.py"
        ).read_text(encoding="utf-8")
        factory_version = core_text.split('FACTORY_VERSION = "', 1)[1].split(
            '"',
            1,
        )[0]
        self.assertEqual(package_version, factory_version)

    def test_release_is_deterministic_and_manifested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first_dir = Path(temp) / "first"
            second_dir = Path(temp) / "second"
            first, first_checksum = release_builder.build_release(first_dir)
            second, second_checksum = release_builder.build_release(second_dir)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(
                first_checksum.read_text("utf-8").split()[0],
                second_checksum.read_text("utf-8").split()[0],
            )

            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertFalse(
                    any(
                        "__pycache__" in name
                        or name.endswith((".pyc", ".pyo"))
                        for name in names
                    )
                )
                manifest_name = (
                    f"{release_builder.ARCHIVE_ROOT}/RELEASE_MANIFEST.json"
                )
                manifest = json.loads(archive.read(manifest_name))
                listed = {item["path"] for item in manifest["files"]}
                actual = {
                    name.removeprefix(release_builder.ARCHIVE_ROOT + "/")
                    for name in names
                    if name != manifest_name
                }
                self.assertEqual(listed, actual)
                for item in manifest["files"]:
                    relative = item["path"]
                    content = archive.read(
                        f"{release_builder.ARCHIVE_ROOT}/{relative}"
                    )
                    self.assertEqual(len(content), item["size"], relative)
                    self.assertEqual(
                        hashlib.sha256(content).hexdigest(),
                        item["sha256"],
                        relative,
                    )

            first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
            sidecar_parts = first_checksum.read_text(encoding="utf-8").split()
            self.assertEqual(sidecar_parts, [first_digest, first.name])

    def test_extracted_release_cold_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            archive_path, _ = release_builder.build_release(temp_root / "release")
            extract_root = temp_root / "extract"
            with zipfile.ZipFile(archive_path) as archive:
                archive.extractall(extract_root)
            portable = extract_root / release_builder.ARCHIVE_ROOT

            gui = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(portable / "launch_factory.pyw"),
                    "--smoke-test",
                ],
                cwd=portable,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            self.assertEqual(gui.returncode, 0, gui.stdout + gui.stderr)

            projects = temp_root / "projects"
            created = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(portable / "run_factory.py"),
                    "create",
                    "--parent",
                    str(projects),
                    "--name",
                    "Portable Cold Start",
                    "--no-git",
                ],
                cwd=portable,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            project = projects / "Portable Cold Start"
            doctor = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(portable / "run_factory.py"),
                    "doctor",
                    str(project),
                ],
                cwd=portable,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
            self.assertFalse(
                any(project.rglob("__pycache__")),
                "Portable cold start must not mutate the generated project with bytecode.",
            )

    @unittest.skipUnless(
        HAS_SETUPTOOLS,
        "the wheel build uses --no-build-isolation, so the backend "
        "must already be installed",
    )
    def test_wheel_is_deterministic_and_cold_starts_in_fresh_venv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, first_checksum = wheel_builder.build_wheel(root / "first")
            second, second_checksum = wheel_builder.build_wheel(root / "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            digest = hashlib.sha256(first.read_bytes()).hexdigest()
            self.assertEqual(
                first_checksum.read_text(encoding="utf-8").split(),
                [digest, first.name],
            )
            self.assertEqual(
                second_checksum.read_text(encoding="utf-8").split()[0],
                digest,
            )

            environment = root / "venv"
            venv.EnvBuilder(with_pip=True, clear=True).create(environment)
            if os.name == "nt":
                python = environment / "Scripts" / "python.exe"
            else:
                python = environment / "bin" / "python"
            install = subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--no-deps",
                    str(first),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertEqual(
                install.returncode,
                0,
                install.stdout + install.stderr,
            )
            smoke = subprocess.run(
                [
                    str(python),
                    "-B",
                    "-m",
                    "ai_project_factory",
                    "gui",
                    "--smoke-test",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(smoke.returncode, 0, smoke.stdout + smoke.stderr)
            installed_version = subprocess.run(
                [
                    str(python),
                    "-B",
                    "-c",
                    (
                        "from ai_project_factory.core import FACTORY_VERSION;"
                        "print(FACTORY_VERSION)"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(installed_version.returncode, 0)
            expected_factory_version = (
                ROOT
                / "src"
                / "ai_project_factory"
                / "core.py"
            ).read_text(encoding="utf-8").split('FACTORY_VERSION = "', 1)[1].split(
                '"',
                1,
            )[0]
            self.assertEqual(
                installed_version.stdout.strip(),
                expected_factory_version,
            )

            projects = root / "projects"
            create = subprocess.run(
                [
                    str(python),
                    "-B",
                    "-m",
                    "ai_project_factory",
                    "create",
                    "--parent",
                    str(projects),
                    "--name",
                    "Wheel Cold Start",
                    "--no-git",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(create.returncode, 0, create.stdout + create.stderr)
            project = projects / "Wheel Cold Start"
            project_state = json.loads(
                (project / "AI_PROJECT.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                project_state["factory_version"],
                expected_factory_version,
            )
            doctor = subprocess.run(
                [
                    str(python),
                    "-B",
                    "-m",
                    "ai_project_factory",
                    "doctor",
                    str(project),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)

            codex_skills = root / "codex-skills"
            claude_skills = root / "claude-skills"
            sync = subprocess.run(
                [
                    str(python),
                    "-B",
                    "-m",
                    "ai_project_factory",
                    "sync-adapters",
                    "--codex-skills",
                    str(codex_skills),
                    "--claude-skills",
                    str(claude_skills),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
            bridge = (
                codex_skills
                / "ai-project-factory"
                / "scripts"
                / "factory_bridge.py"
            )
            bridge_projects = root / "bridge-projects"
            bridge_create = subprocess.run(
                [
                    str(python),
                    "-B",
                    str(bridge),
                    "create",
                    "--parent",
                    str(bridge_projects),
                    "--name",
                    "Bridge Wheel",
                    "--no-git",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(
                bridge_create.returncode,
                0,
                bridge_create.stdout + bridge_create.stderr,
            )
            bridge_project = bridge_projects / "Bridge Wheel"
            bridge_doctor = subprocess.run(
                [
                    str(python),
                    "-B",
                    str(bridge),
                    "doctor",
                    str(bridge_project),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(
                bridge_doctor.returncode,
                0,
                bridge_doctor.stdout + bridge_doctor.stderr,
            )
            exported = root / "bridge-context.md"
            bridge_export = subprocess.run(
                [
                    str(python),
                    "-B",
                    str(bridge),
                    "export",
                    str(bridge_project),
                    "--output",
                    str(exported),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
            self.assertEqual(
                bridge_export.returncode,
                0,
                bridge_export.stdout + bridge_export.stderr,
            )
            self.assertTrue(exported.is_file())


if __name__ == "__main__":
    unittest.main()
