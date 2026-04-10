"""
src/auto_mapper.py  ── Figma 节点自动映射模块（v1.2.0）
================================================
职责：
    不依赖手工配置的 element_map，自动从 Figma 节点名称和页面 DOM
    推导出 {figma_layer_name: css_selector} 映射关系，
    供 ElementCompare 使用。

核心类 AutoMapper：
    generate(figma_elements, page, root_frame)
        ── 主入口，对所有语义节点（含 TEXT）尝试自动匹配 DOM 元素。
        ── 返回 dict {figma_name: css_selector}。

    _find_best_selector(figma_elem, page, root_frame)
        ── 对非 TEXT 节点生成候选选择器列表，依次验证存在性，
           并用位置 IoU（交并比）评分选出最优项。

    _find_text_node_by_position(figma_elem, page, root_frame, page_size, attr_name)
        ── 对 TEXT 节点：将 Figma 坐标映射到页面坐标，
           通过 document.elementsFromPoint() 直接定位该位置的 DOM 元素，
           注入唯一 data-* 属性作为稳定 CSS 选择器。
           【重要】匹配依据是坐标位置，而非文字内容，
           因此 Figma 占位文本（如 "Category11111111111"）不影响匹配结果。

    _generate_candidates(figma_elem)
        ── 从层级名称（"Category/Variant"）生成候选 CSS 选择器：
           [1] 语义类型映射（button/input/nav 等）
           [2] 名称转 kebab-case 类名
           [3] aria-label / data-testid / data-component 属性匹配
           [4] 节点类型兜底标签

    _score_candidates(candidates, figma_elem, page, root_frame)
        ── 对每个候选选择器：
           (a) 查询 DOM 中第一个匹配元素的 rect
           (b) 计算归一化坐标系下的位置 IoU
           (c) 综合位置得分 × 0.7 + 存在性得分 × 0.3 排序
        ── 返回 (selector, score) 列表，降序排列

    _iou(r1, r2)
        ── 计算两个归一化矩形 [x1,y1,x2,y2] 的交并比

    _to_kebab(name)
        ── "Button/Primary" / "ButtonPrimary" → "button-primary"

    _parse_name(name)
        ── "Category/Sub/Variant" → (category, sub, variant, full_kebab)

语义映射表 SEMANTIC_HINTS：
    将常见 Figma 层级分类关键词映射到 HTML 标签/角色/类名候选列表，
    可在 SEMANTIC_HINTS 末尾自由扩展。

位置归一化说明：
    - Figma 坐标：以 root_frame.absoluteBoundingBox 为参考系，
      归一化到 [0, 1] 区间。
    - DOM 坐标：以视口宽度 × 页面总高度为参考系，
      getBoundingClientRect().y + window.scrollY 作为绝对 y 坐标，
      归一化到 [0, 1] 区间。
    - 两者在相同视口宽度下（建议 set_viewport_size 与 Figma 帧等宽）
      位置 IoU 能有效过滤错误匹配。

注意：
    - TEXT 节点改由 _find_text_node_by_position() 按坐标定位，
      不再使用文字内容匹配，彻底解决占位符（placeholder）干扰问题。
    - 非 TEXT 节点：IoU 分数为 0 但候选选择器存在于 DOM 时，
      仍会作为低分候选返回，确保即使坐标不对应也不会完全遗漏。
    - 对于得分 < MIN_SCORE 的匹配，不纳入 element_map。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.figma_extractor import FigmaElement


# ==============================
# 语义映射表
# ==============================

SEMANTIC_HINTS: Dict[str, List[str]] = {
    # ── 中文常见设计层级名映射 ─────────────────────────────────────────
    # Figma 设计师常用中文命名，统一映射到对应 HTML 语义标签
    "导航":      ["nav", "header nav", "[role='navigation']"],
    "菜单":      ["nav", "[role='menu']", "ul.menu", "nav ul"],
    "首页":      ["main", "section:first-of-type", "[class*='home']"],
    "头部":      ["header", "[role='banner']"],
    "底部":      ["footer", "[role='contentinfo']"],
    "页脚":      ["footer", "[role='contentinfo']"],
    "按钮":      ["button", "[role='button']", "a.btn"],
    "输入框":    ["input", "textarea", "[contenteditable]"],
    "搜索":      ["input[type='search']", "[role='search'] input"],
    "图片":      ["img", "picture", "figure"],
    "轮播":      ["[class*='carousel']", "[class*='slider']", "[class*='swiper']"],
    "卡片":      ["[class*='card']", "article"],
    "列表":      ["ul", "ol", "[role='list']"],
    "标题":      ["h1", "h2", "h3", "[class*='title']"],
    "文本":      ["p", "span", "[class*='text']"],
    "链接":      ["a[href]", "[role='link']"],
    "图标":      ["svg", "[class*='icon']"],
    "弹窗":      ["[role='dialog']", ".modal", "[aria-modal]"],
    "侧边栏":    ["aside", "[role='complementary']", ".sidebar"],
    "面包屑":    ["[aria-label*='breadcrumb']", "nav ol", ".breadcrumb"],
    "标签":      ["[role='tab']", "[class*='tab']"],
    "内容区":    ["main", "article", "[role='main']"],
    "容器":      ["main", ".container", "[class*='container']"],
    "分类":      ["[class*='category']", "[class*='tag']", "nav ul li"],
    "模板":      ["main", "section"],

    # ── 交互元素 ─────────────────────────────────────────────────────────
    "button":    ["button", "[role='button']", "a.btn", "input[type='submit']", "input[type='button']"],
    "btn":       ["button", "[role='button']", "a.btn"],
    "cta":       ["button", "a.btn", "[class*='cta']"],
    "submit":    ["input[type='submit']", "button[type='submit']"],
    "link":      ["a[href]", "[role='link']"],

    # 表单
    "input":     ["input:not([type='hidden'])", "textarea", "[contenteditable='true']"],
    "search":    ["input[type='search']", "[role='search'] input", "input[placeholder]"],
    "select":    ["select", "[role='listbox']", "[role='combobox']"],
    "checkbox":  ["input[type='checkbox']", "[role='checkbox']"],
    "radio":     ["input[type='radio']", "[role='radio']"],
    "form":      ["form", "[role='form']"],

    # 导航
    "nav":       ["nav", "[role='navigation']", "header nav"],
    "navbar":    ["nav", ".navbar", "header"],
    "menu":      ["[role='menu']", "ul.menu", "nav ul", "[role='menubar']"],
    "breadcrumb":["[aria-label*='breadcrumb']", "nav ol", ".breadcrumb"],
    "tab":       ["[role='tab']", "[role='tablist']", ".tab"],
    "pagination":["[aria-label*='pagination']", ".pagination", "nav[aria-label]"],

    # 布局
    "header":    ["header", "[role='banner']"],
    "footer":    ["footer", "[role='contentinfo']"],
    "sidebar":   ["aside", "[role='complementary']", ".sidebar"],
    "hero":      ["[class*='hero']", "section:first-of-type", ".hero"],
    "section":   ["section", "main", "[role='main']"],
    "card":      [".card", "[class*='card']", "article"],
    "modal":     ["[role='dialog']", ".modal", "[aria-modal='true']"],
    "tooltip":   ["[role='tooltip']", "[title]"],
    "banner":    ["[class*='banner']", ".banner", "[role='banner']"],
    "container": ["main", ".container", "[class*='container']"],

    # 媒体
    "image":     ["img", "picture", "[role='img']", "figure"],
    "img":       ["img", "picture"],
    "icon":      ["svg", "[class*='icon']", "i[class]"],
    "logo":      [".logo", "[class*='logo']", "header img", "a[href='/'] img"],
    "avatar":    ["[class*='avatar']", "img[alt*='avatar']", ".profile-pic"],
    "video":     ["video", "[class*='video']", "iframe"],

    # 문자
    "title":     ["h1", "h2", "h3", "[class*='title']", "[class*='heading']"],
    "heading":   ["h1", "h2", "h3", "h4"],
    "label":     ["label", "[class*='label']", "span"],
    "text":      ["p", "span", "[class*='text']", "[class*='description']"],
    "body":      ["p", "main", "article"],
    "caption":   ["figcaption", "[class*='caption']", "small"],
    "tag":       ["[class*='tag']", "[class*='badge']", "span.tag"],
    "badge":     ["[class*='badge']", ".badge"],
    "chip":      ["[class*='chip']", "[class*='tag']"],

    # 数据展示
    "table":     ["table", "[role='table']", "[class*='table']"],
    "list":      ["ul", "ol", "[role='list']"],
    "item":      ["li", "[role='listitem']", ".item"],
    "row":       ["tr", "[role='row']", ".row"],
    "column":    ["td", "th", "[role='columnheader']"],

    # 状态/反馈
    "alert":     ["[role='alert']", ".alert", "[class*='alert']"],
    "toast":     ["[role='status']", "[class*='toast']", "[class*='snackbar']"],
    "progress":  ["[role='progressbar']", "progress", "[class*='progress']"],
    "spinner":   ["[class*='spinner']", "[class*='loader']", "[class*='loading']"],
    "skeleton":  ["[class*='skeleton']", "[class*='placeholder']"],

    # 特殊
    "dropdown":  ["[role='listbox']", "[class*='dropdown']", "details"],
    "accordion": ["details", "[class*='accordion']", "[class*='collapse']"],
    "stepper":   ["[class*='stepper']", "[class*='steps']", "ol"],
}


# ==============================
# 主类
# ==============================

class AutoMapper:
    """
    自动从 Figma 节点名称 + 页面 DOM 推导出 element_map，无需手工配置选择器。

    匹配原理：
      非 TEXT 节点（FRAME / COMPONENT / RECTANGLE 等）：
        1. 按层级名（"Category/Sub"）生成候选 CSS 选择器列表
        2. 对每个候选选择器查询 DOM，获取元素的 getBoundingClientRect
        3. 将 Figma 坐标与 DOM 坐标归一化后计算 IoU（交并比）
        4. 综合 IoU 得分和语义优先级排序，取最高分候选作为最终映射

      TEXT 节点：
        1. 将 Figma TEXT 节点的中心坐标转换为页面坐标
        2. 调用 document.elementsFromPoint(x, y) 找到该位置最顶层的可见 DOM 元素
        3. 向该元素注入唯一 data-* 属性，生成稳定 CSS 选择器
        【重要】TEXT 节点匹配依据是「坐标位置」而非「文字内容」，
        Figma 中的占位文本（如 "Category11111111111"）完全不影响匹配结果。

    注意事项：
      - 推荐传入 FigmaExtractor.extract_semantic() 的结果，而非全量节点
      - 同一 CSS 选择器只被分配给一个 Figma 节点（IoU 得分最高者优先）
      - IoU = 0 且无语义类型匹配时不纳入 element_map（避免泛化误匹配）
    """

    # IoU 综合评分最低阈值：低于此值的匹配不纳入 element_map
    # 0.3 表示要求 DOM 元素与 Figma 节点有至少 30% 的位置重叠（经验值）
    MIN_SCORE = 0.3

    # 泛化兜底选择器黑名单：这些选择器匹配范围太宽，禁止作为最终映射结果
    # 它们可以在候选列表中出现但不作为最终答案，防止整个 <div> 或 <section> 误匹配
    GENERIC_SELECTORS = {
        "div", "section", "article", "main", "span", "p",
        "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    }

    def generate(
        self,
        figma_elements: List[FigmaElement],
        page,
        root_frame: Optional[FigmaElement] = None,
    ) -> Dict[str, str]:
        """
        自动生成 {figma_id: css_selector} 映射。

        【重要】返回的 key 是 Figma 节点的唯一 ID（如 "12539:1074"），而非图层名称。
        原因：Figma 设计稿中同名图层大量存在（如 "模板6" 可能出现 100 次），
        若以名称为 key，同名节点会互相覆盖，导致大量错误匹配。
        以 figma_id 为 key 确保每个节点都有独立的 DOM 对应关系。

        Args:
            figma_elements: 推荐使用 FigmaExtractor.extract_semantic() 的结果，
                            传入全量节点也可以，但会产生更多噪音尝试
            page:           Playwright Page（已导航到目标 URL，viewport 已设置好）
            root_frame:     根帧节点（用于坐标归一化），None 时自动取第一个 FRAME 节点

        Returns:
            {figma_id: css_selector} 映射字典：
            - TEXT 节点：坐标位置命中的 DOM 元素，选择器为注入的唯一 data-figma-t-N 属性
            - 非 TEXT 节点：IoU 得分 > MIN_SCORE 的可信映射（非泛化选择器）
        """
        if root_frame is None:
            root_frame = next(
                (e for e in figma_elements if e.node_type in ("FRAME", "COMPONENT")),
                None,
            )

        # 获取页面尺寸，用于归一化 DOM 坐标（含滚动高度）
        page_size = self._get_page_size(page)

        # key = figma_id（全局唯一），value = css_selector
        id_element_map: Dict[str, str] = {}
        seen_selectors: set = set()  # 防止同一 CSS 选择器被多个 Figma 节点共用
        text_idx = 0                 # TEXT 节点注入的 data-figma-t-N 属性编号，全局唯一

        for elem in figma_elements:

            # ── TEXT 节点：按坐标位置定位 DOM 元素，而非文本内容匹配
            # 完全忽略 Figma 设计稿中的占位文字（如 "Category11111111111"），
            # 以节点中心坐标找到页面上对应位置的 DOM 元素，对比其 CSS 样式属性。
            if elem.node_type == "TEXT":
                attr_name = f"data-figma-t-{text_idx}"
                text_idx += 1
                selector, score = self._find_text_node_by_position(
                    elem, page, root_frame, page_size, attr_name
                )
                if selector and selector not in seen_selectors:
                    id_element_map[elem.id] = selector   # key = figma_id，保证唯一性
                    seen_selectors.add(selector)
                continue

            # ── 跳过装饰性类型（图标路径、分割线等，无对应 DOM 元素）
            if elem.node_type in {"VECTOR", "LINE", "STAR", "POLYGON", "BOOLEAN_OPERATION"}:
                continue
            # ── 跳过极小节点（装饰细节，宽/高 < 8px）
            if elem.width < 8 or elem.height < 8:
                continue

            # ── 非 TEXT 节点：候选选择器 + IoU 评分
            selector, score = self._find_best_selector(
                elem, page, root_frame, page_size
            )

            if (
                selector
                and score > self.MIN_SCORE       # 严格大于：IoU=0 时 score==MIN_SCORE，不通过
                and selector not in seen_selectors
                and selector not in self.GENERIC_SELECTORS  # 过滤泛化选择器
            ):
                id_element_map[elem.id] = selector   # key = figma_id
                seen_selectors.add(selector)

        return id_element_map

    # ------------------------------------------------
    # 单节点匹配
    # ------------------------------------------------

    def _find_best_selector(
        self,
        figma_elem: FigmaElement,
        page,
        root_frame: Optional[FigmaElement],
        page_size: Tuple[float, float],
    ) -> Tuple[Optional[str], float]:
        """
        对单个 Figma 节点找最佳匹配选择器。

        Returns:
            (selector, score) 或 (None, 0.0)
        """
        candidates = self._generate_candidates(figma_elem)
        if not candidates:
            return None, 0.0

        scored = self._score_candidates(
            candidates, figma_elem, page, root_frame, page_size
        )

        if not scored:
            return None, 0.0

        best_selector, best_score = scored[0]
        return best_selector, best_score

    # ------------------------------------------------
    # TEXT 节点：坐标位置定位
    # ------------------------------------------------

    def _find_text_node_by_position(
        self,
        figma_elem: FigmaElement,
        page,
        root_frame: Optional[FigmaElement],
        page_size: Tuple[float, float],
        attr_name: str,
    ) -> Tuple[Optional[str], float]:
        """
        按 Figma TEXT 节点的中心坐标，定位页面中对应位置的 DOM 元素。

        流程：
          1. 将 Figma 绝对坐标转换为相对于根帧的页面坐标
          2. 滚动到目标区域，使其进入视口
          3. 调用 document.elementsFromPoint(x, y)，取最顶层可见元素
          4. 向该元素注入唯一 data-* 属性（attr_name），作为后续 CSS 选择器

        注意：匹配依据是「坐标位置」，与 Figma 节点的 characters（文字内容）无关，
              因此占位文本不会干扰匹配。

        Args:
            figma_elem:  Figma TEXT 节点
            page:        Playwright Page
            root_frame:  根帧节点（坐标归一化参考）
            page_size:   (page_width, page_height) 用于 IoU 计算
            attr_name:   注入 DOM 的唯一属性名，如 "data-figma-t-0"

        Returns:
            (selector, score) 或 (None, 0.0)
            selector 形如 '[data-figma-t-0]'
        """
        # Figma 绝对坐标 → 页面坐标（相对于根帧左上角）
        if root_frame:
            page_x = figma_elem.x - root_frame.x
            page_y = figma_elem.y - root_frame.y
        else:
            page_x = figma_elem.x
            page_y = figma_elem.y

        # 使用 TEXT 节点的中心点，提高命中精度
        center_x = page_x + figma_elem.width / 2
        center_y = page_y + figma_elem.height / 2

        try:
            raw = page.evaluate(
                """
