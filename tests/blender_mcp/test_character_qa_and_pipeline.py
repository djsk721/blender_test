"""Unit and integration tests for character QA profiles, visual QA repair loop, 11-stage pipeline, and benchmark fairness."""

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
    AssetQAProfileType,
    CharacterProductionPipeline,
    ImageObservation,
    RepairLoopPolicy,
    SkillExecutionContract,
    VisualDefect,
    VisualQARepairLoop,
    VisualReviewEvidence,
    analyze_character_scene_metrics,
    compute_approval_policy_digest,
    detect_polygon_inflation,
    evaluate_asset_qa,
    evaluate_skill_contract,
    evaluate_verified_character_gates,
    get_qa_profile,
    hash_file_sha256,
    validate_profile_criteria_hygiene,
)

AGENTS_MD = PROJECT_ROOT / ".opencode" / "AGENTS.md"


def _write_dummy_png(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + data)
    return hash_file_sha256(path)


class TestCharacterQAProfilesAndVisualLoop(unittest.TestCase):
    def test_01_five_asset_profiles_and_character_hygiene(self) -> None:
        hum = get_qa_profile("character", animated=False, reference_mode="TEXT_ONLY")
        anim = get_qa_profile("character", animated=True, reference_mode="TEXT_ONLY")
        cre = get_qa_profile("creature", animated=False, reference_mode="TEXT_ONLY")
        hs = get_qa_profile("hard_surface_prop", animated=False, reference_mode="TEXT_ONLY")
        env = get_qa_profile("environment", animated=False, reference_mode="TEXT_ONLY")

        self.assertEqual(hum.profile_id, AssetQAProfileType.HUMANOID_CHARACTER)
        self.assertEqual(anim.profile_id, AssetQAProfileType.ANIMATED_CHARACTER)
        self.assertEqual(cre.profile_id, AssetQAProfileType.CREATURE)
        self.assertEqual(hs.profile_id, AssetQAProfileType.HARD_SURFACE_PROP)
        self.assertEqual(env.profile_id, AssetQAProfileType.ENVIRONMENT)

        # Character profile itself must be completely free of ship/hull/metal-panel criteria
        hygiene = validate_profile_criteria_hygiene(hum)
        self.assertTrue(hygiene["hygienic"], hygiene)

        # Injecting ship/hull criteria into a character checklist must fail hygiene check
        contaminated = validate_profile_criteria_hygiene(
            hum,
            ["Windows, ports, vents placed on hull surfaces", "Metal panel greeble density"],
        )
        self.assertFalse(contaminated["hygienic"])
        self.assertGreaterEqual(len(contaminated["violations"]), 2)
        self.assertTrue({"hull", "metal panel", "greeble"}.issubset({v["forbidden_keyword"] for v in contaminated["violations"]}))

        # Mode distinction: REFERENCE_GUIDED vs TEXT_ONLY
        ref_hum = get_qa_profile("character", animated=False, reference_mode="REFERENCE_GUIDED")
        self.assertIn("reference_identity_match", ref_hum.criterion_ids())
        self.assertNotIn("reference_identity_match", hum.criterion_ids())
        self.assertIn("text_specification_compliance", hum.criterion_ids())

    def test_02_scene_metrics_and_polygon_inflation_detection(self) -> None:
        bad_audit = {
            "mesh_objects": 140,
            "triangles": 125000,
            "material_count": 24,
            "meshes": [{"name": "Tube_01", "tris": 1000, "non_manifold_edges": 4, "loose_verts": 2, "uv_layers": 0, "scale_applied": False}],
            "character_spatial_checks": {
                "scalp_gap_detected": True,
                "max_thigh_seam_gap_m": 0.04,
                "eye_protrusion_m": 0.02,
                "hair_face_penetration_ratio": 0.15,
                "hand_prop_gap_m": 0.05,
            },
        }
        res = analyze_character_scene_metrics(bad_audit, triangle_budget=100000, material_budget=8, max_fragment_objects=50)
        self.assertFalse(res["passed_technical_geometry_checks"])
        defect_types = {d["defect_type"] for d in res["detected_defects"]}
        for expected in (
            "triangle_budget_overrun",
            "primitive_stacking",
            "material_sprawl",
            "mesh_topology_errors",
            "missing_uv_layers",
            "unapplied_transforms",
            "scalp_exposure",
            "floating_thigh_seam",
            "round_protruding_eyes",
            "hair_face_penetration",
            "stiff_fingers_bad_grip",
        ):
            self.assertIn(expected, defect_types)

        # Increasing polygons by >20% without reducing defects must be flagged
        inf = detect_polygon_inflation(
            {"triangles": 10000, "open_defect_count": 3, "quality_score": 5.0},
            {"triangles": 35000, "open_defect_count": 3, "quality_score": 5.0},
        )
        self.assertTrue(inf["inflation_only_detected"])

        valid_refinement = detect_polygon_inflation(
            {"triangles": 10000, "open_defect_count": 3, "quality_score": 5.0},
            {"triangles": 15000, "open_defect_count": 1, "quality_score": 8.0},
        )
        self.assertFalse(valid_refinement["inflation_only_detected"])

    def test_03_anti_forging_visual_qa_enforcement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img_path = root / "render_front.png"
            blend_path = root / "char.blend"
            img_sha = _write_dummy_png(img_path, b"front_view_v1")
            blend_path.write_bytes(b"BLENDER-v503")
            blend_sha = hash_file_sha256(blend_path)

            # 1. Bare booleans without review_evidence MUST NOT pass evaluate_asset_qa
            spoofed = evaluate_asset_qa(
                rendered_images=[str(img_path)],
                visually_inspected=True,
                independent_reviewer=True,
                findings=["looks great"],
                major_defects=[],
                technical_checks_passed=True,
                within_poly_budget=True,
                review_evidence=None,
            )
            self.assertEqual(spoofed["status"], "NOT_VERIFIED")
            self.assertFalse(spoofed["evidence_verified"])

            profile = get_qa_profile("character", reference_mode="TEXT_ONLY")
            policy = RepairLoopPolicy()
            digest = compute_approval_policy_digest(profile, policy)

            # 2. Self-approval (reviewer_context_id == producer_context_id) rejected
            self_ev = VisualReviewEvidence(
                reviewer_id="agent_a",
                reviewer_context_id="same_ctx",
                producer_context_id="same_ctx",
                reviewed_at="2026-11-24T23:50:00Z",
                target_asset_path=str(blend_path),
                target_asset_sha256=blend_sha,
                profile_id=profile.profile_id,
                reference_mode="TEXT_ONLY",
                image_observations=[ImageObservation(str(img_path), img_sha, "front", ["Clean face & silhouette"])],
                criteria_verdicts={c.criterion_id: "PASS" for c in profile.criteria},
                approval_policy_digest=digest,
            )
            self_qa = evaluate_asset_qa(
                rendered_images=[str(img_path)],
                visually_inspected=True,
                independent_reviewer=True,
                findings=["Clean face & silhouette"],
                major_defects=[],
                technical_checks_passed=True,
                within_poly_budget=True,
                review_evidence=self_ev,
                expected_policy_digest=digest,
            )
            self.assertEqual(self_qa["status"], "NOT_VERIFIED")

            # 3. Valid independent hashed evidence passes
            valid_ev = VisualReviewEvidence(
                reviewer_id="qa_subagent_01",
                reviewer_context_id="ctx_qa_independent",
                producer_context_id="ctx_producer_01",
                reviewed_at="2026-11-24T23:51:00Z",
                target_asset_path=str(blend_path),
                target_asset_sha256=blend_sha,
                profile_id=profile.profile_id,
                reference_mode="TEXT_ONLY",
                image_observations=[ImageObservation(str(img_path), img_sha, "front", ["Natural eyes, covered scalp, clean boot ankle."])],
                criteria_verdicts={c.criterion_id: "PASS" for c in profile.criteria},
                approval_policy_digest=digest,
            )
            valid_qa = evaluate_asset_qa(
                rendered_images=[str(img_path)],
                visually_inspected=True,
                independent_reviewer=True,
                findings=["Natural eyes, covered scalp, clean boot ankle."],
                major_defects=[],
                technical_checks_passed=True,
                within_poly_budget=True,
                review_evidence=valid_ev,
                expected_policy_digest=digest,
            )
            self.assertEqual(valid_qa["status"], "PASS")
            self.assertTrue(valid_qa["evidence_verified"])

            # 4. Tampered policy digest in Gate A -> FAIL (not PASS)
            preflight = bp.resolve_preflight_policy("Create character without using any reference images.", agents_md_path=AGENTS_MD)
            tampered_ev = VisualReviewEvidence(**{**valid_ev.__dict__, "approval_policy_digest": "0" * 64})
            gates = evaluate_verified_character_gates(
                preflight,
                gate_a_review={
                    "rendered_images_inspected": True,
                    "independent_reviewer": True,
                    "major_defects": [],
                    "dimensions": {d: "PASS" for d in bp.GATE_A_DIMENSIONS},
                    "visual_evidence": tampered_ev.to_dict(),
                    "expected_policy_digest": digest,
                },
            )
            self.assertEqual(gates["gate_a"]["modeling_quality_status"], "FAIL")
            self.assertFalse(gates["gate_a"]["rigging_eligible"])

    def test_04_repair_loop_fix_iteration_rerender_and_stall_policy(self) -> None:
        profile = get_qa_profile("character", reference_mode="TEXT_ONLY")
        policy = RepairLoopPolicy(max_fix_iterations=3)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blend_path = root / "char.blend"
            blend_path.write_bytes(b"BLENDER-test")
            blend_sha = hash_file_sha256(blend_path)

            # Case A: 1 fix resolves defect + 1 pure re-render does not count as a fix iteration
            state = {"fixed": False}

            def render_fn(pass_idx: int, views: list[str]) -> dict:
                p = root / f"render_{pass_idx}.png"
                sha = _write_dummy_png(p, f"frame_{pass_idx}_{state['fixed']}".encode())
                return {"renders": [{"view": "front", "path": str(p), "sha256": sha}]}

            def review_fn(bundle: dict, pass_idx: int, policy_digest: str) -> VisualReviewEvidence:
                r = bundle["renders"][0]
                verdicts = {c.criterion_id: "PASS" for c in profile.criteria}
                defects = []
                if not state["fixed"]:
                    verdicts["scalp_coverage_no_bald_exposure"] = "FAIL"
                    defects.append(
                        VisualDefect(
                            defect_id="D1",
                            defect_type="scalp_exposure",
                            severity="BLOCKER",
                            affected_objects=["SM_Char_Hair_Scalp"],
                            source_render=r["path"],
                            visual_problem="Bald patch behind braid",
                            required_fix_action="Expand scalp crown coverage",
                            qa_criterion_id="scalp_coverage_no_bald_exposure",
                        )
                    )
                return VisualReviewEvidence(
                    reviewer_id="qa_agent",
                    reviewer_context_id="ctx_qa",
                    producer_context_id="ctx_prod",
                    reviewed_at="2026-11-24T23:52:00Z",
                    target_asset_path=str(blend_path),
                    target_asset_sha256=blend_sha,
                    profile_id=profile.profile_id,
                    reference_mode="TEXT_ONLY",
                    image_observations=[ImageObservation(r["path"], r["sha256"], "front", ["Checked scalp coverage"])],
                    defects=defects,
                    criteria_verdicts=verdicts,
                    approval_policy_digest=policy_digest,
                )

            fix_calls = {"n": 0}

            def apply_fix(actions: list, fix_num: int) -> dict:
                fix_calls["n"] += 1
                if fix_calls["n"] == 1:
                    # First attempt only re-renders without modifying mesh (does not count as fix!)
                    return {"mesh_modified": False}
                state["fixed"] = True
                return {"mesh_modified": True, "modified_objects": ["SM_Char_Hair_Scalp"]}

            res = VisualQARepairLoop(profile, policy).run(render_fn=render_fn, review_fn=review_fn, apply_fix_fn=apply_fix)
            self.assertEqual(res["status"], "PASS")
            self.assertEqual(res["fix_iteration_count"], 1)
            self.assertEqual(res["rerender_count"], 3)
            self.assertTrue(res["before_after_comparisons"][-1]["improved"])

            # Case B: Stalled loop (same defect persists after identical fix) fails immediately instead of looping blindly
            def review_always_bad(bundle: dict, pass_idx: int, policy_digest: str) -> VisualReviewEvidence:
                r = bundle["renders"][0]
                verdicts = {c.criterion_id: "PASS" for c in profile.criteria}
                verdicts["bulbous_boots"] = "FAIL"
                return VisualReviewEvidence(
                    reviewer_id="qa_agent",
                    reviewer_context_id="ctx_qa",
                    producer_context_id="ctx_prod",
                    reviewed_at="2026-11-24T23:53:00Z",
                    target_asset_path=str(blend_path),
                    target_asset_sha256=blend_sha,
                    profile_id=profile.profile_id,
                    reference_mode="TEXT_ONLY",
                    image_observations=[ImageObservation(r["path"], r["sha256"], "front", ["Boots remain bulbous"])],
                    defects=[
                        VisualDefect(
                            defect_id="D2",
                            defect_type="bulbous_boots",
                            severity="MAJOR",
                            affected_objects=["SM_Char_Boots"],
                            source_render=r["path"],
                            visual_problem="Bulbous boots",
                            required_fix_action="Contour boot ankles",
                            qa_criterion_id="boot_instep_heel_ankle_anatomy",
                        )
                    ],
                    criteria_verdicts=verdicts,
                    approval_policy_digest=policy_digest,
                )

            stalled = VisualQARepairLoop(profile, policy).run(
                render_fn=render_fn,
                review_fn=review_always_bad,
                apply_fix_fn=lambda actions, n: {"mesh_modified": True},
            )
            self.assertEqual(stalled["status"], "FAIL")
            self.assertTrue(any("Stalled repair loop" in r for r in stalled["reasons"]))

    def test_05_eleven_stage_pipeline_blocks_on_gate_a_fail_and_separates_6_verdicts(self) -> None:
        preflight = bp.resolve_preflight_policy("Create animated Princess Zelda without reference images.", agents_md_path=AGENTS_MD)
        pipeline = CharacterProductionPipeline(preflight, asset_type="character", animation_mode="animated")
        downstream_called = False

        def mark_downstream(ctx: dict) -> dict:
            nonlocal downstream_called
            downstream_called = True
            return {"status": "PASS"}

        res = pipeline.run({
            "production_brief_and_identity": lambda c: {"status": "PASS", "evidence": {"mcp_connected": True}},
            "silhouette_and_proportion_blockout": lambda c: {"status": "PASS", "evidence": {"blender_exec_ok": True, "scene_objects": ["SM_Char_Body"]}},
            "face_body_costume_modeling": lambda c: {"status": "PASS", "evidence": {"blender_exec_ok": True, "scene_objects": ["SM_Char_Body", "SM_Char_Head"]}},
            "hair_and_detail_sculpting": lambda c: {"status": "PASS", "evidence": {"blender_exec_ok": True, "scene_objects": ["SM_Char_Body", "SM_Char_Head", "SM_Char_Hair_Scalp"]}},
            "modeling_visual_qa": lambda c: {"status": "FAIL", "evidence": {"technical_passed": True}},
            "topology_uv_material_validation": mark_downstream,
            "rigging_and_skin_weights": mark_downstream,
            "final_optimization_and_export": mark_downstream,
        })
        self.assertFalse(downstream_called)
        self.assertTrue(res["scene_continuity_preserved"])
        for st in res["stages"][5:]:
            self.assertEqual(st["status"], "BLOCKED")
        v = res["verdicts"]
        self.assertEqual(v["mcp_connection"], "PASS")
        self.assertEqual(v["blender_execution"], "PASS")
        self.assertEqual(v["geometry_and_technical_validation"], "PASS")
        self.assertEqual(v["visual_quality_validation"], "FAIL")
        self.assertEqual(v["final_asset_production"], "FAIL")

    def test_06_hair_groom_and_character_contract_validators(self) -> None:
        c_rope = SkillExecutionContract(
            skill_id="hair-groom",
            production_stage="hair",
            selection_reason="Character hair",
            loaded=True,
            performed_operations=["hair_blockout_clumps", "scalp_attachment", "hair_silhouette_check"],
            mcp_evidence=["execute_blender_code:hair"],
            scene_evidence=["SM_Hair_Tubes"],
            validation_evidence=["checked"],
            evidence_details={"rope_or_tube_only": True},
        )
        self.assertEqual(evaluate_skill_contract(c_rope).status, "NOT_VERIFIED")

        c_bald = SkillExecutionContract(
            skill_id="hair-groom",
            production_stage="hair",
            selection_reason="Character hair",
            loaded=True,
            performed_operations=["hair_blockout_clumps", "scalp_attachment", "hair_silhouette_check"],
            mcp_evidence=["execute_blender_code:hair"],
            scene_evidence=["SM_Hair_Scalp"],
            validation_evidence=["checked"],
            evidence_details={"scalp_exposure_detected": True},
        )
        self.assertEqual(evaluate_skill_contract(c_bald).status, "FAILED")

    def test_07_cross_model_fairness_and_skills_preflight(self) -> None:
        # Missing skills dir detected cleanly
        with tempfile.TemporaryDirectory() as tmp:
            missing_skills = bp.validate_required_skills(Path(tmp) / "no_skills")
            self.assertFalse(missing_skills["installed"])
            self.assertEqual(missing_skills["status"], "SKILLS_MISSING")
            self.assertTrue(missing_skills["remediation_steps"])

        # Present skills dir passes
        present = bp.validate_required_skills(PROJECT_ROOT / ".opencode" / "skills")
        self.assertTrue(present["installed"])
        self.assertEqual(present["status"], "READY")

        # Mismatched prompts reject fair direct comparison
        m1 = bp.build_benchmark_metadata("Create Zelda without reference images.", run_id="20261124_235000_modelA", model_id="modelA", agents_md_path=AGENTS_MD)
        m2 = bp.build_benchmark_metadata("Create Link without reference images.", run_id="20261124_235100_modelB", model_id="modelB", agents_md_path=AGENTS_MD)
        self.assertTrue(m1["run_id_format_valid"])
        cmp_res = bp.compare_run_environments(m1, m2)
        self.assertFalse(cmp_res["identical_prompt"])
        self.assertFalse(cmp_res["fair_direct_comparison_allowed"])
        self.assertIn("quality", cmp_res["separated_metrics"]["run_a"])
        self.assertIn("runtime", cmp_res["separated_metrics"]["run_a"])
        self.assertIn("tool_efficiency", cmp_res["separated_metrics"]["run_a"])


if __name__ == "__main__":
    unittest.main()
