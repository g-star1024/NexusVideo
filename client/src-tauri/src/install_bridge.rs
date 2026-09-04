//! install_bridge.rs — M2「设置中心 · 一键拉取」Tauri 桥接层
//! ============================================================================
//! 职责边界（只做"底座"，不碰业务算法）：
//!   1. 原生目录选择 dialog（Windows 资源管理器 / macOS Finder）
//!   2. 目标目录体检：路径规范化 + 系统盘警示 + 剩余空间 + 可写性
//!   3. 触发后端一键拉取（Rust → FastAPI，沿用 commands.rs 的代理约定）
//!   4. SSE 进度桥接（降级通道，默认前端直连；见下方「SSE 决策」）
//!
//! ----------------------------------------------------------------------------
//! 后端接口契约（M1 已锁定，本文件只消费不修改）：
//!   POST /install/pull      body {target_dir?, mirror?} → {task_id, accepted}
//!   GET  /install/progress  SSE  text/event-stream，每帧 `data: <json>\n\n`
//!   GET  /install/verify?target_dir=  → {missing: string[], ok: bool}
//!
//! ----------------------------------------------------------------------------
//! 【SSE 决策】默认「前端直连 EventSource」，Tauri 侧只提供降级通道：
//!   - Windows：前端直连**可用**，无需 Tauri 介入。依据两条已核对的事实：
//!       a) tauri.conf.json 的 CSP `connect-src` 已显式放行
//!          http://127.0.0.1:9881（EventSource 受 connect-src 管辖）；
//!       b) backend/local_server.py:235 CORSMiddleware allow_origins=["*"]，
//!          跨源 EventSource 能拿到 Access-Control-Allow-Origin。
//!     且 src/api/settings.ts、auth.ts、skills.ts 已在用 fetch 直连 9881，
//!     说明"设置中心域直连后端"是本仓既有约定，SSE 保持一致最省事。
//!   - macOS：WKWebView 下 Tauri 用自定义 scheme（tauri://localhost）承载页面，
//!     其安全上下文判定与 Windows(http://tauri.localhost) 不同，明文 http://
//!     子请求存在被判为混合内容而拦截的风险。这是**难以在 Windows 复现**的
//!     边缘 Case，故提供 `listen_install_progress` 作为兜底：Rust 侧 reqwest
//!     拉流 → Tauri event 推给前端，前端换个监听源即可，UI 逻辑零改动。
//!   → 前端策略建议：先尝试 EventSource，onerror 且从未收到任何帧时，自动
//!     切到 `listen_install_progress()`（见 src/api/install.ts 的实现）。
use crate::events::event_name;
use crate::file_manager::{disk_space_of, human_size};
use crate::state::AppState;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_dialog::DialogExt;

// ===========================================================================
// 常量
// ===========================================================================

const GIB: u64 = 1024 * 1024 * 1024;

/// Windows 默认 staging 根，与 backend/config.py:117 的 `staging_dir` 默认值严格一致。
/// 一键拉取的产物**默认落 D 盘**，绝不默认写系统盘。
pub const DEFAULT_STAGING_WINDOWS: &str = "D:/nexusvideo_staging";

/// 推荐可用空间：install_manifest.json 中模型合计约 9.6GB
/// （6.7 + 0.24 + 2.64），叠加下载临时文件与解压峰值，留到 20GiB 才算宽裕。
const REQUIRED_RECOMMENDED_BYTES: u64 = 20 * GIB;

/// 硬下限：低于此值几乎必然拉取中途失败，直接判 block 不放行。
const REQUIRED_MIN_BYTES: u64 = 12 * GIB;

/// AppState.progress_handles 中 SSE 桥接任务的固定键。
/// 复用既有 map 的好处：lib.rs 退出钩子的 `clear_progress_handles()` 会自动
/// 回收这个任务，不会留下悬挂的长连接（子进程/任务泄漏是本项目的红线）。
const INSTALL_SSE_KEY: &str = "__install_sse__";

/// SSE 空闲超时：单帧之间超过该时长没有任何字节，判定连接已死并上报。
/// 注意：**不能**复用 AppState.http（state.rs:38 设了 30s 整体 timeout），
/// 那会把长达数十分钟的拉取流在 30 秒时掐断。
const SSE_IDLE_TIMEOUT: Duration = Duration::from_secs(180);

pub const LEVEL_OK: &str = "ok";
pub const LEVEL_WARN: &str = "warn";
pub const LEVEL_BLOCK: &str = "block";

