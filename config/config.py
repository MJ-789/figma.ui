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
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 兼容未安装 python-dotenv 的环境
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv()


class Config:
    BASE_DIR = Path(__file__).parent.parent
    REPORTS_DIR = BASE_DIR / "reports"
    # 截图统一存放在 reports/ 下，与差异图、JSON、HTML 集中在同一结果目录
    SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"
    KNOWLEDGE_DIR = BASE_DIR / "knowledge"

    @staticmethod
    def _get_bool(name: str, default: str = "false") -> bool:
        """把 .env 中的布尔字符串统一解析为 bool。"""
        return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _get_csv(name: str, default: str = "") -> list[str]:
        """把逗号分隔配置解析为列表，自动去空白项。"""
        return [p.strip() for p in os.getenv(name, default).split(",") if p.strip()]

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
    HEADLESS = _get_bool.__func__('HEADLESS', 'true')
    JSON_REPORT_PATH = REPORTS_DIR / "json" / "run_result.json"
    ELEMENT_DIFF_PATH = REPORTS_DIR / "json" / "element_diff.json"
    HTML_REPORT_PATH = REPORTS_DIR / "report.html"
    SITE_INVENTORY_PATH = REPORTS_DIR / "json" / "site_inventory.json"
    FIGMA_INVENTORY_PATH = REPORTS_DIR / "json" / "figma_inventory.json"
    PAGE_PAIRS_PATH = REPORTS_DIR / "json" / "page_pairs.json"
    TEST_PLAN_PATH = REPORTS_DIR / "json" / "test_plan.json"
    KNOWLEDGE_PAGE_PAIRS_PATH = KNOWLEDGE_DIR / "page_pairs.json"

    # v1.1.0: 多页面爬取配置
    CRAWL_ENABLED = _get_bool.__func__('CRAWL_ENABLED', 'true')
    CRAWL_MAX_DEPTH = int(os.getenv('CRAWL_MAX_DEPTH', '2'))
    CRAWL_MAX_PAGES = int(os.getenv('CRAWL_MAX_PAGES', '20'))
    CRAWL_MAX_CLICKS_PER_PAGE = int(os.getenv('CRAWL_MAX_CLICKS_PER_PAGE', '8'))
    CRAWL_SEED_PATHS = _get_csv.__func__('CRAWL_SEED_PATHS', '/')
    CRAWL_CLICK_SELECTORS = _get_csv.__func__(
        'CRAWL_CLICK_SELECTORS',
        "a[href],button,[role='link'],[role='button']"
    )
    CRAWL_EXCLUDE_KEYWORDS = [
        p.lower() for p in _get_csv.__func__(
            'CRAWL_EXCLUDE_KEYWORDS',
            "logout,signout,delete,remove"
        )
    ]

    # vNext: 自动测试代理配置
    AGENT_MODE = _get_bool.__func__('AGENT_MODE', 'false')
    DISCOVERY_ENABLED = _get_bool.__func__('DISCOVERY_ENABLED', 'true')
    DISCOVERY_MAX_DEPTH = int(os.getenv('DISCOVERY_MAX_DEPTH', str(CRAWL_MAX_DEPTH)))
    DISCOVERY_MAX_PAGES = int(os.getenv('DISCOVERY_MAX_PAGES', str(CRAWL_MAX_PAGES)))
    DISCOVERY_SEED_PATHS = _get_csv.__func__('DISCOVERY_SEED_PATHS', '/')
    DISCOVERY_EXCLUDE_KEYWORDS = [
        p.lower() for p in _get_csv.__func__(
            'DISCOVERY_EXCLUDE_KEYWORDS',
            "logout,signout,delete,remove"
        )
    ]

    PAGE_MATCH_ENABLED = _get_bool.__func__('PAGE_MATCH_ENABLED', 'true')
    PAGE_MATCH_TOP_K = int(os.getenv('PAGE_MATCH_TOP_K', '3'))
    PAGE_MATCH_MIN_CONFIDENCE = float(os.getenv('PAGE_MATCH_MIN_CONFIDENCE', '0.70'))

    AGENT_VIEWPORT_WIDTH = int(os.getenv('AGENT_VIEWPORT_WIDTH', '1440'))
    AGENT_VIEWPORT_HEIGHT = int(os.getenv('AGENT_VIEWPORT_HEIGHT', '900'))
    AGENT_HIDE_SELECTORS = _get_csv.__func__(
        'AGENT_HIDE_SELECTORS',
        ".advertisement,.cookie-banner,[class*='timestamp'],[id*='chat']"
    )

    LLM_ENABLED = _get_bool.__func__('LLM_ENABLED', 'false')
    LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'openai')
    LLM_MODEL = os.getenv('LLM_MODEL', 'gpt-4.1-mini')
    LLM_API_KEY = os.getenv('LLM_API_KEY', '')
    LLM_BASE_URL = os.getenv('LLM_BASE_URL', '')
    LLM_TIMEOUT = int(os.getenv('LLM_TIMEOUT', '60'))
    LLM_MAX_CANDIDATES = int(os.getenv('LLM_MAX_CANDIDATES', '3'))

    @classmethod
    def _parse_page_map(cls) -> dict:
        """
        解析 .env 中的 PAGE_MAP，构建 TEST_PAGES 字典。
        PAGE_MAP 格式：标签|figma_node|网站路径（逗号分隔多条）
        仅供旧版 pytest 测试流程（test_desktop.py）使用。
        新版自动代理（run_agent.py）不依赖此配置。
        """
        raw = os.getenv('PAGE_MAP', '').strip()
        if not raw:
            return {}
        vp = {
            "width": cls.AGENT_VIEWPORT_WIDTH,
            "height": cls.AGENT_VIEWPORT_HEIGHT,
        }
        pages = {}
        for entry in raw.split(','):
            parts = [p.strip() for p in entry.strip().split('|')]
            if len(parts) != 3:
                continue
            label, node_id, url_path = parts
            if not label or not node_id:
                continue
            key = label.lower().replace(' ', '_').replace('/', '_')
            pages[key] = {
                'figma_node': node_id,
                'url':        url_path,
                'wait_for':   '',
                'viewport':   vp,
                'element_map': {},
                '_label': label,
            }
        return pages

    TEST_PAGES: dict = {}

    @classmethod
    def build_test_pages(cls):
        """从 PAGE_MAP 环境变量构建 TEST_PAGES。"""
        cls.TEST_PAGES = cls._parse_page_map()

    @classmethod
    def setup_directories(cls):
        for directory in [
            cls.SCREENSHOTS_DIR / "figma",
            cls.SCREENSHOTS_DIR / "web",
            cls.SCREENSHOTS_DIR / "site",
            cls.REPORTS_DIR / "html",
            cls.REPORTS_DIR / "images",
            cls.REPORTS_DIR / "json",
            cls.KNOWLEDGE_DIR,
        ]:
            directory.mkdir(parents=True, exist_ok=True)


# 模块加载时立即从环境变量构建 TEST_PAGES
Config.build_test_pages()