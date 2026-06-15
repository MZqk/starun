# Starun — 天文后期智能处理平台

## 开发需求文档 v1.0

> 本文档包含从零构建 Starun 所需的全部设计规范、页面规格、组件定义和技术约束。
> 当前原型部署: https://b4d9db3268ea4f28afd363b00141f6df.app.codebuddy.work
>
> 已批准的实现依据:
> [MVP 设计文档](docs/superpowers/specs/2026-06-11-starun-mvp-design.md)、
> [MVP 实施计划](docs/superpowers/plans/2026-06-11-starun-mvp-implementation.md)。

---

## 1. 产品概述

**Starun** 是一款面向天文摄影爱好者的在线后期处理工具。用户上传 FITS 格式天文原始文件，通过 AI 分析图像关键参数并生成优化建议，或一键执行完整后期处理管线。

**核心定位**: 专业但不复杂。界面风格偏向深空观测台的暗室氛围——深黑底色、红色点缀、银河星场纹理。

**Milestone 1 能力边界**: FITS 验证、HDU 扫描与选择、头信息、图像尺寸、位深和基础统计是真实结果。当前 SNR、FWHM、椭圆率等专业指标，Agent 计划与结果、处理预览和下载产物均为明确标识的确定性 Mock，不代表真实天文图像分析或处理结果。

---

## 2. 设计系统 (Design Tokens)

### 2.1 色彩方案

| Token | 色值 | 用途 |
|-------|------|------|
| `--color-space-bg` | `#08080A` | 主背景 (近纯黑) |
| `--color-space-surface` | `#0E0E10` | 次级背景/浮层基底 |
| `--color-space-card` | `#141416` | 卡片背景 |
| `--color-space-muted` | `#1A1A1E` | 弱化区域/分隔 |
| `--color-space-border` | `#2A2A30` | 边框/分割线 |
| `--color-space-accent` | `#D9382E` | 主强调色 (深红) |
| `--color-space-accent-hover` | `#C02A22` | 悬停加深 |
| `--color-space-accent-soft` | `rgba(217,56,46,0.10)` | 弱强调背景 |
| `--color-space-green` | `#7D9B76` | 成功/完成 (鼠尾草绿) |
| `--color-space-amber` | `#D9882E` | 警告/数据指标 |
| `--color-space-purple` | `#9B8EC4` | 辅助色 (灰调薰衣草) |
| `--color-space-red` | `#D9382E` | 错误/删除 |
| `--color-space-text` | `#E8E4DD` | 正文 (暖米白) |
| `--color-space-text-secondary` | `#9E9890` | 辅助文字 |
| `--color-space-text-muted` | `#706A64` | 弱化文字 |

**WCAG 对比度**: 正文 `#E8E4DD` 在 `#08080A` 上对比度约 13:1 (AAA 级)。

### 2.2 字体系统

| 用途 | 字体 | 变体 |
|------|------|------|
| UI / 正文 | Geist | Regular 400, Medium 500, SemiBold 600, Bold 700 |
| 等宽/代码/日志 | JetBrains Mono | Regular 400, Medium 500, Bold 700 |
| 降级 | system-ui, -apple-system, sans-serif | — |

**字号梯度**: 11px (标签) → 12px (辅助) → 13px (正文) → 14px (强调正文) → 16px (副标题) → 18-20px (小标题) → 24px (章节标题) → 48px-68px (大标题 Hero)

### 2.3 圆角 & 间距

- **圆角**: 按钮 `0.75rem`(12px), 卡片 `1rem`(16px), 大容器 `1rem`(16px), 标签 `0.375rem`(6px)
- **间距基准**: 4px 最小单位，常用 `gap-3`(12px), `gap-4`(16px), `gap-5`(20px), `gap-8`(32px)
- **页面内边距**: 移动端 `px-5`, 桌面端 `px-8`
- **最大宽度**: `max-w-7xl` (1280px)

---

## 3. 深空视觉效果

### 3.1 星场背景 (Starfield)

通过 CSS `radial-gradient` 叠加 15 个散落星点，覆盖整个页面背景:

- 白色星点: 12 颗，透明度 0.25-0.6，大小 0.5-1px
- 红色星点: 3 颗 (Betelgeuse 风格)，透明度 0.2-0.5，大小 1-1.5px
- 固定位置分布，不随滚动移动

### 3.2 星云光晕 (Galaxy Glow)

三层 `radial-gradient` 模拟深空星云:
- 左上方红色光晕: `rgba(217,56,46,0.07)`，椭圆 70%×50%
- 右下方紫色光晕: `rgba(155,142,196,0.04)`，椭圆 50%×40%
- 中央暗红色光晕: `rgba(217,56,46,0.03)`，椭圆 30%×60%

