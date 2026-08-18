/**
 * cloud.ts — 云端生成 API 封装层
 * ============================================================
 * 来源：程流深 Task #11（云端转发层）
 *
 * 端点：
 *   POST /api/v1/cloud/generate  → body {mode, prompt, params} → {task_id, queue_position, auto_fallback, source}
 *   WS   /api/v1/cloud/progress/ws?task_id=xxx  → 双向代理
 *
 * 降级策略：
 *   - 云端不可用（网络错误）→ 自动降级本地
 *   - 后端返回 auto_fallback=true → 自动降级本地
 *   - 本地也不可用 → 提示用户
 */
import { getApiBaseUrl, getWsBaseUrl } from './utils';
import { getToken } from './auth';
import type { GenerateParams } from './nexus';

// ---------- 类型定义 ----------

export interface CloudGenerateRequest {
  mode: 'txt2video' | 'img2video' | 'video2video';
  prompt: string;
  params?: Record<string, unknown>;
  image_path?: string;
}

export interface CloudGenerateResponse {
  task_id: string;
  queue_position?: number;
  auto_fallback?: boolean;
  source: 'cloud' | 'local';
  message?: string;
}

export interface CloudProgressPayload {
  type: 'progress' | 'completed' | 'failed';
  task_id: string;
  progress?: number;
  phase?: number;
  message?: string;
  phase_messages?: string[];
  estimated_text?: string;
  output_url?: string;
  source?: 'cloud' | 'local';
  error?: string;
}

// ---------- 云端生成请求 ----------

/**
 * 发起云端生成请求
 * POST /api/v1/cloud/generate
 * @returns CloudGenerateController（含终态回调）
 */
export async function cloudGenerate(params: GenerateParams): Promise<CloudGenerateController> {
  const url = `${getApiBaseUrl()}/api/v1/cloud/generate`;
  const token = getToken();

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      mode: params.mode,
      prompt: params.prompt,
      image_path: params.image_path,
      params: params.params || {},
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`云端生成失败 (${res.status}): ${err}`);
  }

  const data = (await res.json()) as CloudGenerateResponse;
  return createCloudController(data.task_id);
}

// ---------- 云端控制器 ----------

export interface CloudGenerateController {
  task_id: string;
  auto_fallback?: boolean;
  onDone(cb: (p: CloudProgressPayload) => void): CloudGenerateController;
  onError(cb: (p: CloudProgressPayload) => void): CloudGenerateController;
}

function createCloudController(taskId: string): CloudGenerateController {
  let doneCb: ((p: CloudProgressPayload) => void) | null = null;
  let errorCb: ((p: CloudProgressPayload) => void) | null = null;
  let ws: WebSocket | null = null;

  // 连接云端 WS 进度通道
  connectCloudWs(taskId, (payload) => {
    if (payload.type === 'completed') {
      doneCb?.(payload);
      cleanup();
    } else if (payload.type === 'failed') {
      errorCb?.(payload);
      cleanup();
    }
  });

  function connectCloudWs(taskId: string, onMessage: (payload: CloudProgressPayload) => void) {
    const wsUrl = `${getWsBaseUrl()}/api/v1/cloud/progress/ws?task_id=${encodeURIComponent(taskId)}`;

    try {
      ws = new WebSocket(wsUrl);
    } catch {
      // WS 创建失败，仅依赖 HTTP 终态
      return;
    }

    ws.onopen = () => {
      console.log('[cloud] WS 已连接', taskId);
    };

    ws.onmessage = (event) => {
      try {
        const data: CloudProgressPayload = JSON.parse(event.data);
        onMessage(data);
      } catch { /* 忽略 */ }
    };

    ws.onclose = () => {
      // 云 WS 关闭后不自动重连，依赖 HTTP 终态
      console.warn('[cloud] WS 关闭', taskId);
    };
  }

  function cleanup() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.close();
    }
    ws = null;
  }

  return {
    task_id: taskId,
    onDone(cb): CloudGenerateController { doneCb = cb; return this; },
    onError(cb): CloudGenerateController { errorCb = cb; return this; },
  };
}