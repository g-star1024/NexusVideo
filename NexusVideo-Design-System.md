# NexusVideo 毛玻璃深色设计系统规范 v1.0

> 设计负责人：苏璃光 · 版本 1.0 · 2026-08-18
> 设计语言：毛玻璃质感 (Glassmorphism) + 黑暗极简 (Dark Minimalism)
> 面向产品：NexusVideo — 全球最简单的 AI 视频生成桌面端

---

## 〇、设计原则与情绪基调

### 核心原则

| 原则 | 说明 | 反例（禁止） |
|------|------|-------------|
| 零门槛 | 界面不出现任何专业术语（潜空间、VAE、CFG、Denoising 等） | ❌ "Denoising Strength: 0.65" |
| 沉浸感 | 全屏深色，毛玻璃层次，背景视频营造创作氛围 | ❌ 白色卡片堆砌，工具栏密集 |
| 丝滑 | 所有交互过渡 150-500ms，缓动自然，不突兀 | ❌ 瞬间切换、卡顿跳变 |
| 克制 | 信息密度低，留白充足，每屏只做一件事 | ❌ 参数面板全展开、多窗口 |
| 友好 | 语义化文案，进度用人话讲，出错给人话解法 | ❌ "Error: CUDA out of memory" |

### 情绪基调

- **专业但不冷漠**：深色 + 渐变光晕传递科技专业感，毛玻璃柔化边缘增加亲和力
- **高级但不距离**：大留白 + 精致微交互传递品质感，圆润组件降低距离感
- **安静但不无聊**：深色背景安静专注，背景视频/渐变光带来生命力

---

## 一、色彩体系

### 1.1 背景色阶（深邃黑系）

应用整体以 `#0A0A0A` 为最深层底色，通过透明度白色叠加和毛玻璃模糊建立层次。

| Token | 用途 | HEX | RGB | 透明度 |
|-------|------|-----|-----|--------|
| `--bg-base` | 应用最深层底色 | `#0A0A0A` | `10, 10, 10` | 100% |
| `--bg-surface` | 基础表面（侧栏底色） | `#121214` | `18, 18, 20` | 100% |
| `--bg-elevated` | 浮起表面（卡片底色） | `#1A1A1E` | `26, 26, 30` | 100% |
| `--glass-1` | 毛玻璃 L1（卡片） | `rgba(255,255,255,0.04)` | `255,255,255` | 4% |
| `--glass-2` | 毛玻璃 L2（面板） | `rgba(255,255,255,0.07)` | `255,255,255` | 7% |
| `--glass-3` | 毛玻璃 L3（弹窗） | `rgba(26,26,30,0.80)` | `26,26,30` | 80% |
| `--hover-overlay` | 悬停叠加层 | `rgba(255,255,255,0.10)` | `255,255,255` | 10% |
| `--active-overlay` | 激活叠加层 | `rgba(255,255,255,0.14)` | `255,255,255` | 14% |

### 1.2 品牌主色（蓝紫渐变系）

| Token | 用途 | HEX | RGB |
|-------|------|-----|-----|
| `--brand-blue` | 渐变起始色（蓝） | `#5B6CFF` | `91, 108, 255` |
| `--brand-purple` | 渐变终止色（紫） | `#B14CFF` | `177, 76, 255` |
| `--brand-blue-hover` | 蓝色悬停态 | `#4A5BFF` | `74, 91, 255` |
| `--brand-purple-hover` | 紫色悬停态 | `#A03BFF` | `160, 59, 255` |
| `--brand-blue-pressed` | 蓝色按下态 | `#3A4BFF` | `58, 75, 255` |
| `--brand-purple-pressed` | 紫色按下态 | `#8F2AFF` | `143, 42, 255` |

### 1.3 辅助点缀色

| Token | 用途 | HEX | RGB |
|-------|------|-----|-----|
| `--accent-cyan` | 点缀/高亮信息 | `#3DD6E8` | `61, 214, 232` |
| `--accent-pink` | 点缀/创意标签 | `#FF6B9D` | `255, 107, 157` |

### 1.4 语义色

| Token | 语义 | 前景色 HEX / RGB | 背景色（12%透明） |
|-------|------|-------------------|-------------------|
| `--success` | 成功/完成 | `#3DD68C` / `61, 214, 140` | `rgba(61,214,140,0.12)` |
| `--warning` | 警告/注意 | `#FFB938` / `255, 185, 56` | `rgba(255,185,56,0.12)` |
| `--error` | 错误/失败 | `#FF5B5B` / `255, 91, 91` | `rgba(255,91,91,0.12)` |
| `--info` | 信息/提示 | `#5B6CFF` / `91, 108, 255` | `rgba(91,108,255,0.12)` |

### 1.5 文字/灰阶色

