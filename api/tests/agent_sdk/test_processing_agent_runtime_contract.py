import json
import logging
from io import BytesIO
from types import SimpleNamespace

from app.agent_sdk.bridge import AgentSdkBridge
from app.agent_sdk import runtime as runtime_module
from app.agent_sdk.runtime import (
    DirectAnalysisSkillRuntime,
    DirectProcessingSkillRuntime,
    _analysis_entrypoint_command,
    _processing_entrypoint_command,
)
from app.artifacts.store import ArtifactStore
from app.config import Settings
from app.db.models import ProcessingStyle
from app.fits.schemas import BasicStatistics, FitsInspection, HduSummary
from app.processing.models import GeneratedArtwork
import pytest
from agents.sandbox.types import ExecResult
from PIL import Image


def _inspection() -> FitsInspection:
    hdu = HduSummary(
        index=0,
        name="PRIMARY",
        kind="primary_image",
        shape=[16, 16],
        dtype="float32",
        supported=True,
    )
    return FitsInspection(
        format="fits",
        hdus=[hdu],
        selected_hdu=hdu,
        statistics=BasicStatistics(
            minimum=0.0,
            maximum=1.0,
            mean=0.1,
            median=0.1,
            standard_deviation=0.01,
            finite_pixel_count=256,
        ),
        header={},
    )


def _analysis_result_bytes() -> bytes:
    return (
        json.dumps(
            {
                "schema_version": "starun.skill-result/v1",
                "status": "success",
                "provider": "deep-sky-advisor",
                "model": "deterministic-skill-v1",
                "preview": {
                    "artifact": "analysis-preview.png",
                    "width": 16,
                    "height": 16,
                    "lower_percentile_value": 0.0,
                    "upper_percentile_value": 1.0,
                },
                "analysis": {
                    "overview": "IC 434 analysis",
                    "image_quality": {
                        "rating": "good",
                        "summary": "usable signal",
                        "confidence": 0.8,
                    },
                    "observations": {
                        "target": "IC 434",
                        "background": "mild gradient",
                        "stars": "limited star samples",
                        "noise": "low noise",
                        "color": "rgb",
                    },
                    "issues": [],
                    "workflow": [
                        {
                            "order": 1,
                            "step": "stretch",
                            "purpose": "reveal faint signal",
                            "guidance": "use controlled stretch",
                        }
                    ],
                    "caveats": ["preview is stretched"],
                },
                "markdown": "# Deep sky report\n\nIC 434 markdown output",
                "artifacts": [
                    {"name": "analysis-report.json", "media_type": "application/json"},
                    {"name": "analysis-preview.png", "media_type": "image/png"},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )


def _png_bytes(color: tuple[int, int, int] = (8, 16, 32)) -> bytes:
    output = BytesIO()
    Image.new("RGB", (4, 4), color).save(output, format="PNG")
    return output.getvalue()


def test_agent_max_turns_caps_env_override_above_hard_limit() -> None:
    assert Settings(_env_file=None, agent_max_turns=30).agent_max_turns == 30
    assert Settings(_env_file=None, agent_max_turns=31).agent_max_turns == 30


def test_realistic_processing_uses_direct_skill_runtime(tmp_path) -> None:
    source = tmp_path / "source.fits"
    source.write_bytes(b"fits")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        processing_skill_path=tmp_path / "deep-sky-processor",
    )
    settings.processing_skill_path.mkdir()
    bridge = AgentSdkBridge(settings)

    spec = bridge.build_processing_spec(
        task_id="task-1",
        source_path=source,
        inspection=_inspection(),
        style=ProcessingStyle.REALISTIC,
    )

    assert isinstance(bridge._default_runtime(spec), DirectProcessingSkillRuntime)
    assert spec.skill_path == settings.processing_skill_path


def test_analysis_uses_direct_skill_runtime(tmp_path) -> None:
    source = tmp_path / "source.fits"
    source.write_bytes(b"fits")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        analysis_skill_path=tmp_path / "deep-sky-advisor",
    )
    settings.analysis_skill_path.mkdir()
    bridge = AgentSdkBridge(settings)

    spec = bridge.build_analysis_spec(
        task_id="analysis-1",
        source_path=source,
        inspection=_inspection(),
    )

    assert isinstance(bridge._default_runtime(spec), DirectAnalysisSkillRuntime)
    assert spec.skill_path == settings.analysis_skill_path


@pytest.mark.asyncio
async def test_analysis_publish_includes_skill_result_file(tmp_path) -> None:
    source = tmp_path / "source.fits"
    source.write_bytes(b"fits")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        analysis_skill_path=tmp_path / "deep-sky-advisor",
    )
    settings.analysis_skill_path.mkdir()
    spec = AgentSdkBridge(settings).build_analysis_spec(
        task_id="analysis-result-publish",
        source_path=source,
        inspection=_inspection(),
    )
    result_bytes = _analysis_result_bytes()

    class FakeRuntime:
        async def read_bytes(self, path: str) -> bytes:
            if path == "output/analysis-result.json":
                return result_bytes
            if path == "output/analysis-report.json":
                return b'{"source_analysis":{},"advice":{}}\n'
            if path == "output/analysis-preview.png":
                return b"png"
            raise FileNotFoundError(path)

    task_dir = tmp_path / "data" / "tasks" / spec.task_id
    with ArtifactStore(task_dir) as store:
        run = await AgentSdkBridge(settings)._read_and_publish(
            FakeRuntime(),
            spec,
            store,
        )

    names = [artifact.name for artifact in run.artifacts]
    assert names == [
        "analysis-result.json",
        "analysis-report.json",
        "analysis-preview.png",
    ]
    assert (task_dir / "analysis-result.json").read_bytes() == result_bytes
    assert run.summary["analysis"]["overview"] == "IC 434 analysis"
    assert run.summary["markdown"] == "# Deep sky report\n\nIC 434 markdown output"


