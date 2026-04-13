"""
src/site_discovery.py  ── 自动测试代理的网站发现模块
=====================================================
职责：
    在已有 PageCrawler 的基础上，进一步补齐每个页面的截图、标题、文本摘要、
    DOM 结构摘要，并整理成后续页面配对可直接消费的 site_inventory.json。

适用阶段：
    vNext / 版本1：先做“自动发现网站页面 + 自动产出页面清单”，
    暂不涉及 Figma 页面配对与元素级映射。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List
from urllib.parse import urlparse

from config.config import Config
from src.report_writer import ReportWriter

if TYPE_CHECKING:  # pragma: no cover - 仅供类型提示
    from src.page_crawler import PageCrawler
    from src.web_capture import WebCapture

# Python 3.11+ has datetime.UTC; 3.10 需用 timezone.utc
try:
    from datetime import UTC
except ImportError:  # pragma: no cover - 兼容 3.10
    from datetime import timezone

    UTC = timezone.utc


@dataclass
class DiscoveredPage:
    """自动发现后的页面结构化摘要。"""

    page_id: str
    url: str
    path: str
    title: str
    depth: int
    from_url: str
    status: str
    screenshot_path: str
    text_summary: List[str]
    dom_summary: Dict[str, Any]
    fingerprint: Dict[str, str]


class SiteDiscovery:
    """自动测试代理的站点发现器。"""

    def __init__(
        self,
        base_url: str | None = None,
        browser_type: str | None = None,
        headless: bool | None = None,
        max_depth: int | None = None,
        max_pages: int | None = None,
        seed_paths: List[str] | None = None,
        exclude_keywords: List[str] | None = None,
        hide_selectors: List[str] | None = None,
        viewport: Dict[str, int] | None = None,
    ):
        self.base_url = (base_url or Config.BASE_URL).rstrip("/")
        self.browser_type = browser_type or Config.DEFAULT_BROWSER
        self.headless = Config.HEADLESS if headless is None else headless
        self.max_depth = Config.DISCOVERY_MAX_DEPTH if max_depth is None else max_depth
        self.max_pages = Config.DISCOVERY_MAX_PAGES if max_pages is None else max_pages
        self.seed_paths = seed_paths or Config.DISCOVERY_SEED_PATHS
        self.exclude_keywords = exclude_keywords or Config.DISCOVERY_EXCLUDE_KEYWORDS
        self.hide_selectors = hide_selectors or Config.AGENT_HIDE_SELECTORS
        self.viewport = viewport or {
            "width": Config.AGENT_VIEWPORT_WIDTH,
            "height": Config.AGENT_VIEWPORT_HEIGHT,
        }

    @staticmethod
    def _normalize_path(url: str) -> str:
        """把完整 URL 转成稳定的相对路径表示。"""
        parsed = urlparse(url)
        path = parsed.path or "/"
        if not path.startswith("/"):
            path = "/" + path
        return path

    @staticmethod
    def _slug_from_url(url: str) -> str:
        """把 URL 转成可落盘的文件名。"""
        parsed = urlparse(url)
        path = (parsed.path or "/").strip("/")
        if not path:
            return "home"
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_").lower()
        return slug or "page"

    @staticmethod
    def _compact_text(value: str, limit: int = 80) -> str:
        """压缩空白并限制摘要长度，便于后续匹配。"""
        text = re.sub(r"\s+", " ", (value or "")).strip()
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    @classmethod
    def _dedupe_non_empty(cls, values: List[str], limit: int = 8) -> List[str]:
        """去重并过滤空文本，保留前若干条作为页面摘要。"""
        result: List[str] = []
        seen: set[str] = set()
        for value in values:
            compact = cls._compact_text(value)
            if not compact:
                continue
            lower = compact.lower()
            if lower in seen:
                continue
            seen.add(lower)
            result.append(compact)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _fingerprint_for(url: str, title: str, text_summary: List[str], dom_summary: Dict[str, Any]) -> Dict[str, str]:
        """生成轻量页面指纹，供后续模板聚类和页面配对使用。"""
        layout_seed = "|".join(
            [
                str(dom_summary.get("heading_count", 0)),
                str(dom_summary.get("button_count", 0)),
                str(dom_summary.get("link_count", 0)),
                str(dom_summary.get("form_count", 0)),
                str(dom_summary.get("image_count", 0)),
            ]
        )
        text_seed = "|".join([title] + text_summary)
        return {
            "path_key": hashlib.md5(url.encode("utf-8")).hexdigest()[:12],
            "layout_key": hashlib.md5(layout_seed.encode("utf-8")).hexdigest()[:12],
            "text_key": hashlib.md5(text_seed.encode("utf-8")).hexdigest()[:12],
        }

    @classmethod
    def build_page_record(
        cls,
        crawl_item: Dict[str, Any],
        page_snapshot: Dict[str, Any],
        screenshot_path: Path,
    ) -> DiscoveredPage:
        """把爬取结果 + 页面摘要拼成结构化页面记录。"""
        url = crawl_item["url"]
        path = cls._normalize_path(url)
        title = cls._compact_text(page_snapshot.get("title", ""), limit=120)
        headings = page_snapshot.get("headings", [])
        buttons = page_snapshot.get("buttons", [])
        text_summary = cls._dedupe_non_empty([title] + headings + buttons, limit=8)
        dom_summary = {
            "heading_count": int(page_snapshot.get("heading_count", len(headings))),
            "button_count": int(page_snapshot.get("button_count", len(buttons))),
            "link_count": int(page_snapshot.get("link_count", 0)),
            "form_count": int(page_snapshot.get("form_count", 0)),
            "image_count": int(page_snapshot.get("image_count", 0)),
            "headings": cls._dedupe_non_empty(headings, limit=5),
            "buttons": cls._dedupe_non_empty(buttons, limit=5),
        }
        return DiscoveredPage(
            page_id="site::" + hashlib.md5(url.encode("utf-8")).hexdigest()[:12],
            url=url,
            path=path,
            title=title,
            depth=int(crawl_item.get("depth", 0)),
            from_url=crawl_item.get("from", "seed"),
            status=crawl_item.get("status", "ok"),
            screenshot_path=str(screenshot_path),
            text_summary=text_summary,
            dom_summary=dom_summary,
            fingerprint=cls._fingerprint_for(url, title, text_summary, dom_summary),
        )

    @staticmethod
    def _extract_page_snapshot(capture: "WebCapture") -> Dict[str, Any]:
        """从当前页面提取基础摘要，避免后续页面匹配直接依赖整页 HTML。"""
        raw = capture.page.evaluate(
            """
