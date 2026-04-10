"""
tests/test_desktop.py  ── 桌面端 UI 视觉回归测试
================================================
职责：
    把 Figma 设计稿导出图 与 真实网站截图 做像素级对比，
    相似度低于阈值则判定"设计与实现不一致"，测试失败。

测试套件：
    TestDesktop（@pytest.mark.desktop）
        通用流程 _run_ui_test() 串联以下 5 步：
            [1/5] 用 FigmaClient 按 node_id 导出设计稿 PNG（2x 分辨率）。
            [2/5] 用 WebCapture 打开真实网页，隐藏动态元素，截全页图。
            [3/5] 用 ImageCompare 计算相似度（absdiff 算法）。
            [4/5] 生成差异高亮图（*_diff.png）和左右对比图（*_compare.png）。
            [5/5] 写结构化 JSON 报告，并断言相似度 >= 阈值。

        内置用例：
            test_homepage_chromium  ── 首页 × Chrome 浏览器
            test_homepage_firefox   ── 首页 × Firefox（未安装时自动跳过）
            test_homepage_webkit    ── 首页 × Safari（仅 Mac，已硬跳过）

    TestElementMatch（@pytest.mark.desktop @pytest.mark.element，v1.2.0）
        _run_element_test() 串联 5 步：
            [1/5] FigmaClient.get_node_json → FigmaExtractor.extract → FigmaElement 列表
            [2/5] WebCapture 打开页面 → AutoMapper.generate() 自动推导映射
                  → DOMExtractor 提取 DOM 计算样式
            [3/5] ElementCompare.compare → 属性级 diff 报告
            [4/5] ReportWriter.write_element_diff_report → element_diff.json
            [5/5] assert overall_score >= threshold

        内置用例：
            test_homepage_elements_chromium  ── 首页元素属性对比 × Chromium

    TestCrawlDiscovery（@pytest.mark.desktop @pytest.mark.crawl，v1.1.0）
        test_crawl_discovery
            ── 用 PageCrawler 从种子路径出发，自动发现站内页面。
            ── 把发现结果写入 reports/json/run_result.json。
            ── CRAWL_ENABLED=false 时自动跳过。

输出产物：
    screenshots/figma/*.png             ── Figma 导出的设计稿
    screenshots/web/*.png               ── 网站实际截图
    reports/images/*_diff.png           ── 差异高亮图
    reports/images/*_compare.png        ── 左右并排对比图
    reports/html/report.html            ── pytest-html 测试报告
    reports/json/run_result.json        ── 像素对比 JSON 报告（v1.1.0）
    reports/json/element_diff.json      ── 属性级 diff JSON 报告（v1.2.0）

常用运行命令：
    pytest                              # 全部用例
    pytest -k chromium                  # 只跑 chromium
    pytest -k element                   # 只跑属性级对比用例
    pytest -k crawl_discovery           # 只跑页面发现
    pytest --tb=short                   # 失败时精简 traceback
"""

import pytest
from src.figma_client import FigmaClient
from src.web_capture import WebCapture
from src.image_compare import ImageCompare
from src.page_crawler import PageCrawler
from src.report_writer import ReportWriter
from src.figma_extractor import FigmaExtractor
from src.dom_extractor import DOMExtractor
from src.element_compare import ElementCompare
from src.auto_mapper import AutoMapper
from config.config import Config