def test_direct_processing_runtime_command_targets_starun_entrypoint(tmp_path) -> None:
    source = tmp_path / "source.xisf"
    source.write_bytes(b"xisf")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        processing_skill_path=tmp_path / "deep-sky-processor",
    )
    settings.processing_skill_path.mkdir()
    spec = AgentSdkBridge(settings).build_processing_spec(
        task_id="task-2",
        source_path=source,
        inspection=_inspection().model_copy(update={"format": "xisf"}),
        style=ProcessingStyle.BALANCED,
    )

    command = _processing_entrypoint_command(spec)

    assert "scripts/run_starun_processing.py" in command
    assert "../../input/source.xisf" in command
    assert "Runner.run" not in command


def test_direct_analysis_runtime_command_targets_starun_entrypoint(tmp_path) -> None:
    source = tmp_path / "source.xisf"
    source.write_bytes(b"xisf")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        analysis_skill_path=tmp_path / "deep-sky-advisor",
    )
    settings.analysis_skill_path.mkdir()
    spec = AgentSdkBridge(settings).build_analysis_spec(
        task_id="analysis-2",
        source_path=source,
        inspection=_inspection().model_copy(update={"format": "xisf"}),
    )

    command = _analysis_entrypoint_command(spec)

    assert "scripts/run_starun_analysis.py" in command
    assert "../../input/source.xisf" in command
    assert "Runner.run" not in command


@pytest.mark.asyncio
async def test_artistic_processing_calls_image_provider_without_art_direction_planning(
    monkeypatch,
    tmp_path,
) -> None:
    source = tmp_path / "source.fits"
    source.write_bytes(b"fits")
    settings = Settings(_env_file=None, image_ai_api_key="image-key")
    spec = AgentSdkBridge(settings).build_processing_spec(
        task_id="artistic-direct",
        source_path=source,
        inspection=_inspection(),
        style=ProcessingStyle.ARTISTIC,
    )
    preview = _png_bytes()
    generated = _png_bytes(color=(24, 32, 48))
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "app.agent_sdk.bridge.render_image_preview",
        lambda *_args, **_kwargs: SimpleNamespace(
            data=preview,
            width=16,
            height=16,
            lower_percentile=0.0,
            upper_percentile=1.0,
        ),
    )

    class FakeImageProvider:
        def __init__(self, **_kwargs) -> None:
            pass

        async def generate(self, *, reference_png, direction):
            captured["reference_png"] = reference_png
            captured["direction"] = direction
            return GeneratedArtwork(
                data=generated,
                media_type="image/png",
                width=1024,
                height=1024,
                provider_width=1024,
                provider_height=1024,
                normalized_to_requested_size=False,
                provider_request_id="image-req",
                revised_prompt="provider revised",
                provider_request_controls={"reference_strength": 0.98},
            )

    monkeypatch.setattr("app.agent_sdk.bridge.TokenHubImageProvider", FakeImageProvider)
    events: list[tuple[str, dict[str, object]]] = []

    async def emit(event_type: str, payload: dict[str, object]) -> None:
        events.append((event_type, payload))

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    with ArtifactStore(task_dir) as store:
        run = await AgentSdkBridge(settings).run(
            spec,
            artifact_store=store,
            cancellation_check=lambda: False,
            event_sink=emit,
        )

    assert [payload["tool_name"] for event, payload in events if event == "tool_started"] == [
        "tencent.hunyuan_image"
    ]
    assert not any(
        payload.get("tool_name") == "starun_agent_model.art_direction"
        for _, payload in events
    )
    assert (task_dir / "generation-prompt.json").exists()
    assert not (task_dir / "art-direction.json").exists()
    assert captured["reference_png"] == preview
    assert run.pipeline_status == "success"
    assert run.summary["art_direction_model"] is None
    assert run.summary["art_direction_summary"]
    assert [artifact.name for artifact in run.artifacts] == [
        "processing-reference.png",
        "generation-prompt.json",
        "generated-artwork.png",
        "generation-record.json",
    ]


