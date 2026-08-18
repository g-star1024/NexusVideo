<!--
 * StyleTransferView.vue — 模式三：视频风格化
 * ============================================================
 * 来源：苏璃光高保真规格书 第四章
 * 布局（主内容区 x=200,y=40 为相对原点）：
 *   标题 y=80
 *   副标题 y=116
 *   视频上传区 y=168 (400×300)
 *   风格卡片区 y=504 (三张 160×200 卡片，gap 16)
 *   生成按钮 y=720
 * ============================================================
 * 交互要点：
 *   - 视频上传 400×300（支持 MP4/MOV）
 *   - 3 张风格卡：油画 / 3D卡通 / 水墨画
 *   - 选中态：border --brand-blue (2px) + 品牌阴影 + 右上角勾选圆点
 *   - 生成按钮：未选风格或无视频时禁用
-->
<script setup lang="ts">
import { ref, watch, computed, onUnmounted } from 'vue';
import { useGenerateStore } from '../stores/generate';
import { useProgress, formatRemainingText } from '../composables/useProgress';

const store = useGenerateStore();
const progress = useProgress();

const videoFile = ref<File | null>(null);
const previewUrl = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);
const dragging = ref(false);
const selectedStyle = ref<string | null>(null);

const titleEntered = ref(false);
const uploadEntered = ref(false);
const stylesEntered = ref(false);
onMountedOnce();
function onMountedOnce() {
  setTimeout(() => { titleEntered.value = true; }, 400);
  setTimeout(() => { uploadEntered.value = true; }, 800);
  setTimeout(() => { stylesEntered.value = true; }, 1200);
}

const canGenerate = computed(() => videoFile.value !== null && selectedStyle.value !== null);

// ---- 风格数据 ----
const styles = [
  { id: 'oil',      name: '油画',     desc: '艺术纹理',   gradient: 'linear-gradient(135deg, #FF6B6B 0%, #FFA07A 100%)' },
  { id: '3d',       name: '3D 卡通', desc: '立体建模',   gradient: 'linear-gradient(135deg, #6BCB77 0%, #4D96FF 100%)' },
  { id: 'ink',      name: '水墨画',   desc: '东方笔触',   gradient: 'linear-gradient(135deg, #2C2C2C 0%, #4A4A6A 100%)' },
];

// ---- 监听 ----
watch(() => store.state, (ns) => {
  if (ns === 'generating') progress.startCycle();
  else { progress.stopCycle(); progress.reset(); }
});
// progress 由 useProgress 内部通过 WebSocket 自动更新，无需额外 watch

const estimatedText = computed(() => formatRemainingText(progress.remainingSeconds.value ?? null));

// ---- 文件处理 ----
function onDragOver(e: DragEvent) { e.preventDefault(); dragging.value = true; }
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
  return ['video/mp4', 'video/quicktime', 'video/x-msvideo'].includes(f.type);
}
function handleFile(f: File) {
  videoFile.value = f;
  previewUrl.value = URL.createObjectURL(f);
}
function clearFile() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  videoFile.value = null;
  previewUrl.value = null;
}

