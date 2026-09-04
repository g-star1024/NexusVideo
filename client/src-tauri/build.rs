// Tauri 构建脚本：生成 capability / context，并声明应用命令的 ACL 权限
//
// 为什么必须在这里声明：Tauri v2 是「默认拒绝」。应用自己的命令不会自动生成权限，
// 必须在 build.rs 用 AppManifest::commands() 显式列出；否则 __app-acl__ manifest 为空，
// capabilities 里无法引用任何应用命令，运行时所有 invoke() 都会被 webview 侧的 ACL
// 检查拒绝（一键拉取整条链路都不通）。
//
// 参数约定（已核对 tauri-build 2.6.3 / tauri-utils 2.9.3 源码）：
//   - commands(&[...]) 传 **snake_case 命令名**，与 generate_handler! 的注册名一致，
//     这也是运行时 invoke 的 key（tauri-macros command/handler.rs 对应用命令不加前缀）。
//   - 生成器自动把下划线转连字符：identifier = "allow-<kebab-case>"
//     （tauri-utils acl/build.rs: `command.replace('_', "-")`）。
//   - capabilities 里引用时**不加 `__app__:` 前缀**：应用 ACL 的 manifest key 是
//     APP_ACL_KEY = "__app-acl__"，而 validate_capabilities 用
//     `get_prefix().unwrap_or(APP_ACL_KEY)` 回填缺省前缀。写成 "__app__:allow-xxx"
//     会以 "__app__" 为 key 查 manifest → 查不到 → 构建报错。
//   - 权限名必须与生成器输出一致（kebab-case），写 snake_case 同样会构建报错。
fn main() {
    if let Err(e) = tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                // ---- 后端进程控制 ----
                "start_backend",
                "stop_backend",
                "get_backend_status",
                // ---- 生成请求代理 ----
                "generate_video",
                "query_task",
                "cancel_task",
                "get_models",
                // ---- 应用信息 / 文件系统 ----
                "get_app_info",
                "open_output_dir",
                // ---- 首次启动 ----
                "init_app",
                "get_init_status",
                // ---- 进程状态与磁盘 ----
                "get_process_status",
                "start_backend_full",
                // ---- 文件系统管理 ----
                "get_disk_space",
                "get_video_list",
                "cleanup_old_files",
                "evict_thumbnails",
                "read_settings",
                "write_settings",
                // ---- 文件上传 ----
                "upload_file",
                "move_uploaded_file",
                // ---- 进度推送桥接 ----
                "listen_progress",
                "stop_progress",
                // ---- 自动更新 IPC ----
                "auto_update_ipc_check",
                "auto_update_ipc_download",
                "auto_update_ipc_restart",
                // ---- 崩溃日志 IPC ----
                "reload_frontend",
                "get_crash_reports",
                "clear_crash_reports",
                // ---- M2: 设置中心 · 一键拉取 ----
                "get_default_install_dir",
                "audit_install_dir",
                "pick_install_dir",
                "start_install",
                "verify_install",
                "listen_install_progress",
                "stop_install_progress",
            ]),
        ),
    ) {
        eprintln!("{e:#}");
        std::process::exit(1);
    }
}
