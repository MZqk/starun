# Starun MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable Starun vertical slice: a Next.js website and FastAPI service with real FITS inspection, SQLite-backed serial tasks, browser-local history, and a deterministic Mock Agent that produces clearly marked demo reports and artifacts.

**Architecture:** Use a monorepo with `web/` and `api/`. FastAPI owns uploads, SQLite state, the serial in-process worker, FITS inspection, Agent execution, artifacts, quotas, and cleanup; Next.js owns the UI, polling, i18n-ready copy, and IndexedDB history. Keep task execution behind interfaces so it can later move into a separate worker without changing the HTTP contract.

**Tech Stack:** Next.js 16, React, TypeScript, Tailwind CSS 4, Vitest, Testing Library, Playwright, Python 3.12, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Astropy, NumPy, pytest, httpx, Ruff, mypy, SQLite, Docker Compose.

---

## Scope And File Map

This plan implements milestone 1 from `docs/superpowers/specs/2026-06-11-starun-mvp-design.md`. It deliberately excludes real AI providers, real astronomical processing algorithms, XISF, accounts, payment, Redis, Celery, and multi-node execution.

Primary files and responsibilities:

```text
.
├── .env.example
├── .gitignore
├── Makefile
├── compose.yaml
├── web/
│   ├── Dockerfile
│   ├── package.json
│   ├── playwright.config.ts
│   ├── src/app/...
│   ├── src/components/...
│   ├── src/lib/api/...
│   ├── src/lib/history/...
│   └── tests/...
└── api/
    ├── Dockerfile
    ├── pyproject.toml
    ├── alembic.ini
    ├── app/
    │   ├── main.py
    │   ├── config.py
    │   ├── db/...
    │   ├── uploads/...
    │   ├── fits/...
    │   ├── tasks/...
    │   ├── agent/...
    │   ├── artifacts/...
    │   ├── usage/...
    │   └── cleanup/...
    └── tests/...
```

## Phase 1: Runnable Project Foundation

### Task 1: Initialize The Monorepo And Health Checks

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `Makefile`
- Create: `compose.yaml`
- Create: `api/Dockerfile`
- Create: `api/pyproject.toml`
- Create: `api/app/__init__.py`
- Create: `api/app/main.py`
- Create: `api/tests/test_health.py`
- Create: `web/Dockerfile`
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/next.config.ts`
- Create: `web/postcss.config.mjs`
- Create: `web/vitest.config.ts`
- Create: `web/src/test/setup.ts`
- Create: `web/src/app/layout.tsx`
- Create: `web/src/app/page.tsx`
- Create: `web/src/app/globals.css`
- Create: `web/src/app/api-health.test.tsx`

- [ ] **Step 1: Initialize Git before making implementation commits**

Run:

```bash
git init
git branch -M main
```

Expected: an empty repository on branch `main`.

- [ ] **Step 2: Write the failing API health test**

```python
# api/tests/test_health.py
from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 3: Run the API test and verify it fails**

Run:

```bash
cd api && python -m pytest tests/test_health.py -v
```

Expected: FAIL because `app.main` or `/api/health` does not exist.

- [ ] **Step 4: Create the minimal FastAPI application and Python tooling**

```python
# api/app/main.py
from fastapi import FastAPI

app = FastAPI(title="Starun API")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
```

Configure `api/pyproject.toml` with:

```toml
[project]
name = "starun-api"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "alembic>=1.13",
  "astropy>=6.1",
  "fastapi>=0.115",
  "httpx>=0.27",
  "numpy>=2.1",
  "pydantic-settings>=2.5",
  "sqlalchemy>=2.0",
  "uvicorn[standard]>=0.30",
]

[project.optional-dependencies]
dev = ["mypy>=1.11", "pytest>=8.3", "pytest-asyncio>=0.24", "ruff>=0.6"]

[tool.pytest.ini_options]
pythonpath = ["."]

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.12"
strict = true
```

- [ ] **Step 5: Write the failing web landing-page test**

```tsx
// web/src/app/api-health.test.tsx
import { render, screen } from "@testing-library/react";
import HomePage from "./page";

it("shows the two primary product actions", () => {
  render(<HomePage />);
  expect(screen.getByRole("link", { name: "开始分析" })).toHaveAttribute(
    "href",
    "/analysis",
  );
  expect(screen.getByRole("link", { name: "自动出图" })).toHaveAttribute(
    "href",
    "/processing",
  );
});
```

- [ ] **Step 6: Run the web test and verify it fails**

Run:

```bash
cd web && npm test -- --run src/app/api-health.test.tsx
```

Expected: FAIL because the project and page are not configured.

- [ ] **Step 7: Create the minimal Next.js application**

```tsx
// web/src/app/page.tsx
import Link from "next/link";

export default function HomePage() {
  return (
    <main>
      <h1>Starun</h1>
      <p>面向有经验天文摄影爱好者的后期分析与自动出图平台。</p>
      <Link href="/analysis">开始分析</Link>
      <Link href="/processing">自动出图</Link>
    </main>
  );
}
```

Configure `web/package.json` scripts:

