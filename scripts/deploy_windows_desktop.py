#!/usr/bin/env python3
"""Deploy the Factory behind one stable Windows desktop shortcut.

The shortcut never points at a dated source checkout or a versioned release
directory.  It points at a small launcher in a fixed per-user install root;
each deployment verifies a staged payload and then switches ``current``.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = ROOT / "scripts" / "build_portable_release.py"
DEFAULT_SHORTCUT_NAME = "AI Project Factory.lnk"
MANAGED_DESCRIPTION = "AI Project Factory stable desktop channel"
DESKTOP_ICON = Path("assets/branding/desktop/ai-project-factory.ico")
DEPLOY_LOCK = ".desktop-deploy.lock"

LAUNCHER_VBS = r"""Option Explicit
Dim shell, files, root, target
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
target = files.BuildPath(files.BuildPath(root, "current"), "AI Project Factory.cmd")

If Not files.FileExists(target) Then
  MsgBox "AI Project Factory is not installed correctly." & vbCrLf & _
         "Run the desktop installer again.", 16, "AI Project Factory"
  WScript.Quit 1
End If

shell.CurrentDirectory = files.BuildPath(root, "current")
shell.Run Chr(34) & target & Chr(34), 0, False
"""

SHORTCUT_WRITER = r"""param(
  [Parameter(Mandatory=$true)][string]$ShortcutPath,
  [Parameter(Mandatory=$true)][string]$TargetPath,
  [Parameter(Mandatory=$true)][string]$TargetArguments,
  [Parameter(Mandatory=$true)][string]$WorkingDirectory,
  [Parameter(Mandatory=$true)][string]$IconLocation,
  [Parameter(Mandatory=$true)][string]$Description
)
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
$shortcut.TargetPath = $TargetPath
$shortcut.Arguments = $TargetArguments
$shortcut.WorkingDirectory = $WorkingDirectory
$shortcut.IconLocation = $IconLocation
$shortcut.Description = $Description
$shortcut.WindowStyle = 7
$shortcut.Save()
"""

SHORTCUT_READER = r"""param(
  [Parameter(Mandatory=$true)][string]$ShortcutPath
)
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($ShortcutPath)
[ordered]@{
  TargetPath = $shortcut.TargetPath
  Arguments = $shortcut.Arguments
  WorkingDirectory = $shortcut.WorkingDirectory
  IconLocation = $shortcut.IconLocation
  Description = $shortcut.Description
} | ConvertTo-Json -Compress
"""


def _load_release_builder():
    spec = importlib.util.spec_from_file_location(
        "_factory_release_builder_for_desktop",
        RELEASE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load release builder: {RELEASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_install_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "AI Project Factory"
    return Path.home() / "AppData" / "Local" / "AI Project Factory"


def windows_desktop() -> Path:
    if sys.platform != "win32":
        return Path.home() / "Desktop"
    try:
        import winreg

        key_path = (
            r"Software\Microsoft\Windows\CurrentVersion\Explorer"
            r"\User Shell Folders"
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            raw, _ = winreg.QueryValueEx(key, "Desktop")
        return Path(os.path.expandvars(raw)).expanduser()
    except (OSError, TypeError, ValueError):
        return Path.home() / "Desktop"


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def validate_install_root(path: Path) -> Path:
    resolved = _resolved(path)
    filesystem_root = Path(resolved.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    if resolved in {filesystem_root, home}:
        raise ValueError("Install root must be a dedicated child directory.")
    if len(resolved.parts) < 3:
        raise ValueError("Install root is too broad.")
    return resolved


def _safe_remove_tree(path: Path, install_root: Path) -> None:
    path = _resolved(path)
    install_root = _resolved(install_root)
    if path.parent != install_root:
        raise ValueError(f"Refusing to remove path outside install root: {path}")
    if not (
        path.name.startswith(".staging-")
        or path.name.startswith(".previous-")
    ):
        raise ValueError(f"Refusing to remove unmanaged directory: {path}")
    if path.exists():
        shutil.rmtree(path)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp = Path(raw_temp)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _snapshot_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _restore_file(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
    else:
        _write_atomic(path, snapshot)


def _stage_payload(staging: Path) -> tuple[str, int]:
    builder = _load_release_builder()
    version = builder.project_version()
    payload: dict[str, bytes] = builder.collect_payload()
    payload["RELEASE_MANIFEST.json"] = builder.manifest_bytes(version, payload)
    for relative, content in payload.items():
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise ValueError(f"Unsafe payload path: {relative}")
        destination = staging.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return version, len(payload)


def _verify_payload_manifest(staging: Path) -> None:
    manifest_path = staging / "RELEASE_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Staged release manifest is unreadable.") from exc
    listed = manifest.get("files")
    if not isinstance(listed, list):
        raise RuntimeError("Staged release manifest has no file list.")
    expected: dict[str, tuple[int, str]] = {}
    for item in listed:
        if not isinstance(item, dict):
            raise RuntimeError("Staged release manifest contains an invalid entry.")
        relative = str(item.get("path", ""))
        if (
            not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative in expected
        ):
            raise RuntimeError(f"Unsafe or duplicate manifest path: {relative}")
        try:
            size = int(item["size"])
            digest = str(item["sha256"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid manifest metadata for: {relative}"
            ) from exc
        expected[relative] = (size, digest)

    actual = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual != set(expected):
        raise RuntimeError("Staged payload does not match the manifest file set.")
    for relative, (expected_size, expected_digest) in expected.items():
        content = (staging / Path(relative)).read_bytes()
        if len(content) != expected_size:
            raise RuntimeError(f"Staged payload size mismatch: {relative}")
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise RuntimeError(f"Staged payload hash mismatch: {relative}")


def _smoke_test_launcher_vbs(launcher_text: str = LAUNCHER_VBS) -> None:
    """Compile and execute the real VBS launcher against a harmless stub."""

    if sys.platform != "win32":
        return
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    cscript = system_root / "System32" / "cscript.exe"
    if not cscript.is_file():
        discovered = shutil.which("cscript.exe") or shutil.which("cscript")
        if not discovered:
            raise RuntimeError(
                "Windows Script Host is required for the stable launcher."
            )
        cscript = Path(discovered)

    with tempfile.TemporaryDirectory(
        prefix="ai-project-factory-launcher-smoke-"
    ) as raw_temp:
        root = Path(raw_temp) / "Factory Root With Spaces"
        current = root / "current"
        current.mkdir(parents=True)
        launcher = root / "launch.vbs"
        launcher.write_text(
            launcher_text,
            encoding="utf-8",
            newline="\r\n",
        )
        marker = current / "launcher-smoke.txt"
        stub = current / "AI Project Factory.cmd"
        stub.write_text(
            '@echo off\r\n> "%~dp0launcher-smoke.txt" echo launched\r\n',
            encoding="ascii",
            newline="",
        )
        result = subprocess.run(
            [
                str(cscript),
                "//B",
                "//NoLogo",
                str(launcher),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            details = (result.stdout + "\n" + result.stderr).strip()
            raise RuntimeError(
                "Windows launcher failed its Script Host smoke test"
                + (f":\n{details}" if details else ".")
            )
        deadline = time.monotonic() + 5.0
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not marker.is_file():
            raise RuntimeError(
                "Windows launcher compiled but did not execute its quoted target."
            )


def _verify_staging(staging: Path) -> None:
    _verify_payload_manifest(staging)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(staging / "launch_factory.pyw"),
            "--smoke-test",
        ],
        cwd=staging,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode != 0:
        details = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(
            "Staged Factory failed its GUI smoke test"
            + (f":\n{details}" if details else ".")
        )
    _smoke_test_launcher_vbs()


def _switch_current(staging: Path, install_root: Path) -> tuple[Path, Path | None]:
    current = install_root / "current"
    previous = (
        install_root / f".previous-{uuid.uuid4().hex}"
        if current.exists()
        else None
    )
    if previous is not None:
        try:
            os.replace(current, previous)
        except OSError as exc:
            if isinstance(exc, PermissionError) or getattr(
                exc,
                "winerror",
                None,
            ) in {5, 32, 33}:
                raise RuntimeError(
                    "AI Project Factory is still running. Close every Factory "
                    "window, then run the desktop update again."
                ) from exc
            raise
    try:
        os.replace(staging, current)
    except BaseException:
        if previous is not None and previous.exists() and not current.exists():
            os.replace(previous, current)
        raise
    return current, previous


@contextmanager
def desktop_deploy_lock(
    install_root: Path,
    *,
    timeout_seconds: float = 30.0,
):
    """Serialize all deployment, shortcut, icon, and rollback mutations."""

    lock_path = install_root / DEPLOY_LOCK
    if lock_path.is_symlink() or (
        lock_path.exists() and not lock_path.is_file()
    ):
        raise RuntimeError(f"Desktop deployment lock is not a file: {lock_path}")
    handle = lock_path.open("a+b")
    if lock_path.stat().st_size == 0:
        handle.write(b"\0")
        handle.flush()
    deadline = time.monotonic() + timeout_seconds
    acquired = False
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Another AI Project Factory desktop update is still running."
                    )
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
        else:
            handle.close()


def _powershell_executable() -> str:
    candidate = shutil.which("powershell.exe") or shutil.which("powershell")
    if not candidate:
        raise RuntimeError("Windows PowerShell is required to create the shortcut.")
    return candidate


def _run_powershell_file(script: str, arguments: list[str]) -> str:
    handle, raw_path = tempfile.mkstemp(suffix=".ps1")
    path = Path(raw_path)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(script)
        result = subprocess.run(
            [
                _powershell_executable(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(path),
                *arguments,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "PowerShell shortcut operation failed:\n"
                + (result.stdout + "\n" + result.stderr).strip()
            )
        return result.stdout.strip()
    finally:
        path.unlink(missing_ok=True)


def inspect_shortcut(shortcut_path: Path) -> dict[str, str]:
    output = _run_powershell_file(
        SHORTCUT_READER,
        ["-ShortcutPath", str(shortcut_path)],
    )
    data = json.loads(output)
    if not isinstance(data, dict):
        raise RuntimeError("Shortcut inspection returned invalid data.")
    return {str(key): str(value) for key, value in data.items()}


def _shortcut_is_managed(shortcut_path: Path, launcher: Path) -> bool:
    if not shortcut_path.exists():
        return True
    details = inspect_shortcut(shortcut_path)
    description = details.get("Description", "")
    arguments = details.get("Arguments", "")
    return (
        description == MANAGED_DESCRIPTION
        and str(launcher).casefold() in arguments.casefold()
    )


def _create_shortcut(
    shortcut_path: Path,
    install_root: Path,
    launcher: Path,
    icon: Path,
    *,
    replace_unmanaged: bool,
) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    if not replace_unmanaged and not _shortcut_is_managed(shortcut_path, launcher):
        raise FileExistsError(
            f"Refusing to replace an unmanaged shortcut: {shortcut_path}"
        )
    wscript = shutil.which("wscript.exe")
    if not wscript:
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        wscript = str(windir / "System32" / "wscript.exe")
    _run_powershell_file(
        SHORTCUT_WRITER,
        [
            "-ShortcutPath",
            str(shortcut_path),
            "-TargetPath",
            wscript,
            "-TargetArguments",
            f'"{launcher}"',
            "-WorkingDirectory",
            str(install_root),
            "-IconLocation",
            f"{icon},0",
            "-Description",
            MANAGED_DESCRIPTION,
        ],
    )
    if not shortcut_path.is_file():
        raise RuntimeError(f"Shortcut was not created: {shortcut_path}")


def _expected_wscript() -> str:
    candidate = shutil.which("wscript.exe")
    if candidate:
        return candidate
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return str(windir / "System32" / "wscript.exe")


def _verify_shortcut(
    details: dict[str, str],
    *,
    install_root: Path,
    launcher: Path,
    icon: Path,
) -> None:
    expected = {
        "TargetPath": _expected_wscript(),
        "Arguments": f'"{launcher}"',
        "WorkingDirectory": str(install_root),
        "IconLocation": f"{icon},0",
        "Description": MANAGED_DESCRIPTION,
    }
    for field, value in expected.items():
        actual = details.get(field, "")
        if actual.casefold() != value.casefold():
            raise RuntimeError(
                f"Shortcut verification found the wrong {field}: {actual}"
            )


def _refresh_shell(shortcut_path: Path) -> None:
    if sys.platform != "win32":
        return
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        shell32.SHChangeNotify(0x00002000, 0x0005, str(shortcut_path), None)
        shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
    except (AttributeError, OSError):
        pass


def _deploy_locked(
    *,
    install_root: Path,
    desktop: Path,
    shortcut_name: str = DEFAULT_SHORTCUT_NAME,
    replace_unmanaged_shortcut: bool = False,
) -> dict[str, str | int]:
    if sys.platform != "win32":
        raise RuntimeError("Desktop deployment is supported on Windows only.")
    install_root = validate_install_root(install_root)
    desktop = _resolved(desktop)
    install_root.mkdir(parents=True, exist_ok=True)

    staging = install_root / f".staging-{uuid.uuid4().hex}"
    staging.mkdir()
    launcher = install_root / "launch.vbs"
    stable_icon_alias = install_root / "assets" / "ai-project-factory.ico"
    shortcut_path = desktop / shortcut_name
    state_path = install_root / "DEPLOYMENT.json"
    support_snapshots = {
        launcher: _snapshot_file(launcher),
        stable_icon_alias: _snapshot_file(stable_icon_alias),
        shortcut_path: _snapshot_file(shortcut_path),
        state_path: _snapshot_file(state_path),
    }
    previous: Path | None = None
    current: Path | None = None
    try:
        version, file_count = _stage_payload(staging)
        _verify_staging(staging)
        staged_icon = staging / DESKTOP_ICON
        icon_bytes = staged_icon.read_bytes()
        icon_digest = hashlib.sha256(icon_bytes).hexdigest()
        versioned_icon = (
            install_root
            / "assets"
            / f"ai-project-factory-{icon_digest[:16]}.ico"
        )
        support_snapshots[versioned_icon] = _snapshot_file(versioned_icon)
        current, previous = _switch_current(staging, install_root)

        _write_atomic(launcher, LAUNCHER_VBS.encode("utf-8"))

        source_icon = current / DESKTOP_ICON
        if not source_icon.is_file():
            raise RuntimeError(f"Desktop icon is missing from payload: {source_icon}")
        if source_icon.read_bytes() != icon_bytes:
            raise RuntimeError("Desktop icon changed after payload verification.")
        _write_atomic(stable_icon_alias, icon_bytes)
        _write_atomic(versioned_icon, icon_bytes)

        _create_shortcut(
            shortcut_path,
            install_root,
            launcher,
            versioned_icon,
            replace_unmanaged=replace_unmanaged_shortcut,
        )
        details = inspect_shortcut(shortcut_path)
        _verify_shortcut(
            details,
            install_root=install_root,
            launcher=launcher,
            icon=versioned_icon,
        )

        state = {
            "schema_version": "ai-project-factory/desktop-channel-v1",
            "version": version,
            "deployed_at": dt.datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "channel": "current",
            "file_count": file_count,
            "shortcut": str(shortcut_path),
            "launcher": str(launcher),
            "icon": str(versioned_icon),
            "icon_alias": str(stable_icon_alias),
            "icon_sha256": icon_digest,
        }
        _write_atomic(
            state_path,
            (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
        _refresh_shell(shortcut_path)
    except BaseException:
        if current is not None:
            failed = install_root / f".staging-failed-{uuid.uuid4().hex}"
            if current.exists():
                os.replace(current, failed)
            if previous is not None and previous.exists():
                os.replace(previous, install_root / "current")
            try:
                _safe_remove_tree(failed, install_root)
            except OSError:
                pass
            for path, snapshot in support_snapshots.items():
                try:
                    _restore_file(path, snapshot)
                except OSError:
                    pass
            _refresh_shell(shortcut_path)
        if staging.exists():
            _safe_remove_tree(staging, install_root)
        raise

    cleanup_warning = ""
    if previous is not None and previous.exists():
        try:
            _safe_remove_tree(previous, install_root)
        except OSError as exc:
            cleanup_warning = str(exc)

    return {
        **state,
        "install_root": str(install_root),
        "cleanup_warning": cleanup_warning,
    }


def deploy(
    *,
    install_root: Path,
    desktop: Path,
    shortcut_name: str = DEFAULT_SHORTCUT_NAME,
    replace_unmanaged_shortcut: bool = False,
) -> dict[str, str | int]:
    if sys.platform != "win32":
        raise RuntimeError("Desktop deployment is supported on Windows only.")
    resolved_root = validate_install_root(install_root)
    resolved_root.mkdir(parents=True, exist_ok=True)
    with desktop_deploy_lock(resolved_root):
        return _deploy_locked(
            install_root=resolved_root,
            desktop=desktop,
            shortcut_name=shortcut_name,
            replace_unmanaged_shortcut=replace_unmanaged_shortcut,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install or update AI Project Factory behind one stable "
            "Windows desktop shortcut."
        )
    )
    parser.add_argument(
        "--install-root",
        type=Path,
        default=default_install_root(),
    )
    parser.add_argument("--desktop", type=Path, default=windows_desktop())
    parser.add_argument("--shortcut-name", default=DEFAULT_SHORTCUT_NAME)
    parser.add_argument(
        "--replace-unmanaged-shortcut",
        action="store_true",
        help="Allow replacing a same-named shortcut not managed by the Factory.",
    )
    args = parser.parse_args(argv)
    try:
        result = deploy(
            install_root=args.install_root,
            desktop=args.desktop,
            shortcut_name=args.shortcut_name,
            replace_unmanaged_shortcut=args.replace_unmanaged_shortcut,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
