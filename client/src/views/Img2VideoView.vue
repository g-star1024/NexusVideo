<!--
 * Img2VideoView.vue — 模式二：图生视频
 * ============================================================
 * 来源：苏璃光高保真规格书 第三章
 * 布局（主内容区 x=200,y=40 为相对原点）：
 *   标题 y=80
 *   副标题 y=116
 *   上传区 y=168 (400×300)
 *   描述框 y=496 (500×80)
 *   滑块  y=596 (320×6px 轨道)
 *   生成按钮 y=680
 * ============================================================
 * 交互要点：
 *   - 拖拽上传 400×300 区域
 *   - 运动描述 textarea 高 80px
 *   - 运动强度 1-10 滑块（前端值，后端映射 denoising_strength = value/10）
 *   - 生成按钮无图禁用
-->
<script setup lang="ts">
import { ref, watch, computed, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { useGenerateStore } from '../stores/generate';
import { useProgress, formatRemainingText } from '../composables/useProgress';

const store = useGenerateStore();
const progress = useProgress();
const router = useRouter();

function goToSettings() {
  router.push('/settings');
}

const promptText = ref('');
const motionStrength = ref(5); // 1-10，前端值
const dragging = ref(false);
const file = ref<File | null>(null);
const previewUrl = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

// 首屏引导
const titleEntered = ref(false);
const uploadEntered = ref(false);
const formEntered = ref(false);
onMountedOnce();
function onMountedOnce() {
  setTimeout(() => { titleEntered.value = true; }, 400);
  setTimeout(() => { uploadEntered.value = true; }, 800);
  setTimeout(() => { formEntered.value = true; }, 1200);
}

const canGenerate = computed(() => file.value !== null && promptText.value.trim().length > 0);

// ---- 监听生成状态 ----
watch(() => store.state, (ns) => {
  if (ns === 'generating') progress.startCycle();
  else { progress.stopCycle(); progress.reset(); }
});
// progress 由 useProgress 内部通过 WebSocket 自动更新，无需额外 watch

const estimatedText = computed(() => formatRemainingText(progress.remainingSeconds.value ?? null));

// ---- 拖拽事件 ----
function onDragOver(e: DragEvent) {
  e.preventDefault();
  dragging.value = true;
}
function onDragLeave() { dragging.value = false; }
function onDrop(e: DragEvent) {
  e.preventDefault();
  dragging.value = false;
  const f = e.dataTransfer?.files?.[0];
  if (f && acceptFile(f)) handleFile(f);
}
function onFileInput(e: Event) {
  const f = (e.target as HTMLInputElement).files?.[0];
  if (f && acceptFile(f)) handleFile(f);
}
function acceptFile(f: File): boolean {
  return ['image/png', 'image/jpeg', 'image/webp'].includes(f.type);
}
function handleFile(f: File) {
  file.value = f;
  previewUrl.value = URL.createObjectURL(f);
}
function replaceFile() {
  fileInput.value?.click();
}
function clearFile() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  file.value = null;
  previewUrl.value = null;
}

// ---- 滑块交互 ----
const trackRef = ref<HTMLDivElement | null>(null);
const isDragging = ref(false);

function setStrengthFromEvent(e: MouseEvent) {
  if (!trackRef.value) return;
  const rect = trackRef.value.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  motionStrength.value = Math.round(pct * 9 + 1);
}
function onTrackClick(e: MouseEvent) {
  setStrengthFromEvent(e);
}

function trackWidth(): string {
  const pct = (motionStrength.value - 1) / 9;
  return `${pct * 100}%`;
}
function thumbLeft(): string {
  const pct = (motionStrength.value - 1) / 9;
  return `${pct * 100}%`;
}

function onMouseDown(e: MouseEvent) {
  e.preventDefault();
  isDragging.value = true;
  const onMove = (ev: MouseEvent) => setStrengthFromEvent(ev);
  const onUp = () => {
    isDragging.value = false;
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
  };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
}

// ---- 生成 ----
async function doGenerate() {
  if (!canGenerate.value || !file.value) return;
  await store.startGeneration({
    prompt: promptText.value.trim(),
    mode: 'i2v',
    imagePath: file.value.name, // 实际路径由 Tauri 处理
    motionStrength: motionStrength.value,
  });
}
function doCancel() { store.cancelGeneration(); }

function openVideoUrl(url: string | null) {
  if (!url) return;
  const w: Window | undefined = typeof window !== 'undefined' ? window : undefined;
  w?.open(url, '_blank');
}

