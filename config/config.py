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


# ── 模块级辅助函数（在类体定义阶段可安全调用，无需 __func__ hack）──────────
def _env_bool(name: str, default: str = "false") -> bool:
    """从环境变量读取布尔值，支持 1/true/yes/on 写法。"""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str = "") -> list:
    """从环境变量读取逗号分隔列表，自动过滤空白项。"""
    return [p.strip() for p in os.getenv(name, default).split(",") if p.strip()]


class Config:
    BASE_DIR = Path(__file__).parent.parent
    REPORTS_DIR = BASE_DIR / "reports"
    # 截图统一存放在 reports/ 下，与差异图、JSON、HTML 集中在同一结果目录
    SCREENSHOTS_DIR = REPORTS_DIR / "screenshots"

    FIGMA_ACCESS_TOKEN = os.getenv('FIGMA_ACCESS_TOKEN')

    # ── Figma 设计稿：两种配法，择一 ──────────────────
    # 方式 A (推荐)：在 .env 里贴完整 URL
    #     FIGMA_DESIGN_URL=https://www.figma.com/design/<key>/Slug?node-id=15480-72
    # 方式 B (兼容)：分别填
    #     FIGMA_FILE_KEY=...
    #     FIGMA_TARGET_NODE_ID=15480:72
    FIGMA_DESIGN_URL = os.getenv('FIGMA_DESIGN_URL', '').strip()

    _url_file_key: str | None = None
    _url_node_id: str | None = None
    if FIGMA_DESIGN_URL:
        # 延迟 import 避免循环依赖 (config.py -> src.figma_url -> config.Config)
        from src.figma_url import parse_figma_url as _parse_figma_url
        _info = _parse_figma_url(FIGMA_DESIGN_URL)
        _url_file_key = _info.file_key
        _url_node_id = _info.node_id

    FIGMA_FILE_KEY = os.getenv('FIGMA_FILE_KEY') or _url_file_key
    FIGMA_TARGET_NODE_ID = (
        os.getenv('FIGMA_TARGET_NODE_ID', '').replace("-", ":").strip()
        or (_url_node_id or '')
    )
    FIGMA_INDEX_MIN_WIDTH = float(os.getenv('FIGMA_INDEX_MIN_WIDTH', '0'))
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
    HEADLESS = _env_bool('HEADLESS', 'true')
    JSON_REPORT_PATH = REPORTS_DIR / "json" / "run_result.json"
    ELEMENT_DIFF_PATH = REPORTS_DIR / "json" / "element_diff.json"
    HTML_REPORT_PATH = REPORTS_DIR / "report.html"
    SITE_INVENTORY_PATH = REPORTS_DIR / "json" / "site_inventory.json"
    FIGMA_INVENTORY_PATH = REPORTS_DIR / "json" / "figma_inventory.json"
    PAGE_PAIRS_PATH = REPORTS_DIR / "json" / "page_pairs.json"
    TEST_PLAN_PATH = REPORTS_DIR / "json" / "test_plan.json"

    # v1.1.0: 多页面爬取配置（CRAWL_* 为主配置）
    CRAWL_ENABLED = _env_bool('CRAWL_ENABLED', 'true')
    CRAWL_MAX_DEPTH = int(os.getenv('CRAWL_MAX_DEPTH', '2'))
    CRAWL_MAX_PAGES = int(os.getenv('CRAWL_MAX_PAGES', '20'))
    CRAWL_MAX_CLICKS_PER_PAGE = int(os.getenv('CRAWL_MAX_CLICKS_PER_PAGE', '8'))
    CRAWL_SEED_PATHS = _env_csv('CRAWL_SEED_PATHS', '/')
    CRAWL_CLICK_SELECTORS = _env_csv(
        'CRAWL_CLICK_SELECTORS',
        "a[href],button,[role='link'],[role='button']"
    )
    CRAWL_EXCLUDE_KEYWORDS = [
        p.lower() for p in _env_csv('CRAWL_EXCLUDE_KEYWORDS', "logout,signout,delete,remove")
    ]

    # 自动测试代理配置（DISCOVERY_* 未设置时自动复用 CRAWL_* 值）
    AGENT_MODE = _env_bool('AGENT_MODE', 'false')
    DISCOVERY_ENABLED = _env_bool('DISCOVERY_ENABLED', 'true')
    DISCOVERY_MAX_DEPTH = int(os.getenv('DISCOVERY_MAX_DEPTH', os.getenv('CRAWL_MAX_DEPTH', '2')))
    DISCOVERY_MAX_PAGES = int(os.getenv('DISCOVERY_MAX_PAGES', os.getenv('CRAWL_MAX_PAGES', '20')))
    DISCOVERY_SEED_PATHS = _env_csv('DISCOVERY_SEED_PATHS') or _env_csv('CRAWL_SEED_PATHS', '/')
    DISCOVERY_EXCLUDE_KEYWORDS = (
        [p.lower() for p in _env_csv('DISCOVERY_EXCLUDE_KEYWORDS')]
        or [p.lower() for p in _env_csv('CRAWL_EXCLUDE_KEYWORDS', "logout,signout,delete,remove")]
    )

    PAGE_MATCH_ENABLED = _env_bool('PAGE_MATCH_ENABLED', 'true')
    PAGE_MATCH_TOP_K = int(os.getenv('PAGE_MATCH_TOP_K', '3'))
    PAGE_MATCH_MIN_CONFIDENCE = float(os.getenv('PAGE_MATCH_MIN_CONFIDENCE', '0.70'))
    PAGE_MATCH_WEIGHT_NAME = float(os.getenv('PAGE_MATCH_WEIGHT_NAME', '0.35'))
    PAGE_MATCH_WEIGHT_TEXT = float(os.getenv('PAGE_MATCH_WEIGHT_TEXT', '0.10'))
    PAGE_MATCH_WEIGHT_STRUCTURE = float(os.getenv('PAGE_MATCH_WEIGHT_STRUCTURE', '0.30'))
    PAGE_MATCH_WEIGHT_PAGE_TYPE = float(os.getenv('PAGE_MATCH_WEIGHT_PAGE_TYPE', '0.25'))

    AGENT_VIEWPORT_WIDTH = int(os.getenv('AGENT_VIEWPORT_WIDTH', '1440'))
    AGENT_VIEWPORT_HEIGHT = int(os.getenv('AGENT_VIEWPORT_HEIGHT', '900'))
    AGENT_HIDE_SELECTORS = _env_csv(
        'AGENT_HIDE_SELECTORS',
        ".advertisement,.cookie-banner,[class*='timestamp'],[id*='chat']"
    )

    # LLM 辅助配置（当前版本暂未接入，预留扩展用）
    LLM_ENABLED = _env_bool('LLM_ENABLED', 'false')
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
        """确保必备输出目录存在。

        focused_ui_check 把所有图片/报告打平放进
        ``reports/focused_ui_report/``，run_orchestrator 则写进
        ``reports/agent_run/``，所以这里只建共享的 JSON 目录。
        其他脚本需要写截图时会在写入处按需 mkdir。
        """
        (cls.REPORTS_DIR / "json").mkdir(parents=True, exist_ok=True)


# 模块加载时立即从环境变量构建 TEST_PAGES
Config.build_test_pages()