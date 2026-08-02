"""Windows launcher that makes GUI startup failures visible to non-CLI users."""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def show_startup_failure(details: str) -> None:
    log_path = Path(tempfile.gettempdir()) / "ai-project-factory-startup-error.log"
    try:
        log_path.write_text(details, encoding="utf-8", newline="\n")
        message = (
            "AI Project Factory failed to start.\n\n"
            f"Diagnostic log:\n{log_path}"
        )
    except OSError:
        message = "AI Project Factory failed to start.\n\n" + details[-1500:]

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                0,
                message,
                "AI Project Factory",
                0x10,
            )
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def main() -> int:
    from ai_project_factory.cli import main as factory_main

    arguments = ["gui", *sys.argv[1:]]
    return factory_main(arguments)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        show_startup_failure(
            f"Factory exited with code {exc.code}.\n\n"
            + "".join(traceback.format_exception(exc))
        )
        raise
    except BaseException:
        show_startup_failure(traceback.format_exc())
        raise SystemExit(1)
