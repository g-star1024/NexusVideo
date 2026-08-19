# NexusVideo 鉴权安全配置

> 作者：唐磐石（运维架构师）
> 版本：v1.0 | 更新日期：2026-08-18

---

## 一、JWT 鉴权配置

### 1.1 Token 体系设计

```
┌────────────────────────────────────────────────────────────────┐
│                      NexusVideo 鉴权流程                         │
│                                                                 │
│  手机号 + 验证码           JWT Access Token                    │
│  ──────────────────────→   ─────────────────                  │
│          │                    │ (15 分钟有效)                   │
│          ▼                    ▼                                 │
│  ┌──────────────┐     ┌──────────────────┐                    │
│  │ 短信网关     │     │ FastAPI          │                    │
│  │ 验证码验证   │     │ JWT 验证         │                    │
│  └──────────────┘     └──────────────────┘                    │
│          │                    │                                 │
│          ▼                    ▼                                 │
│  ┌──────────────┐     ┌──────────────────┐                    │
│  │ 返回 Token   │     │ 业务逻辑处理     │                    │
│  │ (Access +   │     │                    │                    │
│  │  Refresh)   │     └──────────────────┘                    │
│  └──────────────┘                    │                        │
│                                       ▼                        │
│                              ┌──────────────────┐              │
│                              │ 返回 JWT         │              │
│                              │ 给客户端         │              │
│                              └──────────────────┘              │
│                                                                 │
│  ┌──────────────────────────────────────────────┐              │
│  │           Access Token 过期后                  │              │
│  │                                              │              │
│  │  Refresh Token (7天有效)                     │              │
│  │  ─────────────────────────────────→          │              │
│  │  客户端提交 Refresh Token                     │              │
│  │       ↓                                      │              │
│  │  服务端验证 → 签发新的 Access Token           │              │
│  │  (Refresh Token 可轮换)                       │              │
│  └──────────────────────────────────────────────┘              │
└────────────────────────────────────────────────────────────────┘
```

### 1.2 JWT 配置参数

```yaml
# 写入 FastAPI 配置（.env 或配置中心）
# ============================================================
# JWT 鉴权配置
# ============================================================

# Access Token（短期令牌）
JWT_ALGORITHM: HS256                    # 加密算法
JWT_ACCESS_TOKEN_EXPIRE_MINUTES: 15    # 有效期 15 分钟
JWT_SECRET_KEY: "${JWT_SECRET_KEY}"    # 从环境变量读取，至少 32 字节

# Refresh Token（长期令牌）
JWT_REFRESH_TOKEN_EXPIRE_DAYS: 7       # 有效期 7 天
JWT_REFRESH_TOKEN_NAME: refresh_token  # Cookie 名称

# Token 来源
JWT_TOKEN_URL: /api/auth/login         # 获取 Token 端点
JWT_REFRESH_URL: /api/auth/refresh     # 刷新 Token 端点

# Token 存储
JWT_COOKIE_SECURE: true                # 仅 HTTPS 传输
JWT_COOKIE_HTTPONLY: true              # 禁止 JS 访问
JWT_COOKIE_SAMESITE: Lax               # CSRF 保护

# 签名密钥轮换
JWT_SECRET_KEY_PREV: "${JWT_SECRET_KEY_PREV}"  # 上一版密钥（用于验证旧 token）
```

### 1.3 JWT 签发流程（Python 代码参考）

```python
# backend/auth/jwt_helper.py（供后端团队参考）
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext

SECRET_KEY = os.environ["JWT_SECRET_KEY"]
ALGORITHM = "HS256"
ACCESS_EXPIRE_MINUTES = 15
REFRESH_EXPIRE_DAYS = 7

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(user_id: str, phone: str) -> str:
    """签发 Access Token"""
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "phone": phone,
        "type": "access",
        "exp": expire,
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    """签发 Refresh Token"""
    expire = datetime.utcnow() + timedelta(days=REFRESH_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.utcnow(),
        "jti": str(uuid.uuid4()),  # 唯一 ID，用于吊销
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    """验证 Token 并返回 payload"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
```

