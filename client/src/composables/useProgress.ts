/*
 * useProgress.ts — 进度反馈 Composable（方案 B：WebSocket 直连 + 云端支持）
 * ============================================================
 * 核心职责：
 *   1. 管理文案化进度文案库（16+ 条，按 4 个阶段分组）
 *   2. 直连后端 WebSocket（本地 /progress/ws 或 云端 /api/v1/cloud/progress/ws）
 *   3. 断连时降级为 HTTP 轮询
 *   4. 控制 crossfade 切换（400ms：旧 200ms 淡出 + 新 200ms 淡入）
 *   5. 提供副文案格式化（"预计还需 XX 秒"）
 *
 * 设计来源：苏璃光 animation-spec.md 2.2 + 11.1 节
 * 本地契约：WS /progress/ws?task_id=xxx → payload:
 *   { task_id, progress, phase, message, phase_messages, estimated_text }
 * 云端契约：WS /api/v1/cloud/progress/ws?task_id=xxx → 同上 payload
 *
 * 云端模式由 startTracking(taskId, 'cloud') 参数控制
 * 云端端点：
 *   - WS:  ws://127.0.0.1:9881/api/v1/cloud/progress/ws?task_id=xxx
 *   - HTTP 轮询: GET /api/v1/cloud/task/{task_id}
 * 本地端点：
 *   - WS:  ws://127.0.0.1:9881/progress/ws?task_id=xxx
 *   - HTTP 轮询: GET /progress/status/{task_id}
 */
import { ref, watch, onUnmounted, computed } from 'vue';
import { getApiBaseUrl, getWsBaseUrl } from '../api/utils';

/* ============ 常量配置 ============ */

const BACKEND_BASE = getApiBaseUrl();
const WS_BASE = getWsBaseUrl();
const WS_RETRY_DELAYS = [3000, 6000, 12000];
const POLL_INTERVAL_MS = 2000;
const CROSSFADE_OUT_MS = 200;
const CROSSFADE_IN_MS = 200;

// 端点路径（按模式切换）
const LOCAL_WS_PATH = '/progress/ws';
const LOCAL_POLL_PATH = '/progress/status/';
const CLOUD_WS_PATH = '/api/v1/cloud/progress/ws';
const CLOUD_POLL_PATH = '/api/v1/cloud/task/';

/* ============ 进度反馈文案库（16 条，4 阶段 × 4 条） ============ */

export type ProgressPhase = 'thinking' | 'rendering' | 'refining' | 'finalizing';
export type ProgressMode = 'local' | 'cloud';

const PHASE_MESSAGE_MAP: Record<number, { phase: ProgressPhase; messages: string[] }> = {
  0: {
    phase: 'thinking',
    messages: [
      '正在理解你的创意…',
      '解析画面元素中…',
      '构思镜头运动中…',
      '感受故事节奏中…',
    ],
  },
  1: {
    phase: 'rendering',
    messages: [
      '正在绘制第一帧…',
      '生成关键画面中…',
      '构建光影效果中…',
      '渲染动态轨迹中…',
      '绘制细节纹理中…',
    ],
  },
  2: {
    phase: 'refining',
    messages: [
      '画面逐渐成形…',
      '打磨运动流畅度…',
      '调整色调质感中…',
      '渲染中间帧过渡…',
    ],
  },
  3: {
    phase: 'finalizing',
    messages: [
      '最后润色中…',
      '优化视频清晰度…',
      '渲染最终成片…',
      '即将完成，稍等片刻…',
    ],
  },
};

const ALL_MESSAGES: string[] = Object.values(PHASE_MESSAGE_MAP)
  .flatMap((p) => p.messages);

const PHASE_MESSAGE_POOLS: string[][] = Object.values(PHASE_MESSAGE_MAP).map(
  (p) => p.messages
);

export function formatRemainingText(remaining: number | string | null): string {
  if (typeof remaining === 'string') return remaining;
  if (remaining == null) return '';
  if (remaining > 60) return '生成中，请耐心等待';
  if (remaining >= 30) return '预计还需约 1 分钟';
  if (remaining >= 10) return `预计还需 ${remaining} 秒`;
  return '马上就好';
}

