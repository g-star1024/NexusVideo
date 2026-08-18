<#
.SYNOPSIS
    NexusVideo ComfyUI 模型下载脚本（Windows PowerShell 版）

.DESCRIPTION
    自动下载 MVP 所需 ComfyUI 模型文件，支持断点续传（-Resume）、SHA256 校验、
    --dry-run 预览模式。

.OUTPUTS
    ~/NexusVideo/models/{model_name}/

.CHECKSUMS
    校验和来源：https://releases.nexusvideo.com/checksums.txt

.EXAMPLES
    .\download_models.ps1 -DryRun          # 预览模式
    .\download_models.ps1                  # 开始下载
    .\download_models.ps1 -Resume          # 启用断点续传
    .\download_models.ps1 -ModelsDir "D:\NexusVideo\Models"

.NOTES
    作者：NexusVideo MVP 团队 · 运维架构师 唐磐石
    版本：1.0.0
#>

[CmdletBinding()]
param(
    [Parameter(HelpMessage = "预览模式，不实际下载")]
    [Switch]$DryRun,

    [Parameter(HelpMessage = "启用断点续传")]
    [Switch]$Resume,

    [Parameter(HelpMessage = "模型输出目录")]
    [string]$ModelsDir = "$env:USERPROFILE\NexusVideo\models",

    [Parameter(HelpMessage = "跳过 SHA256 校验")]
    [Switch]$SkipVerify,

    [Parameter(HelpMessage = "NexusVideo 根目录")]
    [string]$NexusHome = "$env:USERPROFILE\NexusVideo"
)

# =============================================================================
# 模型清单
# 字段：Name, Description, Url, FileName
# =============================================================================
$Models = @(
    @{
        Name = "wan2.1_14b_gguf_q4"
        Description = "Wan2.1 14B GGUF Q4（文生视频主力模型）"
        Url = "https://huggingface.co/NexusVideo/Wan2.1-14B-GGUF-Q4_K_M/resolve/main/wan2.1_14b_q4_k_m.gguf"
        FileName = "wan2.1_14b_q4_k_m.gguf"
    },
    @{
        Name = "animatediff_baseline"
        Description = "AnimateDiff 基线模型（6GB 显存保底方案）"
        Url = "https://huggingface.co/guoyww/animatediff/resolve/main/v2/model.ckpt"
        FileName = "model.ckpt"
    },
    @{
        Name = "controlnet_tile"
        Description = "ControlNet Tile（图生视频空间一致性）"
        Url = "https://huggingface.co/lllyasviel/sd_control_collection/resolve/main/T2I-adapter_tile_sd14.safetensors"
        FileName = "T2I-adapter_tile_sd14.safetensors"
    },
    @{
        Name = "controlnet_depth"
        Description = "ControlNet Depth（图生视频深度引导）"
        Url = "https://huggingface.co/lllyasviel/sd_control_collection/resolve/main/T2I-adapter_depth_sd14.safetensors"
        FileName = "T2I-adapter_depth_sd14.safetensors"
    },
    @{
        Name = "ip_adapter"
        Description = "IP-Adapter（图生视频参考图适配）"
        Url = "https://huggingface.co/h94/IP-Adapter/resolve/main/models/ip-adapter_sd15.safetensors"
        FileName = "ip-adapter_sd15.safetensors"
    },
    @{
        Name = "rife_interpolation"
        Description = "RIFE 帧插值模型（视频帧率提升）"
        Url = "https://github.com/hzwer/Practical-RIFE/releases/download/v1/rife-v1.6-flowmodel.safetensors"
        FileName = "rife-v1.6-flowmodel.safetensors"
    },
    @{
        Name = "vae_decoders"
        Description = "基础 VAE 解码器"
        Url = "https://huggingface.co/stabilityai/sd-vae-ft-mse-original/resolve/main/vae-ft-mse-840000-ema.ckpt"
        FileName = "vae-ft-mse-840000-ema.ckpt"
    }
)

$ChecksumUrl = "https://releases.nexusvideo.com/checksums.txt"
$ChecksumCache = Join-Path $NexusHome ".checksums_cache"

# =============================================================================
# 颜色输出辅助
# =============================================================================
function Write-Colored {
    param([string]$Text, [ConsoleColor]$Color = [ConsoleColor]::White)
    $fg = $Host.UI.RawUI.ForegroundColor
    $Host.UI.RawUI.ForegroundColor = $Color
    Write-Host $Text
    $Host.UI.RawUI.ForegroundColor = $fg
}

function Log-Info  { Write-Colored "[INFO]  $args" -Color Green }
function Log-Warn  { Write-Colored "[WARN]  $args" -Color Yellow }
function Log-Error { Write-Colored "[ERROR] $args" -Color Red }
function Log-Step  { Write-Colored "[STEP]  $args" -Color Cyan }
function Log-Ok    { Write-Colored "[OK]    $args" -Color Green }
function Log-Data  { Write-Colored "        $args" -Color DarkCyan }