([cx, cy, attrName, figmaH]) => {
    // 滚动到目标区域（垂直居中），使元素进入视口
    window.scrollTo(0, Math.max(0, cy - window.innerHeight / 2));

    // 将页面绝对坐标换算为当前视口坐标
    const viewportY = cy - window.pageYOffset;

    // elementsFromPoint 从最具体（最深）到最宽泛依次返回
    const elements = document.elementsFromPoint(cx, viewportY);

    // 语义文字标签集合：优先选取这些标签，因为它们自身携带文字样式
    // （而非从父容器继承的默认字体/颜色）
    const TEXT_TAGS = new Set([
        'p', 'span', 'a', 'label', 'button',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'li', 'td', 'th', 'dt', 'dd',
        'strong', 'em', 'b', 'i', 'small', 'time',
        'figcaption', 'caption', 'blockquote', 'q', 'cite', 'address'
    ]);

    // 高度比例容差：DOM 元素高度与 Figma TEXT 节点高度的最大倍数差
    // 超出此范围说明找到的是容器元素（如整个 section），而非对应文字元素
    const MAX_H_RATIO = 4.0;
    const MIN_H_RATIO = 0.25;  // = 1/4

    function isVisible(el, cs) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 4 || rect.height < 4) return false;
        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
        return true;
    }

    function hasDirectText(el) {
        // 元素是否直接包含非空文本节点（而非只有子元素）
        return [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 0);
    }

    function heightOk(rect) {
        // 检查 DOM 元素高度是否在 Figma TEXT 节点高度的合理范围内
        if (figmaH <= 0) return true;
        const ratio = rect.height / figmaH;
        return ratio <= MAX_H_RATIO && ratio >= MIN_H_RATIO;
    }

    // 第一轮：优先选择语义文字标签 或 直接含文本的元素，同时验证高度比例
    for (const el of elements) {
        if (el === document.body || el === document.documentElement) continue;
        const cs = window.getComputedStyle(el);
        if (!isVisible(el, cs)) continue;
        const rect = el.getBoundingClientRect();
        if (!heightOk(rect)) continue;  // 高度差异过大，可能是错误容器
        const tag = el.tagName.toLowerCase();
        if (TEXT_TAGS.has(tag) || hasDirectText(el)) {
            el.setAttribute(attrName, '1');
            return {
                selector: '[' + attrName + ']',
                x: rect.left,
                y: rect.top + window.pageYOffset,
                w: rect.width,
                h: rect.height
            };
        }
    }

    // 第二轮：放开文字标签限制，仍保留高度比例检验
    for (const el of elements) {
        if (el === document.body || el === document.documentElement) continue;
        const cs = window.getComputedStyle(el);
        if (!isVisible(el, cs)) continue;
        const rect = el.getBoundingClientRect();
        if (!heightOk(rect)) continue;
        el.setAttribute(attrName, '1');
        return {
            selector: '[' + attrName + ']',
            x: rect.left,
            y: rect.top + window.pageYOffset,
            w: rect.width,
            h: rect.height
        };
    }

    return null;
}
""",
                [center_x, center_y, attr_name, figma_elem.height],
            )
        except Exception:
            return None, 0.0

        if raw is None:
            return None, 0.0

        # 计算 IoU 作为置信度参考（不用于过滤，只供报告参考）
        page_w, page_h = page_size
        figma_norm = self._normalize_figma_rect(figma_elem, root_frame)
        if page_w > 0 and page_h > 0:
            dom_norm = [
                raw["x"] / page_w,
                raw["y"] / page_h,
                (raw["x"] + raw["w"]) / page_w,
                (raw["y"] + raw["h"]) / page_h,
            ]
        else:
            dom_norm = [0, 0, 1, 1]

        iou = self._iou(figma_norm, dom_norm)
        # 基础分 0.3（元素存在即得）+ IoU 贡献 0.7
        score = round(0.3 + iou * 0.7, 4)
        return raw["selector"], score

    # ------------------------------------------------
    # 候选生成
    # ------------------------------------------------

    def _generate_candidates(self, figma_elem: FigmaElement) -> List[str]:
        """
        从 Figma 节点名生成候选 CSS 选择器列表，按可靠性从高到低排列。
        """
        name = figma_elem.name
        category, sub, variant, full_kebab = self._parse_name(name)
        candidates: List[str] = []

        # ── 1. 语义类型映射（最可靠）
        for keyword in [category, sub, variant]:
            if keyword and keyword in SEMANTIC_HINTS:
                candidates.extend(SEMANTIC_HINTS[keyword])

        # ── 2. aria-label / data-testid / data-component 属性匹配
        for part in [full_kebab, category, sub]:
            if part:
                candidates += [
                    f"[aria-label*='{part}']",
                    f"[data-testid*='{part}']",
                    f"[data-component*='{part}']",
                    f"[data-cy*='{part}']",
                ]

        # ── 3. 类名推导
        if full_kebab:
            candidates += [
                f".{full_kebab}",
                f"[class*='{full_kebab}']",
            ]
        if category and sub:
            cat_sub = f"{category}-{sub}"
            candidates += [
                f".{cat_sub}",
                f"[class*='{cat_sub}']",
            ]
        if sub:
            candidates += [
                f".{sub}",
                f"[class*='{sub}']",
            ]

        # ── 4. 节点类型兜底
        type_fallbacks = {
            "FRAME":     ["div", "section", "article"],
            "COMPONENT": ["div", "section"],
            "INSTANCE":  ["div"],
            "RECTANGLE": ["div", "section"],
            "VECTOR":    ["svg", "img"],
            "ELLIPSE":   ["div", "svg"],
            "GROUP":     ["div", "span"],
        }
        candidates.extend(type_fallbacks.get(figma_elem.node_type, []))

        # 去重，保留顺序
        seen: set = set()
        deduped: List[str] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                deduped.append(c)

        return deduped

    # ------------------------------------------------
    # 候选评分
    # ------------------------------------------------

    def _score_candidates(
        self,
        candidates: List[str],
        figma_elem: FigmaElement,
        page,
        root_frame: Optional[FigmaElement],
        page_size: Tuple[float, float],
    ) -> List[Tuple[str, float]]:
        """
        对每个候选选择器：
        - 查 DOM 中第一个匹配元素的 rect（归一化坐标）
        - 计算与 Figma 元素位置的 IoU
        - 综合评分 = IoU × 0.7 + 存在性 × 0.3

        Returns:
            [(selector, score), ...] 降序排列
        """
        figma_norm = self._normalize_figma_rect(figma_elem, root_frame)
        page_w, page_h = page_size

        scored: List[Tuple[str, float]] = []

        for selector in candidates:
            try:
                raw = page.evaluate(
                    """
(selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const scrollY = window.pageYOffset || document.documentElement.scrollTop;
    return {
        x: r.left,
        y: r.top + scrollY,
        w: r.width,
        h: r.height
    };
}
""",
                    selector,
                )
            except Exception:
                continue

            if raw is None:
                continue

            # 归一化 DOM 坐标
            if page_w > 0 and page_h > 0:
                dom_norm = [
                    raw["x"] / page_w,
                    raw["y"] / page_h,
                    (raw["x"] + raw["w"]) / page_w,
                    (raw["y"] + raw["h"]) / page_h,
                ]
            else:
                dom_norm = [0, 0, 1, 1]

            iou = self._iou(figma_norm, dom_norm)
            # 存在即得 0.3 基础分，IoU 贡献 0.7
            score = iou * 0.7 + 0.3
            scored.append((selector, round(score, 4)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    # ------------------------------------------------
    # 位置归一化 & IoU
    # ------------------------------------------------

    def _normalize_figma_rect(
        self,
        elem: FigmaElement,
        root: Optional[FigmaElement],
    ) -> List[float]:
        """
        把 Figma 绝对坐标归一化到 [0, 1] 区间（相对于根帧）。
        Returns: [x1_norm, y1_norm, x2_norm, y2_norm]
        """
        if root and root.width > 0 and root.height > 0:
            rx, ry, rw, rh = root.x, root.y, root.width, root.height
        else:
            rx, ry, rw, rh = 0, 0, 1440, 900

        x1 = (elem.x - rx) / rw
        y1 = (elem.y - ry) / rh
        x2 = (elem.x + elem.width - rx) / rw
        y2 = (elem.y + elem.height - ry) / rh

        return [x1, y1, x2, y2]

    @staticmethod
    def _iou(r1: List[float], r2: List[float]) -> float:
        """
        计算两个归一化矩形的交并比（Intersection over Union）。
        r1, r2: [x1, y1, x2, y2]
        """
        ix1 = max(r1[0], r2[0])
        iy1 = max(r1[1], r2[1])
        ix2 = min(r1[2], r2[2])
        iy2 = min(r1[3], r2[3])

        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0

        inter = (ix2 - ix1) * (iy2 - iy1)
        area1 = max(0, r1[2] - r1[0]) * max(0, r1[3] - r1[1])
        area2 = max(0, r2[2] - r2[0]) * max(0, r2[3] - r2[1])
        union = area1 + area2 - inter

        if union <= 0:
            return 0.0
        return round(inter / union, 4)

    # ------------------------------------------------
    # 页面尺寸
    # ------------------------------------------------

    @staticmethod
    def _get_page_size(page) -> Tuple[float, float]:
        """获取当前页面的视口宽度和总高度（含 scroll）"""
        try:
            size = page.evaluate("""
