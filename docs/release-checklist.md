# NexusVideo MVP Release Checklist

> 版本: 0.1.0 | 更新日期: 2026-08-18 | 负责人: 封易安 (client-tauri-dev)

---

## 发布前（Pre-release）

- [ ] Rust 编译通过：`cargo check --workspace`
- [ ] TypeScript 编译通过：`npm run build`
- [ ] 工作流模板验证：`python validate_workflow.py`
- [ ] Wan2.1 1.3B 模型已就位
- [ ] 本地集成测试通过（3步出视频端到端）
- [ ] 版本号更新（tauri.conf.json + package.json + Cargo.toml 三处一致）
- [ ] Changelog 已撰写

## 打包（Build）

- [ ] Windows .exe 打包完成
- [ ] Windows .exe 代码签名（OV Code Signing）
- [ ] macOS .dmg 打包完成（x64 + aarch64）
- [ ] macOS .dmg 代码签名（Developer ID）+ Notarization
- [ ] 安装包大小确认（< 500MB 不含模型）
- [ ] Tauri Update Manifest (`update.json`) 生成

## 发布（Release）

- [ ] GitHub Release 创建（tag + changelog）
- [ ] 自动更新服务端部署（OSS/GitHub Releases）
- [ ] CDN 预热完成
- [ ] 内测用户安装包分发
- [ ] 内测用户反馈收集（3天观察期）

## 发布后（Post-release）

- [ ] 监控告警确认运行正常
- [ ] 崩溃上报服务确认正常
- [ ] 首次生成成功率统计
- [ ] 社区/用户渠道公告

---

## 1. 版本管理

### 语义化版本号规则

| 版本部分 | 变更类型 | 示例 |
|---------|---------|------|
| MAJOR | 破坏性变更 | 0.1.0 → 1.0.0 |
| MINOR | 新功能（向后兼容） | 0.1.0 → 0.2.0 |
| PATCH | Bug 修复 | 0.1.0 → 0.1.1 |

### 版本号更新位置

- `client/src-tauri/Cargo.toml` → `version = "0.1.0"`
- `client/package.json` → `"version": "0.1.0"`
- `client/src-tauri/tauri.conf.json` → `"version": "0.1.0"`

### Changelog 模板

```markdown
## [0.2.0] - 2026-08-25

### Added
- 新增图生视频功能
- 新增云端生成模式

### Changed
- 优化生成排队逻辑

### Fixed
- 修复 ComfyUI 子进程内存泄漏
- 修复 macOS 下中文路径异常

### Removed
- 移除过期的 API 端点
```

---

## 2. 打包前检查

### 2.1 开发环境检查

- [ ] Rust toolchain ≥ 1.77 (`rustc --version`)
- [ ] cargo-tauri 已安装 (`cargo tauri --version`)
- [ ] Node.js ≥ 18 (`node --version`)
- [ ] npm 可用 (`npm --version`)
- [ ] macOS: Xcode Command Line Tools (`xcode-select --install`)
- [ ] Windows: Visual Studio Build Tools 2022 (C++ 桌面开发工作负载)
- [ ] Windows: Python 3.10+ (`python --version`)

### 2.2 代码检查

- [ ] `git status` 确认工作区干净
- [ ] 版本号已同步更新（Cargo.toml / package.json / tauri.conf.json）
- [ ] Changelog 已更新
- [ ] 所有 feature flag 已确认（release 不包含 devtools）

### 2.3 构建验证

```bash
# 前端
cd client && npm ci && npm run build

# Rust
cd client/src-tauri && cargo build --release

# 完整 Tauri 构建
cd client/src-tauri && cargo tauri build
```

- [ ] 前端 `npm run build` 无错误
- [ ] Rust `cargo build --release` 无 warning（或已记录）
- [ ] `cargo tauri build` 成功完成

---

## 3. 打包步骤

### 3.1 Windows

