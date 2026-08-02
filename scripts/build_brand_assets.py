#!/usr/bin/env python3
"""Build the deterministic H2 brand master, previews, and Windows icon.

The geometry constants below are the single editable source. The SVG and every
raster frame are generated from the same coordinates so the vector master and
the optically corrected ICO cannot silently drift apart.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRAND_ROOT = ROOT / "assets" / "branding"

VIEWBOX = 512
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
NAVY = "#14283A"
TEAL = "#19A69A"
WARM_WHITE = "#F7F8F6"
TILE_KEYLINE = "#D3DDDE"

TILE_INSET = 8
TILE_RADIUS = 96
TILE_KEYLINE_WIDTH = 2
FRAME_STROKE = 24
CORE_SIZE = 58
CORE_RADIUS = 7

SMALL_STROKES = {16: 1.25, 20: 1.5, 24: 1.75, 32: 2.25}
SMALL_CORES = {16: 2.0, 20: 2.5, 24: 3.0, 32: 4.0}
SMALL_KEYLINES = {16: 0.5, 20: 0.5, 24: 0.5, 32: 0.75}
SMALL_GAP_BOOST = {16: 16, 20: 14, 24: 12, 32: 8}


def _pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError(
            "Pillow 12.x is required only when rebuilding brand assets. "
            "Install the project with the 'branding' optional dependency."
        ) from exc
    return Image, ImageDraw, ImageFont


def svg_text() -> str:
    """Return the editable, flat H2 vector master."""

    frame_1_path = (
        "M 303 192 V 125 "
        "Q 303 112 290 112 "
        "H 125 "
        "Q 112 112 112 125 "
        "V 270 "
        "Q 112 283 125 283 "
        "H 198"
    )
    frame_2_path = (
        "M 209 320 V 387 "
        "Q 209 400 222 400 "
        "H 387 "
        "Q 400 400 400 387 "
        "V 242 "
        "Q 400 229 387 229 "
        "H 314"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEWBOX} {VIEWBOX}" role="img" aria-labelledby="title desc">
  <title id="title">AI Project Factory H2 Transfer Frame</title>
  <desc id="desc">One portable project core continuing between two open agent frames.</desc>
  <rect x="{TILE_INSET}" y="{TILE_INSET}" width="{VIEWBOX - 2 * TILE_INSET}" height="{VIEWBOX - 2 * TILE_INSET}" rx="{TILE_RADIUS}" fill="{WARM_WHITE}" stroke="{NAVY}" stroke-opacity=".78" stroke-width="{TILE_KEYLINE_WIDTH}"/>
  <path d="{frame_1_path}" fill="none" stroke="{NAVY}" stroke-width="{FRAME_STROKE}" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="{frame_2_path}" fill="none" stroke="{NAVY}" stroke-width="{FRAME_STROKE}" stroke-linecap="round" stroke-linejoin="round"/>
  <rect x="{(VIEWBOX - CORE_SIZE) // 2}" y="{(VIEWBOX - CORE_SIZE) // 2}" width="{CORE_SIZE}" height="{CORE_SIZE}" rx="{CORE_RADIUS}" fill="{TEAL}"/>
</svg>
"""


def _scaled(point: tuple[float, float], scale: float) -> tuple[int, int]:
    return (round(point[0] * scale), round(point[1] * scale))


def _draw_round_cap(draw: Any, point: tuple[int, int], width: int, fill: str) -> None:
    radius = width / 2
    x, y = point
    draw.ellipse(
        (
            round(x - radius),
            round(y - radius),
            round(x + radius),
            round(y + radius),
        ),
        fill=fill,
    )