// ---- 生成 ----
async function doGenerate() {
  if (!canGenerate.value || !videoFile.value || !selectedStyle.value) return;
  await store.startGeneration({
    prompt: `视频风格化：${selectedStyle.value}`,
    mode: 'v2v',
    imagePath: videoFile.value.name,
    style: selectedStyle.value,
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
  <div class="v2v-view">
    <!-- ============= 输入态 ============= -->
    <div v-if="store.state === 'idle'" class="v2v-input">
      <h1
        class="v2v-title"
        :class="{ 'btn-enter-fade': true, 'entered': titleEntered }"
      >为你的视频换一种风格</h1>
      <p
        class="v2v-subtitle"
        :class="{ 'btn-enter-fade': true, 'entered': titleEntered }"
      >上传视频，选择风格，AI 为你重新演绎</p>

      <!-- 视频上传区 -->
      <div
        class="upload-zone"
        :class="{ dragover: dragging, 'has-file': !!videoFile, 'btn-enter-fade': true, 'entered': uploadEntered }"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
        @click="() => fileInput?.click()"
      >
        <input
          ref="fileInput"
          type="file"
          accept="video/mp4,video/quicktime,video/x-msvideo"
          style="display: none"
          @change="onFileInput"
        />

        <template v-if="!previewUrl">
          <svg class="upload-zone__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round">
            <rect x="3" y="5" width="18" height="14" rx="2"/>
            <polygon points="10,9 16,12 10,15" fill="currentColor" stroke="none"/>
          </svg>
          <span class="upload-zone__title">拖拽视频到此处</span>
          <span class="upload-zone__subtitle">支持 MP4 / MOV · 最长 60 秒</span>
        </template>
        <template v-else>
          <video
            :src="previewUrl"
            class="upload-zone__preview"
            muted
            loop
            autoplay
          ></video>
          <button class="upload-zone__replace-btn" @click.stop="clearFile()" title="更换视频">×</button>
        </template>
      </div>

      <!-- 风格卡片区 -->
      <div
        class="style-cards"
        :class="{ 'btn-enter-fade': true, 'entered': stylesEntered }"
      >
        <div
          v-for="s in styles"
          :key="s.id"
          class="style-card"
          :class="{ selected: selectedStyle === s.id }"
          @click="selectedStyle = s.id"
        >
          <div class="style-card__image" :style="{ background: s.gradient }"></div>
          <div class="style-card__label">
            <span class="style-card__name">{{ s.name }}</span>
            <span class="style-card__desc">{{ s.desc }}</span>
          </div>
          <span class="style-card__check">✓</span>
        </div>
      </div>

      <!-- 生成按钮 -->
      <div class="v2v-btn-wrap">
        <button
          class="btn-primary"
          :disabled="!canGenerate"
          @click="doGenerate()"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M14.5 2.5l-2 4-4-1 1-4-5 9 4 1-1 4 4-1 2 4 5-9-4-1 1-4-4 1"/>
          </svg>
          开始风格化
        </button>
      </div>
    </div>

    <!-- ============= 进度/完成/失败态 ============= -->
    <div v-else-if="store.state === 'generating'" class="v2v-progress">
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

    <div v-else-if="store.state === 'completed'" class="v2v-result">
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
          <button class="btn-primary" @click="doGenerate()">再来一次</button>
          <button class="btn-secondary" @click="() => openVideoUrl(store.currentResultUrl)">下载视频</button>
        </div>
        <div class="result-card__prompt">
          <div class="result-card__prompt-label">Style</div>
          <div class="result-card__prompt-text">{{ selectedStyle }}</div>
        </div>
        <div class="result-card__meta">
          <span>1280×720</span><span>4 秒</span>
          <span>{{ store.cloudMode === 'cloud' ? '云端加速' : '本地GPU' }}</span>
        </div>
      </div>
    </div>

    <div v-else-if="store.state === 'failed'" class="v2v-error">
      <div class="progress-container">
        <p class="progress-container__text" style="color: var(--error)">生成失败</p>
        <p class="progress-container__subtext">{{ store.error }}</p>
        <button class="btn-primary" @click="doGenerate()">重试</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.v2v-view {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}
.v2v-input {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 888px;
}
.v2v-title {
  color: var(--text-primary);
  font-size: var(--text-h1);
  font-weight: 600;
  text-align: center;
  margin-bottom: 12px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 300ms var(--ease-out-expo), transform 300ms var(--ease-out-expo);
}
.v2v-title.entered { opacity: 1; transform: translateY(0); }
.v2v-subtitle {
  color: var(--text-secondary);
  font-size: 14px;
  text-align: center;
  margin-bottom: 28px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 300ms var(--ease-out-expo), transform 300ms var(--ease-out-expo);
}
.v2v-subtitle.entered { opacity: 1; transform: translateY(0); }

.upload-zone {
  margin-bottom: 24px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 400ms var(--ease-out-expo), transform 400ms var(--ease-out-expo);
}
.upload-zone.entered { opacity: 1; transform: translateY(0); }

.style-cards {
  display: flex;
  gap: 16px;
  justify-content: center;
  margin-bottom: 24px;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity 400ms var(--ease-out-expo), transform 400ms var(--ease-out-expo);
}
.style-cards.entered { opacity: 1; transform: translateY(0); }

.v2v-btn-wrap { margin-top: 4px; }

.v2v-progress,
.v2v-result,
.v2v-error {
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
</style>