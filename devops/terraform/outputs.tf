# NexusVideo Terraform 输出变量

output "vpc_id" {
  description = "VPC 实例 ID"
  value       = alicloud_vpc.main.id
}

output "vpc_cidr" {
  description = "VPC CIDR"
  value       = var.vpc_cidr
}

output "gpu_vswitch_id" {
  description = "GPU 子网 ID（用于部署 GPU Worker）"
  value       = alicloud_vswitch.gpu.id
}

output "general_vswitch_id" {
  description = "常规子网 ID（用于 Redis / RDS / Nginx）"
  value       = alicloud_vswitch.general.id
}

output "slb_ip" {
  description = "SLB 公网 IP（Nginx / FastAPI 对外入口）"
  value       = alicloud_slb.main.address
}

output "slb_listener_port_https" {
  description = "HTTPS 监听端口"
  value       = 443
}

output "slb_listener_port_http" {
  description = "HTTP 监听端口"
  value       = 80
}

output "nas_mount_point" {
  description = "NAS 挂载点地址（用于 Worker 挂载 /models）"
  value       = alicloud_nas_mount_target.gpu_zone.nas_mount_point
}

output "nas_file_system_id" {
  description = "NAS 文件系统 ID"
  value       = alicloud_nas_file_system.main.id
}

output "oss_bucket_name" {
  description = "OSS 存储桶名称（生成结果持久化）"
  value       = alicloud_oss_bucket.outputs.bucket
}

output "rds_endpoint" {
  description = "RDS MySQL 内网地址"
  value       = alicloud_rds_instance.main.inner_address
}

output "rds_port" {
  description = "RDS MySQL 端口"
  value       = alicloud_rds_instance.main.port
}

output "rds_account" {
  description = "RDS 应用账户"
  value       = alicloud_rds_account.app.account_name
}

output "redis_endpoint" {
  description = "Redis 内网地址"
  value       = alicloud_redis_instance.main.internal_endpoint
}

output "redis_port" {
  description = "Redis 端口"
  value       = alicloud_redis_instance.main.port
}

output "ess_scaling_group_id" {
  description = "ESS 伸缩组 ID（用于 ESS Hook / API 调用）"
  value       = alicloud_ess_scaling_group.comfyui_workers.id
}

output "ess_scaling_group_name" {
  description = "ESS 伸缩组名称"
  value       = alicloud_ess_scaling_group.comfyui_workers.name
}

output "comfyui_security_group_id" {
  description = "ComfyUI Worker 安全组 ID"
  value       = alicloud_security_group.comfyui.id
}

output "api_security_group_id" {
  description = "API 安全组 ID"
  value       = alicloud_security_group.api.id
}

output "monitoring_security_group_id" {
  description = "监控安全组 ID"
  value       = alicloud_security_group.monitoring.id
}

output "region" {
  description = "部署区域"
  value       = var.region
}

output "env" {
  description = "环境标识"
  value       = var.env
}