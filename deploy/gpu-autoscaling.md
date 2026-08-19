# NexusVideo GPU 弹性伸缩策略

> 作者：唐磐石（运维架构师）
> 版本：v1.0 | 更新日期：2026-08-18

---

## 一、设计原则

```
┌────────────────────────────────────────────────────────────────────┐
│  成本敏感原则：GPU 云实例是最大成本项（占 65%+），必须弹性调度    │
│  稳定性优先原则：宁可少量冗余，不可让任务排队超时                 │
│  渐进扩容原则：先扩 1 个，观察 2 分钟，再决定扩 2 个             │
└────────────────────────────────────────────────────────────────────┘
```

---

## 二、GPU 选型与成本对比

### 2.1 实例规格对照

| 规格 | 显存 | 算力(TFLOPS) | 月费(包月) | 时费(按需) | 适用场景 |
|------|------|-------------|-----------|-----------|---------|
| **A10 24GB** | 24 GB | 312 | ¥5,760 | ¥8/h | ✅ **主力**：视频生成（SDXL/AnimateDiff） |
| A100 80GB | 80 GB | 312 | ¥18,000 | ¥25/h | 可选：高分辨率/多帧并行/模型微调 |
| T4 16GB | 16 GB | 65 | ¥2,400 | ¥3/h | 备选：低配降级方案，仅适合 512x512 |

### 2.2 选型结论

**主力选 A10（24GB）**，理由：
- 24GB 显存满足 SDXL + AnimateDiff 单次推理（约需 16-18GB）
- 时费仅 ¥8，比 A100 便宜 68%
- 同卡可跑 2-3 个并行任务（通过显存池化）
- MVP 阶段 2 张 A10 即可覆盖日活 < 500 用户

**A100 按需预留**：仅当出现以下场景时按需启动：
- 用户提交 4K 分辨率视频生成请求
- 多帧 AnimateDiff 需要 60+ 帧
- 模型微调任务（LoRA 训练）

---

## 三、弹性伸缩策略

### 3.1 伸缩触发条件

```
┌──────────────────────────────────────────────────────────────────┐
│                    弹性伸缩触发条件矩阵                            │
├──────────────┬─────────────┬──────────────┬─────────────────────┤
│  指标        │  扩容阈值   │  缩容阈值    │  动作               │
├──────────────┼─────────────┼──────────────┼─────────────────────┤
│ 队列长度     │  > 5 任务   │  < 2 任务    │ 扩/缩 1 个 worker  │
│ GPU 利用率   │  > 80%      │  < 30%       │ 扩/缩 1 个 worker  │
│ 平均等待时间 │  > 30s      │  < 5s        │ 扩/缩 1 个 worker  │
│ 显存使用率   │  > 85%      │  < 50%       │ 扩/缩 1 个 worker  │
│ 并发任务数   │  > 4 个     │  < 1 个      │ 扩/缩 1 个 worker  │
└──────────────┴─────────────┴──────────────┴─────────────────────┘

扩容上限：4 个 A10 实例（同时 8 张卡）
缩容下限：1 个 A10 实例（至少保持 1 张卡在线）
冷却时间：扩容后 5 分钟内不再扩容，缩容后 10 分钟内不再缩容
```

### 3.2 时间窗口策略

```
┌────────────────────────────────────────────────────────────────────┐
│                     24 小时弹性调度策略                              │
├───────────────┬────────────┬─────────────────────────────────────┤
│ 时间段        │ 期望实例数 │ 说明                                  │
├───────────────┼────────────┼─────────────────────────────────────┤
│ 00:00 - 08:00 │ 1（最小） │ 凌晨低谷，保留 1 张卡即可             │
│ 08:00 - 09:00 │ 1 → 2    │ 早高峰前预热扩容                      │
│ 09:00 - 18:00 │ 2（基准） │ 工作日高峰，2 张卡基准负载            │
│ 18:00 - 22:00 │ 2 → 3    │ 晚间高峰，根据队列动态扩至 3          │
│ 22:00 - 24:00 │ 2         │ 回落，稳定在 2 张                     │
│               │          │                                      │
│ 周末 00:00-12 │ 1         │ 周末凌晨至中午低谷                    │
│ 周末 12:00-24 │ 2 → 3    │ 周末下午晚间高峰                      │
└───────────────┴────────────┴─────────────────────────────────────┘

缩容到 0 的条件（仅适用于无付费用户场景）：
  - DAU 连续 7 天 < 10 人
  - 队列始终为 0
  - 执行时间：凌晨 00:00-06:00
  - 恢复时间：用户首次请求时自动启动（冷启动 ~30s）
```

