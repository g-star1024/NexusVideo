//! process_manager.rs — ComfyUI + FastAPI 子进程生命周期管理
//! ============================================================================
//! 职责（白皮书 4.1 节）：
//!   1. 以隐藏窗口方式启动 ComfyUI（python main.py --headless --port 8188 --windows-foreground）
//!   2. 启动 FastAPI 中转服务（python local_server.py，端口 9881）
//!   3. 端口级健康检测（轮询 /health 或 TCP 探活）+ WebSocket 就绪检测
//!   4. 优雅停止（SIGTERM/任务栏关闭 → 超时强杀）
//!   5. 崩溃检测 + 自动重启（带退避，最多 3 次重试，间隔 5 秒）
//!   6. 内存/CPU 使用率监控（可选，低频率轮询）
//!
//! 进程归属策略（已与 python-backend-core 对齐）：
//!   - Tauri(Rust) 拥有完整进程树，spawn 并管理 ComfyUI + FastAPI
//!   - FastAPI 通过环境变量 NEXUS_MANAGE_COMFYUI=false 不再自行拉起 ComfyUI
//!   - 这样 App 退出时 Tauri 可靠回收全部子进程，杜绝孤儿/僵尸
use crate::events::{BackendLog, ProcState};
use serde::Serialize;
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

/// 最大自动重启次数（超出后标记为 Crashed，不再重试）
const MAX_RESTART_ATTEMPTS: u32 = 3;
/// 重启间隔（秒）
const RESTART_DELAY_SECS: u64 = 5;
/// 内存/CPU 监控轮询间隔（秒）
const MONITOR_INTERVAL_SECS: u64 = 30;

/// 单个被托管子进程
pub struct ManagedProcess {
    pub name: String,            // "comfyui" | "fastapi"
    pub child: Option<Child>,    // 进程句柄
    pub state: ProcState,
    pub started_at: Option<Instant>,
    pub restart_count: u32,
    pub pid: Option<u32>,
    /// 当前内存使用量（MB），由监控任务填充
    pub memory_mb: Option<f64>,
    /// 当前 CPU 使用率（%），由监控任务填充
    pub cpu_percent: Option<f64>,
    /// WebSocket 是否就绪（仅 ComfyUI 适用）
    pub ws_ready: bool,
}

impl ManagedProcess {
    fn new(name: &str) -> Self {
        Self {
            name: name.into(),
            child: None,
            state: ProcState::Stopped,
            started_at: None,
            restart_count: 0,
            pid: None,
            memory_mb: None,
            cpu_percent: None,
            ws_ready: false,
        }
    }
}

/// 进程管理器：持有 ComfyUI + FastAPI 两个子进程
pub struct ProcessManager {
    comfyui: Arc<Mutex<ManagedProcess>>,
    fastapi: Arc<Mutex<ManagedProcess>>,
    /// 监控任务句柄（用于优雅停止时取消）
    monitor_handle: Arc<Mutex<Option<tokio::task::JoinHandle<()>>>>,
}

impl ProcessManager {
    pub fn new() -> Self {
        Self {
            comfyui: Arc::new(Mutex::new(ManagedProcess::new("comfyui"))),
            fastapi: Arc::new(Mutex::new(ManagedProcess::new("fastapi"))),
            monitor_handle: Arc::new(Mutex::new(None)),
        }
    }

    // ========================================================================
    // 启动
    // ========================================================================

    /// 启动 ComfyUI 子进程（带最多 3 次自动重启）
    /// 命令：python main.py --headless --port 8188 --windows-foreground
    /// cwd ：resources/comfyui/
    pub async fn start_comfyui(&self, app: &AppHandle) -> Result<(), String> {
        let python = crate::paths::python_executable().map_err(|e| e.to_string())?;
        let main_py = crate::paths::comfyui_entry().map_err(|e| e.to_string())?;
        let cwd = crate::paths::comfyui_dir().map_err(|e| e.to_string())?;

        self.spawn_with_retry(
            &self.comfyui,
            app,
            SpawnSpec {
                program: python.to_string_lossy().to_string(),
                args: vec![
                    main_py.to_string_lossy().to_string(),
                    "--headless".into(),
                    "--port".into(),
                    "8188".into(),
                    "--windows-foreground".into(),
                ],
                cwd: Some(cwd),
                envs: comfyui_env(),
            },
        )
        .await
    }

