# NexusVideo 运维 Runbook

> 作者：唐磐石（运维架构师）
> 版本：v1.0 | 更新日期：2026-08-18
> 本手册涵盖所有 P0/P1 事件的响应流程与日常运维操作

---

## 一、事件分级与响应

| 等级 | 定义 | 响应时间 | 解决时间 | 通知对象 |
|------|------|---------|---------|---------|
| **P0** | 核心服务不可用 / 数据丢失 / 全量用户受影响 | ≤ 5min | ≤ 30min | 全员 + 团队 Lead |
| **P1** | 部分功能异常 / 性能严重下降 / 影响部分用户 | ≤ 15min | ≤ 4h | 运维 + 相关开发 |
| **P2** | 非核心功能异常 / 体验问题 / 不影响主流程 | ≤ 4h | ≤ 24h | 值班人员 |

---

## 二、P0 事件响应流程

### 2.1 GPU 显存溢出

#### 症状
- 视频生成任务超时或返回 OOM 错误
- Grafana 看板 GPU 显存使用率 > 90%
- ComfyUI 日志出现 `CUDA out of memory`

#### 排查步骤

```bash
# Step 1: 查看 GPU 显存使用详情
docker exec nexusvideo-comfyui-1 nvidia-smi

# Step 2: 查看 ComfyUI 进程显存占用
docker exec nexusvideo-comfyui-1 nvidia-smi dmon -c 5

# Step 3: 查看 ComfyUI 日志中的 OOM 信息
docker logs --tail 100 nexusvideo-comfyui-1 | grep -i "out of memory\|OOM\|CUDA"

# Step 4: 检查当前并发任务数
redis-cli -h redis -p 6379 LLEN task_queue
redis-cli -h redis -p 6379 LLEN processing_queue

# Step 5: 检查模型加载情况（哪些模型在显存中）
docker exec nexusvideo-comfyui-1 nvidia-smi nvml-import --query-gpu=index,memory.used,memory.total
```

#### 应急处理

```bash
# Step 1: 暂停新任务入队（防止雪崩）
# 在 FastAPI 中设置维护模式
curl -X POST http://localhost:8000/admin/maintenance-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "reason": "GPU显存溢出应急处理"}'

# Step 2: 终止占用显存过高的 ComfyUI 进程
docker exec nexusvideo-comfyui-1 kill -9 $(pgrep -f "python.*comfy" | head -1)

# Step 3: 清理显存残留（重启 CUDA 上下文）
docker restart nexusvideo-comfyui-1

# Step 4: 扩容 1 个 worker 分摊负载
docker compose -f deploy/docker-compose.prod.yml up -d --scale comfyui-worker=3

# Step 5: 恢复任务入队
curl -X POST http://localhost:8000/admin/maintenance-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Step 6: 通知用户（通过 WebSocket 广播）
echo "GPU 已恢复，排队任务将逐步处理"
```

#### 根因分析

| 可能原因 | 验证方法 | 修复方案 |
|---------|---------|---------|
| 模型加载未释放 | 检查 ComfyUI 代码中是否调用 `torch.cuda.empty_cache()` | 添加显存释放逻辑 |
| 并发任务过高 | 检查队列历史数据 | 降低并发上限或扩容 |
| 模型分辨率过高 | 检查任务参数中的 width/height | 限制最大分辨率 |
| 显存碎片化 | `nvidia-smi` 显示 total 正常但 used 异常 | 重启 worker 释放碎片 |
| 单次任务显存超限 | 检查日志中的具体模型大小 | 切换至 A100 实例 |

---

### 2.2 ComfyUI 进程崩溃

#### 症状
- Grafana 告警 `ComfyUIProcessDown`
- `/health` 检测返回失败
- 所有生成任务返回 502/504

#### 排查步骤

```bash
# Step 1: 查看 ComfyUI 容器状态
docker ps -a | grep comfyui

# Step 2: 查看崩溃前日志
docker logs --tail 200 nexusvideo-comfyui-1

# Step 3: 查看系统日志
journalctl -u docker --since "1 hour ago" | grep -i "oom\|kill\|error"

# Step 4: 检查 OOM killer 是否杀死了进程
dmesg | grep -i "oom\|killed process"

# Step 5: 检查模型文件是否损坏
docker exec nexusvideo-comfyui-1 ls -la /opt/ComfyUI/models/checkpoints/
docker exec nexusvideo-comfyui-1 sha256sum /opt/ComfyUI/models/checkpoints/*.safetensors

# Step 6: 检查 CUDA 驱动是否正常
docker exec nexusvideo-comfyui-1 nvidia-smi
```

