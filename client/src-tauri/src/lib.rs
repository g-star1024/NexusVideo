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
pub mod startup;
pub mod state;
pub mod static_server;

// ---- 自引用声明（edition 2021 的 extern prelude 不含自身 crate 名，
//      必须显式 `extern crate self` 才能在本 crate 内用 `nexusvideo_client_lib::`
//      路径自引用；否则顶部 20 处 `use nexusvideo_client_lib::*` 全部报
//      "cannot find crate nexusvideo_client_lib"）----
extern crate self as nexusvideo_client_lib;

use nexusvideo_client_lib::init_flow::InitState;
use nexusvideo_client_lib::state::AppState;
use tauri::Listener;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{TrayIconBuilder, TrayIconEvent, MouseButton};
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // ==================================================================
    // P0 修复 (线上崩溃 c0000409 / 双击无反应)：
    // 启动前先检测 WebView2 运行时。release 构建无控制台，一旦缺失，
    // Tauri 无法建窗 → .run() 返回 Err → 旧代码 .expect() panic →
    // abort() 静默死亡。这里提前检测，缺失则弹 MessageBox 引导安装，
    // 干净退出（std::process::exit），绝不静默 abort。
    // ==================================================================
    #[cfg(windows)]
    {
        if let Err(reason) = nexusvideo_client_lib::startup::ensure_webview2_available() {
            nexusvideo_client_lib::startup::show_fatal_messagebox("NexusVideo 无法启动", &reason);
            std::process::exit(1);
        }
    }

    let run_result = tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::new()
                .rotation_strategy(tauri_plugin_log::RotationStrategy::KeepOne)
                .max_file_size(1 << 20) // 1MB，与原 JSON 配置 max_size:1048576 一致
                .build(),
        )
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

            // ==================================================================
            // V2 系统托盘菜单：注册菜单项 + 托盘事件处理。
            // 点击托盘图标 → 恢复主窗口；菜单「退出 NexusVideo」→ 彻底退出。
            // ==================================================================
            {
                let handle = app.handle().clone();

                let open_item =
                    MenuItem::with_id(&handle, "show_window", "打开主窗口", true, None::<&str>)
                        .map_err(|e| e.to_string())?;
                let quit_item =
                    MenuItem::with_id(&handle, "quit", "退出 NexusVideo", true, None::<&str>)
                        .map_err(|e| e.to_string())?;

                let tray_menu = Menu::with_items(
                    &handle,
                    &[&open_item, &quit_item],
                )
                .map_err(|e| e.to_string())?;

                // Tauri 2.x TrayIconBuilder：不传 icon 也能在 Windows/macOS 正常显示菜单；
                // Linux 需要 icon+menu 才能显示图标，但 MVP 阶段目标平台是 Win/mac，先无 icon 上线。
                TrayIconBuilder::<tauri::Wry>::with_id("tray")
                    .tooltip("NexusVideo")
                    .menu(&tray_menu)
                    .on_tray_icon_event(|tray, event| {
                        if let TrayIconEvent::Click { button, .. } = event {
                            if button == MouseButton::Left {
                                let _ = tray.app_handle().emit("ShowWindow", ());
                            }
                        }
                    })
                    .on_menu_event(|app, event| {
                        if let Some(menu_item_id) = event.menu_item_id() {
                            match menu_item_id.as_str() {
                                "show_window" => {
                                    let _ = app.emit("ShowWindow", ());
                                }
                                "quit" => {
                                    let _ = app.emit("Quit", ());
                                }
                                _ => {}
                            }
                        }
                    })
                    .build(&handle)
                    .map_err(|e| e.to_string())?;

                log::info!("[tray] 系统托盘菜单已注册");
            }

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
                if let Err(e) =
                    nexusvideo_client_lib::static_server::start_static_server(app_handle.clone())
                        .await
                {
                    log::error!("[static_server] 启动失败: {e}");
                }
            });

            // ==================================================================
            // 系统托盘 (System Tray) + 关闭按钮行为：
            // 拦截窗口关闭 → 隐藏窗口（最小化到托盘），通过托盘菜单重新打开或退出。
            // ==================================================================
            let app_handle_setup = app.handle().clone();
            app.listen("ShowWindow", move |_app, _event| {
                let h = app_handle_setup.clone();
                // 在后台异步恢复窗口（避免在事件回调里阻塞）
                tauri::async_runtime::spawn(async move {
                    if let Some(window) = h.get_webview_window("main") {
                        log::info!("[tray] 打开主窗口");
                        let _ = window.set_focus();
                        if let Err(e) = window.show() {
                            log::error!("[tray] show window 失败: {e}");
                        }
                    } else {
                        log::error!("[tray] 找不到 main 窗口");
                    }
                });
            });

            app.listen("Quit", move |app, _event| {
                log::info!("[tray] 用户选择退出");
                // 关闭所有窗口 → 触发 Destroyed 事件 → 触发 on_window_event 清理子进程
                app.exit(0);
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            // ==================================================================
            // 系统托盘：拦截窗口关闭 → 隐藏窗口（最小化到托盘），不退出。
            // 真正退出由托盘菜单「退出 NexusVideo」触发。
            // ==================================================================
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                log::info!("[window] 用户点击关闭窗口 → 最小化到托盘");
                // 拦截关闭，不执行默认关闭逻辑
                api.prevent_close();
                if let Err(e) = window.hide() {
                    log::error!("[window] 隐藏窗口失败: {e}");
                }
                return;
            }

            // 窗口关闭时优雅停止全部子进程，杜绝孤儿/僵尸进程。
            if let tauri::WindowEvent::Destroyed = event {
                let handle = window.app_handle().clone();
                if let Some(state) = handle.try_state::<AppState>() {
                    // 克隆一份 AppHandle 用于发射事件与停止子进程：
                    // 原 `handle` 已被 `state`（State<'_, AppState>）借用，
                    // 不能再次 move 进 async 块（否则 E0505）。emit_handle 是
                    // 独立克隆，move 进 async 块不影响 `state` 对 `handle` 的借用。
                    let emit_handle = handle.clone();
                    tauri::async_runtime::block_on(async move {
                        // 1) 通知前端正在退出
                        let _ = tauri::Emitter::emit(
                            &emit_handle,
                            nexusvideo_client_lib::events::event_name::BACKEND_STATUS,
                            serde_json::json!({"message": "应用退出，正在停止后端..."}),
                        );

                        // 2) 停止静态文件服务
                        nexusvideo_client_lib::static_server::stop_static_server().await;

                        // 3) 清理进度监听句柄
                        state.clear_progress_handles().await;

                        // 4) 停止全部子进程
                        let mgr = state.proc_mgr.read().await;
                        mgr.stop_all(&emit_handle).await;
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
        .run(tauri::generate_context!());

    // ==================================================================
    // P0 修复：用 match 替代 .expect()（panic=abort 会静默死亡，
    // 事件查看器 EventID 1000 / ExceptionCode c0000409 = abort() 快速失败）。
    // 任何 build/run 失败都弹 MessageBox 让用户"看见"原因，而不是静默死亡。
    // （panic 模式已改回 unwind，见 Cargo.toml，确保 crash_handler 的
    //  panic hook 能完整写入 %APPDATA%\com.nexusvideo.client\logs\crash_reports\）
    // ==================================================================
    match run_result {
        Ok(()) => {
            // 正常进入事件循环；窗口关闭时由 on_window_event 优雅停止子进程并退出。
        }
        Err(e) => {
            let msg = nexusvideo_client_lib::startup::classify_startup_error(&e);
            log::error!("[startup] Tauri 应用启动失败:\n{msg}");
            nexusvideo_client_lib::startup::show_fatal_messagebox("NexusVideo 启动失败", &msg);
            std::process::exit(1);
        }
    }
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
