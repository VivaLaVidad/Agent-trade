@echo off
REM ═══════════════════════════════════════════════════════════
REM  TradeStealth_Core — Windows 生产构建脚本 (PyArmor)
REM  将 src/core/ 加密混淆为 .pyd，其余模块原样复制至 dist/
REM ═══════════════════════════════════════════════════════════

setlocal enabledelayedexpansion
set "ROOT=%~dp0"
set "DIST=%ROOT%dist"
set "SRC=%ROOT%src"

echo [1/6] 检查 PyArmor ...
pip show pyarmor >nul 2>&1
if errorlevel 1 (
    echo      PyArmor 未安装，正在安装...
    pip install pyarmor
)

echo [2/6] 清理旧构建 ...
if exist "%DIST%" rmdir /s /q "%DIST%"
mkdir "%DIST%\src"

echo [3/6] 加密核心模块 src/core/ ...
pyarmor gen ^
    --output "%DIST%\src\core" ^
    --enable-jit ^
    "%SRC%\core\logger.py" "%SRC%\core\security.py"

if errorlevel 1 (
    echo [ERROR] PyArmor 加密失败，请检查许可证与 Python 版本兼容性。
    exit /b 1
)

echo [4/6] 复制非加密模块 ...
REM agents
xcopy "%SRC%\agents" "%DIST%\src\agents\" /e /i /q >nul
REM database
xcopy "%SRC%\database" "%DIST%\src\database\" /e /i /q >nul
REM rpa_engine
xcopy "%SRC%\rpa_engine" "%DIST%\src\rpa_engine\" /e /i /q >nul
REM monitor
xcopy "%SRC%\monitor" "%DIST%\src\monitor\" /e /i /q >nul
REM src/__init__.py
copy "%SRC%\__init__.py" "%DIST%\src\__init__.py" >nul

echo [5/6] 复制入口文件与配置 ...
copy "%ROOT%main.py"          "%DIST%\" >nul
copy "%ROOT%rpa_server.py"    "%DIST%\" >nul
copy "%ROOT%requirements.txt" "%DIST%\" >nul
if exist "%ROOT%.env.example" copy "%ROOT%.env.example" "%DIST%\" >nul

REM 创建运行时目录
mkdir "%DIST%\db"  2>nul
mkdir "%DIST%\logs" 2>nul

echo [6/6] 构建完成！
echo.
echo 产物目录: %DIST%
echo 加密范围: src/core/ (security.py, logger.py)
echo.
echo 部署步骤:
echo   1. 将 dist/ 复制到目标机器
echo   2. 在 dist/ 中创建 .env 并配置密钥
echo   3. pip install -r requirements.txt
echo   4. python rpa_server.py   (启动 RPA 进程)
echo   5. python main.py         (启动主服务)

endlocal
