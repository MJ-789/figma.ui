@echo off
chcp 65001 >nul
title Figma UI 对比测试

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║   Figma UI 对比测试  ·  一键启动             ║
echo  ╚══════════════════════════════════════════════╝
echo.

:: ── 切换到脚本所在目录（避免从其他位置调用时路径错误）
cd /d "%~dp0"

:: ── 激活虚拟环境 ──────────────────────────────────
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo [OK] 虚拟环境已激活
) else (
    echo [提示] 未找到 venv，使用系统 Python
    echo        如需创建请先运行 setup.bat
)
echo.

:: ── 参数说明 ──────────────────────────────────────
echo 运行模式（可按需修改下方 pytest 命令）：
echo   -k "chromium"        只跑 Chromium 像素 + 元素对比（默认）
echo   -k "element"         只跑元素属性对比
echo   -k "not crawl"       跳过爬取，只跑视觉 + 元素
echo   （去掉 -k 参数运行全部用例）
echo.

:: ── 运行测试 ──────────────────────────────────────
echo [1/2] 运行对比测试...
echo ──────────────────────────────────────────────────
pytest -k "chromium" --tb=short -q
set PYTEST_EXIT=%ERRORLEVEL%
echo ──────────────────────────────────────────────────
echo.

:: ── 打开报告 ──────────────────────────────────────
echo [2/2] 查找报告文件...
if exist "reports\report.html" (
    echo.
    echo  ══════════════════════════════════════════════
    if %PYTEST_EXIT% equ 0 (
        echo   ✅  测试全部通过！
    ) else (
        echo   ❌  存在差异，请查看报告了解详情
    )
    echo   📄  reports\report.html
    echo  ══════════════════════════════════════════════
    echo.
    start "" "reports\report.html"
) else (
    echo.
    echo  ⚠️  未找到可视化报告（reports\report.html）
    echo      请检查测试是否成功生成了 JSON 结果文件：
    echo        reports\json\run_result.json
    echo        reports\json\element_diff.json
    echo.
    echo  可手动生成报告：python -m src.html_reporter
)

echo.
pause
