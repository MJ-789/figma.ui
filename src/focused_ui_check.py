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
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from PIL import Image
Image.MAX_IMAGE_PIXELS = None  # 资讯长页面截图可能非常大，避免 PIL 误判为炸弹图而中断

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
# 默认配置路径（不带 --template 参数时使用）
FOCUSED_PAGES_CONFIG = Config.BASE_DIR / "config" / "focused_pages.json"

# 默认报告目录（不带 --template 参数时使用）
# 带模板名时会动态变为 focused_ui_report_{template}/


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
    p = quote(p, safe="/%")
    return f"{base}{p}"


def _build_figma_url(file_key: Optional[str], node_id: str) -> str:
    """根据 file_key + node_id 反推一个可点的 Figma URL(纯展示用)."""
    if not file_key or not node_id:
        return ""
    node_param = node_id.replace(":", "-")
    return f"https://www.figma.com/design/{file_key}/Slug?node-id={node_param}"


def _load_focused_pages(
    config_path: Optional[Path] = None,
    template_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """从 focused_pages.json 读取页面清单。

    支持两种格式：

    ① 新模板格式（推荐）—— figma_node 填写即启用，留空自动跳过：
        {
          "games": { "figma_node": "16000:13788", "pages": [{"key":…, "label":…, "path":…}] },
          "news":  { "figma_node": "",             "pages": […] }
        }

    ② 旧平铺格式（兼容）—— 逐条指定完整字段：
        { "pages": [ {"key":…, "figma_node":…, "site_path":…} ] }

    Args:
        config_path:     配置文件路径，默认使用 FOCUSED_PAGES_CONFIG。
        template_filter: 仅加载指定模板（新格式专用）。None = 加载全部已启用模板。
    """
    cfg = config_path or FOCUSED_PAGES_CONFIG
    if not cfg.exists():
        print(f"[ERROR] 配置文件不存在: {cfg}")
        sys.exit(1)
    try:
        raw = json.loads(cfg.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERROR] {cfg.name} JSON 解析失败: {e}")
        sys.exit(1)

    # ── 新模板格式检测 ────────────────────────────────────────────────
    known_templates = [k for k in ("games", "news") if k in raw]
    if known_templates:
        return _load_template_format(raw, known_templates, template_filter)

    # ── 旧平铺格式兼容 ────────────────────────────────────────────────
    pages_raw = raw.get("pages") or []
    if not pages_raw:
        print(f"[ERROR] {cfg.name} 的 pages 列表为空")
        sys.exit(1)

    env_file_key = (Config.FIGMA_FILE_KEY or "").strip() or None
    env_default_node = (Config.FIGMA_TARGET_NODE_ID or "").strip().replace("-", ":")
    pages: List[Dict[str, Any]] = []

    for idx, p in enumerate(pages_raw):
        figma_url = (p.get("figma_url") or "").strip()
        figma_node = (p.get("figma_node") or "").strip().replace("-", ":")
        file_key: Optional[str] = None

        if figma_url:
            info = parse_figma_url(figma_url)
            if not info.ok() or not info.node_id:
                print(f"[ERROR] 第 {idx+1} 条 figma_url 无法解析: {figma_url}")
                sys.exit(1)
            figma_node = info.node_id
            file_key = info.file_key
        else:
            if not figma_node:
                figma_node = env_default_node
            if not figma_node:
                print(f"[ERROR] 第 {idx+1} 条缺少 figma_node，且 .env 未设置默认节点")
                sys.exit(1)
            file_key = env_file_key
            if not file_key:
                print(f"[ERROR] 第 {idx+1} 条缺少 file_key，请在 .env 设置 FIGMA_DESIGN_URL")
                sys.exit(1)
            figma_url = _build_figma_url(file_key, figma_node)

        site_url = (p.get("site_url") or "").strip()
        site_path = (p.get("site_path") or "").strip()
        if not site_url:
            if not site_path:
                print(f"[ERROR] 第 {idx+1} 条缺少 site_url / site_path")
                sys.exit(1)
            if not Config.BASE_URL:
                print(f"[ERROR] 第 {idx+1} 条用了 site_path，但 .env 未设置 BASE_URL")
                sys.exit(1)
            site_url = _join_site_url(site_path)

        key = (p.get("key") or f"page_{idx+1}").strip()
        label = (p.get("label") or key.title()).strip()
        pages.append({
            "key": key,
            "label": label,
            "figma_node": figma_node,
            "figma_file_key": file_key,
            "figma_url": figma_url,
            "site_url": site_url,
            "figma_scope": (p.get("figma_scope") or "").strip(),
        })
    return pages


def _load_template_format(
    raw: Dict[str, Any],
    known_templates: List[str],
    template_filter: Optional[str],
) -> List[Dict[str, Any]]:
    """解析新模板格式，返回平铺的页面列表。"""
    env_file_key = (Config.FIGMA_FILE_KEY or "").strip() or None
    if not env_file_key:
        print("[ERROR] .env 未设置 FIGMA_DESIGN_URL 或 FIGMA_FILE_KEY")
        sys.exit(1)
    if not Config.BASE_URL:
        print("[ERROR] .env 未设置 BASE_URL（网站地址）")
        sys.exit(1)

    to_process = [template_filter] if template_filter else known_templates
    pages: List[Dict[str, Any]] = []
    skipped: List[str] = []

    for tmpl_name in to_process:
        if tmpl_name not in raw:
            print(f"[ERROR] 配置中未找到模板 '{tmpl_name}'，可用: {known_templates}")
            sys.exit(1)

        tmpl = raw[tmpl_name]
        node = (tmpl.get("figma_node") or "").strip().replace("-", ":")

        if not node:
            if template_filter:
                # 明确指定了但未配置，报错
                print(
                    f"[ERROR] '{tmpl_name}' 的 figma_node 为空\n"
                    f"        请在 focused_pages.json 的 {tmpl_name}.figma_node 填写节点 ID"
                )
                sys.exit(1)
            skipped.append(tmpl_name)
            continue

        for p in (tmpl.get("pages") or []):
            key = (p.get("key") or f"{tmpl_name}_{len(pages)+1}").strip()
            label = (p.get("label") or key).strip()
            path = (p.get("path") or "/").strip()
            pages.append({
                "key": key,
                "label": label,
                "figma_node": node,
                "figma_file_key": env_file_key,
                "figma_url": _build_figma_url(env_file_key, node),
                "site_url": _join_site_url(path),
                "template": tmpl_name,
                "figma_scope": (p.get("figma_scope") or "").strip(),
            })

    if skipped:
        print(f"[SKIP] figma_node 为空，自动跳过: {', '.join(skipped)}")

    if not pages:
        print(
            "[ERROR] 没有可运行的页面。\n"
            "        请在 focused_pages.json 中填写至少一个模板的 figma_node，例如：\n"
            '        "games": { "figma_node": "16000:13788", ... }'
        )
        sys.exit(1)

    return pages


GENERIC_STABLE_BLOCKS = [
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

# games 专用：不再沿用通用 Header/Hero/Content/Footer 语义，改为更贴近
# justalplay 页面结构的分块。用于降低“结构块错位”导致的误报。
GAMES_STABLE_BLOCKS = [
    {
        "key": "page_marker",
        "label": "页面标记区（home/category/details）",
        "figma_patterns": ["home", "category", "details", "rectangle 20"],
        "web_selectors": ["main", "body"],
        "fallback_ratio": [0.0, 0.0, 0.30, 0.22],
    },
    {
        "key": "top_nav",
        "label": "顶栏导航区（Logo + 分类导航）",
        "figma_patterns": ["logo", "nav", "menu", "category", "top"],
        "web_selectors": ["header", "[role='banner']", "nav", ".header"],
        "fallback_ratio": [0.10, 0.16, 1.0, 0.34],
    },
    {
        "key": "search_bar",
        "label": "搜索条区（Search/Input）",
        "figma_patterns": ["search", "find", "input", "game name"],
        "web_selectors": [
            "input[placeholder*='game' i]",
            "form[role='search']",
            "[class*='search']",
            ".search",
        ],
        "fallback_ratio": [0.20, 0.24, 0.98, 0.46],
    },
    {
        "key": "game_list",
        "label": "游戏列表区（卡片内容）",
        "figma_patterns": ["list", "card", "must-play", "content", "destiny", "newly released"],
        "web_selectors": ["main", "[role='main']", ".content", ".list", "article", "[class*='card']"],
        "fallback_ratio": [0.18, 0.40, 0.98, 0.88],
    },
    {
        "key": "bottom_action",
        "label": "底部/侧边互动区（Footer/点赞评论）",
        "figma_patterns": ["footer", "comment", "like", "copyright", "company", "categories"],
        "web_selectors": ["footer", "[role='contentinfo']", ".footer", "[class*='comment']", "[class*='social']"],
        "fallback_ratio": [0.0, 0.84, 1.0, 1.0],
    },
]

_NOISE_NAME_RE = re.compile(r"^(image\s+\d+|frame\s*\d*|group\s*\d*)$", re.IGNORECASE)

# 默认（非 games）仅跳过“行高”：
#   用户要求不考虑文字行高（动态变化大）。文本内容本身也不参与属性评分。
_SKIP_PROPS = {"line_height"}
# 用于报告展示（中文名称）
_SKIP_PROPS_LABELS = ["行高"]

# games 模板要求：字体问题不能忽略 → 不跳过任何字体属性。
_SKIP_PROPS_GAMES = set()
_SKIP_PROPS_LABELS_GAMES: List[str] = []


# Self-contained report folder. Every artifact the user needs to view the
# report (HTML + screenshots + markdown) lives inside this one folder so
# it can be zipped and e-mailed in a single step.
FOCUSED_REPORT_DIR = Config.REPORTS_DIR / "focused_ui_report"


def _resolve_compare_profile(template: str) -> Dict[str, Any]:
    """Resolve property compare policy by template."""
    tmpl = (template or "").strip().lower()
    if tmpl == "games":
        return {
            "name": "games",
            "skip_props": _SKIP_PROPS_GAMES,
            "skip_prop_labels": _SKIP_PROPS_LABELS_GAMES,
            "strategy_cn": (
                "games 模板已移除稳定结构块分段，仅做整页截图对比；"
                "元素级对比覆盖尺寸、字体、字号、字重、行高、文字色、填充色、圆角。"
            ),
            "html_note": (
                "✅ <b>已启用字体属性对比</b>（字体 / 字号 / 字重 / 行高 / 文字色）。"
                "并且已移除稳定结构块分段，当前报告以整页截图对比 + 元素级差异为主。"
            ),
        }
    return {
        "name": "generic",
        "skip_props": _SKIP_PROPS,
        "skip_prop_labels": _SKIP_PROPS_LABELS,
        "strategy_cn": (
            "当前模式已移除稳定结构块分段，采用整页截图对比；"
            "元素级对比覆盖尺寸、颜色、圆角与必要字体属性。"
            "不比较文字内容与行高（动态波动项）。"
        ),
        "html_note": (
            "✅ <b>已忽略动态文本项</b>：文字内容与行高不纳入元素评分。"
            "当前报告为整页截图对比 + 元素级差异。"
        ),
    }


def _img_src(path: str) -> str:
    """Return a filename-only reference so images load even when the whole
    report folder is sent/copied/zipped elsewhere.

    All images are flat inside the report directory, so ``basename`` is enough.
    This function no longer depends on the global FOCUSED_REPORT_DIR, so
    multi-template runs each get the correct relative path automatically.
    """
    p = Path(path)
    if not p.exists():
        return ""
    return p.name


def _force_remove(path: Path) -> bool:
    """删除单个文件，Windows 下自动解除只读属性后重试。

    返回 True 表示删除成功，False 表示文件被其他进程锁定（如浏览器占用）。
    """
    import stat as _stat
    try:
        path.unlink()
        return True
    except PermissionError:
        # Windows 常见场景：文件被标记为只读，或浏览器正在查看该文件。
        # 先尝试去掉只读属性后重试一次。
        try:
            path.chmod(_stat.S_IWRITE | _stat.S_IREAD)
            path.unlink()
            return True
        except Exception:
            return False
    except Exception:
        return False


def _clean_dir_contents(directory: Path) -> list:
    """清空目录内所有文件/子目录，返回无法删除的文件路径列表。"""
    locked: list = []
    if not directory.exists():
        return locked
    for item in list(directory.iterdir()):
        try:
            if item.is_file() or item.is_symlink():
                if not _force_remove(item):
                    locked.append(item)
            elif item.is_dir():
                # 递归清空子目录；子目录中有锁定文件时也收集
                sub_locked = _clean_dir_contents(item)
                locked.extend(sub_locked)
                if not sub_locked:
                    try:
                        item.rmdir()  # 仅当目录已空时移除
                    except Exception:
                        pass
        except Exception:
            pass
    return locked


def _clean_output_dirs(report_dir: Optional[Path] = None) -> None:
    """每次运行前清空上次产物，确保报告始终是最新内容。

    Args:
        report_dir: 报告目录路径。默认使用 FOCUSED_REPORT_DIR；
                    多模板运行时传入对应模板的目录（如 focused_ui_report_games/）。

    Windows 注意事项：
      如果 index.html 正在被浏览器打开（文件被锁定），该文件无法删除。
      此时清理函数会打印警告并跳过该文件，新内容会直接覆盖写入——
      刷新浏览器（F5 或 Ctrl+R）即可看到最新报告。
    """
    rd = report_dir or FOCUSED_REPORT_DIR
    Config.setup_directories()

    # 1) 清空自包含报告目录（HTML + 所有 PNG）
    locked_files: list = []
    if rd.exists():
        locked_files = _clean_dir_contents(rd)
        # 若目录已完全清空，删除并重建；否则保留目录（内含锁定文件）
        if not locked_files:
            try:
                rd.rmdir()
            except Exception:
                pass
    rd.mkdir(parents=True, exist_ok=True)

    if locked_files:
        print(
            f"\n[WARN] 以下文件被其他进程锁定，无法删除（通常是浏览器正在查看报告）：\n"
            + "\n".join(f"       {f}" for f in locked_files)
            + "\n       → 新内容将直接覆盖写入；刷新浏览器（F5）即可看到最新报告。\n"
        )

    # 2) 清空 reports/json/（机器可读中间结果）
    json_dir = Config.REPORTS_DIR / "json"
    json_dir.mkdir(parents=True, exist_ok=True)
    for item in json_dir.iterdir():
        try:
            if item.is_file() or item.is_symlink():
                _force_remove(item)
            elif item.is_dir():
                _clean_dir_contents(item)
        except Exception:
            pass

    # 3) 清理旧版遗留文件
    for legacy_file in [
        Config.REPORTS_DIR / "report.html",
        Config.REPORTS_DIR / "verification_summary.md",
        Config.REPORTS_DIR / "focused_ui_report.html",
        Config.REPORTS_DIR / "focused_ui_summary.md",
    ]:
        if legacy_file.exists():
            _force_remove(legacy_file)

    # 4) 清理旧版目录结构
    for legacy_dir in [
        Config.REPORTS_DIR / "html",
        Config.REPORTS_DIR / "images",
        Config.SCREENSHOTS_DIR / "figma",
        Config.SCREENSHOTS_DIR / "web",
        Config.SCREENSHOTS_DIR / "site",
        Config.SCREENSHOTS_DIR,
    ]:
        if legacy_dir.exists():
            _clean_dir_contents(legacy_dir)
            try:
                legacy_dir.rmdir()
            except Exception:
                pass


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


def _page_scope_hints(page: Dict[str, Any]) -> List[str]:
    """Build robust scope hints for picking a child frame in a large CANVAS."""
    hints: List[str] = []
    explicit = (page.get("figma_scope") or "").strip().lower()
    if explicit:
        hints.append(explicit)

    key = (page.get("key") or "").strip().lower()
    label = (page.get("label") or "").strip().lower()
    url = (page.get("site_url") or "").strip().lower()
    hints.extend([key, label])

    # Heuristics for common naming drifts in design files.
    if "home" in key or "home" in label:
        hints.extend(["home", "index"])
    if "category" in key or "category" in label or "/games/" in url:
        hints.extend(["category", "categories", "list", "search-result"])
    if (
        "detail" in key
        or "details" in key
        or "detail" in label
        or "details" in label
        or "/play" in url
    ):
        hints.extend(["detail", "details", "play", "game-detail"])

    # Deduplicate while preserving order.
    dedup: List[str] = []
    for h in hints:
        h = h.strip()
        if h and h not in dedup:
            dedup.append(h)
    return dedup


def _select_figma_scope_node(root: Dict[str, Any], page: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Select the page-specific child frame from a multi-page CANVAS node.

    When the design node is a CANVAS containing siblings like "home/category/details",
    this picks the best-matching child frame and returns:
      (selected_node, selected_name)
    """
    children = root.get("children") or []
    if not children:
        return root, (root.get("name") or "")

    hints = _page_scope_hints(page)
    if not hints:
        return root, (root.get("name") or "")

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for idx, child in enumerate(children):
        box = _node_box(child)
        if not box:
            continue
        name = (child.get("name") or "").strip()
        lname = name.lower()
        ctype = (child.get("type") or "").upper()
        if ctype not in {"FRAME", "COMPONENT", "SECTION", "GROUP"}:
            continue

        score = 0.0
        for h in hints:
            if lname == h:
                score += 8.0
            elif h in lname:
                score += 3.0
            elif lname in h:
                score += 1.0

        # Slight preference for top-level "page-sized" frames.
        _, _, w, h = box
        if w >= 1000 and h >= 700:
            score += 1.5

        # Stable tie-breaker: earlier nodes first.
        score -= idx * 0.001
        if score > 0:
            scored.append((score, child))

    if not scored:
        return root, (root.get("name") or "")
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = scored[0][1]
    return picked, (picked.get("name") or "")


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
    # games 专用块
    "page_marker":  {"max_depth": 2, "min_width_ratio": 0.10, "min_height": 40,  "max_height_ratio": 0.35, "max_height_px": 420,  "y_rel_range": (0.0, 0.30)},
    "top_nav":      {"max_depth": 2, "min_width_ratio": 0.55, "min_height": 40,  "max_height_ratio": 0.22, "max_height_px": 460,  "y_rel_range": (0.08, 0.38)},
    "search_bar":   {"max_depth": 2, "min_width_ratio": 0.45, "min_height": 48,  "max_height_ratio": 0.25, "max_height_px": 500,  "y_rel_range": (0.14, 0.55)},
    "game_list":    {"max_depth": 2, "min_width_ratio": 0.50, "min_height": 180, "max_height_ratio": 0.60, "max_height_px": 1600, "y_rel_range": (0.26, 0.95)},
    "bottom_action":{"max_depth": 2, "min_width_ratio": 0.12, "min_height": 80,  "max_height_ratio": 0.30, "max_height_px": 1000, "y_rel_range": (0.55, 1.0)},
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


def _goto_page_robust(page, url: str, timeout_ms: int = 30000) -> None:
    """Navigate with fallback to reduce flaky networkidle timeouts."""
    try:
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        return
    except Exception as e1:
        # Some sites keep analytics/polling requests alive and never reach
        # strict networkidle. Fallback to DOM ready so comparison can proceed.
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1200)
            print(f"[WARN] networkidle 超时，已降级为 domcontentloaded: {url} ({type(e1).__name__})")
            return
        except Exception:
            raise


def _normalize_abs_url(url: str) -> str:
    """Normalize URL for stable equality checks."""
    u = (url or "").strip()
    if not u:
        return ""
    if "#" in u:
        u = u.split("#", 1)[0]
    if u.endswith("/"):
        u = u[:-1]
    return u


def _click_or_goto_target(page, target_url: str, timeout_ms: int = 12000) -> Tuple[str, str]:
    """Try click-based navigation first; fallback to direct goto.

    Returns:
        (method, error)
        method: "click" | "goto"
    """
    target_norm = _normalize_abs_url(target_url)
    try:
        idx = page.evaluate(
            """
            (target) => {
              const normalize = (u) => {
                try {
                  const x = new URL(u, window.location.href);
                  x.hash = "";
                  let s = x.href;
                  if (s.endsWith("/")) s = s.slice(0, -1);
                  return s;
                } catch (_) {
                  return "";
                }
              };
              const targetNorm = normalize(target);
              const links = Array.from(document.querySelectorAll("a[href]"));
              for (let i = 0; i < links.length; i++) {
                const a = links[i];
                const r = a.getBoundingClientRect();
                if (r.width < 4 || r.height < 4) continue;
                if (a.offsetParent === null) continue;
                if (normalize(a.href) === targetNorm) return i;
              }
              return -1;
            }
            """,
            target_url,
        )
        if isinstance(idx, int) and idx >= 0:
            loc = page.locator("a[href]").nth(idx)
            if loc.count() > 0:
                with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout_ms):
                    loc.click(timeout=2500)
                page.wait_for_timeout(500)
                cur = _normalize_abs_url(page.url)
                if cur == target_norm or cur.startswith(target_norm):
                    return "click", ""
    except Exception as exc:
        click_err = f"{type(exc).__name__}: {exc}"
    else:
        click_err = "no matching visible link"

    try:
        _goto_page_robust(page, target_url, timeout_ms=timeout_ms)
        return "goto", click_err
    except Exception as exc:
        return "goto", f"{click_err}; goto failed: {type(exc).__name__}: {exc}"


def _run_navigation_flows(page, pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run deterministic route-combo validation:
    home -> category -> detail -> home/category
    """
    by_key = {str(p.get("key", "")).strip().lower(): p for p in pages}
    home = by_key.get("home")
    category = by_key.get("category")
    detail = by_key.get("detail") or by_key.get("details")
    if not (home and category and detail):
        return [{
            "name": "组合流程验证",
            "status": "skipped",
            "error": "缺少 home/category/detail 页面配置，无法执行组合流程验证。",
            "steps": [],
        }]

    flows_def = [
        ("流程A: 首页→分类→详情→回首页", [home, category, detail, home]),
        ("流程B: 首页→分类→详情→回分类", [home, category, detail, category]),
    ]
    results: List[Dict[str, Any]] = []

    for flow_name, seq in flows_def:
        steps: List[Dict[str, Any]] = []
        ok = True
        err = ""
        for i in range(len(seq) - 1):
            src = seq[i]
            dst = seq[i + 1]
            dst_url = dst["site_url"]
            from_url = page.url
            method, method_err = _click_or_goto_target(page, dst_url, timeout_ms=15000)
            cur = _normalize_abs_url(page.url)
            expected = _normalize_abs_url(dst_url)
            step_ok = cur == expected or cur.startswith(expected)
            steps.append({
                "step": i + 1,
                "from_label": src.get("label", ""),
                "to_label": dst.get("label", ""),
                "from_url": from_url,
                "to_url": page.url,
                "expected_url": dst_url,
                "method": method,
                "ok": step_ok,
                "error": method_err if not step_ok else "",
            })
            if not step_ok:
                ok = False
                err = steps[-1]["error"] or f"未到达预期URL: {dst_url}"
                break
        results.append({
            "name": flow_name,
            "status": "ok" if ok else "failed",
            "error": err,
            "steps": steps,
        })
    return results


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


def _aggregate_function_checks(pages: List[Dict[str, Any]], flows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
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

    flow_items = flows or []
    flow_failed = sum(1 for f in flow_items if f.get("status") != "ok")

    return {
        "links": links,
        "buttons": buttons,
        "flows": flow_items,
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
            "flows_total": len(flow_items),
            "flows_failed": flow_failed,
            "flow_pass_rate": (1 - flow_failed / len(flow_items)) * 100 if flow_items else 0.0,
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
    flows = agg.get("flows", []) or []
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
    flow_rate = f"{summary.get('flow_pass_rate', 0):.1f}%" if summary.get("flows_total") else "—"

    flow_rows = []
    for flow in flows:
        flow_name = _escape_html(flow.get("name", ""))
        status = flow.get("status", "")
        cls = "ok" if status == "ok" else ("muted" if status == "skipped" else "fail")
        steps = flow.get("steps", []) or []
        if steps:
            step_desc = " → ".join(
                f"{_escape_html(s.get('from_label', ''))}→{_escape_html(s.get('to_label', ''))}"
                f"({_escape_html(s.get('method', ''))})"
                for s in steps
            )
        else:
            step_desc = "—"
        flow_rows.append(
            "<tr>"
            f"<td>{flow_name}</td>"
            f"<td>{step_desc}</td>"
            f"<td class='{cls}'>{_escape_html(status)}</td>"
            f"<td>{_escape_html(flow.get('error', ''))}</td>"
            "</tr>"
        )
    if not flow_rows:
        flow_rows.append("<tr><td colspan='4' class='empty'>未配置组合流程验证</td></tr>")

    return f"""
<section class="card">
  <h2>功能检测（全局 · 已去重）</h2>
  <p class="hint">链接按 URL 去重：同一条 Header/Footer 链接在多页都出现时，只保留一行，并在"出现页"列中标记来源页面。</p>
  <div class="stats">
    <div><b>唯一链接</b><span>{summary.get('unique_links', 0)}</span></div>
    <div><b>链接失败 / 通过率</b><span>{summary.get('unique_links_failed', 0)} · {link_rate}</span></div>
    <div><b>按钮点击次数</b><span>{summary.get('buttons_tested', 0)}</span></div>
    <div><b>按钮失败 / 通过率</b><span>{summary.get('buttons_failed', 0)} · {btn_rate}</span></div>
    <div><b>组合流程 / 通过率</b><span>{summary.get('flows_total', 0)} · {flow_rate}</span></div>
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
  <h3>组合流程验证（首页→分类→详情→回首页/回分类）</h3>
  <table>
    <thead>
      <tr><th>流程</th><th>步骤（含动作）</th><th>结果</th><th>备注</th></tr>
    </thead>
    <tbody>{''.join(flow_rows)}</tbody>
  </table>
  <h3>异常记录（Console / PageError / 4xx-5xx 请求）</h3>
  {errors_html}
</section>
"""


def _safe_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """写文件，Windows 文件锁场景下通过临时文件 + 重命名实现原子覆盖。

    直接 write_text() 在 Windows 上如果文件被浏览器以独占方式打开会抛
    PermissionError。改用 tempfile 写入同目录临时文件后重命名，可绕过大部分
    "文件正在被查看"的锁，确保每次运行后报告内容都是最新的。
    """
    import tempfile as _tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先尝试直接写入（最快路径）
    try:
        path.write_text(content, encoding=encoding)
        return
    except PermissionError:
        pass
    # 回退：写临时文件后 os.replace()（原子重命名，即使目标存在也成功）
    try:
        fd, tmp = _tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
    except Exception as exc:
        print(f"[WARN] 无法写入 {path.name}: {exc}  → 刷新浏览器 (F5) 后重试")


def _render_html(
    pages: List[Dict[str, Any]],
    function_agg: Dict[str, Any],
    output_path: Path,
    compare_note_html: str,
) -> Path:
    page_rows = []
    # 重点展示用户关心的元素级差异：尺寸 + 颜色 + 字体关键项 + 未匹配。
    issue_keys = [
        "width",
        "height",
        "font_size",
        "font_weight",
        "text_color",
        "fill_color",
        "border_radius",
        "unmatched",
    ]
    issue_header = "".join(f"<th>{k}</th>" for k in issue_keys)
    issue_rows = []
    for page in pages:
        elem = page["element"]
        issue_count = _issue_counts(page["top_diffs"])
        page_rows.append(
            f"<tr><td>{page['label']}</td><td>{page['pixel']['similarity']:.2f}%</td>"
            f"<td>{elem['overall_score']:.2%}</td><td>{elem['coverage_rate']:.2%}</td><td>{elem['total_matched']}</td><td>{elem['total_unmatched']}</td></tr>"
        )
        issue_rows.append(
            "<tr>"
            f"<td>{page['label']}</td>"
            + "".join(f"<td>{issue_count.get(k, 0)}</td>" for k in issue_keys)
            + "</tr>"
        )

    overview_tables = f"""
<section class="card">
  <h2>总览表格对比</h2>
  <h3>页面指标总览</h3>
  <table>
    <thead>
      <tr><th>页面</th><th>整页相似度</th><th>元素得分</th><th>元素覆盖率</th><th>匹配元素</th><th>未匹配元素</th></tr>
    </thead>
    <tbody>
      {''.join(page_rows)}
    </tbody>
  </table>
  <h3>元素差异频次矩阵（Top Diffs）</h3>
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
    <div><b>元素得分</b><span>{elem['overall_score']:.2%}</span></div>
    <div><b>元素覆盖率</b><span>{elem['coverage_rate']:.2%}</span></div>
    <div><b>匹配/未匹配</b><span>{elem['total_matched']}/{elem['total_unmatched']}</span></div>
  </div>
  <h3>整页截图对比（核心）</h3>
  <h3>开发修复建议</h3>
  <ul>{"".join(f"<li>{s}</li>" for s in suggestions) if suggestions else "<li>暂无建议</li>"}</ul>
  <div class="img-grid">
    <div><div class="lbl">整页 Figma</div><img src="{_img_src(pixel['figma_path'])}" /></div>
    <div><div class="lbl">整页 Website</div><img src="{_img_src(pixel['web_path'])}" /></div>
    <div><div class="lbl">整页 Diff</div><img src="{_img_src(pixel['diff_path'])}" /></div>
    <div><div class="lbl">整页 Side By Side</div><img src="{_img_src(pixel['compare_path'])}" /></div>
  </div>
  <h3>元素级差异列表（可直接改样式）</h3>
  <p style="font-size:12px;color:#6b7280;margin:0 0 10px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:8px 12px;">
    {compare_note_html}
  </p>
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
    <p>多页面视觉 + 元素属性对比 · 生成时间: {datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")}</p>
  </header>
  <div class="wrap">
    {overview_tables}
    {function_section_html}
    {''.join(cards)}
  </div>
</body>
</html>"""
    _safe_write_text(output_path, html)
    return output_path


def _render_markdown(
    pages: List[Dict[str, Any]],
    function_agg: Dict[str, Any],
    output_path: Path,
    compare_strategy_cn: str,
) -> Path:
    lines = [
        "# Focused UI Summary",
        "",
        "This report compares 3 fixed pages. Content text is not used as the core matching signal; the comparison focuses on layout, typography, colors, size, and radius.",
        "",
        f"对比策略：{compare_strategy_cn}",
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
                f"- Element overall score: {elem['overall_score']:.2%}",
                f"- Element coverage: {elem['coverage_rate']:.2%}",
                f"- Matched elements: {elem['total_matched']}",
                f"- Unmatched elements: {elem['total_unmatched']}",
            ]
        )
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
        f"- Flow combos: {summary.get('flows_total', 0)}, "
        f"failed: {summary.get('flows_failed', 0)}, "
        f"pass rate: {summary.get('flow_pass_rate', 0):.1f}%"
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
    flows = function_agg.get("flows") or []
    if flows:
        lines.append("")
        lines.append("## 组合流程验证（首页→分类→详情→回首页/回分类）")
        lines.append("")
        for flow in flows:
            lines.append(f"- {flow.get('name','')}: {flow.get('status','')}")
            for st in flow.get("steps", []) or []:
                lines.append(
                    f"  - Step {st.get('step')}: {st.get('from_label','')} -> {st.get('to_label','')} "
                    f"[{st.get('method','')}] {'OK' if st.get('ok') else 'FAIL'}"
                )
            if flow.get("error"):
                lines.append(f"  - error: {flow.get('error')}")
    lines.append("")

    _safe_write_text(output_path, "\n".join(lines))
    return output_path


def _parse_template_arg() -> str:
    """从 sys.argv 解析 --template NAME，返回模板名称。

    未指定时返回空字符串（使用默认配置 focused_pages.json）。

    支持两种写法：
      python -m src.focused_ui_check --template games
      python -m src.focused_ui_check --template=news
    """
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--template" and i + 1 < len(args):
            return args[i + 1].strip().lower()
        if a.startswith("--template="):
            return a.split("=", 1)[1].strip().lower()
    return ""


def run(template: str = "") -> Dict[str, Any]:
    """运行聚焦 UI 对比流水线（单模板）。

    Args:
        template: 模板名称（"games" / "news"）。
                  空字符串 → 读取全部已启用模板（旧格式兼容）。
    """
    _tmpl = template.strip().lower()

    if _tmpl:
        report_dir = Config.REPORTS_DIR / f"focused_ui_report_{_tmpl}"
        json_prefix = f"focused_{_tmpl}_"
        print(f"\n{'='*60}")
        print(f"[Focused UI Check]  模板: {_tmpl.upper()}")
        print(f"  报告目录: {report_dir}")
        print(f"{'='*60}\n")
    else:
        report_dir = FOCUSED_REPORT_DIR
        json_prefix = "focused_"

    pages = _load_focused_pages(FOCUSED_PAGES_CONFIG, template_filter=_tmpl or None)
    _clean_output_dirs(report_dir)
    profile = _resolve_compare_profile(_tmpl)
    skip_props = profile["skip_props"]
    skip_prop_labels = profile["skip_prop_labels"]
    compare_strategy_cn = profile["strategy_cn"]
    compare_note_html = profile["html_note"]

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
    navigation_flows: List[Dict[str, Any]] = []

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
        for page in pages:
            key = page["key"]
            # Every image for this run lives flat inside the self-contained
            # report folder, so shipping the folder gives the reader a
            # fully-working HTML report with all screenshots attached.
            figma_path = report_dir / f"{key}_full_figma.png"
            web_path = report_dir / f"{key}_full_web.png"
            diff_path = report_dir / f"{key}_full_diff.png"
            compare_path = report_dir / f"{key}_full_compare.png"

            # 1) Pull Figma node JSON. If this node is a multi-page CANVAS
            #    (home/category/details in one node), auto-pick the child frame
            #    matching current page and compare within that scope only.
            figma = _get_figma(page.get("figma_file_key"))
            node_json = figma.get_node_json(page["figma_node"])
            ReportWriter._write_json(Config.REPORTS_DIR / "json" / f"focused_figma_{key}.json", node_json)

            scope_node, scope_name = _select_figma_scope_node(node_json, page)
            root_box = _node_box(scope_node) or _node_box(node_json)
            design_w = int(round(root_box[2])) if root_box else Config.AGENT_VIEWPORT_WIDTH
            design_h = int(round(root_box[3])) if root_box else Config.AGENT_VIEWPORT_HEIGHT
            # Cap viewport width so we do not launch absurd viewports on large designs.
            viewport_w = max(360, min(design_w, 2560))
            viewport_h = max(600, min(design_h, 2200))

            # 2) Export full node at scale=1, then crop to selected page scope
            #    when needed. This fixes "one node contains multiple pages".
            figma_raw_path = norm_tmp / f"{key}_figma_raw.png"
            figma.save_node_to_file(page["figma_node"], figma_raw_path, scale=1)
            full_root_box = _node_box(node_json)
            scope_box = _node_box(scope_node)
            if (
                scope_node is not node_json
                and full_root_box is not None
                and scope_box is not None
            ):
                _crop_from_figma_image(figma_raw_path, full_root_box, scope_box, figma_path)
            else:
                # Already a page-level frame; keep as-is.
                shutil.copyfile(figma_raw_path, figma_path)

            # 3) Capture the site at the Figma-aligned viewport width.
            capture.page.set_viewport_size({"width": viewport_w, "height": viewport_h})
            _goto_page_robust(capture.page, page["site_url"], timeout_ms=30000)
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
                    "figma_scope_name": scope_name,
                    "figma_scope_id": scope_node.get("id", ""),
                    "figma_path": str(figma_path),
                    "web_path": str(web_path),
                    "diff_path": str(diff_path),
                    "compare_path": str(compare_path),
                    "viewport": {"width": viewport_w, "height": viewport_h},
                    "figma_design": {"width": design_w, "height": design_h},
                    "status": "ok",
                }
            )
            figma_elements = figma_extractor.extract_semantic(scope_node, max_depth=Config.COMPARE_MAX_DEPTH)
            figma_elements = [
                e for e in figma_elements
                if e.width >= 12
                and e.height >= 12
                and not _NOISE_NAME_RE.match((e.name or "").strip())
            ]
            # 让 AutoMapper 使用全量候选节点自动构建根坐标包围盒，
            # 避免“首个 FRAME 非页面根”导致的大面积坐标偏移。
            auto_map = auto_mapper.generate(figma_elements=figma_elements, page=capture.page, root_frame=None)
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
                skip_props=skip_props,
            )

            element_payload = {
                "version": version,
                "generated_at": datetime.now(UTC).isoformat(),
                "page_key": key,
                "page_label": page["label"],
                "figma_node": page["figma_node"],
                "figma_scope_name": scope_name,
                "figma_scope_id": scope_node.get("id", ""),
                "figma_url": page["figma_url"],
                "site_url": page["site_url"],
                "result": result,
                "auto_mapped_count": len(auto_map),
                # 明确记录跳过的属性，供报告层展示说明
                "skipped_props": sorted(skip_props),
                "skipped_props_reason": (
                    "已按当前模板策略决定是否跳过字体类属性。"
                    if skip_props
                    else "当前模板已启用字体类属性对比（未跳过字体/字号/字重/行高/文字色）。"
                ),
                "compare_strategy": compare_strategy_cn,
                "compare_config": {
                    "color_tolerance":     Config.COMPARE_COLOR_TOLERANCE,
                    "size_tolerance":      Config.COMPARE_SIZE_TOLERANCE,
                    "font_size_tolerance": Config.COMPARE_FONT_SIZE_TOLERANCE,
                    "radius_tolerance":    Config.COMPARE_RADIUS_TOLERANCE,
                },
            }
            ReportWriter._write_json(Config.REPORTS_DIR / "json" / f"element_diff_{key}.json", element_payload)

            # --- Functional check: links + buttons + console/network anomalies.
            # Details pages mostly re-expose the same header/footer/related
            # links found on Home + Category. We keep the per-page cap small
            # so only its genuinely unique links show up after global dedup.
            if key in {"detail", "details"} or "detail" in key:
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
                    "figma_scope_name": scope_name,
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
                    "function_check": function_check,
                }
            )

        # 组合流程验证：首页 -> 分类 -> 详情 -> 回首页/回分类
        try:
            navigation_flows = _run_navigation_flows(capture.page, pages)
        except Exception as exc:  # pragma: no cover - defensive
            navigation_flows = [{
                "name": "组合流程验证",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "steps": [],
            }]

    # Deduplicate link results across pages so the report stops repeating
    # the same Header/Footer nav links once per page.
    function_agg = _aggregate_function_checks(element_pages, flows=navigation_flows)

    focused_run = {
        "version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": Config.BASE_URL,
        "compare_strategy": compare_strategy_cn,
        "skip_props": sorted(skip_props),
        "skip_prop_labels": skip_prop_labels,
        "page_results": page_results,
        "function_check_global": function_agg,
    }
    # JSON 文件名加模板前缀，避免两套模板互相覆盖
    json_dir = Config.REPORTS_DIR / "json"
    ReportWriter._write_json(json_dir / f"{json_prefix}run_result.json", focused_run)
    ReportWriter._write_json(json_dir / f"{json_prefix}element_diffs.json", {"pages": element_pages})
    ReportWriter._write_json(json_dir / f"{json_prefix}function_global.json", function_agg)

    # Write HTML + Markdown INSIDE the self-contained folder so it can be
    # zipped / mailed in one shot (every <img> points to a sibling file).
    html_path = _render_html(
        element_pages,
        function_agg,
        report_dir / "index.html",
        compare_note_html,
    )
    md_path = _render_markdown(
        element_pages,
        function_agg,
        report_dir / "summary.md",
        compare_strategy_cn,
    )

    # Drop the normalization scratch dir once the report is written.
    shutil.rmtree(norm_tmp, ignore_errors=True)

    print(f"[OK] Focused report generated: {html_path}")
    print(f"[OK] Folder (zip & send this): {report_dir}")
    print(f"[OK] Focused summary generated: {md_path}")
    return {
        "template": _tmpl or "default",
        "focused_run": focused_run,
        "element_pages": element_pages,
        "function_check_global": function_agg,
        "html_report": str(html_path),
        "markdown_summary": str(md_path),
        "report_folder": str(report_dir),
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m src.focused_ui_check",
        description=(
            "聚焦 UI 对比。\n"
            "配置文件：config/focused_pages.json\n"
            "  - 推荐使用最简 pages 列表（home/category/detail）\n"
            "  - 默认运行该列表并输出 reports/focused_ui_report/\n"
            "  - 可选 --template 仅用于兼容旧模板配置"
        ),
    )
    parser.add_argument(
        "--template",
        metavar="NAME",
        default="",
        help="强制只跑指定模板（games / news）。不传则自动运行所有已配置模板。",
    )
    args = parser.parse_args()

    if args.template:
        # 明确指定模板：只跑一套
        run(template=args.template)
    else:
        # 自动检测：读配置，看哪些模板填了 figma_node 就跑哪些
        try:
            _raw = json.loads(FOCUSED_PAGES_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            _raw = {}

        _known = [k for k in ("games", "news") if k in _raw]

        if _known:
            # 新模板格式：逐模板判断
            _enabled = [
                k for k in _known
                if (_raw[k].get("figma_node") or "").strip()
            ]
            if not _enabled:
                print(
                    "[ERROR] focused_pages.json 中所有模板的 figma_node 均为空。\n"
                    "        请填写至少一个模板的 figma_node 后再运行。\n"
                    "        示例：games.figma_node = \"16000:13788\""
                )
                sys.exit(1)
            for _tmpl in _enabled:
                run(template=_tmpl)
        else:
            # 旧平铺格式：直接跑
            run()