```json
{
  "name": "starun-web",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "test": "vitest",
    "test:e2e": "playwright test",
    "lint": "eslint ."
  },
  "dependencies": {
    "@tailwindcss/postcss": "^4",
    "next": "^16",
    "react": "^19",
    "react-dom": "^19",
    "tailwindcss": "^4"
  },
  "devDependencies": {
    "@playwright/test": "^1.50",
    "@testing-library/jest-dom": "^6",
    "@testing-library/react": "^16",
    "@testing-library/user-event": "^14",
    "@types/node": "^22",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "eslint": "^9",
    "eslint-config-next": "^16",
    "fake-indexeddb": "^6",
    "jsdom": "^25",
    "typescript": "^5.7",
    "vitest": "^3"
  }
}
```

- [ ] **Step 8: Add test, CSS, and container configuration**

Configure Vitest with `jsdom` and `web/src/test/setup.ts`; the setup file imports `@testing-library/jest-dom/vitest`. Configure PostCSS with `@tailwindcss/postcss`.

Use these container entry points:

```dockerfile
# api/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```dockerfile
# web/Dockerfile
FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build
CMD ["npm", "run", "start"]
```

- [ ] **Step 9: Add local orchestration**

`compose.yaml` must run:

```yaml
services:
  api:
    build: ./api
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    volumes:
      - starun-data:/data
    ports: ["8000:8000"]
  web:
    build: ./web
    environment:
      NEXT_PUBLIC_API_BASE_URL: http://localhost:8000
    ports: ["3000:3000"]
volumes:
  starun-data:
```

The root `Makefile` must expose `install`, `dev`, `test`, `lint`, and `build`.

Use:

```make
install:
	cd api && python -m pip install -e ".[dev]"
	cd web && npm install

test:
	cd api && python -m pytest
	cd web && npm test -- --run

lint:
	cd api && ruff check . && mypy app
	cd web && npm run lint

build:
	cd web && npm run build
```

- [ ] **Step 10: Run foundation verification**

Run:

```bash
make test
make build
```

Expected: API and web tests PASS; production builds complete.

- [ ] **Step 11: Commit**

```bash
git add .gitignore .env.example Makefile compose.yaml api web
git commit -m "chore: scaffold starun web and api"
```

## Phase 2: Persistent Upload And Task Domain

### Task 2: Add Configuration, SQLite Models, And Migrations

**Files:**
- Create: `api/app/config.py`
- Create: `api/app/db/base.py`
- Create: `api/app/db/session.py`
- Create: `api/app/db/models.py`
- Create: `api/alembic.ini`
- Create: `api/alembic/env.py`
- Create: `api/alembic/versions/0001_initial.py`
- Create: `api/tests/conftest.py`
- Create: `api/tests/db/test_models.py`

- [ ] **Step 1: Write failing persistence tests**

```python
# api/tests/db/test_models.py
from datetime import UTC, datetime

from app.db.models import Task, TaskStatus, TaskType, Upload, UploadStatus


def test_upload_and_task_defaults(db_session) -> None:
    upload = Upload(
        id="upload-1",
        client_id_hash="client",
        ip_hash="ip",
        original_file_name="m31.fits",
        stored_path="/data/uploads/upload-1/input.fits",
        size_bytes=128,
        status=UploadStatus.READY,
        expires_at=datetime.now(UTC),
    )
    task = Task(
        id="task-1",
        type=TaskType.ANALYSIS,
        status=TaskStatus.QUEUED,
        client_id_hash="client",
        ip_hash="ip",
        upload_id=upload.id,
        quota_charged=True,
    )
    db_session.add_all([upload, task])
    db_session.commit()
    assert task.progress == 0
    assert task.retryable is False
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
cd api && python -m pytest tests/db/test_models.py -v
```

Expected: FAIL because models and fixtures do not exist.

- [ ] **Step 3: Implement settings and typed enums**

`Settings` must define:

```python
class Settings(BaseSettings):
    database_url: str = "sqlite:///./starun.db"
    data_root: Path = Path("./data")
    max_upload_bytes: int = 500 * 1024 * 1024
    upload_ttl_seconds: int = 3600
    task_ttl_seconds: int = 86400
    daily_task_limit: int = 5
    analysis_timeout_seconds: int = 600
    processing_timeout_seconds: int = 3600
    min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024
```

Define string enums exactly:

```python
class UploadStatus(StrEnum):
    UPLOADING = "uploading"
    VALIDATING = "validating"
    READY = "ready"
    INVALID = "invalid"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"
```

- [ ] **Step 4: Implement SQLAlchemy models and the initial migration**

Create `Upload`, `Task`, `TaskEvent`, and `DailyUsage` with the fields and indexes in the approved design. Add:

```python
UniqueConstraint("task_id", "sequence", name="uq_task_event_sequence")
UniqueConstraint("date", "client_id_hash", "ip_hash", name="uq_daily_usage_client")
```

Use UTC-aware timestamps at the application boundary and store ISO-compatible UTC values.

- [ ] **Step 5: Add isolated test database fixtures**

`api/tests/conftest.py` must create a temporary SQLite database per test, create all metadata, override the FastAPI database dependency, and provide `db_session`, `client`, and deterministic `headers` fixtures:

```python
@pytest.fixture
def headers() -> dict[str, str]:
    return {"X-Starun-Client-Id": "test-client"}
