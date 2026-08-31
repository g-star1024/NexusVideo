<!--
 * SettingsView.vue — 设置中心
 * ============================================================
 * 来源：程流深（后端）+ 顾如画（前端）
 * 布局：
 *   ┌── 页面标题 ─────────────────────────────┐
 *   ├ 系统概况卡片（GPU / RAM / 版本 / 磁盘）  │
 *   ├ 组件状态列表（8 个组件）                │
 *   ├ 最近 ERROR 日志                         │
 *   └── 底部按钮 ─────────────────────────────┘
 *
 * 关键交互：
 *   - 进入页面自动拉取 components + system + logs
 *   - status=missing → 红色标签 + 操作按钮（启动 / 下载）
 *   - status=ok     → 绿色标签
 *   - 点击操作按钮 → loading 状态 → 完成后刷新
 *   - "一键刷新" → 重新拉取全部状态
 *
 * 毛玻璃深色主题：--glass-1 + backdrop-filter
 * 组件状态用 emoji + 中文标签（api/settings.ts 已封装）
-->
<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';
import {
  getComponents,
  getSystemInfo,
  getErrorLogs,
  executeComponentAction,
  refreshAll,
  statusLabel,
  statusEmoji,
  actionLabel,
  actionDanger,
  installComfyUI,
  getComfyUIInstallStatus,
  type ComponentStatusItem,
  type SystemInfo,
  type ErrorLog,
} from '../api/settings';

// ---------- 状态 ----------
const components = ref<ComponentStatusItem[]>([]);
const system = ref<SystemInfo | null>(null);
const logs = ref<ErrorLog[]>([]);
const loading = ref(true);
const refreshing = ref(false);
const actionInProgress = ref<Record<string, boolean>>({});
const error = ref<string | null>(null);

// 计算：是否有未就绪组件
const hasIssue = computed(() =>
  components.value.some(
    (c) => c.status === 'missing' || c.status === 'error'
  )
);

const issueCount = computed(() =>
  components.value.filter(
    (c) => c.status === 'missing' || c.status === 'error'
  ).length
);

// 显存感知：带 group 的组件视为「模型下载项」，单独成卡片区；其余留在通用状态列表
const modelItems = computed(() =>
  components.value.filter((c) => c.group !== undefined)
);
const otherComponents = computed(() =>
  components.value.filter((c) => c.group === undefined)
);
const recommendedModels = computed(() =>
  modelItems.value.filter((m) => m.group === 'recommended')
);
const advancedModels = computed(() =>
  modelItems.value.filter((m) => m.group === 'advanced')
);

// ---------- 页面进入时拉取 ----------
async function fetchAll() {
  loading.value = true;
  error.value = null;
  try {
    const [cs, sys, logsRaw] = await Promise.allSettled([
      getComponents(),
      getSystemInfo(),
      getErrorLogs(),
    ]);
    components.value = cs.status === 'fulfilled' ? cs.value : [];
    system.value = sys.status === 'fulfilled' ? sys.value : null;
    logs.value = logsRaw.status === 'fulfilled' ? logsRaw.value : [];
  } catch (e) {
    error.value = `获取设置信息失败: ${e}`;
  } finally {
    loading.value = false;
  }
}

// ---------- 一键刷新 ----------
async function handleRefresh() {
  refreshing.value = true;
  error.value = null;
  try {
    const all = await refreshAll();
    components.value = all.components;
    system.value = all.system;
    logs.value = all.logs;
  } catch (e) {
    error.value = `刷新失败: ${e}`;
  } finally {
    refreshing.value = false;
  }
}

// ---------- 执行组件操作（启动/安装/下载） ----------
async function handleAction(item: ComponentStatusItem) {
  if (!item.action || actionInProgress.value[item.id]) return;
  error.value = null;
  actionInProgress.value[item.id] = true;
  try {
    const res = await executeComponentAction(item.id, item.action);
    // 操作完成后延迟 1.2s 再拉取，给后端一点缓冲
    await new Promise((r) => setTimeout(r, 1200));
    await fetchAll();
    if (res.message) {
      console.log(`[设置] ${item.id}: ${res.message}`);
    }
  } catch (e) {
    error.value = `操作失败: ${e}`;
  } finally {
    actionInProgress.value[item.id] = false;
  }
}

// ---------- ComfyUI 一键拉取（专用安装流程） ----------
const installState = ref<{
  active: boolean;
  status: string;
  progress: number;
  stage: string;
  message: string;
}>({
  active: false,
  status: 'idle',
  progress: 0,
  stage: '',
  message: '',
});
let installTimer: ReturnType<typeof setInterval> | null = null;

