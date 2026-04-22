"""embed_report.py — 把已生成的报告转为单文件版本（图片内嵌 base64）

用法：
    python embed_report.py                        # 默认处理 reports/focused_ui_report/index.html
    python embed_report.py reports/focused_ui_report/index.html

输出：
    同目录下的 index_standalone.html — 发给任何人，单文件打开即可看到图片。
"""

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) > 1:
        html_path = Path(sys.argv[1])
    else:
        html_path = Path(__file__).parent / "reports" / "focused_ui_report" / "index.html"

    if not html_path.exists():
        print(f"[ERROR] 找不到报告文件: {html_path}")
        print("        请先运行:  python -m src.focused_ui_check")
        sys.exit(1)

    from src.focused_ui_check import _embed_html_images
    out = _embed_html_images(html_path)
    print(f"\n[OK] 单文件报告已生成，直接发送此文件即可：\n     {out}")


if __name__ == "__main__":
    main()
