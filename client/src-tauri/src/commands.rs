//! commands.rs — Tauri IPC 命令层（前端 → Rust → FastAPI → ComfyUI）
//! ============================================================================
//! 前端通过 invoke("command_name", { ... }) 调用。所有生成类请求由 Rust
//! 代理转发到本地 FastAPI(9881)，前端永远不直连 HTTP——这样未来 P2
//! "本地/云端智能路由"只需在 Rust 层切换目标地址，前端零改动。
use crate::events::{
    event_name, BackendStatus, InitProgress, ProcState, ProgressPayload, TaskState, TaskStatus,
};
use crate::init_flow;
use crate::process_manager::{ProcKind, ProcessManager};
use crate::state::AppState;
use base64::Engine;
use futures_util::{FutureExt, SinkExt};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Emitter, State};

// ===========================================================================
// 一、后端进程控制命令
// ===========================================================================

/// 启动后端：先 ComfyUI，就绪后启动 FastAPI，再等 FastAPI 就绪
#[tauri::command]
pub async fn start_backend(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<BackendStatus, String> {
    let mgr = state.proc_mgr.read().await;
    let mgr_ref: &ProcessManager = &mgr;

    // 1) 启动 ComfyUI
    mgr_ref.start_comfyui(&app).await?;
    broadcast(&app, "ComfyUI 启动中...").await;

    // 2) 等 ComfyUI 健康（模型加载可能较慢，给 120s）
    mgr_ref
        .wait_until_healthy(ProcKind::ComfyUI, &app, Duration::from_secs(120))
        .await?;
    broadcast(&app, "ComfyUI 就绪").await;

    // 3) 启动 FastAPI
    mgr_ref.start_fastapi(&app).await?;
    broadcast(&app, "FastAPI 启动中...").await;

    // 4) 等 FastAPI 健康
    mgr_ref
        .wait_until_healthy(ProcKind::FastAPI, &app, Duration::from_secs(30))
        .await?;
    broadcast(&app, "后端全部就绪").await;

    Ok(build_status(mgr_ref).await)
}

/// 优雅停止全部后端进程
#[tauri::command]
pub async fn stop_backend(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let mgr = state.proc_mgr.read().await;
    mgr.stop_all(&app).await;
    Ok(())
}

/// 查询后端状态
#[tauri::command]
pub async fn get_backend_status(
    state: State<'_, AppState>,
) -> Result<BackendStatus, String> {
    let mgr = state.proc_mgr.read().await;
    Ok(build_status(&mgr).await)
}

// ===========================================================================
// 二、生成请求代理命令（Rust → FastAPI）
// ===========================================================================

#[derive(Debug, Serialize, Deserialize)]
pub struct GenerateRequest {
    pub mode: String,            // txt2video | img2video | video2video
    pub prompt: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub image_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub params: Option<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct GenerateResponse {
    pub task_id: String,
}

/// 发起生成请求 → 转发到 FastAPI POST /generate
/// 同时启动一个轮询任务，把进度通过 task://progress 事件推给前端
#[tauri::command]
pub async fn generate_video(
    app: AppHandle,
    state: State<'_, AppState>,
    payload: GenerateRequest,
) -> Result<String, String> {
    let base = state.fastapi_base();
    let resp = state
        .http
        .post(format!("{base}/generate"))
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("FastAPI 请求失败: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("FastAPI 返回 {status}: {body}"));
    }

    let gen: GenerateResponse = resp
        .json()
        .await
        .map_err(|e| format!("解析 /generate 响应失败: {e}"))?;

    // 启动进度轮询任务（P0 用轮询，P1 可升级 WebSocket）
    let task_id = gen.task_id.clone();
    let http = state.http.clone();
    let base_clone = base.clone();
    let app_clone = app.clone();
    tokio::spawn(async move {
        poll_task_progress(app_clone, http, base_clone, task_id).await;
    });

    Ok(gen.task_id)
}

/// 主动查询任务状态（前端也可单次拉取）
#[tauri::command]
pub async fn query_task(
    state: State<'_, AppState>,
    task_id: String,
) -> Result<TaskStatus, String> {
    let url = format!("{}/task/{}", state.fastapi_base(), task_id);
    let status = state
        .http
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("查询任务失败: {e}"))?
        .json::<serde_json::Value>()
        .await
        .map_err(|e| format!("解析任务状态失败: {e}"))?;
    Ok(parse_task_status(&task_id, &status))
}

/// 取消任务
#[tauri::command]
pub async fn cancel_task(
    state: State<'_, AppState>,
    task_id: String,
) -> Result<bool, String> {
    let url = format!("{}/cancel/{}", state.fastapi_base(), task_id);
    let resp = state
        .http
        .post(&url)
        .send()
        .await
        .map_err(|e| format!("取消任务请求失败: {e}"))?;
    let v: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
    Ok(v.get("ok").and_then(|o| o.as_bool()).unwrap_or(false))
}

/// 获取可用模型列表
#[tauri::command]
pub async fn get_models(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let url = format!("{}/models", state.fastapi_base());
    state
        .http
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("获取模型列表失败: {e}"))?
        .json()
        .await
        .map_err(|e| format!("解析模型列表失败: {e}"))
}