def test_direct_skill_exec_result_logs_streams_as_lines(caplog, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STARUN_DEBUG_LOG_TEXT_LIMIT", "5000")
    source = tmp_path / "source.fits"
    source.write_bytes(b"fits")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        processing_skill_path=tmp_path / "deep-sky-processor",
    )
    settings.processing_skill_path.mkdir()
    spec = AgentSdkBridge(settings).build_processing_spec(
        task_id="task-log",
        source_path=source,
        inspection=_inspection(),
        style=ProcessingStyle.REALISTIC,
    )
    result = ExecResult(
        stdout=("phase 1\n" + ("x" * 2100)).encode(),
        stderr=b"warning 1\nwarning 2",
        exit_code=0,
    )

    with caplog.at_level(logging.DEBUG, logger="app.agent_sdk.runtime"):
        runtime_module._log_exec_result(spec, result)

    messages = [record.getMessage() for record in caplog.records]
    assert any("stdout_bytes=" in message and "stderr_bytes=" in message for message in messages)
    assert any("Direct skill stdout:" in message and "line=1 text=phase 1" in message for message in messages)
    assert any("Direct skill stdout:" in message and "line=2 chunk=1" in message for message in messages)
    assert any("Direct skill stdout:" in message and "line=2 chunk=2" in message for message in messages)
    assert any("Direct skill stderr:" in message and "line=2 text=warning 2" in message for message in messages)


@pytest.mark.asyncio
async def test_direct_processing_runtime_starts_session_before_exec(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.fits"
    source.write_bytes(b"fits")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        processing_skill_path=tmp_path / "deep-sky-processor",
    )
    settings.processing_skill_path.mkdir()
    spec = AgentSdkBridge(settings).build_processing_spec(
        task_id="task-3",
        source_path=source,
        inspection=_inspection(),
        style=ProcessingStyle.REALISTIC,
    )
    calls: list[str] = []

    class FakeSession:
        async def start(self):
            calls.append("start")

        async def exec(self, *_args, **_kwargs):
            calls.append("exec")
            return ExecResult(stdout=b"", stderr=b"", exit_code=0)

    class FakeClient:
        async def create(self, *, manifest):
            calls.append("create")
            return FakeSession()

    monkeypatch.setattr(runtime_module, "UnixLocalSandboxClient", lambda: FakeClient())

    async def emit(_event_type, _payload):
        return None

    await DirectProcessingSkillRuntime(spec).run(spec, emit)

    assert calls[:3] == ["create", "start", "exec"]


@pytest.mark.asyncio
async def test_direct_analysis_runtime_starts_session_before_exec(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.fits"
    source.write_bytes(b"fits")
    settings = Settings(
        _env_file=None,
        ai_api_key="test-key",
        analysis_skill_path=tmp_path / "deep-sky-advisor",
    )
    settings.analysis_skill_path.mkdir()
    spec = AgentSdkBridge(settings).build_analysis_spec(
        task_id="analysis-3",
        source_path=source,
        inspection=_inspection(),
    )
    calls: list[str] = []

    class FakeSession:
        async def start(self):
            calls.append("start")

        async def exec(self, *_args, **_kwargs):
            calls.append("exec")
            return ExecResult(stdout=b"", stderr=b"", exit_code=0)

    class FakeClient:
        async def create(self, *, manifest):
            calls.append("create")
            return FakeSession()

    monkeypatch.setattr(runtime_module, "UnixLocalSandboxClient", lambda: FakeClient())

    async def emit(_event_type, _payload):
        return None

    await DirectAnalysisSkillRuntime(spec).run(spec, emit)

    assert calls[:3] == ["create", "start", "exec"]
