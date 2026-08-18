<!--
 * AppStatusBar.vue — 底部状态栏
 * ============================================================
 * 来源：苏璃光高保真规格书 1.3 节 + 程流深 Task #11 用户系统
 * 尺寸：1440×32，背景 --bg-surface
 *
 * 左侧：用户角色徽章 + 剩余额度 + 本地/云端模式 + GPU 信息
 * 右侧：版本号 + 后端运行时间
 -->
<script setup lang="ts">
import { computed } from 'vue';
import { useGenerateStore } from '../stores/generate';
import { useAuthStore } from '../stores/auth';

const store = useGenerateStore();
const auth = useAuthStore();

// 模式状态文本
const modeText = computed(() => {
  if (store.cloudMode === 'cloud') return '云端加速';
  return '本地GPU';
});

// 剩余额度文字
const quotaText = computed(() => {
  if (!auth.isAuthenticated) return '未登录';
  return auth.quotaRemainingText || '额度加载中…';
});

// 角色徽章
const roleBadge = computed(() => {
  if (!auth.isAuthenticated) return '未登录';
  return auth.roleBadgeText;
});

const hasAuth = computed(() => auth.isAuthenticated);
const isCloud = computed(() => store.cloudMode === 'cloud');
</script>

<template>
  <footer class="status-bar">
    <!-- 左侧信息 -->
    <div class="status-bar__left">
      <!-- 用户角色徽章 -->
      <span
        class="status-bar__item role-badge"
        :class="{ paid: auth.isPaid }"
      >
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 1a5 5 0 0 0-5 5v1a3 3 0 0 0 0 6v2h10v-2a3 3 0 0 0 0-6V6a5 5 0 0 0-5-5z"/>
        </svg>
        <span>{{ roleBadge }}</span>
      </span>
      <span class="status-bar__divider"></span>

      <!-- 剩余额度 -->
      <span class="status-bar__item quota">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">
          <path d="M14 6l-6-4-6 4v6l6 4 6-4V6z"/>
        </svg>
        <span>{{ quotaText }}</span>
      </span>
      <span class="status-bar__divider"></span>

      <!-- 模式状态 -->
      <template v-if="isCloud">
        <span class="status-bar__item cloud">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <path d="M4 11a3 3 0 0 1 0-6 4 4 0 0 1 7.5-1A3 3 0 0 1 12 11H4z"/>
          </svg>
          <span>{{ modeText }}</span>
        </span>
        <span class="status-bar__divider"></span>
        <span class="status-bar__item">
          <span class="status-bar__dot" style="background: var(--accent-cyan)"></span>
          <span>连接正常</span>
        </span>
      </template>
      <template v-else>
        <span class="status-bar__item">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
            <rect x="3" y="3" width="10" height="10" rx="1" fill="none" stroke="currentColor" stroke-width="1.2"/>
            <rect x="6" y="6" width="4" height="4" rx="0.5" fill="currentColor"/>
          </svg>
          <span>{{ modeText }}</span>
        </span>
        <span class="status-bar__divider"></span>
        <span class="status-bar__item">
          <span class="status-bar__dot" style="background: var(--success)"></span>
          <span>GPU: RTX 4070</span>
        </span>
        <span class="status-bar__divider"></span>
        <span class="status-bar__item">
          <span>VRAM: 8.2 / 12 GB</span>
        </span>
      </template>
    </div>

    <!-- 右侧信息 -->
    <div class="status-bar__right">
      <span class="status-bar__version">v0.1.0</span>
      <span class="status-bar__divider"></span>
      <span class="status-bar__commit">
        {{ store.backendStatus.uptime_secs
          ? `运行 ${Math.floor(store.backendStatus.uptime_secs / 60)} 分钟`
          : '后端未启动'
        }}
      </span>
    </div>
  </footer>
</template>

<style scoped>
.status-bar {
  height: var(--statusbar-height);
  background: var(--bg-surface);
  padding: 0 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  border-top: var(--border-subtle);
  font-size: 12px;
  color: var(--text-tertiary);
}

.status-bar__left,
.status-bar__right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-bar__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-tertiary);
}

.status-bar__item.cloud {
  color: var(--accent-cyan);
}

.status-bar__item.role-badge.paid {
  color: var(--accent-cyan);
}

.status-bar__item.quota {
  color: var(--text-secondary);
}

.status-bar__divider {
  width: 1px;
  height: 12px;
  background: rgba(255, 255, 255, 0.08);
}

.status-bar__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
}

.status-bar__version {
  color: var(--text-disabled);
}
</style>