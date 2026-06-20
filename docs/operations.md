# Starun 运维说明

## 存储与备份

- 生产环境应将 `STARUN_DATA_ROOT` 设为 `/data`。运行 API 的用户必须拥有
  `/data` 及其子目录，并具有读、写、创建、重命名和删除权限；不要以
  root 运行应用。
- `/data` 必须位于支持 POSIX 目录文件描述符和不跟随符号链接操作的本地
  文件系统。上传、任务和产物路径不得包含符号链接；平台还必须提供
  `shutil.rmtree.avoids_symlink_attacks` 所需的安全递归删除能力。
- 原始上传和任务产物是有时限的工作数据，不应视为长期归档。若需要灾难
  恢复，应一致地备份 SQLite 数据库和 `/data`；备份工具不得改变所有权或
  权限。恢复时必须将数据库与文件恢复到同一快照。
- SQLite 默认 URL 是 `sqlite:///./starun.db`，相对于 API 进程工作目录。
  Docker 镜像的工作目录是 `/app`，因此生产 Compose 应显式设置
  `STARUN_DATABASE_URL=sqlite:////data/starun.db`，使数据库位于持久卷。

## 容量与保留期

- `STARUN_MIN_FREE_DISK_BYTES` 默认 `5368709120`（5 GiB）。上传开始前，
  可用空间必须至少覆盖该保留量加本次上传声明大小。
- 无效上传，以及创建后一小时仍未被任务认领的有效上传，会由后台清理器
  删除。`STARUN_UPLOAD_TTL_SECONDS` 默认 `3600`。
- 任务进入 `completed`、`failed` 或 `cancelled` 终态后，任务文件在 24
  小时后删除，任务记录转为 `expired`。`STARUN_TASK_TTL_SECONDS` 默认
  `86400`；当前执行器的终态保留期同样固定为 24 小时。
- 清理器约每分钟扫描一次，因此实际删除时间可能略晚于到期时间。

## 启动恢复与诊断

- API 启动时，遗留的 `queued`、`running` 或 `cancelling` 任务会标记为
  `failed`，错误码为 `restart_interrupted`，可重试并重新获得 24 小时
  保留期。已进入删除流程的任务继续删除流程。
- 系统错误响应或任务事件可能包含 `diagnostic_id`。使用该完整 ID 搜索 API
  进程或容器日志，例如 `docker compose logs api | rg '<diagnostic_id>'`。
  同时记录任务 ID、时间和请求路径；不要只依赖面向用户的错误文案。

## 部署限制

- 当前支持单个 API 进程、单个 SQLite 数据库和一个应用内串行 worker；
  全局一次只执行一个任务。不要配置多个 Uvicorn worker 或多个 API 副本。
- 当前目标服务器约束为 4 核 CPU、4 GB 内存、无 GPU。这不是通用性能
  基线，也不承诺对 500 MB FITS 执行真实后期处理。
- API 路由限流使用进程内 token bucket。状态不会跨进程共享，因此多
  worker/多副本部署不受支持，也不能提供一致限流。

## Agent Sandbox 与 Skill

- 每个任务创建新的 `UnixLocalSandboxClient` session；任务之间不共享
  session、snapshot、模型上下文或可写文件。
- Analysis Agent 仅装载 `STARUN_ANALYSIS_SKILL_PATH`，Processing Agent
  仅装载 `STARUN_PROCESSING_SKILL_PATH`。
- 容器中的 `/opt/starun-skills` 必须只读。不要把仓库根目录作为 Skills
  source，否则两个 Agent 会看到不属于自己的 skill。
- `STARUN_AGENT_PROTOCOL` 必须显式设为 `responses` 或
  `chat_completions`。系统不会在请求失败后自动切换协议。
- Agents SDK Sandbox/Skills API 当前为 beta，依赖锁定在 `0.14.x`。
  升级该范围前必须重新验证 provider、workspace、取消、产物和页面流程。
- SDK tracing 默认关闭，避免上传 FITS 输入、header 或 skill 输出。
- `UnixLocalSandboxClient` 不是独立虚拟机；shell 命令仍在 API 容器内
  执行。因此 API 必须使用 UID 10001、只读根文件系统、
  `no-new-privileges` 和 `cap_drop: ALL`。当前仅信任仓库内置 skill。
- 旧持久卷若由 root 创建，升级前需要一次性把 `/data` 所有权调整为
  UID/GID 10001，否则 API 无法写入 SQLite 和任务产物。