| Token | 用途 | HEX | RGB | 对比度 (on #0A0A0A) |
|-------|------|-----|-----|---------------------|
| `--text-primary` | 主文字/标题 | `#F5F5F7` | `245, 245, 247` | 18.1 : 1 (AAA) |
| `--text-secondary` | 次要文字/正文 | `#B0B0B8` | `176, 176, 184` | 9.2 : 1 (AAA) |
| `--text-tertiary` | 辅助/提示文字 | `#80808C` | `128, 128, 140` | 5.0 : 1 (AA) |
| `--text-disabled` | 禁用文字 | `#4A4A52` | `74, 74, 82` | 2.1 : 1 (仅装饰) |
| `--text-on-brand` | 渐变按钮上的文字 | `#FFFFFF` | `255, 255, 255` | 4.6 : 1 (AA) |
| `--text-on-brand-disabled` | 渐变按钮禁用文字 | `rgba(255,255,255,0.40)` | `255, 255, 255` | — |

### 1.6 渐变规范

| 名称 | CSS 值 | 用途 |
|------|--------|------|
| 品牌主渐变 | `linear-gradient(135deg, #5B6CFF 0%, #B14CFF 100%)` | 主按钮、进度条、选中态边框 |
| 品牌悬停渐变 | `linear-gradient(135deg, #4A5BFF 0%, #A03BFF 100%)` | 主按钮 hover |
| 背景光晕 | `radial-gradient(circle at 50% 25%, rgba(91,108,255,0.15) 0%, transparent 55%)` | 首屏背景光效 |
| 紫色光晕 | `radial-gradient(circle at 80% 70%, rgba(177,76,255,0.10) 0%, transparent 50%)` | 角落辅助光效 |
| 进度条流动 | `linear-gradient(90deg, #5B6CFF, #B14CFF, #5B6CFF)` + `background-size: 200%` | 加载流动条 |

> **渐变角度统一 135deg**（左上→右下），全产品保持一致。

### 1.7 毛玻璃效果参数

| 层级 | backdrop-filter | background | border | 用途 |
|------|----------------|------------|--------|------|
| Glass L1 | `blur(20px) saturate(150%)` | `rgba(255,255,255,0.04)` | `1px solid rgba(255,255,255,0.08)` | 卡片、缩略图卡片 |
| Glass L2 | `blur(30px) saturate(160%)` | `rgba(255,255,255,0.07)` | `1px solid rgba(255,255,255,0.10)` | 侧栏、参数面板、结果卡 |
| Glass L3 | `blur(40px) saturate(180%)` | `rgba(26,26,30,0.80)` | `1px solid rgba(255,255,255,0.12)` | 弹窗、抽屉、菜单 |

> **前端提示**：`backdrop-filter` 在 Tauri WebView (Windows WebView2 / macOS WKWebView) 中支持良好。需为 `-webkit-backdrop-filter` 添加前缀以兼容。毛玻璃区域下方必须有可模糊的内容（背景视频/光晕/其他元素），纯黑背景上毛玻璃无效果，需配合 `radial-gradient` 光晕打底。

### 1.8 边框与分割线

| Token | 值 | 用途 |
|-------|-----|------|
| `--border-subtle` | `1px solid rgba(255,255,255,0.06)` | 区块分割、弱分割线 |
| `--border-default` | `1px solid rgba(255,255,255,0.10)` | 卡片边框、输入框边框 |
| `--border-strong` | `1px solid rgba(255,255,255,0.16)` | 悬停态边框增强 |
| `--border-focus` | `1px solid #5B6CFF` | 输入框聚焦态 |
| `--divider` | `1px solid rgba(255,255,255,0.06)` | 列表分割线 |

### 1.9 阴影规范

| Token | 值 | 用途 |
|-------|-----|------|
| `--shadow-sm` | `0 2px 8px rgba(0,0,0,0.3)` | 卡片基础阴影 |
| `--shadow-md` | `0 8px 24px rgba(0,0,0,0.4)` | 浮起卡片/悬停态 |
| `--shadow-lg` | `0 16px 48px rgba(0,0,0,0.5)` | 弹窗/抽屉 |
| `--shadow-glow` | `0 0 0 4px rgba(91,108,255,0.15)` | 输入框聚焦光晕 |
| `--shadow-brand` | `0 8px 24px rgba(91,108,255,0.30)` | 主按钮悬停发光 |

### 1.10 圆角规范

| Token | 值 | 用途 |
|-------|-----|------|
| `--radius-sm` | `8px` | 小按钮、标签、输入框（普通） |
| `--radius-md` | `12px` | 按钮、卡片、缩略图 |
| `--radius-lg` | `16px` | 大卡片、面板 |
| `--radius-xl` | `20px` | 超大输入框、弹窗、结果卡 |
| `--radius-full` | `9999px` | 圆形元素（头像、手柄、气泡） |

---

## 二、字体系统

### 2.1 字体选型

| 用途 | 首选字体 | 备选字体 | 说明 |
|------|----------|----------|------|
| 中文 | **HarmonyOS Sans SC** | Noto Sans SC / PingFang SC | 开源免费，现代几何无衬线，笔画清晰，远距离辨识度高 |
| 英文/数字 | **Inter** | SF Pro Display / Segoe UI | 专为屏幕设计，字怀开阔，小字号下依然清晰 |
| 等宽（仅内部调试） | JetBrains Mono | Fira Code / Consolas | 仅在开发者调试面板使用，不对用户暴露 |

```css
--font-sans: "Inter", "HarmonyOS Sans SC", "Noto Sans SC", "PingFang SC",
             -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
--font-mono: "JetBrains Mono", "Fira Code", Consolas, monospace;
```

> **字体打包提示**：Inter 和 HarmonyOS Sans SC 均为开源字体，可内嵌到 Tauri resources 中随安装包分发，避免用户机器缺字体导致渲染不一致。HarmonyOS Sans SC 完整字重约 5-8MB，建议只打包 Regular(400) + Medium(500) + Semibold(600) 三个字重。

### 2.2 字号层级

| Token | 用途 | 字号 | 字重 | 行高 | 字间距 |
|-------|------|------|------|------|--------|
| `--text-display-xl` | 品牌名/首屏标题 | 48px | 600 | 1.2 | -0.02em |
| `--text-display-lg` | 超大输入框文字 | 24px | 400 | 1.5 | 0 |
| `--text-h1` | 页面主标题 | 28px | 600 | 1.3 | -0.01em |
| `--text-h2` | 区块标题 | 22px | 600 | 1.35 | -0.01em |
| `--text-h3` | 副标题/卡片标题 | 18px | 500 | 1.4 | 0 |
| `--text-body-lg` | 主要正文/输入提示 | 16px | 400 | 1.6 | 0 |
| `--text-body` | 正文 | 14px | 400 | 1.6 | 0 |
| `--text-body-sm` | 辅助文字/标签 | 13px | 400 | 1.5 | 0 |
| `--text-caption` | 最小说明文字 | 12px | 400 | 1.5 | 0.01em |
| `--text-btn` | 按钮文字 | 14px | 500 | 1.0 | 0.02em |
| `--text-btn-sm` | 小按钮文字 | 13px | 500 | 1.0 | 0.02em |

### 2.3 字重规范

| 字重 | 数值 | 用途 |
|------|------|------|
| Regular | 400 | 正文、描述、输入框文字、placeholder |
| Medium | 500 | 按钮、导航项、标签、强调正文 |
| Semibold | 600 | 标题、品牌名、进度文案 |

> **禁止使用 700 (Bold)**：在深色背景上过粗的字重会显得粗糙糊成一团，降低精致感。最大用到 600。

### 2.4 行高与字间距原则

- **大标题 (>22px)**：行高 1.2-1.35（紧凑有力），字间距 -0.01em ~ -0.02em（负间距更聚合）
- **正文 (14-18px)**：行高 1.6（舒适阅读宽度），字间距 0（默认）
- **小文字 (<13px)**：行高 1.5，字间距 0.01em（正间距提高小字辨识度）
- **按钮文字**：行高 1.0（单行垂直居中），字间距 0.02em（略宽松增加呼吸感）

### 2.5 远距离清晰标准

桌面端用户可能距离屏幕 60-80cm，核心操作文字需保证远距离可读：

| 场景 | 最小字号 | 理由 |
|------|----------|------|
| 超大输入框（核心操作） | 24px | 用户主要交互区域，必须醒目 |
| 进度文案（全屏关注） | 24px | 用户等待时远距离观看 |
| 按钮（可点击操作） | 14px | 最小可操作文字 |
| 辅助提示（非必须阅读） | 12px | 仅近距查看，不作为关键信息 |

---

## 三、布局规范

### 3.1 整体布局结构

```
┌─────────────────────────────────────────────────────────────┐
│  自定义标题栏 (40px)                                    - □ × │
├──────────┬──────────────────────────────┬───────────────────┤
│          │                              │                   │
│  左侧栏   │      中央超大输入区            │   右侧参数栏       │
│  240px   │      (flex-grow: 1, ≈70%)     │   320px (可折叠)   │
│  (可收起  │                              │                   │
│   至72px) │                              │                   │
│          │                              │                   │
│  历史     │   ┌────────────────────┐     │   高级参数         │
│  缩略图   │   │   超大输入框         │     │   (默认收起)       │
│  列表     │   │                    │     │                   │
│          │   └────────────────────┘     │                   │
│          │      [灵感词] [灵感词] ...     │                   │
│          │         [生成按钮]             │                   │
│          │                              │                   │
├──────────┴──────────────────────────────┴───────────────────┤
│  状态栏 (32px) — 模式切换 / 本地·云端状态 / 版本号            │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 三栏尺寸定义

| 区域 | 默认宽度 | 收起宽度 | 最小宽度 | 说明 |
|------|----------|----------|----------|------|
| 左侧历史栏 | 240px | 72px (图标模式) | 72px | 可收起为仅缩略图图标列 |
| 中央输入区 | flex-grow: 1 | — | 480px | 自适应填充剩余空间，1920px 屏下约占 70% |
| 右侧参数栏 | 320px | 0px (完全隐藏) | 320px | 默认收起，点击"高级参数"展开 |
| 标题栏 | 100% | — | — | 高度 40px |
| 状态栏 | 100% | — | — | 高度 32px |

### 3.3 栅格系统

| 参数 | 值 | 说明 |
|------|-----|------|
| 基础单位 | 8px | 所有间距为 8 的倍数 |
| 中央区栅格 | 12 列 | 中央内容区使用 12 列栅格 |
| 列间距 | 24px | 栅格列之间的间距 |
| 最大内容宽度 | 800px | 超大输入框 + 灵感词区域的最大宽度，居中 |
| 内容水平内边距 | 32px | 中央区域左右内边距 |

### 3.4 间距规范 (Spacing Scale)

基于 8px 基础单位的全局间距梯度：

| Token | 值 | 典型用途 |
|-------|-----|----------|
| `--space-xs` | 4px | 图标与文字间距、紧凑组件内间距 |
| `--space-sm` | 8px | 按钮内图标间距、列表项间距 |
| `--space-md` | 16px | 组件间距、卡片内边距、表单项间距 |
| `--space-lg` | 24px | 区块间距、面板内边距、安全区域 |
| `--space-xl` | 32px | 大区块间距、内容水平内边距 |
| `--space-2xl` | 48px | 页面级垂直间距、首屏元素间距 |
| `--space-3xl` | 64px | 首屏大间距、品牌区呼吸空间 |

### 3.5 响应式断点（窗口缩放适配）

桌面端窗口缩放适配策略，非移动端断点：

| 断点 | 窗口宽度 | 布局行为 |
|------|----------|----------|
| XL | ≥ 1920px | 三栏全展开，间距使用标准值，输入框 max-width 800px |
| LG | 1440px - 1919px | 三栏全展开，间距缩小一档 (space-xl→space-lg)，输入框 max-width 720px |
| MD | 1024px - 1439px | 右侧参数栏默认收起，左侧栏保持 240px，输入框 max-width 640px |
| SM | < 1024px | 左侧收起为 72px 图标模式，右侧隐藏，顶部提示"建议放大窗口获得更好体验" |

### 3.6 安全区域定义

| 参数 | 值 |
|------|-----|
| 窗口最小尺寸 | 1024px × 640px |
| 窗口默认尺寸 | 1440px × 900px |
| 窗口推荐尺寸 | 1920px × 1080px |
| 内容安全边距 | 四周 24px (MD 及以下) / 32px (LG 及以上) |
| 标题栏高度 | 40px (自定义无边框标题栏) |
| 状态栏高度 | 32px |

---

## 四、组件库规范

### 4.1 按钮 (Button)

#### 4.1.1 主按钮 (Primary Button)

```
尺寸: height 44px, padding 0 24px, min-width 96px
背景: linear-gradient(135deg, #5B6CFF 0%, #B14CFF 100%)
文字: #FFFFFF, 14px, weight 500, letter-spacing 0.02em
圆角: 12px
边框: 无

Hover:  渐变 → linear-gradient(135deg, #4A5BFF, #A03BFF)
        box-shadow: 0 8px 24px rgba(91,108,255,0.30)
        transform: translateY(-1px)
Pressed: transform: scale(0.97), box-shadow 减弱
Disabled: opacity 0.4, cursor not-allowed, 无 hover 效果
```

#### 4.1.2 次按钮 (Secondary Button)

```
尺寸: height 44px, padding 0 24px, min-width 96px
背景: rgba(255,255,255,0.06) + backdrop-filter: blur(20px)
文字: #F5F5F7, 14px, weight 500
圆角: 12px
边框: 1px solid rgba(255,255,255,0.10)

Hover:  background rgba(255,255,255,0.10), border rgba(255,255,255,0.16)
Pressed: background rgba(255,255,255,0.14)
Disabled: opacity 0.4
```

#### 4.1.3 图标按钮 (Icon Button)

```
尺寸: 44px × 44px (正方形)
背景: transparent (默认) / rgba(255,255,255,0.06) (带背景变体)
图标: 20px × 20px, 颜色 #B0B0B8
圆角: 12px

Hover:  background rgba(255,255,255,0.10), 图标颜色 #F5F5F7
Pressed: background rgba(255,255,255,0.14), transform scale(0.95)
```

#### 4.1.4 危险按钮 (Danger Button)

```
尺寸: height 44px, padding 0 24px
背景: rgba(255,91,91,0.12)
文字: #FF5B5B, 14px, weight 500
圆角: 12px
边框: 1px solid rgba(255,91,91,0.30)

Hover:  background rgba(255,91,91,0.18), border rgba(255,91,91,0.40)
Pressed: background rgba(255,91,91,0.24)
```

#### 4.1.5 小按钮 / 灵感词按钮 (Tag Button)

```
尺寸: height 32px, padding 0 14px
背景: rgba(255,255,255,0.06)
文字: #B0B0B8, 13px, weight 400
圆角: 8px (胶囊形可选: radius-full)
边框: 1px solid rgba(255,255,255,0.08)

Hover:  background rgba(255,255,255,0.10), 文字 #F5F5F7, border rgba(255,255,255,0.14)
Selected: background linear-gradient(135deg, rgba(91,108,255,0.20), rgba(177,76,255,0.20))
          文字 #F5F5F7, border rgba(91,108,255,0.40)
```

### 4.2 输入框 (Input)

#### 4.2.1 超大输入框 (Hero Input) — 核心组件

```
尺寸: width 100% (max-width 800px), min-height 120px, auto-grow
背景: rgba(255,255,255,0.04) + backdrop-filter: blur(20px)
边框: 1px solid rgba(255,255,255,0.08)
文字: #F5F5F7, 24px, weight 400, line-height 1.5
Placeholder: #80808C, 24px, weight 400
圆角: 20px
Padding: 24px 28px

Focus:  border 1px solid #5B6CFF
        box-shadow: 0 0 0 4px rgba(91,108,255,0.15)
        background rgba(255,255,255,0.06)

Placeholder 文案: "描述你想要的视频画面…"
辅助提示 (输入框下方): #80808C, 13px — "试试输入：赛博朋克、电影级光影、慢动作"
```

#### 4.2.2 普通输入框 (Standard Input)

```
尺寸: height 44px, width 100%
背景: rgba(255,255,255,0.04)
边框: 1px solid rgba(255,255,255,0.08)
文字: #F5F5F7, 14px, weight 400
Placeholder: #80808C, 14px
圆角: 10px
Padding: 0 14px

Focus: border 1px solid #5B6CFF, box-shadow 0 0 0 3px rgba(91,108,255,0.12)
```

#### 4.2.3 搜索框 (Search Input)

```
尺寸: height 36px, width 100%
背景: rgba(255,255,255,0.04)
边框: 1px solid rgba(255,255,255,0.06)
图标: 搜索图标 16px, 左侧, 颜色 #80808C
文字: #F5F5F7, 13px
圆角: 8px
Padding: 0 12px 0 36px (左侧为图标留空间)

Focus: border rgba(91,108,255,0.40)
```

### 4.3 滑块 (Slider) — 运动强度专用

小白友好的语义化滑块，不暴露 Denoising Strength 参数名：

```
整体宽度: 100% (max 280px)
轨道:
  高度: 6px
  背景: rgba(255,255,255,0.08)
  圆角: 3px
  已选轨道: linear-gradient(90deg, #5B6CFF, #B14CFF)

手柄 (Thumb):
  默认: 22px × 22px 圆形, 背景 #FFFFFF, box-shadow 0 2px 8px rgba(0,0,0,0.30)
  Hover: 26px × 26px (放大), box-shadow 0 4px 12px rgba(91,108,255,0.40)
  Dragging: 26px × 26px, box-shadow 0 4px 16px rgba(91,108,255,0.50)

刻度标记: 1-10 数字, #4A4A52 (未选) / #B0B0B8 (已选范围), 12px
当前值气泡: 手柄上方, 蓝紫渐变背景, #FFFFFF 文字, 13px, 圆角 8px, padding 4px 10px

语义标签:
  左端: "温柔" (#80808C, 13px)
  右端: "激烈" (#80808C, 13px)
  当前值描述: 根据数值显示 "轻微运动" / "流畅运动" / "大幅度运动" 等

前端映射 (不对用户暴露):
  滑块值 1-10 → Denoising Strength 0.15-0.75 (线性映射)
```

### 4.4 卡片 (Card)

#### 4.4.1 历史缩略图卡片

```
尺寸: width 100% (栏内自适应), 缩略图 16:9
背景: rgba(255,255,255,0.04) + backdrop-filter blur(20px)
圆角: 12px
边框: 1px solid rgba(255,255,255,0.06)
内边距: 0 (缩略图占满) + 底部信息区 padding 12px

缩略图区: 16:9 比例, 圆角顶部 12px, object-fit cover
底部信息:
  Prompt 文字: #F5F5F7, 13px, weight 400, 1行截断 (text-overflow ellipsis)
  时间: #80808C, 12px

Hover:  transform translateY(-2px)
        border rgba(255,255,255,0.12)
        box-shadow 0 8px 24px rgba(0,0,0,0.40)
        缩略图上覆盖半透明遮罩 + 播放图标 (居中, 32px, #FFFFFF)

Active (选中): border 1px solid rgba(91,108,255,0.50)
```

#### 4.4.2 结果展示卡片

```
尺寸: 居中, max-width 720px
背景: rgba(255,255,255,0.07) + backdrop-filter blur(30px)
圆角: 20px
边框: 1px solid rgba(255,255,255,0.10)
内边距: 20px

视频区: 16:9, 圆角 12px, 背景 #000000
操作区 (视频下方):
  布局: flex, gap 12px, justify-center
  按钮: [再来一次] (主按钮) + [下载] (次按钮) + [分享] (图标按钮)
  间距: margin-top 20px
```

### 4.5 弹窗 / 对话框

#### 4.5.1 确认框 (Confirm Dialog)

```
宽度: 400px (固定), 自适应高度
背景: rgba(26,26,30,0.80) + backdrop-filter blur(40px) saturate(180%)
圆角: 20px
边框: 1px solid rgba(255,255,255,0.12)
阴影: 0 16px 48px rgba(0,0,0,0.50)
内边距: 28px

遮罩层: rgba(10,10,10,0.60) + backdrop-filter blur(8px)

标题: #F5F5F7, 18px, weight 600, margin-bottom 12px
内容: #B0B0B8, 14px, weight 400, line-height 1.6, margin-bottom 24px
按钮区: flex, gap 12px, justify-end
  主按钮 (确认) + 次按钮 (取消)

入场动效: opacity 0→1 + scale 0.95→1, 300ms, ease-out-expo
```

#### 4.5.2 设置面板 (Settings Drawer)

```
位置: 右侧滑出抽屉
宽度: 400px, 高度 100% (减去标题栏)
背景: rgba(26,26,30,0.80) + backdrop-filter blur(40px) saturate(180%)
边框: 1px solid rgba(255,255,255,0.10) (仅左侧)
内边距: 24px

分组:
  分组标题: #80808C, 13px, weight 500, 大写字母间距 0.05em
  分组项: 高度 48px, flex 布局 (标签 + 控件)
  分组间距: 24px

入场动效: translateX(100%) → translateX(0), 300ms, ease-out-expo
```

### 4.6 加载态 — 进度文案化容器

**核心原则：不显示百分比数字，用人类语言描述进度。**

```
容器: 居中, max-width 480px
背景: rgba(255,255,255,0.04) + backdrop-filter blur(30px)
圆角: 20px
边框: 1px solid rgba(255,255,255,0.08)
内边距: 40px 32px
文字居中对齐

主文案 (进度文案):
  #F5F5F7, 24px, weight 600
  呼吸动画: opacity 0.7 → 1.0 → 0.7, 2s 循环, ease-in-out

副文案 (预计时间):
  #80808C, 14px, weight 400
  margin-top 12px
  示例: "预计还需 30 秒"

动画指示器 (进度条):
  宽度: 240px, 高度: 4px, 圆角 2px
  背景: rgba(255,255,255,0.08)
  填充: linear-gradient(90deg, #5B6CFF, #B14CFF, #5B6CFF), background-size 200%
  流动动画: background-position 0% → 200%, 1.5s linear infinite
  位于文案下方, margin-top 24px
```

### 4.7 拖拽上传区

```
尺寸: width 100%, height 200px
背景: rgba(91,108,255,0.04)
边框: 2px dashed rgba(255,255,255,0.15)
圆角: 16px
内容居中对齐

默认态:
  图标: 上传图标 48px, #80808C
  主文案: #B0B0B8, 16px, weight 500 — "拖拽图片到此处"
  副文案: #80808C, 13px — "或点击选择文件"

拖拽悬停态 (dragover):
  边框: 2px solid #5B6CFF
  背景: rgba(91,108,255,0.08)
  图标颜色: #5B6CFF
  主文案颜色: #F5F5F7
  box-shadow: 0 0 0 4px rgba(91,108,255,0.15)
  scale: 1.02
```

### 4.8 风格选择卡片

```
尺寸: 120px × 140px (宽 × 高)
背景: rgba(255,255,255,0.04)
圆角: 12px
边框: 1px solid rgba(255,255,255,0.06)
内容: 预览图 (占上部 80px, 16:9 裁切) + 风格名 (下部)

风格名: #B0B0B8, 13px, weight 500, 居中

Hover: border rgba(255,255,255,0.12), transform translateY(-2px)

选中态:
  border: 2px solid
  border-image: linear-gradient(135deg, #5B6CFF, #B14CFF) 1
  右上角: 蓝紫渐变圆形勾选标记 (16px)
  风格名颜色: #F5F5F7

预设风格: 油画 / 3D / 水墨 / 赛博朋克 / 日系动漫 / 写实
```

---

## 五、动效规范

### 5.1 缓动函数 (Easing)

| Token | cubic-bezier | 用途 |
|-------|-------------|------|
| `--ease-out-quad` | `cubic-bezier(0.25, 0.46, 0.45, 0.94)` | 标准出场，自然减速 |
| `--ease-in-out-cubic` | `cubic-bezier(0.65, 0.05, 0.36, 1)` | 平滑过渡，进出对称 |
| `--ease-out-expo` | `cubic-bezier(0.16, 1, 0.3, 1)` | 弹性出场，高级感 |
| `--ease-spring` | `cubic-bezier(0.34, 1.56, 0.64, 1)` | 弹簧微交互，轻微过冲 |

### 5.2 时长规范 (Duration)

| Token | 时长 | 用途 |
|-------|------|------|
| `--dur-fast` | 150ms | 按钮 press/hover 反馈、图标状态切换 |
| `--dur-normal` | 300ms | 面板展开/收起、卡片切换、弹窗出现 |
| `--dur-slow` | 500ms | 页面切换过渡、进度文案切换 |
| `--dur-slower` | 800ms | 首屏引导动画、品牌动画 |

### 5.3 具体动效定义

#### 5.3.1 毛玻璃过渡动效

```
触发: 面板展开/收起、弹窗出现/消失
属性: opacity + transform + backdrop-filter
时长: 300ms
缓动: ease-out-expo
入场: opacity 0→1, scale 0.96→1 (弹窗) / translateX(100%)→0 (抽屉)
出场: opacity 1→0, scale 1→0.96 / translateX 0→100%
```

#### 5.3.2 进度文案化动效

```
文案切换 (crossfade):
  旧文案: opacity 1→0, translateY(0)→translateY(-8px), 200ms, ease-in-out-cubic
  新文案: opacity 0→1, translateY(8px)→translateY(0), 200ms, ease-in-out-cubic
  切换间隔: 每 2.5-3 秒切换一条文案

呼吸动画 (持续):
  opacity 0.7 → 1.0 → 0.7, 2s 循环, ease-in-out
  与文案切换叠加，文案静止时呼吸感持续

进度条流动:
  background-position: 0% → 200%, 1.5s linear infinite
```

#### 5.3.3 首屏引导动画时序

```
T=0ms      背景循环视频开始播放 (loop, muted, object-fit cover)
           背景上方叠加: rgba(10,10,10,0.50) + radial-gradient 光晕

T=800ms    超大输入框淡入
           opacity 0→1, translateY(20px)→translateY(0)
           时长 600ms, ease-out-expo

T=1000ms   "生成" 按钮淡入
           opacity 0→1, translateY(16px)→translateY(0)
           时长 500ms, ease-out-expo

T=1400ms   灵感词按钮逐个 stagger 淡入
           每个间隔 80ms, 单个时长 300ms, ease-out-quad
           opacity 0→1, translateY(8px)→translateY(0)

T=2000ms   左侧历史栏滑入 (如有历史记录)
           translateX(-100%)→translateX(0), 400ms, ease-out-expo
```

#### 5.3.4 按钮点击微交互

```
Hover:
  transform: translateY(-1px), 150ms, ease-out-quad
  (主按钮) box-shadow 出现品牌光晕

Pressed:
  transform: scale(0.97), 100ms, ease-out-quad

Released:
  transform: scale(1.0), 200ms, ease-spring (轻微过冲回弹)
```

#### 5.3.5 页面切换过渡

```
模式切换 (一句话出片 ↔ 图生视频 ↔ 风格化):
  旧页面: opacity 1→0, translateY(0)→translateY(-8px), 200ms
  新页面: opacity 0→1, translateY(8px)→translateY(0), 300ms, ease-out-expo
  总时长: 500ms (200ms 淡出 + 300ms 淡入，有 100ms 交叉)

生成 → 结果展示:
  进度容器: opacity 1→0, scale 1→0.96, 300ms
  结果卡片: opacity 0→1, scale 0.96→1, 400ms, ease-out-expo (延迟 100ms)
```

#### 5.3.6 滑块拖动反馈

```
手柄 Hover:
  尺寸: 22px → 26px, 150ms, ease-out-quad

值气泡出现:
  opacity 0→1, scale(0.5)→scale(1), 200ms, ease-spring
  位于手柄上方, 间距 8px

拖动中:
  手柄保持 26px
  已选轨道实时跟随 (无动画延迟)
  值气泡数字实时更新

释放:
  手柄: 26px → 22px (如未 hover), 150ms
  值气泡: opacity 1→0, scale(1)→scale(0.5), 150ms
```

### 5.4 前端可实现性标注

| 动效 | 实现方式 | 成本 | 说明 |
|------|----------|------|------|
| 按钮 hover/press | CSS `transition` | 低 | 纯 CSS，无依赖 |
| 面板展开/收起 | CSS `transition` + `transform` | 低 | 纯 CSS |
| 淡入淡出 | CSS `transition` + `opacity` | 低 | 纯 CSS |
| 滑块反馈 | CSS `transition` + JS 事件 | 低 | 原生 range 或自定义 |
| 进度条流动 | CSS `@keyframes` + `background-position` | 低 | 纯 CSS 动画 |
| 呼吸动画 | CSS `@keyframes` + `opacity` | 低 | 纯 CSS |
| 进度文案 crossfade | JS 定时器 + CSS `transition` | 中 | 需 JS 控制文案数组与定时切换 |
| Stagger 淡入 | JS + CSS `transition-delay` | 中 | 需 JS 计算 delay 或循环赋值 |
| 首屏引导时序 | JS `setTimeout` 队列 | 中 | 需精确时序控制 |
| "再来一次"图标旋转 | CSS `@keyframes rotate` | 低 | 纯 CSS 360° 旋转 |
| 复杂加载动画 (可选) | Lottie JSON | 高 | 可选增强项，非必须。建议 MVP 阶段用 CSS 流动条替代 |

> **MVP 阶段建议**：全部用 CSS + 少量 JS 实现，不引入 Lottie 依赖，降低打包体积和前端复杂度。Lottie 动效留作 V2 增强项。

---

## 六、Logo / 品牌基础概念

### 6.1 品牌调性关键词

| 维度 | 关键词 |
|------|--------|
| 气质 | 通透、流光、折射、未来感 |
| 性格 | 专业、克制、温暖、可靠 |
| 感受 | 高级但不距离、科技但不冰冷、简约但不简单 |
| 隐喻 | "玻璃之光" — AI 是一束光，NexusVideo 是棱镜，折射出用户的创意 |

### 6.2 Logo 设计方向（3 个概念）

#### 方向一："N" 光棱镜

- **形态**：字母 "N" 作为主体几何骨架，用蓝紫渐变线条勾勒
- **核心意象**：N 中间的斜线设计为光束穿过棱镜的折射路径，斜线处有渐变光效
- **表达**：AI 是一束光 → NexusVideo 是棱镜 → 折射出创意视频
- **风格**：几何、锐利、科技感强
- **适用场景**：品牌主 Logo、应用图标

#### 方向二：播放键 + 光环

- **形态**：圆角三角形播放键为主体，外圈环绕蓝紫渐变光环
- **核心意象**：播放键 = 视频直觉认知，光环 = AI 赋能感
- **表达**：一看就知道是"播放/生成视频"的工具
- **风格**：友好、直觉、亲切，小白一看就懂
- **适用场景**：应用图标、启动页、空状态

#### 方向三："N" + 视频帧

- **形态**：字母 "N" 与视频帧/胶片元素融合，N 的竖线变形为视频画框边框
- **核心意象**：Nexus + Video 的字面融合
- **表达**：连接 (Nexus) 创意与视频 (Video)
- **风格**：现代、融合、专业
- **适用场景**：品牌主 Logo、标题栏

> **推荐**：方向一 "光棱镜" 作为品牌主 Logo（辨识度+概念深度），方向二 "播放键+光环" 作为应用图标（直觉认知）。两者共享蓝紫渐变色彩体系。

### 6.3 品牌色彩应用指南

| 应用场景 | 配色方案 |
|----------|----------|
| 深色背景 (App内/官网) | 渐变主体 (#5B6CFF→#B14CFF) + 白色辅助 |
| 浅色背景 (文档/名片) | 深色主体 (#0A0A0A) + 渐变点缀 |
| 单色场景 (favicon/水印) | 纯白 #FFFFFF 或 纯蓝紫 #5B6CFF |
| 启动图标 | 渐变背景 (#0A0A0A 底 + 渐变光晕) + 白色/渐变 Logo |

**Logo 安全空间**：Logo 高度的 1× 四周留白
**Logo 最小尺寸**：24px 高度（再小则渐变和细节丢失）

---

## 七、小白体验特殊设计

### 7.1 灵感词汇按钮

**10 个预设灵感词**（覆盖常见创作场景，激发想象力）：

| 序号 | 灵感词 | 对应风格倾向 |
|------|--------|-------------|
| 1 | 赛博朋克 | 科幻/霓虹 |
| 2 | 电影级光影 | 写实/质感 |
| 3 | 慢动作 | 动态/氛围 |
| 4 | 水下世界 | 场景/梦幻 |
| 5 | 极光星空 | 场景/唯美 |
| 6 | 油画风格 | 艺术/纹理 |
| 7 | 樱花飘落 | 唯美/粒子 |
| 8 | 火焰特效 | 特效/动态 |
| 9 | 复古胶片 | 风格/质感 |
| 10 | 微距特写 | 视角/细节 |

**设计规格**：
- 样式：小按钮 (Tag Button) 规格，见 4.1.5
- 布局：输入框下方，flex-wrap 自动换行，gap 8px
- 交互：
  - 点击 → 自动填入输入框末尾（如输入框已有内容，追加并用逗号分隔）
  - 按钮变为"已选"状态（渐变背景）
  - 再次点击已选按钮 → 从输入框中移除该词，恢复未选状态
- 入场：首屏引导时 stagger 淡入（见 5.3.3）

### 7.2 "再来一次"按钮设计

```
位置: 结果展示卡片下方，主操作位（最醒目）
样式: 主按钮 (Primary Button) + 刷新图标
文案: "再来一次"
图标: 刷新/循环图标 18px, 位于文字左侧, gap 8px

交互:
  1. 点击 → 图标旋转 360° (500ms, ease-out-expo)
  2. 自动更换随机种子 (用户完全无感)
  3. 结果卡片淡出 → 进度文案化容器淡入
  4. 重新进入生成流程

设计意图:
  - "再来一次" 比 "重新生成" 更口语化、更友好
  - 主按钮样式传达"这是最自然的下一步操作"
  - 图标旋转提供"刷新"的直觉反馈
  - 种子更换对用户不可见，用户只感知到"换了一个新结果"
```

### 7.3 进度文案化文案库

**核心原则：不显示百分比，用人类语言描述进度，文案有温度、有画面感。**

#### 阶段一：构思阶段（对应 0%-20%）

| 序号 | 文案 |
|------|------|
| 1 | 正在构思画面… |
| 2 | 理解你的创意中… |
| 3 | 想象这个场景… |
| 4 | 正在解读你的描述… |

#### 阶段二：生成阶段（对应 20%-60%）

| 序号 | 文案 |
|------|------|
| 5 | 正在绘制第一帧… |
| 6 | 画面逐渐成形… |
| 7 | 正在渲染细节… |
| 8 | 色彩与光影融合中… |
| 9 | 笔触正在落下… |

#### 阶段三：精修阶段（对应 60%-90%）

| 序号 | 文案 |
|------|------|
| 10 | 正在让画面动起来… |
| 11 | 优化流畅度中… |
| 12 | 即将完成，请稍候… |
| 13 | 画面正在鲜活起来… |

#### 阶段四：完成阶段（对应 90%-100%）

| 序号 | 文案 |
|------|------|
| 14 | 最后润色中… |
| 15 | 马上就好… |
| 16 | 正在为您打包视频… |

#### 切换规则

- 每个阶段内随机选取文案，每 2.5-3 秒切换一条
- 同一条文案不连续重复
- 阶段切换跟随后端 WebSocket 进度回调（前端将 0-100% 映射到四阶段）
- 副文案（预计时间）根据剩余时间动态更新：
  - > 60 秒：不显示副文案（避免焦虑）
  - 30-60 秒："预计还需约 1 分钟"
  - 10-30 秒："预计还需 30 秒"
  - < 10 秒："马上就好"

#### 异常态文案（出错时用人话）

| 场景 | 文案 | 设计说明 |
|------|------|----------|
| 显存不足 | "您的电脑显存不太够，要不要试试云端加速？" | 不说 OOM，给解决方案 |
| 网络断开 | "网络好像断了，检查一下再试试" | 口语化，不说 Network Error |
| 生成超时 | "这次生成花了太长时间，要不要再来一次？" | 不说 Timeout，给重试选项 |
| 模型未就绪 | "AI 正在热身，请稍等片刻…" | 不说 Model Loading |

### 7.4 首次启动初始化进度页

```
全屏布局: 深色背景 #0A0A0A + 蓝紫光晕 (radial-gradient)

中央内容 (垂直居中):
  Logo: 64px 高度, 居中
  品牌名: "NexusVideo", #F5F5F7, 28px, weight 600, margin-top 16px
  品牌口号: "让创意如光般流动", #80808C, 14px, margin-top 8px

  进度文案区 (margin-top 48px):
    主文案: #F5F5F7, 20px, weight 600, 呼吸动画
    进度条: 宽度 280px, 高度 4px, 蓝紫渐变流动条
    副文案: #80808C, 13px, margin-top 16px

底部:
  首次启动提示: "首次准备约需 2-3 分钟，请耐心等待", #4A4A52, 12px

初始化文案序列:
  "正在准备创作环境…"
  "加载 AI 模型中…"
  "配置渲染引擎…"
  "即将就绪…"

入场动效:
  T=0:      背景光晕淡入 (800ms)
  T=400ms:  Logo + 品牌名淡入 (600ms, ease-out-expo)
  T=800ms:  进度文案 + 进度条淡入 (400ms)

设计意图:
  - 首次启动模型解压需 2-3 分钟，必须有进度反馈避免用户以为卡死
  - 不显示百分比（用户不知道 45% 意味着还要多久）
  - 用"准备创作环境"等人话替代"解压模型文件"
  - 进度条流动动画提供"正在进行中"的持续反馈
```

---

## 八、Design Token CSS 变量汇总

> 以下为前端可直接使用的 CSS 自定义属性完整定义，建议放入全局 `:root` 中。

```css
:root {
  /* ===== 背景色阶 ===== */
  --bg-base: #0A0A0A;
  --bg-surface: #121214;
  --bg-elevated: #1A1A1E;
  --glass-1: rgba(255, 255, 255, 0.04);
  --glass-2: rgba(255, 255, 255, 0.07);
  --glass-3: rgba(26, 26, 30, 0.80);
  --hover-overlay: rgba(255, 255, 255, 0.10);
  --active-overlay: rgba(255, 255, 255, 0.14);

  /* ===== 品牌色 ===== */
  --brand-blue: #5B6CFF;
  --brand-purple: #B14CFF;
  --brand-blue-hover: #4A5BFF;
  --brand-purple-hover: #A03BFF;
  --brand-gradient: linear-gradient(135deg, #5B6CFF 0%, #B14CFF 100%);
  --brand-gradient-hover: linear-gradient(135deg, #4A5BFF 0%, #A03BFF 100%);
  --bg-glow: radial-gradient(circle at 50% 25%, rgba(91, 108, 255, 0.15) 0%, transparent 55%);

  /* ===== 辅助色 ===== */
  --accent-cyan: #3DD6E8;
  --accent-pink: #FF6B9D;

  /* ===== 语义色 ===== */
  --success: #3DD68C;
  --success-bg: rgba(61, 214, 140, 0.12);
  --warning: #FFB938;
  --warning-bg: rgba(255, 185, 56, 0.12);
  --error: #FF5B5B;
  --error-bg: rgba(255, 91, 91, 0.12);
  --info: #5B6CFF;
  --info-bg: rgba(91, 108, 255, 0.12);

  /* ===== 文字色 ===== */
  --text-primary: #F5F5F7;
  --text-secondary: #B0B0B8;
  --text-tertiary: #80808C;
  --text-disabled: #4A4A52;
  --text-on-brand: #FFFFFF;

  /* ===== 边框 ===== */
  --border-subtle: 1px solid rgba(255, 255, 255, 0.06);
  --border-default: 1px solid rgba(255, 255, 255, 0.10);
  --border-strong: 1px solid rgba(255, 255, 255, 0.16);
  --border-focus: 1px solid #5B6CFF;

  /* ===== 阴影 ===== */
  --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.3);
  --shadow-md: 0 8px 24px rgba(0, 0, 0, 0.4);
  --shadow-lg: 0 16px 48px rgba(0, 0, 0, 0.5);
  --shadow-glow: 0 0 0 4px rgba(91, 108, 255, 0.15);
  --shadow-brand: 0 8px 24px rgba(91, 108, 255, 0.30);

  /* ===== 圆角 ===== */
  --radius-sm: 8px;
  --radius-md: 12px;
  --radius-lg: 16px;
  --radius-xl: 20px;
  --radius-full: 9999px;

  /* ===== 间距 ===== */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --space-2xl: 48px;
  --space-3xl: 64px;

  /* ===== 字体 ===== */
  --font-sans: "Inter", "HarmonyOS Sans SC", "Noto Sans SC", "PingFang SC",
               -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-mono: "JetBrains Mono", "Fira Code", Consolas, monospace;

  /* ===== 缓动 ===== */
  --ease-out-quad: cubic-bezier(0.25, 0.46, 0.45, 0.94);
  --ease-in-out-cubic: cubic-bezier(0.65, 0.05, 0.36, 1);
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);

  /* ===== 时长 ===== */
  --dur-fast: 150ms;
  --dur-normal: 300ms;
  --dur-slow: 500ms;
  --dur-slower: 800ms;
}
```

---

## 九、小白友好度风险点标注

| 风险点 | 风险等级 | 说明 | 缓解措施 |
|--------|----------|------|----------|
| 信息密度过高 | 中 | 右侧参数栏展开时可能信息过多 | 默认收起，仅展示"高级参数"入口；展开后分组清晰、间距充足 |
| 专业术语暴露 | 高 | 后端 ComfyUI 参数可能泄漏到前端 | 前端硬性白名单：只接收 prompt/seed/运动强度等简化参数；运动强度映射 Denoising 对用户不可见 |
| 首次等待焦虑 | 高 | 模型解压 2-3 分钟 + 首次生成慢 | 初始化进度页 + 进度文案化 + 预计时间提示；生成中用文案替代百分比 |
| 错误信息吓人 | 高 | CUDA OOM / 网络错误等原始报错 | 后端错误码 → 前端人话文案映射表；绝不直接展示原始 error message |
| 空状态迷茫 | 中 | 首次打开无历史记录、无引导 | 首屏背景视频 + 灵感词按钮 + 输入框 placeholder 引导 |
| 操作步骤过多 | 低 | 三步以内完成生成 | 模式切换在状态栏一键切换；参数默认最优值，用户可不调参直接生成 |

---

## 十、交付物清单与下游衔接

### 本文档交付物

1. 本规范文档（`NexusVideo-Design-System.md`）— 完整设计系统规范
2. Design Token CSS 变量定义（第八章）— 前端可直接复制使用
3. 布局结构示意图（可视化）— 三栏布局 + 尺寸标注
4. 色板可视化（可视化）— 完整色板预览

### 下游衔接说明

| 下游任务 | 衔接内容 |
|----------|----------|
| Task #6 高保真设计稿 | 本规范作为设计基础，三大模式（一句话出片/图生视频/风格化）高保真稿将严格遵循本系统 |
| Task #8 主界面 UI 实现 | 前端 (client-tauri-dev) 可直接使用第八章 CSS 变量；组件规格参考第四章；动效参考第五章 |
| 进度反馈系统 | 进度文案化文案库（7.3）+ 动效（5.3.2）直接对接前端进度组件 |

### 与前端开发 (client-tauri-dev) 对齐要点

1. **毛玻璃实现**：`backdrop-filter` 需加 `-webkit-` 前缀；Tauri WebView2/WKWebView 支持良好；毛玻璃区域下方必须有可模糊内容
2. **字体打包**：Inter + HarmonyOS Sans SC 需内嵌到 resources，建议只打包 400/500/600 三字重
3. **动效实现**：MVP 阶段全部用 CSS + 少量 JS，不引入 Lottie；所有缓动用 CSS 变量统一管理
4. **进度文案化**：前端维护文案数组 + 阶段映射，后端 WebSocket 推送进度百分比，前端映射到四阶段并随机选文案
5. **运动强度滑块**：前端值 1-10，发送给后端时映射为 Denoising Strength（0.15-0.75），映射逻辑在后端 local_server.py 完成

---

*文档结束 · NexusVideo Design System v1.0 · 苏璃光*
