# NexusVideo 备份与容灾 SOP

> 作者：唐磐石（运维架构师）
> 版本：v1.0 | 更新日期：2026-08-18

---

## 一、备份策略总览

### 1.1 备份矩阵

| 数据类型 | 频率 | 保留周期 | 备份工具 | 存储位置 | RPO | RTO |
|---------|------|---------|---------|---------|-----|-----|
| **PostgreSQL 数据库** | 每日 03:00 | 7 天全量 + 30 天增量 | pg_dump → OSS | 阿里云 OSS (备份桶) | ≤ 24h | ≤ 30min |
| **Redis RDB** | 每 4 小时 | 3 天 | redis-cli BGSAVE | 本地 + OSS 同步 | ≤ 4h | ≤ 10min |
| **用户视频文件** | 实时同步 | 90 天 | OSS 生命周期策略 | 阿里云 OSS (视频桶) | 实时 | 实时 |
| **配置文件** | 每次变更 | 永久 | Git | GitHub / GitLab | 实时 | 实时 |
| **ComfyUI 模型文件** | 每周 | 最近 3 个版本 | rsync → OSS | 阿里云 OSS (模型桶) | ≤ 7d | ≤ 1h |
| **日志文件** | 每日归档 | 7 天 | logrotate + OSS | 阿里云 OSS (日志桶) | ≤ 24h | N/A |
| **Docker 镜像** | 每次发布 | 最近 5 个 tag | Docker Registry | 阿里云 ACR | ≤ 发布间隔 | ≤ 5min |

### 1.2 RPO / RTO 目标

| 等级 | RPO（数据丢失容忍） | RTO（恢复时间） | 适用数据 |
|------|-------------------|---------------|---------|
| **P0** | ≤ 1h | ≤ 30min | 用户数据库、模型文件 |
| **P1** | ≤ 4h | ≤ 1h | Redis 缓存、视频文件 |
| **P2** | ≤ 24h | ≤ 4h | 日志、配置、监控数据 |

---

## 二、备份执行流程

### 2.1 PostgreSQL 每日备份

```bash
# 执行时间：每日 03:00（低峰期）
# 工具：pg_dump（逻辑备份）+ pg_basebackup（物理备份，可选）

# 步骤 1：创建备份目录
mkdir -p /backups/postgres/$(date +%Y/%m)

# 步骤 2：执行逻辑备份（全量）
pg_dump -U nvuser \
  -h localhost \
  -d nexusvideo \
  -F c \                    # custom format（支持压缩和选择性恢复）
  -f /backups/postgres/$(date +%Y/%m)/nexusvideo_$(date +%Y%m%d).dump \
  --verbose

# 步骤 3：校验备份完整性
pg_restore --list /backups/postgres/$(date +%Y/%m)/nexusvideo_$(date +%Y%m%d).dump > /dev/null
echo "备份校验通过"

# 步骤 4：上传至 OSS
ossutil cp /backups/postgres/$(date +%Y/%m)/nexusvideo_$(date +%Y%m%d).dump \
  "oss://nexusvideo-backup/postgres/$(date +%Y/%m)/"

# 步骤 5：清理本地 7 天前的备份
find /backups/postgres -name "*.dump" -mtime +7 -delete
```

### 2.2 Redis 定期备份

```bash
# Redis 已在配置中启用 AOF 持久化
# 额外 RDB 备份每 4 小时执行

redis-cli -h localhost -p 6379 BGSAVE
echo "Redis RDB 备份触发成功"

# 复制 RDB 文件到备份目录
cp /data/dump.rdb /backups/redis/dump_$(date +%Y%m%d_%H%M).rdb

# 上传 OSS
ossutil cp /backups/redis/dump_$(date +%Y%m%d_%H%M).rdb \
  "oss://nexusvideo-backup/redis/"

# 清理 3 天前的 RDB
find /backups/redis -name "*.rdb" -mtime +3 -delete
```