// ===========================================================================
// 一、路径规范化
// ===========================================================================

/// 把系统 dialog / 用户输入的目录路径统一成「后端期望形态」：正斜杠、无尾斜杠、
/// 盘符大写，例如 `D:\nexusvideo_staging\` → `D:/nexusvideo_staging`。
///
/// 为什么在 Tauri 侧做：后端 config 层虽已有 MSYS→Windows 归一化，但那是兜底；
/// 前端/Tauri 递给后端的值保持 `D:/nexusvideo_staging` 风格最稳，也让设置中心
/// 回显的路径与后端日志里的路径完全一致，排障时不用心算两种写法。
pub fn normalize_dir(raw: &str) -> String {
    let s = raw.trim();
    if s.is_empty() {
        return String::new();
    }

    // 1) 反斜杠 → 正斜杠
    let mut s = s.replace('\\', "/");

    // 2) 折叠重复斜杠（UNC 前导 `//` 需保留，先记后补）
    let is_unc = s.starts_with("//");
    while s.contains("//") {
        s = s.replace("//", "/");
    }
    if is_unc {
        s.insert(0, '/');
    }

    // 3) MSYS/Git-Bash 风格 `/d/foo` → `D:/foo`（仅 Windows；
    //    macOS 下 `/d/foo` 是合法绝对路径，绝不能改）
    //
    //    只匹配严格的 MSYS 形态，避免把 `/ab` 这类普通路径误判成盘符：
    //      - `/d`      （长度 2）
    //      - `/d/...`  （第 3 字节必须是 `/`）
    if cfg!(windows) && !is_unc {
        let b = s.as_bytes();
        let msys = (b.len() == 2 && b[0] == b'/' && b[1].is_ascii_alphabetic())
            || (b.len() >= 3
                && b[0] == b'/'
                && b[1].is_ascii_alphabetic()
                && b[2] == b'/');
        if msys {
            let drive = (b[1] as char).to_ascii_uppercase();
            let rest = &s[2..];
            s = if rest.is_empty() {
                format!("{drive}:/")
            } else {
                format!("{drive}:{rest}")
            };
        }
    }

    // 4) 盘符大写：`d:/foo` → `D:/foo`
    {
        let b = s.as_bytes();
        if b.len() >= 2 && b[1] == b':' && b[0].is_ascii_alphabetic() {
            let mut c = s.chars();
            let first = c.next().unwrap().to_ascii_uppercase();
            s = format!("{first}{}", c.as_str());
        }
    }

    // 5) 去尾斜杠，但保留卷根（`D:/`、`/`）——去掉会变成 `D:` 这种歧义相对路径
    let is_volume_root = (s.len() == 3 && s.as_bytes()[1] == b':' && s.ends_with('/')) || s == "/";
    if !is_volume_root {
        while s.len() > 1 && s.ends_with('/') {
            s.pop();
        }
    }

    s
}

/// 平台原生显示形态：Windows 用反斜杠（贴合资源管理器），其余原样。
/// 只用于 UI 展示，**递给后端的一律是 normalize_dir 的正斜杠形态**。
fn display_form(normalized: &str) -> String {
    if cfg!(windows) {
        normalized.replace('/', "\\")
    } else {
        normalized.to_string()
    }
}

/// 向上回溯到最近一个已存在的祖先目录（用于对"还没创建的目标目录"做体检）
fn nearest_existing(dir: &Path) -> Option<PathBuf> {
    let mut probe = dir;
    loop {
        if probe.exists() {
            return Some(probe.to_path_buf());
        }
        match probe.parent() {
            Some(p) if p != probe => probe = p,
            _ => return None,
        }
    }
}

/// 双平台默认 staging 目录。
///   Windows：`D:/nexusvideo_staging`（与后端默认值一致）；
///            D 盘不存在时**不硬写 D:**，回落到用户数据目录下的 staging，
///            避免把一个不存在的盘塞给后端导致拉取秒失败。
///   macOS  ：`~/Library/Application Support/com.nexusvideo.client/staging`
///            （复用 paths::user_data_dir() = dirs::data_dir()，与既有
///             output/logs/videos 的 macOS 路径约定同源，不另立门户）
pub fn default_staging_dir() -> String {
    #[cfg(target_os = "windows")]
    {
        if Path::new("D:\\").exists() {
            return DEFAULT_STAGING_WINDOWS.to_string();
        }
        log::warn!(
            "[install] 本机无 D 盘，默认 staging 回落到用户数据目录（不默认写系统盘根）"
        );
        if let Ok(p) = crate::paths::user_data_dir() {
            return normalize_dir(&p.join("staging").to_string_lossy());
        }
        DEFAULT_STAGING_WINDOWS.to_string()
    }
    #[cfg(not(target_os = "windows"))]
    {
        crate::paths::user_data_dir()
            .map(|p| normalize_dir(&p.join("staging").to_string_lossy()))
            .unwrap_or_else(|_| "/tmp/nexusvideo_staging".to_string())
    }
}

