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
import { ref, watch, onMounted, computed } from 'vue';
import { useGenerateStore } from '../stores/generate';
import { useProgress, formatRemainingText } from '../composables/useProgress';
import {
  PRESET_PROMPTS,
  CATEGORY_LABEL,
  type PresetPrompt,
} from '../data/preset-prompts';

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

// ---- 推荐场景 ----
const presetVisible = ref(6); // 默认展示前 6 个
const isAllVisible = ref(false);

const visiblePresets = computed(() =>
  PRESET_PROMPTS.slice(0, presetVisible.value),
);

function togglePresetExpand() {
  isAllVisible.value = !isAllVisible.value;
  presetVisible.value = isAllVisible.value ? PRESET_PROMPTS.length : 6;
}

let presetPulseTarget: HTMLButtonElement | null = null;
let presetPulseTimer: ReturnType<typeof setTimeout> | null = null;

function triggerPresetPulse(el: EventTarget | null) {
  if (presetPulseTimer) clearTimeout(presetPulseTimer);
  if (presetPulseTarget) {
    presetPulseTarget.classList.remove('preset-card--pulse');
  }
  if (el instanceof HTMLButtonElement) {
    presetPulseTarget = el;
    el.classList.add('preset-card--pulse');
    presetPulseTimer = setTimeout(() => {
      presetPulseTarget?.classList.remove('preset-card--pulse');
      presetPulseTarget = null;
    }, 700);
  }
}

