# NexusVideo Terraform IaC 使用说明

## 概述

本目录包含 NexusVideo 云端 GPU 基础设施的 Terraform 脚本，用于在阿里云上一键部署：

| 资源 | 用途 | 备注 |
|------|------|------|
| VPC + 子网 | 网络隔离 | GPU 子网 + 常规子网 |
| 安全组 | 入站规则 | ComfyUI / API / 监控三套 |
| NAS | 共享存储 | 100GB 容量型，挂载模型文件 |
| OSS | 结果持久化 | 30天低频 / 90天归档 |
| RDS MySQL | 用户数据 + 任务记录 | 8.0，跨可用区 |
| Redis | 任务队列 + 会话缓存 | 6.0，1GB |
| SLB | 入口负载均衡 | 443 HTTPS + 80 HTTP |
| ESS | GPU 弹性伸缩 | A10 包月基线 + 抢占式弹性 |

## 前置条件

1. 已安装 Terraform >= 1.5.0
2. 已配置阿里云 AccessKey：
   ```bash
   export ALICLOUD_ACCESS_KEY="LTAI..."
   export ALICLOUD_SECRET_KEY="xxxx..."
   ```
3. 已在阿里云 ACR 推送 ComfyUI Worker 镜像
4. GPU 镜像 ID 需在 `gpu_image_id` 中填入

## 部署流程

### 1. 初始化
```bash
cd devops/terraform
terraform init
```

### 2. 规划（staging）
```bash
terraform plan -var-file="terraform.tfvars.staging"
```

### 3. 应用
```bash
terraform apply -var-file="terraform.tfvars.staging"
```

### 4. 导出连接信息
```bash
terraform output -json
```

## 环境变量（从 terraform output 获取后注入）

| 变量 | 值来源 | 用途 |
|------|--------|------|
| `REDIS_HOST` | redis_endpoint | Celery 任务队列 |
| `DB_HOST` | rds_endpoint | SQLAlchemy 连接 |
| `NAS_MOUNT` | nas_mount_point | ComfyUI Worker 挂载 /models |
| `OSS_BUCKET` | oss_bucket_name | 结果上传目标 |
| `SLB_IP` | slb_ip | Nginx 反向代理目标 |

## 弹性伸缩策略

| 规则 | 触发条件 | 动作 | 冷却 |
|------|---------|------|------|
| 扩容 | GPU 利用率 > 70% 持续 5 分钟 | +2 台 | 300s |
| 缩容 | GPU 利用率 < 30% 持续 10 分钟 | -1 台 | 300s |
| 缩容下限 | 实例数 > 1（基线） | 保留 1 台包月 | — |

> 抢占式实例中断时，ESS 自动释放并补新实例，Worker 优雅排空正在执行的任务。

## OSS 生命周期

| 阶段 | 天数 | 存储类型 | 单价参考 |
|------|------|---------|---------|
| 热存 | 0-30 天 | 标准存储 | ¥0.12/GB/月 |
| 低频 | 30-90 天 | 低频访问 | ¥0.06/GB/月 |
| 归档 | 90+ 天 | 归档存储 | ¥0.012/GB/月 |

## 成本估算（staging 参考）

| 资源 | 规格 | 月度成本（约） |
|------|------|--------------|
| ESS 基线 | A10 × 1 包月 | ¥1,500 |
| ESS 弹性 | A10 抢占式 × 5 | ¥1,500（平均） |
| NAS | 100GB 容量型 | ¥20 |
| OSS | 1TB 存储 + 出网 | ¥150 |
| RDS | t2.small | ¥120 |
| Redis | basic.2c.xd 1GB | ¥100 |
| SLB | 100Mbps | ¥80 |
| **合计** | | **约 ¥3,470/月** |

## 生产环境切换

1. 复制 `terraform.tfvars.example` 为 `terraform.tfvars.production`
2. 修改 `env = "production"`
3. 升级 RDS / Redis 规格
4. `admin_cidr` 改为公司 VPN CIDR
5. `db_password` 使用 Secrets Manager 注入
6. `terraform apply -var-file="terraform.tfvars.production"`

## 安全注意事项

- **生产环境**禁止将 `admin_cidr` 设为 `0.0.0.0/0`
- RDS 密码必须使用 Secrets Manager 或 KMS 管理
- OSS Bucket 保持 `private` ACL
- ComfyUI 端口（9000 / 8000）仅允许同 VPC 内网访问