# NexusVideo 云端 GPU 基础设施 Terraform 脚本
# 适用环境：staging / production
# 提供商：阿里云（AliCloud）
#
# 使用方法：
#   terraform init
#   terraform plan -var-file="terraform.tfvars.staging"
#   terraform apply -var-file="terraform.tfvars.staging"
#
# 资源命名规范：nexusvideo-{env}-{type}
# 例如：nexusvideo-staging-vpc, nexusvideo-staging-ess

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.250"
    }
  }
}

provider "alicloud" {
  region = var.region
  # 通过环境变量 ALICLOUD_ACCESS_KEY / ALICLOUD_SECRET_KEY 注入
}

# ============================================================
# VPC：专有网络
# ============================================================
resource "alicloud_vpc" "main" {
  vpc_name   = "nexusvideo-${var.env}-vpc"
  cidr_block = var.vpc_cidr

  tags = local.tags
}

# 路由表（默认即可，无需额外路由）
resource "alicloud_route_table" "main" {
  vpc_id     = alicloud_vpc.main.id
  name       = "nexusvideo-${var.env}-rt"
  depends_on = [alicloud_vpc.main]
}

# ============================================================
# 交换机（子网）：GPU 可用区 + 常规可用区
# ============================================================
# GPU 子网（用于 A10/A100 实例）
resource "alicloud_vswitch" "gpu" {
  vpc_id          = alicloud_vpc.main.id
  zone_id         = var.gpu_zone
  cidr_block      = var.gpu_subnet_cidr
  name            = "nexusvideo-${var.env}-gpu-vsw"
  route_table_id  = alicloud_route_table.main.id

  tags = merge(local.tags, { role = "gpu" })
}

# 常规子网（用于 Redis / Nginx / Prometheus 等）
resource "alicloud_vswitch" "general" {
  vpc_id          = alicloud_vpc.main.id
  zone_id         = var.general_zone
  cidr_block      = var.general_subnet_cidr
  name            = "nexusvideo-${var.env}-general-vsw"
  route_table_id  = alicloud_route_table.main.id

  tags = merge(local.tags, { role = "general" })
}

# ============================================================
# 安全组
# ============================================================
resource "alicloud_security_group" "comfyui" {
  name        = "nexusvideo-${var.env}-sg-comfyui"
  vpc_id      = alicloud_vpc.main.id
  description = "ComfyUI Worker 内部通信（9000 API + 8000 probe）"

  tags = merge(local.tags, { role = "comfyui" })
}

# ComfyUI Worker 入站：仅允许同 VPC 内网 9000（API）+ 8000（健康检查）
resource "alicloud_security_group_rule" "comfyui_allow_internal" {
  type              = "ingress"
  ip_protocol       = "tcp"
  from_port         = 9000
  to_port           = 9000
  cidr_ip           = var.vpc_cidr
  security_group_id = alicloud_security_group.comfyui.id
  policy            = "accept"
  priority          = 1
  description       = "允许同 VPC 内网访问 ComfyUI API"
}

resource "alicloud_security_group_rule" "comfyui_probe" {
  type              = "ingress"
  ip_protocol       = "tcp"
  from_port         = 8000
  to_port           = 8000
  cidr_ip           = var.vpc_cidr
  security_group_id = alicloud_security_group.comfyui.id
  policy            = "accept"
  priority          = 2
  description       = "健康检查端点（liveness/readiness）"
}

resource "alicloud_security_group" "api" {
  name        = "nexusvideo-${var.env}-sg-api"
  vpc_id      = alicloud_vpc.main.id
  description = "FastAPI + Nginx 对外暴露（SLB 后端）"

  tags = merge(local.tags, { role = "api" })
}

resource "alicloud_security_group_rule" "api_allow_slb" {
  type              = "ingress"
  ip_protocol       = "tcp"
  from_port         = 8000
  to_port           = 8000
  cidr_ip           = var.vpc_cidr
  security_group_id = alicloud_security_group.api.id
  policy            = "accept"
  priority          = 1
  description       = "允许 SLB 回源到 FastAPI"
}

resource "alicloud_security_group" "monitoring" {
  name        = "nexusvideo-${var.env}-sg-monitoring"
  vpc_id      = alicloud_vpc.main.id
  description = "Prometheus + Grafana 监控"

  tags = merge(local.tags, { role = "monitoring" })
}

resource "alicloud_security_group_rule" "grafana_allow_vpc" {
  type              = "ingress"
  ip_protocol       = "tcp"
  from_port         = 3000
  to_port           = 3000
  cidr_ip           = var.admin_cidr
  security_group_id = alicloud_security_group.monitoring.id
  policy            = "accept"
  priority          = 1
  description       = "Grafana 管理端仅允许运维 IP 段"
}

