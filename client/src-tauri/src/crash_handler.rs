//! crash_handler.rs — 崩溃日志与恢复系统 (Task #13)
//! ============================================================================
//! 崩溃捕获分层：
//!   Layer 1 - Rust 层 panic → std::panic::set_hook 捕获，写入 crash_reports/
//!   Layer 2 - ComfyUI 子进程崩溃 → process_manager 自动重启（Task #9）
//!   Layer 3 - FastAPI 子进程崩溃 → process_manager 自动重启（Task #9）
//!   Layer 4 - 前端 Vue 崩溃 → 通过 app://reload_frontend 事件触发前端重载
//!   Layer 5 - Tauri 主进程崩溃 → 操作系统级崩溃报告（Windows WER / macOS CrashReporter）
//!
//! 崩溃日志格式：
//!   {
//!     "timestamp": "2026-08-20T12:34:56+08:00",
//!     "version": "0.1.0",
//!     "platform": "windows-x86_64",
//!     "stack_trace": "...",
//!     "user_action": ""
//!   }
//!
//! 日志保留策略：最多 10 条，按时间升序删除最旧的
//! 可选上报：用户授权后可 POST 到服务端（默认关闭）
//! ============================================================================

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::paths::config_dir;
use tauri::{AppHandle, Emitter};

// ---- 事件常量 ----
pub const EVENT_CRASH: &str = "app://crash";

// ---- 静态开关：用户是否同意上报崩溃 ----
pub static CRASH_UPLOAD_ENABLED: AtomicBool = AtomicBool::new(false);

/// 设置用户上报授权开关
pub fn set_crash_upload_enabled(enabled: bool) {
    CRASH_UPLOAD_ENABLED.store(enabled, Ordering::Relaxed);
}

/// 查看当前上报授权状态
pub fn is_crash_upload_enabled() -> bool {
    CRASH_UPLOAD_ENABLED.load(Ordering::Relaxed)
}

/// 单条崩溃记录结构
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CrashRecord {
    pub timestamp: DateTime<Utc>,
    pub version: String,
    pub platform: String,
    pub stack_trace: String,
    pub user_action: String,
}

impl CrashRecord {
    fn new(stack_trace: &str, user_action: &str) -> Self {
        Self {
            timestamp: Utc::now(),
            version: env!("CARGO_PKG_VERSION").to_string(),
            platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
            stack_trace: stack_trace.to_string(),
            user_action: user_action.to_string(),
        }
    }

    /// 将崩溃记录写入本地文件
    fn write_to_disk(&self, crash_dir: &PathBuf) -> std::io::Result<()> {
        let ts_str = self.timestamp.format("%Y%m%d_%H%M%S");
        let file_path = crash_dir.join(format!("crash_{}.json", ts_str));
        let json = serde_json::to_string_pretty(self)?;
        fs::write(&file_path, json)
    }
}

/// 获取崩溃日志存储目录
fn crash_dir() -> std::io::Result<PathBuf> {
    let base = config_dir()?;
    let crash_path = base.join("crash_reports");
    fs::create_dir_all(&crash_path)?;
    Ok(crash_path)
}

/// 保留最近 N 条崩溃日志
const MAX_CRASH_FILES: usize = 10;

fn prune_old_crashes(crash_dir: &PathBuf) -> std::io::Result<()> {
    let mut entries: Vec<PathBuf> = fs::read_dir(crash_dir)?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().map_or(false, |ext| ext == "json"))
        .collect();

    if entries.len() > MAX_CRASH_FILES {
        entries.sort(); // 按文件名排序（时间戳前缀 → 时间升序）
        let to_remove = entries.len() - MAX_CRASH_FILES;
        for path in entries.iter().take(to_remove) {
            let _ = fs::remove_file(path);
        }
    }
    Ok(())
}

