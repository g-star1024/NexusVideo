//! events.rs — Tauri 事件载荷定义（Rust → 前端）
//! ============================================================================
//! 前端通过 listen("事件名", handler) 订阅。所有事件统一用 NexusEvent 包装，
//! 便于前端用一个泛型监听器分发。
use serde::{Deserialize, Serialize};

/// 事件名常量（避免魔法字符串）
pub mod event_name {
    pub const BACKEND_STATUS: &str = "backend://status"; // 后端进程状态变化
    pub const BACKEND_LOG: &str = "backend://log";       // 子进程日志行
    pub const TASK_PROGRESS: &str = "task://progress";   // 生成进度
    pub const TASK_COMPLETED: &str = "task://completed"; // 生成完成
    pub const TASK_FAILED: &str = "task://failed";       // 生成失败
    pub const INIT_PROGRESS: &str = "init://progress";   // 首次启动初始化进度

    // ---- 联调对齐新增事件 ----
    pub const STATIC_READY: &str = "static://ready";     // 静态文件服务就绪
    pub const VIDEO_PREVIEW_URL: &str = "video://preview-url"; // 视频预览基础 URL

    // ---- Task #13: 自动更新 ----
    pub const UPDATE_AVAILABLE: &str = "update://available";
    pub const UPDATE_DOWNLOADING: &str = "update://downloading";
    pub const UPDATE_DOWNLOADED: &str = "update://downloaded";
    pub const UPDATE_ERROR: &str = "update://error";

    // ---- Task #13: 崩溃上报 ----
    pub const CRASH: &str = "app://crash";              // Rust 层 panic 崩溃报告
}

/// 进程状态枚举
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ProcState {
    Stopped,   // 未启动
    Starting,  // 启动中（已 spawn，等待健康检测通过）
    Running,   // 运行中
    Stopping,  // 停止中
    Crashed,   // 崩溃（异常退出）
    Restarting, // 崩溃后自动重启中
}

/// backend://status 载荷
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackendStatus {
    pub comfyui: ProcState,
    pub fastapi: ProcState,
    pub comfyui_url: String,
    pub fastapi_url: String,
    pub uptime_secs: u64,
    pub message: String,
}

/// backend://log 载荷
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BackendLog {
    pub source: String,   // "comfyui" | "fastapi"
    pub level: String,    // "info" | "warn" | "error"
    pub line: String,
    pub ts: String,       // ISO 时间
}

/// 任务状态枚举（与 FastAPI /task 返回对齐）
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum TaskState {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl TaskState {
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "queued" => Self::Queued,
            "running" => Self::Running,
            "completed" => Self::Completed,
            "failed" => Self::Failed,
            "cancelled" => Self::Cancelled,
            _ => Self::Running,
        }
    }
    pub fn is_terminal(&self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
}

/// task://progress 载荷（也是 GET /task/{id} 的返回结构）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskStatus {
    pub task_id: String,
    pub state: TaskState,
    pub progress: u8,          // 0-100
    pub step: String,          // 当前步骤描述（如"加载模型""采样中"）
    pub output_path: Option<String>,
    pub error: Option<String>,
}

/// 进度推送 WebSocket 载荷（Rust → 前端，task://progress）
/// 与前端顾如画实现的 payload 格式保持一致
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressPayload {
    pub task_id: String,         // 任务 ID
    pub progress: f32,           // 进度 0-100
    pub phase: u8,               // 阶段 0-3
    pub message: String,         // 当前阶段描述
    pub phase_messages: Vec<String>, // 各阶段描述列表
    pub estimated_text: String,  // 预估剩余时间文本
}

/// init://progress 载荷（首次启动模型解压进度，Task #9 用）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InitProgress {
    pub stage: String,         // "check_env" | "extract_model" | "verify_files" | "start_backend"
    pub progress: u8,          // 0-100
    pub message: String,
}
