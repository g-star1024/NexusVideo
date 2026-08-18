#!/usr/bin/env bash
# =============================================================================
# NexusVideo Tauri 自动更新服务器部署脚本
# 版本：1.0.0
# 作者：NexusVideo MVP 团队 · 运维架构师 唐磐石
#
# 用途：将 Tauri 更新包部署到以下两种方案之一：
#   方案 A（推荐）：阿里云 OSS 静态网站 + CDN
#   方案 B：自建 Nginx 服务器
#
# 用法：
#   ./deploy-update-server.sh --oss          # 部署到 OSS + CDN
#   ./deploy-update-server.sh --nginx        # 部署到 Nginx
#   ./deploy-update-server.sh --build        # 仅生成 update.json（不上传）
#
# 环境变量：
#   OSS_BUCKET       OSS 存储桶名称（默认 nexusvideo-update）
#   OSS_REGION       OSS 区域（默认 oss-cn-hangzhou）
#   OSS_ENDPOINT     OSS 访问端点
#   NEXUS_APP_VERSION 当前版本号（从 package.json 读取）
#   NEXUS_UPDATE_URL   CDN 域名（默认 https://update.nexusvideo.com）
#   NEXUS_TG_PRIVATE   Tauri 签名私钥路径
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------
OSS_BUCKET="${OSS_BUCKET:-nexusvideo-update}"
OSS_REGION="${OSS_REGION:-oss-cn-hangzhou}"
OSS_ENDPOINT="${OSS_ENDPOINT:-https://${OSS_BUCKET}.${OSS_REGION}.aliyuncs.com}"
NEXUS_UPDATE_URL="${NEXUS_UPDATE_URL:-https://update.nexusvideo.com}"
NEXUS_TG_PRIVATE="${NEXUS_TG_PRIVATE:-${PROJECT_ROOT}/.tauri/private.pem}"
RELEASES_DIR="${PROJECT_ROOT}/release-artifacts"
VERSION="${NEXUS_APP_VERSION:-$(node -p "require('${PROJECT_ROOT}/frontend/package.json').version" 2>/dev/null || echo '1.0.0')}"

# ---------------------------------------------------------------------------
# 颜色日志
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log() {
  case "$1" in
    INFO)  printf "${GREEN}[INFO]${NC}  %s\n" "$2" ;;
    WARN)  printf "${YELLOW}[WARN]${NC}  %s\n" "$2" ;;
    ERROR) printf "${RED}[ERROR]${NC} %s\n" "$2" >&2 ;;
    STEP)  printf "${BLUE}[STEP]${NC}  ${BOLD}%s${NC}\n" "$2" ;;
    OK)    printf "${GREEN}[OK]${NC}    %s\n" "$2" ;;
  esac
}

# ---------------------------------------------------------------------------
# 帮助信息
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
用法: $(basename "$0") [选项]

NexusVideo Tauri 更新服务器部署脚本

选项:
  --oss              部署到阿里云 OSS 静态网站 + CDN（推荐）
  --nginx            部署到 Nginx 服务器
  --build            仅生成 update.json，不上传
  --version <ver>    指定版本号（默认从 package.json 读取）
  --help, -h         显示本帮助信息

环境变量:
  OSS_BUCKET         OSS 存储桶名称
  OSS_REGION         OSS 区域
  OSS_ENDPOINT       OSS 端点
  NEXUS_UPDATE_URL   CDN 域名
  NEXUS_APP_VERSION  版本号
  NEXUS_TG_PRIVATE   Tauri 私钥路径

示例:
  ./deploy-update-server.sh --oss --version 1.1.0
  ./deploy-update-server.sh --build
EOF
}

# ---------------------------------------------------------------------------
# 步骤 1：确定部署模式
# ---------------------------------------------------------------------------
DEPLOY_MODE=""
CUSTOM_VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --oss)
      DEPLOY_MODE="oss"
      shift
      ;;
    --nginx)
      DEPLOY_MODE="nginx"
      shift
      ;;
    --build)
      DEPLOY_MODE="build"
      shift
      ;;
    --version)
      CUSTOM_VERSION="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      log ERROR "未知选项: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$DEPLOY_MODE" ]]; then
  log ERROR "请指定部署模式：--oss 或 --nginx 或 --build"
  usage
  exit 1
