"""
src/focused_ui_check.py -- 3-page focused visual + element comparison

Runs a fixed set of pages:
- Home
- Category (Pharmaceuticals)
- Details (first Pharmaceuticals article)

Outputs (self-contained so the whole folder can be zipped and mailed):
- reports/focused_ui_report/index.html
- reports/focused_ui_report/summary.md
- reports/focused_ui_report/*.png          (all screenshots live next to the html)
- reports/json/focused_run_result.json     (machine-readable, stays separate)
- reports/json/focused_element_diffs.json
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
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
from src.figma_url import parse_figma_url
from src.function_check import FunctionChecker
from src.image_compare import ImageCompare
from src.report_writer import ReportWriter
from src.web_capture import WebCapture

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


# ──────────────────────────────────────────────────────────────────
# 页面清单：从 config/focused_pages.json 动态加载
# ──────────────────────────────────────────────────────────────────
# 两种配法，JSON 里可任选（同一条里混用也行）:
#
#   A) 短格式(推荐): 只写 node_id + 网站相对路径
#      { "key": "home", "figma_node": "15480:75", "site_path": "/" }
#      → file_key 取自 .env FIGMA_DESIGN_URL / FIGMA_FILE_KEY
#      → 站点 host 取自 .env BASE_URL
#      换 .env 不需要改 JSON, 换一批页面也只改短字段.
#
#   B) 完整 URL: 直接贴浏览器链接
#      { "key": "home", "figma_url": "https://www.figma.com/design/.../?node-id=15480-75",
#        "site_url": "https://host.com/" }
#      → node_id / file_key 从 figma_url 解析.
#
FOCUSED_PAGES_CONFIG = Config.BASE_DIR / "config" / "focused_pages.json"


def _join_site_url(site_path: str) -> str:
    """把相对 path 拼到 .env BASE_URL 上；已经是绝对 URL 就原样返回."""
    p = (site_path or "").strip()
    if not p:
        return ""
    if p.startswith(("http://", "https://")):
        return p
    base = (Config.BASE_URL or "").rstrip("/")
    if not p.startswith("/"):
        p = "/" + p
    return f"{base}{p}"


def _build_figma_url(file_key: Optional[str], node_id: str) -> str:
    """根据 file_key + node_id 反推一个可点的 Figma URL(纯展示用)."""
    if not file_key or not node_id:
        return ""
    node_param = node_id.replace(":", "-")
    return f"https://www.figma.com/design/{file_key}/Slug?node-id={node_param}"


def _load_focused_pages() -> List[Dict[str, Any]]:
    """从 JSON 读取页面清单, 短字段自动拼接 .env 里的 host / file_key.

    校验失败时打印清晰错误并退出程序, 避免下游拿到空配置.
    """
    if not FOCUSED_PAGES_CONFIG.exists():
        print(
            f"[ERROR] 未找到页面配置文件: {FOCUSED_PAGES_CONFIG}\n"
            f"        需要填入 pages 列表."
        )
        sys.exit(1)

    try:
        raw = json.loads(FOCUSED_PAGES_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERROR] focused_pages.json 解析失败: {e}")
        sys.exit(1)

    pages_raw = raw.get("pages") or []
    if not pages_raw:
        print(f"[ERROR] focused_pages.json 的 pages 列表为空")
        sys.exit(1)

    env_file_key = (Config.FIGMA_FILE_KEY or "").strip() or None
    env_default_node = (Config.FIGMA_TARGET_NODE_ID or "").strip().replace("-", ":")

    pages: List[Dict[str, Any]] = []
    for idx, p in enumerate(pages_raw):
        # ── 1) Figma 节点 ─────────────────────────
        figma_url = (p.get("figma_url") or "").strip()
        figma_node = (p.get("figma_node") or "").strip().replace("-", ":")
        file_key: Optional[str] = None

        if figma_url:
            info = parse_figma_url(figma_url)
            if not info.ok() or not info.node_id:
                print(
                    f"[ERROR] 第 {idx+1} 条 figma_url 无法解析: {figma_url}\n"
                    f"        请确保 URL 带有 ?node-id=xxxx-yyyy."
                )
                sys.exit(1)
            figma_node = info.node_id
            file_key = info.file_key
        else:
            # 允许最短配置: 不写 figma_node / figma_url 时, 回退到 .env 的
            # FIGMA_TARGET_NODE_ID 作为默认节点, 便于先批量跑站点页面验证.
            if not figma_node:
                figma_node = env_default_node
            if not figma_node:
                print(
                    f"[ERROR] 第 {idx+1} 条既没有 figma_url 也没有 figma_node, "
                    f"且 .env 未提供 FIGMA_TARGET_NODE_ID 默认值."
                )
                sys.exit(1)
            file_key = env_file_key
            if not file_key:
                print(
                    f"[ERROR] 第 {idx+1} 条只填了 figma_node, 但 .env 里没设置 "
                    f"FIGMA_DESIGN_URL / FIGMA_FILE_KEY 作为默认 file_key."
                )
                sys.exit(1)
            figma_url = _build_figma_url(file_key, figma_node)

        # ── 2) 网站 URL ───────────────────────────
        site_url = (p.get("site_url") or "").strip()
        site_path = (p.get("site_path") or "").strip()
        if not site_url:
            if not site_path:
                print(
                    f"[ERROR] 第 {idx+1} 条既没有 site_url 也没有 site_path, "
                    f"至少要填一个."
                )
                sys.exit(1)
            if not Config.BASE_URL:
                print(
                    f"[ERROR] 第 {idx+1} 条用了 site_path, 但 .env 里没设置 BASE_URL."
                )
                sys.exit(1)
            site_url = _join_site_url(site_path)

        key = (p.get("key") or f"page_{idx+1}").strip()
        label = (p.get("label") or key.title()).strip()

        pages.append(
            {
                "key": key,
                "label": label,
                "figma_node": figma_node,
                "figma_file_key": file_key,
                "figma_url": figma_url,
                "site_url": site_url,
            }
        )
    return pages


FOCUSED_PAGES: List[Dict[str, Any]] = _load_focused_pages()

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

# Typography properties are intentionally skipped for this report: the user
# wants a purely visual/structural comparison (image size, width/height, box
# colors, radius) — font families and sizes already diverge heavily between
# Figma's rendering engine and real browsers and add noise rather than
# actionable info here.
_SKIP_PROPS = {"font_family", "font_size", "font_weight", "line_height", "text_color"}


# Self-contained report folder. Every artifact the user needs to view the
# report (HTML + screenshots + markdown) lives inside this one folder so
# it can be zipped and e-mailed in a single step.
FOCUSED_REPORT_DIR = Config.REPORTS_DIR / "focused_ui_report"


def _img_src(path: str) -> str:
    """Return a filename-only reference so images load even when the whole
    ``focused_ui_report/`` folder is sent/copied/zipped elsewhere."""
    p = Path(path)
    if not p.exists():
        return ""
    # Everything under FOCUSED_REPORT_DIR is flat, so just the filename works.
    try:
        return p.resolve().relative_to(FOCUSED_REPORT_DIR.resolve()).as_posix()
    except ValueError:
        return p.name


def _clean_output_dirs() -> None:
    """Wipe previous run artifacts so every run starts fresh.

    The focused report folder is nuked wholesale (HTML + every PNG inside),
    and ``reports/json/`` is emptied. Legacy sibling directories left over
    from earlier versions are also removed so ``reports/`` stays tidy.
    """
    Config.setup_directories()

    # 1) Fully recreate the self-contained report folder.
    if FOCUSED_REPORT_DIR.exists():
        shutil.rmtree(FOCUSED_REPORT_DIR, ignore_errors=True)
    FOCUSED_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 2) Empty reports/json/ (machine-readable outputs live here).
    json_dir = Config.REPORTS_DIR / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    for item in json_dir.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
        except PermissionError:
            pass

    # 3) Drop legacy top-level report files from previous versions.
    legacy_files = [
        Config.REPORTS_DIR / "report.html",
        Config.REPORTS_DIR / "verification_summary.md",
        Config.REPORTS_DIR / "focused_ui_report.html",
        Config.REPORTS_DIR / "focused_ui_summary.md",
    ]
    for file in legacy_files:
        if file.exists() and file.is_file():
            try:
                file.unlink()
            except PermissionError:
                pass

    # 4) Drop deprecated sibling directories. They used to hold screenshots
    #    / diff images that are now bundled inside FOCUSED_REPORT_DIR, and
    #    other scripts (run_orchestrator) may still create them if run.
    for legacy_dir in [
        Config.REPORTS_DIR / "html",
        Config.REPORTS_DIR / "images",
        Config.SCREENSHOTS_DIR / "figma",
        Config.SCREENSHOTS_DIR / "web",
        Config.SCREENSHOTS_DIR / "site",
        Config.SCREENSHOTS_DIR,  # the now-unused root itself
    ]:
        if legacy_dir.exists():
            shutil.rmtree(legacy_dir, ignore_errors=True)


def _iter_nodes(root: Dict[str, Any], max_depth: int = -1) -> List[Tuple[int, Dict[str, Any]]]:
    """DFS over the Figma node tree, yielding (depth, node)."""
    result: List[Tuple[int, Dict[str, Any]]] = []
    stack: List[Tuple[int, Dict[str, Any]]] = [(0, root)]
    while stack:
        depth, node = stack.pop()
        result.append((depth, node))
        if max_depth >= 0 and depth >= max_depth:
            continue
        for child in reversed(node.get("children", []) or []):
            stack.append((depth + 1, child))
    return result


def _node_box(node: Dict[str, Any]) -> Optional[Tuple[float, float, float, float]]:
    box = node.get("absoluteBoundingBox") or {}
    if not box:
        return None
    x, y, w, h = float(box.get("x", 0)), float(box.get("y", 0)), float(box.get("width", 0)), float(box.get("height", 0))
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


# Per-block constraints used when picking a Figma layer as the visual block.
# A valid match must (a) live in the top 2 levels of the frame so we don't
# accidentally pick a deeply nested icon/text node, (b) span a significant
# fraction of the frame's width, (c) be tall enough to be a real block, and
# (d) NOT be so tall that it swallows multiple sibling sections.
# The max_height_ratio cap is critical: without it, ``content`` picks the
# mega-wrapper that stacks every card, and the web side is forced to do a
# scroll capture of the whole page instead of a single card-level compare.
_BLOCK_CONSTRAINTS = {
    "header":  {"max_depth": 2, "min_width_ratio": 0.6, "min_height": 40,  "max_height_ratio": 0.20, "max_height_px": 400,  "y_rel_range": (0.0, 0.25)},
    "footer":  {"max_depth": 2, "min_width_ratio": 0.6, "min_height": 60,  "max_height_ratio": 0.30, "max_height_px": 900,  "y_rel_range": (0.55, 1.0)},
    "hero":    {"max_depth": 2, "min_width_ratio": 0.6, "min_height": 200, "max_height_ratio": 0.45, "max_height_px": 1100, "y_rel_range": (0.03, 0.45)},
    "content": {"max_depth": 2, "min_width_ratio": 0.6, "min_height": 200, "max_height_ratio": 0.35, "max_height_px": 900,  "y_rel_range": (0.15, 0.9)},
}


def _pick_figma_block_node(root: Dict[str, Any], block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pick a top-level-ish Figma layer matching a stable block.

    Rules:
    - Limit search to the upper levels of the frame (``max_depth``) so small
      nested nodes (icons, labels inside a card) never win.
    - Require the candidate to be nearly full-width so we never pick a
      badge/column inside the real block.
    - Require a minimum height per block type to filter out thin labels.
    - Prefer candidates whose relative Y position matches the block role
      (header near top, footer near bottom, hero near the first screen, etc).
    - If a pattern/keyword match exists it wins; otherwise we fall back to the
      best-positioned candidate purely by geometry, which is way more robust
      than trusting every designer's naming convention.
    """
    root_box = _node_box(root)
    if not root_box:
        return None
    rx, ry, rw, rh = root_box
    key = block["key"]
    rules = _BLOCK_CONSTRAINTS.get(
        key,
        {"max_depth": 2, "min_width_ratio": 0.6, "min_height": 80, "max_height_ratio": 1.0, "y_rel_range": (0.0, 1.0)},
    )
    max_depth = rules["max_depth"]
    min_w = rw * rules["min_width_ratio"]
    min_h = rules["min_height"]
    # Cap by the tighter of (ratio of root, absolute px budget).
    max_h_ratio = rh * rules.get("max_height_ratio", 1.0)
    max_h_abs = rules.get("max_height_px", 100000)
    max_h = min(max_h_ratio, max_h_abs)
    y_lo, y_hi = rules["y_rel_range"]
    patterns = [p.lower() for p in block["figma_patterns"]]

    named_candidates: List[Tuple[float, Dict[str, Any]]] = []
    geom_candidates: List[Tuple[float, Dict[str, Any]]] = []

    for depth, node in _iter_nodes(root, max_depth=max_depth):
        if depth == 0:
            continue  # skip the root itself
        box = _node_box(node)
        if not box:
            continue
        x, y, w, h = box
        if w < min_w or h < min_h:
            continue
        if h > max_h:
            # Reject wrappers that stack multiple sibling sections.
            continue
        y_rel = (y - ry) / max(rh, 1)
        if not (y_lo <= y_rel <= y_hi):
            continue

        # Positional score tuned per block role.
        if key == "header":
            pos_score = max(0.0, 1.0 - y_rel * 4)  # earlier == better
        elif key == "footer":
            pos_score = max(0.0, (y_rel - 0.55) * 2)  # later == better
        elif key == "hero":
            pos_score = max(0.0, 1.0 - abs(y_rel - 0.18) * 3)
        else:  # content
            pos_score = max(0.0, 1.0 - abs(y_rel - 0.5) * 2)

        width_score = w / max(rw, 1)
        height_score = min(1.0, h / max(rh, 1) * 4)
        score = pos_score * 1.5 + width_score * 1.0 + height_score * 0.5

        name = (node.get("name") or "").lower()
        if any(p in name for p in patterns):
            named_candidates.append((score + 0.8, node))
        else:
            geom_candidates.append((score, node))

    pool = named_candidates or geom_candidates
    if not pool:
        return None
    pool.sort(key=lambda item: item[0], reverse=True)
    return pool[0][1]


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


