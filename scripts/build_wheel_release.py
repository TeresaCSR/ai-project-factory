#!/usr/bin/env python3
"""Build and verify a reproducible wheel without mutating the source tree."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = ROOT / "scripts" / "build_portable_release.py"
FIXED_SOURCE_DATE_EPOCH = "315532800"


def _load_release_builder():
    spec = importlib.util.spec_from_file_location(
        "_factory_release_builder_for_wheel",
        RELEASE_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load release builder: {RELEASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_version() -> str:
    return str(_load_release_builder().project_version())


def _prepare_source(destination: Path) -> None:
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")
    shutil.copy2(ROOT / "README.md", destination / "README.md")
    shutil.copytree(
        ROOT / "src",
        destination / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def _verify_wheel(path: Path, version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if any(
            "__pycache__" in name
            or name.endswith((".pyc", ".pyo"))
            or ".egg-info/" in name
            for name in names
        ):
            raise RuntimeError("Wheel contains build or bytecode residue.")
        metadata_names = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise RuntimeError("Wheel must contain exactly one METADATA file.")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
        if f"Version: {version}" not in metadata.splitlines():
            raise RuntimeError("Wheel METADATA version does not match pyproject.")
        required = {
            "ai_project_factory/__init__.py",
            "ai_project_factory/__main__.py",
            "ai_project_factory/core.py",
            "ai_project_factory/gui.py",
            "ai_project_factory/templates/v1/AI_START_HERE.md",
            (
                "ai_project_factory/resources/agent-skills/"
                "ai-project-factory/SKILL.md"
            ),
        }
        missing = required - set(names)
        if missing:
            raise RuntimeError(
                "Wheel is missing required files: " + ", ".join(sorted(missing))
            )


def build_wheel(
    output_dir: Path,
    *,
    force: bool = False,
) -> tuple[Path, Path]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    version = project_version()
    expected_name = f"ai_project_factory_demo-{version}-py3-none-any.whl"
    output = output_dir / expected_name
    checksum = output.with_suffix(".sha256.txt")
    if not force and (output.exists() or checksum.exists()):
        raise FileExistsError(f"Refusing to overwrite an existing wheel: {output}")

    with tempfile.TemporaryDirectory(prefix="ai-project-factory-wheel-") as temp:
        temp_root = Path(temp)
        source = temp_root / "source"
        wheel_dir = temp_root / "wheel"
        source.mkdir()
        wheel_dir.mkdir()
        _prepare_source(source)
        environment = os.environ.copy()
        environment["SOURCE_DATE_EPOCH"] = FIXED_SOURCE_DATE_EPOCH
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "--no-build-isolation",
                "--no-cache-dir",
                "--wheel-dir",
                str(wheel_dir),
            ],
            cwd=source,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Wheel build failed:\n"
                + (result.stdout + "\n" + result.stderr).strip()
            )
        wheels = list(wheel_dir.glob("*.whl"))
        if len(wheels) != 1 or wheels[0].name != expected_name:
            raise RuntimeError(
                "Wheel builder produced an unexpected file set: "
                + ", ".join(path.name for path in wheels)
            )
        _verify_wheel(wheels[0], version)
        content = wheels[0].read_bytes()

    handle, raw_temp = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=str(output_dir),
    )
    os.close(handle)
    temp_output = Path(raw_temp)
    try:
        temp_output.write_bytes(content)
        os.replace(temp_output, output)
    finally:
        temp_output.unlink(missing_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    checksum.write_text(
        f"{digest}  {output.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    return output, checksum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the reproducible AI Project Factory wheel."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        output, checksum = build_wheel(args.output_dir, force=args.force)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(output)
    print(checksum)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
