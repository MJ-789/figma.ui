"""测试 test_plan_builder 和 run_orchestrator 的纯数据逻辑。"""

from src.test_plan_builder import TestPlanBuilder, TestPlanItem
from src.report_writer import ReportWriter

MOCK_PAIRS_RESULT = {
    "generated_at": "2025-01-01T00:00:00+00:00",
    "pairs": [
        {
            "figma_page_id": "figma::100:1",
            "figma_node_id": "100:1",
            "figma_name": "Homepage",
            "site_page_id": "site::aaa",
            "site_url": "https://example.com/",
            "site_path": "/",
            "match_method": "rules",
            "confidence": 0.85,
            "scores": {"name_score": 0.4, "text_score": 0.8, "structure_score": 0.6},
            "reason": "文本重叠，结构接近",
            "status": "matched",
        },
        {
            "figma_page_id": "figma::200:1",
            "figma_node_id": "200:1",
            "figma_name": "Pricing",
            "site_page_id": "site::bbb",
            "site_url": "https://example.com/pricing",
            "site_path": "/pricing",
            "match_method": "rules",
            "confidence": 0.72,
            "scores": {"name_score": 0.9, "text_score": 0.5, "structure_score": 0.4},
            "reason": "名称相似，文本重叠",
            "status": "matched",
        },
        {
            "figma_page_id": "figma::300:1",
            "figma_node_id": "300:1",
            "figma_name": "Contact",
            "site_page_id": "site::ccc",
            "site_url": "https://example.com/contact",
            "site_path": "/contact",
            "match_method": "rules",
            "confidence": 0.55,
            "scores": {"name_score": 0.7, "text_score": 0.3, "structure_score": 0.2},
            "reason": "名称相似",
            "status": "matched",
        },
    ],
    "summary": {
        "total_figma": 3,
        "total_site": 3,
        "matched": 3,
    },
}


class TestTestPlanBuilder:
    def test_build_returns_correct_count(self):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        assert result["summary"]["total_items"] == 3

    def test_plan_items_have_required_fields(self):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        for item in result["items"]:
            assert "plan_id" in item
            assert "figma_node_id" in item
            assert "site_url" in item
            assert "test_type" in item
            assert "viewport" in item
            assert "hide_selectors" in item
            assert "priority" in item

    def test_all_items_use_page_screenshot_compare(self):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        for item in result["items"]:
            assert item["test_type"] == "page_screenshot_compare"

    def test_priority_assignment(self):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        priorities = {item["figma_name"]: item["priority"] for item in result["items"]}
        assert priorities["Homepage"] == "high"
        assert priorities["Pricing"] == "high"
        assert priorities["Contact"] == "medium"

    def test_plan_id_format(self):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        ids = [item["plan_id"] for item in result["items"]]
        assert ids == ["plan::001", "plan::002", "plan::003"]

    def test_custom_viewport(self):
        vp = {"width": 1920, "height": 1080}
        builder = TestPlanBuilder(viewport=vp)
        result = builder.build(MOCK_PAIRS_RESULT)
        for item in result["items"]:
            assert item["viewport"] == vp

    def test_empty_pairs(self):
        builder = TestPlanBuilder()
        result = builder.build({"pairs": []})
        assert result["summary"]["total_items"] == 0
        assert result["items"] == []

    def test_by_type_summary(self):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        assert result["summary"]["by_type"]["page_screenshot_compare"] == 3

    def test_by_priority_summary(self):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        bp = result["summary"]["by_priority"]
        assert bp["high"] == 2
        assert bp["medium"] == 1
        assert bp["low"] == 0


class TestPlanSaveToJson:
    def test_save_creates_json(self, tmp_path):
        builder = TestPlanBuilder()
        result = builder.build(MOCK_PAIRS_RESULT)
        output = tmp_path / "test_plan.json"
        ReportWriter._write_json(output, result)
        assert output.exists()

        import json
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["summary"]["total_items"] == 3
        assert len(data["items"]) == 3
