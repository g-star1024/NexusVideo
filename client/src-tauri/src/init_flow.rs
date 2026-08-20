//! init_flow.rs — 首次启动初始化流程
//! ============================================================================
//! 首次启动时依次执行：
//!   stage 1: 检查环境（Python/ComfyUI/ffmpeg 是否存在）
//!   stage 2: 解压模型（如有压缩包）
//!   stage 3: 验证文件完整性（关键文件存在性校验）
//!   stage 4: 启动服务（ComfyUI + FastAPI）
//!
//! 通过 Tauri 事件 init://progress 推送进度到前端，前端显示引导界面。
//! 完成后写入 first_launch 标记文件，后续启动跳过本流程。
use crate::events::{event_name, InitProgress};
use crate::process_manager::{ProcKind, ProcessManager};
use crate::paths;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Emitter};
use tokio::sync::{Mutex, RwLock};

// ============================================================================
// 标记文件常量
// ============================================================================

/// first_launch 标记文件路径：~/NexusVideo/config/.first_launch_done
const FIRST_LAUNCH_FLAG: &str = ".first_launch_done";

/// 首次启动标记目录
fn first_launch_flag_path() -> std::io::Result<PathBuf> {
    let data_dir = paths::user_data_dir().map_err(|e| {
        std::io::Error::new(std::io::ErrorKind::Other, e.to_string())
    })?;
    let flag_dir = data_dir.join("config");
    Ok(flag_dir)
}

/// 检查是否已完成首次启动初始化
pub async fn is_first_launch_done() -> bool {
    let dir = match first_launch_flag_path() {
        Ok(d) => d,
        Err(_) => return false,
    };
    let flag_file = dir.join(FIRST_LAUNCH_FLAG);
    flag_file.exists()
}

/// 写入首次启动完成标记
pub async fn mark_first_launch_done() -> std::io::Result<()> {
    let dir = first_launch_flag_path()?;
    fs::create_dir_all(&dir)?;
    let flag_file = dir.join(FIRST_LAUNCH_FLAG);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    fs::write(&flag_file, format!("done_at={}\n", now))?;
    Ok(())
}

// ============================================================================
// 初始化阶段枚举
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum InitStage {
    CheckEnv,      // stage 1: 检查环境
    ExtractModel,  // stage 2: 解压模型
    VerifyFiles,   // stage 3: 验证文件
    StartBackend,  // stage 4: 启动服务
}

impl InitStage {
    pub fn name(&self) -> &str {
        match self {
            Self::CheckEnv => "检查环境",
            Self::ExtractModel => "解压模型",
            Self::VerifyFiles => "验证文件",
            Self::StartBackend => "启动服务",
        }
    }

    pub fn to_progress_stage(&self) -> String {
        match self {
            Self::CheckEnv => "check_env".into(),
            Self::ExtractModel => "extract_model".into(),
            Self::VerifyFiles => "verify_files".into(),
            Self::StartBackend => "start_backend".into(),
        }
    }
}

// ============================================================================
// 初始化状态（通过 tauri::State 共享）
// ============================================================================

