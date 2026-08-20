//! file_manager.rs — 本地文件系统管理
//! ============================================================================
//! 职责：
//!   1. 视频输出路径管理（按日期分组：~/NexusVideo/output/videos/2026-08-18/）
//!   2. 缩略图缓存管理（~/NexusVideo/output/thumbnails/，最大 100MB）
//!   3. 配置文件读写（~/NexusVideo/config/settings.json）
//!   4. 磁盘空间检查（启动时检查，低于 10GB 弹窗警告）
//!   5. 自动清理旧文件（超过 30 天，可配置开关）
//!
//! 所有操作通过 Tauri IPC 命令暴露给前端。
use crate::paths;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

// ============================================================================
// 常量
// ============================================================================

/// 磁盘空间最低阈值（10GB）
const MIN_DISK_SPACE_BYTES: u64 = 10 * 1024 * 1024 * 1024;

/// 缩略图缓存最大容量（100MB）
const THUMB_CACHE_MAX_BYTES: u64 = 100 * 1024 * 1024;

/// 旧文件清理默认天数
const DEFAULT_CLEANUP_DAYS: u32 = 30;

// ============================================================================
// 视频列表项
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VideoEntry {
    pub filename: String,
    pub path: String,
    pub created_at: u64,       // Unix timestamp (秒)
    pub size_bytes: u64,
    pub size_human: String,
    pub thumb_path: Option<String>,
    pub date_group: String,    // "2026-08-18"
}

// ============================================================================
// 磁盘空间信息
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DiskSpaceInfo {
    pub free_bytes: u64,
    pub free_human: String,
    pub total_bytes: u64,
    pub total_human: String,
    pub used_bytes: u64,
    pub used_human: String,
    pub free_percent: u8,
    pub warning: bool,          // 低于 MIN_DISK_SPACE_BYTES
    pub warning_msg: String,
}

// ============================================================================
// 清理结果
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CleanupResult {
    pub deleted_count: u32,
    pub freed_bytes: u64,
    pub freed_human: String,
    pub errors: Vec<String>,
}

// ============================================================================
// 路径管理
// ============================================================================

/// 视频存储根目录：~/NexusVideo/output/videos/
pub fn videos_dir() -> std::io::Result<PathBuf> {
    let out = paths::output_dir().map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))?;
    let dir = out.join("videos");
    Ok(dir)
}

/// 按日期分组的视频目录：~/NexusVideo/output/videos/{YYYY-MM-DD}/
pub fn video_date_dir(date: &str) -> std::io::Result<PathBuf> {
    let dir = videos_dir()?;
    Ok(dir.join(date))
}

/// 今天日期的视频目录（自动创建）
pub fn today_video_dir() -> std::io::Result<PathBuf> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    // 简易 ISO 日期（本地时区近似，不引 chrono 以减小体积）
    let secs = now.as_secs() as i64;
    // UTC 日期（本地化需要 chrono，这里用 UTC 作为近似）
    let days = secs / 86400;
    // 从 Unix epoch (1970-01-01) 计算日期
    let (y, m, d) = unix_days_to_date(days as u64);
    let date_str = format!("{:04}-{:02}-{:02}", y, m, d);
    let dir = video_date_dir(&date_str)?;
    fs::create_dir_all(&dir)?;
    Ok(dir)
}

/// 缩略图缓存目录：~/NexusVideo/output/thumbnails/
pub fn thumbnails_dir() -> std::io::Result<PathBuf> {
    let out = paths::output_dir().map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))?;
    let dir = out.join("thumbnails");
    fs::create_dir_all(&dir)?;
    Ok(dir)
}

/// 用户配置文件路径：~/NexusVideo/config/settings.json
pub fn settings_file() -> std::io::Result<PathBuf> {
    let data_dir = paths::user_data_dir().map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))?;
    let config_dir = data_dir.join("config");
    fs::create_dir_all(&config_dir)?;
    Ok(config_dir.join("settings.json"))
}

// Unix days 转日期（简化版，仅用于文件分组，精度到日即可）
fn unix_days_to_date(days: u64) -> (u32, u32, u32) {
    // 简化算法：基于 1970-01-01 起的天数
    let mut d = days as i64;
    // 计算年
    let mut year: i64 = 1970;
    loop {
        let days_in_year = if is_leap(year) { 366 } else { 365 };
        if d < days_in_year {
            break;
        }
        d -= days_in_year;
        year += 1;
    }
    // 计算月
    let months = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    let leap_days = if is_leap(year) { 29 } else { 28 };
    let mut month: i64 = 1;
    for i in 0..12 {
        let md = if i == 1 { leap_days } else { months[i] };
        if d < md {
            break;
        }
        d -= md;
        month += 1;
    }
    let day = d + 1;
    (year as u32, month as u32, day as u32)
}

