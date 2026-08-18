//! static_server.rs — 本地 HTTP 静态文件服务（视频/缩略图预览）
//! ============================================================================
//! 前端 `<video :src="...">` 只能播放 URL，无法直接读取本地文件系统。
//! 本模块在 127.0.0.1:9882 启动一个轻量 HTTP 服务器，将本地上传文件
//! 通过 URL 暴露给 WebView。只监听 127.0.0.1，不对外暴露。
//!
//! 路由：
//!   GET /videos/{task_id}/{filename}     → ./uploads/{task_id}/{filename}
//!   GET /thumbnails/{task_id}/{filename} → ./uploads/{task_id}/{filename}
//!
//! 启动时通过 Tauri 事件 `static://ready` 通知前端服务就绪。
//! 进程退出前必须调用 StaticServer::stop() 关闭服务，避免端口泄漏。

use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::sync::{Mutex, OnceCell};
use tauri::{AppHandle, Emitter};

/// 静态文件服务端口（与前端硬编码值保持一致）
pub const STATIC_PORT: u16 = 9882;
pub const STATIC_HOST: &str = "127.0.0.1";

/// 服务就绪事件名
pub const EVENT_STATIC_READY: &str = "static://ready";

/// 单例静态服务器句柄（全局唯一，通过 Arc 共享）
static STATIC_SERVER: OnceCell<Arc<Mutex<StaticServer>>> = OnceCell::const_new();

/// 静态 HTTP 服务器
pub struct StaticServer {
    /// 后台 accept 循环任务句柄（用于优雅停止）
    accept_handle: Option<tokio::task::JoinHandle<()>>,
}

impl StaticServer {
    /// 创建静态服务器实例（尚未启动）
    pub fn new(app: AppHandle) -> Self {
        Self {
            accept_handle: None,
        }
    }

    /// 启动静态文件服务（绑定 127.0.0.1:9882）
    pub async fn start(&mut self, app: AppHandle) -> Result<(), String> {
        let addr = format!("{}:{}", STATIC_HOST, STATIC_PORT);
        let listener = tokio::net::TcpListener::bind(&addr)
            .await
            .map_err(|e| format!("静态文件服务绑定失败 {}: {e}", addr))?;

        let app_for_spawn = app.clone();
        let accept_handle = tokio::spawn(async move {
            let mut recv = listener;
            loop {
                match recv.accept().await {
                    Ok((stream, _peer)) => {
                        let app_clone = app_for_spawn.clone();
                        tokio::spawn(async move {
                            handle_request(stream, app_clone).await;
                        });
                    }
                    Err(e) => {
                        log::debug!("[static_server] accept 错误，停止: {e}");
                        break;
                    }
                }
            }
        });

        self.accept_handle = Some(accept_handle);

        // 通过 Tauri 事件通知前端服务就绪
        let _ = app.emit(
            EVENT_STATIC_READY,
            serde_json::json!({
                "host": STATIC_HOST,
                "port": STATIC_PORT,
                "base_url": format!("http://{}:{}", STATIC_HOST, STATIC_PORT),
            }),
        );
        log::info!("[static_server] 已启动，监听 {}:{}", STATIC_HOST, STATIC_PORT);

        Ok(())
    }

    /// 优雅关闭静态文件服务
    pub fn stop(&mut self) {
        if let Some(handle) = self.accept_handle.take() {
            handle.abort();
        }
        log::info!("[static_server] 已关闭");
    }
}

/// 获取全局单例静态服务器（创建或返回已有）
pub async fn get_static_server(app: AppHandle) -> Arc<Mutex<StaticServer>> {
    STATIC_SERVER
        .get_or_init(|| async { Arc::new(Mutex::new(StaticServer::new(app))) })
        .await
        .clone()
}

/// 启动全局静态服务器（幂等操作）
pub async fn start_static_server(app: AppHandle) -> Result<(), String> {
    let server = get_static_server(app.clone()).await;
    {
        let mut s = server.lock().await;
        if s.accept_handle.is_some() {
            log::debug!("[static_server] 服务已在运行，跳过重复启动");
            return Ok(());
        }
        s.start(app).await?;
    }
    Ok(())
}

/// 停止全局静态服务器
pub async fn stop_static_server() {
    if let Some(server) = STATIC_SERVER.get() {
        let mut s = server.lock().await;
        s.stop();
    }
}

// ============================================================================
// HTTP 请求处理
// ============================================================================

