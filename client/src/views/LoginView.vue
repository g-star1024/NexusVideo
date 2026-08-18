<!--
 * LoginView.vue — 登录 / 注册页
 * ============================================================
 * 来源：程流深 Task #11（用户认证系统）+ 苏璃光设计 Token
 * 设计意图：
 *   - 深色毛玻璃背景，与主界面视觉一致
 *   - 居中卡片布局（420×520），品牌渐变点缀
 *   - 登录/注册 Tab 切换
 *   - 手机号 + 密码输入框（使用 design-tokens）
 *   - 注册后自动登录
 *   - 错误提示浮于输入框下方
 *
 * 布局坐标系（App 1440×900）：
 *   卡片中心：(720, 450)
 *   卡片尺寸：420 × 520
 * ============================================================
-->
<script setup lang="ts">
import { ref, reactive, computed } from 'vue';
import { useRouter } from 'vue-router';
import { useAuthStore } from '../stores/auth';

const router = useRouter();
const auth = useAuthStore();

// ---- Tab 切换 ----
type TabMode = 'login' | 'register';
const activeTab = ref<TabMode>('login');

// ---- 表单数据 ----
const form = reactive({
  phone: '',
  password: '',
});

// ---- 表单状态 ----
const loading = ref(false);
const errorMsg = ref('');
const passwordVisible = ref(false);

// ---- 表单校验 ----
const phoneValid = computed(() => /^1[3-9]\d{9}$/.test(form.phone));
const passwordValid = computed(() => form.password.length >= 6);
const canSubmit = computed(() => phoneValid.value && passwordValid.value && !loading.value);

function clearError() {
  errorMsg.value = '';
}

async function handleSubmit() {
  clearError();
  if (!canSubmit.value) return;

  loading.value = true;

  try {
    if (activeTab.value === 'register') {
      await auth.doRegister(form.phone, form.password);
    } else {
      await auth.doLogin(form.phone, form.password);
    }
    // 登录成功后跳转到主界面
    router.push('/');
  } catch (e) {
    errorMsg.value = auth.error || '操作失败，请稍后重试';
  } finally {
    loading.value = false;
  }
}

function switchTab(tab: TabMode) {
  activeTab.value = tab;
  clearError();
}
</script>

<template>
  <div class="login-page">
    <!-- 背景装饰光晕 -->
    <div class="login-bg-glow glow-blue"></div>
    <div class="login-bg-glow glow-purple"></div>

    <!-- 登录卡片 -->
    <div class="login-card">
      <!-- 品牌 Logo -->
      <div class="login-brand">
        <svg
          width="48" height="48" viewBox="0 0 24 24"
          fill="none" xmlns="http://www.w3.org/2000/svg"
          aria-label="NexusVideo Logo"
        >
          <defs>
            <linearGradient id="login-logo-grad" x1="0%" y1="0%" x2="100%" y2="100%">
              <stop offset="0%" stop-color="#5B6CFF"/>
              <stop offset="100%" stop-color="#B14CFF"/>
            </linearGradient>
          </defs>
          <circle cx="12" cy="12" r="10" stroke="url(#login-logo-grad)" stroke-width="2" fill="none"/>
          <polygon points="10,8 16,12 10,16" fill="url(#login-logo-grad)"/>
        </svg>
        <span class="login-brand__name">NexusVideo</span>
        <span class="login-brand__desc">AI 视频生成平台</span>
      </div>

      <!-- Tab 切换 -->
      <div class="login-tabs">
        <button
          class="login-tab"
          :class="{ active: activeTab === 'login' }"
          @click="switchTab('login')"
        >登录</button>
        <button
          class="login-tab"
          :class="{ active: activeTab === 'register' }"
          @click="switchTab('register')"
        >注册</button>
      </div>

      <!-- 错误提示 -->
      <div v-if="errorMsg" class="login-error">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm0 2.5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0V4.25A.75.75 0 0 1 8 3.5zM8 11a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5z"/>
        </svg>
        <span>{{ errorMsg }}</span>
      </div>

      <!-- 表单 -->
      <form class="login-form" @submit.prevent="handleSubmit">
        <div class="login-field">
          <label class="login-field__label">手机号</label>
          <input
            v-model="form.phone"
            class="login-input"
            type="tel"
            placeholder="请输入手机号"
            maxlength="11"
            autocomplete="tel"
            @input="clearError"
          />
          <span
            v-if="form.phone && !phoneValid"
            class="login-field__hint"
          >请输入有效手机号</span>
        </div>

        <div class="login-field">
          <label class="login-field__label">密码</label>
          <div class="login-input__wrapper">
            <input
              v-model="form.password"
              class="login-input"
              :type="passwordVisible ? 'text' : 'password'"
              placeholder="请输入密码（至少6位）"
              autocomplete="current-password"
              @input="clearError"
            />
            <button
              type="button"
              class="login-input__toggle"
              @click="passwordVisible = !passwordVisible"
              :title="passwordVisible ? '隐藏密码' : '显示密码'"
            >
              <svg v-if="!passwordVisible" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
              <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
              </svg>
            </button>
          </div>
        </div>

        <button
          class="login-submit"
          :disabled="!canSubmit || loading"
          :class="{ 'is-loading': loading }"
        >
          <span v-if="loading" class="login-submit__spinner"></span>
          <span v-else>
            {{ activeTab === 'login' ? '登录' : '注册并登录' }}
          </span>
        </button>
      </form>

      <p class="login-footer">
        {{ activeTab === 'login'
          ? '没有账号？'
          : '已有账号？'
        }}
        <span
          class="login-footer__link"
          @click="switchTab(activeTab === 'login' ? 'register' : 'login')"
        >
          {{ activeTab === 'login' ? '立即注册' : '去登录' }}
        </span>
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  width: 100%;
  height: 900px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  background: var(--bg-base);
  overflow: hidden;
}

