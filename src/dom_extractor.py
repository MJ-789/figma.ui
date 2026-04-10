"""
src/dom_extractor.py  ── DOM 元素计算样式提取模块
================================================
职责：
    通过 Playwright page.evaluate() 向目标页面注入 JavaScript，
    批量提取指定 CSS 选择器所匹配元素的位置、尺寸及 computed style，
    返回结构化的 DOMElement 列表，供 ElementCompare 与 Figma 属性对比。

核心类 DOMExtractor：
    extract(page, selectors)    ── 主入口，对每个 selector 查询 DOM 并提取属性。
    extract_all_text(page)      ── 提取页面所有可见文本节点，用于文本内容匹配。
    _parse_rgb(css_color)       ── "rgb(255, 255, 255)" / "rgba(...)" → "#RRGGBB"
    _parse_px(value)            ── "16px" → 16.0；无单位数字字符串 → float；失败 → 0.0
    _parse_padding(style)       ── 从 computedStyle 提取四边 padding
    _js_extract_element()       ── 返回用于单个元素属性提取的 JS 表达式（字符串）

DOMElement 字段说明：
    selector         ── 查询时使用的 CSS 选择器
    tag              ── 标签名（小写），如 "button"、"h1"
    text             ── 元素 textContent（去除首尾空白，截断至 200 字符）
    x, y             ── 相对于视口左上角的坐标（px）
    width, height    ── 元素尺寸（px）
    background_color ── 背景色（"#RRGGBB"）
    color            ── 前景/文字颜色（"#RRGGBB"）
    font_family      ── computedStyle.fontFamily（第一个值）
    font_size        ── px 数值
    font_weight      ── 整数（100~900）
    line_height      ── px 数值（若为 "normal" 则估算为 font_size * 1.2）
    border_radius    ── borderTopLeftRadius 的 px 数值（取四角最大值）
    padding          ── {"top": 8, "right": 16, "bottom": 8, "left": 16}
    border           ── computedStyle.border 原始字符串

注意：
    - extract() 对每个 selector 只取第一个匹配元素。
    - 若选择器无匹配，该条目不计入结果列表。
    - 所有颜色统一为 "#RRGGBB"（忽略 alpha），与 FigmaExtractor 保持一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ==============================
# 数据类
# ==============================

@dataclass
class DOMElement:
    selector: str
    tag: str
    text: str
    x: float
    y: float
    width: float
    height: float
    background_color: str
    color: str
    font_family: str
    font_size: float
    font_weight: int
    line_height: float
    border_radius: float
    padding: Dict[str, float] = field(default_factory=dict)
    border: str = ""


# ==============================
# 提取器
# ==============================

class DOMExtractor:
    """从 Playwright 页面提取 DOM 元素的计算样式"""

    # ------------------------------------------------
    # 主入口
    # ------------------------------------------------

    def extract(self, page, selectors: List[str]) -> List[DOMElement]:
        """
        对每个 CSS 选择器查询页面，提取第一个匹配元素的属性。

        Args:
            page:      Playwright Page 对象（已导航到目标 URL）
            selectors: CSS 选择器列表，例如 ["button.btn-primary", "h1.hero"]

        Returns:
            list[DOMElement]，与 selectors 顺序对应（无匹配则跳过）
        """
        results: List[DOMElement] = []

        for selector in selectors:
            try:
                raw = page.evaluate(
                    self._js_extract_by_selector(),
                    selector,
                )
                if raw is None:
                    continue
                elem = self._build(selector, raw)
                results.append(elem)
            except Exception:
                continue

        return results

    def extract_all_text(self, page) -> List[DOMElement]:
        """
        提取页面内所有携带可见文字的叶节点，
        用于 ElementCompare 的文本内容匹配阶段。

        返回 selector 字段为"text::{内容前20字符}" 的虚拟标识。
        """
        try:
            raw_list = page.evaluate(self._js_extract_all_text())
        except Exception:
            return []

        results: List[DOMElement] = []
        for raw in (raw_list or []):
            text = (raw.get("text") or "").strip()
            if not text:
                continue
            fake_selector = f"text::{text[:20]}"
            elem = self._build(fake_selector, raw)
            results.append(elem)

        return results

    # ------------------------------------------------
    # 构建 DOMElement
    # ------------------------------------------------

    def _build(self, selector: str, raw: dict) -> DOMElement:
        font_size = self._parse_px(raw.get("fontSize", "0"))
        raw_lh = raw.get("lineHeight", "")
        if raw_lh in ("normal", "", None):
            line_height = round(font_size * 1.2, 2)
        else:
            line_height = self._parse_px(str(raw_lh))

        return DOMElement(
            selector=selector,
            tag=(raw.get("tag") or "").lower(),
            text=(raw.get("text") or "")[:200],
            x=float(raw.get("x", 0)),
            y=float(raw.get("y", 0)),
            width=float(raw.get("width", 0)),
            height=float(raw.get("height", 0)),
            background_color=self._parse_rgb(raw.get("backgroundColor", "")),
            color=self._parse_rgb(raw.get("color", "")),
            font_family=self._clean_font_family(raw.get("fontFamily", "")),
            font_size=font_size,
            font_weight=self._parse_font_weight(raw.get("fontWeight", "400")),
            line_height=line_height,
            border_radius=self._parse_border_radius_raw(raw),
            padding=self._parse_padding(raw),
            border=raw.get("border", ""),
        )

    # ------------------------------------------------
    # JS 注入脚本
    # ------------------------------------------------

    @staticmethod
    def _js_extract_by_selector() -> str:
        """
        返回一个 JS 表达式（接受 selector 参数），
        对第一个匹配元素提取 rect + computed style。
        """
        return """
(selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;

    const rect = el.getBoundingClientRect();
    const cs = window.getComputedStyle(el);

    return {
        tag:             el.tagName,
        text:            el.textContent,
        x:               rect.x,
        y:               rect.y,
        width:           rect.width,
        height:          rect.height,
        backgroundColor: cs.backgroundColor,
        color:           cs.color,
        fontFamily:      cs.fontFamily,
        fontSize:        cs.fontSize,
        fontWeight:      cs.fontWeight,
        lineHeight:      cs.lineHeight,
        borderTopLeftRadius:     cs.borderTopLeftRadius,
        borderTopRightRadius:    cs.borderTopRightRadius,
        borderBottomLeftRadius:  cs.borderBottomLeftRadius,
        borderBottomRightRadius: cs.borderBottomRightRadius,
        paddingTop:      cs.paddingTop,
        paddingRight:    cs.paddingRight,
        paddingBottom:   cs.paddingBottom,
        paddingLeft:     cs.paddingLeft,
        border:          cs.border,
    };
}
"""

    @staticmethod
    def _js_extract_all_text() -> str:
        """
        返回 JS 表达式，遍历所有含文本的叶节点，
        提取 rect + computed style 列表（最多 200 条）。
        """
        return """
() => {
    const results = [];
    const walker = document.createTreeWalker(
        document.body,
        NodeFilter.SHOW_TEXT,
        null,
        false
    );
    let node;
    while ((node = walker.nextNode()) && results.length < 200) {
        const text = node.textContent.trim();
        if (!text) continue;
        const el = node.parentElement;
        if (!el) continue;

        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;

        const cs = window.getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;

        results.push({
            tag:             el.tagName,
            text:            text,
            x:               rect.x,
            y:               rect.y,
            width:           rect.width,
            height:          rect.height,
            backgroundColor: cs.backgroundColor,
            color:           cs.color,
            fontFamily:      cs.fontFamily,
            fontSize:        cs.fontSize,
            fontWeight:      cs.fontWeight,
            lineHeight:      cs.lineHeight,
            borderTopLeftRadius:     cs.borderTopLeftRadius,
            borderTopRightRadius:    cs.borderTopRightRadius,
            borderBottomLeftRadius:  cs.borderBottomLeftRadius,
            borderBottomRightRadius: cs.borderBottomRightRadius,
            paddingTop:      cs.paddingTop,
            paddingRight:    cs.paddingRight,
            paddingBottom:   cs.paddingBottom,
            paddingLeft:     cs.paddingLeft,
            border:          cs.border,
        });
    }
    return results;
}
"""

    # ------------------------------------------------
    # 颜色解析
    # ------------------------------------------------

    @staticmethod
    def _parse_rgb(css_color: str) -> str:
        """
        把 "rgb(r, g, b)" 或 "rgba(r, g, b, a)" 转成 "#RRGGBB"。
        无法解析时返回空字符串。
        """
        if not css_color:
            return ""
        nums = re.findall(r"[\d.]+", css_color)
        if len(nums) >= 3:
            r, g, b = (int(float(n)) for n in nums[:3])
            return f"#{r:02X}{g:02X}{b:02X}"
        return ""

    # ------------------------------------------------
    # 数值解析
    # ------------------------------------------------

    @staticmethod
    def _parse_px(value: str) -> float:
        """"16px" / "16.5px" / "16" → float；失败 → 0.0"""
        if not value:
            return 0.0
        try:
            return float(re.sub(r"[^\d.]", "", value))
        except ValueError:
            return 0.0

    def _parse_border_radius_raw(self, raw: dict) -> float:
        """取四角 borderRadius 中的最大值（px）"""
        keys = [
            "borderTopLeftRadius", "borderTopRightRadius",
            "borderBottomLeftRadius", "borderBottomRightRadius",
        ]
        vals = [self._parse_px(raw.get(k, "0")) for k in keys]
        return max(vals)

    def _parse_padding(self, raw: dict) -> Dict[str, float]:
        return {
            "top":    self._parse_px(raw.get("paddingTop", "0")),
            "right":  self._parse_px(raw.get("paddingRight", "0")),
            "bottom": self._parse_px(raw.get("paddingBottom", "0")),
            "left":   self._parse_px(raw.get("paddingLeft", "0")),
        }

    @staticmethod
    def _parse_font_weight(value: str) -> int:
        """"600" / "bold" → int"""
        mapping = {"normal": 400, "bold": 700, "lighter": 300, "bolder": 700}
        if value in mapping:
            return mapping[value]
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 400

    @staticmethod
    def _clean_font_family(value: str) -> str:
        """取 computedStyle.fontFamily 的第一个字体名，去掉引号"""
        if not value:
            return ""
        first = value.split(",")[0].strip()
        return first.strip('"\'')


# ==============================
# 本地测试（需要实际浏览器）
# ==============================

if __name__ == "__main__":
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://example.com", wait_until="networkidle")

        extractor = DOMExtractor()
        elements = extractor.extract(page, ["h1", "p", "a"])

        for el in elements:
            print(f"[{el.tag}] {el.text[:30]!r}")
            print(f"  size: {el.width:.0f}x{el.height:.0f}  pos: ({el.x:.0f}, {el.y:.0f})")
            print(f"  color: {el.color}  bg: {el.background_color}")
            print(f"  font: {el.font_family} {el.font_size}px w{el.font_weight}")
            print()

        browser.close()
