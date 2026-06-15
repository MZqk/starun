# Starun

Starun 是面向天文摄影爱好者的 FITS 分析与后期流程演示平台。Next.js
负责页面、HTTP 轮询和浏览器本地历史；FastAPI 负责流式上传、FITS 检查、
SQLite 状态、串行任务执行、产物下载与清理。

## 本地开发

需要 Docker、Node.js/npm、Python 3.12 和
[uv](https://docs.astral.sh/uv/)。

```bash
make install
make dev
make test
make lint
make build
```

`make dev` 等同于：

```bash
docker compose up --build
```

启动后 Web 默认位于 `http://localhost:3000`，API 位于
`http://localhost:8000`。

## Milestone 1 能力边界

真实能力包括 FITS 文件验证、HDU 扫描与选择、头信息、图像尺寸、位深和
基础统计。当前专业指标（包括 SNR、FWHM 和椭圆率）、Agent 计划与结果、
处理预览以及可下载产物均为明确标识的确定性 Mock，不代表真实天文图像
分析或处理结果。

设计依据见
[MVP 设计文档](docs/superpowers/specs/2026-06-11-starun-mvp-design.md)，
部署和维护要求见 [运维说明](docs/operations.md)。