#### 应急处理

```bash
# Step 1: 重启 ComfyUI worker
docker restart nexusvideo-comfyui-1

# Step 2: 等待健康检查通过（约 60-90 秒）
sleep 90
curl http://localhost:8188/system_stats

# Step 3: 如果再次崩溃，检查是否模型文件损坏
docker exec nexusvideo-comfyui-1 python -c "
import os
models_dir = '/opt/ComfyUI/models/checkpoints/'
for f in os.listdir(models_dir):
    if f.endswith(('.safetensors', '.ckpt', '.bin')):
        path = os.path.join(models_dir, f)
        size = os.path.getsize(path)
        print(f'{f}: {size} bytes')
        if size < 1048576:
            print(f'WARNING: {f} 文件异常小！')
"

# Step 4: 模型损坏则从备份恢复
docker exec nexusvideo-comfyui-1 bash -c "
cd /opt/ComfyUI/models
ossutil cp oss://nexusvideo-backup/models/ /tmp/models_backup/ -r
cp -r /tmp/models_backup/* checkpoints/
"
```

#### 根因分析

| 可能原因 | 验证方法 | 修复方案 |
|---------|---------|---------|
| Python 未捕获异常 | 日志中出现 Traceback | 添加异常捕获，重启 worker |
| CUDA 驱动不兼容 | `nvidia-smi` 版本与 CUDA 不匹配 | 升级驱动或降级 CUDA |
| 模型文件损坏 | MD5 校验失败 | 从备份恢复模型文件 |
| 系统 OOM Killer | `dmesg` 中有 killed process | 增加 worker 内存限制或减少并发 |
| Docker 容器资源限制 | `docker inspect` 检查 memory limit | 调整 Docker 资源配置 |

---

### 2.3 数据库连接失败

#### 症状
- 所有 API 返回 500 错误
- FastAPI 日志出现 `connection refused` 或 `timeout`
- Grafana 告警 `PostgreSQLDown`

#### 排查步骤

```bash
# Step 1: 检查 PostgreSQL 进程状态
docker exec nexusvideo-postgres pg_isready -U nvuser -d nexusvideo

# Step 2: 查看 PostgreSQL 日志
docker logs --tail 100 nexusvideo-postgres

# Step 3: 检查磁盘空间
docker exec nexusvideo-postgres df -h /var/lib/postgresql/data

# Step 4: 检查连接数
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo -c "
SELECT count(*) AS total_connections,
       state,
       count(*) AS connections_by_state
FROM pg_stat_activity
GROUP BY state;
"

# Step 5: 检查 max_connections 是否达上限
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo -c "
SHOW max_connections;
SELECT count(*) FROM pg_stat_activity;
"

# Step 6: 检查 PostgreSQL 内存使用情况
docker stats nexusvideo-postgres --no-stream
```

#### 应急处理

```bash
# Step 1: 重启 PostgreSQL
docker restart nexusvideo-postgres

# Step 2: 如果重启失败，检查磁盘空间
docker exec nexusvideo-postgres df -h
# 如果磁盘满，清理旧文件
docker exec nexusvideo-postgres bash -c "
# 清理超过 7 天的 WAL 文件
find /var/lib/postgresql/data/pg_wal -name '0000*' -mtime +7 -delete
# 清理 pg_stat_tmp
rm -rf /var/lib/postgresql/data/pg_stat_tmp/*
"

# Step 3: 如果连接池耗尽，强制断开空闲连接
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo -c "
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle'
AND pid <> pg_backend_pid();
"

# Step 4: 重启 FastAPI 重建连接池
docker restart nexusvideo-fastapi

# Step 5: 验证恢复
curl http://localhost:8000/health
```

#### 根因分析

| 可能原因 | 验证方法 | 修复方案 |
|---------|---------|---------|
| 磁盘空间不足 | `df -h` 显示 100% | 清理旧备份/日志，扩容磁盘 |
| OOM 导致 PG 被杀 | `dmesg` 检查 | 增加容器内存限制 |
| 连接池耗尽 | `pg_stat_activity` 满 | 增加 max_connections，优化连接释放 |
| PostgreSQL 崩溃 | 日志中 fatal 错误 | 修复数据文件，或从备份恢复 |
| 网络问题 | `ping postgres` 不通 | 检查 Docker 网络 |

