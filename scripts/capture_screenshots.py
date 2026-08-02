#!/usr/bin/env python3
"""Capture the README screenshots from the real GUI.

Hand-cropped screenshots rot: someone renames a button, the README keeps
showing the old one, and nobody notices until a reader is confused. So the
images are generated from the actual widgets, against real projects created by
the real ``create_project``, and can be regenerated after any UI change with a
single command.

Windows only, because the GUI is. Requires Pillow, which the package itself
does not depend on -- this is a maintainer tool, not part of the product.

    python scripts/capture_screenshots.py
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ai_project_factory.core import CreateProjectRequest, create_project  # noqa: E402
from ai_project_factory.gui import FactoryApp  # noqa: E402

# PrintWindow's undocumented-but-universal flag for compositing the whole
# window, including parts a plain BitBlt of the screen would miss.
PW_RENDERFULLCONTENT = 0x00000002

DEMO_PROJECTS = (
    ("Thermal Rig Study", "research"),
    ("Inventory Sync", "software"),
)

# The projects are really created, in a temporary folder, so the console shows
# state the Core actually produced. But a temp path contains the operator's
# Windows account name, and these images go into a public README, so the path
# fields are set to a neutral one for the capture itself.
DISPLAY_ROOT = r"D:\Projects\AI Projects"


def assert_no_personal_paths(window) -> None:
    """Refuse to capture anything showing the operator's home directory.

    The repo already blocks personal paths inside generated projects; a
    screenshot is the same leak with a friendlier file extension.
    """
    home = str(Path.home())
    account = Path.home().name
    shown = [
        window.parent_var.get(),
        window.project_var.get(),
        window.output.get("1.0", "end"),
        window.idea_text.get("1.0", "end"),
    ]
    for text in shown:
        for needle in (home, account):
            if needle and needle in text:
                raise SystemExit(
                    f"refusing to capture: {needle!r} is visible in the GUI"
                )


def capture(window, destination: Path) -> None:
    """Write the window's own pixels to ``destination`` as a PNG."""
    from PIL import Image

    window.update_idletasks()
    window.update()
    # Two frames of settle time: ttk draws some states lazily, and a capture
    # taken in the same tick as the last update can catch a half-themed widget.
    time.sleep(0.4)
    window.update()

    hwnd = wintypes.HWND(window.winfo_id())
    width = window.winfo_width()
    height = window.winfo_height()

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)

    if not user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT):
        raise RuntimeError("PrintWindow refused to render the GUI")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.biWidth = width
    # Negative height asks GDI for a top-down bitmap, which is the row order
    # Pillow expects; otherwise the image comes out upside down.
    header.biHeight = -height
    header.biPlanes = 1
    header.biBitCount = 32
    header.biCompression = 0  # BI_RGB

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(
        memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), 0
    )

    image = Image.frombuffer(
        "RGB", (width, height), buffer, "raw", "BGRX", 0, 1
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, optimize=True)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
    print(f"{destination.relative_to(ROOT)}  {width}x{height}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "docs",
        help="folder for the generated PNGs",
    )
    args = parser.parse_args()

    if sys.platform != "win32":
        print("The GUI is Windows-only; nothing to capture here.")
        return 0

    with tempfile.TemporaryDirectory() as temp:
        parent = Path(temp) / "AI Projects"
        for name, profile in DEMO_PROJECTS:
            create_project(
                CreateProjectRequest(
                    parent=parent,
                    project_name=name,
                    profile=profile,
                    initialize_git=False,
                )
            )

        app = FactoryApp()
        app.parent_var.set(str(parent))
        app.name_var.set("Thermal Rig Study")
        app.idea_text.insert(
            "1.0",
            "A rig that logs three thermocouples, and the analysis has to "
            "survive me switching machines.",
        )
        app.deiconify()

        # Discovery has to run against the real folder; the swap to the
        # display path happens afterwards, and nothing re-reads it before the
        # captures are done.
        app._show_page("manage")
        rows = app.project_tree.get_children()
        if rows:
            app.project_tree.selection_set(rows[0])
        # ``selection_set`` queues <<TreeviewSelect>>, and the handler rewrites
        # the path field from the real folder. Let it run and settle before
        # substituting the display path, or it undoes the substitution.
        app.update()

        selected = f"{DISPLAY_ROOT}\\Inventory Sync"
        app.parent_var.set(DISPLAY_ROOT)
        app.project_var.set(selected)
        app._set_output(
            "Selected: Inventory Sync\n"
            f"Folder: {selected}\n"
            "State: discussion / none\n"
            "Handoff revision: 1\n\n"
            "You can start Codex now, or read the project state and run the "
            "full validation first."
        )
        assert_no_personal_paths(app)
        app.update()
        capture(app, args.out / "screenshot-console.png")

        app._show_page("create")
        assert_no_personal_paths(app)
        app.update()
        capture(app, args.out / "screenshot-create.png")

        app._closing = True
        app.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
