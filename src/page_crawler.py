"""
v1.1.0 页面发现模块
支持种子页 + 受控点击发现站内页面
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