function stopInstallPolling() {
  if (installTimer !== null) {
    clearInterval(installTimer);
    installTimer = null;
  }
}

async function startInstall() {
  if (installState.value.active) return;
  error.value = null;
  stopInstallPolling();
  // 初始态：克隆阶段
  installState.value = {
    active: true,
    status: 'cloning',
    progress: 0,
    stage: '正在触发拉取任务…',
    message: '',
  };
  // 1) 触发安装
  try {
    await installComfyUI();
  } catch (e) {
    stopInstallPolling();
    installState.value = {
      active: false,
      status: 'error',
      progress: 0,
      stage: '',
      message: `启动拉取失败: ${e}`,
    };
    return;
  }
  // 2) 轮询安装状态（每 2s）
  installTimer = setInterval(async () => {
    try {
      const st = await getComfyUIInstallStatus();
      if (st.status === 'done' || st.status === 'error') {
        stopInstallPolling();
        installState.value = {
          active: false,
          status: st.status,
          progress: st.status === 'done' ? 100 : st.progress,
          stage: st.stage,
          message: st.message,
        };
        if (st.status === 'done') {
          await fetchAll();
        }
      } else {
        installState.value = {
          active: true,
          status: st.status,
          progress: st.progress,
          stage: st.stage,
          message: st.message,
        };
      }
    } catch (e) {
      stopInstallPolling();
      installState.value = {
        active: false,
        status: 'error',
        progress: installState.value.progress,
        stage: '',
        message: `状态轮询失败: ${e}`,
      };
    }
  }, 2000);
}

// ComfyUI 安装完成后的「启动」操作（复用现有启动逻辑）
async function handleStartComfy() {
  error.value = null;
  actionInProgress.value['comfyui'] = true;
  try {
    const res = await executeComponentAction('comfyui', 'start');
    await new Promise((r) => setTimeout(r, 1200));
    await fetchAll();
    if (res.message) {
      console.log(`[设置] comfyui start: ${res.message}`);
    }
    // 启动成功后回到通用状态展示
    const comfy = components.value.find((c) => c.id === 'comfyui');
    if (comfy && comfy.status === 'ok') {
      installState.value = {
        active: false,
        status: 'idle',
        progress: 0,
        stage: '',
        message: '',
      };
    }
  } catch (e) {
    error.value = `启动失败: ${e}`;
  } finally {
    actionInProgress.value['comfyui'] = false;
  }
}

// ---------- 辅助函数 ----------
function statusRowClass(s: ComponentStatusItem['status']) {
  if (s === 'missing') return 'component-row--missing';
  if (s === 'error') return 'component-row--error';
  if (s === 'ok') return 'component-row--ok';
  return '';
}

function levelClass(l: ErrorLog['level']) {
  return l === 'ERROR' ? 'log-row--error' : 'log-row--warn';
}

// 显存需求：MB → GB（保留 1 位小数）
function vramGb(mb?: number): string {
  if (!mb || mb <= 0) return '0.0';
  return (mb / 1024).toFixed(1);
}

onMounted(fetchAll);
onUnmounted(stopInstallPolling);
</script>