onUnmounted(() => progress.stopCycle());
</script>

<template>
  <div class="i2v-view">
    <!-- ============= 输入态 ============= -->
    <div v-if="store.state === 'idle'" class="i2v-input">
      <!-- 标题 -->
      <h1
        class="i2v-title"
        :class="{ 'btn-enter-fade': true, 'entered': titleEntered }"
      >让图片动起来</h1>
      <p
        class="i2v-subtitle"
        :class="{ 'btn-enter-fade': true, 'entered': titleEntered }"
      >上传一张图片，描述你想要的运动效果</p>

      <!-- 上传区 -->
      <div
        class="upload-zone"
        :class="{ dragover: dragging, 'has-file': !!file, 'btn-enter-fade': true, 'entered': uploadEntered }"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
        @click="() => fileInput?.click()"
      >
        <input
          ref="fileInput"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          style="display: none"
          @change="onFileInput"
        />

        <img
          v-if="previewUrl"
          :src="previewUrl"
          class="upload-zone__preview"
          alt="预览"
        />
        <button
          v-if="file"
          class="upload-zone__replace-btn"
          @click.stop="clearFile()"
          title="更换图片"
        >×</button>

        <template v-else>
          <svg class="upload-zone__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
            <rect x="3" y="3" width="18" height="18" rx="2"/>
            <circle cx="8.5" cy="8.5" r="1.5"/>
            <polyline points="3 17 9 11 13 15 21 7"/>
          </svg>
          <span class="upload-zone__title">拖拽图片到此处</span>
          <span class="upload-zone__subtitle">或点击选择文件 · 支持 PNG / JPG / WEBP</span>
        </template>
      </div>

      <!-- 描述框 -->
      <div
        class="i2v-form"
        :class="{ 'btn-enter-fade': true, 'entered': formEntered }"
      >
        <textarea
          v-model="promptText"
          class="i2v-textarea"
          placeholder="描述你想要的运动效果…"
        ></textarea>

        <!-- 运动强度滑块 -->
        <div class="motion-slider">
          <div class="motion-slider__label">
            <span class="motion-slider__title">运动强度</span>
            <span class="motion-slider__value">{{ motionStrength }} / 10</span>
          </div>
          <div
            class="motion-slider__track"
            ref="trackRef"
            @click="onTrackClick"
          >
            <div
              class="motion-slider__track-selected"
              :style="{ width: trackWidth() }"
            ></div>
            <div
              class="motion-slider__thumb"
              :class="{ dragging: isDragging }"
              :style="{ left: thumbLeft() }"
              @mousedown="onMouseDown"
            ></div>
          </div>
          <div class="motion-slider__label-row">
            <span class="motion-slider__label-left">温柔</span>
            <span class="motion-slider__label-right">激烈</span>
          </div>
        </div>

        <!-- 生成按钮 -->
        <div class="i2v-btn-wrap">
          <button
            class="btn-primary"
            :disabled="!canGenerate"
            @click="doGenerate()"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M14.5 2.5l-2 4-4-1 1-4-5 9 4 1-1 4 4-1 2 4 5-9-4-1 1-4-4 1"/>
            </svg>
            开始生成
          </button>
        </div>
      </div>
    </div>

    <!-- ============= 进度 / 完成态 ============= -->
    <div v-else-if="store.state === 'generating'" class="i2v-progress">
      <div class="progress-container">
        <p
          class="progress-container__text progress-text--breathing progress-text-fade"
          :class="{ 'out': progress.isCrossfading }"
        >{{ progress.currentText }}</p>
        <div class="progress-bar-real">
          <div class="progress-bar-real__fill" :style="{ width: `${progress.currentProgress}%` }"></div>
        </div>
        <p class="progress-container__subtext">{{ estimatedText }}</p>
        <button class="progress-container__cancel-btn" @click="doCancel()">取消生成</button>
      </div>
    </div>

    <div v-else-if="store.state === 'completed'" class="i2v-result">
      <div class="result-card result-card-enter entered">
        <div class="result-card__video">
          <video
            v-if="store.currentResultUrl"
            :src="store.currentResultUrl"
            controls autoplay loop muted
            class="result-card__video-inner"
          ></video>
          <div v-else class="result-card__video-placeholder"><span>视频生成完成</span></div>
        </div>
        <div class="result-card__actions">
          <button class="btn-primary" @click="doGenerate()">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <path d="M21 12a9 9 0 1 1-3-6.7"/>
              <polyline points="21 4 21 9 16 9"/>
            </svg>
            再来一次
          </button>
          <button class="btn-secondary" @click="() => openVideoUrl(store.currentResultUrl)">下载视频</button>
        </div>
        <div class="result-card__prompt">
          <div class="result-card__prompt-label">Motion Prompt</div>
          <div class="result-card__prompt-text">{{ store.currentPrompt }} · 强度 {{ motionStrength }}/10</div>
        </div>
        <div class="result-card__meta">
          <span>1280×720</span><span>4 秒</span>
          <span>{{ store.cloudMode === 'cloud' ? '云端加速' : '本地GPU' }}</span>
        </div>
      </div>
    </div>

    <div v-else-if="store.state === 'failed'" class="i2v-error">
      <div class="progress-container">
        <p class="progress-container__text" style="color: var(--error)">生成失败</p>
        <p class="progress-container__subtext">{{ store.error }}</p>

        <div v-if="store.engineNotReady" class="engine-not-ready-banner">
          <p class="engine-not-ready-banner__title">🔧 推理引擎未就绪</p>
          <p class="engine-not-ready-banner__sub">ComfyUI 未运行或缺少模型，请先完成环境准备</p>
          <button class="btn-primary engine-not-ready-banner__btn" @click="goToSettings()">前往设置中心</button>
        </div>

        <button class="btn-primary" @click="doGenerate()">重试</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.i2v-view {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}