export interface WsProgressPayload {
  task_id: string;
  progress: number;
  phase: number;
  message: string;
  phase_messages?: string[];
  estimated_text?: string;
  output_url?: string;
  state?: 'running' | 'completed' | 'failed';
  error?: string;
}

export function useProgress() {
  const currentText = ref<string>('正在构思画面…');
  const currentPhase = ref<number>(0);
  const currentProgress = ref<number>(0);
  const remainingSeconds = ref<number | null>(null);
  const isCrossfading = ref<boolean>(false);
  const connectionState = ref<'idle' | 'connecting' | 'ws_connected' | 'polling' | 'disconnected'>('idle');

  const subText = computed(() => formatRemainingText(remainingSeconds.value));

  const INTERVAL_MS = 2800;
  let fadeTimer: ReturnType<typeof setInterval> | null = null;
  let lastPoolIndex = -1;

  let ws: WebSocket | null = null;
  let wsReconnectAttempts = 0;
  let wsReconnectTimer: ReturnType<typeof setTimeout> | null = null;

  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let pollTaskId: string | null = null;
  let currentMode: ProgressMode = 'local';

  let onCompletedCb: ((payload: WsProgressPayload) => void) | null = null;
  let onFailedCb: ((payload: WsProgressPayload) => void) | null = null;
  let onFallbackCb: ((mode: 'polling' | 'ws') => void) | null = null;

  function switchText(targetPhase: number) {
    const pool = getPoolForPhase(targetPhase);
    let idx: number;
    do {
      idx = Math.floor(Math.random() * pool.length);
    } while (idx === lastPoolIndex && pool.length > 1);
    lastPoolIndex = idx;

    isCrossfading.value = true;
    setTimeout(() => {
      currentText.value = pool[idx];
      isCrossfading.value = false;
    }, CROSSFADE_OUT_MS);
  }

  function getPoolForPhase(phase: number): string[] {
    return PHASE_MESSAGE_POOLS[phase] || PHASE_MESSAGE_POOLS[0];
  }

  function updatePhase(phase: number) {
    currentPhase.value = phase;
    lastPoolIndex = -1;
    switchText(phase);
  }

  function updateProgress(pct: number) {
    currentProgress.value = Math.min(100, Math.max(0, pct));
  }

  function updateRemaining(sec: number | string | null) {
    remainingSeconds.value = sec;
  }

  // ========== WebSocket 连接 ==========

  function getWsUrl(taskId: string, mode: ProgressMode): string {
    const path = mode === 'cloud' ? CLOUD_WS_PATH : LOCAL_WS_PATH;
    return `${WS_BASE}${path}?task_id=${encodeURIComponent(taskId)}`;
  }

  function connectWebSocket(taskId: string) {
    if (connectionState.value === 'ws_connected') return;

    connectionState.value = 'connecting';
    const wsUrl = getWsUrl(taskId, currentMode);

    try {
      ws = new WebSocket(wsUrl);
    } catch (e) {
      console.warn('[useProgress] WebSocket 创建失败，降级 HTTP 轮询', e);
      startPolling(taskId);
      return;
    }

    ws.onopen = () => {
      console.log('[useProgress] WebSocket 已连接', taskId, 'mode:', currentMode);
      connectionState.value = 'ws_connected';
      wsReconnectAttempts = 0;
      if (onFallbackCb) onFallbackCb('ws');
    };

    ws.onmessage = (event) => {
      try {
        const data: WsProgressPayload = JSON.parse(event.data);
        if (data.task_id !== taskId) return;

        if (data.progress != null) updateProgress(data.progress);
        if (data.phase != null) updatePhase(data.phase);
        if (data.estimated_text) updateRemaining(data.estimated_text);
        if (data.message) currentText.value = data.message;

        if (data.state === 'completed') {
          handleComplete(taskId, data);
        } else if (data.state === 'failed') {
          handleFailed(data);
        }
      } catch (e) {
        console.warn('[useProgress] WebSocket 消息解析失败', e);
      }
    };

    ws.onclose = (event) => {
      console.warn('[useProgress] WebSocket 关闭', event.code, event.reason);
      connectionState.value = 'disconnected';
      ws = null;
      wsReconnectAttempts++;

      if (wsReconnectAttempts <= WS_RETRY_DELAYS.length) {
        const delay = WS_RETRY_DELAYS[wsReconnectAttempts - 1];
        console.log(`[useProgress] ${delay}ms 后重试 WebSocket 连接...`);
        wsReconnectTimer = setTimeout(() => {
          connectWebSocket(taskId);
        }, delay);
      } else {
        console.warn('[useProgress] WebSocket 重连次数用尽，降级 HTTP 轮询');
        startPolling(taskId);
      }
    };

    ws.onerror = () => {
      // 等 onclose 触发后再处理
    };
  }

  function sendWsCommand(action: 'ping' | 'push_now') {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action }));
    }
  }

  // ========== HTTP 轮询兜底 ==========

  function getPollUrl(taskId: string, mode: ProgressMode): string {
    if (mode === 'cloud') {
      return `${BACKEND_BASE}${CLOUD_POLL_PATH}${taskId}`;
    }
    return `${BACKEND_BASE}${LOCAL_POLL_PATH}${taskId}`;
  }

  function startPolling(taskId: string) {
    pollTaskId = taskId;
    connectionState.value = 'polling';
    if (onFallbackCb) onFallbackCb('polling');

    pollOnce(taskId);

    pollTimer = setInterval(() => {
      pollOnce(taskId);
    }, POLL_INTERVAL_MS);
  }

  async function pollOnce(taskId: string) {
    try {
      const res = await fetch(getPollUrl(taskId, currentMode));
      if (!res.ok) return;
      const data = await res.json();
      if (data.progress != null) updateProgress(data.progress);
      if (data.phase != null) updatePhase(data.phase);
      if (data.estimated_text) updateRemaining(data.estimated_text);
      if (data.message) currentText.value = data.message;

      if (data.state === 'completed') {
        handleComplete(taskId, data);
      } else if (data.state === 'failed') {
        handleFailed(data);
      }
    } catch {
      // 静默失败，下次重试
    }
  }

  function handleComplete(taskId: string, data: WsProgressPayload) {
    stopAll();
    connectionState.value = 'idle';
    updateProgress(100);
    onCompletedCb?.({ ...data, output_url: data.output_url || `/static/uploads/${taskId}/output.mp4` });
  }

  function handleFailed(data: WsProgressPayload) {
    stopAll();
    connectionState.value = 'idle';
    onFailedCb?.(data);
  }

  function startCycle() {
    if (fadeTimer) return;
    fadeTimer = setInterval(() => {
      switchText(currentPhase.value);
    }, INTERVAL_MS);
  }

  function stopCycle() {
    if (fadeTimer) {
      clearInterval(fadeTimer);
      fadeTimer = null;
    }
    lastPoolIndex = -1;
  }

  function stopAll() {
    stopCycle();
    if (wsReconnectTimer) {
      clearTimeout(wsReconnectTimer);
      wsReconnectTimer = null;
    }
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (ws) {
      ws.onclose = null;
      ws.close(1000, 'task completed');
      ws = null;
    }
  }

  function reset() {
    currentText.value = '正在构思画面…';
    currentPhase.value = 0;
    currentProgress.value = 0;
    remainingSeconds.value = null;
    isCrossfading.value = false;
    connectionState.value = 'idle';
    stopAll();
  }

  /** 开始跟踪任务进度，支持云端模式 */
  function startTracking(taskId: string, mode: ProgressMode = 'local') {
    reset();
    currentMode = mode;
    startCycle();
    connectWebSocket(taskId);
  }

  function onCompleted(cb: (p: WsProgressPayload) => void) { onCompletedCb = cb; }
  function onFailed(cb: (p: WsProgressPayload) => void) { onFailedCb = cb; }
  function onFallback(cb: (mode: 'polling' | 'ws') => void) { onFallbackCb = cb; }

  watch(currentPhase, (newPhase) => {
    updatePhase(newPhase);
  });

  onUnmounted(() => {
    stopAll();
  });

  return {
    currentText,
    currentPhase,
    currentProgress,
    remainingSeconds,
    subText,
    isCrossfading,
    connectionState,
    updatePhase,
    updateProgress,
    updateRemaining,
    startTracking,
    startCycle,
    stopCycle,
    reset,
    onCompleted,
    onFailed,
    onFallback,
    sendWsCommand,
    formatRemainingText,
  };
}

export { PHASE_MESSAGE_MAP, ALL_MESSAGES, PHASE_MESSAGE_POOLS };