def _crop_web_by_figma_coords(
    full_web_path: Path,
    root_box: Tuple[float, float, float, float],
    abs_box: Tuple[float, float, float, float],
    output_path: Path,
) -> Optional[Tuple[float, float, float, float]]:
    """Crop the web full-page screenshot using the Figma layer's bbox.

    Because the Playwright viewport width is already aligned with the Figma
    root frame width, 1 CSS px ≈ 1 Figma px. So we can map the Figma layer's
    absolute box directly to page coordinates on the web screenshot. This
    guarantees Figma block and Web block cover the *same visual region*,
    even when the site's DOM structure disagrees with the design's layer
    groupings (e.g., Figma Hero = 4 cards, Web uses 2 separate <section>s).
    """
    try:
        img = Image.open(full_web_path)
    except Exception:
        return None
    rx, ry, _, _ = root_box
    x, y, w, h = abs_box
    left = int(max(0, x - rx))
    top = int(max(0, y - ry))
    right = int(min(img.width, left + w))
    bottom = int(min(img.height, top + h))
    if right <= left or bottom <= top:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.crop((left, top, right, bottom)).save(output_path)
    return (float(left), float(top), float(right - left), float(bottom - top))


def _crop_web_block_from_full(
    page,
    selectors: List[str],
    full_web_path: Path,
    output_path: Path,
) -> Optional[Tuple[float, float, float, float]]:
    """Locate a DOM element and crop it out of the full-page web screenshot.

    Used as a fallback when Figma does not provide a usable layer for the
    block. We deliberately avoid ``locator.screenshot()`` because an
    element-only screenshot forces Playwright to scroll the element into
    view, which re-lays out sticky headers and produces visuals that do NOT
    match the coordinate system of the full-page image.

    Returns the page-absolute bounding box (x, y, w, h) in CSS px, or None.
    """
    for selector in selectors:
        try:
            handle = page.locator(selector).first
            count = handle.count()
            if count == 0:
                continue
            # DOMRect (viewport-relative) + scroll offset = absolute page coords
            rect = handle.evaluate(
                "el => { const r = el.getBoundingClientRect();"
                "return { x: r.left + window.scrollX,"
                "         y: r.top + window.scrollY,"
                "         w: r.width, h: r.height }; }"
            )
        except Exception:
            continue
        if not rect:
            continue
        x, y, w, h = rect.get("x", 0), rect.get("y", 0), rect.get("w", 0), rect.get("h", 0)
        if w < 40 or h < 20:
            continue
        try:
            img = Image.open(full_web_path)
        except Exception:
            return None
        left = int(max(0, x))
        top = int(max(0, y))
        right = int(min(img.width, left + w))
        bottom = int(min(img.height, top + h))
        if right <= left or bottom <= top:
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.crop((left, top, right, bottom)).save(output_path)
        return (float(x), float(y), float(w), float(h))
    return None