/// 系统盘盘符（Windows）。%SystemDrive% 通常为 "C:"，取不到则保守按 'C'。
#[cfg(windows)]
fn system_drive_letter() -> char {
    std::env::var("SystemDrive")
        .ok()
        .and_then(|s| s.chars().next())
        .map(|c| c.to_ascii_uppercase())
        .unwrap_or('C')
}

/// 提取盘符（`D:/foo` → Some("D:")）；macOS/Linux 返回 None
fn drive_of(normalized: &str) -> Option<String> {
    let b = normalized.as_bytes();
    if b.len() >= 2 && b[1] == b':' && b[0].is_ascii_alphabetic() {
        Some(format!("{}:", (b[0] as char).to_ascii_uppercase()))
    } else {
        None
    }
}

/// 是否落在"系统卷"上（需要警示的位置）。
///   Windows：盘符 == %SystemDrive%
///   macOS  ：既不在用户 home 下、也不在 /Volumes 外挂卷下 → 视为系统卷
fn is_system_location(normalized: &str) -> bool {
    #[cfg(target_os = "windows")]
    {
        drive_of(normalized)
            .map(|d| d.starts_with(system_drive_letter()))
            .unwrap_or(false)
    }
    #[cfg(not(target_os = "windows"))]
    {
        let home = dirs::home_dir()
            .map(|h| normalize_dir(&h.to_string_lossy()))
            .unwrap_or_default();
        let in_home = !home.is_empty() && normalized.starts_with(&home);
        let in_volumes = normalized.starts_with("/Volumes/");
        !in_home && !in_volumes
    }
}

/// 非侵入式可写探测：在「最近存在的祖先目录」里建删一个探针文件。
///
/// 为什么不直接 create_dir_all(目标目录)：体检会在每次弹 dialog 后触发，用户
/// 可能只是看看就取消，静默造目录是不该有的副作用。真正建目录交给后端拉取时做。
fn probe_writable(normalized: &str) -> (bool, Option<String>) {
    let Some(anchor) = nearest_existing(Path::new(normalized)) else {
        return (
            false,
            Some(format!("路径不可达：{normalized} 及其所有上级目录都不存在")),
        );
    };
    let probe = anchor.join(".nexusvideo_write_probe");
    match std::fs::write(&probe, b"nexusvideo") {
        Ok(_) => {
            let _ = std::fs::remove_file(&probe);
            (true, None)
        }
        Err(e) => (
            false,
            Some(format!(
                "目录不可写（在 {} 创建测试文件失败：{e}）。若是受保护位置，请换一个普通数据盘目录，或以管理员身份运行",
                anchor.display()
            )),
        ),
    }
}

// ===========================================================================
// 二、目录体检结果（IPC 返回给前端，字段直接可渲染）
// ===========================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DirAudit {
    /// 规范化路径（**这个值才是给后端 POST /install/pull 用的 target_dir**）
    pub path: String,
    /// 平台原生显示形态（Windows 反斜杠），仅供 UI 展示
    pub display_path: String,
    pub exists: bool,
    pub writable: bool,
    /// 盘符，如 "D:"；macOS 为 null
    pub drive: Option<String>,
    /// 是否落在系统卷（Windows = C 盘）→ 前端必须显式警示
    pub is_system_drive: bool,
    pub free_bytes: u64,
    pub free_human: String,
    pub total_bytes: u64,
    pub total_human: String,
    pub required_bytes: u64,
    pub required_human: String,
    /// 空间是否达到推荐值
    pub space_ok: bool,
    /// "ok" | "warn" | "block"；block 表示 start_install 会拒绝
    pub level: String,
    /// 中文警示文案，前端按序展示即可（不含英文术语）
    pub warnings: Vec<String>,
    /// 是否等于当前平台默认目录
    pub is_default: bool,
}