### 1.4 手机号登录流程

```
客户端                         服务端
  │                              │
  │  POST /api/auth/send-code    │
  │  {"phone": "138xxxx"}       │
  │─────────────────────────────→│
  │                              │──→ 短信网关发送验证码
  │                              │──→ Redis 存储验证码（TTL 5min）
  │  {"status": "ok"}            │
  │←─────────────────────────────│
  │                              │
  │  POST /api/auth/login        │
  │  {"phone": "138xxxx",        │
  │   "code": "123456"}         │
  │─────────────────────────────→│
  │                              │──→ 从 Redis 读取验证码比对
  │                              │──→ 比对成功 → 查询/创建用户
  │                              │──→ 签发 Access + Refresh Token
  │                              │──→ 设置 Refresh Token Cookie
  │                              │
  │  {"access_token": "...",     │
  │   "token_type": "bearer",    │
  │   "expires_in": 900,        │
  │   "user": {...}}            │
  │  Set-Cookie: refresh_token= │
  │    xxx; HttpOnly; Secure    │
  │←─────────────────────────────│
```

### 1.5 Token 刷新与吊销

```python
# Refresh Token 轮换策略：
# 每次使用 Refresh Token 获取新 Access Token 时，同时签发新的 Refresh Token
# 旧 Refresh Token 立即作废（防止重放攻击）

def refresh_access_token(refresh_token: str) -> dict:
    """使用 Refresh Token 换取新的 Access Token"""
    payload = verify_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="无效的 Refresh Token")

    # 检查 Refresh Token 是否已被吊销
    jti = payload.get("jti")
    if is_token_revoked(jti):
        raise HTTPException(status_code=401, detail="Token 已吊销")

    # 吊销旧 Refresh Token
    revoke_token(jti)

    # 签发新 Token 对
    user_id = payload["sub"]
    new_access = create_access_token(user_id, phone=lookup_phone(user_id))
    new_refresh = create_refresh_token(user_id)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
        "expires_in": ACCESS_EXPIRE_MINUTES * 60,
    }
```

---

## 二、CORS 白名单配置

### 2.1 CORS 配置

```python
# backend/main.py CORS 配置
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.nexusvideo.com",       # 主站
        "https://admin.nexusvideo.com",     # 管理后台
        "https://localhost:1420",           # Tauri 本地开发
        "https://localhost:5173",           # Vite 开发服务器
    ],
    allow_credentials=True,                 # 允许携带 Cookie（Refresh Token）
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Requested-With",
        "X-User-ID",
    ],
    max_age=600,                            # 预检请求缓存 10 分钟
)
```

### 2.2 Nginx 层 CORS 头（备用）

```nginx
# 在 Nginx 中设置 CORS 头（作为 FastAPI CORS 的兜底）
location /api/ {
    # 允许的来源
    if ($http_origin ~* "^https://(app|admin)\.nexusvideo\.com$") {
        add_header Access-Control-Allow-Origin $http_origin always;
        add_header Access-Control-Allow-Credentials "true" always;
        add_header Access-Control-Allow-Methods "GET, POST, PUT, PATCH, DELETE" always;
        add_header Access-Control-Allow-Headers "Authorization, Content-Type, X-Requested-With, X-User-ID" always;
    }

    # 预检请求
    if ($request_method = OPTIONS) {
        add_header Access-Control-Max-Age 600;
        add_header Content-Length 0;
        return 204;
    }

    proxy_pass http://fastapi;
}
```

---

## 三、API 限流策略

### 3.1 限流规则矩阵

| 接口类型 | 限流规则 | 说明 |
|---------|---------|------|
| 所有 API（按 IP） | 30 req/min | 防止 CC 攻击 |
| 所有 API（按用户） | 100 req/min | 防止恶意用户 |
| 登录接口 | 5 req/min per IP | 防止暴力破解 |
| 短信验证码 | 1 req/min per phone, 10 req/hour per phone | 防止短信轰炸 |
| 视频生成接口 | 10 req/hour per user (免费), 100 req/hour (付费) | 资源保护 |
| 文件上传 | 3 req/min per user, 500MB max size | 防止大文件滥用 |

