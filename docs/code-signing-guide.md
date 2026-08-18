# NexusVideo 代码签名配置指南

> 最后更新：2026-08-18
> 负责：封易安（client-tauri-dev-5）

---

## 目录

1. [为什么需要代码签名](#1-为什么需要代码签名)
2. [Windows 代码签名](#2-windows-代码签名)
3. [macOS 代码签名与公证](#3-macos-代码签名与公证)
4. [CI/CD 签名集成](#4-cicd-签名集成)
5. [证书轮换计划](#5-证书轮换计划)
6. [失败场景处理](#6-失败场景处理)
7. [环境变量速查表](#7-环境变量速查表)

---

## 1. 为什么需要代码签名

| 平台 | 不签名的后果 |
|------|-------------|
| **Windows** | SmartScreen 红屏警告"未知发布者"，部分用户直接关闭安装程序，转化率下降 30%+ |
| **macOS** | Gatekeeper 拦截，用户需要"系统设置 → 隐私与安全性 → 仍要打开"手动放行，小白用户直接放弃 |

签名后：
- **Windows**：SmartScreen 显示"已识别的发布者"，无红屏
- **macOS**：Gatekeeper 自动放行，首次安装零阻力

---

## 2. Windows 代码签名

### 2.1 购买 EV Code Signing Certificate

EV（Extended Validation）证书是 Microsoft SmartScreen 建立信誉的唯一方式，OV（Organization Validation）证书无此效果。

**推荐供应商**（按推荐顺序）：

| 供应商 | 年费（USD） | 智能卡要求 | 适用场景 |
|--------|------------|-----------|---------|
| **DigiCert** | ~$359/年 | 是 | 首选，与 Microsoft SmartScreen 对接最成熟 |
| **Sectigo** | ~$300/年 | 是 | 性价比高，支持 API 自动签名 |
| **GlobalSign** | ~$379/年 | 是 | 品牌知名度高 |

> **关键要求**：必须购买 **EV** 级别，不要买 OV。SmartScreen 信誉只在 EV 下积累。

**申请流程**：
1. 访问供应商官网，选择 "EV Code Signing Certificate"
2. 填写公司信息（必须与公司注册信息一致）
3. 上传营业执照 / 注册证明
4. 通过身份验证电话（通常 1-3 个工作日）
5. 收到智能卡（USB Token）+ 证书文件
6. 在本地机器安装智能卡驱动和证书

### 2.2 证书安装步骤

```powershell
# 步骤 1：插入 EV 智能卡 USB Token
# 系统会自动识别并提示安装驱动（通常已内置）

# 步骤 2：安装 PFX 证书到 Windows 证书存储
Import-Certificate -FilePath "C:\certs\NexusVideo_EV.pfx" -CertStoreLocation Cert:\CurrentUser\My -Password (ConvertTo-SecureString "YourPassword" -AsPlainText -Force)

# 步骤 3：验证证书已安装
Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -like "*NexusVideo*" }
```

**手动安装方式**（GUI）：
1. 双击 `.pfx` 文件，启动证书导入向导
2. 选择 "Current User"
3. 输入密码
4. 证书存储选择 "Personal"（个人）
5. **重要**：勾选 **"允许导出私钥"**（将私钥标记为可导出）
6. 完成

### 2.3 signtool 签名命令

> `signtool` 包含在 [Windows SDK Signing Tools](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/) 中。
> 安装路径通常为：`C:\Program Files (x86)\Windows Kits\10\bin\10.0.xxxxx.0\x64\signtool.exe`

```powershell
# 前置：设置环境变量
$env:NEXUS_SIGN_CERT_PATH = "C:\certs\NexusVideo_EV.pfx"
$env:NEXUS_SIGN_PASSWORD = "YourCertPassword"

# 签名命令
signtool sign ^
  /fd SHA256 ^
  /f "$env:NEXUS_SIGN_CERT_PATH" ^
  /p "$env:NEXUS_SIGN_PASSWORD" ^
  /tr http://timestamp.digicert.com ^
  /td SHA256 ^
  /n "NexusVideo" ^
  "target-release\NexusVideo_0.1.0_x64-setup.exe"
```

**参数说明**：

| 参数 | 含义 |
|------|------|
| `/fd SHA256` | 文件哈希算法使用 SHA256（微软要求的最低标准） |
| `/f cert.pfx` | 证书文件路径 |
| `/p password` | 证书密码 |
| `/tr` | RFC 3161 时间戳服务器（必须加时间戳，否则证书过期后签名失效） |
| `/td SHA256` | 时间戳哈希算法 |
| `/n` | 证书中使用的友好名称 |

### 2.4 签名验证命令

```powershell
# 验证签名（/pa = 信任链可包含根证书，/v = 详细输出）
signtool verify /pa /v "target-release\NexusVideo_0.1.0_x64-setup.exe"

# 预期输出：
# Number of files successfully Verified: 1
```

### 2.5 SmartScreen 信誉建立

| 阶段 | 时间 | 状态 |
|------|------|------|
| 首次签名发布 | T+0 | 可能仍有 SmartScreen 警告 |
| 微软爬取后 | T+24h | 开始积累信誉 |
| 稳定期 | T+48h ~ T+72h | SmartScreen 警告消失 |
| 成熟期 | T+30d | 完全信任 |

**加速方法**：
1. 发布后立即访问 [SmartScreen Submission](https://www.microsoft.com/en-us/wdsi/filesubmission)
2. 上传已签名的 .exe 文件
3. 等待审核（通常 2-7 天）

### 2.6 环境变量配置

```powershell
# 永久设置（Windows）
[Environment]::SetEnvironmentVariable("NEXUS_SIGN_CERT_PATH", "C:\certs\NexusVideo_EV.pfx", "Machine")
[Environment]::SetEnvironmentVariable("NEXUS_SIGN_PASSWORD", "YourCertPassword", "Machine")

# 验证
$env:NEXUS_SIGN_CERT_PATH
$env:NEXUS_SIGN_PASSWORD
```

---

## 3. macOS 代码签名与公证

### 3.1 Apple Developer ID 申请

**费用**：$99/年
**前提条件**：
- 必须是公司实体或个人（需要 DUA - Digital Information Services Agreement）
- 需要 D-U-N-S Number（邓白氏编码，[申请链接](https://www.dnb.com/dunsnumber.html)，免费，3-5 个工作日）

**申请流程**：
1. 访问 [Apple Developer Program](https://developer.apple.com/programs/enroll/)
2. 选择 "Apple Developer Program"（$99/年）
3. 填写组织信息
4. 上传营业执照 + D-U-N-S Number
5. 等待 Apple 审核（1-3 个工作日）
6. 登录后在 **Certificates, Identifiers & Profiles** → **Certificates** → **Developer ID Application** 创建证书
7. 下载 `.cer` 文件，双击安装到 Keychain

### 3.2 Keychain 证书安装

```bash
# 1. 双击 .cer 文件安装到 Keychain Access
# 2. 验证安装
security find-identity -v -p codesigning

# 预期输出：
# 1) ABC123DEF456 "Developer ID Application: Your Company (TEAM_ID)"
#    <KeyID> <Hash>

# 3. 如果签名失败，展开 Keychain 中的证书 → 信任 → 代码签名设为"始终信任"
```

### 3.3 签名命令

```bash
# 前置：设置环境变量
export NEXUS_TEAM_ID="TEAM_ID"      # 从 Apple Developer Portal 获取
export NEXUS_SIGN_IDENTITY="Developer ID Application: Your Company (TEAM_ID)"

# 签名 .app
codesign --deep --force --verify --verbose --sign "$NEXUS_SIGN_IDENTITY" \
  --options runtime \
  build/release/NexusVideo.app

# 参数说明：
#   --deep       : 递归签名所有嵌套组件（frameworks、plugins 等）
#   --force      : 覆盖已有签名
#   --verify     : 签名后自动验证
#   --verbose    : 详细输出
#   --options runtime : 启用 App Sandbox + Hardened Runtime（公证必需）

# 验证签名
codesign --verify --deep --verbose=4 build/release/NexusVideo.app
```

### 3.4 生成 DMG 并签名

```bash
# 1. 创建 DMG
hdiutil create \
  -volname "NexusVideo" \
  -srcfolder "build/release/NexusVideo.app" \
  -ov -format UDZO \
  "build/release/NexusVideo_0.1.0_x64.dmg"

# 2. 签名 DMG
codesign --force --sign "$NEXUS_SIGN_IDENTITY" \
  "build/release/NexusVideo_0.1.0_x64.dmg"

# 3. 验证 DMG 签名
codesign --verify --deep --verbose=4 "build/release/NexusVideo_0.1.0_x64.dmg"
```

### 3.5 Notarization（公证）

> 公证是 macOS 10.14+ 上 Gatekeeper 放行的必要条件。未公证的 App 即使签名也会被拦截。

```bash
# 前置：生成 App-Specific Password
# 1. 访问 https://appleid.apple.com → 密码与安全性 → 应用专用密码
# 2. 设置环境变量
export NEXUS_APPLE_ID="your-email@nexusvideo.com"
export NEXUS_TEAM_ID="TEAM_ID"
export NEXUS_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"

# 1. 提交公证
xcrun notarytool submit "build/release/NexusVideo_0.1.0_x64.dmg" \
  --apple-id "$NEXUS_APPLE_ID" \
  --password "@keychain:NEXUS_APP_PASSWORD" \
  --team-id "$NEXUS_TEAM_ID" \
  --wait

# --wait 表示等待公证完成（最长 15 分钟）
# 输出示例：
# Submitting "NexusVideo_0.1.0_x64.dmg"
# Waiting for submission to complete...
# ✓ Accepted (id: abc123-...)
# ✓ Success
#   date: 2026-08-18T10:00:00Z
#   path: /Applications/NexusVideo.app
#   os: macOS 14.0
#   type: notary

# 2. Stapling（将公证结果嵌入 App）
# 用户联网时会直接验证，否则使用嵌入的公证结果
xcrun stapler staple "build/release/NexusVideo.app"

# 3. 验证 Stapling
xcrun stapler validate "build/release/NexusVideo.app"

# 4. 验证 Gatekeeper 放行
spctl --assess --verbose "build/release/NexusVideo.app"
# 预期输出：
# build/release/NexusVideo.app: accepted
# source=Developer ID; authority=Developer ID Application: Your Company (TEAM_ID)
```

### 3.6 公证历史查询

```bash
# 查看所有公证记录
xcrun notarytool history \
  --apple-id "$NEXUS_APPLE_ID" \
  --team-id "$NEXUS_TEAM_ID"
```

### 3.7 macOS 环境变量配置

```bash
# 添加到 ~/.zshrc 或 ~/.bash_profile
export NEXUS_APPLE_ID="your-email@nexusvideo.com"
export NEXUS_TEAM_ID="TEAM_ID"
export NEXUS_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"

# 在 Keychain 中安全存储密码（推荐）
# 将密码存入 macOS Keychain，然后引用：--password "@keychain:NEXUS_APP_PASSWORD"
xcrun notarytool store-password "$NEXUS_APP_PASSWORD" --apple-id "$NEXUS_APPLE_ID" \
  --label "NEXUS_APP_PASSWORD"
```

---

## 4. CI/CD 签名集成

### 4.1 GitHub Actions 签名步骤

> **绝不将密码/密钥硬编码在 workflow 文件中**。使用 GitHub Secrets 存储。

```yaml
# .github/workflows/release.yml（摘要，完整版本见 devops 文档）
jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      # ... 构建步骤 ...
      - name: Sign executable
        if: env.NEXUS_SIGN_CERT_PATH != ''
        run: |
          $securePassword = ConvertTo-SecureString "${{ secrets.NEXUS_SIGN_PASSWORD }}" -AsPlainText -Force
          signtool sign /fd SHA256 /f "${{ secrets.NEXUS_SIGN_CERT_PATH }}" `
            /p $securePassword /tr http://timestamp.digicert.com `
            /td SHA256 "target-release/NexusVideo_0.1.0_x64-setup.exe"
        env:
          NEXUS_SIGN_CERT_PATH: ${{ secrets.NEXUS_SIGN_CERT_PATH }}
          NEXUS_SIGN_PASSWORD: ${{ secrets.NEXUS_SIGN_PASSWORD }}

  build-macos:
    runs-on: macos-latest
    steps:
      # ... 构建步骤 ...
      - name: Sign and notarize
        if: env.NEXUS_APPLE_ID != ''
        run: |
          # Store app-specific password in keychain
          xcrun notarytool store-password "${{ secrets.NEXUS_APP_PASSWORD }}" \
            --apple-id "${{ secrets.NEXUS_APPLE_ID }}" \
            --label "NEXUS_APP_PASSWORD"

          # Sign
          codesign --deep --force --verify --verbose --sign "${{ secrets.NEXUS_SIGN_IDENTITY }}" \
            --options runtime "build/release/NexusVideo.app"

          # Create and sign DMG
          hdiutil create -volname "NexusVideo" -srcfolder "build/release/NexusVideo.app" \
            -ov -format UDZO "build/release/NexusVideo_0.1.0_x64.dmg"
          codesign --force --sign "${{ secrets.NEXUS_SIGN_IDENTITY }}" \
            "build/release/NexusVideo_0.1.0_x64.dmg"

          # Notarize
          xcrun notarytool submit "build/release/NexusVideo_0.1.0_x64.dmg" \
            --apple-id "${{ secrets.NEXUS_APPLE_ID }}" \
            --password "@keychain:NEXUS_APP_PASSWORD" \
            --team-id "${{ secrets.NEXUS_TEAM_ID }}" \
            --wait

          # Staple
          xcrun stapler staple "build/release/NexusVideo.app"
        env:
          NEXUS_APPLE_ID: ${{ secrets.NEXUS_APPLE_ID }}
          NEXUS_TEAM_ID: ${{ secrets.NEXUS_TEAM_ID }}
          NEXUS_APP_PASSWORD: ${{ secrets.NEXUS_APP_PASSWORD }}
          NEXUS_SIGN_IDENTITY: ${{ secrets.NEXUS_SIGN_IDENTITY }}
```

### 4.2 GitHub Secrets 配置

在 GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret 中添加：

| Secret 名称 | 内容 | 平台 |
|------------|------|------|
| `NEXUS_SIGN_CERT_PATH` | 证书文件路径（CI 中通过 action 下载） | Windows |
| `NEXUS_SIGN_PASSWORD` | 证书密码 | Windows |
| `NEXUS_SIGN_IDENTITY` | `Developer ID Application: Company (TEAM_ID)` | macOS |
| `NEXUS_APPLE_ID` | Apple ID 邮箱 | macOS |
| `NEXUS_TEAM_ID` | 团队 ID（如 `ABC123XYZ4`） | macOS |
| `NEXUS_APP_PASSWORD` | Apple App-Specific Password | macOS |

### 4.3 Windows 证书在 CI 中的使用

```yaml
# 将 PFX 证书上传为 GitHub Secret（base64 编码）
- name: Download signing certificate
  run: |
    [System.IO.File]::WriteAllBytes("$env:USERPROFILE\NexusVideo_EV.pfx",
      [System.Convert]::FromBase64String("${{ secrets.NEXUS_SIGN_PFX_BASE64 }}"))
  env:
    NEXUS_SIGN_CERT_PATH: ${{ secrets.NEXUS_SIGN_CERT_PATH }}
```

---

## 5. 证书轮换计划

| 证书 | 有效期 | 续期提醒时间 | 操作 |
|------|--------|-------------|------|
| Windows EV 证书 | 1 年 | 到期前 60 天 | 重新申请 → 等待审核 → 替换旧证书 |
| Apple Developer ID | 1 年 | 到期前 60 天 | Apple 自动续期（已开通自动续费） |
| Apple App-Specific Password | 长期 | 每年轮换 | 生成新密码 → 更新 CI Secrets |

**续期流程**：
1. **T-60d**：收到日历提醒，确认预算
2. **T-45d**：提交续期申请
3. **T-30d**：完成身份验证
4. **T-14d**：安装新证书，配置 CI
5. **T-7d**：使用新证书打测试包，验证签名
6. **T+0**：新证书生效，旧证书进入 14 天宽限期

---

## 6. 失败场景处理

### 6.1 Windows：证书过期

**症状**：`signtool` 报错 "SignerSign() failed" 或 SmartScreen 仍显示警告。

**处理**：
```powershell
# 检查证书有效期
Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -like "*NexusVideo*" } | Format-List *

# 重新签名（确保旧签名的 .exe 有 timestamp，否则过期后签名失效）
signtool sign /fd SHA256 /f "new_cert.pfx" /p "new_password" ^
  /tr http://timestamp.digicert.com /td SHA256 "NexusVideo_setup.exe"
```

### 6.2 Windows：SmartScreen 误报

**症状**：签名有效但 SmartScreen 仍提示"Windows 已保护你的电脑"。

**处理**：
1. 访问 https://www.microsoft.com/en-us/wdsi/filesubmission
2. 上传 .exe 文件 + 说明使用场景
3. 等待 2-7 个工作日处理
4. 临时方案：在 README 中告知用户"点击更多信息 → 仍要运行"

### 6.3 macOS：公证失败

**常见错误**：

| 错误 | 原因 | 修复 |
|------|------|------|
| `code 6` | 缺少必需 entitlements | 检查 `entitlements.plist` |
| `code 4` | 签名无效或过期 | 重新签名 |
| `code 1093` | 提交队列满 | 等待 10 分钟后重试 |
| `code 1076` | 提交过于频繁 | 每 5 分钟限 1 次 |

**调试命令**：
```bash
# 查看公证日志
xcrun notarytool log <submission-id> \
  --apple-id "$NEXUS_APPLE_ID" \
  --team-id "$NEXUS_TEAM_ID"

# 常见日志：
# - "Your executable does not match the minimum operating system requirements"
#   → 提高 Info.plist 的 LSMinimumSystemVersion
# - "The binary is not a Mach-O binary"
#   → 确保使用 --deep 递归签名
```

### 6.4 macOS：Gatekeeper 拦截

**症状**：用户打开 App 时提示"无法打开，因为 Apple 无法检查其是否包含恶意软件"。

**处理**：
1. 确认已完成公证 + Stapling
2. 如果 Stapling 失败（用户离线）：
   ```bash
   # 用户手动验证
   spctl --assess --verbose "/Applications/NexusVideo.app"
   # 如果通过，在系统设置 → 隐私与安全性 → 仍要打开
   ```
3. 如果证书被吊销：
   ```bash
   # 重新公证 + Stapling
   xcrun notarytool submit "NexusVideo_0.1.0_x64.dmg" \
     --apple-id "$NEXUS_APPLE_ID" \
     --password "@keychain:NEXUS_APP_PASSWORD" \
     --team-id "$NEXUS_TEAM_ID" --wait
   xcrun stapler staple "/Applications/NexusVideo.app"
   ```

### 6.5 macOS：`xcrun: error: insufficient privileges`

```bash
# 修复 Xcode 命令行工具权限
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer

# 确认
xcode-select -p
```

---

## 7. 环境变量速查表

### Windows

| 变量名 | 示例值 | 用途 |
|--------|--------|------|
| `NEXUS_SIGN_CERT_PATH` | `C:\certs\NexusVideo_EV.pfx` | 证书文件路径 |
| `NEXUS_SIGN_PASSWORD` | `YourCertPassword` | 证书密码 |

### macOS

| 变量名 | 示例值 | 用途 |
|--------|--------|------|
| `NEXUS_APPLE_ID` | `your-email@nexusvideo.com` | Apple ID |
| `NEXUS_TEAM_ID` | `ABC123XYZ4` | 开发者团队 ID |
| `NEXUS_APP_PASSWORD` | `xxxx-xxxx-xxxx-xxxx` | App 专用密码 |
| `NEXUS_SIGN_IDENTITY` | `Developer ID Application: Company (TEAM_ID)` | 签名身份 |

### CI/CD（GitHub Secrets）

| Secret 名称 | 用途 |
|------------|------|
| `NEXUS_SIGN_PFX_BASE64` | Windows 证书 base64 编码（CI 用） |
| `NEXUS_SIGN_PASSWORD` | Windows 证书密码 |
| `NEXUS_SIGN_IDENTITY` | macOS 签名身份 |
| `NEXUS_APPLE_ID` | Apple ID |
| `NEXUS_TEAM_ID` | 团队 ID |
| `NEXUS_APP_PASSWORD` | App 专用密码 |

---

## 附录：常用命令速查

```powershell
# ============ Windows ============

# 安装证书
Import-Certificate -FilePath "cert.pfx" -CertStoreLocation Cert:\CurrentUser\My -Password (ConvertTo-SecureString "pwd" -AsPlainText -Force)

# 签名
signtool sign /fd SHA256 /f "cert.pfx" /p "pwd" /tr http://timestamp.digicert.com /td SHA256 "app.exe"

# 验证
signtool verify /pa /v "app.exe"

# ============ macOS ============

# 查找签名身份
security find-identity -v -p codesigning

# 签名 App
codesign --deep --force --verify --verbose --sign "Developer ID Application: Company (TEAM_ID)" --options runtime "App.app"

# 创建并签名 DMG
hdiutil create -volname "App" -srcfolder "App.app" -ov -format UDZO "App.dmg"
codesign --force --sign "Developer ID Application: Company (TEAM_ID)" "App.dmg"

# 公证
xcrun notarytool submit "App.dmg" --apple-id "$NEXUS_APPLE_ID" --password "@keychain:NEXUS_APP_PASSWORD" --team-id "$NEXUS_TEAM_ID" --wait

# Stapling
xcrun stapler staple "App.app"

# Gatekeeper 验证
spctl --assess --verbose "App.app"
```
