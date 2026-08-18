# NexusVideo 云端 GPU 部署执行手册

> 版本：v1.0 | 适用环境：staging / production | 提供商：阿里云

---

## 前提条件

| 项目 | 要求 | 验证命令 |
|---|---|---|
| 阿里云账号 | 已开通 VPC、NAS、OSS、RDS、Redis、SLB、ECS、ESS、ACR | 控制台确认 |
| RAM 权限 | AdministratorAccess 或自定义策略（含上述服务全部资源） | `aliyun ram GetPolicy` |
| Terraform | ≥ 1.5.0 | `terraform version` |
| Docker | ≥ 24.0，含 NVIDIA Container Toolkit | `docker version && nvidia-smi` |
| kubectl | ≥ 1.28 | `kubectl version --client` |
| aliyun CLI | 最新版 | `aliyun --version` |
| 阿里云 ACR | 已创建命名空间 nexusvideo | 控制台确认 |
| 域名 | api.nexusvideo.com / app.nexusvideo.com 已解析 | `dig api.nexusvideo.com` |
| SSL 证书 | Let's Encrypt（certbot）或阿里云 CA 签发 | — |

环境变量（写入 `~/.bashrc` 或 Terraform env）：

```bash
export ALICLOUD_ACCESS_KEY="LTAI5t..."
export ALICLOUD_SECRET_KEY="xxxx"
export ALICLOUD_REGION="cn-beijing"
export ACR_NAMESPACE="nexusvideo"
export ACR_REGION="cn-beijing"
```

---

## 架构总览

```
用户浏览器 / Tauri 客户端
        │
        ▼
  api.nexusvideo.com（SLB 443）
        │
        ▼
  ┌────────────────────────────┐
  │ Nginx（Ingress / 反向代理） │
  │  /api/v1/* → API Gateway    │
  │  /ws/*    → WebSocket 转发   │
  │  /        → 静态前端        │
  └──────────┬─────────────────┘
             │
             ▼
  ┌────────────────────────────┐
  │ Inference Gateway (FastAPI)│
  │ JWT 鉴权 + 额度校验          │
  │ Redis 优先级队列             │
  │ WebSocket 进度转发           │
  │ 限流（IP + 用户）            │
  └──────────┬─────────────────┘
             │
      ┌──────┴──────┐
      ▼              ▼
  ┌─────────┐   ┌────────────┐
  │ RDS     │   │ Redis      │
  │ (任务/   │   │ (队列/     │
  │  用户)   │   │  会话/限流) │
  └─────────┘   └────────────┘
             │
             ▼
  ┌────────────────────────────┐
  │ ComfyUI Worker 集群 (K8s)   │
  │ min=1 max=10 · GPU A10     │
  │ 抢占式 · NAS 模型挂载       │
  └──────────┬─────────────────┘
             │
             ▼
  ┌────────────────────────────┐
  │ NAS（/models）· OSS（/out）│
  └────────────────────────────┘
```

---

## Step 1：初始化 Terraform 工作区

```bash
# 进入 Terraform 目录
cd /c/Users/DMax/NexusVideo/devops/terraform

# 复制并编辑变量文件
cp terraform.tfvars.example terraform.tfvars.staging

# 编辑 tfvars，按需修改：
#   env = "staging"
#   region = "cn-beijing"
#   gpu_zone = "cn-beijing-h"     # 确认有 A10/A100 可用
#   general_zone = "cn-beijing-g"
#   gpu_image_id = "ubuntu_22_04_x64_20G_alibase_20240601.vhd"
#   gpu_instance_type = "ecs.gn7i-c16g1.4xlarge"   # 1×A10 24GB
#   gpu_spot_instance_type = "ecs.gn7i-c16g1.4xlarge"
#   ess_min_size = 1
#   ess_max_size = 10
#   nas_size_gb = 200
#   rds_instance_type = "rds.mysql.t2.micro"
#   redis_instance_type = "redis.conf.small.r6"
#   db_password = "Str0ng!P@ss2024"
#   admin_cidr = "你的公网IP/32"

# 初始化
terraform init

# 预期输出：
# Initializing the backend...
# Initializing provider plugins...
# - Finding aliyun/alicloud versions matching "~> 1.250"...
# - Installing aliyun/alicloud v1.250.0...
# Terraform has been successfully initialized!
```