    /// 启动 FastAPI 子进程
    /// 命令：python local_server.py（内部 uvicorn 监听 9881）
    /// 传入 NEXUS_MANAGE_COMFYUI=false 让 FastAPI 不重复拉起 ComfyUI
    pub async fn start_fastapi(&self, app: &AppHandle) -> Result<(), String> {
        let python = crate::paths::python_executable().map_err(|e| e.to_string())?;
        let entry = crate::paths::fastapi_entry().map_err(|e| e.to_string())?;
        let cwd = entry.parent().map(PathBuf::from);

        let mut envs = HashMap::new();
        // 关键：告诉 FastAPI 不要自己拉起 ComfyUI，Tauri 已接管
        envs.insert("NEXUS_MANAGE_COMFYUI".into(), "false".into());
        envs.insert("NEXUS_HOST".into(), "127.0.0.1".into());
        envs.insert("NEXUS_PORT".into(), "9881".into());
        envs.insert("NEXUS_COMFYUI_PORT".into(), "8188".into());
        envs.insert("NEXUS_COMFYUI_BASE_URL".into(), "http://127.0.0.1:8188".into());
        envs.insert(
            "NEXUS_WORKFLOWS_DIR".into(),
            crate::paths::workflows_dir()
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default(),
        );

        self.spawn_with_retry(
            &self.fastapi,
            app,
            SpawnSpec {
                program: python.to_string_lossy().to_string(),
                args: vec![entry.to_string_lossy().to_string()],
                cwd,
                envs,
            },
        )
        .await
    }

    /// spawn 并带最多 MAX_RESTART_ATTEMPTS 次自动重启
    async fn spawn_with_retry(
        &self,
        proc_arc: &Arc<Mutex<ManagedProcess>>,
        app: &AppHandle,
        spec: SpawnSpec,
    ) -> Result<(), String> {
        let max_attempts = MAX_RESTART_ATTEMPTS;
        for attempt in 1..=max_attempts {
            // 如果不是第一次尝试，先停掉残留进程
            if attempt > 1 {
                {
                    let mut p = proc_arc.lock().await;
                    if let Some(child) = p.child.as_mut() {
                        let _ = child.start_kill();
                        let _ = tokio::time::timeout(
                            Duration::from_secs(5),
                            child.wait(),
                        )
                        .await;
                    }
                    p.child = None;
                    p.state = ProcState::Restarting;
                    p.pid = None;
                    p.ws_ready = false;
                    p.memory_mb = None;
                    p.cpu_percent = None;
                }
                emit_status(app);
                log::warn!("[{}] 第 {}/{attempt} 次尝试重启...", spec.program, attempt, max_attempts);
                tokio::time::sleep(Duration::from_secs(RESTART_DELAY_SECS)).await;
            }

            let result = self.spawn(proc_arc, app, spec.clone()).await;
            if result.is_ok() {
                // 检查是否真正启动成功（端口就绪）
                let (port, kind) = if proc_arc == &self.comfyui {
                    (8188u16, ProcKind::ComfyUI)
                } else {
                    (9881u16, ProcKind::FastAPI)
                };
                let healthy = self
                    .wait_until_healthy(kind, app, Duration::from_secs(60))
                    .await;
                if healthy.is_ok() {
                    log::info!("[{}] 启动成功（第 {attempt} 次尝试）", proc_arc.lock().await.name, attempt);
                    return Ok(());
                }
                log::warn!("[{}] 第 {attempt} 次启动未就绪: {}", proc_arc.lock().await.name, healthy.unwrap_err());
            } else {
                log::warn!("[{}] 第 {attempt} 次 spawn 失败: {}", proc_arc.lock().await.name, result.unwrap_err());
            }
        }
        Err(format!(
            "{} 启动失败（已重试 {max_attempts} 次）",
            proc_arc.lock().await.name
        ))
    }

