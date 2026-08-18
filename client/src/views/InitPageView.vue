<!--
 * InitPageView.vue — 首次启动初始化页
 * ============================================================
 * 来源：苏璃光高保真规格书 第五章
 * 布局（全屏 1440×900）：
 *   Logo 64×64 (x=688, y=200)
 *   品牌名 (x=560, y=280)
 *   品牌口号 (x=500, y=324)
 *   进度文案区 (y=400)
 *     主文案 (y=408)
 *     进度条 (y=456, 280×4)
 *     副文案 (y=472)
 *   底部提示 (y=832)
 * ============================================================
 * 入场时序（animation-spec.md 3.1 节）：
 *   T=0ms     → 背景光晕淡入 800ms
 *   T=400ms   → Logo + 品牌名 opacity 0→1, translateY 20→0, 600ms
 *   T=800ms   → 进度文案 + 进度条 opacity 0→1, translateY 16→0, 400ms
-->
<script setup lang="ts">
import { ref, onUnmounted } from 'vue';

const emit = defineEmits<{
  ready: [];
}>();

const messages = [
  '正在准备创作环境…',
  '解压 AI 模型中…',
  '加载 ComfyUI 引擎…',
  '初始化 FastAPI 服务…',
  '预热 GPU 显存…',
  '检查系统依赖…',
  '即将完成准备…',
];
const currentIndex = ref(0);
let timer: ReturnType<typeof setInterval> | null = null;

onMountedInit();
function onMountedInit() {
  timer = setInterval(() => {
    currentIndex.value = (currentIndex.value + 1) % messages.length;
  }, 2500);
}

function handleStartCreating() {
  emit('ready');
}

onUnmounted(() => {
  if (timer) clearInterval(timer);
});
</script>

<template>
  <div class="init-page">
    <div class="init-page__glow"></div>

    <div class="init-page__content">
      <!-- Logo -->
      <svg
        class="init-page__logo init-page__fade"
        width="64" height="64" viewBox="0 0 64 64"
        fill="none" xmlns="http://www.w3.org/2000/svg"
        aria-label="NexusVideo"
      >
        <defs>
          <linearGradient id="init-logo-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#5B6CFF"/>
            <stop offset="100%" stop-color="#B14CFF"/>
          </linearGradient>
        </defs>
        <circle cx="32" cy="32" r="28" stroke="url(#init-logo-grad)" stroke-width="2.5" fill="none"/>
        <circle cx="32" cy="32" r="20" stroke="url(#init-logo-grad)" stroke-width="1" fill="none" opacity="0.5"/>
        <polygon points="28,22 44,32 28,42" fill="url(#init-logo-grad)"/>
      </svg>

      <h1 class="init-page__brand init-page__fade">NexusVideo</h1>
      <p class="init-page__slogan init-page__fade">让创意如光般流动</p>

      <div class="init-page__progress init-page__fade-2">
        <p class="init-page__progress-text">
          {{ messages[currentIndex] }}
        </p>

        <div class="init-page__bar"></div>

        <p class="init-page__subtext">首次准备约需 2-3 分钟，请耐心等待</p>
      </div>

      <button
        class="init-page__btn"
        @click="handleStartCreating()"
      >开始创作</button>
    </div>

    <p class="init-page__footer">
      首次准备约需 2-3 分钟，请耐心等待
    </p>
  </div>
</template>

<style scoped>
.init-page {
  width: 1440px;
  height: 900px;
  background: var(--bg-base);
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.init-page__glow {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 50% 25%, rgba(91, 108, 255, 0.18) 0%, transparent 55%),
    radial-gradient(circle at 80% 70%, rgba(177, 76, 255, 0.12) 0%, transparent 50%);
}

.init-page__content {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 480px;
  padding-top: 140px;
}

.init-page__logo {
  margin-bottom: var(--space-md);
}

.init-page__brand {
  text-align: center;
  color: var(--text-primary);
  font-size: var(--text-h1);
  font-weight: var(--font-weight-semibold);
  letter-spacing: var(--letter-spacing-heading);
}

.init-page__slogan {
  text-align: center;
  color: var(--text-tertiary);
  font-size: var(--text-body);
  margin-top: var(--space-sm);
}

.init-page__progress {
  margin-top: 48px;
  text-align: center;
  width: 100%;
}

.init-page__progress-text {
  color: var(--text-primary);
  font-size: var(--text-h3);
  font-weight: var(--font-weight-semibold);
  text-align: center;
}

.init-page__bar {
  width: 280px;
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
  margin: var(--space-md) auto 0;
  overflow: hidden;
  position: relative;
}
.init-page__bar::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--progress-flow);
  background-size: 200% 100%;
  animation: gradient-flow 1.5s linear infinite;
}

.init-page__subtext {
  color: var(--text-tertiary);
  font-size: var(--text-body-sm);
  margin-top: var(--space-md);
  text-align: center;
}

.init-page__btn {
  margin-top: 32px;
  height: 44px;
  padding: 0 32px;
  border-radius: var(--radius-md);
  background: var(--brand-gradient);
  border: none;
  color: var(--text-on-brand);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  transition:
    transform var(--dur-fast) var(--ease-out-quad),
    box-shadow var(--dur-hover) var(--ease-out-quad);
}
.init-page__btn:hover {
  box-shadow: var(--shadow-brand);
  transform: translateY(-1px);
}
.init-page__btn:active {
  transform: scale(0.95);
}

.init-page__footer {
  position: absolute;
  bottom: 32px;
  left: 0;
  right: 0;
  text-align: center;
  color: var(--text-disabled);
  font-size: 12px;
}
</style>