/// 目录体检核心逻辑（纯函数式，除了可写探针无副作用；便于单测）
pub fn audit_dir(raw: &str) -> DirAudit {
    let path = normalize_dir(raw);
    let p = Path::new(&path);
    let exists = p.exists();
    let drive = drive_of(&path);
    let is_system_drive = is_system_location(&path);

    let mut warnings: Vec<String> = Vec::new();
    let mut level = LEVEL_OK;

    // ---- 可写性 ----
    let (writable, write_err) = probe_writable(&path);
    if let Some(msg) = write_err {
        warnings.push(msg);
        level = LEVEL_BLOCK;
    }

    // ---- 剩余空间 ----
    let (free_bytes, total_bytes) = disk_space_of(p).unwrap_or_else(|e| {
        // 不静默吞错：查不到空间就明确告诉用户"没测出来"，而不是假装 0/正常
        log::warn!("[install] 目标盘空间查询失败 path={path}: {e}");
        warnings.push(
            "无法读取该目录所在磁盘的剩余空间，请自行确认至少有 20GB 可用空间".to_string(),
        );
        (0, 0)
    });

    let space_ok = free_bytes >= REQUIRED_RECOMMENDED_BYTES;
    if total_bytes > 0 {
        if free_bytes < REQUIRED_MIN_BYTES {
            warnings.push(format!(
                "剩余空间严重不足：仅 {}，拉取需要约 {}（模型与组件合计），请先清理磁盘或换个目录",
                human_size(free_bytes),
                human_size(REQUIRED_RECOMMENDED_BYTES)
            ));
            level = LEVEL_BLOCK;
        } else if !space_ok {
            warnings.push(format!(
                "剩余空间偏紧：{}，建议留出 {} 以上，否则可能在解压阶段失败",
                human_size(free_bytes),
                human_size(REQUIRED_RECOMMENDED_BYTES)
            ));
            if level == LEVEL_OK {
                level = LEVEL_WARN;
            }
        }
    }

    // ---- 系统盘警示（允许继续，但绝不默认，也绝不静默）----
    if is_system_drive {
        #[cfg(target_os = "windows")]
        warnings.push(format!(
            "你选择的是系统盘（{}）。模型与组件体积很大，放系统盘可能拖慢开机、占满 C 盘，也可能受系统保护策略影响写入。建议改用数据盘，例如 {}",
            drive.clone().unwrap_or_else(|| "C:".to_string()),
            DEFAULT_STAGING_WINDOWS
        ));
        #[cfg(not(target_os = "windows"))]
        warnings.push(
            "你选择的位置在系统卷上，且不在个人用户目录内。大体积模型建议放到个人目录或外接卷，以免受系统权限保护影响写入。".to_string(),
        );
        if level == LEVEL_OK {
            level = LEVEL_WARN;
        }
    }

    if !exists && level != LEVEL_BLOCK {
        warnings.push("该目录尚不存在，开始拉取时会自动创建".to_string());
    }

    DirAudit {
        display_path: display_form(&path),
        is_default: path == default_staging_dir(),
        path,
        exists,
        writable,
        drive,
        is_system_drive,
        free_bytes,
        free_human: human_size(free_bytes),
        total_bytes,
        total_human: human_size(total_bytes),
        required_bytes: REQUIRED_RECOMMENDED_BYTES,
        required_human: human_size(REQUIRED_RECOMMENDED_BYTES),
        space_ok,
        level: level.to_string(),
        warnings,
    }
}

// ===========================================================================
// 三、IPC 命令：目录选择与体检
// ===========================================================================

/// 取当前平台的默认拉取目录（Windows: D:/nexusvideo_staging）
#[tauri::command]
pub async fn get_default_install_dir() -> Result<String, String> {
    Ok(default_staging_dir())
}

/// 对指定目录做体检（前端手输路径 / 页面加载回显时调用）
#[tauri::command]
pub async fn audit_install_dir(dir: Option<String>) -> Result<DirAudit, String> {
    let target = dir
        .map(|d| normalize_dir(&d))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(default_staging_dir);
    Ok(audit_dir(&target))
}

