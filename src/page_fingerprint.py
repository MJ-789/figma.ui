"""
src/page_fingerprint.py  ── 页面相似度计算
=====================================================
职责：
    为"页面配对"提供底层相似度评分函数。
    输入是 site_inventory 的 DiscoveredPage（dict）和
    figma_inventory 的 FigmaPageEntry（dict），
    输出 0~1 之间的相似度分数。

    不依赖任何外部 AI，纯规则计算，用于候选召回和初步排序。
    后续 AI 精排只需在 top-K 候选上做判断即可。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List


_PAGE_TYPE_ALIASES = {
    "home": {"home", "homepage", "landing", "index", "首", "首页"},
    "category": {"category", "categories", "list", "listing", "栏目", "分类"},
    "detail": {"detail", "details", "article", "post", "story", "详情", "文章"},
    "search": {"search", "result", "results", "搜索"},
    "author": {"author", "profile", "user", "person", "作者", "个人"},
}


def text_similarity(a: str, b: str) -> float:
    """两段文本的归一化相似度（0~1），不区分大小写。"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def list_overlap(list_a: List[str], list_b: List[str]) -> float:
    """两组文本列表的 Jaccard 重叠度（0~1），不区分大小写。"""
    if not list_a or not list_b:
        return 0.0
    set_a = {s.lower() for s in list_a if s}
    set_b = {s.lower() for s in list_b if s}
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def best_pairwise_similarity(list_a: List[str], list_b: List[str]) -> float:
    """
    对 list_a 中每条文本，找 list_b 中最接近的，取平均值。
    适用于"两边文字表达不完全一样，但含义接近"的场景。
    """
    if not list_a or not list_b:
        return 0.0
    scores = []
    for a in list_a:
        best = max(text_similarity(a, b) for b in list_b)
        scores.append(best)
    return sum(scores) / len(scores)


def _normalize_name(name: str) -> str:
    """把 Frame / 页面名中的各种分隔符统一成空格小写，便于比较。"""
    name = re.sub(r"[/_\-]+", " ", name)
    return re.sub(r"\s+", " ", name).strip().lower()


def _page_type_from_figma_name(figma_name: str) -> str:
    normalized = _normalize_name(figma_name)
    if not normalized:
        return "unknown"
    for page_type, aliases in _PAGE_TYPE_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return page_type
    return "unknown"


def _page_type_from_site(site_path: str, site_title: str) -> str:
    normalized = _normalize_name(f"{site_path} {site_title}")
    path_parts = [p for p in site_path.strip("/").split("/") if p]
    if site_path == "/":
        return "home"
    if "/list/" in site_path or site_path.rstrip("/") == "/list":
        return "category"
    if "/search" in site_path:
        return "search"
    if any(token in normalized for token in _PAGE_TYPE_ALIASES["author"]):
        return "author"
    # 形如 /es/some-long-slug 的深层文章页通常是 detail 模板
    if len(path_parts) >= 2 and path_parts[-2].lower() not in {"list", "search", "author", "profile", "user"}:
        return "detail"
    if any(part in normalized for part in _PAGE_TYPE_ALIASES["detail"]):
        return "detail"
    for page_type, aliases in _PAGE_TYPE_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            return page_type
    return "unknown"


def page_type_similarity(figma_name: str, site_title: str, site_path: str) -> float:
    """按页面类型打分，更贴近视觉结构，而不是具体文案。"""
    figma_type = _page_type_from_figma_name(figma_name)
    site_type = _page_type_from_site(site_path, site_title)
    if figma_type == "unknown" or site_type == "unknown":
        return 0.0
    if figma_type == site_type:
        return 1.0
    # category/listing 视觉结构通常接近
    if {figma_type, site_type} <= {"category", "search"}:
        return 0.45
    return 0.0


def name_similarity(figma_name: str, site_title: str, site_path: str) -> float:
    """
    综合比较 Figma Frame 名称与网站页面标题 + 路径。
    取三者最高分：
      - Frame 名 vs 页面标题
      - Frame 名 vs URL 最后一段路径
      - Frame 名 vs 完整路径
    """
    fn = _normalize_name(figma_name)
    if not fn:
        return 0.0

    scores = []

    if site_title:
        scores.append(text_similarity(fn, _normalize_name(site_title)))

    path_parts = [p for p in site_path.strip("/").split("/") if p]
    if path_parts:
        last_seg = _normalize_name(path_parts[-1])
        scores.append(text_similarity(fn, last_seg))
        full_path = _normalize_name(" ".join(path_parts))
        scores.append(text_similarity(fn, full_path))
    else:
        if any(kw in fn for kw in ("home", "首页", "index", "landing")):
            scores.append(0.85)
        else:
            scores.append(0.0)

    return max(scores) if scores else 0.0


def structure_similarity(
    figma_structure: Dict[str, int],
    site_dom: Dict[str, Any],
) -> float:
    """
    比较 Figma 结构计数与网站 DOM 计数。
    两边字段名不同，需要做映射。
    """
    pairs = [
        (figma_structure.get("text_count", 0), int(site_dom.get("heading_count", 0)) + int(site_dom.get("button_count", 0))),
        (figma_structure.get("button_hint_count", 0), int(site_dom.get("button_count", 0))),
        (figma_structure.get("image_count", 0), int(site_dom.get("image_count", 0))),
    ]

    if not pairs:
        return 0.0

    scores = []
    for a, b in pairs:
        if a == 0 and b == 0:
            scores.append(1.0)
        elif a == 0 or b == 0:
            scores.append(0.0)
        else:
            ratio = min(a, b) / max(a, b)
            scores.append(ratio)

    return sum(scores) / len(scores)


def compute_page_similarity(
    figma_page: Dict[str, Any],
    site_page: Dict[str, Any],
    weights: Dict[str, float] | None = None,
) -> Dict[str, Any]:
    """
    计算一对 (Figma page, Site page) 的综合相似度。

    Args:
        figma_page: figma_inventory 中的一个条目（dict）
        site_page:  site_inventory 中的一个条目（dict）
        weights:    各维度权重，默认均衡

    Returns:
        {
            "name_score": float,
            "text_score": float,
            "structure_score": float,
            "total_score": float,
            "details": {...}
        }
    """
    w = weights or {
        "name": 0.35,
        "text": 0.10,
        "structure": 0.30,
        "page_type": 0.25,
    }

    n_score = name_similarity(
        figma_page.get("frame_name", ""),
        site_page.get("title", ""),
        site_page.get("path", ""),
    )

    figma_texts = figma_page.get("text_summary", [])
    site_texts = site_page.get("text_summary", [])
    t_overlap = list_overlap(figma_texts, site_texts)
    t_pairwise = best_pairwise_similarity(figma_texts, site_texts)
    t_score = max(t_overlap, t_pairwise)

    s_score = structure_similarity(
        figma_page.get("structure_summary", {}),
        site_page.get("dom_summary", {}),
    )
    p_score = page_type_similarity(
        figma_page.get("frame_name", ""),
        site_page.get("title", ""),
        site_page.get("path", ""),
    )

    total = (
        w["name"] * n_score
        + w["text"] * t_score
        + w["structure"] * s_score
        + w["page_type"] * p_score
    )

    return {
        "name_score": round(n_score, 4),
        "text_score": round(t_score, 4),
        "structure_score": round(s_score, 4),
        "page_type_score": round(p_score, 4),
        "total_score": round(total, 4),
    }