@pytest.mark.desktop
class TestDesktop:
    """桌面端UI测试套件"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """测试前准备"""
        self.figma = FigmaClient()
        self.comparator = ImageCompare(threshold=Config.SIMILARITY_THRESHOLD)
        self.version = (Config.BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
        Config.setup_directories()
        yield

    def _run_ui_test(self, test_name: str, page_config: dict, browser_type: str = "chromium"):
        print(f"\n{'=' * 70}")
        print(f"[TEST] {test_name}")
        print(f"{'=' * 70}")

        figma_path = Config.SCREENSHOTS_DIR / "figma" / f"{test_name}.png"
        web_path = Config.SCREENSHOTS_DIR / "web" / f"{test_name}.png"
        diff_path = Config.REPORTS_DIR / "images" / f"{test_name}_diff.png"
        sidebyside_path = Config.REPORTS_DIR / "images" / f"{test_name}_compare.png"

        print(f"\n[1/5] 获取Figma设计稿...")
        node_id = page_config["figma_node"]
        print(f"      NodeID: {node_id}")
        self.figma.save_node_to_file(node_id=node_id, output_path=figma_path, scale=2)
        print(f"      [OK] 设计稿已保存: {figma_path}")

        print(f"\n[2/5] 截取网站页面...")
        full_url = Config.BASE_URL + page_config["url"]
        print(f"      URL: {full_url}")
        print(f"      浏览器: {browser_type}")

        with WebCapture(browser_type=browser_type, headless=Config.HEADLESS) as capture:
            capture.page.goto(full_url, wait_until="networkidle")
            if page_config.get("wait_for"):
                try:
                    capture.page.wait_for_selector(page_config["wait_for"], timeout=10000, state="visible")
                except Exception:
                    print(f"      [WARN] 等待元素超时: {page_config['wait_for']}")

            hide_selectors = page_config.get(
                "hide_elements",
                [".advertisement", ".cookie-banner", "[class*='timestamp']", "[id*='chat']"],
            )
            capture.hide_elements(hide_selectors)
            if page_config.get("viewport"):
                capture.page.set_viewport_size(page_config["viewport"])
            capture.page.screenshot(path=str(web_path), full_page=True)

        print(f"      [OK] 网站截图已保存: {web_path}")
        print(f"\n[3/5] 进行视觉对比...")
        similarity = self.comparator.calculate_similarity(figma_path, web_path)
        print(f"      相似度: {similarity}%")
        print(f"      阈值: {Config.SIMILARITY_THRESHOLD}%")

        print(f"\n[4/5] 生成差异报告...")
        self.comparator.generate_diff_image(figma_path, web_path, diff_path)
        self.comparator.generate_side_by_side(
            figma_path, web_path, sidebyside_path, labels=("Figma设计", browser_type.upper())
        )
        print(f"      [OK] 差异图: {diff_path}")
        print(f"      [OK] 对比图: {sidebyside_path}")

        print(f"\n[5/5] 生成测试报告...")
        report = self.comparator.get_comparison_report(figma_path, web_path)
        report.update(
            {
                "page_name": test_name,
                "browser": browser_type,
                "figma_path": str(figma_path),
                "web_path": str(web_path),
                "diff_path": str(diff_path),
                "sidebyside_path": str(sidebyside_path),
            }
        )
        print(f"      相似度: {report['similarity']}%")
        print(f"      MSE: {report['mse']}")
        print(f"      结果: {'PASS' if report['passed'] else 'FAIL'}")

        ReportWriter.write_run_result(
            output_path=Config.JSON_REPORT_PATH,
            version=self.version,
            base_url=Config.BASE_URL,
            crawl_summary={"enabled": False, "discovered_pages": 1, "max_depth": 0, "max_pages": 1},
            page_results=[report],
        )

        assert report["passed"], (
            f"\n[FAIL] UI一致性不达标\n"
            f"   相似度: {similarity}%\n"
            f"   阈值: {Config.SIMILARITY_THRESHOLD}%\n"
            f"   差异图: {diff_path}"
        )

    def test_homepage_chromium(self):
        self._run_ui_test(
            test_name="homepage_chromium",
            page_config=Config.TEST_PAGES["homepage"],
            browser_type="chromium",
        )

    def test_homepage_firefox(self):
        self._run_ui_test(
            test_name="homepage_firefox",
            page_config=Config.TEST_PAGES["homepage"],
            browser_type="firefox",
        )

    @pytest.mark.skip(reason="需要Safari支持，仅Mac运行")
    def test_homepage_webkit(self):
        self._run_ui_test(
            test_name="homepage_webkit",
            page_config=Config.TEST_PAGES["homepage"],
            browser_type="webkit",
        )


@pytest.mark.desktop
@pytest.mark.element
class TestElementMatch:
    """
    v1.2.0  结构化属性级对比套件
    ── 不依赖像素截图，直接对比 Figma JSON 设计属性 vs DOM computed style
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        self.figma = FigmaClient()
        self.figma_extractor = FigmaExtractor()
        self.dom_extractor = DOMExtractor()
        self.comparator = ElementCompare()
        self.auto_mapper = AutoMapper()
        self.version = (Config.BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
        Config.setup_directories()
        yield

    def _run_element_test(
        self,
        test_name: str,
        page_config: dict,
        browser_type: str = "chromium",
    ):
        print(f"\n{'=' * 70}")
        print(f"[ELEMENT TEST] {test_name}")
        print(f"{'=' * 70}")

        node_id = page_config["figma_node"]
        # Config 中的 element_map 作为兜底（手工补充优先级最高）
        manual_map = page_config.get("element_map", {})
        full_url = Config.BASE_URL + page_config["url"]
        viewport = page_config.get("viewport", {"width": 1440, "height": 900})

        # ── [1/5] 从 Figma 获取节点 JSON 并提取设计属性
        print(f"\n[1/5] 提取 Figma 节点属性...")
        print(f"      NodeID: {node_id}")
        node_json = self.figma.get_node_json(node_id)
        # extract_semantic() 过滤装饰性类型、自动生成名称、极小节点等噪音，
        # 并用 max_depth 限制深度避免把移动端状态栏/图标内部路径纳入对比
        figma_elements = self.figma_extractor.extract_semantic(
            node_json, max_depth=Config.COMPARE_MAX_DEPTH
        )
        root_frame = next(
            (e for e in figma_elements if e.node_type in ("FRAME", "COMPONENT")), None
        )
        print(f"      [OK] 提取到 {len(figma_elements)} 个语义节点（已过滤装饰/自动生成节点）")

        # ── [2/5] 打开页面，自动推导映射 + 提取 DOM 元素样式
        print(f"\n[2/5] 自动推导 Figma→DOM 映射并提取 DOM 样式...")
        print(f"      URL: {full_url}")
        print(f"      浏览器: {browser_type}")

        with WebCapture(browser_type=browser_type, headless=Config.HEADLESS) as capture:
            capture.page.goto(full_url, wait_until="networkidle")

            if page_config.get("wait_for"):
                try:
                    capture.page.wait_for_selector(
                        page_config["wait_for"], timeout=10000, state="visible"
                    )
                except Exception:
                    print(f"      [WARN] 等待元素超时: {page_config['wait_for']}")

            capture.page.set_viewport_size(viewport)

            # 自动推导：{figma_id: css_selector}（ID 为 key，避免同名图层冲突）
            auto_map = self.auto_mapper.generate(
                figma_elements=figma_elements,
                page=capture.page,
                root_frame=root_frame,
            )

            print(f"      [OK] 自动映射 {len(auto_map)} 个节点，"
                  f"手工补充 {len(manual_map)} 个")

            # 构建 ID→名称反查表，仅用于日志打印（不影响匹配逻辑）
            id_to_name = {e.id: e.name for e in figma_elements}
            for fid, sel in auto_map.items():
                fname = id_to_name.get(fid, fid)
                print(f"           (自动) {fname!r} [{fid}] → {sel!r}")
            for name, sel in manual_map.items():
                print(f"           (手工) {name!r} → {sel!r}")

            # 提取所有已映射选择器对应的 DOM 元素样式
            # auto_map（ID-keyed）与 manual_map（name-keyed）的选择器合并去重后提取
            all_selectors = list(dict.fromkeys(
                list(auto_map.values()) + list(manual_map.values())
            ))
            dom_mapped = self.dom_extractor.extract(capture.page, all_selectors)

        dom_elements = dom_mapped
        print(f"      [OK] 提取到 {len(dom_elements)} 个 DOM 元素")

        # ── [3/5] 属性级对比
        print(f"\n[3/5] 进行属性级对比...")
        compare_config = {
            "color_tolerance": Config.COMPARE_COLOR_TOLERANCE,
            "size_tolerance": Config.COMPARE_SIZE_TOLERANCE,
            "font_size_tolerance": Config.COMPARE_FONT_SIZE_TOLERANCE,
            "radius_tolerance": Config.COMPARE_RADIUS_TOLERANCE,
            "element_threshold": Config.COMPARE_ELEMENT_THRESHOLD,
        }
        result = self.comparator.compare(
            figma_elements=figma_elements,
            dom_elements=dom_elements,
            element_map=manual_map,           # name-keyed：Config 手工补充（兜底）
            id_element_map=auto_map,          # id-keyed：AutoMapper 自动推导（优先）
            threshold=Config.COMPARE_ELEMENT_THRESHOLD,
            color_tol=Config.COMPARE_COLOR_TOLERANCE,
            size_tol=Config.COMPARE_SIZE_TOLERANCE,
            font_size_tol=Config.COMPARE_FONT_SIZE_TOLERANCE,
            radius_tol=Config.COMPARE_RADIUS_TOLERANCE,
            min_match_count=Config.COMPARE_MIN_MATCH_COUNT,
        )

        print(f"      语义节点数:  {len(figma_elements)}")
        print(f"      成功匹配:    {result['total_matched']}")
        print(f"      未匹配:      {result['total_unmatched']}")
        print(f"      覆盖率:      {result['coverage_rate']:.2%}  (已匹配/总节点)")
        print(f"      整体得分:    {result['overall_score']:.2%}  (仅已匹配节点属性通过率)")
        print(f"      得分阈值:    {Config.COMPARE_ELEMENT_THRESHOLD:.2%}")
        if result.get("warning"):
            print(f"      [WARN] {result['warning']}")

        for elem in result["elements"]:
            status = "PASS" if elem["passed"] else ("FAIL" if elem["matched"] else "NO MATCH")
            print(f"      [{status}] {elem['figma_name']}")
            if elem["matched"]:
                for prop, detail in elem["properties"].items():
                    if not detail["passed"]:
                        diff = detail.get("diff", "")
                        print(f"              ! {prop}: "
                              f"figma={detail['figma']} web={detail['web']}"
                              + (f" diff={diff}" if diff else ""))

        # ── [4/5] 写报告（包含自动映射结果）
        # auto_map 以 figma_id 为 key，记录数量即可；manual_map 以名称为 key
        compare_config["auto_mapped_count"] = len(auto_map)
        compare_config["manual_mapped_count"] = len(manual_map)

        print(f"\n[4/5] 生成属性级 diff 报告...")
        report_path = Config.REPORTS_DIR / "json" / "element_diff.json"
        ReportWriter.write_element_diff_report(
            output_path=report_path,
            version=self.version,
            base_url=Config.BASE_URL,
            page_name=test_name,
            browser=browser_type,
            figma_node=node_id,
            compare_config=compare_config,
            result=result,
        )
        print(f"      [OK] 报告已保存: {report_path}")

        # ── [5/5] 断言
        if result.get("warning") and result["total_matched"] == 0:
            print(f"\n[SKIP-like] 无可匹配元素，测试结果为 inconclusive（不计入失败）。")
            print(f"   建议：在 config.py 的 TEST_PAGES['homepage']['element_map'] 中")
            print(f"         手动填写 Figma 图层名 → CSS 选择器 的对应关系。")

        assert result["overall_passed"], (
            f"\n[FAIL] 元素属性一致性不达标\n"
            f"   整体得分: {result['overall_score']:.2%}  (阈值: {Config.COMPARE_ELEMENT_THRESHOLD:.2%})\n"
            f"   覆盖率:   {result['coverage_rate']:.2%}  (已匹配 {result['total_matched']} 个节点)\n"
            f"   最低匹配: {result['min_match_count']} 个节点  (实际: {result['total_matched']})\n"
            f"   警告:     {result.get('warning', '')}\n"
            f"   详细报告: {report_path}"
        )

    def test_homepage_elements_chromium(self):
        self._run_element_test(
            test_name="homepage_elements_chromium",
            page_config=Config.TEST_PAGES["homepage"],
            browser_type="chromium",
        )


@pytest.mark.desktop
@pytest.mark.crawl
class TestCrawlDiscovery:
    """v1.1.0 多页面发现（第一批）"""

    def test_crawl_discovery(self):
        if not Config.CRAWL_ENABLED:
            pytest.skip("CRAWL_ENABLED=false，已跳过页面发现测试")

        Config.setup_directories()
        crawler = PageCrawler(
            base_url=Config.BASE_URL,
            browser_type=Config.DEFAULT_BROWSER,
            headless=Config.HEADLESS,
            max_depth=Config.CRAWL_MAX_DEPTH,
            max_pages=Config.CRAWL_MAX_PAGES,
            max_clicks_per_page=Config.CRAWL_MAX_CLICKS_PER_PAGE,
            click_selectors=Config.CRAWL_CLICK_SELECTORS,
            exclude_keywords=Config.CRAWL_EXCLUDE_KEYWORDS,
        )
        discovered = crawler.discover(Config.CRAWL_SEED_PATHS)
        assert discovered, "未发现可访问页面，请检查 BASE_URL 或种子配置"

        version = (Config.BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
        result_path = ReportWriter.write_run_result(
            output_path=Config.JSON_REPORT_PATH,
            version=version,
            base_url=Config.BASE_URL,
            crawl_summary={
                "enabled": True,
                "max_depth": Config.CRAWL_MAX_DEPTH,
                "max_pages": Config.CRAWL_MAX_PAGES,
                "discovered_pages": len(discovered),
                "seed_paths": Config.CRAWL_SEED_PATHS,
            },
            page_results=discovered,
        )
        print(f"\n[OK] JSON报告已生成: {result_path}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--tb=short"])