### 3.3 成本优化计算

```
常驻 1 张 A10 月费：¥5,760
弹性 2 张 A10 日均使用 6h：¥8 × 6h × 2 × 30 = ¥2,880
弹性 3 张 A10 日均使用 3h：¥8 × 3h × 3 × 30 = ¥2,160
────────────────────────────────────
月度 GPU 总成本：¥5,760 + ¥2,880 + ¥2,160 = ¥10,800

对比常驻 2 张方案：¥5,760 × 2 = ¥11,520
弹性方案节省：(¥11,520 - ¥10,800) / ¥11,520 = 6.2%

若凌晨自动缩容到 0（无付费用户时）：
  节省 8h × ¥5,760/720h = ¥64/h × 8h = ¥512/天 = ¥15,360/月
```

---

## 四、K8s HPA 配置

### 4.1 HorizontalPodAutoscaler（水平自动伸缩）

```yaml
# deploy/k8s/hpa-comfyui-worker.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: comfyui-worker-hpa
  namespace: nexusvideo
  labels:
    app: comfyui-worker
    component: gpu-inference
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: comfyui-worker
  minReplicas: 1
  maxReplicas: 4
  metrics:
    # 指标 1：队列长度（自定义指标）
    - type: External
      external:
        metric:
          name: redis_task_queue_length
        target:
          type: AverageValue
          averageValue: "5"
    # 指标 2：GPU 显存使用率
    - type: External
      external:
        metric:
          name: nvidia_gpu_memory_usage_percent
        target:
          type: AverageValue
          averageValue: "80"
    # 指标 3：平均等待时间
    - type: External
      external:
        metric:
          name: task_queue_wait_time_seconds
        target:
          type: AverageValue
          averageValue: "30"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Percent
          value: 50          # 每次最多扩 50%
          periodSeconds: 120  # 每 2 分钟检查一次
    scaleDown:
      stabilizationWindowSeconds: 300   # 缩容稳定窗口 5 分钟
      policies:
        - type: Percent
          value: 25           # 每次最多缩 25%
          periodSeconds: 300  # 每 5 分钟检查一次
```

### 4.2 Cluster Autoscaler 配置（节点级）

```yaml
# deploy/k8s/cluster-autoscaler.yaml
apiVersion: karpenter.sh/v1alpha5
kind: Provisioner
metadata:
  name: gpu-provisioner
spec:
  requirements:
    - key: node.kubernetes.io/instance-family
      operator: In
      values: ["g", "gn"]      # GPU 实例族
    - key: nvidia.com/gpu.product
      operator: In
      values: ["A10"]
    - key: nvidia.com/gpu.memory
      operator: Gt
      values: ["20480"]        # 至少 20GB 显存
  limits:
    cpu: 32
    memory: 128Gi
    gpu: 4
  ttlSecondsAfterEmpty: 600   # 空闲 10 分钟释放节点
  ttlSecondsUntilExpired: 604800  # 节点最长存活 7 天
  providerRef:
    name: gpu-instance-profile
---
apiVersion: karpenter.sh/v1alpha5
kind: NodePool
metadata:
  name: gpu-nodepool
spec:
  template:
    spec:
      requirements:
        - key: nvidia.com/gpu.product
          operator: In
          values: ["A10"]
      providerRef:
        name: gpu-provisioner
  limits:
    cpu: 16
    memory: 64Gi
    gpu: 4
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 5m
```

### 4.3 CronJob 定时缩容（凌晨）

