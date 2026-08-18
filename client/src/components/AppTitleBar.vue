<!--
 * AppTitleBar.vue — 标题栏组件
 * ============================================================
 * 来源：苏璃光高保真规格书 1.1 节
 * 尺寸：1440×40，背景 --bg-surface
 * 布局：[Logo 24×24] [品牌名] — [模式切换标签组] — [spacer] — [云端/本地切换] [设置图标]
 * 模式切换对应 router 导航
-->
<script setup lang="ts">
import { computed } from 'vue';
import { useGenerateStore } from '../stores/generate';
import { useRouter } from 'vue-router';

const store = useGenerateStore();
const router = useRouter();

const modeToRoute: Record<string, string> = {
  t2v: '/',
  i2v: '/img2video',
  v2v: '/style',
};

function switchMode(m: 't2v' | 'i2v' | 'v2v') {
  store.setMode(m);
  router.push(modeToRoute[m]);
}

const isCloudMode = computed(() => store.cloudMode === 'cloud');
</script>

<template>
  <header class="title-bar">
    <!-- Logo + 品牌名 -->
    <div class="title-bar__brand">
      <svg
        class="title-bar__logo"
        width="24" height="24" viewBox="0 0 24 24"
        fill="none" xmlns="http://www.w3.org/2000/svg"
        aria-label="NexusVideo Logo"
      >
        <defs>
          <linearGradient id="logo-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#5B6CFF"/>
            <stop offset="100%" stop-color="#B14CFF"/>
          </linearGradient>
        </defs>
        <circle cx="12" cy="12" r="10" stroke="url(#logo-grad)" stroke-width="2" fill="none"/>
        <polygon points="10,8 16,12 10,16" fill="url(#logo-grad)"/>
      </svg>
      <span class="title-bar__name">NexusVideo</span>
    </div>

    <!-- 模式切换标签组 -->
    <div class="mode-tabs">
      <button
        class="mode-tab"
        :class="{ active: store.mode === 't2v' }"
        @click="switchMode('t2v')"
      >一句话出片</button>
      <button
        class="mode-tab"
        :class="{ active: store.mode === 'i2v' }"
        @click="switchMode('i2v')"
      >图生视频</button>
      <button
        class="mode-tab"
        :class="{ active: store.mode === 'v2v' }"
        @click="switchMode('v2v')"
      >视频风格化</button>
    </div>

    <span class="spacer"></span>

    <!-- 云端/本地切换（集成 DevOps CloudModeToggle 紧凑版） -->
    <button
      class="title-bar__cloud-toggle"
      :class="{ active: isCloudMode }"
      @click="store.toggleCloud()"
      title="点击切换本地/云端"
      :aria-label="isCloudMode ? '切换到本地模式' : '切换到云端模式'"
    >
      <svg v-if="!isCloudMode" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
        <rect x="3" y="3" width="10" height="10" rx="1.5"/>
        <rect x="6" y="6" width="4" height="4" rx="0.5" fill="currentColor" stroke="none"/>
        <line x1="1" y1="6" x2="3" y2="6"/>
        <line x1="1" y1="10" x2="3" y2="10"/>
        <line x1="13" y1="6" x2="15" y2="6"/>
        <line x1="13" y1="10" x2="15" y2="10"/>
        <line x1="6" y1="1" x2="6" y2="3"/>
        <line x1="10" y1="1" x2="10" y2="3"/>
        <line x1="6" y1="13" x2="6" y2="15"/>
        <line x1="10" y1="13" x2="10" y2="15"/>
      </svg>
      <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
        <path d="M4 11a3 3 0 0 1 0-6 4 4 0 0 1 7.5-1A3 3 0 0 1 12 11H4z"/>
      </svg>
      <span class="title-bar__cloud-text">
        {{ isCloudMode ? '云端' : '本地' }}
      </span>
      <span class="title-bar__cloud-dot"></span>
    </button>

    <span class="title-bar__mode-indicator" :class="isCloudMode ? 'cloud' : 'local'">
      {{ isCloudMode ? '云端加速模式' : '本地GPU渲染' }}
    </span>

    <!-- 设置图标 -->
    <button class="icon-btn title-bar__settings" title="设置">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1.1z"/>
      </svg>
    </button>
  </header>
</template>

<style scoped>
.title-bar {
  height: var(--titlebar-height);
  background: var(--bg-surface);
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
  border-bottom: var(--border-subtle);
}

.title-bar__brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.title-bar__name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}
.spacer { flex: 1; }
.title-bar__settings { width: 28px; height: 28px; }

.title-bar__cloud-toggle {
  height: 28px;
  padding: 0 12px 0 8px;
  border-radius: 14px;
  border: var(--border-default);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all 0.2s ease;
}
.title-bar__cloud-toggle:hover {
  border-color: var(--border-strong);
  color: var(--text-primary);
}
.title-bar__cloud-toggle.active {
  background: var(--brand-gradient);
  color: var(--text-on-brand);
  border-color: transparent;
  box-shadow: 0 0 12px rgba(91, 108, 255, 0.3);
}
.title-bar__cloud-text {
  font-size: 12px;
}
.title-bar__cloud-dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: var(--success);
  animation: pulse-dot 2s ease-in-out infinite;
}
.title-bar__cloud-toggle:not(.active) .title-bar__cloud-dot {
  background: var(--text-tertiary);
  animation: none;
}

.title-bar__mode-indicator {
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--bg-elevated);
  border: var(--border-subtle);
  color: var(--text-tertiary);
}
.title-bar__mode-indicator.cloud {
  color: var(--accent-cyan);
  border-color: rgba(61, 214, 232, 0.25);
  background: rgba(61, 214, 232, 0.08);
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>