// ===========================================================================
// 三、应用信息 / 文件系统命令
// ===========================================================================

/// 返回应用关键路径与版本（调试/诊断用）
#[tauri::command]
pub async fn get_app_info() -> Result<serde_json::Value, String> {
    let paths = crate::paths::dump_paths();
    Ok(serde_json::json!({
        "version": env!("CARGO_PKG_VERSION"),
        "platform": std::env::consts::OS,
        "paths": paths,
    }))
}

/// 在系统文件管理器中打开输出目录
#[tauri::command]
pub async fn open_output_dir() -> Result<String, String> {
    let dir = crate::paths::output_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer")
            .arg(dir.to_string_lossy().to_string())
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(dir.to_string_lossy().to_string())
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    Ok(dir.to_string_lossy().to_string())
}

// ===========================================================================
// 四、进度轮询任务（Rust → 前端事件推送）
// ===========================================================================

/// 每 1.5s 轮询 FastAPI /task/{id}，把状态 emit 给前端；
/// 任务进入终态（completed/failed/cancelled）后停止轮询并 emit 终态事件。
async fn poll_task_progress(
    app: AppHandle,
    http: reqwest::Client,
    base: String,
    task_id: String,
) {
    let url = format!("{base}/task/{task_id}");
    let mut elapsed = 0u64;
    let timeout = 600u64; // 10 分钟超时（与 config.py task_timeout 对齐）
    loop {
        if elapsed >= timeout {
            let _ = app.emit(
                event_name::TASK_FAILED,
                TaskStatus {
                    task_id: task_id.clone(),
                    state: TaskState::Failed,
                    progress: 0,
                    step: "timeout".into(),
                    output_path: None,
                    error: Some(format!("任务超过 {timeout}s 未完成")),
                },
            );
            break;
        }
        if let Ok(resp) = http.get(&url).send().await {
            if let Ok(v) = resp.json::<serde_json::Value>().await {
                let status = parse_task_status(&task_id, &v);
                let _ = app.emit(event_name::TASK_PROGRESS, status.clone());
                if status.state.is_terminal() {
                    match status.state {
                        TaskState::Completed => {
                            let _ = app.emit(event_name::TASK_COMPLETED, status);
                        }
                        TaskState::Failed | TaskState::Cancelled => {
                            let _ = app.emit(event_name::TASK_FAILED, status);
                        }
                        _ => {}
                    }
                    break;
                }
            }
        }
        tokio::time::sleep(Duration::from_millis(1500)).await;
        elapsed += 1;
    }
}

// ===========================================================================
// 辅助函数
// ===========================================================================

/// 把 FastAPI 返回的 JSON 解析成 TaskStatus
fn parse_task_status(task_id: &str, v: &serde_json::Value) -> TaskStatus {
    let state_str = v.get("state").and_then(|s| s.as_str()).unwrap_or("running");
    let state = TaskState::from_str(state_str);
    TaskStatus {
        task_id: task_id.to_string(),
        state: state.clone(),
        progress: v.get("progress").and_then(|p| p.as_u64()).unwrap_or(0) as u8,
        step: v.get("step").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        output_path: v
            .get("output_path")
            .and_then(|o| o.as_str())
            .map(|s| s.to_string()),
        error: v.get("error").and_then(|e| e.as_str()).map(|s| s.to_string()),
    }
}

