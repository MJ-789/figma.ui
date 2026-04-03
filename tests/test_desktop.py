"""
桌面端UI测试
测试桌面端设计稿与网站的视觉一致性
"""

import pytest
from src.figma_client import FigmaClient
from src.web_capture import WebCapture
from src.image_compare import ImageCompare
from src.page_crawler import PageCrawler
from src.report_writer import ReportWriter
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