/// 弹出**原生**目录选择框，返回用户选定目录的体检结果；用户取消返回 null。
///
/// 入参 `current`：对话框的起始定位目录，缺省用平台默认（Windows D 盘）。
/// 返回 `Option<DirAudit>`：`audit.path` 即前端该拿去发 `POST /install/pull` 的
/// `target_dir`；`audit.level == "warn"` 时前端务必把 `warnings` 显式展示出来
/// （例如选到 C 盘），`"block"` 时禁用「开始拉取」按钮。
#[tauri::command]
pub async fn pick_install_dir(
    app: AppHandle,
    current: Option<String>,
) -> Result<Option<DirAudit>, String> {
    let start = current
        .map(|c| normalize_dir(&c))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(default_staging_dir);

    // 起始目录若还不存在，定位到最近的存在祖先，否则部分系统会忽略 set_directory
    let start_dir = nearest_existing(Path::new(&start));
    log::info!(
        "[install] 打开目录选择框，起始定位: {:?}（请求值 {start}）",
        start_dir
    );

    // 用回调 + oneshot 而非 blocking_pick_folder：
    // 原生文件框必须在主线程泵消息，blocking_* 在异步 command 里调用一旦线程
    // 模型变化就有死锁风险；回调版由插件内部 dispatch 到主线程，最稳。
    let (tx, rx) = tokio::sync::oneshot::channel();
    let mut builder = app
        .dialog()
        .file()
        .set_title("选择 NexusVideo 组件与模型的存放目录")
        .set_can_create_directories(true);
    if let Some(d) = start_dir {
        builder = builder.set_directory(d);
    }
    builder.pick_folder(move |picked| {
        let _ = tx.send(picked);
    });

    let picked = rx
        .await
        .map_err(|_| "目录选择框异常关闭（回调通道断开），请重试".to_string())?;

    match picked {
        None => {
            log::info!("[install] 用户取消了目录选择");
            Ok(None)
        }
        Some(fp) => {
            // FilePath 可能是 Url(file://) 或 Path，统一转 PathBuf
            let pb = fp
                .into_path()
                .map_err(|e| format!("选定路径无法解析为本地目录: {e}"))?;
            let audit = audit_dir(&pb.to_string_lossy());
            log::info!(
                "[install] 用户选定目录: {} (level={}, 系统盘={}, 剩余={})",
                audit.path,
                audit.level,
                audit.is_system_drive,
                audit.free_human
            );
            for w in &audit.warnings {
                log::warn!("[install] 目录警示: {w}");
            }
            Ok(Some(audit))
        }
    }
}

// ===========================================================================
// 四、IPC 命令：触发一键拉取 / 自检（Rust → FastAPI 代理）
// ===========================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PullAccepted {
    pub task_id: String,
    pub accepted: bool,
    /// 实际提交给后端的目标目录（规范化后），供前端回显与日志对齐
    pub target_dir: String,
}

/// 触发一键拉取：POST /install/pull → 返回 task_id
///
/// 沿用 commands.rs 顶部声明的代理约定（前端不直连生成/控制类 HTTP），
/// 好处是拉取前能在 Rust 侧再上一道闸：目标目录 `block` 就直接拒绝，
/// 不把注定失败的任务丢给后端，也避免用户等半天才看到失败。
#[tauri::command]
pub async fn start_install(
    state: State<'_, AppState>,
    target_dir: Option<String>,
    mirror: Option<String>,
) -> Result<PullAccepted, String> {
    let target = target_dir
        .map(|t| normalize_dir(&t))
        .filter(|s| !s.is_empty());

    // 落盘前最后一道闸
    let effective = match &target {
        Some(t) => {
            let audit = audit_dir(t);
            if audit.level == LEVEL_BLOCK {
                return Err(format!(
                    "目标目录不可用，已取消拉取：{}",
                    audit.warnings.join("；")
                ));
            }
            if audit.is_system_drive {
                // 允许继续，但留痕（用户已在 UI 上被警示过）
                log::warn!("[install] 用户坚持把拉取目标放在系统盘: {}", audit.path);
            }
            t.clone()
        }
        None => {
            log::info!("[install] 未指定 target_dir，交由后端使用其默认 staging 根");
            default_staging_dir()
        }
    };

    let mut body = serde_json::Map::new();
    if let Some(t) = &target {
        body.insert("target_dir".into(), serde_json::Value::String(t.clone()));
    }
    if let Some(m) = mirror.filter(|s| !s.trim().is_empty()) {
        body.insert("mirror".into(), serde_json::Value::String(m));
    }

    let url = format!("{}/install/pull", state.fastapi_base());
    log::info!("[install] POST {url} body={:?}", body);

    let resp = state
        .http
        .post(&url)
        .json(&serde_json::Value::Object(body))
        .send()
        .await
        .map_err(|e| {
            format!("无法连接本地服务（127.0.0.1:9881），请确认后端已启动：{e}")
        })?;

    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(format!(
            "后端拒绝了拉取请求（HTTP {}）：{}",
            status.as_u16(),
            if text.is_empty() { "无响应内容".into() } else { text }
        ));
    }

    let v: serde_json::Value = serde_json::from_str(&text)
        .map_err(|e| format!("后端返回的内容无法解析（{e}）：{text}"))?;
    let task_id = v
        .get("task_id")
        .and_then(|x| x.as_str())
        .ok_or_else(|| format!("后端返回缺少 task_id：{text}"))?
        .to_string();
    let accepted = v.get("accepted").and_then(|x| x.as_bool()).unwrap_or(false);

    log::info!("[install] 拉取已受理 task_id={task_id} accepted={accepted} target={effective}");
    Ok(PullAccepted {
        task_id,
        accepted,
        target_dir: effective,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VerifyResult {
    pub missing: Vec<String>,
    pub ok: bool,
}

/// 模型齐全度自检：GET /install/verify?target_dir=
#[tauri::command]
pub async fn verify_install(
    state: State<'_, AppState>,
    target_dir: Option<String>,
) -> Result<VerifyResult, String> {
    let mut url = format!("{}/install/verify", state.fastapi_base());
    if let Some(t) = target_dir
        .map(|t| normalize_dir(&t))
        .filter(|s| !s.is_empty())
    {
        // 手工转义即可（路径只含盘符/斜杠/常规字符），避免引入 urlencoding 依赖
        let enc = t.replace('%', "%25").replace(' ', "%20").replace('#', "%23");
        url = format!("{url}?target_dir={enc}");
    }

    let resp = state
        .http
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("无法连接本地服务（127.0.0.1:9881）：{e}"))?;

    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(format!("自检失败（HTTP {}）：{text}", status.as_u16()));
    }
    serde_json::from_str::<VerifyResult>(&text)
        .map_err(|e| format!("自检返回无法解析（{e}）：{text}"))
}

