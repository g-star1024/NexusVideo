/**
 * nexus.ts — 前端 IPC 封装层 + HTTP API 封装
 * ============================================================
 * 把所有 Tauri command / event 封装成类型安全的 TypeScript API，
 * Vue 组件只 import 这个文件，不直接碰 @tauri-apps 裸 API。
 *
 * 方案 B 架构说明：
 *   - 进度推送：前端直连 WebSocket（useProgress.ts 内部处理）
 *   - 文件上传：直接调用后端 HTTP API（POST /upload/image, /upload/video）
 *   - 视频播放：直接引用后端静态文件服务 URL（/static/uploads/xxx/xxx）
 *   - Tauri IPC：仅用于后端控制命令（start/stop/getStatus），生成进度已不依赖 IPC
 */
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// ---------- 常量 ----------

const BACKEND_BASE = 'http://127.0.0.1:9881';

// ---------- 类型定义（与 Rust 侧 serde 结构对齐）----------

export type ProcState =
  | "stopped" | "starting" | "running" | "stopping" | "crashed" | "restarting";

export type TaskState =
  | "queued" | "running" | "completed" | "failed" | "cancelled";

export interface BackendStatus {
  comfyui: ProcState;
  fastapi: ProcState;
  comfyui_url: string;
  fastapi_url: string;
  uptime_secs: number;
  message: string;
}

export interface BackendLog {
  source: string;   // "comfyui" | "fastapi"
  level: string;
  line: string;
  ts: string;
}

export interface TaskStatus {
  task_id: string;
  state: TaskState;
  progress: number;      // 0-100
  step: string;
  output_path: string | null;
  error: string | null;
}

export interface GenerateParams {
  mode: "txt2video" | "img2video" | "video2video";
  prompt: string;
  image_path?: string;
  params?: Record<string, unknown>;
}

export interface UploadResult {
  status: "ok";
  path: string;        // 相对路径，如 "./uploads/xxx/filename"
  url: string;         // HTTP 路径，如 "/static/uploads/xxx/filename"
  filename: string;
  size: number;
}

// ---------- IPC 命令封装（Tauri 层后端控制） ----------

/** 启动后端（ComfyUI + FastAPI），返回就绪状态 */
export function startBackend(): Promise<BackendStatus> {
  return invoke<BackendStatus>("start_backend");
}

/** 停止全部后端进程 */
export function stopBackend(): Promise<void> {
  return invoke("stop_backend");
}

/** 查询后端状态 */
export function getBackendStatus(): Promise<BackendStatus> {
  return invoke<BackendStatus>("get_backend_status");
}

/** 发起生成请求，返回 task_id（进度通过 WebSocket 直连获取） */
export function generateVideo(p: GenerateParams): Promise<string> {
  return invoke<string>("generate_video", { payload: p });
}

/** 单次查询任务状态 */
export function queryTask(taskId: string): Promise<TaskStatus> {
  return invoke<TaskStatus>("query_task", { taskId });
}

/** 取消任务 */
export function cancelTask(taskId: string): Promise<boolean> {
  return invoke<boolean>("cancel_task", { taskId });
}

/** 获取模型列表 */
export function getModels(): Promise<unknown[]> {
  return invoke("get_models");
}

/** 应用信息（路径/版本，诊断用） */
export function getAppInfo(): Promise<Record<string, unknown>> {
  return invoke("get_app_info");
}

/** 打开输出目录 */
export function openOutputDir(): Promise<string> {
  return invoke("open_output_dir");
}

// ================================================================
// 用户认证 API（通过 HTTP 后端转发）
// 来源：程流深 Task #11 — 认证系统
// 完整的登录/注册/me/quota 封装见 ../api/auth.ts
// 此处仅保留 getAuthHeaders / getAuthToken / setAuthToken / clearAuthToken 供其他模块使用
// ================================================================

/** 认证请求头（自动附加 Bearer Token） */
export function getAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...extra,
  };
  const token = getAuthToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

/** 获取 JWT Token（localStorage key: nexus_token） */
export function getAuthToken(): string | null {
  const t = localStorage.getItem('nexus_token');
  return t || null;
}

/** 保存 JWT Token */
export function setAuthToken(token: string): void {
  localStorage.setItem('nexus_token', token);
}

/** 清除 Token */
export function clearAuthToken(): void {
  localStorage.removeItem('nexus_token');
  localStorage.removeItem('nexus_user');
}

// ---------- 后端 HTTP API 封装 ----------

/**
 * 上传参考图片
 * POST /upload/image?task_id=xxx
 * Content-Type: multipart/form-data
 * 限制：单文件 ≤ 10MB
 */
export async function uploadImage(taskId: string, file: File): Promise<UploadResult> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('task_id', taskId);
  const res = await fetch(`${BACKEND_BASE}/upload/image`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`上传图片失败 (${res.status}): ${err}`);
  }
  return res.json() as Promise<UploadResult>;
}

/**
 * 上传参考视频
 * POST /upload/video?task_id=xxx
 * Content-Type: multipart/form-data
 * 限制：单文件 ≤ 200MB
 */
export async function uploadVideo(taskId: string, file: File): Promise<UploadResult> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('task_id', taskId);
  const res = await fetch(`${BACKEND_BASE}/upload/video`, {
    method: 'POST',
    body: formData,
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`上传视频失败 (${res.status}): ${err}`);
  }
  return res.json() as Promise<UploadResult>;
}

