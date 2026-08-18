/*
 * auth.ts — 用户认证 Pinia Store
 * ============================================================
 * 来源：程流深 Task #11（用户系统 + 云端转发层）
 * 负责管理：
 *   - 登录/注册/登出状态
 *   - Token 持久化与自动刷新
 *   - 用户信息
 *   - 剩余额度
 *   - 登录态守卫
 *
 * 与 useProgress / generate store 协作：
 *   - 生成请求前检查额度
 *   - Token 过期时引导重新登录
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import {
  register,
  login,
  getCurrentUser,
  getQuotaInfo,
  logout,
  getToken,
  getUser,
  getQuota,
  type AuthUser,
  type QuotaResponse,
  type AuthResponse,
} from '../api/auth';

export const useAuthStore = defineStore('auth', () => {
  // ---- 状态 ----
  const isAuthenticated = ref<boolean>(false);
  const user = ref<AuthUser | null>(null);
  const quota = ref<QuotaResponse | null>(null);
  const isLoading = ref(false);
  const error = ref<string | null>(null);

  // ---- 计算属性 ----
  const remainingQuota = computed(() => {
    if (quota.value) return quota.value.remaining;
    if (user.value) return Math.max(0, (user.value.quota_daily || 5) - (user.value.used_today || 0));
    return 0;
  });
  const isQuotaExhausted = computed(() => remainingQuota.value <= 0);
  const isPaid = computed(() => user.value?.role === 'paid');
  const isFree = computed(() => !isPaid.value);
  const dailyUsagePercent = computed(() => {
    if (!quota.value) return 0;
    const { remaining, quota_daily } = quota.value;
    if (quota_daily === 0) return 100;
    return Math.round(((quota_daily - remaining) / quota_daily) * 100);
  });
  const quotaDisplayText = computed(() => {
    if (!quota.value) return '额度加载中…';
    const { remaining, quota_daily } = quota.value;
    return `今日剩余额度 ${remaining} / ${quota_daily}`;
  });
  const quotaRemainingText = computed(() => {
    const remain = remainingQuota.value;
    const total = quota.value?.quota_daily || (user.value?.quota_daily || 5);
    return `剩余 ${remain} / ${total} 次`;
  });
  const roleBadgeText = computed(() => {
    if (!user.value) return '未登录';
    return user.value.role === 'paid' ? '付费用户' : '免费用户';
  });

  // ---- Actions ----

  /** 初始化：从 localStorage 恢复登录态 */
  function initAuth() {
    const token = getToken();
    const storedUser = getUser();
    const storedQuota = getQuota();
    if (token && storedUser) {
      isAuthenticated.value = true;
      user.value = storedUser;
      if (storedQuota) quota.value = storedQuota;
    }
  }

  /**
   * 登录
   * @returns { user, token }
   */
  async function doLogin(phone: string, password: string): Promise<AuthResponse> {
    isLoading.value = true;
    error.value = null;
    try {
      const res = await login(phone, password);
      isAuthenticated.value = true;
      user.value = res.user;
      await refreshQuota();
      return res;
    } catch (e) {
      const msg = String(e);
      if (msg === '401: 手机号或密码错误') {
        error.value = '手机号或密码错误';
      } else if (msg === 'AUTH_EXPIRED') {
        error.value = '登录已过期，请重新登录';
      } else {
        error.value = `登录失败: ${msg}`;
      }
      throw e;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * 注册
   */
  async function doRegister(phone: string, password: string): Promise<AuthResponse> {
    isLoading.value = true;
    error.value = null;
    try {
      const res = await register(phone, password);
      isAuthenticated.value = true;
      user.value = res.user;
      await refreshQuota();
      return res;
    } catch (e) {
      const msg = String(e);
      if (msg.includes('409')) {
        error.value = '该手机号已注册';
      } else {
        error.value = `注册失败: ${msg}`;
      }
      throw e;
    } finally {
      isLoading.value = false;
    }
  }

  /** 登出 */
  function doLogout() {
    logout();
    isAuthenticated.value = false;
    user.value = null;
    quota.value = null;
  }

  /** 刷新用户信息 */
  async function refreshUser() {
    try {
      const u = await getCurrentUser();
      user.value = u;
    } catch {
      // Token 过期
    }
  }

  /** 刷新额度 */
  async function refreshQuota() {
    try {
      const q = await getQuotaInfo();
      quota.value = q;
    } catch {
      // 忽略，非关键路径
    }
  }

  /** 消耗一次额度（生成完成后调用） */
  function consumeQuota() {
    if (quota.value) {
      quota.value.remaining = Math.max(0, quota.value.remaining - 1);
    }
  }

  /** 检查是否可生成 */
  function canGenerate(): { ok: boolean; reason?: string } {
    if (!isAuthenticated.value) return { ok: false, reason: '请先登录' };
    if (isQuotaExhausted.value) return { ok: false, reason: '今日生成次数已用尽' };
    return { ok: true };
  }

  return {
    isAuthenticated, user, quota, isLoading, error,
    remainingQuota, isQuotaExhausted, isPaid, isFree,
    dailyUsagePercent,
    quotaDisplayText, quotaRemainingText, roleBadgeText,
    initAuth, doLogin, doRegister, doLogout,
    refreshUser, refreshQuota, consumeQuota, canGenerate,
  };
});