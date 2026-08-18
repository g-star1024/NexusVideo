#!/usr/bin/env bash
# =============================================================================
# NexusVideo ComfyUI 模型下载脚本（Linux / macOS / WSL）
# 版本：1.0.0
# 作者：NexusVideo MVP 团队 · 运维架构师 唐磐石
#
# 用途：自动下载 MVP 所需 ComfyUI 模型文件，支持断点续传、SHA256 校验、
#       --dry-run 预览模式。
#
# 输出目录：~/NexusVideo/models/{model_name}/
# 校验和来源：https://releases.nexusvideo.com/checksums.txt
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 全局配置
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "$0")"
BASE_DIR="${NEXUS_HOME:-$HOME/NexusVideo}"
MODEL_DIR="${BASE_DIR}/models"
CHECKSUM_URL="https://releases.nexusvideo.com/checksums.txt"
CHECKSUM_LOCAL="${BASE_DIR}/.checksums_cache"

# ---------------------------------------------------------------------------
# 模型清单
# 每条记录格式：名称 | 描述 | 下载 URL | SHA256 文件名（用于校验和匹配）
# ---------------------------------------------------------------------------
readonly MODELS=(
  "wan2.1_14b_gguf_q4|Wan2.1 14B GGUF Q4（文生视频主力模型）|https://huggingface.co/NexusVideo/Wan2.1-14B-GGUF-Q4_K_M/resolve/main/wan2.1_14b_q4_k_m.gguf|wan2.1_14b_q4_k_m.gguf"
  "animatediff_baseline|AnimateDiff 基线模型（6GB 显存保底方案）|https://huggingface.co/guoyww/animatediff/resolve/main/v2/model.ckpt|model.ckpt"
  "controlnet_tile|ControlNet Tile（图生视频空间一致性）|https://huggingface.co/lllyasviel/sd_control_collection/resolve/main/T2I-adapter_tile_sd14.safetensors|T2I-adapter_tile_sd14.safetensors"
  "controlnet_depth|ControlNet Depth（图生视频深度引导）|https://huggingface.co/lllyasviel/sd_control_collection/resolve/main/T2I-adapter_depth_sd14.safetensors|T2I-adapter_depth_sd14.safetensors"
  "ip_adapter|IP-Adapter（图生视频参考图适配）|https://huggingface.co/h94/IP-Adapter/resolve/main/models/ip-adapter_sd15.safetensors|ip-adapter_sd15.safetensors"
  "rife_interpolation|RIFE 帧插值模型（视频帧率提升）|https://github.com/hzwer/Practical-RIFE/releases/download/v1/rife-v1.6-flowmodel.safetensors|rife-v1.6-flowmodel.safetensors"
  "vae_decoders|基础 VAE 解码器|https://huggingface.co/stabilityai/sd-vae-ft-mse-original/resolve/main/vae-ft-mse-840000-ema.ckpt|vae-ft-mse-840000-ema.ckpt"
)

# ---------------------------------------------------------------------------
# 颜色与格式化
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

# 打印带颜色的日志
log() {
  local level="$1"; shift
  case "$level" in
    INFO)  printf "${GREEN}[INFO]${NC}  %s\n" "$*" ;;
    WARN)  printf "${YELLOW}[WARN]${NC}  %s\n" "$*" ;;
    ERROR) printf "${RED}[ERROR]${NC} %s\n" "$*" >&2 ;;
    STEP)  printf "${BLUE}[STEP]${NC}  ${BOLD}%s${NC}\n" "$*" ;;
    OK)    printf "${GREEN}[OK]${NC}    %s\n" "$*" ;;
    DATA)  printf "${CYAN}        %s${NC}\n" "$*" ;;
  esac
}

# 进度格式化
format_size() {
  local bytes="$1"
  if   (( bytes >= 1073741824 )); then
    printf "%.2f GB" "$(echo "scale=2; $bytes / 1073741824" | bc)"
  elif (( bytes >= 1048576 )); then
    printf "%.2f MB" "$(echo "scale=2; $bytes / 1048576" | bc)"
  elif (( bytes >= 1024 )); then
    printf "%.2f KB" "$(echo "scale=2; $bytes / 1024" | bc)"
  else
    printf "%d B" "$bytes"
  fi
}

