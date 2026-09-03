/**
 * dialog.ts — 原生目录选择桥接层（前端 ↔ Tauri）
 * ============================================================
 * 这是与 tauri-pull-ipc（client-tauri-dev）的衔接点：
 *   - 前端期望从 Tauri 侧拿到一个「普通 JS 字符串路径」，例如 "D:/nexusvideo_staging"
 *   - 底层由 Tauri dialog 插件（@tauri-apps/plugin-dialog）提供原生目录选择框
 *   - Tauri 侧所需能力已在 capabilities/default.json 开启：dialog:allow-open
 *     （含 directory:true 选择），CSP connect-src 也已放行 127.0.0.1:9881
 *
 * 若 Tauri 不可用（纯 web 调试 / vite dev），动态 import 会失败，
 * 此处捕获并返回 null，由上层组件提供「手动输入路径」的回退入口。
 */
import type { OpenDialogOptions } from '@tauri-apps/plugin-dialog';

/** 默认建议目录（与后端 settings.staging_dir 默认值一致） */
export const DEFAULT_STAGING_DIR = 'D:/nexusvideo_staging';

/**
 * 打开原生目录选择对话框，返回用户选中的目录绝对路径。
 * @returns 选中的路径字符串；用户取消或环境不支持时返回 null。
 */
export async function selectInstallDir(defaultPath?: string): Promise<string | null> {
  try {
    // 动态 import，避免在非 Tauri 环境下加载插件导致的副作用
    const { open } = await import('@tauri-apps/plugin-dialog');
    const opts: OpenDialogOptions = {
      directory: true,
      multiple: false,
      defaultPath: defaultPath || DEFAULT_STAGING_DIR,
    };
    const selected = await open(opts);
    // open 在 directory:true + multiple:false 时返回 string | null
    if (Array.isArray(selected)) return selected[0] ?? null;
    return (selected as string | null) ?? null;
  } catch (e) {
    console.warn('[dialog] 原生目录选择不可用（非 Tauri 环境？），回退手动输入', e);
    return null;
  }
}

/**
 * 判断给定路径是否落在系统盘（C:）。
 * 设计文档要求：选到 C: 时弹出明确警示（空间/风险），但允许继续。
 */
export function isSystemDrive(path: string | null | undefined): boolean {
  if (!path) return false;
  // 归一化：去掉可能的 file:// 前缀
  const p = path.replace(/^file:\/\//i, '');
  return /^[cC]:[\\/]/i.test(p) || /^[cC]:$/i.test(p);
}