/// 构造后端状态快照
async fn build_status(mgr: &ProcessManager) -> BackendStatus {
    let (cui, fapi) = mgr.snapshot().await;
    BackendStatus {
        comfyui: cui.state,
        fastapi: fapi.state,
        comfyui_url: format!("http://127.0.0.1:{}", crate::state::COMFYUI_PORT),
        fastapi_url: format!("http://127.0.0.1:{}", crate::state::FASTAPI_PORT),
        uptime_secs: 0,
        message: format!(
            "comfyui(pid={:?},restart={},mem={:?}MB,cpu={:?}%,ws={}) fastapi(pid={:?},restart={},mem={:?}MB,cpu={:?}%)",
            cui.pid, cui.restart_count, cui.memory_mb, cui.cpu_percent, cui.ws_ready,
            fapi.pid, fapi.restart_count, fapi.memory_mb, fapi.cpu_percent,
        ),
    }
}

/// 广播状态变化（带 message）
async fn broadcast(app: &AppHandle, msg: &str) {
    let _ = app.emit(
        event_name::BACKEND_STATUS,
        serde_json::json!({ "message": msg }),
    );
}

// 保留 InitProgress 导入（Task #9 首次启动流程将使用）
#[allow(dead_code)]
fn _init_progress_marker() -> InitProgress {
    InitProgress {
        stage: "".into(),
        progress: 0,
        message: "".into(),
    }
}

// ===========================================================================
// 五、Task #9 新增 IPC 命令
// ===========================================================================