function onPresetClick(preset: PresetPrompt, el: EventTarget | null) {
  promptText.value = preset.prompt;
  selectedWords.value = [];
  triggerPresetPulse(el);
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

// ---- 高级设置折叠面板 ----
const advancedOpen = ref(false);
const advancedResolution = ref<'480p' | '720p'>('480p');
const advancedDuration = ref<'3s' | '5s' | '8s'>('5s');

function toggleAdvanced() {
  advancedOpen.value = !advancedOpen.value;
}
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

      <!-- 推荐场景区域 -->
      <div class="preset-scenes">
        <div class="preset-scenes__header">
          <span class="preset-scenes__title">🎬 推荐场景</span>
          <button
            v-if="!isAllVisible"
            class="preset-scenes__more"
            @click="togglePresetExpand()"
          >展开全部 ▾</button>
          <button
            v-else
            class="preset-scenes__more preset-scenes__more--collapse"
            @click="togglePresetExpand()"
          >收起 ▴</button>
        </div>

        <div class="preset-scenes__grid">
          <button
            v-for="preset in visiblePresets"
            :key="preset.id"
            class="preset-card"
            :class="[`preset-card--${preset.category}`]"
            @click="onPresetClick(preset, $event.currentTarget)"
          >
            <span class="preset-card__icon">{{ preset.icon }}</span>
            <span class="preset-card__title">{{ preset.title }}</span>
            <span class="preset-card__tag">{{ CATEGORY_LABEL[preset.category] }}</span>
          </button>
        </div>
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

        <button
          class="btn-advanced-toggle"
          :class="{ open: advancedOpen }"
          @click="toggleAdvanced()"
          title="高级设置"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
          高级设置
        </button>
      </div>

      <!-- 高级设置面板 -->
      <div class="advanced-panel" :class="{ open: advancedOpen }">
        <div class="advanced-panel__row">
          <span class="advanced-panel__label">分辨率</span>
          <div class="advanced-panel__group">
            <button
              class="advanced-radio"
              :class="{ active: advancedResolution === '480p' }"
              @click="advancedResolution = '480p'"
            >
              480p
              <span class="advanced-radio__hint">默认 · 快速</span>
            </button>
            <button
              class="advanced-radio"
              :class="{ active: advancedResolution === '720p' }"
              @click="advancedResolution = '720p'"
            >
              720p
              <span class="advanced-radio__hint">12GB+ GPU</span>
            </button>
          </div>
        </div>
        <div class="advanced-panel__row">
          <span class="advanced-panel__label">时长</span>
          <div class="advanced-panel__group">
            <button
              class="advanced-radio"
              :class="{ active: advancedDuration === '3s' }"
              @click="advancedDuration = '3s'"
            >
              3 秒
              <span class="advanced-radio__hint">最快</span>
            </button>
            <button
              class="advanced-radio"
              :class="{ active: advancedDuration === '5s' }"
              @click="advancedDuration = '5s'"
            >
              5 秒
              <span class="advanced-radio__hint">默认</span>
            </button>
            <button
              class="advanced-radio"
              :class="{ active: advancedDuration === '8s' }"
              @click="advancedDuration = '8s'"
            >
              8 秒
              <span class="advanced-radio__hint">更慢</span>
            </button>
          </div>
        </div>
        <p class="advanced-panel__note">
          * MVP 阶段使用固定参数，V2 将接入后端
        </p>
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
          <span>832×480</span>
          <span>5 秒</span>
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

/* ---- 推荐场景区域 ---- */
.preset-scenes {
  width: 100%;
  max-width: 900px;
  margin-top: 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.preset-scenes__header {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  margin-bottom: 12px;
  width: 100%;
}

.preset-scenes__title {
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.5px;
}

.preset-scenes__more {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
  transition: all 0.2s;
}
.preset-scenes__more:hover {
  border-color: var(--accent-1);
  color: var(--accent-1);
}
.preset-scenes__more--collapse {
  color: var(--accent-1);
  border-color: var(--accent-1);
  opacity: 0.7;
}

.preset-scenes__grid {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
  width: 100%;
}

.preset-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  width: 112px;
  height: 72px;
  padding: 8px 6px;
  background: var(--glass-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  overflow: hidden;
  gap: 2px;
}
.preset-card:hover {
  transform: translateY(-2px);
  border-color: var(--accent-1);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
  background: var(--glass-2);
}

.preset-card--pulse {
  animation: presetPulse 0.7s ease-out;
}
@keyframes presetPulse {
  0% { box-shadow: 0 0 0 0 var(--accent-1); transform: scale(1); }
  30% { box-shadow: 0 0 0 8px rgba(138, 92, 246, 0.3); transform: scale(1.04); }
  100% { box-shadow: 0 0 0 0 rgba(138, 92, 246, 0); transform: scale(1); }
}

/* 分类主题色 */
.preset-card--landscape { border-top: 2px solid #4ade80; }
.preset-card--landscape:hover { border-color: #4ade80; box-shadow: 0 4px 16px rgba(74, 222, 128, 0.15); }

.preset-card--lifestyle { border-top: 2px solid #f59e0b; }
.preset-card--lifestyle:hover { border-color: #f59e0b; box-shadow: 0 4px 16px rgba(245, 158, 11, 0.15); }

.preset-card--product { border-top: 2px solid #60a5fa; }
.preset-card--product:hover { border-color: #60a5fa; box-shadow: 0 4px 16px rgba(96, 165, 250, 0.15); }

.preset-card--advertising { border-top: 2px solid #a78bfa; }
.preset-card--advertising:hover { border-color: #a78bfa; box-shadow: 0 4px 16px rgba(167, 139, 250, 0.15); }

.preset-card--education { border-top: 2px solid #22d3ee; }
.preset-card--education:hover { border-color: #22d3ee; box-shadow: 0 4px 16px rgba(34, 211, 238, 0.15); }

.preset-card--abstract { border-top: 2px solid #f472b6; }
.preset-card--abstract:hover { border-color: #f472b6; box-shadow: 0 4px 16px rgba(244, 114, 182, 0.15); }

.preset-card__icon {
  font-size: 20px;
  line-height: 1;
  margin-bottom: 2px;
}

.preset-card__title {
  font-size: 12px;
  color: var(--text-primary);
  font-weight: 500;
  text-align: center;
  line-height: 1.3;
  max-width: 90%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preset-card__tag {
  font-size: 9px;
  color: var(--text-tertiary);
  padding: 1px 6px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 8px;
  margin-top: 1px;
}

/* ---- 高级设置面板 ---- */
.btn-advanced-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 12px;
  padding: 10px 16px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  color: var(--text-tertiary);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}
.btn-advanced-toggle:hover {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}
.btn-advanced-toggle.open {
  border-color: var(--accent-1);
  color: var(--accent-1);
}
.btn-advanced-toggle svg {
  transition: transform 0.3s ease;
}
.btn-advanced-toggle.open svg {
  transform: rotate(180deg);
}

.advanced-panel {
  margin-top: 16px;
  padding: 0;
  max-height: 0;
  overflow: hidden;
  background: var(--glass-1);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  transition: max-height 0.35s cubic-bezier(0.4, 0, 0.2, 1),
              padding 0.35s cubic-bezier(0.4, 0, 0.2, 1),
              opacity 0.25s;
  opacity: 0;
  width: 100%;
  max-width: 700px;
}
.advanced-panel.open {
  max-height: 200px;
  padding: 20px 24px;
  opacity: 1;
}

.advanced-panel__row {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 0;
}
.advanced-panel__row + .advanced-panel__row {
  border-top: 1px solid var(--border);
}

.advanced-panel__label {
  width: 56px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 500;
}

.advanced-panel__group {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.advanced-radio {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
  font-family: inherit;
}
.advanced-radio:hover {
  border-color: var(--text-tertiary);
  color: var(--text-primary);
}
.advanced-radio.active {
  background: var(--accent-1);
  border-color: var(--accent-1);
  color: #fff;
}
.advanced-radio.active .advanced-radio__hint {
  color: rgba(255, 255, 255, 0.8);
}

.advanced-radio__hint {
  font-size: 10px;
  color: var(--text-disabled);
  margin-left: 2px;
}

.advanced-panel__note {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
  font-size: 10px;
  color: var(--text-disabled);
  text-align: center;
}
</style>