# NexusVideo macOS 打包配置

> 平台: macOS 12 (Monterey) / 13 (Ventura) / 14 (Sonoma) / 15 (Sequoia)
> 架构: x64 (Intel) + ARM (Apple Silicon)
> 打包格式: DMG
> 签名: Developer ID Application + Notarization

---

## 1. 图标资源

### 必需文件

| 文件 | 尺寸 | 用途 |
|------|------|------|
| `icon.icns` | 多分辨率 | 应用图标（.app 包） |
| `128x128.png` | 128×128 | Finder 图标 |
| `128x128@2x.png` | 256×256 | Retina 图标 |
| `256x256.png` | 256×256 | 文件预览 |
| `256x256@2x.png` | 512×512 | Retina 预览 |
| `512x512.png` | 512×512 | App Store |
| `512x512@2x.png` | 1024×1024 | App Store Retina |
| `dmg-background.png` | 600×400 | DMG 安装界面背景 |

**存放路径**: `client/src-tauri/icons/`

### 生成 ICNS

```bash
# 使用 ImageMagick 生成 icns
magick convert source.png \
  -resize 16x16 icons/icon_16.png \
  -resize 32x32 icons/icon_32.png \
  -resize 64x64 icons/icon_64.png \
  -resize 128x128 icons/icon_128.png \
  -resize 256x256 icons/icon_256.png \
  -resize 512x512 icons/icon_512.png

# 或使用 sips（macOS 自带）
sips -Z 512 source.png --out icon_512.png

# 合成 icns
iconutil -c icns icon.iconset/
```

### DMG 背景图

macOS DMG 安装界面背景图，存放于 `client/src-tauri/icons/dmg-background.png`。
推荐尺寸: 1600×900（适配高分屏），会在显示时缩放到 DMG 窗口大小。

---

## 2. 代码签名配置

### tauri.conf.json 配置项

```json
"bundle": {
  "macOS": {
    "minimumSystemVersion": "12.0",
    "signingIdentity": "<PLACEHOLDER:Developer ID Application>",
    "hardenedRuntime": true,
    "entitlements": "entitlements.plist"
  }
}
```

| 配置项 | 说明 | 当前值 |
|--------|------|--------|
| `minimumSystemVersion` | 最低系统版本 | `12.0` (Monterey) |
| `signingIdentity` | 签名身份 | 占位符，替换为真实 Developer ID |
| `hardenedRuntime` | 启用 Hardened Runtime | `true`（公证必需） |
| `entitlements` | 权限描述文件 | `entitlements.plist` |

### 签名命令

```bash
# 签名 .app
codesign --deep --force --verify --verbose \
  --sign "Developer ID Application: Your Company (TEAM_ID)" \
  --options runtime \
  build/release/NexusVideo.app

# 创建并签名 DMG
hdiutil create -volname "NexusVideo" -srcfolder "build/release/NexusVideo.app" \
  -ov -format UDZO "build/release/NexusVideo_0.1.0_x64.dmg"
codesign --force --sign "Developer ID Application: Your Company (TEAM_ID)" \
  "build/release/NexusVideo_0.1.0_x64.dmg"

# Notarization
xcrun notarytool submit "build/release/NexusVideo_0.1.0_x64.dmg" \
  --apple-id "$NEXUS_APPLE_ID" \
  --password "@keychain:NEXUS_APP_PASSWORD" \
  --team-id "$NEXUS_TEAM_ID" \
  --wait

# Stapling
xcrun stapler staple "build/release/NexusVideo.app"
```

### 公证配置

```bash
# 存储 App-Specific Password 到 Keychain
xcrun notarytool store-password "$NEXUS_APP_PASSWORD" \
  --apple-id "$NEXUS_APPLE_ID" \
  --label "NEXUS_APP_PASSWORD"
```

---

## 3. Entitlements 配置

详见 `client/src-tauri/macos/entitlements.plist`，包含：
- **app-sandbox**: App Sandbox 权限
- **network-client**: 允许网络访问（连接云服务 + 自动更新）
- **com.apple.security.files.user-selected.read-write**: 用户选择文件的读写
- **com.apple.security.files.downloads.read-write**: 下载目录访问

---

## 4. 打包输出路径

| 架构 | 路径 |
|------|------|
| Intel (x64) | `client/src-tauri/target/release/bundle/dmg/NexusVideo_0.1.0_x64.dmg` |
| Apple Silicon (ARM) | `client/src-tauri/target/release/bundle/dmg/NexusVideo_0.1.0_aarch64.dmg` |
| Universal | `client/src-tauri/target/release/bundle/dmg/NexusVideo_0.1.0_universal.dmg` |

**目标大小**: < 100MB（不含 ComfyUI 模型文件）

---

## 5. 关联文档

- [代码签名完整指南](/docs/code-signing-guide.md)
- [发布检查清单](/docs/release-checklist.md)
- [macOS 签名脚本](../scripts/sign_macos.sh)