function Format-Size {
    param([long]$Bytes)
    if     ($Bytes -ge 1073741824) { "{0:F2} GB" -f ($Bytes / 1073741824) }
    elseif ($Bytes -ge 1048576)    { "{0:F2} MB" -f ($Bytes / 1048576) }
    elseif ($Bytes -ge 1024)       { "{0:F2} KB" -f ($Bytes / 1024) }
    else                           { "$Bytes B" }
}

# =============================================================================
# SHA256 校验和下载与查询
# =============================================================================
function Get-Checksums {
    if (Test-Path $ChecksumCache) {
        Log-Info "使用本地缓存校验和文件"
        return $true
    }

    Log-Step "下载 SHA256 校验和文件..."
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("User-Agent", "NexusVideo-Downloader/1.0")
        $wc.DownloadFile($ChecksumUrl, $ChecksumCache)
        Log-Ok "校验和文件已缓存"
        return $true
    }
    catch {
        Log-Warn "无法获取校验和文件，将跳过 SHA256 校验"
        Log-Warn "请手动从 $ChecksumUrl 下载后放入 $ChecksumCache"
        if (Test-Path $ChecksumCache) { Remove-Item $ChecksumCache -Force }
        return $false
    }
}

function Get-ExpectedSha256 {
    param([string]$FileName)
    if (-not (Test-Path $ChecksumCache)) { return $null }
    $line = Select-String -Path $ChecksumCache -Pattern ([regex]::Escape($FileName)) -SimpleMatch -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($line) { return ($line.Line -split '\s+')[0] }
    return $null
}

function Verify-Sha256 {
    param([string]$FilePath)
    $fileName = [System.IO.Path]::GetFileName($FilePath)
    $expected = Get-ExpectedSha256 $fileName

    if (-not $expected) {
        Log-Warn "  无校验和记录，跳过验证"
        return $true
    }

    $actual = (Get-FileHash -Path $FilePath -Algorithm SHA256).Hash.ToLower()
    if ($actual -eq $expected.ToLower()) {
        Log-Ok "  SHA256 校验通过"
        return $true
    }
    else {
        Log-Error "  SHA256 校验失败！期望: $expected，实际: $actual"
        Log-Error "  文件已删除"
        Remove-Item $FilePath -Force
        return $false
    }
}

