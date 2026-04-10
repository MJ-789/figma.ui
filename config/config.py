"""
config/config.py  ── 全局配置中心
================================================
职责：
    从 .env 文件读取环境变量，统一暴露给整个项目使用。
    所有路径、阈值、爬取规则都在这里集中管理，
    修改参数只需改 .env 或本文件，不必动测试代码。

主要配置分组：
    [目录]  BASE_DIR / SCREENSHOTS_DIR / REPORTS_DIR
            定义截图、报告的存储根路径。

    [Figma] FIGMA_ACCESS_TOKEN / FIGMA_FILE_KEY
            连接 Figma REST API 所需的凭证（从 .env 注入，不写死在代码里）。

    [网站]  BASE_URL
            被测网站的域名，如 https://infoscribel.com。

    [阈值]  SIMILARITY_THRESHOLD
            视觉对比的相似度门槛（0~100），低于此值测试失败。默认 95%。

    [浏览器] DEFAULT_BROWSER / HEADLESS
            选用 chromium/firefox/webkit；是否无头模式（CI 时建议 true）。

    [爬取]  CRAWL_* 系列
            v1.1.0 新增。控制多页面自动发现的深度、数量、候选选择器等。

    [测试页] TEST_PAGES
            手工维护的"页面注册表"，每条记录包含
            figma_node（设计稿节点 ID）、url（相对路径）、
            wait_for（等待选择器）、viewport（视口尺寸）等。

用法：
    from config.config import Config
    print(Config.BASE_URL)
    Config.setup_directories()   # 在测试前建好所有输出目录
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    BASE_DIR = Path(__file__).parent.parent
    REPORTS_DIR = BASE_DIR / "reports"
    # 截图统一存放在 reports/ 下，与差异图、JSON、HTML 集中在同一结果目录
    SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"

    FIGMA_ACCESS_TOKEN = os.getenv('FIGMA_ACCESS_TOKEN')
    FIGMA_FILE_KEY = os.getenv('FIGMA_FILE_KEY')
    BASE_URL = os.getenv('BASE_URL', 'https://example.com')
    SIMILARITY_THRESHOLD = float(os.getenv('SIMILARITY_THRESHOLD', '95'))

    # ── v1.2.0: 结构化元素属性对比参数 ──────────────────────
    # 颜色容差：每通道（0~255）最大允许差值，默认 ±5
    COMPARE_COLOR_TOLERANCE = int(os.getenv('COMPARE_COLOR_TOLERANCE', '5'))
    # 尺寸/位置容差（px），默认 ±4
    COMPARE_SIZE_TOLERANCE = float(os.getenv('COMPARE_SIZE_TOLERANCE', '4'))
    # 字号容差（px），默认 ±1
    COMPARE_FONT_SIZE_TOLERANCE = float(os.getenv('COMPARE_FONT_SIZE_TOLERANCE', '1'))
    # 圆角容差（px），默认 ±2
    COMPARE_RADIUS_TOLERANCE = float(os.getenv('COMPARE_RADIUS_TOLERANCE', '2'))
    # 已匹配元素平均属性通过率阈值（0~1），低于此值判定 FAIL
    # 建议 0.65~0.75：Figma 与实际渲染之间存在字体/抗锯齿差异，完全一致率偏低
    COMPARE_ELEMENT_THRESHOLD = float(os.getenv('COMPARE_ELEMENT_THRESHOLD', '0.70'))
    # Figma 节点语义提取最大深度（层数），限制范围以排除图标路径、移动端组件等
    COMPARE_MAX_DEPTH = int(os.getenv('COMPARE_MAX_DEPTH', '4'))
    # 最低有效匹配节点数，用于防止样本太少导致的虚假通过
    COMPARE_MIN_MATCH_COUNT = int(os.getenv('COMPARE_MIN_MATCH_COUNT', '3'))

    DEFAULT_BROWSER = os.getenv('DEFAULT_BROWSER', 'chromium')
    HEADLESS = os.getenv('HEADLESS', 'true').lower() == 'true'
    JSON_REPORT_PATH = REPORTS_DIR / "json" / "run_result.json"
    ELEMENT_DIFF_PATH = REPORTS_DIR / "json" / "element_diff.json"
    HTML_REPORT_PATH = REPORTS_DIR / "report.html"

    # v1.1.0: 多页面爬取配置
    CRAWL_ENABLED = os.getenv('CRAWL_ENABLED', 'true').lower() == 'true'
    CRAWL_MAX_DEPTH = int(os.getenv('CRAWL_MAX_DEPTH', '2'))
    CRAWL_MAX_PAGES = int(os.getenv('CRAWL_MAX_PAGES', '20'))
    CRAWL_MAX_CLICKS_PER_PAGE = int(os.getenv('CRAWL_MAX_CLICKS_PER_PAGE', '8'))
    CRAWL_SEED_PATHS = [p.strip() for p in os.getenv('CRAWL_SEED_PATHS', '/').split(',') if p.strip()]
    CRAWL_CLICK_SELECTORS = [
        p.strip() for p in os.getenv(
            'CRAWL_CLICK_SELECTORS',
            "a[href],button,[role='link'],[role='button']"
        ).split(',') if p.strip()
    ]
    CRAWL_EXCLUDE_KEYWORDS = [
        p.strip().lower() for p in os.getenv(
            'CRAWL_EXCLUDE_KEYWORDS',
            "logout,signout,delete,remove"
        ).split(',') if p.strip()
    ]

    # 全局视口默认值
    _DEFAULT_WIDTH  = int(os.getenv('HOMEPAGE_WIDTH',  '1920'))
    _DEFAULT_HEIGHT = int(os.getenv('HOMEPAGE_HEIGHT', '900'))

    @classmethod
    def _parse_page_map(cls) -> dict:
        """
        解析 .env 中的 PAGE_MAP，构建 TEST_PAGES 字典。

        PAGE_MAP 格式（多条用英文逗号分隔）：
            标签|figma_node|网站路径
        示例：
            PAGE_MAP=Home|15661-163|/ , category|15661-164|/list/Finance

        若 PAGE_MAP 未设置，退回到旧版单页配置（HOMEPAGE_FIGMA_NODE 等）。
        """
        raw = os.getenv('PAGE_MAP', '').strip()
        if raw:
            pages = {}
            for entry in raw.split(','):
                parts = [p.strip() for p in entry.strip().split('|')]
                if len(parts) != 3:
                    continue
                label, node_id, url_path = parts
                if not label or not node_id:
                    continue
                # 把标签转为安全 key（小写 + 下划线）
                key = label.lower().replace(' ', '_').replace('/', '_')
                pages[key] = {
                    'figma_node': node_id,
                    'url':        url_path,
                    'wait_for':   '',          # 不等待特定元素（避免误判）
                    'viewport': {
                        'width':  cls._DEFAULT_WIDTH,
                        'height': cls._DEFAULT_HEIGHT,
                    },
                    'element_map': {},
                    '_label': label,           # 原始标签，供报告使用
                }
            if pages:
                return pages

        # 兼容旧版单页配置
        return {
            'homepage': {
                'figma_node': os.getenv('HOMEPAGE_FIGMA_NODE', '15661-163'),
                'url':        os.getenv('HOMEPAGE_URL', '/'),
                'wait_for':   os.getenv('HOMEPAGE_WAIT_FOR', ''),
                'viewport': {
                    'width':  cls._DEFAULT_WIDTH,
                    'height': cls._DEFAULT_HEIGHT,
                },
                'element_map': {},
                '_label': 'homepage',
            }
        }

    # TEST_PAGES 由 PAGE_MAP 动态生成
    TEST_PAGES: dict = {}   # 占位，实际值在类定义后填充

    @classmethod
    def build_test_pages(cls):
        """（重新）从环境变量构建 TEST_PAGES，load_dotenv 后调用一次即可。"""
        cls.TEST_PAGES = cls._parse_page_map()

    @classmethod
    def setup_directories(cls):
        for directory in [
            cls.SCREENSHOTS_DIR / "figma",
            cls.SCREENSHOTS_DIR / "web",
            cls.REPORTS_DIR / "html",
            cls.REPORTS_DIR / "images",
            cls.REPORTS_DIR / "json",
        ]:
            directory.mkdir(parents=True, exist_ok=True)


# 模块加载时立即从环境变量构建 TEST_PAGES
Config.build_test_pages()