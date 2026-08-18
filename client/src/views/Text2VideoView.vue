<!--
 * Text2VideoView.vue — 模式一：一句话出片
 * ============================================================
 * 来源：苏璃光高保真规格书 第二章
 * 三大状态：
 *   1. idle   → 超大输入框 + 灵感词 + 生成按钮
 *   2. generating → 进度文案化容器（560×360）
 *   3. completed → 结果视频卡（720×680）
 * ============================================================
 * 方案 B 进度接入：
 *   - useProgress.startTracking(taskId) → WebSocket 直连 /progress/ws
 *   - 断连自动降级 HTTP 轮询
 *   - 终态通过 onCompleted / onFailed 回调返回
 *   - 视频播放使用 buildVideoUrl 转换的 HTTP URL
-->
<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, computed } from 'vue';
import { useGenerateStore } from '../stores/generate';
import { useProgress, formatRemainingText } from '../composables/useProgress';
import { buildVideoUrl } from '../api/nexus';

const store = useGenerateStore();
const progress = useProgress();

// ---- 用户输入 ----
const promptText = ref('');
const focused = ref(false);

// ---- 首屏引导动画状态 ----
const heroEntered = ref(false);
const btnEntered = ref(false);
const wordsEntered = ref(false);

// ---- "再来一次"按钮 ----
const retryPulse = ref(false);
const retrySpin = ref(false);

// ---- 灵感词数据 ----
const words = [
  '赛博朋克', '电影级光影', '慢动作', '水下世界', '极光星空',
  '油画风格', '樱花飘落', '火焰特效', '复古胶片', '微距特写',
];
const selectedWords = ref<string[]>([]);

// ---- 连接状态显示（调试用） ----
const connLabel = computed(() => {
  const map: Record<string, string> = {
    idle: '准备就绪',
    connecting: '连接中…',
    ws_connected: 'WebSocket 实时同步',
    polling: 'HTTP 轮询模式',
    disconnected: '连接断开',
  };
  return map[progress.connectionState.value] || progress.connectionState.value;
});

// ---- 初始化 ----
onMounted(() => {
  setTimeout(() => { heroEntered.value = true; }, 800);
  setTimeout(() => { btnEntered.value = true; }, 1400);
  setTimeout(() => { wordsEntered.value = true; }, 1800);
});

// ---- 注册 progress 终态回调 ----
progress.onCompleted(() => {
  // useProgress 内部已处理进度归零
});

// ---- 监听生成状态，控制文案轮播 + WebSocket 连接 ----
watch(() => store.state, (newState) => {
  if (newState === 'generating') {
    // 方案 B：通过 WebSocket 直连后端接收进度
    if (store.currentTaskId) {
      // 云端模式下使用云端 WS 端点，本地模式使用本地端点
      const mode = store.cloudMode === 'cloud' ? 'cloud' : 'local';
      progress.startTracking(store.currentTaskId, mode);
    }
  } else {
    // 离开生成态时清理 WebSocket / 轮询
    progress.reset();
    if (newState === 'completed') {
      retryPulse.value = true;
      setTimeout(() => { retryPulse.value = false; }, 2200);
    }
  }
});

function onWordClick(word: string) {
  if (selectedWords.value.includes(word)) {
    selectedWords.value = selectedWords.value.filter((w) => w !== word);
  } else {
    selectedWords.value.push(word);
  }
  const joined = selectedWords.value.join('，');
  promptText.value = joined;
}

function buildPrompt(): string {
  if (promptText.value.trim()) return promptText.value.trim();
  if (selectedWords.value.length > 0) return selectedWords.value.join('，');
  return '';
}

async function doGenerate() {
  const prompt = buildPrompt();
  if (!prompt) return;
  await store.startGeneration({
    prompt,
    mode: 't2v',
  });
  // startGeneration 内部会设置 currentTaskId 和 state='generating'
  // 上面的 watch 会在 state 变为 'generating' 时触发 startTracking
  // 确保 taskId 已经设置后再启动 WebSocket
  if (store.state === 'generating' && store.currentTaskId) {
    const mode = store.cloudMode === 'cloud' ? 'cloud' : 'local';
    progress.startTracking(store.currentTaskId, mode);
  }
}

function doRetry() {
  retrySpin.value = true;
  setTimeout(() => { retrySpin.value = false; }, 500);
  doGenerate();
}

function doDownload() {
  if (!store.currentResultUrl) return;
  const w: Window | undefined = typeof window !== 'undefined' ? window : undefined;
  w?.open(store.currentResultUrl, '_blank');
}

function doCancel() {
  store.cancelGeneration();
}

// ---- 副文案（响应式） ----
const estimatedText = computed(() => formatRemainingText(progress.remainingSeconds.value ?? null));
</script>

