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
import { ref, onMounted, computed } from 'vue';
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

onMounted(fetchAll);
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
              {{ components.length }} 个组件
              <span v-if="hasIssue" class="section-hint--warn">（{{ issueCount }} 个需关注）</span>
            </span>
          </div>

          <div class="components-list">
            <div
              v-for="c in components"
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
              </div>
            </div>

            <div v-if="components.length === 0" class="empty-state">
              <span class="empty-state__icon">🧩</span>
              <span class="empty-state__text">暂无组件数据，请刷新</span>
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
}
</style>
