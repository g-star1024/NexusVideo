//! startup.rs — 启动期致命错误可视化 (P0 修复)
//! ============================================================================
//! 背景：release 构建带 `windows_subsystem = "windows"`，没有控制台窗口，
//! 任何 `.expect()` / `panic!` 失败都是「静默死亡」——进程被立刻终止，
//! 用户只看到"双击无反应"。Windows 事件查看器会记录
//!   EventID 1000 / ExceptionCode c0000409
//! = STATUS_STACK_BUFFER_OVERRUN = std::process::abort() 触发的快速失败。
//!
//! 这正是线上 P0（双击 NexusVideo 无窗口、无报错）的根因路径：
//!   WebView2 缺失（embedBootstrapper+silent 在离线/企业 GPO 下静默失败）
//!   → Tauri 无法创建 WebView 窗口 → .run() 返回 Err
//!   → 旧代码 .expect("Tauri 应用启动失败") panic → abort() → c0000409。
//!
//! 本模块提供三条「可见化」通道：
//!   1) show_fatal_messagebox()  —— 用 Win32 MessageBoxW 弹窗。release 下
//!      这是唯一可见的报错出口（stdout/stderr 都不可见）。
//!   2) ensure_webview2_available() —— 启动前检测 WebView2 运行时，缺失则
//!      给出带官方下载链接的友好提示，干净退出（绝不静默 abort）。
//!   3) classify_startup_error() —— 把 tauri::Error 转成用户可读的中文说明，
//!      重点识别 WebView2 缺失 / 配置错误 / 前端资源缺失。
//! ============================================================================

// ---- Windows：真正的 MessageBoxW 实现 + WebView2 检测 ----
#[cfg(windows)]
mod windows_impl {
    use std::os::windows::ffi::OsStrExt;
    use std::ptr;

    use winapi::um::winuser::{MessageBoxW, MB_ICONERROR, MB_OK};

    /// 把 &str 转成以 null 结尾的 UTF-16 宽字符串（MessageBoxW 需要 *const u16）
    fn to_wide(s: &str) -> Vec<u16> {
        std::ffi::OsStr::new(s)
            .encode_wide()
            .chain(std::iter::once(0u16))
            .collect()
    }

    /// 弹出一个阻塞式错误对话框。调用返回即代表用户已点「确定」，进程随后退出。
    pub fn show_fatal_messagebox(title: &str, message: &str) {
        let title_w = to_wide(title);
        let msg_w = to_wide(message);
        unsafe {
            // hWnd = NULL（无父窗口）；MB_OK | MB_ICONERROR = 仅「确定」+ 错误图标。
            MessageBoxW(
                ptr::null_mut(),
                msg_w.as_ptr(),
                title_w.as_ptr(),
                MB_OK | MB_ICONERROR,
            );
        }
    }

    /// 启动前检测 WebView2 运行时是否可用。
    /// 返回 Err(友好原因) 表示缺失或不可用（文件名/下载指引见 reason）。
    pub fn ensure_webview2_available() -> Result<(), String> {
        match tauri::webview_version() {
            Ok(ver) => {
                log::info!("[startup] 检测到 WebView2 运行时版本: {ver}");
                Ok(())
            }
            Err(e) => Err(format!(
                "未检测到 Microsoft WebView2 运行时。\n\nNexusVideo 依赖 WebView2 来显示界面，缺少它无法启动。\n（错误详情：{e}）\n\n请安装后重试：\nhttps://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n若处于企业内网/离线环境，请让 IT 协助安装，或下载离线安装包后重装 NexusVideo。"
            )),
        }
    }
}

// ---- 非 Windows（macOS / Linux）：有系统日志/终端通道，直接 stderr 输出 ----
#[cfg(not(windows))]
mod windows_impl {
    /// 非 Windows 平台不会走到 WebView2 检测分支；保留空实现以满足调用点。
    pub fn ensure_webview2_available() -> Result<(), String> {
        Ok(())
    }

    /// macOS / Linux 启动失败由系统崩溃报告或终端可见，这里落到 stderr。
    pub fn show_fatal_messagebox(title: &str, message: &str) {
        eprintln!("[FATAL] {title}\n{message}");
    }
}

pub use windows_impl::show_fatal_messagebox;

/// 启动前检测 WebView2 运行时（仅 Windows 有意义；非 Windows 恒返回 Ok）。
pub fn ensure_webview2_available() -> Result<(), String> {
    windows_impl::ensure_webview2_available()
}

/// 把 tauri::Error 分类成用户可读的中文说明。
/// 重点识别：WebView2 缺失 / 配置错误 / 前端资源缺失，并引导到日志或下载。
pub fn classify_startup_error(e: &tauri::Error) -> String {
    let raw = format!("{e:?}");
    let lower = raw.to_lowercase();

    if lower.contains("webview2") || lower.contains("webview") || lower.contains("0x8004") {
        format!(
            "启动失败：WebView2 运行时不可用。\n\n技术信息：\n{raw}\n\n解决方法：\n\
1. 联网后访问 https://go.microsoft.com/fwlink/p/?LinkId=2124703 安装 WebView2 运行时；\n\
2. 或联系 IT 解除对企业软件安装的限制（GPO）后，重新安装 NexusVideo。\n\n安装完成后重新打开 NexusVideo 即可。"
        )
    } else if lower.contains("config")
        || lower.contains("context")
        || lower.contains("frontend")
        || lower.contains("asset")
        || lower.contains("dist")
    {
        format!(
            "启动失败：应用配置或前端资源异常。\n\n技术信息：\n{raw}\n\n请联系技术支持，并提供此信息（日志位于 %APPDATA%\\com.nexusvideo.client\\logs\\）。"
        )
    } else {
        format!(
            "NexusVideo 启动失败，错误信息如下：\n\n{raw}\n\n请尝试重新安装；若仍失败，请联系技术支持（日志位于 %APPDATA%\\com.nexusvideo.client\\logs\\）。"
        )
    }
}