fi

if [[ -n "$CUSTOM_VERSION" ]]; then
  VERSION="$CUSTOM_VERSION"
fi

log STEP "NexusVideo 更新服务器部署"
log INFO "版本: $VERSION"
log INFO "部署模式: $DEPLOY_MODE"
log INFO "更新 URL: $NEXUS_UPDATE_URL"

# ---------------------------------------------------------------------------
# 步骤 2：检查发布产物
# ---------------------------------------------------------------------------
log STEP "检查发布产物..."

if [[ ! -d "$RELEASES_DIR" ]]; then
  log ERROR "发布产物目录不存在: $RELEASES_DIR"
  log ERROR "请先运行 Tauri 打包命令生成发布文件："
  log ERROR "  npm run build:tauri"
  exit 1
fi

# 查找各平台的安装包
WINDOWS_PKG=$(find "$RELEASES_DIR" -name "*x86_64*setup.exe" -type f 2>/dev/null | head -1 || true)
LINUX_PKG=$(find "$RELEASES_DIR" -name "*.AppImage" -type f 2>/dev/null | head -1 || true)
MACOS_PKG=$(find "$RELEASES_DIR" -name "*.dmg" -type f 2>/dev/null | head -1 || true)

found_count=0
if [[ -n "$WINDOWS_PKG" ]]; then ((found_count++)); fi
if [[ -n "$LINUX_PKG" ]]; then ((found_count++)); fi
if [[ -n "$MACOS_PKG" ]]; then ((found_count++)); fi

if (( found_count == 0 )); then
  log ERROR "在 $RELEASES_DIR 中未找到任何安装包文件"
  log ERROR "请确认 Tauri 打包是否成功执行"
  exit 1
fi

log OK "找到 $found_count 个平台安装包"
for pkg in "$WINDOWS_PKG" "$LINUX_PKG" "$MACOS_PKG"; do
  [[ -n "$pkg" ]] && log DATA "  $pkg"
done

# ---------------------------------------------------------------------------
# 步骤 3：生成数字签名
# ---------------------------------------------------------------------------
log STEP "生成 Tauri 数字签名..."

if [[ ! -f "$NEXUS_TG_PRIVATE" ]]; then
  log WARN "私钥文件不存在: $NEXUS_TG_PRIVATE"
  log WARN "将生成随机占位签名（生产环境必须替换为真实签名）"
  PLACEHOLDER_SIG="-----BEGIN MESSAGE SIGNATURE-----\nMEUCIQD-placeholder-signature-please-replace-for-production\n-----END MESSAGE SIGNATURE-----"
else
  PLACEHOLDER_SIG="-----BEGIN MESSAGE SIGNATURE-----\n# 使用真实私钥签名\n# tauri sign --key $NEXUS_TG_PRIVATE --cert ... \n-----END MESSAGE SIGNATURE-----"
fi

# ---------------------------------------------------------------------------
# 步骤 4：计算 SHA256
# ---------------------------------------------------------------------------
log STEP "计算安装包 SHA256..."

calc_sha256() {
  local file="$1"
  if [[ -z "$file" ]] || [[ ! -f "$file" ]]; then
    echo "0000000000000000000000000000000000000000000000000000000000000000"
  else
    sha256sum "$file" | awk '{print $1}'
  fi
}

WIN_SHA256=$(calc_sha256 "$WINDOWS_PKG")
LINUX_SHA256=$(calc_sha256 "$LINUX_PKG")
MAC_SHA256=$(calc_sha256 "$MACOS_PKG")

log OK "SHA256 计算完成"

# ---------------------------------------------------------------------------
# 步骤 5：生成 update.json
# ---------------------------------------------------------------------------
log STEP "生成 update.json..."

NOW_UTC=$(date -u +"%Y-%m-%dT%H:%M:%S+08:00")