### 3.2 FastAPI 限流实现

```python
# backend/middleware/rate_limiter.py
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse

# 初始化限流器
limiter = Limiter(
    key_func=get_remote_address,
    strategy="fixed-window",
    storage_uri="redis://redis:6379/1",  # 使用 Redis 存储计数
)

@limiter.limit("30/minute")
async def api_rate_limit(request: Request):
    """通用 API 限流"""
    pass

@limiter.limit("5/minute")
async def login_rate_limit(request: Request):
    """登录接口限流"""
    pass

@limiter.limit("1/minute")
async def sms_rate_limit(request: Request):
    """短信验证码限流"""
    pass

@limiter.limit("10/hour")
async def video_gen_rate_limit(request: Request):
    """视频生成限流"""
    pass

# 限流异常处理
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "detail": "请求过于频繁，请稍后再试",
            "retry_after": int(exc.retry_after) if exc.retry_after else 60,
        },
        headers={
            "Retry-After": str(int(exc.retry_after)) if exc.retry_after else "60",
        },
    )
```

### 3.3 Nginx 层限流（第一道防线）

```nginx
# 在 nginx.conf 中定义限流区域
limit_req_zone $binary_remote_addr zone=api:10m rate=30r/m;
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=sms:10m rate=1r/m;

# 登录接口限流
location /api/auth/login {
    limit_req zone=login burst=3 nodelay;
    proxy_pass http://fastapi;
}

# 短信验证码限流
location /api/auth/send-code {
    limit_req zone=sms burst=1 nodelay;
    proxy_pass http://fastapi;
}

# 通用 API 限流
location /api/ {
    limit_req zone=api burst=10 nodelay;
    proxy_pass http://fastapi;
}
```

---

## 四、敏感操作审计日志

### 4.1 审计日志记录范围

