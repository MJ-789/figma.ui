"""测试 FigmaPageIndexer 的纯数据逻辑（不依赖 Figma API）。"""

from pathlib import Path

from src.figma_page_indexer import FigmaPageIndexer
from src.report_writer import ReportWriter

SAMPLE_FILE_DATA = {
    "document": {
        "children": [
            {
                "id": "0:1",
                "name": "Desktop",
                "type": "CANVAS",
                "children": [
                    {
                        "id": "12539:1073",
                        "name": "Homepage",
                        "type": "FRAME",
                        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 1440, "height": 2400},
                        "fills": [
                            {"type": "SOLID", "visible": True, "color": {"r": 1, "g": 1, "b": 1, "a": 1}}
                        ],
                        "children": [
                            {
                                "id": "12539:1074",
                                "name": "Hero/Title",
                                "type": "TEXT",
                                "absoluteBoundingBox": {"x": 120, "y": 80, "width": 600, "height": 60},
                                "fills": [],
                                "characters": "Welcome to Example",
                                "style": {"fontFamily": "Inter", "fontSize": 48, "fontWeight": 700},
                                "children": [],
                            },
                            {
                                "id": "12539:1075",
                                "name": "Hero/Subtitle",
                                "type": "TEXT",
                                "absoluteBoundingBox": {"x": 120, "y": 160, "width": 600, "height": 40},
                                "fills": [],
                                "characters": "Build something amazing",
                                "style": {"fontFamily": "Inter", "fontSize": 20, "fontWeight": 400},
                                "children": [],
                            },
                            {
                                "id": "12539:1076",
                                "name": "Button/CTA",
                                "type": "COMPONENT",
                                "absoluteBoundingBox": {"x": 120, "y": 240, "width": 200, "height": 56},
                                "fills": [
                                    {"type": "SOLID", "visible": True, "color": {"r": 0.31, "g": 0.27, "b": 0.9, "a": 1}}
                                ],
                                "children": [
                                    {
                                        "id": "12539:1077",
                                        "name": "Label",
                                        "type": "TEXT",
                                        "absoluteBoundingBox": {"x": 140, "y": 256, "width": 160, "height": 24},
                                        "fills": [],
                                        "characters": "Get Started",
                                        "style": {"fontFamily": "Inter", "fontSize": 16, "fontWeight": 600},
                                        "children": [],
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "id": "12539:2000",
                        "name": "Pricing",
                        "type": "FRAME",
                        "absoluteBoundingBox": {"x": 1600, "y": 0, "width": 1440, "height": 1800},
                        "fills": [],
                        "children": [
                            {
                                "id": "12539:2001",
                                "name": "Title",
                                "type": "TEXT",
                                "absoluteBoundingBox": {"x": 1720, "y": 80, "width": 400, "height": 50},
                                "fills": [],
                                "characters": "Choose your plan",
                                "style": {"fontFamily": "Roboto", "fontSize": 36, "fontWeight": 700},
                                "children": [],
                            },
                        ],
                    },
                    {
                        "id": "99:1",
                        "name": "tiny-icon",
                        "type": "FRAME",
                        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 24, "height": 24},
                        "fills": [],
                        "children": [],
                    },
                ],
            },
        ],
    },
}


class TestFigmaPageIndexer:
    def test_index_basic(self):
        entries = FigmaPageIndexer.index_from_file_data(SAMPLE_FILE_DATA)
        assert len(entries) == 2, f"should find Homepage + Pricing, got {len(entries)}"

    def test_skips_tiny_frame(self):
        entries = FigmaPageIndexer.index_from_file_data(SAMPLE_FILE_DATA)
        names = [e.frame_name for e in entries]
        assert "tiny-icon" not in names

    def test_homepage_entry(self):
        entries = FigmaPageIndexer.index_from_file_data(SAMPLE_FILE_DATA)
        home = next(e for e in entries if e.frame_name == "Homepage")

        assert home.figma_node_id == "12539:1073"
        assert home.page_name == "Desktop"
        assert home.size == {"width": 1440.0, "height": 2400.0}
        assert "Welcome to Example" in home.text_summary
        assert "Build something amazing" in home.text_summary
        assert "Get Started" in home.text_summary
        assert "Inter" in home.style_summary["font_families"]
        assert home.structure_summary["text_count"] == 3
        assert home.structure_summary["button_hint_count"] >= 1

    def test_pricing_entry(self):
        entries = FigmaPageIndexer.index_from_file_data(SAMPLE_FILE_DATA)
        pricing = next(e for e in entries if e.frame_name == "Pricing")

        assert pricing.figma_node_id == "12539:2000"
        assert "Choose your plan" in pricing.text_summary
        assert "Roboto" in pricing.style_summary["font_families"]

    def test_fingerprint_keys(self):
        entries = FigmaPageIndexer.index_from_file_data(SAMPLE_FILE_DATA)
        for entry in entries:
            assert set(entry.fingerprint.keys()) == {"name_key", "layout_key", "text_key"}

    def test_colors_collected(self):
        entries = FigmaPageIndexer.index_from_file_data(SAMPLE_FILE_DATA)
        home = next(e for e in entries if e.frame_name == "Homepage")
        colors = home.style_summary["primary_colors"]
        assert "#FFFFFF" in colors
        assert any(c.startswith("#4F") or c.startswith("#4E") for c in colors)

    def test_index_from_target_page_node(self):
        entries = FigmaPageIndexer.index_from_file_data(
            SAMPLE_FILE_DATA,
            target_node_id="0:1",
        )
        names = [e.frame_name for e in entries]
        assert names == ["Homepage", "Pricing"]

    def test_index_from_target_frame_node(self):
        entries = FigmaPageIndexer.index_from_file_data(
            SAMPLE_FILE_DATA,
            target_node_id="12539:1073",
        )
        assert len(entries) == 1
        assert entries[0].frame_name == "Homepage"
        assert entries[0].page_name == "Homepage"


class TestFigmaReportWriter:
    def test_write_figma_inventory(self, tmp_path: Path):
        output = tmp_path / "figma_inventory.json"
        result = ReportWriter.write_figma_inventory(
            output_path=output,
            figma_file_key="AbCdEf123",
            generated_at="2026-04-13T10:00:00+00:00",
            summary={"total_pages": 2},
            pages=[{"frame_name": "Homepage", "figma_node_id": "12539:1073"}],
        )

        assert result == output
        content = output.read_text(encoding="utf-8")
        assert '"figma_file_key": "AbCdEf123"' in content
        assert '"total_pages": 2' in content
        assert '"frame_name": "Homepage"' in content