<template>
  <div class="settings-view">
    <!-- 骨架 loading -->
    <div v-if="loading" class="settings-loading">
      <div class="loading-spinner"></div>
      <span>正在加载系统信息…</span>
    </div>

    <template v-else>
      <!-- 页面头部 -->
      <header class="settings-header">
        <div class="settings-header__left">
          <span class="settings-header__title">⚙️ 设置中心</span>
          <span class="settings-header__subtitle">检查并管理本地推理环境</span>
        </div>
        <div class="settings-header__right">
          <span
            v-if="hasIssue"
            class="settings-header__warn"
          >
            ⚠️ 发现 {{ issueCount }} 个问题
          </span>
          <button
            class="btn-refresh"
            :disabled="refreshing"
            @click="handleRefresh"
          >
            <svg
              width="14" height="14" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"
              :class="{ 'spin': refreshing }"
            >
              <path d="M21 12a9 9 0 1 1-6.2-8.6" />
              <polyline points="21 4 21 10 15 10" />
            </svg>
            <span>{{ refreshing ? '刷新中…' : '一键刷新' }}</span>
          </button>
        </div>
      </header>

      <div class="settings-body">
        <!-- ====== 系统概况卡片 ====== -->
        <section class="system-card" v-if="system">
          <h3 class="system-card__title">🖥️ 系统概况</h3>
          <div class="system-grid">
            <div class="system-item">
              <span class="system-item__label">操作系统</span>
              <span class="system-item__value">{{ system.os || '未知' }}</span>
            </div>
            <div class="system-item">
              <span class="system-item__label">GPU 型号</span>
              <span class="system-item__value">
                <span class="gpu-dot"></span>
                {{ system.gpu || '未知' }}
              </span>
            </div>
            <div class="system-item">
              <span class="system-item__label">显存</span>
              <span class="system-item__value">{{ system.vram || '-' }}</span>
            </div>
            <div class="system-item">
              <span class="system-item__label">CUDA</span>
              <span class="system-item__value">{{ system.cuda || '-' }}</span>
            </div>
            <div class="system-item">
              <span class="system-item__label">内存</span>
              <span class="system-item__value">
                {{ system.ram_used || '-' }}
                <span class="muted">/ {{ system.ram_total || '-' }}</span>
              </span>
            </div>
            <div class="system-item">
              <span class="system-item__label">磁盘剩余</span>
              <span class="system-item__value">{{ system.disk_free || '-' }}</span>
            </div>
            <div class="system-item">
              <span class="system-item__label">应用版本</span>
              <span class="system-item__value ver">{{ system.version || '-' }}</span>
            </div>
            <div class="system-item">
              <span class="system-item__label">后端地址</span>
              <span class="system-item__value">127.0.0.1:9881</span>
            </div>
          </div>
        </section>

        <!-- ====== 组件状态列表 ====== -->
        <section class="components-section">
          <div class="section-header">
            <h3 class="section-title">🧩 组件状态</h3>
            <span class="section-hint">
              {{ otherComponents.length }} 个组件
              <span v-if="hasIssue" class="section-hint--warn">（{{ issueCount }} 个需关注）</span>
            </span>
          </div>

          <div class="components-list">
            <div
              v-for="c in otherComponents"
              :key="c.id"
              class="component-row"
              :class="statusRowClass(c.status)"
            >
              <!-- 状态 -->
              <div class="component-row__status">
                <span class="component-row__emoji">{{ statusEmoji(c.status) }}</span>
                <span class="component-row__label">{{ statusLabel(c.status) }}</span>
              </div>

              <!-- 组件名 + 版本 + 大小 -->
              <div class="component-row__name">
                <span class="component-row__id">{{ c.id }}</span>
                <span class="component-row__ver" v-if="c.version">
                  v{{ c.version }}
                </span>
                <span class="component-row__size" v-if="c.size">
                  {{ c.size }}
                </span>
              </div>

              <!-- 详情 -->
              <div class="component-row__detail" v-if="c.detail">
                {{ c.detail }}
              </div>

              <!-- 进度条（下载中） -->
              <div
                v-if="c.progress !== undefined && c.progress >= 0 && c.action === 'download'"
                class="component-row__progress"
              >
                <div class="progress-bar">
                  <div
                    class="progress-bar__fill"
                    :style="{ width: `${c.progress}%` }"
                  />
                </div>
                <span class="progress-bar__text">{{ c.progress }}%</span>
              </div>

              <!-- 操作按钮 -->
              <div class="component-row__action">
                <!-- ComfyUI 专用：拉取中 -->
                <button
                  v-if="c.id === 'comfyui' && installState.active"
                  class="btn-action btn-action--loading"
                  disabled
                >
                  <svg
                    width="14" height="14" viewBox="0 0 24 24"
                    fill="none" stroke="currentColor" stroke-width="2"
                    stroke-linecap="round"
                    class="spin"
                  >
                    <circle cx="12" cy="12" r="9" stroke-opacity="0.3"/>
                    <path d="M12 3a9 9 0 0 1 9 9"/>
                  </svg>
                  <span>拉取中…</span>
                </button>

                <!-- ComfyUI 专用：完成 → 启动 -->
                <template v-else-if="c.id === 'comfyui' && installState.status === 'done'">
                  <span class="btn-action btn-action--ok-static">✅ 完成</span>
                  <button
                    class="btn-action btn-action--primary"
                    :disabled="actionInProgress['comfyui']"
                    @click="handleStartComfy"
                  >
                    <svg
                      v-if="actionInProgress['comfyui']"
                      width="14" height="14" viewBox="0 0 24 24"
                      fill="none" stroke="currentColor" stroke-width="2"
                      stroke-linecap="round"
                      class="spin"
                    >
                      <circle cx="12" cy="12" r="9" stroke-opacity="0.3"/>
                      <path d="M12 3a9 9 0 0 1 9 9"/>
                    </svg>
                    <span>{{ actionInProgress['comfyui'] ? '启动中…' : '启动' }}</span>
                  </button>
                </template>

                <!-- ComfyUI 专用：错误 → 重试 -->
                <button
                  v-else-if="c.id === 'comfyui' && installState.status === 'error'"
                  class="btn-action btn-action--danger"
                  @click="startInstall"
                >🔁 重试</button>

                <!-- ComfyUI 专用：未安装 → 一键拉取 -->
                <button
                  v-else-if="c.id === 'comfyui' && c.status === 'missing'"
                  class="btn-action btn-action--primary btn-action--primary-lg"
                  @click="startInstall"
                >🚀 一键拉取 ComfyUI</button>

                <!-- 通用操作按钮（其他组件 / ComfyUI 其它状态） -->
                <template v-else>
                  <button
                    v-if="c.action && c.status !== 'checking'"
                    class="btn-action"
                    :class="{
                      'btn-action--danger': actionDanger(c.action),
                      'btn-action--primary': c.action === 'start' && c.status === 'missing',
                      'btn-action--success': c.status === 'ok',
                      'btn-action--loading': actionInProgress[c.id],
                    }"
                    :disabled="actionInProgress[c.id]"
                    @click="handleAction(c)"
                  >
                    <svg
                      v-if="actionInProgress[c.id]"
                      width="14" height="14" viewBox="0 0 24 24"
                      fill="none" stroke="currentColor" stroke-width="2"
                      stroke-linecap="round"
                      class="spin"
                    >
                      <circle cx="12" cy="12" r="9" stroke-opacity="0.3"/>
                      <path d="M12 3a9 9 0 0 1 9 9"/>
                    </svg>
                    <span>{{
                      actionInProgress[c.id] ? '进行中…' : actionLabel(c.action)
                    }}</span>
                  </button>
                  <span v-if="c.status === 'ok' && !c.action" class="btn-action btn-action--ok-static">
                    ✅ 就绪
                  </span>
                  <span v-if="c.status === 'checking'" class="btn-action btn-action--checking">
                    ⏳ 检测中…
                  </span>
                </template>
              </div>

              <!-- ComfyUI 专用：安装进度面板 -->
              <div
                v-if="c.id === 'comfyui' && (installState.active || installState.status === 'done' || installState.status === 'error')"
                class="component-row__install"
              >
                <div class="install-progress" v-if="installState.status !== 'done'">
                  <div class="progress-bar">
                    <div
                      class="progress-bar__fill"
                      :class="{ 'progress-bar__fill--error': installState.status === 'error' }"
                      :style="{ width: `${installState.progress}%` }"
                    />
                  </div>
                  <span class="progress-bar__text">{{ installState.progress }}%</span>
                </div>
                <div
                  class="install-stage"
                  :class="{
                    'install-stage--error': installState.status === 'error',
                    'install-stage--done': installState.status === 'done',
                  }"
                >
                  <template v-if="installState.status === 'error'">❌ {{ installState.message || '安装失败，请点击重试' }}</template>
                  <template v-else-if="installState.status === 'done'">✅ ComfyUI 已安装完成，点击「启动」运行</template>
                  <template v-else>⏳ {{ installState.stage || installState.message || '正在准备…' }}</template>
                </div>
              </div>
            </div>

            <div v-if="otherComponents.length === 0" class="empty-state">
              <span class="empty-state__icon">🧩</span>
              <span class="empty-state__text">暂无组件数据，请刷新</span>
            </div>
          </div>
        </section>

        <!-- ====== 模型下载列表（显存感知） ====== -->
        <section class="models-section" v-if="modelItems.length > 0">
          <div class="section-header">
            <h3 class="section-title">📦 模型下载列表</h3>
            <span class="section-hint">
              按显存需求分组
              <span v-if="system" class="section-hint--vram">· 当前显存 {{ system.vram }}</span>
            </span>
          </div>

          <div class="models-cols">
            <!-- 推荐栏 -->
            <div class="models-col">
              <div class="models-col__head models-col__head--recommended">
                <span class="models-col__badge">✓ 推荐</span>
                <span class="models-col__sub">轻量模型，本机可流畅运行</span>
              </div>
              <div class="models-col__body">
                <article
                  v-for="m in recommendedModels"
                  :key="m.id"
                  class="model-card"
                  :class="{ 'model-card--warn': m.vram_warning }"
                >
                  <span v-if="m.recommended" class="model-card__rec-badge">✓ 推荐</span>

                  <div class="model-card__head">
                    <span class="model-card__name">{{ m.name || m.id }}</span>
                    <span class="model-card__ver" v-if="m.version">v{{ m.version }}</span>
                    <span class="model-card__size" v-if="m.size">{{ m.size }}</span>
                  </div>

                  <div class="model-card__vram">
                    <span class="model-card__vram-icon">🧠</span>
                    需 {{ vramGb(m.min_vram_mb) }} GB 显存
                  </div>

                  <!-- 下载进度条 -->
                  <div
                    v-if="m.progress !== undefined && m.progress >= 0 && m.action === 'download'"
                    class="model-card__progress"
                  >
                    <div class="progress-bar">
                      <div class="progress-bar__fill" :style="{ width: `${m.progress}%` }" />
                    </div>
                    <span class="progress-bar__text">{{ m.progress }}%</span>
                  </div>

                  <!-- 操作区（复用 .btn-action / handleAction，不破坏既有下载逻辑） -->
                  <div class="model-card__action">
                    <button
                      v-if="m.action === 'download' && m.status !== 'ok'"
                      class="btn-action btn-action--danger"
                      :class="{ 'btn-action--loading': actionInProgress[m.id] }"
                      :disabled="m.vram_warning || actionInProgress[m.id]"
                      @click="handleAction(m)"
                    >
                      <svg
                        v-if="actionInProgress[m.id]"
                        width="14" height="14" viewBox="0 0 24 24"
                        fill="none" stroke="currentColor" stroke-width="2"
                        stroke-linecap="round"
                        class="spin"
                      >
                        <circle cx="12" cy="12" r="9" stroke-opacity="0.3"/>
                        <path d="M12 3a9 9 0 0 1 9 9"/>
                      </svg>
                      <span>{{ actionInProgress[m.id] ? '下载中…' : (m.vram_warning ? '显存不足' : '下载') }}</span>
                    </button>
                    <span v-if="m.status === 'ok' && !m.action" class="btn-action btn-action--ok-static">
                      ✅ 就绪
                    </span>
                    <span v-if="m.status === 'checking'" class="btn-action btn-action--checking">
                      ⏳ 检测中…
                    </span>
                  </div>

                  <!-- 显存警告说明（红字） -->
                  <p v-if="m.vram_warning && m.vram_note" class="model-card__note">
                    ⚠️ {{ m.vram_note }}
                  </p>
                </article>
                <div v-if="recommendedModels.length === 0" class="models-col__empty">暂无推荐模型</div>
              </div>
            </div>

            <!-- 高级不推荐栏 -->
            <div class="models-col">
              <div class="models-col__head models-col__head--advanced">
                <span class="models-col__badge">⚠ 高级不推荐</span>
                <span class="models-col__sub">高显存需求，可能爆显存</span>
              </div>
              <div class="models-col__body">
                <article
                  v-for="m in advancedModels"
                  :key="m.id"
                  class="model-card"
                  :class="{ 'model-card--warn': m.vram_warning }"
                >
                  <span v-if="m.recommended" class="model-card__rec-badge">✓ 推荐</span>

                  <div class="model-card__head">
                    <span class="model-card__name">{{ m.name || m.id }}</span>
                    <span class="model-card__ver" v-if="m.version">v{{ m.version }}</span>
                    <span class="model-card__size" v-if="m.size">{{ m.size }}</span>
                  </div>

                  <div class="model-card__vram">
                    <span class="model-card__vram-icon">🧠</span>
                    需 {{ vramGb(m.min_vram_mb) }} GB 显存
                  </div>

                  <!-- 下载进度条 -->
                  <div
                    v-if="m.progress !== undefined && m.progress >= 0 && m.action === 'download'"
                    class="model-card__progress"
                  >
                    <div class="progress-bar">
                      <div class="progress-bar__fill" :style="{ width: `${m.progress}%` }" />
                    </div>
                    <span class="progress-bar__text">{{ m.progress }}%</span>
                  </div>

                  <!-- 操作区 -->
                  <div class="model-card__action">
                    <button
                      v-if="m.action === 'download' && m.status !== 'ok'"
                      class="btn-action btn-action--danger"
                      :class="{ 'btn-action--loading': actionInProgress[m.id] }"
                      :disabled="m.vram_warning || actionInProgress[m.id]"
                      @click="handleAction(m)"
                    >
                      <svg
                        v-if="actionInProgress[m.id]"
                        width="14" height="14" viewBox="0 0 24 24"
                        fill="none" stroke="currentColor" stroke-width="2"
                        stroke-linecap="round"
                        class="spin"
                      >
                        <circle cx="12" cy="12" r="9" stroke-opacity="0.3"/>
                        <path d="M12 3a9 9 0 0 1 9 9"/>
                      </svg>
                      <span>{{ actionInProgress[m.id] ? '下载中…' : (m.vram_warning ? '显存不足' : '下载') }}</span>
                    </button>
                    <span v-if="m.status === 'ok' && !m.action" class="btn-action btn-action--ok-static">
                      ✅ 就绪
                    </span>
                    <span v-if="m.status === 'checking'" class="btn-action btn-action--checking">
                      ⏳ 检测中…
                    </span>
                  </div>

                  <!-- 显存警告说明（红字） -->
                  <p v-if="m.vram_warning && m.vram_note" class="model-card__note">
                    ⚠️ {{ m.vram_note }}
                  </p>
                </article>
                <div v-if="advancedModels.length === 0" class="models-col__empty">暂无高级模型</div>
              </div>
            </div>
          </div>
        </section>

        <!-- ====== 错误日志 ====== -->
        <section class="logs-section" v-if="logs.length > 0">
          <div class="section-header">
            <h3 class="section-title">📋 最近错误日志</h3>
            <span class="section-hint">{{ logs.length }} 条</span>
          </div>

          <div class="logs-list">
            <div
              v-for="(log, idx) in logs"
              :key="idx"
              class="log-row"
              :class="levelClass(log.level)"
            >
              <span class="log-row__time">{{ log.time || log.timestamp }}</span>
              <span class="log-row__level">{{ log.level }}</span>
              <span class="log-row__source">{{ log.source }}</span>
              <span class="log-row__message">{{ log.message }}</span>
            </div>
          </div>
        </section>

        <!-- 全局错误提示 -->
        <div v-if="error" class="settings-error">
          <span>⚠️</span>
          <span>{{ error }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* ====== 页面容器 ====== */
.settings-view {
  width: 100%;
  height: 100%;
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

/* ====== Loading ====== */
.settings-loading {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  color: var(--text-tertiary);
  font-size: var(--text-body);
}
.loading-spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--border-subtle);
  border-top-color: var(--brand-blue);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
.spin {
  animation: spin 0.8s linear infinite;
}

/* ====== 页面头部 ====== */
.settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0 16px;
  border-bottom: var(--divider);
}
.settings-header__title {
  font-size: var(--text-h2);
  font-weight: var(--font-weight-semibold);
  color: var(--text-primary);
  letter-spacing: var(--letter-spacing-heading);
}
.settings-header__subtitle {
  margin-left: 12px;
  font-size: var(--text-body-sm);
  color: var(--text-tertiary);
}
.settings-header__warn {
  font-size: var(--text-body-sm);
  color: var(--warning);
  background: var(--warning-bg);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  margin-right: 12px;
}
.btn-refresh {
  height: 36px;
  padding: 0 16px;
  border-radius: var(--radius-sm);
  background: var(--glass-1);
  border: var(--border-default);
  color: var(--text-primary);
  font-size: var(--text-body-sm);
  font-weight: var(--font-weight-medium);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s ease;
  font-family: inherit;
}
.btn-refresh:hover:not(:disabled) {
  background: var(--hover-overlay);
  border-color: var(--border-strong);
}
.btn-refresh:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ====== 系统概况卡片 ====== */
.system-card {
  background: var(--glass-1);
  -webkit-backdrop-filter: var(--glass-blur-1);
  backdrop-filter: var(--glass-blur-1);
  border: var(--border-default);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
}
.system-card__title {
  font-size: var(--text-body);
  font-weight: var(--font-weight-medium);
  color: var(--text-secondary);
  margin-bottom: 12px;
}
.system-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px 20px;
}
.system-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 0;
  border-bottom: var(--divider);
}
.system-item__label {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
  letter-spacing: var(--letter-spacing-label);
  text-transform: uppercase;
}
.system-item__value {
  font-size: var(--text-body);
  color: var(--text-primary);
  font-weight: var(--font-weight-medium);
  display: flex;
  align-items: center;
  gap: 6px;
}
.system-item__value .muted {
  color: var(--text-tertiary);
  font-weight: var(--font-weight-regular);
}
.system-item__value.ver {
  font-family: var(--font-mono);
  color: var(--accent-cyan);
}
.gpu-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--success);
  box-shadow: 0 0 6px var(--success);
  flex-shrink: 0;
}

