"""
src/html_reporter.py  ── 可视化 HTML 对比报告生成器
================================================
职责：
    读取 JSON 结果文件 + 截图，生成一份自包含的 HTML 报告。
    图片以 base64 嵌入，单文件即可分享、离线浏览。

输出：
    reports/report.html

包含四大区域：
    [摘要]      整体通过率、严重问题数量、各指标一览
    [像素对比]  Figma 设计 / 网站截图 / 差异高亮 三图并排 + 相似度进度条
                严重级别：Critical(<80%) / Warning(80-95%) / Pass(>=95%)
    [元素对比]  每个 Figma 节点的属性逐行对比表（颜色/字号/圆角/尺寸…）
               支持从 run_result.json 的 element_result 字段或独立 element_diff.json 读取
    [功能检查]  链接状态 / 控制台错误（若存在）

用法：
    python -m src.html_reporter           # 使用 Config 默认路径
    python src/html_reporter.py           # 同上
"""

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from datetime import UTC
except ImportError:
    from datetime import timezone
    UTC = timezone.utc


# ──────────────────────────────────────────────────────────────────────────────
# 内联 CSS
# ──────────────────────────────────────────────────────────────────────────────
_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
     background:#f1f5f9;color:#1e293b;line-height:1.6}
a{color:inherit}
.wrap{max-width:1440px;margin:0 auto;padding:24px}

/* ── Header ── */
header{background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);color:#fff;
       padding:36px 32px 28px;margin-bottom:28px}
header h1{font-size:26px;font-weight:700;letter-spacing:-.3px;margin-bottom:10px}
.meta{display:flex;flex-wrap:wrap;gap:20px;font-size:13px;opacity:.8;margin-bottom:16px}
.overall{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:600}

/* ── Summary dashboard ── */
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
               gap:14px;margin-bottom:28px}
.stat-card{background:#fff;border-radius:10px;padding:16px 18px;
           box-shadow:0 1px 4px rgba(0,0,0,.08);text-align:center}
