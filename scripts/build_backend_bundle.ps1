<#
.SYNOPSIS
    Build the NexusVideo backend bundle (Windows, used by the installer CI runner).

.DESCRIPTION
    1. Create an isolated venv at resources/python_env
    2. Install backend/requirements-pack.txt (lightweight API deps ONLY; NO torch / NO comfyui)
    3. Copy backend source into resources/backend
       (local_server.py + routers + core + config + models + workflows ...)

.NOTES
    Run with PowerShell. Paths are resolved relative to this script's location.
    Every step prints verbose diagnostics for CI troubleshooting.
#>

$ErrorActionPreference = "Stop"

# Resolve repo root = parent of the scripts/ directory
$RepoRoot      = Resolve-Path (Join-Path $PSScriptRoot "..")
$PythonEnv     = Join-Path $RepoRoot "resources" "python_env"
$PythonEnvExe  = Join-Path $PythonEnv "Scripts" "python.exe"
$PipExe        = Join-Path $PythonEnv "Scripts" "pip.exe"
$BackendSrc    = Join-Path $RepoRoot "backend"
$BackendDst    = Join-Path $RepoRoot "resources" "backend"
$ReqFile       = Join-Path $BackendSrc "requirements-pack.txt"

Write-Host "==============================================="
Write-Host " NexusVideo 后端打包环境构建 (Windows)"
Write-Host " RepoRoot : $RepoRoot"
Write-Host " ScriptRoot: $PSScriptRoot"
Write-Host "==============================================="

# ---------------------------------------------------------------
# 0. Preconditions — 详细调试
# ---------------------------------------------------------------
Write-Host "[0/6] 检查前置条件..."
Write-Host "  [debug] 工作目录: $(Get-Location)"
Write-Host "  [debug] PowerShell 版本: $($PSVersionTable.PSVersion)"

if (-not (Test-Path $ReqFile)) {
    Write-Host "::error::找不到 $ReqFile"
    Write-Host "[debug] backend 目录内容:"
    Get-ChildItem -Path $BackendSrc -ErrorAction SilentlyContinue | Format-Table Name,Length
    throw "找不到 $ReqFile"
}
Write-Host "  [OK] requirements-pack.txt 存在 ($(Get-Item $ReqFile).Length bytes)"

$PythonExe = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonExe = "py"
        Write-Host "  [WARN] 未找到 python，回退使用 py"
    } else {
        throw "未找到 python 或 py"
    }
}
Write-Host "  [debug] Python 命令: $PythonExe"
Write-Host "  [debug] Python 路径: $(Get-Command $PythonExe | Select-Object -ExpandProperty Source)"
Write-Host "  [debug] Python 版本: $(& $PythonExe --version 2>&1)"

# 检查 venv 模块是否可用
$VenvOk = & $PythonExe -c "import venv; print('venv OK')" 2>&1
Write-Host "  [debug] venv 模块: $VenvOk"

# ---------------------------------------------------------------
# 1. Create venv
# ---------------------------------------------------------------
Write-Host ""
Write-Host "[1/6] 创建 venv: $PythonEnv"
if (Test-Path $PythonEnv) {
    Write-Host "  [WARN] 已有 venv 目录，先清理"
    Remove-Item -Path $PythonEnv -Recurse -Force -ErrorAction SilentlyContinue
}
& $PythonExe -m venv $PythonEnv 2>&1 | ForEach-Object { Write-Host "  [venv] $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Host "::error::创建 venv 失败 (exit=$LASTEXITCODE)"
    Write-Host "[debug] Python -m venv --help 输出:"
    & $PythonExe -m venv --help 2>&1 | Out-String | Write-Host
    throw "创建 venv 失败"
}
Write-Host "  [OK] venv 创建成功"

# 验证 venv 内有 python 和 pip
$HasPython = Test-Path $PythonEnvExe
$HasPip    = Test-Path $PipExe
Write-Host "  [debug] venv python.exe 存在: $HasPython"
Write-Host "  [debug] venv pip.exe 存在:    $HasPip"
if (-not $HasPython -or -not $HasPip) {
    Write-Host "::error::venv 内缺少 python.exe 或 pip.exe"
    Write-Host "[debug] venv 目录结构:"
    Get-ChildItem -Path $PythonEnv -Recurse -ErrorAction SilentlyContinue | Select-Object FullName | Format-Table
    throw "venv 不完整"
}

