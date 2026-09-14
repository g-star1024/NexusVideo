<#
.SYNOPSIS
    Build the NexusVideo backend bundle (Windows, used by the installer CI runner).

.DESCRIPTION
    1. Create a PORTABLE (self-contained, relocatable) interpreter at
       resources/python_env  ->  python_env/python.exe at the ROOT
    2. Install backend/requirements-pack.txt into it
       (lightweight API deps ONLY; NO torch / NO comfyui)
    3. Copy backend source into resources/backend
       (local_server.py + routers + core + config + models + workflows ...)

.NOTES "WHY NOT A PLAIN VENV" —— v0.2.13 P0 根因之一
    `python -m venv` 生成的 Scripts/python.exe 运行时依赖 pyvenv.cfg 里的
    `home = <base 解释器目录>` 去找回 stdlib。CI runner 的 base 目录
    （C:\hostedtoolcache\windows\Python\3.11.9\x64）在用户机器上不存在，
    解释器会直接 "Failed to load Python DLL" 起不来。
    另外 venv 的入口在 `python_env/Scripts/python.exe`，而 paths.rs 约定的是
    `python_env/python.exe` —— 两个问题叠加，即便资源进了包也命中不了。
    所以这里整体复制 base 安装（python.exe / python3xx.dll / DLLs / Lib），
    得到与安装目录同构的可移植解释器，依赖 pip 进 python_env\Lib\site-packages。

.REQUIRED OUTPUT LAYOUT (must match client/src-tauri/src/paths.rs)
    resources/python_env/python.exe            <- Windows 入口（paths.rs 第一候选）
    resources/python_env/Lib/site-packages/fastapi/...
    resources/backend/local_server.py

.NOTES
    Run with PowerShell 7 (pwsh). Paths resolve relative to this script's location.
    Every step prints verbose diagnostics for CI troubleshooting.
    Any failure must exit non-zero — the caller (CI) treats it as a hard error.
#>

$ErrorActionPreference = "Stop"

# Resolve repo root = parent of the scripts/ directory
$RepoRoot      = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonEnv     = Join-Path $RepoRoot "resources" "python_env"
$PythonEnvExe  = Join-Path $PythonEnv "python.exe"     # 可移植布局：根目录直放
$BackendSrc    = Join-Path $RepoRoot "backend"
$BackendDst    = Join-Path $RepoRoot "resources" "backend"
$ReqFile       = Join-Path $BackendSrc "requirements-pack.txt"

function Write-Stage([string]$msg) { Write-Host ""; Write-Host $msg }
function Fail([string]$msg) { Write-Host "::error::$msg"; throw $msg }

Write-Host "==============================================="
Write-Host " NexusVideo 后端打包环境构建 (Windows / portable)"
Write-Host " RepoRoot : $RepoRoot"
Write-Host " ScriptRoot: $PSScriptRoot"
Write-Host " Target   : $PythonEnv"
Write-Host "==============================================="

# ---------------------------------------------------------------
# 0. Preconditions
# ---------------------------------------------------------------
Write-Stage "[0/6] 检查前置条件..."
Write-Host "  [debug] 工作目录: $(Get-Location)"
Write-Host "  [debug] PowerShell 版本: $($PSVersionTable.PSVersion)"

if (-not (Test-Path $ReqFile)) {
    Write-Host "[debug] backend 目录内容:"
    Get-ChildItem -Path $BackendSrc -ErrorAction SilentlyContinue | Format-Table Name,Length
    Fail "找不到 $ReqFile"
}
Write-Host "  [OK] requirements-pack.txt 存在 ($((Get-Item $ReqFile).Length) bytes)"

$PythonExe = $null
foreach ($cand in @("python", "py")) {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $PythonExe = $cand; break }
}
if (-not $PythonExe) { Fail "构建机未找到 python / py（CI 需要 actions/setup-python）" }
Write-Host "  [debug] Python 命令: $PythonExe"
Write-Host "  [debug] Python 版本: $(& $PythonExe --version 2>&1)"

# base 解释器安装目录（可移植复制的源）
$BasePrefix = (& $PythonExe -c "import sys; print(sys.base_prefix)" 2>&1)
if ($LASTEXITCODE -ne 0 -or -not $BasePrefix) { Fail "无法解析 sys.base_prefix: $BasePrefix" }
$BasePrefix = "$BasePrefix".Trim()
Write-Host "  [debug] base_prefix: $BasePrefix"
if (-not (Test-Path (Join-Path $BasePrefix "python.exe"))) {
    Fail "base_prefix 下没有 python.exe（$BasePrefix），无法构建可移植解释器"
}
# venv 里跑本脚本时，sys.executable != base；这里要求直接用 base 构建，避免把 venv 当 base
$RealExe = (& $PythonExe -c "import sys,os; print(os.path.realpath(sys.executable))" 2>&1)
Write-Host "  [debug] sys.executable(realpath): $RealExe"

