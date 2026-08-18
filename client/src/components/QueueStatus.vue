<template>
  <div class="queue-status" :class="{ empty: position === 0 }">
    <div v-if="position > 0" class="queue-main">
      <div class="queue-icon">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v5l3 3" stroke-linecap="round" />
        </svg>
      </div>
      <div class="queue-content">
        <div class="queue-header">
          <span class="queue-title">前方还有 <strong>{{ position }}</strong> 人排队中</span>
          <span class="queue-eta">预计等待 <strong>{{ estimatedMinutes }}分钟</strong></span>
        </div>
        <div class="queue-bar">
          <div
            class="queue-bar-fill"
            :style="{ width: `${Math.min(position / (maxQueue ?? 20) * 100, 100)}%` }"
          />
        </div>
        <div class="queue-subtitle">
          <span v-if="tier === 'paid'">
            <span class="badge-paid">付费用户</span>
            优先队列中
          </span>
          <span v-else>
            <span class="badge-free">免费用户</span>
            普通队列中
          </span>
        </div>
      </div>
    </div>

    <div v-if="position === 0 && state === 'running'" class="queue-processing">
      <div class="processing-spinner">
        <div class="spinner-ring" />
        <span class="spinner-text">处理中</span>
      </div>
      <div class="processing-detail">
        <span>{{ step || '正在生成视频...' }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

/**
 * QueueStatus — 云端模式下的排队状态展示
 *
 * 架构位置：在生成视图（Text2VideoView / Img2VideoView / StyleTransferView）底部
 *          当云端模式开启且任务处于排队状态时显示
 *
 * 数据来源：通过 WebSocket 接收 /api/v1/tasks/{task_id} 的实时状态
 *
 * 显示规则：
 *   - position > 0: 显示排队人数 + 预计等待时间 + 用户等级
 *   - position === 0 且 state === 'running': 显示"处理中"动画
 *   - position === 0 且 state === 'queued': 空状态
 *
 * 设计参考：苏璃光 component-tokens.md
 * - 品牌渐变色进度条
 * - 间距 8px 倍数
 * - 字号 12-13px
 *
 * @example
 *   <QueueStatus
 *     :position="task.queue_position"
 *     :tier="userTier"
 *     :state="task.state"
 *     :step="task.step"
 *   />
 */

const props = defineProps<{
  position: number   // 队列中前方人数（0 = 正在处理）
  tier: string       // "paid" | "free"
  state: string      // "queued" | "running" | "completed" | "failed"
  step: string       // 当前步骤描述
  maxQueue?: number  // 最大队列深度（用于进度条比例），默认 20
}>()

const estimatedMinutes = computed(() => {
  if (props.position === 0) return 0
  const avgSeconds = 90  // 单次平均 90 秒
  return Math.ceil(props.position * avgSeconds / 60)
})
</script>

<style scoped>
.queue-status {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 10px;
  background: var(--bg-elevated);
  border: 1px solid rgba(91, 108, 255, 0.2);
  animation: fade-in 0.3s ease;
}

.queue-status.empty {
  display: none;
}

.queue-main {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.queue-icon {
  width: 36px;
  height: 36px;
  border-radius: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--info-bg);
  color: var(--brand-blue);
  flex-shrink: 0;
  animation: pulse-icon 2s ease-in-out infinite;
}

.queue-content {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
  min-width: 0;
}

.queue-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
}

.queue-title {
  font-size: 13px;
  color: var(--text-primary);
  font-weight: 500;
}

.queue-title strong {
  color: var(--brand-blue);
  font-size: 16px;
  font-weight: 700;
  margin: 0 2px;
}

.queue-eta {
  font-size: 12px;
  color: var(--text-secondary);
}

.queue-eta strong {
  color: var(--accent-cyan);
}

.queue-bar {
  width: 100%;
  height: 4px;
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.06);
  overflow: hidden;
}

.queue-bar-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--brand-gradient);
  transition: width 0.3s ease;
  animation: shimmer-bar 2s linear infinite;
}

.queue-subtitle {
  font-size: 11px;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.badge-paid {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  background: var(--brand-gradient);
  color: var(--text-on-brand);
}

.badge-free {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 700;
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.06);
}

/* 处理中状态 */
.queue-processing {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
}

.processing-spinner {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--accent-cyan);
}

.spinner-ring {
  width: 20px;
  height: 20px;
  border: 2px solid rgba(61, 214, 232, 0.2);
  border-top-color: var(--accent-cyan);
  border-radius: 10px;
  animation: spin 1s linear infinite;
}

.spinner-text {
  font-size: 13px;
  font-weight: 600;
  color: var(--accent-cyan);
}

.processing-detail {
  font-size: 12px;
  color: var(--text-secondary);
  flex: 1;
}

@keyframes fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes pulse-icon {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

@keyframes shimmer-bar {
  0% { background-position: -200px 0; }
  100% { background-position: 200px 0; }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>