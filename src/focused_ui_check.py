"""
src/focused_ui_check.py -- 3-page focused visual + element comparison

Runs a fixed set of pages:
- Home
- Category (Pharmaceuticals)
- Details (first Pharmaceuticals article)

Outputs:
- reports/json/focused_run_result.json
- reports/json/focused_element_diffs.json
- reports/focused_ui_report.html
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from config.config import Config
from src.auto_mapper import AutoMapper
from src.dom_extractor import DOMExtractor
from src.element_compare import ElementCompare
from src.figma_client import FigmaClient
from src.figma_extractor import FigmaExtractor
from src.image_compare import ImageCompare
from src.report_writer import ReportWriter
from src.web_capture import WebCapture

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


FOCUSED_PAGES = [
    {
        "key": "home",
        "label": "Home",
        "figma_node": "15480:75",
        "figma_url": "https://www.figma.com/design/fYPLGfJU35LLvqgBvUQ1bG/%E8%B5%84%E8%AE%AF%E7%AB%99?node-id=15480-75&p=f&m=dev",
        "site_url": "https://newsdrafte.com/",
    },
    {
        "key": "category",
        "label": "Category",
        "figma_node": "15480:1305",
        "figma_url": "https://www.figma.com/design/fYPLGfJU35LLvqgBvUQ1bG/%E8%B5%84%E8%AE%AF%E7%AB%99?node-id=15480-1305&p=f&m=dev",
        "site_url": "https://newsdrafte.com/list/Pharmaceuticals",
    },
    {
        "key": "details",
        "label": "Details",
        "figma_node": "15497:1924",
        "figma_url": "https://www.figma.com/design/fYPLGfJU35LLvqgBvUQ1bG/%E8%B5%84%E8%AE%AF%E7%AB%99?node-id=15497-1924&p=f&m=dev",
        "site_url": "https://newsdrafte.com/anti-allergy-medications-scientific-overview-of-types-mechanisms-medical-context",
    },
]


def _b64(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def _clean_output_dirs() -> None:
    Config.setup_directories()
    json_dir = Config.REPORTS_DIR / "json"
    for file in json_dir.glob("focused_*"):
        if file.is_file():
            file.unlink()
    for file in json_dir.glob("element_diff_*.json"):
        if file.is_file():
            file.unlink()
    for target in [
        Config.REPORTS_DIR / "focused_ui_report.html",
        Config.REPORTS_DIR / "focused_ui_summary.md",
    ]:
        if target.exists():
            target.unlink()


def _top_diff_items(result: Dict[str, Any], limit: int = 12) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for el in result.get("elements", []):
        if not el.get("matched"):
            items.append(
                {
                    "figma_name": el.get("figma_name", ""),
                    "figma_id": el.get("figma_id", ""),
                    "dom_selector": None,
                    "issue": "unmatched",
                    "details": "No matched DOM element",
                }
            )
            continue
        for prop, detail in el.get("properties", {}).items():
            if detail.get("passed"):
                continue
            items.append(
                {
                    "figma_name": el.get("figma_name", ""),
                    "figma_id": el.get("figma_id", ""),
                    "dom_selector": el.get("dom_selector", ""),
                    "issue": prop,
                    "details": {
                        "figma": detail.get("figma"),
                        "web": detail.get("web"),
                        "diff": detail.get("diff"),
                    },
                }
            )
    return items[:limit]


def _render_html(pages: List[Dict[str, Any]], output_path: Path) -> Path:
    cards = []
    for page in pages:
        pixel = page["pixel"]
        elem = page["element"]

        diff_rows = []
        for item in page["top_diffs"]:
            if item["issue"] == "unmatched":
                diff_rows.append(
                    f"<tr><td>{item['figma_name']}</td><td>unmatched</td><td colspan='3'>No matched DOM element</td></tr>"
                )
            else:
                details = item["details"]
                diff_rows.append(
                    "<tr>"
                    f"<td>{item['figma_name']}</td>"
                    f"<td>{item['issue']}</td>"
                    f"<td>{details.get('figma','')}</td>"
                    f"<td>{details.get('web','')}</td>"
                    f"<td>{details.get('diff','')}</td>"
                    "</tr>"
                )
        if not diff_rows:
            diff_rows.append("<tr><td colspan='5'>No visual property differences captured in top list.</td></tr>")

        cards.append(
            f"""
