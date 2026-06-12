import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.contracts import (
    AgentPlan,
    AgentStep,
    TaskContext,
    ToolResult,
)
from app.agent.mock_model import DeterministicMockModel
from app.agent.mock_tools import build_mock_tools
from app.agent.registry import DuplicateToolError, ToolRegistry, UnknownToolError
from app.agent.runner import (
    AgentCancelledError,
    AgentGuardrailError,
    AgentRunner,
    InvalidToolArgumentsError,
)
from app.artifacts.store import ArtifactPathError, ArtifactStore
from app.db.models import ProcessingStyle, TaskType
from app.fits.schemas import BasicStatistics, FitsInspection, HduSummary
from PIL import Image

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
    source_path.write_bytes(b"SIMPLE DEMO INPUT")
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
    return AgentRunner(
        model=DeterministicMockModel(),
        registry=ToolRegistry(build_mock_tools(store)),
        artifact_store=store,
    )


@pytest.mark.asyncio
async def test_mock_agent_is_byte_deterministic(
    tmp_path: Path,
    fits_inspection: FitsInspection,
) -> None:
    first_context = make_context(tmp_path / "first", fits_inspection)
    second_context = make_context(tmp_path / "second", fits_inspection)

    first = await build_runner(first_context).run(first_context)
    second = await build_runner(second_context).run(second_context)

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
                assert image.tag_v2[700] == "STARUN MOCK / 演示结果".encode()
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
    def __init__(self, plan: AgentPlan | dict[str, Any]) -> None:
        self._plan = plan

    async def plan(self, context: TaskContext) -> AgentPlan | dict[str, Any]:
        del context
        return self._plan

    async def evaluate(self, observation: ToolResult) -> float:
        del observation
        return 0.5


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