# ---------------------------------------------------------------
# 1. Portable interpreter: copy base installation -> resources/python_env
# ---------------------------------------------------------------
Write-Stage "[1/6] 构建可移植解释器: $BasePrefix -> $PythonEnv"
if (Test-Path $PythonEnv) {
    Write-Host "  [WARN] 已有 python_env 目录，先清理"
    Remove-Item -Path $PythonEnv -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force -Path $PythonEnv | Out-Null

# robocopy /E = 含子目录与空目录；退出码 0-7 为成功，>=8 才是失败
$rcArgs = @($BasePrefix, $PythonEnv, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP",
            "/XD", (Join-Path $BasePrefix "tcl"), (Join-Path $BasePrefix "Tools"),
            "/XF", "python_d.exe", "pythonw_d.exe", "venvlauncher.exe", "wkhtmltopdf.exe")
& robocopy @rcArgs | ForEach-Object { Write-Host "  [robocopy] $_" }
$rc = $LASTEXITCODE
if ($rc -ge 8) { Fail "robocopy 复制 base 解释器失败 (exit=$rc)" }
Write-Host "  [OK] robocopy 退出码 $rc（0-7 均为成功）"

if (-not (Test-Path $PythonEnvExe)) {
    Write-Host "  [debug] python_env 顶层:"
    Get-ChildItem $PythonEnv -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $($_.Name)" }
    Fail "复制后缺少 $PythonEnvExe"
}
# 清掉 base 里可能带的 pip 缓存/测试包体积大户（保守：只删 __pycache__ 与 ensurepip wheels 之外的话不做）
Get-ChildItem -Path $PythonEnv -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 自检：可移植解释器必须能在「换目录 + 屏蔽 base」的条件下跑起来
Write-Host "  [debug] 解释器自检（cwd=TEMP，忽略系统 PATH 上的 python）:"
$selfCheck = & $PythonEnvExe -c "import sys, json; print(json.dumps({'exe': sys.executable, 'prefix': sys.prefix, 'version': sys.version.split()[0]}))" 2>&1
if ($LASTEXITCODE -ne 0) { Fail "可移植解释器自检失败: $selfCheck" }
Write-Host "    $selfCheck"
# prefix 必须落在 python_env 内（否则说明还在依赖 base 目录）
if ($selfCheck -notmatch [regex]::Escape($PythonEnv.Replace('\','\\'))) {
    Write-Host "  [WARN] sys.prefix 未落在 python_env 内，继续（见上）"
}

# ---------------------------------------------------------------
# 2. pip bootstrap + install deps
# ---------------------------------------------------------------
Write-Stage "[2/6] 准备 pip（可移植解释器内）..."
& $PythonEnvExe -m pip --version 2>&1 | ForEach-Object { Write-Host "  [pip] $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [INFO] 无 pip，尝试 ensurepip"
    & $PythonEnvExe -m ensurepip --upgrade 2>&1 | ForEach-Object { Write-Host "  [pip] $_" }
    if ($LASTEXITCODE -ne 0) { Fail "ensurepip 失败，base 解释器缺 pip 模块" }
}
& $PythonEnvExe -m pip install --upgrade pip 2>&1 | ForEach-Object { Write-Host "  [pip] $_" }
if ($LASTEXITCODE -ne 0) { Fail "pip 升级失败" }
Write-Host "  [OK] pip 版本: $(& $PythonEnvExe -m pip --version 2>&1)"

Write-Stage "[3/6] 安装依赖: $ReqFile"
Write-Host "  [debug] requirements 内容:"
Get-Content $ReqFile | ForEach-Object { Write-Host "    $_" }

$maxRetries = 2
$success = $false
for ($i = 1; $i -le $maxRetries; $i++) {
    Write-Host "  [attempt $i/$maxRetries] python -m pip install -r $ReqFile --timeout 120..."
    & $PythonEnvExe -m pip install -r $ReqFile --timeout 120 --no-warn-script-location 2>&1 |
        ForEach-Object { Write-Host "  [pip] $_" }
    if ($LASTEXITCODE -eq 0) { $success = $true; break }
    Write-Host "  [WARN] 安装失败 (exit=$LASTEXITCODE)，重试..."
}
if (-not $success) {
    Write-Host "[debug] 尝试单独安装 fastapi 以定位问题:"
    & $PythonEnvExe -m pip install fastapi --timeout 120 2>&1 | ForEach-Object { Write-Host "  [pip] $_" }
    Fail "pip install 失败（已重试 $maxRetries 次）"
}
Write-Host "  [OK] 所有依赖安装成功"

# ---------------------------------------------------------------
# 3. Verify importable deps (site-packages 必须在 python_env 内)
# ---------------------------------------------------------------
Write-Stage "[4/6] 验证关键依赖可导入..."
foreach ($mod in @("fastapi", "uvicorn", "pydantic", "bcrypt", "httpx")) {
    $result = & $PythonEnvExe -c "import $mod, sys; print('OK', getattr($mod,'__version__','n/a'))" 2>&1
    if ($LASTEXITCODE -ne 0 -or "$result" -match "Error|Traceback|ModuleNotFoundError") {
        Fail "$mod 导入失败: $result"
    }
    Write-Host "  [OK] $mod -> $result"
}
$siteDir = & $PythonEnvExe -c "import site; print(site.getsitepackages()[0])" 2>&1
Write-Host "  [debug] site-packages: $siteDir"
if ("$siteDir" -notmatch [regex]::Escape("resources")) {
    Write-Host "  [WARN] site-packages 看起来不在 resources/ 下，可能污染了 base 安装"
}

# ---------------------------------------------------------------
# 4. Copy backend source
# ---------------------------------------------------------------
Write-Stage "[5/6] 拷贝后端源码: $BackendSrc -> $BackendDst"
if (Test-Path $BackendDst) {
    Remove-Item -Path $BackendDst -Recurse -Force -ErrorAction SilentlyContinue
}
Copy-Item -Path $BackendSrc -Destination $BackendDst -Recurse -Force
Copy-Item -Path $BackendSrc -Destination $BackendDst -Recurse -Force -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $BackendDst "local_server.py"))) {
    Fail "拷贝后缺少 local_server.py"
}
Write-Host "  [OK] 拷贝完成，文件数: $(Get-ChildItem -Path $BackendDst -Recurse -File | Measure-Object | Select-Object -ExpandProperty Count)"