# ============================================================
# NAS 文件存储（模型 + 用户数据共享挂载）
# ============================================================
resource "alicloud_nas_file_system" "main" {
  name        = "nexusvideo-${var.env}-nas"
  description = "NexusVideo 共享存储：模型文件 + 生成结果"

  storage_type    = " Capacity"   # 容量型（成本最优）
  protocol_type   = "NFS"
  capacity        = var.nas_size_gb

  tags = merge(local.tags, { role = "nas" })
}

resource "alicloud_nas_mount_target" "gpu_zone" {
  file_system_id = alicloud_nas_file_system.main.id
  vswitch_id     = alicloud_vswitch.gpu.id
  depends_on     = [alicloud_nas_file_system.main]
}

resource "alicloud_nas_mount_target" "general_zone" {
  file_system_id = alicloud_nas_file_system.main.id
  vswitch_id     = alicloud_vswitch.general.id
  depends_on     = [alicloud_nas_file_system.main]
}

resource "alicloud_nas_fs_permission" "worker_access" {
  file_system_id = alicloud_nas_file_system.main.id
  rule           = "rw"
  client         = var.vpc_cidr
}

# ============================================================
# OSS 对象存储（生成结果持久化）
# ============================================================
resource "alicloud_oss_bucket" "outputs" {
  bucket        = "nexusvideo-${var.env}-outputs"
  acl           = "private"
  force_destroy = true

  tags = merge(local.tags, { role = "outputs" })
}

# OSS 生命周期规则：30天转低频，90天转归档
resource "alicloud_oss_bucket_lifecycle_rule" "outputs_lifecycle" {
  bucket = alicloud_oss_bucket.outputs.id
  enabled = true
  rule_id = "auto-archive"

  rule {
    enabled = true
    rule_id = "transition-to-infrequent"
    prefix  = ""

    transition {
      days          = 30
      storage_class = "IA"  # 低频访问
    }

    transition {
      days          = 90
      storage_class = "Archive"  # 归档
    }
  }
}

# ============================================================
# RDS MySQL（用户数据 + 任务记录）
# ============================================================
resource "alicloud_rds_instance" "main" {
  engine                   = "MySQL"
  engine_version           = "8.0"
  instance_type            = var.rds_instance_type
  instance_storage         = var.rds_storage_gb
  instance_name            = "nexusvideo-${var.env}-rds"
  security_groups          = [alicloud_security_group.api.id]
  vswitch_id               = alicloud_vswitch.general.id
  instance_network_type    = "VPC"
  pay_type                 = "Postpaid"  # 按量付费

  multi_zone    = true
  zone_id       = var.general_zone

  tags = merge(local.tags, { role = "rds" })
}

resource "alicloud_rds_account" "app" {
  instance_id = alicloud_rds_instance.main.id
  account_name = "nexusvideo_app"
  account_password = var.db_password
}

resource "alicloud_rds_database" "app" {
  instance_id = alicloud_rds_instance.main.id
  name        = "nexusvideo"
  character_type = "utf8mb4"
}

# ============================================================
# Redis（任务队列 + 会话缓存）
# ============================================================
resource "alicloud_redis_instance" "main" {
  instance_name       = "nexusvideo-${var.env}-redis"
  engine_version      = "6.0"
  instance_spec       = var.redis_instance_type
  capacity            = var.redis_capacity_gb
  vswitch_id          = alicloud_vswitch.general.id
  security_group_id   = alicloud_security_group.api.id
  instance_charge_type = "Postpaid"
  port                = 6379
  security_ips        = [var.vpc_cidr]

  tags = merge(local.tags, { role = "redis" })
}

# ============================================================
# SLB（负载均衡器，作为 API 网关入口）
# ============================================================
resource "alicloud_slb" "main" {
  name            = "nexusvideo-${var.env}-slb"
  internet_charge_type = "PayByTraffic"
  io_optimized    = true
  address_ip_version = "ipv4"
  internet_max_bandwidth_out = var.slb_bandwidth_mbps

  tags = merge(local.tags, { role = "slb" })
}

# SLB 监听（443 HTTPS → 8000 FastAPI / 80 Nginx）
resource "alicloud_slb_listener" "https" {
  load_balancer_id = alicloud_slb.main.id
  protocol         = "https"
  load_balancer_port = 443
  backend_port     = 8000
  bandwidth        = -1
  scheduler        = "wrr"

  health_check {
    type                = "tcp"
    interval            = 5
    unhealthy_threshold = 2
    healthy_threshold   = 2
    timeout             = 3
    healthy_http_code   = "200"
  }
}

resource "alicloud_slb_listener" "http_nginx" {
  load_balancer_id = alicloud_slb.main.id
  protocol         = "http"
  load_balancer_port = 80
  backend_port     = 80
  scheduler        = "rr"
}

