<template>
  <Teleport to="body">
    <div v-if="visible" class="smart-route-modal-overlay" @click.self="close">
      <div class="smart-route-modal">
        <!-- 顶部装饰渐变条 -->
        <div class="modal-glow" />

        <!-- 图标区 -->
        <div class="modal-icon">
          <svg viewBox="0 0 64 64" width="56" height="56" aria-hidden="true">
            <defs>
              <linearGradient id="icon-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" stop-color="#5B6CFF" />
                <stop offset="100%" stop-color="#B14CFF" />
              </linearGradient>
            </defs>
            <circle cx="32" cy="32" r="30" fill="none" stroke="url(#icon-grad)" stroke-width="2" />
            <path d="M20 32c0-6.6 5.4-12 12-12s12 5.4 12 12" stroke="url(#icon-grad)" stroke-width="2.5" stroke-linecap="round" fill="none" />
            <path d="M32 20v24" stroke="url(#icon-grad)" stroke-width="2.5" stroke-linecap="round" />
            <circle cx="32" cy="32" r="3" fill="url(#icon-grad)" />
            <path d="M14 46l4-4M50 46l-4-4M14 18l4 4M50 18l-4 4" stroke="url(#icon-grad)" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </div>

        <!-- 标题 -->
        <h2 class="modal-title">
          检测到本地 GPU 显存不足
        </h2>

        <!-- GPU 信息卡片 -->
        <div class="gpu-info-card">
          <div class="gpu-bar-wrapper">
            <div class="gpu-bar-container">
              <div
                class="gpu-bar-fill"
                :style="{ width: `${gpuUtilizationPercent}%` }"
              />
              <div
                class="gpu-bar-threshold"
                :style="{ left: `${thresholdPercent}%` }"
              />
            </div>
            <div class="gpu-bar-labels">
              <span class="gpu-current">
                <span class="dot-current" />
                当前：<strong>{{ gpuMemory }}GB</strong>
              </span>
              <span class="gpu-needed">
                推荐 <strong>≥6GB</strong>
              </span>
            </div>
          </div>
          <div class="gpu-device-name">{{ gpuDeviceName }}</div>
        </div>

        <!-- 说明文字 -->
        <p class="modal-desc">
          本地显卡显存低于推荐值，可能导致
          <strong class="text-warn">生成失败、画面质量下降或长时间卡顿</strong>。
          建议使用云端 GPU 集群，获得更快更稳定的生成体验。
        </p>

        <!-- 对比卡片 -->
        <div class="comparison-grid">
          <div class="comparison-card local-card">
            <div class="card-header">
              <span class="card-tag tag-local">本地</span>
              <span class="card-title-text">继续使用本地</span>
            </div>
            <ul class="card-list">
              <li><span class="check-fail">✕</span> 生成速度较慢</li>
              <li><span class="check-fail">✕</span> 可能显存不足失败</li>
              <li><span class="check-pass">✓</span> 免费（仅电费）</li>
            </ul>
          </div>
          <div class="comparison-card cloud-card recommended">
            <div class="card-header">
              <span class="card-tag tag-cloud">云端</span>
              <span class="card-title-text">云端极速模式</span>
              <span class="badge">推荐</span>
            </div>
            <ul class="card-list">
              <li><span class="check-pass">✓</span> 生成速度 <strong>2-5×</strong> 更快</li>
              <li><span class="check-pass">✓</span> A10 24GB 显存，稳定不爆显存</li>
              <li><span class="check-fail">~</span> 约 <strong>¥0.12</strong>/次</li>
            </ul>
          </div>
        </div>

        <!-- 操作按钮 -->
        <div class="modal-actions">
          <button
            class="btn btn-primary btn-activate"
            @click="activateCloud"
          >
            <svg viewBox="0 0 20 20" width="16" height="16" fill="currentColor" aria-hidden="true">
              <path d="M11.3 1.046A1 1 0 0 1 12 1.5v3h2.5a1.5 1.5 0 0 1 1.088 2.564l-9.25 9.25a1.5 1.5 0 0 1-2.122 0l-2.878-2.878a1.5 1.5 0 0 1 0-2.122l9.25-9.25A1.5 1.5 0 0 1 11 4.5h2.5V1.5a1 1 0 0 1 .8-.454z" />
            </svg>
            开启云端极速模式
          </button>
          <button
            class="btn btn-ghost btn-local"
            @click="useLocal"
          >
            继续使用本地
          </button>
        </div>

        <div class="modal-footer">
          <label class="remember-checkbox">
            <input type="checkbox" v-model="rememberChoice" />
            <span>不再提示</span>
          </label>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * SmartRouteModal — 显存不足时自动弹窗推荐云端
 *
 * 架构位置：在 Text2VideoView / Img2VideoView / StyleTransferView 中
 *          当检测到本地 GPU 显存 < 6GB 时自动弹出
 *
 * 触发时机：
 *   - 首次启动应用时，通过 useProgress / getBackendStatus 获取 GPU 信息
 *   - 如果 gpu_memory_gb < 6，弹出此 Modal
 *   - 用户关闭后（如勾选"不再提示"），下次不再弹出
 *
 * 设计参考：苏璃光 component-tokens.md
 * - 居中 Modal，带半透明遮罩
 * - 品牌渐变色按钮
 * - 8px 间距系统
 * - 暗色主题一致
 *
 * @emits activate-cloud 用户选择开启云端
 * @emits use-local      用户选择继续使用本地
 */

