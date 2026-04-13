from pathlib import Path

from src.report_writer import ReportWriter
from src.site_discovery import SiteDiscovery


class TestSiteDiscovery:
    def test_build_page_record(self, tmp_path: Path):
        crawl_item = {
            "url": "https://example.com/pricing",
            "depth": 1,
            "from": "https://example.com",
            "status": "ok",
        }
        page_snapshot = {
            "title": "Pricing   Page",
            "headings": ["Pricing", "Choose your plan", "Pricing"],
            "buttons": ["Start free trial", "Contact sales"],
            "heading_count": 3,
            "button_count": 2,
            "link_count": 7,
            "form_count": 0,
            "image_count": 4,
        }

        record = SiteDiscovery.build_page_record(
            crawl_item=crawl_item,
            page_snapshot=page_snapshot,
            screenshot_path=tmp_path / "pricing.png",
        )

        assert record.path == "/pricing"
        assert record.title == "Pricing Page"
        assert record.depth == 1
        assert record.dom_summary["heading_count"] == 3
        assert record.dom_summary["button_count"] == 2
        assert record.text_summary[:3] == [
            "Pricing Page",
            "Pricing",
            "Choose your plan",
        ]
        assert record.screenshot_path.endswith("pricing.png")
        assert record.page_id.startswith("site::")
        assert set(record.fingerprint.keys()) == {"path_key", "layout_key", "text_key"}

    def test_slug_from_url(self):
        assert SiteDiscovery._slug_from_url("https://example.com/") == "home"
        assert SiteDiscovery._slug_from_url("https://example.com/pricing") == "pricing"
        assert SiteDiscovery._slug_from_url("https://example.com/products/sku-001") == "products_sku_001"


class TestReportWriter:
    def test_write_site_inventory(self, tmp_path: Path):
        output = tmp_path / "site_inventory.json"
        result = ReportWriter.write_site_inventory(
            output_path=output,
            base_url="https://example.com",
            generated_at="2026-04-13T10:00:00+00:00",
            summary={"inventory_pages": 2},
            pages=[{"url": "https://example.com/", "title": "Home"}],
        )

        assert result == output
        content = output.read_text(encoding="utf-8")
        assert '"base_url": "https://example.com"' in content
        assert '"inventory_pages": 2' in content
        assert '"title": "Home"' in content