# ---------------------------------------------------------------
# 2. Upgrade pip + install deps（带重试和超时）
# ---------------------------------------------------------------
Write-Host ""
Write-Host "[2/6] 升级 pip（Windows 必须用 python -m pip，不能直接用 pip.exe）..."
& $PythonEnvExe -m pip install --upgrade pip 2>&1 | ForEach-Object { Write-Host "  [pip] $_" }
if ($LASTEXITCODE -ne 0) { throw "pip 升级失败" }
Write-Host "  [OK] pip 版本: $(& $PythonEnvExe -m pip --version 2>&1)"

Write-Host ""
Write-Host "[3/6] 安装依赖: $ReqFile"
Write-Host "  [debug] requirements 内容:"
Get-Content $ReqFile | ForEach-Object { Write-Host "    $_" }

$maxRetries = 2
$success = $false
for ($i = 1; $i -le $maxRetries; $i++) {
    Write-Host "  [attempt $i/$maxRetries] python -m pip install -r $ReqFile --timeout 120..."
    & $PythonEnvExe -m pip install -r $ReqFile --timeout 120 2>&1 | ForEach-Object { Write-Host "  [pip] $_" }
    if ($LASTEXITCODE -eq 0) {
        $success = $true
        break
    }
    Write-Host "  [WARN] 安装失败 (exit=$LASTEXITCODE)，重试..."
}
if (-not $success) {
    Write-Host "::error::pip install 失败（已重试 $maxRetries 次）"
    Write-Host "[debug] 尝试单独安装 fastapi 以定位问题:"
    & $PythonEnvExe -m pip install fastapi --timeout 120 2>&1 | ForEach-Object { Write-Host "  [pip] $_" }
    throw "pip install 失败"
}
Write-Host "  [OK] 所有依赖安装成功"

# 验证关键依赖可导入
Write-Host ""
Write-Host "[4/6] 验证关键依赖可导入..."
foreach ($mod in @("fastapi", "uvicorn", "pydantic", "bcrypt", "httpx")) {
    $result = & $PythonEnvExe -c "import $mod; print('OK')" 2>&1
    if ($result -match "Error|Traceback|ModuleNotFoundError") {
        Write-Host "::error::$mod 导入失败: $result"
        throw "$mod 导入失败"
    }
    Write-Host "  [OK] $mod"
}

# ---------------------------------------------------------------
# 3. Copy backend source
# ---------------------------------------------------------------
Write-Host ""
Write-Host "[5/6] 拷贝后端源码: $BackendSrc -> $BackendDst"
if (Test-Path $BackendDst) {
    Remove-Item -Path $BackendDst -Recurse -Force -ErrorAction SilentlyContinue
}
Copy-Item -Path $BackendSrc -Destination $BackendDst -Recurse -Force -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $BackendDst "local_server.py"))) {
    Write-Host "::error::拷贝后缺少 local_server.py"
    throw "拷贝失败"
}
Write-Host "  [OK] 拷贝完成，文件数: $(Get-ChildItem -Path $BackendDst -Recurse -File | Measure-Object | Select-Object -ExpandProperty Count)"

# ---------------------------------------------------------------
# 4. Verify
# ---------------------------------------------------------------
Write-Host ""
Write-Host "[6/6] 最终验证..."
Write-Host "  python.exe: $(Test-Path $PythonEnvExe)"
Write-Host "  local_server.py: $(Test-Path (Join-Path $BackendDst 'local_server.py'))"
$testResult = & $PythonEnvExe -c "import fastapi; print(fastapi.__version__)" 2>&1
Write-Host "  fastapi 版本: $testResult"
Write-Host ""
Write-Host "==============================================="
Write-Host " 后端打包环境构建完成"
Write-Host "   venv   : $PythonEnv"
Write-Host "   backend: $BackendDst"
Write-Host "==============================================="
