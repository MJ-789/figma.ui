"""
src/ai_analyzer.py  ── AI 视觉分析模块
=====================================================
优先使用智谱 AI（GLM-4V-Flash，国内直连免费），
回退到 Gemini（需海外网络）。

配置（.env）：
    ZHIPU_API_KEY=xxx.xxx          ← 优先使用
    GEMINI_API_KEY=AIzaSy...       ← 备用

使用：
    from src.ai_analyzer import AIAnalyzer
    analyzer = AIAnalyzer()
    result = analyzer.compare_page("Home", figma_path, web_path)
"""

from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.config import Config


# ── 空结果模板 ─────────────────────────────────────────────────────────────
_EMPTY: Dict[str, Any] = {
    "summary": "",
    "issues": [],
    "suggestions": [],
    "raw": "",
    "error": None,
}

# ── 提示词（中英双语，GLM/Gemini 均适用）─────────────────────────────────
_PROMPT = """\
你是一位专业的 UI 还原度审查员。
第一张图是 **Figma 设计稿截图**，第二张图是**真实网站截图**。

请对比两张图，重点关注（忽略文字内容差异）：
1. 整体布局结构（位置、对齐、间距）
2. 组件尺寸（宽高比例）
3. 颜色与视觉风格
4. 图片/图标是否正确显示
5. 卡片/列表数量与排列

按以下格式输出（中文）：

## 总结
（一句话概括整体还原度）

## 差异问题
- [P1] （严重，影响视觉一致性）
- [P2] （中等，轻微偏差）
- [P3] （轻微，几乎不影响）

## 修复建议
- （开发可直接执行的 CSS 修复方向）

注意：每类最多 5 条；完全一致时写"无明显差异"。
"""


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def _parse(text: str) -> Dict[str, Any]:
    r = dict(_EMPTY)
    r["raw"] = text
    m = re.search(r"##\s*总结\s*\n(.+?)(?=\n##|\Z)", text, re.S)
    if m:
        r["summary"] = m.group(1).strip()
    m = re.search(r"##\s*差异问题\s*\n(.+?)(?=\n##|\Z)", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip().lstrip("-•* ")
            if not line:
                continue
            level = "P2"
            lm = re.match(r"\[(P\d)\]\s*", line)
            if lm:
                level = lm.group(1)
                line = line[lm.end():]
            r["issues"].append({"level": level, "desc": line})
    m = re.search(r"##\s*修复建议\s*\n(.+?)(?=\n##|\Z)", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            line = line.strip().lstrip("-•* ")
            if line:
                r["suggestions"].append(line)
    return r


# ══════════════════════════════════════════════════════════════════════════════
# 后端：智谱 AI  (GLM-4V-Flash)
# ══════════════════════════════════════════════════════════════════════════════
class _ZhipuBackend:
    """智谱 AI GLM-4V 后端，使用 OpenAI 兼容接口。"""

    BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
    MODEL = "glm-4v-flash"

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._client: Any = None
        self._ok = False
        self._init()

    def _init(self) -> None:
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._key, base_url=self.BASE_URL)
            self._ok = True
        except Exception as e:
            print(f"[AI] 智谱初始化失败: {e}")

    @property
    def ok(self) -> bool:
        return self._ok

    def call(self, figma_path: Path, web_path: Path) -> str:
        for attempt in range(1, 4):
            try:
                resp = self._client.chat.completions.create(
                    model=self.MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": _PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{_b64(figma_path)}"
                                    },
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{_b64(web_path)}"
                                    },
                                },
                            ],
                        }
                    ],
                    max_tokens=1024,
                    temperature=0.2,
                )
                return resp.choices[0].message.content or ""
            except Exception as e:
                err = str(e)
                if "429" in err and attempt < 3:
                    wait = 30 * attempt
                    print(f"[AI] 速率限制，{wait}s 后重试 ({attempt}/3)...")
                    time.sleep(wait)
                    continue
                raise
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# 后端：Gemini (备用)
# ══════════════════════════════════════════════════════════════════════════════
class _GeminiBackend:
    MODEL = "gemini-2.0-flash-lite"

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._client: Any = None
        self._ok = False
        self._init()

    def _init(self) -> None:
        try:
            from google import genai
            self._client = genai.Client(api_key=self._key)
            self._ok = True
        except Exception as e:
            print(f"[AI] Gemini 初始化失败: {e}")

    @property
    def ok(self) -> bool:
        return self._ok

    def call(self, figma_path: Path, web_path: Path) -> str:
        from google import genai
        from google.genai import types
        for attempt in range(1, 4):
            try:
                resp = self._client.models.generate_content(
                    model=self.MODEL,
                    contents=[
                        types.Part.from_text(text=_PROMPT),
                        types.Part.from_bytes(
                            data=base64.b64decode(_b64(figma_path)),
                            mime_type="image/png",
                        ),
                        types.Part.from_bytes(
                            data=base64.b64decode(_b64(web_path)),
                            mime_type="image/png",
                        ),
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.2, max_output_tokens=1024
                    ),
                )
                return resp.text or ""
            except Exception as e:
                err = str(e)
                m = re.search(r"retryDelay['\"]:\s*['\"](\d+)s", err)
                wait = int(m.group(1)) + 5 if m else 65
                if "429" in err and attempt < 3:
                    print(f"[AI] Gemini 速率限制，{wait}s 后重试 ({attempt}/3)...")
                    time.sleep(wait)
                    continue
                raise
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# 公开类
# ══════════════════════════════════════════════════════════════════════════════
class AIAnalyzer:
    """自动选择可用后端（智谱优先，Gemini 备用）。"""

    def __init__(self) -> None:
        self._backend: Any = None
        self._backend_name = ""
        self._init_backend()

    def _init_backend(self) -> None:
        zhipu_key = Config.ZHIPU_API_KEY
        gemini_key = Config.GEMINI_API_KEY

        if zhipu_key:
            b = _ZhipuBackend(zhipu_key)
            if b.ok:
                self._backend = b
                self._backend_name = "智谱 GLM-4V-Flash"
                print(f"[AI] 使用后端: {self._backend_name}")
                return

        if gemini_key:
            b = _GeminiBackend(gemini_key)
            if b.ok:
                self._backend = b
                self._backend_name = "Gemini"
                print(f"[AI] 使用后端: {self._backend_name}")
                return

        print("[AI] 未配置可用 AI Key，跳过 AI 分析")

    @property
    def enabled(self) -> bool:
        return self._backend is not None

    @property
    def backend_name(self) -> str:
        return self._backend_name

    def compare_page(
        self,
        label: str,
        figma_path: Path,
        web_path: Path,
    ) -> Dict[str, Any]:
        result = dict(_EMPTY)
        if not self.enabled:
            result["error"] = "未配置 AI Key"
            return result
        if not figma_path.exists() or not web_path.exists():
            result["error"] = f"截图不存在: {figma_path.name} / {web_path.name}"
            return result
        try:
            text = self._backend.call(figma_path, web_path)
            parsed = _parse(text)
            parsed["page_label"] = label
            print(f"[AI] {label} 分析完成，发现 {len(parsed['issues'])} 个差异")
            return parsed
        except Exception as e:
            print(f"[AI] {label} 失败: {e}")
            result["error"] = str(e)[:300]
            return result

    def analyze_pages(
        self,
        pages: List[Dict[str, Any]],
        report_dir: Path,
        inter_page_delay: float = 5.0,
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        for idx, page in enumerate(pages):
            key = page.get("key", "")
            label = page.get("label", key)
            figma_path = report_dir / f"{key}_full_figma.png"
            web_path = report_dir / f"{key}_full_web.png"
            print(f"[AI] 分析 {label} ...")
            results[key] = self.compare_page(label, figma_path, web_path)
            if idx < len(pages) - 1 and not results[key].get("error"):
                time.sleep(inter_page_delay)
        return results


# ══════════════════════════════════════════════════════════════════════════════
# HTML 渲染
# ══════════════════════════════════════════════════════════════════════════════
def render_ai_section_html(
    ai_results: Dict[str, Dict[str, Any]],
    backend_name: str = "AI",
) -> str:
    if not ai_results:
        return ""

    level_color = {"P1": "#e53e3e", "P2": "#dd6b20", "P3": "#718096"}
    level_bg    = {"P1": "#fff5f5", "P2": "#fffaf0", "P3": "#f7fafc"}

    cards = []
    for key, r in ai_results.items():
        label = r.get("page_label") or key
        error = r.get("error")

        if error:
            cards.append(f"""
            <div class="ai-card">
              <h4>{label}</h4>
              <p class="ai-error">AI 分析跳过：{error}</p>
            </div>""")
            continue

        summary     = r.get("summary", "")
        issues      = r.get("issues", [])
        suggestions = r.get("suggestions", [])

        issue_html = "".join(
            f'<li style="background:{level_bg.get(i["level"],"#f7fafc")};'
            f'border-left:3px solid {level_color.get(i["level"],"#718096")};'
            f'padding:4px 8px;margin:4px 0;border-radius:3px;">'
            f'<b style="color:{level_color.get(i["level"],"#718096")};">[{i["level"]}]</b> {i["desc"]}</li>'
            for i in issues
        ) or "<li>无明显差异</li>"

        sug_html = "".join(f"<li>{s}</li>" for s in suggestions) or "<li>暂无建议</li>"

        cards.append(f"""
            <div class="ai-card">
              <h4>{label} — AI 分析</h4>
              <p class="ai-summary">&#128203; {summary}</p>
              <b>差异问题：</b>
              <ul class="ai-issues">{issue_html}</ul>
              <b>修复建议：</b>
              <ul class="ai-suggestions">{sug_html}</ul>
            </div>""")

    return f"""
<section class="module" id="ai-analysis">
  <h2>&#129302; AI 视觉分析（{backend_name}）</h2>
  <p class="module-desc">对设计稿与网站截图进行视觉语义对比，重点分析布局、尺寸、颜色差异，忽略动态文字内容。</p>
  <div class="ai-cards">{"".join(cards)}</div>
</section>
<style>
.ai-cards{{display:flex;flex-wrap:wrap;gap:16px;margin-top:12px}}
.ai-card{{flex:1 1 300px;border:1px solid #e2e8f0;border-radius:8px;padding:16px;background:#fff}}
.ai-card h4{{margin:0 0 8px;color:#2d3748;font-size:1rem}}
.ai-summary{{background:#ebf8ff;border-left:3px solid #3182ce;padding:8px;border-radius:4px;margin-bottom:10px;font-size:.9rem}}
.ai-issues,.ai-suggestions{{padding-left:0;list-style:none;margin:4px 0 10px;font-size:.85rem}}
.ai-error{{color:#718096;font-style:italic}}
</style>
"""
