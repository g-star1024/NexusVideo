//! auto_update.rs — 自动更新模块 (Task #13)
//! ============================================================================
//! 基于 tauri-plugin-updater 实现：
//!   - 启动时后台检查更新（不阻塞主进程）
//!   - 发现更新 → 弹窗提示下载
//!   - 下载完成 → 弹窗提示安装重启
//!
//! 更新服务器端点：https://releases.nexusvideo.com/update.json
//! 端点返回格式（Tauri Updater JSON）：
//! {
//!   "version": "0.2.0",
//!   "date": "2026-08-20T00:00:00Z",
//!   "url": "https://releases.nexusvideo.com/NexusVideo_0.2.0_x64-setup.exe",
//!   "sha256": "abc123...",
//!   "pub_date": "2026-08-20T00:00:00Z",
//!   "body": "Release notes..."
//! }
//!
//! 增量更新说明：
//!   当前 Tauri Updater 默认全量下载。增量更新需要：
//!   1) 服务端将更新文件分割为 patch 块
//!   2) 客户端通过自定义 Updater 策略只下载差异块
//!   3) 本地合并 patch 块到已有二进制
//!   4) 本阶段使用全量更新（简单可靠）；增量方案留作 P2 优化
//! ============================================================================

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::UpdaterExt;

// ---- 事件常量 ----
pub const EVENT_UPDATE_AVAILABLE: &str = "update://available";
pub const EVENT_UPDATE_DOWNLOADING: &str = "update://downloading";
pub const EVENT_UPDATE_DOWNLOADED: &str = "update://downloaded";
pub const EVENT_UPDATE_ERROR: &str = "update://error";

/// 启动时异步检查是否有新版本。
/// - 不阻塞主进程，静默后台运行
/// - 有新版本时通过 EVENT_UPDATE_AVAILABLE 通知前端
/// - 用户确认下载后触发 EVENT_UPDATE_DOWNLOADING / EVENT_UPDATE_DOWNLOADED
pub async fn check_for_updates(app: AppHandle) {
    let updater = match app.updater().await {
        Ok(updater) => match updater.check().await {
            Ok(u) => u,
            Err(e) => {
                log::warn!("[auto_update] 更新检查失败: {e}");
                let _ = tauri::Emitter::emit(&app, EVENT_UPDATE_ERROR, serde_json::json!({
                    "error": e.to_string(),
                    "message": "更新检查失败，请稍后重试"
                }));
                return;
            }
        },
        Err(e) => {
            log::warn!("[auto_update] 更新检查失败: {e}");
            let _ = tauri::Emitter::emit(&app, EVENT_UPDATE_ERROR, serde_json::json!({
                "error": e.to_string(),
                "message": "更新检查失败，请稍后重试"
            }));
            return;
        }
    };

    if updater.is_latest() {
        log::info!("[auto_update] 当前已是最新版本");
        return;
    }

    let new_version = updater.version().to_string();
    let release_notes = updater.body().map(|s| s.to_string()).unwrap_or_default();
    let current_version = app.package_info().version.to_string();

    log::info!(
        "[auto_update] 发现新版本: 当前 {current_version} → 新 {new_version}",
    );

    // 通知前端展示更新提示弹窗
    let _ = tauri::Emitter::emit(&app, EVENT_UPDATE_AVAILABLE, serde_json::json!({
        "current_version": current_version,
        "new_version": new_version,
        "release_notes": release_notes,
        "changelog_url": format!("https://github.com/NexusVideo/NexusVideo/releases/tag/v{}", new_version),
    }));
}

/// 开始下载更新包
pub async fn start_download(app: AppHandle) -> Result<(), String> {
    let mut updater = match app.updater().await {
        Ok(up) => match up.check().await {
            Ok(u) => u,
            Err(e) => return Err(e.to_string()),
        },
        Err(e) => return Err(e.to_string()),
    };

    if updater.is_latest() {
        return Ok(());
    }

    let new_version = updater.version().to_string();

    // 通知前端下载开始
    let _ = tauri::Emitter::emit(&app, EVENT_UPDATE_DOWNLOADING, serde_json::json!({
        "version": new_version,
        "progress": 0,
        "message": "正在下载更新包，请稍候..."
    }));

    // 执行下载并安装
    updater.download_and_install(|_, _loaded, _total| {
        // tauri-plugin-updater 的回调签名为 (chunk, loaded_bytes, total_bytes)
        // 版本兼容：这里只记录，具体进度通过单独方式推送
        log::debug!("[auto_update] 下载中...");
    }, || {
        log::info!("[auto_update] 下载完成，等待重启安装");
    }).await.map_err(|e| e.to_string())?;

    // 通知前端更新已下载，等待重启安装
    let _ = tauri::Emitter::emit(&app, EVENT_UPDATE_DOWNLOADED, serde_json::json!({
        "version": new_version,
        "message": "更新下载完成，重启后自动安装"
    }));

    Ok(())
}

/// 重启应用以安装已下载的更新
pub async fn restart_and_install(app: AppHandle) {
    log::info!("[auto_update] 用户确认重启安装");
    app.restart();
}