def _normalize_pair_for_compare(
    img1_path: Path,
    img2_path: Path,
    out1_path: Path,
    out2_path: Path,
) -> Tuple[Path, Path]:
    """Align two images for fair pixel comparison without stretching.

    - Scale both images to the SAME target width (the larger of the two),
      preserving aspect ratio (so no horizontal squashing).
    - Pad the shorter image with white to match the taller one, so both
      final images share the exact same canvas size.

    This way, a design block that is physically taller than its web
    counterpart will show up as an obvious "extra region" in the diff,
    instead of being squashed into the web block's height.
    """
    img1 = Image.open(img1_path).convert("RGB")
    img2 = Image.open(img2_path).convert("RGB")

    target_w = max(img1.width, img2.width)

    def _scale_width(img: Image.Image, w: int) -> Image.Image:
        if img.width == w:
            return img
        ratio = w / img.width
        new_h = max(1, int(round(img.height * ratio)))
        return img.resize((w, new_h), Image.Resampling.LANCZOS)

    img1 = _scale_width(img1, target_w)
    img2 = _scale_width(img2, target_w)
    target_h = max(img1.height, img2.height)

    def _pad_bottom(img: Image.Image, h: int) -> Image.Image:
        if img.height == h:
            return img
        canvas = Image.new("RGB", (img.width, h), (255, 255, 255))
        canvas.paste(img, (0, 0))
        return canvas

    img1 = _pad_bottom(img1, target_h)
    img2 = _pad_bottom(img2, target_h)

    out1_path.parent.mkdir(parents=True, exist_ok=True)
    out2_path.parent.mkdir(parents=True, exist_ok=True)
    img1.save(out1_path)
    img2.save(out2_path)
    return out1_path, out2_path


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


