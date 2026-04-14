#!/usr/bin/env python
"""
run_agent.py  ── 自动测试代理一键入口
=====================================================
用法：
    python run_agent.py              # 完整执行（复用已有 inventory 缓存）
    python run_agent.py --dry        # 只做发现+配对+计划，跳过实际截图对比
    python run_agent.py --fresh      # 强制重新爬取网站和请求 Figma API
    python run_agent.py --dry --fresh
"""

import sys
from src.run_orchestrator import RunOrchestrator


def main():
    args = sys.argv[1:]
    dry_run = "--dry" in args
    fresh = "--fresh" in args
    orchestrator = RunOrchestrator(dry_run=dry_run, reuse_inventory=not fresh)
    orchestrator.run()


if __name__ == "__main__":
    main()
