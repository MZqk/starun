import hashlib
import json
import math
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.agent import build_mock_runner
from app.agent.contracts import (
    ArtifactManifestEntry,
    AgentPlan,
    AgentStep,
    TaskContext,
    ToolResult,
)
from app.agent.mock_tools import ExportArguments, StrengthArguments, build_mock_tools
from app.agent.registry import DuplicateToolError, ToolRegistry, UnknownToolError
from app.agent.runner import (
    AgentCancelledError,
    AgentGuardrailError,
    AgentOutputError,
    AgentRunner,
    InvalidToolArgumentsError,
)
from app.artifacts.contracts import MAX_ARTIFACT_BYTES
from app.artifacts.store import (
    ArtifactPathError,
    ArtifactSizeError,
    ArtifactStore,
    UnsupportedArtifactError,
)
from app.db.models import ProcessingStyle, TaskType
from app.fits.schemas import BasicStatistics, FitsInspection, HduSummary
from PIL import Image
import numpy as np
from astropy.io import fits

EXPECTED_TOOLS = {
    ("mock.inspect", "v1"),
    ("mock.stretch", "v1"),
    ("mock.denoise", "v1"),
    ("mock.sharpen", "v1"),
    ("mock.color", "v1"),
    ("mock.evaluate", "v1"),
    ("mock.export", "v1"),
}
EXPECTED_ARTIFACTS = ["result-demo.tiff", "preview-demo.png", "manifest.json"]


@pytest.fixture
def fits_inspection() -> FitsInspection:
    selected = HduSummary(
        index=0,
        name="PRIMARY",
        kind="primary_image",
        shape=[32, 48],
        dtype="float32",
        supported=True,
    )
    return FitsInspection(
        hdus=[selected],
        selected_hdu=selected,
        statistics=BasicStatistics(
            minimum=0.0,
            maximum=1.0,
            mean=0.4,
            median=0.35,
            standard_deviation=0.2,
            finite_pixel_count=1536,
        ),
        header={"OBJECT": "DEMO"},
    )


def make_context(
    root: Path,
    inspection: FitsInspection,
    *,
    task_id: str = "task-001",
    style: ProcessingStyle = ProcessingStyle.BALANCED,
    cancellation_check: Callable[[], bool] = lambda: False,
) -> TaskContext:
    task_dir = root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    source_path = task_dir / "source.fits"
    data = np.zeros((32, 48), dtype=np.float32)
    fits.PrimaryHDU(data=data).writeto(source_path, overwrite=True)
    return TaskContext(
        task_id=task_id,
        task_type=TaskType.PROCESSING,
        style=style,
        task_dir=task_dir,
        source_path=source_path,
        fits_inspection=inspection,
        basic_metadata={"source_kind": "bounded-demo"},
        cancellation_check=cancellation_check,
    )


def build_runner(context: TaskContext) -> AgentRunner:
    store = ArtifactStore(context.task_dir)
    return build_mock_runner(store)


