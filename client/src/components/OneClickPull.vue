<!--
 * OneClickPull.vue — 设置中心「一键拉取」卡片（M2）
 * ============================================================
 * 来源：顾如画（前端）+ 苏璃光毛玻璃深色设计系统 §4.6 / §7.3
 * 对接后端：POST /install/pull → SSE GET /install/progress → 终态
 *
 * 小白友好硬性要求：
 *   - 只展示后端下发的「人话」 message，绝不出现百分比/节点名/显存/路径
 *   - 进度用毛玻璃文案卡 + 流动不确定进度条（不写具体数字）
 *   - 失败 → 后端 message + 一键重试（断点续传）
 *   - 完成 → 「准备就绪」+「开始创作」跳生成页
 *   - 自检警告 → 列出缺失模型（文件名）+ 提示检查网络后重试
 *
 * 衔接 tauri-pull-ipc：目录选择经 api/install.ts 的 pickInstallDir()
 * / auditInstallDir() 拿到 DirAudit（含规范路径与风险等级）。
-->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useInstallStore } from '../stores/install';
import { pickInstallDir, auditInstallDir, type DirAudit } from '../api/install';

const store = useInstallStore();
const router = useRouter();

const devMode = import.meta.env.DEV;

// 目录输入双向绑定（直接写 store.targetDir，带 localStorage 持久化）
const dirModel = computed<string>({
  get: () => store.targetDir,
  set: (v: string) => store.setTargetDir(v),
});

// 当前目录体检结果（picker / 手输回显都用同一份）
const dirAudit = ref<DirAudit | null>(null);

// 缺失模型只展示文件名（basename），降低技术感
function basename(p: string): string {
  return p.split(/[\\/]/).pop() || p;
}

async function openPicker() {
  const a = await pickInstallDir(store.targetDir);
  if (a) {
    dirAudit.value = a;
    store.setTargetDir(a.path);
  }
}

// 手动输入：每次变更都向后端体检一遍，用于实时禁用/警示
function onDirInput(v: string) {
  auditInstallDir(v).then((a) => (dirAudit.value = a));
}

function goCreate() {
  // 跳转到生成页（一句话出片），由该页负责启动后端并出片
  router.push('/');
}

onMounted(() => store.ensureSubscription());
onUnmounted(() => store.closeProgress());
</script>