<template>
  <div class="t2v-view">
    <!-- ============= 状态 1：输入态 ============= -->
    <div v-if="store.state === 'idle'" class="t2v-input">
      <p class="t2v-subtitle">描述你想要的视频画面</p>

      <textarea
        ref="heroInput"
        v-model="promptText"
        class="hero-input hero-input-fade"
        :class="{ entered: heroEntered }"
        :placeholder="selectedWords.length === 0 ? '描述你想要的视频画面…' : ''"
        @focus="focused = true"
        @blur="focused = false"
        @keydown.enter.exact.prevent="doGenerate()"
      ></textarea>

      <p
        v-if="focused"
        class="t2v-focus-hint"
      >试试输入：赛博朋克、电影级光影、慢动作</p>

      <div
        class="inspiration-words"
        :class="{ entered: wordsEntered }"
        :style="{ marginTop: focused ? '8px' : '24px' }"
      >
        <button
          v-for="(word, idx) in words"
          :key="word"
          class="word-btn word-btn-fade"
          :class="{ selected: selectedWords.includes(word) }"
          :style="{ transitionDelay: `${idx * 80}ms` }"
          @click="onWordClick(word)"
        >{{ word }}</button>
      </div>

      <div class="t2v-btn-wrap">
        <button
          class="btn-primary btn-enter-fade"
          :class="{ entered: btnEntered }"
          :disabled="!promptText && selectedWords.length === 0"
          @click="doGenerate()"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M14.5 2.5l-2 4-4-1 1-4-5 9 4 1-1 4 4-1 2 4 5-9-4-1 1-4-4 1"/>
          </svg>
          生成视频
        </button>
      </div>

      <p class="t2v-footer">
        支持本地 GPU 渲染<span class="t2v-footer__sep">|</span>切换云端加速
      </p>
    </div>

    <!-- ============= 状态 2：生成中 ============= -->
    <div v-else-if="store.state === 'generating'" class="t2v-progress">
      <div class="progress-container">
        <p
          class="progress-container__text progress-text--breathing progress-text-fade"
          :class="{ 'out': progress.isCrossfading }"
        >{{ progress.currentText }}</p>

        <div class="progress-bar-real">
          <div
            class="progress-bar-real__fill"
            :style="{ width: `${progress.currentProgress}%` }"
          ></div>
        </div>

        <p class="progress-container__subtext">{{ estimatedText }}</p>

        <button
          class="progress-container__cancel-btn"
          @click="doCancel()"
        >取消生成</button>
      </div>
    </div>

    <!-- ============= 状态 3：完成 ============= -->
    <div v-else-if="store.state === 'completed'" class="t2v-result">
      <div class="result-card result-card-enter entered">
        <div class="result-card__video">
          <!-- 方案 B：currentResultUrl 已是完整 HTTP URL -->
          <video
            v-if="store.currentResultUrl"
            :src="store.currentResultUrl"
            controls
            autoplay
            loop
            muted
            class="result-card__video-inner"
          ></video>
          <div v-else class="result-card__video-placeholder">
            <span>视频生成完成</span>
          </div>
        </div>

        <div class="result-card__actions">
          <button
            class="btn-primary btn-retry"
            :class="{ 'btn-retry--pulse': retryPulse }"
            @click="doRetry()"
          >
            <svg
              class="btn-retry--icon-spin"
              :class="{ 'spinning': retrySpin }"
              width="18" height="18" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
            >
              <path d="M21 12a9 9 0 1 1-3-6.7"/>
              <polyline points="21 4 21 9 16 9"/>
            </svg>
            再来一次
          </button>
          <button class="btn-secondary" @click="doDownload()">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M12 3v12"/>
              <polyline points="6 9 12 15 16 9"/>
              <path d="M4 19h16"/>
            </svg>
            下载视频
          </button>
          <button class="btn-secondary" title="分享">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <circle cx="18" cy="5" r="3"/>
              <circle cx="6" cy="12" r="3"/>
              <circle cx="18" cy="19" r="3"/>
              <line x1="8.6" y1="10.6" x2="15.4" y2="5.4"/>
              <line x1="8.6" y1="13.4" x2="15.4" y2="18.6"/>
            </svg>
            分享
          </button>
        </div>

        <div class="result-card__prompt">
          <div class="result-card__prompt-label">Prompt</div>
          <div class="result-card__prompt-text">{{ store.currentPrompt }}</div>
        </div>

        <div class="result-card__meta">
          <span>1280×720</span>
          <span>4 秒</span>
          <span>{{ store.cloudMode === 'cloud' ? '云端加速' : '本地GPU' }}</span>
          <span>{{ new Date().toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }}</span>
        </div>
      </div>
    </div>

    <!-- ============= 状态 4：失败 ============= -->
    <div v-else-if="store.state === 'failed'" class="t2v-error">
      <div class="progress-container">
        <p class="progress-container__text" style="color: var(--error)">生成失败</p>
        <p class="progress-container__subtext">{{ store.error }}</p>
        <button class="btn-primary" @click="doGenerate()">重试</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.t2v-view {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.t2v-input {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 900px;
}

.t2v-subtitle {
  color: var(--text-secondary);
  font-size: 16px;
  font-weight: 400;
  text-align: center;
  margin-bottom: 20px;
}

.t2v-focus-hint {
  color: var(--text-tertiary);
  font-size: 13px;
  text-align: center;
  margin-top: 8px;
}

.inspiration-words {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  justify-content: center;
  max-width: 900px;
}

.t2v-btn-wrap {
  margin-top: 24px;
  display: flex;
  justify-content: center;
}

.t2v-footer {
  margin-top: 20px;
  color: var(--text-tertiary);
  font-size: 12px;
  text-align: center;
}
.t2v-footer__sep {
  margin: 0 12px;
  color: var(--text-disabled);
}

.t2v-progress,
.t2v-result,
.t2v-error {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
}

.result-card__video-inner {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-md);
  object-fit: cover;
}
.result-card__video-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--glass-1);
  color: var(--text-tertiary);
  font-size: 14px;
  border-radius: var(--radius-md);
}
</style>