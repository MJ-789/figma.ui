"""
src/figma_page_sync.py  ── Figma Frame × 网站页面 发现工具
================================================
用途：
    首次配置时运行一次，自动：
    1. 从 Figma 文件列出所有 Frame 及其 node_id
    2. 爬取网站，列出所有发现的页面 URL
    3. 打印两边的对照表，供用户填写 .env 中的 PAGE_MAP

运行方式：
    python -m src.figma_page_sync
    python -m src.figma_page_sync --no-crawl    # 只看 Figma，不爬网站
    python -m src.figma_page_sync --no-figma    # 只爬网站，不拉 Figma
"""

import argparse
import sys
from typing import Dict, List

from config.config import Config


# ──────────────────────────────────────────────────────────────────────────────
# Figma 部分
# ──────────────────────────────────────────────────────────────────────────────
def discover_figma_frames() -> List[Dict]:
    """
    拉取 Figma 文件结构，返回所有 FRAME / COMPONENT 节点列表。
    每项：{page, name, node_id}
    """
    from src.figma_client import FigmaClient

    print("\n[Figma] 连接 Figma API...")
    try:
        client = FigmaClient()
    except ValueError as e:
        print(f"  ❌ {e}")
        return []

    print(f"  文件: {Config.FIGMA_FILE_KEY}")

    frames = []
    try:
        data = client.get_file_structure()
        for page in data["document"]["children"]:
            page_name = page["name"]
            for child in page.get("children", []):
                if child["type"] in ("FRAME", "COMPONENT"):
                    frames.append({
                        "page":    page_name,
                        "name":    child["name"],
                        "node_id": child["id"],
                    })
    except Exception as e:
        print(f"  ❌ Figma API 失败: {e}")
        return []

    print(f"  发现 {len(frames)} 个 Frame\n")
    return frames


# ──────────────────────────────────────────────────────────────────────────────
# 网站爬取部分
# ──────────────────────────────────────────────────────────────────────────────
def discover_site_pages(max_pages: int = 30) -> List[str]:
    """
    BFS 爬取网站，返回发现的去重 URL 列表（相对路径）。
    """
    from src.page_crawler import PageCrawler
    from urllib.parse import urlparse

    print("[网站] 开始爬取...")
    print(f"  BASE_URL: {Config.BASE_URL}")

    crawler = PageCrawler(
        base_url=Config.BASE_URL,
        browser_type=Config.DEFAULT_BROWSER,
        headless=True,
        max_depth=Config.CRAWL_MAX_DEPTH,
        max_pages=max_pages,
        max_clicks_per_page=Config.CRAWL_MAX_CLICKS_PER_PAGE,
        click_selectors=Config.CRAWL_CLICK_SELECTORS,
        exclude_keywords=Config.CRAWL_EXCLUDE_KEYWORDS,
    )

    discovered = crawler.discover(Config.CRAWL_SEED_PATHS)
    base = Config.BASE_URL.rstrip("/")

    paths = []
    for page in discovered:
        if page.get("status") != "ok":
            continue
        url = page["url"]
        # 转为相对路径
        path = url.replace(base, "") or "/"
        paths.append(path)

    print(f"  发现 {len(paths)} 个页面\n")
    return paths


# ──────────────────────────────────────────────────────────────────────────────
# 打印结果
# ──────────────────────────────────────────────────────────────────────────────
def _print_figma_table(frames: List[Dict]):
    if not frames:
        return
    print("=" * 70)
    print("  FIGMA FRAMES")
    print("=" * 70)
    print(f"  {'Frame 名称':<30} {'所在 Page':<20} {'node_id'}")
    print("  " + "-" * 66)
    for f in frames:
        print(f"  {f['name']:<30} {f['page']:<20} {f['node_id']}")
    print()


def _print_site_table(paths: List[str]):
    if not paths:
        return
    print("=" * 70)
    print("  网站发现的页面")
    print("=" * 70)
    for i, p in enumerate(paths, 1):
        print(f"  {i:>3}. {p}")
    print()


def _print_page_map_template(frames: List[Dict], paths: List[str]):
    """打印可直接复制到 .env 的 PAGE_MAP 模板。"""
    print("=" * 70)
    print("  PAGE_MAP 配置模板（复制到 .env）")
    print("  格式：标签|node_id|网站路径  （多条用逗号分隔）")
    print("=" * 70)
    print()
    print("# 示例：根据上方表格，把 Figma Frame 名称和对应网站路径填进去")
    print("# PAGE_MAP=Home|<node_id>|/ , category|<node_id>|/list/Finance")
    print()

    # 尝试自动匹配（Frame名转小写后在路径中查找）
    auto_pairs = []
    url_map = {
        "home": "/",
        "首页": "/",
        "index": "/",
    }
    # 加入已发现路径
    for p in paths:
        seg = p.strip("/").split("/")[-1].lower() if p.strip("/") else "home"
        url_map[seg] = p

    for f in frames:
        key = f["name"].lower().replace(" ", "").replace("/", "")
        matched_url = url_map.get(key, "???")
        auto_pairs.append(f"{f['name']}|{f['node_id']}|{matched_url}")

    if auto_pairs:
        print("# 自动猜测的映射（请核对后修改 ??? 的部分）：")
        print("PAGE_MAP=" + " , \\\n        ".join(auto_pairs))
    print()


# ──────────────────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Figma Frame × 网站页面发现工具")
    parser.add_argument("--no-crawl",  action="store_true", help="跳过网站爬取")
    parser.add_argument("--no-figma",  action="store_true", help="跳过 Figma 拉取")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  Figma × 网站 页面发现工具")
    print("=" * 70)

    frames: List[Dict] = []
    paths:  List[str]  = []

    if not args.no_figma:
        frames = discover_figma_frames()
        _print_figma_table(frames)

    if not args.no_crawl:
        paths = discover_site_pages()
        _print_site_table(paths)

    _print_page_map_template(frames, paths)

    print("=" * 70)
    print("  完成！把上方 PAGE_MAP 行复制到 .env，然后运行 run.bat 开始对比。")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