def _issue_counts(top_diffs: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in top_diffs:
        issue = item.get("issue", "unknown")
        counts[issue] = counts.get(issue, 0) + 1
    return counts


def _escape_html(value: Any) -> str:
    """Minimal HTML escape for cell text to avoid breaking the table markup."""
    s = "" if value is None else str(value)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


def _aggregate_function_checks(pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge per-page function check results into a single dedup'd view.

    - Links are deduplicated by URL. Header/footer links that appear on every
      page collapse into one row, with a "pages" column listing where they
      were tested. Details-only links (e.g. related article in the sidebar)
      naturally stay as their own rows.
    - Buttons are NOT deduplicated (the same button text can mean different
      things on different pages), but are rolled into one aggregated table
      with a "page" column.
    - Console errors / page errors / failed requests are gathered with the
      page they originated from.
    """
    links_by_url: Dict[str, Dict[str, Any]] = {}
    buttons: List[Dict[str, Any]] = []
    console_errors: List[Dict[str, str]] = []
    page_errors: List[Dict[str, str]] = []
    failed_requests: List[Dict[str, Any]] = []
    page_errors_by_page: List[Dict[str, Any]] = []

    for p in pages:
        fn = p.get("function_check") or {}
        page_label = p.get("label", "")
        if fn.get("error"):
            page_errors_by_page.append({"page": page_label, "error": fn["error"]})
            continue
        for l in fn.get("links", []) or []:
            href = l.get("href") or ""
            if not href:
                continue
            existing = links_by_url.get(href)
            if existing:
                if page_label and page_label not in existing["pages"]:
                    existing["pages"].append(page_label)
                # If any page saw this link as failed, persist the failure.
                if not l.get("ok") and existing.get("ok"):
                    existing["ok"] = False
                    existing["status"] = l.get("status", existing.get("status"))
                    existing["error"] = l.get("error", existing.get("error", ""))
            else:
                links_by_url[href] = {
                    "href": href,
                    "text": (l.get("text") or "").strip(),
                    "status": l.get("status"),
                    "ok": l.get("ok", True),
                    "elapsed_ms": l.get("elapsed_ms", 0),
                    "error": l.get("error", ""),
                    "pages": [page_label] if page_label else [],
                }
        for b in fn.get("buttons", []) or []:
            buttons.append({**b, "page": page_label})
        for e in fn.get("console_errors", []) or []:
            console_errors.append({"page": page_label, "message": e})
        for e in fn.get("page_errors", []) or []:
            page_errors.append({"page": page_label, "message": e})
        for r in fn.get("failed_requests", []) or []:
            failed_requests.append({"page": page_label, **r})

    # Sort: failed first, then by URL.
    links = sorted(
        links_by_url.values(),
        key=lambda x: (0 if not x.get("ok") else 1, x.get("href", "")),
    )

    button_failed_statuses = {"click_failed", "console_error", "not_found"}
    buttons_failed = sum(1 for b in buttons if b.get("status") in button_failed_statuses)
    links_failed = sum(1 for l in links if not l.get("ok"))

    return {
        "links": links,
        "buttons": buttons,
        "console_errors": console_errors,
        "page_errors": page_errors,
        "failed_requests": failed_requests,
        "page_errors_by_page": page_errors_by_page,
        "summary": {
            "unique_links": len(links),
            "unique_links_failed": links_failed,
            "link_pass_rate": (1 - links_failed / len(links)) * 100 if links else 0.0,
            "buttons_tested": len(buttons),
            "buttons_failed": buttons_failed,
            "button_pass_rate": (1 - buttons_failed / len(buttons)) * 100 if buttons else 0.0,
            "console_errors": len(console_errors),
            "page_errors": len(page_errors),
            "failed_requests": len(failed_requests),
        },
    }


_BUTTON_STATUS_CLASS = {
    "ok": "ok",
    "navigated": "warn",
    "skipped": "muted",
    "enumerated": "muted",
    "console_error": "fail",
    "click_failed": "fail",
    "not_found": "fail",
}


def _render_function_global_html(agg: Dict[str, Any]) -> str:
    """Render the single consolidated function check module for the whole run."""
    summary = agg.get("summary", {})
    links = agg.get("links", []) or []
    buttons = agg.get("buttons", []) or []
    console_errors = agg.get("console_errors", []) or []
    page_errors = agg.get("page_errors", []) or []
    failed_reqs = agg.get("failed_requests", []) or []
    page_errors_by_page = agg.get("page_errors_by_page", []) or []

    # Link rows (deduped by URL).
    link_rows = []
    for l in links:
        ok = l.get("ok")
        status = l.get("status")
        status_cell = f"{status}" if status is not None else "—"
        cls = "ok" if ok else "fail"
        text = _escape_html((l.get("text") or "").strip() or "(无文案)")
        href_raw = l.get("href", "")
        href = _escape_html(href_raw)
        err = _escape_html(l.get("error", ""))
        pages = ", ".join(l.get("pages", []) or [])
        link_rows.append(
            "<tr>"
            f"<td>{_escape_html(pages) or '—'}</td>"
            f"<td>{text}</td>"
            f"<td><a href='{href}' target='_blank' rel='noopener'>{href}</a></td>"
            f"<td class='{cls}'>{status_cell}</td>"
            f"<td>{l.get('elapsed_ms', 0)} ms</td>"
            f"<td class='{cls}'>{'OK' if ok else 'FAIL'}</td>"
            f"<td>{err}</td>"
            "</tr>"
        )
    if not link_rows:
        link_rows.append("<tr><td colspan='7' class='empty'>未发现可见链接</td></tr>")

    # Button rows (with page column).
    button_rows = []
    for b in buttons:
        status = b.get("status", "")
        cls = _BUTTON_STATUS_CLASS.get(status, "muted")
        text = _escape_html((b.get("text") or "").strip() or "(无文案)")
        extra = b.get("navigated_to") or b.get("error") or b.get("reason") or ""
        button_rows.append(
            "<tr>"
            f"<td>{_escape_html(b.get('page', ''))}</td>"
            f"<td>{text}</td>"
            f"<td class='{cls}'>{_escape_html(status)}</td>"
            f"<td>{_escape_html(extra)}</td>"
            "</tr>"
        )
    if not button_rows:
        button_rows.append("<tr><td colspan='4' class='empty'>未发现可见按钮</td></tr>")

    # Anomaly list (grouped & capped).
    err_items = []
    for item in console_errors[:12]:
        err_items.append(
            f"<li><code>console</code> [{_escape_html(item.get('page',''))}] "
            f"{_escape_html(item.get('message',''))}</li>"
        )
    for item in page_errors[:12]:
        err_items.append(
            f"<li><code>pageerror</code> [{_escape_html(item.get('page',''))}] "
            f"{_escape_html(item.get('message',''))}</li>"
        )
    for item in failed_reqs[:12]:
        err_items.append(
            f"<li><code>{item.get('status','?')}</code> "
            f"<code>{_escape_html(item.get('method','GET'))}</code> "
            f"[{_escape_html(item.get('page',''))}] "
            f"{_escape_html(item.get('url',''))}</li>"
        )
    for item in page_errors_by_page:
        err_items.append(
            f"<li><code>checker-error</code> [{_escape_html(item.get('page',''))}] "
            f"{_escape_html(item.get('error',''))}</li>"
        )
    errors_html = (
        f"<ul class='err-list'>{''.join(err_items)}</ul>"
        if err_items
        else "<div class='empty'>无异常记录</div>"
    )

    link_rate = f"{summary.get('link_pass_rate', 0):.1f}%" if summary.get("unique_links") else "—"
    btn_rate = f"{summary.get('button_pass_rate', 0):.1f}%" if summary.get("buttons_tested") else "—"

    return f"""
<section class="card">
  <h2>功能检测（全局 · 已去重）</h2>
  <p class="hint">链接按 URL 去重：同一条 Header/Footer 链接在多页都出现时，只保留一行，并在"出现页"列中标记来源页面。</p>
  <div class="stats">
    <div><b>唯一链接</b><span>{summary.get('unique_links', 0)}</span></div>
    <div><b>链接失败 / 通过率</b><span>{summary.get('unique_links_failed', 0)} · {link_rate}</span></div>
    <div><b>按钮点击次数</b><span>{summary.get('buttons_tested', 0)}</span></div>
    <div><b>按钮失败 / 通过率</b><span>{summary.get('buttons_failed', 0)} · {btn_rate}</span></div>
    <div><b>Console 错误</b><span>{summary.get('console_errors', 0)}</span></div>
    <div><b>Page 错误</b><span>{summary.get('page_errors', 0)}</span></div>
    <div><b>4xx/5xx 请求</b><span>{summary.get('failed_requests', 0)}</span></div>
  </div>
  <h3>链接跳转验证（去重）</h3>
  <table>
    <thead>
      <tr>
        <th>出现页</th><th>文案</th><th>URL</th><th>状态码</th><th>耗时</th><th>结果</th><th>备注</th>
      </tr>
    </thead>
    <tbody>{''.join(link_rows)}</tbody>
  </table>
  <h3>按钮点击结果（按页面）</h3>
  <table>
    <thead>
      <tr><th>页面</th><th>按钮文案</th><th>状态</th><th>详情</th></tr>
    </thead>
    <tbody>{''.join(button_rows)}</tbody>
  </table>
  <h3>异常记录（Console / PageError / 4xx-5xx 请求）</h3>
  {errors_html}
</section>
"""


def _render_html(pages: List[Dict[str, Any]], function_agg: Dict[str, Any], output_path: Path) -> Path:
    page_rows = []
    # Visual/structural issues only — typography properties are skipped at
    # the compare layer (see _SKIP_PROPS).
    issue_keys = ["fill_color", "border_radius", "width", "height", "unmatched"]
    issue_header = "".join(f"<th>{k}</th>" for k in issue_keys)
    issue_rows = []
    block_header = "".join(f"<th>{b['label']}</th>" for b in STABLE_BLOCKS)
    block_rows = []
    for page in pages:
        elem = page["element"]
        issue_count = _issue_counts(page["top_diffs"])
        page_rows.append(
            f"<tr><td>{page['label']}</td><td>{page['pixel']['similarity']:.2f}%</td><td>{page['block_avg_similarity']:.2f}%</td>"
            f"<td>{elem['overall_score']:.2%}</td><td>{elem['coverage_rate']:.2%}</td><td>{elem['total_matched']}</td><td>{elem['total_unmatched']}</td></tr>"
        )
        issue_rows.append(
            "<tr>"
            f"<td>{page['label']}</td>"
            + "".join(f"<td>{issue_count.get(k, 0)}</td>" for k in issue_keys)
            + "</tr>"
        )
        block_map = {b["key"]: b["similarity"] for b in page["blocks"]}
        block_rows.append(
            "<tr>"
            f"<td>{page['label']}</td>"
            + "".join(f"<td>{block_map.get(b['key'], 0.0):.2f}%</td>" for b in STABLE_BLOCKS)
            + "</tr>"
        )

    overview_tables = f"""
<section class="card">
  <h2>总览表格对比</h2>
  <h3>页面指标总览</h3>
  <table>
    <thead>
      <tr><th>页面</th><th>整页相似度</th><th>结构块平均相似度</th><th>元素得分</th><th>元素覆盖率</th><th>匹配元素</th><th>未匹配元素</th></tr>
    </thead>
    <tbody>
      {''.join(page_rows)}
    </tbody>
  </table>
  <h3>结构块相似度矩阵</h3>
  <table>
    <thead>
      <tr><th>页面</th>{block_header}</tr>
    </thead>
    <tbody>
      {''.join(block_rows)}
    </tbody>
  </table>
  <h3>差异项频次矩阵（Top Diffs）</h3>
  <table>
    <thead>
      <tr><th>页面</th>{issue_header}</tr>
    </thead>
    <tbody>
      {''.join(issue_rows)}
    </tbody>
  </table>
</section>
"""

    function_section_html = _render_function_global_html(function_agg)

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
            fig_sz = block.get("figma_size", {})
            web_sz = block.get("web_size", {})
            fig_dim = f"{fig_sz.get('width','?')}×{fig_sz.get('height','?')}"
            web_dim = f"{web_sz.get('width','?')}×{web_sz.get('height','?')}"
            layer = block.get("figma_layer_name", "") or "—"
            block_cards.append(
                f"""
<div class="block-card">
  <div class="block-title">{block['label']} · 相似度 {block['similarity']:.2f}%</div>
  <div class="block-meta">Figma 图层: <code>{layer}</code> · Figma 尺寸: {fig_dim} · Web 尺寸: {web_dim}</div>
  <div class="img-grid block-grid">
    <div><div class="lbl">Figma</div><img src="{_img_src(block['figma_path'])}" /></div>
    <div><div class="lbl">Website</div><img src="{_img_src(block['web_path'])}" /></div>
    <div><div class="lbl">Diff</div><img src="{_img_src(block['diff_path'])}" /></div>
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
    <div><div class="lbl">整页 Figma</div><img src="{_img_src(pixel['figma_path'])}" /></div>
    <div><div class="lbl">整页 Website</div><img src="{_img_src(pixel['web_path'])}" /></div>
    <div><div class="lbl">整页 Diff</div><img src="{_img_src(pixel['diff_path'])}" /></div>
    <div><div class="lbl">整页 Side By Side</div><img src="{_img_src(pixel['compare_path'])}" /></div>
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
    .block-title{{font-weight:700;margin-bottom:4px}}
    .block-meta{{font-size:12px;color:#6b7280;margin-bottom:10px}}
    .block-meta code{{background:#eef2ff;color:#4338ca;padding:1px 6px;border-radius:4px}}
    td.ok,th.ok{{color:#047857;font-weight:600}}
    td.fail,th.fail{{color:#b91c1c;font-weight:700;background:#fef2f2}}
    td.warn,th.warn{{color:#b45309;font-weight:600;background:#fffbeb}}
    td.muted,th.muted{{color:#6b7280}}
    .empty{{color:#9ca3af;font-size:13px;padding:8px 0}}
    .err-list{{margin:6px 0 0;padding-left:20px;font-size:13px}}
    .err-list li{{margin:3px 0;word-break:break-all}}
    .err-list code{{background:#f1f5f9;color:#0f172a;padding:1px 5px;border-radius:4px;margin-right:4px}}
    h4{{margin:16px 0 8px;font-size:14px;color:#374151}}
    p.hint{{color:#6b7280;font-size:13px;margin:0 0 14px}}
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
    {overview_tables}
    {function_section_html}
    {''.join(cards)}
  </div>
</body>
</html>"""
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _render_markdown(pages: List[Dict[str, Any]], function_agg: Dict[str, Any], output_path: Path) -> Path:
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

    # Single consolidated function-check module, deduplicated by URL.
    summary = function_agg.get("summary", {}) if function_agg else {}
    lines.append("## Function check (global, deduped)")
    lines.append("")
    lines.append(
        f"- Unique links: {summary.get('unique_links', 0)}, "
        f"failed: {summary.get('unique_links_failed', 0)}, "
        f"pass rate: {summary.get('link_pass_rate', 0):.1f}%"
    )
    lines.append(
        f"- Buttons clicked: {summary.get('buttons_tested', 0)}, "
        f"failed: {summary.get('buttons_failed', 0)}, "
        f"pass rate: {summary.get('button_pass_rate', 0):.1f}%"
    )
    lines.append(
        f"- Console errors: {summary.get('console_errors', 0)}, "
        f"page errors: {summary.get('page_errors', 0)}, "
        f"failed requests: {summary.get('failed_requests', 0)}"
    )
    bad_links = [l for l in (function_agg.get("links") or []) if not l.get("ok")][:10]
    if bad_links:
        lines.append("")
        lines.append("Failing links:")
        for l in bad_links:
            status = l.get("status") or "ERR"
            pages_s = ", ".join(l.get("pages", []) or [])
            lines.append(
                f"- [{pages_s}] `{(l.get('text') or '').strip()[:40]}` -> {l.get('href','')} "
                f"[{status}] {l.get('error','')}"
            )
    bad_buttons = [
        b for b in (function_agg.get("buttons") or [])
        if b.get("status") in ("click_failed", "console_error", "not_found")
    ][:10]
    if bad_buttons:
        lines.append("")
        lines.append("Failing buttons:")
        for b in bad_buttons:
            lines.append(
                f"- [{b.get('page','')}] `{(b.get('text') or '').strip()[:40]}` "
                f"[{b.get('status','')}] {b.get('error','')}"
            )
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def run() -> Dict[str, Any]:
    _clean_output_dirs()

    version = (Config.BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    # 支持跨 Figma 文件对比: 每个 file_key 对应一个 FigmaClient,按需缓存.
    figma_clients: Dict[str, FigmaClient] = {}

    def _get_figma(file_key: Optional[str]) -> FigmaClient:
        key = (file_key or Config.FIGMA_FILE_KEY or "").strip()
        if not key:
            raise RuntimeError(
                "缺少 Figma file_key: 既没在 .env 设置 FIGMA_DESIGN_URL/FIGMA_FILE_KEY, "
                "也没在 focused_pages.json 的 figma_url 里解析出有效 key."
            )
        if key not in figma_clients:
            figma_clients[key] = FigmaClient(file_key=key)
        return figma_clients[key]

    comparator = ImageCompare(threshold=Config.SIMILARITY_THRESHOLD)
    figma_extractor = FigmaExtractor()
    dom_extractor = DOMExtractor()
    auto_mapper = AutoMapper()
    element_compare = ElementCompare()

    page_results: List[Dict[str, Any]] = []
    element_pages: List[Dict[str, Any]] = []

    # Normalization scratch files (same-size copies used only for pixel diff)
    # live in a temp dir so they never pollute reports/screenshots/.
    norm_tmp = Path(tempfile.mkdtemp(prefix="focused_norm_"))

    # We intentionally open WebCapture with a placeholder viewport; we reset it
    # per-page below once we know each design's canvas width.
    with WebCapture(
        browser_type=Config.DEFAULT_BROWSER,
        headless=Config.HEADLESS,
        viewport={"width": Config.AGENT_VIEWPORT_WIDTH, "height": Config.AGENT_VIEWPORT_HEIGHT},
    ) as capture:
        for page in FOCUSED_PAGES:
            key = page["key"]
            # Every image for this run lives flat inside the self-contained
            # report folder, so shipping the folder gives the reader a
            # fully-working HTML report with all screenshots attached.
            figma_path = FOCUSED_REPORT_DIR / f"{key}_full_figma.png"
            web_path = FOCUSED_REPORT_DIR / f"{key}_full_web.png"
            diff_path = FOCUSED_REPORT_DIR / f"{key}_full_diff.png"
            compare_path = FOCUSED_REPORT_DIR / f"{key}_full_compare.png"

            # 1) Pull the Figma node JSON FIRST so we know the design canvas size,
            #    then align the browser viewport to the Figma frame width. This
            #    makes 1 CSS px ≈ 1 Figma design px, so coordinates are directly
            #    comparable and cropped blocks are already at the same scale.
            figma = _get_figma(page.get("figma_file_key"))
            node_json = figma.get_node_json(page["figma_node"])
            ReportWriter._write_json(Config.REPORTS_DIR / "json" / f"focused_figma_{key}.json", node_json)
            root_box = _node_box(node_json)
            design_w = int(round(root_box[2])) if root_box else Config.AGENT_VIEWPORT_WIDTH
            design_h = int(round(root_box[3])) if root_box else Config.AGENT_VIEWPORT_HEIGHT
            # Cap viewport width so we do not launch absurd viewports on large designs.
            viewport_w = max(360, min(design_w, 2560))
            viewport_h = max(600, min(design_h, 2200))

            # 2) Export Figma at scale=1 so PNG pixels == design pixels.
            figma.save_node_to_file(page["figma_node"], figma_path, scale=1)

            # 3) Capture the site at the Figma-aligned viewport width.
            capture.page.set_viewport_size({"width": viewport_w, "height": viewport_h})
            capture.page.goto(page["site_url"], wait_until="networkidle", timeout=30000)
            if Config.AGENT_HIDE_SELECTORS:
                capture.hide_elements(Config.AGENT_HIDE_SELECTORS)
            # Scroll to top so sticky/lazy elements render at their "initial" state,
            # which is what the Figma design typically portrays.
            capture.page.evaluate("window.scrollTo(0, 0)")
            capture.page.screenshot(path=str(web_path), full_page=True)

            # 4) Full-page comparison -- normalize canvases so aspect ratios stay intact.
            norm_fig_path = norm_tmp / f"focused_{key}_norm_figma.png"
            norm_web_path = norm_tmp / f"focused_{key}_norm_web.png"
            _normalize_pair_for_compare(figma_path, web_path, norm_fig_path, norm_web_path)
            similarity = comparator.calculate_similarity(norm_fig_path, norm_web_path)
            comparator.generate_diff_image(norm_fig_path, norm_web_path, diff_path)
            comparator.generate_side_by_side(norm_fig_path, norm_web_path, compare_path, labels=("Figma", "Website"))

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
                    "viewport": {"width": viewport_w, "height": viewport_h},
                    "figma_design": {"width": design_w, "height": design_h},
                    "status": "ok",
                }
            )
            figma_block_abs_boxes: List[Tuple[float, float, float, float]] = []
            block_results = []
            for block in STABLE_BLOCKS:
                bkey = block["key"]
                fig_block_path = FOCUSED_REPORT_DIR / f"{key}_{bkey}_figma.png"
                web_block_path = FOCUSED_REPORT_DIR / f"{key}_{bkey}_web.png"
                diff_block_path = FOCUSED_REPORT_DIR / f"{key}_{bkey}_diff.png"

                # --- Figma side: crop the exact layer bbox when we can identify it.
                node_for_block = _pick_figma_block_node(node_json, block)
                abs_box: Optional[Tuple[float, float, float, float]] = None
                figma_layer_name = ""
                if node_for_block and root_box:
                    abs_box = _node_box(node_for_block)
                    figma_layer_name = node_for_block.get("name", "")
                    _crop_from_figma_image(figma_path, root_box, abs_box, fig_block_path)
                    figma_block_abs_boxes.append(abs_box)
                elif root_box:
                    abs_box = _ratio_to_abs_box(root_box, block["fallback_ratio"])
                    figma_layer_name = "(ratio fallback)"
                    _crop_from_figma_image(figma_path, root_box, abs_box, fig_block_path)
                    figma_block_abs_boxes.append(abs_box)
                else:
                    _crop_by_ratio_from_image(figma_path, block["fallback_ratio"], fig_block_path)

                # --- Web side: prefer cropping by the SAME Figma bbox on the
                # web full-page screenshot. Viewport width is aligned with the
                # design, so Figma page px ≈ web page px. This locks both
                # sides to the identical visual region, avoiding the class of
                # bugs where Figma layer "Hero" = 4 cards but web's
                # ``main section:first-of-type`` = only 2 cards.
                web_rect: Optional[Tuple[float, float, float, float]] = None
                if abs_box and root_box:
                    web_rect = _crop_web_by_figma_coords(
                        web_path, root_box, abs_box, web_block_path
                    )

                # Fallback 1: DOM-selector crop (only when we could NOT
                # identify a Figma layer to drive the crop).
                if web_rect is None:
                    web_rect = _crop_web_block_from_full(
                        capture.page, block["web_selectors"], web_path, web_block_path
                    )

                # Fallback 2: ratio slice of the full web page.
                if web_rect is None:
                    _crop_by_ratio_from_image(web_path, block["fallback_ratio"], web_block_path)

                # --- Normalize to the SAME canvas (aspect preserving) before pixel diff.
                norm_fig_block = norm_tmp / f"focused_{key}_{bkey}_norm_figma.png"
                norm_web_block = norm_tmp / f"focused_{key}_{bkey}_norm_web.png"
                _normalize_pair_for_compare(fig_block_path, web_block_path, norm_fig_block, norm_web_block)
                block_similarity = comparator.calculate_similarity(norm_fig_block, norm_web_block)
                comparator.generate_diff_image(norm_fig_block, norm_web_block, diff_block_path)

                fig_size = Image.open(fig_block_path).size
                web_size = Image.open(web_block_path).size
                block_results.append(
                    {
                        "key": bkey,
                        "label": block["label"],
                        "similarity": float(block_similarity),
                        "figma_path": str(fig_block_path),
                        "web_path": str(web_block_path),
                        "diff_path": str(diff_block_path),
                        "figma_size": {"width": fig_size[0], "height": fig_size[1]},
                        "web_size": {"width": web_size[0], "height": web_size[1]},
                        "figma_layer_name": figma_layer_name,
                        "web_rect": (
                            {"x": web_rect[0], "y": web_rect[1], "w": web_rect[2], "h": web_rect[3]}
                            if web_rect
                            else None
                        ),
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
                skip_props=_SKIP_PROPS,
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

            # --- Functional check: links + buttons + console/network anomalies.
            # Details pages mostly re-expose the same header/footer/related
            # links found on Home + Category. We keep the per-page cap small
            # so only its genuinely unique links show up after global dedup.
            if key == "details":
                max_links_here, max_buttons_here = 8, 6
            else:
                max_links_here, max_buttons_here = 25, 12
            function_check: Dict[str, Any] = {}
            try:
                checker = FunctionChecker(
                    capture.page,
                    max_links=max_links_here,
                    max_buttons=max_buttons_here,
                )
                function_check = checker.run(check_buttons=True)
                ReportWriter._write_json(
                    Config.REPORTS_DIR / "json" / f"focused_function_{key}.json",
                    {
                        "version": version,
                        "generated_at": datetime.now(UTC).isoformat(),
                        "page_key": key,
                        "page_label": page["label"],
                        "site_url": page["site_url"],
                        **function_check,
                    },
                )
            except Exception as exc:  # pragma: no cover - defensive
                function_check = {
                    "error": f"{type(exc).__name__}: {exc}",
                    "summary": {"link_total": 0, "link_failed": 0, "button_total": 0, "button_failed": 0},
                }

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
                    "function_check": function_check,
                }
            )

    # Deduplicate link results across pages so the report stops repeating
    # the same Header/Footer nav links once per page.
    function_agg = _aggregate_function_checks(element_pages)

    focused_run = {
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": Config.BASE_URL,
        "page_results": page_results,
        "function_check_global": function_agg,
    }
    ReportWriter._write_json(Config.REPORTS_DIR / "json" / "focused_run_result.json", focused_run)
    ReportWriter._write_json(Config.REPORTS_DIR / "json" / "focused_element_diffs.json", {"pages": element_pages})
    ReportWriter._write_json(
        Config.REPORTS_DIR / "json" / "focused_function_global.json", function_agg
    )

    # Write HTML + Markdown INSIDE the self-contained folder so it can be
    # zipped / mailed in one shot (every <img> points to a sibling file).
    html_path = _render_html(element_pages, function_agg, FOCUSED_REPORT_DIR / "index.html")
    md_path = _render_markdown(element_pages, function_agg, FOCUSED_REPORT_DIR / "summary.md")

    # Drop the normalization scratch dir once the report is written.
    shutil.rmtree(norm_tmp, ignore_errors=True)

    print(f"[OK] Focused report generated: {html_path}")
    print(f"[OK] Folder (zip & send this): {FOCUSED_REPORT_DIR}")
    print(f"[OK] Focused summary generated: {md_path}")
    return {
        "focused_run": focused_run,
        "element_pages": element_pages,
        "function_check_global": function_agg,
        "html_report": str(html_path),
        "markdown_summary": str(md_path),
        "report_folder": str(FOCUSED_REPORT_DIR),
    }


if __name__ == "__main__":
    run()