/// 启动后端并触发完整启动流程（非首次启动）
/// 首次启动时需先调用 init_app 完成初始化，再调用此命令
#[tauri::command]
pub async fn start_backend_full(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<BackendStatus, String> {
    let is_first = init_flow::is_first_launch_done().await;

    if !is_first {
        log::info!("[backend] 首次启动，请先调用 init_app 完成初始化");
        return Err("首次启动未完成，请先调用 init_app".into());
    }

    let mgr = state.proc_mgr.read().await;
    let mgr_ref: &ProcessManager = &mgr;

    // 1) 启动 ComfyUI
    mgr_ref.start_comfyui(&app).await?;
    broadcast(&app, "ComfyUI 启动中...").await;

    // 2) 等 ComfyUI 健康（模型加载可能较慢，给 120s）
    mgr_ref
        .wait_until_healthy(ProcKind::ComfyUI, &app, Duration::from_secs(120))
        .await?;
    broadcast(&app, "ComfyUI 就绪").await;

    // 3) 检查 ComfyUI WebSocket 就绪
    let ws_ready = mgr_ref.check_comfyui_ws_ready(&app).await;
    if ws_ready {
        log::info!("[backend] ComfyUI WebSocket 已就绪");
    } else {
        log::warn!("[backend] ComfyUI WebSocket 暂未就绪，将在后续监控中重试");
    }

    // 4) 启动 FastAPI
    mgr_ref.start_fastapi(&app).await?;
    broadcast(&app, "FastAPI 启动中...").await;

    // 5) 等 FastAPI 健康
    mgr_ref
        .wait_until_healthy(ProcKind::FastAPI, &app, Duration::from_secs(30))
        .await?;
    broadcast(&app, "后端全部就绪").await;

    // 6) 启动后台监控任务（内存/CPU 轮询 + WebSocket 状态检查）
    mgr_ref.start_monitoring(app.clone()).await;
    log::info!("[backend] 后台监控任务已启动");

    Ok(build_status(mgr_ref).await)
}

/// 获取 ComfyUI/FastAPI 双进程实时状态（含内存/CPU/WS 状态）
#[tauri::command]
pub async fn get_process_status(
    state: State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    let mgr = state.proc_mgr.read().await;
    let (cui, fapi) = mgr.snapshot().await;
    Ok(serde_json::json!({
        "comfyui": {
            "state": cui.state,
            "pid": cui.pid,
            "restart_count": cui.restart_count,
            "memory_mb": cui.memory_mb,
            "cpu_percent": cui.cpu_percent,
            "ws_ready": cui.ws_ready,
            "url": format!("http://127.0.0.1:{}", crate::state::COMFYUI_PORT),
        },
        "fastapi": {
            "state": fapi.state,
            "pid": fapi.pid,
            "restart_count": fapi.restart_count,
            "memory_mb": fapi.memory_mb,
            "cpu_percent": fapi.cpu_percent,
            "url": format!("http://127.0.0.1:{}", crate::state::FASTAPI_PORT),
        },
    }))
}

// ===========================================================================
// 六、联调对齐 — 文件上传 + 进度推送桥接
// ===========================================================================

// ---- 文件上传 ----

/// 单次上传大小限制：图片 10MB
const MAX_IMAGE_SIZE: u64 = 10 * 1024 * 1024;
/// 单次上传大小限制：视频 200MB
const MAX_VIDEO_SIZE: u64 = 200 * 1024 * 1024;

/// 允许的上传文件扩展名白名单
const ALLOWED_EXTENSIONS: &[&str] = &[
    "png", "jpg", "jpeg", "webp", "bmp", // 图片
    "mp4", "mov", "avi", "mkv", "webm",   // 视频
];

/// 接收前端传来的 base64 编码文件，保存到本地 uploads/{task_id}/{filename}
///
/// 参数：
///   file_path — base64 编码的文件内容（前缀如 "data:image/png;base64," 会被剥离）
///   task_id   — 任务 ID，用于分组存储
///   filename  — 原始文件名（含扩展名）
///   file_type — "image" 或 "video"，用于大小限制
///
/// 返回：本地文件绝对路径（供前端传入后端 generate 接口）
#[tauri::command]
pub async fn upload_file(
    file_path: String,
    task_id: String,
    filename: String,
    file_type: String,
) -> Result<String, String> {
    // 1) 校验文件类型
    let file_type = file_type.to_lowercase();
    let (max_size, type_label) = match file_type.as_str() {
        "image" => (MAX_IMAGE_SIZE, "图片"),
        "video" => (MAX_VIDEO_SIZE, "视频"),
        _ => return Err(format!("不支持的文件类型: {}（仅支持 image/video）", file_type)),
    };

    // 2) 扩展名白名单校验
    let ext = get_extension(&filename)
        .ok_or_else(|| "文件名缺失扩展名".to_string())?
        .to_lowercase();
    if !ALLOWED_EXTENSIONS.contains(&ext.as_str()) {
        return Err(format!("不允许的文件扩展名: .{ext}（仅允许: {}）", ALLOWED_EXTENSIONS.join(", ")));
    }

    // 3) 剥离 data URL 前缀
    let raw = strip_data_url_prefix(&file_path);

    // 4) Base64 解码
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(&raw)
        .map_err(|e| format!("Base64 解码失败: {e}"))?;

    // 5) 大小限制校验
    let max_mb = max_size / (1024 * 1024);
    if (decoded.len() as u64) > max_size {
        let mb = decoded.len() as f64 / (1024.0 * 1024.0);
        return Err(format!(
            "上传文件过大（{:.1}MB），{type_label} 文件限制 {max_mb}MB",
            mb
        ));
    }

    // 6) 清理文件名（移除路径分隔符等危险字符）
    let safe_name = sanitize_filename(&filename);

    // 7) 写入本地文件
    let task_dir = crate::paths::upload_task_dir(&task_id)?;
    let dest_path = task_dir.join(&safe_name);
    tokio::fs::write(&dest_path, &decoded)
        .await
        .map_err(|e| format!("写入文件失败 {}: {e}", dest_path.display()))?;

    log::info!(
        "[upload] 文件上传成功: task_id={}, filename={}, size={}B, path={}",
        task_id,
        safe_name,
        decoded.len(),
        dest_path.display()
    );

    Ok(dest_path.to_string_lossy().to_string())
}

/// 将用户通过文件选择器选中的本地文件移动到 uploads/{task_id}/ 目录
///
/// 参数：
///   source    — 源文件绝对路径（前端通过 tauri dialog 插件获得）
///   task_id   — 任务 ID
///   filename  — 目标文件名（可选，不传则使用原文件名）
///   file_type — "image" 或 "video"
///
/// 返回：移动后在 uploads 目录中的新路径
#[tauri::command]
pub async fn move_uploaded_file(
    source: String,
    task_id: String,
    filename: Option<String>,
    file_type: String,
) -> Result<String, String> {
    // 1) 校验文件类型
    let file_type = file_type.to_lowercase();
    let (max_size, type_label) = match file_type.as_str() {
        "image" => (MAX_IMAGE_SIZE, "图片"),
        "video" => (MAX_VIDEO_SIZE, "视频"),
        _ => return Err(format!("不支持的文件类型: {}（仅支持 image/video）", file_type)),
    };

    // 2) 校验源文件存在且读取元数据
    let source_path = std::path::PathBuf::from(&source);
    if !source_path.exists() {
        return Err(format!("源文件不存在: {source}"));
    }
    let meta = std::fs::metadata(&source_path)
        .map_err(|e| format!("读取源文件元数据失败: {e}"))?;

    // 3) 大小限制
    let max_mb = max_size / (1024 * 1024);
    if meta.len() > max_size {
        let mb = meta.len() as f64 / (1024.0 * 1024.0);
        return Err(format!(
            "文件过大（{:.1}MB），{type_label} 文件限制 {max_mb}MB",
            mb
        ));
    }

    // 4) 扩展名白名单
    let original_ext = source_path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();
    if !ALLOWED_EXTENSIONS.contains(&original_ext.as_str()) {
        return Err(format!(
            "不允许的文件扩展名: .{original_ext}（仅允许: {}）",
            ALLOWED_EXTENSIONS.join(", ")
        ));
    }

    // 5) 确定目标文件名
    let safe_name = match filename {
        Some(name) => sanitize_filename(&name),
        None => {
            let orig = source_path.file_name().unwrap_or_default().to_string_lossy().to_string();
            sanitize_filename(&orig)
        }
    };

    // 6) 使用 std::fs::copy 复制到 uploads 目录（兼容跨设备移动）
    let task_dir = crate::paths::upload_task_dir(&task_id)?;
    let dest_path = task_dir.join(&safe_name);

    std::fs::copy(&source_path, &dest_path)
        .map_err(|e| format!("复制文件失败: {e}"))?;

    log::info!(
        "[move_uploaded] 文件移动成功: source={} → dest={}, size={}B",
        source,
        dest_path.display(),
        meta.len()
    );

    Ok(dest_path.to_string_lossy().to_string())
}

// ---- 进度推送桥接（WebSocket 方案 A） ----

/// 启动 WebSocket 连接后端的 /progress/ws?task_id=xxx，
/// 接收后端推送的进度后通过 Tauri 事件 `task://progress` 转发给前端
///
/// 参数：task_id — 任务 ID
/// 返回：任务 ID（确认已启动监听）
#[tauri::command]
pub async fn listen_progress(
    app: AppHandle,
    state: State<'_, AppState>,
    task_id: String,
) -> Result<String, String> {
    let base = state.fastapi_base();
    let task_id_clone = task_id.clone();
    let ws_url = format!("{base}/progress/ws?task_id={task_id}");
    // 先克隆一份供下方日志使用：ws_url 即将被 move 进 tokio::spawn 的 'static future
    let ws_url_log = ws_url.clone();

    // 用 Arc 包装 Notify，确保可安全传递给 'static future（tokio::spawn）
    let cancel = Arc::new(tokio::sync::Notify::new());

    let app_clone = app.clone();
    let cancel_clone = Arc::clone(&cancel);
    let handle = tokio::spawn(async move {
        listen_progress_loop(ws_url, task_id_clone, app_clone, cancel_clone).await;
    });

    // 存储句柄供 stop_progress 使用
    state
        .progress_handles
        .write()
        .await
        .insert(task_id.clone(), (handle, cancel));

    log::info!("[progress] 已启动 WebSocket 进度监听: task_id={task_id}, url={ws_url_log}");
    Ok(task_id)
}

/// 停止指定任务的进度监听
///
/// 参数：task_id — 任务 ID
/// 返回：true 表示成功停止
#[tauri::command]
pub async fn stop_progress(
    state: State<'_, AppState>,
    task_id: String,
) -> Result<bool, String> {
    let mut handles = state.progress_handles.write().await;
    if let Some((handle, cancel)) = handles.remove(&task_id) {
        // 通知循环退出 + 直接 abort 任务
        cancel.notify_one();
        handle.abort();
        log::info!("[progress] 已停止 WebSocket 进度监听: task_id={task_id}");
        Ok(true)
    } else {
        log::warn!("[progress] 未找到进度监听任务: task_id={task_id}");
        Ok(false)
    }
}

/// WebSocket 进度监听循环
async fn listen_progress_loop(
    ws_url: String,
    task_id: String,
    app: AppHandle,
    cancel: Arc<tokio::sync::Notify>,
) {
    use futures_util::StreamExt;
    use tokio_tungstenite::tungstenite::Message;

    // 重试策略：连接失败后最多重试 5 次
    let max_retries = 5;
    let mut retry = 0;

    loop {
        // 检查是否收到取消信号
        if cancel.notified().now_or_never().is_some() {
            log::info!("[progress] 收到停止信号，退出监听: task_id={task_id}");
            return;
        }

        // 建立 WebSocket 连接
        let (ws_stream, _) = match tokio_tungstenite::connect_async(&ws_url).await {
            Ok(result) => result,
            Err(e) => {
                retry += 1;
                if retry > max_retries {
                    log::error!("[progress] WebSocket 连接失败（已重试 {retry} 次）: {e}");
                    return;
                }
                log::warn!("[progress] WebSocket 连接失败（第 {retry} 次重试）: {e}");
                tokio::time::sleep(Duration::from_secs(2)).await;
                continue;
            }
        };

        retry = 0; // 连接成功，重置重试计数

        // 拆分读写
        let (write, mut read) = ws_stream.split();

        // 后台发送 ping 维持连接（每 30s）
        let ping_cancel = tokio::sync::Notify::new();
        let _ = tokio::spawn(async move {
            let mut interval = tokio::time::interval(Duration::from_secs(30));
            loop {
                tokio::select! {
                    _ = interval.tick() => {
                        let _ = write.send(Message::Ping(vec![])).await;
                    }
                    _ = ping_cancel.notified() => break,
                }
            }
        });

        // 读取消息并转发给前端
        'read_loop: loop {
            // 检查取消
            if cancel.notified().now_or_never().is_some() {
                ping_cancel.notify_one();
                log::info!("[progress] 监听循环收到停止信号: task_id={task_id}");
                break;
            }

            let msg = tokio::select! {
                result = read.next() => match result {
                    Some(msg) => msg,
                    None => {
                        ping_cancel.notify_one();
                        log::info!("[progress] WebSocket 服务端关闭连接: task_id={task_id}");
                        break;
                    }
                },
                _ = cancel.notified() => {
                    ping_cancel.notify_one();
                    log::info!("[progress] 监听循环收到停止信号（select）: task_id={task_id}");
                    break;
                }
            };

            let msg = match msg {
                Ok(m) => m,
                Err(e) => {
                    ping_cancel.notify_one();
                    log::warn!("[progress] WebSocket 读取错误: {e}");
                    break;
                }
            };

            match msg {
                Message::Text(text) => {
                    // 解析 JSON 为 ProgressPayload
                    let payload: ProgressPayload = match serde_json::from_str(&text) {
                        Ok(p) => p,
                        Err(e) => {
                            log::debug!("[progress] 解析进度 JSON 失败: {e}");
                            continue 'read_loop;
                        }
                    };

                    // 通过 Tauri 事件推送给前端
                    let _ = app.emit(event_name::TASK_PROGRESS, &payload);

                    // 后端推送终态（progress=100）后自动退出
                    if payload.progress >= 100.0 {
                        ping_cancel.notify_one();
                        log::info!("[progress] 任务 {task_id} 完成，停止监听");
                        break 'read_loop;
                    }
                }
                Message::Binary(_) | Message::Ping(_) | Message::Pong(_) => {
                    // 二进制/心跳消息忽略
                }
                Message::Close(_) => {
                    ping_cancel.notify_one();
                    log::info!("[progress] WebSocket 服务端关闭连接: task_id={task_id}");
                    break 'read_loop;
                }
                Message::Frame(_) => {
                    // 原始帧忽略
                }
            }
        }

        // 连接断开后退出外层循环
        log::info!("[progress] WebSocket 连接断开，退出: task_id={task_id}");
        break;
    }
}