@pytest.mark.asyncio
async def test_mock_agent_default_has_no_step_delay(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = make_context(tmp_path, fits_inspection)
    sleep_calls: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("app.agent.runner.asyncio.sleep", record_sleep)

    await build_runner(context).run(context)

    assert sleep_calls == []


@pytest.mark.asyncio
async def test_configured_step_delay_is_cancellation_responsive(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = False
    sleep_calls: list[float] = []
    context = make_context(
        tmp_path,
        fits_inspection,
        cancellation_check=lambda: cancelled,
    )
    runner = build_mock_runner(
        ArtifactStore(context.task_dir),
        step_delay_seconds=0.2,
    )

    async def cancel_after_checkpoint(seconds: float) -> None:
        nonlocal cancelled
        sleep_calls.append(seconds)
        cancelled = True

    monkeypatch.setattr(
        "app.agent.runner.asyncio.sleep",
        cancel_after_checkpoint,
    )

    with pytest.raises(AgentCancelledError, match="agent_run_cancelled"):
        await runner.run(context)
    assert sleep_calls == [0.05]
    assert sum(sleep_calls) < 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_event_sink_receives_each_event_immediately_and_preserves_result(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    asynchronous: bool,
) -> None:
    context = make_context(tmp_path, fits_inspection)
    received: list[str] = []

    async def async_sink(event: Any) -> None:
        received.append(event.event_type)

    def sync_sink(event: Any) -> None:
        received.append(event.event_type)

    runner = build_mock_runner(
        ArtifactStore(context.task_dir),
        event_sink=async_sink if asynchronous else sync_sink,
    )

    result = await runner.run(context)

    expected = [event.event_type for event in result.events]
    assert received == expected
    assert received[:3] == ["plan", "tool_started", "tool_finished"]
    assert received[-2:] == ["evaluation", "completion"]


@pytest.mark.asyncio
async def test_event_sink_failure_stops_agent_consistently(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    context = make_context(tmp_path, fits_inspection)

    def failing_sink(event: Any) -> None:
        if event.event_type == "tool_started":
            raise OSError("event store unavailable")

    runner = build_mock_runner(
        ArtifactStore(context.task_dir),
        event_sink=failing_sink,
    )

    with pytest.raises(OSError, match="event store unavailable"):
        await runner.run(context)
    assert not (context.task_dir / "result-demo.tiff").exists()


@pytest.mark.asyncio
async def test_mock_agent_is_byte_deterministic(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    first_context = make_context(tmp_path / "first", fits_inspection)
    second_context = make_context(tmp_path / "second", fits_inspection)

    first = await build_runner(first_context).run(first_context)
    second = await build_runner(second_context).run(second_context)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.plan == second.plan
    assert first.quality_score == second.quality_score
    assert [artifact.name for artifact in first.artifacts] == EXPECTED_ARTIFACTS
    assert [artifact.model_dump() for artifact in first.artifacts] == [
        artifact.model_dump() for artifact in second.artifacts
    ]
    for name in EXPECTED_ARTIFACTS:
        assert (first_context.task_dir / name).read_bytes() == (
            second_context.task_dir / name
        ).read_bytes()
    first_timestamps = [event.timestamp for event in first.events]
    second_timestamps = [event.timestamp for event in second.events]
    assert all(timestamp is not None for timestamp in first_timestamps)
    assert first_timestamps == second_timestamps
    assert first_timestamps == sorted(first_timestamps)
    assert len(set(first_timestamps)) == len(first_timestamps)
    assert all(
        timestamp is not None and timestamp.utcoffset().total_seconds() == 0
        for timestamp in first_timestamps
    )


@pytest.mark.asyncio
async def test_exported_images_are_valid_and_visibly_watermarked(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    context = make_context(tmp_path, fits_inspection)

    await build_runner(context).run(context)

    for name, expected_format in (
        ("result-demo.tiff", "TIFF"),
        ("preview-demo.png", "PNG"),
    ):
        with Image.open(context.task_dir / name) as image:
            image.load()
            assert image.format == expected_format
            assert image.size == (640, 360)
            if expected_format == "PNG":
                assert image.info["Description"] == "STARUN MOCK / 演示结果"
            else:
                assert image.tag_v2[270] == "STARUN MOCK / DEMO RESULT"
                assert 700 not in image.tag_v2
            watermark_area = image.convert("RGB").crop((110, 275, 530, 345))
            bright_pixels = sum(
                1
                for pixel in watermark_area.get_flattened_data()
                if min(pixel) >= 220
            )
            assert bright_pixels > 1_000


@pytest.mark.asyncio
async def test_manifest_entries_are_correct_and_deterministic(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    context = make_context(tmp_path, fits_inspection)

    result = await build_runner(context).run(context)

    manifest_bytes = (context.task_dir / "manifest.json").read_bytes()
    assert manifest_bytes.endswith(b"\n")
    manifest = json.loads(manifest_bytes)
    assert manifest == {
        "artifacts": [artifact.model_dump() for artifact in result.artifacts[:2]],
        "demo": True,
        "notice": "STARUN MOCK / 演示结果",
    }
    for entry in result.artifacts:
        data = (context.task_dir / entry.name).read_bytes()
        assert entry.size == len(data)
        assert entry.sha256 == hashlib.sha256(data).hexdigest()
        assert entry.demo is True


@pytest.mark.asyncio
@pytest.mark.parametrize("style", list(ProcessingStyle))
async def test_all_styles_produce_allowed_valid_plans(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    style: ProcessingStyle,
) -> None:
    context = make_context(tmp_path, fits_inspection, style=style)

    result = await build_runner(context).run(context)

    assert 1 <= result.plan.max_iterations <= 2
    assert len(result.plan.steps) <= 12
    assert {(step.tool_name, step.tool_version) for step in result.plan.steps} <= EXPECTED_TOOLS
    assert result.plan.steps[-1].tool_name == "mock.export"


def test_registry_rejects_unknown_versions_and_duplicate_registration() -> None:
    registry = ToolRegistry(build_mock_tools(ArtifactStore(Path.cwd())))
    with pytest.raises(UnknownToolError, match="mock.inspect@v2"):
        registry.resolve("mock.inspect", "v2")
    with pytest.raises(UnknownToolError, match="shell@v1"):
        registry.resolve("shell", "v1")
    tools = build_mock_tools(ArtifactStore(Path.cwd()))
    with pytest.raises(DuplicateToolError, match="mock.inspect@v1"):
        ToolRegistry([tools[0], tools[0]])


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordingTool:
    name: ClassVar[str] = "test.record"
    version: ClassVar[str] = "v1"
    input_model: ClassVar[type[BaseModel]] = EmptyArguments

    def __init__(
        self,
        calls: list[str],
        *,
        after_execute: Callable[[], None] = lambda: None,
    ) -> None:
        self.calls = calls
        self.after_execute = after_execute

    async def execute(
        self,
        context: TaskContext,
        arguments: BaseModel,
    ) -> ToolResult:
        del context, arguments
        self.calls.append("execute")
        self.after_execute()
        return ToolResult(observations={"status": "ok"})


class StaticModel:
    def __init__(
        self,
        plan: AgentPlan | dict[str, Any],
        *,
        after_plan: Callable[[], None] = lambda: None,
        after_evaluate: Callable[[], None] = lambda: None,
        quality_score: float = 0.5,
    ) -> None:
        self._plan = plan
        self._after_plan = after_plan
        self._after_evaluate = after_evaluate
        self._quality_score = quality_score

    async def plan(self, context: TaskContext) -> AgentPlan | dict[str, Any]:
        del context
        self._after_plan()
        return self._plan

    async def evaluate(self, observation: ToolResult) -> float:
        del observation
        self._after_evaluate()
        return self._quality_score

    async def summarize(self, context: TaskContext, result: ToolResult) -> dict[str, Any]:
        del context, result
        return {"demo": True}


@pytest.mark.asyncio
async def test_invalid_tool_arguments_are_rejected_before_execute(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    calls: list[str] = []
    tool = RecordingTool(calls)
    plan = AgentPlan(
        version="1",
        max_iterations=1,
        steps=[
            AgentStep(
                id="step-1",
                tool_name=tool.name,
                tool_version=tool.version,
                arguments={"command": "do not run"},
            )
        ],
    )
    context = make_context(tmp_path, fits_inspection)
    runner = AgentRunner(
        model=StaticModel(plan),
        registry=ToolRegistry([tool]),
        artifact_store=ArtifactStore(context.task_dir),
    )

    with pytest.raises(InvalidToolArgumentsError):
        await runner.run(context)

    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("step_count", "max_iterations"),
    [(13, 1), (1, 3)],
)
async def test_runner_enforces_step_and_iteration_limits(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    step_count: int,
    max_iterations: int,
) -> None:
    tool = RecordingTool([])
    raw_plan = {
        "version": "1",
        "max_iterations": max_iterations,
        "steps": [
            {
                "id": f"step-{index}",
                "tool_name": tool.name,
                "tool_version": tool.version,
                "arguments": {},
            }
            for index in range(step_count)
        ],
    }
    context = make_context(tmp_path, fits_inspection)
    runner = AgentRunner(
        model=StaticModel(raw_plan),
        registry=ToolRegistry([tool]),
        artifact_store=ArtifactStore(context.task_dir),
    )

    with pytest.raises(AgentGuardrailError):
        await runner.run(context)


@pytest.mark.asyncio
async def test_cancellation_is_checked_before_tool_execution(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    calls: list[str] = []
    context = make_context(
        tmp_path,
        fits_inspection,
        cancellation_check=lambda: True,
    )
    tool = RecordingTool(calls)
    runner = AgentRunner(
        model=StaticModel(
            AgentPlan(
                version="1",
                max_iterations=1,
                steps=[
                    AgentStep(
                        id="step-1",
                        tool_name=tool.name,
                        tool_version=tool.version,
                        arguments={},
                    )
                ],
            )
        ),
        registry=ToolRegistry([tool]),
        artifact_store=ArtifactStore(context.task_dir),
    )

    with pytest.raises(AgentCancelledError, match="agent_run_cancelled"):
        await runner.run(context)

    assert calls == []


@pytest.mark.asyncio
async def test_cancellation_is_checked_after_tool_execution(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    cancelled = False

    def request_cancellation() -> None:
        nonlocal cancelled
        cancelled = True

    context = make_context(
        tmp_path,
        fits_inspection,
        cancellation_check=lambda: cancelled,
    )
    calls: list[str] = []
    tool = RecordingTool(calls, after_execute=request_cancellation)
    runner = AgentRunner(
        model=StaticModel(
            AgentPlan(
                version="1",
                max_iterations=1,
                steps=[
                    AgentStep(
                        id="step-1",
                        tool_name=tool.name,
                        tool_version=tool.version,
                        arguments={},
                    )
                ],
            )
        ),
        registry=ToolRegistry([tool]),
        artifact_store=ArtifactStore(context.task_dir),
    )

    with pytest.raises(AgentCancelledError, match="agent_run_cancelled"):
        await runner.run(context)

    assert calls == ["execute"]


def test_artifact_store_rejects_traversal_and_symlink_escape(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ArtifactStore(task_dir)

    with pytest.raises(ArtifactPathError):
        store.write_bytes("../outside.txt", b"no")
    (task_dir / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactPathError):
        store.write_bytes("escape/outside.txt", b"no")


def test_clean_artifact_package_import_has_no_cycle() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.agent import AgentRunner, TaskContext, ToolRegistry; "
                "from app.artifacts import ArtifactStore; "
                "print(AgentRunner.__name__, TaskContext.__name__, "
                "ToolRegistry.__name__, ArtifactStore.__name__)"
            ),
        ],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AgentRunner TaskContext ToolRegistry ArtifactStore"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unsupported")
def test_artifact_store_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    script = """
import os
import sys
from pathlib import Path

from app.artifacts import ArtifactPathError, ArtifactStore

root = Path(sys.argv[1])
root.mkdir()
os.mkfifo(root / "preview-demo.png")
with ArtifactStore(root) as store:
    assert not store.exists("preview-demo.png")
    try:
        store.read_bytes("preview-demo.png")
    except ArtifactPathError:
        pass
    else:
        raise AssertionError("FIFO read did not raise ArtifactPathError")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "task")],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
    )

    assert result.returncode == 0, result.stderr


def test_artifact_store_rejects_symlink_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    symlink_root = tmp_path / "linked"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ArtifactPathError, match="symlink"):
        ArtifactStore(symlink_root)


def test_artifact_store_retains_open_root_when_path_is_swapped(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    moved_dir = tmp_path / "moved"
    outside = tmp_path / "outside"
    outside.mkdir()

    with ArtifactStore(task_dir) as store:
        task_dir.rename(moved_dir)
        task_dir.symlink_to(outside, target_is_directory=True)
        store.write_bytes("preview-demo.png", b"safe")

    assert (moved_dir / "preview-demo.png").read_bytes() == b"safe"
    assert not (outside / "preview-demo.png").exists()


def test_artifact_store_does_not_follow_artifact_symlink(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    linked = task_dir / "preview-demo.png"
    linked.symlink_to(outside)

    with ArtifactStore(task_dir) as store:
        with pytest.raises(ArtifactPathError):
            store.read_bytes("preview-demo.png")
        store.write_bytes("preview-demo.png", b"inside")

    assert outside.read_bytes() == b"outside"
    assert linked.read_bytes() == b"inside"
    assert not linked.is_symlink()


def test_invalid_artifact_extension_is_rejected_without_file(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    with ArtifactStore(task_dir) as store:
        with pytest.raises(UnsupportedArtifactError):
            store.write_bytes("invalid.txt", b"must not exist")

    assert list(task_dir.iterdir()) == []


def test_oversized_artifact_is_rejected_without_file(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    with ArtifactStore(task_dir) as store:
        with pytest.raises(ArtifactSizeError):
            store.write_bytes(
                "preview-demo.png",
                b"x" * (MAX_ARTIFACT_BYTES + 1),
            )

    assert list(task_dir.iterdir()) == []


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        ".hidden.png",
        "dir/file.png",
        "x\\y.png",
        "bad name.png",
        "bad\nname.png",
        f"{'x' * 125}.png",
    ],
)
def test_artifact_names_are_flat_safe_basenames(tmp_path: Path, name: str) -> None:
    with ArtifactStore(tmp_path / "task") as store:
        with pytest.raises(ArtifactPathError):
            store.write_bytes(name, b"no")


def test_artifact_store_closes_directory_fd(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "task")
    root_fd = store.root_fd

    store.close()

    with pytest.raises(OSError):
        os.fstat(root_fd)


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "../preview-demo.png"},
        {"media_type": "image/tiff"},
        {"size": -1},
        {"size": True},
        {"size": 10 * 1024 * 1024 + 1},
        {"sha256": "A" * 64},
        {"sha256": "0" * 63},
    ],
)
def test_artifact_descriptor_validation_is_strict(changes: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "name": "preview-demo.png",
        "media_type": "image/png",
        "size": 10,
        "sha256": "0" * 64,
        "demo": True,
    }
    values.update(changes)

    with pytest.raises(ValidationError):
        ArtifactManifestEntry.model_validate(values)


def test_plan_rejects_duplicate_step_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        AgentPlan(
            version="1",
            max_iterations=1,
            steps=[
                AgentStep(
                    id="same",
                    tool_name="mock.inspect",
                    tool_version="v1",
                    arguments={},
                ),
                AgentStep(
                    id="same",
                    tool_name="mock.export",
                    tool_version="v1",
                    arguments={},
                ),
            ],
        )


@pytest.mark.parametrize(
    "raw_plan",
    [
        {"version": "1", "max_iterations": True, "steps": []},
        {"version": "1", "max_iterations": "1", "steps": []},
    ],
)
def test_plan_numeric_fields_are_strict(raw_plan: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AgentPlan.model_validate(raw_plan)


def test_tool_numeric_inputs_are_strict() -> None:
    with pytest.raises(ValidationError):
        ExportArguments.model_validate({"seed": True, "style": "balanced"})
    with pytest.raises(ValidationError):
        ExportArguments.model_validate({"seed": "1", "style": "balanced"})
    with pytest.raises(ValidationError):
        StrengthArguments.model_validate({"strength": "0.5"})


def test_tool_result_rejects_non_finite_metrics() -> None:
    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValidationError):
            ToolResult(metrics={"score": value})


class ArtifactClaimTool(RecordingTool):
    def __init__(self, result: ToolResult) -> None:
        super().__init__([])
        self._result = result

    async def execute(
        self,
        context: TaskContext,
        arguments: BaseModel,
    ) -> ToolResult:
        del context, arguments
        return self._result


def single_step_plan(tool: RecordingTool) -> AgentPlan:
    return AgentPlan(
        version="1",
        max_iterations=1,
        steps=[
            AgentStep(
                id="step-1",
                tool_name=tool.name,
                tool_version=tool.version,
                arguments={},
            )
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_kind", ["missing", "mismatch", "duplicate"])
async def test_runner_rejects_untrusted_artifact_claims(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    claim_kind: str,
) -> None:
    context = make_context(tmp_path, fits_inspection)
    store = ArtifactStore(context.task_dir)
    valid = store.write_bytes("preview-demo.png", b"actual")
    if claim_kind == "missing":
        claim = ArtifactManifestEntry(
            name="result-demo.tiff",
            media_type="image/tiff",
            size=valid.size,
            sha256=valid.sha256,
        )
        artifacts = [claim]
    elif claim_kind == "mismatch":
        claim = valid.model_copy(update={"sha256": "0" * 64})
        artifacts = [claim]
    else:
        artifacts = [valid, valid]
    tool = ArtifactClaimTool(ToolResult(artifacts=artifacts))
    runner = AgentRunner(
        model=StaticModel(single_step_plan(tool)),
        registry=ToolRegistry([tool]),
        artifact_store=store,
    )

    with pytest.raises(AgentOutputError):
        await runner.run(context)


@pytest.mark.asyncio
async def test_runner_rejects_observation_and_artifact_count_overages(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    context = make_context(tmp_path, fits_inspection)
    store = ArtifactStore(context.task_dir)
    artifact = store.write_bytes("preview-demo.png", b"x")
    oversized_observation = ToolResult(observations={"value": "x" * 70_000})
    too_many_artifacts = ToolResult(artifacts=[artifact] * 17)
    for result in (oversized_observation, too_many_artifacts):
        tool = ArtifactClaimTool(result)
        runner = AgentRunner(
            model=StaticModel(single_step_plan(tool)),
            registry=ToolRegistry([tool]),
            artifact_store=store,
        )
        with pytest.raises(AgentOutputError):
            await runner.run(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_plan", "after_plan", "before_evaluate", "after_evaluate"])
async def test_cancellation_is_checked_around_model_calls(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    stage: str,
) -> None:
    checks = 0
    cancel_at = {
        "before_plan": 1,
        "after_plan": 2,
        "before_evaluate": 5,
        "after_evaluate": 6,
    }[stage]

    def cancellation_check() -> bool:
        nonlocal checks
        checks += 1
        return checks >= cancel_at

    context = make_context(
        tmp_path,
        fits_inspection,
        cancellation_check=cancellation_check,
    )
    tool = RecordingTool([])
    runner = AgentRunner(
        model=StaticModel(single_step_plan(tool)),
        registry=ToolRegistry([tool]),
        artifact_store=ArtifactStore(context.task_dir),
    )

    with pytest.raises(AgentCancelledError):
        await runner.run(context)


@pytest.mark.asyncio
async def test_generic_runner_default_timestamps_are_real_utc_occurrence_times(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    context = make_context(tmp_path, fits_inspection)
    tool = RecordingTool([])
    runner = AgentRunner(
        model=StaticModel(single_step_plan(tool)),
        registry=ToolRegistry([tool]),
        artifact_store=ArtifactStore(context.task_dir),
    )
    before = datetime.now(UTC)

    result = await runner.run(context)

    after = datetime.now(UTC)
    timestamps = [event.timestamp for event in result.events]
    assert all(timestamp is not None for timestamp in timestamps)
    assert timestamps == sorted(timestamps)
    assert all(
        timestamp is not None
        and timestamp.utcoffset() == timedelta(0)
        and before <= timestamp <= after
        for timestamp in timestamps
    )


class FailingArtifactStore(ArtifactStore):
    def __init__(self, root: Path, fail_on_write: int) -> None:
        super().__init__(root)
        self._write_count = 0
        self._fail_on_write = fail_on_write

    def write_bytes(self, name: str, data: bytes, *, demo: bool = False) -> ArtifactManifestEntry:
        self._write_count += 1
        if self._write_count == self._fail_on_write:
            raise OSError("simulated write failure")
        return super().write_bytes(name, data, demo=demo)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on_write", [2, 3])
async def test_mock_export_rolls_back_partial_artifact_set(
    tmp_path: Path,
    fits_inspection: FitsInspection,
    fail_on_write: int,
) -> None:
    context = make_context(tmp_path, fits_inspection)
    store = FailingArtifactStore(context.task_dir, fail_on_write)
    export_tool = build_mock_tools(store)[-1]

    with pytest.raises(OSError, match="simulated"):
        await export_tool.execute(
            context,
            ExportArguments(seed=123, style="balanced"),
        )

    assert not any((context.task_dir / name).exists() for name in EXPECTED_ARTIFACTS)


@pytest.mark.asyncio
async def test_event_sequence_is_structured_and_deterministic(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    context = make_context(tmp_path, fits_inspection)

    result = await build_runner(context).run(context)

    event_types = [event.event_type for event in result.events]
    assert event_types[0] == "plan"
    assert event_types[-2:] == ["evaluation", "completion"]
    tool_events = event_types[1:-2]
    assert tool_events == [
        event_type
        for _step in result.plan.steps
        for event_type in ("tool_started", "tool_finished")
    ]
    assert [event.sequence for event in result.events] == list(
        range(1, len(result.events) + 1)
    )