<template>
  <section class="pull-card">
    <!-- 卡片头部 -->
    <header class="pull-card__head">
      <div class="pull-card__icon">🚀</div>
      <div class="pull-card__titles">
        <h2 class="pull-card__title">一键拉取创作环境</h2>
        <p class="pull-card__sub">把推理引擎、模型一次性备好，拉完就能直接出片</p>
      </div>
    </header>

    <!-- 目标目录选择 -->
    <div class="pull-target">
      <div class="pull-target__row">
        <span class="pull-target__icon">📁</span>
        <input
          v-model="dirModel"
          class="pull-target__input"
          type="text"
          spellcheck="false"
          placeholder="选择安装位置，如 D:/nexusvideo_staging"
          aria-label="安装位置"
          @input="onDirInput(($event.target as HTMLInputElement).value)"
        />
        <button class="pull-btn pull-btn--ghost" @click="openPicker">
          选择目录
        </button>
      </div>
      <div v-if="dirAudit && dirAudit.warnings.length" class="pull-target__warn">
        <p v-for="w in dirAudit.warnings" :key="w" class="pull-target__warn-item">
          ⚠️ {{ w }}
        </p>
      </div>
      <p v-else class="pull-target__hint">
        默认装在 D:/nexusvideo_staging，文件较大请确保磁盘空间充足。
      </p>
    </div>

    <!-- 状态区 -->
    <div class="pull-state">
      <!-- idle：主操作 -->
      <button
        v-if="store.status === 'idle'"
        class="pull-btn pull-btn--primary pull-btn--lg"
        :disabled="dirAudit?.level === 'block'"
        @click="store.startPull()"
      >
        🚀 一键拉取
      </button>

      <!-- pulling：毛玻璃文案卡 + 流动进度条（不暴露数字） -->
      <div v-else-if="store.status === 'pulling'" class="pull-progress">
        <p class="pull-progress__text progress-text--breathing">
          {{ store.message || '正在准备运行环境…' }}
        </p>
        <!-- 复用设计系统 §4.6 不确定流动进度条 -->
        <div class="progress-bar-flow"></div>
        <p class="pull-progress__hint">这一步可能要几分钟，你可以先去喝口水 ☕</p>
      </div>

      <!-- failed：后端人话 + 重试 -->
      <div v-else-if="store.status === 'failed'" class="pull-result pull-result--failed">
        <div class="pull-result__badge">⚠️</div>
        <p class="pull-result__text">{{ store.message }}</p>
        <button
          class="pull-btn pull-btn--primary btn-retry--pulse"
          @click="store.retry()"
        >
          🔁 重试
        </button>
      </div>

      <!-- warning：缺失模型清单 + 重试 -->
      <div v-else-if="store.status === 'warning'" class="pull-result pull-result--warning">
        <div class="pull-result__badge">📦</div>
        <p class="pull-result__text">{{ store.message }}</p>
        <ul v-if="store.missing.length" class="pull-missing">
          <li v-for="m in store.missing" :key="m" class="pull-missing__item">
            {{ basename(m) }}
          </li>
        </ul>
        <p class="pull-result__hint">请检查网络后重试，缺失的模型会接着下载，已完成的不会重来。</p>
        <button
          class="pull-btn pull-btn--primary btn-retry--pulse"
          @click="store.retry()"
        >
          🔁 重试
        </button>
      </div>

      <!-- done：准备就绪 + 开始创作 -->
      <div v-else-if="store.status === 'done'" class="pull-result pull-result--done">
        <div class="pull-result__badge">✅</div>
        <p class="pull-result__text">准备就绪</p>
        <p class="pull-result__hint">创作环境已全部就位，去出你的第一个视频吧！</p>
        <button class="pull-btn pull-btn--primary pull-btn--lg" @click="goCreate">
          ✨ 开始创作
        </button>
      </div>
    </div>

    <!-- DEV：状态机自走 mock（无需真实下载即可验证 idle→pulling→done/failed/warning） -->
    <div v-if="devMode" class="pull-dev">
      <span class="pull-dev__label">DEV 模拟：</span>
      <button class="pull-dev__btn" @click="store.simulate('done')">done</button>
      <button class="pull-dev__btn" @click="store.simulate('failed')">failed</button>
      <button class="pull-dev__btn" @click="store.simulate('warning')">warning</button>
      <button class="pull-dev__btn" @click="store.reset()">reset</button>
    </div>
  </section>
</template>

<style scoped>
/* ====== 卡片容器（毛玻璃面板 L2） ====== */
.pull-card {
  position: relative;
  background: var(--glass-2);
  -webkit-backdrop-filter: var(--glass-blur-2);
  backdrop-filter: var(--glass-blur-2);
  border: var(--border-default);
  border-radius: var(--radius-lg);
  padding: 24px 28px;
  display: flex;
  flex-direction: column;
  gap: 20px;
  overflow: hidden;
}
/* 品牌光晕打底（毛玻璃下方需有可模糊内容，否则无效果） */
.pull-card::before {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 12% 0%, rgba(91, 108, 255, 0.10) 0%, transparent 45%),
    radial-gradient(circle at 92% 100%, rgba(177, 76, 255, 0.08) 0%, transparent 45%);
  pointer-events: none;
}

/* ====== 头部 ====== */
.pull-card__head {
  display: flex;
  align-items: center;
  gap: 14px;
  position: relative;
}
.pull-card__icon {
  width: 44px;
  height: 44px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  font-size: 22px;
  border-radius: var(--radius-md);
  background: var(--brand-gradient);
  box-shadow: var(--shadow-brand);
}
.pull-card__title {
  font-size: var(--text-h3);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  letter-spacing: var(--letter-spacing-heading);
  margin: 0;
}
.pull-card__sub {
  margin: 4px 0 0;
  font-size: var(--text-body-sm);
  color: var(--text-tertiary);
  line-height: var(--line-height-small);
}

