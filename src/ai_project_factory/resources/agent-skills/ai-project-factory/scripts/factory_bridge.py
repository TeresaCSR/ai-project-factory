#!/usr/bin/env python3
"""Thin installed bridge to the one canonical Factory source."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


CONFIGURED_FACTORY_ROOT = "{{FACTORY_ROOT_JSON}}"
CONFIGURED_FACTORY_PYTHON = "{{FACTORY_PYTHON_JSON}}"


def locate_factory() -> Path:
    if not CONFIGURED_FACTORY_ROOT.startswith("{{"):
        return Path(CONFIGURED_FACTORY_ROOT).expanduser().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "run_factory.py").is_file():
            return parent
    return Path()


def main() -> int:
    root = locate_factory()
    configured_python = Path(CONFIGURED_FACTORY_PYTHON)
    factory_python = (
        str(configured_python)
        if not CONFIGURED_FACTORY_PYTHON.startswith("{{")
        and configured_python.is_file()
        else sys.executable
    )
    source_entrypoint = root / "run_factory.py"
    if source_entrypoint.is_file():
        command = [factory_python, str(source_entrypoint), *sys.argv[1:]]
        cwd: Path | None = root
    else:
        command = [factory_python, "-m", "ai_project_factory", *sys.argv[1:]]
        cwd = None
    return subprocess.run(command, cwd=cwd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
