import json
from pathlib import Path

import pytest

from blender_orchestration.autonomous_harness import (
    AutonomousBlenderHarness,
    ProductionBlockedError,
    _check_agent_health,
    _load_resume_state,
    _write_json_atomic,
)
from blender_orchestration.rubric_evaluator import QWEN_FLASH_NEXT_MODEL_ID, StageVisualReviewer
from blender_orchestration.visual_qa import hash_file_sha256


def test_resume_rejects_other_stage_goal_checkpoint_and_design(tmp_path):
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    blend = checkpoints / "verified.blend"
    blend.write_bytes(b"BLENDER fixture")
    handoffs = tmp_path / "handoffs"
    handoffs.mkdir()
    spec = handoffs / "01_design_spec.json"
    _write_json_atomic(spec, {"type": "prop"})
    goal = {"prompt": "Make a calibration weight"}
    state = {
        "goal": goal, "next_stage": 3, "checkpoint": str(blend),
        "technical_gates": {"blockout_created": True},
        "checkpoint_sha256": hash_file_sha256(blend), "design_sha256": hash_file_sha256(spec),
    }
    _write_json_atomic(tmp_path / "production_state.json", state)
    assert _load_resume_state(tmp_path, blend, 3, goal) == state
    with pytest.raises(ValueError, match="does not match"):
        _load_resume_state(tmp_path, blend, 4, goal)
    with pytest.raises(ValueError, match="does not match"):
        _load_resume_state(tmp_path, blend, 3, {"prompt": "Different goal"})
    _write_json_atomic(tmp_path / "production_state.json", {
        **state, "next_stage": 4, "technical_gates": {"blockout_created": True, "detailed_modeling_created": True},
    })
    with pytest.raises(ValueError, match="independent visual review"):
        _load_resume_state(tmp_path, blend, 4, goal)
    _write_json_atomic(tmp_path / "production_state.json", state)
    blend.write_bytes(b"BLENDER modified")
    with pytest.raises(ValueError, match="does not match"):
        _load_resume_state(tmp_path, blend, 3, goal)
    blend.write_bytes(b"BLENDER fixture")
    _write_json_atomic(spec, {"type": "character"})
    with pytest.raises(ValueError, match="does not match"):
        _load_resume_state(tmp_path, blend, 3, goal)


def test_failed_atomic_write_keeps_previous_resume_state(tmp_path):
    state = tmp_path / "production_state.json"
    _write_json_atomic(state, {"checkpoint": "verified.blend"})
    with pytest.raises(TypeError):
        _write_json_atomic(state, {"checkpoint": object()})
    assert json.loads(state.read_text()) == {"checkpoint": "verified.blend"}
    assert list(tmp_path.iterdir()) == [state]


def test_supervisor_work_order_uses_separate_model_and_records_trace(tmp_path):
    (tmp_path / "handoffs").mkdir()
    calls = []

    def invoke(model, prompt, **kwargs):
        calls.append((model, kwargs))
        return {"returncode": 0, "session_id": "gpt-plan", "text": '{"decision":"stop","reason":"No valid repair"}'}

    harness = AutonomousBlenderHarness(
        producer_model_id=QWEN_FLASH_NEXT_MODEL_ID,
        supervisor_model_id="openai/gpt-supervisor",
        reviewer_model_id="openai/gpt-reviewer",
        agent_invoke_fn=invoke,
    )
    image = Path("/tmp/stage-render.png")
    telemetry = []
    assert harness._plan_correction(
        stage="detailed_modeling", attempt=1, run_dir=tmp_path, design_spec="Workshop prop",
        review={"score": 60}, best_review={"score": 65}, inventory="1 mesh",
        images=[image], telemetry=telemetry,
    )["decision"] == "stop"
    assert calls[0][0] == "openai/gpt-supervisor"
    assert calls[0][1]["files"] == [image]
    assert harness.model_id == QWEN_FLASH_NEXT_MODEL_ID
    assert harness.stage_reviewer.model_id == harness.final_evaluator.model_id == "openai/gpt-reviewer"
    assert telemetry[0]["session_id"] == "gpt-plan"
    assert (tmp_path / "handoffs/detailed_modeling_work_order_1.json").is_file()


@pytest.mark.parametrize("response", [
    {"timed_out": True},
    {"errors": ["native embedding: cannot pin 322 MiB"]},
    {"stderr": "GPU is lost"},
])
def test_backends_with_resource_failures_stop_production(response):
    with pytest.raises(ProductionBlockedError, match="backend unavailable"):
        _check_agent_health(response)


def test_visual_stage_cannot_pass_without_observations(tmp_path, monkeypatch):
    render = tmp_path / "front.png"
    render.touch()
    monkeypatch.setattr("blender_orchestration.rubric_evaluator.compose_review_sheets", lambda renders: [render])
    reviewer = StageVisualReviewer(
        invoke_fn=lambda *args, **kwargs: {
            "returncode": 0, "session_id": "reviewer", "text": '{"stage_score_100":99,"passed":true}',
        },
    )
    verdict = reviewer.review_stage(
        stage_name="detailed_modeling", renders=[{"path": str(render), "view": "front"}],
        design_spec_summary="Workshop prop", producer_session_ids={"producer"},
    )
    assert verdict["verdict"] == "NOT_VERIFIED"


@pytest.mark.parametrize(("manifest", "expected"), [(None, False), (Path("manifest.json"), True)])
def test_mcp_wrapper_serializes_a_python_boolean(tmp_path, manifest, expected):
    import ast

    class Adapter:
        def execute_blender_json(self, code, **kwargs):
            assignments = [node for node in ast.parse(code).body if isinstance(node, ast.Assign)]
            flag = next(node for node in assignments if node.targets[0].id == "_CHECK_PRESERVE")
            assert ast.literal_eval(flag.value) is expected
            return {"parsed": {"ok": True}}

    result = AutonomousBlenderHarness()._exec_blender_wrapped(
        Adapter(), "SCENE_Test", tmp_path, "pass", stage_label="geometry_fix",
        user_prompt="Fixture", base_mesh_manifest=manifest,
    )
    assert result == {"ok": True}


def test_production_reset_leaves_other_blender_sessions_alone(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from blender_orchestration.sequential_benchmark import BlenderSessionResetter

    class Manager:
        cfg = SimpleNamespace(active_jobs_dir=tmp_path, pending_jobs_dir=tmp_path)

        def status(self, probe_live):
            return {"addon_reachable": True, "ownership": "external", "blender_alive": False}

    resetter = BlenderSessionResetter(runtime_dir=tmp_path)
    monkeypatch.setattr(resetter, "_make_manager", Manager)
    with pytest.raises(RuntimeError, match="outside this managed runtime"):
        resetter.reset_for_next_model(preserve_backups=True)