```powershell
# 在 PowerShell 中执行
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

- [ ] 脚本执行无错误
- [ ] 产物路径: `client/src-tauri/target/release/bundle/nsis/NexusVideo_0.1.0_x64-setup.exe`
- [ ] 安装包体积 < 100MB

### 3.2 macOS

```bash
# 在终端中执行
chmod +x scripts/build.sh
./scripts/build.sh
```

- [ ] 脚本执行无错误
- [ ] 产物路径: `client/src-tauri/target/release/bundle/dmg/NexusVideo_0.1.0_x64.dmg`
- [ ] 安装包体积 < 100MB

---

## 4. 签名步骤

### 4.1 Windows (Authenticode + EV Code Signing)

| 项目 | 说明 |
|------|------|
| 证书类型 | EV Code Signing Certificate (.pfx) |
| 推荐厂商 | DigiCert / GlobalSign / Sectigo |
| 签名工具 | `signtool.exe` (Visual Studio) |
| 时间戳服务器 | `http://timestamp.digicert.com` |

签名命令:
```powershell
& "C:\Program Files (x86)\Microsoft Visual Studio\2022\Enterprise\Common7\Tools\signtool.exe" `
  sign /f "C:\certs\nexusvideo.pfx" /p "YourPassword" `
  /tr "http://timestamp.digicert.com" /td sha256 /fd sha256 `
  "NexusVideo_0.1.0_x64-setup.exe"
```

验证签名:
```powershell
signtool verify /pa "NexusVideo_0.1.0_x64-setup.exe"
```

### 4.2 macOS (Developer ID + Notarization)

| 项目 | 说明 |
|------|------|
| 证书类型 | Developer ID Application (macOS) |
| 来源 | Apple Developer Program ($99/年) |
| 签名工具 | `codesign` |
| 公证工具 | `notarytool` (Xcode 13+) |

签名命令:
```bash
codesign --force --deep --sign "Developer ID Application: Your Name (TEAM_ID)" \
  "NexusVideo.app"

# 公证
xcrun notarytool submit "NexusVideo_0.1.0_x64.dmg" \
  --apple-id "your@apple.com" \
  --password "app-specific-password" \
  --team-id "TEAM_ID" \
  --wait

# Stapling
xcrun stapler staple "NexusVideo.app"
```

验证签名:
```bash
codesign --verify --deep "NexusVideo.app"
spctl --assess --type execute "NexusVideo.app"
```

---

## 5. 发布步骤

### 5.1 GitHub Release

1. 在 GitHub 仓库创建新 Release (`v0.1.0`)
2. 上传 .exe / .dmg 安装包
3. 填写 Release Notes (使用 Changelog 模板)
4. 标记为 "Latest Release"

### 5.2 Tauri Updater 配置

更新服务器 (`https://releases.nexusvideo.com/update.json`) 需要返回:

```json
{
  "version": "0.1.0",
  "date": "2026-08-25T00:00:00Z",
  "url": "https://releases.nexusvideo.com/NexusVideo_0.1.0_x64-setup.exe",
  "sha256": "计算出的 SHA256 哈希值",
  "pub_date": "2026-08-25T00:00:00Z",
  "body": "# Release Notes\n\n## 新功能\n- 图生视频\n\n## Bug 修复\n- 修复崩溃问题"
}
```

macOS 版本需要单独的 URL 和 SHA256:

```json
{
  "version": "0.1.0",
  "date": "2026-08-25T00:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "url": "https://releases.nexusvideo.com/NexusVideo_0.1.0_x64-setup.exe",
      "sha256": "windows-sha256..."
    },
    "darwin-x86_64": {
      "url": "https://releases.nexusvideo.com/NexusVideo_0.1.0_x64.dmg",
      "sha256": "macos-sha256..."
    },
    "darwin-aarch64": {
      "url": "https://releases.nexusvideo.com/NexusVideo_0.1.0_aarch64.dmg",
      "sha256": "macos-arm-sha256..."
    }
  },
  "pub_date": "2026-08-25T00:00:00Z",
  "body": "# Release Notes\n..."
}
```

### 5.3 发布后验证

- [ ] 新版本 update.json 可通过 HTTP GET 访问
- [ ] SHA256 哈希值与实际文件匹配
- [ ] 旧版客户端启动后能检测到更新
- [ ] 更新下载安装后重启能正确升级到新版本

---

## 6. 回归测试清单

### 6.1 首次启动

- [ ] 全新安装后双击图标能正常启动
- [ ] 启动时不报错 / 不闪退
- [ ] 首次启动初始化流程正常（模型下载 / 目录创建）
- [ ] 启动日志无 ERROR 级别条目

### 6.2 核心功能

