# NexusVideo Windows 打包配置

> 平台: Windows 10 (1809+) / Windows 11
> 打包格式: NSIS 安装包 (.exe)
> 签名: Authenticode EV Code Signing

---

## 1. 图标资源

### 必需文件

| 文件 | 尺寸 | 用途 |
|------|------|------|
| `icon.ico` | 256×256 (多分辨率嵌入) | 安装程序图标 + 任务栏图标 |
| `128x128.png` | 128×128 | Windows 图标缓存 |
| `256x256.png` | 256×256 | 高分屏显示 |
| `512x512.png` | 512×512 | 文件资源管理器预览 |

**存放路径**: `client/src-tauri/icons/icon.ico` 等

**生成命令**（从 PNG 源图生成 ICO）:
```bash
# 使用 ImageMagick 或在线工具
magick convert source.png -define icon:auto-resize=16,32,48,64,128,256 icon.ico
```

> **注意**: Windows 要求 `.ico` 格式（不能直接用 `.png`）。Tauri 构建时自动将 PNG 图标嵌入安装包。

### DMG 背景图

macOS DMG 安装界面背景图，存放于 `client/src-tauri/icons/dmg-background.png`。
Windows 端 NSIS 安装器可选配置 `headerImage` 用于安装界面顶部图片。

---

## 2. 代码签名配置

### tauri.conf.json 配置项

```json
"bundle": {
  "windows": {
    "certificateThumbprint": "<PLACEHOLDER:OV_CERT_THUMBPRINT>",
    "digestAlgorithm": "sha256",
    "timestampUrl": "http://timestamp.digicert.com"
  }
}
```

| 配置项 | 说明 | 当前值 |
|--------|------|--------|
| `certificateThumbprint` | OV/EV 证书指纹（SHA1） | 占位符，替换为真实指纹 |
| `digestAlgorithm` | 哈希算法 | `sha256` |
| `timestampUrl` | RFC 3161 时间戳服务器 | `http://timestamp.digicert.com` |

### 签名命令

```powershell
# 签名
signtool sign ^
  /fd SHA256 ^
  /f "C:\certs\NexusVideo_EV.pfx" ^
  /p "YourCertPassword" ^
  /tr http://timestamp.digicert.com ^
  /td SHA256 ^
  "NexusVideo_0.1.0_x64-setup.exe"

# 验证
signtool verify /pa /v "NexusVideo_0.1.0_x64-setup.exe"
```

### 证书指纹获取

```powershell
Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -like "*NexusVideo*" } | Select-Object Thumbprint, Subject
```

将输出的 `Thumbprint` 填入 `tauri.conf.json` 的 `certificateThumbprint` 字段。

---

## 3. WebView2 运行时

tauri.conf.json 中已配置:
```json
"webviewInstallMode": {
  "type": "embedBootstrapper",
  "silent": true
}
```

这会在安装时静默嵌入 WebView2 Bootstrapper。对于 Win10 1809+ 和 Win11 系统，Windows 更新会自动安装 WebView2 运行时。

---

## 4. 打包输出路径

```
client/src-tauri/target/release/bundle/nsis/NexusVideo_0.1.0_x64-setup.exe
```

**目标大小**: < 100MB（不含 ComfyUI 模型文件）

---

## 5. 关联文档

- [代码签名完整指南](/docs/code-signing-guide.md)
- [发布检查清单](/docs/release-checklist.md)