<section class="card">
  <h2>{page['label']}</h2>
  <div class="links">
    <a href="{page['figma_url']}">Figma 原型</a>
    <a href="{page['site_url']}">对比网站</a>
  </div>
  <div class="stats">
    <div><b>像素相似度</b><span>{pixel['similarity']:.2f}%</span></div>
    <div><b>元素得分</b><span>{elem['overall_score']:.2%}</span></div>
    <div><b>元素覆盖率</b><span>{elem['coverage_rate']:.2%}</span></div>
    <div><b>元素匹配数</b><span>{elem['total_matched']}</span></div>
  </div>
  <div class="img-grid">
    <div><div class="lbl">Figma</div><img src="{_b64(pixel['figma_path'])}" /></div>
    <div><div class="lbl">Website</div><img src="{_b64(pixel['web_path'])}" /></div>
    <div><div class="lbl">Diff</div><img src="{_b64(pixel['diff_path'])}" /></div>
    <div><div class="lbl">Side By Side</div><img src="{_b64(pixel['compare_path'])}" /></div>
  </div>
  <h3>差异列表</h3>
  <table>
    <thead>
      <tr><th>Figma 元素</th><th>差异项</th><th>Figma</th><th>Web</th><th>Diff</th></tr>
    </thead>
    <tbody>
      {''.join(diff_rows)}
    </tbody>
  </table>
