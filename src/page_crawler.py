"""
src/page_crawler.py  ── 多页面自动发现模块（v1.1.0）
================================================
职责：
    从一组"种子路径"出发，用 BFS 广度优先算法自动发现站内所有可访问页面，
    供后续批量视觉对比使用。

工作原理：
    1. 把种子路径（如 ["/", "/about"]）加入队列。
    2. 用 WebCapture 打开每个 URL，等待网络空闲。
    3. 在页面 DOM 中找到所有可点击元素（a、button 等），提取 href。
    4. 过滤：只保留同域名、未访问过、不含排除关键词的 URL。
    5. 未超过最大深度时，把子页面加入队列继续遍历。
    6. 返回发现的所有页面列表（包含 url、depth、from、status 字段）。

核心类：
    CrawlPage（dataclass）
        url       ── 当前页面的完整 URL
        depth     ── 距离种子的层数（种子本身 = 0）
        from_url  ── 从哪个父页面发现的

    PageCrawler（主类）
        __init__()              ── 配置爬取参数（base_url、最大深度/页数/点击数、
                                    点击选择器、排除关键词）。
        _is_allowed_url()       ── URL 白名单检查（同域 + 非排除词 + http/https）。
        _normalize_url()        ── 去掉 hash 和 query，统一去重标准。
        _collect_urls_from_dom()── 在页面执行 JS，收集候选可点击元素的 href。
        discover()              ── BFS 入口，返回发现页面列表。

可通过 .env 控制的参数（对应 Config.CRAWL_*）：
    CRAWL_MAX_DEPTH           ── 最大爬取深度，默认 2
    CRAWL_MAX_PAGES           ── 最多发现页面数，默认 20
    CRAWL_MAX_CLICKS_PER_PAGE ── 每页最多取几个候选 href，默认 8
    CRAWL_CLICK_SELECTORS     ── 候选点击元素的 CSS 选择器列表
    CRAWL_EXCLUDE_KEYWORDS    ── URL 含这些关键词时跳过（如 logout、delete）
    CRAWL_SEED_PATHS          ── 起点路径列表，逗号分隔

依赖：
    collections.deque（BFS 队列）、urllib.parse、src.web_capture.WebCapture
"""

from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Set
from urllib.parse import urljoin, urlparse

from src.web_capture import WebCapture


@dataclass
class CrawlPage:
    url: str
    depth: int
    from_url: str


class PageCrawler:
    """受控页面发现器（站内、限深、限量）"""

    def __init__(
        self,
        base_url: str,
        browser_type: str = "chromium",
        headless: bool = True,
        max_depth: int = 2,
        max_pages: int = 20,
        max_clicks_per_page: int = 8,
        click_selectors: List[str] = None,
        exclude_keywords: List[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.browser_type = browser_type
        self.headless = headless
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.max_clicks_per_page = max_clicks_per_page
        self.click_selectors = click_selectors or ["a[href]", "button", "[role='link']", "[role='button']"]
        self.exclude_keywords = [k.lower() for k in (exclude_keywords or [])]
        self.base_netloc = urlparse(self.base_url).netloc

    def _is_allowed_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.netloc != self.base_netloc:
            return False
        lower = url.lower()
        if any(keyword in lower for keyword in self.exclude_keywords):
            return False
        return True

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        normalized = parsed._replace(fragment="", query="").geturl()
        return normalized.rstrip("/")

    def _collect_urls_from_dom(self, capture: WebCapture) -> List[str]:
        selector = ",".join(self.click_selectors)
        urls = capture.page.eval_on_selector_all(
            selector,
            """
            (elements) => {
                const results = [];
                for (const el of elements) {
                    const href = el.getAttribute("href")
                        || el.dataset?.href
                        || el.getAttribute("data-url")
                        || el.getAttribute("data-href");
                    if (href) results.push(href);
                }
                return results;
            }
            """,
        )
        return urls or []

    def discover(self, seed_paths: List[str]) -> List[Dict]:
        queue = deque()
        visited: Set[str] = set()
        discovered: List[Dict] = []

        for path in seed_paths:
            seed_url = urljoin(f"{self.base_url}/", path.lstrip("/"))
            queue.append(CrawlPage(url=seed_url, depth=0, from_url="seed"))

        with WebCapture(browser_type=self.browser_type, headless=self.headless) as capture:
            while queue and len(discovered) < self.max_pages:
                current = queue.popleft()
                norm_url = self._normalize_url(current.url)

                if norm_url in visited:
                    continue
                if not self._is_allowed_url(norm_url):
                    continue

                visited.add(norm_url)

                try:
                    capture.page.goto(norm_url, wait_until="networkidle", timeout=30000)
                except Exception as exc:
                    discovered.append(
                        {
                            "url": norm_url,
                            "depth": current.depth,
                            "from": current.from_url,
                            "status": f"error:{type(exc).__name__}",
                        }
                    )
                    continue

                discovered.append(
                    {
                        "url": norm_url,
                        "depth": current.depth,
                        "from": current.from_url,
                        "status": "ok",
                    }
                )

                if current.depth >= self.max_depth:
                    continue

                child_urls = self._collect_urls_from_dom(capture)[: self.max_clicks_per_page]
                for child in child_urls:
                    next_url = self._normalize_url(urljoin(norm_url + "/", child))
                    if next_url not in visited and self._is_allowed_url(next_url):
                        queue.append(
                            CrawlPage(url=next_url, depth=current.depth + 1, from_url=norm_url)
                        )

        return discovered
