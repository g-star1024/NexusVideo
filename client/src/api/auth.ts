/**
 * auth.ts — 用户认证 API 封装层
 * ============================================================
 * 来源：程流深 Task #11（用户系统 + 云端转发层）
 * 端点基础：VITE_API_BASE_URL（默认 http://localhost:9881）
 *
 * 认证流程：
 *   1. 注册 POST /api/v1/auth/register   → body {phone, password}
 *   2. 登录 POST /api/v1/auth/login      → body {phone, password}
 *   3. 刷新 POST /api/v1/auth/refresh    → body {access_token}
 *   4. 当前用户 GET /api/v1/auth/me
 *   5. 剩余额度 GET /api/v1/auth/quota
 *
 * Token 管理策略：
 *   - 登录后将 access_token 存入 localStorage
 *   - 每次 API 请求自动附加 Authorization: Bearer <token>
 *   - 401 响应时自动调用 refresh 刷新
 *   - 刷新失败则清除 Token 并返回 401，触发前端跳转登录页
 */
import { getApiBaseUrl } from './utils';

// ---------- 类型定义 ----------

/**
 * 后端返回的 Token 即字符串（JWT）。统一为 string，去掉 .access_token 访问。
 */
export type AuthToken = string;

export interface AuthUser {
  id: string | number;
  phone: string;
  username?: string;
  avatar?: string;
  role?: 'free' | 'paid';
  quota_daily?: number;
  used_today?: number;
  created_at?: string;
}

export interface AuthResponse {
  token: AuthToken;
  user: AuthUser;
}

export interface RefreshResponse {
  token: AuthToken;
}

export interface QuotaResponse {
  remaining: number;
  quota_daily: number;
  used_today: number;
}

/**
 * 注册 / 登录 / 刷新返回体中 data 字段的真实结构（以 backend/routers/auth.py 的
 * TokenResponse 为准）：token 是「字符串」，user.id 是「数字」。
 */
export interface RawAuthData {
  token: string;
  token_type?: string;
  expires_in?: number;
  user: AuthUser;
}

// ---------- 内部工具 ----------

/** 获取当前 Token（from localStorage） */
export function getToken(): string | null {
  const t = localStorage.getItem('nexus_token');
  return t || null;
}

/** 保存 Token（字符串）到 localStorage */
export function setToken(token: AuthToken): void {
  localStorage.setItem('nexus_token', token);
}

/** 清除所有认证信息 */
export function clearAuth(): void {
  localStorage.removeItem('nexus_token');
  localStorage.removeItem('nexusvideo_refresh_token');
  localStorage.removeItem('nexusvideo_user');
}

/** 获取存储的用户信息 */
export function getUser(): AuthUser | null {
  const raw = localStorage.getItem('nexusvideo_user');
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

/** 保存用户信息 */
export function setUser(user: AuthUser): void {
  localStorage.setItem('nexusvideo_user', JSON.stringify(user));
}

/** 获取存储的剩余额度 */
export function getQuota(): QuotaResponse | null {
  const raw = localStorage.getItem('nexusvideo_quota');
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

/** 保存额度信息 */
export function setQuota(q: QuotaResponse): void {
  localStorage.setItem('nexusvideo_quota', JSON.stringify(q));
}

/**
 * 创建带 Token 的 fetch 请求头
 */
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...extra,
  };
  const token = getToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

/**
 * 解析后端统一信封 {success, error_code, message, data}
 * - 成功响应：返回 data
 * - 错误响应（FastAPI HTTPException）：body 为 {detail:{success, error_code, message}}，返回 detail
 * - 其它：原样返回
 */
function unwrapEnvelope<T>(body: any): T {
  if (body && typeof body === 'object' && 'data' in body) {
    return body.data as T;
  }
  if (body && typeof body === 'object' && 'detail' in body) {
    return body.detail as T;
  }
  return body as T;
}

/**
 * 提取错误响应中的可读信息（兼容 {detail:{message}} / {detail:string} / {message} / 纯文本）
 */
async function extractErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body?.detail?.message) return body.detail.message;
    if (body?.detail) return typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    if (body?.message) return body.message;
    return JSON.stringify(body);
  } catch {
    try {
      return (await res.text()) || res.statusText;
    } catch {
      return res.statusText;
    }
  }
}

/**
 * 通用 API 请求（自动附加 Token + 解包信封 + 401 自动刷新 + 网络错误捕获）
 *
 * 约定（以 backend/routers/auth.py 为准）：
 *   - 成功响应统一信封，真实数据在 .data
 *   - 失败响应为 FastAPI HTTPException，可读信息在 .detail.message
 *   - 连接级失败（后端未启动 / 地址不可达 / CORS / 断网）抛 Error('无法连接服务器，请确认本地服务已启动（127.0.0.1:9881）')
 */
