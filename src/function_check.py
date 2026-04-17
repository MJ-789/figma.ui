"""
src/function_check.py  ── 功能检测模块
================================================
职责：
    对一个已加载好的 Playwright Page 做"功能级"体检：
      1. 链接跳转（link navigation）：遍历所有可见 <a href>，
         用浏览器凭据发请求，验证 HTTP 状态码、耗时、
         以及响应内容里是否有 "not found"/"404" 之类的错误文案。
      2. 按钮点击（button click）：遍历所有可见 <button> /
         [role=button] / input[type=button|submit]，
         每个按钮在隔离的安全策略下点击，捕获控制台错误、页面错误、
         误触发的导航（导航后自动返回原页面）。
      3. 异常页面识别：捕获页面加载期间的 console error、pageerror、
         4xx/5xx 网络请求，作为"问题线索"附带在报告里。

安全：
    - 默认排除危险按钮（logout / delete / submit / 支付等），
      可通过 DEFAULT_BUTTON_EXCLUDE 扩展。
    - 所有操作都有短超时（链接 8s / 点击 2.5s）。
    - 每个页面硬性上限（链接 25 / 按钮 12）避免耗时。
    - 点击触发导航后会自动 goto 回原始 URL，不污染上下文。

输出：
    FunctionChecker.run() 返回一个 dict：
        {
          "links": [{href,text,status,ok,elapsed_ms,error}],
          "buttons": [{text,status,reason,error,navigated_to?}],
          "console_errors": [str],
          "page_errors": [str],
          "failed_requests": [{url,status,method}],
          "summary": {...}
        }
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


DEFAULT_BUTTON_EXCLUDE = {
    "logout", "signout", "sign out", "log out", "登出", "退出",
    "delete", "remove", "删除", "移除", "移除账户",
    "cancel subscription", "unsubscribe", "取消订阅",
    "submit", "提交", "confirm", "确认", "确定",
    "login", "sign in", "登录", "注册", "register",
    "pay", "checkout", "结算", "支付", "购买",
    "publish", "发布",
}


class FunctionChecker:
    """功能检测执行器.

    Args:
        page: 已经 goto 过目标 URL 的 Playwright Page。
        max_links: 单页最多验证多少条不同 URL 的链接。
        max_buttons: 单页最多点击多少个按钮。
        link_timeout_ms: 链接请求单次超时 (ms)。
        button_timeout_ms: 按钮点击单次超时 (ms)。
        extra_button_excludes: 追加的按钮文本/aria-label 屏蔽关键词。
    """

    def __init__(
        self,
        page,
        max_links: int = 25,
        max_buttons: int = 12,
        link_timeout_ms: int = 8000,
        button_timeout_ms: int = 2500,
        extra_button_excludes: Optional[List[str]] = None,
    ):
        self.page = page
        self.max_links = max_links
        self.max_buttons = max_buttons
        self.link_timeout_ms = link_timeout_ms
        self.button_timeout_ms = button_timeout_ms
        self._exclude_kw = {kw.lower() for kw in DEFAULT_BUTTON_EXCLUDE}
        if extra_button_excludes:
            self._exclude_kw.update(kw.lower() for kw in extra_button_excludes)

        self._console_errors: List[str] = []
        self._page_errors: List[str] = []
        self._failed_requests: List[Dict[str, Any]] = []
        self._attached = False

    # ------------------------------------------------------------------
    # Event listeners
    # ------------------------------------------------------------------
    def _attach_listeners(self) -> None:
        """Hook console / pageerror / response listeners exactly once."""
        if self._attached:
            return
        self._attached = True

        def _on_console(msg):
            try:
                if msg.type == "error":
                    self._console_errors.append(msg.text[:400])
            except Exception:
                pass

        def _on_pageerror(exc):
            try:
                self._page_errors.append(str(exc)[:400])
            except Exception:
                pass

        def _on_response(resp):
            try:
                status = resp.status
                if status >= 400:
                    self._failed_requests.append(
                        {
                            "url": resp.url,
                            "status": status,
                            "method": resp.request.method if resp.request else "",
                        }
                    )
            except Exception:
                pass

        self.page.on("console", _on_console)
        self.page.on("pageerror", _on_pageerror)
        self.page.on("response", _on_response)

    # ------------------------------------------------------------------
    # Enumeration
    # ------------------------------------------------------------------
    def _collect_links(self) -> List[Dict[str, Any]]:
        js = """
        () => {
          const out = [];
          const seen = new Set();
          for (const a of document.querySelectorAll('a[href]')) {
            const r = a.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;
            if (a.offsetParent === null) continue;
            const href = a.href || '';
            if (!href) continue;
            if (href.startsWith('javascript:')) continue;
            if (href.startsWith('mailto:') || href.startsWith('tel:')) continue;
            if (seen.has(href)) continue;
            seen.add(href);
            out.push({
              href: href,
              text: (a.innerText || a.textContent || '').trim().slice(0, 80),
              title: a.getAttribute('title') || '',
              aria: a.getAttribute('aria-label') || '',
              target: a.getAttribute('target') || ''
            });
          }
          return out;
        }
        """
        try:
            return self.page.evaluate(js) or []
        except Exception:
            return []

    # Same selector used by the JS enumerator so _locate_button can point at
    # the exact same element by nth index.
    BUTTON_SELECTOR = "button, [role='button'], input[type='button'], input[type='submit']"

    def _collect_buttons(self) -> List[Dict[str, Any]]:
        js = """
        () => {
          const out = [];
          const nodes = document.querySelectorAll(
            "button, [role='button'], input[type='button'], input[type='submit']"
          );
          nodes.forEach((b, idx) => {
            const r = b.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) return;
            if (b.offsetParent === null) return;
            const text = (b.innerText || b.textContent || b.value || '').trim().slice(0, 120);
            if (!text) return;
            out.push({
              dom_index: idx,
              text: text,
              aria: b.getAttribute('aria-label') || '',
              type: b.getAttribute('type') || '',
              disabled: b.hasAttribute('disabled'),
              role: b.getAttribute('role') || b.tagName.toLowerCase()
            });
          });
          return out;
        }
        """
        try:
            return self.page.evaluate(js) or []
        except Exception:
            return []

    def _is_dangerous_button(self, btn: Dict[str, Any]) -> bool:
        label = (btn.get("text", "") + " " + btn.get("aria", "")).lower()
        if not label.strip():
            return True
        for kw in self._exclude_kw:
            if kw in label:
                return True
        if btn.get("type", "").lower() in ("submit",):
            return True
        if btn.get("disabled"):
            return True
        return False

    # ------------------------------------------------------------------
    # Link navigation check
    # ------------------------------------------------------------------
    def _check_links(self, links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        request_api = getattr(self.page, "request", None)
        if request_api is None:
            # Very old Playwright builds — skip gracefully.
            return [{**link, "status": None, "ok": False, "error": "page.request unavailable"} for link in links[: self.max_links]]

        for link in links[: self.max_links]:
            href = link["href"]
            entry: Dict[str, Any] = {
                "href": href,
                "text": link.get("text", ""),
                "aria": link.get("aria", ""),
                "target": link.get("target", ""),
                "status": None,
                "ok": False,
                "elapsed_ms": 0,
                "error": "",
            }
            start = time.monotonic()
            try:
                resp = request_api.get(href, timeout=self.link_timeout_ms, max_redirects=10)
                entry["status"] = resp.status
                entry["elapsed_ms"] = int((time.monotonic() - start) * 1000)
                entry["ok"] = 200 <= resp.status < 400
                if entry["ok"]:
                    try:
                        body = (resp.text() or "")[:4000].lower()
                        if ("not found" in body and "404" in body) or "页面不存在" in body or "找不到页面" in body:
                            entry["ok"] = False
                            entry["error"] = "page body indicates not-found"
                    except Exception:
                        pass
            except Exception as exc:
                entry["elapsed_ms"] = int((time.monotonic() - start) * 1000)
                entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            results.append(entry)
        return results

    # ------------------------------------------------------------------
    # Button click check
    # ------------------------------------------------------------------
    def _locate_button(self, btn: Dict[str, Any]):
        """Locate by DOM index first (rock solid), with name-based fallbacks."""
        idx = btn.get("dom_index")
        if isinstance(idx, int):
            try:
                loc = self.page.locator(self.BUTTON_SELECTOR).nth(idx)
                if loc.count() > 0:
                    return loc
            except Exception:
                pass
        # Fallbacks, in case the DOM has shifted since enumeration.
        name = (btn.get("aria") or btn.get("text") or "").strip().splitlines()[0]
        if not name:
            return None
        try:
            loc = self.page.get_by_role("button", name=name).first
            if loc.count() > 0:
                return loc
        except Exception:
            pass
        try:
            loc = self.page.get_by_text(name, exact=False).first
            if loc.count() > 0:
                return loc
        except Exception:
            pass
        return None

    def _click_buttons(
        self,
        buttons: List[Dict[str, Any]],
        origin_url: str,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        clicked = 0

        for btn in buttons:
            if clicked >= self.max_buttons:
                break
            base_entry = {
                "text": btn.get("text", ""),
                "aria": btn.get("aria", ""),
                "type": btn.get("type", ""),
                "role": btn.get("role", ""),
                "status": "skipped",
                "reason": "",
                "error": "",
                "navigated_to": "",
            }
            if self._is_dangerous_button(btn):
                base_entry["reason"] = "excluded by safety filter"
                results.append(base_entry)
                continue

            loc = self._locate_button(btn)
            if loc is None:
                base_entry["status"] = "not_found"
                base_entry["reason"] = "no matching locator"
                results.append(base_entry)
                continue

            err_before = len(self._console_errors) + len(self._page_errors)
            url_before = self.page.url
            try:
                loc.click(timeout=self.button_timeout_ms)
                self.page.wait_for_timeout(400)
                err_after = len(self._console_errors) + len(self._page_errors)
                url_after = self.page.url

                base_entry["status"] = "ok"
                if err_after > err_before:
                    base_entry["status"] = "console_error"
                    base_entry["error"] = (
                        self._console_errors[-1]
                        if self._console_errors
                        else (self._page_errors[-1] if self._page_errors else "")
                    )
                if url_after != url_before:
                    base_entry["navigated_to"] = url_after
                    if base_entry["status"] == "ok":
                        base_entry["status"] = "navigated"
                    # Restore original page so subsequent buttons have consistent context.
                    try:
                        self.page.goto(origin_url, wait_until="networkidle", timeout=10000)
                    except Exception:
                        pass
            except Exception as exc:
                base_entry["status"] = "click_failed"
                base_entry["error"] = f"{type(exc).__name__}: {str(exc)[:140]}"
            clicked += 1
            results.append(base_entry)
        return results

    # ------------------------------------------------------------------
    # Public entrypoint
    # ------------------------------------------------------------------
    def run(self, check_buttons: bool = True) -> Dict[str, Any]:
        self._attach_listeners()
        origin_url = self.page.url

        links_raw = self._collect_links()
        buttons_raw = self._collect_buttons()
        link_results = self._check_links(links_raw)

        if check_buttons:
            button_results = self._click_buttons(buttons_raw, origin_url=origin_url)
        else:
            button_results = [
                {
                    "text": b.get("text", ""),
                    "aria": b.get("aria", ""),
                    "type": b.get("type", ""),
                    "role": b.get("role", ""),
                    "status": "enumerated",
                    "reason": "click disabled",
                    "error": "",
                    "navigated_to": "",
                }
                for b in buttons_raw[: self.max_buttons]
            ]

        link_failed = sum(1 for x in link_results if not x.get("ok"))
        button_failed = sum(
            1 for x in button_results if x.get("status") in ("click_failed", "console_error", "not_found")
        )

        return {
            "links": link_results,
            "buttons": button_results,
            "console_errors": list(self._console_errors),
            "page_errors": list(self._page_errors),
            "failed_requests": list(self._failed_requests),
            "summary": {
                "link_total": len(link_results),
                "link_failed": link_failed,
                "link_pass_rate": round((len(link_results) - link_failed) / max(1, len(link_results)) * 100, 2),
                "button_total": len(button_results),
                "button_failed": button_failed,
                "button_pass_rate": round((len(button_results) - button_failed) / max(1, len(button_results)) * 100, 2),
                "console_errors": len(self._console_errors),
                "page_errors": len(self._page_errors),
                "failed_requests": len(self._failed_requests),
            },
        }
