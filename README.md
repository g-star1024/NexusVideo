# NexusVideo

> **AI 视频生成桌面客户端** — 一句话出片，小白友好

[![CI](https://github.com/g-star1024/NexusVideo/actions/workflows/ci.yml/badge.svg)](https://github.com/g-star1024/NexusVideo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

NexusVideo 是一款面向"完全不懂 AI 的小白用户"的 AI 视频生成桌面软件。核心逻辑：**不招全职算法博士、不自研模型，所有资源集中在"体验封装"上**——把 ComfyUI 这种晦涩的工作流，包装成"打开软件 → 输入一句话 → 看到视频，全程不超过 3 步"的丝滑体验。

## 技术架构

```
┌─────────────────────────────────────────────────────────┐
│  Tauri (Rust + Vue3) 桌面端                            │
│  ┌─────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │  IPC     │  │ 进程管理  │  │  毛玻璃深色主题 UI    │  │
│  │  桥接     │  │ ComfyUI  │  │  WebSocket 实时进度    │  │
│  │          │  │ FastAPI  │  │  视频预览播放器        │  │
│  └────┬────┘  └────┬─────┘  └──────────┬───────────┘  │
├───────┼─────────────┼───────────────────┼──────────────┤
│       │  FastAPI    │    ComfyUI        │  本地 GPU    │
│       │  中转服务    │    推理引擎        │  或 云端 GPU  │
└───────┴─────────────┴───────────────────┴──────────────┘
```

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| **桌面端** | Tauri (Rust + Vue3) | 轻量级桌面框架，原生 IPC |
| **前端** | Vue3 + Pinia + TypeScript | 毛玻璃深色主题，毛玻璃动效 |
| **后端** | Python FastAPI | 本地中转服务，WebSocket 实时进度 |
| **推理** | ComfyUI 便携版 | 封装隐藏，前端绝不暴露节点 |
| **模型** | Wan2.1 14B GGUF Q4 | 文生视频主力，AnimateDiff 保底 |
| **云端** | 阿里云 GPU 集群 | A100 实例，排队负载均衡 |

## 项目结构

```
NexusVideo/
├── backend/              # Python FastAPI 本地中转服务
│   ├── config.py         # 全局配置
│   ├── local_server.py   # FastAPI 应用入口（7 个路由）
│   ├── core/             # 核心业务逻辑
│   │   ├── auth.py       # JWT 认证（自实现 HS256）
│   │   ├── comfyui_client.py  # ComfyUI API 客户端
│   │   ├── comfyui_ws.py      # ComfyUI WebSocket 进度监听
│   │   ├── inference_router.py # 本地/云端智能路由
│   │   ├── progress_translator.py # 进度文案翻译（四阶段 16 条中文文案）
│   │   ├── task_manager.py  # 任务生命周期管理
│   │   ├── user_db.py   # SQLite 用户数据库（额度管理）
│   │   └── workflow_translator.py # 前端参数 → ComfyUI JSON
│   ├── routers/          # API 路由（auth/generate/task/upload/cloud/system）
│   └── requirements.txt  # Python 依赖
│
├── client/               # Tauri + Vue3 桌面客户端
│   ├── src/              # Vue3 前端代码
│   │   ├── api/          # IPC 调用封装（generate/start_backend/query_task 等）
│   │   ├── components/   # 5 个核心组件（Sidebar/StatusBar/TitleBar/CloudToggle/Queue）
│   │   ├── views/        # 6 个页面（Login/Init/Text2Video/Img2Video/StyleTransfer）
│   │   ├── composables/  # useProgress（WebSocket 直连 + 指数退避重连）
│   │   ├── stores/       # Pinia 状态管理（auth + generate）
│   │   └── assets/css/   # 设计系统（Tokens + 组件样式 + 动画）
│   └── src-tauri/        # Rust 桌面端原生层
│       ├── lib.rs        # 15 个 IPC 命令注册
│       ├── commands.rs   # IPC 命令实现
│       ├── process_manager.rs # ComfyUI/FastAPI 子进程管理（状态机）
│       ├── init_flow.rs  # 首次启动初始化（4 阶段）
│       ├── file_manager.rs    # 视频文件管理（LRU 缓存/自动清理）
│       ├── auto_update.rs  # 自动更新
│       ├── crash_handler.rs  # 崩溃恢复
│       └── static_server.rs  # 静态文件服务（视频预览）
│
├── workflows/            # ComfyUI 工作流模板（JSON）
│   ├── txt2video.json    # 文生视频（12 节点）
│   ├── img2video.json    # 图生视频（13 节点）
│   └── video2video.json  # 风格化（17 节点）
│
├── devops/               # 云端基础设施
│   ├── terraform/        # 阿里云 IaC（VPC/NAS/OSS/RDS/Redis/SLB/ESS）
│   ├── docker/           # ComfyUI Worker + Gateway 镜像
│   ├── deploy/           # K8s manifests + Nginx 配置
│   ├── api/              # JWT 网关 + 优先级队列
│   └── production/       # Secrets 迁移 + 更新服务器
│
├── scripts/              # 构建与部署脚本
│   ├── build.sh / build.ps1      # 双平台打包
│   └── download_models.sh/ps1    # 模型下载（7 个模型，断点续传）
│
├── docs/                 # 文档
│   ├── code-signing-guide.md      # Windows EV + macOS Developer ID 签名
│   ├── production-build-checklist.md # 生产构建验证清单
│   ├── release-checklist.md       # 发布检查表
│   ├── final-qa-report.md         # QA 回归测试报告
│   └── Task4_模型选型与ComfyUI节点评估报告.md
│
├── integration_test.py   # 端到端集成测试（23 项）
└── .github/workflows/ci.yml # GitHub Actions CI（5 jobs）
```

## 快速开始

### 1. 环境要求

- **Python ≥ 3.11**
- **Node.js ≥ 18**
- **Rust ≥ 1.70**（Tauri 系统依赖）
- **ComfyUI**（可选，本地推理需要）

### 2. 安装依赖

```bash
# 后端
pip install -r backend/requirements.txt

# 前端
cd client && npm install
```

### 3. 运行后端

```bash
cd backend
python local_server.py
# 服务启动于 http://127.0.0.1:9881
```

### 4. 运行前端（开发模式）

```bash
cd client
npm run tauri dev
```

### 5. 运行集成测试

```bash
python integration_test.py
```

预期输出：**23/23 通过**

## 核心设计原则

| 原则 | 说明 |
|------|------|
| **小白优先** | 默认用户不懂 Python、显存、Seed，参数全隐藏 |
| **封装为王** | ComfyUI 永远是"幕后黑手"，前端绝不暴露节点 |
| **体验第一** | 进度用中文文案（"正在构思画面…"），而非百分比 |

## CI/CD

- **GitHub Actions**：5 个并行 Job — Python 语法验证 / Rust 编译 / TypeScript 检查 / 集成测试 / 工作流校验
- **自动触发**：`main` 分支 push 和 PR 自动运行
- **模型下载**：`scripts/download_models.sh`（Linux/macOS）和 `download_models.ps1`（Windows），支持 `--dry-run` 预览和断点续传

## 版本

当前版本：**v0.1.0**（MVP 首版交付）

## 团队

| 角色 | 负责人 | 技术栈 |
|------|--------|--------|
| 产品交付总监 | 许明澈 | 需求管理 / 体验流定义 |
| Tauri 客户端 | 封易安 | Rust / Tauri / IPC |
| 前端开发 | 顾如画 | Vue3 / TypeScript |
| 后端核心 | 程流深 | FastAPI / ComfyUI / 显存优化 |
| 视觉设计 | 苏璃光 | Figma / 毛玻璃主题 |
| 运维 | 唐磐石 | K8s / Terraform / GPU 调度 |
| 算法顾问 | 甄知远 | 模型选型 / 调参指导 |

## License

MIT