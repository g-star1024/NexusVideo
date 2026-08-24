//! paths.rs — 资源路径解析（开发期 vs 打包期）
//! ============================================================================
//! 白皮书物理目录结构：
//!   NexusVideo/
//!   ├── NexusVideo.exe
//!   ├── resources/
//!   │   ├── python_env/      嵌入式 Python（python.exe / bin/python）
//!   │   ├── comfyui/         ComfyUI 便携版（含 main.py / custom_nodes / models）
//!   │   ├── workflows/       txt2video.json / img2video.json
//!   │   └── ffmpeg/          视频后处理（合成/转码）
//!   └── config/
//!
//! 关键策略：resources/ 与 exe 同级（不走 Tauri 内置 resource_dir），
//! 因为 python_env + comfyui + models 体积可达 15-30GB，交给安装器放置，
//! Tauri 只负责"按相对路径找到它们"。
use crate::error::{NexusError, NexusResult};
use std::path::{Path, PathBuf};

/// 资源根目录：开发期 = <repo>/resources，打包期 = <exe_dir>/resources
pub fn resources_root() -> NexusResult<PathBuf> {
    #[cfg(debug_assertions)]
    {
        // 开发期：CARGO_MANIFEST_DIR = client/src-tauri，向上两级到 repo 根
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repo_root = manifest
            .ancestors()
            .nth(2)
            .ok_or_else(|| NexusError::PathResolve("无法定位 repo 根目录".into()))?;
        Ok(repo_root.join("resources"))
    }
    #[cfg(not(debug_assertions))]
    {
        // 打包期：exe 同级的 resources/
        let exe = std::env::current_exe()?;
        let exe_dir = exe
            .parent()
            .ok_or_else(|| NexusError::PathResolve("无法定位 exe 目录".into()))?;
        Ok(exe_dir.join("resources"))
    }
}

/// 拼接资源子路径，并校验存在性
pub fn resource(sub: &str) -> NexusResult<PathBuf> {
    let p = resources_root()?.join(sub);
    if !p.exists() {
        return Err(NexusError::PathResolve(format!(
            "资源不存在: {}（期望路径 {}）",
            sub,
            p.display()
        )));
    }
    Ok(p)
}

/// CARGO_MANIFEST_DIR = <repo>/client/src-tauri（编译期确定，不受运行时 cwd 影响）
fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// 开发期「就近资源」候选（兼顾多种本地布局，按优先级排列）：
///   1) client/src-tauri/resources/<sub>   —— 与 CI 构建位置一致（推荐）
///   2) client/resources/<sub>             —— CARGO_MANIFEST_DIR 的上级
///   3) <repo>/resources/<sub>             —— resources_root() 开发期约定（repo 根）
/// 打包后 resource() 已能命中，这些候选只在开发期（resources 未预先构建）兜底。
fn dev_resources_candidates(sub: &str) -> Vec<PathBuf> {
    let m = manifest_dir();
    vec![
        m.join("resources").join(sub),
        m.join("../resources").join(sub),
        m.join("../../resources").join(sub),
    ]
}

/// 开发期仓库内 backend 候选（backend 实际位于 <repo>/backend）：
///   1) <repo>/backend/local_server.py        —— CARGO_MANIFEST_DIR/../../backend
///   2) client/backend/local_server.py        —— 兼容 backend 在 client/ 的布局
fn dev_backend_candidates() -> Vec<PathBuf> {
    let m = manifest_dir();
    vec![
        m.join("../../backend/local_server.py"),
        m.join("../backend/local_server.py"),
    ]
}

/// 在 PATH 上定位系统 Python（python3 / python）。
/// 开发期没有预建 venv 时，用系统 Python 直接拉起 FastAPI，避免 dev 强依赖打包资源。
fn system_python() -> NexusResult<PathBuf> {
    for name in ["python3", "python"] {
        // 用 --version 探测是否可用（Command::new 直接走 OS PATH 解析）
        if std::process::Command::new(name)
            .arg("--version")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
        {
            return Ok(PathBuf::from(name));
        }
    }
    Err(NexusError::PathResolve(
        "未找到系统 Python（python3 / python 均不在 PATH 中）。开发期请安装 Python，\
         或在 client/src-tauri 下执行 `python -m venv resources/python_env` 构建本地 venv"
            .into(),
    ))
}

/// 嵌入式 Python 解释器路径
///   Windows: resources/python_env/python.exe
///   macOS:   resources/python_env/bin/python3
///
/// 解析顺序（「不静默失败」）：
///   1) 打包/构建好的 venv（release 必然命中；dev 若已建好 resources 也命中）
///   2) 开发期就近资源候选（client/src-tauri/resources、client/resources、<repo>/resources）
///   3) 系统 PATH 上的 python3 / python（dev 不强制预建 venv 也能起后端）
pub fn python_executable() -> NexusResult<PathBuf> {
    let sub = if cfg!(target_os = "windows") {
        "python_env/python.exe"
    } else {
        "python_env/bin/python3"
    };

    // 1) 标准 resource 路径
    if let Ok(p) = resource(sub) {
        return Ok(p);
    }
    // 2) 开发期就近兜底（与 CI 构建位置一致，或历史布局）
    for c in dev_resources_candidates(sub) {
        if c.exists() {
            return Ok(c);
        }
    }
    // 3) 系统 Python
    system_python()
}