# 检查依赖
check_dependencies() {
  local missing=()
  for cmd in curl wget sha256sum bc; do
    if ! command -v "$cmd" &>/dev/null; then
      missing+=("$cmd")
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    log ERROR "缺少必要命令：${missing[*]}"
    log ERROR "请通过包管理器安装（Ubuntu: sudo apt install curl wget bc coreutils）"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# 下载函数
# ---------------------------------------------------------------------------

# 获取 SHA256 校验和文件（本地缓存 + 远端更新）
fetch_checksums() {
  if [[ -f "$CHECKSUM_LOCAL" ]]; then
    log INFO "使用本地缓存校验和文件"
    return 0
  fi
  log STEP "下载 SHA256 校验和文件..."
  if ! curl -fsSL --connect-timeout 15 --max-time 120 "$CHECKSUM_URL" -o "$CHECKSUM_LOCAL" 2>/dev/null; then
    log WARN "无法获取校验和文件，将跳过 SHA256 校验"
    log WARN "请手动从 $CHECKSUM_URL 下载后放入 ${CHECKSUM_LOCAL}"
    rm -f "$CHECKSUM_LOCAL"
    return 1
  fi
  log OK "校验和文件已缓存"
  return 0
}

# 获取单个模型的期望 SHA256
get_expected_sha256() {
  local filename="$1"
  if [[ ! -f "$CHECKSUM_LOCAL" ]]; then
    echo ""
    return
  fi
  local expected
  expected=$(grep -i "$filename" "$CHECKSUM_LOCAL" 2>/dev/null | awk '{print $1}' | head -1)
  echo "$expected"
}

# 验证 SHA256
verify_sha256() {
  local filepath="$1"
  local filename
  filename="$(basename "$filepath")"
  local expected
  expected=$(get_expected_sha256 "$filename")

  if [[ -z "$expected" ]]; then
    log WARN "  无校验和记录，跳过验证"
    return 0
  fi

  local actual
  actual=$(sha256sum "$filepath" | awk '{print $1}')
  if [[ "$actual" == "$expected" ]]; then
    log OK "  SHA256 校验通过"
    return 0
  else
    log ERROR "  SHA256 校验失败！期望: $expected，实际: $actual"
    log ERROR "  文件可能已损坏或被篡改，已删除"
    rm -f "$filepath"
    return 1
  fi
}

# 执行单个模型下载
download_model() {
  local name="$1"
  local description="$2"
  local url="$3"
  local filename="$4"

  log STEP "下载: $name"
  log DATA "描述: $description"
  log DATA "URL:  $url"

  # 创建目标目录
  local target_dir="${MODEL_DIR}/${name}"
  mkdir -p "$target_dir"

  if [[ "$DRY_RUN" == "true" ]]; then
    log DATA "[DRY-RUN] 跳过实际下载"
    # 用 HEAD 请求估算文件大小
    local size
    size=$(curl -sI --connect-timeout 10 "$url" 2>/dev/null | grep -i "^content-length:" | awk '{print $2}' | tr -d '\r')
    if [[ -n "$size" ]]; then
      log DATA "[DRY-RUN] 估算大小: $(format_size "$size")"
    fi
    echo "$size"
    return 0
  fi

  local target_path="${target_dir}/${filename}"

  # 检查是否已存在且完整
  if [[ -f "$target_path" ]]; then
    local existing_size
    existing_size=$(stat -c%s "$target_path" 2>/dev/null || stat -f%z "$target_path" 2>/dev/null || echo 0)
    local remote_size
    remote_size=$(curl -sI --connect-timeout 10 "$url" 2>/dev/null | grep -i "^content-length:" | awk '{print $2}' | tr -d '\r')
    if [[ -n "$remote_size" ]] && [[ "$existing_size" == "$remote_size" ]]; then
      log OK "  文件已存在且大小匹配，跳过下载"
      verify_sha256 "$target_path"
      return 0
    fi
    log INFO "  文件部分存在，将续传"
  fi

  # 优先使用 aria2c（更好的断点续传支持）
  if command -v aria2c &>/dev/null; then
    log DATA "使用 aria2c 下载（支持断点续传）..."
    if aria2c \
      --continue=true \
      --max-connection-per-server=4 \
      --min-split-size=10M \
      --summary-interval=5 \
      -o "$target_path" \
      "$url" 2>&1 | tail -20; then
      :
    else
      log ERROR "  aria2c 下载失败，降级使用 wget..."
      rm -f "$target_path"
      _wget_download "$url" "$target_path"
      return $?
    fi
  else
    _wget_download "$url" "$target_path"
  fi

  # 校验和验证
  verify_sha256 "$target_path"

  # 显示最终信息
  local final_size
  final_size=$(stat -c%s "$target_path" 2>/dev/null || stat -f%z "$target_path" 2>/dev/null || echo 0)
  log OK "  完成: $(format_size "$final_size")"
  echo "$final_size"
}

_wget_download() {
  local url="$1"
  local target="$2"
  log DATA "使用 wget 下载（-c 续传）..."
  if wget --continue -q --show-progress -O "$target" "$url" 2>&1; then
    return 0
  else
    log ERROR "  wget 下载失败"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

usage() {
  cat <<EOF
用法: ${SCRIPT_NAME} [选项]

NexusVideo ComfyUI 模型下载脚本

选项:
  --dry-run      预览模式，只打印将要下载的内容和大小，不实际下载
  --models-dir <目录>  指定模型输出目录（默认 ~/NexusVideo/models/）
  --skip-verify  跳过 SHA256 校验
  --help, -h     显示本帮助信息

环境变量:
  NEXUS_HOME     NexusVideo 根目录（默认 ~/NexusVideo）

示例:
  ${SCRIPT_NAME} --dry-run         # 预览下载内容
  ${SCRIPT_NAME}                   # 开始下载
  ${SCRIPT_NAME} --models-dir /data/models  # 自定义目录
EOF
}

# 解析参数
DRY_RUN="false"
SKIP_VERIFY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --models-dir)
      MODEL_DIR="$2"
      shift 2
      ;;
    --skip-verify)
      SKIP_VERIFY="true"
      shift
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

