"""
src/report_writer.py  ── 结构化 JSON 报告输出（v1.2.0）
================================================
职责：
    把每次测试运行的元信息 + 各页面结果汇总成一个 JSON 文件，
    方便后续分析、CI 集成或可视化展示。

输出路径：
    reports/json/run_result.json        ── 像素对比 / 爬取结果（历史兼容）
    reports/json/element_diff.json      ── 结构化属性级 diff（v1.2.0 新增）

element_diff.json 结构（write_element_diff_report）：
    {
      "version": "1.2.x",
      "generated_at": "ISO 8601",
      "base_url": "https://...",
      "page_name": "homepage_chromium",
      "browser": "chromium",
      "figma_node": "12539:1073",
      "compare_config": {
        "color_tolerance": 5,          # 颜色每通道容差（0~255）
        "size_tolerance": 4,           # 尺寸/位置容差（px）
        "font_size_tolerance": 1,      # 字号容差（px）
        "radius_tolerance": 2,         # 圆角容差（px）
        "element_threshold": 0.85,     # 单元素属性通过率阈值
        "auto_mapped_count": 8,        # AutoMapper 自动推导的映射数
        "manual_mapped_count": 0       # Config element_map 手工补充的映射数
      },
      "result": {                      # ElementCompare.compare() 完整输出
        "total_matched": 8,
        "total_unmatched": 4,
        "overall_score": 0.91,         # 仅已匹配节点的平均属性通过率
        "coverage_rate": 0.67,         # 已匹配节点 / 总节点数
        "overall_passed": true,
        "threshold": 0.85,
        "min_coverage": 0.10,
        "elements": [ ... ]
      }
    }

兼容性说明：
    Python 3.10 无 datetime.UTC，已用 try/except 兼容 3.10 和 3.11+。
    所有数值字段已确保为原生 float/bool（不是 numpy 类型），可直接 json.dump。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Python 3.11+ has datetime.UTC; 3.10 需用 timezone.utc
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone

    UTC = timezone.utc


class ReportWriter:

    @staticmethod
    def _write_json(output_path: Path, payload: Dict[str, Any]) -> Path:
        """统一写入 JSON，避免各类报告重复实现落盘逻辑。"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return output_path

    # ------------------------------------------------
    # vNext  自动测试代理：站点发现报告
    # ------------------------------------------------

    @staticmethod
    def write_site_inventory(
        output_path: Path,
        base_url: str,
        summary: Dict[str, Any],
        pages: List[Dict[str, Any]],
        generated_at: str | None = None,
    ) -> Path:
        """
        写入自动测试代理第一阶段的站点发现报告（site_inventory.json）。

        Args:
            output_path:   输出路径（通常为 reports/json/site_inventory.json）
            base_url:      被发现的网站根地址
            summary:       站点发现概要（页数、深度、种子路径等）
            pages:         结构化页面清单
            generated_at:  可选，外部已生成的时间戳；为空时自动补当前时间
        """
        payload = {
            "base_url": base_url,
            "generated_at": generated_at or datetime.now(UTC).isoformat(),
            "summary": summary,
            "pages": pages,
        }
        return ReportWriter._write_json(output_path, payload)

    # ------------------------------------------------
    # vNext  自动测试代理：页面配对报告
    # ------------------------------------------------

    @staticmethod
    def write_page_pairs(
        output_path: Path,
        payload: Dict[str, Any],
    ) -> Path:
        """写入页面配对结果（page_pairs.json）。"""
        return ReportWriter._write_json(output_path, payload)

    # ------------------------------------------------
    # vNext  自动测试代理：Figma 设计稿索引报告
    # ------------------------------------------------

    @staticmethod
    def write_figma_inventory(
        output_path: Path,
        figma_file_key: str,
        summary: Dict[str, Any],
        pages: List[Dict[str, Any]],
        generated_at: str | None = None,
    ) -> Path:
        """
        写入 Figma 设计稿页面索引报告（figma_inventory.json）。

        Args:
            output_path:     输出路径（通常为 reports/json/figma_inventory.json）
            figma_file_key:  Figma 文件唯一标识
            summary:         索引概要（页面数等）
            pages:           结构化 Frame 清单
            generated_at:    可选时间戳
        """
        payload = {
            "figma_file_key": figma_file_key,
            "generated_at": generated_at or datetime.now(UTC).isoformat(),
            "summary": summary,
            "pages": pages,
        }
        return ReportWriter._write_json(output_path, payload)

    # ------------------------------------------------
    # v1.2.0  属性级 diff 报告
    # ------------------------------------------------

    @staticmethod
    def write_element_diff_report(
        output_path: Path,
        version: str,
        base_url: str,
        page_name: str,
        browser: str,
        figma_node: str,
        compare_config: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Path:
        """
        写入元素属性级对比报告（element_diff.json）。

        Args:
            output_path:    输出文件路径（通常为 reports/json/element_diff.json）
            version:        项目版本号
            base_url:       被测网站域名
            page_name:      测试名称（如 "homepage_chromium"）
            browser:        浏览器类型
            figma_node:     对比使用的 Figma 节点 ID
            compare_config: 对比参数字典（容差、阈值等）
            result:         ElementCompare.compare() 的返回值

        Returns:
            写入完成的文件路径
        """
        payload = {
            "version": version,
            "generated_at": datetime.now(UTC).isoformat(),
            "base_url": base_url,
            "page_name": page_name,
            "browser": browser,
            "figma_node": figma_node,
            "compare_config": compare_config,
            "result": result,
        }

        return ReportWriter._write_json(output_path, payload)

    # ------------------------------------------------
    # v1.1.0  像素对比 / 爬取结果报告（保留）
    # ------------------------------------------------

    @staticmethod
    def write_run_result(
        output_path: Path,
        version: str,
        base_url: str,
        crawl_summary: Dict[str, Any],
        page_results: List[Dict[str, Any]],
    ) -> Path:
        """
        写入像素对比 / 页面爬取结果报告（run_result.json）。

        Args:
            output_path:    输出文件路径（通常为 reports/json/run_result.json）
            version:        项目版本号（读自 VERSION 文件）
            base_url:       被测网站域名
            crawl_summary:  爬取概要（enabled、max_depth、discovered_pages 等）
            page_results:   各页面结果列表（像素相似度 / 爬取状态）

        Returns:
            写入完成的文件路径
        """
        payload = {
            "version": version,
            "generated_at": datetime.now(UTC).isoformat(),
            "base_url": base_url,
            "crawl_summary": crawl_summary,
            "page_results": page_results,
        }

        return ReportWriter._write_json(output_path, payload)