const props = defineProps<{
  visible: boolean
  gpuMemory: number          // 当前 GPU 显存（GB）
  gpuDeviceName: string      // 显卡名称
}>()

const emit = defineEmits<{
  (e: 'activate-cloud'): void
  (e: 'use-local'): void
}>()

// 是否在弹出时不再提示
const rememberChoice = ref(false)

// 显存利用率百分比（用于进度条显示）
const gpuUtilizationPercent = computed(() => {
  const max = 24  // A10 24GB 为参考上限
  return Math.min((props.gpuMemory / max) * 100, 100)
})

// 阈值线位置（6GB / 24GB = 25%）
const thresholdPercent = computed(() => {
  return (6 / 24) * 100  // 25%
})

function activateCloud(): void {
  if (rememberChoice.value) {
    try {
      localStorage.setItem('nexusvideo:cloud-mode', 'true')
      localStorage.setItem('nexusvideo:skip-gpu-modal', 'true')
    } catch (_e) {}
  }
  emit('activate-cloud')
}

function useLocal(): void {
  if (rememberChoice.value) {
    try {
      localStorage.setItem('nexusvideo:skip-gpu-modal', 'true')
    } catch (_e) {}
  }
  emit('use-local')
}

function close(): void {
  useLocal()
}
</script>

<style scoped>
.smart-route-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(4px);
  animation: fade-in 0.2s ease;
}

.smart-route-modal {
  position: relative;
  width: 100%;
  max-width: 480px;
  padding: 28px;
  border-radius: 16px;
  background: var(--bg-surface);
  border: 1px solid var(--border-strong);
  box-shadow: 0 24px 48px rgba(0, 0, 0, 0.4);
  animation: slide-up 0.3s ease;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.modal-glow {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  border-radius: 16px 16px 0 0;
  background: var(--brand-gradient);
}

.modal-icon {
  display: flex;
  justify-content: center;
  padding: 8px 0 0;
}

.modal-title {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  text-align: center;
  margin: 0;
}

.gpu-info-card {
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--bg-elevated);
  border: var(--border-subtle);
}

.gpu-bar-wrapper {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.gpu-bar-container {
  position: relative;
  width: 100%;
  height: 8px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.06);
  overflow: visible;
}

.gpu-bar-fill {
  height: 100%;
  border-radius: 4px;
  background: linear-gradient(90deg, var(--error) 0%, var(--warning) 100%);
  transition: width 0.5s ease;
}

.gpu-bar-threshold {
  position: absolute;
  top: -3px;
  width: 2px;
  height: 14px;
  background: var(--success);
  border-radius: 1px;
}

.gpu-bar-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-secondary);
}

.gpu-current {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--warning);
}

.gpu-current .dot-current {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: var(--warning);
}

.gpu-needed {
  color: var(--text-tertiary);
}

.gpu-device-name {
  margin-top: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
  text-align: center;
}

.modal-desc {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
  margin: 0;
}

.text-warn {
  color: var(--warning);
}

/* 对比卡片 */
.comparison-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.comparison-card {
  padding: 12px;
  border-radius: 10px;
  background: var(--bg-elevated);
  border: var(--border-subtle);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.comparison-card.recommended {
  border-color: var(--brand-blue);
  background: linear-gradient(135deg, rgba(91, 108, 255, 0.08), rgba(177, 76, 255, 0.08));
  box-shadow: 0 0 0 1px rgba(91, 108, 255, 0.2);
}

.card-header {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.card-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}

.tag-local {
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.06);
}

.tag-cloud {
  color: var(--text-on-brand);
  background: var(--brand-gradient);
}

.card-title-text {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--warning);
  color: #1a1a1e;
}

.card-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  color: var(--text-secondary);
}

.card-list li {
  display: flex;
  align-items: center;
  gap: 4px;
}

.check-pass {
  color: var(--success);
  font-weight: 700;
}

.check-fail {
  color: var(--error);
  font-weight: 700;
}

.card-list strong {
  color: var(--text-primary);
  font-weight: 600;
}

/* 操作按钮 */
.modal-actions {
  display: flex;
  gap: 8px;
}

.btn {
  flex: 1;
  height: 42px;
  border-radius: 10px;
  font-family: 'Inter', sans-serif;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: all 0.15s ease;
  border: none;
}

.btn-primary {
  background: var(--brand-gradient);
  color: var(--text-on-brand);
  box-shadow: 0 2px 8px rgba(91, 108, 255, 0.3);
}

.btn-primary:hover {
  background: var(--brand-gradient-hover);
  box-shadow: 0 4px 16px rgba(91, 108, 255, 0.4);
  transform: translateY(-1px);
}

.btn-ghost {
  background: var(--bg-elevated);
  color: var(--text-secondary);
  border: var(--border-default);
}

.btn-ghost:hover {
  color: var(--text-primary);
  border-color: var(--border-strong);
  background: var(--hover-overlay);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  padding-top: 4px;
}

.remember-checkbox {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-tertiary);
  cursor: pointer;
}

.remember-checkbox input {
  width: 14px;
  height: 14px;
  accent-color: var(--brand-blue);
  cursor: pointer;
}

@keyframes fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes slide-up {
  from {
    opacity: 0;
    transform: translateY(12px) scale(0.98);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}
</style>