```

No test may write to the developer's normal `starun.db` or data directory.

- [ ] **Step 6: Run migration and tests**

Run:

```bash
cd api
alembic upgrade head
python -m pytest tests/db/test_models.py -v
```

Expected: migration succeeds; tests PASS.

- [ ] **Step 7: Commit**

```bash
git add api/app/config.py api/app/db api/alembic.ini api/alembic api/tests/conftest.py api/tests/db
git commit -m "feat: add persistent upload and task models"
```

### Task 3: Implement FITS Inspection And Validation

**Files:**
- Create: `api/app/fits/schemas.py`
- Create: `api/app/fits/inspector.py`
- Create: `api/app/fits/errors.py`
- Create: `api/tests/fits/test_inspector.py`
- Create: `api/tests/fixtures/fits_factory.py`

- [ ] **Step 1: Write failing FITS tests**

```python
def test_selects_largest_supported_image_hdu(tmp_path) -> None:
    path = make_fits(
        tmp_path,
        primary=np.zeros((32, 32), dtype=np.uint16),
        extensions=[np.zeros((64, 48), dtype=np.float32)],
    )
    result = FitsInspector().inspect(path)
    assert result.selected_hdu.index == 1
    assert result.selected_hdu.shape == [64, 48]
    assert result.statistics.median == 0.0


def test_rejects_table_only_fits(tmp_path) -> None:
    path = make_table_only_fits(tmp_path)
    with pytest.raises(UnsupportedFitsDataError):
        FitsInspector().inspect(path)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd api && python -m pytest tests/fits/test_inspector.py -v
```

Expected: FAIL because `FitsInspector` does not exist.

- [ ] **Step 3: Implement schemas**

Define:

```python
class HduSummary(BaseModel):
    index: int
    name: str
    kind: str
    shape: list[int] | None
    dtype: str | None
    supported: bool


class BasicStatistics(BaseModel):
    minimum: float
    maximum: float
    mean: float
    median: float
    standard_deviation: float


class FitsInspection(BaseModel):
    hdus: list[HduSummary]
    selected_hdu: HduSummary
    statistics: BasicStatistics
    header: dict[str, str | int | float | bool]
```

- [ ] **Step 4: Implement memory-conscious inspection**

Use `astropy.io.fits.open(path, memmap=True, lazy_load_hdus=True)`. A supported image is:

- a 2D numeric array; or
- a 3D numeric array where one axis has length 3 and can be normalized to RGB.

Select the supported HDU with the largest pixel count. Compute statistics in bounded row chunks and ignore non-finite values. Raise stable domain errors:

```python
invalid_fits
unsupported_fits_data
fits_statistics_failed
```

- [ ] **Step 5: Run FITS tests**

Run:

```bash
cd api && python -m pytest tests/fits/test_inspector.py -v
```

Expected: all FITS tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/app/fits api/tests/fits api/tests/fixtures
git commit -m "feat: inspect supported fits image data"
```

### Task 4: Implement Streaming Uploads

**Files:**
- Create: `api/app/uploads/router.py`
- Create: `api/app/uploads/service.py`
- Create: `api/app/uploads/schemas.py`
- Modify: `api/app/main.py`
- Create: `api/tests/uploads/test_upload_api.py`

- [ ] **Step 1: Write failing upload API tests**

```python
def test_upload_streams_and_returns_ready(client, fits_bytes, headers) -> None:
    response = client.post(
        "/api/uploads",
        files={"file": ("m31.fits", fits_bytes, "application/fits")},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ready"
    assert body["inspection"]["selected_hdu"]["index"] == 0


def test_unsupported_fits_does_not_create_task_or_usage(client, table_fits_bytes, headers) -> None:
    response = client.post(
        "/api/uploads",
        files={"file": ("catalog.fits", table_fits_bytes, "application/fits")},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "unsupported_fits_data"
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd api && python -m pytest tests/uploads/test_upload_api.py -v
```

Expected: FAIL with route not found.

- [ ] **Step 3: Implement the upload service**

`UploadService.create()` must:

1. Validate `.fits`, `.fit`, or `.fts`.
2. Check free disk is at least `min_free_disk_bytes + declared size`.
3. Generate a 128-bit-or-greater URL-safe random upload ID.
4. Write in 1 MiB chunks while enforcing `max_upload_bytes`.
5. Run `FitsInspector`.
6. Persist `ready` or `invalid`.
7. Set unclaimed upload expiry to exactly one hour.
8. Delete partial files on disconnect, validation failure, or exception.

- [ ] **Step 4: Expose `POST /api/uploads`**

Return HTTP 201:

```json
{
  "upload_id": "opaque-id",
  "status": "ready",
  "expires_at": "2026-06-11T10:00:00Z",
  "inspection": {
    "selected_hdu": {"index": 0, "name": "PRIMARY", "shape": [4176, 6248]}
  }
}
```

Use the `X-Starun-Client-Id` header and hash it before persistence. Derive and hash the request IP separately.

- [ ] **Step 5: Run tests**

