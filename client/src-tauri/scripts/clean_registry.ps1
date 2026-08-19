#!/usr/bin/env pwsh
# ============================================================================
# clean_registry.ps1 — NexusVideo Windows 注册表清理脚本
# ============================================================================
# 用途：卸载 NexusVideo 客户端后，清理残留的注册表项
# 使用方式：
#   1. 先手动卸载 NexusVideo（控制面板 → 程序和功能 → 卸载）
#   2. 以管理员身份运行本脚本
#   3. 脚本自动扫描并清理以下注册表路径：
#      - HKCU\Software\NexusVideo
#      - HKLM\SOFTWARE\NexusVideo
#      - HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\NexusVideo
#      - 应用启动项（Run / RunOnce）
#
# 注意：此脚本为幂等安全操作。如果注册表项不存在，直接跳过不报错。
# ============================================================================

$ErrorActionPreference = "Stop"
$APP_NAME = "NexusVideo"
$APP_IDENTIFIER = "com.nexusvideo.client"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " NexusVideo 注册表清理工具" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# 确认管理员权限
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[WARN] 请以管理员身份运行此脚本以清理 HKLM 注册表项" -ForegroundColor Yellow
    Write-Host "      右键 → 以管理员身份运行" -ForegroundColor Yellow
    Write-Host ""
}

# ---- 定义要清理的注册表路径 ----
$registryPaths = @(
    # 用户级应用数据
    "HKCU:\Software\NexusVideo",
    "HKCU:\Software\com.nexusvideo.client",

    # 系统级应用数据（需要管理员权限）
    "HKLM:\SOFTWARE\NexusVideo",
    "HKLM:\SOFTWARE\com.nexusvideo.client",
    "HKLM:\SOFTWARE\WOW6432Node\NexusVideo",

    # 卸载信息
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\NexusVideo",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\NexusVideo",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\NexusVideo",

    # 应用启动项
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",

    # Tauri 自动更新注册表项
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\NexusVideo IsWow64"
)

# ---- 清理函数 ----
function Remove-RegistryKeyIfExists {
    param(
        [string]$Path
    )
    if (Test-Path $Path) {
        try {
            Remove-Item $Path -Recurse -Force -ErrorAction Stop
            Write-Host "  [OK] 已删除: $Path" -ForegroundColor Green
            return $true
        } catch {
            Write-Host "  [FAIL] 删除失败: $Path → $($_.Exception.Message)" -ForegroundColor Red
            return $false
        }
    } else {
        Write-Host "  [SKIP] 不存在: $Path" -ForegroundColor DarkGray
        return $false
    }
}

function Remove-RegistryValueIfExists {
    param(
        [string]$Path,
        [string]$ValueName
    )
    if (Test-Path $Path) {
        $keys = Get-Item $Path -ErrorAction SilentlyContinue
        if ($keys -and ($keys.GetValueNames() -contains $ValueName)) {
            try {
                Remove-ItemProperty -Path $Path -Name $ValueName -Force -ErrorAction Stop
                Write-Host "  [OK] 已删除值: $Path\$ValueName" -ForegroundColor Green
                return $true
            } catch {
                Write-Host "  [FAIL] 删除值失败: $Path\$ValueName → $($_.Exception.Message)" -ForegroundColor Red
                return $false
            }
        } else {
            Write-Host "  [SKIP] 值不存在: $Path\$ValueName" -ForegroundColor DarkGray
            return $false
        }
    } else {
        Write-Host "  [SKIP] 路径不存在: $Path" -ForegroundColor DarkGray
        return $false
    }
}

# ---- 执行清理 ----
$successCount = 0
$failCount = 0

Write-Host "=== 清理注册表键 ===" -ForegroundColor Cyan
Write-Host ""

foreach ($path in $registryPaths) {
    # 对于 Run/RunOnce 路径，只删除特定值（不删除整个键）
    if ($path -match "CurrentVersion\\Run($|Once)$") {
        $removed = Remove-RegistryValueIfExists -Path $path -ValueName $APP_NAME
        $removed2 = Remove-RegistryValueIfExists -Path $path -ValueName "NexusVideo Auto Update"
        if ($removed -or $removed2) { $successCount++ }
    } else {
        $result = Remove-RegistryKeyIfExists -Path $path
        if ($result) { $successCount++ }
    }
}

Write-Host ""
Write-Host "=== 清理用户数据目录 ===" -ForegroundColor Cyan
Write-Host ""

# 清理 AppData 目录
$userAppData = [Environment]::GetFolderPath("LocalApplicationData")
$userRoamingData = [Environment]::GetFolderPath("ApplicationData")

$dataDirs = @(
    "$userAppData\NexusVideo",
    "$userRoamingData\NexusVideo",
    "$userAppData\com.nexusvideo.client",
    "$userRoamingData\com.nexusvideo.client"
)

foreach ($dir in $dataDirs) {
    if (Test-Path $dir) {
        try {
            # 先移动而不是直接删除，方便用户恢复
            $backupDir = "$dir.bak.$(Get-Date -Format 'yyyyMMdd_HHmmss')"
            Rename-Item $dir $backupDir -Force
            Write-Host "  [OK] 已移动备份: $backupDir" -ForegroundColor Green
        } catch {
            Write-Host "  [FAIL] 移动失败: $dir → $($_.Exception.Message)" -ForegroundColor Red
        }
    } else {
        Write-Host "  [SKIP] 不存在: $dir" -ForegroundColor DarkGray
    }
}

# ---- 输出摘要 ----
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " 清理完成" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " 注册表项清理: $successCount 项成功" -ForegroundColor Green
Write-Host ""
Write-Host "注意：" -ForegroundColor Yellow
Write-Host "  - 用户数据目录已重命名备份（.bak 后缀）" -ForegroundColor Yellow
Write-Host "  - 如需完全删除备份，请手动删除以下目录：" -ForegroundColor Yellow
Write-Host "    $userAppData\NexusVideo.bak.*" -ForegroundColor Yellow
Write-Host "    $userRoamingData\NexusVideo.bak.*" -ForegroundColor Yellow
Write-Host ""
Write-Host "按任意键退出..." -ForegroundColor DarkGray
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")