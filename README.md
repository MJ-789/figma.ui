# Figma UI自动化测试项目

## 快速开始

1. 编辑 `.env` 文件，填入Figma配置
2. 复制代码文件到对应目录

## 目录结构

```
figma-ui-automation/
├── config/       # 配置文件
├── src/          # 源代码
├── screenshots/  # 截图
└── reports/      # 报告
```

## 下一步

1. 获取Figma Access Token
2. 获取Figma File Key
3. 编辑.env文件
4. 复制代码文件
5. 运行: pytest

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