.stat-card .val{font-size:32px;font-weight:800;line-height:1.1;margin-bottom:4px}
.stat-card .lbl{font-size:12px;color:#64748b;font-weight:500}
.stat-card.s-pass .val{color:#16a34a}
.stat-card.s-fail .val{color:#dc2626}
.stat-card.s-warn .val{color:#d97706}
.stat-card.s-info .val{color:#2563eb}

/* ── Badges ── */
.badge{display:inline-block;padding:3px 11px;border-radius:20px;font-size:12px;
       font-weight:700;letter-spacing:.4px;white-space:nowrap}
.pass{background:#dcfce7;color:#15803d}
.fail{background:#fee2e2;color:#b91c1c}
.warn{background:#fef9c3;color:#a16207}
.critical{background:#fce7f3;color:#9d174d}
.error{background:#f3f4f6;color:#374151}

/* ── Severity bar labels ── */
.sev-critical{color:#9d174d;font-weight:700}
.sev-warning{color:#d97706;font-weight:600}
.sev-pass{color:#16a34a;font-weight:600}
.sev-error{color:#6b7280;font-style:italic}

/* ── Section ── */
section{margin-bottom:28px}
section>h2{font-size:17px;font-weight:700;color:#334155;margin-bottom:14px;
            display:flex;align-items:center;gap:8px}

/* ── Card ── */
.card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.08);
      margin-bottom:18px;overflow:hidden;border-left:5px solid #94a3b8}
.card.c-pass{border-left-color:#16a34a}
.card.c-fail{border-left-color:#dc2626}
.card.c-warn{border-left-color:#d97706}
.card.c-error{border-left-color:#9ca3af}
.card-hd{display:flex;align-items:center;justify-content:space-between;
          padding:14px 20px;border-bottom:1px solid #f1f5f9}
.card-title{font-weight:600;font-size:14px;color:#0f172a}

/* ── Score bar ── */
.score-row{display:flex;align-items:center;gap:12px;padding:10px 20px;
            background:#f8fafc;flex-wrap:wrap}
.s-label{font-size:12px;color:#64748b;min-width:56px}
.bar-wrap{flex:1;min-width:120px;max-width:280px;height:8px;
           background:#e2e8f0;border-radius:4px;overflow:hidden}
.bar{height:100%;border-radius:4px}
.s-value{font-size:22px;font-weight:800;min-width:72px}
.s-sub{font-size:12px;color:#94a3b8}

/* ── Meta row ── */
.meta-row{display:flex;flex-wrap:wrap;gap:20px;padding:8px 20px;
           font-size:12px;color:#64748b;background:#f8fafc;
           border-bottom:1px solid #f1f5f9}
.meta-row code{background:#e2e8f0;padding:1px 5px;border-radius:3px;font-size:11px}

/* ── Image grid ── */
.img-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:#e2e8f0}
.img-col{background:#fff;padding:12px}
.img-lbl{font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;
          letter-spacing:.5px;margin-bottom:8px}
.img-col img{width:100%;height:auto;display:block;border:1px solid #e2e8f0;
              border-radius:6px;cursor:zoom-in;transition:opacity .15s}
.img-col img:hover{opacity:.9}
.no-img{height:100px;display:flex;align-items:center;justify-content:center;
         color:#94a3b8;font-size:12px;background:#f8fafc;border-radius:6px;
         border:2px dashed #e2e8f0}

/* ── Error banner ── */
.error-banner{padding:14px 20px;background:#fef2f2;border-bottom:1px solid #fecaca;
               font-size:13px;color:#b91c1c}
.error-banner code{background:#fecaca;padding:1px 5px;border-radius:3px;font-size:11px}

/* ── Element diff table ── */
.tbl-wrap{padding:16px 20px;overflow-x:auto}
.diff-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.diff-tbl th{text-align:left;padding:7px 12px;background:#f1f5f9;color:#475569;
              font-weight:700;border-bottom:2px solid #e2e8f0;white-space:nowrap}
.diff-tbl td{padding:7px 12px;border-bottom:1px solid #f1f5f9;vertical-align:middle}
.diff-tbl tr.r-pass{background:#f0fdf4}
.diff-tbl tr.r-fail{background:#fff5f5}
.diff-tbl tr.r-skip{background:#fefce8;opacity:.85}
.diff-tbl tr.r-error{background:#f3f4f6;opacity:.7}
.diff-tbl tr.r-detail td{padding:2px 12px 8px;border-bottom:none}

.el-name{font-family:'Consolas','Menlo',monospace;font-size:12px;
          color:#0f172a;max-width:220px;word-break:break-all}
.el-sel{font-family:'Consolas','Menlo',monospace;font-size:11px;color:#64748b}
.tc{text-align:center}
.prop-ok{color:#16a34a;font-size:14px}
.prop-fail{color:#dc2626}
.prop-fail small{display:block;font-size:10px;color:#dc2626;font-weight:600}
.prop-na{color:#cbd5e1}
.unmatched{color:#92400e;font-size:11px}
.unmatched code{background:#fef9c3;padding:1px 4px;border-radius:3px}

/* ── Diff detail chips ── */
.detail-chips{display:flex;flex-wrap:wrap;gap:5px;padding:0 4px}
.chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;
       background:#fee2e2;border-radius:4px;font-size:11px;color:#991b1b}
.chip b{font-weight:700}
.chip code{background:#fca5a5;padding:1px 4px;border-radius:3px;font-size:10px}

/* ── Notice ── */
.notice{background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;
         padding:10px 16px;margin:0 20px 14px;font-size:12px;color:#1e40af}

/* ── Warning ── */
.alert{background:#fef9c3;border:1px solid #fde047;border-radius:8px;
        padding:10px 16px;margin-bottom:14px;font-size:13px;color:#713f12}

/* ── Empty state ── */
.empty{text-align:center;padding:48px 24px;color:#94a3b8;font-size:14px}

footer{text-align:center;padding:24px 0 32px;color:#94a3b8;font-size:12px}

@media(max-width:860px){.img-grid{grid-template-columns:1fr}}
"""

# ──────────────────────────────────────────────────────────────────────────────
# 内联 JS（图片点击放大）
# ──────────────────────────────────────────────────────────────────────────────
_JS = """
document.querySelectorAll('.img-col img').forEach(img=>{
  img.title='点击放大';
  img.addEventListener('click',()=>{
    const ov=document.createElement('div');
    ov.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,.88);z-index:9999;'+
      'display:flex;align-items:center;justify-content:center;cursor:zoom-out;padding:16px';
    const big=document.createElement('img');
    big.src=img.src;
    big.style.cssText='max-width:95vw;max-height:92vh;border-radius:8px;'+
      'box-shadow:0 12px 40px rgba(0,0,0,.6)';
    ov.appendChild(big);
    ov.addEventListener('click',()=>ov.remove());
    document.addEventListener('keydown',e=>{if(e.key==='Escape')ov.remove()},{once:true});
    document.body.appendChild(ov);
  });
});
"""

# ──────────────────────────────────────────────────────────────────────────────
# 属性显示名映射
# ──────────────────────────────────────────────────────────────────────────────
_PROP_LABEL: Dict[str, str] = {
    "color":            "颜色",
    "fill_color":       "填充色",
    "text_color":       "文字色",
    "background_color": "背景色",
    "font_size":        "字号",
    "font_weight":      "字重",
    "font_family":      "字体",
    "border_radius":    "圆角",
    "width":            "宽度",
    "height":           "高度",
    "padding":          "内边距",
    "margin":           "外边距",
    "opacity":          "透明度",
    "letter_spacing":   "字间距",
    "line_height":      "行高",
}


# ──────────────────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────────────────
def _b64(path) -> str:
    """Return base64 data-URI for a PNG; empty string if not found."""
    p = Path(path) if path else None
    if p and p.exists():
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    return ""


def _load(path: Path) -> Optional[Dict]:
    if path and path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def _severity(sim: Optional[float], threshold: float) -> str:
    """把相似度值分级：critical / warning / pass / error。"""
    if sim is None:
        return "error"
    if sim >= threshold:
        return "pass"
    if sim >= threshold * 0.85:
        return "warning"
    return "critical"


def _score_color(sim: Optional[float], threshold: float) -> str:
    sev = _severity(sim, threshold)
    return {"pass": "#16a34a", "warning": "#d97706", "critical": "#dc2626", "error": "#9ca3af"}[sev]


def _badge(passed: bool) -> str:
    cls, txt = ("pass", "PASS") if passed else ("fail", "FAIL")
    return f'<span class="badge {cls}">{txt}</span>'


def _severity_badge(sim: Optional[float], threshold: float) -> str:
    sev = _severity(sim, threshold)
    mapping = {
        "pass":     ('<span class="badge pass">PASS</span>', ""),
        "warning":  ('<span class="badge warn">WARN</span>', '<span class="sev-warning"> ⚠ 偏差较大</span>'),
        "critical": ('<span class="badge critical">CRITICAL</span>', '<span class="sev-critical"> ✖ 差异严重</span>'),
        "error":    ('<span class="badge error">ERROR</span>', '<span class="sev-error"> 截图/对比失败</span>'),
    }
    badge, extra = mapping[sev]
    return badge + extra


def _prop_cell(data: Optional[Dict]) -> str:
    if data is None:
        return '<td class="tc prop-na">—</td>'
    if data.get("passed"):
        return '<td class="tc prop-ok">✅</td>'
    diff = data.get("diff", "")
    tip = f"Figma={data.get('figma','')}  Web={data.get('web','')}" + (f"  Δ={diff}" if diff else "")
    diff_txt = f"<small>Δ{diff}</small>" if diff else ""
    return f'<td class="tc prop-fail" title="{tip}">❌{diff_txt}</td>'


# ──────────────────────────────────────────────────────────────────────────────
# 摘要面板
# ──────────────────────────────────────────────────────────────────────────────
def _summary_section(run_result: Optional[Dict], element_diffs: List[Dict]) -> str:
    if not run_result:
        return ""

    results = run_result.get("page_results", [])
    threshold = float(run_result.get("crawl_summary", {}).get("threshold",
                      next((r.get("threshold", 95) for r in results), 95)))

    total = len(results)
    errors = sum(1 for r in results if str(r.get("status", "")).startswith("error"))
    ok_results = [r for r in results if r.get("status") == "ok" and r.get("similarity") is not None]
    passed = sum(1 for r in ok_results if r.get("passed"))
    critical = sum(1 for r in ok_results if _severity(r.get("similarity"), r.get("threshold", threshold)) == "critical")
    warning = sum(1 for r in ok_results if _severity(r.get("similarity"), r.get("threshold", threshold)) == "warning")

    # Element comparison summary
    elem_pages_total = len(element_diffs)
    elem_pages_pass = sum(1 for d in element_diffs if (d.get("result") or {}).get("overall_passed"))

    avg_sim = (
        sum(r["similarity"] for r in ok_results) / len(ok_results)
        if ok_results else 0.0
    )

    def _stat(val, label, cls="s-info"):
        return f'<div class="stat-card {cls}"><div class="val">{val}</div><div class="lbl">{label}</div></div>'

    cards = [
        _stat(total, "总测试页面", "s-info"),
        _stat(passed, "像素 PASS", "s-pass"),
        _stat(critical, "像素 CRITICAL", "s-fail" if critical else "s-info"),
        _stat(warning, "像素 WARNING", "s-warn" if warning else "s-info"),
        _stat(errors, "执行错误", "s-fail" if errors else "s-info"),
        _stat(f"{avg_sim:.1f}%", "平均相似度", "s-pass" if avg_sim >= 95 else "s-warn"),
    ]
    if elem_pages_total:
        cards.append(_stat(f"{elem_pages_pass}/{elem_pages_total}", "元素对比 PASS", "s-pass" if elem_pages_pass == elem_pages_total else "s-warn"))

    return f'<div class="summary-grid">{"".join(cards)}</div>'


# ──────────────────────────────────────────────────────────────────────────────
# 像素对比区块
# ──────────────────────────────────────────────────────────────────────────────
def _pixel_section(run_result: Optional[Dict]) -> str:
    if not run_result:
        return ""
    results = [r for r in run_result.get("page_results", []) if r.get("similarity") is not None
               or str(r.get("status", "")).startswith("error")]
    if not results:
        return ""

    cards = []
    for r in results:
        sim = r.get("similarity")
        thr = r.get("threshold", 95)
        ok = r.get("passed", False)
        is_error = str(r.get("status", "")).startswith("error")
        name = r.get("page_name") or r.get("figma_name") or r.get("site_path", "unknown")
        browser = r.get("browser") or "—"
        mse = r.get("mse")
        mse_str = f"{mse:.0f}" if mse is not None else "—"
        site_url = r.get("site_url", "")
        error_detail = r.get("error_detail", "")
        sev = _severity(sim, thr)
        col = _score_color(sim, thr)
        bar = min(int(sim or 0), 100)
        card_cls = {"pass": "c-pass", "warning": "c-warn", "critical": "c-fail", "error": "c-error"}[sev]

        def _img(keys, label: str) -> str:
            if isinstance(keys, str):
                keys = [keys]
            src = ""
            for key in keys:
                src = _b64(r.get(key, ""))
                if src:
                    break
            if src:
                return f'<div class="img-col"><div class="img-lbl">{label}</div><img src="{src}" alt="{label}"></div>'
            return f'<div class="img-col"><div class="img-lbl">{label}</div><div class="no-img">暂无图片</div></div>'

        error_banner = (
            f'<div class="error-banner">⚠ 执行失败: <code>{error_detail}</code></div>'
            if is_error else ""
        )

        sim_str = f"{sim:.1f}%" if sim is not None else "—"

        cards.append(f"""
<div class="card {card_cls}">
  <div class="card-hd">
    <span class="card-title">📸 {name}</span>
    {_severity_badge(sim, thr)}
  </div>
  {error_banner}
  <div class="score-row">
    <span class="s-label">相似度</span>
    <div class="bar-wrap"><div class="bar" style="width:{bar}%;background:{col}"></div></div>
    <span class="s-value" style="color:{col}">{sim_str}</span>
    <span class="s-sub">阈值 {thr}%</span>
    <span class="s-sub">MSE {mse_str}</span>
    <span class="s-sub">浏览器 {browser}</span>
  </div>
  <div class="meta-row">
    <span>页面链接 <code>{site_url or "—"}</code></span>
  </div>
  <div class="img-grid">
    {_img(["figma_path","figma_image"],    "Figma 设计稿")}
    {_img(["web_path","web_image"],        f"网站截图 ({browser})")}
    {_img(["diff_path","diff_image"],      "差异高亮（红=差异区域）")}
  </div>
</div>""")

    return f'<section><h2>🖼 像素级视觉对比</h2>{"".join(cards)}</section>'


# ──────────────────────────────────────────────────────────────────────────────
# 元素属性对比区块（支持来自 run_result 的 element_result 字段，或独立 diffs 列表）
# ──────────────────────────────────────────────────────────────────────────────
def _element_section(element_diffs: List[Dict]) -> str:
    """渲染元素对比区块，每页一张卡片。"""
    if not element_diffs:
        return ""

    cards = []
    for diff in element_diffs:
        result = diff.get("result") or {}
        if not result:
            continue

        page_name = diff.get("page_name", "")
        browser   = diff.get("browser", "")
        node_id   = diff.get("figma_node", "")
        cfg       = diff.get("compare_config", {})
        skipped   = diff.get("skipped_props", [])

        overall   = result.get("overall_score", 0)
        ok        = result.get("overall_passed", False)
        thr       = result.get("threshold", 0.70)
        matched   = result.get("total_matched", 0)
        unmatched = result.get("total_unmatched", 0)
        coverage  = result.get("coverage_rate", 0)
        warning   = result.get("warning", "")
        elements: List[Dict] = result.get("elements", [])

        col = _score_color(overall * 100, thr * 100)
        bar = min(int(overall * 100), 100)

        prop_keys: List[str] = []
        seen: set = set()
        for el in elements:
            if el.get("matched"):
                for k in el.get("properties", {}):
                    if k not in seen:
                        seen.add(k)
                        prop_keys.append(k)

        th_cells = "".join(
            f'<th class="tc">{_PROP_LABEL.get(k, k)}</th>' for k in prop_keys
        )

        rows_html_parts = []
        for el in elements:
            name     = el.get("figma_name", "—")
            el_ok    = el.get("passed", False)
            matched_ = el.get("matched", False)
            sel      = el.get("selector", "") or el.get("dom_selector", "—")
            props    = el.get("properties", {})

            if not matched_:
                row_cls = "r-skip"
                status  = '<span class="badge warn">未匹配</span>'
                data_cells = (
                    f'<td colspan="{len(prop_keys)}" class="unmatched">'
                    f'未找到 DOM 元素 &nbsp;<code>{sel}</code></td>'
                )
                detail_row = ""
            else:
                row_cls    = "r-pass" if el_ok else "r-fail"
                status     = _badge(el_ok)
                data_cells = "".join(_prop_cell(props.get(k)) for k in prop_keys)

                chips = []
                for k, v in props.items():
                    if not v.get("passed"):
                        diff_v  = v.get("diff", "")
                        figma_v = v.get("figma", "")
                        web_v   = v.get("web", "")
                        label   = _PROP_LABEL.get(k, k)
                        chip    = (
                            f'<span class="chip"><b>{label}</b>'
                            f' Figma<code>{figma_v}</code>'
                            f' Web<code>{web_v}</code>'
                            + (f' Δ<code>{diff_v}</code>' if diff_v else "")
                            + "</span>"
                        )
                        chips.append(chip)

                detail_row = (
                    f'<tr class="r-detail">'
                    f'<td></td><td></td>'
                    f'<td colspan="{len(prop_keys)}">'
                    f'<div class="detail-chips">{"".join(chips)}</div></td></tr>'
                ) if chips else ""

            rows_html_parts.append(f"""
<tr class="{row_cls}">
  <td class="el-name" title="{sel}">{name}<br><span class="el-sel">{sel}</span></td>
  <td class="tc">{status}</td>
  {data_cells}
</tr>
{detail_row}""")

        tol_parts = []
        for key, label in [
            ("color_tolerance",     "颜色容差"),
            ("size_tolerance",      "尺寸容差"),
            ("font_size_tolerance", "字号容差"),
            ("radius_tolerance",    "圆角容差"),
        ]:
            v = cfg.get(key)
            if v is not None:
                tol_parts.append(f"{label} ±{v}")
        tol_str = "　".join(tol_parts) if tol_parts else ""

        alert_html = f'<div class="alert">⚠️ {warning}</div>' if warning else ""
        skipped_html = (
            f'<div class="notice">ℹ️ 以下属性已跳过对比（Figma 渲染引擎与浏览器差异过大，噪音多于信号）：'
            f' <b>{", ".join(_PROP_LABEL.get(p, p) for p in skipped)}</b></div>'
        ) if skipped else ""

        cards.append(f"""
<div class="card {'c-pass' if ok else 'c-fail'}">
  <div class="card-hd">
    <span class="card-title">⚙️ {page_name} ({browser})</span>
    {_badge(ok)}
  </div>
  {alert_html}
  <div class="score-row">
    <span class="s-label">属性得分</span>
    <div class="bar-wrap"><div class="bar" style="width:{bar}%;background:{col}"></div></div>
    <span class="s-value" style="color:{col}">{bar}%</span>
    <span class="s-sub">阈值 {int(thr*100)}%</span>
    <span class="s-sub">匹配 {matched}/{matched+unmatched}</span>
    <span class="s-sub">覆盖率 {int(coverage*100)}%</span>
  </div>
  <div class="meta-row">
    <span>Figma 节点 <code>{node_id}</code></span>
    {f'<span>{tol_str}</span>' if tol_str else ''}
  </div>
  {skipped_html}
  <div class="tbl-wrap">
    <table class="diff-tbl">
      <thead>
        <tr>
          <th style="min-width:160px">元素 (Figma Layer → CSS)</th>
          <th class="tc">状态</th>
          {th_cells}
        </tr>
      </thead>
      <tbody>{"".join(rows_html_parts)}</tbody>
    </table>
  </div>
</div>""")

    return f'<section><h2>🔬 元素属性级对比</h2>{"".join(cards)}</section>'


# ──────────────────────────────────────────────────────────────────────────────
# 主函数
# ──────────────────────────────────────────────────────────────────────────────
def generate_report(
    run_result_path: Optional[Path] = None,
    element_diff_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> Path:
    """
    生成自包含可视化 HTML 报告。

    元素对比数据优先从 run_result.json 的 page_results[].element_result 字段读取
    （run_orchestrator 生成）；若没有则回退到独立的 element_diff.json
    （pytest pipeline / focused 生成）。

    Args:
        run_result_path:   像素对比 JSON 路径（默认 Config.JSON_REPORT_PATH）
        element_diff_path: 元素对比 JSON 路径（默认 Config.ELEMENT_DIFF_PATH）
        output_path:       输出 HTML 路径（默认 Config.HTML_REPORT_PATH）

    Returns:
        生成的 HTML 文件路径
    """
    from config.config import Config

    run_result_path   = run_result_path   or Config.JSON_REPORT_PATH
    element_diff_path = element_diff_path or Config.ELEMENT_DIFF_PATH
    output_path       = output_path       or Config.HTML_REPORT_PATH

    run_result   = _load(run_result_path)
    element_diff = _load(element_diff_path)

    if not run_result and not element_diff:
        print("⚠️  未找到任何测试结果，跳过报告生成。")
        print(f"   期望: {run_result_path}")
        print(f"         {element_diff_path}")
        return output_path

    # ── 从 run_result 提取嵌入的元素对比结果
    inline_elem_diffs: List[Dict] = []
    if run_result:
        for r in run_result.get("page_results", []):
            elem_res = r.get("element_result")
            if elem_res:
                inline_elem_diffs.append({
                    "page_name": r.get("figma_name") or r.get("page_name", ""),
                    "browser":   r.get("browser", ""),
                    "figma_node": r.get("plan_id", ""),
                    "result":    elem_res,
                    "compare_config": {
                        "color_tolerance":     Config.COMPARE_COLOR_TOLERANCE,
                        "size_tolerance":      Config.COMPARE_SIZE_TOLERANCE,
                        "font_size_tolerance": Config.COMPARE_FONT_SIZE_TOLERANCE,
                        "radius_tolerance":    Config.COMPARE_RADIUS_TOLERANCE,
                    },
                })

    # ── 回退：读独立 element_diff.json（pytest / focused pipeline）
    fallback_elem_diffs: List[Dict] = []
    if not inline_elem_diffs and element_diff:
        if "diffs" in element_diff:
            # run_orchestrator 写入的格式: {"diffs": [...]}
            fallback_elem_diffs = element_diff["diffs"]
        elif "result" in element_diff:
            # 旧版单页 element_diff.json 格式（pytest pipeline）
            fallback_elem_diffs = [element_diff]

    element_diffs = inline_elem_diffs or fallback_elem_diffs

    src       = run_result or element_diff or {}
    base_url  = src.get("base_url", "—")
    version   = src.get("version", "—")
    now_str   = datetime.now(UTC).strftime("%Y-%m-%d  %H:%M  UTC")

    # ── 整体通过判断
    pixel_ok = all(
        r.get("passed", True)
        for r in (run_result or {}).get("page_results", [])
        if r.get("similarity") is not None
    )
    elem_ok = all(
        (d.get("result") or {}).get("overall_passed", True)
        for d in element_diffs
    ) if element_diffs else True
    all_ok = pixel_ok and elem_ok

    overall_badge = _badge(all_ok)
    overall_text  = "全部通过 ✨" if all_ok else "存在差异，请查看详情"

    summary_html = _summary_section(run_result, element_diffs)
    pixel_html   = _pixel_section(run_result)
    element_html = _element_section(element_diffs)

    body_content = summary_html + pixel_html + element_html
    if not body_content.strip():
        body_content = '<div class="empty">📭 暂无测试结果，请先运行测试。</div>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>UI 对比报告 · {now_str}</title>
  <style>{_CSS}</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>🎨 UI 对比报告</h1>
    <div class="meta">
      <span>🌐 {base_url}</span>
      <span>📦 v{version}</span>
      <span>🕒 {now_str}</span>
    </div>
    <div class="overall">{overall_badge} &nbsp;{overall_text}</div>
  </div>
</header>
<div class="wrap">
  {body_content}
</div>
<footer>由 figma-ui-automation 自动生成 · 点击图片可放大查看</footer>
<script>{_JS}</script>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"[OK] 可视化报告已生成: {output_path}")
    return output_path


# ──────────────────────────────────────────────────────────────────────────────
# 直接运行
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    generate_report()
