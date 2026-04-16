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
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

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

STABLE_BLOCKS = [
    {
        "key": "header",
        "label": "Header",
        "figma_patterns": ["header", "导航", "nav", "top", "logo", "menu"],
        "web_selectors": ["header", "[role='banner']", "nav", ".header"],
        "fallback_ratio": [0.0, 0.0, 1.0, 0.14],
    },
    {
        "key": "hero",
        "label": "Hero",
        "figma_patterns": ["hero", "banner", "首屏", "main visual"],
        "web_selectors": ["main section:first-of-type", ".hero", "[class*='hero']", "[class*='banner']"],
        "fallback_ratio": [0.0, 0.14, 1.0, 0.38],
    },
    {
        "key": "content",
        "label": "Content",
        "figma_patterns": ["content", "list", "card", "article", "details", "category"],
        "web_selectors": ["main", "[role='main']", ".content", ".list", "article"],
        "fallback_ratio": [0.0, 0.38, 1.0, 0.86],
    },
    {
        "key": "footer",
        "label": "Footer",
        "figma_patterns": ["footer", "底部", "copyright", "company", "categories"],
        "web_selectors": ["footer", "[role='contentinfo']", ".footer"],
        "fallback_ratio": [0.0, 0.86, 1.0, 1.0],
    },
]

_NOISE_NAME_RE = re.compile(r"^(image\s+\d+|frame\s*\d*|group\s*\d*)$", re.IGNORECASE)


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
            try:
                file.unlink()
            except PermissionError:
                pass
    for file in json_dir.glob("element_diff_*.json"):
        if file.is_file():
            try:
                file.unlink()
            except PermissionError:
                pass
    for target in [
        Config.REPORTS_DIR / "focused_ui_report.html",
        Config.REPORTS_DIR / "focused_ui_summary.md",
    ]:
        if target.exists():
            try:
                target.unlink()
            except PermissionError:
                pass
    for file in (Config.REPORTS_DIR / "images").glob("focused_*"):
        if file.is_file():
            try:
                file.unlink()
            except PermissionError:
                pass
    for file in (Config.SCREENSHOTS_DIR / "figma").glob("focused_*"):
        if file.is_file():
            try:
                file.unlink()
            except PermissionError:
                pass
    for file in (Config.SCREENSHOTS_DIR / "web").glob("focused_*"):
        if file.is_file():
            try:
                file.unlink()
            except PermissionError:
                pass


def _iter_nodes(root: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    stack = [root]
    while stack:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.get("children", [])))
    return result


