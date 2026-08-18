# NexusVideo Terraform 变量定义
# 所有资源命名遵循 nexusvideo-{env}-{type} 规范

# ============================================================
# 全局变量
# ============================================================
variable "region" {
  description = "阿里云地域，推荐使用华南1（深圳）或华东2（上海）"
  type        = string
  default     = "cn-shenzhen"

  validation {
    condition     = can(regex("^(cn-|ap-|eu-|us-)[a-z]+-[0-9]$", var.region))
    error_message = "region 必须为阿里云标准 region 格式。"
  }
}

variable "env" {
  description = "环境标识：staging / production"
  type        = string
  default     = "staging"

  validation {
    condition     = var.env == "staging" || var.env == "production"
    error_message = "env 必须为 staging 或 production。"
  }
}

# ============================================================
# VPC / 子网
# ============================================================
variable "vpc_cidr" {
  description = "VPC CIDR 网段"
  type        = string
  default     = "10.0.0.0/16"
}

variable "gpu_zone" {
  description = "GPU 子网所在可用区"
  type        = string
  default     = "cn-shenzhen-d"
}

variable "general_zone" {
  description = "常规子网所在可用区（Redis / RDS / Nginx）"
  type        = string
  default     = "cn-shenzhen-a"
}

variable "gpu_subnet_cidr" {
  description = "GPU 子网 CIDR"
  type        = string
  default     = "10.0.1.0/24"
}

variable "general_subnet_cidr" {
  description = "常规子网 CIDR"
  type        = string
  default     = "10.0.2.0/24"
}

# ============================================================
# NAS 共享存储
# ============================================================
variable "nas_size_gb" {
  description = "NAS 文件系统容量（GB），容量型存储"
  type        = number
  default     = 100

  validation {
    condition     = var.nas_size_gb >= 50 && var.nas_size_gb <= 10000
    error_message = "NAS 容量范围：50 ~ 10000 GB。"
  }
}

# ============================================================
# OSS 对象存储
# ============================================================
variable "oss_bucket_suffix" {
  description = "OSS 存储桶名称后缀（前缀固定为 nexusvideo-{env}-）"
  type        = string
  default     = "outputs"
}

# ============================================================
# GPU 实例规格
# ============================================================
variable "gpu_instance_type" {
  description = "包月基线 GPU 实例规格（A10 兜底），如 gn5i-c16g1.4xlarge"
  type        = string
  default     = "gn5i-c16g1.4xlarge"
}

variable "gpu_spot_instance_type" {
  description = "抢占式 GPU 实例规格（A10 主力），如 gfn7-c16g1.4xlarge"
  type        = string
  default     = "gfn7-c16g1.4xlarge"
}

variable "gpu_image_id" {
  description = "自定义 ComfyUI Worker 镜像 ID（从 ACR 推送后的镜像）"
  type        = string
  default     = "m-cn-shenzhen-xxxxxxxxx"
}

# ============================================================
# ESS 弹性伸缩
# ============================================================
variable "ess_min_size" {
  description = "ESS 最小实例数（包月基线兜底）"
  type        = number
  default     = 1
}

variable "ess_max_size" {
  description = "ESS 最大实例数（弹性上限，包含基线 + 抢占式）"
  type        = number
  default     = 10
}

variable "ess_cooldown" {
  description = "伸缩冷却时间（秒），防止频繁扩缩"
  type        = number
  default     = 300
}

variable "ess_scale_up_delta" {
  description = "扩容时新增实例数"
  type        = number
  default     = 2
}

variable "ess_scale_down_delta" {
  description = "缩容时移除实例数"
  type        = number
  default     = 1
}

# ============================================================
# RDS MySQL
# ============================================================
variable "rds_instance_type" {
  description = "RDS 实例规格"
  type        = string
  default     = "rds.mysql.t2.small"  # 1C2G 入门级，低成本
}

variable "rds_storage_gb" {
  description = "RDS 存储空间（GB）"
  type        = number
  default     = 20
}

variable "db_password" {
  description = "RDS 应用账户密码（建议使用 Secrets Manager 管理）"
  type        = string
  sensitive   = true
  default     = "ChangeMe_NexusVideo_2024!"
}

# ============================================================
# Redis
# ============================================================
variable "redis_instance_type" {
  description = "Redis 实例规格，如 redis.basic.2c.xd"
  type        = string
  default     = "redis.basic.2c.xd"
}

variable "redis_capacity_gb" {
  description = "Redis 容量（GB）"
  type        = number
  default     = 1
}

# ============================================================
# SLB
# ============================================================
variable "slb_bandwidth_mbps" {
  description = "SLB 公网带宽上限（Mbps）"
  type        = number
  default     = 100
}

# ============================================================
# 运维安全
# ============================================================
variable "admin_cidr" {
  description = "允许访问 Grafana 的运维 IP 段（CIDR）"
  type        = string
  default     = "0.0.0.0/0"
}

# ============================================================
# 本地标签（所有资源统一注入）
# ============================================================
locals {
  tags = {
    Project   = "NexusVideo"
    Environment = var.env
    ManagedBy = "Terraform"
    Team      = "DevOps"
  }
}