### 2.3 用户视频文件备份

```bash
# 用户视频通过 OSS 生命周期策略自动管理
# 无需手动备份，实时存储在 OSS 中

# 生命周期策略配置（通过 ossutil）：
# 过渡到低频存储（IA）：30 天
# 过渡到归档存储（Archive）：60 天
# 永久删除：90 天

ossutil lifecycle --put oss://nexusvideo-videos/ << 'EOF'
{
  "Rules": [
    {
      "ID": "video-lifecycle",
      "Status": "Enabled",
      "Filter": { "Prefix": "" },
      "Transitions": [
        { "Days": 30, "StorageClass": "IA" },
        { "Days": 60, "StorageClass": "Archive" }
      ],
      "Expiration": { "Days": 90 }
    }
  ]
}
EOF
```

### 2.4 模型文件备份

```bash
# 每周日凌晨 4:00 执行模型备份
rsync -avz --delete /opt/ComfyUI/models/ /backups/models/

# 压缩后上传 OSS
tar -czf /backups/models/comfyui_models_$(date +%Y%m%d).tar.gz -C /backups/ models/
ossutil cp /backups/models/comfyui_models_$(date +%Y%m%d).tar.gz \
  "oss://nexusvideo-backup/models/"

# 只保留最近 3 个版本
ls -t /backups/models/*.tar.gz | tail -n +4 | xargs -r rm -f
```

---

## 三、恢复演练 SOP

### 3.1 演练计划

| 项目 | 内容 |
|------|------|
| 执行时间 | 每月最后周五 14:00-16:00 |
| 参与人员 | 运维（唐磐石）、后端开发（python-backend-core） |
| 影响范围 | 仅测试环境，不影响生产 |
| 通知方式 | 提前 3 天在团队频道通知 |

### 3.2 恢复演练清单

```markdown
## 演练验证清单

### 1. 数据库恢复
- [ ] 从 OSS 下载最新备份文件
- [ ] 创建测试数据库
- [ ] 执行 pg_restore 恢复数据
- [ ] 验证表结构和行数一致
- [ ] 验证外键约束有效
- [ ] 记录恢复耗时

### 2. Redis 恢复
- [ ] 从 OSS 下载最新 RDB 文件
- [ ] 启动 Redis 实例加载 RDB
- [ ] 验证 key 数量和任务队列内容
- [ ] 记录恢复耗时

### 3. 模型文件恢复
- [ ] 从 OSS 下载模型压缩包
- [ ] 解压到指定目录
- [ ] 验证模型文件完整性（MD5 校验）
- [ ] 启动 ComfyUI 验证模型可加载

### 4. 全链路恢复验证
- [ ] 恢复数据库 + Redis + 模型
- [ ] 启动全部服务
- [ ] 执行端到端测试（注册 → 上传 → 生成 → 下载）
- [ ] 验证 WebSocket 进度推送正常
- [ ] 记录总恢复耗时
```

### 3.3 演练报告模板

```markdown
# 备份恢复演练报告

- **演练日期**：2026-XX-XX
- **演练人员**：唐磐石、python-backend-core
- **数据版本**：备份时间 2026-XX-XX 03:00

## 恢复结果

| 组件 | 恢复状态 | 耗时 | 备注 |
|------|---------|------|------|
| PostgreSQL | ✅ 成功 | 12min | 数据完整 |
| Redis | ✅ 成功 | 3min | 队列完整 |
| 模型文件 | ✅ 成功 | 8min | MD5 校验通过 |
| 全链路 | ✅ 成功 | 25min | 端到端验证通过 |

## 发现的问题
1. ...

## 改进建议
1. ...
```

---

## 四、灾难恢复流程

### 4.1 严重灾难（数据中心故障 / 实例丢失）

