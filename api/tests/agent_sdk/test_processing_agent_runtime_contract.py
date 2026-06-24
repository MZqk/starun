from app.agent_sdk.bridge import AgentSdkBridge
from app.agent_sdk import runtime as runtime_module
from app.agent_sdk.runtime import (
    DirectAnalysisSkillRuntime,
    DirectProcessingSkillRuntime,
    _analysis_entrypoint_command,
    _processing_entrypoint_command,
)
from app.config import Settings
from app.db.models import ProcessingStyle
from app.fits.schemas import BasicStatistics, FitsInspection, HduSummary
import pytest
from agents.sandbox.types import ExecResult


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