---

## 三、P1 事件响应流程

### 3.1 任务队列积压

```bash
# 症状：Grafana 告警 TaskQueueBacklog（队列 > 10）

# 排查
redis-cli -h redis -p 6379 LLEN task_queue
redis-cli -h redis -p 6379 LRANGE task_queue 0 10   # 查看队列头部任务

# 应急
# 扩容 worker
docker compose -f deploy/docker-compose.prod.yml up -d --scale comfyui-worker=3

# 检查是否有死锁任务
redis-cli -h redis -p 6379 LRANGE task_queue 0 -1 | jq '.[].status'
# 将卡住的任务移出队列
redis-cli -h redis -p 6379 LREM task_queue 1 "task-id-here"
```

### 3.2 API 延迟过高

```bash
# 症状：Grafana 告警 APIHighLatencyP99

# 排查
# 查看慢查询
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo -c "
SELECT query, calls, mean_exec_time, rows
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;
"

# 查看 FastAPI 慢接口
curl http://localhost:8000/admin/slow-endpoints

# 应急
# 如果单个接口拖慢全局，考虑降级
# 如果是数据库慢查询，添加索引或优化 SQL
```

---

## 四、日常运维操作

### 4.1 日志清理

```bash
# 每日 04:00 执行
# 清理 7 天前的日志

# Docker 容器日志清理
find /var/lib/docker/containers/ -name "*.log" -mtime +7 -delete

# 应用日志清理
find /app/logs -name "*.log" -mtime +7 -delete

# 备份日志归档到 OSS
ossutil cp /app/logs/archived/ "oss://nexusvideo-backup/logs/$(date +%Y%m)/" -r
```

### 4.2 模型更新流程

```bash
# ============================================================
# 模型更新：四步法，确保零中断
# ============================================================

# Step 1: 下载新模型到临时目录
mkdir -p /opt/ComfyUI/models/staging/
cd /opt/ComfyUI/models/staging/
# 使用 wget 或 ossutil 下载
wget https://model-source.com/new-model.safetensors

# Step 2: 灰度验证（在测试 worker 上加载）
docker exec nexusvideo-comfyui-test python -c "
from diffusers import StableDiffusionPipeline
pipe = StableDiffusionPipeline.from_pretrained('/opt/ComfyUI/models/staging/new-model.safetensors')
print('模型加载成功')
"

# Step 3: 灰度验证通过后，替换生产模型
docker exec nexusvideo-comfyui-1 bash -c "
mv /opt/ComfyUI/models/staging/new-model.safetensors /opt/ComfyUI/models/checkpoints/
"

# Step 4: 验证生产 worker 可用
curl http://localhost:8188/system_stats
echo "模型更新完成"
```

### 4.3 SSL 证书续期检查

```bash
# 每日检查证书过期时间
openssl x509 -in /etc/nginx/ssl/fullchain.pem -noout -enddate

# 自动续期（Let's Encrypt）
certbot renew --quiet

# 续期后重载 Nginx
nginx -s reload

# 配置 cron 任务
# 每周一 3:00 检查续期
0 3 * * 1 /usr/bin/certbot renew --quiet && systemctl reload nginx
```

### 4.4 系统更新

```bash
# 安全补丁更新（每月一次）
apt update && apt upgrade -y --with-new-pkgs

# Docker 镜像更新
docker compose -f deploy/docker-compose.prod.yml pull
docker compose -f deploy/docker-compose.prod.yml up -d

# 清理旧镜像
docker image prune -a -f
```

### 4.5 数据库维护

```bash
# 每周执行 VACUUM ANALYZE（优化查询计划）
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo -c "
VACUUM ANALYZE;
"

# 每月检查数据库大小增长趋势
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo -c "
SELECT pg_size_pretty(pg_database_size('nexusvideo')) AS db_size;
SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 10;
"
```

---

## 五、值班交接清单

### 5.1 每日值班检查（10 分钟）

```markdown
- [ ] Grafana 看板无 P0/P1 告警
- [ ] GPU 显存使用率 < 90%
- [ ] 任务队列长度 < 5
- [ ] API 5xx 错误率 < 1%
- [ ] 所有容器状态 healthy
- [ ] 磁盘使用率 < 80%
- [ ] 备份任务昨晚正常执行
- [ ] SSL 证书未在 30 天内过期
```