.i2v-input {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 888px;
}
.i2v-title {
  color: var(--text-primary);
  font-size: var(--text-h1);
  font-weight: 600;
  text-align: center;
  margin-bottom: 12px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 300ms var(--ease-out-expo), transform 300ms var(--ease-out-expo);
}
.i2v-title.entered { opacity: 1; transform: translateY(0); }
.i2v-subtitle {
  color: var(--text-secondary);
  font-size: 14px;
  text-align: center;
  margin-bottom: 28px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 300ms var(--ease-out-expo), transform 300ms var(--ease-out-expo);
}
.i2v-subtitle.entered { opacity: 1; transform: translateY(0); }

.upload-zone { margin-bottom: 28px; opacity: 0; transform: translateY(8px); transition: opacity 400ms var(--ease-out-expo), transform 400ms var(--ease-out-expo); }
.upload-zone.entered { opacity: 1; transform: translateY(0); }

.i2v-form {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 400ms var(--ease-out-expo), transform 400ms var(--ease-out-expo);
}
.i2v-form.entered { opacity: 1; transform: translateY(0); }

.i2v-textarea {
  width: 500px;
  height: 80px;
  padding: 16px 20px;
  border-radius: var(--radius-md);
  background: var(--glass-1);
  -webkit-backdrop-filter: var(--glass-blur-1);
  backdrop-filter: var(--glass-blur-1);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: 16px;
  resize: none;
  outline: none;
  transition:
    border-color var(--dur-hover) var(--ease-in-out-cubic),
    box-shadow var(--dur-hover) var(--ease-in-out-cubic);
}
.i2v-textarea::placeholder { color: var(--text-tertiary); font-size: 16px; }
.i2v-textarea:focus { border: 1px solid var(--brand-blue); box-shadow: var(--shadow-glow); }

.i2v-btn-wrap { margin-top: 4px; }
.i2v-progress,
.i2v-result,
.i2v-error {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
}
.result-card__video-inner { width: 100%; height: 100%; border-radius: var(--radius-md); object-fit: cover; }
.result-card__video-placeholder {
  width: 100%; height: 100%;
  display: flex; align-items: center; justify-content: center;
  background: var(--glass-1); color: var(--text-tertiary);
  font-size: 14px; border-radius: var(--radius-md);
}

/* ---- engineNotReady 横幅（推理引擎未就绪） ---- */
.engine-not-ready-banner {
  margin: 14px auto 4px;
  max-width: 480px;
  padding: 14px 18px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  background: var(--glass-1);
  -webkit-backdrop-filter: var(--glass-blur-1);
  backdrop-filter: var(--glass-blur-1);
  border: 1px solid rgba(245, 158, 11, 0.42);
  border-radius: var(--radius-md);
  box-shadow: 0 0 18px rgba(245, 158, 11, 0.10), inset 0 0 12px rgba(245, 158, 11, 0.06);
}
.engine-not-ready-banner__title {
  margin: 0;
  font-size: 14px;
  font-weight: 500;
  color: #f59e0b;
}
.engine-not-ready-banner__sub {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
  text-align: center;
}
.engine-not-ready-banner__btn {
  margin-top: 4px;
  padding: 8px 20px;
  font-size: 13px;
}
</style>