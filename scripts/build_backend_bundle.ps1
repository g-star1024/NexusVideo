<#
.SYNOPSIS
    Build the NexusVideo backend bundle (Windows, used by the installer CI runner).

.DESCRIPTION
    1. Create an isolated venv at resources/python_env
    2. Install backend/requirements-pack.txt (lightweight API deps ONLY; NO torch / NO comfyui)
    3. Copy backend source into resources/backend
       (local_server.py + routers + core + config + models + workflows ...)

    After this runs, the Rust side (paths.rs) can launch the backend from:
      - resources/python_env/python.exe    (python_executable)
      - resources/backend/local_server.py   (fastapi_entry)
    i.e. the two paths the Tauri app expects will be valid inside the installer.

    The backend is started with NEXUS_MANAGE_COMFYUI=false by the Rust side
    (see client/src-tauri/src/process_manager.rs), so it boots in pure
    auth mode without pulling up the ComfyUI inference engine.

.NOTES
    Run with PowerShell. Paths are resolved relative to this script's location,
    so it works regardless of the current working directory.
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
Write-Host "==============================================="

# ---------------------------------------------------------------
# 0. Preconditions
# ---------------------------------------------------------------
if (-not (Test-Path $ReqFile)) {
    throw "找不到 $ReqFile，请确认精简依赖清单已生成。"
}

# Resolve python interpreter (prefer `python`, fall back to `py`)
$PythonExe = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonExe = "py"
        Write-Host "[*] 未找到 python，回退使用 py"
    } else {
        throw "未找到 python 或 py，请先安装 Python >= 3.10 并加入 PATH。"
    }
}

# ---------------------------------------------------------------
# 1. Create venv at resources/python_env
# ---------------------------------------------------------------
Write-Host "[1/3] 创建 venv: $PythonEnv"
& $PythonExe -m venv $PythonEnv
if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败 (exit=$LASTEXITCODE)" }
if (-not (Test-Path $PipExe)) { throw "venv 内未找到 pip: $PipExe" }

# ---------------------------------------------------------------
# 2. Install lightweight API dependencies (NO torch / NO comfyui)
# ---------------------------------------------------------------
Write-Host "[2/3] 安装依赖: $ReqFile"
& $PipExe install --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) { throw "pip 升级失败" }

& $PipExe install -r $ReqFile --no-cache-dir
if ($LASTEXITCODE -ne 0) { throw "pip install 失败 (exit=$LASTEXITCODE)" }

# ---------------------------------------------------------------
# 3. Copy backend source into resources/backend
#    (local_server.py + routers + core + config + models + workflows ...)
# ---------------------------------------------------------------
Write-Host "[3/3] 拷贝后端源码: $BackendSrc -> $BackendDst"
if (-not (Test-Path $BackendDst)) {
    New-Item -ItemType Directory -Path $BackendDst | Out-Null
}

# /E 递归  /I 目标视为目录  /Y 覆盖
& xcopy $BackendSrc $BackendDst /E /I /Y
if ($LASTEXITCODE -ne 0) { throw "xcopy 后端源码失败 (exit=$LASTEXITCODE)" }

# 清理复制带来的 __pycache__，保持打包目录干净（不影响运行）
Get-ChildItem -Path $BackendDst -Recurse -Directory -Filter "__pycache__" `
    | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "==============================================="
Write-Host " 后端打包环境构建完成"
Write-Host "   venv   : $PythonEnv"
Write-Host "   backend: $BackendDst"
Write-Host "   启动   : $PythonEnvExe $BackendDst\local_server.py"
Write-Host "==============================================="
