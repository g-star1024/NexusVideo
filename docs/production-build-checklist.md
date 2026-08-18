# NexusVideo 生产构建验证清单

> 最后更新：2026-08-18
> 负责：封易安（client-tauri-dev-5）
> 适用范围：v0.1.0 正式版本发布

---

## 目录

1. [构建前检查](#1-构建前检查)
2. [构建执行](#2-构建执行)
3. [构建后验证](#3-构建后验证)
4. [分发前检查](#4-分发前检查)
5. [回滚方案](#5-回滚方案)

---

## 1. 构建前检查

> 负责：封易安（client-tauri-dev-5）

### 1.1 开发环境确认

```bash
# Node.js 版本（要求 ≥ 18）
node --version
# 预期：v18.x 或 v20.x+

# Rust 工具链（要求 ≥ 1.70）
rustc --version
# 预期：rustc 1.70.x 或更高

# Tauri CLI
cargo tauri --version
# 预期：cargo-tauri 2.x.x

# npm / pnpm
npm --version
# 预期：9.x 或 10.x+

# Git
git --version
```

| 检查项 | 命令 | 预期版本 | 状态 |
|--------|------|---------|------|
| [ ] Node.js | `node --version` | ≥ 18 | ☐ |
| [ ] Rust | `rustc --version` | ≥ 1.70 | ☐ |
| [ ] Tauri CLI | `cargo tauri --version` | ≥ 2.0 | ☐ |
| [ ] npm | `npm --version` | ≥ 9 | ☐ |
| [ ] Git | `git --version` | ≥ 2.40 | ☐ |

### 1.2 前端依赖确认

```bash
# 进入前端项目目录
cd client

# 确认 lockfile 存在且与 package.json 一致
ls package-lock.json || ls pnpm-lock.yaml

# 安装依赖（使用 ci 模式确保与 lockfile 一致）
npm ci

# 验证前端构建可完成
npm run build
# 预期：无错误，dist/ 目录生成
```

| 检查项 | 命令 | 状态 |
|--------|------|------|
| [ ] 前端依赖安装 | `npm ci` | ☐ |
| [ ] 前端生产构建 | `npm run build` | ☐ |
| [ ] dist/ 目录生成 | `ls dist/` | ☐ |

### 1.3 Rust 依赖确认

```bash
# 进入 Tauri 项目
cd client/src-tauri

# 编译 release 模式
cargo build --release

# 预期：无错误，target/release/ 目录生成
```

| 检查项 | 命令 | 状态 |
|--------|------|------|
| [ ] Rust 依赖编译 | `cargo build --release` | ☐ |
| [ ] target/release/ 生成 | `ls target/release/` | ☐ |

### 1.4 环境变量确认

```bash
# 检查必要环境变量
echo $JWT_SECRET            # JWT 密钥（非空）
echo $CLOUD_API_URL          # 云端 API 地址
echo $NEXUS_SIGN_CERT_PATH   # Windows 签名证书路径（CI 构建需要）
echo $NEXUS_APPLE_ID         # Apple ID（macOS 构建需要）

# Windows PowerShell 版本
$env:JWT_SECRET
$env:CLOUD_API_URL
```

| 检查项 | 变量名 | 来源 | 状态 |
|--------|--------|------|------|
| [ ] JWT 密钥 | `JWT_SECRET` | CI Secrets / .env | ☐ |
| [ ] 云端 API | `CLOUD_API_URL` | CI Secrets / .env | ☐ |
| [ ] 签名证书 | `NEXUS_SIGN_CERT_PATH` | CI Secrets | ☐ |
| [ ] Apple ID | `NEXUS_APPLE_ID` | CI Secrets | ☐ |
| [ ] 团队 ID | `NEXUS_TEAM_ID` | CI Secrets | ☐ |

### 1.5 CSP 配置确认

检查 `client/src-tauri/tauri.conf.json` 中 `app.security.csp`：

```
default-src 'self';
img-src 'self' data: blob: https://*;
media-src 'self' blob: file: https://*;
connect-src 'self'
  http://127.0.0.1:8188   # ComfyUI API
  http://127.0.0.1:9881   # Python 后端
  http://127.0.0.1:9882   # Python 后端备选
  ws://127.0.0.1:8188     # ComfyUI WebSocket
  ws://127.0.0.1:9881     # Python 后端 WebSocket
  https://*;
style-src 'self' 'unsafe-inline';
script-src 'self' 'unsafe-inline' 'unsafe-eval'
```

| 检查项 | 来源 | 已覆盖 | 状态 |
|--------|------|--------|------|
| [ ] ComfyUI HTTP | `http://127.0.0.1:8188` | ✅ | ☐ |
| [ ] ComfyUI WebSocket | `ws://127.0.0.1:8188` | ✅ | ☐ |
| [ ] Python 后端 HTTP | `http://127.0.0.1:9881` | ✅ | ☐ |
| [ ] Python 后端 WebSocket | `ws://127.0.0.1:9881` | ✅ | ☐ |
| [ ] 图片源 | `img-src` 含 `data: blob: https://*` | ✅ | ☐ |
| [ ] 媒体源 | `media-src` 含 `blob: file: https://*` | ✅ | ☐ |
| [ ] 云端 HTTPS | `connect-src` 含 `https://*` | ✅ | ☐ |

---

## 2. 构建执行

> 负责：封易安（client-tauri-dev-5）

### 2.1 前端生产构建

```bash
cd client
npm run build

# 验证
ls dist/
# 确认 index.html 存在
```

| 检查项 | 预期 | 状态 |
|--------|------|------|
| [ ] 构建无错误 | 退出码 0 | ☐ |
| [ ] dist/index.html 存在 | ✅ | ☐ |
| [ ] 构建体积合理 | < 5MB（含 vendor） | ☐ |

### 2.2 Tauri 双平台打包

```bash
cd client
npm run tauri build

# 或指定平台
npm run tauri build -- --target x86_64  # Windows
npm run tauri build -- --target x86_64-apple-darwin  # macOS

# 打包产物路径
# Windows: client/src-tauri/target/release/bundle/nsis/
# macOS:   client/src-tauri/target/release/bundle/dmg/
```

| 检查项 | 预期 | 状态 |
|--------|------|------|
| [ ] Windows .exe 生成 | `NexusVideo_0.1.0_x64-setup.exe` | ☐ |
| [ ] macOS .dmg 生成 | `NexusVideo_0.1.0_x64.dmg` | ☐ |
| [ ] 构建无警告 | 0 warnings（或已记录已知警告） | ☐ |

### 2.3 签名步骤

> 详见 [`code-signing-guide.md`](./code-signing-guide.md)

**Windows**：
```powershell
signtool sign /fd SHA256 /f "$env:NEXUS_SIGN_CERT_PATH" `
  /p "$env:NEXUS_SIGN_PASSWORD" /tr http://timestamp.digicert.com `
  /td SHA256 "NexusVideo_0.1.0_x64-setup.exe"
signtool verify /pa /v "NexusVideo_0.1.0_x64-setup.exe"
```

**macOS**：
```bash
codesign --deep --force --verify --verbose --sign "$NEXUS_SIGN_IDENTITY" \
  --options runtime "NexusVideo.app"
xcrun notarytool submit "NexusVideo_0.1.0_x64.dmg" \
  --apple-id "$NEXUS_APPLE_ID" --password "@keychain:NEXUS_APP_PASSWORD" \
  --team-id "$NEXUS_TEAM_ID" --wait
xcrun stapler staple "NexusVideo.app"
spctl --assess --verbose "NexusVideo.app"
```

| 检查项 | 状态 |
|--------|------|
| [ ] Windows 签名完成 | ☐ |
| [ ] Windows 签名验证通过 | ☐ |
| [ ] macOS 签名完成 | ☐ |
| [ ] macOS 公证完成 | ☐ |
| [ ] macOS Stapling 完成 | ☐ |
| [ ] macOS Gatekeeper 验证通过 | ☐ |

### 2.4 体积检查

```bash
# Windows
# 目标：< 60MB
dir "target-release\NexusVideo_0.1.0_x64-setup.exe"

# macOS
# 目标：< 60MB
ls -lh "build/release/NexusVideo_0.1.0_x64.dmg"
```

| 平台 | 目标 | 实际 | 状态 |
|------|------|------|------|
| Windows .exe | < 60MB | ____ MB | ☐ |
| macOS .dmg | < 60MB | ____ MB | ☐ |

> **超限处理**：如超过 60MB，检查以下项目：
> 1. ComfyUI Sidecar 是否包含不必要的模型文件
> 2. Python 虚拟环境是否包含 `__pycache__` / `.pyc` 冗余
> 3. 是否嵌入了调试符号（Windows PDB 文件可剥离）
> 4. Tauri 构建是否使用 `--debug` 模式（应为 `--release`）

---

## 3. 构建后验证

> 负责：封易安（client-tauri-dev-5）+ 前端/后端同事配合

### 3.1 安装测试

> 在干净的虚拟机或物理机（未安装过 NexusVideo）上执行。

```
# Windows
双击 NexusVideo_0.1.0_x64-setup.exe
→ 确认无 SmartScreen 红屏（或显示"已识别的发布者"）
→ 按向导完成安装
→ 确认桌面快捷方式 + 开始菜单入口

# macOS
双击 NexusVideo_0.1.0_x64.dmg
→ 确认 Gatekeeper 无拦截
→ 拖拽到 Applications 文件夹
→ 首次打开确认无安全警告
```

| 检查项 | Windows | macOS |
|--------|---------|-------|
| [ ] 无安全警告 | ☐ | ☐ |
| [ ] 安装/部署成功 | ☐ | ☐ |
| [ ] 快捷方式正确 | ☐ | ☐ |

### 3.2 首次启动测试

```
# 首次启动行为验证
1. 启动 NexusVideo
2. 确认出现初始化页面
3. 确认 ComfyUI 子进程自动启动（后台日志可见）
4. 确认 ComfyUI 启动完成后界面跳转到主界面
5. 确认版本号正确（0.1.0）
```

| 检查项 | 预期 | 状态 |
|--------|------|------|
| [ ] 启动无崩溃 | 应用正常打开 | ☐ |
| [ ] 初始化页显示 | 显示引导内容 | ☐ |
| [ ] ComfyUI 自动启动 | 后台日志可见 | ☐ |
| [ ] 主界面跳转 | ComfyUI 就绪后自动跳转 | ☐ |
| [ ] 版本号正确 | 0.1.0 | ☐ |

### 3.3 三大模式冒烟测试

> 每种模式生成 1 次，确认完整链路可用。

#### 模式 A：文生视频

```
1. 选择"文生视频"模式
2. 输入提示词："A cat walking on the beach, cinematic lighting"
3. 点击"生成"
4. 确认进度条正常推进
5. 确认生成完成后视频可在客户端内预览
6. 确认视频文件已保存到本地输出目录
```

| 检查项 | 状态 |
|--------|------|
| [ ] 提交请求成功 | ☐ |
| [ ] 进度条正常 | ☐ |
| [ ] 视频生成成功 | ☐ |
| [ ] 预览可播放 | ☐ |

#### 模式 B：图生视频

```
1. 选择"图生视频"模式
2. 上传一张本地图片（测试用 PNG/JPG）
3. 输入提示词（可选）
4. 点击"生成"
5. 确认生成成功后可预览
```

| 检查项 | 状态 |
|--------|------|
| [ ] 图片上传成功 | ☐ |
| [ ] 提交请求成功 | ☐ |
| [ ] 视频生成成功 | ☐ |
| [ ] 预览可播放 | ☐ |

#### 模式 C：一键出片

```
1. 选择"一键出片"模式
2. 输入一段描述文本
3. 点击"一键生成"
4. 确认自动完成选题 → 生成 → 合成全流程
5. 确认最终视频可预览和下载
```

| 检查项 | 状态 |
|--------|------|
| [ ] 全流程自动执行 | ☐ |
| [ ] 视频生成成功 | ☐ |
| [ ] 预览可播放 | ☐ |

### 3.4 云端切换测试

```
1. 在设置页面切换"云端模式"
2. 确认 UI 显示云端状态（非本地模式）
3. 使用云端模式执行 1 次文生视频
4. 确认云端请求正常发出（不启动本地 ComfyUI）
5. 切换回"本地模式"
6. 确认本地 ComfyUI 自动启动
7. 使用本地模式执行 1 次文生视频
```

| 检查项 | 状态 |
|--------|------|
| [ ] 云端模式切换 | ☐ |
| [ ] 云端生成成功 | ☐ |
| [ ] 本地模式切换 | ☐ |
| [ ] 本地 ComfyUI 自动启动 | ☐ |
| [ ] 本地生成成功 | ☐ |

### 3.5 崩溃恢复测试

```
1. 启动生成任务
2. 在生成过程中，打开任务管理器 / Activity Monitor
3. 手动 kill ComfyUI 子进程
4. 观察客户端行为：
   - 应在 30 秒内检测到 ComfyUI 异常退出
   - 应自动重启 ComfyUI 子进程
   - UI 应显示"服务重启中"提示
   - 重启完成后任务队列应重新处理或提示用户
5. 确认无内存泄漏（任务管理器观察 2 分钟）
```

| 检查项 | 预期 | 状态 |
|--------|------|------|
| [ ] 检测异常退出 | < 30 秒 | ☐ |
| [ ] 自动重启 ComfyUI | 成功重启 | ☐ |
| [ ] UI 提示正确 | 显示重启状态 | ☐ |
| [ ] 无内存泄漏 | 内存稳定 | ☐ |

### 3.6 更新检查测试

```
# 模拟新版本
1. 修改 tauri.conf.json 中的 version 为 0.1.1（仅测试用）
2. 重新打包测试版本
3. 部署到更新服务器（测试端点）
4. 在原版本客户端中点击"检查更新"
5. 确认弹出更新对话框
6. 确认"更新"按钮可点击
7. 确认更新下载 + 安装流程正常
```

| 检查项 | 状态 |
|--------|------|
| [ ] 检测到新版本 | ☐ |
| [ ] 更新对话框显示 | ☐ |
| [ ] 下载正常 | ☐ |
| [ ] 安装正常 | ☐ |
| [ ] 重启后版本正确 | ☐ |

### 3.7 卸载测试

```
# Windows
控制面板 → 卸载程序 → 找到 NexusVideo → 卸载
→ 确认所有文件已清理
→ 确认无残留注册表项

# macOS
将 NexusVideo.app 拖入废纸篓 → 清空
→ 确认 ~/Library/Application Support/com.nexusvideo.client 已清理
→ 确认 ~/Library/Preferences/com.nexusvideo.client.plist 已清理
```

| 检查项 | Windows | macOS |
|--------|---------|-------|
| [ ] 卸载成功 | ☐ | ☐ |
| [ ] 文件清理完整 | ☐ | ☐ |
| [ ] 无残留配置 | ☐ | ☐ |

---

## 4. 分发前检查

| 检查项 | 状态 | 负责人 |
|--------|------|--------|
| [ ] 版本号已更新（tauri.conf.json） | ☐ | 封易安 |
| [ ] CHANGELOG 已更新 | ☐ | 封易安 |
| [ ] 更新服务器（releases.nexusvideo.com/update.json）已部署 | ☐ | DevOps |
| [ ] 签名安装包已上传到发布渠道 | ☐ | 封易安 |
| [ ] 云端 API 已部署新版本 | ☐ | Python 后端 |
| [ ] CDN 缓存已刷新 | ☐ | DevOps |

---

## 5. 回滚方案

### 5.1 触发条件

| 严重程度 | 描述 | 响应时间 |
|---------|------|---------|
| P0 | 应用无法启动 / 全面崩溃 | 立即回滚 |
| P1 | 核心功能不可用（生成全部失败） | 30 分钟内回滚 |
| P2 | 部分功能异常（特定模式失败） | 评估后决定 |

### 5.2 回滚步骤

```bash
# 1. 回滚更新服务器
# 将 update.json 中的 version 改回上一稳定版本
curl -X PUT https://releases.nexusvideo.com/api/update \
  -H "Content-Type: application/json" \
  -d '{"version": "0.0.9", "url": "https://releases.nexusvideo.com/NexusVideo_0.0.9_x64-setup.exe"}'

# 2. 通知团队
# 在飞书/Slack 群中发布回滚通知

# 3. 保留现场
# 保留崩溃日志、错误报告用于后续分析
```

### 5.3 回滚后动作

1. 创建 GitHub Issue 记录回滚原因
2. 分析根因
3. 修复后重新走完整发布流程

---

## 附录：快速命令参考

```bash
# ============ 环境检查 ============
node --version && rustc --version && cargo tauri --version

# ============ 构建 ============
cd client && npm ci && npm run tauri build

# ============ 签名（Windows） ============
signtool sign /fd SHA256 /f "$env:NEXUS_SIGN_CERT_PATH" /p "$env:NEXUS_SIGN_PASSWORD" ^
  /tr http://timestamp.digicert.com /td SHA256 "NexusVideo_setup.exe"

# ============ 签名（macOS） ============
codesign --deep --force --verify --verbose --sign "$NEXUS_SIGN_IDENTITY" --options runtime "NexusVideo.app"
xcrun notarytool submit "NexusVideo.dmg" --apple-id "$NEXUS_APPLE_ID" --password "@keychain:NEXUS_APP_PASSWORD" --team-id "$NEXUS_TEAM_ID" --wait
xcrun stapler staple "NexusVideo.app"
```
