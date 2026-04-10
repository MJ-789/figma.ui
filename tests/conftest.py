"""
tests/conftest.py  ── Pytest 全局钩子配置
================================================
职责：
    pytest 启动时自动加载本文件，统一处理会话级操作。

当前钩子：
    pytest_sessionfinish()
        ── 所有测试结束后，读取 JSON 结果文件，
           自动生成自包含的可视化 HTML 报告（reports/report.html）。

为什么需要这个文件：
    - 本项目用手写 sync_playwright()（WebCapture），而非 pytest-playwright 插件。
    - pytest.ini 中已加 -p no:playwright 禁用插件，因此不能用插件的 browser fixture。
"""

import pytest
from config.config import Config


def pytest_sessionfinish(session, exitstatus):
    """测试全部结束后，自动生成可视化 HTML 报告。"""
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
