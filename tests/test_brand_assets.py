from __future__ import annotations

import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_brand_assets.py"
SPEC = importlib.util.spec_from_file_location("_factory_brand_builder", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
brand_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brand_builder)

try:
    from PIL import Image
except ImportError:
    Image = None


class BrandAssetTests(unittest.TestCase):
    def test_svg_master_is_flat_h2_geometry(self) -> None:
        text = brand_builder.svg_text()
        root = ET.fromstring(text)
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        paths = root.findall("svg:path", namespace)
        rects = root.findall("svg:rect", namespace)
        self.assertEqual(root.attrib["viewBox"], "0 0 512 512")
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(rects), 2)
        self.assertEqual(
            {path.attrib["stroke"] for path in paths},
            {brand_builder.NAVY},
        )
        self.assertEqual(rects[-1].attrib["fill"], brand_builder.TEAL)
        lowered = text.casefold()
        for forbidden in ("lineargradient", "radialgradient", "<filter", "<text"):
            self.assertNotIn(forbidden, lowered)

    @unittest.skipIf(Image is None, "Pillow branding dependency is unavailable")
    def test_brand_build_is_deterministic_and_matches_checked_in_assets(self) -> None:
        """Two separate claims, which need to be checked two different ways.

        *Determinism* -- building twice gives identical bytes -- is a real
        invariant and holds on every platform, so it is asserted on the raw
        bytes.

        *Matching what is committed* is not a byte-level property for the
        raster formats. PNG and ICO embed zlib output, and zlib's exact
        encoding varies between the versions Pillow is built against, so the
        same source renders to different bytes on macOS than on Linux while
        depicting exactly the same image. Comparing bytes there asserts the
        build machine, not the artwork. The rasters are therefore compared by
        decoded pixels, which is the thing that actually has to stay true;
        the SVG is text and is compared exactly.
        """
        with tempfile.TemporaryDirectory() as first_temp, tempfile.TemporaryDirectory() as second_temp:
            first_root = Path(first_temp) / "branding"
            second_root = Path(second_temp) / "branding"
            brand_builder.build_assets(first_root)
            brand_builder.build_assets(second_root)
            relative_paths = (
                Path("master/ai-project-factory-h2.svg"),
                Path("master/ai-project-factory-h2-512.png"),
                Path("previews/ai-project-factory-h2-qa.png"),
                Path("desktop/ai-project-factory.ico"),
            )
            checked_in = ROOT / "assets" / "branding"
            for relative in relative_paths:
                first = (first_root / relative).read_bytes()
                second = (second_root / relative).read_bytes()
                self.assertEqual(first, second, f"not deterministic: {relative}")

                committed = checked_in / relative
                if relative.suffix == ".svg":
                    self.assertEqual(first, committed.read_bytes(), str(relative))
                else:
                    self._assert_same_image(first_root / relative, committed)

    def _assert_same_image(self, built: Path, committed: Path) -> None:
        """Compare two raster files by what they depict, not by their bytes."""
        with Image.open(built) as a, Image.open(committed) as b:
            frames_a = sorted(a.ico.sizes()) if built.suffix == ".ico" else [a.size]
            frames_b = sorted(b.ico.sizes()) if committed.suffix == ".ico" else [b.size]
            self.assertEqual(frames_a, frames_b, f"frame sizes differ: {built.name}")

            if built.suffix == ".ico":
                for size in frames_a:
                    a.size = size
                    b.size = size
                    self.assertEqual(
                        a.convert("RGBA").tobytes(),
                        b.convert("RGBA").tobytes(),
                        f"{built.name} differs at {size[0]}px",
                    )
            else:
                self.assertEqual(
                    a.convert("RGBA").tobytes(),
                    b.convert("RGBA").tobytes(),
                    f"{built.name} pixels differ from the committed asset",
                )

    @unittest.skipIf(Image is None, "Pillow branding dependency is unavailable")
    def test_ico_uses_optically_corrected_exact_frames(self) -> None:
        icon = (
            ROOT
            / "assets"
            / "branding"
            / "desktop"
            / "ai-project-factory.ico"
        )
        with Image.open(icon) as opened:
            self.assertEqual(
                opened.ico.sizes(),
                {(size, size) for size in brand_builder.ICON_SIZES},
            )
            opened.size = (16, 16)
            frame = opened.convert("RGBA")
        self.assertEqual(frame.getpixel((0, 0))[3], 0)
        center = frame.getpixel((8, 8))
        self.assertGreater(center[1], center[0])
        self.assertGreater(center[1], center[2])
        self.assertGreaterEqual(center[3], 240)


if __name__ == "__main__":
    unittest.main()
