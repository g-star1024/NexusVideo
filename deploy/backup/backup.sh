#!/bin/bash
# ============================================================
# NexusVideo 一键备份脚本
# ============================================================
# 作者：唐磐石（运维架构师）
# 用途：执行全量备份（数据库 + Redis + 模型 + 配置）
# 用法：
#   ./backup.sh                    # 全量备份
#   ./backup.sh --only db          # 仅备份数据库
#   ./backup.sh --only redis       # 仅备份 Redis
#   ./backup.sh --only models      # 仅备份模型
#   ./backup.sh --dry-run          # 预览将执行的操作
# ============================================================
set -euo pipefail

# ---------- 配置 ----------
BACKUP_ROOT="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DATE_DIR=$(date +%Y/%m)
LOG_FILE="${BACKUP_ROOT}/backup_${TIMESTAMP}.log"
OSS_BUCKET="oss://nexusvideo-backup"

# 数据库配置
PG_HOST="localhost"
PG_PORT="5432"
PG_USER="nvuser"
PG_DB="nexusvideo"

# Redis 配置
REDIS_HOST="localhost"
REDIS_PORT="6379"

# 模型路径
MODELS_DIR="/opt/ComfyUI/models"

# 备份目录结构
DB_DIR="${BACKUP_ROOT}/postgres/${DATE_DIR}"
REDIS_DIR="${BACKUP_ROOT}/redis"
MODELS_BACKUP_DIR="${BACKUP_ROOT}/models"

# ---------- 颜色输出 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }

# ---------- 参数解析 ----------
MODE="full"
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --only) shift; MODE="$1" ;;
        --dry-run) DRY_RUN=true ;;
        *) echo "未知参数: $arg"; exit 1 ;;
    esac
done

# ---------- 初始化 ----------
mkdir -p "$DB_DIR" "$REDIS_DIR" "$MODELS_BACKUP_DIR"
echo "备份日志: $LOG_FILE" | tee "$LOG_FILE"

if [ "$DRY_RUN" = true ]; then
    echo ""
    echo "========================================"
    echo "  干运行模式（Dry Run）"
    echo "  模式: $MODE"
    echo "  时间: $TIMESTAMP"
    echo "========================================"
    echo ""
    if [ "$MODE" = "full" ] || [ "$MODE" = "db" ]; then
        echo "[预览] 将备份 PostgreSQL 数据库 → ${DB_DIR}/"
    fi
    if [ "$MODE" = "full" ] || [ "$MODE" = "redis" ]; then
        echo "[预览] 将备份 Redis RDB → ${REDIS_DIR}/"
    fi
    if [ "$MODE" = "full" ] || [ "$MODE" = "models" ]; then
        echo "[预览] 将备份模型文件 → ${MODELS_BACKUP_DIR}/"
    fi
    echo ""
    echo "使用 --dry-run 模式不执行任何实际操作。"
    exit 0
fi

# ============================================================
# 1. PostgreSQL 备份
# ============================================================
backup_database() {
    log_info "========== 开始备份 PostgreSQL =========="

    local backup_file="${DB_DIR}/nexusvideo_${TIMESTAMP}.dump"

    # 设置密码环境变量（避免命令行泄露）
    export PGPASSWORD="${POSTGRES_PASSWORD:-}"

    # 执行备份
    if pg_dump -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" \
        -d "$PG_DB" -F c -f "$backup_file" --verbose 2>> "$LOG_FILE"; then
        local size
        size=$(stat --format=%s "$backup_file" 2>/dev/null || echo "0")

        if [ "$size" -lt 1048576 ]; then
            log_warn "备份文件异常小 (${size} bytes)，可能是空数据库"
        fi

        log_info "数据库备份完成: ${backup_file} (${size} bytes)"

        # 上传 OSS
        upload_to_oss "$backup_file" "postgres/${DATE_DIR}/"

        # 清理 7 天前备份
        find "$DB_DIR" -name "*.dump" -mtime +7 -delete 2>/dev/null || true
        log_info "已清理 7 天前的旧备份"
    else
        log_error "数据库备份失败！"
        return 1
    fi

    unset PGPASSWORD
}

# ============================================================
# 2. Redis 备份
# ============================================================
backup_redis() {
    log_info "========== 开始备份 Redis =========="

    # 触发 RDB 持久化
    if redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" BGSAVE 2>> "$LOG_FILE"; then
        log_info "Redis BGSAVE 触发成功"

        # 等待 RDB 文件写入完成
        local retries=0
        while [ $retries -lt 30 ]; do
            local rdb_busy
            rdb_busy=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" INFO persistence 2>/dev/null | grep rdb_bgsave_in_progress | cut -d: -f2 | tr -d '\r')
            if [ "$rdb_busy" != "1" ]; then
                break
            fi
            retries=$((retries + 1))
            sleep 2
        done

        # 复制 RDB 文件
        local redis_data_dir
        redis_data_dir=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" CONFIG GET dir 2>/dev/null | tail -1)
        local redis_dump_file="${redis_data_dir}/dump.rdb"

        if [ -f "$redis_dump_file" ]; then
            local backup_rdb="${REDIS_DIR}/dump_${TIMESTAMP}.rdb"
            cp "$redis_dump_file" "$backup_rdb"
            local size
            size=$(stat --format=%s "$backup_rdb" 2>/dev/null || echo "0")
            log_info "Redis RDB 备份完成: ${backup_rdb} (${size} bytes)"
            upload_to_oss "$backup_rdb" "redis/"

            # 清理 3 天前 RDB
            find "$REDIS_DIR" -name "*.rdb" -mtime +3 -delete 2>/dev/null || true
        else
            log_warn "未找到 Redis RDB 文件: $redis_dump_file"
        fi
    else
        log_error "Redis 备份失败！"
        return 1
    fi
}