| 操作类型 | 接口 | 记录内容 |
|---------|------|---------|
| 用户登录 | POST /api/auth/login | 手机号（脱敏）、IP、User-Agent、时间、结果 |
| 用户注册 | POST /api/auth/register | 手机号（脱敏）、IP、时间 |
| 密码修改 | PUT /api/users/password | 用户 ID、IP、时间 |
| 视频删除 | DELETE /api/videos/{id} | 用户 ID、视频 ID、IP、时间 |
| 用量查询 | GET /api/users/usage | 用户 ID、IP、时间 |
| 管理员操作 | /api/admin/* | 管理员 ID、操作类型、目标资源、IP、时间 |
| 配置变更 | /api/admin/config | 变更前后值、操作者、时间 |

### 4.2 审计日志实现

```python
# backend/middleware/audit_logger.py
import json
import logging
from datetime import datetime
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

audit_logger = logging.getLogger("audit")

SENSITIVE_PATHS = [
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/send-code",
    "/api/users/password",
    "/api/videos/{id}",
    "/api/admin/",
]

class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # 仅记录敏感操作
        path = request.url.path
        if not any(path.startswith(p.rstrip("{")) or path == p.replace("{id}", "<id>") for p in SENSITIVE_PATHS):
            return response

        # 记录审计日志
        audit_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "method": request.method,
            "path": path,
            "status_code": response.status_code,
            "user_id": getattr(request.state, "user_id", None),
            "ip": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent", ""),
            "request_time_ms": int(getattr(response, "elapsed_ms", 0)),
        }

        audit_logger.info(json.dumps(audit_record))
        return response

app.add_middleware(AuditLogMiddleware)
```

### 4.3 审计日志存储

```yaml
# 审计日志使用独立 Logstash 管道
# deploy/monitoring/logstash-audit.conf

input {
  beats {
    port => 5044
    type => "audit"
  }
}

filter {
  json {
    source => "message"
  }
  date {
    match => ["timestamp", "ISO8601"]
    target => "@timestamp"
  }
}

output {
  # Elasticsearch 存储
  elasticsearch {
    hosts => ["http://elasticsearch:9200"]
    index => "nexusvideo-audit-%{+YYYY.MM.dd}"
  }

  # 同步到 OSS（长期归档）
  file {
    path => "/var/log/audit/nexusvideo-audit-%{+YYYY-MM-dd}.jsonl"
    codec => json_lines
  }
}
```

### 4.4 审计日志保留策略

| 存储方式 | 保留周期 | 用途 |
|---------|---------|------|
| Elasticsearch | 90 天 | 实时查询与分析 |
| OSS 归档 | 1 年 | 合规审计与取证 |
| 敏感操作独立表 | 永久 | 安全事件追溯 |

---

## 五、安全加固清单

### 5.1 传输安全

| 项目 | 配置 | 状态 |
|------|------|------|
| HTTPS 强制跳转 | Nginx 301 → HTTPS | ✅ 已配置 |
| TLS 版本 | TLS 1.2+ | ✅ 已配置 |
| HSTS | max-age=31536000 | ✅ 已配置 |
| OCSP Stapling | 启用 | ⬜ 待配置 |

### 5.2 应用安全

| 项目 | 配置 | 状态 |
|------|------|------|
| CORS 白名单 | 仅允许 app/admin 域名 | ✅ 已配置 |
| API 限流 | 30 req/min per IP | ✅ 已配置 |
| JWT 过期 | 15min Access / 7d Refresh | ✅ 已配置 |
| 敏感信息脱敏 | 手机号、密码不记录日志 | ✅ 已配置 |
| SQL 注入防护 | 使用 ORM / 参数化查询 | ✅ 后端团队 |
| XSS 防护 | Nginx 安全头 + 前端框架 | ✅ 已配置 |
| CSRF 防护 | SameSite Cookie + Token | ✅ 已配置 |

### 5.3 基础设施安全

| 项目 | 配置 | 状态 |
|------|------|------|
| SSH 密钥登录 | 禁用密码登录 | ⬜ 待配置 |
| 防火墙规则 | 仅开放 80/443/3000 | ⬜ 待配置 |
| 数据库公网暴露 | PostgreSQL 仅内网访问 | ✅ 已配置 |
| Redis 公网暴露 | Redis 仅内网访问 | ✅ 已配置 |
| 日志访问控制 | 限制日志文件权限 | ⬜ 待配置 |
| 定期安全扫描 | 每周 Trivy 容器扫描 | ⬜ 待配置 |

---

## 六、密钥管理

### 6.1 密钥存储方案

| 密钥类型 | 存储方式 | 轮换周期 |
|---------|---------|---------|
| JWT_SECRET_KEY | 环境变量 / 云平台密钥管理服务 | 90 天 |
| POSTGRES_PASSWORD | 环境变量 / 云平台密钥管理服务 | 90 天 |
| SMS_API_KEY | 云平台密钥管理服务 | 180 天 |
| OSS_ACCESS_KEY | 云平台密钥管理服务 | 180 天 |
| SSL 私钥 | 本地文件，600 权限 | 按证书到期 |

### 6.2 JWT 密钥轮换流程

```bash
# 轮换步骤：
# 1. 生成新密钥
NEW_KEY=$(openssl rand -hex 32)

# 2. 将旧密钥设为 prev（用于验证旧 token）
export JWT_SECRET_KEY_PREV=$OLD_KEY

# 3. 设置新密钥
export JWT_SECRET_KEY=$NEW_KEY

# 4. 重启 FastAPI（所有新请求使用新密钥）
docker restart nexusvideo-fastapi

# 5. 等待 15 分钟后（旧 token 全部过期），移除 prev 密钥
unset JWT_SECRET_KEY_PREV

# 6. 更新密钥管理服务中的密钥值
```