Run:

```bash
cd api && python -m pytest tests/uploads/test_upload_api.py -v
```

Expected: upload tests PASS without creating daily usage rows.

- [ ] **Step 6: Commit**

```bash
git add api/app/uploads api/app/main.py api/tests/uploads
git commit -m "feat: add validated streaming fits uploads"
```

## Phase 3: Serial Tasks And Agent Skeleton

### Task 5: Add Quotas And Task Creation APIs

**Files:**
- Create: `api/app/usage/service.py`
- Create: `api/app/tasks/schemas.py`
- Create: `api/app/tasks/service.py`
- Create: `api/app/tasks/router.py`
- Modify: `api/app/main.py`
- Create: `api/tests/tasks/test_creation.py`

- [ ] **Step 1: Write failing quota and creation tests**

```python
def test_analysis_creation_claims_upload_and_charges_once(client, ready_upload, headers):
    response = client.post(
        "/api/tasks/analysis",
        json={"upload_id": ready_upload.id},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert response.json()["quota_charged"] is True


def test_sixth_task_is_rejected(client, five_completed_tasks, ready_upload, headers):
    response = client.post(
        "/api/tasks/analysis",
        json={"upload_id": ready_upload.id},
        headers=headers,
    )
    assert response.status_code == 429
    assert response.json()["error_code"] == "daily_task_limit_reached"
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd api && python -m pytest tests/tasks/test_creation.py -v
```

Expected: FAIL because task routes do not exist.

- [ ] **Step 3: Implement atomic quota consumption**

Within one SQLite transaction:

1. Verify upload is `ready`, unexpired, and unclaimed.
2. Read or create the UTC-date `DailyUsage`.
3. Reject when count is 5.
4. Increment count.
5. Create a queued task with a cryptographically random ID.
6. Move the upload expiry to the task lifecycle.

For processing creation, accept exactly one of:

```python
class ProcessingTaskCreate(BaseModel):
    upload_id: str | None = None
    source_task_id: str | None = None
    style: Literal["realistic", "balanced", "artistic"] = "balanced"
```

- [ ] **Step 4: Implement task creation and usage routes**

Add:

```text
POST /api/tasks/analysis
POST /api/tasks/process
GET  /api/usage
```

The processing route must reuse a completed analysis input only while the source task file exists and has not expired.

- [ ] **Step 5: Run tests**

Run:

```bash
cd api && python -m pytest tests/tasks/test_creation.py -v
```

Expected: quota and task creation tests PASS.

- [ ] **Step 6: Commit**

```bash
git add api/app/usage api/app/tasks api/app/main.py api/tests/tasks/test_creation.py
git commit -m "feat: create quota-controlled tasks"
```

### Task 6: Build The Deterministic Agent Core

**Files:**
- Create: `api/app/agent/contracts.py`
- Create: `api/app/agent/mock_model.py`
- Create: `api/app/agent/registry.py`
- Create: `api/app/agent/runner.py`
- Create: `api/app/agent/mock_tools.py`
- Create: `api/app/artifacts/store.py`
- Create: `api/tests/agent/test_runner.py`

- [ ] **Step 1: Write failing Agent tests**

```python
@pytest.mark.asyncio
async def test_mock_agent_is_deterministic(task_context, artifact_store):
    runner = build_mock_runner(artifact_store)
    first = await runner.run(task_context)
    second = await runner.run(task_context)
    assert first.plan == second.plan
    assert first.quality_score == second.quality_score
    assert [item.name for item in first.artifacts] == [
        "result-demo.tiff",
        "preview-demo.png",
        "manifest.json",
    ]


@pytest.mark.asyncio
async def test_registry_rejects_unknown_tool(task_context, artifact_store):
    runner = AgentRunner(
        model=StaticPlanModel(tool_name="shell"),
        registry=ToolRegistry([]),
        artifact_store=artifact_store,
    )
    with pytest.raises(UnknownToolError):
        await runner.run(task_context)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd api && python -m pytest tests/agent/test_runner.py -v
```

Expected: FAIL because Agent contracts do not exist.

- [ ] **Step 3: Define Agent contracts**

Use typed Pydantic models:

```python
class AgentStep(BaseModel):
    id: str
    tool_name: str
    tool_version: str
    arguments: dict[str, JsonValue]


class AgentPlan(BaseModel):
    version: Literal["1"]
    steps: list[AgentStep]
    max_iterations: int = Field(ge=1, le=2)


class Tool(Protocol):
    name: str
    version: str
    input_model: type[BaseModel]

    async def execute(self, context: TaskContext, arguments: BaseModel) -> ToolResult: ...
```

- [ ] **Step 4: Implement deterministic Mock planning and tools**

Seed Mock values from `sha256(task_id + style)`. Register only:

```text
mock.inspect
mock.stretch
mock.denoise
mock.sharpen
mock.color
mock.evaluate
mock.export
```

`mock.export` creates a valid, clearly watermarked TIFF and PNG plus `manifest.json`. It must never present the output as scientifically processed.

- [ ] **Step 5: Implement runner guardrails**

The runner must:

