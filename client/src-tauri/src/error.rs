//! error.rs — 统一错误类型
//! ============================================================================
//! 所有 Tauri command 返回 Result<T, String>，这里把结构化错误转成
//! 前端可读的字符串。桌面端"不静默吞错"：任何错误都带上下文上报。
use thiserror::Error;

#[derive(Debug, Error)]
pub enum NexusError {
    #[error("资源路径解析失败: {0}")]
    PathResolve(String),

    #[error("子进程未启动: {0}")]
    ProcessNotRunning(String),

    #[error("子进程启动失败 [{name}]: {reason}")]
    ProcessSpawn { name: String, reason: String },

    #[error("健康检测失败 [{target}]: {reason}（已重试 {retries} 次）")]
    HealthCheck {
        target: String,
        reason: String,
        retries: u32,
    },

    #[error("FastAPI 请求失败: {0}")]
    FastApiRequest(String),

    #[error("FastAPI 返回非 2xx: status={status}, body={body}")]
    FastApiStatus { status: u16, body: String },

    #[error("任务超时: task_id={task_id}, 超过 {timeout_secs}s")]
    TaskTimeout { task_id: String, timeout_secs: u64 },

    #[error("端口已被占用: {port}（可能存在残留进程）")]
    PortInUse { port: u16 },

    #[error("IO 错误: {0}")]
    Io(#[from] std::io::Error),

    #[error("序列化错误: {0}")]
    Serde(#[from] serde_json::Error),

    #[error("HTTP 错误: {0}")]
    Http(#[from] reqwest::Error),
}

/// 转 String 供 Tauri command 的 Err 返回
impl From<NexusError> for String {
    fn from(e: NexusError) -> String {
        e.to_string()
    }
}

pub type NexusResult<T> = Result<T, NexusError>;
