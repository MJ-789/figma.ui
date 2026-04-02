import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    BASE_DIR = Path(__file__).parent.parent
    SCREENSHOTS_DIR = BASE_DIR / "screenshots"
    REPORTS_DIR = BASE_DIR / "reports"

    FIGMA_ACCESS_TOKEN = os.getenv('FIGMA_ACCESS_TOKEN')
    FIGMA_FILE_KEY = os.getenv('FIGMA_FILE_KEY')
    BASE_URL = os.getenv('BASE_URL', 'https://example.com')
    SIMILARITY_THRESHOLD = float(os.getenv('SIMILARITY_THRESHOLD', '95'))
    DEFAULT_BROWSER = os.getenv('DEFAULT_BROWSER', 'chromium')
    HEADLESS = os.getenv('HEADLESS', 'true').lower() == 'true'
    JSON_REPORT_PATH = REPORTS_DIR / "json" / "run_result.json"

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

    TEST_PAGES = {
        'homepage': {
            'figma_node': '12539:1073',
            'url': '/',
            'wait_for': '.main-content',
            'viewport': {
                'width': 1440,
                'height': 900
            }
        }
    }

    @classmethod
    def setup_directories(cls):
        for directory in [cls.SCREENSHOTS_DIR / "figma",
                          cls.SCREENSHOTS_DIR / "web",
                          cls.REPORTS_DIR / "html",
                          cls.REPORTS_DIR / "images",
                          cls.REPORTS_DIR / "json"]:
            directory.mkdir(parents=True, exist_ok=True)