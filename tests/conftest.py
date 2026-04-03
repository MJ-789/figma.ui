"""
Pytest 钩子：与手动 sync Playwright 共存，并跳过未安装的浏览器测试。
"""

import os
from pathlib import Path

import pytest


def _playwright_firefox_installed() -> bool:
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not root.is_dir():
        return False
    for child in root.glob("firefox-*"):
        exe = child / "firefox" / "firefox.exe"
        if exe.is_file():
            return True
    return False


def pytest_collection_modifyitems(config, items):
    if _playwright_firefox_installed():
        return
    skip_firefox = pytest.mark.skip(
        reason="Playwright Firefox 未安装，执行: playwright install firefox"
    )
    for item in items:
        if "homepage_firefox" in item.nodeid:
            item.add_marker(skip_firefox)
