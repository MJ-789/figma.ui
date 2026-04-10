"""
tests/conftest.py  ── Pytest 全局钩子配置
================================================
职责：
    pytest 启动时自动加载本文件，在此做"测试前置条件"的统一检查，
    让个别用例在环境不满足时优雅跳过而不是崩溃报错。

当前钩子：
    pytest_collection_modifyitems()
        ── 在用例收集完成后遍历所有用例。
        ── 若本机未安装 Playwright Firefox（检查 LOCALAPPDATA/ms-playwright/firefox-* 目录），
           则自动给 homepage_firefox 用例打上 skip 标记。
        ── 安装 Firefox 的命令：playwright install firefox

为什么需要这个文件：
    - 本项目用手写 sync_playwright()（WebCapture），而非 pytest-playwright 插件。
    - pytest.ini 中已加 -p no:playwright 禁用插件，因此不能用插件的 browser fixture。
    - Firefox/WebKit 在 Windows 上需要额外安装，跳过比报错更友好。

扩展建议：
    如需在所有用例之前做一次环境预检（网络通、Figma Token 有效等），
    也可在此文件中用 pytest_configure() 或 session 级 fixture 来实现。
"""

import os
from pathlib import Path

import pytest
from config.config import Config


def _playwright_firefox_installed() -> bool:
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    if not root.is_dir():
        return False
    for child in root.glob("firefox-*"):
        exe = child / "firefox" / "firefox.exe"
        if exe.is_file():
            return True
    return False


def pytest_sessionfinish(session, exitstatus):
    """测试全部结束后，自动生成可视化 HTML 报告。"""
    # 只要有任意一份 JSON 结果就生成报告
    has_pixel   = Config.JSON_REPORT_PATH.exists()
    has_element = Config.ELEMENT_DIFF_PATH.exists()
    if not has_pixel and not has_element:
        return
    try:
        from src.html_reporter import generate_report
        path = generate_report()
        print(f"\n📄 可视化报告: {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n[WARN] 可视化报告生成失败: {exc}")


def pytest_collection_modifyitems(config, items):
    if _playwright_firefox_installed():
        return
    skip_firefox = pytest.mark.skip(
        reason="Playwright Firefox 未安装，执行: playwright install firefox"
    )
    for item in items:
        if "homepage_firefox" in item.nodeid:
            item.add_marker(skip_firefox)