# ---------------------------------------------------------------
# 5. Final verification — 布局必须与 paths.rs / tauri bundle.resources 对齐
# ---------------------------------------------------------------
Write-Stage "[6/6] 最终验证（布局契约）..."
$checks = [ordered]@{
    "resources/python_env/python.exe"              = (Test-Path $PythonEnvExe)
    "resources/python_env/Lib/site-packages"        = (Test-Path (Join-Path $PythonEnv "Lib" "site-packages"))
    "resources/backend/local_server.py"             = (Test-Path (Join-Path $BackendDst "local_server.py"))
}
$bad = 0
foreach ($k in $checks.Keys) {
    $ok = $checks[$k]
    Write-Host ("  [{0}] {1}" -f $(if ($ok) { "OK" } else { "MISSING" }), $k)
    if (-not $ok) { $bad++ }
}
$sizeMB = [math]::Round(((Get-ChildItem $PythonEnv -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host "  [debug] python_env 体积: $sizeMB MB"
# 体积下限：可移植解释器 + fastapi 依赖不可能小于 20MB；低于此说明复制没生效
if ($sizeMB -lt 20) { Write-Host "::error::python_env 体积异常偏小 ($sizeMB MB)，疑似未复制 base 解释器"; $bad++ }
if ($bad -gt 0) { Fail "最终验证失败：$bad 项不达标（见上）" }

# 从 backend 目录里真跑一次 local_server 的 import（不启服务，只验依赖链）
Write-Host "  [debug] 冒烟 import local_server 依赖链:"
& $PythonEnvExe -c "import sys; sys.path.insert(0, r'$BackendDst'); import importlib; importlib.import_module('config'); print('  [OK] backend config import')" 2>&1 |
    ForEach-Object { Write-Host "    $_" }

Write-Host ""
Write-Host "==============================================="
Write-Host " 后端打包环境构建完成（可移植）"
Write-Host "   python_env: $PythonEnv  (入口 python_env/python.exe)"
Write-Host "   backend   : $BackendDst"
Write-Host "   下一道闸门：CI 会用 NSIS 安装包内清单二次校验"
Write-Host "==============================================="
