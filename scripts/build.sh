#!/bin/bash
# ============================================================================
# NexusVideo — macOS / Linux 打包脚本 (build.sh)
# 架构师：封易安 (client-tauri-dev)
# 说明：在 macOS 上执行，产出 .dmg 安装包；Linux 上产出 .AppImage
#
# 使用方式:
#   chmod +x scripts/build.sh
#   ./scripts/build.sh
#
# 前置依赖:
#   - Rust toolchain (rustc >= 1.77)
#   - cargo-tauri (cargo install tauri-cli@latest)
#   - Node.js >= 18 + npm
#   - Xcode Command Line Tools (macOS)
#   - 签名证书已安装 (Keychain Access)
#
# 输出路径:
#   client/src-tauri/target/release/bundle/dmg/NexusVideo_0.1.0_x64.dmg
#   client/src-tauri/target/release/bundle/appimage/NexusVideo_0.1.0_amd64.AppImage
#
# 安装包体积估算:
#   Tauri app 框架 (~30MB) + 前端资源 (~5MB) + Rust 二进制 (~15MB)
#   + ComfyUI 引擎 (首次启动时下载，不嵌入) = 约 50MB
#   目标: < 100MB
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CLIENT_DIR="$PROJECT_DIR/client"
TAURI_DIR="$CLIENT_DIR/src-tauri"

echo "=============================================="
echo " NexusVideo — macOS/Linux 打包"
echo " 项目根目录: $PROJECT_DIR"
echo "=============================================="

# ---- 步骤 1: 安装前端依赖 ----
echo ""
echo "[1/5] 安装前端依赖..."
cd "$CLIENT_DIR"
npm ci
echo "  ✓ 前端依赖安装完成"

# ---- 步骤 2: 构建前端 ----
echo ""
echo "[2/5] 构建前端 (npm run build)..."
npm run build
echo "  ✓ 前端构建完成，产物: $CLIENT_DIR/dist/"

# ---- 步骤 3: 检测 Rust 工具链 ----
echo ""
echo "[3/5] 检测 Rust 工具链..."
if ! command -v cargo &> /dev/null; then
    echo "  ✗ 未找到 cargo，请先安装 Rust: https://rustup.rs/"
    exit 1
fi
RUST_VERSION=$(rustc --version)
echo "  ✓ Rust: $RUST_VERSION"

if ! command -v cargo-tauri &> /dev/null && ! cargo tauri --version &> /dev/null; then
    echo "  ⚠ 未找到 cargo-tauri，尝试安装..."
    cargo install tauri-cli@latest
fi
echo "  ✓ cargo-tauri 就绪"

# ---- 步骤 4: 清理旧构建产物 ----
echo ""
echo "[4/5] 清理旧构建产物..."
cd "$TAURI_DIR"
cargo clean
echo "  ✓ 清理完成"

# ---- 步骤 5: Tauri 打包 ----
echo ""
echo "[5/5] Tauri 打包构建..."
# macOS: 同时产出 Intel + Apple Silicon 通用二进制
# Linux: 产出 AppImage
if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "  平台: macOS"
    echo "  目标: universal-apple-darwin (Intel + Apple Silicon)"
    # universal-apple-darwin 需要交叉编译，生产环境使用
    # 开发环境先用本机架构
    # cargo tauri build --target universal-apple-darwin
    cargo tauri build
    echo "  ✓ macOS 打包完成"
    echo ""
    echo "  📦 输出文件:"
    echo "    target/release/bundle/dmg/NexusVideo_*_x64.dmg"
    echo "    target/release/bundle/macos/NexusVideo.app"
else
    echo "  平台: Linux"
    cargo tauri build
    echo "  ✓ Linux 打包完成"
    echo ""
    echo "  📦 输出文件:"
    echo "    target/release/bundle/appimage/NexusVideo_*_amd64.AppImage"
fi

# ---- 步骤 6: 签名 (macOS Notarization) ----
echo ""
echo "[额外] macOS 签名 & 公证 (如配置了证书)..."
if [[ "$(uname -s)" == "Darwin" ]]; then
    APP_PATH=$(find target/release/bundle/macos -name "*.app" 2>/dev/null | head -1)
    if [[ -n "$APP_PATH" ]]; then
        # 检查是否有 Developer ID 证书
        CERT_ID=$(security find-identity -v -p codesigning | grep "Developer ID Application" | head -1 | awk -F'"' '{print $2}')
        if [[ -n "$CERT_ID" ]]; then
            echo "  使用证书: $CERT_ID"
            codesign --force --deep --sign "$CERT_ID" "$APP_PATH"
            echo "  ✓ 代码签名完成"

            # Notarization (需要 APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / TEAM_ID 环境变量)
            if [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" && -n "${TEAM_ID:-}" ]]; then
                DMG_PATH=$(find target/release/bundle/dmg -name "*.dmg" 2>/dev/null | head -1)
                if [[ -n "$DMG_PATH" ]]; then
                    echo "  开始公证 Notarization..."
                    xcrun notarytool submit "$DMG_PATH" \
                        --apple-id "$APPLE_ID" \
                        --password "$APPLE_APP_SPECIFIC_PASSWORD" \
                        --team-id "$TEAM_ID" \
                        --wait
                    echo "  ✓ Notarization 完成"
                    xcrun stapler staple "$APP_PATH"
                    echo "  ✓ Stapling 完成"
                fi
            else
                echo "  ⚠ 未设置 APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / TEAM_ID"
                echo "    跳过 Notarization，签名后不可在 macOS Gatekeeper 通过验证"
            fi
        else
            echo "  ⚠ 未找到 'Developer ID Application' 证书"
            echo "    请先从 Apple Developer 申请并安装到 Keychain"
        fi
    fi
fi

# ---- 完成 ----
echo ""
echo "=============================================="
echo " ✅ NexusVideo 打包完成！"
echo "=============================================="
echo ""
echo "  产物位置: $TAURI_DIR/target/release/bundle/"
echo ""
echo "  各平台安装包:"
echo "    macOS:  target/release/bundle/dmg/*.dmg"
echo "    Linux:  target/release/bundle/appimage/*.AppImage"
echo ""
echo "  安装包体积: $(du -sh $TAURI_DIR/target/release/bundle/ 2>/dev/null | cut -f1)"
echo ""
echo "  下一步:"
echo "    1. 代码签名 (Windows EV Cert / macOS Developer ID)"
echo "    2. 上传至 GitHub Release 或 https://releases.nexusvideo.com/"
echo "    3. 配置 Tauri Updater update.json 端点"
echo "=============================================="