<template>
  <div class="cloud-mode-toggle">
    <div class="toggle-row">
      <span class="toggle-label">
        <span class="label-text">云端极速模式</span>
        <span class="label-desc">使用云端 GPU 集群加速生成（<span class="cost-hint">约 ¥0.12/次</span>）</span>
      </span>

      <button
        class="toggle-switch"
        :class="{ active: modelValue }"
        @click="toggle"
        :aria-label="modelValue ? '关闭云端模式' : '开启云端模式'"
        :aria-checked="modelValue"
      >
        <span class="toggle-thumb" />
        <span v-if="modelValue" class="toggle-indicator indicator-on">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-hidden="true">
            <path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96z" />
          </svg>
        </span>
        <span v-else class="toggle-indicator indicator-off">
          <svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true">
            <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" transform="translate(-1 -1)"/>
          </svg>
        </span>
      </button>
    </div>

    <div v-if="modelValue" class="mode-info">
      <div class="mode-status">
        <span class="status-dot" />
        <span>云端模式已启用 · 预计加速 <strong>2-5×</strong></span>
      </div>
      <div class="mode-detail">
        任务将提交到云端 A10 GPU 集群处理，生成速度更快，适合本地显存不足的场景。
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * CloudModeToggle — 云端/本地模式切换开关
 *
 * 架构位置：设置页（Settings）中的偏好设置
 * 设计参考：苏璃光 component-tokens.md
 * - 尺寸：48×26（标准 toggle）
 * - 颜色：品牌渐变色（#5B6CFF → #B14CFF）
 * - 圆角：radius-sm = 13px（高度的一半）
 * - 间距：8px 倍数
 *
 * 用法示例：
 *   <CloudModeToggle v-model="cloudEnabled" />
 *
 * @emits update:modelValue 当 toggle 状态变化时
 */

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

function toggle(): void {
  const newVal = !props.modelValue
  emit('update:modelValue', newVal)

  // 触发持久化（存储到 localStorage，由父组件或 store 处理）
  try {
    localStorage.setItem('nexusvideo:cloud-mode', String(newVal))
  } catch (_e) {
    // 某些环境可能不支持 localStorage，静默忽略
  }
}
</script>

<style scoped>
.cloud-mode-toggle {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border-radius: 12px;
  background: var(--bg-surface);
  border: var(--border-subtle);
}

.toggle-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.toggle-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.label-text {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.label-desc {
  font-size: 12px;
  color: var(--text-secondary);
}

.cost-hint {
  color: var(--accent-cyan);
  font-weight: 500;
}

/* Toggle Switch — 48×26 */
.toggle-switch {
  position: relative;
  width: 48px;
  height: 26px;
  border-radius: 13px;
  border: 1px solid var(--border-default);
  background: var(--bg-elevated);
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
  padding: 0;
  outline: none;
  display: flex;
  align-items: center;
}

.toggle-switch:hover {
  border-color: var(--border-strong);
}

.toggle-switch:active {
  transform: scale(0.96);
}

.toggle-switch.active {
  background: var(--brand-gradient);
  border-color: transparent;
  box-shadow: 0 0 12px rgba(91, 108, 255, 0.3);
}

.toggle-thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 9px;
  background: var(--text-secondary);
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  justify-content: center;
}

.toggle-switch.active .toggle-thumb {
  left: 25px;
  background: var(--text-on-brand);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

.toggle-indicator {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.indicator-on {
  left: 4px;
  color: var(--text-on-brand);
  opacity: 0;
}

.indicator-off {
  left: 26px;
  color: var(--text-tertiary);
  opacity: 1;
}

.toggle-switch.active .indicator-on {
  opacity: 1;
}

.toggle-switch.active .indicator-off {
  opacity: 0;
}

/* 云端模式已启用的状态信息 */
.mode-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--info-bg);
  border: 1px solid rgba(91, 108, 255, 0.2);
}

.mode-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-primary);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: var(--success);
  animation: pulse-dot 2s ease-in-out infinite;
}

.mode-detail {
  font-size: 11px;
  color: var(--text-secondary);
  line-height: 1.5;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>