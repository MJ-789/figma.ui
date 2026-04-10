"""
src/web_capture.py  ── Playwright 网页截图模块
================================================
职责：
    用 Playwright（同步 API）打开真实网站，执行截图操作，
    为对比模块提供"实际页面截图"。

核心类：
    WebCapture（主类，推荐用 with 语句管理生命周期）
        __enter__ / __exit__   ── 自动启动 / 关闭浏览器，配合 with 使用。
        start()                ── 启动浏览器进程 + 创建上下文 + 新建页面。
        capture_full_page()    ── 导航到 URL，等待网络空闲，截全页长图。
        capture_element()      ── 截取页面上某个 CSS 选择器匹配的元素。
        capture_viewport()     ── 只截当前可视区（不滚动）。
        hide_elements()        ── 用 JS 把广告、时间戳等动态元素设为 display:none，
                                  避免它们干扰像素对比。
        wait_for_network_idle() ── 等待网络静止（主动调用版本）。
        close()                ── 释放所有 Playwright 资源。

    BatchCapture（批量工具，静态方法集合）
        capture_multiple_pages()  ── 批量截取多个 URL。
        capture_cross_browser()   ── 同一 URL 跑 chromium / firefox / webkit。
        capture_responsive()      ── 同一 URL 模拟多种设备（手机/平板/桌面）。

支持浏览器：
    chromium（默认）/ firefox / webkit（仅 macOS）

注意：
    本模块使用 sync_playwright，与 pytest-playwright 插件的 asyncio 机制冲突。
    pytest.ini 中已加 -p no:playwright 屏蔽该插件，请勿去掉该参数。
"""

from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
from typing import Optional, Dict, List
from pathlib import Path
import time


class WebCapture:
    """Playwright网站截图客户端"""

    def __init__(self,
                 browser_type: str = 'chromium',
                 headless: bool = True,
                 device: Optional[str] = None,
                 viewport: Optional[Dict] = None):
        """
        初始化

        Args:
            browser_type: 浏览器类型 chromium/firefox/webkit
            headless: 是否无头模式
            device: 设备名称,如 'iPhone 13'
            viewport: 视口尺寸 {'width': 1920, 'height': 1080}
        """
        self.browser_type = browser_type
        self.headless = headless
        self.device = device
        self.viewport = viewport or {'width': 1920, 'height': 1080}

        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def start(self):
        """启动浏览器"""
        self.playwright = sync_playwright().start()

        # 选择浏览器
        browser_launcher = {
            'chromium': self.playwright.chromium,
            'firefox': self.playwright.firefox,
            'webkit': self.playwright.webkit
        }.get(self.browser_type)

        if not browser_launcher:
            raise ValueError(f"不支持的浏览器: {self.browser_type}")

        self.browser = browser_launcher.launch(headless=self.headless)

        # 创建上下文
        if self.device:
            device_config = self.playwright.devices[self.device]
            self.context = self.browser.new_context(**device_config)
        else:
            self.context = self.browser.new_context(viewport=self.viewport)

        self.page = self.context.new_page()

    def capture_full_page(self,
                          url: str,
                          output_path: Path,
                          wait_time: int = 2,
                          wait_for_selector: Optional[str] = None) -> Path:
        """
        截取整个页面

        Args:
            url: 目标URL
            output_path: 保存路径
            wait_time: 等待时间(秒)
            wait_for_selector: 等待特定元素

        Returns:
            截图文件路径
        """
        # 访问页面
        self.page.goto(url, wait_until='networkidle', timeout=30000)

        # 等待特定元素
        if wait_for_selector:
            try:
                self.page.wait_for_selector(
                    wait_for_selector,
                    timeout=10000,
                    state='visible'
                )
            except Exception as e:
                print(f"⚠️  等待元素超时: {wait_for_selector}")

        # 额外等待
        time.sleep(wait_time)

        # 确保目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 截图
        self.page.screenshot(path=str(output_path), full_page=True)

        return output_path

    def capture_element(self,
                        url: str,
                        selector: str,
                        output_path: Path,
                        wait_time: int = 2) -> Path:
        """
        截取特定元素

        Args:
            url: 目标URL
            selector: CSS选择器
            output_path: 保存路径
            wait_time: 等待时间
        """
        self.page.goto(url, wait_until='networkidle')
        time.sleep(wait_time)

        element = self.page.locator(selector)
        element.wait_for(state='visible')

        output_path.parent.mkdir(parents=True, exist_ok=True)
        element.screenshot(path=str(output_path))

        return output_path

    def capture_viewport(self,
                         url: str,
                         output_path: Path) -> Path:
        """
        截取视口区域(不滚动)
        """
        self.page.goto(url, wait_until='networkidle')

        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(output_path), full_page=False)

        return output_path

    def hide_elements(self, selectors: List[str]):
        """
        隐藏页面元素(用于隐藏动态内容)

        Args:
            selectors: CSS选择器列表
        """
        for selector in selectors:
            try:
                self.page.evaluate(f"""
                    const elements = document.querySelectorAll('{selector}');
                    elements.forEach(el => el.style.display = 'none');
                """)
            except Exception:
                pass  # 元素不存在时忽略

    def wait_for_network_idle(self, timeout: int = 5000):
        """等待网络空闲"""
        self.page.wait_for_load_state('networkidle', timeout=timeout)

    def close(self):
        """关闭浏览器"""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()


