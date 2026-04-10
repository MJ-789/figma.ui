# Figma UI 自动化视觉回归测试

把 Figma 设计稿与真实网站做像素级对比，自动判断视觉一致性。

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt
playwright install chromium

# 2. 填写配置
cp .env.example .env   # 编辑 .env，填入 token 和 BASE_URL

# 3. 运行测试
pytest
```

---

## 目录结构与说明

```
figma-ui-automation/
│
├── .env                        # 本地环境变量（不提交 Git）
│                               # 存放 token、BASE_URL、阈值等敏感/环境配置
│
├── .env.example                # .env 模板，展示所有可填字段（可提交）
│
├── requirements.txt            # Python 依赖包及版本锁定
│
├── pytest.ini                  # pytest 全局配置：测试目录、HTML 报告路径、
│                               # 自定义 marker、-p no:playwright 禁用冲突插件
│
├── VERSION                     # 当前项目版本号，格式 x.y.z
├── CHANGELOG.md                # 每次版本变更的详细记录
│
├── config/
│   └── config.py               # 全局配置中心
│                               # 从 .env 读取所有环境变量，统一暴露给项目使用
│                               # 包含：路径、Figma 凭证、阈值、浏览器、爬取参数、
│                               #       手工维护的页面注册表 TEST_PAGES
│
├── src/                        # 核心功能模块（业务逻辑层）
│   ├── figma_client.py         # Figma REST API 客户端
│   │                           # 功能：按 node_id 导出设计稿 PNG、
│   │                           #       列出文件结构/页面/Frame
│   │
│   ├── web_capture.py          # Playwright 网页截图模块
│   │                           # 功能：启动浏览器、全页截图、元素截图、
│   │                           #       隐藏动态元素、批量/跨浏览器/响应式截图
│   │
│   ├── image_compare.py        # 图像对比模块
│   │                           # 功能：计算相似度（absdiff/MSE/SSIM）、
│   │                           #       生成差异高亮图、左右并排对比图、汇总报告
│   │
│   ├── page_crawler.py         # 多页面自动发现模块（v1.1.0）
│   │                           # 功能：BFS 爬取站内页面，从种子路径出发，
│   │                           #       自动提取可点击链接，限制深度/数量
│   │
│   └── report_writer.py        # 结构化 JSON 报告输出（v1.1.0）
│                               # 功能：把运行结果汇总为 run_result.json，
│                               #       包含版本、时间、crawl 摘要、各页结果
│
├── tests/                      # 测试用例层
│   ├── conftest.py             # pytest 全局钩子
│   │                           # 功能：检测 Firefox 是否安装，未装则自动跳过
│   │
│   └── test_desktop.py         # 桌面端视觉回归测试
│                               # TestDesktop      ── 指定页面对比（chromium/firefox）
│                               # TestCrawlDiscovery ── 多页面自动发现（v1.1.0）
│
├── screenshots/                # 截图存储（测试运行时自动创建）
│   ├── figma/                  # Figma 导出的设计稿 PNG
│   └── web/                    # 真实网站截图 PNG
│
├── reports/                    # 报告存储（测试运行时自动创建）
│   ├── html/
│   │   └── report.html         # pytest-html 可视化测试报告（在浏览器打开）
│   ├── images/
│   │   ├── *_diff.png          # 差异高亮图（蓝色标注不一致区域）
│   │   └── *_compare.png       # 左右并排对比图（Figma vs 网站）
│   └── json/
│       └── run_result.json     # 结构化 JSON 报告（机器可读，便于 CI 集成）
│
└── docs/
    └── ROADMAP.md              # 功能演进路线（元素级对比、爬取增强等规划）
```

---

## 测试流程图

```
Figma API ──► figma_client ──► 设计稿 PNG
                                    │
                                    ▼
网站 URL ───► web_capture ──► 网站截图 PNG
                                    │
                                    ▼
                            image_compare
                          ┌────────────────┐
                          │  相似度计算     │
                          │  差异图生成     │
                          │  断言 >= 阈值   │
                          └────────────────┘
                                    │
                          report_writer ──► run_result.json
                          pytest-html  ──► report.html
```

---

## 下一步

1. 获取 Figma Access Token（个人设置 → Personal access tokens）
2. 在 Figma 设计稿中右键节点，复制 Node ID（格式：`数字:数字`）
3. 编辑 `.env` 填入 token、Node ID 和 `BASE_URL`
4. 运行：`pytest`

## 版本管理

- 当前项目版本: `1.1.3`（见 `VERSION`）
- 变更记录文件: `CHANGELOG.md`
- 从 `1.0.0` 开始，每次修改都要新增版本条目并说明：
  - 变更内容
  - 影响范围
  - 回归验证方式

## 后续演进规划

- 元素级精准定位与差异输出
- 多页面自动导航与爬取
- 实施路线见: `docs/ROADMAP.md`

## v1.1.0 新增配置（可选）

可在 `.env` 增加以下项控制多页面发现：

- `CRAWL_ENABLED=true`
- `CRAWL_MAX_DEPTH=2`
- `CRAWL_MAX_PAGES=20`
- `CRAWL_MAX_CLICKS_PER_PAGE=8`
- `CRAWL_SEED_PATHS=/,/products,/about`
- `CRAWL_CLICK_SELECTORS=a[href],button,[role='link'],[role='button']`
- `CRAWL_EXCLUDE_KEYWORDS=logout,signout,delete,remove`

结构化结果输出到：`reports/json/run_result.json`

## Pytest 与 Playwright 说明

本项目使用 **手写 `sync_playwright()`**（`WebCapture`），与 `pytest-playwright` 插件自带的 asyncio 机制会冲突。因此 `pytest.ini` 中已加入 `-p no:playwright` 禁用该插件。若你需要使用插件提供的 `page` fixture，可去掉该参数并改为异步用例或统一一种用法。

跨浏览器：未安装 Firefox 时，`test_homepage_firefox` 会自动跳过；安装命令：`playwright install firefox`。