</section>
"""
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Focused UI Report</title>
  <style>
    body{{font-family:Segoe UI,Arial,sans-serif;background:#f4f7fb;color:#1f2937;margin:0}}
    .wrap{{max-width:1500px;margin:0 auto;padding:24px}}
    header{{background:#111827;color:#fff;padding:24px 28px}}
    header h1{{margin:0 0 8px;font-size:28px}}
    header p{{margin:0;color:#cbd5e1}}
    .card{{background:#fff;border-radius:12px;padding:20px;margin:20px 0;box-shadow:0 2px 10px rgba(0,0,0,.08)}}
    h2{{margin:0 0 10px}}
    h3{{margin:18px 0 10px}}
    .links{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}}
    .links a{{color:#2563eb;text-decoration:none}}
    .stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:16px}}
    .stats div{{background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:12px}}
    .stats b{{display:block;font-size:12px;color:#6b7280;margin-bottom:6px}}
    .stats span{{font-size:24px;font-weight:700}}
    .img-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:18px}}
    .img-grid .lbl{{font-size:12px;color:#6b7280;margin-bottom:6px;font-weight:700}}
    .img-grid img{{width:100%;border:1px solid #e5e7eb;border-radius:8px;background:#fff}}
    table{{width:100%;border-collapse:collapse}}
    th,td{{border-bottom:1px solid #e5e7eb;padding:10px 8px;text-align:left;font-size:13px;vertical-align:top}}
    th{{background:#f8fafc}}
    @media (max-width: 900px) {{
      .stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}
      .img-grid{{grid-template-columns:1fr}}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Focused UI Report</h1>
    <p>3-page visual + element comparison. Content text is not used as the core matching signal.</p>
  </header>
  <div class="wrap">
    {''.join(cards)}
  </div>
</body>
</html>"""
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _render_markdown(pages: List[Dict[str, Any]], output_path: Path) -> Path:
    lines = [
        "# Focused UI Summary",
        "",
        "This report compares 3 fixed pages. Content text is not used as the core matching signal; the comparison focuses on layout, typography, colors, size, and radius.",
        "",
    ]
    for page in pages:
        elem = page["element"]
        pixel = page["pixel"]
        lines.extend(
            [
                f"## {page['label']}",
                "",
                f"- Figma: {page['figma_url']}",
                f"- Website: {page['site_url']}",
                f"- Pixel similarity: {pixel['similarity']:.2f}%",
                f"- Element overall score: {elem['overall_score']:.2%}",
                f"- Element coverage: {elem['coverage_rate']:.2%}",
                f"- Matched elements: {elem['total_matched']}",
                f"- Unmatched elements: {elem['total_unmatched']}",
                "",
                "Top differences:",
            ]
        )
        if page["top_diffs"]:
            for item in page["top_diffs"][:8]:
                if item["issue"] == "unmatched":
                    lines.append(f"- `{item['figma_name']}`: no matched DOM element")
                else:
                    details = item["details"]
                    lines.append(
                        f"- `{item['figma_name']}` `{item['issue']}`: Figma=`{details.get('figma','')}` Web=`{details.get('web','')}` Diff=`{details.get('diff','')}`"
                    )
        else:
            lines.append("- No major differences captured in the top list.")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def run() -> Dict[str, Any]:
    _clean_output_dirs()

    Config.setup_directories()
    version = (Config.BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    figma = FigmaClient()
    comparator = ImageCompare(threshold=Config.SIMILARITY_THRESHOLD)
    figma_extractor = FigmaExtractor()
    dom_extractor = DOMExtractor()
    auto_mapper = AutoMapper()
    element_compare = ElementCompare()

    page_results: List[Dict[str, Any]] = []
    element_pages: List[Dict[str, Any]] = []

    with WebCapture(
        browser_type=Config.DEFAULT_BROWSER,
        headless=Config.HEADLESS,
        viewport={"width": Config.AGENT_VIEWPORT_WIDTH, "height": Config.AGENT_VIEWPORT_HEIGHT},
    ) as capture:
        for page in FOCUSED_PAGES:
            key = page["key"]
            figma_path = Config.SCREENSHOTS_DIR / "figma" / f"focused_{key}.png"
            web_path = Config.SCREENSHOTS_DIR / "web" / f"focused_{key}.png"
            diff_path = Config.REPORTS_DIR / "images" / f"focused_{key}_diff.png"
            compare_path = Config.REPORTS_DIR / "images" / f"focused_{key}_compare.png"

            figma.save_node_to_file(page["figma_node"], figma_path, scale=2)

            capture.page.set_viewport_size({"width": Config.AGENT_VIEWPORT_WIDTH, "height": Config.AGENT_VIEWPORT_HEIGHT})
            capture.page.goto(page["site_url"], wait_until="networkidle", timeout=30000)
            if Config.AGENT_HIDE_SELECTORS:
                capture.hide_elements(Config.AGENT_HIDE_SELECTORS)
            capture.page.screenshot(path=str(web_path), full_page=True)

            similarity = comparator.calculate_similarity(figma_path, web_path)
            comparator.generate_diff_image(figma_path, web_path, diff_path)
            comparator.generate_side_by_side(figma_path, web_path, compare_path, labels=("Figma", "Website"))

            page_results.append(
                {
                    "page_name": page["label"],
                    "figma_name": page["label"],
                    "browser": Config.DEFAULT_BROWSER,
                    "site_url": page["site_url"],
                    "site_path": page["site_url"].replace(Config.BASE_URL, "") or "/",
                    "similarity": float(similarity),
                    "threshold": float(Config.SIMILARITY_THRESHOLD),
                    "passed": similarity >= Config.SIMILARITY_THRESHOLD,
                    "figma_path": str(figma_path),
                    "web_path": str(web_path),
                    "diff_path": str(diff_path),
                    "compare_path": str(compare_path),
                    "status": "ok",
                }
            )

            node_json = figma.get_node_json(page["figma_node"])
            ReportWriter._write_json(Config.REPORTS_DIR / "json" / f"focused_figma_{key}.json", node_json)
            figma_elements = figma_extractor.extract_semantic(node_json, max_depth=Config.COMPARE_MAX_DEPTH)
            root_frame = next((e for e in figma_elements if e.node_type in ("FRAME", "COMPONENT")), None)
            auto_map = auto_mapper.generate(figma_elements=figma_elements, page=capture.page, root_frame=root_frame)
            dom_elements = dom_extractor.extract(capture.page, list(dict.fromkeys(auto_map.values())))

            result = element_compare.compare(
                figma_elements=figma_elements,
                dom_elements=dom_elements,
                element_map={},
                id_element_map=auto_map,
                threshold=Config.COMPARE_ELEMENT_THRESHOLD,
                color_tol=Config.COMPARE_COLOR_TOLERANCE,
                size_tol=Config.COMPARE_SIZE_TOLERANCE,
                font_size_tol=Config.COMPARE_FONT_SIZE_TOLERANCE,
                radius_tol=Config.COMPARE_RADIUS_TOLERANCE,
                min_match_count=Config.COMPARE_MIN_MATCH_COUNT,
            )

            element_payload = {
                "version": version,
                "generated_at": datetime.now(UTC).isoformat(),
                "page_key": key,
                "page_label": page["label"],
                "figma_node": page["figma_node"],
                "figma_url": page["figma_url"],
                "site_url": page["site_url"],
                "result": result,
                "auto_mapped_count": len(auto_map),
            }
            ReportWriter._write_json(Config.REPORTS_DIR / "json" / f"element_diff_{key}.json", element_payload)
            element_pages.append(
                {
                    "key": key,
                    "label": page["label"],
                    "figma_url": page["figma_url"],
                    "site_url": page["site_url"],
                    "pixel": {
                        "similarity": float(similarity),
                        "figma_path": str(figma_path),
                        "web_path": str(web_path),
                        "diff_path": str(diff_path),
                        "compare_path": str(compare_path),
                    },
                    "element": result,
                    "top_diffs": _top_diff_items(result),
                }
            )

    focused_run = {
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": Config.BASE_URL,
        "page_results": page_results,
    }
    ReportWriter._write_json(Config.REPORTS_DIR / "json" / "focused_run_result.json", focused_run)
    ReportWriter._write_json(Config.REPORTS_DIR / "json" / "focused_element_diffs.json", {"pages": element_pages})
    html_path = _render_html(element_pages, Config.REPORTS_DIR / "focused_ui_report.html")
    md_path = _render_markdown(element_pages, Config.REPORTS_DIR / "focused_ui_summary.md")

    print(f"[OK] Focused report generated: {html_path}")
    print(f"[OK] Focused summary generated: {md_path}")
    return {
        "focused_run": focused_run,
        "element_pages": element_pages,
        "html_report": str(html_path),
        "markdown_summary": str(md_path),
    }


if __name__ == "__main__":
    run()
