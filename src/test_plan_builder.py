"""
src/test_plan_builder.py  ── 测试计划生成器
=====================================================
职责：
    接收 page_pairs.json 的配对结果，为每对页面生成一条测试计划。
    版本1 统一使用"整页截图对比"策略。
    后续版本会根据页面特征自动选择截图 / 元素 / 混合对比。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List

from config.config import Config
from src.report_writer import ReportWriter

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


@dataclass
class TestPlanItem:
    """一条测试计划。"""

    plan_id: str
    figma_page_id: str
    figma_node_id: str
    figma_name: str
    site_url: str
    site_path: str
    test_type: str
    viewport: Dict[str, int]
    hide_selectors: List[str]
    confidence: float
    priority: str


class TestPlanBuilder:
    """从配对结果生成测试计划。"""

    def __init__(
        self,
        viewport: Dict[str, int] | None = None,
        hide_selectors: List[str] | None = None,
    ):
        self.viewport = viewport or {
            "width": Config.AGENT_VIEWPORT_WIDTH,
            "height": Config.AGENT_VIEWPORT_HEIGHT,
        }
        self.hide_selectors = hide_selectors or Config.AGENT_HIDE_SELECTORS

    def build(self, page_pairs_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        从 page_pairs.json 的完整结果生成测试计划。

        Args:
            page_pairs_result: PageMatcher.match() 的返回值

        Returns:
            完整测试计划 dict（和 test_plan.json 结构一致）
        """
        pairs = page_pairs_result.get("pairs", [])
        items: List[TestPlanItem] = []

        for idx, pair in enumerate(pairs):
            confidence = float(pair.get("confidence", 0))
            if confidence >= 0.7:
                priority = "high"
            elif confidence >= 0.5:
                priority = "medium"
            else:
                priority = "low"

            items.append(TestPlanItem(
                plan_id=f"plan::{idx + 1:03d}",
                figma_page_id=pair.get("figma_page_id", ""),
                figma_node_id=pair.get("figma_node_id", ""),
                figma_name=pair.get("figma_name", ""),
                site_url=pair.get("site_url", ""),
                site_path=pair.get("site_path", ""),
                test_type="page_screenshot_compare",
                viewport=self.viewport,
                hide_selectors=self.hide_selectors,
                confidence=confidence,
                priority=priority,
            ))

        generated_at = datetime.now(UTC).isoformat()

        type_counts: Dict[str, int] = {}
        for item in items:
            type_counts[item.test_type] = type_counts.get(item.test_type, 0) + 1

        payload = {
            "generated_at": generated_at,
            "items": [asdict(item) for item in items],
            "summary": {
                "total_items": len(items),
                "by_type": type_counts,
                "by_priority": {
                    "high": sum(1 for i in items if i.priority == "high"),
                    "medium": sum(1 for i in items if i.priority == "medium"),
                    "low": sum(1 for i in items if i.priority == "low"),
                },
            },
        }

        return payload

    def build_and_save(self, page_pairs_result: Dict[str, Any]) -> Dict[str, Any]:
        """生成计划并写入 test_plan.json。"""
        result = self.build(page_pairs_result)
        Config.setup_directories()
        ReportWriter._write_json(Config.TEST_PLAN_PATH, result)
        return result
