/**
 * settings.ts — 设置中心 API 封装层
 * ============================================================
 * 来源：程流深（后端）交付的设置中心接口
 * 端点：
 *   GET  /api/v1/settings/components       → 组件状态列表
 *   POST /api/v1/settings/components/{id}/action  → 执行操作（启动/安装/下载）
 *   GET  /api/v1/settings/system           → 系统信息
 *   GET  /api/v1/settings/logs             → 最近错误日志
 *
 * 设计要点：
 *   - 所有原始英文 status/action 在前端映射为中文标签
 *   - 前端不直接展示后端英文字段，小白用户无感知
 */
import { getApiBaseUrl } from './utils';

// ---------- 类型定义（与后端对齐） ----------

export type ComponentStatus = 'ok' | 'missing' | 'error' | 'checking';
export type ComponentAction = 'start' | 'install' | 'download' | 'repair';

export interface ComponentStatusItem {
  id: string;                 // component_id，如 comfyui
  name: string;               // 中文名称（后端回填 or 前端映射）
  status: ComponentStatus;
  version?: string;
  detail?: string;            // 详情，如 "RTX 4060, 8GB, CUDA 12.4"
  action?: ComponentAction;   // 可执行操作
  size?: string;              // 文件大小，如 "12.5GB"
  progress?: number;          // 下载/安装进度 0-100
}

export interface SystemInfo {
  os: string;
  gpu: string;
  vram: string;
  cuda: string;
  disk_free: string;
  ram_total: string;
  ram_used: string;
  version: string;
}

export interface ErrorLog {
  timestamp: string;
  time: string;     // 短格式时间，如 "10:00"
  level: 'ERROR' | 'WARN';
  source: string;   // 来源，如 "comfyui"
  message: string;
}

// ---------- 组件注册表：前端维护中文名称映射 ----------

const COMPONENT_LABELS: Record<string, { name: string; icon: string }> = {
  python_env:        { name: 'Python 运行环境', icon: '🐍' },
  comfyui:           { name: 'ComfyUI 推理引擎', icon: '🧠' },
  model_wan21_t2v:   { name: 'Wan2.1 文生视频模型', icon: '🎬' },
  model_wan21_i2v:   { name: 'Wan2.1 图生视频模型', icon: '🖼️' },
  model_cogvideox:   { name: 'CogVideoX 模型', icon: '🎞️' },
  model_animatediff: { name: 'AnimateDiff 模型', icon: '💫' },
  gpu_driver:        { name: 'NVIDIA GPU 驱动', icon: '🖥️' },
  ffmpeg:            { name: 'FFmpeg 编解码器', icon: '🎵' },
};

// status → 中文标签 + emoji
export function statusLabel(s: ComponentStatus): string {
  const map: Record<ComponentStatus, string> = {
    ok:       '正常',
    missing:  '未安装',
    error:    '异常',
    checking: '检测中…',
  };
  return map[s] || '未知';
}

export function statusEmoji(s: ComponentStatus): string {
  const map: Record<ComponentStatus, string> = {
    ok:       '🟢',
    missing:  '🔴',
    error:    '🟡',
    checking: '⚪',
  };
  return map[s] || '⚪';
}

// action → 中文操作按钮文案
export function actionLabel(a?: ComponentAction | null): string {
  if (!a) return '';
  const map: Record<string, string> = {
    start:    '启动',
    install:  '安装',
    download: '下载',
    repair:   '修复',
  };
  return map[a] || a;
}

export function actionDanger(a?: ComponentAction | null): boolean {
  // 下载/安装操作体积大，用危险按钮样式提示用户注意
  return a === 'download' || a === 'install';
}

// 为组件项填充中文标签
export function enrichComponent(item: {
  id: string;
  status: ComponentStatus;
  version?: string;
  detail?: string;
  action?: ComponentAction;
  size?: string;
  progress?: number;
}): ComponentStatusItem {
  const label = COMPONENT_LABELS[item.id];
  return {
    id: item.id,
    name: label?.name || item.id,
    status: item.status,
    version: item.version,
    detail: item.detail,
    action: item.action,
    size: item.size,
    progress: item.progress,
  };
}

// ---------- API 调用 ----------

const BASE = getApiBaseUrl();

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`设置接口请求失败 (${res.status}): ${err}`);
  }
  return res.json() as Promise<T>;
}

async function requestPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`设置操作失败 (${res.status}): ${err}`);
  }
  return res.json() as Promise<T>;
}

/**
 * 获取所有组件状态
 * 后端返回结构（可能为数组或对象包裹，兼容处理）
 */
export async function getComponents(): Promise<ComponentStatusItem[]> {
  const raw = await request<Record<string, unknown>[]>(
    '/api/v1/settings/components'
  );
  return raw.map(enrichComponent) as unknown as ComponentStatusItem[];
}

/**
 * 执行组件操作（启动/安装/下载）
 */
export async function executeComponentAction(
  componentId: string,
  action: ComponentAction,
): Promise<{ status: string; message?: string }> {
  return requestPost<{ status: string; message?: string }>(
    `/api/v1/settings/components/${componentId}/action`,
    { action },
  );
}

/**
 * 获取系统信息
 */
export async function getSystemInfo(): Promise<SystemInfo> {
  return request<SystemInfo>('/api/v1/settings/system');
}

/**
 * 获取最近错误日志（取最后 10 条 ERROR）
 */
export async function getErrorLogs(): Promise<ErrorLog[]> {
  const raw = await request<ErrorLog[]>('/api/v1/settings/logs');
  return raw.slice(-10);
}

/**
 * 一键刷新全部状态（组件 + 系统）
 */
export async function refreshAll(): Promise<{
  components: ComponentStatusItem[];
  system: SystemInfo;
  logs: ErrorLog[];
}> {
  const [components, system, logs] = await Promise.allSettled([
    getComponents(),
    getSystemInfo(),
    getErrorLogs(),
  ]);

  return {
    components: components.status === 'fulfilled' ? components.value : [],
    system: system.status === 'fulfilled' ? system.value : {
      os: '未知', gpu: '未知', vram: '-', cuda: '-',
      disk_free: '-', ram_total: '-', ram_used: '-', version: '-',
    },
    logs: logs.status === 'fulfilled' ? logs.value : [],
  };
}