```yaml
# deploy/k8s/cronjob-offpeak-scale-down.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: gpu-scale-down-night
  namespace: nexusvideo
spec:
  schedule: "0 0 * * *"    # 每天凌晨 0 点
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: scale-down
            image: bitnami/kubectl:latest
            command:
            - /bin/sh
            - -c
            - |
              echo "=== 凌晨缩容检查 ==="
              QUEUE_LEN=$(curl -s http://redis:6379/ \
                | redis-cli LLEN task_queue)
              DAU=$(kubectl exec deploy/fastapi -- \
                curl -s http://localhost:8000/admin/dau-today | jq .count)
              echo "队列长度: $QUEUE_LEN, 今日活跃用户: $DAU"

              if [ "$QUEUE_LEN" -eq 0 ] && [ "$DAU" -lt 10 ]; then
                echo "触发缩容：队列空且 DAU < 10"
                kubectl scale deploy comfyui-worker --replicas=1
              else
                echo "不触发缩容，保持当前副本数"
              fi
          restartPolicy: OnFailure
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: gpu-scale-up-morning
  namespace: nexusvideo
spec:
  schedule: "0 8 * * *"    # 每天早上 8 点
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: scale-up
            image: bitnami/kubectl:latest
            command:
            - /bin/sh
            - -c
            - |
              echo "=== 早高峰预热扩容 ==="
              kubectl scale deploy comfyui-worker --replicas=2
              echo "已扩容至 2 个副本"
          restartPolicy: OnFailure
```

### 4.4 自定义 Metrics Server 配置

```yaml
# deploy/k8s/metrics-adapter.yaml
# 用于暴露 Redis 队列长度和 GPU 指标给 HPA
apiVersion: v1
kind: ConfigMap
metadata:
  name: custom-metrics-adapter
  namespace: kube-system
data:
  rules.yaml: |
    rules:
      - seriesQuery: 'redis_queue_length{queue="task_queue"}'
        resources:
          overrides:
            namespace: {resource: "namespace"}
        name:
          matches: "^redis_(.*)_length$"
          as: "redis_${1}_queue_length"
        metricsQuery: '<<.Series>>'
      - seriesQuery: 'nvidia_gpu_memory_usage_bytes'
        resources:
          overrides:
            namespace: {resource: "namespace"}
        name:
          as: "nvidia_gpu_memory_usage_percent"
        metricsQuery: '<<.Series>> / on() (nvidia_gpu_memory_total_bytes) * 100'
```

---

## 五、Docker Compose 版本下的伸缩替代方案

> MVP 阶段可能不使用 K8s，以下是 Docker Compose 下的弹性伸缩方案。

### 5.1 autoscale.sh — 定时检查脚本

```bash
#!/bin/bash
# deploy/scripts/autoscale.sh
# 每 30 秒执行一次，通过 crontab 调度
#
# 用法：
#   */1 * * * * /opt/nexusvideo/deploy/scripts/autoscale.sh

REDIS_HOST="redis"
REDIS_PORT="6379"
MAX_REPLICAS=4
MIN_REPLICAS=1
SCALE_UP_THRESHOLD=5
SCALE_DOWN_THRESHOLD=2
COOLDOWN=300

# 获取队列长度
QUEUE_LEN=$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LLEN task_queue 2>/dev/null || echo "0")

# 获取当前 worker 副本数
CURRENT_REPLICAS=$(docker compose -f /opt/nexusvideo/deploy/docker-compose.prod.yml \
  ps --format "{{.Name}}" | grep comfyui-worker | wc -l)

# 获取 5 分钟内的 GPU 平均利用率（通过 nvidia-smi）
GPU_USAGE=$(docker exec -i nexusvideo-comfyui-1 nvidia-smi 2>/dev/null \
  | grep -oP 'Utilization\s+\d+' | grep -oP '\d+' || echo "0")

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Queue=$QUEUE_LEN, Replicas=$CURRENT_REPLICAS, GPU=$GPU_USAGE%"

# 扩容逻辑
if [ "$QUEUE_LEN" -ge "$SCALE_UP_THRESHOLD" ] && [ "$CURRENT_REPLICAS" -lt "$MAX_REPLICAS" ]; then
    NEW_REPLICAS=$((CURRENT_REPLICAS + 1))
    docker compose -f /opt/nexusvideo/deploy/docker-compose.prod.yml \
      up -d --scale comfyui-worker=$NEW_REPLICAS
    echo ">>> 扩容至 $NEW_REPLICAS 个 worker（队列积压 $QUEUE_LEN 个任务）"
fi

# 缩容逻辑
if [ "$QUEUE_LEN" -le "$SCALE_DOWN_THRESHOLD" ] && [ "$CURRENT_REPLICAS" -gt "$MIN_REPLICAS" ] && [ "$GPU_USAGE" -lt 30 ]; then
    NEW_REPLICAS=$((CURRENT_REPLICAS - 1))
    docker compose -f /opt/nexusvideo/deploy/docker-compose.prod.yml \
      up -d --scale comfyui-worker=$NEW_REPLICAS
    echo ">>> 缩容至 $NEW_REPLICAS 个 worker（队列空闲，GPU 利用率 $GPU_USAGE%）"
fi
```