### 3.3 星光闪烁动画

`.star` 类使用 `twinkle` 关键帧 (3s 周期，不规则闪烁)，5 个子元素各有不同的 `animation-delay` 和 `animation-duration`。

### 3.4 卡片质感

`bg-card-warm`: 135° 渐变 + `1px solid rgba(42,42,48,0.5)` 边框
`glow-warm`: 红色外发光 shadow (不透明度 0.04-0.08) + 顶部 1px 内高光

---

## 4. 页面路由 & 结构

```
/              首页 (Dashboard)
/analysis      AI 优化建议
/processing    AI 智能处理
/history       上传历史
```

所有页面共享 `<NavBar />` (固定在顶部, sticky, 带 backdrop-blur 毛玻璃效果)。

### 4.1 导航栏 (NavBar)

- **高度**: 64px (h-16)
- **背景**: `bg-space-surface/70` + `backdrop-blur-xl`
- **底部边框**: `border-b border-space-border/30`
- **Logo**: SVG 星标十字图标 (中心圆 + 十字线 + 对角短线) + "Starun" 文字
- **导航项**: 首页 / 优化建议 / 智能处理 / 历史记录
  - 当前页: `text-space-accent font-medium` + 底部下划线
  - 其他页: `text-space-text-secondary hover:text-space-text`
- **右侧按钮**: "上传 FITS"，SVG 上传图标，红色半透明背景

---

## 5. 页面详细规格

### 5.1 首页 `/`

```
┌─────────────────────────────────────┐
│  [NavBar]                           │
├─────────────────────────────────────┤
│                                     │
│  Hero (12-col grid)                 │
│  ┌───────────────┬───────────┐      │
│  │ 大标题 (7col)  │ 上传区    │      │
│  │ 副标题 + CTA   │ (5col)    │      │
│  │               │ 向上偏移  │      │
│  └───────────────┴───────────┘      │
│                                     │
│  核心功能 (7-col grid)              │
│  ┌──────────┬──────┐               │
│  │ 主卡片    │ 副卡片│               │
│  │ (4col)   │(3col)│               │
│  ├──────────┴──────┤               │
│  │ 横向横条卡片     │               │
│  └─────────────────┘               │
│                                     │
│  三步上手 (时间线)                   │
│  ──●────────●────────●──           │
│   1        2        3              │
│                                     │
└─────────────────────────────────────┘
```

**Hero 区域**:
- 标题: 54-68px Bold，行高 1.04，"应有的" 三字用红色强调
- 副标题: 16-18px，最大宽度 512px
- 两个 CTA: 红色填充 "开始分析" + 描边 "一键处理"
- 上传区: 圆角卡片 + glow，内部 `UploadZone` 组件紧凑模式

**功能卡片** (`FeatureCard` 组件):

| 变体 | 列宽 | 样式特征 |
|------|------|---------|
| `primary` | md:col-span-4 | 更大内边距(p-7 sm:p-8)，hover 时 glow-warm，标题 20-24px |
| `secondary` | md:col-span-3 | 较小内边距(p-6)，灰色边框，标题 18px |
| `horizontal` | full-width | flex-row 横排，hover 显示箭头，标签在右侧 |

每个卡片顶部有装饰元素: 彩色方块 + 横线分隔。底部有彩色标签组。
标签颜色: amber(暖橙) / sage(鼠尾草绿) / dusty(灰紫)。

**三步上手**:
- 桌面端: 顶部横线连接三个圆形数字
- 移动端: 纵向排列，数字在左侧
- 圆形数字: 14-16px，hover 时边框变红、放大 1.05x、数字变红

### 5.2 AI 优化建议 `/analysis`

```
┌──────────────────────────────────────┐
│  [NavBar]                            │
├──────────┬───────────────────────────┤
│ 上传面板  │ 分析报告                   │
│ (420px)  │                           │
│          │ ● 分析完成                  │
│ ┌──────┐ │                           │
│ │上传区 │ │ ┌──────┬──────┬──────┐    │
│ │      │ │ │ SNR  │ FWHM │ 椭圆 │    │
│ │文件信息│ │ │ 47.2 │ 2.83 │ 0.12 │    │
│ │      │ │ └──────┴──────┴──────┘    │
│ │[分析] │ │                           │
│ └──────┘ │ 处理建议                   │
│          │ ┃ 拉伸策略 - Arcsinh...    │
│          │ ┃ 降噪参数 - σ=0.8...      │
│          │ ┃ 反卷积 - RL 15次...      │
│          │ ┃ 色彩校准 - PCC...        │
└──────────┴───────────────────────────┘
```

