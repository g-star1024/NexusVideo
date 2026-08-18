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

/// 嵌入式 Python 解释器路径
///   Windows: resources/python_env/python.exe
///   macOS:   resources/python_env/bin/python3
pub fn python_executable() -> NexusResult<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        resource("python_env/python.exe")
    }
    #[cfg(target_os = "macos")]
    {
        resource("python_env/bin/python3")
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    {
        resource("python_env/bin/python3")
    }
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
pub fn fastapi_entry() -> NexusResult<PathBuf> {
    resource("python_env/local_server.py").or_else(|_| resource("backend/local_server.py"))
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
