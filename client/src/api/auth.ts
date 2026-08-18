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

export interface AuthToken {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
  expires_in?: number;
}

export interface AuthUser {
  id: string;
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

// ---------- 内部工具 ----------

/** 获取当前 Token（from localStorage） */
export function getToken(): string | null {
  const t = localStorage.getItem('nexus_token');
  return t || null;
}

/** 保存 Token 到 localStorage */
export function setToken(token: AuthToken): void {
  localStorage.setItem('nexus_token', token.access_token);
  if (token.refresh_token) {
    localStorage.setItem('nexusvideo_refresh_token', token.refresh_token);
  }
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
 * 通用 API 请求（自动附加 Token + 401 自动刷新）
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

  const res = await fetch(url, {
    ...fetchOptions,
    headers,
  });

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

    const retryRes = await fetch(url, {
      ...fetchOptions,
      headers: retryHeaders,
    });
    if (!retryRes.ok) {
      const err = await retryRes.text();
      throw new Error(`${retryRes.status}: ${err}`);
    }
    return (await retryRes.json()) as T;
  }

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`${res.status}: ${err}`);
  }
  return (await res.json()) as T;
}

/** 尝试刷新 Token */
async function tryRefreshToken(): Promise<boolean> {
  const refreshToken = localStorage.getItem('nexusvideo_refresh_token');
  if (!refreshToken) return false;

  try {
    const url = `${getApiBaseUrl()}/api/v1/auth/refresh`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ access_token: refreshToken }),
    });
    if (!res.ok) return false;
    const data = (await res.json()) as RefreshResponse;
    setToken(data.token);
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
  const data = await apiRequest<AuthResponse>('/api/v1/auth/register', {
    method: 'POST',
    skipAuth: true,
    body: JSON.stringify({ phone, password }),
  });
  setToken(data.token);
  setUser(data.user);
  return data;
}

/**
 * 登录
 * POST /api/v1/auth/login
 * body: { phone: string, password: string }
 * returns: { token, user }
 */
export async function login(phone: string, password: string): Promise<AuthResponse> {
  const data = await apiRequest<AuthResponse>('/api/v1/auth/login', {
    method: 'POST',
    skipAuth: true,
    body: JSON.stringify({ phone, password }),
  });
  setToken(data.token);
  setUser(data.user);
  return data;
}

/**
 * 刷新 Token
 * POST /api/v1/auth/refresh
 * body: { access_token: string }
 */
export async function refreshToken(): Promise<RefreshResponse> {
  return apiRequest<RefreshResponse>('/api/v1/auth/refresh', {
    method: 'POST',
    body: JSON.stringify({ access_token: getToken() }),
  });
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