    /// 统一 spawn 实现
    async fn spawn(
        &self,
        proc_arc: &Arc<Mutex<ManagedProcess>>,
        app: &AppHandle,
        spec: SpawnSpec,
    ) -> Result<(), String> {
        {
            let mut p = proc_arc.lock().await;
            if p.child.is_some() && matches!(p.state, ProcState::Running | ProcState::Starting) {
                return Err(format!("{} 已在运行", p.name));
            }
            p.state = ProcState::Starting;
        }
        emit_status(app);

        let mut cmd = Command::new(&spec.program);
        cmd.args(&spec.args);
        if let Some(cwd) = &spec.cwd {
            cmd.current_dir(cwd);
        }
        for (k, v) in &spec.envs {
            cmd.env(k, v);
        }
        // 捕获 stdout/stderr 用于日志推送
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());
        // 关键：Windows 下隐藏控制台窗口（CREATE_NO_WINDOW = 0x08000000）
        // 避免 ComfyUI/FastAPI 弹出黑色 CMD 窗口惊吓小白用户
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        // 子进程随父进程退出（Unix：进程组；Windows：JobObject 兜底见 stop）
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            unsafe {
                cmd.pre_exec(|| {
                    // 新建进程组，便于整组信号
                    libc::setsid();
                    Ok(())
                });
            }
        }

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                let mut p = proc_arc.lock().await;
                p.state = ProcState::Crashed;
                emit_status(app);
                return Err(format!(
                    "{} 启动失败: {}（程序={}）",
                    p.name, e, spec.program
                ));
            }
        };

        let pid = child.id();
        // 拿走 stdout/stderr 句柄，开两个异步任务逐行读 → emit 日志
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let name = {
            let mut p = proc_arc.lock().await;
            p.pid = pid;
            p.started_at = Some(Instant::now());
            p.child = Some(child);
            p.name.clone()
        };
        if let Some(out) = stdout {
            spawn_log_reader(app, &name, out, "info");
        }
        if let Some(err) = stderr {
            spawn_log_reader(app, &name, err, "warn");
        }

        log::info!("[{}] 进程已启动 pid={:?}", name, pid);
        Ok(())
    }

    // ========================================================================
    // 健康检测
    // ========================================================================

    /// 等待某进程端口可达（TCP 探活），超时返回 Err
    pub async fn wait_until_healthy(
        &self,
        which: ProcKind,
        app: &AppHandle,
        timeout: Duration,
    ) -> Result<(), String> {
        let (port, name) = match which {
            ProcKind::ComfyUI => (8188u16, "comfyui"),
            ProcKind::FastAPI => (9881u16, "fastapi"),
        };
        let deadline = Instant::now() + timeout;
        let mut retries = 0u32;
        loop {
            if Instant::now() > deadline {
                let mut p = self.get_inner(which).await;
                p.state = ProcState::Crashed;
                emit_status(app);
                return Err(format!(
                    "{} 启动超时（{}s 内端口 {} 未就绪，重试 {} 次）",
                    name,
                    timeout.as_secs(),
                    port,
                    retries
                ));
            }
            // FastAPI 用 HTTP /health；ComfyUI 用 TCP 探活（更轻量）
            let ok = match which {
                ProcKind::FastAPI => probe_http("127.0.0.1", port, "/health").await,
                ProcKind::ComfyUI => probe_tcp("127.0.0.1", port).await,
            };
            if ok {
                let mut p = self.get_inner(which).await;
                p.state = ProcState::Running;
                emit_status(app);
                return Ok(());
            }
            retries += 1;
            tokio::time::sleep(Duration::from_millis(800)).await;
        }
    }

    /// ComfyUI WebSocket 就绪检测
    /// 不仅检测 HTTP 端口，还尝试连接 WebSocket 确认服务真正可用
    /// 返回 true 表示 WebSocket 已就绪
    pub async fn check_comfyui_ws_ready(&self, app: &AppHandle) -> bool {
        let result = probe_ws("127.0.0.1", 8188, "/ws").await;
        if result {
            let mut p = self.comfyui.lock().await;
            p.ws_ready = true;
            log::info!("[comfyui] WebSocket 已就绪");
        } else {
            let mut p = self.comfyui.lock().await;
            p.ws_ready = false;
            log::debug!("[comfyui] WebSocket 尚未就绪");
        }
        emit_status(app);
        result
    }

    /// 启动后台监控任务（低频率轮询内存/CPU 使用率 + WebSocket 状态）
    /// 在 start_backend 成功后调用一次即可
    pub fn start_monitoring(&self, app: AppHandle) {
        let comfyui_arc = self.comfyui.clone();
        let fastapi_arc = self.fastapi.clone();
        let monitor_arc = self.monitor_handle.clone();
        let app_clone = app.clone();

        let handle = tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(MONITOR_INTERVAL_SECS)).await;

                // 检查两个进程是否仍在运行
                // tokio::process::Child 没有 started_output()，用 stdout 是否存在判断
                let (cui_alive, fapi_alive) = {
                    let cui = comfyui_arc.lock().await;
                    let fapi = fastapi_arc.lock().await;
                    (
                        cui.child.as_ref().map(|c| c.stdout.is_some()).unwrap_or(false),
                        fapi.child.as_ref().map(|c| c.stdout.is_some()).unwrap_or(false),
                    )
                };

                if !cui_alive {
                    let mut cui = comfyui_arc.lock().await;
                    if matches!(cui.state, ProcState::Running | ProcState::Starting) {
                        cui.state = ProcState::Crashed;
                        cui.ws_ready = false;
                        cui.memory_mb = None;
                        cui.cpu_percent = None;
                        emit_status(&app_clone);
                        log::warn!("[monitor] ComfyUI 进程已退出");
                    }
                }
                if !fapi_alive {
                    let mut fapi = fastapi_arc.lock().await;
                    if matches!(fapi.state, ProcState::Running | ProcState::Starting) {
                        fapi.state = ProcState::Crashed;
                        fapi.memory_mb = None;
                        fapi.cpu_percent = None;
                        emit_status(&app_clone);
                        log::warn!("[monitor] FastAPI 进程已退出");
                    }
                }

                // 获取内存/CPU 使用率（通过 sysinfo）
                let _ = fetch_process_stats(&comfyui_arc, &fastapi_arc).await;

                // 定期检查 ComfyUI WebSocket 状态
                let _ = probe_ws("127.0.0.1", 8188, "/ws").await;
            }
        });

        let mut mh = monitor_arc.lock().await;
        *mh = Some(handle);
    }

    async fn get_inner(&self, which: ProcKind) -> tokio::sync::MutexGuard<'_, ManagedProcess> {
        match which {
            ProcKind::ComfyUI => self.comfyui.lock().await,
            ProcKind::FastAPI => self.fastapi.lock().await,
        }
    }

    async fn get(&self, which: ProcKind) -> ManagedProcessSnapshot {
        let p = self.get_inner(which).await;
        ManagedProcessSnapshot {
            state: p.state.clone(),
            pid: p.pid,
            restart_count: p.restart_count,
            memory_mb: p.memory_mb,
            cpu_percent: p.cpu_percent,
            ws_ready: p.ws_ready,
        }
    }

    /// 探测两个进程当前状态（供 command 查询）
    pub async fn snapshot(&self) -> (ManagedProcessSnapshot, ManagedProcessSnapshot) {
        (self.get(ProcKind::ComfyUI).await, self.get(ProcKind::FastAPI).await)
    }

    // ========================================================================
    // 优雅停止
    // ========================================================================

    /// 优雅停止全部子进程（App 退出时调用）
    /// 顺序：先停 FastAPI（停止接收新请求）→ 再停 ComfyUI
    pub async fn stop_all(&self, app: &AppHandle) {
        log::info!("开始优雅停止全部子进程...");
        // 先取消监控任务
        {
            let mut mh = self.monitor_handle.lock().await;
            if let Some(handle) = mh.take() {
                handle.abort();
            }
        }
        self.stop_one(ProcKind::FastAPI, app).await;
        self.stop_one(ProcKind::ComfyUI, app).await;
        log::info!("全部子进程已停止");
    }

    async fn stop_one(&self, which: ProcKind, app: &AppHandle) {
        let proc_arc = match which {
            ProcKind::ComfyUI => &self.comfyui,
            ProcKind::FastAPI => &self.fastapi,
        };
        let mut p = proc_arc.lock().await;
        let Some(child) = p.child.as_mut() else {
            return;
        };
        p.state = ProcState::Stopping;
        emit_status(app);

        // 1) 优先：ComfyUI 走 HTTP /interrupt + 自然退出；FastAPI 走 kill(优雅)
        //    这里统一先尝试 kill(Parent) 的 start_kill（Unix SIGTERM / Win TerminateProcess）
        let pid = child.id();
        let _ = child.start_kill();

        // 2) 等待最多 8s 优雅退出
        let exited = tokio::time::timeout(Duration::from_secs(8), child.wait())
            .await
            .map(|r| r.is_ok())
            .unwrap_or(false);

        if !exited {
            // 3) 超时强杀
            log::warn!("[{}] 8s 未退出，强杀 pid={:?}", p.name, pid);
            let _ = child.kill().await;
        }
        p.child = None;
        p.state = ProcState::Stopped;
        p.pid = None;
        p.ws_ready = false;
        p.memory_mb = None;
        p.cpu_percent = None;
        emit_status(app);
    }
}