fn is_leap(year: i64) -> bool {
    (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0)
}

// ============================================================================
// 磁盘空间检查
// ============================================================================

/// 检查 output 目录所在分区的剩余空间
pub fn check_disk_space() -> std::io::Result<DiskSpaceInfo> {
    let out_dir = paths::output_dir().map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))?;

    // 跨平台磁盘空间获取
    #[cfg(target_os = "windows")]
    let (free, total) = get_disk_space_windows(&out_dir)?;

    #[cfg(target_os = "macos")]
    let (free, total) = get_disk_space_posix(&out_dir)?;

    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let (free, total) = get_disk_space_posix(&out_dir)?;

    let used = if total >= free {
        total - free
    } else {
        0
    };
    let free_percent = if total > 0 {
        ((free * 100) / total) as u8
    } else {
        0
    };
    let warning = free < MIN_DISK_SPACE_BYTES;

    Ok(DiskSpaceInfo {
        free_bytes: free,
        free_human: human_size(free),
        total_bytes: total,
        total_human: human_size(total),
        used_bytes: used,
        used_human: human_size(used),
        free_percent,
        warning,
        warning_msg: if warning {
            format!(
                "磁盘剩余空间不足（{}），建议释放至少 {} 空间",
                human_size(free),
                human_size(MIN_DISK_SPACE_BYTES)
            )
        } else {
            String::new()
        },
    })
}

#[cfg(target_os = "windows")]
fn get_disk_space_windows(dir: &Path) -> std::io::Result<(u64, u64)> {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use winapi::um::fileapi::GetDiskFreeSpaceExW;
    use winapi::um::winnt::ULARGE_INTEGER;
    let wide: Vec<u16> = OsStr::new(dir)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    // GetDiskFreeSpaceExW 的 2/3/4 参数类型为 PULARGE_INTEGER（无符号 64 位）。
    // 为避免 winapi UNION 元组结构体的字段访问坑，直接用 u64 承载返回值，
    // 再强转为 *mut ULARGE_INTEGER 传入（u64 与 ULARGE_INTEGER 同为 8 字节、布局一致）。
    let mut free_bytes: u64 = 0;
    let mut total_bytes: u64 = 0;
    let mut avail_bytes: u64 = 0;
    let ret = unsafe {
        GetDiskFreeSpaceExW(
            wide.as_ptr(),
            &mut free_bytes as *mut u64 as *mut ULARGE_INTEGER,
            &mut total_bytes as *mut u64 as *mut ULARGE_INTEGER,
            &mut avail_bytes as *mut u64 as *mut ULARGE_INTEGER,
        )
    };
    if ret == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok((free_bytes, total_bytes))
}

#[cfg(not(target_os = "windows"))]
fn get_disk_space_posix(dir: &Path) -> std::io::Result<(u64, u64)> {
    // macOS/Linux: 使用 std::fs::metadata 不直接支持空间查询
    // 简化方案：通过系统命令获取
    let output = std::process::Command::new("df")
        .arg("-P")
        .arg(dir.to_string_lossy().to_string())
        .output()
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, format!("df 命令失败: {e}")))?;

    if !output.status.success() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "df 命令执行失败",
        ));
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let lines: Vec<&str> = stdout.lines().collect();
    if lines.len() < 2 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "df 输出格式异常",
        ));
    }
    // df 输出（跳过表头）： Filesystem 1K-blocks Used Available Use% Mounted
    let parts: Vec<&str> = lines[1].split_whitespace().collect();
    if parts.len() < 4 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Other,
            "df 输出解析失败",
        ));
    }
    let total_kb: u64 = parts[1].parse().unwrap_or(0);
    let free_kb: u64 = parts[3].parse().unwrap_or(0);
    Ok((free_kb * 1024, total_kb * 1024))
}

// ============================================================================
// 视频列表获取
// ============================================================================

