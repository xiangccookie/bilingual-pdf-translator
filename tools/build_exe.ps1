# 本地打包脚本：生成 Windows 目录版程序，并压缩成 zip
#
# 用法（在本仓库根目录）：
#   .\tools\build_exe.ps1
#
# 前置：pip install -r requirements.txt pyinstaller
# 产物：dist\BilingualPdfTranslator-windows.zip
#
# 三个关键决定（都是实测踩出来的，改动前请看 README 的「已知问题」）：
#   1) 用 --onedir 而不是 --onefile。
#      单文件版每次运行都要把 ~200 MB 解包到临时目录，实测直接失败：
#        [PYI-xxxx:ERROR] Could not create temporary directory!
#      目录版不需要解包，启动快，也不容易被杀毒软件误伤。
#   2) 用 --console + 运行时钩子隐藏黑窗，而不是 --windowed。
#      实测同一份源码：--console 版能跑，--windowed 版一启动就退出（连日志都没写出来）。
#      控制台版 stdout 一切正常；钩子在启动瞬间 ShowWindow(SW_HIDE) 把黑窗藏掉。
#   3) 模型（67.5 MB）用 --add-data 一起打包 —— 目标机器无需联网、无需 Python。

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---- 1) 确保模型到位
$model = Join-Path $root "assets\translate-en_zh.argosmodel"
if (-not (Test-Path $model)) {
    Write-Host "[1/4] 本地没有模型，开始下载（约 67.5 MB）…"
    New-Item -ItemType Directory -Force -Path (Join-Path $root "assets") | Out-Null
    Invoke-WebRequest -Uri "https://argos-net.com/v1/translate-en_zh-1_9.argosmodel" -OutFile $model -TimeoutSec 900
} else {
    Write-Host "[1/4] 已有模型：$model"
}
$size = (Get-Item $model).Length
Write-Host "        模型大小 $size 字节"
if ($size -lt 50MB) { throw "模型文件不完整（小于 50 MB），请删除后重试" }

# ---- 2) 清理旧产物
Write-Host "[2/4] 清理旧产物…"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item -Force *.spec -ErrorAction SilentlyContinue

# ---- 3) 打包（参数放在数组里，避免多行续行符出错）
Write-Host "[3/4] 开始打包（约 1-2 分钟）…"
$pyiArgs = @(
    "--onedir", "--console",
    "--name", "BilingualPdfTranslator",
    "--paths", "src",
    "--add-data", "assets/translate-en_zh.argosmodel;assets",
    "--add-data", "assets/glossary;assets/glossary",
    "--runtime-hook", "tools/runtime_hook.py",
    "--collect-submodules", "reportlab",
    "--collect-data", "reportlab",
    "--collect-submodules", "ctranslate2",
    "--collect-submodules", "sentencepiece",
    "--exclude-module", "argostranslate",
    "--exclude-module", "torch",
    "--exclude-module", "spacy",
    "--exclude-module", "stanza",
    "--exclude-module", "minisbd",
    "--exclude-module", "thinc",
    "--exclude-module", "onnxruntime",
    "--exclude-module", "weasel",
    "--exclude-module", "typer",
    "src/bi_app.py"
)
& pyinstaller @pyiArgs

$appDir = Join-Path $root "dist\BilingualPdfTranslator"
$exe = Join-Path $appDir "BilingualPdfTranslator.exe"
if (-not (Test-Path $exe)) { throw "打包失败：没有生成 exe" }

# ---- 4) 冒烟测试 + 压缩
Write-Host "[4/4] 冒烟测试（造一个 2 页 PDF，用本地离线引擎跑一遍）…"
$sample = Join-Path $root "sample.pdf"
$pyCode = "import pymupdf; d=pymupdf.open(); p=d.new_page(); p.insert_text((72,100),'The tumor microenvironment facilitates immune evasion. This is a test sentence about cancer.', fontsize=11); d.save(r'" + $sample + "')"
python -c $pyCode
$outDir = Join-Path $root "out"
Remove-Item -Recurse -Force $outDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
# 注意：这里**不能用 Start-Process -Wait**（实测会卡住不返回），直接调用即可
& $exe --batch-out $outDir --backend local --naming orig $sample
$code = $LASTEXITCODE
Get-ChildItem $outDir -Filter *.pdf | Format-Table Name, Length
if ($code -ne 0) { throw "自检失败，退出码 $code" }
$made = Get-ChildItem $outDir -Filter *.pdf
if (-not $made) { throw "自检失败：没有生成任何 PDF" }
Write-Host "自检通过（生成 $($made[0].Name)）"

$zip = Join-Path $root "dist\BilingualPdfTranslator-windows.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path $appDir -DestinationPath $zip -CompressionLevel Optimal
$zs = (Get-Item $zip).Length
$raw = (Get-ChildItem $appDir -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ""
Write-Host ("完成：" + $zip)
Write-Host ("压缩包 " + [math]::Round($zs / 1MB, 1) + " MB，解压后 " + [math]::Round($raw / 1MB, 1) + " MB")
Write-Host "用法：解压后双击 BilingualPdfTranslator.exe，无需 Python、无需联网、无需配置环境变量。"