/* ====== 组件状态列表 ====== */
.components-section {
  background: var(--glass-1);
  -webkit-backdrop-filter: var(--glass-blur-1);
  backdrop-filter: var(--glass-blur-1);
  border: var(--border-default);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.section-title {
  font-size: var(--text-body);
  font-weight: var(--font-weight-medium);
  color: var(--text-secondary);
}
.section-hint {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
}
.section-hint--warn {
  color: var(--warning);
}

.components-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.component-row {
  display: grid;
  grid-template-columns: 100px 220px 1fr 160px 110px;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  border: var(--border-subtle);
  transition: all 0.15s ease;
}
.component-row:hover {
  background: var(--hover-overlay);
  border-color: var(--border-strong);
}
.component-row--missing {
  border-left: 3px solid var(--error);
}
.component-row--error {
  border-left: 3px solid var(--warning);
}
.component-row--ok {
  border-left: 3px solid var(--success);
}

.component-row__status {
  display: flex;
  align-items: center;
  gap: 6px;
}
.component-row__emoji {
  font-size: 14px;
}
.component-row__label {
  font-size: var(--text-body-sm);
  color: var(--text-secondary);
  font-weight: var(--font-weight-medium);
}
.component-row--missing .component-row__label { color: var(--error); }
.component-row--error .component-row__label { color: var(--warning); }
.component-row--ok .component-row__label { color: var(--success); }

.component-row__name {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.component-row__id {
  font-size: var(--text-body);
  color: var(--text-primary);
  font-weight: var(--font-weight-medium);
}
.component-row__ver {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  padding: 1px 6px;
  background: var(--glass-1);
  border-radius: 4px;
}
.component-row__size {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
}
.component-row__detail {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.component-row__progress {
  display: flex;
  align-items: center;
  gap: 8px;
  grid-column: 3 / 6;
  margin-top: 4px;
}
.progress-bar {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.progress-bar__fill {
  height: 100%;
  background: var(--brand-gradient);
  border-radius: 2px;
  transition: width 0.3s ease;
}
.progress-bar__text {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* ====== 操作按钮 ====== */
.component-row__action {
  display: flex;
  justify-content: flex-end;
}
.btn-action {
  height: 28px;
  padding: 0 12px;
  border-radius: var(--radius-sm);
  border: var(--border-subtle);
  background: var(--glass-1);
  color: var(--text-primary);
  font-size: var(--text-body-sm);
  font-weight: var(--font-weight-medium);
  font-family: inherit;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  transition: all 0.15s ease;
  user-select: none;
}
.btn-action:hover:not(:disabled) {
  background: var(--hover-overlay);
  border-color: var(--border-strong);
}
.btn-action:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-action--primary {
  background: var(--brand-gradient);
  border: none;
  color: var(--text-on-brand);
}
.btn-action--primary:hover:not(:disabled) {
  background: var(--brand-gradient-hover);
  box-shadow: var(--shadow-brand);
}
.btn-action--danger {
  color: var(--error);
  border-color: var(--border-error);
}
.btn-action--danger:hover:not(:disabled) {
  background: var(--error-bg);
}
.btn-action--success {
  color: var(--success);
  border-color: rgba(61, 214, 140, 0.3);
  background: var(--success-bg);
  cursor: default;
}
.btn-action--ok-static {
  cursor: default;
}
.btn-action--checking {
  color: var(--text-tertiary);
  cursor: default;
}
.btn-action--loading {
  color: var(--text-secondary);
}
.btn-action--primary-lg {
  height: 32px;
  padding: 0 18px;
  font-size: var(--text-body);
  box-shadow: var(--shadow-brand);
}
.btn-action--primary-lg:hover:not(:disabled) {
  box-shadow: 0 4px 16px rgba(99, 102, 241, 0.4);
}

/* ====== ComfyUI 安装进度面板 ====== */
.component-row__install {
  grid-column: 1 / -1;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 6px;
  padding: 10px 12px 4px;
  border-top: var(--divider);
}
.install-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}
.install-stage {
  font-size: var(--text-caption);
  color: var(--text-secondary);
  line-height: 1.5;
}
.install-stage--error {
  color: var(--error);
}
.install-stage--done {
  color: var(--success);
  font-weight: var(--font-weight-medium);
}
.progress-bar__fill--error {
  background: var(--error);
}

/* ====== 空状态 ====== */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px 12px;
  color: var(--text-tertiary);
}
.empty-state__icon { font-size: 28px; }
.empty-state__text { font-size: var(--text-body-sm); }

/* ====== 错误日志 ====== */
.logs-section {
  background: var(--glass-1);
  -webkit-backdrop-filter: var(--glass-blur-1);
  backdrop-filter: var(--glass-blur-1);
  border: var(--border-default);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
}
.logs-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-family: var(--font-mono);
}
.log-row {
  display: grid;
  grid-template-columns: 70px 56px 80px 1fr;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  font-size: var(--text-caption);
  color: var(--text-secondary);
  border-left: 2px solid transparent;
  transition: background 0.15s ease;
}
.log-row:hover {
  background: var(--hover-overlay);
}
.log-row--error {
  border-left-color: var(--error);
  background: rgba(255, 91, 91, 0.04);
}
.log-row--warn {
  border-left-color: var(--warning);
  background: rgba(255, 185, 56, 0.04);
}
.log-row__time { color: var(--text-tertiary); }
.log-row__level {
  font-weight: var(--font-weight-medium);
}
.log-row--error .log-row__level { color: var(--error); }
.log-row--warn .log-row__level { color: var(--warning); }
.log-row__source {
  color: var(--text-tertiary);
  padding: 1px 6px;
  background: var(--glass-1);
  border-radius: 3px;
  justify-self: start;
}
.log-row__message {
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ====== 全局错误提示 ====== */
.settings-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-radius: var(--radius-sm);
  background: var(--error-bg);
  border: var(--border-error);
  color: var(--error);
  font-size: var(--text-body-sm);
}

/* ====== 响应式 ====== */
@media (max-width: 1024px) {
  .component-row {
    grid-template-columns: 1fr;
    gap: 4px;
  }
  .component-row__action {
    justify-content: flex-start;
  }
  .system-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .models-cols {
    grid-template-columns: 1fr;
  }
}

/* ====== 模型下载列表（显存感知） ====== */
.models-section {
  background: var(--glass-1);
  -webkit-backdrop-filter: var(--glass-blur-1);
  backdrop-filter: var(--glass-blur-1);
  border: var(--border-default);
  border-radius: var(--radius-lg);
  padding: 16px 20px;
}
.section-hint--vram {
  color: var(--text-tertiary);
}
.models-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.models-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}
.models-col__head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-bottom: 6px;
  border-bottom: var(--divider);
}
.models-col__badge {
  font-size: var(--text-body-sm);
  font-weight: var(--font-weight-semibold);
  padding: 2px 10px;
  border-radius: var(--radius-sm);
}
.models-col__head--recommended .models-col__badge {
  color: var(--success);
  background: var(--success-bg);
  border: 1px solid rgba(61, 214, 140, 0.3);
}
.models-col__head--advanced .models-col__badge {
  color: var(--warning);
  background: var(--warning-bg);
  border: 1px solid rgba(255, 185, 56, 0.3);
}
.models-col__sub {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
}
.models-col__body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.models-col__empty {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
  padding: 12px;
  text-align: center;
  border: var(--border-subtle);
  border-radius: var(--radius-sm);
}