def _quadratic_points(
    start: tuple[float, float],
    control: tuple[float, float],
    end: tuple[float, float],
    *,
    steps: int = 10,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for index in range(1, steps + 1):
        t = index / steps
        inverse = 1.0 - t
        points.append(
            (
                inverse * inverse * start[0]
                + 2 * inverse * t * control[0]
                + t * t * end[0],
                inverse * inverse * start[1]
                + 2 * inverse * t * control[1]
                + t * t * end[1],
            )
        )
    return points


def _frame_points(
    gap_boost: float = 0,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    frame_1: list[tuple[float, float]] = [
        (303, 192 - gap_boost),
        (303, 125),
    ]
    frame_1 += _quadratic_points((303, 125), (303, 112), (290, 112))
    frame_1.append((125, 112))
    frame_1 += _quadratic_points((125, 112), (112, 112), (112, 125))
    frame_1.append((112, 270))
    frame_1 += _quadratic_points((112, 270), (112, 283), (125, 283))
    frame_1.append((198 - gap_boost, 283))

    frame_2: list[tuple[float, float]] = [
        (209, 320 + gap_boost),
        (209, 387),
    ]
    frame_2 += _quadratic_points((209, 387), (209, 400), (222, 400))
    frame_2.append((387, 400))
    frame_2 += _quadratic_points((387, 400), (400, 400), (400, 387))
    frame_2.append((400, 242))
    frame_2 += _quadratic_points((400, 242), (400, 229), (387, 229))
    frame_2.append((314 + gap_boost, 229))
    return frame_1, frame_2


def _draw_frames(
    draw: Any,
    scale: float,
    width: int,
    gap_boost: float = 0,
) -> None:
    for points in _frame_points(gap_boost):
        scaled = [_scaled(point, scale) for point in points]
        draw.line(scaled, fill=NAVY, width=width, joint="curve")
        _draw_round_cap(draw, scaled[0], width, NAVY)
        _draw_round_cap(draw, scaled[-1], width, NAVY)


def render_icon(size: int):
    """Render one optically corrected icon frame at an exact pixel size."""

    if size < 8 or size > 1024:
        raise ValueError("Icon size must be between 8 and 1024 pixels.")
    Image, ImageDraw, _ = _pillow()
    supersample = 8 if size <= 64 else 4
    high_size = size * supersample
    scale = high_size / VIEWBOX
    image = Image.new("RGBA", (high_size, high_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    tile_inset_px = max(TILE_INSET * size / VIEWBOX, 0.25) * supersample
    tile_radius_px = TILE_RADIUS * scale
    keyline_target = SMALL_KEYLINES.get(
        size,
        max(TILE_KEYLINE_WIDTH * size / VIEWBOX, 0.75),
    )
    keyline_px = keyline_target * supersample
    draw.rounded_rectangle(
        (
            round(tile_inset_px),
            round(tile_inset_px),
            round(high_size - tile_inset_px),
            round(high_size - tile_inset_px),
        ),
        radius=round(tile_radius_px),
        fill=WARM_WHITE,
        outline=(20, 40, 58, 199),
        width=max(1, round(keyline_px)),
    )

    target_stroke_px = SMALL_STROKES.get(
        size,
        FRAME_STROKE * size / VIEWBOX,
    )
    frame_width = max(1, round(target_stroke_px * supersample))
    _draw_frames(
        draw,
        scale,
        frame_width,
        SMALL_GAP_BOOST.get(size, 0),
    )

    target_core_px = SMALL_CORES.get(
        size,
        CORE_SIZE * size / VIEWBOX,
    )
    core_radius_px = max(CORE_RADIUS * size / VIEWBOX, 0.55)
    center = high_size / 2
    half_core = target_core_px * supersample / 2
    draw.rounded_rectangle(
        (
            round(center - half_core),
            round(center - half_core),
            round(center + half_core),
            round(center + half_core),
        ),
        radius=max(1, round(core_radius_px * supersample)),
        fill=TEAL,
    )
    return image.resize((size, size), Image.Resampling.LANCZOS)


def _atomic_write(path: Path, content: bytes) -> None:
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


def _image_bytes(image: Any, format_name: str) -> bytes:
    from io import BytesIO

    stream = BytesIO()
    image.save(stream, format=format_name, optimize=True, compress_level=9)
    return stream.getvalue()


def _ico_bytes(frames: dict[int, Any]) -> bytes:
    from io import BytesIO

    stream = BytesIO()
    frames[256].save(
        stream,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=[frames[size] for size in ICON_SIZES if size != 256],
        bitmap_format="png",
    )
    return stream.getvalue()


def _qa_sheet(frames: dict[int, Any]):
    Image, ImageDraw, ImageFont = _pillow()
    sheet = Image.new("RGB", (1500, 850), "#E9EEED")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    draw.text((48, 35), "AI PROJECT FACTORY / H2 TRANSFER FRAME", fill=NAVY, font=font)
    draw.text(
        (48, 55),
        "FINAL VECTOR GEOMETRY - LIGHT / DARK / ACTUAL PIXEL FRAMES",
        fill="#52616B",
        font=font,
    )

    large = render_icon(512)
    large.thumbnail((520, 520), Image.Resampling.LANCZOS)
    sheet.paste(large, (50, 115), large)

    panels = (("#F4F6F5", "LIGHT"), ("#17212B", "DARK"))
    for panel_index, (background, label) in enumerate(panels):
        x0 = 620
        y0 = 105 + panel_index * 350
        draw.rounded_rectangle(
            (x0, y0, 1450, y0 + 315),
            radius=28,
            fill=background,
            outline="#BBC8C9",
            width=2,
        )
        draw.text(
            (x0 + 24, y0 + 20),
            label,
            fill=WARM_WHITE if label == "DARK" else NAVY,
            font=font,
        )
        x = x0 + 30
        for size in ICON_SIZES:
            frame = frames[size]
            preview = frame.resize(
                (72, 72),
                Image.Resampling.NEAREST,
            )
            sheet.paste(preview, (x, y0 + 70), preview)
            draw.text(
                (x, y0 + 260),
                f"{size}px",
                fill=WARM_WHITE if label == "DARK" else NAVY,
                font=font,
            )
            x += 86
    return sheet


def build_assets(brand_root: Path = DEFAULT_BRAND_ROOT) -> dict[str, str]:
    brand_root = brand_root.expanduser().resolve()
    master_dir = brand_root / "master"
    preview_dir = brand_root / "previews"
    desktop_dir = brand_root / "desktop"
    svg_path = master_dir / "ai-project-factory-h2.svg"
    png_path = master_dir / "ai-project-factory-h2-512.png"
    preview_path = preview_dir / "ai-project-factory-h2-qa.png"
    ico_path = desktop_dir / "ai-project-factory.ico"

    frames = {size: render_icon(size) for size in ICON_SIZES}
    master = render_icon(512)
    outputs = {
        svg_path: svg_text().encode("utf-8"),
        png_path: _image_bytes(master, "PNG"),
        preview_path: _image_bytes(_qa_sheet(frames), "PNG"),
        ico_path: _ico_bytes(frames),
    }
    for path, content in outputs.items():
        _atomic_write(path, content)
    return {
        str(path): hashlib.sha256(content).hexdigest()
        for path, content in outputs.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build final H2 SVG, PNG, QA preview, and Windows ICO."
    )
    parser.add_argument(
        "--brand-root",
        type=Path,
        default=DEFAULT_BRAND_ROOT,
    )
    args = parser.parse_args(argv)
    try:
        hashes = build_assets(args.brand_root)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
