/**
 * install.ts — M2「设置中心 · 一键拉取」前端桥接层
 * ============================================================================
 * 作者：封易安（client-tauri-dev）
 * 这是 Tauri 侧暴露给设置中心 UI 的**唯一**入口，UI 只 import 本文件，
 * 不要自己去 invoke 命令名、也不要自己拼后端 URL——命令名/端口若变动，
 * 只改这里，UI 零改动。
 *
 * ---------------------------------------------------------------------------
 * 命令契约（Rust: client/src-tauri/src/install_bridge.rs）
 *   getDefaultInstallDir()            → string        平台默认目录
 *   auditInstallDir(dir?)             → DirAudit      目录体检（手输/回显时用）
 *   pickInstallDir(current?)          → DirAudit|null 弹原生目录框，null=用户取消
 *   startInstall(targetDir?, mirror?) → PullAccepted  触发拉取，拿 task_id
 *   verifyInstall(targetDir?)         → VerifyResult  模型齐全度自检
 *   subscribeInstallProgress(h)       → () => void    订阅进度，返回退订函数
 *
 * ---------------------------------------------------------------------------
 * 进度通道策略（UI 无需关心，subscribeInstallProgress 内部已自动处理）：
 *   1. 优先 EventSource 直连 GET /install/progress
 *      —— Windows 下已验证可用：tauri.conf.json 的 CSP connect-src 放行了
 *         127.0.0.1:9881，后端 CORS 为 *。
 *   2. 若首帧之前就 onerror（典型场景：macOS WKWebView 把明文 http 子请求
 *      判为混合内容拦截），自动切换到 Tauri 侧 SSE 转发通道（Rust reqwest
 *      拉流 → Tauri event）。切换对 UI 完全透明。
 */
import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { getApiBaseUrl } from './utils';

// ---------------------------------------------------------------------------
// 类型定义（与 Rust 侧 serde 结构一一对应）
// ---------------------------------------------------------------------------

/** 目录风险等级：ok=可用 / warn=可用但需显式提示 / block=禁止开始拉取 */
export type DirLevel = 'ok' | 'warn' | 'block';

export interface DirAudit {
  /** 规范化路径（正斜杠、无尾斜杠、盘符大写）——**发给后端就用这个值** */
  path: string;
  /** 平台原生显示形态（Windows 反斜杠）——只用于界面展示 */
  display_path: string;
  exists: boolean;
  writable: boolean;
  /** 盘符，如 "D:"；macOS 为 null */
  drive: string | null;
  /** 是否落在系统盘（Windows = C 盘）→ 必须显式警示用户 */
  is_system_drive: boolean;
  free_bytes: number;
  free_human: string;
  total_bytes: number;
  total_human: string;
  required_bytes: number;
  required_human: string;
  space_ok: boolean;
  level: DirLevel;
  /** 中文警示文案，按序展示即可（已本地化，无英文术语） */
  warnings: string[];
  is_default: boolean;
}

export interface PullAccepted {
  task_id: string;
  accepted: boolean;
  /** 实际提交给后端的目录（规范化后），用于界面回显 */
  target_dir: string;
}

export interface VerifyResult {
  /** 缺失的模型/组件名清单 */
  missing: string[];
  ok: boolean;
}

/** 后端 /install/progress 的一帧事件（字段与 install_service._emit 对齐） */
export interface InstallProgressEvent {
  stage: string;            // prepare | core | model | verify | failed
  message: string;          // 中文阶段文案，可直接展示
  current?: number;
  total?: number;
  task_id?: string | null;
  done?: boolean;           // true = 全部完成
  failed?: boolean;         // true = 失败终止
  ok?: boolean;
  warning?: boolean;        // 组件就位但自检有缺失
  missing?: string[];
  error?: string;
  /** "tauri-bridge" 表示这条是 Tauri 桥接层产生的连接类错误 */
  source?: string;
}

export interface InstallProgressHandlers {
  onProgress?: (e: InstallProgressEvent) => void;
  onDone?: (e: InstallProgressEvent) => void;
  onFailed?: (e: InstallProgressEvent) => void;
  /** 通道切换时回调，便于排障时在界面角落标注当前通道 */
  onChannel?: (channel: 'eventsource' | 'tauri-bridge') => void;
}

// ---------------------------------------------------------------------------
// 一、目录选择与体检
// ---------------------------------------------------------------------------

/** 平台默认拉取目录（Windows: D:/nexusvideo_staging；macOS: 用户数据目录下 staging） */
export function getDefaultInstallDir(): Promise<string> {
  return invoke<string>('get_default_install_dir');
}

/** 体检指定目录；dir 省略时体检平台默认目录 */
export function auditInstallDir(dir?: string): Promise<DirAudit> {
  return invoke<DirAudit>('audit_install_dir', { dir: dir ?? null });
}

