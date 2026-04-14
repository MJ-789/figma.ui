#!/usr/bin/env python
"""
run_agent.py  ── 自动测试代理一键入口
=====================================================
用法：
    python run_agent.py          # 完整执行（发现→索引→配对→计划→截图对比→报告）
    python run_agent.py --dry    # 只做发现+配对+计划，跳过实际截图和对比
"""

import sys
from src.run_orchestrator import RunOrchestrator


def main():
    dry_run = "--dry" in sys.argv
    orchestrator = RunOrchestrator(dry_run=dry_run)
    orchestrator.run()


if __name__ == "__main__":
    main()
