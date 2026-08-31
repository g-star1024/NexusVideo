<# ============================================================================
# NexusVideo — Windows 打包脚本 (build.ps1)
# 架构师：封易安 (client-tauri-dev)
# 说明：在 Windows 上执行，产出 .exe 安装包 (NSIS)
#
# 使用方式:
#   powershell -ExecutionPolicy Bypass -File scripts/build.ps1
#
# 前置依赖:
#   - Rust toolchain (rustc >= 1.77, MSVC toolchain)
#   - @tauri-apps/cli (npm install -g @tauri-apps/cli@latest)
#   - Node.js >= 18 + npm
#   - Visual Studio Build Tools 2022 (C++ 桌面开发工作负载)
#   - Python 3.10+ (Rust 编译依赖)
#
# 输出路径:
#   client/src-tauri/target/release/bundle/nsis/NexusVideo_0.1.0_x64-setup.exe
#
# 安装包体积估算:
#   Tauri app 框架 (~30MB) + 前端资源 (~5MB) + Rust 二进制 (~15MB)
#   + WebView2 引导程序 (~5MB, 仅首次启动时下载)
#   + ComfyUI 引擎 (首次启动时下载，不嵌入) = 约 50MB
#   目标: < 100MB
#
# 签名说明:
#   Windows 需要 EV Code Signing Certificate (.pfx 文件)
#   推荐: DigiCert / GlobalSign EV Code Signing
#   脚本末尾包含 signtool 签名步骤（需要 CERT_PASSWORD 环境变量）
# ============================================================================

$ErrorActionPreference = "Stop"

# ---- 路径 ----
$ScriptDir = Split-Path -Parent $MyInvokeCommand
$ProjectDir = Split-Path -Parent $ScriptDir
$ClientDir = Join-Path $ProjectDir "client"
$TauriDir = Join-Path $ClientDir "src-tauri"

Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " NexusVideo — Windows 打包" -ForegroundColor Cyan
Write-Host " 项目根目录: $ProjectDir" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

# ---- 步骤 1: 安装前端依赖 ----
Write-Host ""
Write-Host "[1/5] 安装前端依赖..." -ForegroundColor Yellow
Set-Location $ClientDir
if (-not (Test-Path "node_modules")) {
    npm ci
} else {
    Write-Host "  ✓ node_modules 已存在，跳过"
}

# ---- 步骤 2: 构建前端 ----
Write-Host ""
Write-Host "[2/5] 构建前端 (npm run build)..." -ForegroundColor Yellow
npm run build
Write-Host "  ✓ 前端构建完成，产物: $ClientDir\dist\"

# ---- 步骤 3: 检测 Rust 工具链 ----
Write-Host ""
Write-Host "[3/5] 检测 Rust 工具链..." -ForegroundColor Yellow
try {
    $env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-msvc"
    $rustVersion = rustc --version
    Write-Host "  ✓ Rust: $rustVersion"
} catch {
    Write-Host "  ✗ 未找到 Rust，请先安装: https://rustup.rs/" -ForegroundColor Red
    exit 1
}

try {
    $tauriVersion = tauri --version
    Write-Host "  ✓ tauri CLI: $tauriVersion"
} catch {
    Write-Host "  ⚠ 未找到 tauri CLI，尝试通过 npm 全局安装..."
    npm install -g @tauri-apps/cli@latest
}

# ---- 步骤 4: 清理旧构建产物 ----
Write-Host ""
Write-Host "[4/5] 清理旧构建产物..." -ForegroundColor Yellow
Set-Location $TauriDir
cargo clean
Write-Host "  ✓ 清理完成"

# ---- 步骤 5: Tauri 打包 (NSIS 安装包) ----
Write-Host ""
Write-Host "[5/5] Tauri 打包构建 (NSIS installer)..." -ForegroundColor Yellow
tauri build
Write-Host "  ✓ Windows 打包完成"
Write-Host ""
Write-Host "  📦 输出文件:" -ForegroundColor Green
Write-Host "    target\release\bundle\nsis\NexusVideo_*_x64-setup.exe" -ForegroundColor Green
Write-Host "    target\release\NexusVideo.exe" -ForegroundColor Green

# ---- 步骤 6: 代码签名 (Authenticode) ----
Write-Host ""
Write-Host "[额外] Windows 代码签名 (Authenticode)..." -ForegroundColor Yellow

$EXE_PATH = Join-Path $TauriDir "target\release\bundle\nsis\*.exe"
$EXE_FILES = Get-ChildItem -Path $EXE_PATH -File

if ($EXE_FILES.Count -gt 0) {
    $EXE_FULL = $EXE_FILES[0].FullName

    # 检查 signtool 是否可用
    $signtoolFound = $false
    $vsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vsWhere) {
        $vsInstall = & $vsWhere -latest -property installationPath
        $signtoolPath = Join-Path $vsInstall "Common7\Tools\signtool.exe"
        if (Test-Path $signtoolPath) {
            $signtoolFound = $true
        }
    }

    if ($signtoolFound -and $env:CERT_PASSWORD) {
        $pfxPath = $env:CERT_PFX_PATH
        if (Test-Path $pfxPath) {
            Write-Host "  使用证书: $pfxPath"
            Write-Host "  签名中..."
            & $signtoolPath sign /f "$pfxPath" /p "$env:CERT_PASSWORD" `
                /tr "http://timestamp.digicert.com" /td sha256 /fd sha256 `
                $EXE_FULL
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  ✓ 代码签名完成" -ForegroundColor Green
            } else {
                Write-Host "  ✗ 代码签名失败" -ForegroundColor Red
            }
        } else {
            Write-Host "  ⚠ 未找到证书文件: $pfxPath"
            Write-Host "    请设置 CERT_PFX_PATH 环境变量指向 .pfx 文件"
        }
    } else {
        Write-Host "  ⚠ 未配置代码签名证书"
        Write-Host "    Windows Defender SmartScreen 将标记为'未知发布者'"
        Write-Host "    需要 EV Code Signing Certificate (.pfx) 用于正式发布"
        Write-Host ""
        Write-Host "    配置方式:"
        Write-Host "      \$env:CERT_PFX_PATH = 'C:\certs\nexusvideo.pfx'"
        Write-Host "      \$env:CERT_PASSWORD = 'YourPfxPassword'"
    }
}

# ---- 输出安装包信息 ----
Write-Host ""
if ($EXE_FILES.Count -gt 0) {
    $fileInfo = Get-Item $EXE_FILES[0].FullName
    $sizeMB = [math]::Round($fileInfo.Length / 1MB, 1)
    Write-Host "  📊 安装包信息:" -ForegroundColor Cyan
    Write-Host "    文件名: $($fileInfo.Name)" -ForegroundColor Cyan
    Write-Host "    大小:   $sizeMB MB" -ForegroundColor Cyan
    Write-Host "    路径:   $($fileInfo.FullName)" -ForegroundColor Cyan
}

# ---- 完成 ----
Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " ✅ NexusVideo Windows 打包完成！" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  产物位置: $TauriDir\target\release\bundle\"
Write-Host ""
Write-Host "  下一步:"
Write-Host "    1. 配置 EV Code Signing Certificate 进行签名"
Write-Host "    2. 上传至 GitHub Release 或 https://releases.nexusvideo.com/"
Write-Host "    3. 配置 Tauri Updater update.json 端点"
Write-Host "    4. macOS 打包: 在 Mac 上执行 scripts/build.sh"
Write-Host "==============================================" -ForegroundColor Cyan

Set-Location $ProjectDir