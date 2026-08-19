#!/bin/bash
# ============================================================================
# sign_macos.sh — NexusVideo macOS 签名 + 公证脚本
# ============================================================================
# 用途：对 Tauri 构建产出的 .app 进行 codesign + Notarization + Stapling
#
# 使用方式：
#   1. 确保 NEXUS_TEAM_ID, NEXUS_APPLE_ID 等环境变量已设置
#   2. 运行本脚本，指定 .app 路径（默认读取环境变量）
#
# 环境变量要求：
#   NEXUS_TEAM_ID      = "TEAM_ID"           (从 Apple Developer Portal 获取)
#   NEXUS_APPLE_ID     = "your@apple.com"    (Apple ID 邮箱)
#   NEXUS_APP_PASSWORD = "xxxx-xxxx-xxxx-xxxx" (App-Specific Password)
#   NEXUS_SIGN_IDENTITY= "Developer ID Application: Company (TEAM_ID)"
#
# 用法：
#   export NEXUS_TEAM_ID="ABC123XYZ4"
#   export NEXUS_APPLE_ID="dev@nexusvideo.com"
#   export NEXUS_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
#   export NEXUS_SIGN_IDENTITY="Developer ID Application: NexusVideo (ABC123XYZ4)"
#   ./sign_macos.sh target/release/NexusVideo.app
# ============================================================================

set -euo pipefail

# ---- 默认路径 ----
APP_PATH="${1:-target/release/NexusVideo.app}"
DMG_NAME="NexusVideo_$(cat ../../tauri.conf.json | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])")_x64.dmg"

# ---- 检查环境变量 ----
: "${NEXUS_TEAM_ID:?错误: 请设置 NEXUS_TEAM_ID (如 ABC123XYZ4)}"
: "${NEXUS_APPLE_ID:?错误: 请设置 NEXUS_APPLE_ID (如 dev@nexusvideo.com)}"
: "${NEXUS_APP_PASSWORD:?错误: 请设置 NEXUS_APP_PASSWORD (App-Specific Password)}"
: "${NEXUS_SIGN_IDENTITY:?错误: 请设置 NEXUS_SIGN_IDENTITY}"

echo "============================================"
echo " NexusVideo macOS 签名 + 公证"
echo "============================================"
echo ""
echo "  App:        $APP_PATH"
echo "  Team ID:    $NEXUS_TEAM_ID"
echo "  Apple ID:   $NEXUS_APPLE_ID"
echo "  Sign ID:    $NEXUS_SIGN_IDENTITY"
echo ""

# ---- 检查 .app 是否存在 ----
if [ ! -d "$APP_PATH" ]; then
    echo "[ERROR] App 不存在: $APP_PATH"
    echo "        请确认 Tauri build 已完成，并传入正确的 .app 路径"
    exit 1
fi

# ---- 步骤 1: 签名 .app ----
echo "=== 步骤 1: 签名 .app ==="
codesign --deep --force --verify --verbose=2 \
  --sign "$NEXUS_SIGN_IDENTITY" \
  --options runtime \
  "$APP_PATH"

echo "  [OK] .app 签名完成"
echo ""

# ---- 步骤 2: 验证签名 ----
echo "=== 步骤 2: 验证签名 ==="
codesign --verify --deep --verbose=2 "$APP_PATH"
echo "  [OK] 签名验证通过"
echo ""

# ---- 步骤 3: 生成 DMG ----
echo "=== 步骤 3: 生成 DMG ==="
OUTPUT_DIR="$(dirname "$APP_PATH")"
DMG_PATH="${OUTPUT_DIR}/${DMG_NAME}"
# 如果 DMG 已存在则删除
[ -f "$DMG_PATH" ] && rm -f "$DMG_PATH"

hdiutil create \
  -volname "NexusVideo" \
  -srcfolder "$APP_PATH" \
  -ov -format UDZO \
  "$DMG_PATH"

echo "  [OK] DMG 已创建: $DMG_PATH"
echo ""

# ---- 步骤 4: 签名 DMG ----
echo "=== 步骤 4: 签名 DMG ==="
codesign --force --sign "$NEXUS_SIGN_IDENTITY" "$DMG_PATH"
codesign --verify --deep --verbose=2 "$DMG_PATH"
echo "  [OK] DMG 签名完成"
echo ""

# ---- 步骤 5: 存储 App-Specific Password ----
echo "=== 步骤 5: 存储 App-Specific Password ==="
xcrun notarytool store-password "$NEXUS_APP_PASSWORD" \
  --apple-id "$NEXUS_APPLE_ID" \
  --label "NEXUS_APP_PASSWORD"
echo "  [OK] Password 已存储到 Keychain"
echo ""

# ---- 步骤 6: Notarization 公证 ----
echo "=== 步骤 6: Notarization ==="
xcrun notarytool submit "$DMG_PATH" \
  --apple-id "$NEXUS_APPLE_ID" \
  --password "@keychain:NEXUS_APP_PASSWORD" \
  --team-id "$NEXUS_TEAM_ID" \
  --wait

echo "  [OK] Notarization 完成"
echo ""

# ---- 步骤 7: Stapling ----
echo "=== 步骤 7: Stapling ==="
xcrun stapler staple "$APP_PATH"
xcrun stapler validate "$APP_PATH"
echo "  [OK] Stapling 完成"
echo ""

# ---- 步骤 8: Gatekeeper 验证 ----
echo "=== 步骤 8: Gatekeeper 验证 ==="
spctl --assess --verbose=2 "$APP_PATH"
echo "  [OK] Gatekeeper 验证通过"
echo ""

# ---- 输出摘要 ----
echo "============================================"
echo " 签名 + 公证完成!"
echo "============================================"
echo ""
echo " 产出文件："
echo "  .app: $APP_PATH"
echo "  .dmg: $DMG_PATH"
echo ""
echo " 下一步："
echo "  1. 上传 .dmg 到更新服务器"
echo "  2. 生成 update.json"
echo "  3. 发布 GitHub Release"
echo ""