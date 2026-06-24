from app.agent_sdk.agents import (
    BALANCED_PROCESSING_INSTRUCTIONS,
    PROCESSING_BASE_INSTRUCTIONS,
    REALISTIC_PROCESSING_INSTRUCTIONS,
)
from app.agent_sdk.bridge import AgentSdkBridge
from app.agent_sdk.runtime import DirectProcessingSkillRuntime, _processing_entrypoint_command
from app.agent_sdk.workspaces import DEFAULT_EXEC_YIELD_MS
from app.config import Settings
from app.db.models import ProcessingStyle
from app.fits.schemas import BasicStatistics, FitsInspection, HduSummary


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


def test_processing_instructions_pin_skill_path_and_avoid_sandbox_exploration() -> None:
    assert ".agents/deep-sky-processor/" in PROCESSING_BASE_INSTRUCTIONS
    assert "不要通过 find" in PROCESSING_BASE_INSTRUCTIONS
    assert "scripts/run_starun_processing.py" in PROCESSING_BASE_INSTRUCTIONS
    assert "--result ../../output/processing-result.json" in PROCESSING_BASE_INSTRUCTIONS


def test_processing_style_instructions_delegate_to_starun_entrypoint() -> None:
    assert "run_starun_processing.py" in REALISTIC_PROCESSING_INSTRUCTIONS
    assert "run_starun_processing.py" in BALANCED_PROCESSING_INSTRUCTIONS
    assert "python scripts/pipeline.py" not in REALISTIC_PROCESSING_INSTRUCTIONS
    assert "python scripts/pipeline.py" not in BALANCED_PROCESSING_INSTRUCTIONS


def test_exec_command_yields_quickly_for_long_running_pty_commands() -> None:
    assert DEFAULT_EXEC_YIELD_MS == 30_000


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
