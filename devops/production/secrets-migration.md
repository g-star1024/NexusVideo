# NexusVideo 生产环境 Secrets 迁移指南

> **文档版本**：1.0.0
> **作者**：运维架构师 唐磐石
> **更新日期**：2026-08-18
> **适用范围**：Staging → Production secrets 迁移

---

## 目录

1. [概述](#1-概述)
2. [当前 Staging Secrets 清单](#2-当前-staging-secrets-清单)
3. [阿里云 KMS 配置步骤](#3-阿里云-kms-配置步骤)
4. [External Secrets Operator 部署指南](#4-external-secrets-operator-部署指南)
5. [环境变量映射表](#5-环境变量映射表)
6. [迁移检查清单](#6-迁移检查清单)
7. [回滚方案](#7-回滚方案)
8. [附录：安全最佳实践](#8-附录安全最佳实践)

---

## 1. 概述

### 1.1 迁移目标

将 NexusVideo 当前 Staging 环境中硬编码 / 明文存储的敏感信息，全部迁移到**阿里云 KMS 托管密钥** + **External Secrets Operator** 自动化同步方案，实现：

- 密钥不入代码仓库
- 密钥自动轮换能力
- 审计日志可追溯
- 多环境隔离（Staging / Production）

### 1.2 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                      Kubernetes 集群 (ACK)                      │
│                                                                 │
│  ┌──────────────────────┐      ┌────────────────────────────┐  │
│  │ External Secrets     │─────▶│  Secrets / ConfigMaps       │  │
│  │ Operator             │      │  (供应用 Pod 消费)          │  │
│  └──────────┬───────────┘      └────────────────────────────┘  │
│             │                                                   │
└─────────────┼───────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     阿里云 KMS                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ JWT Key  │  │ DB Key   │  │ Redis    │  │ OSS Keys │  ...  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 前置条件

| 前置条件 | 说明 | 状态 |
|---------|------|------|
| 阿里云账号已开通 KMS | 生产账号 | ☐ |
| ACK 集群已创建 | K8s 1.26+ | ☐ |
| External Secrets Operator 可部署 | Helm 3.x | ☐ |
| 现有 secrets 清单已确认 | 见 [第2节](#2-当前-staging-secrets-清单) | ☐ |
| K8s RBAC 权限 | cluster-admin 或 secrets 管理员 | ☐ |

---

## 2. 当前 Staging Secrets 清单

### 2.1 需要替换的 Secrets

> 以下 secret 名称与对应环境变量名需在生产环境通过 KMS + External Secrets 重新生成。

| # | Secret 名称 | 环境变量名 | 用途 | 当前存储方式 | 需要替换？ |
|---|------------|-----------|------|------------|----------|
| 1 | `nexus-jwt-secret` | `NEXUS_JWT_SECRET` | JWT Token 签名密钥 | Docker Compose env_file | ✅ 是 |
| 2 | `nexus-db-password` | `DB_PASSWORD` | PostgreSQL 数据库密码 | Docker Compose env_file | ✅ 是 |
| 3 | `nexus-redis-password` | `REDIS_PASSWORD` | Redis 密码 | Docker Compose env_file | ✅ 是 |
| 4 | `nexus-oss-access-key` | `OSS_ACCESS_KEY` | 阿里云 OSS 访问密钥 | Docker Compose env_file | ✅ 是 |
| 5 | `nexus-oss-secret-key` | `OSS_SECRET_KEY` | 阿里云 OSS 密钥 | Docker Compose env_file | ✅ 是 |
| 6 | `nexus-cloud-api-jwt` | `CLOUD_API_JWT_SECRET` | 云端 API 网关签名密钥 | 硬编码配置 | ✅ 是 |
| 7 | `nexus-comfyui-token` | `COMFYUI_API_TOKEN` | ComfyUI API 鉴权 | 环境变量 | ✅ 是 |
| 8 | `nexus-sms-signature` | `SMS_SIGN_NAME` | 短信签名 | 环境变量 | ✅ 是 |

### 2.2 当前硬编码风险点

```yaml
# ⚠️ staging/docker-compose.env（当前状态，需清除）
NEXUS_JWT_SECRET=super-secret-staging-key-2026
DB_PASSWORD=staging_db_pass_123
REDIS_PASSWORD=staging_redis_pass
OSS_ACCESS_KEY=LTAI5tStagingAccessKeyXXX
OSS_SECRET_KEY=StagingSecretKeyXXX
CLOUD_API_JWT_SECRET=cloud-staging-jwt-key
```

**风险等级：🔴 高危** — 这些密钥如果泄漏，攻击者可以：
- 伪造 JWT Token 获取任意用户身份
- 直接访问数据库
- 操作 OSS 存储桶读取/篡改用户视频数据
- 绕过云端 API 鉴权

---

## 3. 阿里云 KMS 配置步骤

### 3.1 创建 KMS 密钥

#### 步骤 1：登录 KMS 控制台

```bash
# 使用阿里云 CLI 创建密钥（或手动在控制台操作）
# 安装 aliyun CLI 后配置 AccessKey
aliyun configure --profile nexus-prod
```

#### 步骤 2：创建用户托管密钥（CMK）

```bash
# 为每个 secret 创建独立 CMK（最小权限原则）
# JWT 签名密钥
aliyun kms CreateKey \
  --KeyUsage ENCRYPT_DECRYPT \
  --KeyMaterialOrigin ALI_CLOUD_KMSManagedKeyMaterial \
  --Description "NexusVideo-JWT-Secret" \
  --RegionId cn-hangzhou

# 输出示例：
# {
#   "KeyId": "c2b1a2c3-4d5e-6f7g-8h9i-0j1k2l3m4n5o",
#   "KeyStatus": "Enabled"
# }

# 记录每个 KeyId：
#   JWT_KEY_ID=c2b1a2c3-4d5e-6f7g-8h9i-0j1k2l3m4n5o
#   DB_KEY_ID=...
#   REDIS_KEY_ID=...
#   OSS_ACCESS_KEY_ID=...
#   OSS_SECRET_KEY_ID=...
#   CLOUD_API_KEY_ID=...
```

#### 步骤 3：设置密钥自动轮换

```bash
# 启用自动轮换（90天）
aliyun kms RotateKeyOnCreation \
  --KeyId $JWT_KEY_ID \
  --RegionId cn-hangzhou
```

#### 步骤 4：创建密钥别名（便于引用）

```bash
aliyun kms AliasList
aliyun kms CreateAlias \
  --AliasName alias/nexus/jwt-secret \
  --TargetKeyId $JWT_KEY_ID

aliyun kms CreateAlias \
  --AliasName alias/nexus/db-password \
  --TargetKeyId $DB_KEY_ID

# ... 同理为其他密钥创建别名
```

### 3.2 授权 RAM 用户 / 角色

```bash
# 为 K8s 节点角色创建 KMS 授权策略
# 1. 创建自定义策略
cat > kms-policy.json << 'POLICY'
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:GenerateDataKeyWithoutPlaintext"
      ],
      "Resource": "acs:kms:cn-hangzhou:*:key/*",
      "Condition": {}
    }
  ]
}
POLICY

aliyun ram CreatePolicy \
  --PolicyName "NexusVideo-KMS-Access" \
  --PolicyDocument file://kms-policy.json

# 2. 将策略附加到 ACK 集群的节点 RAM 角色
aliyun ram AttachRolePolicy \
  --RoleName "ack-node-role" \
  --PolicyName "NexusVideo-KMS-Access" \
  --PolicyType "Custom"

# 3. 验证权限
aliyun ram ListPoliciesForRole --RoleName ack-node-role
```

---

## 4. External Secrets Operator 部署指南

### 4.1 部署 External Secrets Operator

```bash
# 使用 Helm 安装
helm repo add external-secrets https://charts.external-secrets.io
helm repo update

# 安装到 kube-system 命名空间
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets \
  --create-namespace \
  --set installCRDs=true \
  --set operator.rbac.enabled=true \
  --set operator.logLevel=info

# 验证安装
kubectl get pods -n external-secrets
kubectl get crds | grep external-secrets
```

### 4.2 创建 Cluster Secret Store（阿里云 KMS 后端）

> External Secrets Operator 通过阿里云 Secret Manager (SSM/Secrets Manager) 或 KMS 的 API 拉取密钥。我们使用阿里云 Secrets Manager 作为中间层（KMS 加密存储 + Secrets Manager 提供 API 接口）。

#### 方案 A：使用阿里云 Secrets Manager（推荐）

```yaml
# cluster-secret-store.yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: aliyun-kms-store
spec:
  provider:
    alibaba:
      service: SecretsManager  # 使用 Secrets Manager 作为 API 入口
      region: cn-hangzhou
      auth:
        secretRef:
          accessKeyIDSecretRef:
            name: aliyun-credentials
            key: access-key-id
          accessKeySecretSecretRef:
            name: aliyun-credentials
            key: access-key-secret
```

```bash
kubectl apply -f cluster-secret-store.yaml
kubectl get clustersecretstore aliyun-kms-store -o yaml
```

#### 方案 B：直接 KMS（如需直连）

```yaml
# cluster-secret-store-direct-kms.yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: aliyun-kms-direct
spec:
  provider:
    alicloudkms:
      region: cn-hangzhou
      auth:
        secretRef:
          accessKeyIDSecretRef:
            name: aliyun-credentials
            key: access-key-id
          accessKeySecretSecretRef:
            name: aliyun-credentials
            key: access-key-secret
```

### 4.3 在阿里云 Secrets Manager 注册密钥

> 由于 KMS CMK 需要通过 Secrets Manager 提供 API，先在 Secrets Manager 中创建对应用户密钥。

```bash
# 为每个 secret 在 Secrets Manager 中注册
# 1. JWT Secret
JWT_SECRET_VALUE=$(openssl rand -hex 32)
aliyun secretsmanager CreateSecret \
  --SecretName "nexus/production/jwt-secret" \
  --Description "NexusVideo JWT Signing Secret" \
  --SecretValue "$JWT_SECRET_VALUE" \
  --RotationEnabled true \
  --RotationDays 90 \
  --RegionId cn-hangzhou

# 2. DB Password
DB_PASS_VALUE=$(openssl rand -base64 32)
aliyun secretsmanager CreateSecret \
  --SecretName "nexus/production/db-password" \
  --Description "PostgreSQL Database Password" \
  --SecretValue "$DB_PASS_VALUE" \
  --RegionId cn-hangzhou

# 3-8: 同理创建其他 secrets
#   nexus/production/redis-password
#   nexus/production/oss-access-key
#   nexus/production/oss-secret-key
#   nexus/production/cloud-api-jwt-secret
#   nexus/production/comfyui-api-token
#   nexus/production/sms-sign-name
```

### 4.4 创建 ExternalSecret 对象

```yaml
# external-secrets.yaml
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: nexus-jwt-secret
  namespace: nexusvideo
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aliyun-kms-store
    kind: ClusterSecretStore
  target:
    name: nexus-secrets              # K8s Secret 名称
    creationPolicy: Owner
    template:
      metadata:
        labels:
          app: nexusvideo
          managed-by: external-secrets
      data:
        NEXUS_JWT_SECRET: "{{ .jwt-secret }}"
  data:
    - secretKey: jwt-secret
      remoteRef:
        key: nexus/production/jwt-secret
        property: secret-value
---
# 其余 ExternalSecret 对象同理...
# 每个对应一个阿里云 Secret 名称
```

```bash
kubectl apply -f external-secrets.yaml
kubectl get externalsecret -n nexusvideo
kubectl get secret nexus-secrets -n nexusvideo -o jsonpath='{.data}'
```

---

## 5. 环境变量映射表

### 5.1 完整映射

| K8s Secret Key | 环境变量名 | 阿里云 Secret 名称 | KMS 密钥别名 | 自动轮换 |
|---------------|-----------|------------------|-------------|---------|
| `jwt-secret` | `NEXUS_JWT_SECRET` | `nexus/production/jwt-secret` | `alias/nexus/jwt-secret` | 90天 |
| `db-password` | `DB_PASSWORD` | `nexus/production/db-password` | `alias/nexus/db-password` | 90天 |
| `redis-password` | `REDIS_PASSWORD` | `nexus/production/redis-password` | `alias/nexus/redis-password` | 90天 |
| `oss-access-key` | `OSS_ACCESS_KEY` | `nexus/production/oss-access-key` | `alias/nexus/oss-access-key` | 90天 |
| `oss-secret-key` | `OSS_SECRET_KEY` | `nexus/production/oss-secret-key` | `alias/nexus/oss-secret-key` | 90天 |
| `cloud-api-jwt-secret` | `CLOUD_API_JWT_SECRET` | `nexus/production/cloud-api-jwt-secret` | `alias/nexus/cloud-api-jwt-secret` | 90天 |
| `comfyui-api-token` | `COMFYUI_API_TOKEN` | `nexus/production/comfyui-api-token` | `alias/nexus/comfyui-api-token` | 90天 |
| `sms-sign-name` | `SMS_SIGN_NAME` | `nexus/production/sms-sign-name` | `alias/nexus/sms-sign-name` | 180天 |

### 5.2 Deployment 中使用

```yaml
# deployment.yaml (片段)
envFrom:
  - secretRef:
      name: nexus-secrets       # ExternalSecret 自动同步的 K8s Secret
```

---

## 6. 迁移检查清单

### 6.1 迁移前准备

- [ ] ☐ 确认阿里云账号开通 KMS + Secrets Manager
- [ ] ☐ 确认 ACK 集群正常，kubectl 可连接
- [ ] ☐ ☐ 备份当前 staging secrets（导出到安全的离线存储）
- [ ] ☐ 通知团队维护窗口（建议：02:00-04:00 UTC+8）
- [ ] ☐ 在 GitHub 创建 release branch `chore/secrets-migration-v1`
- [ ] ☐ 确保 staging 环境与生产环境隔离（不同 KMS Key、不同 RAM 角色）

### 6.2 KMS 密钥创建

- [ ] ☐ 创建所有 8 个 CMK 密钥（JWT / DB / Redis / OSS×2 / Cloud API / ComfyUI / SMS）
  - 验证命令：
    ```bash
    aliyun kms ListKeys --RegionId cn-hangzhou --Limit 20
    ```
- [ ] ☐ 为每个密钥设置别名（alias/nexus/xxx）
  - 验证命令：
    ```bash
    aliyun kms GetKeyByAlias --AliasName alias/nexus/jwt-secret
    ```
- [ ] ☐ 启用自动轮换
  - 验证命令：
    ```bash
    aliyun kms DescribeKey --KeyId <KeyId> | jq '.KeyMetadata.KeyRotationStatus'
    ```
- [ ] ☐ 配置 RAM 授权策略
  - 验证命令：
    ```bash
    aliyun ram ListPoliciesForRole --RoleName ack-node-role
    ```

### 6.3 Secrets Manager 注册

- [ ] ☐ 在 Secrets Manager 注册 8 个密钥
  - 验证命令：
    ```bash
    aliyun secretsmanager DescribeSecret --SecretName nexus/production/jwt-secret
    ```
- [ ] ☐ 验证密钥值可读取
  - 验证命令：
    ```bash
    aliyun secretsmanager GetSecretValue --SecretName nexus/production/jwt-secret | jq '.SecretString'
    ```

### 6.4 External Secrets Operator 部署

- [ ] ☐ 安装 External Secrets Operator
  - 验证命令：
    ```bash
    kubectl get pods -n external-secrets
    kubectl get crds externalsecrets.external-secrets.io
    ```
- [ ] ☐ 创建 ClusterSecretStore
  - 验证命令：
    ```bash
    kubectl get clustersecretstore aliyun-kms-store
    kubectl describe clustersecretstore aliyun-kms-store
    ```
- [ ] ☐ 创建 ExternalSecret 对象（8 个）
  - 验证命令：
    ```bash
    kubectl get externalsecret -n nexusvideo
    kubectl get secret nexus-secrets -n nexusvideo
    kubectl describe externalsecret -n nexusvideo  # 检查 Synced 状态
    ```

### 6.5 应用切换

- [ ] ☐ 更新 Deployment envFrom 指向新的 Secret
  - 验证命令：
    ```bash
    kubectl rollout status deployment/nexusvideo-backend -n nexusvideo
    kubectl logs deployment/nexusvideo-backend -n nexusvideo --tail=50
    ```
- [ ] ☐ 验证 JWT 登录功能正常
  - 验证命令：
    ```bash
    # 发送登录请求，验证返回 Token
    curl -s -X POST https://api.nexusvideo.com/auth/login \
      -H "Content-Type: application/json" \
      -d '{"phone":"测试手机号","code":"123456"}' | jq '.token'
    ```
- [ ] ☐ 验证数据库连接正常
  - 验证命令：
    ```bash
    kubectl exec -n nexusvideo deployment/nexusvideo-backend -- \
      python -c "from app.db import db; print(db.engine.connect().exec('SELECT 1'))"
    ```
- [ ] ☐ 验证 Redis 连接正常
  - 验证命令：
    ```bash
    kubectl exec -n nexusvideo deployment/nexusvideo-backend -- \
      redis-cli -a "$(echo $REDIS_PASSWORD)" ping
    ```
- [ ] ☐ 验证 OSS 上传/下载正常
  - 验证命令：
    ```bash
    kubectl exec -n nexusvideo deployment/nexusvideo-backend -- \
      python -c "from app.storage import oss; print(oss.bucket.listdir('test/'))"
    ```
- [ ] ☐ 验证全链路 E2E 测试通过
  - 验证命令：
    ```bash
    npm run test:e2e -- --grep "auth|upload|video-generation"
    ```

### 6.6 清理

- [ ] ☐ 从代码仓库删除旧 env_file
  ```bash
  git rm staging/.env staging/docker-compose.env 2>/dev/null
  git commit -m "chore: remove plaintext secrets after KMS migration"
  ```
- [ ] ☐ 确认 .gitignore 已包含 .env 文件
  ```bash
  echo ".env" >> .gitignore
  echo ".env.*" >> .gitignore
  echo "docker-compose.env" >> .gitignore
  ```
- [ ] ☐ 轮换所有旧密钥（即使已不再使用）
- [ ] ☐ 通知团队迁移完成

---

## 7. 回滚方案

### 7.1 快速回滚（5 分钟）

如果迁移后应用异常，执行以下步骤恢复到旧版 secrets：

```bash
# Step 1: 恢复 Deployment 配置到旧版
kubectl rollout undo deployment/nexusvideo-backend -n nexusvideo

# Step 2: 重新挂载旧版 Secret（已备份到离线存储）
# 从离线备份恢复
kubectl apply -f /backup/nexusvideo-staging-secrets-backup.yaml

# Step 3: 验证回滚成功
kubectl rollout status deployment/nexusvideo-backend -n nexusvideo
kubectl logs deployment/nexusvideo-backend -n nexusvideo --tail=20

# Step 4: 从代码仓库恢复旧版 env_file（如果需要）
git checkout chore/secrets-migration-v1 -- .  # 撤销迁移分支改动
```

### 7.2 分阶段回滚

```
阶段 1（15分钟）: kubectl rollout undo → 恢复旧版 Deployment
阶段 2（30分钟）: 重新创建旧版 K8s Secret → 挂载
阶段 3（60分钟）: E2E 验证 → 确认业务恢复
阶段 4（随时）  : 修复问题后重新执行迁移
```

### 7.3 回滚触发条件

| 条件 | 动作 |
|------|------|
| JWT 登录失败 | 立即回滚 |
| 数据库连接超时 | 立即回滚 |
| OSS 操作失败 | 评估影响后决定 |
| E2E 测试失败率 > 5% | 回滚 |
| 告警阈值触发（错误率 > 1%） | 回滚 |

---

## 8. 附录：安全最佳实践

### 8.1 密钥管理原则

1. **最小权限**：每个 KMS Key 只授权给需要的服务
2. **自动轮换**：敏感密钥 90 天自动轮换
3. **审计日志**：所有 KMS 操作记录到 SLS（日志服务）
4. **网络隔离**：Secrets Manager API 接入使用私网地址
5. **密钥分级**：
   - 🔴 P0 密钥（JWT / DB）：KMS CMK + 手动轮换
   - 🟡 P1 密钥（Redis / OSS）：Secrets Manager + 90天轮换
   - 🟢 P2 密钥（SMS）：Secrets Manager + 180天轮换

### 8.2 监控告警

```yaml
# Prometheus 告警规则（添加到 devops/monitoring/alerts.yaml）
- alert: ExternalSecretSyncFailed
  expr: externalsecrets_synced == 0
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "ExternalSecret 同步失败: {{ $labels.name }}"

- alert: KMSKeyRotationPending
  expr: aliyun_kms_key_rotation_enabled == 0
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "KMS 密钥未启用自动轮换"
```

### 8.3 定期演练

- **每月**：检查 Secrets Manager 轮换状态
- **每季度**：执行一次完整的回滚演练
- **每半年**：审计所有 KMS 密钥授权策略