"""
src/figma_page_indexer.py  ── Figma 设计稿页面索引模块
=====================================================
职责：
    读取 Figma 文件结构（通过 FigmaClient），把所有顶层页面和 Frame
    整理成结构化清单 figma_inventory.json，供后续页面配对直接消费。

    不依赖真实 Figma API 也能工作：核心逻辑 index_from_file_data()
    只接收文件结构 dict，方便测试和离线使用。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.config import Config
from src.report_writer import ReportWriter

try:
    from datetime import UTC
except ImportError:  # pragma: no cover
    from datetime import timezone

    UTC = timezone.utc


@dataclass
class FigmaPageEntry:
    """Figma 中一个可测试的顶层 Frame / Component。"""

    figma_page_id: str
    figma_node_id: str
    page_name: str
    frame_name: str
    frame_type: str
    size: Dict[str, float]
    text_summary: List[str]
    style_summary: Dict[str, Any]
    structure_summary: Dict[str, int]
    fingerprint: Dict[str, str]


class FigmaPageIndexer:
    """从 Figma 文件结构中索引所有顶层 Frame，生成设计稿清单。"""

    # 自动生成的无意义名称
    _AUTO_NAME_RE = re.compile(
        r"^(Frame|Group|Rectangle|Component|Instance|Section)\s+\d+$",
        re.IGNORECASE,
    )

    @classmethod
    def _normalize_node_id(cls, node_id: str) -> str:
        return (node_id or "").replace("-", ":").strip()

    @classmethod
    def _find_node_by_id(cls, node: dict, target_node_id: str) -> Optional[dict]:
        if cls._normalize_node_id(node.get("id", "")) == target_node_id:
            return node
        for child in node.get("children", []):
            found = cls._find_node_by_id(child, target_node_id)
            if found:
                return found
        return None

    @classmethod
    def index_from_file_data(
        cls,
        file_data: dict,
        target_node_id: str | None = None,
    ) -> List[FigmaPageEntry]:
        """
        纯数据方法：从 get_file_structure() 的返回值中提取页面清单。

        Args:
            file_data: FigmaClient.get_file_structure() 的完整返回 dict，
                       结构为 {"document": {"children": [page, ...]}, ...}

        Returns:
            FigmaPageEntry 列表，每条对应一个顶层 FRAME / COMPONENT
        """
        document = file_data.get("document", {})
        normalized_target = cls._normalize_node_id(target_node_id or "")

        if normalized_target:
            target_node = cls._find_node_by_id(document, normalized_target)
            if not target_node:
                return []
            return cls._index_from_target_node(target_node)

        entries: List[FigmaPageEntry] = []
        for page_node in document.get("children", []):
            page_name = page_node.get("name", "")
            for child in page_node.get("children", []):
                child_type = child.get("type", "")
                if child_type not in ("FRAME", "COMPONENT", "COMPONENT_SET"):
                    continue
                entry = cls._build_entry(page_name, child)
                if entry is not None:
                    entries.append(entry)

        return entries

    @classmethod
    def _index_from_target_node(cls, target_node: dict) -> List[FigmaPageEntry]:
        """仅索引指定目标节点下的页面，避免扫描整份 Figma 文件。"""
        node_type = target_node.get("type", "")
        node_name = target_node.get("name", "")

        if node_type in ("FRAME", "COMPONENT", "COMPONENT_SET"):
            entry = cls._build_entry(node_name, target_node)
            return [entry] if entry is not None else []

        entries: List[FigmaPageEntry] = []
        for child in target_node.get("children", []):
            child_type = child.get("type", "")
            if child_type not in ("FRAME", "COMPONENT", "COMPONENT_SET"):
                continue
            entry = cls._build_entry(node_name, child)
            if entry is not None:
                entries.append(entry)
        return entries

    @classmethod
    def _build_entry(cls, page_name: str, frame_node: dict) -> Optional[FigmaPageEntry]:
        """把一个顶层 Frame/Component 节点转成结构化条目。"""
        name = frame_node.get("name", "")
        node_id = frame_node.get("id", "")
        node_type = frame_node.get("type", "")

        box = frame_node.get("absoluteBoundingBox") or {}
        width = float(box.get("width", 0))
        height = float(box.get("height", 0))

        # 跳过极小 Frame（图标 / 装饰碎片）
        if width < 100 or height < 100:
            return None
        if Config.FIGMA_INDEX_MIN_WIDTH and width < Config.FIGMA_INDEX_MIN_WIDTH:
            return None

        texts = cls._collect_texts(frame_node, max_items=10)
        colors = cls._collect_colors(frame_node, max_items=6)
        fonts = cls._collect_fonts(frame_node, max_items=4)
        counts = cls._count_structure(frame_node)

        text_summary = cls._dedupe(texts)
        fingerprint = cls._make_fingerprint(name, text_summary, counts)

        return FigmaPageEntry(
            figma_page_id=f"figma::{node_id}",
            figma_node_id=node_id,
            page_name=page_name,
            frame_name=name,
            frame_type=node_type,
            size={"width": width, "height": height},
            text_summary=text_summary,
            style_summary={
                "primary_colors": colors,
                "font_families": fonts,
            },
            structure_summary=counts,
            fingerprint=fingerprint,
        )

    # ------------------------------------------------------------------
    # 递归收集辅助方法
    # ------------------------------------------------------------------

    @classmethod
    def _collect_texts(cls, node: dict, max_items: int = 10) -> List[str]:
        """DFS 收集 TEXT 节点的 characters 字段。"""
        result: List[str] = []
        cls._walk_texts(node, result, max_items)
        return result

    @classmethod
    def _walk_texts(cls, node: dict, result: List[str], limit: int) -> None:
        if len(result) >= limit:
            return
        if node.get("type") == "TEXT":
            raw = (node.get("characters") or "").strip()
            compact = re.sub(r"\s+", " ", raw)
            if compact and len(compact) <= 120:
                result.append(compact)
        for child in node.get("children", []):
            if len(result) >= limit:
                return
            cls._walk_texts(child, result, limit)

    @classmethod
    def _collect_colors(cls, node: dict, max_items: int = 6) -> List[str]:
        """收集去重后的 SOLID 填充颜色。"""
        colors: List[str] = []
        seen: set[str] = set()
        cls._walk_colors(node, colors, seen, max_items)
        return colors

    @classmethod
    def _walk_colors(cls, node: dict, colors: List[str], seen: set, limit: int) -> None:
        if len(colors) >= limit:
            return
        for fill in node.get("fills", []):
            if fill.get("type") == "SOLID" and fill.get("visible", True):
                c = fill.get("color", {})
                r = round(c.get("r", 0) * 255)
                g = round(c.get("g", 0) * 255)
                b = round(c.get("b", 0) * 255)
                hex_color = f"#{r:02X}{g:02X}{b:02X}"
                if hex_color not in seen:
                    seen.add(hex_color)
                    colors.append(hex_color)
        for child in node.get("children", []):
            if len(colors) >= limit:
                return
            cls._walk_colors(child, colors, seen, limit)

    @classmethod
    def _collect_fonts(cls, node: dict, max_items: int = 4) -> List[str]:
        """收集去重后的字体族名。"""
        fonts: List[str] = []
        seen: set[str] = set()
        cls._walk_fonts(node, fonts, seen, max_items)
        return fonts

    @classmethod
    def _walk_fonts(cls, node: dict, fonts: List[str], seen: set, limit: int) -> None:
        if len(fonts) >= limit:
            return
        style = node.get("style") or {}
        family = style.get("fontFamily", "")
        if family and family not in seen:
            seen.add(family)
            fonts.append(family)
        for child in node.get("children", []):
            if len(fonts) >= limit:
                return
            cls._walk_fonts(child, fonts, seen, limit)

    @classmethod
    def _count_structure(cls, node: dict) -> Dict[str, int]:
        """统计 Frame 内各类节点数量，供页面指纹使用。"""
        counts = {
            "text_count": 0,
            "frame_count": 0,
            "component_count": 0,
            "image_count": 0,
            "button_hint_count": 0,
            "total_children": 0,
        }
        cls._walk_counts(node, counts)
        return counts

    @classmethod
    def _walk_counts(cls, node: dict, counts: Dict[str, int]) -> None:
        node_type = node.get("type", "")
        name_lower = node.get("name", "").lower()

        if node_type == "TEXT":
            counts["text_count"] += 1
        elif node_type in ("FRAME", "GROUP", "SECTION"):
            counts["frame_count"] += 1
        elif node_type in ("COMPONENT", "INSTANCE", "COMPONENT_SET"):
            counts["component_count"] += 1
        elif node_type in ("RECTANGLE", "ELLIPSE") and any(
            f.get("type") == "IMAGE" for f in node.get("fills", [])
        ):
            counts["image_count"] += 1

        if any(kw in name_lower for kw in ("button", "btn", "cta", "按钮")):
            counts["button_hint_count"] += 1

        children = node.get("children", [])
        counts["total_children"] += len(children)
        for child in children:
            cls._walk_counts(child, counts)

    # ------------------------------------------------------------------
    # 指纹 / 去重
    # ------------------------------------------------------------------

    @staticmethod
    def _dedupe(values: List[str], limit: int = 8) -> List[str]:
        result: List[str] = []
        seen: set[str] = set()
        for v in values:
            lower = v.lower()
            if lower in seen:
                continue
            seen.add(lower)
            result.append(v)
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _make_fingerprint(
        name: str, text_summary: List[str], counts: Dict[str, int]
    ) -> Dict[str, str]:
        layout_seed = "|".join(str(counts.get(k, 0)) for k in sorted(counts.keys()))
        text_seed = "|".join([name] + text_summary)
        return {
            "name_key": hashlib.md5(name.encode("utf-8")).hexdigest()[:12],
            "layout_key": hashlib.md5(layout_seed.encode("utf-8")).hexdigest()[:12],
            "text_key": hashlib.md5(text_seed.encode("utf-8")).hexdigest()[:12],
        }

    # ------------------------------------------------------------------
    # 高层入口：连接 FigmaClient + 写报告
    # ------------------------------------------------------------------

    @classmethod
    def index(
        cls,
        write_report: bool = True,
        target_node_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        从 Figma API 拉取文件结构并索引，输出 figma_inventory.json。

        Returns:
            完整的 inventory dict（和 JSON 内容一致）
        """
        from src.figma_client import FigmaClient

        Config.setup_directories()
        client = FigmaClient()
        file_data = client.get_file_structure()
        scope_node_id = cls._normalize_node_id(target_node_id or Config.FIGMA_TARGET_NODE_ID)
        entries = cls.index_from_file_data(file_data, target_node_id=scope_node_id or None)

        generated_at = datetime.now(UTC).isoformat()
        payload = {
            "figma_file_key": Config.FIGMA_FILE_KEY,
            "generated_at": generated_at,
            "pages": [asdict(e) for e in entries],
            "summary": {
                "total_pages": len(entries),
                "scope_node_id": scope_node_id,
            },
        }

        if write_report:
            ReportWriter.write_figma_inventory(
                output_path=Config.FIGMA_INVENTORY_PATH,
                figma_file_key=Config.FIGMA_FILE_KEY or "",
                summary=payload["summary"],
                pages=payload["pages"],
                generated_at=generated_at,
            )

        return payload