// ===========================================================================
// 文件上传辅助函数
// ===========================================================================

/// 从 data URL 中剥离前缀，提取纯 base64 内容
fn strip_data_url_prefix(input: &str) -> String {
    // 格式：data:<mime>;base64,<content>
    if let Some(idx) = input.find(",") {
        input[idx + 1..].to_string()
    } else {
        input.to_string()
    }
}

/// 获取文件扩展名
fn get_extension(filename: &str) -> Option<String> {
    let path = std::path::Path::new(filename);
    path.extension().and_then(|e| e.to_str()).map(|s| s.to_string())
}

/// 清理文件名，移除路径分隔符和危险字符
fn sanitize_filename(name: &str) -> String {
    // 移除路径分隔符，只保留文件名
    let path = std::path::Path::new(name);
    let file_part = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or(name.to_string());

    // 移除危险字符：仅保留字母、数字、下划线、连字符、点号、中文字符
    let cleaned: String = file_part
        .chars()
        .filter(|c| {
            c.is_alphanumeric()
                || *c == '_'
                || *c == '-'
                || *c == '.'
                || (*c >= '\u{4e00}' && *c <= '\u{9fff}')
        })
        .collect();

    if cleaned.is_empty() {
        // 如果清理后为空，生成随机名
        format!("upload_{}.bin", chrono_now())
    } else {
        cleaned
    }
}

/// 简易 ISO 时间戳（用于生成默认文件名）
fn chrono_now() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}