/**
 * 将输出路径转换为完整的 HTTP URL（用于 <video src>）
 * @param outputPath 后端返回的路径，如 "./uploads/xxx/filename" 或绝对路径
 * @param taskId 任务 ID（用于兜底构造路径）
 * @returns 完整的 HTTP URL，如 "http://127.0.0.1:9881/static/uploads/xxx/filename"
 */
export function buildVideoUrl(outputPath: string, taskId?: string): string {
  // 如果已经是完整 URL，直接返回
  if (outputPath.startsWith('http://') || outputPath.startsWith('https://')) {
    return outputPath;
  }

  // 如果是相对路径 "/static/uploads/xxx/filename"，补全主机
  if (outputPath.startsWith('/static/')) {
    return `${BACKEND_BASE}${outputPath}`;
  }

  // 如果是 "./uploads/xxx/filename" 格式，转换为 /static/uploads/xxx/filename
  const relativeMatch = outputPath.match(/^\.\/uploads\/([^/]+)\/(.+)$/);
  if (relativeMatch) {
    const [, id, filename] = relativeMatch;
    return `${BACKEND_BASE}/static/uploads/${id}/${encodeURIComponent(filename)}`;
  }

  // 兜底：用 taskId 构造路径
  if (taskId) {
    const filename = outputPath.split('/').pop() || 'output.mp4';
    return `${BACKEND_BASE}/static/uploads/${taskId}/${encodeURIComponent(filename)}`;
  }

  // 最后的兜底
  return `${BACKEND_BASE}/static/uploads/${outputPath}`;
}

// ---------- 事件订阅封装（保留以兼容 Tauri IPC 事件流） ----------

/** 订阅后端状态变化 */
export function onBackendStatus(cb: (s: Partial<BackendStatus>) => void): Promise<UnlistenFn> {
  return listen<Partial<BackendStatus>>("backend://status", (e) => cb(e.payload));
}

/** 订阅后端日志 */
export function onBackendLog(cb: (l: BackendLog) => void): Promise<UnlistenFn> {
  return listen<BackendLog>("backend://log", (e) => cb(e.payload));
}

/** 订阅生成进度（Tauri IPC 事件，备用通道） */
export function onTaskProgress(cb: (s: TaskStatus) => void): Promise<UnlistenFn> {
  return listen<TaskStatus>("task://progress", (e) => cb(e.payload));
}

/** 订阅生成完成（Tauri IPC 事件，备用通道） */
export function onTaskCompleted(cb: (s: TaskStatus) => void): Promise<UnlistenFn> {
  return listen<TaskStatus>("task://completed", (e) => cb(e.payload));
}

/** 订阅生成失败（Tauri IPC 事件，备用通道） */
export function onTaskFailed(cb: (s: TaskStatus) => void): Promise<UnlistenFn> {
  return listen<TaskStatus>("task://failed", (e) => cb(e.payload));
}

// ---------- 便捷组合：一键发起生成 + 自动收集进度（方案 B：WebSocket 直连）----------

/**
 * 高层 API：发起生成并返回一个"进度控制器"。
 *
 * 方案 B 流程：
 *   1. 通过 Tauri IPC 调用 generate_video 创建任务，获取 task_id
 *   2. 前端通过 WebSocket 直连后端 /progress/ws?task_id=xxx 接收进度
 *   3. WebSocket 断连时自动降级为 HTTP 轮询（GET /progress/status/{task_id}）
 *   4. 终态（完成/失败）时触发 onDone / onError 回调并清理所有连接
 *
 * 调用方只需：
 *   const ctrl = await generate(params);
 *   ctrl.onDone((s) => { /* 播放视频 *\/ });
 *   ctrl.onError((s) => { /* 显示错误 *\/ });
 *   // progress 由 useProgress 通过 WebSocket 自行接收
 */
export interface GenerationController {
  taskId: string;
  onDone(cb: (s: TaskStatus) => void): GenerationController;
  onError(cb: (s: TaskStatus) => void): GenerationController;
  cancel(): Promise<boolean>;
}

export async function generate(p: GenerateParams): Promise<GenerationController> {
  const taskId = await generateVideo(p);
  const unlisteners: UnlistenFn[] = [];
  let doneCb: ((s: TaskStatus) => void) | null = null;
  let errorCb: ((s: TaskStatus) => void) | null = null;

  const cleanup = () => unlisteners.forEach((u) => u());

  // 仍监听 Tauri IPC 终态事件作为备用（后端仍会通过 IPC 推送完成/失败）
  unlisteners.push(
    await onTaskCompleted((s) => {
      if (s.task_id === taskId) {
        doneCb?.(s);
        cleanup();
      }
    })
  );
  unlisteners.push(
    await onTaskFailed((s) => {
      if (s.task_id === taskId) {
        errorCb?.(s);
        cleanup();
      }
    })
  );

  return {
    taskId,
    onDone(cb: (s: TaskStatus) => void): GenerationController { doneCb = cb; return this as unknown as GenerationController; },
    onError(cb: (s: TaskStatus) => void): GenerationController { errorCb = cb; return this as unknown as GenerationController; },
    async cancel(): Promise<boolean> { return cancelTask(taskId); },
  };
}