```
触发条件：
  - 整个可用区不可用
  - 云平台故障导致实例不可恢复
  - 数据库磁盘损坏

恢复步骤：
  1. 确认故障范围和影响程度（5 min）
  2. 通知团队进入灾难恢复模式（2 min）
  3. 在新的可用区/区域创建 GPU 实例（15 min）
  4. 从 OSS 恢复 PostgreSQL 数据（15 min）
  5. 从 OSS 恢复 Redis RDB（5 min）
  6. 从 OSS 恢复模型文件（10 min）
  7. 启动所有服务（5 min）
  8. 验证全链路功能正常（10 min）
  9. DNS 切流到新实例（2 min）
  10. 通知用户服务已恢复（2 min）

总恢复时间目标：≤ 60 min
```

### 4.2 数据误删除恢复

```
场景：
  - 用户误删视频
  - 管理员误删数据库表
  - 误操作删除备份文件

恢复步骤：
  1. 确认删除时间和范围
  2. 找到删除前的最近备份（OSS 有版本控制）
  3. 从备份中恢复特定对象（pg_restore -t tablename）
  4. 验证数据完整性
  5. 记录事故根因

OSS 版本控制配置：
  ossutil versioning --put oss://nexusvideo-backup/ --status Enabled
```

---

## 五、备份监控

### 5.1 备份任务监控指标

| 指标 | 告警阈值 | 告警级别 |
|------|---------|---------|
| pg_dump 备份耗时 | > 30min | P2 |
| 备份文件大小异常 | 相比前日变化 > 50% | P1 |
| OSS 上传失败 | 连续 2 次失败 | P0 |
| 本地备份磁盘使用率 | > 80% | P2 |
| 备份文件 MD5 校验失败 | 任意失败 | P0 |
| 恢复演练未按时执行 | 延迟 > 3 天 | P2 |

### 5.2 备份健康检查脚本

```bash
#!/bin/bash
# deploy/scripts/backup-health-check.sh
# 每日 06:00 执行，检查前一天备份是否成功

BACKUP_DIR="/backups/postgres"
TODAY=$(date +%Y/%m/%d)
YESTERDAY=$(date -d "yesterday" +%Y/%m/%d)
BACKUP_FILE="${BACKUP_DIR}/${YESTERDAY}/nexusvideo_${YESTERDAY//[-\/]}.dump"

echo "=== 备份健康检查 $(date) ==="

# 检查备份文件是否存在
if [ ! -f "$BACKUP_FILE" ]; then
    echo "❌ 错误：未找到昨日备份文件 $BACKUP_FILE"
    # 发送告警通知
    exit 1
fi

# 检查备份文件大小
SIZE=$(stat --format=%s "$BACKUP_FILE")
if [ "$SIZE" -lt 1048576 ]; then
    echo "⚠️  警告：备份文件异常小（${SIZE} bytes），可能为空备份"
    exit 2
fi

# 校验 OSS 同步
OSS_FILE="oss://nexusvideo-backup/postgres/$(echo $YESTERDAY | tr '/' '/')/"
OSS_SIZE=$(ossutil ls "$OSS_FILE" 2>/dev/null | awk '{print $4}')
if [ "$SIZE" != "$OSS_SIZE" ]; then
    echo "⚠️  警告：本地备份与 OSS 大小不一致（本地=${SIZE}, OSS=${OSS_SIZE}）"
    exit 2
fi

echo "✅ 备份健康：${BACKUP_FILE}（${SIZE} bytes）"
```

---

## 六、备份成本估算

| 备份类型 | 月数据量 | OSS 标准存储 | 月费用 |
|---------|---------|------------|--------|
| PostgreSQL 全量 | ~2GB × 7 = 14GB | ¥0.0012/GB/天 | ¥0.50 |
| Redis RDB | ~100MB × 3 = 300MB | 同上 | ¥0.01 |
| 模型文件 | ~50GB × 3 = 150GB | 同上 | ¥5.40 |
| 日志归档 | ~5GB/月 | 同上 | ¥0.18 |
| **合计** | | | **¥6.09/月** |