# ============ 批量截图工具 ============

class BatchCapture:
    """批量截图工具"""

    @staticmethod
    def capture_multiple_pages(pages: List[Dict],
                               browser_type: str = 'chromium',
                               headless: bool = True):
        """
        批量截图多个页面

        Args:
            pages: 页面列表 [{'url': '...', 'output': '...'}]
            browser_type: 浏览器类型
            headless: 无头模式
        """
        with WebCapture(browser_type, headless) as capture:
            for page_info in pages:
                url = page_info['url']
                output_path = Path(page_info['output'])

                print(f"📸 截图: {url}")
                capture.capture_full_page(url, output_path)
                print(f"   ✓ 保存到: {output_path}")

    @staticmethod
    def capture_cross_browser(url: str,
                              output_dir: Path,
                              browsers: List[str] = None):
        """
        跨浏览器截图

        Args:
            url: 目标URL
            output_dir: 输出目录
            browsers: 浏览器列表
        """
        browsers = browsers or ['chromium', 'firefox', 'webkit']

        for browser in browsers:
            print(f"\n🌐 {browser.upper()} 截图...")

            with WebCapture(browser_type=browser) as capture:
                output_path = output_dir / f"{browser}.png"
                capture.capture_full_page(url, output_path)
                print(f"   ✓ {output_path}")

    @staticmethod
    def capture_responsive(url: str,
                           output_dir: Path,
                           devices: List[str] = None):
        """
        响应式设计截图

        Args:
            url: 目标URL
            output_dir: 输出目录
            devices: 设备列表
        """
        devices = devices or ['Desktop Chrome', 'iPhone 13', 'iPad Pro']

        for device in devices:
            print(f"\n📱 {device} 截图...")

            device_config = None if device == 'Desktop Chrome' else device

            with WebCapture(device=device_config) as capture:
                device_name = device.replace(' ', '_')
                output_path = output_dir / f"{device_name}.png"
                capture.capture_full_page(url, output_path)
                print(f"   ✓ {output_path}")


# ============ 使用示例 ============

if __name__ == "__main__":
    """测试Playwright截图功能"""

    from pathlib import Path

    output_dir = Path("screenshots/test")
    output_dir.mkdir(parents=True, exist_ok=True)

    test_url = "https://www.example.com"

    print("\n🚀 Playwright截图测试")
    print("=" * 60)

    # 测试1: 基础截图
    print("\n📸 测试1: 基础全页面截图")
    with WebCapture() as capture:
        capture.capture_full_page(
            url=test_url,
            output_path=output_dir / "full_page.png"
        )
    print("✓ 完成")

    # 测试2: 移动端截图
    print("\n📱 测试2: iPhone 13截图")
    with WebCapture(device='iPhone 13') as capture:
        capture.capture_full_page(
            url=test_url,
            output_path=output_dir / "mobile.png"
        )
    print("✓ 完成")

    # 测试3: 跨浏览器截图
    print("\n🌐 测试3: 跨浏览器截图")
    BatchCapture.capture_cross_browser(
        url=test_url,
        output_dir=output_dir
    )
    print("✓ 完成")

    print("\n✅ 所有测试完成")
    print(f"📁 截图保存在: {output_dir}")