/// 获取历史视频列表（遍历 videos/ 下所有日期的子目录）——同步实现
/// 上层 async command `get_video_list` 会调用本函数。
pub fn get_video_list_inner() -> std::io::Result<Vec<VideoEntry>> {
    let videos_root = videos_dir()?;
    if !videos_root.exists() {
        return Ok(vec![]);
    }

    let mut entries: Vec<VideoEntry> = vec![];

    // 遍历日期子目录
    for date_entry in fs::read_dir(&videos_root)?.filter_map(|e| e.ok()) {
        let date_dir = date_entry.path();
        if !date_dir.is_dir() {
            continue;
        }
        let date_group = date_dir.file_name().and_then(|n| n.to_str()).unwrap_or("").to_string();

        // 遍历日期目录下的视频文件
        for file_entry in fs::read_dir(&date_dir)?.filter_map(|e| e.ok()) {
            let path = file_entry.path();
            if !path.is_file() {
                continue;
            }
            let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("");
            if !is_video_ext(ext) {
                continue;
            }
            let metadata = file_entry.metadata()?;
            let size = metadata.len();
            let created = metadata
                .created()
                .or_else(|_| metadata.modified())
                .unwrap_or(SystemTime::UNIX_EPOCH);
            let created_ts = created
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            let filename = path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("")
                .to_string();

            // 查找对应缩略图
            let thumb = find_thumbnail(&path);

            entries.push(VideoEntry {
                filename,
                path: path.to_string_lossy().to_string(),
                created_at: created_ts,
                size_bytes: size,
                size_human: human_size(size),
                thumb_path: thumb,
                // date_group 在同一日期目录下会被多次 push，需 clone 避免 move-after-move
                date_group: date_group.clone(),
            });
        }
    }

    // 按创建时间倒序
    entries.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    Ok(entries)
}

/// 常见视频扩展名
fn is_video_ext(ext: &str) -> bool {
    matches!(
        ext.to_lowercase().as_str(),
        "mp4" | "mov" | "avi" | "mkv" | "webm" | "gif"
    )
}

/// 查找视频文件对应的缩略图
fn find_thumbnail(video_path: &Path) -> Option<String> {
    let thumb_dir = thumbnails_dir().ok()?;
    let stem = video_path.file_stem()?.to_string_lossy().to_string();
    let ext = video_path.extension()?.to_string_lossy().to_string();
    let thumb_name = format!("{}_thumb.{ext}", stem);
    let thumb_path = thumb_dir.join(&thumb_name);
    if thumb_path.exists() {
        Some(thumb_path.to_string_lossy().to_string())
    } else {
        None
    }
}

// ============================================================================
// 缩略图缓存管理
// ============================================================================

/// 计算缩略图缓存当前大小
pub fn thumbnail_cache_size() -> u64 {
    let dir = match thumbnails_dir() {
        Ok(d) => d,
        Err(_) => return 0,
    };
    dir_total_size(&dir)
}

/// 清理缩略图缓存，保持不超过最大容量
/// 策略：按修改时间升序删除最旧的文件
pub fn evict_thumbnail_cache() -> std::io::Result<u64> {
    let dir = thumbnails_dir()?;
    let mut freed = 0u64;

    loop {
        let current_size = dir_total_size(&dir);
        if current_size <= THUMB_CACHE_MAX_BYTES {
            break;
        }

        // 找到最旧的文件
        let mut oldest: Option<(PathBuf, SystemTime)> = None;
        for entry in fs::read_dir(&dir)?.filter_map(|e| e.ok()) {
            if !entry.path().is_file() {
                continue;
            }
            let modified = entry.metadata().ok().and_then(|m| m.modified().ok());
            if let Some(mtime) = modified {
                match oldest {
                    None => oldest = Some((entry.path(), mtime)),
                    Some((_, old_mtime)) => {
                        if mtime < old_mtime {
                            oldest = Some((entry.path(), mtime));
                        }
                    }
                }
            }
        }

        let Some((path, _)) = oldest else {
            break;
        };
        let file_size = fs::metadata(&path).ok().map_or(0, |m| m.len());
        fs::remove_file(&path)?;
        freed += file_size;
    }

    Ok(freed)
}

// ============================================================================
// 旧文件清理
// ============================================================================

/// 清理超过指定天数的视频和缩略图——同步实现
/// 上层 async command `cleanup_old_files` 会调用本函数。
pub fn cleanup_old_files_sync(days: u32) -> std::io::Result<CleanupResult> {
    let now = SystemTime::now();
    let cutoff = now - Duration::from_secs(days as u64 * 86400);
    let mut result = CleanupResult {
        deleted_count: 0,
        freed_bytes: 0,
        freed_human: String::new(),
        errors: vec![],
    };

    // 清理视频
    let videos_root = videos_dir()?;
    if videos_root.exists() {
        for date_entry in fs::read_dir(&videos_root)?.filter_map(|e| e.ok()) {
            let date_dir = date_entry.path();
            if !date_dir.is_dir() {
                continue;
            }
            for file_entry in fs::read_dir(&date_dir)?.filter_map(|e| e.ok()) {
                let path = file_entry.path();
                if !path.is_file() || !is_video_ext(file_entry.path().extension().and_then(|e| e.to_str()).unwrap_or("")) {
                    continue;
                }
                let modified = file_entry.metadata().ok().and_then(|m| m.modified().ok());
                if let Some(mtime) = modified {
                    if mtime < cutoff {
                        let size = file_entry.metadata().ok().map_or(0, |m| m.len());
                        if fs::remove_file(&path).is_ok() {
                            result.deleted_count += 1;
                            result.freed_bytes += size;
                        }
                        // 同时删除对应的缩略图
                        if let Some(thumb) = find_thumbnail(&path) {
                            let _ = fs::remove_file(Path::new(&thumb));
                        }
                    }
                }
            }
            // 如果日期目录为空，删除目录
            if let Ok(entries) = fs::read_dir(&date_dir) {
                if entries.count() == 0 {
                    let _ = fs::remove_dir(&date_dir);
                }
            }
        }
    }

    // 清理缩略图（无对应视频的孤立缩略图）
    let thumb_dir = thumbnails_dir()?;
    if thumb_dir.exists() {
        for entry in fs::read_dir(&thumb_dir)?.filter_map(|e| e.ok()) {
            let path = entry.path();
            if !path.is_file() {
                continue;
            }
            let modified = entry.metadata().ok().and_then(|m| m.modified().ok());
            if let Some(mtime) = modified {
                if mtime < cutoff {
                    let size = entry.metadata().ok().map_or(0, |m| m.len());
                    if fs::remove_file(&path).is_ok() {
                        result.deleted_count += 1;
                        result.freed_bytes += size;
                    }
                }
            }
        }
    }

    result.freed_human = human_size(result.freed_bytes);
    Ok(result)
}