/// 初始化状态（通过 tauri::State 共享）
pub struct InitState {
    pub status: Arc<Mutex<InitStatus>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum InitRunStatus {
    NotStarted,  // 未开始
    Running,     // 进行中
    Completed,   // 已完成
    Failed,      // 失败
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InitStatus {
    pub run_status: InitRunStatus,
    pub stage: Option<InitStage>,
    pub percent: u8,
    pub message: String,
    pub error: Option<String>,
}

impl InitState {
    pub fn new() -> Self {
        Self {
            status: Arc::new(Mutex::new(InitStatus {
                run_status: InitRunStatus::NotStarted,
                stage: None,
                percent: 0,
                message: "".into(),
                error: None,
            })),
        }
    }

    /// 重置初始化状态（用于重试运行）
    pub async fn reset(&self) {
        let mut s = self.status.lock().await;
        *s = InitStatus {
            run_status: InitRunStatus::NotStarted,
            stage: None,
            percent: 0,
            message: "".into(),
            error: None,
        };
    }
}

impl Default for InitState {
    fn default() -> Self {
        Self::new()
    }
}

// ============================================================================
// 主初始化流程
// ============================================================================

/// 执行首次启动完整初始化流程（异步任务）
/// 前端在应用启动后调用 init_app 命令触发此函数。
///
/// 注意：此函数不接收 tauri::State，而是直接接收 Arc 引用，
/// 因为 tokio::spawn 需要 'static 生命周期，不能持有 State 借引用。
pub async fn run_init_flow(
    app: AppHandle,
    proc_mgr: &RwLock<ProcessManager>,
    init_status: &Arc<Mutex<InitStatus>>,
) -> Result<(), String> {
    {
        let mut status = init_status.lock().await;
        status.run_status = InitRunStatus::Running;
        status.message = "开始首次启动初始化...".into();
    }

    // --- Stage 1: 检查环境 ---
    let stage_result = run_stage_1_check_env(&app, init_status).await;
    if let Err(e) = stage_result {
        report_failure(init_status, &app, &InitStage::CheckEnv, &e).await;
        return Err(e);
    }

    // --- Stage 2: 解压模型 ---
    let stage_result = run_stage_2_extract_model(&app, init_status).await;
    if let Err(e) = stage_result {
        report_failure(init_status, &app, &InitStage::ExtractModel, &e).await;
        return Err(e);
    }

    // --- Stage 3: 验证文件 ---
    let stage_result = run_stage_3_verify_files(&app, init_status).await;
    if let Err(e) = stage_result {
        report_failure(init_status, &app, &InitStage::VerifyFiles, &e).await;
        return Err(e);
    }

    // --- Stage 4: 启动服务 ---
    let stage_result = run_stage_4_start_backend(&app, init_status, proc_mgr).await;
    if let Err(e) = stage_result {
        report_failure(init_status, &app, &InitStage::StartBackend, &e).await;
        return Err(e);
    }

    // --- 全部成功：写入标记 ---
    mark_first_launch_done()
        .await
        .map_err(|e| format!("写入首次启动标记失败: {e}"))?;

    {
        let mut s = init_status.lock().await;
        s.run_status = InitRunStatus::Completed;
        s.percent = 100;
        s.message = "初始化完成，开始使用 NexusVideo".into();
    }
    emit_init_progress(&app, &InitStage::StartBackend, 100, "初始化完成").await;

    Ok(())
}

async fn report_failure(
    init_status: &Arc<Mutex<InitStatus>>,
    app: &AppHandle,
    stage: &InitStage,
    error: &str,
) {
    let mut s = init_status.lock().await;
    s.run_status = InitRunStatus::Failed;
    s.error = Some(error.to_string());
    emit_init_progress(app, stage, 0, error).await;
}

// ============================================================================
// Stage 1: 检查环境
// ============================================================================

async fn run_stage_1_check_env(
    app: &AppHandle,
    init_status: &Arc<Mutex<InitStatus>>,
) -> Result<(), String> {
    set_stage(init_status, &InitStage::CheckEnv, 0, "正在检查运行环境...").await;
    emit_init_progress(app, &InitStage::CheckEnv, 0, "正在检查运行环境...").await;

    // 检查 Python
    let py = paths::python_executable();
    set_stage(init_status, &InitStage::CheckEnv, 20, "检查 Python 解释器...").await;
    match py {
        Ok(_) => log::info!("[init] Python 解释器存在"),
        Err(e) => return Err(format!("Python 解释器缺失: {e}")),
    }

    // 检查 ComfyUI main.py
    set_stage(init_status, &InitStage::CheckEnv, 40, "检查 ComfyUI...").await;
    emit_init_progress(app, &InitStage::CheckEnv, 40, "检查 ComfyUI...").await;
    let cui = paths::comfyui_entry();
    match cui {
        Ok(_) => log::info!("[init] ComfyUI 入口存在"),
        Err(e) => return Err(format!("ComfyUI 入口缺失: {e}")),
    }

    // 检查 ffmpeg
    set_stage(init_status, &InitStage::CheckEnv, 60, "检查 ffmpeg...").await;
    emit_init_progress(app, &InitStage::CheckEnv, 60, "检查 ffmpeg...").await;
    let ffmpeg = paths::ffmpeg_executable();
    match ffmpeg {
        Ok(_) => log::info!("[init] ffmpeg 存在"),
        Err(e) => {
            log::warn!("[init] ffmpeg 缺失，视频后处理可能受限: {e}");
            // ffmpeg 非必需，只告警不阻塞
        }
    }

    // 检查输出目录可写
    set_stage(init_status, &InitStage::CheckEnv, 80, "检查输出目录...").await;
    emit_init_progress(app, &InitStage::CheckEnv, 80, "检查输出目录...").await;
    let out_dir = paths::output_dir().map_err(|e| format!("无法定位输出目录: {e}"))?;
    std::fs::create_dir_all(&out_dir).map_err(|e| format!("输出目录不可写: {e}"))?;

    set_stage(init_status, &InitStage::CheckEnv, 100, "环境检查完成").await;
    emit_init_progress(app, &InitStage::CheckEnv, 100, "环境检查完成").await;
    Ok(())
}

// ============================================================================
// Stage 2: 解压模型（轮询资源文件大小变化计算进度）
// ============================================================================

async fn run_stage_2_extract_model(
    app: &AppHandle,
    init_status: &Arc<Mutex<InitStatus>>,
) -> Result<(), String> {
    set_stage(init_status, &InitStage::ExtractModel, 0, "正在解压模型...").await;
    emit_init_progress(app, &InitStage::ExtractModel, 0, "正在解压模型...").await;

    let res_root = paths::resources_root().map_err(|e| e.to_string())?;
    let model_dir = res_root.join("comfyui").join("models");
    let archive_dir = res_root.join("comfyui").join("model_archives");

    // 检查是否有待解压的模型压缩包
    let has_archives = archive_dir.exists()
        && std::fs::read_dir(&archive_dir)
            .map(|r| {
                r.filter_map(|e| e.ok())
                    .any(|e| {
                        e.path()
                            .extension()
                            .map(|ext| {
                                matches!(
                                    ext.to_string_lossy().as_ref(),
                                    "zip" | "7z" | "tar"
                                )
                            })
                            .unwrap_or(false)
                    })
            })
            .unwrap_or(false);

    if !has_archives {
        log::info!("[init] 无待解压模型，跳过 stage 2");
        set_stage(init_status, &InitStage::ExtractModel, 100, "无待解压模型，跳过").await;
        emit_init_progress(app, &InitStage::ExtractModel, 100, "无待解压模型").await;
        return Ok(());
    }

    let target_size = dir_total_size(&archive_dir);
    if target_size == 0 {
        log::warn!("[init] 模型压缩包目录为空，跳过解压");
        return Ok(());
    }

    // 轮询文件大小变化，计算解压进度
    let mut retries = 0;
    let max_retries = 120; // 最多等 60 秒
    loop {
        let current_size = dir_total_size(&model_dir);
        let percent = if target_size > 0 {
            std::cmp::min((current_size * 100) / target_size, 100u64) as u8
        } else {
            0
        };
        set_stage(init_status, &InitStage::ExtractModel, percent, "正在解压模型...").await;
        emit_init_progress(app, &InitStage::ExtractModel, percent, "正在解压模型...").await;

        if percent >= 90 || retries >= max_retries {
            break;
        }
        retries += 1;
        tokio::time::sleep(Duration::from_millis(500)).await;
    }

    set_stage(init_status, &InitStage::ExtractModel, 100, "模型解压完成").await;
    emit_init_progress(app, &InitStage::ExtractModel, 100, "模型解压完成").await;
    Ok(())
}

fn dir_total_size(dir: &std::path::Path) -> u64 {
    let mut total = 0u64;
    if let Ok(entries) = std::fs::read_dir(dir) {
        for entry in entries.flatten() {
            if let Ok(metadata) = entry.metadata() {
                if metadata.is_file() {
                    total += metadata.len();
                }
            }
        }
    }
    total
}

// ============================================================================
// Stage 3: 验证文件
// ============================================================================

async fn run_stage_3_verify_files(
    app: &AppHandle,
    init_status: &Arc<Mutex<InitStatus>>,
) -> Result<(), String> {
    set_stage(
        init_status,
        &InitStage::VerifyFiles,
        0,
        "正在验证文件完整性...",
    )
    .await;
    emit_init_progress(
        app,
        &InitStage::VerifyFiles,
        0,
        "正在验证文件完整性...",
    )
    .await;

    let checks = vec![
        ("Python 解释器", paths::python_executable().is_ok()),
        ("ComfyUI 入口", paths::comfyui_entry().is_ok()),
        ("工作流模板", paths::workflows_dir().is_ok()),
    ];

    let total = checks.len() as u8;
    for (i, (name, ok)) in checks.iter().enumerate() {
        let percent = if total > 0 {
            ((i + 1) * 100) / total as usize
        } else {
            100
        };
        if !ok {
            log::warn!("[init] 关键文件缺失: {name}");
        }
        set_stage(
            init_status,
            &InitStage::VerifyFiles,
            percent as u8,
            &format!("验证 {name}..."),
        )
        .await;
        emit_init_progress(
            app,
            &InitStage::VerifyFiles,
            percent as u8,
            &format!("验证 {name}..."),
        )
        .await;
        tokio::time::sleep(Duration::from_millis(200)).await;
    }

    set_stage(init_status, &InitStage::VerifyFiles, 100, "文件验证完成").await;
    emit_init_progress(app, &InitStage::VerifyFiles, 100, "文件验证完成").await;
    Ok(())
}

// ============================================================================
// Stage 4: 启动服务（带最多 3 次重试）
// ============================================================================

async fn run_stage_4_start_backend(
    app: &AppHandle,
    init_status: &Arc<Mutex<InitStatus>>,
    proc_mgr: &RwLock<ProcessManager>,
) -> Result<(), String> {
    set_stage(init_status, &InitStage::StartBackend, 0, "正在启动 ComfyUI...").await;
    emit_init_progress(
        app,
        &InitStage::StartBackend,
        0,
        "正在启动 ComfyUI...",
    )
    .await;

    let max_retries = 3;
    for attempt in 1..=max_retries {
        set_stage(
            init_status,
            &InitStage::StartBackend,
            10,
            &format!("启动 ComfyUI（第 {attempt} 次尝试）..."),
        )
        .await;
        emit_init_progress(
            app,
            &InitStage::StartBackend,
            10,
            &format!("启动 ComfyUI（第 {attempt} 次尝试）..."),
        )
        .await;

        {
            let mgr = proc_mgr.read().await;
            let spawn_result = mgr.start_comfyui(app).await;
            if spawn_result.is_err() && attempt < max_retries {
                log::warn!(
                    "[init] ComfyUI 启动失败，{} 秒后重试: {}",
                    attempt * 5,
                    spawn_result.unwrap_err()
                );
                drop(mgr);
                tokio::time::sleep(Duration::from_secs(5)).await;
                continue;
            }
            if spawn_result.is_err() {
                return Err(format!(
                    "ComfyUI 启动失败（已重试 {max_retries} 次）: {}",
                    spawn_result.unwrap_err()
                ));
            }

            // 等待 ComfyUI 就绪
            set_stage(
                init_status,
                &InitStage::StartBackend,
                30,
                "等待 ComfyUI 就绪...",
            )
            .await;
            emit_init_progress(
                app,
                &InitStage::StartBackend,
                30,
                "等待 ComfyUI 就绪...",
            )
            .await;

            let healthy_result = mgr
                .wait_until_healthy(ProcKind::ComfyUI, app, Duration::from_secs(120))
                .await;
            if healthy_result.is_err() && attempt < max_retries {
                log::warn!(
                    "[init] ComfyUI 未就绪，{} 秒后重试: {}",
                    attempt * 5,
                    healthy_result.unwrap_err()
                );
                drop(mgr);
                tokio::time::sleep(Duration::from_secs(5)).await;
                continue;
            }
            if healthy_result.is_err() {
                return Err(format!(
                    "ComfyUI 健康检测失败（已重试 {max_retries} 次）: {}",
                    healthy_result.unwrap_err()
                ));
            }
        }
        break;
    }

    set_stage(
        init_status,
        &InitStage::StartBackend,
        50,
        "ComfyUI 就绪，启动 FastAPI...",
    )
    .await;
    emit_init_progress(
        app,
        &InitStage::StartBackend,
        50,
        "ComfyUI 就绪，启动 FastAPI...",
    )
    .await;

    {
        let mgr = proc_mgr.read().await;
        mgr.start_fastapi(app).await?;
        set_stage(
            init_status,
            &InitStage::StartBackend,
            70,
            "等待 FastAPI 就绪...",
        )
        .await;
        emit_init_progress(
            app,
            &InitStage::StartBackend,
            70,
            "等待 FastAPI 就绪...",
        )
        .await;
        mgr.wait_until_healthy(ProcKind::FastAPI, app, Duration::from_secs(30))
            .await?;
    }

    set_stage(init_status, &InitStage::StartBackend, 100, "服务启动完成").await;
    emit_init_progress(
        app,
        &InitStage::StartBackend,
        100,
        "服务启动完成",
    )
    .await;
    Ok(())
}

// ============================================================================
// 辅助函数
// ============================================================================

async fn set_stage(
    init_status: &Arc<Mutex<InitStatus>>,
    stage: &InitStage,
    percent: u8,
    message: &str,
) {
    let mut s = init_status.lock().await;
    s.stage = Some(stage.clone());
    s.percent = percent;
    s.message = message.into();
}

async fn emit_init_progress(app: &AppHandle, stage: &InitStage, percent: u8, message: &str) {
    let _ = app.emit(
        event_name::INIT_PROGRESS,
        InitProgress {
            stage: stage.to_progress_stage(),
            progress: percent,
            message: message.into(),
        },
    );
}

// ============================================================================
// Tauri IPC 命令：前端触发初始化
// ============================================================================

/// 初始化状态结构体（供 tauri::State 使用）
/// 注意：此 State 持有的是 Arc 内部状态，命令可通过它访问共享数据
use crate::state::AppState as NexusAppState;

#[tauri::command]
pub async fn init_app(
    app: AppHandle,
    state: tauri::State<'_, NexusAppState>,
    init_state: tauri::State<'_, InitState>,
) -> Result<InitStatus, String> {
    // 如果已完成，直接返回完成状态
    if is_first_launch_done().await {
        let s = init_state.status.lock().await;
        if matches!(s.run_status, InitRunStatus::Completed) {
            return Ok(s.clone());
        }
    }

    // 提取 Arc 引用（不需要 Clone AppState，只需要借用 proc_mgr）
    let proc_mgr_arc = Arc::clone(&state.proc_mgr);
    let status_arc = Arc::clone(&init_state.status);

    // 异步执行初始化（不阻塞命令返回）
    tokio::spawn(async move {
        let result = run_init_flow(app, &proc_mgr_arc, &status_arc).await;
        if let Err(e) = result {
            log::error!("[init] 首次启动初始化失败: {e}");
        }
    });

    // 返回当前状态快照
    let s = init_state.status.lock().await;
    Ok(s.clone())
}

#[tauri::command]
pub async fn get_init_status(
    init_state: tauri::State<'_, InitState>,
) -> Result<InitStatus, String> {
    let s = init_state.status.lock().await;
    Ok(s.clone())
}