**参数卡片差异化**: 三张参数卡视觉上不完全相同——
- 第一张 (SNR): 左边框红色高亮 (`border-l-2 border-space-accent/25`)
- 第二张 (FWHM): 绿色染色背景 (`bg-space-green/5`)
- 第三张 (椭圆率): 保持中性
- 每张内部有进度条 (h-1, rounded)

**推荐卡片**: 左边框 2px 彩色线条 + 淡化标签
- 拉伸策略: 红色左边框
- 降噪参数: 绿色左边框
- 反卷积/色彩: 紫色左边框

### 5.3 AI 智能处理 `/processing`

```
┌──────────────────────────────────────┐
│  [NavBar]                            │
├──────────────┬───────────────────────┤
│ 处理管线     │ 前后对比              │
│ (440px)     │                       │
│             │ [并排|原始|处理后]     │
│ ┌─────────┐ │                       │
│ │上传 FITS │ │ ┌───────┬───────┐    │
│ │[开始处理]│ │ │ 原始   │ 处理后 │    │
│ └─────────┘ │ │ 预览   │ 预览   │    │
│             │ └───────┴───────┘    │
│ ● 预处理 ✓  │                       │
│ │ ● 对齐 ✓  │ 输出                  │
│ │ ● 拉伸→   │ [PNG] [TIFF]          │
│ │ ○ 降噪    │                       │
│ │ ○ 反卷积  │                       │
│ │ ○ 色彩    │                       │
│             │                       │
│ 处理日志     │                       │
│ [14:23:01]  │                       │
│ 开始预处理…  │                       │
└──────────────┴───────────────────────┘
```

**管线步骤**:
- 完成: 绿色圆点 + 绿色竖线连接下一项 + ✓ 图标
- 进行中: 红色圆点 + `animate-pulse-soft` + "进行中" 文字 + 呼吸动画
- 待处理: 灰色圆点 + 灰色文字
- 每个步骤显示名称 + 详细说明

**处理日志**:
- JetBrains Mono 等宽字体, 12px
- 偶数行浅色背景 (`bg-space-surface/20`)
- 时间戳灰色，日志文字按类型着色 (绿/灰/红)

**对比预览**:
- 三个视图切换: 并排 (grid-cols-2) / 原始 / 处理后
- 原始预览: 灰色星空 SVG 占位图
- 处理后: 渐进背景 + 跳动红点动画 + "正在处理…"
- 下载: 红色 PNG 按钮 + 描边 TIFF 按钮

### 5.4 上传历史 `/history`

```
┌──────────────────────────────────────┐
│  [NavBar]                            │
├──────────────────────────────────────┤
│ 历史记录                              │
│                                      │
│ ┌────┬────┬────┬────┐                │
│ │ 127│ 118│18.4│ 23 │  统计卡片      │
│ │上传│完成│ GB │本月│                │
│ └────┴────┴────┴────┘                │
│                                      │
│ ┌────────────────────────────────┐   │
│ │ 文件名    大小  模式  状态 时间 操作│   │
│ │ M31...  247MB 优化  完成  3分钟 →│   │
│ │ M42...  312MB 智能  完成  2小时 →│   │
│ │ NGC...  198MB 优化  处理中 5小时→ │   │
│ │ IC4...  456MB 智能  完成  昨天  →│   │
│ │ M45...   89MB 优化  失败  2天前 →│   │
│ └────────────────────────────────┘   │
└──────────────────────────────────────┘
```

- 统计卡片: `bg-card-warm` 圆角，数字 24px Bold
- 表格: 桌面端 12 列网格，移动端堆叠
- 文件图标: SVG 文档图标
- 状态着色: 完成=绿, 处理中=红, 失败=红
- 操作链接: 红色 + 箭头 SVG

---

## 6. 组件规格

### 6.1 UploadZone

```
Props:
  compact?: boolean    // 紧凑模式 (小内边距)
  onUpload?: (file: File) => void

States:
  - 默认: 上传图标 + "将 FITS 文件拖拽到此处" + 格式提示
  - 拖拽悬停: 边框变红 + scale 1.01
  - 已选择: SVG 文件图标 + 文件名 + "点击更换文件"

规格:
  - 虚线边框: 2px dashed, border-space-border/40
  - 圆角: 1rem (16px)
  - 上传图标: SVG (upload arrow), 44px 容器
  - 支持: .fits / .fit, 最大 500MB
  - 紧凑模式: p-5 (默认 p-8 sm:p-10)
```

### 6.2 FeatureCard