---

## Step 2：创建基础设施

```bash
# 先做一次 plan 审核
terraform plan -var-file="terraform.tfvars.staging"

# 预期输出：
# Plan: 45 to add, 0 to change, 0 to destroy.

# 确认后 apply
terraform apply -var-file="terraform.tfvars.staging" -auto-approve

# 预期输出：
# alicloud_vpc.main: Creating...
# alicloud_vswitch.gpu: Creating...
# ...
# Apply complete! Resources: 45 added, 0 changed, 0 destroyed.

# 输出关键信息
terraform output -json
```

**验证基础设施**：

```bash
# 检查 VPC
aliyun ecs DescribeVpcs --RegionId cn-beijing --VpcName "nexusvideo-staging-vpc"
# 应返回一个 VPC 信息

# 检查 NAS 挂载
aliyun nas DescribeFileSystems --RegionId cn-beijing | jq '.FileSystems[] | select(.Name | startswith("nexusvideo-staging"))'

# 检查 Redis 状态
aliyun redis DescribeInstances --RegionId cn-beijing | jq '.items[] | select(.InstanceName | startswith("nexusvideo-staging")) | .InstanceStatus'
# 期望：Running
```

---

## Step 3：构建 ComfyUI Worker Docker 镜像

```bash
cd /c/Users/DMax/NexusVideo/devops/docker

# 三层镜像构建（耗时约 25-40 分钟，取决于网络）
docker build \
  --build-arg PYTORCH_VERSION=2.1.0 \
  --build-arg XFORMERS_VERSION=0.0.22 \
  -t nexusvideo/comfyui-worker:v1.0.0 \
  -f comfyui-worker/Dockerfile \
  comfyui-worker/

# 预期输出末尾：
# Successfully built xxx
# Successfully tagged nexusvideo/comfyui-worker:v1.0.0

# 验证镜像大小（预期 8-12GB）
docker images nexusvideo/comfyui-worker:v1.0.0

# 验证镜像启动（在本地 GPU 上快速冒烟测试）
docker run --rm --gpus all -p 9001:9000 \
  -v /tmp/test_models:/models \
  nexusvideo/comfyui-worker:v1.0.0

# 新终端测试健康检查端点：
curl http://localhost:9001/health
# 应返回 {"status": "alive"}
```

---

## Step 4：推送镜像到阿里云 ACR

```bash
# 登录 ACR
# 在 ACR 控制台获取登录命令，形如：
docker login --username=<阿里云账号> registry.cn-beijing.aliyuncs.com

# 重新 tag
docker tag nexusvideo/comfyui-worker:v1.0.0 \
  registry.cn-beijing.aliyuncs.com/nexusvideo/comfyui-worker:v1.0.0

# 推送（约 5-10 分钟）
docker push registry.cn-beijing.aliyuncs.com/nexusvideo/comfyui-worker:v1.0.0

# 预期输出：
# The push refers to repository [registry.cn-beijing.aliyuncs.com/nexusvideo/comfyui-worker]
# v1.0.0: digest: sha256:abc123...

# 验证
docker pull registry.cn-beijing.aliyuncs.com/nexusvideo/comfyui-worker:v1.0.0
# 应能正常拉取
```

同时推送 API Gateway 镜像（FastAPI 网关 + Nginx）：

