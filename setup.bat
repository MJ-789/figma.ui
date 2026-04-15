@echo off
chcp 65001 >nul
echo ========================================
echo   Figma UI自动化测试 - Windows快速安装
echo ========================================
echo.

:: 检查Python是否安装
echo [1/10] 检查Python环境...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ 错误: 未找到Python
    echo.
    echo 请先安装Python:
    echo 1. 访问 https://www.python.org/downloads/
    echo 2. 下载并安装
    echo 3. 安装时勾选 "Add Python to PATH"
    pause
    exit /b 1
)

python --version
echo    ✓ Python已安装
echo.

:: 创建项目结构
echo [2/10] 创建项目目录结构...
mkdir config 2>nul
mkdir src 2>nul
mkdir tests 2>nul
mkdir reports\screenshots\figma 2>nul
mkdir reports\screenshots\web 2>nul
mkdir reports\screenshots\site 2>nul
mkdir reports\html 2>nul
mkdir reports\images 2>nul
mkdir reports\json 2>nul
mkdir knowledge 2>nul

:: 创建__init__.py文件
type nul > config\__init__.py
type nul > src\__init__.py
type nul > tests\__init__.py

echo    ✓ 目录结构创建完成
echo.

:: 创建虚拟环境
echo [3/10] 创建Python虚拟环境...
if exist "venv\" (
    echo    ⚠️  虚拟环境已存在，跳过创建
) else (
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ❌ 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo    ✓ 虚拟环境创建成功
)
echo.

:: 激活虚拟环境
echo [4/10] 激活虚拟环境...
call venv\Scripts\activate.bat
if %errorlevel% neq 0 (
    echo ❌ 虚拟环境激活失败
    pause
    exit /b 1
)
echo    ✓ 虚拟环境已激活
echo.

:: 创建requirements.txt
echo [5/10] 创建requirements.txt...
(
echo playwright==1.41.0
echo pytest==7.4.3
echo pytest-playwright==0.4.4
echo pytest-html==4.1.1
echo pytest-xdist==3.5.0
echo requests==2.31.0
echo Pillow==10.2.0
echo opencv-python==4.9.0.80
echo numpy==1.26.3
echo python-dotenv==1.0.0
echo jinja2==3.1.3
echo colorama==0.4.6
) > requirements.txt
echo    ✓ requirements.txt创建完成
echo.

:: 升级pip
echo [6/10] 升级pip...
python -m pip install --upgrade pip -q
echo    ✓ pip已升级
echo.

:: 安装依赖
echo [7/10] 安装Python依赖包...
echo    (这可能需要3-5分钟，请耐心等待...)
pip install -r requirements.txt -q
if %errorlevel% neq 0 (
    echo ❌ 依赖安装失败
    echo    尝试重新运行: pip install -r requirements.txt
    pause
    exit /b 1
)
echo    ✓ Python依赖安装完成
echo.

:: 安装Playwright浏览器
echo [8/10] 安装Playwright浏览器...
echo    (这可能需要2-3分钟...)
playwright install chromium
if %errorlevel% neq 0 (
    echo ❌ Playwright安装失败
    echo    尝试手动安装: playwright install chromium
    pause
    exit /b 1
)
echo    ✓ Playwright浏览器安装完成
echo.

:: 创建配置文件
echo [9/10] 创建配置文件...

:: 创建.env文件
(
echo # Figma配置
echo FIGMA_ACCESS_TOKEN=your_token_here
echo FIGMA_FILE_KEY=your_file_key_here
echo.
echo # 网站配置
echo BASE_URL=https://your-website.com
echo.
echo # 测试配置
echo SIMILARITY_THRESHOLD=95
echo DEFAULT_BROWSER=chromium
echo HEADLESS=true
) > .env

echo    ✓ .env文件已创建
echo.

:: 创建pytest.ini
(
echo [pytest]
echo testpaths = tests
echo python_files = test_*.py
echo python_classes = Test*
echo python_functions = test_*
echo.
echo addopts =
echo     -v
echo     -s
echo     --html=reports/html/report.html
echo     --self-contained-html
echo.
echo markers =
echo     desktop: 桌面端测试
echo     mobile: 移动端测试
echo     cross_browser: 跨浏览器测试
) > pytest.ini

echo    ✓ pytest.ini已创建
echo.

:: 创建.gitignore
(
echo # Python
echo __pycache__/
echo *.py[cod]
echo .Python
echo venv/
echo env/
echo.
echo # 环境变量
echo .env
echo.
echo # 测试输出
echo screenshots/
echo reports/
echo recordings/
echo.
echo # IDE
echo .idea/
echo .vscode/
echo.
echo # OS
echo Thumbs.db
) > .gitignore

echo    ✓ .gitignore已创建
echo.

:: 创建README
echo [10/10] 创建README.md...
(
echo # Figma UI自动化测试项目
echo.
echo ## 快速开始
echo.
echo 1. 编辑 `.env` 文件，填入Figma配置
echo 2. 复制代码文件到对应目录
echo 3. 运行测试: `pytest`
echo.
echo ## 目录结构
echo.
echo ```
echo figma-ui-automation/
echo ├── config/       # 配置文件
echo ├── src/          # 源代码
echo ├── tests/        # 测试文件
echo ├── screenshots/  # 截图
echo └── reports/      # 报告
echo ```
echo.
echo ## 下一步
echo.
echo 1. 获取Figma Access Token
echo 2. 获取Figma File Key
echo 3. 编辑.env文件
echo 4. 复制代码文件
echo 5. 运行: pytest
) > README.md

echo    ✓ README.md已创建
echo.

:: 完成
echo ========================================
echo   ✅ 安装完成！
echo ========================================
echo.
echo 📋 下一步操作:
echo.
echo 1️⃣  获取Figma配置:
echo    - Access Token: https://www.figma.com → Settings → Personal Access Tokens
echo    - File Key: 从Figma文件URL中获取
echo.
echo 2️⃣  编辑 .env 文件:
echo    - 填入FIGMA_ACCESS_TOKEN
echo    - 填入FIGMA_FILE_KEY
echo    - 填入BASE_URL (你的网站地址)
echo.
echo 3️⃣  复制代码文件到项目:
echo    - config\config.py
echo    - src\figma_client.py
echo    - src\web_capture.py
echo    - src\image_compare.py
echo    - tests\test_desktop.py
echo.
echo 4️⃣  测试Figma连接:
echo    python src\figma_client.py
echo.
echo 5️⃣  运行测试:
echo    pytest
echo.
echo 💡 提示:
echo    - 使用PyCharm打开项目获得最佳体验
echo    - 虚拟环境已激活，可以直接运行命令
echo    - 如需重新激活: venv\Scripts\activate
echo.
echo 🎉 祝测试顺利！
echo.
pause