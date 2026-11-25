"""Tests for sequential GPT->Reset->Qwen isolation, structural repair strategies, multimodal QA, and OpenCode runner."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import benchmark_preflight as bp  # noqa: E402
from blender_orchestration import (  # noqa: E402
    BlenderFixAction,
    IndependentMultimodalReviewer,
    ImageObservation,
    RepairLoopPolicy,
    RepairStrategyTier,
    SEVEN_QA_DOMAINS,
    SequentialBenchmarkOrchestrator,
    SequentialBenchmarkState,
    VisualDefect,
    VisualQARepairLoop,
    VisualReviewEvidence,
    compute_approval_policy_digest,
    detect_2d_projection_or_primitive_shortcuts,
    get_qa_profile,
    hash_file_sha256,
    select_strategy_for_defect,
    validate_repair_effectiveness,
)


def _write_png(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + data)
    return hash_file_sha256(path)


class _StubResetter:
    def __init__(self, *, reset_should_pass: bool = True) -> None:
        self.reset_should_pass = reset_should_pass
        self.reset_called = 0
        self.cleanup_called = 0

    def inspect_status(self) -> dict:
        return {"state": "READY", "blender_pid": 11111, "boot_id": "boot-1"}

    def reset_for_next_model(self, *, expected_saved_files=None, forbidden_residual_objects=None, start_fresh_blender=True) -> dict:
        self.reset_called += 1
        return {
            "reset_passed": self.reset_should_pass,
            "reset_started_at": "2026-11-25T09:00:00+00:00",
            "reset_finished_at": "2026-11-25T09:00:02+00:00",
            "old_pid": 11111,
            "old_boot_id": "boot-gpt",
            "new_pid": 22222 if self.reset_should_pass else None,
            "new_boot_id": "boot-qwen" if self.reset_should_pass else None,
            "stop_result": {"stopped": self.reset_should_pass},
            "post_stop_status": {"blender_alive": False, "xvfb_alive": False, "addon_reachable": False},
            "gpu_before": {"health": {"status": "healthy"}},
            "gpu_after": {"health": {"status": "healthy"}},
            "gpu_released": {"released": True},
            "new_scene_state": {"object_count": 0, "object_names": []},
            "residual_objects_found": [],
            "checks": {
                "1_prior_outputs_saved": True,
                "2_no_inflight_mcp_jobs": True,
                "3_memory_datablocks_purged": True,
                "4_managed_blender_stopped": self.reset_should_pass,
                "5_mcp_session_closed": True,
                "6_xvfb_cleaned_up": True,
                "7_old_pid_terminated": self.reset_should_pass,
                "8_gpu_memory_released": True,
                "9_new_blender_started_fresh_pid": self.reset_should_pass,
                "10_clean_empty_scene_verified": self.reset_should_pass,
            },
        }

    def final_cleanup(self) -> dict:
        self.cleanup_called += 1
        return {"stopped": True}


class TestSequentialIsolationAndRepairV2(unittest.TestCase):
    def test_01_sequential_fsm_transitions_and_isolation_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = root / "seq_runs"
            reports_dir = root / "reports"
            rt_dir = root / "runtime"
            resetter = _StubResetter(reset_should_pass=True)
            orch = SequentialBenchmarkOrchestrator(
                output_root=out_root,
                reports_dir=reports_dir,
                runtime_dir=rt_dir,
                resetter=resetter,  # type: ignore[arg-type]
            )
            order: list[str] = []

            def run_gpt(d: Path, guard) -> dict:
                order.append("GPT")
                (d / "execution_trace.json").write_text("{}", encoding="utf-8")
                return {"model_id": "gpt-test", "producer_session_id": "ses_gpt", "verdicts": {"final_asset_production": "FAIL"}}

            def run_qwen(d: Path, guard) -> dict:
                order.append("QWEN")
                # Cross-access to GPT folder must be blocked during Phase C
                gpt_sibling = next(out_root.glob("*_gpt"))
                cross_blocked = False
                try:
                    guard.validate_read_path(gpt_sibling / "execution_trace.json")
                except PermissionError:
                    cross_blocked = True
                self.assertTrue(cross_blocked)
                (d / "execution_trace.json").write_text("{}", encoding="utf-8")
                return {"model_id": "qwen-test", "producer_session_id": "ses_qwen", "verdicts": {"final_asset_production": "NOT_VERIFIED"}}

            res = orch.run(gpt_runner_fn=run_gpt, qwen_runner_fn=run_qwen, timestamp="20261125_093000")
            self.assertEqual(res["state"], SequentialBenchmarkState.COMPLETE)
            self.assertEqual(order, ["GPT", "QWEN"])
            self.assertEqual(resetter.reset_called, 1)
            self.assertEqual(resetter.cleanup_called, 1)
            self.assertEqual(
                [t["to"] for t in res["transitions"]],
                list(SequentialBenchmarkState.ORDERED_MAIN[1:]),
            )
            iso = json.loads((reports_dir / "session_isolation.json").read_text(encoding="utf-8"))
            self.assertEqual(iso["isolation_verdict"], "PASS")
            self.assertEqual(iso["gpt_blender_pid"], 11111)
            self.assertEqual(iso["qwen_blender_pid"], 22222)
            self.assertFalse(iso["execution_windows_overlapped"])
            self.assertTrue((reports_dir / "gpt_vs_qwen_comparison.md").is_file())

    def test_02_reset_failure_blocks_qwen_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resetter = _StubResetter(reset_should_pass=False)
            orch = SequentialBenchmarkOrchestrator(
                output_root=root / "seq_runs",
                reports_dir=root / "reports",
                runtime_dir=root / "runtime",
                resetter=resetter,  # type: ignore[arg-type]
            )
            qwen_invoked = {"called": False}

            def run_gpt(d: Path, guard) -> dict:
                (d / "execution_trace.json").write_text("{}", encoding="utf-8")
                return {"model_id": "gpt-test"}

            def run_qwen(d: Path, guard) -> dict:
                qwen_invoked["called"] = True
                return {"model_id": "qwen-test"}

            res = orch.run(gpt_runner_fn=run_gpt, qwen_runner_fn=run_qwen, timestamp="20261125_093100")
            self.assertEqual(res["state"], SequentialBenchmarkState.BLOCKED_RESET_FAILED)
            self.assertFalse(qwen_invoked["called"])
            self.assertEqual(res["session_isolation"]["isolation_verdict"], "FAIL")

    def test_03_superficial_fix_rejected_for_structural_defects_and_best_checkpoint_kept(self) -> None:
        action = BlenderFixAction(
            action_id="FIX-1",
            defect_id="D-EYE",
            defect_type="round_protruding_eyes",
            target_objects=["SM_Char_EyesEars"],
            operation_type="recess_eyes_and_contour_eyelids",
        )
        # Material-only tweak must be rejected
        mat_val = validate_repair_effectiveness([action], {"mesh_modified": True, "material_only": True, "geometry_changed": False})
        self.assertFalse(mat_val.effective)
        self.assertIn("SUPERFICIAL_FIX_REJECTED", str(mat_val.failure_reason))

        # Real structural geometry remodel is accepted
        struct_val = validate_repair_effectiveness([action], {"mesh_modified": True, "geometry_changed": True})
        self.assertTrue(struct_val.effective)
        self.assertEqual(struct_val.applied_tier, RepairStrategyTier.STRUCTURAL_REMODEL)

        # Escalation chain moves from STRUCTURAL_REMODEL -> ROLLBACK_TO_BLOCKOUT on repeat
        self.assertEqual(select_strategy_for_defect("round_protruding_eyes", attempt_index=0), RepairStrategyTier.STRUCTURAL_REMODEL)
        self.assertEqual(select_strategy_for_defect("round_protruding_eyes", attempt_index=1), RepairStrategyTier.ROLLBACK_TO_BLOCKOUT)

        # VisualQARepairLoop rejects superficial fix and keeps best_checkpoint
        profile = get_qa_profile("character", reference_mode="TEXT_ONLY")
        policy = RepairLoopPolicy(max_fix_iterations=3, escalate_strategy_on_repeat=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blend = root / "char.blend"
            blend.write_bytes(b"BLENDER-v503")
            bsha = hash_file_sha256(blend)

            def render_fn(idx: int, views: list[str]) -> dict:
                p = root / f"r{idx}.png"
                sha = _write_png(p, f"r{idx}".encode())
                return {"renders": [{"view": "front", "path": str(p), "sha256": sha}]}

            def review_fn(bundle: dict, idx: int, digest: str) -> VisualReviewEvidence:
                r = bundle["renders"][0]
                verdicts = {c.criterion_id: "PASS" for c in profile.criteria}
                verdicts["non_protruding_eyes_and_almond_contour"] = "FAIL"
                return VisualReviewEvidence(
                    reviewer_id="qa",
                    reviewer_context_id="ctx_qa",
                    producer_context_id="ctx_prod",
                    reviewed_at="2026-11-25T09:32:00Z",
                    target_asset_path=str(blend),
                    target_asset_sha256=bsha,
                    profile_id=profile.profile_id,
                    reference_mode="TEXT_ONLY",
                    image_observations=[ImageObservation(r["path"], r["sha256"], "front", ["Eyes protrude"])],
                    defects=[VisualDefect("D1", "round_protruding_eyes", "MAJOR", ["SM_Char_EyesEars"], r["path"], "Protruding eyes", "Remodel sockets", qa_criterion_id="non_protruding_eyes_and_almond_contour")],
                    criteria_verdicts=verdicts,
                    approval_policy_digest=digest,
                )

            loop_res = VisualQARepairLoop(profile, policy).run(
                render_fn=render_fn,
                review_fn=review_fn,
                apply_fix_fn=lambda actions, n: {"mesh_modified": True, "material_only": True, "geometry_changed": False},
            )
            self.assertEqual(loop_res["status"], "FAIL")
            self.assertIsNotNone(loop_res["best_checkpoint"])
            self.assertTrue(any("SUPERFICIAL_FIX_REJECTED" in r for r in loop_res["reasons"]))

    def test_04_multimodal_reviewer_fail_closed_and_anti_rubber_stamp(self) -> None:
        profile = get_qa_profile("character", reference_mode="TEXT_ONLY")
        digest = compute_approval_policy_digest(profile, RepairLoopPolicy())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blend = root / "char.blend"
            blend.write_bytes(b"BLENDER-v503")
            img = root / "front.png"
            _write_png(img, b"front_render")

            # 1. Non-multimodal / unreadable image -> NOT_VERIFIED
            rev_unreadable = IndependentMultimodalReviewer(
                invoke_fn=lambda *a, **k: {
                    "returncode": 0,
                    "session_id": "ses_rev_1",
                    "text": '```json\n{"multimodal_access_failed": true}\n```',
                }
            )
            r1 = rev_unreadable.inspect_renders(
                renders=[{"view": "front", "path": str(img)}],
                target_asset_path=blend,
                producer_context_id="ses_prod_1",
                profile=profile,
                policy_digest=digest,
            )
            self.assertEqual(r1["status"], "NOT_VERIFIED")

            # 2. Reviewer claims PASS on all 7 domains, but scene_audit shows scalp gap -> forced FAIL!
            rubber_stamp_payload = {
                "observations_by_view": {"front": ["Face looks nice"]},
                "domain_verdicts": {d: "PASS" for d in SEVEN_QA_DOMAINS},
                "criteria_verdicts": {c.criterion_id: "PASS" for c in profile.criteria},
                "defects": [],
            }
            rev_rubber = IndependentMultimodalReviewer(
                invoke_fn=lambda *a, **k: {
                    "returncode": 0,
                    "session_id": "ses_rev_2",
                    "text": "```json\n" + json.dumps(rubber_stamp_payload) + "\n```",
                }
            )
            r2 = rev_rubber.inspect_renders(
                renders=[{"view": "front", "path": str(img)}],
                target_asset_path=blend,
                producer_context_id="ses_prod_1",
                profile=profile,
                policy_digest=digest,
                scene_audit={
                    "mesh_objects": 10,
                    "triangles": 8000,
                    "material_count": 4,
                    "meshes": [{"name": "SM_Char_Hair_Scalp", "tris": 600, "non_manifold_edges": 0, "loose_verts": 0, "uv_layers": 1, "scale_applied": True}],
                    "character_spatial_checks": {"scalp_gap_detected": True},
                },
            )
            self.assertEqual(r2["status"], "FAIL")
            self.assertEqual(r2["domain_verdicts"]["technical_mesh_validity"], "FAIL")

    def test_05_detect_2d_projection_and_primitive_shortcuts(self) -> None:
        bad_script = """
def P(x,z,d=0): return Vector(((x-193.5)*K, d*K, (782-z)*K))
def face(x,z): return FH.ray_cast(P(x,z,-250), (0,1,0))[0].y
bmesh.ops.create_uvsphere(bm, radius=0.02)
sweep('Hair_Main', rows)
"""
        res = detect_2d_projection_or_primitive_shortcuts(bad_script, {"mesh_objects": 230})
        self.assertFalse(res["clean"])
        detected = {s["shortcut"] for s in res["shortcuts_detected"]}
        self.assertIn("2d_pixel_depth_projection", detected)
        self.assertIn("uv_sphere_without_orbital_socket", detected)
        self.assertIn("rope_tube_hair", detected)
        self.assertIn("excessive_primitive_fragmentation", detected)


if __name__ == "__main__":
    unittest.main()
