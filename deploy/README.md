# NexusVideo MVP 生产环境部署指南

> 作者：唐磐石（运维架构师）
> 版本：v1.0 | 更新日期：2026-08-18

---

## 一、架构总览

```
                    ┌─────────────────────────────────────────────────┐
                    │               客户端 (Tauri / Web)                │
                    └────────────────────┬────────────────────────────┘
                                         │ HTTPS (443)
                                         ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         Nginx 反向代理网关                              │
│                                                                        │
│  /api/*          → FastAPI (8000)  ──→ Redis 队列 ──→ ComfyUI-Worker  │
│  /static/*       → 本地/OSS 静态资源                                     │
│  /progress/ws    → WebSocket 升级 → FastAPI                             │
│  /health         → 各服务健康检测                                        │
│  /metrics        → Prometheus /metrics                                 │
└────────────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
    ┌──────────┐     ┌──────────────┐     ┌──────────────────┐
    │ PostgreSQL│     │    Redis     │     │ ComfyUI-Worker   │
    │ (5432)   │     │   (6379)     │     │ (8188, GPU)      │
    └──────────┘     └──────────────┘     └──────────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │  Prometheus      │
                    │  (9090)          │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │    Grafana       │
                    │    (3000)        │
                    └──────────────────┘
```

### 服务端口对照表

| 服务 | 容器端口 | 宿主机端口 | 说明 |
|------|---------|-----------|------|
| Nginx | 80/443 | 80/443 | HTTP/HTTPS 入口 |
| FastAPI | 8000 | 8000 | 主应用 API |
| Redis | 6379 | 不暴露 | 内部通信 |
| PostgreSQL | 5432 | 5432 | 数据库 |
| ComfyUI-Worker | 8188 | 不暴露 | GPU 推理 |
| Prometheus | 9090 | 9090 | 监控指标 |
| Grafana | 3000 | 3000 | 监控看板 |

---

## 二、前置条件

### 2.1 硬件要求

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 4 核 | 8 核 |
| 内存 | 16 GB | 32 GB |
| 磁盘 | 100 GB SSD | 500 GB NVMe SSD |
| GPU | 1× NVIDIA A10 (24GB) | 2× NVIDIA A10 (24GB) |
| 网络 | 100 Mbps | 1 Gbps |

### 2.2 软件依赖

```bash
# 安装 Docker + Docker Compose (v2.20+)
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker

# 验证版本
docker --version        # >= 24.0
docker compose version  # >= 2.20

# 安装 NVIDIA Container Toolkit（GPU 支持）
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | sed 's#deb https://nvidia.github.io/libnvidia-container#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://nvidia.github.io/libnvidia-container#' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo nvidia-container-toolkit configure
sudo systemctl restart docker

# 验证 GPU 可被容器访问
docker run --rm --runtime=nvidia --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### 2.3 安全要求

- 生成 JWT 密钥：`openssl rand -hex 32`
- 生成 PostgreSQL 密码：`openssl rand -base64 24`
- 生成 Grafana 管理密码：`openssl rand -base64 16`
- SSL 证书：通过 Let's Encrypt 或云平台证书服务获取

---

## 三、部署步骤

### Step 1：克隆仓库 & 进入 deploy 目录

```bash
cd NexusVideo
cd deploy
```

### Step 2：配置环境变量

```bash
# 创建 .env 文件
cat > .env << 'EOF'
# --- JWT 鉴权 ---
JWT_SECRET_KEY=your-jwt-secret-key-change-this-in-production
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# --- 数据库 ---
POSTGRES_PASSWORD=your-secure-postgres-password

# --- Redis ---
REDIS_URL=redis://redis:6379/0

# --- ComfyUI ---
COMFYUI_API_PORT=8188
TASK_TIMEOUT=600
GPU_ALLOWLIST=0,1

# --- Grafana ---
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=your-grafana-password

# --- CORS 白名单 ---
CORS_ORIGINS=https://app.nexusvideo.com,https://admin.nexusvideo.com

# --- 限流 ---
API_RATE_LIMIT=30/minute
EOF
```

### Step 3：配置 Nginx SSL 证书

```bash
mkdir -p nginx/conf.d nginx/ssl

# 如果使用 Let's Encrypt，先获取证书
sudo certbot --nginx -d app.nexusvideo.com -d api.nexusvideo.com