### 5.2 crontab 配置

```bash
# 每 1 分钟检查一次伸缩
echo "* * * * * /opt/nexusvideo/deploy/scripts/autoscale.sh >> /var/log/autoscale.log 2>&1" | crontab -

# 凌晨 0 点强制缩容到 1（如果队列空闲）
echo "0 0 * * * /opt/nexusvideo/deploy/scripts/night-scale-down.sh >> /var/log/autoscale.log 2>&1" | crontab -

# 早上 8 点强制扩容到 2
echo "0 8 * * * /opt/nexusvideo/deploy/scripts/morning-scale-up.sh >> /var/log/autoscale.log 2>&1" | crontab -
```

---

## 六、冷启动与预热策略

| 阶段 | 耗时 | 操作 |
|------|------|------|
| Docker 容器启动 | ~5s | 启动 ComfyUI 进程 |
| CUDA 驱动初始化 | ~3s | 加载 CUDA 运行时 |
| 模型加载到显存 | ~30-60s | 首次加载 SDXL/AnimateDiff |
| 健康检查通过 | ~60s | healthcheck start_period |
| **总计冷启动** | **~90s** | |

**预热策略**：
- 每日 8:00 提前扩容 1 个副本（CronJob）
- 副本保持 1 张卡在线时，每 30 分钟执行一次 keepalive 请求
- 凌晨缩容到 0 后，用户首次请求触发 auto-scaling（冷启动 ~90s）

---

## 七、容量规划

### 7.1 并发任务数与 GPU 对应关系

| GPU 数量 | 并发任务上限 | 平均生成耗时 | 适合 DAU |
|---------|------------|-------------|---------|
| 1 张 A10 | 1-2 个 | ~90s | < 50 |
| 2 张 A10 | 3-4 个 | ~60s | 50-200 |
| 3 张 A10 | 5-6 个 | ~45s | 200-500 |
| 4 张 A10 | 7-8 个 | ~30s | 500-1000 |
| 1 张 A100 | 4-6 个 | ~25s | 1000+ |

### 7.2 扩容决策树

```
用户提交视频生成请求
│
├─ 队列长度 < 5？
│   ├─ YES → 分配给空闲 worker
│   └─ NO  → 触发扩容检查
│               │
│               ├─ 当前副本 < 4？
│               │   ├─ YES → 扩容 1 个副本
│               │   └─ NO  → 加入等待队列，返回预估等待时间
│
├─ 冷启动延迟 > 30s？
│   └─ YES → 使用已热启动的备用副本（预留 1 张卡）
│
└─ 显存不足？
    └─ YES → 释放低优先级任务占用的显存，或触发 A100 弹性实例
```

---

## 八、与 python-backend-core 的切换逻辑

```
python-backend-core（本地 ComfyUI）          ←→  云端 GPU（ComfyUI-Worker）
                         │
                         ├── 本地 GPU 可用 + 显存充足 → 使用本地
                         ├── 本地 GPU 不可用/显存不足 → 切换到云端
                         ├── 云端任务排队 < 3 个 → 优先本地
                         └── 云端任务排队 ≥ 3 个 → 使用云端（本地降级）
```