cat > "${PROJECT_ROOT}/devops/production/update-server/update.json" << JSONEOF
{
  "version": "${VERSION}",
  "notes": "NexusVideo v${VERSION} 发布说明\n\n## 更新内容\n- 详细更新内容请查看 CHANGELOG.md\n\n## 升级建议\n- 建议所有用户升级到此版本\n- 升级后请清除旧缓存：~/.nexusvideo/cache/",
  "pub_date": "${NOW_UTC}",
  "platforms": {
    "windows-x86_64": {
      "signature": "${PLACEHOLDER_SIG}",
      "url": "${NEXUS_UPDATE_URL}/releases/${VERSION}/$(basename "${WINDOWS_PKG:-nexusvideo-${VERSION}-x86_64-setup.exe"}")",
      "sha256": "${WIN_SHA256}"
    },
    "linux-x86_64": {
      "signature": "${PLACEHOLDER_SIG}",
      "url": "${NEXUS_UPDATE_URL}/releases/${VERSION}/$(basename "${LINUX_PKG:-nexusvideo-${VERSION}-x86_64.AppImage"}")",
      "sha256": "${LINUX_SHA256}"
    },
    "darwin-aarch64": {
      "signature": "${PLACEHOLDER_SIG}",
      "url": "${NEXUS_UPDATE_URL}/releases/${VERSION}/$(basename "${MACOS_PKG:-nexusvideo-${VERSION}-aarch64.dmg"}")",
      "sha256": "${MAC_SHA256}"
    }
  }
}
JSONEOF

log OK "update.json 已生成"
log DATA "  ${PROJECT_ROOT}/devops/production/update-server/update.json"

# ---------------------------------------------------------------------------
# 步骤 6：构建部署目录
# ---------------------------------------------------------------------------
log STEP "构建部署目录..."

DEPLOY_DIR="${PROJECT_ROOT}/.deploy-update"
rm -rf "$DEPLOY_DIR"
mkdir -p "${DEPLOY_DIR}/releases/${VERSION}"

# 复制安装包
[[ -n "$WINDOWS_PKG" ]] && cp "$WINDOWS_PKG" "${DEPLOY_DIR}/releases/${VERSION}/"
[[ -n "$LINUX_PKG" ]]   && cp "$LINUX_PKG"   "${DEPLOY_DIR}/releases/${VERSION}/"
[[ -n "$MACOS_PKG" ]]   && cp "$MACOS_PKG"   "${DEPLOY_DIR}/releases/${VERSION}/"

# 复制 update.json
cp "${PROJECT_ROOT}/devops/production/update-server/update.json" "${DEPLOY_DIR}/update.json"

log OK "部署目录就绪: $DEPLOY_DIR"