```bash
# 构建 API Gateway 镜像
docker build -t nexusvideo/inference-gateway:v1.0.0 -f deploy/Dockerfile-gateway api/
docker tag nexusvideo/inference-gateway:v1.0.0 \
  registry.cn-beijing.aliyuncs.com/nexusvideo/inference-gateway:v1.0.0
docker push registry.cn-beijing.aliyuncs.com/nexusvideo/inference-gateway:v1.0.0
```

---

## Step 5：部署到 K8s 集群

> NexusVideo 选择 K8s 部署方案（比 Docker Compose 弹性更强、更适合 GPU 调度）。
> 以下假设已有一个阿里云 ACK（Kubernetes）集群，GPU 节点池已配置。

### 5.1 准备 kubeconfig

```bash
# 在 ACK 控制台获取 kubeconfig，写入 ~/.kube/config
# 或用 aliyun CLI：
aliyun cs DescribeClusterDetail --ClusterId <cluster-id> | jq -r '.clusterInfo.kubeconfig' > ~/.kube/nexusvideo.kubeconfig
export KUBECONFIG=~/.kube/nexusvideo.kubeconfig

# 验证
kubectl get nodes
# 应看到 GPU 节点（如 gn7i-c16g1.4xlarge）
```

### 5.2 创建命名空间和 ConfigMap

```bash
cd /c/Users/DMax/NexusVideo/devops/deploy/k8s

# 创建命名空间
kubectl apply -f namespace.yaml

# 创建 ConfigMap（工作流路径引用）
kubectl apply -f configmap.yaml

# 创建 Secret（用 External Secrets Operator 或 Vault 对接 KMS 推荐）
kubectl apply -f secrets.yaml
```

### 5.3 部署 ComfyUI Worker

```bash
kubectl apply -f deployment.yaml

# 验证 Worker 启动
kubectl get pods -n nexusvideo -l app=comfyui-worker -w
# 期望状态：Running（需要等待模型挂载完成，约 30-60 秒）

# 检查日志
kubectl logs -n nexusvideo deploy/comfyui-worker --tail=50
# 应看到 "[INFO] ComfyUI 已就绪" 和 "[INFO] 健康检查端点启动"

# 测试 readiness
kubectl exec -n nexusvideo deploy/comfyui-worker -- curl -s http://localhost:8000/ready
# 期望：{"status": "ready", "gpu_available": true, "nas_mounted": true, "comfyui_ready": true}
```

### 5.4 部署 Service + Ingress + HPA

```bash
kubectl apply -f service.yaml
kubectl apply -f hpa.yaml

# 创建 Ingress（需已有 Ingress Controller，阿里云 ACK 内置）
kubectl apply -f ingress.yaml

# 验证 Ingress
kubectl get ingress -n nexusvideo
# 应返回域名和外部 IP

# 验证 HPA
kubectl get hpa -n nexusvideo
# 应显示当前副本数和目标队列长度
```

---

## Step 6：配置 Nginx + SSL

### 6.1 部署 Nginx 反向代理

```bash
# 使用阿里云 CLB/SLB 做 443 入口，内部 Nginx 处理路由
# 或将 nginx.conf 挂载到 Ingress Controller 的 ConfigMap

kubectl create configmap nexusvideo-nginx \
  --from-file=nginx.conf=/c/Users/DMax/NexusVideo/devops/deploy/nginx/nginx.conf \
  -n nexusvideo
```

### 6.2 SSL 证书

```bash
# 方案 A：阿里云 CA 签发（推荐）
# 在 SSL 控制台申请免费证书，绑定 api.nexusvideo.com
# 下载后上传到 K8s Secret
kubectl create secret tls nexusvideo-tls \
  --cert=api.nexusvideo.com.pem \
  --key=api.nexusvideo.com.key \
  -n nexusvideo

# 方案 B：Let's Encrypt（certbot）
# 在 SLB 前端节点执行：
sudo certbot certonly --nginx -d api.nexusvideo.com -d app.nexusvideo.com
sudo certbot renew --dry-run  # 验证自动续期
```

---

## Step 7：验证部署

