//! state.rs — 全局共享状态
//! ============================================================================
//! 通过 tauri::State<AppState> 在所有 command 间共享：
//! - HTTP 客户端（复用连接池）
//! - 子进程管理器
//! - 端口配置
//! - 进度监听 WebSocket 句柄（联调对齐新增）
use crate::process_manager::ProcessManager;
use reqwest::Client;
use std::collections::HashMap;
use std::time::Duration;
use std::sync::Arc;
use tokio::sync::RwLock;

/// 端口常量（与 backend/config.py 对齐）
///   ComfyUI : 8188（白皮书指定）
///   FastAPI : 9881（python-backend-core config.py 实际值）
pub const COMFYUI_PORT: u16 = 8188;
pub const FASTAPI_PORT: u16 = 9881;
pub const COMFYUI_HOST: &str = "127.0.0.1";
pub const FASTAPI_HOST: &str = "127.0.0.1";

pub struct AppState {
    /// 复用连接池的 HTTP 客户端（Rust → FastAPI 转发）
    pub http: Client,
    /// 子进程管理器（ComfyUI + FastAPI 生命周期）
    /// 用 Arc<RwLock<>> 包装以支持 tokio::spawn 内共享（'static 生命周期）
    pub proc_mgr: Arc<RwLock<ProcessManager>>,

    /// 进度监听 WebSocket 任务句柄（task_id → (JoinHandle, Arc<Notify>)）
    /// 用 Arc<Notify> 确保可安全传递给 'static future（tokio::spawn）
    pub progress_handles: RwLock<HashMap<String, (tokio::task::JoinHandle<()>, Arc<tokio::sync::Notify>)>>,
}

impl AppState {
    pub fn new() -> Self {
        let http = Client::builder()
            .timeout(Duration::from_secs(30)) // 普通请求超时
            .connect_timeout(Duration::from_secs(5))
            .pool_max_idle_per_host(4)
            .build()
            .expect("HTTP client 构建失败");
        Self {
            http,
            proc_mgr: Arc::new(RwLock::new(ProcessManager::new())),
            progress_handles: RwLock::new(HashMap::new()),
        }
    }

    /// FastAPI base url
    pub fn fastapi_base(&self) -> String {
        format!("http://{}:{}", FASTAPI_HOST, FASTAPI_PORT)
    }

    /// ComfyUI base url
    pub fn comfyui_base(&self) -> String {
        format!("http://{}:{}", COMFYUI_HOST, COMFYUI_PORT)
    }

    /// 清理所有进度监听句柄（应用退出时调用）
    pub async fn clear_progress_handles(&self) {
        let mut handles = self.progress_handles.write().await;
        for (task_id, (handle, cancel)) in handles.drain() {
            cancel.notify_one();
            handle.abort();
            log::info!("[state] 已清理进度监听句柄: task_id={task_id}");
        }
    }
}

impl Default for AppState {
    fn default() -> Self {
        Self::new()
    }
}