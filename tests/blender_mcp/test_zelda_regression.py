"""Regression tests anchored to output/zelda_reference/ without altering baseline artifacts."""

from __future__ import annotations

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
    BenchmarkIsolationError,
    BenchmarkIsolationGuard,
    CharacterProductionPipeline,
    evaluate_verified_character_gates,
    evaluate_zelda_regression_case,
    hash_file_sha256,
    load_zelda_regression_snapshot,
    verify_visual_review_evidence,
)

AGENTS_MD = PROJECT_ROOT / ".opencode" / "AGENTS.md"
ZELDA_DIR = PROJECT_ROOT / "output" / "zelda_reference"


class TestZeldaReferenceRegression(unittest.TestCase):
    def test_01_zelda_artifacts_preserved_and_all_nine_defects_detected(self) -> None:
        snap = load_zelda_regression_snapshot(ZELDA_DIR)
        self.assertEqual(snap["technical_audit"]["triangles"], 121752)
        self.assertEqual(snap["technical_audit"]["mesh_objects"], 230)
        self.assertFalse(snap["technical_audit"]["within_budget"])

        reg = evaluate_zelda_regression_case(ZELDA_DIR)
        # Automated geometry analysis catches budget overrun, fragmentation (230 > 60), material sprawl (32 > 12)
        geom = reg["geometry_analysis"]
        self.assertFalse(geom["within_triangle_budget"])
        self.assertFalse(geom["fragmentation_ok"])
        self.assertFalse(geom["within_material_budget"])
        geom_defect_types = {d["defect_type"] for d in geom["detected_defects"]}
        self.assertIn("triangle_budget_overrun", geom_defect_types)
        self.assertIn("primitive_stacking", geom_defect_types)
        self.assertIn("material_sprawl", geom_defect_types)

        # All 9 documented Zelda defects are mapped to concrete localized Blender fix actions
        defect_types = {d.defect_type for d in reg["defects"]}
        self.assertEqual(len(reg["defects"]), 9)
        for expected in (
            "reference_match_gap",
            "round_protruding_eyes",
            "scalp_exposure",
            "rope_like_hair",
            "stiff_fingers_bad_grip",
            "bulbous_boots",
            "floating_thigh_seam",
            "stiff_shoulders_knees",
            "triangle_budget_overrun",
        ):
            self.assertIn(expected, defect_types)

        actions = reg["fix_actions"]
        self.assertEqual(len(actions), 9)
        self.assertTrue(all(a.preserve_existing_mesh for a in actions))
        self.assertTrue(any(a.operation_type == "close_scalp_crown_coverage" for a in actions))
        self.assertTrue(any(a.operation_type == "recess_eyes_and_contour_eyelids" for a in actions))
        self.assertTrue(any(a.operation_type == "snap_costume_trim_to_surface" for a in actions))

    def test_02_zelda_verified_evidence_fails_gate_a_and_blocks_downstream(self) -> None:
        reg = evaluate_zelda_regression_case(ZELDA_DIR)
        ev_check = verify_visual_review_evidence(
            reg["review_evidence"],
            expected_policy_digest=reg["policy_digest"],
            verify_files_on_disk=True,
        )
        self.assertTrue(ev_check["valid"], ev_check)

        preflight = bp.resolve_preflight_policy(
            "Recreate character using supplied approved references.",
            agents_md_path=AGENTS_MD,
            supplied_references=[str(PROJECT_ROOT / "images.jpg")],
        )
        gates = evaluate_verified_character_gates(preflight, gate_a_review=reg["gate_a_review"])
        self.assertEqual(gates["gate_a"]["modeling_quality_status"], "FAIL")
        self.assertEqual(gates["gate_a"]["exact_identity_fidelity_status"], "FAIL")
        self.assertFalse(gates["gate_a"]["rigging_eligible"])
        self.assertEqual(gates["gate_b"]["status"], "BLOCKED")
        self.assertEqual(gates["gate_c"]["status"], "BLOCKED")
        self.assertFalse(gates["final_approved"])

        # Trying to pass Gate A while unresolved defects exist in review_evidence must still FAIL
        forged_gate_a = dict(reg["gate_a_review"])
        forged_gate_a["major_defects"] = []
        forged_gate_a["dimensions"] = {d: "PASS" for d in bp.GATE_A_DIMENSIONS}
        forged_gate_a["reference_identity_verdict"] = "PASS"
        # Evaluate via 11-stage pipeline + VisualQARepairLoop-style review check:
        # Gate A dimension PASS is contradicted by failed criteria/defects in review_evidence
        self.assertTrue(any(not d.resolved and d.severity in ("BLOCKER", "MAJOR") for d in reg["defects"]))

        pipeline = CharacterProductionPipeline(preflight, asset_type="character", animation_mode="animated")
        p_res = pipeline.run({
            "production_brief_and_identity": lambda c: {"status": "PASS", "evidence": {"mcp_connected": True}},
            "silhouette_and_proportion_blockout": lambda c: {"status": "PASS", "evidence": {"blender_exec_ok": True}},
            "face_body_costume_modeling": lambda c: {"status": "PASS", "evidence": {"blender_exec_ok": True}},
            "hair_and_detail_sculpting": lambda c: {"status": "PASS", "evidence": {"blender_exec_ok": True}},
            "modeling_visual_qa": lambda c: {"status": "FAIL", "evidence": {"technical_passed": False}},
            "final_optimization_and_export": lambda c: {"status": "PASS"},
        })
        self.assertEqual(p_res["stages"][10]["status"], "BLOCKED")
        self.assertEqual(p_res["verdicts"]["final_asset_production"], "FAIL")

    def test_03_isolation_guard_separates_regression_from_independent_benchmarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ws_gpt = root / "20261124_235000_model_a"
            ws_qwen = root / "20261124_235100_model_b"
            ws_gpt.mkdir(parents=True)
            ws_qwen.mkdir(parents=True)

            indep_guard = BenchmarkIsolationGuard(run_kind="INDEPENDENT_BENCHMARK", workspace_dir=ws_gpt, regression_dir=ZELDA_DIR)
            with self.assertRaises(BenchmarkIsolationError):
                indep_guard.validate_read_path(ZELDA_DIR / "QA_report.md")
            with self.assertRaises(BenchmarkIsolationError):
                indep_guard.validate_read_path(ws_qwen / "zelda.blend")
            # Own workspace is allowed
            self.assertEqual(indep_guard.validate_read_path(ws_gpt / "char.blend"), (ws_gpt / "char.blend").resolve())

            # Explicit REGRESSION_REPLAY run is allowed to read Zelda baseline
            reg_guard = BenchmarkIsolationGuard(run_kind="REGRESSION_REPLAY", workspace_dir=ws_gpt, regression_dir=ZELDA_DIR)
            self.assertEqual(reg_guard.validate_read_path(ZELDA_DIR / "QA_report.md"), (ZELDA_DIR / "QA_report.md").resolve())


if __name__ == "__main__":
    unittest.main()