```bash
# 1. 健康检查
curl -s https://api.nexusvideo.com/health
# 期望：{"status": "ok"}

curl -s https://api.nexusvideo.com/ready
# 期望：{"workers_ready": 1, "queue_depth": 0}

# 2. 提交测试任务
curl -s -X POST https://api.nexusvideo.com/api/v1/generate \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "txt2video",
    "prompt": "A cat walking on the beach, cinematic",
    "params": {"duration": 5, "resolution": "720p"}
  }'
# 期望：{"task_id": "task_xxx", "queue_position": 1, "estimated_seconds": 60}

# 3. WebSocket 进度验证（用 wscat）
npx wscat -c wss://api.nexusvideo.com/ws/progress?task_id=task_xxx
# 应收到进度事件：{"task_id": "task_xxx", "progress": 10, "step": "Loading model..."}

# 4. Grafana 看板
# 浏览器访问 http://<SLB-IP>:3000
# 用户名/密码：admin/admin（生产环境务必修改）
# 应看到 GPU 利用率、任务队列深度、响应延迟等面板
```

---

## Step 8：回滚方案

### 8.1 基础设施回滚

```bash
cd /c/Users/DMax/NexusVideo/devops/terraform
terraform destroy -var-file="terraform.tfvars.staging" -auto-approve

# 预期输出：
# Apply complete! Resources: 0 added, 0 changed, 45 destroyed.
```

### 8.2 镜像回退

```bash
# 切回上一版本镜像
kubectl set image deployment/comfyui-worker \
  -n nexusvideo \
  comfyui-worker=registry.cn-beijing.aliyuncs.com/nexusvideo/comfyui-worker:v0.9.9

# 验证回退成功
kubectl rollout status deployment/comfyui-worker -n nexusvideo
# deployment "comfyui-worker" successfully rolled out

# 检查 Worker 健康状态
kubectl exec -n nexusvideo deploy/comfyui-worker -- curl -s http://localhost:8000/health
```

### 8.3 灰度发布（金丝雀）

```bash
# 先部署新版本到 1 个 Pod
kubectl scale deployment/comfyui-worker-canary -n nexusvideo --replicas=1

# 观察 10 分钟错误率和延迟
# 确认无问题后切全部流量
kubectl scale deployment/comfyui-worker -n nexusvideo --replicas=0
kubectl scale deployment/comfyui-worker-canary -n nexusvideo --replicas=10
```

### 8.4 数据恢复

```bash
# RDS 备份恢复
# 在阿里云 RDS 控制台选择「备份恢复」→「按时间点恢复」→ 恢复到新实例
# 或将备份文件还原：
aliyun rds RestoreInstance --DBInstanceId <id> --RestoreTime "2024-12-01T00:00:00Z"

# OSS 文件恢复（30天转 IA，90天转 Archive，需先解冻）
# 在 OSS 控制台搜索文件 → 解冻（Archive 解冻约需 1 小时）
```

---

## 附录：成本核算（staging 示例）

| 资源 | 规格 | 单价 | 月成本估算 |
|---|---|---|---|
| 1×A10 Worker（包月基线） | gn7i-c16g1.4xlarge | ¥3.2/小时 | ¥2,304 |
| 抢占式 A10（弹性） | 按次 ¥0.12 | — | 视调用量 |
| NAS 容量型 | 200GB | ¥0.012/GB/月 | ¥2.4 |
| OSS 标准+IA+Archive | 50GB | 综合 ¥0.008/GB/月 | ¥0.4 |
| RDS T2.micro | MySQL 8.0 20GB | ¥0.2/小时 | ¥144 |
| Redis 单节点 | 1GB | ¥0.15/小时 | ¥108 |
| SLB 按量 | 10Mbps 峰值 | ¥0.5/天 | ¥15 |
| **staging 月度总计** | | | **~¥2,574** |

> 生产环境建议保留 2 台基线 + 8 台弹性上限，月成本约 ¥5,000-8,000。