/* ====== 目标目录 ====== */
.pull-target {
  display: flex;
  flex-direction: column;
  gap: 8px;
  position: relative;
}
.pull-target__row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pull-target__icon {
  font-size: 16px;
  flex-shrink: 0;
}
.pull-target__input {
  flex: 1;
  min-width: 0;
  height: 40px;
  padding: 0 14px;
  font-size: var(--text-body);
  font-family: var(--font-mono);
  color: var(--text-primary);
  background: rgba(255, 255, 255, 0.04);
  border: var(--border-default);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--dur-hover) var(--ease-in-out-cubic),
    box-shadow var(--dur-hover) var(--ease-in-out-cubic),
    background var(--dur-hover) var(--ease-in-out-cubic);
}
.pull-target__input::placeholder {
  color: var(--text-tertiary);
  font-family: var(--font-sans);
}
.pull-target__input:focus {
  border: var(--border-focus);
  box-shadow: var(--shadow-glow);
  background: rgba(255, 255, 255, 0.06);
}
.pull-target__hint {
  margin: 0;
  font-size: var(--text-caption);
  color: var(--text-tertiary);
  line-height: var(--line-height-small);
}
.pull-target__warn {
  margin: 0;
  font-size: var(--text-caption);
  color: var(--warning);
  background: var(--warning-bg);
  border: 1px solid rgba(255, 185, 56, 0.30);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  line-height: var(--line-height-small);
}
.pull-target__warn-item {
  margin: 0;
}
.pull-target__warn-item + .pull-target__warn-item {
  margin-top: 6px;
}

/* ====== 状态区 ====== */
.pull-state {
  min-height: 96px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

/* ====== 进度文案卡（§4.6） ====== */
.pull-progress {
  width: 100%;
  max-width: 480px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.pull-progress__text {
  font-size: var(--text-h3);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  margin: 0;
  line-height: var(--line-height-sub);
}
.pull-progress__hint {
  margin: 16px 0 0;
  font-size: var(--text-body-sm);
  color: var(--text-tertiary);
}

/* ====== 结果态（失败/警告/完成） ====== */
.pull-result {
  width: 100%;
  max-width: 480px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
}
.pull-result__badge {
  font-size: 32px;
  line-height: 1;
}
.pull-result__text {
  margin: 0;
  font-size: var(--text-h3);
  font-weight: var(--font-weight-semibold);
  line-height: var(--line-height-sub);
}
.pull-result--failed .pull-result__text { color: var(--warning); }
.pull-result--done .pull-result__text { color: var(--success); }
.pull-result--warning .pull-result__text { color: var(--text-primary); }
.pull-result__hint {
  margin: 0;
  font-size: var(--text-body-sm);
  color: var(--text-tertiary);
  line-height: var(--line-height-small);
}

/* 缺失模型清单（文件名 chip，不暴露路径） */
.pull-missing {
  list-style: none;
  margin: 4px 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.pull-missing__item {
  font-size: var(--text-caption);
  font-family: var(--font-mono);
  color: var(--text-secondary);
  background: var(--glass-1);
  border: var(--border-subtle);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
}

/* ====== 按钮（§4.1） ====== */
.pull-btn {
  height: 44px;
  padding: 0 24px;
  min-width: 96px;
  border-radius: var(--radius-md);
  border: none;
  font-size: var(--text-btn);
  font-weight: var(--font-weight-medium);
  letter-spacing: var(--letter-spacing-btn);
  font-family: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: transform var(--dur-fast) var(--ease-out-quad),
    box-shadow var(--dur-hover) var(--ease-in-out-cubic),
    background var(--dur-hover) var(--ease-in-out-cubic),
    opacity var(--dur-hover) var(--ease-in-out-cubic);
  user-select: none;
}
.pull-btn--lg {
  height: 48px;
  padding: 0 32px;
  font-size: var(--text-body);
}
.pull-btn--primary {
  background: var(--brand-gradient);
  color: var(--text-on-brand);
}
.pull-btn--primary:hover {
  background: var(--brand-gradient-hover);
  box-shadow: var(--shadow-brand);
  transform: translateY(-1px);
}
.pull-btn--primary:active {
  transform: scale(0.97);
}
.pull-btn--ghost {
  background: var(--glass-1);
  border: var(--border-default);
  color: var(--text-primary);
}
.pull-btn--ghost:hover {
  background: var(--hover-overlay);
  border-color: var(--border-strong);
}
.pull-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ====== DEV 模拟条 ====== */
.pull-dev {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-top: 12px;
  border-top: var(--divider);
  position: relative;
}
.pull-dev__label {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
}
.pull-dev__btn {
  height: 28px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  border: var(--border-subtle);
  background: var(--glass-1);
  color: var(--text-secondary);
  font-size: var(--text-caption);
  font-family: var(--font-mono);
  cursor: pointer;
}
.pull-dev__btn:hover {
  background: var(--hover-overlay);
  border-color: var(--border-strong);
}

/* ====== 响应式 ====== */
@media (max-width: 1024px) {
  .pull-target__row {
    flex-wrap: wrap;
  }
  .pull-target__input {
    width: 100%;
  }
}
</style>