def _node_box(node: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    box = node.get("absoluteBoundingBox") or {}
    if not box:
        return None
    x, y, w, h = float(box.get("x", 0)), float(box.get("y", 0)), float(box.get("width", 0)), float(box.get("height", 0))
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _pick_figma_block_node(root: Dict[str, Any], block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    root_box = _node_box(root)
    if not root_box:
        return None
    rx, ry, rw, rh = root_box
    candidates = []
    patterns = [p.lower() for p in block["figma_patterns"]]
    for node in _iter_nodes(root):
        box = _node_box(node)
        if not box:
            continue
        x, y, w, h = box
        if w < 40 or h < 20:
            continue
        name = (node.get("name") or "").lower()
        if not any(p in name for p in patterns):
            continue
        y_rel = (y - ry) / max(rh, 1)
        h_rel = h / max(rh, 1)
        area_rel = (w * h) / max(rw * rh, 1)
        key = block["key"]
        if key == "header":
            score = (1 - min(max(y_rel, 0), 1)) * 1.8 + max(0, 0.25 - abs(h_rel - 0.1)) + area_rel
        elif key == "footer":
            score = min(max(y_rel, 0), 1) * 1.8 + max(0, 0.25 - abs(h_rel - 0.1)) + area_rel
        elif key == "hero":
            score = max(0, 1 - abs(y_rel - 0.2)) + area_rel * 1.6
        else:
            score = max(0, 1 - abs(y_rel - 0.55)) + area_rel * 1.4
        candidates.append((score, node))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _ratio_to_abs_box(root_box: Tuple[float, float, float, float], ratio: List[float]) -> Tuple[float, float, float, float]:
    rx, ry, rw, rh = root_box
    x1 = rx + rw * ratio[0]
    y1 = ry + rh * ratio[1]
    x2 = rx + rw * ratio[2]
    y2 = ry + rh * ratio[3]
    return x1, y1, max(1.0, x2 - x1), max(1.0, y2 - y1)


def _crop_from_figma_image(
    figma_full_path: Path,
    root_box: Tuple[float, float, float, float],
    abs_box: Tuple[float, float, float, float],
    output_path: Path,
) -> Path:
    img = Image.open(figma_full_path)
    rx, ry, rw, rh = root_box
    x, y, w, h = abs_box
    sx = img.width / max(rw, 1)
    sy = img.height / max(rh, 1)
    left = int(max(0, (x - rx) * sx))
    top = int(max(0, (y - ry) * sy))
    right = int(min(img.width, left + w * sx))
    bottom = int(min(img.height, top + h * sy))
    if right <= left or bottom <= top:
        left, top, right, bottom = 0, 0, img.width, img.height
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.crop((left, top, right, bottom)).save(output_path)
    return output_path


def _crop_by_ratio_from_image(img_path: Path, ratio: List[float], output_path: Path) -> Path:
    img = Image.open(img_path)
    x1 = int(max(0, ratio[0] * img.width))
    y1 = int(max(0, ratio[1] * img.height))
    x2 = int(min(img.width, ratio[2] * img.width))
    y2 = int(min(img.height, ratio[3] * img.height))
    if x2 <= x1 or y2 <= y1:
        x1, y1, x2, y2 = 0, 0, img.width, img.height
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.crop((x1, y1, x2, y2)).save(output_path)
    return output_path


def _screenshot_web_block(page, selectors: List[str], output_path: Path) -> bool:
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            box = loc.bounding_box()
            if not box or box.get("width", 0) < 20 or box.get("height", 0) < 20:
                continue
            loc.screenshot(path=str(output_path))
            return True
        except Exception:
            continue
    return False


def _element_in_boxes(element, boxes: List[Tuple[float, float, float, float]]) -> bool:
    if not boxes:
        return True
    cx = element.x + element.width / 2
    cy = element.y + element.height / 2
    for x, y, w, h in boxes:
        if x <= cx <= x + w and y <= cy <= y + h:
            return True
    return False


def _normalize_css_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _css_fix_hint(issue: str, figma_value: Any) -> str:
    v = _normalize_css_value(figma_value)
    mapping = {
        "text_color": f"color: {v};",
        "fill_color": f"background-color: {v};",
        "font_family": f"font-family: {v};",
        "font_size": f"font-size: {v}px;",
        "font_weight": f"font-weight: {v};",
        "line_height": f"line-height: {v}px;",
        "border_radius": f"border-radius: {v}px;",
        "width": f"width: {v}px;",
        "height": f"height: {v}px;",
    }
    return mapping.get(issue, "check style token or selector mapping")


def _top_diff_items(result: Dict[str, Any], limit: int = 18) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for el in result.get("elements", []):
        if not el.get("matched"):
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
                    "css_hint": _css_fix_hint(prop, detail.get("figma")),
                }
            )
    # keep unmatched in tail only when nothing failed in matched nodes
    if not items:
        for el in result.get("elements", []):
            if el.get("matched"):
                continue
            items.append(
                {
                    "figma_name": el.get("figma_name", ""),
                    "figma_id": el.get("figma_id", ""),
                    "dom_selector": None,
                    "issue": "unmatched",
                    "details": "No matched DOM element",
                    "css_hint": "add stable element_map selector",
                }
            )
    return items[:limit]


def _build_dev_suggestions(top_diffs: List[Dict[str, Any]]) -> List[str]:
    counter: Dict[str, int] = {}
    for item in top_diffs:
        issue = item.get("issue", "")
        counter[issue] = counter.get(issue, 0) + 1
    priority = sorted(counter.items(), key=lambda x: x[1], reverse=True)
    suggestions = []
    seen_groups: set[str] = set()
    for issue, count in priority[:6]:
        if issue == "font_family":
            group = "font_family"
            if group in seen_groups:
                continue
            seen_groups.add(group)
            suggestions.append(f"[P1] 字体体系不一致 ({count} 处): 对齐全局字体 token，并检查 fallback 字体顺序。")
        elif issue in {"font_size", "line_height", "font_weight"}:
            group = "typography"
            if group in seen_groups:
                continue
            seen_groups.add(group)
            total = sum(counter.get(k, 0) for k in ["font_size", "line_height", "font_weight"])
            suggestions.append(f"[P1] 字体排版参数偏差 ({total} 处): 对齐 typography scale（字号/字重/行高）。")
        elif issue in {"text_color", "fill_color"}:
            group = "color"
            if group in seen_groups:
                continue
            seen_groups.add(group)
            total = sum(counter.get(k, 0) for k in ["text_color", "fill_color"])
            suggestions.append(f"[P1] 颜色偏差 ({total} 处): 对齐颜色 token，避免写死颜色值。")
        elif issue in {"width", "height", "border_radius"}:
            group = "size"
            if group in seen_groups:
                continue
            seen_groups.add(group)
            total = sum(counter.get(k, 0) for k in ["width", "height", "border_radius"])
            suggestions.append(f"[P2] 结构尺寸偏差 ({total} 处): 核对组件尺寸、圆角及容器约束。")
        elif issue == "unmatched":
            group = "unmatched"
            if group in seen_groups:
                continue
            seen_groups.add(group)
            suggestions.append(f"[P0] 元素未匹配 ({count} 处): 先补稳定选择器映射，再进行属性修复。")
        else:
            suggestions.append(f"[P2] {issue} 差异 ({count} 处): 检查对应 CSS。")
    return suggestions


