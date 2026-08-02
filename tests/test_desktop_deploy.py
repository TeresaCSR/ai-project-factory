from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def canonical(path: object) -> str:
    """A path spelling that two references to the same location agree on.

    Windows Script Host hands back 8.3 short names, so a shortcut created
    under a home directory whose account name is longer than eight characters
    reads back in the truncated ``NAME~1`` form. Comparing the raw strings
    therefore only passes where the account name is short -- which is why this
    held locally and failed on CI. ``realpath`` expands the short form, so
    both spellings meet.

    (Deliberately no literal example path here: the portable-release guard
    scans the payload for this machine's home directory, and on a runner whose
    account matches the example, the comment itself would read as a leak.)
    """
    return os.path.realpath(str(path)).casefold()
SCRIPT = ROOT / "scripts" / "deploy_windows_desktop.py"
SPEC = importlib.util.spec_from_file_location("_factory_desktop_deployer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
desktop_deployer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop_deployer)


class DesktopDeploymentTests(unittest.TestCase):
    def test_launcher_targets_current_without_a_versioned_path(self) -> None:
        launcher = desktop_deployer.LAUNCHER_VBS
        self.assertIn('"current"', launcher)
        self.assertIn('"AI Project Factory.cmd"', launcher)
        self.assertIn("Chr(34) & target & Chr(34)", launcher)
        self.assertNotIn("shell.Run  & target &", launcher)
        self.assertNotIn("v0.", launcher)
        self.assertNotIn(str(ROOT), launcher)

    @unittest.skipUnless(sys.platform == "win32", "Windows Script Host test")
    def test_launcher_vbscript_executes_quoted_target(self) -> None:
        desktop_deployer._smoke_test_launcher_vbs()

    @unittest.skipUnless(sys.platform == "win32", "Windows Script Host test")
    def test_launcher_vbscript_rejects_invalid_syntax(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Script Host smoke test"):
            desktop_deployer._smoke_test_launcher_vbs(
                "Option Explicit\r\nshell.Run &\r\n"
            )

    def test_install_root_rejects_broad_targets(self) -> None:
        with self.assertRaises(ValueError):
            desktop_deployer.validate_install_root(Path.home())
        with self.assertRaises(ValueError):
            desktop_deployer.validate_install_root(Path(Path.home().anchor))

    def test_stage_contains_icon_launcher_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp) / "stage"
            staging.mkdir()
            version, count = desktop_deployer._stage_payload(staging)
            self.assertEqual(
                version,
                desktop_deployer._load_release_builder().project_version(),
            )
            self.assertGreater(count, 3)
            self.assertTrue((staging / "launch_factory.pyw").is_file())
            self.assertTrue(
                (
                    staging
                    / "assets"
                    / "branding"
                    / "desktop"
                    / "ai-project-factory.ico"
                ).is_file()
            )
            self.assertTrue((staging / "RELEASE_MANIFEST.json").is_file())
            desktop_deployer._verify_payload_manifest(staging)

    def test_manifest_verification_rejects_payload_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp) / "stage"
            staging.mkdir()
            desktop_deployer._stage_payload(staging)
            launcher = staging / "launch_factory.pyw"
            launcher.write_bytes(launcher.read_bytes() + b"\n# tampered\n")
            with self.assertRaisesRegex(RuntimeError, "size mismatch"):
                desktop_deployer._verify_payload_manifest(staging)

    def test_desktop_icon_has_small_and_large_frames(self) -> None:
        icon = (
            ROOT
            / "assets"
            / "branding"
            / "desktop"
            / "ai-project-factory.ico"
        )
        raw = icon.read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", raw, 0)
        self.assertEqual((reserved, kind), (0, 1))
        self.assertGreaterEqual(count, 4)
        sizes: set[int] = set()
        for index in range(count):
            width, height = struct.unpack_from(
                "<BB",
                raw,
                6 + 16 * index,
            )
            self.assertEqual(width, height)
            sizes.add(256 if width == 0 else width)
        self.assertTrue({16, 32, 48, 256}.issubset(sizes))

    def test_switch_current_can_roll_forward(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            install_root = Path(temp) / "factory"
            install_root.mkdir()
            current = install_root / "current"
            current.mkdir()
            (current / "marker.txt").write_text("old", encoding="utf-8")
            staging = install_root / ".staging-test"
            staging.mkdir()
            (staging / "marker.txt").write_text("new", encoding="utf-8")

            switched, previous = desktop_deployer._switch_current(
                staging,
                install_root,
            )
            self.assertEqual(
                (switched / "marker.txt").read_text(encoding="utf-8"),
                "new",
            )
            assert previous is not None
            self.assertEqual(
                (previous / "marker.txt").read_text(encoding="utf-8"),
                "old",
            )

    def test_switch_current_reports_a_running_factory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            install_root = Path(temp) / "factory"
            install_root.mkdir()
            (install_root / "current").mkdir()
            staging = install_root / ".staging-test"
            staging.mkdir()
            with mock.patch.object(
                desktop_deployer.os,
                "replace",
                side_effect=PermissionError("in use"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "still running",
                ):
                    desktop_deployer._switch_current(staging, install_root)

    def test_existing_unmanaged_shortcut_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            shortcut = Path(temp) / "AI Project Factory.lnk"
            shortcut.write_bytes(b"not managed")
            launcher = Path(temp) / "launch.vbs"
            with mock.patch.object(
                desktop_deployer,
                "inspect_shortcut",
                return_value={
                    "Description": "Some other application",
                    "Arguments": "",
                },
            ):
                self.assertFalse(
                    desktop_deployer._shortcut_is_managed(shortcut, launcher)
                )

    @unittest.skipUnless(sys.platform == "win32", "Windows integration test")
    def test_two_deployments_reuse_one_verified_shortcut(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            desktop = root / "Desktop"
            desktop.mkdir()
            install_root = root / "install"

            first = desktop_deployer.deploy(
                install_root=install_root,
                desktop=desktop,
            )
            shortcut = Path(str(first["shortcut"]))
            first_details = desktop_deployer.inspect_shortcut(shortcut)
            second = desktop_deployer.deploy(
                install_root=install_root,
                desktop=desktop,
            )
            second_details = desktop_deployer.inspect_shortcut(shortcut)

            self.assertEqual(first["shortcut"], second["shortcut"])
            self.assertEqual(
                first_details["TargetPath"],
                second_details["TargetPath"],
            )
            self.assertEqual(
                first_details["Arguments"],
                second_details["Arguments"],
            )
            self.assertEqual(
                canonical(second_details["WorkingDirectory"]),
                canonical(install_root),
            )
            self.assertEqual(
                second_details["Description"],
                desktop_deployer.MANAGED_DESCRIPTION,
            )
            icon_path, _, icon_index = second_details["IconLocation"].rpartition(",")
            self.assertEqual(canonical(icon_path), canonical(second["icon"]))
            self.assertEqual(icon_index, "0")
            # The launcher path is embedded in a longer argument string, so
            # canonicalise the needle and search the haystack the same way.
            arguments = second_details["Arguments"]
            launcher = str(install_root / "launch.vbs")
            self.assertTrue(
                canonical(launcher) in canonical(arguments)
                or launcher.casefold() in arguments.casefold(),
                f"launcher {launcher!r} not referenced by arguments {arguments!r}",
            )
            leftovers = [
                path.name
                for path in install_root.iterdir()
                if path.name.startswith((".staging-", ".previous-"))
            ]
            self.assertEqual(leftovers, [])

    @unittest.skipUnless(sys.platform == "win32", "Windows integration test")
    def test_icon_content_update_reuses_shortcut_and_changes_icon_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            desktop = root / "Desktop"
            desktop.mkdir()
            install_root = root / "install"
            first = desktop_deployer.deploy(
                install_root=install_root,
                desktop=desktop,
            )
            shortcut = Path(str(first["shortcut"]))
            first_details = desktop_deployer.inspect_shortcut(shortcut)
            original_stage = desktop_deployer._stage_payload

            def stage_with_new_icon(staging: Path) -> tuple[str, int]:
                version, count = original_stage(staging)
                icon = staging / desktop_deployer.DESKTOP_ICON
                changed = icon.read_bytes() + b"\0"
                icon.write_bytes(changed)
                manifest_path = staging / "RELEASE_MANIFEST.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                relative = desktop_deployer.DESKTOP_ICON.as_posix()
                for item in manifest["files"]:
                    if item["path"] == relative:
                        item["size"] = len(changed)
                        item["sha256"] = hashlib.sha256(changed).hexdigest()
                        break
                manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                return version, count

            with mock.patch.object(
                desktop_deployer,
                "_stage_payload",
                side_effect=stage_with_new_icon,
            ):
                second = desktop_deployer.deploy(
                    install_root=install_root,
                    desktop=desktop,
                )
            second_details = desktop_deployer.inspect_shortcut(shortcut)
            self.assertEqual(first["shortcut"], second["shortcut"])
            self.assertEqual(
                first_details["TargetPath"],
                second_details["TargetPath"],
            )
            self.assertEqual(
                first_details["Arguments"],
                second_details["Arguments"],
            )
            self.assertNotEqual(
                first_details["IconLocation"],
                second_details["IconLocation"],
            )
            self.assertEqual(
                Path(str(second["icon_alias"])).read_bytes(),
                (
                    install_root
                    / "current"
                    / desktop_deployer.DESKTOP_ICON
                ).read_bytes(),
            )

    @unittest.skipUnless(sys.platform == "win32", "Windows integration test")
    def test_failed_update_restores_current_and_desktop_support_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            desktop = root / "Desktop"
            desktop.mkdir()
            install_root = root / "install"
            installed = desktop_deployer.deploy(
                install_root=install_root,
                desktop=desktop,
            )
            tracked = [
                install_root / "current" / "RELEASE_MANIFEST.json",
                install_root / "launch.vbs",
                install_root / "assets" / "ai-project-factory.ico",
                install_root / "DEPLOYMENT.json",
                Path(str(installed["shortcut"])),
            ]
            before = {path: path.read_bytes() for path in tracked}

            with mock.patch.object(
                desktop_deployer,
                "_create_shortcut",
                side_effect=RuntimeError("injected shortcut failure"),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected shortcut failure",
                ):
                    desktop_deployer.deploy(
                        install_root=install_root,
                        desktop=desktop,
                    )

            after = {path: path.read_bytes() for path in tracked}
            self.assertEqual(before, after)
            leftovers = [
                path.name
                for path in install_root.iterdir()
                if path.name.startswith((".staging-", ".previous-"))
            ]
            self.assertEqual(leftovers, [])

    @unittest.skipUnless(sys.platform == "win32", "Windows lock behavior")
    def test_desktop_deploy_lock_rejects_a_second_writer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            install_root = Path(temp) / "install"
            install_root.mkdir()
            with desktop_deployer.desktop_deploy_lock(install_root):
                with self.assertRaisesRegex(RuntimeError, "still running"):
                    with desktop_deployer.desktop_deploy_lock(
                        install_root,
                        timeout_seconds=0.05,
                    ):
                        self.fail("Second deployment lock must not be acquired.")


if __name__ == "__main__":
    unittest.main()
