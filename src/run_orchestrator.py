"""
src/run_orchestrator.py  ── 自动测试代理：全流程编排器
=====================================================
职责：
    把"发现 → 索引 → 配对 → 计划 → 执行 → 报告"六步串成一键流程。
    每一步的中间结果都落盘为 JSON，方便排查和复用。

使用：
    python run_agent.py          # 完整流程
    python run_agent.py --dry    # 只做发现+配对+计划，不执行截图对比
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.config import Config

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


class RunOrchestrator:
    """自动测试代理全流程编排。"""

    def __init__(self, dry_run: bool = False, reuse_inventory: bool = True):
        self.dry_run = dry_run
        self.reuse_inventory = reuse_inventory
        self.version = self._read_version()

    @staticmethod
    def _read_version() -> str:
        version_file = Config.BASE_DIR / "VERSION"
        if version_file.exists():
            return version_file.read_text(encoding="utf-8").strip()
        return "dev"

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
        return slug or "page"

    def _load_or_run_site_discovery(self) -> Dict[str, Any]:
        cache = Config.SITE_INVENTORY_PATH
        if self.reuse_inventory and cache.exists():
            print("      (复用已有 site_inventory.json)")
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        from src.site_discovery import SiteDiscovery
        return SiteDiscovery().discover(write_report=True)

    def _load_or_run_figma_index(self) -> Dict[str, Any]:
        cache = Config.FIGMA_INVENTORY_PATH
        if self.reuse_inventory and cache.exists():
            print("      (复用已有 figma_inventory.json)")
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        from src.figma_page_indexer import FigmaPageIndexer
        return FigmaPageIndexer.index(write_report=True)

    def _clean_previous_reports(self):
        """清理上次运行产出的报告和截图，确保结果不残留。

        所有截图统一放进 reports/agent_run/（自包含），
        顺手删掉历史版本留下的空目录，避免 reports/ 下冒出无用文件夹。
        """
        import shutil

        agent_run_dir = Config.REPORTS_DIR / "agent_run"
        if agent_run_dir.exists():
            shutil.rmtree(agent_run_dir, ignore_errors=True)
        agent_run_dir.mkdir(parents=True, exist_ok=True)

        legacy_dirs = [
            Config.REPORTS_DIR / "images",
            Config.SCREENSHOTS_DIR / "figma",
            Config.SCREENSHOTS_DIR / "web",
            Config.SCREENSHOTS_DIR / "site",
            Config.SCREENSHOTS_DIR,
        ]
        for d in legacy_dirs:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)

        json_dir = Config.REPORTS_DIR / "json"
        if json_dir.exists():
            inventory_names = {"site_inventory.json", "figma_inventory.json"}
            for f in json_dir.iterdir():
                if f.is_file() and f.name not in inventory_names:
                    f.unlink()

    def run(self) -> Dict[str, Any]:
        """执行完整流程，返回汇总结果。"""
        self._clean_previous_reports()
        Config.setup_directories()
        results: Dict[str, Any] = {"steps": {}}

        # ── Step 1: 发现网站页面 ─────────────────────────
        print(f"\n{'=' * 60}")
        print("[1/6] 发现网站页面...")
        print(f"{'=' * 60}")

        site_inv = self._load_or_run_site_discovery()
        site_pages = site_inv["pages"]
        print(f"      发现 {len(site_pages)} 个页面")
        results["steps"]["site_discovery"] = {
            "status": "ok",
            "page_count": len(site_pages),
        }

        # ── Step 2: 索引 Figma 设计稿 ───────────────────
        print(f"\n{'=' * 60}")
        print("[2/6] 索引 Figma 设计稿...")
        print(f"{'=' * 60}")

        figma_inv = self._load_or_run_figma_index()
        figma_pages = figma_inv["pages"]
        print(f"      索引到 {len(figma_pages)} 个设计页面")
        results["steps"]["figma_index"] = {
            "status": "ok",
            "page_count": len(figma_pages),
        }

        # ── Step 3: 自动页面配对 ─────────────────────────
        print(f"\n{'=' * 60}")
        print("[3/6] 自动页面配对...")
        print(f"{'=' * 60}")

        from src.page_matcher import PageMatcher

        matcher = PageMatcher()
        pairs_result = matcher.match_and_save(figma_pages, site_pages)
        pairs = pairs_result["pairs"]
        summary = pairs_result["summary"]
        print(f"      配对成功 {summary['matched']} 对，"
              f"未配对 Figma {summary['unmatched_figma']} 个，"
              f"未配对网站 {summary['unmatched_site']} 个")
        for p in pairs:
            print(f"        {p['figma_name']!r} → {p['site_path']!r}  "
                  f"(置信度 {p['confidence']:.2f})")
        results["steps"]["page_match"] = {
            "status": "ok",
            "matched": summary["matched"],
        }

        # ── Step 4: 生成测试计划 ─────────────────────────
        print(f"\n{'=' * 60}")
        print("[4/6] 生成测试计划...")
        print(f"{'=' * 60}")

        from src.test_plan_builder import TestPlanBuilder

        builder = TestPlanBuilder()
        plan = builder.build_and_save(pairs_result)
        items = plan["items"]
        print(f"      生成 {len(items)} 条测试计划")
        for item in items:
            print(f"        [{item['priority'].upper()}] {item['figma_name']!r} "
                  f"vs {item['site_path']!r}")
        results["steps"]["test_plan"] = {
            "status": "ok",
            "item_count": len(items),
        }

        if self.dry_run:
            print(f"\n{'=' * 60}")
            print("[DRY RUN] 跳过截图对比和报告生成。")
            print(f"{'=' * 60}")
            results["steps"]["execute"] = {"status": "skipped_dry_run"}
            results["steps"]["report"] = {"status": "skipped_dry_run"}
            return results

        # ── Step 5: 执行测试计划 ─────────────────────────
        print(f"\n{'=' * 60}")
        print("[5/6] 执行截图对比...")
        print(f"{'=' * 60}")

        from src.figma_client import FigmaClient
        from src.web_capture import WebCapture
        from src.image_compare import ImageCompare
        from src.figma_extractor import FigmaExtractor
        from src.dom_extractor import DOMExtractor
        from src.auto_mapper import AutoMapper
        from src.element_compare import ElementCompare

        figma_client = FigmaClient()
        comparator = ImageCompare(threshold=Config.SIMILARITY_THRESHOLD)
        figma_extractor = FigmaExtractor()
        dom_extractor = DOMExtractor()
        auto_mapper = AutoMapper()
        elem_compare = ElementCompare()
        # 噪音层名过滤：自动生成的 Frame/Group/Image 编号命名无对应 DOM 元素
        _noise_re = re.compile(r"^(image\s+\d+|frame\s*\d*|group\s*\d*)$", re.IGNORECASE)
        page_results: List[Dict[str, Any]] = []

        with WebCapture(
            browser_type=Config.DEFAULT_BROWSER,
            headless=Config.HEADLESS,
            viewport={
                "width": Config.AGENT_VIEWPORT_WIDTH,
                "height": Config.AGENT_VIEWPORT_HEIGHT,
            },
        ) as capture:
            # Every agent-run artifact lives in a single self-contained
            # folder so it can be zipped / sent without the old
            # screenshots/images subdir jungle.
            agent_run_dir = Config.REPORTS_DIR / "agent_run"
            agent_run_dir.mkdir(parents=True, exist_ok=True)

            for item in items:
                name_slug = self._slug(item["figma_name"])
                figma_path = agent_run_dir / f"{name_slug}_figma.png"
                web_path = agent_run_dir / f"{name_slug}_web.png"
                diff_path = agent_run_dir / f"{name_slug}_diff.png"
                compare_path = agent_run_dir / f"{name_slug}_compare.png"

                print(f"\n      [{item['plan_id']}] {item['figma_name']!r} vs {item['site_url']!r}")

                # 5a: 获取 Figma 设计稿截图（同时缓存节点 JSON 供元素对比用）
                node_json: Optional[Dict[str, Any]] = None
                try:
                    node_json = figma_client.get_node_json(node_id=item["figma_node_id"])
                    figma_client.save_node_to_file(
                        node_id=item["figma_node_id"],
                        output_path=figma_path,
                        scale=2,
                    )
                    print(f"        Figma 截图: {figma_path.name}")
                except Exception as exc:
                    print(f"        [ERROR] Figma 导出失败: {exc}")
                    page_results.append(self._error_result(item, f"figma_export:{type(exc).__name__}"))
                    continue

                # 5b: 获取网站截图（页面保持打开以供后续元素提取）
                try:
                    vp = item.get("viewport", {})
                    if vp:
                        capture.page.set_viewport_size(vp)
                    capture.page.goto(item["site_url"], wait_until="networkidle", timeout=30000)
                    hide = item.get("hide_selectors", [])
                    if hide:
                        capture.hide_elements(hide)
                    # 滚回顶部确保首屏状态与设计稿一致
                    capture.page.evaluate("window.scrollTo(0, 0)")
                    capture.page.screenshot(path=str(web_path), full_page=True)
                    print(f"        网站截图: {web_path.name}")
                except Exception as exc:
                    print(f"        [ERROR] 网站截图失败: {exc}")
                    page_results.append(self._error_result(item, f"web_capture:{type(exc).__name__}"))
                    continue

                # 5c: 像素级对比
                similarity = 0.0
                mse_val: Optional[float] = None
                try:
                    similarity = comparator.calculate_similarity(figma_path, web_path)
                    try:
                        mse_val = float(comparator.calculate_mse(figma_path, web_path))
                    except Exception:
                        pass
                    comparator.generate_diff_image(figma_path, web_path, diff_path)
                    comparator.generate_side_by_side(
                        figma_path, web_path, compare_path,
                        labels=("Figma", "Website"),
                    )
                    passed = similarity >= Config.SIMILARITY_THRESHOLD
                    status_label = "PASS" if passed else "FAIL"
                    print(f"        像素相似度: {similarity:.1f}%  阈值: {Config.SIMILARITY_THRESHOLD}%  → {status_label}")

                    page_results.append({
                        "plan_id": item["plan_id"],
                        "figma_name": item["figma_name"],
                        "site_url": item["site_url"],
                        "site_path": item["site_path"],
                        "browser": Config.DEFAULT_BROWSER,
                        "similarity": float(similarity),
                        "mse": round(mse_val, 2) if mse_val is not None else None,
                        "threshold": float(Config.SIMILARITY_THRESHOLD),
                        "passed": passed,
                        "figma_image": str(figma_path),
                        "web_image": str(web_path),
                        "diff_image": str(diff_path),
                        "compare_image": str(compare_path),
                        "element_result": None,
                        "status": "ok",
                    })
                except Exception as exc:
                    print(f"        [ERROR] 像素对比失败: {exc}")
                    page_results.append(self._error_result(item, f"compare:{type(exc).__name__}"))
                    continue

                # 5d: 元素属性级对比（在已打开的页面上直接提取 DOM 计算样式）
                if node_json:
                    try:
                        figma_elems = figma_extractor.extract_semantic(
                            node_json, max_depth=Config.COMPARE_MAX_DEPTH
                        )
                        # 过滤极小节点和自动命名噪音层
                        figma_elems = [
                            e for e in figma_elems
                            if e.width >= 12 and e.height >= 12
                            and not _noise_re.match((e.name or "").strip())
                        ]
                        root_frame = next(
                            (e for e in figma_elems if e.node_type in ("FRAME", "COMPONENT")),
                            None,
                        )
                        # 用 Figma 坐标自动定位对应 DOM 元素（TEXT 按位置匹配，非 TEXT 按 IoU）
                        auto_map = auto_mapper.generate(
                            figma_elements=figma_elems,
                            page=capture.page,
                            root_frame=root_frame,
                        )
                        dom_elems = dom_extractor.extract(
                            capture.page,
                            list(dict.fromkeys(auto_map.values())),
                        )
                        elem_result = elem_compare.compare(
                            figma_elements=figma_elems,
                            dom_elements=dom_elems,
                            element_map={},
                            id_element_map=auto_map,
                            threshold=Config.COMPARE_ELEMENT_THRESHOLD,
                            color_tol=Config.COMPARE_COLOR_TOLERANCE,
                            size_tol=Config.COMPARE_SIZE_TOLERANCE,
                            font_size_tol=Config.COMPARE_FONT_SIZE_TOLERANCE,
                            radius_tol=Config.COMPARE_RADIUS_TOLERANCE,
                            min_match_count=Config.COMPARE_MIN_MATCH_COUNT,
                        )
                        page_results[-1]["element_result"] = elem_result
                        page_results[-1]["auto_mapped_count"] = len(auto_map)
                        elem_passed = "✅" if elem_result.get("overall_passed") else "❌"
                        print(
                            f"        元素对比: 匹配 {elem_result['total_matched']}/"
                            f"{elem_result['total_matched'] + elem_result['total_unmatched']}  "
                            f"得分 {elem_result['overall_score']:.2f}  {elem_passed}"
                        )
                    except Exception as exc:
                        print(f"        [WARN] 元素对比跳过: {type(exc).__name__}: {exc}")
                        page_results[-1]["element_result"] = None

        results["steps"]["execute"] = {
            "status": "ok",
            "total": len(page_results),
            "passed": sum(1 for r in page_results if r.get("passed")),
            "failed": sum(1 for r in page_results if r.get("status") == "ok" and not r.get("passed")),
            "errors": sum(1 for r in page_results if r.get("status", "").startswith("error")),
        }

        # ── Step 6: 写总报告 ─────────────────────────────
        print(f"\n{'=' * 60}")
        print("[6/6] 生成总报告...")
        print(f"{'=' * 60}")

        from src.report_writer import ReportWriter

        ReportWriter.write_run_result(
            output_path=Config.JSON_REPORT_PATH,
            version=self.version,
            base_url=Config.BASE_URL,
            crawl_summary={
                "mode": "agent_v1",
                "matched_pairs": len(pairs),
                "executed": len(page_results),
            },
            page_results=page_results,
        )
        print(f"      JSON 报告: {Config.JSON_REPORT_PATH}")

        # 把每页的元素对比结果单独写入 element_diff.json 供 html_reporter 消费
        elem_diffs = [
            {
                "page_name": r.get("figma_name", ""),
                "browser": r.get("browser", Config.DEFAULT_BROWSER),
                "figma_node": r.get("plan_id", ""),
                "result": r.get("element_result"),
                "compare_config": {
                    "color_tolerance": Config.COMPARE_COLOR_TOLERANCE,
                    "size_tolerance": Config.COMPARE_SIZE_TOLERANCE,
                    "font_size_tolerance": Config.COMPARE_FONT_SIZE_TOLERANCE,
                    "radius_tolerance": Config.COMPARE_RADIUS_TOLERANCE,
                },
            }
            for r in page_results
            if r.get("status") == "ok" and r.get("element_result") is not None
        ]
        if elem_diffs:
            import json as _json
            Config.ELEMENT_DIFF_PATH.parent.mkdir(parents=True, exist_ok=True)
            Config.ELEMENT_DIFF_PATH.write_text(
                _json.dumps({"diffs": elem_diffs}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"      元素对比: {Config.ELEMENT_DIFF_PATH}")

        total_ok = sum(1 for r in page_results if r.get("status") == "ok")
        total_pass = sum(1 for r in page_results if r.get("passed"))
        total_fail = total_ok - total_pass
        total_err = len(page_results) - total_ok

        print(f"\n{'=' * 60}")
        print(f"  完成！共 {len(page_results)} 条  |  "
              f"PASS {total_pass}  |  FAIL {total_fail}  |  ERROR {total_err}")
        print(f"{'=' * 60}")

        results["steps"]["report"] = {"status": "ok"}
        return results

    @staticmethod
    def _error_result(item: Dict[str, Any], error: str) -> Dict[str, Any]:
        return {
            "plan_id": item.get("plan_id", ""),
            "figma_name": item.get("figma_name", ""),
            "site_url": item.get("site_url", ""),
            "site_path": item.get("site_path", ""),
            "browser": Config.DEFAULT_BROWSER,
            # None 而非 0.0，避免与真实相似度为 0% 的结果混淆
            "similarity": None,
            "mse": None,
            "threshold": float(Config.SIMILARITY_THRESHOLD),
            "passed": False,
            "element_result": None,
            "error_detail": error,
            "status": f"error:{error}",
        }
