"""
src/figma_url.py  ── Figma URL 解析工具
================================================
职责：
    从用户粘贴的任意 Figma 链接中抽取 ``file_key`` 和 ``node_id``，
    让用户只需在 ``.env`` / ``config/focused_pages.json`` 里贴完整 URL，
    不用再手动对照拆出 key 和 node。

支持的 URL 形态：
    https://www.figma.com/design/{fileKey}/{slug}?node-id=15480-72&...
    https://www.figma.com/file/{fileKey}/{slug}?node-id=15480-72
    https://www.figma.com/design/{fileKey}/branch/{branchKey}/{slug}?...
    https://www.figma.com/proto/{fileKey}/{slug}?node-id=...
    https://www.figma.com/board/{fileKey}/{slug}        (FigJam)
    https://www.figma.com/make/{makeFileKey}/{slug}     (Figma Make)

归一化：
    Figma URL 里 node-id 用短横线（``15480-72``），
    Figma API 响应里用冒号（``15480:72``）。
    本模块统一输出冒号形式，和 FigmaClient/Indexer 一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlparse


_FILE_KEY_PATH_PREFIXES = ("design", "file", "proto", "board", "make")


@dataclass(frozen=True)
class FigmaUrlInfo:
    """解析结果。任意字段可能为 None（URL 不完整时）。"""

    file_key: Optional[str]
    node_id: Optional[str]  # 已归一化为冒号形式
    is_figjam: bool = False
    is_make: bool = False

    def ok(self) -> bool:
        return bool(self.file_key)


def normalize_node_id(node_id: Optional[str]) -> Optional[str]:
    """把 ``15480-72`` / ``15480:72`` 统一成 ``15480:72``。空值透传。"""
    if not node_id:
        return None
    return node_id.strip().replace("-", ":")


def parse_figma_url(url: str) -> FigmaUrlInfo:
    """从 Figma URL 抽取 file_key 与 node_id。

    无效 URL 不会抛异常，只返回 file_key=None 的 FigmaUrlInfo。调用方
    可用 ``.ok()`` 判断是否拿到有效 key。
    """
    if not url or not isinstance(url, str):
        return FigmaUrlInfo(None, None)

    parsed = urlparse(url.strip())
    if "figma.com" not in (parsed.netloc or ""):
        return FigmaUrlInfo(None, None)

    # 路径形如 /design/{fileKey}/... 或 /file/{fileKey}/...
    # branch 链接形如 /design/{fileKey}/branch/{branchKey}/...
    parts = [p for p in (parsed.path or "").split("/") if p]
    file_key: Optional[str] = None
    is_figjam = False
    is_make = False
    for i, seg in enumerate(parts):
        if seg in _FILE_KEY_PATH_PREFIXES and i + 1 < len(parts):
            file_key = parts[i + 1]
            is_figjam = seg == "board"
            is_make = seg == "make"
            # branch 链接下一步的 key 更精确
            if i + 2 < len(parts) and parts[i + 2] == "branch" and i + 3 < len(parts):
                file_key = parts[i + 3]
            break

    # node-id 从 query string 里取
    q = parse_qs(parsed.query or "")
    node_raw = None
    if "node-id" in q and q["node-id"]:
        node_raw = q["node-id"][0]
    else:
        # 兼容 fragment 形式 #node-id=... (偶尔出现)
        frag = parse_qs(parsed.fragment or "")
        if "node-id" in frag and frag["node-id"]:
            node_raw = frag["node-id"][0]

    return FigmaUrlInfo(
        file_key=file_key,
        node_id=normalize_node_id(node_raw),
        is_figjam=is_figjam,
        is_make=is_make,
    )


# ── 便捷函数 ──────────────────────────────────────────────────────

def extract_file_key(url: str) -> Optional[str]:
    return parse_figma_url(url).file_key


def extract_node_id(url: str) -> Optional[str]:
    return parse_figma_url(url).node_id


# 供其它模块快速自检用的小型测试（`python -m src.figma_url` 即可跑）
if __name__ == "__main__":
    samples = [
        "https://www.figma.com/design/fYPLGfJU35LLvqgBvUQ1bG/%E8%B5%84%E8%AE%AF"
        "%E7%AB%99?node-id=15480-75&p=f&m=dev",
        "https://www.figma.com/file/ABC123/Demo?node-id=10:20",
        "https://www.figma.com/design/KKK/branch/BBB/Slug?node-id=3-4",
        "https://www.figma.com/board/BOARDKEY/MyJam",
        "https://www.figma.com/make/MAKEKEY/MyApp",
        "not a figma url",
    ]
    for s in samples:
        print(s, "→", parse_figma_url(s))