() => {
    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const pickTexts = (selector, limit = 8) => {
        const values = [];
        for (const el of Array.from(document.querySelectorAll(selector))) {
            const text = clean(el.innerText || el.textContent || '');
            if (!text) continue;
            values.push(text);
            if (values.length >= limit) break;
        }
        return values;
    };

    const headings = pickTexts('h1, h2, h3');
    const buttons = pickTexts('button, [role="button"], a[href]');
    return {
        title: clean(document.title || ''),
        headings,
        buttons,
        heading_count: document.querySelectorAll('h1, h2, h3').length,
        button_count: document.querySelectorAll('button, [role="button"]').length,
        link_count: document.querySelectorAll('a[href]').length,
        form_count: document.querySelectorAll('form').length,
        image_count: document.querySelectorAll('img, picture, svg').length,
    };
}
"""
        )
        return raw or {}

    def _build_crawler(self) -> "PageCrawler":
        """复用现有 PageCrawler 做 URL 发现，避免重复造 BFS 轮子。"""
        from src.page_crawler import PageCrawler

        return PageCrawler(
            base_url=self.base_url,
            browser_type=self.browser_type,
            headless=self.headless,
            max_depth=self.max_depth,
            max_pages=self.max_pages,
            max_clicks_per_page=Config.CRAWL_MAX_CLICKS_PER_PAGE,
            click_selectors=Config.CRAWL_CLICK_SELECTORS,
            exclude_keywords=self.exclude_keywords,
        )

    def discover(self, write_report: bool = True) -> Dict[str, Any]:
        """
        自动发现网站页面，并生成后续页面配对所需的 site inventory。

        Returns:
            {
              "base_url": ...,
              "generated_at": ...,
              "pages": [...],
              "summary": {...}
            }
        """
        Config.setup_directories()
        crawler = self._build_crawler()
        crawl_results = crawler.discover(self.seed_paths)

        pages: List[DiscoveredPage] = []
        ok_items = [item for item in crawl_results if item.get("status") == "ok"]

        from src.web_capture import WebCapture

        with WebCapture(
            browser_type=self.browser_type,
            headless=self.headless,
            viewport=self.viewport,
        ) as capture:
            capture.page.set_viewport_size(self.viewport)

            for item in ok_items:
                url = item["url"]
                try:
                    capture.page.goto(url, wait_until="networkidle", timeout=30000)
                    if self.hide_selectors:
                        capture.hide_elements(self.hide_selectors)

                    screenshot_path = Config.SCREENSHOTS_DIR / "site" / f"{self._slug_from_url(url)}.png"
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    capture.page.screenshot(path=str(screenshot_path), full_page=False)

                    snapshot = self._extract_page_snapshot(capture)
                    pages.append(self.build_page_record(item, snapshot, screenshot_path))
                except Exception as exc:  # noqa: BLE001
                    fallback_path = Config.SCREENSHOTS_DIR / "site" / f"{self._slug_from_url(url)}.png"
                    pages.append(
                        DiscoveredPage(
                            page_id="site::" + hashlib.md5(url.encode("utf-8")).hexdigest()[:12],
                            url=url,
                            path=self._normalize_path(url),
                            title="",
                            depth=int(item.get("depth", 0)),
                            from_url=item.get("from", "seed"),
                            status=f"snapshot_error:{type(exc).__name__}",
                            screenshot_path=str(fallback_path),
                            text_summary=[],
                            dom_summary={},
                            fingerprint=self._fingerprint_for(url, "", [], {}),
                        )
                    )

        payload = {
            "base_url": self.base_url,
            "generated_at": datetime.now(UTC).isoformat(),
            "pages": [asdict(page) for page in pages],
            "summary": {
                "discovered_urls": len(crawl_results),
                "inventory_pages": len(pages),
                "ok_pages": sum(1 for p in pages if p.status == "ok"),
                "failed_pages": sum(1 for p in pages if p.status != "ok"),
                "seed_paths": self.seed_paths,
                "max_depth": self.max_depth,
                "max_pages": self.max_pages,
            },
        }

        if write_report:
            ReportWriter.write_site_inventory(
                output_path=Config.SITE_INVENTORY_PATH,
                base_url=self.base_url,
                summary=payload["summary"],
                pages=payload["pages"],
                generated_at=payload["generated_at"],
            )

        return payload