/// ComfyUI 便携版根目录（main.py 所在）
pub fn comfyui_dir() -> NexusResult<PathBuf> {
    resource("comfyui")
}

/// ComfyUI 入口 main.py 完整路径
pub fn comfyui_entry() -> NexusResult<PathBuf> {
    Ok(comfyui_dir()?.join("main.py"))
}

/// FastAPI local_server.py 路径（打包期随 resources 一同放置）
///
/// 解析顺序（「不静默失败」）：
///   1) resources/python_env/local_server.py（venv 内自带入口）
///   2) resources/backend/local_server.py    （CI 把 backend 拷到 resources/backend）
///   3) 开发期仓库内 backend/local_server.py  （<repo>/backend 或 client/backend）
pub fn fastapi_entry() -> NexusResult<PathBuf> {
    if let Ok(p) = resource("python_env/local_server.py") {
        return Ok(p);
    }
    if let Ok(p) = resource("backend/local_server.py") {
        return Ok(p);
    }
    for c in dev_backend_candidates() {
        if c.exists() {
            return Ok(c);
        }
    }
    Err(NexusError::PathResolve(
        "未找到 FastAPI 入口 local_server.py（已尝试 resources/python_env、resources/backend 及开发期仓库路径）".into(),
    ))
}

/// ffmpeg 可执行路径
pub fn ffmpeg_executable() -> NexusResult<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        resource("ffmpeg/ffmpeg.exe")
    }
    #[cfg(not(target_os = "windows"))]
    {
        resource("ffmpeg/ffmpeg")
    }
}

/// 工作流模板目录
pub fn workflows_dir() -> NexusResult<PathBuf> {
    resource("workflows")
}

/// 用户数据目录（配置、历史记录、生成视频输出）
///   Windows: C:\Users\<u>\AppData\Roaming\com.nexusvideo.client
///   macOS:   ~/Library/Application Support/com.nexusvideo.client
pub fn user_data_dir() -> NexusResult<PathBuf> {
    dirs::data_dir()
        .map(|d| d.join("com.nexusvideo.client"))
        .ok_or_else(|| NexusError::PathResolve("无法定位用户数据目录".into()))
}

/// 生成视频输出目录（用户可见的历史记录）
pub fn output_dir() -> NexusResult<PathBuf> {
    Ok(user_data_dir()?.join("output"))
}

/// 配置文件路径
pub fn config_file() -> NexusResult<PathBuf> {
    Ok(user_data_dir()?.join("config.json"))
}

/// 启动日志目录（崩溃上报用）
pub fn log_dir() -> NexusResult<PathBuf> {
    Ok(user_data_dir()?.join("logs"))
}

/// 调试用：打印所有关键路径
pub fn dump_paths() -> String {
    let f = |r: &Path| r.display().to_string();
    let py = python_executable().map(|p| f(&p)).unwrap_or_else(|e| e.to_string());
    let cui = comfyui_dir().map(|p| f(&p)).unwrap_or_else(|e| e.to_string());
    let fapi = fastapi_entry().map(|p| f(&p)).unwrap_or_else(|e| e.to_string());
    let out = output_dir().map(|p| f(&p)).unwrap_or_else(|e| e.to_string());
    let vid = videos_dir().map(|p| f(&p)).unwrap_or_else(|e| e.to_string());
    let thb = thumbnails_dir().map(|p| f(&p)).unwrap_or_else(|e| e.to_string());
    let upl = uploads_dir().map(|p| f(&p)).unwrap_or_else(|e| e.to_string());
    format!(
        "[paths] python={py}\n[paths] comfyui_dir={cui}\n[paths] fastapi={fapi}\n[paths] output={out}\n[paths] videos={vid}\n[paths] thumbnails={thb}\n[paths] uploads={upl}"
    )
}

// ============================================================================
// 视频与缩略图路径（Task #9）
// ============================================================================

/// 视频存储目录：~/NexusVideo/output/videos/
pub fn videos_dir() -> NexusResult<PathBuf> {
    Ok(output_dir()?.join("videos"))
}

/// 缩略图缓存目录：~/NexusVideo/output/thumbnails/
pub fn thumbnails_dir() -> NexusResult<PathBuf> {
    Ok(output_dir()?.join("thumbnails"))
}

// ============================================================================
// 上传目录（文件上传用，Task 联调）
// ============================================================================

/// 上传文件根目录：~/NexusVideo/output/uploads/
/// 按 task_id 分组：./uploads/{task_id}/{filename}
pub fn uploads_dir() -> NexusResult<PathBuf> {
    Ok(output_dir()?.join("uploads"))
}

/// 按 task_id 分组的上传目录：./uploads/{task_id}/
/// 自动创建目录（如果不存在）
pub fn upload_task_dir(task_id: &str) -> NexusResult<PathBuf> {
    let base = uploads_dir()?;
    let task_dir = base.join(task_id);
    std::fs::create_dir_all(&task_dir).map_err(|e| {
        NexusError::PathResolve(format!("创建上传目录失败 {}: {e}", task_dir.display()))
    })?;
    Ok(task_dir)
}