# ============================================================
# ESS 弹性伸缩组（GPU Worker 弹性伸缩）
# ============================================================

# 镜像：自定义 ComfyUI Worker 镜像（在 ACR 中构建）
# 此处假设已在阿里云 ACR 推送
resource "alicloud_ecs_image" "comfyui_worker" {
  name        = "nexusvideo-${var.env}-comfyui-worker"
  description = "NexusVideo ComfyUI Worker 镜像"

  tags = merge(local.tags, { role = "ecs-image" })
}

# 伸缩组
resource "alicloud_ess_scaling_group" "comfyui_workers" {
  name             = "nexusvideo-${var.env}-ess-comfyui"
  vswitch_ids      = [alicloud_vswitch.gpu.id]
  min_size         = var.ess_min_size        # 1（基线）
  max_size         = var.ess_max_size        # 10（弹性上限）
  cooldown         = var.ess_cooldown
  scaling_rules    = []
  removal_policies = ["OldestInstance", "NewestInstance"]

  load_balancer_ids = [alicloud_slb.main.id]

  tags = merge(local.tags, { role = "ess" })
}

# 伸缩配置：包月基线兜底（1台）
resource "alicloud_ess_scaling_configuration" "baseline" {
  name              = "nexusvideo-${var.env}-ess-baseline"
  scaling_group_id  = alicloud_ess_scaling_group.comfyui_workers.id
  image_id          = var.gpu_image_id
  instance_type     = var.gpu_instance_type
  security_groups   = [alicloud_security_group.comfyui.id]
  internet_charge_type = "PayByTraffic"

  # 系统盘（仅 OS，模型走 NAS 挂载）
  system_disk_category  = "cloud_efficiency"
  system_disk_size      = 40

  # 启动模板：挂载 NAS 到 /models
  user_data = base64encode(<<-EOF
    #!/bin/bash
    # 等待 NAS 挂载点就绪
    sleep 10
    # 创建挂载目录
    mkdir -p /models /output /input
    # 通过 cloud-init 配置 NAS 挂载
    echo "${alicloud_nas_mount_target.gpu_zone.nas_mount_point} /models nfs defaults,nofail 0 0" >> /etc/fstab
    mount -a
    # 启动 ComfyUI Worker
    cd /app && python3 start_worker.py
  EOF
  )

  tags = merge(local.tags, { role = "ess-baseline", pay_type = "Prepaid" })
}

# 伸缩规则：CPU > 70% 扩容
resource "alicloud_ess_scaling_rule" "scale_up" {
  scaling_group_id = alicloud_ess_scaling_group.comfyui_workers.id
  adjustment_type  = "AddCapacity"
  adjustment_value = var.ess_scale_up_delta
  cooldown         = var.ess_cooldown
  name             = "nexusvideo-${var.env}-scale-up"
}

# 伸缩规则：CPU < 30% 缩容
resource "alicloud_ess_scaling_rule" "scale_down" {
  scaling_group_id = alicloud_ess_scaling_group.comfyui_workers.id
  adjustment_type  = "RemoveCapacity"
  adjustment_value = var.ess_scale_down_delta
  cooldown         = var.ess_cooldown
  name             = "nexusvideo-${var.env}-scale-down"
}

# 告警：触发扩容
resource "alicloud_clb_rule" "scale_up_trigger" {
  # 实际通过 CloudMonitor 指标报警关联 ESS 伸缩规则
  # 此处省略 CloudMonitor Alarm 定义
}

# ============================================================
# 抢占式实例配置（在 ESS 中以竞价策略启动）
# 抢占式 A10 成本 ~ 0.12 元/次
# ============================================================
resource "alicloud_ess_scaling_configuration" "spot" {
  name              = "nexusvideo-${var.env}-ess-spot"
  scaling_group_id  = alicloud_ess_scaling_group.comfyui_workers.id
  image_id          = var.gpu_image_id
  instance_type     = var.gpu_spot_instance_type
  security_groups   = [alicloud_security_group.comfyui.id]
  instance_charge_type = "Postpaid"  # 抢占式
  internet_charge_type = "PayByTraffic"

  system_disk_category  = "cloud_efficiency"
  system_disk_size      = 40

  # 抢占式实例中断时优雅排空
  user_data = base64encode(<<-EOF
    #!/bin/bash
    sleep 10
    mkdir -p /models /output /input
    echo "${alicloud_nas_mount_target.gpu_zone.nas_mount_point} /models nfs defaults,nofail 0 0" >> /etc/fstab
    mount -a
    cd /app && python3 start_worker.py --spot-mode
  EOF
  )

  tags = merge(local.tags, { role = "ess-spot", pay_type = "Postpaid-Spot" })
}