async function apiRequest<T>(
  path: string,
  options: RequestInit & { skipAuth?: boolean } = {},
): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  const { skipAuth = false, ...fetchOptions } = options;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(fetchOptions.headers as Record<string, string> || {}),
  };

  if (!skipAuth) {
    const token = getToken();
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
  }

  let res: Response;
  try {
    res = await fetch(url, {
      ...fetchOptions,
      headers,
    });
  } catch (e) {
    // 网络层失败：fetch 直接抛 TypeError: Failed to fetch / ECONNREFUSED 等。
    // 不要将裸 TypeError 直接甩给用户，统一为友好提示。
    void e;
    throw new Error('无法连接服务器，请确认本地服务已启动（127.0.0.1:9881）');
  }

  if (res.status === 401) {
    // 尝试刷新 Token
    const refreshed = await tryRefreshToken();
    if (!refreshed) {
      // 刷新失败，清除认证
      clearAuth();
      throw new Error('AUTH_EXPIRED');
    }
    // 用新 Token 重试
    const retryHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(fetchOptions.headers as Record<string, string> || {}),
    };
    retryHeaders['Authorization'] = `Bearer ${getToken() || ''}`;

    let retryRes: Response;
    try {
      retryRes = await fetch(url, {
        ...fetchOptions,
        headers: retryHeaders,
      });
  } catch (e) {
    throw new Error('无法连接服务器，请确认本地服务已启动（127.0.0.1:9881）');
  }
    if (!retryRes.ok) {
      const err = await extractErrorDetail(retryRes);
      throw new Error(`${retryRes.status}: ${err}`);
    }
    return unwrapEnvelope<T>(await retryRes.json());
  }

  if (!res.ok) {
    const err = await extractErrorDetail(res);
    throw new Error(`${res.status}: ${err}`);
  }
  return unwrapEnvelope<T>(await res.json());
}

/** 尝试刷新 Token（后端 /refresh 吃当前 access_token，无需独立 refresh_token） */
async function tryRefreshToken(): Promise<boolean> {
  const token = getToken();
  if (!token) return false;

  try {
    const url = `${getApiBaseUrl()}/api/v1/auth/refresh`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: token }),
    });
    if (!res.ok) return false;
    const data = unwrapEnvelope<RawAuthData>(await res.json());
    setToken(data.token);
    setUser(data.user);
    return true;
  } catch {
    return false;
  }
}

// ---------- 公开 API ----------

/**
 * 注册
 * POST /api/v1/auth/register
 * body: { phone: string, password: string }
 * returns: { token, user }
 */
export async function register(phone: string, password: string): Promise<AuthResponse> {
  const data = await apiRequest<RawAuthData>('/api/v1/auth/register', {
    method: 'POST',
    skipAuth: true,
    body: JSON.stringify({ phone, password }),
  });
  setToken(data.token);
  setUser(data.user);
  return {
    token: data.token,
    user: data.user,
  };
}

/**
 * 登录
 * POST /api/v1/auth/login
 * body: { phone: string, password: string }
 * returns: { token, user }
 */
export async function login(phone: string, password: string): Promise<AuthResponse> {
  const data = await apiRequest<RawAuthData>('/api/v1/auth/login', {
    method: 'POST',
    skipAuth: true,
    body: JSON.stringify({ phone, password }),
  });
  setToken(data.token);
  setUser(data.user);
  return {
    token: data.token,
    user: data.user,
  };
}

/**
 * 刷新 Token
 * POST /api/v1/auth/refresh
 * body: { access_token: string }
 */
export async function refreshToken(): Promise<RefreshResponse> {
  const data = await apiRequest<RawAuthData>('/api/v1/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ access_token: getToken() }),
  });
  setToken(data.token);
  setUser(data.user);
  return { token: data.token };
}

/**
 * 获取当前用户
 * GET /api/v1/auth/me
 */
export async function getCurrentUser(): Promise<AuthUser> {
  const user = await apiRequest<AuthUser>('/api/v1/auth/me');
  setUser(user);
  return user;
}

/**
 * 查询剩余额度
 * GET /api/v1/auth/quota
 */
export async function getQuotaInfo(): Promise<QuotaResponse> {
  const q = await apiRequest<QuotaResponse>('/api/v1/auth/quota');
  setQuota(q);
  return q;
}

/**
 * 登出（清除本地 Token）
 */
export function logout(): void {
  clearAuth();
}