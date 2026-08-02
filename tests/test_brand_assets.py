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
                self.assertEqual(first, second, str(relative))
                self.assertEqual(first, (checked_in / relative).read_bytes())

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