// ===========================================================================
// 五、SSE 桥接（降级通道，详见文件头「SSE 决策」）
// ===========================================================================

/// 启动 Rust 侧 SSE 转发：GET /install/progress → Tauri event。
///
/// 事件：`install://progress`（每帧原始 JSON）、`install://done`、`install://failed`。
/// 幂等：已在监听则直接返回 false，不会开出第二条连接。
#[tauri::command]
pub async fn listen_install_progress(
    app: AppHandle,
    state: State<'_, AppState>,
) -> Result<bool, String> {
    {
        let handles = state.progress_handles.read().await;
        if handles.contains_key(INSTALL_SSE_KEY) {
            log::info!("[install] SSE 桥接已在运行，跳过重复启动");
            return Ok(false);
        }
    }

    let url = format!("{}/install/progress", state.fastapi_base());
    let cancel = Arc::new(tokio::sync::Notify::new());
    let cancel_clone = Arc::clone(&cancel);
    let app_clone = app.clone();
    let url_log = url.clone();

    let handle = tokio::spawn(async move {
        sse_forward_loop(url, app_clone, cancel_clone).await;
    });

    state
        .progress_handles
        .write()
        .await
        .insert(INSTALL_SSE_KEY.to_string(), (handle, cancel));

    log::info!("[install] SSE 桥接已启动: {url_log}");
    Ok(true)
}

/// 停止 SSE 转发
#[tauri::command]
pub async fn stop_install_progress(state: State<'_, AppState>) -> Result<bool, String> {
    let mut handles = state.progress_handles.write().await;
    if let Some((handle, cancel)) = handles.remove(INSTALL_SSE_KEY) {
        cancel.notify_one();
        handle.abort();
        log::info!("[install] SSE 桥接已停止");
        Ok(true)
    } else {
        Ok(false)
    }
}