# =============================================================================
# 下载函数
# =============================================================================
function Invoke-ModelDownload {
    param(
        [string]$Name,
        [string]$Description,
        [string]$Url,
        [string]$FileName
    )

    Log-Step "下载: $Name"
    Log-Data "描述: $Description"
    Log-Data "URL:  $Url"

    $targetDir = Join-Path $ModelsDir $Name
    if (-not (Test-Path $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    $targetPath = Join-Path $targetDir $FileName

    # ---- Dry-run 模式 ----
    if ($DryRun) {
        Log-Data "[DRY-RUN] 跳过实际下载"
        try {
            $req = [System.Net.WebRequest]::CreateHead($Url)
            $req.Timeout = 10000
            $resp = $req.GetResponse()
            $size = $resp.ContentLength
            $resp.Close()
            Log-Data "[DRY-RUN] 估算大小: $(Format-Size $size)"
            return $size
        }
        catch {
            Log-Data "[DRY-RUN] 无法获取文件大小"
            return 0
        }
    }

    # ---- 检查是否已完整 ----
    if (Test-Path $targetPath) {
        $existingSize = (Get-Item $targetPath).Length
        try {
            $req = [System.Net.WebRequest]::CreateHead($Url)
            $req.Timeout = 10000
            $resp = $req.GetResponse()
            $remoteSize = $resp.ContentLength
            $resp.Close()
            if ($existingSize -eq $remoteSize) {
                Log-Ok "  文件已存在且大小匹配，跳过下载"
                Verify-Sha256 $targetPath
                return $existingSize
            }
        }
        catch { Log-Warn "  无法获取远端大小，将重新下载" }
        Log-Info "  文件部分存在，$(if($Resume){'将续传'}else{'将重新下载'})"
    }

    # ---- 优先 aria2c ----
    $aria2cPath = Get-Command aria2c -ErrorAction SilentlyContinue
    if ($aria2cPath) {
        Log-Data "使用 aria2c 下载（支持断点续传）..."
        $args = @(
            "--continue=true",
            "--max-connection-per-server=4",
            "--min-split-size=10M",
            "--summary-interval=5",
            "-o", $targetPath,
            $Url
        )
        try {
            $proc = Start-Process -FilePath $aria2cPath.Source -ArgumentList $args -Wait -NoNewWindow -PassThru
            if ($proc.ExitCode -ne 0) {
                Log-Error "  aria2c 下载失败，降级使用 Invoke-WebRequest..."
                if (Test-Path $targetPath) { Remove-Item $targetPath -Force }
                throw "aria2c_failed"
            }
        }
        catch {
            if ($_.Exception.Message -ne "aria2c_failed") { throw }
            # 降级
            Log-Data "使用 Invoke-WebRequest 下载（不支持续传）..."
            $wc = New-Object System.Net.WebClient
            $wc.Headers.Add("User-Agent", "NexusVideo-Downloader/1.0")

            # 显示进度
            $wc.DownloadProgressChanged += {
                param($s, $e)
                $pct = [math]::Round($e.ProgressPercentage, 1)
                Write-Host "\r        进度: $pct%  已下载: $(Format-Size $e.BytesReceived) / $(Format-Size $e.TotalBytesToReceive)" -NoNewline
            }
            try {
                $wc.DownloadFileAsync($Url, $targetPath)
                # 等待完成（轮询）
                while ((Get-Item $targetPath -ErrorAction SilentlyContinue) -and -not ($wc.IsBusy)) {}
                while ($wc.IsBusy) { Start-Sleep -Milliseconds 500 }
                Write-Host ""
            }
            catch {
                Log-Error "  下载失败: $_"
                if (Test-Path $targetPath) { Remove-Item $targetPath -Force }
                return $null
            }
        }
    }
    else {
        Log-Data "使用 Invoke-WebRequest 下载（未检测到 aria2c）..."
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("User-Agent", "NexusVideo-Downloader/1.0")
        $wc.DownloadProgressChanged += {
            param($s, $e)
            $pct = [math]::Round($e.ProgressPercentage, 1)
            Write-Host "\r        进度: $pct%  已下载: $(Format-Size $e.BytesReceived) / $(Format-Size $e.TotalBytesToReceive)" -NoNewline
        }
        try {
            $wc.DownloadFileAsync($Url, $targetPath)
            while ($wc.IsBusy) { Start-Sleep -Milliseconds 500 }
            Write-Host ""
        }
        catch {
            Log-Error "  下载失败: $_"
            if (Test-Path $targetPath) { Remove-Item $targetPath -Force }
            return $null
        }
    }

    # ---- 校验 ----
    if (-not $SkipVerify) {
        if (-not (Verify-Sha256 $targetPath)) { return $null }
    }

    $finalSize = (Get-Item $targetPath).Length
    Log-Ok "  完成: $(Format-Size $finalSize)"
    return $finalSize
}

# =============================================================================
# Banner & 主流程
# =============================================================================
Write-Host ""
Write-Colored "╔══════════════════════════════════════════════════════════════════════════╗" -Color Cyan
Write-Colored "║       NexusVideo ComfyUI 模型下载工具（Windows PowerShell 版）         ║" -Color Cyan
Write-Colored "║       运维架构师: 唐磐石  ·  NexusVideo MVP 团队                       ║" -Color Cyan
Write-Colored "╚══════════════════════════════════════════════════════════════════════════╝" -Color Cyan
Write-Host ""

if ($DryRun) {
    Log-Warn "DRY-RUN 模式：以下操作将不会实际执行"
    Write-Host ""
}

# 下载校验和
Get-Checksums

# 统计
$totalModels = $Models.Count
$downloadedCount = 0
$failedCount = 0
$totalSize = [long]0
$startTime = Get-Date

foreach ($model in $Models) {
    Write-Host ""
    $result = Invoke-ModelDownload `
        -Name $model.Name `
        -Description $model.Description `
        -Url $model.Url `
        -FileName $model.FileName

    if ($null -ne $result) {
        $downloadedCount++
        $totalSize += $result
    }
    else {
        $failedCount++
        Log-Error "模型 $($model.Name) 下载失败，继续下一个..."
    }
}

# 耗时
$endTime = Get-Date
$elapsed = ($endTime - $startTime)
$elapsedMin = $elapsed.TotalMinutes
$elapsedSec = $elapsed.Seconds
$elapsedTotalMin = [math]::Floor($elapsedMin)

# ---------------------------------------------------------------------------
# 总结报告
# ---------------------------------------------------------------------------
Write-Host ""
Write-Colored "═══════════════════════════════════════════════════════════════════════════" -Color Cyan
Log-Step "下载完成 - 总结报告"
Log-Data "模型总数:    $totalModels"
Log-Data "成功:        $downloadedCount"
Log-Data "失败:        $failedCount"
Log-Data "总下载量:    $(Format-Size $totalSize)"
Log-Data "耗时:        ${elapsedTotalMin}分${elapsedSec}秒"
Log-Data "输出目录:    $ModelsDir"

if ($DryRun) {
    Write-Host ""
    Log-Warn "这是 DRY-RUN 预览结果，未实际下载任何文件"
}

Write-Colored "═══════════════════════════════════════════════════════════════════════════" -Color Cyan
Write-Host ""

if ($failedCount -gt 0) {
    exit 1
}
exit 0