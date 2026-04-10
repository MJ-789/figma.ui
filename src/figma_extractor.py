"""
src/figma_extractor.py  ── Figma 节点属性提取模块
================================================
职责：
    接收 FigmaClient.get_node_json() 返回的节点字典，
    递归展平为平坦的 FigmaElement 列表，每条记录包含
    位置、尺寸、颜色、字体等设计属性，供 ElementCompare 使用。

核心类 FigmaExtractor：
    extract(node_json)          ── 递归展平入口，返回全量 list[FigmaElement]。
    extract_semantic(node_json) ── 在 extract 基础上过滤噪音节点，
                                   只保留语义化的、可与 DOM 对应的节点。
                                   推荐在 AutoMapper / ElementCompare 使用此方法。
    is_semantic(elem)           ── 判断单个节点是否为有意义的语义节点。
    _visit(node, result)        ── 递归遍历子节点。
    _to_element(node)           ── 把单个节点 dict 转成 FigmaElement。
    _parse_color(fills)         ── Figma fills 列表 → "#RRGGBB" 或 None。
    _parse_stroke_color(strokes)── Figma strokes 列表 → "#RRGGBB" 或 None。
    _parse_border_radius(node)  ── 提取统一圆角或四角最大值（px）。
    _parse_font(style)          ── 从 style 字段提取字体属性。

FigmaElement 字段说明：
    id            ── Figma 节点 ID（"数字:数字"）
    name          ── 节点命名（设计稿中的 Layer 名称）
    node_type     ── FRAME / TEXT / RECTANGLE / COMPONENT / VECTOR 等
    x, y          ── 相对于父帧的左上角坐标（px，绝对画布坐标）
    width, height ── 节点尺寸（px）
    fill_color    ── 第一个 SOLID fill 的十六进制颜色，None 表示无填充
    stroke_color  ── 第一个描边颜色
    border_radius ── 统一圆角（px），无则 None
    font_family   ── 字体族名称（TEXT 节点）
    font_size     ── 字号（px，TEXT 节点）
    font_weight   ── 字重（100~900，TEXT 节点）
    line_height   ── 行高（px，TEXT 节点）
    text_content  ── 节点的可见文本（TEXT 节点，来自 characters 字段）
    opacity       ── 透明度（0~1，默认 1）
    children_count── 直接子节点数量（便于过滤叶节点）

噪音节点过滤规则（is_semantic / extract_semantic）：
    1. 装饰性节点类型：VECTOR / LINE / STAR / POLYGON / BOOLEAN_OPERATION
    2. 自动生成名称：匹配 "Frame 123456" / "Line 97" / "Rectangle 165" 等模式
    3. 极小节点：宽或高 < 8px（通常为分割线、点缀元素）
    4. 完全透明：opacity == 0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ==============================
# 数据类
# ==============================

@dataclass
class FigmaElement:
    id: str
    name: str
    node_type: str
    x: float
    y: float
    width: float
    height: float
    fill_color: Optional[str]
    stroke_color: Optional[str]
    border_radius: Optional[float]
    font_family: Optional[str]
    font_size: Optional[float]
    font_weight: Optional[int]
    line_height: Optional[float]
    text_content: Optional[str]
    opacity: float
    children_count: int = field(default=0)


# ==============================
# 提取器
# ==============================

class FigmaExtractor:
    """从 Figma 节点 JSON 中提取设计属性列表"""

    # 递归时跳过这些纯容器层（不输出本身，但继续遍历子节点）
    SKIP_TYPES = {"DOCUMENT", "CANVAS", "PAGE"}

    # 纯装饰性节点类型，不对应任何 DOM 元素，直接过滤
    DECORATIVE_TYPES = {"VECTOR", "LINE", "STAR", "POLYGON", "BOOLEAN_OPERATION"}

    # 自动生成名称的正则：匹配 "Frame 123456" / "Rectangle 165" / "Line 97" 等
    _AUTO_NAME_RE = re.compile(
        r"^(Frame|Line|Rectangle|Vector|Ellipse|Group|Path|Circle|Polygon|Star"
        r"|Arrow|Union|Subtract|Intersect|Exclude|Slice|Container|Boolean"
        r"|Shape|Layer)\s+\d+$",
        re.IGNORECASE,
    )

    # 最小尺寸阈值（px）：低于此值视为装饰性细节，不参与对比
    MIN_SIZE_PX = 8.0

    def extract(self, node_json: dict) -> List[FigmaElement]:
        """
        递归展平节点树，返回全量节点列表（包含噪音节点）。

        适合需要完整结构的场景；若用于与 DOM 对比，
        建议改用 extract_semantic() 以过滤干扰项。

        Args:
            node_json: FigmaClient.get_node_json() 的返回值

        Returns:
            list[FigmaElement]，按 DFS 顺序排列
        """
        result: List[FigmaElement] = []
        self._visit(node_json, result)
        return result

    def extract_semantic(
        self,
        node_json: dict,
        max_depth: int = 4,
    ) -> List[FigmaElement]:
        """
        递归展平节点树，过滤噪音节点，并限制递归深度，
        只返回可与 DOM 元素对应的语义节点。

        过滤规则：
          - 应用 is_semantic() 过滤（装饰类型 / 自动生成名 / 极小节点 / 透明节点）
          - max_depth 限制递归层数：避免把图标内部路径、移动端状态栏等
            深层嵌套的与 Web DOM 无关的节点纳入对比

        推荐在 AutoMapper.generate() 和 ElementCompare.compare() 时使用。

        Args:
            node_json: FigmaClient.get_node_json() 的返回值
            max_depth: 最大递归深度（根节点本身为深度 0），默认 4。
                       设为 None 可禁用深度限制（等同于 extract() 加过滤）

        Returns:
            list[FigmaElement]，仅含语义化节点
        """
        result: List[FigmaElement] = []
        self._visit_semantic(node_json, result, depth=0, max_depth=max_depth)
        return result

    def _visit_semantic(
        self,
        node: dict,
        result: List[FigmaElement],
        depth: int,
        max_depth: Optional[int],
    ) -> None:
        """
        带深度控制的语义节点递归遍历。

        深度限制规则：
          - TEXT 节点不受 max_depth 约束：文本内容是最可靠的匹配依据，
            即使嵌套很深也应保留（避免丢失导航链接、标题等文本）
          - 非 TEXT 容器节点（FRAME / COMPONENT / RECTANGLE 等）超过
            max_depth 层后停止递归，防止图标内部路径等无关细节入库
        """
        node_type = node.get("type", "")

        # TEXT 节点：不受深度限制，直接判断语义性并加入结果
        if node_type == "TEXT":
            elem = self._to_element(node)
            if self.is_semantic(elem):
                result.append(elem)
            return  # TEXT 节点无子节点，直接返回

        # 非 TEXT 容器节点：超过深度限制则停止递归
        if max_depth is not None and depth > max_depth:
            return

        if node_type not in self.SKIP_TYPES:
            elem = self._to_element(node)
            if self.is_semantic(elem):
                result.append(elem)

        for child in node.get("children", []):
            self._visit_semantic(child, result, depth + 1, max_depth)

    # 无语义的极短名称：单字符（任意）或纯数字（任意长度）
    # 例："x"、"3"、"@" 等均视为无意义，过滤掉
    _TRIVIAL_NAME_RE = re.compile(r"^(.$|\d+$)$")

    @classmethod
    def is_semantic(cls, elem: FigmaElement) -> bool:
        """
        判断单个 FigmaElement 是否为有意义的语义节点。

        被过滤掉的情况：
          - 装饰性类型（VECTOR / LINE / STAR 等）
          - 自动生成名称（"Frame 123456" / "Line 97" 等）
          - 无意义极短名称（单字符如 "x"、"3"、"_" 等）
          - 尺寸极小（宽或高 < 8px）
          - 完全透明（opacity == 0）

        Returns:
            True 表示应保留，False 表示应过滤
        """
        # 装饰性节点类型
        if elem.node_type in cls.DECORATIVE_TYPES:
            return False
        # 自动生成名称（无语义）
        if cls._AUTO_NAME_RE.match(elem.name):
            return False
        # 无意义极短名称（单字符如 "x"、纯数字 "3"）
        if cls._TRIVIAL_NAME_RE.match(elem.name.strip()):
            return False
        # 尺寸极小（装饰线、点、图标内部路径等）
        if elem.width < cls.MIN_SIZE_PX or elem.height < cls.MIN_SIZE_PX:
            return False
        # 完全透明（不可见元素）
        if elem.opacity == 0:
            return False
        return True

    # ------------------------------------------------
    # 递归遍历
    # ------------------------------------------------

    def _visit(self, node: dict, result: List[FigmaElement]) -> None:
        """深度优先遍历节点树，跳过 SKIP_TYPES 层但继续递归其子节点"""
        node_type = node.get("type", "")

        if node_type not in self.SKIP_TYPES:
            elem = self._to_element(node)
            result.append(elem)

        for child in node.get("children", []):
            self._visit(child, result)

    # ------------------------------------------------
    # 节点 → FigmaElement
    # ------------------------------------------------

    def _to_element(self, node: dict) -> FigmaElement:
        box = node.get("absoluteBoundingBox") or {}
        style = node.get("style") or {}
        children = node.get("children", [])

        font_family, font_size, font_weight, line_height = self._parse_font(style)

        return FigmaElement(
            id=node.get("id", ""),
            name=node.get("name", ""),
            node_type=node.get("type", ""),
            x=float(box.get("x", 0)),
            y=float(box.get("y", 0)),
            width=float(box.get("width", 0)),
            height=float(box.get("height", 0)),
            fill_color=self._parse_color(node.get("fills", [])),
            stroke_color=self._parse_stroke_color(node.get("strokes", [])),
            border_radius=self._parse_border_radius(node),
            font_family=font_family,
            font_size=font_size,
            font_weight=font_weight,
            line_height=line_height,
            text_content=node.get("characters"),
            opacity=float(node.get("opacity", 1.0)),
            children_count=len(children),
        )

    # ------------------------------------------------
    # 颜色解析
    # ------------------------------------------------

    @staticmethod
    def _parse_color(fills: list) -> Optional[str]:
        """
        从 Figma fills 数组中取第一个 SOLID fill，
        转换为 "#RRGGBB"（忽略 alpha）。
        """
        for fill in fills:
            if fill.get("type") == "SOLID" and fill.get("visible", True):
                c = fill.get("color", {})
                r = round(c.get("r", 0) * 255)
                g = round(c.get("g", 0) * 255)
                b = round(c.get("b", 0) * 255)
                return f"#{r:02X}{g:02X}{b:02X}"
        return None

    @staticmethod
    def _parse_stroke_color(strokes: list) -> Optional[str]:
        """从 strokes 数组取第一个 SOLID 描边颜色"""
        for stroke in strokes:
            if stroke.get("type") == "SOLID" and stroke.get("visible", True):
                c = stroke.get("color", {})
                r = round(c.get("r", 0) * 255)
                g = round(c.get("g", 0) * 255)
                b = round(c.get("b", 0) * 255)
                return f"#{r:02X}{g:02X}{b:02X}"
        return None

    # ------------------------------------------------
    # 圆角解析
    # ------------------------------------------------

    @staticmethod
    def _parse_border_radius(node: dict) -> Optional[float]:
        """
        优先取 cornerRadius（统一圆角），
        否则取 rectangleCornerRadii 四个值的最大值，
        若无圆角信息则返回 None。
        """
        if "cornerRadius" in node:
            return float(node["cornerRadius"])
        radii = node.get("rectangleCornerRadii")
        if radii and any(r > 0 for r in radii):
            return float(max(radii))
        return None

    # ------------------------------------------------
    # 字体解析
    # ------------------------------------------------

    @staticmethod
    def _parse_font(
        style: dict,
    ) -> tuple[Optional[str], Optional[float], Optional[int], Optional[float]]:
        """
        从 Figma style 字段解析字体属性。

        Returns:
            (font_family, font_size, font_weight, line_height_px)
        """
        font_family: Optional[str] = style.get("fontFamily")
        font_size: Optional[float] = (
            float(style["fontSize"]) if "fontSize" in style else None
        )
        font_weight: Optional[int] = (
            int(style["fontWeight"]) if "fontWeight" in style else None
        )

        # lineHeightPx 是像素行高；lineHeightPercent 是百分比（相对字号）
        if "lineHeightPx" in style:
            line_height: Optional[float] = float(style["lineHeightPx"])
        elif "lineHeightPercent" in style and font_size:
            line_height = round(font_size * style["lineHeightPercent"] / 100, 2)
        else:
            line_height = None

        return font_family, font_size, font_weight, line_height


# ==============================
# 本地测试
# ==============================

if __name__ == "__main__":
    import json

    sample = {
        "id": "1:2",
        "name": "HomePage",
        "type": "FRAME",
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 1440, "height": 900},
        "fills": [{"type": "SOLID", "color": {"r": 1, "g": 1, "b": 1, "a": 1}}],
        "strokes": [],
        "children": [
            {
                "id": "1:3",
                "name": "Hero/Title",
                "type": "TEXT",
                "absoluteBoundingBox": {"x": 120, "y": 80, "width": 600, "height": 60},
                "fills": [{"type": "SOLID", "color": {"r": 0.1, "g": 0.1, "b": 0.1, "a": 1}}],
                "strokes": [],
                "characters": "Welcome to the Platform",
                "style": {
                    "fontFamily": "Inter",
                    "fontSize": 48,
                    "fontWeight": 700,
                    "lineHeightPx": 60,
                },
                "children": [],
            }
        ],
    }

    extractor = FigmaExtractor()
    elements = extractor.extract(sample)

    for el in elements:
        print(json.dumps(el.__dict__, indent=2, ensure_ascii=False))