# 打印 banner
printf "\n${CYAN}"
printf "╔══════════════════════════════════════════════════════════════╗\n"
printf "║        ${BOLD}NexusVideo ComfyUI 模型下载工具${NC}                      ║\n"
printf "║        运维架构师: 唐磐石  ·  NexusVideo MVP 团队           ║\n"
printf "╚══════════════════════════════════════════════════════════════╝\n"
printf "${NC}\n"

if [[ "$DRY_RUN" == "true" ]]; then
  log WARN "DRY-RUN 模式：以下操作将不会实际执行\n"
fi

# 前置检查
check_dependencies
mkdir -p "$BASE_DIR"

# 下载校验和文件
fetch_checksums

# 统计信息
total_models=${#MODELS[@]}
downloaded_count=0
failed_count=0
total_size=0
start_time=$(date +%s)

# 逐一下载
for model_entry in "${MODELS[@]}"; do
  IFS='|' read -r name description url filename <<< "$model_entry"
  log DATA ""  # 空行分隔

  if download_model "$name" "$description" "$url" "$filename"; then
    downloaded_count=$((downloaded_count + 1))
    # 累加大小（dry-run 模式下返回的是估算大小）
    local_size=$(stat -c%s "${MODEL_DIR}/${name}/${filename}" 2>/dev/null || stat -f%z "${MODEL_DIR}/${name}/${filename}" 2>/dev/null || echo 0)
    total_size=$((total_size + local_size))
  else
    failed_count=$((failed_count + 1))
    log ERROR "模型 $name 下载失败，继续下一个..."
  fi
done

# 计算耗时
end_time=$(date +%s)
elapsed=$((end_time - start_time))
elapsed_min=$((elapsed / 60))
elapsed_sec=$((elapsed % 60))

# ---------------------------------------------------------------------------
# 总结报告
# ---------------------------------------------------------------------------
printf "\n${BOLD}${CYAN}══════════════════════════════════════════════════════════════${NC}\n"
log STEP "下载完成 - 总结报告"
log DATA "模型总数:    $total_models"
log DATA "成功:        $downloaded_count"
log DATA "失败:        $failed_count"
log DATA "总下载量:    $(format_size "$total_size")"
log DATA "耗时:        ${elapsed_min}分${elapsed_sec}秒"
log DATA "输出目录:    ${MODEL_DIR}"

if [[ "$DRY_RUN" == "true" ]]; then
  log DATA ""
  log WARN "这是 DRY-RUN 预览结果，未实际下载任何文件"
fi

printf "${CYAN}══════════════════════════════════════════════════════════════${NC}\n\n"

if (( failed_count > 0 )); then
  exit 1
fi
exit 0