/// SSE 拉流主循环：按 `\n\n` 切帧、取 `data:` 行、解析 JSON 后转发为 Tauri event。
async fn sse_forward_loop(url: String, app: AppHandle, cancel: Arc<tokio::sync::Notify>) {
    // 注：这里用 `Response::chunk()` 而不是 `bytes_stream()`——后者需要给 reqwest
    // 开 `stream` feature，会连带把 wasm-streams 写进 Cargo.lock，导致 CI 的
    // `cargo check --offline`（缓存键 = Cargo.lock 哈希）缓存未命中。
    // chunk() 不依赖任何 feature，逐块读取语义等价。

    // 专用 client：**不设整体 timeout**（AppState.http 的 30s 会掐断长连接），
    // 只设连接超时；空闲检测由下方 tokio::time::timeout 逐帧兜底。
    let client = match reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(5))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            emit_failed(&app, &format!("进度通道初始化失败：{e}"));
            return;
        }
    };

    let resp = tokio::select! {
        _ = cancel.notified() => return,
        r = client.get(&url).header("Accept", "text/event-stream").send() => r,
    };

    let mut resp = match resp {
        Ok(r) if r.status().is_success() => r,
        Ok(r) => {
            emit_failed(
                &app,
                &format!("进度通道被拒绝（HTTP {}）", r.status().as_u16()),
            );
            return;
        }
        Err(e) => {
            emit_failed(&app, &format!("无法连接进度通道：{e}"));
            return;
        }
    };

    let mut buf = String::new();

    loop {
        let next = tokio::select! {
            _ = cancel.notified() => {
                log::info!("[install] SSE 收到取消信号，退出转发");
                return;
            }
            chunk = tokio::time::timeout(SSE_IDLE_TIMEOUT, resp.chunk()) => chunk,
        };

        let chunk = match next {
            Err(_) => {
                // 空闲超时：明确上报，不静默挂死
                emit_failed(
                    &app,
                    &format!(
                        "进度通道超过 {} 秒无响应，已断开（拉取可能仍在后台进行，可刷新页面重连）",
                        SSE_IDLE_TIMEOUT.as_secs()
                    ),
                );
                return;
            }
            Ok(Err(e)) => {
                emit_failed(&app, &format!("进度通道读取中断：{e}"));
                return;
            }
            Ok(Ok(None)) => {
                log::info!("[install] SSE 流已正常结束");
                return;
            }
            Ok(Ok(Some(bytes))) => bytes,
        };

        buf.push_str(&String::from_utf8_lossy(&chunk));

        // 按空行切帧（SSE 规范：帧以 \n\n 结束；兼容 \r\n\r\n）
        loop {
            let cut = buf
                .find("\n\n")
                .map(|i| (i, 2))
                .or_else(|| buf.find("\r\n\r\n").map(|i| (i, 4)));
            let Some((idx, sep_len)) = cut else { break };
            let frame: String = buf.drain(..idx + sep_len).collect();

            for line in frame.lines() {
                let line = line.trim_end();
                let Some(payload) = line.strip_prefix("data:") else {
                    continue; // 忽略 event:/id:/retry:/注释行
                };
                let payload = payload.trim();
                if payload.is_empty() {
                    continue;
                }
                match serde_json::from_str::<serde_json::Value>(payload) {
                    Ok(v) => {
                        let done = v.get("done").and_then(|x| x.as_bool()).unwrap_or(false);
                        let failed = v.get("failed").and_then(|x| x.as_bool()).unwrap_or(false);

                        let _ = app.emit(event_name::INSTALL_PROGRESS, v.clone());

                        if failed {
                            let _ = app.emit(event_name::INSTALL_FAILED, v);
                            log::warn!("[install] 后端上报拉取失败，SSE 转发结束");
                            return;
                        }
                        if done {
                            let _ = app.emit(event_name::INSTALL_DONE, v);
                            log::info!("[install] 后端上报拉取完成，SSE 转发结束");
                            return;
                        }
                    }
                    Err(e) => {
                        // 单帧坏数据不该拖垮整条通道，记日志继续
                        log::warn!("[install] 进度帧解析失败（已跳过）: {e} raw={payload}");
                    }
                }
            }
        }

        // 防御：后端若异常输出无分隔的巨量数据，避免 buf 无限膨胀
        if buf.len() > 1024 * 1024 {
            log::warn!("[install] 进度缓冲超过 1MB 仍无完整帧，清空以防内存膨胀");
            buf.clear();
        }
    }
}

fn emit_failed(app: &AppHandle, msg: &str) {
    log::error!("[install] {msg}");
    let _ = app.emit(
        event_name::INSTALL_FAILED,
        serde_json::json!({
            "stage": "failed",
            "message": msg,
            "failed": true,
            "source": "tauri-bridge",
        }),
    );
}

