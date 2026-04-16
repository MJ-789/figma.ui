# Figma UI Automation

把 Figma 设计稿与真实网站做自动化视觉对比，输出可视化报告和元素级差异清单，帮助开发快速定位并修复 UI 偏差。

## 当前能力

- 自动发现网站页面并生成站点清单
- 自动索引 Figma 页面/模板并进行页面配对
- 页面级截图对比（相似度、差异图、并排图）
- 元素级样式对比（颜色/字号/字重/字体/行高/圆角/尺寸）
- 3 页面专项稳定结构块对比（`Header / Hero / Content / Footer`）
- 生成开发可读报告（差异项 + CSS 修复建议 + 优先级）

## 快速开始

```bash
# 1) 安装依赖
pip install -r requirements.txt
playwright install chromium

# 2) 配置 .env（需自行创建/维护）
#    最少包含 FIGMA_ACCESS_TOKEN、FIGMA_FILE_KEY、BASE_URL、FIGMA_TARGET_NODE_ID

# 3) 运行全链路自动代理
python run_agent.py
```

## 常用运行命令

```bash
# 全链路（可复用缓存 inventory）
python run_agent.py

# 只做发现+配对+计划，不做实际截图对比
python run_agent.py --dry

# 强制重新拉取网站/Figma数据
python run_agent.py --fresh

# 3 页专项稳定结构块 + 元素级差异报告
python -m src.focused_ui_check
```

## 3 页专项对比说明

`src.focused_ui_check` 固定对比以下页面：

- Home: `https://newsdrafte.com/`
- Category: `https://newsdrafte.com/list/Pharmaceuticals`
- Details: `https://newsdrafte.com/anti-allergy-medications-scientific-overview-of-types-mechanisms-medical-context`

输出报告：

- 可视化 HTML：`reports/focused_ui_report.html`
- 可读摘要：`reports/focused_ui_summary.md`
- 页面与块级 JSON：`reports/json/focused_run_result.json`
- 元素差异 JSON：`reports/json/focused_element_diffs.json`
- Figma 节点原始 JSON：`reports/json/focused_figma_*.json`

## 报告怎么给开发用

- 先看 **Stable-block average similarity**（主指标）
- 再看 **Top differences**（元素级差异）
- 按 **Developer fix priorities** 顺序修复（P0/P1/P2）
- 每个差异项都附带建议样式（如 `font-size` / `color` / `font-family`），可直接用于改 CSS 或 design token

## 关键配置（`.env`）

- `FIGMA_ACCESS_TOKEN`
- `FIGMA_FILE_KEY`
- `FIGMA_TARGET_NODE_ID`
- `BASE_URL`
- `SIMILARITY_THRESHOLD`
- `AGENT_VIEWPORT_WIDTH`, `AGENT_VIEWPORT_HEIGHT`
- `AGENT_HIDE_SELECTORS`
- `PAGE_MATCH_MIN_CONFIDENCE`, `PAGE_MATCH_TOP_K`
- `PAGE_MATCH_WEIGHT_NAME`, `PAGE_MATCH_WEIGHT_TEXT`, `PAGE_MATCH_WEIGHT_STRUCTURE`, `PAGE_MATCH_WEIGHT_PAGE_TYPE`
- `COMPARE_COLOR_TOLERANCE`, `COMPARE_SIZE_TOLERANCE`, `COMPARE_FONT_SIZE_TOLERANCE`, `COMPARE_RADIUS_TOLERANCE`
- `COMPARE_ELEMENT_THRESHOLD`, `COMPARE_MAX_DEPTH`, `COMPARE_MIN_MATCH_COUNT`

## 目录说明（精简）

- `run_agent.py`: 自动测试代理入口
- `config/config.py`: 全局配置中心
- `src/run_orchestrator.py`: 发现→索引→配对→计划→执行→报告
- `src/page_crawler.py`: 网站页面发现
- `src/figma_page_indexer.py`: Figma 页面索引
- `src/page_matcher.py`: 页面自动配对
- `src/image_compare.py`: 截图相似度与差异图
- `src/element_compare.py`: 元素属性级对比
- `src/focused_ui_check.py`: 3 页专项稳定结构块报告
- `reports/`: 运行产出（JSON / HTML / 图片）

## 注意事项

- 本项目对 Figma 只读（读取结构和导出图片），不写回设计稿。
- Windows 下请避免同时占用 `reports/focused_ui_report.html`（打开文件时重跑可能导致文件锁）。
- 项目使用 `sync_playwright()`，`pytest.ini` 中已禁用 `pytest-playwright` 插件冲突项。
