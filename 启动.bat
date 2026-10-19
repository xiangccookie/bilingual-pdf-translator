@echo off
setlocal
title 双语 PDF 翻译器 - 启动器

rem ============================================================
rem  用「Code -> Download ZIP」下载源码的人，双击本文件即可运行。
rem  第一次运行会自动从 GitHub Releases 下载程序包（约 197 MB，仅一次），
rem  之后就直接启动，不再联网。
rem ============================================================

set "APP=%LOCALAPPDATA%\BilingualPdfTranslator\app"
set "EXE=%APP%\BilingualPdfTranslator\BilingualPdfTranslator.exe"
set "URL=https://github.com/xiangccookie/bilingual-pdf-translator/releases/latest/download/BilingualPdfTranslator-windows.zip"
set "ZIP=%TEMP%\bilingual_pdf_translator.zip"

if exist "%EXE%" goto run

echo.
echo ================================================================
echo   双语 PDF 翻译器  首次运行准备
echo ----------------------------------------------------------------
echo   需要下载程序包（约 197 MB，含 Python 运行时与离线模型）。
echo   只需下载一次，之后启动不再联网。
echo   存放位置：%APP%
echo ================================================================
echo.

if not exist "%APP%" mkdir "%APP%" 2>nul

where curl >nul 2>nul
if errorlevel 1 (
    echo 正在用 PowerShell 下载，请稍候（几分钟）...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%URL%' -OutFile '%ZIP%' -UseBasicParsing; exit 0 } catch { Write-Host ('下载失败：' + $_.Exception.Message); exit 1 }"
) else (
    curl -L --fail --progress-bar -o "%ZIP%" "%URL%"
)
if errorlevel 1 goto fail
if not exist "%ZIP%" goto fail

echo.
echo 正在解压...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%APP%' -Force -ErrorAction Stop; exit 0 } catch { Write-Host ('解压失败：' + $_.Exception.Message); exit 1 }"
if errorlevel 1 goto fail
del "%ZIP%" >nul 2>nul

if not exist "%EXE%" goto fail
echo 准备完成。

:run
echo 正在启动双语 PDF 翻译器 ...
start "" "%EXE%"
exit /b 0

:fail
echo.
echo [失败] 下载或解压没有完成。
echo.
echo   办法一：检查能不能打开下面的网址（浏览器能开，就说明网络没问题）
echo           https://github.com/xiangccookie/bilingual-pdf-translator/releases/latest
echo   办法二：手动下载 BilingualPdfTranslator-windows.zip，
echo           解压后把整个 BilingualPdfTranslator 文件夹放到：
echo           %APP%
echo   办法三：如果你装了 Python 3.8 以上，可以直接用源码跑：
echo           pip install -r requirements.txt
echo           python tools\install_argos.py
echo           python src\bi_app.py
echo.
pause
exit /b 1
