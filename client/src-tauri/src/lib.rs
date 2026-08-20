//! lib.rs — NexusVideo Tauri 应用入口（库目标）
//! ============================================================================
//! 注册全部 IPC command、挂载插件、注入共享状态、设置退出钩子。
//! Task #13: 集成自动更新 + 崩溃日志系统
//! ============================================================================

// ---- 模块声明（所有 src/*.rs 文件通过 mod 注册为 crate 子模块）----
pub mod auto_update;
pub mod commands;
pub mod crash_handler;
pub mod error;
pub mod events;
pub mod file_manager;
pub mod init_flow;
pub mod paths;
pub mod process_manager;
pub mod state;
pub mod static_server;

// ---- 自引用声明（edition 2021 的 extern prelude 不含自身 crate 名，
//      必须显式 `extern crate self` 才能在本 crate 内用 `nexusvideo_client_lib::`
//      路径自引用；否则顶部 20 处 `use nexusvideo_client_lib::*` 全部报
//      "cannot find crate nexusvideo_client_lib"）----
extern crate self as nexusvideo_client_lib;

use nexusvideo_client_lib::auto_update;
use nexusvideo_client_lib::commands;
use nexusvideo_client_lib::crash_handler;
use nexusvideo_client_lib::init_flow::InitState;
use nexusvideo_client_lib::state::AppState;
use tauri::{Emitter, Manager, WindowEvent};

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(AppState::new())
        .manage(InitState::new())
        .setup(|app| {
            // ==================================================================
            // Task #13: 安装全局 panic hook（崩溃日志系统）
            // ==================================================================
            crash_handler::install_panic_hook(app.handle().clone());

            // 启动时打印关键路径（诊断用）
            log::info!("{}", nexusvideo_client_lib::paths::dump_paths());

            // 确保 output / logs / config / videos / thumbnails / uploads 目录存在
            let _ = nexusvideo_client_lib::paths::output_dir()
                .and_then(|d| std::fs::create_dir_all(&d).map_err(Into::into));
            let _ = nexusvideo_client_lib::paths::log_dir()
                .and_then(|d| std::fs::create_dir_all(&d).map_err(Into::into));
            let _ = nexusvideo_client_lib::paths::videos_dir()
                .and_then(|d| std::fs::create_dir_all(&d).map_err(Into::into));
            let _ = nexusvideo_client_lib::paths::thumbnails_dir()
                .and_then(|d| std::fs::create_dir_all(&d).map_err(Into::into));
            let _ = nexusvideo_client_lib::paths::uploads_dir()
                .and_then(|d| std::fs::create_dir_all(&d).map_err(Into::into));

            // ==================================================================
            // Task #13: 启动时后台检查更新（不阻塞主进程启动）
            // ==================================================================
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                // 延迟 3 秒再检查，避免与应用启动抢占网络资源
                tokio::time::sleep(std::time::Duration::from_secs(3)).await;
                auto_update::check_for_updates(app_handle.clone()).await;
            });

            // 启动静态文件服务（127.0.0.1:9882），供前端 <video> 播放本地文件
            let app_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(e) = nexusvideo_client_lib::static_server::start_static_server(app_handle.clone()).await {
                    log::error!("[static_server] 启动失败: {e}");
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // 窗口关闭时优雅停止全部子进程，杜绝孤儿/僵尸进程。
            if let tauri::WindowEvent::Destroyed = event {
                let handle = window.app_handle().clone();
                if let Some(state) = handle.try_state::<AppState>() {
                    tauri::async_runtime::block_on(async move {
                        // 1) 通知前端正在退出
                        let _ = tauri::Emitter::emit(
                            &handle,
                            nexusvideo_client_lib::events::event_name::BACKEND_STATUS,
                            serde_json::json!({"message": "应用退出，正在停止后端..."}),
                        );

                        // 2) 停止静态文件服务
                        nexusvideo_client_lib::static_server::stop_static_server().await;

                        // 3) 清理进度监听句柄
                        state.clear_progress_handles().await;

                        // 4) 停止全部子进程
                        let mgr = state.proc_mgr.read().await;
                        mgr.stop_all(&handle).await;
                    });
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            // ---- 后端进程控制 ----
            commands::start_backend,
            commands::stop_backend,
            commands::get_backend_status,
            // ---- 生成请求代理 ----
            commands::generate_video,
            commands::query_task,
            commands::cancel_task,
            commands::get_models,
            // ---- 应用信息 / 文件系统 ----
            commands::get_app_info,
            commands::open_output_dir,
            // ---- Task #9: 首次启动 ----
            init_flow::init_app,
            init_flow::get_init_status,
            // ---- Task #9: 进程状态与磁盘 ----
            commands::get_process_status,
            commands::start_backend_full,
            // ---- Task #9: 文件系统管理 ----
            nexusvideo_client_lib::file_manager::get_disk_space,
            nexusvideo_client_lib::file_manager::get_video_list,
            nexusvideo_client_lib::file_manager::cleanup_old_files,
            nexusvideo_client_lib::file_manager::evict_thumbnails,
            nexusvideo_client_lib::file_manager::read_settings,
            nexusvideo_client_lib::file_manager::write_settings,
            // ---- 联调对齐: 文件上传 ----
            commands::upload_file,
            commands::move_uploaded_file,
            // ---- 联调对齐: 进度推送桥接 ----
            commands::listen_progress,
            commands::stop_progress,
            // ---- Task #13: 自动更新 IPC ----
            auto_update_ipc_check,
            auto_update_ipc_download,
            auto_update_ipc_restart,
            // ---- Task #13: 崩溃日志 IPC ----
            crash_handler::reload_frontend,
            crash_handler::get_crash_reports,
            crash_handler::clear_crash_reports,
        ])
        .run(tauri::generate_context!())
        .expect("Tauri 应用启动失败");
}

/// IPC 命令：检查是否有新版本更新
#[tauri::command]
async fn auto_update_ipc_check(app: tauri::AppHandle) -> Result<(), String> {
    auto_update::check_for_updates(app).await;
    Ok(())
}

/// IPC 命令：下载已发现的更新
#[tauri::command]
async fn auto_update_ipc_download(app: tauri::AppHandle) -> Result<(), String> {
    auto_update::start_download(app).await
}

/// IPC 命令：重启应用以安装已下载的更新
#[tauri::command]
fn auto_update_ipc_restart(app: tauri::AppHandle) {
    auto_update::restart_and_install(app);
}