/// 可选上报崩溃到服务端（仅当用户授权时）
async fn upload_crash(report: &CrashRecord) -> Result<(), String> {
    if !CRASH_UPLOAD_ENABLED.load(Ordering::Relaxed) {
        return Ok(()); // 用户未授权，跳过
    }

    let client = reqwest::Client::new();
    let resp = client
        .post("https://api.nexusvideo.com/api/v1/crash-report")
        .json(report)
        .send()
        .await
        .map_err(|e| e.to_string())?;

    if !resp.status().is_success() {
        log::warn!("[crash_handler] 上报失败 HTTP {}", resp.status());
    } else {
        log::info!("[crash_handler] 崩溃报告已上报");
    }
    Ok(())
}

/// 设置全局 panic hook。
/// 捕获 Rust 层 panic，记录崩溃日志，通过 Tauri 事件通知前端，可选上报服务端。
pub fn install_panic_hook(app: AppHandle) {
    // 保存默认 hook（防止递归）
    let default_hook = std::panic::take_hook();

    std::panic::set_hook(Box::new(move |info| {
        // 1) 调用默认 hook 输出到 stderr（保留原始行为）
        default_hook(info);

        // 2) 获取 panic 信息
        let message = if let Some(s) = info.payload().downcast_ref::<&str>() {
            s.to_string()
        } else if let Some(s) = info.payload().downcast_ref::<String>() {
            s.clone()
        } else {
            "未知 panic 类型".to_string()
        };

        // 3) 获取位置信息
        let location = info
            .location()
            .map(|l| format!("{} ({}:{})", l.file(), l.line(), l.column()))
            .unwrap_or_else(|| "未知位置".to_string());

        // 4) 生成栈追踪
        let stack = format!("{:#?}", backtrace::Backtrace::new());

        // 5) 构建崩溃记录
        let record = CrashRecord::new(&format!("{}\n位置: {}\n\n栈追踪:\n{}", message, location, stack), "");

        log::error!("[crash_handler] 捕获到 panic: {}", message);

        // 6) 写入本地文件
        if let Ok(dir) = crash_dir() {
            if let Err(e) = record.write_to_disk(&dir) {
                log::error!("[crash_handler] 写入崩溃日志失败: {e}");
            }
            let _ = prune_old_crashes(&dir);
        }

        // 7) 通过 Tauri 事件通知前端（可能此时主线程已不稳，降级处理）
        let crash_info = serde_json::json!({
            "timestamp": record.timestamp.to_rfc3339(),
            "version": record.version,
            "platform": record.platform,
            "message": message,
            "location": location,
            "stack_trace": stack,
            "report_path": "config/crash_reports/",
        });
        let _ = app.emit(EVENT_CRASH, crash_info);

        // 8) 异步上报（不阻塞 panic 处理流程）
        let record_clone = record.clone();
        std::thread::spawn(move || {
            let rt = tokio::runtime::Runtime::new();
            if let Ok(runtime) = rt {
                runtime.block_on(async {
                    let _ = upload_crash(&record_clone).await;
                });
            }
        });
    }));
}

/// 前端可调用此 IPC 命令重载自身（崩溃恢复）
#[tauri::command]
pub fn reload_frontend(window: tauri::WebviewWindow) {
    log::warn!("[crash_handler] 前端请求重载");
    let _ = window.eval("location.reload()");
}

/// 前端可调用此 IPC 命令获取崩溃日志列表
#[tauri::command]
pub fn get_crash_reports() -> Result<Vec<CrashRecord>, String> {
    let dir = crash_dir().map_err(|e| e.to_string())?;
    let mut records = Vec::new();

    for entry in fs::read_dir(&dir).map_err(|e| e.to_string())? {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
        if let Ok(record) = serde_json::from_str::<CrashRecord>(&content) {
            records.push(record);
        }
    }

    records.sort_by(|a, b| b.timestamp.cmp(&a.timestamp)); // 最新的在前
    Ok(records)
}

/// 前端可调用此 IPC 命令删除所有崩溃日志
#[tauri::command]
pub fn clear_crash_reports() -> Result<(), String> {
    let dir = crash_dir().map_err(|e| e.to_string())?;
    for entry in fs::read_dir(&dir).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.extension().and_then(|s| s.to_str()) == Some("json") {
            let _ = fs::remove_file(path);
        }
    }
    Ok(())
}