- [ ] 文生视频：输入提示词 → 生成 → 预览视频正常
- [ ] 图生视频：上传图片 → 生成 → 预览视频正常
- [ ] 云端生成：切换云端模式 → 登录 → 生成 → 正常
- [ ] 历史记录：生成记录保存 → 退出重启后仍可查看

### 6.3 云端切换

- [ ] 本地模式 → 云端模式 切换流畅
- [ ] 云端模式 → 本地模式 切换后 ComfyUI 自动启动
- [ ] 云端模式断网后能提示重连
- [ ] 用户登录 / 登出 / Token 过期处理正常

### 6.4 用户登录

- [ ] 邮箱验证码登录正常
- [ ] 手机号验证码登录正常
- [ ] 退出登录后本地数据保留
- [ ] 重新登录能恢复云端数据

### 6.5 错误恢复

- [ ] ComfyUI 崩溃后自动重启
- [ ] FastAPI 崩溃后自动重启
- [ ] 生成过程中断网 → 恢复后能续传或重试
- [ ] 磁盘空间不足时提示清理
- [ ] 崩溃日志可查看 (设置 → 关于 → 崩溃日志)

### 6.6 自动更新

- [ ] 旧版本启动时检测到新版本
- [ ] 点击下载后进度条正常显示
- [ ] 下载完成后提示重启安装
- [ ] 重启后正确安装新版本
- [ ] 已是最新版本时无更新提示

---

## 7. 已知问题记录

| # | 问题描述 | 影响平台 | 严重程度 | 状态 | 临时方案 |
|---|---------|---------|---------|------|---------|
| 1 | macOS 12 以下 WebView 不支持 WebRTC | macOS ≤ 12 | 中 | 待修复 | 提示用户升级 macOS |
| 2 | Windows 10 21H2 以下 WebView2 需手动安装 | Win 10 < 21H2 | 低 | 已知 | 启动时检测并引导安装 |
| 3 | 增量更新尚未实现，当前为全量更新 | 全平台 | 低 | 排期中 | 首版接受全量下载 |
| 4 | M1/M2 Mac 上 universal binary 需手动配置 | macOS ARM | 低 | 开发中 | 先用 x64 架构 |
| 5 | 中文路径在某些 Windows 版本上偶发异常 | Windows | 低 | 观察中 | 避免中文安装路径 |

---

## 8. Release Notes 模板

```markdown
# NexusVideo v0.1.0 Release Notes

**发布日期**: 2026-08-25

---

## 🎉 新功能

- AI 文生视频：一句话生成短视频
- AI 图生视频：上传图片一键生成动态视频
- 云端生成模式：无需本地 GPU，随时随地创作
- 自动更新：有新版本时自动通知下载

## 🛠 功能改进

- 优化首次启动流程，模型下载进度可视化
- ComfyUI 子进程崩溃自动重启
- 生成的视频自动保存并支持预览

## 🐛 Bug 修复

- 修复了生成过程中内存泄漏的问题
- 修复了 macOS 下中文路径异常
- 修复了快速连续点击生成按钮导致的重复请求

## 📦 系统要求

- **Windows**: Windows 10 (1809+) / Windows 11, 8GB+ RAM
- **macOS**: macOS 12 Monterey+ (Intel / Apple Silicon)

## 🔗 相关链接

- 在线文档: https://docs.nexusvideo.com
- 使用教程: https://docs.nexusvideo.com/guide
- 常见问题: https://docs.nexusvideo.com/faq
- 加入社区: https://discord.gg/nexusvideo

---

_NexusVideo Team_
```

---

## 附录：打包产物说明

| 文件 | 平台 | 格式 | 说明 |
|------|------|------|------|
| `NexusVideo_0.1.0_x64-setup.exe` | Windows | NSIS 安装包 | 双击安装，支持中文/英文 |
| `NexusVideo_0.1.0_x64.dmg` | macOS Intel | DMG | 拖拽安装 |
| `NexusVideo_0.1.0_aarch64.dmg` | macOS Apple Silicon | DMG | 原生 ARM 性能 |
| `update.json` | 全平台 | JSON | Tauri Updater 元数据 |

> 签名状态：
> - Windows: ✅ Authenticode 签名 (EV Code Signing)
> - macOS: ✅ Developer ID 签名 + Notarization