- reject unknown tool name or version;
- validate every tool input;
- cap steps at 12 and iterations at 2;
- check cancellation before and after every tool;
- write events for plan, tool start, tool finish, evaluation, and completion;
- limit all artifact paths to the current task directory.

- [ ] **Step 6: Run tests**

Run:

```bash
cd api && python -m pytest tests/agent/test_runner.py -v
```

Expected: Agent tests PASS and artifact bytes are deterministic.

- [ ] **Step 7: Commit**

```bash
git add api/app/agent api/app/artifacts api/tests/agent
git commit -m "feat: add deterministic mock agent"
```

### Task 7: Add The Serial Worker And Task Lifecycle

**Files:**
- Create: `api/app/tasks/executor.py`
- Create: `api/app/tasks/handlers.py`
- Create: `api/app/tasks/events.py`
- Create: `api/app/tasks/recovery.py`
- Modify: `api/app/main.py`
- Create: `api/tests/tasks/test_executor.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
@pytest.mark.asyncio
async def test_executor_runs_only_one_task_at_a_time(executor, two_queued_tasks):
    await executor.run_until_idle()
    assert executor.max_observed_concurrency == 1
    assert all(task.status == TaskStatus.COMPLETED for task in two_queued_tasks)


def test_restart_marks_unfinished_tasks_failed(db_session):
    seed_tasks(db_session, statuses=["queued", "running", "cancelling"])
    mark_interrupted_tasks_failed(db_session)
    assert {task.error_code for task in all_tasks(db_session)} == {
        "restart_interrupted"
    }
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd api && python -m pytest tests/tasks/test_executor.py -v
```

Expected: FAIL because executor and recovery are absent.

- [ ] **Step 3: Implement task handlers**

Analysis handler:

- load persisted FITS inspection;
- generate deterministic Mock professional metrics and recommendations;
- write `analysis-report.json`;
- emit events.

Processing handler:

- create `TaskContext`;
- invoke `AgentRunner`;
- persist artifact manifest;
- emit events.

- [ ] **Step 4: Implement serial execution and timeouts**

Use one application-owned `asyncio` worker loop and SQLite leasing:

```python
async def execute_one(task: Task) -> None:
    timeout = (
        settings.analysis_timeout_seconds
        if task.type is TaskType.ANALYSIS
        else settings.processing_timeout_seconds
    )
    async with asyncio.timeout(timeout):
        await handlers[task.type](task)
```

No more than one task may be `running`. A cancellation request changes the status to `cancelling`; handlers and Agent tools check it at safe points.

- [ ] **Step 5: Add startup recovery and shutdown**

On startup:

1. mark `queued`, `running`, and `cancelling` as `failed/restart_interrupted`;
2. start the serial worker;
3. start cleanup scheduling.

On shutdown, stop accepting new work and cancel only the worker loop, leaving the current task to be marked interrupted on next startup.

- [ ] **Step 6: Run lifecycle tests**

Run:

```bash
cd api && python -m pytest tests/tasks/test_executor.py -v
```

Expected: serial execution, timeout, cancellation, and recovery tests PASS.

- [ ] **Step 7: Commit**

```bash
git add api/app/tasks api/app/main.py api/tests/tasks/test_executor.py
git commit -m "feat: execute tasks serially"
```

### Task 8: Expose Polling, Events, Cancellation, Retry, Delete, And Downloads

**Files:**
- Modify: `api/app/tasks/router.py`
- Create: `api/app/artifacts/router.py`
- Create: `api/app/errors.py`
- Modify: `api/app/main.py`
- Create: `api/tests/tasks/test_task_api.py`

- [ ] **Step 1: Write failing task API tests**

```python
def test_events_are_incremental(client, completed_task, headers):
    response = client.get(
        f"/api/tasks/{completed_task.id}/events?after=2",
        headers=headers,
    )
    assert response.status_code == 200
    assert all(event["sequence"] > 2 for event in response.json()["events"])


def test_system_failure_retry_is_free_once(client, failed_system_task, headers):
    first = client.post(f"/api/tasks/{failed_system_task.id}/retry", headers=headers)
    second = client.post(f"/api/tasks/{failed_system_task.id}/retry", headers=headers)
    assert first.json()["quota_charged"] is False
    assert second.json()["quota_charged"] is True
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd api && python -m pytest tests/tasks/test_task_api.py -v
```

Expected: FAIL with missing endpoints.

- [ ] **Step 3: Implement the remaining API contract**

Add:

```text
GET    /api/tasks/{id}
GET    /api/tasks/{id}/events?after={sequence}
POST   /api/tasks/{id}/cancel
POST   /api/tasks/{id}/retry
DELETE /api/tasks/{id}
GET    /api/tasks/{id}/artifacts/{name}
```

Rules:

- no endpoint lists all tasks;
- artifact names must be selected from the persisted manifest;
- `DELETE` removes files, sets status to `expired`, and stores `error_code=user_deleted` in the minimal audit row;
- free retry applies once to charged tasks failed by restart, resource, or system errors;
- expired or missing input returns `source_file_expired`;
- all error bodies use `error_code`, `message`, `retryable`, `quota_charged`, and optional `diagnostic_id`.

- [ ] **Step 4: Run API tests**