() => ({
    w: window.innerWidth || document.documentElement.clientWidth,
    h: Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight,
        document.body.offsetHeight,
        document.documentElement.offsetHeight
    )
})
""")
            return float(size["w"]), float(size["h"])
        except Exception:
            return 1440.0, 900.0

    # ------------------------------------------------
    # 名称解析 & 格式转换
    # ------------------------------------------------

    @staticmethod
    def _parse_name(name: str):
        """
        解析 "Category/Sub/Variant" 格式的 Figma 层级名。

        Returns:
            (category, sub, variant, full_kebab)
            均为小写 kebab-case，缺少的部分为空字符串
        """
        parts = [p.strip() for p in name.split("/")]
        category = AutoMapper._to_kebab(parts[0]) if len(parts) > 0 else ""
        sub = AutoMapper._to_kebab(parts[1]) if len(parts) > 1 else ""
        variant = AutoMapper._to_kebab(parts[2]) if len(parts) > 2 else ""
        full_kebab = AutoMapper._to_kebab(name.replace("/", "-"))
        return category, sub, variant, full_kebab

    @staticmethod
    def _to_kebab(name: str) -> str:
        """
        把各种命名风格统一转成 kebab-case 小写。
        "ButtonPrimary" / "Button Primary" / "Button/Primary" → "button-primary"
        """
        # CamelCase → 插入连字符
        name = re.sub(r"([a-z])([A-Z])", r"\1-\2", name)
        # 非字母数字 → 连字符
        name = re.sub(r"[^a-zA-Z0-9]+", "-", name)
        return name.strip("-").lower()


# ==============================
# 本地测试（需要安装 Playwright）
# ==============================

if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    from src.figma_extractor import FigmaElement

    sample_elements = [
        FigmaElement(
            id="1:2", name="Nav/Header", node_type="FRAME",
            x=0, y=0, width=1440, height=80,
            fill_color="#FFFFFF", stroke_color=None, border_radius=None,
            font_family=None, font_size=None, font_weight=None, line_height=None,
            text_content=None, opacity=1.0,
        ),
        FigmaElement(
            id="1:3", name="Button/CTA", node_type="FRAME",
            x=600, y=400, width=200, height=56,
            fill_color="#4F46E5", stroke_color=None, border_radius=8,
            font_family="Inter", font_size=16, font_weight=600, line_height=24,
            text_content=None, opacity=1.0,
        ),
    ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto("https://example.com", wait_until="networkidle")

        mapper = AutoMapper()
        result = mapper.generate(sample_elements, page)
        print("自动映射结果:", result)

        browser.close()