pub enum ProcKind {
    ComfyUI,
    FastAPI,
}

#[derive(Clone, Serialize)]
pub struct ManagedProcessSnapshot {
    pub state: ProcState,
    pub pid: Option<u32>,
    pub restart_count: u32,
    pub memory_mb: Option<f64>,
    pub cpu_percent: Option<f64>,
    pub ws_ready: bool,
}

#[derive(Clone)]
struct SpawnSpec {
    program: String,
    args: Vec<String>,
    cwd: Option<PathBuf>,
    envs: HashMap<String, String>,
}

// ComfyUI 环境变量：CUDA 显存策略、禁浏览器自动打开等
fn comfyui_env() -> HashMap<String, String> {
    let mut m = HashMap::new();
    m.insert("HF_HUB_OFFLINE".into(), "1".into()); // 离线，不联网拉模型
    m.insert("TRANSFORMERS_OFFLINE".into(), "1".into());
    #[cfg(target_os = "windows")]
    m.insert("CUDA_MODULE_LOADING".into(), "LAZY".into()); // 降低冷启动显存峰值
    m
}

// 逐行读 stdout/stderr，emit backend://log 事件给前端
fn spawn_log_reader(app: &AppHandle, source: &str, pipe: tokio::process::ChildStdout, level: &str) {
    let source = source.to_string();
    let level = level.to_string();
    let app = app.clone();
    tokio::spawn(async move {
        // 注：tokio::process::ChildStdout 与 std 不同，这里用 BufReader
        let mut reader = BufReader::new(pipe);
        let mut buf = String::new();
        loop {
            buf.clear();
            match reader.read_line(&mut buf).await {
                Ok(0) => break, // EOF
                Ok(_) => {
                    let line = buf.trim_end().to_string();
                    if !line.is_empty() {
                        let _ = app.emit(
                            crate::events::event_name::BACKEND_LOG,
                            BackendLog {
                                source: source.clone(),
                                level: level.clone(),
                                line,
                                ts: chrono_now(),
                            },
                        );
                    }
                }
                Err(_) => break,
            }
        }
    });
}