Run:

```bash
cd api && python -m pytest tests/tasks/test_task_api.py -v
```

Expected: task API tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/app/tasks api/app/artifacts api/app/errors.py api/app/main.py api/tests/tasks
git commit -m "feat: expose task lifecycle api"
```

## Phase 4: Product UI And Browser History

### Task 9: Build The Shared Design System, Navigation, And Home Page

**Files:**
- Modify: `web/src/app/globals.css`
- Modify: `web/src/app/layout.tsx`
- Modify: `web/src/app/page.tsx`
- Create: `web/src/components/NavBar.tsx`
- Create: `web/src/components/FeatureCard.tsx`
- Create: `web/src/components/MockNotice.tsx`
- Create: `web/src/lib/i18n/zh-CN.ts`
- Create: `web/tests/home.test.tsx`

- [ ] **Step 1: Write failing home tests**

```tsx
it("states the input and retention limits", () => {
  render(<HomePage />);
  expect(screen.getByText(/FITS/)).toBeInTheDocument();
  expect(screen.getByText(/500 MB/)).toBeInTheDocument();
  expect(screen.getByText(/24 小时/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd web && npm test -- --run tests/home.test.tsx
```

Expected: FAIL because the approved landing page is not implemented.

- [ ] **Step 3: Implement tokens and shared components**

Move all Chinese user-facing copy into `src/lib/i18n/zh-CN.ts`. Implement the approved near-black background, warm text, red accent, focus rings, reduced-motion behavior, and responsive navigation.

- [ ] **Step 4: Implement the home page**

Include:

- product positioning for experienced users;
- “开始分析” and “自动出图” CTAs;
- FITS-only, 500 MB, 24-hour retention, and local-history notices;
- no fabricated usage statistics;
- desktop and mobile layouts.

- [ ] **Step 5: Run tests and build**

Run:

```bash
cd web
npm test -- --run tests/home.test.tsx
npm run build
```

Expected: tests PASS and build succeeds.

- [ ] **Step 6: Commit**

```bash
git add web/src/app web/src/components web/src/lib/i18n web/tests/home.test.tsx
git commit -m "feat: build starun landing experience"
```

### Task 10: Add API Client, Anonymous Identity, And IndexedDB History

**Files:**
- Create: `web/src/lib/api/client.ts`
- Create: `web/src/lib/api/types.ts`
- Create: `web/src/lib/history/db.ts`
- Create: `web/src/lib/history/repository.ts`
- Create: `web/src/lib/client-id.ts`
- Create: `web/tests/history.test.ts`

- [ ] **Step 1: Write failing history tests**

```ts
it("stores and updates a task summary without storing files", async () => {
  const repository = createHistoryRepository(fakeIndexedDb);
  await repository.upsert({
    taskId: "task-1",
    type: "analysis",
    fileName: "m31.fits",
    lastStatus: "queued",
    createdAt: "2026-06-11T00:00:00Z",
    expiresAt: "2026-06-12T00:00:00Z",
    resultAvailable: false,
  });
  expect(await repository.get("task-1")).toMatchObject({
    lastStatus: "queued",
  });
});
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd web && npm test -- --run tests/history.test.ts
```

Expected: FAIL because history modules do not exist.

- [ ] **Step 3: Implement typed API models**

Define TypeScript unions that exactly match API enums:

```ts
export type TaskStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed"
  | "expired";

export type ProcessingStyle = "realistic" | "balanced" | "artistic";
```

Every request must send `X-Starun-Client-Id`, generated once with `crypto.randomUUID()` and stored in IndexedDB settings.

- [ ] **Step 4: Implement IndexedDB repository**

Use database `starun`, version 1, stores `task_history` and `client_settings`. Store summaries only; reject values containing `Blob`, `File`, or `ArrayBuffer`.

- [ ] **Step 5: Run tests**

Run:

```bash
cd web && npm test -- --run tests/history.test.ts
```

Expected: history and client ID tests PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/lib web/tests/history.test.ts
git commit -m "feat: add anonymous client history"
```

### Task 11: Build Upload, Analysis, Processing, And History Pages

**Files:**
- Create: `web/src/components/UploadZone.tsx`
- Create: `web/src/components/TaskStatusPanel.tsx`
- Create: `web/src/components/TaskEventLog.tsx`
- Create: `web/src/components/ArtifactDownloads.tsx`
- Create: `web/src/hooks/useTaskPolling.ts`
- Create: `web/src/app/analysis/page.tsx`
- Create: `web/src/app/processing/page.tsx`
- Create: `web/src/app/history/page.tsx`
- Create: `web/tests/flows.test.tsx`

- [ ] **Step 1: Write failing flow tests**

```tsx
it("creates an analysis task after a ready upload", async () => {
  render(<AnalysisPage />);
  await user.upload(screen.getByLabelText("选择 FITS 文件"), fitsFile);
  await screen.findByText("HDU 0");
  await user.click(screen.getByRole("button", { name: "开始分析" }));
  expect(await screen.findByText("排队中")).toBeInTheDocument();
});


it("requires one processing style and defaults to balanced", () => {
  render(<ProcessingPage />);
  expect(screen.getByRole("radio", { name: "平衡" })).toBeChecked();
});
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
cd web && npm test -- --run tests/flows.test.tsx
```

Expected: FAIL because pages and components do not exist.

- [ ] **Step 3: Implement UploadZone**

Support:

- `.fits,.fit,.fts`;
- client-side 500 MB precheck;
- byte progress through `XMLHttpRequest.upload`;
- upload cancellation;
- server validation summary;
- explicit notice that refresh interrupts upload;
- no quota charge until task creation.

- [ ] **Step 4: Implement task polling**

`useTaskPolling(taskId)` must:

- poll running tasks every 2 seconds;
- poll queued tasks every 5 seconds after the first 30 seconds;
- fetch events with the last sequence;
- stop on terminal state or unmount;
- persist every status change to IndexedDB;
- resume when a history item is opened.

- [ ] **Step 5: Implement analysis page**

Separate:

- real FITS/HDU/basic statistics;
- persistent Mock notice;
- Mock professional metrics;
- Mock recommendations;
- “使用此文件自动出图” action while source is valid.

- [ ] **Step 6: Implement processing page**

Include:

- style selector with `balanced` default;
- Agent plan and event log;
- queue/running/cancelling states;
- cancellation;
- before/demo-after comparison;
- TIFF and PNG download;
- persistent Mock labels.

- [ ] **Step 7: Implement history page**

Read only IndexedDB summaries. Provide:

- open/resume;
- retry when allowed;
- delete;
- expired/result unavailable state;
- local-history durability notice.

- [ ] **Step 8: Run component tests and build**

Run:

```bash
cd web
npm test -- --run tests/flows.test.tsx
npm run build
```

Expected: flow tests PASS and build succeeds.

- [ ] **Step 9: Commit**

```bash
git add web/src/components web/src/hooks web/src/app/analysis web/src/app/processing web/src/app/history web/tests/flows.test.tsx
git commit -m "feat: add upload analysis and processing flows"
```

## Phase 5: Cleanup, Security Controls, And Acceptance

### Task 12: Add Expiration, Disk Guard, Cleanup, And Rate Limits

**Files:**
- Create: `api/app/cleanup/service.py`
- Create: `api/app/security/rate_limit.py`
- Modify: `api/app/uploads/service.py`
- Modify: `api/app/tasks/router.py`
- Create: `api/tests/cleanup/test_cleanup.py`
- Create: `api/tests/security/test_rate_limits.py`

- [ ] **Step 1: Write failing cleanup tests**

```python
def test_cleanup_expires_task_and_removes_files(cleanup_service, expired_task):
    cleanup_service.run_once()
    assert expired_task.status == TaskStatus.EXPIRED
    assert not expired_task.directory.exists()


def test_unclaimed_upload_expires_after_one_hour(cleanup_service, stale_upload):
    cleanup_service.run_once()
    assert get_upload(stale_upload.id) is None
    assert not stale_upload.path.exists()
```

- [ ] **Step 2: Write failing rate-limit tests**

```python
def test_task_lookup_rate_limit(client, task, headers):
    for _ in range(60):
        assert client.get(f"/api/tasks/{task.id}", headers=headers).status_code == 200
    assert client.get(f"/api/tasks/{task.id}", headers=headers).status_code == 429
```

- [ ] **Step 3: Run and verify failure**

Run:

```bash
cd api && python -m pytest tests/cleanup tests/security -v
```

Expected: FAIL because cleanup and rate limiting are absent.

- [ ] **Step 4: Implement idempotent cleanup**

Every minute:

- delete invalid and unclaimed uploads after one hour;
- delete task directories exactly 24 hours after terminal state;
- set terminal tasks to `expired`;
- tolerate missing directories and repeated execution;
- keep only the minimal expired task row needed for status explanations.

- [ ] **Step 5: Implement disk and request controls**

- Reject upload before writing when free space is below `min_free_disk_bytes`.
- Recheck disk after each upload chunk.
- Apply in-memory per-process token buckets to lookup, events, cancel, retry, delete, and artifact routes.
- Do not expose an endpoint that lists task IDs.
- Verify all artifact paths resolve under the task directory.

- [ ] **Step 6: Run tests**

Run:

```bash
cd api && python -m pytest tests/cleanup tests/security -v
```

Expected: cleanup, disk guard, path, and rate-limit tests PASS.

- [ ] **Step 7: Commit**

```bash
git add api/app/cleanup api/app/security api/app/uploads api/app/tasks api/tests/cleanup api/tests/security
git commit -m "feat: enforce task lifecycle controls"
```

### Task 13: Add End-To-End Acceptance Tests

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/analysis.spec.ts`
- Create: `web/e2e/processing.spec.ts`
- Create: `web/e2e/history.spec.ts`
- Create: `web/e2e/mobile.spec.ts`
- Create: `api/tests/resources/test_large_upload.py`
- Create: `api/tests/resources/test_restart.py`

- [ ] **Step 1: Add a deterministic test FITS fixture**

Generate a small FITS in test setup with a primary 2D image and a larger image extension. Expected selected HDU is extension 1.

- [ ] **Step 2: Write the analysis E2E test**

```ts
test("upload, analyze, and reuse the file", async ({ page }) => {
  await page.goto("/analysis");
  await page.getByLabel("选择 FITS 文件").setInputFiles("e2e/fixtures/m31.fits");
  await expect(page.getByText("HDU 1")).toBeVisible();
  await page.getByRole("button", { name: "开始分析" }).click();
  await expect(page.getByText("Mock 专业指标")).toBeVisible();
  await page.getByRole("button", { name: "使用此文件自动出图" }).click();
  await expect(page).toHaveURL(/processing/);
});
```

- [ ] **Step 3: Write processing, history, and mobile E2E tests**

Cover:

- balanced is the default style;
- Agent events appear incrementally;
- cancel reaches `cancelled`;
- TIFF and PNG links appear on completion;
- refresh and history resume polling;
- expired result remains as local summary;
- mobile can browse home, report, history, and status.

- [ ] **Step 4: Add resource and restart tests**

`test_large_upload.py` must stream a sparse near-limit request and assert bounded process memory rather than reading the complete body at once.

`test_restart.py` must seed queued/running/cancelling tasks, invoke startup recovery, and assert `restart_interrupted`.

- [ ] **Step 5: Run full acceptance**

Run:

```bash
make lint
make test
make build
cd web && npm run test:e2e
```

Expected:

- Ruff and mypy PASS.
- API and web unit/integration tests PASS.
- Next.js production build succeeds.
- Playwright desktop projects PASS for Chromium, Firefox, and WebKit.
- Mobile viewport tests PASS.

- [ ] **Step 6: Commit**

```bash
git add web/playwright.config.ts web/e2e api/tests/resources
git commit -m "test: cover starun milestone acceptance"
```

### Task 14: Document Local Operation And Milestone Limits

**Files:**
- Create: `README.md`
- Create: `docs/operations.md`
- Modify: `.env.example`
- Modify: `STARUN-PRD.md`

- [ ] **Step 1: Document exact local commands**

`README.md` must include:

```bash
make install
make dev
make test
make lint
make build
docker compose up --build
```

Also state that milestone 1 uses Mock professional metrics and Mock output artifacts.

- [ ] **Step 2: Document server operations**

`docs/operations.md` must define:

- `/data` directory ownership and backup expectations;
- SQLite location;
- minimum free disk setting;
- one-hour unclaimed upload cleanup;
- 24-hour terminal task cleanup;
- how startup marks interrupted tasks;
- log and diagnostic ID lookup;
- current 4-core/4-GB/no-GPU limitation.

- [ ] **Step 3: Reconcile the original PRD**

Update `STARUN-PRD.md` to:

- link to the approved design;
- remove `output: "export"` as the full-system deployment assumption;
- replace SSE with HTTP polling;
- mark current API results as Mock where applicable;
- keep visual tokens and component specifications intact.

- [ ] **Step 4: Run final verification**

Run:

```bash
make lint
make test
make build
git status --short
```

Expected: all verification passes; only intended documentation changes remain.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/operations.md .env.example STARUN-PRD.md
git commit -m "docs: add starun operations guide"
```

## Final Milestone Verification

- [ ] Run:

```bash
make lint
make test
make build
cd web && npm run test:e2e
```

- [ ] Confirm manually:

```text
1. Valid FITS upload does not consume quota.
2. Unsupported FITS is rejected without quota charge.
3. Creating analysis or processing consumes one shared daily slot.
4. Only one task runs at a time.
5. Mock labels remain visible in reports and downloads.
6. Analysis input can be reused for processing before expiry.
7. Cancel, retry, delete, restart failure, and expiry are explainable.
8. No API lists anonymous task IDs.
9. History persists only in the current browser.
10. All terminal task files are removed exactly 24 hours later.
```

- [ ] Create the milestone completion commit only when the working tree is clean and all checks pass:

```bash
git status --short
git log --oneline --decorate -14
```

## Spec Coverage Map

| Approved requirement | Implemented by |
|---|---|
| Four product pages and deep-space design | Tasks 9 and 11 |
| FITS-only, 500 MB, HDU selection, real basic statistics | Tasks 3 and 4 |
| Analysis report with visibly Mock professional metrics | Tasks 7 and 11 |
| Unattended three-style Mock Agent | Tasks 6, 7, and 11 |
| TIFF plus PNG demo artifacts | Tasks 6, 8, and 11 |
| Browser-only IndexedDB history | Tasks 10 and 11 |
| SQLite state and one serial task | Tasks 2, 5, and 7 |
| Polling and incremental events | Tasks 8 and 11 |
| Cancel, retry, delete, restart interruption | Tasks 7, 8, and 13 |
| Five shared daily tasks | Tasks 5 and 8 |
| One-hour upload and 24-hour task retention | Task 12 |
| Random opaque IDs, no task listing, path and rate controls | Tasks 4, 8, and 12 |
| 4-core/4-GB resource constraints | Tasks 3, 4, 7, 12, and 13 |
| Chinese-first, i18n-ready copy | Tasks 9 and 11 |
| Desktop browsers and mobile browsing flow | Task 13 |
| Operations and PRD reconciliation | Task 14 |