# 将证书复制到 Docker volume
# （首次启动后通过 docker cp 传入，或使用外部证书管理）
```

创建 `nginx/nginx.conf`：

```nginx
worker_processes auto;
events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" $request_time';

    # 限流区域（每 IP 30 req/min）
    limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;
    limit_conn_zone $binary_remote_addr zone=addr:10m;

    server {
        listen 80;
        server_name _;
        return 301 https://$host$request_uri;
    }

    server {
        listen 443 ssl http2;
        server_name app.nexusvideo.com;

        ssl_certificate     /etc/nginx/ssl/fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/privkey.pem;

        # 安全头
        add_header X-Frame-Options DENY;
        add_header X-Content-Type-Options nosniff;
        add_header X-XSS-Protection "1; mode=block";

        # FastAPI API
        location /api/ {
            limit_req zone=api burst=10 nodelay;
            proxy_pass http://fastapi:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # 超时配置
            proxy_read_timeout 300s;
            proxy_send_timeout 300s;
            proxy_connect_timeout 10s;
        }

        # WebSocket 进度推送
        location /progress/ws {
            proxy_pass http://fastapi:8000;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_read_timeout 86400s;  # 长连接超时
        }

        # 静态资源（前端产物）
        location /static/ {
            alias /usr/share/nginx/html/;
            expires 7d;
            add_header Cache-Control "public, immutable";
        }

        # 健康检查（Nginx 自身）
        location /health {
            access_log off;
            return 200 '{"status":"ok","service":"nginx"}';
            add_header Content-Type application/json;
        }

        # Prometheus 指标转发
        location /metrics {
            proxy_pass http://prometheus:9090/metrics;
            allow 127.0.0.1;
            allow 10.0.0.0/8;
            deny all;
        }

        # 上传视频（大文件支持）
        client_max_body_size 500M;
    }
}
```

### Step 4：初始化数据库脚本

创建 `postgresql/init.sql`：

```sql
-- 用户数据库初始化（Alembic 迁移后自动创建表）
CREATE DATABASE nexusvideo;
```

### Step 5：启动服务

```bash
# 拉取镜像
docker compose -f docker-compose.prod.yml pull

# 启动所有服务
docker compose -f docker-compose.prod.yml up -d

# 检查启动状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f
```

### Step 6：初始化数据库

```bash
docker compose -f docker-compose.prod.yml exec fastapi alembic upgrade head
```

### Step 7：验证部署

```bash
# Nginx 健康检查
curl -I https://app.nexusvideo.com/health

# FastAPI 健康检查
curl -I http://localhost:8000/health

# Redis 连通性
docker compose -f docker-compose.prod.yml exec redis redis-cli ping

# PostgreSQL 连通性
docker compose -f docker-compose.prod.yml exec postgres pg_isready -U nvuser -d nexusvideo

# ComfyUI 连通性（首次启动约需 1-2 分钟加载模型）
curl http://localhost:8188/system_stats

# Prometheus
curl http://localhost:9090/-/ready

# Grafana
curl http://localhost:3000/api/health
```

### Step 8：配置 Grafana 看板

1. 访问 http://localhost:3000
2. 登录 admin / 密码
3. Dashboard JSON 已自动 provisioned（`grafana-dashboard.json`）
4. 或手动 Import → 选择 JSON 文件

---

## 四、常用运维命令

```bash
# 查看各服务状态
docker compose -f docker-compose.prod.yml ps

# 查看服务日志（最近 100 行）
docker compose -f docker-compose.prod.yml logs --tail=100 fastapi

# 重启单个服务
docker compose -f docker-compose.prod.yml restart fastapi

# 进入容器
docker compose -f docker-compose.prod.yml exec fastapi /bin/bash

# 查看 GPU 显存
docker compose -f docker-compose.prod.yml exec comfyui-worker nvidia-smi

# 查看 Redis 队列长度
docker compose -f docker-compose.prod.yml exec redis redis-cli LLEN task_queue

# 数据库备份
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U nvuser nexusvideo > backup_$(date +%Y%m%d).sql

# 优雅停机
docker compose -f docker-compose.prod.yml down

# 强制清理（会删除数据！）
docker compose -f docker-compose.prod.yml down -v
```

---

## 五、SSL 证书自动续期

```bash
# 创建 cron 任务（每周一 3:00 检查续期）
cat > /etc/cron.weekly/renew-ssl << 'EOF'
#!/bin/bash
certbot renew --quiet
systemctl reload nginx
EOF
chmod +x /etc/cron.weekly/renew-ssl
```

---

## 六、故障快速排查

| 症状 | 快速诊断命令 | 常见原因 |
|------|------------|---------|
| 404 页面 | `curl https://domain/health` | Nginx 配置错误 |
| 502 Bad Gateway | `docker ps` 检查容器状态 | FastAPI 崩溃/未启动 |
| 504 Gateway Timeout | `docker logs fastapi` | ComfyUI 任务超时 |
| 显存不足 | `docker exec comfyui-worker nvidia-smi` | 并发过高/模型过大 |
| 数据库连接失败 | `docker exec postgres pg_isready` | 磁盘满/OOM |
| 队列堆积 | `docker exec redis redis-cli LLEN task_queue` | Worker 处理慢/数量不足 |

---

## 七、成本核算（月度预估）

| 项目 | 规格 | 单价 | 用量 | 月费用 |
|------|------|------|------|--------|
| GPU 实例（A10 24GB） | 1× A10 | ¥8/小时 | 720h（常驻） | ¥5,760 |
| GPU 实例（弹性） | A10 | ¥8/小时 | 200h（按需） | ¥1,600 |
| 应用服务器 | 4C8G | ¥1/小时 | 720h | ¥720 |
| 数据库（RDS PG） | 2C4G | ¥0.5/小时 | 720h | ¥360 |
| Redis | 2GB | ¥0.2/小时 | 720h | ¥144 |
| 监控（Prometheus） | 含在应用服务器 | - | - | ¥0 |
| OSS 存储（视频） | 100GB | ¥0.0015/GB/天 | 30天 | ¥4.5 |
| 带宽 | 100Mbps 包年 | ¥200/月 | - | ¥200 |
| **合计** | | | | **¥8,788.5** |

> 说明：MVP 阶段 GPU 可按需启停，凌晨自动缩容可节省约 30% GPU 费用。