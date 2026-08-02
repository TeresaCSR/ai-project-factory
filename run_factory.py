#!/usr/bin/env python3
"""Run the Factory directly from a source checkout without installing it."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from ai_project_factory.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