def _render_html(pages: List[Dict[str, Any]], output_path: Path) -> Path:
    cards = []
    for page in pages:
        pixel = page["pixel"]
        elem = page["element"]
        block_results = page["blocks"]
        suggestions = _build_dev_suggestions(page["top_diffs"])

        diff_rows = []
        for item in page["top_diffs"]:
            if item["issue"] == "unmatched":
                diff_rows.append(
                    f"<tr><td>{item['figma_name']}</td><td>unmatched</td><td colspan='2'>No matched DOM element</td><td>{item['css_hint']}</td></tr>"
                )
            else:
                details = item["details"]
                diff_rows.append(
                    "<tr>"
                    f"<td>{item['figma_name']}</td>"
                    f"<td>{item['issue']}</td>"
                    f"<td>Figma={details.get('figma','')} | Web={details.get('web','')} | Diff={details.get('diff','')}</td>"
                    f"<td>{item['css_hint']}</td>"
                    "</tr>"
                )
        if not diff_rows:
            diff_rows.append("<tr><td colspan='4'>No visual property differences captured in top list.</td></tr>")

        block_cards = []
        for block in block_results:
            block_cards.append(
                f"""
<div class="block-card">
  <div class="block-title">{block['label']} · 相似度 {block['similarity']:.2f}%</div>
  <div class="img-grid block-grid">
    <div><div class="lbl">Figma</div><img src="{_b64(block['figma_path'])}" /></div>
    <div><div class="lbl">Website</div><img src="{_b64(block['web_path'])}" /></div>
    <div><div class="lbl">Diff</div><img src="{_b64(block['diff_path'])}" /></div>
  </div>
</div>
"""
            )

        cards.append(
            f"""
<section class="card">
  <h2>{page['label']}</h2>
  <div class="links">
    <a href="{page['figma_url']}">Figma 原型</a>
    <a href="{page['site_url']}">对比网站</a>
  </div>
  <div class="stats">
    <div><b>整页相似度(仅参考)</b><span>{pixel['similarity']:.2f}%</span></div>
    <div><b>结构块平均相似度</b><span>{page['block_avg_similarity']:.2f}%</span></div>
    <div><b>元素得分(结构块内)</b><span>{elem['overall_score']:.2%}</span></div>
    <div><b>元素覆盖率(结构块内)</b><span>{elem['coverage_rate']:.2%}</span></div>
  </div>
  <h3>稳定结构块对比（核心）</h3>
  {''.join(block_cards)}
  <h3>开发修复建议</h3>
  <ul>{"".join(f"<li>{s}</li>" for s in suggestions) if suggestions else "<li>暂无建议</li>"}</ul>
  <div class="img-grid">
    <div><div class="lbl">整页 Figma</div><img src="{_b64(pixel['figma_path'])}" /></div>
    <div><div class="lbl">整页 Website</div><img src="{_b64(pixel['web_path'])}" /></div>
    <div><div class="lbl">整页 Diff</div><img src="{_b64(pixel['diff_path'])}" /></div>
    <div><div class="lbl">整页 Side By Side</div><img src="{_b64(pixel['compare_path'])}" /></div>
  </div>
  <h3>元素级差异列表（可直接改样式）</h3>
  <table>
    <thead>
      <tr><th>Figma 元素</th><th>差异项</th><th>现状</th><th>建议修复</th></tr>
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
    .block-card{{background:#f8fafc;border:1px solid #e5e7eb;border-radius:10px;padding:12px;margin:10px 0}}
    .block-title{{font-weight:700;margin-bottom:10px}}
    .block-grid{{grid-template-columns:repeat(3,minmax(0,1fr));margin-bottom:0}}
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
                f"- Full-page similarity (reference only): {pixel['similarity']:.2f}%",
                f"- Stable-block average similarity (primary): {page['block_avg_similarity']:.2f}%",
                f"- Element overall score (inside stable blocks): {elem['overall_score']:.2%}",
                f"- Element coverage (inside stable blocks): {elem['coverage_rate']:.2%}",
                f"- Matched elements: {elem['total_matched']}",
                f"- Unmatched elements: {elem['total_unmatched']}",
                "",
                "Stable block results:",
            ]
        )
        for block in page["blocks"]:
            lines.append(f"- `{block['label']}` similarity: {block['similarity']:.2f}%")
        lines.append("")
        lines.append("Top differences:")
        if page["top_diffs"]:
            for item in page["top_diffs"][:8]:
                if item["issue"] == "unmatched":
                    lines.append(f"- `{item['figma_name']}`: no matched DOM element")
                else:
                    details = item["details"]
                    lines.append(
                        f"- `{item['figma_name']}` `{item['issue']}`: Figma=`{details.get('figma','')}` Web=`{details.get('web','')}` Diff=`{details.get('diff','')}`; fix=`{item.get('css_hint','')}`"
                    )
        else:
            lines.append("- No major differences captured in the top list.")
        lines.append("")
        lines.append("Developer fix priorities:")
        for suggestion in _build_dev_suggestions(page["top_diffs"]):
            lines.append(f"- {suggestion}")
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
            node_json = figma.get_node_json(page["figma_node"])
            ReportWriter._write_json(Config.REPORTS_DIR / "json" / f"focused_figma_{key}.json", node_json)

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
            root_box = _node_box(node_json)
            figma_block_abs_boxes: List[Tuple[float, float, float, float]] = []
            block_results = []
            for block in STABLE_BLOCKS:
                bkey = block["key"]
                fig_block_path = Config.SCREENSHOTS_DIR / "figma" / f"focused_{key}_{bkey}.png"
                web_block_path = Config.SCREENSHOTS_DIR / "web" / f"focused_{key}_{bkey}.png"
                diff_block_path = Config.REPORTS_DIR / "images" / f"focused_{key}_{bkey}_diff.png"
                node_for_block = _pick_figma_block_node(node_json, block)
                if node_for_block and root_box:
                    abs_box = _node_box(node_for_block)
                    _crop_from_figma_image(figma_path, root_box, abs_box, fig_block_path)
                    figma_block_abs_boxes.append(abs_box)
                elif root_box:
                    abs_box = _ratio_to_abs_box(root_box, block["fallback_ratio"])
                    _crop_from_figma_image(figma_path, root_box, abs_box, fig_block_path)
                    figma_block_abs_boxes.append(abs_box)
                else:
                    _crop_by_ratio_from_image(figma_path, block["fallback_ratio"], fig_block_path)

                web_ok = _screenshot_web_block(capture.page, block["web_selectors"], web_block_path)
                if not web_ok:
                    _crop_by_ratio_from_image(web_path, block["fallback_ratio"], web_block_path)

                block_similarity = comparator.calculate_similarity(fig_block_path, web_block_path)
                comparator.generate_diff_image(fig_block_path, web_block_path, diff_block_path)
                block_results.append(
                    {
                        "key": bkey,
                        "label": block["label"],
                        "similarity": float(block_similarity),
                        "figma_path": str(fig_block_path),
                        "web_path": str(web_block_path),
                        "diff_path": str(diff_block_path),
                    }
                )

            figma_elements = figma_extractor.extract_semantic(node_json, max_depth=Config.COMPARE_MAX_DEPTH)
            figma_elements = [
                e for e in figma_elements
                if e.width >= 12
                and e.height >= 12
                and not _NOISE_NAME_RE.match((e.name or "").strip())
                and _element_in_boxes(e, figma_block_abs_boxes)
            ]
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
                "stable_blocks": block_results,
            }
            ReportWriter._write_json(Config.REPORTS_DIR / "json" / f"element_diff_{key}.json", element_payload)
            block_avg = round(sum(x["similarity"] for x in block_results) / len(block_results), 2) if block_results else 0.0
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
                    "blocks": block_results,
                    "block_avg_similarity": block_avg,
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
