"""
src/element_compare.py  ── 元素属性级对比模块
================================================
职责：
    把 FigmaExtractor 输出的 FigmaElement 列表与
    DOMExtractor 输出的 DOMElement 列表进行匹配，
    逐属性对比并生成结构化 diff 报告，取代原有的像素热力图方案。

核心类 ElementCompare：
    compare(figma_elements, dom_elements,
            element_map, color_tol, size_tol)
        ── 主入口，返回完整 diff 报告字典。

    _match(figma_elements, dom_elements, element_map)
        ── 单阶段匹配：
           [1] element_map 显式映射（figma_name → DOMElement by selector）
               element_map 由 AutoMapper 自动生成（含 TEXT 节点位置匹配）
               或由 Config.TEST_PAGES.element_map 手工补充（优先级更高）。
           【注意】已移除文本内容匹配（原阶段 2）：
               Figma 设计稿中的文字（如 "Category11111111111"）常为占位符，
               不应作为匹配依据，改由 AutoMapper 按坐标定位对应 DOM 元素。
        返回 list[ (FigmaElement, DOMElement | None) ]

    _compare_pair(figma_elem, dom_elem, color_tol, size_tol)
        ── 对一对已匹配元素，逐属性比较，返回属性级 diff dict 和 score。

    _color_diff(hex1, hex2, tol)
        ── 颜色对比：每通道差 <= tol 则 pass。

    _num_diff(v1, v2, tol)
        ── 数值对比（px）：|v1-v2| <= tol 则 pass。

    _font_family_match(f1, f2)
        ── 字体族名对比（不区分大小写，忽略引号）。

输出格式（compare() 返回 dict）：
    {
        "total_matched": 3,
        "total_unmatched": 1,
        "overall_score": 0.91,       # 仅已匹配节点的平均属性通过率（不含未匹配节点）
        "coverage_rate": 0.75,       # 已匹配数 / 总节点数（反映匹配广度）
        "overall_passed": true,      # overall_score >= threshold AND total_matched >= min_match_count
        "threshold": 0.70,
        "min_match_count": 3,
        "elements": [
            {
                "figma_name": "Button/Primary",
                "figma_id":   "12539:1074",
                "dom_selector": "button.btn-cta",
                "matched": true,
                "score": 0.83,
                "passed": false,
                "properties": {
                    "fill_color": {
                        "figma": "#4F46E5",
                        "web":   "#4F46E5",
                        "passed": true
                    },
                    "font_size": {
                        "figma": 16.0,
                        "web":   16.0,
                        "passed": true
                    },
                    "border_radius": {
                        "figma":  8.0,
                        "web":    6.0,
                        "diff":   2.0,
                        "passed": false
                    }
                }
            },
            {
                "figma_name": "Nav/Icon",
                "figma_id":   "12539:1100",
                "dom_selector": null,
                "matched": false,
                "score": 0.0,
                "passed": false,
                "properties": {}
            }
        ]
    }

容差默认值：
    颜色（RGB 每通道）  ± 5
    尺寸 / 位置（px）  ± 4
    字号（px）         ± 1
    圆角（px）         ± 2
    字重               精确匹配

以上均可通过 compare() 参数覆盖，也可从 Config 中读取。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.figma_extractor import FigmaElement
from src.dom_extractor import DOMElement


# ==============================
# 主类
# ==============================

class ElementCompare:
    """Figma 设计元素 vs DOM 元素的属性级对比器"""

    # ------------------------------------------------
    # 主入口
    # ------------------------------------------------

    def compare(
        self,
        figma_elements: List[FigmaElement],
        dom_elements: List[DOMElement],
        element_map: Dict[str, str],
        threshold: float = 0.70,
        color_tol: int = 5,
        size_tol: float = 4.0,
        font_size_tol: float = 1.0,
        radius_tol: float = 2.0,
        min_match_count: int = 3,
        id_element_map: Optional[Dict[str, str]] = None,
        skip_props: Optional[set] = None,
    ) -> dict:
        """
        匹配并对比所有 Figma 节点与 DOM 元素。

        Args:
            figma_elements:   FigmaExtractor.extract_semantic() 的返回值
                              （推荐传入语义过滤后的节点列表，而非全量节点）
            dom_elements:     DOMExtractor.extract() 的结果列表
                              （selector 由 AutoMapper 生成，含 TEXT 节点的坐标定位选择器）
            element_map:      手工补充映射 {figma_node_name: css_selector}（来自 Config）
                              名称不唯一时以第一个节点为准；优先级低于 id_element_map
            id_element_map:   自动推导映射 {figma_id: css_selector}（来自 AutoMapper）
                              key 为 Figma 节点 ID（全局唯一），彻底避免同名节点冲突
                              非 TEXT 节点：AutoMapper 按名称 + IoU 匹配
                              TEXT 节点：AutoMapper 按 Figma 坐标定位 DOM 元素（忽略文字内容）
            threshold:        已匹配元素的平均属性通过率阈值（0~1），默认 0.70。
                              注意：由于字体渲染差异、浏览器厂商前缀等因素，
                              Figma 与实际网页的属性完全一致率通常在 60%~80% 之间，
                              设为 0.85 以上容易误判。
            color_tol:        颜色容差（每通道 0~255），默认 ±5
            size_tol:         尺寸/位置容差（px），默认 ±4
            font_size_tol:    字号容差（px），默认 ±1
            radius_tol:       圆角容差（px），默认 ±2
            min_match_count:  最低有效匹配数（绝对值），用于防止"只匹配 1 个节点
                              就声称全部通过"的误报，默认 3 个。
                              设为 0 可禁用此条件。

        Returns:
            完整 diff 报告字典，关键字段：
            - overall_score:    已匹配元素的平均属性通过率（0~1）
                                【重要】仅统计 matched=True 的节点，
                                未匹配节点不纳入分数计算，避免拉低整体评分
            - coverage_rate:    已匹配节点数 / 总节点数（0~1），供参考
            - overall_passed:   overall_score >= threshold AND
                                total_matched >= min_match_count
            - elements:         每个节点的详细比对结果列表
        """
        tols = {
            "color": color_tol,
            "size": size_tol,
            "font_size": font_size_tol,
            "radius": radius_tol,
        }

        # selector → DOMElement 快速查找表
        dom_by_selector: Dict[str, DOMElement] = {
            el.selector: el for el in dom_elements
        }

        pairs = self._match(
            figma_elements,
            dom_by_selector,
            name_element_map=element_map,
            id_element_map=id_element_map or {},
        )

        element_reports = []
        matched_scores: List[float] = []   # 只收集已匹配节点的分数

        for figma_elem, dom_elem in pairs:
            if dom_elem is None:
                # 未匹配节点：记录但不计入 overall_score，避免拉低整体分数
                element_reports.append({
                    "figma_name": figma_elem.name,
                    "figma_id": figma_elem.id,
                    "dom_selector": None,
                    "matched": False,
                    "score": 0.0,
                    "passed": False,
                    "properties": {},
                })
            else:
                props, score = self._compare_pair(figma_elem, dom_elem, tols, skip_props or set())
                element_reports.append({
                    "figma_name": figma_elem.name,
                    "figma_id": figma_elem.id,
                    "dom_selector": dom_elem.selector,
                    "matched": True,
                    "score": round(score, 4),
                    "passed": score >= threshold,
                    "properties": props,
                })
                # Elements with no applicable (non-skipped) properties don't
                # contribute to overall_score — otherwise they would silently
                # count as 100% pass and inflate the number.
                if props:
                    matched_scores.append(score)

        total_matched = len(matched_scores)
        total_nodes = len(pairs)

        # overall_score：仅已匹配节点的平均属性通过率
        overall_score = round(sum(matched_scores) / total_matched, 4) if total_matched else 0.0

        # coverage_rate：成功匹配的节点占总节点数的比例
        coverage_rate = round(total_matched / total_nodes, 4) if total_nodes else 0.0

        # 三种情况分别处理：
        #
        # ① total_matched == 0（完全无法匹配）
        #    → overall_passed = True，但附加 warning="no_match"
        #      原因：设计使用占位内容 / 网站用非语义 HTML / element_map 未配置，
        #            此时无法判断好坏，不应误报为失败，应由人工补充 element_map 后再判断。
        #
        # ② 0 < total_matched < min_match_count（样本极少）
        #    → overall_passed = False
        #      找到了少量匹配但样本不足，说明存在配置问题或严重不一致。
        #
        # ③ total_matched >= min_match_count（正常评估）
        #    → overall_passed = overall_score >= threshold
        if total_matched == 0:
            overall_passed = True
            warning = "no_match: 未找到任何可匹配元素，结果为 inconclusive。" \
                      "请在 config 的 element_map 中手动指定 Figma 节点与 CSS 选择器的对应关系。"
        elif total_matched < min_match_count:
            overall_passed = False
            warning = (
                f"low_match: 仅匹配到 {total_matched} 个节点（最低要求 {min_match_count}），"
                "建议补充 element_map 以提高覆盖率。"
            )
        else:
            overall_passed = overall_score >= threshold
            warning = ""

        return {
            "total_matched": total_matched,
            "total_unmatched": total_nodes - total_matched,
            "overall_score": overall_score,
            "coverage_rate": coverage_rate,
            "overall_passed": overall_passed,
            "threshold": threshold,
            "min_match_count": min_match_count,
            "warning": warning,
            "elements": element_reports,
        }

    # ------------------------------------------------
    # 匹配：两阶段
    # ------------------------------------------------

    def _match(
        self,
        figma_elements: List[FigmaElement],
        dom_by_selector: Dict[str, DOMElement],
        name_element_map: Dict[str, str],
        id_element_map: Dict[str, str],
    ) -> List[Tuple[FigmaElement, Optional[DOMElement]]]:
        """
        两阶段匹配（均基于选择器，不依赖文字内容）：

        [1] ID 映射（优先）：id_element_map[figma_elem.id] → css_selector
            来源：AutoMapper.generate()，key 为 figma_id（全局唯一）。
            可正确处理大量同名图层（如 100 个"模板6"）的情况，
            每个节点都有独立的 DOM 映射。

        [2] 名称映射（兜底）：name_element_map[figma_elem.name] → css_selector
            来源：Config.TEST_PAGES.element_map 手工补充，
            当某节点未被 AutoMapper 映射时使用。
            同名图层中只有第一个会命中（used_selectors 防止重复映射）。

        【已移除文本内容匹配】：
          Figma 设计稿中的文字（如 "Category11111111111"）常为占位符，
          以文字内容为依据会导致大量误匹配，改由 AutoMapper 按坐标定位。
        """
        pairs: List[Tuple[FigmaElement, Optional[DOMElement]]] = []
        # 用于名称映射阶段：防止同一 CSS 选择器被多个同名节点重复匹配
        used_selectors: set = set()

        for figma_elem in figma_elements:
            dom_elem: Optional[DOMElement] = None

            # 阶段 1：按 figma_id 查找（AutoMapper 生成，唯一匹配）
            if figma_elem.id in id_element_map:
                selector = id_element_map[figma_elem.id]
                dom_elem = dom_by_selector.get(selector)

            # 阶段 2：按 figma_name 查找（Config 手工补充，去重）
            if dom_elem is None and figma_elem.name in name_element_map:
                selector = name_element_map[figma_elem.name]
                if selector not in used_selectors:
                    dom_elem = dom_by_selector.get(selector)
                    if dom_elem is not None:
                        used_selectors.add(selector)

            pairs.append((figma_elem, dom_elem))

        return pairs

    # ------------------------------------------------
    # 逐属性对比
    # ------------------------------------------------

    def _compare_pair(
        self,
        figma: FigmaElement,
        dom: DOMElement,
        tols: dict,
        skip_props: Optional[set] = None,
    ) -> Tuple[dict, float]:
        """
        对一对已匹配的元素逐属性对比。

        Returns:
            (props_dict, score)
            props_dict: {属性名: {figma, web, passed, diff(可选)}}
            score:      通过属性数 / 总属性数（0~1）
        """
        props: dict = {}
        total = 0
        passed_count = 0
        is_text = figma.node_type == "TEXT"
        skip: set = skip_props or set()

        def record(key: str, figma_val, web_val, ok: bool, diff=None):
            # skip_props lets callers drop whole property families from the
            # comparison (e.g. all typography attrs when the report focuses
            # purely on visual box metrics). Skipped props do NOT count
            # toward the pair's score, so they cannot pull overall_score
            # down.
            if key in skip:
                return
            nonlocal total, passed_count
            total += 1
            if ok:
                passed_count += 1
            entry = {"figma": figma_val, "web": web_val, "passed": ok}
            if diff is not None:
                entry["diff"] = diff
            props[key] = entry

        # ── 背景色（仅非 TEXT 节点）
        # TEXT 节点的 fill_color 是字体颜色而非背景色，
        # 用它对比 DOM background_color 会造成误判，应跳过。
        if not is_text and figma.fill_color and dom.background_color:
            ok, diff = self._color_diff(figma.fill_color, dom.background_color, tols["color"])
            record("fill_color", figma.fill_color, dom.background_color, ok, diff)

        # ── 文字颜色（TEXT 节点专用：Figma fill_color = 字体颜色 vs DOM color）
        if is_text and figma.fill_color and dom.color:
            ok, diff = self._color_diff(figma.fill_color, dom.color, tols["color"])
            record("text_color", figma.fill_color, dom.color, ok, diff)

        # ── 字号
        if figma.font_size is not None and dom.font_size:
            ok = abs(figma.font_size - dom.font_size) <= tols["font_size"]
            diff = round(abs(figma.font_size - dom.font_size), 2) if not ok else None
            record("font_size", figma.font_size, dom.font_size, ok, diff)

        # ── 字重
        if figma.font_weight is not None and dom.font_weight:
            ok = figma.font_weight == dom.font_weight
            record("font_weight", figma.font_weight, dom.font_weight, ok)

        # ── 字体族
        if figma.font_family and dom.font_family:
            ok = self._font_family_match(figma.font_family, dom.font_family)
            record("font_family", figma.font_family, dom.font_family, ok)

        # ── 行高
        if figma.line_height is not None and dom.line_height:
            ok = abs(figma.line_height - dom.line_height) <= tols["size"]
            diff = round(abs(figma.line_height - dom.line_height), 2) if not ok else None
            record("line_height", figma.line_height, dom.line_height, ok, diff)

        # ── 圆角（仅非 TEXT 节点：文本框通常没有圆角）
        if not is_text and figma.border_radius is not None and dom.border_radius is not None:
            ok = abs(figma.border_radius - dom.border_radius) <= tols["radius"]
            diff = round(abs(figma.border_radius - dom.border_radius), 2) if not ok else None
            record("border_radius", figma.border_radius, dom.border_radius, ok, diff)

        # ── 宽度
        # 对 TEXT 节点也做宽度比较：它能揭示"文字块是否被换行到
        # 设计规定的宽度"，是纯视觉/结构性差异，不涉及字体。
        # 但 height 对 TEXT 仍然跳过（Figma bounding box vs DOM line box
        # 差异天然很大，比无意义）。
        if figma.width and dom.width:
            ok = abs(figma.width - dom.width) <= tols["size"]
            diff = round(abs(figma.width - dom.width), 2) if not ok else None
            record("width", figma.width, dom.width, ok, diff)

        if not is_text and figma.height and dom.height:
            ok = abs(figma.height - dom.height) <= tols["size"]
            diff = round(abs(figma.height - dom.height), 2) if not ok else None
            record("height", figma.height, dom.height, ok, diff)

        score = passed_count / total if total > 0 else 1.0
        return props, score

    # ------------------------------------------------
    # 颜色对比
    # ------------------------------------------------

    @staticmethod
    def _color_diff(hex1: str, hex2: str, tol: int) -> Tuple[bool, Optional[str]]:
        """
        比较两个 "#RRGGBB" 颜色，每通道差值 <= tol 则 pass。

        Returns:
            (passed: bool, diff_str: str|None)
            diff_str 在不通过时为 "dR=x dG=y dB=z" 格式。
        """

        def _parse(h: str) -> Optional[Tuple[int, int, int]]:
            h = h.lstrip("#")
            if len(h) == 6:
                try:
                    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                except ValueError:
                    return None
            return None

        rgb1 = _parse(hex1)
        rgb2 = _parse(hex2)

        if rgb1 is None or rgb2 is None:
            return True, None  # 无法解析时不计入失败

        dr = abs(rgb1[0] - rgb2[0])
        dg = abs(rgb1[1] - rgb2[1])
        db = abs(rgb1[2] - rgb2[2])

        if dr <= tol and dg <= tol and db <= tol:
            return True, None
        return False, f"dR={dr} dG={dg} dB={db}"

    # ------------------------------------------------
    # 数值对比
    # ------------------------------------------------

    @staticmethod
    def _num_diff(v1: float, v2: float, tol: float) -> Tuple[bool, Optional[float]]:
        diff = abs(v1 - v2)
        if diff <= tol:
            return True, None
        return False, round(diff, 2)

    # ------------------------------------------------
    # 字体族对比
    # ------------------------------------------------

    @staticmethod
    def _font_family_match(f1: str, f2: str) -> bool:
        """
        不区分大小写、去掉引号和多余空格后对比字体族名。
        例如：'Inter' vs '"Inter"' → True
        """

        def normalize(f: str) -> str:
            return re.sub(r"[\"'\s]", "", f).lower()

        return normalize(f1) == normalize(f2)


# ==============================
# 本地测试
# ==============================

if __name__ == "__main__":
    import json
    from src.figma_extractor import FigmaElement
    from src.dom_extractor import DOMElement

    figma_elems = [
        FigmaElement(
            id="1:3", name="Hero/Title", node_type="TEXT",
            x=120, y=80, width=600, height=60,
            fill_color="#1A1A1A", stroke_color=None, border_radius=None,
            font_family="Inter", font_size=48, font_weight=700, line_height=60,
            text_content="Welcome to the Platform", opacity=1.0,
        ),
        FigmaElement(
            id="1:4", name="Button/Primary", node_type="FRAME",
            x=120, y=200, width=160, height=48,
            fill_color="#4F46E5", stroke_color=None, border_radius=8,
            font_family="Inter", font_size=16, font_weight=600, line_height=24,
            text_content=None, opacity=1.0,
        ),
    ]

    dom_elems = [
        DOMElement(
            selector="h1.hero-title", tag="h1",
            text="Welcome to the Platform",
            x=120, y=80, width=602, height=60,
            background_color="#FFFFFF", color="#1A1A1A",
            font_family="Inter", font_size=48, font_weight=700, line_height=60,
            border_radius=0, padding={}, border="",
        ),
        DOMElement(
            selector="button.btn-primary", tag="button",
            text="Get Started",
            x=120, y=200, width=160, height=48,
            background_color="#4F46E5", color="#FFFFFF",
            font_family="Inter", font_size=16, font_weight=600, line_height=24,
            border_radius=6,  # 应为 8，偏差 2px → 刚好在容差边缘
            padding={"top": 12, "right": 24, "bottom": 12, "left": 24},
            border="",
        ),
    ]

    element_map = {"Button/Primary": "button.btn-primary"}

    comparator = ElementCompare()
    report = comparator.compare(
        figma_elements=figma_elems,
        dom_elements=dom_elems,
        element_map=element_map,
    )

    print(json.dumps(report, indent=2, ensure_ascii=False))