# ============================================================
# 3. 模型文件备份
# ============================================================
backup_models() {
    log_info "========== 开始备份模型文件 =========="

    if [ ! -d "$MODELS_DIR" ]; then
        log_warn "模型目录不存在: $MODELS_DIR，跳过"
        return 0
    fi

    local archive_file="${MODELS_BACKUP_DIR}/comfyui_models_${TIMESTAMP}.tar.gz"

    # 压缩模型目录
    tar -czf "$archive_file" -C "$(dirname "$MODELS_DIR")" "$(basename "$MODELS_DIR")"

    local size
    size=$(stat --format=%s "$archive_file" 2>/dev/null || echo "0")
    log_info "模型备份完成: ${archive_file} (${size} bytes)"

    # 上传 OSS
    upload_to_oss "$archive_file" "models/"

    # 只保留最近 3 个版本
    ls -t "${MODELS_BACKUP_DIR}"/*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm -f
    log_info "已清理旧模型备份，仅保留最近 3 个版本"
}

# ============================================================
# 4. OSS 上传辅助函数
# ============================================================
upload_to_oss() {
    local source_file="$1"
    local oss_prefix="$2"

    if command -v ossutil &>/dev/null; then
        if ossutil cp "$source_file" "${OSS_BUCKET}/${oss_prefix}" 2>> "$LOG_FILE"; then
            log_info "已上传至 OSS: ${OSS_BUCKET}/${oss_prefix}$(basename "$source_file")"
        else
            log_warn "OSS 上传失败（ossutil 错误），但本地备份已保留"
        fi
    else
        log_warn "ossutil 未安装，跳过 OSS 上传"
    fi
}

# ============================================================
# 5. 备份摘要
# ============================================================
print_summary() {
    echo ""
    echo "========================================"
    echo "  备份摘要"
    echo "========================================"
    echo "  时间: ${TIMESTAMP}"
    echo "  模式: ${MODE}"
    echo "  日志: ${LOG_FILE}"
    echo ""

    if [ -d "$DB_DIR" ]; then
        local db_count
        db_count=$(find "$DB_DIR" -name "*.dump" 2>/dev/null | wc -l)
        echo "  数据库备份: ${db_count} 个文件在 ${DB_DIR}/"
    fi
    if [ -d "$REDIS_DIR" ]; then
        local redis_count
        redis_count=$(find "$REDIS_DIR" -name "*.rdb" 2>/dev/null | wc -l)
        echo "  Redis 备份: ${redis_count} 个文件在 ${REDIS_DIR}/"
    fi
    if [ -d "$MODELS_BACKUP_DIR" ]; then
        local model_count
        model_count=$(find "$MODELS_BACKUP_DIR" -name "*.tar.gz" 2>/dev/null | wc -l)
        echo "  模型备份: ${model_count} 个文件在 ${MODELS_BACKUP_DIR}/"
    fi

    echo ""
    echo "  磁盘使用:"
    du -sh "$BACKUP_ROOT" 2>/dev/null || echo "  (无法获取)"
    echo "========================================"
}

# ============================================================
# 主流程
# ============================================================
echo ""
echo "========================================"
echo "  NexusVideo 备份脚本"
echo "  时间: ${TIMESTAMP}"
echo "  模式: ${MODE}"
echo "========================================"
echo ""

ERRORS=0

case "$MODE" in
    full)
        backup_database || ERRORS=$((ERRORS + 1))
        backup_redis    || ERRORS=$((ERRORS + 1))
        backup_models   || ERRORS=$((ERRORS + 1))
        ;;
    db)
        backup_database || ERRORS=$((ERRORS + 1))
        ;;
    redis)
        backup_redis || ERRORS=$((ERRORS + 1))
        ;;
    models)
        backup_models || ERRORS=$((ERRORS + 1))
        ;;
    *)
        echo "未知备份模式: $MODE"
        echo "支持: full, db, redis, models"
        exit 1
        ;;
esac

print_summary

if [ "$ERRORS" -gt 0 ]; then
    log_error "备份完成，但有 ${ERRORS} 个错误。请检查日志: $LOG_FILE"
    exit 1
else
    log_info "所有备份成功完成！"
    exit 0
fi