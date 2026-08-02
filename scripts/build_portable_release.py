#!/usr/bin/env python3
"""Build a deterministic, source-portable AI Project Factory archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "AI-Project-Factory-Portable"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ROOT_FILES = (
    "AI Project Factory.cmd",
    "Install or Update Desktop Shortcut.cmd",
    "launch_factory.pyw",
    "MORNING_ACCEPTANCE.md",
    "README.md",
    "ROADMAP.md",
    "VALIDATION.md",
    "assets/branding/README.md",
    "assets/branding/desktop/ai-project-factory.ico",
    "pyproject.toml",
    "run_factory.py",
)
SOURCE_ROOTS = (
    "assets/branding/master",
    "src/ai_project_factory",
    "scripts",
    "tests",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^\[project\]\s.*?^version\s*=\s*\"([^\"]+)\"",
        text,
    )
    if not match:
        raise ValueError("Cannot find [project] version in pyproject.toml.")
    return match.group(1)


def collect_payload() -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for relative in ROOT_FILES:
        path = ROOT / relative
        if not path.is_file():
            raise ValueError(f"Missing portable release file: {relative}")
        payload[relative] = path.read_bytes()

    for relative_root in SOURCE_ROOTS:
        source_root = ROOT / relative_root
        if not source_root.is_dir():
            raise ValueError(f"Missing portable release directory: {relative_root}")
        for path in sorted(item for item in source_root.rglob("*") if item.is_file()):
            if (
                "__pycache__" in path.parts
                or path.suffix.lower() in {".pyc", ".pyo"}
                or ".egg-info" in path.parts
            ):
                continue
            relative = path.relative_to(ROOT).as_posix()
            payload[relative] = path.read_bytes()

    machine_user = Path.home().name.encode("utf-8", errors="ignore")
    leaked = [
        relative
        for relative, content in payload.items()
        if len(machine_user) >= 3 and machine_user in content
    ]
    if leaked:
        raise ValueError(
            "Portable payload contains a machine-specific username: "
            + ", ".join(leaked)
        )
    return payload


def manifest_bytes(version: str, payload: dict[str, bytes]) -> bytes:
    manifest = {
        "schema_version": "ai-project-factory/portable-release-v1",
        "version": version,
        "archive_root": ARCHIVE_ROOT,
        "files": [
            {
                "path": relative,
                "size": len(content),
                "sha256": sha256_bytes(content),
            }
            for relative, content in sorted(payload.items())
        ],
    }
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def write_zip_entry(
    archive: zipfile.ZipFile,
    relative: str,
    content: bytes,
) -> None:
    info = zipfile.ZipInfo(
        filename=f"{ARCHIVE_ROOT}/{relative}",
        date_time=FIXED_ZIP_TIME,
    )
    info.create_system = 3
    executable = relative in {
        "launch_factory.pyw",
        "run_factory.py",
        "scripts/build_portable_release.py",
    }
    info.external_attr = (0o755 if executable else 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def build_release(output_dir: Path, force: bool = False) -> tuple[Path, Path]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = project_version()
    output = output_dir / f"AI-Project-Factory-Portable-v{version}.zip"
    checksum = output.with_suffix(".sha256.txt")
    if not force and (output.exists() or checksum.exists()):
        raise FileExistsError(
            f"Refusing to overwrite an existing release: {output}"
        )

    payload = collect_payload()
    payload["RELEASE_MANIFEST.json"] = manifest_bytes(version, payload)
    fd, raw_temp = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=str(output_dir),
    )
    os.close(fd)
    temp = Path(raw_temp)
    try:
        with zipfile.ZipFile(temp, "w") as archive:
            for relative, content in sorted(payload.items()):
                write_zip_entry(archive, relative, content)
        with zipfile.ZipFile(temp) as archive:
            names = archive.namelist()
            expected = [
                f"{ARCHIVE_ROOT}/{relative}"
                for relative in sorted(payload)
            ]
            if names != expected:
                raise ValueError("Portable archive entry verification failed.")
            if any(
                "__pycache__" in name
                or name.endswith((".pyc", ".pyo"))
                for name in names
            ):
                raise ValueError("Portable archive contains Python bytecode.")
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum_text = f"{digest}  {output.name}\n"
    checksum.write_text(checksum_text, encoding="utf-8", newline="\n")
    return output, checksum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic portable Factory ZIP."
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "dist"),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        output, checksum = build_release(Path(args.output_dir), force=args.force)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(output)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
