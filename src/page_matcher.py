"""
src/page_matcher.py  ── 自动页面配对模块
=====================================================
职责：
    接收 site_inventory 和 figma_inventory，自动判断
    "哪个 Figma 页面对应哪个网站页面"，输出 page_pairs.json。

    配对策略：规则召回 top-K → 按综合分排序 → 贪心分配。
    后续可在 top-K 上叠加 LLM 精排（本版本先不做）。
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from config.config import Config
from src.page_fingerprint import compute_page_similarity
from src.report_writer import ReportWriter

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


@dataclass
class PagePair:
    """一对已配对的 Figma 页面与网站页面。"""

    figma_page_id: str
    figma_node_id: str
    figma_name: str
    site_page_id: str
    site_url: str
    site_path: str
    match_method: str
    confidence: float
    scores: Dict[str, float]
    reason: str
    status: str


class PageMatcher:
    """自动页面配对器。"""

    def __init__(
        self,
        top_k: int | None = None,
        min_confidence: float | None = None,
    ):
        self.top_k = top_k if top_k is not None else Config.PAGE_MATCH_TOP_K
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else Config.PAGE_MATCH_MIN_CONFIDENCE
        )
        self.weights = {
            "name": Config.PAGE_MATCH_WEIGHT_NAME,
            "text": Config.PAGE_MATCH_WEIGHT_TEXT,
            "structure": Config.PAGE_MATCH_WEIGHT_STRUCTURE,
            "page_type": Config.PAGE_MATCH_WEIGHT_PAGE_TYPE,
        }

    def match(
        self,
        figma_pages: List[Dict[str, Any]],
        site_pages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        对所有 Figma 页面和网站页面做自动配对。

        Args:
            figma_pages: figma_inventory["pages"] 列表
            site_pages:  site_inventory["pages"] 列表

        Returns:
            完整配对结果 dict（和 page_pairs.json 结构一致）
        """
        all_candidates = self._score_all(figma_pages, site_pages)
        pairs, unmatched_figma, unmatched_site = self._greedy_assign(
            all_candidates, figma_pages, site_pages
        )

        generated_at = datetime.now(UTC).isoformat()
        return {
            "generated_at": generated_at,
            "config": {
                "top_k": self.top_k,
                "min_confidence": self.min_confidence,
                "weights": self.weights,
            },
            "pairs": [asdict(p) for p in pairs],
            "unmatched_figma_pages": unmatched_figma,
            "unmatched_site_pages": unmatched_site,
            "summary": {
                "total_figma": len(figma_pages),
                "total_site": len(site_pages),
                "matched": len(pairs),
                "unmatched_figma": len(unmatched_figma),
                "unmatched_site": len(unmatched_site),
            },
        }

    def match_and_save(
        self,
        figma_pages: List[Dict[str, Any]],
        site_pages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """配对并写入 page_pairs.json。"""
        result = self.match(figma_pages, site_pages)
        Config.setup_directories()
        ReportWriter.write_page_pairs(
            output_path=Config.PAGE_PAIRS_PATH,
            payload=result,
        )
        return result

    def _score_all(
        self,
        figma_pages: List[Dict[str, Any]],
        site_pages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """为每个 Figma 页面计算与所有网站页面的相似度。"""
        candidates = []
        for fp in figma_pages:
            scored = []
            for sp in site_pages:
                sim = compute_page_similarity(fp, sp, weights=self.weights)
                scored.append({
                    "figma": fp,
                    "site": sp,
                    "scores": sim,
                    "total": sim["total_score"],
                })
            scored.sort(key=lambda x: x["total"], reverse=True)
            candidates.append({
                "figma": fp,
                "top_candidates": scored[: self.top_k],
            })
        return candidates

    def _greedy_assign(
        self,
        all_candidates: List[Dict[str, Any]],
        figma_pages: List[Dict[str, Any]],
        site_pages: List[Dict[str, Any]],
    ):
        """
        贪心分配：按最佳匹配得分从高到低，每个网站页面只分配一次。
        """
        flat: List[Dict[str, Any]] = []
        for entry in all_candidates:
            fp = entry["figma"]
            for cand in entry["top_candidates"]:
                flat.append({
                    "figma": fp,
                    "site": cand["site"],
                    "scores": cand["scores"],
                    "total": cand["total"],
                })
        flat.sort(key=lambda x: x["total"], reverse=True)

        used_figma: set = set()
        used_site: set = set()
        pairs: List[PagePair] = []

        for item in flat:
            fid = item["figma"].get("figma_page_id", "")
            sid = item["site"].get("page_id", "")
            if fid in used_figma or sid in used_site:
                continue
            if item["total"] < self.min_confidence:
                continue

            scores = item["scores"]
            reason_parts = []
            if scores["name_score"] >= 0.6:
                reason_parts.append("名称相似")
            if scores.get("page_type_score", 0) >= 0.8:
                reason_parts.append("页面类型一致")
            if scores["structure_score"] >= 0.5:
                reason_parts.append("结构接近")
            if scores["text_score"] >= 0.55:
                reason_parts.append("文本辅助命中")
            reason = "，".join(reason_parts) if reason_parts else "综合得分达标"

            pairs.append(PagePair(
                figma_page_id=fid,
                figma_node_id=item["figma"].get("figma_node_id", ""),
                figma_name=item["figma"].get("frame_name", ""),
                site_page_id=sid,
                site_url=item["site"].get("url", ""),
                site_path=item["site"].get("path", ""),
                match_method="rules",
                confidence=round(item["total"], 4),
                scores=scores,
                reason=reason,
                status="matched",
            ))

            used_figma.add(fid)
            used_site.add(sid)

        all_figma_ids = {fp.get("figma_page_id", "") for fp in figma_pages}
        all_site_ids = {sp.get("page_id", "") for sp in site_pages}

        unmatched_figma = [
            {"figma_page_id": fid, "frame_name": next(
                (fp.get("frame_name", "") for fp in figma_pages if fp.get("figma_page_id") == fid), ""
            )}
            for fid in sorted(all_figma_ids - used_figma)
        ]
        unmatched_site = [
            {"page_id": sid, "url": next(
                (sp.get("url", "") for sp in site_pages if sp.get("page_id") == sid), ""
            )}
            for sid in sorted(all_site_ids - used_site)
        ]

        return pairs, unmatched_figma, unmatched_site