/**
 * 弹出原生目录选择框。
 * @param current 对话框起始定位目录（一般传 store 里当前选中的路径）
 * @returns 选定目录的体检结果；**用户取消返回 null**（调用方必须处理 null）
 *
 * UI 约定：
 *   - level === 'warn'  → 必须把 warnings 显示出来（例如选到 C 盘），允许继续
 *   - level === 'block' → 禁用「开始拉取」按钮，并展示 warnings
 */
export function pickInstallDir(current?: string): Promise<DirAudit | null> {
  return invoke<DirAudit | null>('pick_install_dir', { current: current ?? null });
}

// ---------------------------------------------------------------------------
// 二、触发拉取与自检
// ---------------------------------------------------------------------------

/**
 * 触发一键拉取（Rust 代理 POST /install/pull）。
 * targetDir 省略时由后端使用其默认 staging 根。
 * Rust 侧会在发请求前复检目录，level=block 时直接 reject（错误信息为中文可读文案）。
 */
export function startInstall(targetDir?: string, mirror?: string): Promise<PullAccepted> {
  return invoke<PullAccepted>('start_install', {
    targetDir: targetDir ?? null,
    mirror: mirror ?? null,
  });
}

/** 模型齐全度自检（Rust 代理 GET /install/verify） */
export function verifyInstall(targetDir?: string): Promise<VerifyResult> {
  return invoke<VerifyResult>('verify_install', { targetDir: targetDir ?? null });
}

// ---------------------------------------------------------------------------
// 三、进度订阅（EventSource 优先，自动降级到 Tauri 桥接）
// ---------------------------------------------------------------------------

const EVT_PROGRESS = 'install://progress';
const EVT_DONE = 'install://done';
const EVT_FAILED = 'install://failed';

/** 首帧等待窗口：超过这个时间还没收到任何帧且已 onerror，就判定直连不可用 */
const FIRST_FRAME_GRACE_MS = 4000;

/**
 * 订阅拉取进度。返回退订函数（组件 onUnmounted 里务必调用，避免连接泄漏）。
 *
 * 用法：
 * ```ts
 * const stop = await subscribeInstallProgress({
 *   onProgress: e => (state.message = e.message),
 *   onDone:     ()=> (state.finished = true),
 *   onFailed:   e => (state.error = e.message),
 * });
 * // onUnmounted(() => stop());
 * ```
 */
export async function subscribeInstallProgress(
  h: InstallProgressHandlers,
): Promise<() => void> {
  let disposed = false;
  let gotFrame = false;
  let es: EventSource | null = null;
  let unlisteners: UnlistenFn[] = [];
  let graceTimer: ReturnType<typeof setTimeout> | null = null;

  const cleanupEs = () => {
    if (es) {
      es.close();
      es = null;
    }
    if (graceTimer) {
      clearTimeout(graceTimer);
      graceTimer = null;
    }
  };

  const dispatch = (e: InstallProgressEvent) => {
    gotFrame = true;
    h.onProgress?.(e);
    if (e.failed) h.onFailed?.(e);
    else if (e.done) h.onDone?.(e);
  };

  /** 降级：改用 Tauri 侧 SSE 转发 */
  const startBridge = async () => {
    if (disposed) return;
    cleanupEs();
    h.onChannel?.('tauri-bridge');
    unlisteners = await Promise.all([
      listen<InstallProgressEvent>(EVT_PROGRESS, (ev) => h.onProgress?.(ev.payload)),
      listen<InstallProgressEvent>(EVT_DONE, (ev) => h.onDone?.(ev.payload)),
      listen<InstallProgressEvent>(EVT_FAILED, (ev) => h.onFailed?.(ev.payload)),
    ]);
    await invoke('listen_install_progress');
  };

  // ---- 先试 EventSource 直连 ----
  try {
    h.onChannel?.('eventsource');
    es = new EventSource(`${getApiBaseUrl()}/install/progress`);

    es.onmessage = (ev) => {
      try {
        dispatch(JSON.parse(ev.data) as InstallProgressEvent);
      } catch {
        // 单帧坏数据不影响整条通道
      }
    };

    es.onerror = () => {
      // 已经收到过帧 → 视为流正常结束/后端收尾，不降级（避免重复订阅）
      if (gotFrame || disposed) {
        cleanupEs();
        return;
      }
      // 首帧之前就报错 → 直连不可用（macOS 混合内容拦截等），降级
      void startBridge();
    };

    // 兜底：某些 WebView 拦截时不触发 onerror，只是永远静默
    graceTimer = setTimeout(() => {
      if (!gotFrame && !disposed) void startBridge();
    }, FIRST_FRAME_GRACE_MS);
  } catch {
    // 构造 EventSource 本身就抛（CSP 直接拦截）→ 立刻降级
    await startBridge();
  }

  return () => {
    disposed = true;
    cleanupEs();
    unlisteners.forEach((u) => u());
    unlisteners = [];
    // 停掉 Rust 侧长连接（未启动时后端返回 false，无副作用）
    void invoke('stop_install_progress').catch(() => {});
  };
}
