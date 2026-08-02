#!/usr/bin/env python3
"""Build a multi-resolution Windows ICO from an approved square PNG."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def build_icon(source: Path, output: Path, *, force: bool = False) -> Path:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required only when rebuilding the desktop icon."
        ) from exc

    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Source PNG does not exist: {source}")
    if output.exists() and not force:
        raise FileExistsError(f"Refusing to overwrite existing icon: {output}")
    if output.suffix.lower() != ".ico":
        raise ValueError("Output must use the .ico extension.")

    with Image.open(source) as opened:
        image = opened.convert("RGBA")
    if image.width != image.height:
        raise ValueError("Desktop icon source must be square.")
    if min(image.getpixel(point)[3] for point in (
        (0, 0),
        (image.width - 1, 0),
        (0, image.height - 1),
        (image.width - 1, image.height - 1),
    )) > 16:
        raise ValueError(
            "Desktop icon source must have transparent outer corners."
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    handle, raw_temp = tempfile.mkstemp(
        prefix=f".{output.stem}.",
        suffix=".ico",
        dir=str(output.parent),
    )
    os.close(handle)
    temp = Path(raw_temp)
    try:
        image.save(
            temp,
            format="ICO",
            sizes=[(size, size) for size in ICON_SIZES],
            bitmap_format="png",
        )
        if temp.stat().st_size < 1024:
            raise RuntimeError("Generated ICO is unexpectedly small.")
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the multi-resolution Factory desktop icon."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "assets"
            / "branding"
            / "desktop"
            / "ai-project-factory.ico"
        ),
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        output = build_icon(args.source, args.output, force=args.force)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
