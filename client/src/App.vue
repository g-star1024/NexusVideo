<!--
 * App.vue — NexusVideo 主应用壳组件
 * ============================================================
 * 布局骨架（苏璃光高保真规格书 0.1 画布坐标系）：
 *   ┌──────────────────────────────────────┐ ← y=0
 *   │  标题栏 (40px)                      │
 *   ├────────┬─────────────────────────────┤ ← y=40
 *   │  左侧  │    主内容区 (1248×828)       │
 *   │  栏     │                             │
 *   │  200px  │  x: 200 ~ 1440             │
 *   │  x: 0~200  │  y: 40 ~ 868            │
 *   ├────────┴─────────────────────────────┤ ← y=868
 *   │  状态栏 (32px)                       │
 *   └──────────────────────────────────────┘ ← y=900
 *
 * 首屏引导动画时序（animation-spec.md 3.1 节）：
 *   T=800ms  → 输入框淡入
 *   T=1400ms → 生成按钮淡入
 *   T=1800ms → 灵感词 stagger 淡入
 *   T=3200ms → 侧栏滑入
 * ============================================================
-->
<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { useGenerateStore } from './stores/generate';
import {
  startBackend,
  onBackendStatus,
  onBackendLog,
} from './api/nexus';

import AppTitleBar from './components/AppTitleBar.vue';
import AppSidebar from './components/AppSidebar.vue';
import AppStatusBar from './components/AppStatusBar.vue';
import InitPage from './views/InitPageView.vue';
import SmartRouteModal from './components/SmartRouteModal.vue';
import { useAuthStore } from './stores/auth';

const router = useRouter();
const store = useGenerateStore();
const auth = useAuthStore();

const appReady = ref(false);
const showInitPage = ref(false);
const heroEntered = ref(false);
const btnEntered = ref(false);
const sidebarEntered = ref(false);

// SmartRouteModal 状态
const showSmartRoute = ref(false);
const gpuMemory = ref(0);
const gpuDeviceName = ref('未知设备');

let unlisteners: Array<() => void> = [];

onMounted(async () => {
  // 1. 初始化认证状态（从 localStorage 恢复登录态）
  auth.initAuth();

  // 2. 加载历史
  store.loadHistory();

  // 2. 订阅后端事件
  unlisteners.push(await onBackendStatus((s) => {
    store.updateBackendStatus(s);
    // 后端就绪后，隐藏初始化页
    if (s.comfyui === 'running' && s.fastapi === 'running') {
      showInitPage.value = false;
      appReady.value = true;
      startHeroAnimation();
    }
  }));
  // 忽略日志详情，仅订阅
  await onBackendLog(() => {});

  // 3. 检测后端是否已运行
  try {
    const s = await store.refreshBackendStatus();
    const backendS = store.backendStatus;
    if (backendS.comfyui === 'running' && backendS.fastapi === 'running') {
      showInitPage.value = false;
      appReady.value = true;
      startHeroAnimation();
    } else {
      showInitPage.value = true;
      // 尝试自动启动后端
      try { await startBackend(); } catch { /* 用户稍后可手动启动 */ }
    }
  } catch {
    showInitPage.value = true;
  }
});

function startHeroAnimation() {
  // 首屏引导动画时序
  setTimeout(() => { heroEntered.value = true; }, 800);
  setTimeout(() => { btnEntered.value = true; }, 1400);
  // 灵感词 stagger 在组件内通过 CSS transition-delay 实现
  setTimeout(() => { sidebarEntered.value = true; }, 3200);
}

function onReadyToCreate() {
  showInitPage.value = false;
  appReady.value = true;
  startHeroAnimation();
}

function onActivateCloud() {
  store.cloudMode = 'cloud';
  showSmartRoute.value = false;
}

function onUseLocal() {
  showSmartRoute.value = false;
}

onUnmounted(() => unlisteners.forEach((u) => u()));
</script>

<template>
  <div class="app-shell">
    <!-- 首次启动初始化页（后端未就绪时显示） -->
    <InitPage v-if="showInitPage" @ready="onReadyToCreate" />

    <!-- 主应用壳（后端就绪后显示） -->
    <template v-else>
      <!-- 标题栏 -->
      <AppTitleBar />

      <!-- 主体区域：左侧历史栏 + 右侧内容区 -->
      <div class="app-main">
        <!-- 左侧历史栏 -->
        <div
          class="app-sidebar"
          :class="{ 'sidebar-enter': true, 'entered': sidebarEntered }"
        >
          <AppSidebar />
        </div>

        <!-- 主内容区 -->
        <div class="app-content">
          <div class="glow-layer glow-blue"></div>
          <div class="glow-layer glow-purple"></div>
          <router-view v-slot="{ Component }">
            <Transition name="page-fade">
              <component :is="Component" />
            </Transition>
          </router-view>
        </div>
      </div>

      <!-- 状态栏 -->
      <AppStatusBar />
    </template>
  </div>

  <!-- 显存不足推荐弹窗 -->
  <SmartRouteModal
    :visible="showSmartRoute"
    :gpu-memory="gpuMemory"
    :gpu-device-name="gpuDeviceName"
    @activate-cloud="onActivateCloud"
    @use-local="onUseLocal"
  />
</template>

<style scoped>
.app-shell {
  width: 1440px;
  height: 900px;
  background: var(--bg-base);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  font-family: var(--font-sans);
  color: var(--text-primary);
}

.app-main {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.app-sidebar {
  width: var(--sidebar-width);
  height: calc(900px - var(--titlebar-height) - var(--statusbar-height));
  background: var(--bg-surface);
  border-right: var(--border-subtle);
  flex-shrink: 0;
}

.app-content {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: var(--bg-base);
}

/* 背景光晕 */
.glow-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}
.glow-blue {
  background: radial-gradient(circle at 50% 25%, rgba(91, 108, 255, 0.12) 0%, transparent 55%);
}
.glow-purple {
  background: radial-gradient(circle at 80% 75%, rgba(177, 76, 255, 0.08) 0%, transparent 50%);
}

.app-content > .page-fade-enter-active,
.app-content > .page-fade-leave-active {
  position: relative;
  z-index: 1;
}
</style>