.model-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  background: var(--glass-1);
  border: var(--border-subtle);
  transition: all 0.15s ease;
}
.model-card:hover {
  border-color: var(--border-strong);
  background: var(--hover-overlay);
}
/* 显存不足的橙色/黄色警告边框 + 警告底 */
.model-card--warn {
  border-color: var(--warning);
  background: var(--warning-bg);
}
.model-card--warn:hover {
  border-color: var(--warning);
  background: var(--warning-bg);
}
.model-card__rec-badge {
  position: absolute;
  top: 10px;
  right: 10px;
  font-size: var(--text-caption);
  font-weight: var(--font-weight-semibold);
  color: var(--success);
  background: var(--success-bg);
  border: 1px solid rgba(61, 214, 140, 0.3);
  padding: 1px 8px;
  border-radius: var(--radius-sm);
}
.model-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding-right: 60px; /* 给右上角徽标留位 */
}
.model-card__name {
  font-size: var(--text-body);
  color: var(--text-primary);
  font-weight: var(--font-weight-medium);
}
.model-card__ver {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  padding: 1px 6px;
  background: var(--glass-1);
  border-radius: 4px;
}
.model-card__size {
  font-size: var(--text-caption);
  color: var(--text-tertiary);
}
.model-card__vram {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-body-sm);
  color: var(--text-secondary);
}
.model-card__vram-icon {
  font-size: 13px;
}
.model-card__progress {
  display: flex;
  align-items: center;
  gap: 8px;
}
.model-card__action {
  display: flex;
  justify-content: flex-start;
}
.model-card__note {
  margin: 0;
  font-size: var(--text-caption);
  color: var(--error);
  line-height: 1.5;
}
</style>