.login-bg-glow {
  position: absolute;
  inset: 0;
  pointer-events: none;
}
.login-bg-glow.glow-blue {
  background: radial-gradient(circle at 30% 40%, rgba(91, 108, 255, 0.15) 0%, transparent 55%);
}
.login-bg-glow.glow-purple {
  background: radial-gradient(circle at 70% 60%, rgba(177, 76, 255, 0.12) 0%, transparent 50%);
}

.login-card {
  position: relative;
  z-index: 1;
  width: 420px;
  padding: 40px 32px;
  border-radius: 20px;
  background: var(--bg-surface);
  border: var(--border-subtle);
  box-shadow: 0 24px 48px rgba(0, 0, 0, 0.3);
  backdrop-filter: blur(12px);
  display: flex;
  flex-direction: column;
  gap: 24px;
  animation: card-enter 0.4s ease-out;
}

@keyframes card-enter {
  from { opacity: 0; transform: translateY(12px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

.login-brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.login-brand__name {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.01em;
  margin-top: 8px;
}
.login-brand__desc {
  font-size: 12px;
  color: var(--text-tertiary);
}

.login-tabs {
  display: flex;
  background: var(--bg-elevated);
  border-radius: 10px;
  padding: 4px;
  gap: 4px;
}
.login-tab {
  flex: 1;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.2s ease;
}
.login-tab.active {
  background: var(--brand-gradient);
  color: var(--text-on-brand);
  box-shadow: 0 2px 8px rgba(91, 108, 255, 0.3);
}

.login-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(255, 97, 97, 0.1);
  border: 1px solid rgba(255, 97, 97, 0.25);
  color: var(--error);
  font-size: 12px;
  animation: fade-in 0.2s ease;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.login-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.login-field__label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
}
.login-field__hint {
  font-size: 11px;
  color: var(--warning);
}

.login-input {
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border-radius: 10px;
  border: var(--border-default);
  background: var(--bg-elevated);
  color: var(--text-primary);
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: all 0.2s ease;
  box-sizing: border-box;
}
.login-input:focus {
  border-color: var(--brand-blue);
  box-shadow: 0 0 0 3px rgba(91, 108, 255, 0.15);
}
.login-input::placeholder {
  color: var(--text-disabled);
}

.login-input__wrapper {
  position: relative;
}
.login-input__wrapper .login-input {
  padding-right: 44px;
}
.login-input__toggle {
  position: absolute;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  color: var(--text-tertiary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
}

.login-submit {
  width: 100%;
  height: 44px;
  border-radius: 10px;
  border: none;
  background: var(--brand-gradient);
  color: var(--text-on-brand);
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease;
  box-shadow: 0 2px 8px rgba(91, 108, 255, 0.3);
}
.login-submit:hover:not(:disabled) {
  box-shadow: 0 4px 16px rgba(91, 108, 255, 0.4);
  transform: translateY(-1px);
}
.login-submit:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}
.login-submit__spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: var(--text-on-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.login-footer {
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
}
.login-footer__link {
  color: var(--brand-blue);
  font-weight: 600;
  cursor: pointer;
  margin-left: 4px;
}
.login-footer__link:hover {
  color: var(--brand-purple);
}

@keyframes fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>