// ===========================================================================
// 单元测试（cargo test -p nexusvideo-client install_bridge）
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_backslash_to_forward() {
        assert_eq!(
            normalize_dir("D:\\nexusvideo_staging"),
            "D:/nexusvideo_staging"
        );
    }

    #[test]
    fn normalize_strips_trailing_slash_but_keeps_volume_root() {
        assert_eq!(
            normalize_dir("D:\\nexusvideo_staging\\"),
            "D:/nexusvideo_staging"
        );
        assert_eq!(normalize_dir("D:/nexusvideo_staging//"), "D:/nexusvideo_staging");
        assert_eq!(normalize_dir("D:\\"), "D:/");
    }

    #[test]
    fn normalize_uppercases_drive_letter() {
        assert_eq!(normalize_dir("d:/foo/bar"), "D:/foo/bar");
    }

    #[test]
    fn normalize_collapses_duplicate_slashes() {
        assert_eq!(normalize_dir("D:\\\\a\\\\\\b"), "D:/a/b");
    }

    #[test]
    fn normalize_trims_and_handles_empty() {
        assert_eq!(normalize_dir("  D:/x  "), "D:/x");
        assert_eq!(normalize_dir("   "), "");
        assert_eq!(normalize_dir(""), "");
    }

    #[cfg(windows)]
    #[test]
    fn normalize_msys_style_to_windows() {
        // Git Bash 里手输 /d/nexusvideo_staging 也要能落成后端认得的形态
        assert_eq!(
            normalize_dir("/d/nexusvideo_staging"),
            "D:/nexusvideo_staging"
        );
        assert_eq!(normalize_dir("/c/Users/x"), "C:/Users/x");
    }

    #[cfg(not(windows))]
    #[test]
    fn normalize_keeps_posix_absolute_path() {
        // macOS 下 /d/foo 是合法路径，绝不能被当成盘符改写
        assert_eq!(normalize_dir("/d/foo"), "/d/foo");
        assert_eq!(normalize_dir("/Users/me/staging/"), "/Users/me/staging");
        assert_eq!(normalize_dir("/"), "/");
    }

    #[test]
    fn drive_extraction() {
        assert_eq!(drive_of("D:/x"), Some("D:".to_string()));
        assert_eq!(drive_of("d:/x"), Some("D:".to_string()));
        assert_eq!(drive_of("/Users/me"), None);
    }

    #[test]
    fn display_form_is_native() {
        let d = display_form("D:/a/b");
        if cfg!(windows) {
            assert_eq!(d, "D:\\a\\b");
        } else {
            assert_eq!(d, "D:/a/b");
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_default_is_d_drive_when_present() {
        // 本机有 D 盘时默认值必须与后端 config.staging_dir 完全一致
        if Path::new("D:\\").exists() {
            assert_eq!(default_staging_dir(), DEFAULT_STAGING_WINDOWS);
        }
    }

    #[cfg(windows)]
    #[test]
    fn system_drive_is_flagged() {
        let sys = system_drive_letter();
        let audit = audit_dir(&format!("{sys}:/nexusvideo_staging_test"));
        assert!(audit.is_system_drive, "系统盘必须被识别");
        assert!(
            audit.level == LEVEL_WARN || audit.level == LEVEL_BLOCK,
            "系统盘至少要 warn，实际 {}",
            audit.level
        );
        assert!(
            audit.warnings.iter().any(|w| w.contains("系统盘")),
            "必须给出中文系统盘警示，实际 warnings={:?}",
            audit.warnings
        );
    }

    #[cfg(windows)]
    #[test]
    fn data_drive_is_not_flagged_as_system() {
        if Path::new("D:\\").exists() && system_drive_letter() != 'D' {
            let audit = audit_dir("D:\\nexusvideo_staging");
            assert!(!audit.is_system_drive);
            assert_eq!(audit.path, "D:/nexusvideo_staging");
            // 数据盘且空间充足时应当放行
            if audit.free_bytes >= REQUIRED_RECOMMENDED_BYTES {
                assert_eq!(audit.level, LEVEL_OK, "warnings={:?}", audit.warnings);
            }
        }
    }

    #[test]
    fn audit_reports_required_space() {
        let a = audit_dir(&default_staging_dir());
        assert_eq!(a.required_bytes, REQUIRED_RECOMMENDED_BYTES);
        assert!(a.required_human.contains("GB"));
        // path 必须已规范化（正斜杠），这是递给后端的形态
        assert!(!a.path.contains('\\'), "path 不应含反斜杠: {}", a.path);
    }

    #[test]
    fn unreachable_path_is_blocked_not_silent() {
        // 不存在的盘符：必须 block 且给出可读原因，绝不静默通过
        let a = audit_dir("Z:/definitely/not/here/nexusvideo");
        if !Path::new("Z:\\").exists() {
            assert_eq!(a.level, LEVEL_BLOCK);
            assert!(!a.warnings.is_empty());
        }
    }
}
