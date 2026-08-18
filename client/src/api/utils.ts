/**
 * utils.ts — 通用工具函数
 * ============================================================
 * 从 VITE_API_BASE_URL 读取后端地址
 */

/**
 * 获取 API 基础 URL
 * 从环境变量 VITE_API_BASE_URL 读取，默认 http://127.0.0.1:9881
 * Tauri dev 模式下可能由 Tauri 层注入
 */
export function getApiBaseUrl(): string {
  const envBase = import.meta.env.VITE_API_BASE_URL;
  if (envBase) return envBase.replace(/\/$/, '');
  return 'http://127.0.0.1:9881';
}

/**
 * 获取 WebSocket 基础地址（将 http:// 替换为 ws://）
 */
export function getWsBaseUrl(): string {
  const base = getApiBaseUrl();
  return base.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
}