### 5.2 每周值班检查（20 分钟）

```markdown
- [ ] 查看过去 7 天告警历史，确认已处理
- [ ] 检查数据库大小增长趋势
- [ ] 检查模型文件完整性（MD5 校验）
- [ ] 审查成本消耗（GPU 小时数、带宽费用）
- [ ] 检查 SSL 证书到期时间
- [ ] 审查 API 访问日志，发现异常流量
- [ ] 更新备份保留策略
```

### 5.3 每月值班检查（30 分钟）

```markdown
- [ ] 执行备份恢复演练（每月最后周五）
- [ ] 审查安全补丁并安排更新
- [ ] 分析月度成本报告，优化 GPU 配置
- [ ] 审查用户增长趋势，评估扩容需求
- [ ] 更新 Runbook（根据本月事故更新）
- [ ] 清理无用数据和旧备份
- [ ] 审查监控告警阈值是否需要调整
```

---

## 六、常用命令速查

```bash
# ==================== 容器管理 ====================
docker compose -f deploy/docker-compose.prod.yml ps          # 查看所有容器状态
docker compose -f deploy/docker-compose.prod.yml logs -f      # 实时查看日志
docker compose -f deploy/docker-compose.prod.yml restart      # 重启所有服务
docker compose -f deploy/docker-compose.prod.yml logs fastapi --tail 100  # 查看 FastAPI 日志

# ==================== GPU 监控 ====================
docker exec nexusvideo-comfyui-1 nvidia-smi                   # GPU 状态
docker exec nexusvideo-comfyui-1 nvidia-smi dmon -c 10        # 实时监控
docker stats --no-stream                                       # 容器资源使用

# ==================== 数据库操作 ====================
docker exec nexusvideo-postgres pg_isready                     # 数据库健康
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo   # 连接数据库
docker exec nexusvideo-postgres pg_dump -U nvuser nexusvideo   # 导出数据库

# ==================== Redis 操作 ====================
docker exec nexusvideo-redis redis-cli ping                    # Redis 健康
docker exec nexusvideo-redis redis-cli LLEN task_queue         # 队列长度
docker exec nexusvideo-redis redis-cli DBSIZE                  # 数据大小

# ==================== 服务重启 ====================
docker restart nexusvideo-nginx                                # 重启 Nginx
docker restart nexusvideo-fastapi                              # 重启 FastAPI
docker restart nexusvideo-comfyui-1                            # 重启 ComfyUI
```

---

## 七、升级与变更管理

### 7.1 变更流程

```
1. 提交变更申请（说明变更内容、影响范围、回滚方案）
2. 代码 Review（至少 1 人审批）
3. 在测试环境验证通过
4. 选择低峰期（凌晨 2:00-4:00）执行
5. 变更完成后执行冒烟测试
6. 观察 30 分钟，确认无异常告警
7. 更新变更记录日志
```

### 7.2 回滚流程

```bash
# 回滚 Docker 镜像到上一版本
docker compose -f deploy/docker-compose.prod.yml \
  pull nexusvideo/fastapi:previous-tag
docker compose -f deploy/docker-compose.prod.yml up -d

# 回滚数据库
docker exec nexusvideo-postgres psql -U nvuser -d nexusvideo \
  -c "SELECT pg_restore('/backups/postgres/latest.dump');"

# 回滚模型
docker exec nexusvideo-comfyui-1 bash -c "
cd /opt/ComfyUI/models
mv checkpoints checkpoints_broken
tar -xzf /backups/models/previous-models.tar.gz -C /opt/ComfyUI/models/
"
```

---

## 八、附录：联系人与通知渠道

| 角色 | 人员 | 联系方式 |
|------|------|---------|
| 运维架构师 | 唐磐石 | 团队 IM 频道 |
| 后端开发 | python-backend-core | 团队 IM 频道 |
| 团队负责人 | team-lead | 团队 IM 频道 |

| 告警渠道 | 工具 | 用途 |
|---------|------|------|
| P0 告警 | 团队 IM 群 + 短信 | 立即响应 |
| P1 告警 | 团队 IM 群 | 30 分钟内响应 |
| P2 告警 | 邮件 | 下次值班处理 |