// ============================================================================
// 配置文件读写
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct UserSettings {
    pub auto_cleanup_enabled: bool,
    pub auto_cleanup_days: u32,
    pub auto_start_backend: bool,
    pub dark_mode: bool,
    pub language: String,
}

/// 读取用户配置——同步实现
/// 上层 async command `read_settings` 会调用本函数。
pub fn read_settings_sync() -> std::io::Result<UserSettings> {
    let path = settings_file()?;
    if !path.exists() {
        return Ok(UserSettings::default());
    }
    let content = fs::read_to_string(&path)?;
    let settings: UserSettings = serde_json::from_str(&content)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, format!("配置解析失败: {e}")))?;
    Ok(settings)
}

/// 写入用户配置——同步实现
/// 上层 async command `write_settings` 会调用本函数。
pub fn write_settings_sync(settings: &UserSettings) -> std::io::Result<()> {
    let path = settings_file()?;
    let content = serde_json::to_string_pretty(settings)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, format!("配置序列化失败: {e}")))?;
    let mut file = fs::File::create(&path)?;
    file.write_all(content.as_bytes())?;
    Ok(())
}

// ============================================================================
// Tauri IPC 命令
// ============================================================================

/// 获取磁盘空间信息
#[tauri::command]
pub async fn get_disk_space() -> Result<DiskSpaceInfo, String> {
    check_disk_space().map_err(|e| format!("磁盘空间检查失败: {e}"))
}

/// 获取历史视频列表
#[tauri::command]
pub async fn get_video_list() -> Result<Vec<VideoEntry>, String> {
    get_video_list_inner().map_err(|e| format!("获取视频列表失败: {e}"))
}

/// 触发旧文件清理
#[tauri::command]
pub async fn cleanup_old_files(
    // Tauri 2 的 command 宏不会把参数上的 #[serde(...)] 转发到生成的 args 结构体，
    // 直接用 Option<u32> + 手动默认值更稳妥（前端可不传 days，缺失即 None）。
    days: Option<u32>,
) -> Result<CleanupResult, String> {
    let days = days.unwrap_or(0);
    let days = if days == 0 { DEFAULT_CLEANUP_DAYS } else { days };
    cleanup_old_files_sync(days).map_err(|e| format!("清理旧文件失败: {e}"))
}

/// 清理缩略图缓存
#[tauri::command]
pub async fn evict_thumbnails() -> Result<u64, String> {
    evict_thumbnail_cache().map_err(|e| format!("缩略图缓存清理失败: {e}"))
}

/// 读取用户配置
#[tauri::command]
pub async fn read_settings() -> Result<UserSettings, String> {
    read_settings_sync().map_err(|e| format!("读取配置失败: {e}"))
}

/// 写入用户配置
#[tauri::command]
pub async fn write_settings(settings: UserSettings) -> Result<bool, String> {
    write_settings_sync(&settings).map_err(|e| format!("写入配置失败: {e}"))?;
    Ok(true)
}

// ============================================================================
// 辅助函数
// ============================================================================

fn dir_total_size(dir: &Path) -> u64 {
    let mut total = 0u64;
    if let Ok(entries) = fs::read_dir(dir) {
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

fn human_size(bytes: u64) -> String {
    if bytes < 1024 {
        format!("{} B", bytes)
    } else if bytes < 1024 * 1024 {
        format!("{:.1} KB", bytes as f64 / 1024.0)
    } else if bytes < 1024 * 1024 * 1024 {
        format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
    } else {
        format!("{:.1} GB", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
    }
}