// 简易 ISO 时间（不引 chrono，减小体积）
fn chrono_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("t{secs}")
}

// 向前端广播当前后端状态
fn emit_status(app: &AppHandle) {
    // 状态在调用方更新后由 commands 层统一广播，这里只做轻量触发
    // （详细快照见 commands::broadcast_status）
    let _ = app.emit(
        crate::events::event_name::BACKEND_STATUS,
        serde_json::json!({ "ts": chrono_now() }),
    );
}

// TCP 端口探活（ComfyUI 用）
async fn probe_tcp(host: &str, port: u16) -> bool {
    use tokio::net::TcpStream;
    TcpStream::connect(format!("{host}:{port}"))
        .await
        .is_ok()
}

// HTTP 探活（FastAPI 用，请求 /health，2xx 视为就绪）
async fn probe_http(host: &str, port: u16, path: &str) -> bool {
    let url = format!("http://{host}:{port}{path}");
    let client = match reqwest::Client::builder().timeout(Duration::from_secs(3)).build() {
        Ok(c) => c,
        Err(_) => return false,
    };
    client
        .get(&url)
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

// WebSocket 探活（ComfyUI 用，确认 WebSocket 连接可建立）
async fn probe_ws(host: &str, port: u16, path: &str) -> bool {
    let url = format!("ws://{host}:{port}{path}");
    // 使用 tokio-tungstenite 尝试建立 WebSocket 连接
    // connect_async 返回 Result<(WebSocketStream, Response), Error>
    if let Ok((ws, _)) = tokio_tungstenite::tokio__connect_async(&url).await {
        // 成功建立连接后立即关闭（不发送任何消息）
        drop(ws);
        true
    } else {
        false
    }
}

/// 获取进程的内存/CPU 使用率（使用 sysinfo）
/// 注意：sysinfo >= 0.30 已移除所有 trait（ProcessExt/SystemExt/SystemExt），
/// 方法直接在 Process/System 类型上调用，无需 import trait。
async fn fetch_process_stats(
    comfyui: &Arc<Mutex<ManagedProcess>>,
    fastapi: &Arc<Mutex<ManagedProcess>>,
) {
    use sysinfo::System;

    let mut sys = System::new_all();
    sys.refresh_processes();

    // 查找 ComfyUI 进程
    if let Some(pid) = {
        let p = comfyui.lock().await;
        p.pid
    } {
        if let Some(process) = sys.process(sysinfo::Pid::from_u32(pid)) {
            let memory_mb = process.memory() as f64 / (1024.0 * 1024.0);
            let cpu = process.cpu_usage();
            let mut p = comfyui.lock().await;
            p.memory_mb = Some(memory_mb.round() as f64);
            p.cpu_percent = Some(cpu.round() as f64);
            log::debug!("[monitor] comfyui mem={:.1}MB cpu={:.1}%", memory_mb, cpu);
        }
    }

    // 查找 FastAPI 进程
    if let Some(pid) = {
        let p = fastapi.lock().await;
        p.pid
    } {
        if let Some(process) = sys.process(sysinfo::Pid::from_u32(pid)) {
            let memory_mb = process.memory() as f64 / (1024.0 * 1024.0);
            let cpu = process.cpu_usage();
            let mut p = fastapi.lock().await;
            p.memory_mb = Some(memory_mb.round() as f64);
            p.cpu_percent = Some(cpu.round() as f64);
            log::debug!("[monitor] fastapi mem={:.1}MB cpu={:.1}%", memory_mb, cpu);
        }
    }
}