/// 处理单个 HTTP 连接
async fn handle_request(
    mut stream: tokio::net::TcpStream,
    _app: AppHandle,
) {
    let mut buf = [0u8; 8192];
    let n = match {
        use tokio::io::AsyncReadExt;
        stream.read(&mut buf).await
    } {
        Ok(n) => n,
        Err(_) => return,
    };
    if n == 0 {
        return;
    }

    let req_str = String::from_utf8_lossy(&buf[..n]);
    let request_line = req_str.lines().next().unwrap_or("");

    // 只处理 GET 请求
    if !request_line.starts_with("GET ") {
        write_response(&mut stream, 405, "text/plain", "Method Not Allowed\n").await;
        return;
    }

    let parts: Vec<&str> = request_line.split_whitespace().collect();
    if parts.len() < 2 {
        write_response(&mut stream, 400, "text/plain", "Bad Request\n").await;
        return;
    }
    let path = parts[1];

    let response = match parse_route(path) {
        RouteResult::Video(task_id, filename) => serve_file(task_id, filename).await,
        RouteResult::Thumbnail(task_id, filename) => serve_file(task_id, filename).await,
        RouteResult::NotFound => {
            write_response(&mut stream, 404, "text/plain", "404 Not Found\n").await;
            return;
        }
    };

    match response {
        Ok((mime, file_data)) => {
            write_response_bytes(&mut stream, 200, &mime, &file_data).await;
        }
        Err(e) => {
            log::warn!("[static_server] 读取文件失败 {}: {e}", path);
            write_response(&mut stream, 404, "text/plain", "Not Found\n").await;
        }
    }
}

/// 路由解析结果
enum RouteResult {
    Video(String, String),
    Thumbnail(String, String),
    NotFound,
}

/// 解析 HTTP 路径
fn parse_route(path: &str) -> RouteResult {
    let trimmed = path.trim_start_matches('/');
    let parts: Vec<&str> = trimmed.splitn(3, '/').collect();

    if parts.len() < 3 {
        return RouteResult::NotFound;
    }

    match parts[0] {
        "videos" => RouteResult::Video(parts[1].to_string(), parts[2].to_string()),
        "thumbnails" => RouteResult::Thumbnail(parts[1].to_string(), parts[2].to_string()),
        _ => RouteResult::NotFound,
    }
}

/// 从 uploads/{task_id}/{filename} 读取文件
async fn serve_file(task_id: String, filename: String) -> Result<(String, Vec<u8>), String> {
    let base_dir = crate::paths::uploads_dir().map_err(|e| e.to_string())?;
    let file_path = base_dir.join(&task_id).join(&filename);

    // 安全检查：防止路径穿越攻击
    if !is_safe_path(&file_path, &base_dir) {
        return Err("路径穿越攻击被阻止".to_string());
    }

    let data = tokio::fs::read(&file_path)
        .await
        .map_err(|e| format!("读取文件失败 {}: {e}", file_path.display()))?;

    let mime = detect_mime_type(&file_path);
    Ok((mime, data))
}

/// 安全检查：确保目标路径在 base_dir 内部
fn is_safe_path(target: &Path, base: &Path) -> bool {
    let canonical_target = target.canonicalize().unwrap_or_else(|_| PathBuf::from(target));
    let canonical_base = base.canonicalize().unwrap_or_else(|_| PathBuf::from(base));
    canonical_target.starts_with(canonical_base)
}

/// 根据文件扩展名检测 MIME 类型
fn detect_mime_type(path: &Path) -> String {
    let ext = path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();

    match ext.as_str() {
        "mp4" | "m4v" => "video/mp4".to_string(),
        "webm" => "video/webm".to_string(),
        "ogg" | "ogv" => "video/ogg".to_string(),
        "avi" => "video/x-msvideo".to_string(),
        "mov" => "video/quicktime".to_string(),
        "mkv" => "video/x-matroska".to_string(),
        "gif" => "image/gif".to_string(),
        "jpg" | "jpeg" => "image/jpeg".to_string(),
        "png" => "image/png".to_string(),
        "webp" => "image/webp".to_string(),
        "bmp" => "image/bmp".to_string(),
        "svg" => "image/svg+xml".to_string(),
        "txt" => "text/plain".to_string(),
        "json" => "application/json".to_string(),
        _ => "application/octet-stream".to_string(),
    }
}

/// 写入 HTTP 文本响应（小 body）
async fn write_response(
    stream: &mut tokio::net::TcpStream,
    status: u16,
    content_type: &str,
    body: &str,
) {
    write_response_bytes(stream, status, content_type, body.as_bytes()).await;
}

/// 写入 HTTP 二进制响应（用于文件内容）
async fn write_response_bytes(
    stream: &mut tokio::net::TcpStream,
    status: u16,
    content_type: &str,
    body: &[u8],
) {
    use tokio::io::AsyncWriteExt;
    let status_text = match status {
        200 => "OK",
        400 => "Bad Request",
        404 => "Not Found",
        405 => "Method Not Allowed",
        500 => "Internal Server Error",
        _ => "Unknown",
    };

    let response = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        status, status_text, content_type, body.len()
    );

    let _ = stream.write_all(response.as_bytes()).await;
    let _ = stream.write_all(body).await;
    let _ = stream.flush().await;
}