```
Props:
  title: string
  description: string
  tags: { label: string; color: "amber" | "sage" | "dusty" }[]
  href: string
  variant?: "primary" | "secondary" | "horizontal"

Variants:
  primary:
    - bg-card-warm, p-7 sm:p-8
    - hover: glow-warm
    - 标题: text-xl sm:text-2xl
    - 正文: text-base

  secondary:
    - bg-space-surface/60, p-6
    - border: border-space-border/20
    - hover: bg-card-warm, border 加深
    - 标题: text-lg
    - 正文: text-sm

  horizontal:
    - bg-card-warm, p-5 sm:p-6
    - flex-row 布局
    - 右侧标签 + 箭头 SVG
    - hover: border-space-accent/20

装饰元素:
  - 顶部: 彩色方块 (w-2 h-2) + 横线分隔
  - 底部: 标签组 (px-2.5 py-1, border + bg, 对应颜色)
```

---

## 7. 交互与动画

### 7.1 动画清单

| 动画 | 用途 | 实现 |
|------|------|------|
| `pressable` | 按钮/卡片按压 | `:active { transform: scale(0.97) }` |
| `breathe` | 处理状态指示 | 3s 周期 opacity 呼吸 (0.5→0.8) |
| `pulse-soft` | 进行中步骤 | 红色光圈脉冲 0→8px |
| `twinkle` | 星光闪烁 | 3s 不规则闪烁 (.star 类) |
| hover 过渡 | 卡片/按钮悬停 | transition-all duration-300 |
| 聚焦环 | 键盘导航 | outline 2px 红色 (`.focus-ring`) |

### 7.2 过渡参数

```
默认过渡: transition-all duration-300
按钮过渡: transition-all duration-150
按压过渡: transform 0.15s cubic-bezier(0.2, 0, 0, 1)
```

---

## 8. 技术栈

```
框架:        Next.js 16 (App Router)
语言:        TypeScript
样式:        Tailwind CSS v4
字体:        Geist + Geist Mono (next/font/google)
部署:        Next.js Web + 独立 FastAPI；上传、任务、轮询和下载由 API 提供
包管理:      npm
图标:        内联 SVG (无图标库依赖)
```

### 8.1 文件结构

```
src/
├── app/
│   ├── globals.css          # 全局样式 + 设计 tokens + 动画
│   ├── layout.tsx           # 根布局 (metadata + 字体)
│   ├── page.tsx             # 首页
│   ├── analysis/
│   │   └── page.tsx         # AI 优化建议
│   ├── processing/
│   │   └── page.tsx         # AI 智能处理
│   └── history/
│       └── page.tsx         # 上传历史
└── components/
    ├── NavBar.tsx           # 导航栏
    ├── FeatureCard.tsx      # 功能卡片
    └── UploadZone.tsx       # 文件上传区
```

### 8.2 启动命令

```bash
npm ci
npm run dev      # 开发: localhost:3000
npm run build    # Next.js 生产构建
```

---

## 9. 响应式断点

| 断点 | 宽度 | 行为 |
|------|------|------|
| 默认 (mobile) | < 640px | 单列布局，堆叠卡片 |
| sm | ≥ 640px | 两列网格，按钮并排 |
| md | ≥ 768px | 导航栏展开，卡片网格 |
| lg | ≥ 1024px | 左右分栏 (分析/处理页) |
| xl | ≥ 1280px | 最大宽度 1280px 居中 |

---

## 10. 后端接口预留

当前 Web 通过 HTTP 轮询任务状态和增量事件。FITS/HDU/基础统计来自真实检查；
专业指标、Agent 结果、预览和产物为明确标识的 Mock。

```
POST /api/uploads                         # 流式上传并验证 FITS (max 500MB)
POST /api/tasks/analysis                  # 创建 Mock 专业分析任务
POST /api/tasks/process                   # 创建 Mock Agent 处理任务
GET  /api/tasks/:id                       # HTTP 轮询任务状态和结果
GET  /api/tasks/:id/events?after=:seq     # HTTP 轮询增量事件
GET  /api/tasks/:id/artifacts/:name       # 下载明确标识的 Mock 产物
```

---

## 11. 附录：配色参考速查

| 场景 | Tailwind Class | 色值 |
|------|---------------|------|
| 页面背景 | `bg-space-bg` | `#08080A` |
| 卡片 | `bg-card-warm` | 渐变 `#141416→#1A1A1E` |
| 主按钮 | `bg-space-accent` | `#D9382E` |
| 正文 | `text-space-text` | `#E8E4DD` |
| 辅助文字 | `text-space-text-secondary` | `#9E9890` |
| 成功/完成 | `text-space-green` | `#7D9B76` |
| 数据指标 | `text-space-amber` | `#D9882E` |
| 辅助强调 | `text-space-purple` | `#9B8EC4` |
| 星场+星云 | `bg-starfield bg-galaxy` | CSS 多层渐变 |
| 发光卡片 | `glow-warm` | 红色阴影 |
| 按压反馈 | `pressable` | scale(0.97) |

---

*文档版本: v1.0 · 最后更新: 2026-06-11*