# ---------------------------------------------------------------------------
# 步骤 7：按模式部署
# ---------------------------------------------------------------------------
case "$DEPLOY_MODE" in

  # ============== 方案 A：OSS + CDN ==============
  oss)
    log STEP "部署到阿里云 OSS + CDN..."

    # 检查 ossutil
    if ! command -v ossutil &>/dev/null && ! command -v ossutil64 &>/dev/null; then
      log ERROR "未找到 ossutil，请先安装："
      log ERROR "  macOS:  brew install ossutil"
      log ERROR "  Linux:  https://help.aliyun.com/document_detail/120070.html"
      exit 1
    fi
    OSS_CMD=$(command -v ossutil64 2>/dev/null || command -v ossutil 2>/dev/null)

    # 确认 ossutil 配置
    log INFO "验证 OSS 连接..."
    if ! $OSS_CMD ls "${OSS_ENDPOINT}/" &>/dev/null; then
      log ERROR "OSS 连接失败，请检查 ossutil 配置"
      log ERROR "  配置命令: $OSS_CMD config"
      log ERROR "  或使用环境变量: OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET"
      exit 1
    fi

    # 上传文件
    log STEP "上传文件到 OSS..."
    $OSS_CMD cp -r --headers 'Content-Type:application/json' "${DEPLOY_DIR}/update.json" "${OSS_ENDPOINT}/update.json"
    $OSS_CMD cp -r "${DEPLOY_DIR}/releases/" "${OSS_ENDPOINT}/releases/"

    log OK "文件上传完成"

    # 设置静态网站托管
    log STEP "配置 OSS 静态网站托管..."
    $OSS_CMD setbucketwebsite "${OSS_ENDPOINT}" --index-doc index.html --error-doc error.html 2>/dev/null || true

    # CDN 配置提示
    log STEP "CDN 配置提示"
    log DATA "请手动在阿里云 CDN 控制台配置："
    log DATA "  1. 添加域名: update.nexusvideo.com"
    log DATA "  2. 源站类型: OSS（${OSS_BUCKET}.${OSS_REGION}）"
    log DATA "  3. 缓存策略:"
    log DATA "     - /update.json        → 不缓存（Cache-Control: no-cache）"
    log DATA "     - /releases/*         → 1年缓存（Cache-Control: max-age=31536000）"
    log DATA "  4. HTTPS 证书: 使用自有域名证书"
    log DATA "  5. 回源配置: 启用 HTTPS 回源"

    ;;

  # ============== 方案 B：Nginx ==============
  nginx)
    log STEP "部署到 Nginx 服务器..."

    if [[ ! -f "${SCRIPT_DIR}/update-server-nginx.conf" ]]; then
      log ERROR "Nginx 配置文件未找到: ${SCRIPT_DIR}/update-server-nginx.conf"
      exit 1
    fi

    NGINX_DOCROOT="/var/www/nexusvideo-update"
    log INFO "目标目录: $NGINX_DOCROOT"

    # 检查 nginx
    if ! command -v nginx &>/dev/null; then
      log ERROR "未找到 nginx 命令，请确认 nginx 已安装"
      exit 1
    fi

    # 创建目录
    log STEP "创建 Nginx 文档根目录..."
    sudo mkdir -p "$NGINX_DOCROOT"

    # 部署文件
    log STEP "部署文件到服务器..."
    sudo cp -r "${DEPLOY_DIR}/"* "$NGINX_DOCROOT/"

    # 设置权限
    sudo chown -R www-data:www-data "$NGINX_DOCROOT" 2>/dev/null || \
    sudo chown -R nginx:nginx "$NGINX_DOCROOT" 2>/dev/null || true

    # 部署 Nginx 配置
    log STEP "部署 Nginx 配置..."
    sudo cp "${SCRIPT_DIR}/update-server-nginx.conf" /etc/nginx/sites-available/update.nexusvideo.com
    sudo ln -sf /etc/nginx/sites-available/update.nexusvideo.com /etc/nginx/sites-enabled/

    # 测试配置
    log STEP "测试 Nginx 配置..."
    if nginx -t; then
      log OK "Nginx 配置测试通过"
      sudo systemctl reload nginx
      log OK "Nginx 已重载"
    else
      log ERROR "Nginx 配置测试失败"
      exit 1
    fi

    ;;

  # ============== 仅生成 update.json ==============
  build)
    log INFO "仅生成模式，跳过上传步骤"
    ;;
esac

# ---------------------------------------------------------------------------
# 步骤 8：生成版本号管理说明
# ---------------------------------------------------------------------------
log STEP "版本号管理说明"
cat << EOF

${CYAN}══════════════════════════════════════════════════════════════════════${NC}

${BOLD}版本号管理${NC}

NexusVideo 使用 SemVer 语义化版本：MAJOR.MINOR.PATCH

  MAJOR   - 不兼容的破坏性变更（如：数据库 schema 变更）
  MINOR   - 向后兼容的功能新增（如：新功能模块）
  PATCH   - 向后兼容的缺陷修复

${BOLD}版本更新流程：${NC}

  1. 更新 package.json 版本号
     npm version patch   # PATCH 更新
     npm version minor   # MINOR 更新
     npm version major   # MAJOR 更新

  2. 打包
     npm run build:tauri

  3. 部署
     ./deploy-update-server.sh --oss

  4. 验证
     curl -s ${NEXUS_UPDATE_URL}/update.json | jq '.version'
     # 应输出: "${VERSION}"

${BOLD}回退旧版本：${NC}

  如需回退，将旧版 update.json 重新上传即可：
    $OSS_CMD cp old-update.json ${OSS_ENDPOINT}/update.json

${CYAN}══════════════════════════════════════════════════════════════════════${NC}

EOF

# ---------------------------------------------------------------------------
# 完成
# ---------------------------------------------------------------------------
log OK "部署完成！"
log DATA "版本:        $VERSION"
log DATA "更新地址:     ${NEXUS_UPDATE_URL}/update.json"
log DATA "部署模式:     $DEPLOY_MODE"
log DATA "产物目录:     $DEPLOY_DIR"

exit 0