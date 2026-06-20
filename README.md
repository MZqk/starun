# Starun

Starun 是面向天文摄影爱好者的 FITS 分析与 AI 后期辅助平台。Next.js
负责首页、分析页、AI 自动出图页、历史记录和浏览器端交互；FastAPI 负责
流式上传、FITS 检查、FITS 预览渲染、SQLite 任务状态、串行任务执行、
AI 调用、产物下载与清理。

## 本地开发

需要 Docker、Node.js/npm、Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
make install
make dev
make test
make lint
make build
```

`make dev` 等同于使用 Docker Compose 启动：

```bash
docker compose up --build
```

启动后 Web 默认位于 `http://localhost:3000`，API 位于
`http://localhost:8000`。

### 手动启动

手动启动适合本机调试 AI 接口和查看运行日志。

```bash
cp .env.example .env
cp api/.env.example api/.env
cd api
uv sync --extra dev
PYTHONPATH=. uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另开一个终端启动 Web：

```bash
cd web
npm ci
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

在部分 worktree 场景下，Next.js Turbopack 可能不接受指向工作区外部的
`node_modules` 符号链接；可改用：

```bash
cd web
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 ./node_modules/.bin/next dev --webpack --hostname 127.0.0.1 --port 3000
```

## 环境参数

项目使用 `STARUN_` 前缀读取后端配置。根目录 `.env` 主要放通用开发参数，
`api/.env` 主要放 AI Provider 参数。密钥只应保存在服务端环境变量或
`api/.env`，不要提交到 Git。

### 前端参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | 开发浏览器中默认 `http://localhost:8000` | 前端访问 API 的 origin。生产或服务端渲染环境需要显式设置。 |
| `STARUN_API_PROXY_TARGET` | 空 | 可选 Next.js same-origin `/api` rewrite 目标。 |

### 后端基础参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `STARUN_DATABASE_URL` | `sqlite:///./starun.db` | SQLite 数据库地址。相对路径按 API 进程工作目录解析。 |
| `STARUN_DATA_ROOT` | `./data` | 上传文件、任务文件和产物存储目录。Docker 中为 `/data`。 |
| `STARUN_MAX_UPLOAD_BYTES` | `524288000` | 单文件最大上传大小，默认 500 MB。 |
| `STARUN_UPLOAD_TTL_SECONDS` | `3600` | 上传记录过期时间。 |
| `STARUN_TASK_TTL_SECONDS` | `86400` | 非终态任务的默认保留时间。 |
| `STARUN_DAILY_TASK_LIMIT` | `5` | 每个浏览器客户端每日任务数量限制。 |
| `STARUN_ANALYSIS_TIMEOUT_SECONDS` | `600` | 专业分析任务超时时间。 |
| `STARUN_PROCESSING_TIMEOUT_SECONDS` | `3600` | AI 自动出图任务超时时间。 |
| `STARUN_MIN_FREE_DISK_BYTES` | `5368709120` | 接收上传前要求的数据盘最小剩余空间，默认 5 GB。 |
| `STARUN_WEB_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | 允许跨域访问 API 的 Web origin 列表。 |

### AI 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `STARUN_AGENT_BASE_URL` | `https://api.openai.com/v1` | OpenAI 或 OpenAI-compatible 模型 API 地址。 |
| `STARUN_AGENT_API_KEY` | 空 | Agents SDK 使用的服务端密钥。 |
| `STARUN_AGENT_MODEL` | `gpt-5.1` | 两个独立 Agent 默认使用的模型。 |
| `STARUN_AGENT_PROTOCOL` | `responses` | `responses` 或 `chat_completions`，必须显式配置，不自动探测。 |
| `STARUN_AGENT_TIMEOUT_SECONDS` | `180` | 单次模型请求超时时间。 |
| `STARUN_AGENT_MAX_TURNS` | `8` | 单个 Agent run 的最大轮次。 |
| `STARUN_ANALYSIS_SKILL_PATH` | `../deep-sky-advisor` | 专业分析 Agent 唯一可见的本地 skill。 |
| `STARUN_PROCESSING_SKILL_PATH` | `../deep-sky-processor` | AI 自动出图 Agent 唯一可见的本地 skill。 |

## 功能边界

当前真实能力包括：

- 首页产品介绍。
- FITS 文件上传、HDU 扫描与选择、头信息、图像尺寸、位深和基础统计。
- 专业分析：独立 OpenAI Agents SDK Sandbox Agent 仅挂载 `deep-sky-advisor`，并将其结构化输出转换为现有分析报告。
- AI 自动出图：独立 Sandbox Agent 仅挂载 `deep-sky-processor`，在任务工作区生成参考图、结果图和处理记录。
- 历史记录：浏览器本地记录分析和处理任务，API 侧保留任务状态、事件和产物。

当前限制：

- 首版仅支持 FITS；XISF、TIFF、PNG/JPG 原图处理尚未纳入主流程。
- AI 自动出图是基于预览图的艺术增强，不是严格可复现的天文线性后期流程，不适合科研测光或真实性验证。
- 任务执行采用单机 SQLite 和串行 worker，适合 4 核 4 GB 级别服务器的早期部署，不适合高并发批处理。
- Agents SDK 的 Sandbox/Skills API 当前锁定在 `0.14.x` beta；每个任务创建独立 session，不共享模型上下文或工作区。

设计依据见
[MVP 设计文档](docs/superpowers/specs/2026-06-11-starun-mvp-design.md)，
部署和维护要求见 [运维说明](docs/operations.md)。

## 开源协议

Starun 使用 [GNU General Public License v3.0](LICENSE)，SPDX 标识为
`GPL-3.0-only`。除另有明确声明的第三方内容外，本仓库代码均按该协议发布。
