"""
v1.1.0 结构化报告输出
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Python 3.11+ has datetime.UTC; 3.10 需用 timezone.utc
try:
    from datetime import UTC
except ImportError:
    from datetime import timezone

    UTC = timezone.utc


class ReportWriter:
    @staticmethod
    def write_run_result(
        output_path: Path,
        version: str,
        base_url: str,
        crawl_summary: Dict[str, Any],
        page_results: List[Dict[str, Any]],
    ) -> Path:
        payload = {
            "version": version,
            "generated_at": datetime.now(UTC).isoformat(),
            "base_url": base_url,
            "crawl_summary": crawl_summary,
            "page_results": page_results,
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        return output_path
