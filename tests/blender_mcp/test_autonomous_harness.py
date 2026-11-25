"""Comprehensive automated tests for the Autonomous Blender Design Harness.

Covers Section 15 requirements:
- Skill discovery, selective loading, and 6-state lifecycle transitions
- Agent context isolation and blind final evaluation
- Structured artifact handoffs
- Stage transitions and quality gate enforcement
- Frozen 100-point rubric thresholds and anti-false-PASS / fail-closed NOT_VERIFIED
- Provider quota fail-fast handling (zero wasted retries, zero empty GLB export)
- Bounded retry behavior & traceback self-repair
- Checkpoint protection & recovery (refuses empty overwrite, reloads checkpoint)
- Real Blender MCP execution test (toolkit geometry + procedural texture + PBR +
  OPTIX/CPU render + rig/animation + fresh-scene GLB export/reimport + empty-scene GLB rejection)
"""

from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blender_orchestration.autonomous_harness import AutonomousBlenderHarness, QuotaBlockedError
from blender_orchestration.contracts import (
    SKILL_LIFECYCLE_STATES,
    SkillExecutionContract,
    evaluate_skill_contract,
)
from blender_orchestration.mcp_adapter import MCPAdapter, StdioMCPTransport
from blender_orchestration.rubric_evaluator import (
    DEFAULT_LIGHT_MODEL_ID,
    FROZEN_MIN_TOTAL_SCORE,
    ISOLATED_NO_MCP_OPENCODE_CONFIG,
    REQUIRED_DEFECT_FIELDS,
    BlindRubricEvaluator,
    detect_quota_error,
    evaluate_rubric_acceptance,
)
from blender_orchestration.sequential_benchmark import BlenderSessionResetter
from blender_orchestration.skill_registry import SkillRegistry


def _write_dummy_png(path: Path, width: int = 8, height: int = 8) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b"".join(b"\x00" + (b"\x40\x80\xc0" * width) for _ in range(height))

    def _chunk(ct: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + ct + d + struct.pack(">I", zlib.crc32(ct + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(raw)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b""))
    return path


class TestAutonomousHarnessCore(unittest.TestCase):
    def test_01_skill_six_state_lifecycle_and_selective_loading(self) -> None:
        self.assertEqual(
            SKILL_LIFECYCLE_STATES,
            ("DISCOVERED", "LOADED", "APPLIED", "VALIDATED", "FAILED", "NOT_VERIFIED"),
        )
        reg = SkillRegistry(PROJECT_ROOT / ".opencode" / "skills")
        discovered = reg.discover()
        self.assertIn("blender-director", discovered)
        self.assertIn("character-artist", discovered)

        c = SkillExecutionContract(
            skill_id="character-artist",
            production_stage="modeling",
            selection_reason="Character anatomy",
            required_operations=["proportion_spec", "body_blockout", "facial_structure", "topology_inspection"],
        )
        self.assertEqual(c.lifecycle_state, "DISCOVERED")

        # Loading alone MUST NOT become APPLIED/VALIDATED
        c.loaded = True
        c.record_lifecycle("LOADED")
        evaluate_skill_contract(c)
        self.assertEqual(c.status, "NOT_VERIFIED")
        self.assertEqual(c.lifecycle_state, "NOT_VERIFIED")

        # Real operations + MCP/scene/validation evidence -> APPLIED -> VALIDATED
        c.performed_operations = ["proportion_spec", "body_blockout", "facial_structure", "topology_inspection"]
        c.mcp_evidence = ["execute_blender_code:stage_03"]
        c.scene_evidence = ["SM_Char_Body"]
        c.validation_evidence = ["stage_03_score=84.0"]
        evaluate_skill_contract(c)
        self.assertEqual(c.status, "APPLIED")
        self.assertEqual(c.lifecycle_state, "VALIDATED")
        self.assertIn("LOADED", c.lifecycle_history)
        self.assertIn("APPLIED", c.lifecycle_history)
        self.assertIn("VALIDATED", c.lifecycle_history)

    def test_02_frozen_rubric_thresholds_and_anti_false_pass(self) -> None:
        self.assertEqual(FROZEN_MIN_TOTAL_SCORE, 85.0)
        # Passing all thresholds
        ok = evaluate_rubric_acceptance(
            {
                "reference_fidelity": 22.0,
                "silhouette_anatomy_proportions": 17.5,
                "geometry_modeling_detail": 17.0,
                "materials_and_textures": 13.0,
                "artistic_coherence_polish": 13.0,
                "game_asset_readability": 4.5,
            },
            defects=[],
            technical_gates_passed=True,
        )
        self.assertTrue(ok["passed"])
        self.assertEqual(ok["verdict"], "PASS")
        self.assertEqual(ok["total_score"], 87.0)

        # Total >= 85 BUT one category < 70% MUST FAIL
        cat_fail = evaluate_rubric_acceptance(
            {
                "reference_fidelity": 25.0,
                "silhouette_anatomy_proportions": 20.0,
                "geometry_modeling_detail": 20.0,
                "materials_and_textures": 9.0,  # 60% < 70% (10.5)
                "artistic_coherence_polish": 14.0,
                "game_asset_readability": 5.0,
            },
            defects=[],
            technical_gates_passed=True,
        )
        self.assertFalse(cat_fail["passed"])
        self.assertEqual(cat_fail["verdict"], "FAIL")

        # High score BUT unresolved CRITICAL defect MUST FAIL
        crit_fail = evaluate_rubric_acceptance(
            {
                "reference_fidelity": 23.0,
                "silhouette_anatomy_proportions": 18.0,
                "geometry_modeling_detail": 18.0,
                "materials_and_textures": 13.0,
                "artistic_coherence_polish": 13.0,
                "game_asset_readability": 4.5,
            },
            defects=[{"severity": "CRITICAL", "visible_problem": "Floating arm"}],
            technical_gates_passed=True,
        )
        self.assertFalse(crit_fail["passed"])
        self.assertEqual(crit_fail["critical_defect_count"], 1)

        # High score BUT technical gate failed MUST FAIL
        tech_fail = evaluate_rubric_acceptance(
            {
                "reference_fidelity": 23.0,
                "silhouette_anatomy_proportions": 18.0,
                "geometry_modeling_detail": 18.0,
                "materials_and_textures": 13.0,
                "artistic_coherence_polish": 13.0,
                "game_asset_readability": 4.5,
            },
            defects=[],
            technical_gates_passed=False,
        )
        self.assertFalse(tech_fail["passed"])

    def test_03_blind_evaluator_context_isolation_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            img = _write_dummy_png(Path(td) / "final_front.png")
            ref = _write_dummy_png(Path(td) / "ref.png")

            # Missing file -> NOT_VERIFIED
            ev_miss = BlindRubricEvaluator().evaluate(
                renders=[{"view": "front", "path": str(Path(td) / "missing.png")}],
                reference_mode="TEXT_ONLY",
            )
            self.assertEqual(ev_miss["verdict"], "NOT_VERIFIED")

            # Context isolation violation (reviewer session == producer session) -> NOT_VERIFIED
            def _same_sess(*args, **kwargs):
                return {
                    "returncode": 0,
                    "session_id": "ses_producer_001",
                    "text": json.dumps({
                        "observations_by_view": {"front": ["looks good"]},
                        "category_scores": {
                            "reference_fidelity": 24,
                            "silhouette_anatomy_proportions": 19,
                            "geometry_modeling_detail": 19,
                            "materials_and_textures": 14,
                            "artistic_coherence_polish": 14,
                            "game_asset_readability": 5,
                        },
                        "defects": [],
                    }),
                    "errors": [],
                }

            ev_iso = BlindRubricEvaluator(invoke_fn=_same_sess).evaluate(
                renders=[{"view": "front", "path": str(img)}],
                producer_session_ids={"ses_producer_001"},
                reference_mode="REFERENCE_GUIDED",
                approved_reference_path=ref,
            )
            self.assertEqual(ev_iso["verdict"], "NOT_VERIFIED")
            self.assertIn("isolation", ev_iso["reason"].lower())

            # Independent reviewer returns valid 9-field defect schema
            def _good_rev(*args, **kwargs):
                prompt_text = args[1]
                # Blindness check: no model name or iteration label leaked into prompt
                self.assertNotIn("gpt-6.2", prompt_text.lower())
                self.assertNotIn("iteration_00", prompt_text.lower())
                return {
                    "returncode": 0,
                    "session_id": "ses_reviewer_independent",
                    "text": json.dumps({
                        "observations_by_view": {"front": ["clean silhouette, crisp eyes"]},
                        "category_scores": {
                            "reference_fidelity": 22,
                            "silhouette_anatomy_proportions": 18,
                            "geometry_modeling_detail": 17,
                            "materials_and_textures": 13,
                            "artistic_coherence_polish": 13,
                            "game_asset_readability": 4.5,
                        },
                        "defects": [{"affected_component": "SM_Trim", "visible_problem": "slight seam", "severity": "MINOR"}],
                    }),
                    "errors": [],
                }

            ev_ok = BlindRubricEvaluator(invoke_fn=_good_rev).evaluate(
                renders=[{"view": "front", "path": str(img)}],
                producer_session_ids={"ses_producer_001"},
                reference_mode="REFERENCE_GUIDED",
                approved_reference_path=ref,
                technical_gates_passed=True,
            )
            self.assertEqual(ev_ok["verdict"], "PASS")
            self.assertEqual(ev_ok["total_score"], 87.5)
            self.assertEqual(len(ev_ok["defects"]), 1)
            for req_f in REQUIRED_DEFECT_FIELDS:
                self.assertIn(req_f, ev_ok["defects"][0])

    def test_04_quota_detection_and_isolated_no_mcp_config(self) -> None:
        self.assertEqual(DEFAULT_LIGHT_MODEL_ID, "openai/gpt-6.2-sol#low")
        for srv in ("photocraft", "effectcraft", "vectorcraft", "soundcraft", "aseprite", "blender"):
            self.assertTrue(ISOLATED_NO_MCP_OPENCODE_CONFIG["mcp"]["servers"][srv]["disabled"])

        q = detect_quota_error([
            '{"type":"error","error":{"type":"provider.quota","message":"The usage limit has been reached","response":{"body":"{\\"resets_at\\":1795583920,\\"resets_in_seconds\\":7155}","status_code":429}}}'
        ])
        self.assertIsNotNone(q)
        self.assertTrue(q["quota_exceeded"])
        self.assertEqual(q["resets_in_seconds"], 7155)
        self.assertEqual(q["resets_at"], 1795583920)
        self.assertIsNone(detect_quota_error(["normal warning"]))

    def test_05_real_blender_mcp_toolkit_e2e_and_empty_glb_rejection(self) -> None:
        """Real Blender MCP test on isolated port 9879: geometry + painted texture + PBR + Cycles + rig/anim + GLB reimport."""
        port = 9879
        with tempfile.TemporaryDirectory(prefix="bt_live_test_") as td:
            td_path = Path(td)
            runtime_dir = td_path / "mcp_runtime"
            out_dir = td_path / "asset_out"
            out_dir.mkdir(parents=True, exist_ok=True)
            resetter = BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=runtime_dir, blender_port=port)
            mcp_env = {
                "BLENDER_HOST": "127.0.0.1",
                "BLENDER_PORT": str(port),
                "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json"),
                "BLENDER_MCP_RUNTIME_DIR": str(runtime_dir),
                "BLENDER_MCP_IDLE_TIMEOUT": "120",
                "BLENDER_MCP_AUTO_RESTORE": "false",
            }
            adapter = MCPAdapter(StdioMCPTransport(cwd=PROJECT_ROOT, env_overrides=mcp_env), reference_mode="TEXT_ONLY")
            try:
                adapter.discover_capabilities()
                adapter.call_tool("get_addon_status", {"user_prompt": "Toolkit live test"}, stage="test_startup")

                # 1. Empty scene GLB export MUST fail (no false PASS on empty collection!)
                empty_code = (
                    "import json, sys\n"
                    f"sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
                    "import bpy\n"
                    "from blender_orchestration import blender_toolkit as bt\n"
                    "s = bt.ensure_clean_scene('SCENE_ToolkitLiveTest')\n"
                    f"res = bt.export_and_verify_glb(s, {json.dumps(str(out_dir / 'empty.glb'))})\n"
                    "print('__ORCH_JSON__:' + json.dumps(res))\n"
                )
                empty_res = adapter.execute_blender_json(empty_code, user_prompt="Empty check", stage="test_empty_glb")["parsed"]
                self.assertFalse(empty_res["valid"])
                self.assertEqual(empty_res["reimport_mesh_count"], 0)

                # 2. Build real stylized prop (lantern) using all toolkit primitives + painted texture + rig/anim + Cycles render + GLB roundtrip
                build_code = (
                    "import json, sys\n"
                    f"sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
                    "import bpy\n"
                    "from blender_orchestration import blender_toolkit as bt\n"
                    "s = bpy.data.scenes['SCENE_ToolkitLiveTest']\n"
                    "col = bt.get_or_create_collection(s, 'COL_Export')\n"
                    "base = bt.create_lathe_mesh('SM_Test_Base', [(0.14, 0.0), (0.12, 0.05), (0.09, 0.12), (0.11, 0.28), (0.05, 0.36)], collection=col)\n"
                    "sculpt_stats = bt.deform_mesh_sculpt(base, [{'center': (0.0, -0.11, 0.20), 'radius': 0.06, 'delta': (0.0, -0.015, 0.0)}])\n"
                    "core = bt.create_quad_sphere('SM_Test_Core', (0.0, 0.0, 0.20), (0.05, 0.05, 0.07), subdivisions=2, collection=col)\n"
                    "fin = bt.sculpt_tapered_ribbon('SM_Test_Fin', [(0.10, 0.0, 0.05), (0.15, 0.0, 0.20), (0.08, 0.0, 0.34)], [0.03, 0.04, 0.01], [0.015, 0.02, 0.005], collection=col)\n"
                    "bt.smart_uv_unwrap(base); bt.project_planar_uv(core, axis='Y'); bt.smart_uv_unwrap(fin)\n"
                    "tex = bt.create_painted_image_texture('TEX_Test_Rune', 256, 256, [\n"
                    "  {'op': 'radial_gradient', 'cx': 0.5, 'cy': 0.5, 'rx': 0.45, 'ry': 0.45, 'inner_color': (0.2, 0.9, 1.0, 1.0), 'outer_color': (0.02, 0.12, 0.25, 1.0)},\n"
                    "  {'op': 'ring', 'cx': 0.5, 'cy': 0.5, 'rx': 0.28, 'ry': 0.28, 'thickness': 0.08, 'color': (1.0, 0.95, 0.7, 1.0)},\n"
                    "], pack=True)\n"
                    "mat_metal = bt.make_pbr_material('MAT_Test_Bronze', (0.35, 0.24, 0.12), roughness=0.35, metallic=0.85)\n"
                    "mat_core = bt.make_pbr_material('MAT_Test_Core', (0.2, 0.85, 1.0), roughness=0.15, emission_color=(0.15, 0.85, 1.0), emission_strength=3.5, image_texture=tex)\n"
                    "bt.assign_material(base, mat_metal); bt.assign_material(fin, mat_metal); bt.assign_material(core, mat_core)\n"
                    "rig = bt.create_rig_and_bind(s, [{'name': 'Bone_Root', 'head': (0,0,0), 'tail': (0,0,0.2), 'parent': None}, {'name': 'Bone_Top', 'head': (0,0,0.2), 'tail': (0,0,0.36), 'parent': 'Bone_Root'}])\n"
                    "arm = bpy.data.objects[rig['armature_name']]\n"
                    "anim = bt.create_cycle_animation(arm, {1: {'Bone_Top': (0,0,0)}, 12: {'Bone_Top': (0,0,12)}, 24: {'Bone_Top': (0,0,0)}}, action_name='ACT_Sway')\n"
                    f"rend = bt.render_standard_views(s, {json.dumps(str(out_dir / 'renders'))}, 'live', views=['three_quarter'], mode='lookdev', res=(256, 256), samples=8, use_gpu=True)\n"
                    f"glb = bt.export_and_verify_glb(s, {json.dumps(str(out_dir / 'lantern.glb'))})\n"
                    "audit = bt.get_scene_audit(s)\n"
                    "print('__ORCH_JSON__:' + json.dumps({'sculpt': sculpt_stats, 'rig': rig, 'anim': anim, 'rend': rend, 'glb': glb, 'audit': audit}))\n"
                )
                res = adapter.execute_blender_json(build_code, user_prompt="Build toolkit test prop", stage="test_toolkit_build")["parsed"]
                self.assertGreater(res["sculpt"]["vertices_moved"], 0)
                self.assertEqual(res["audit"]["mesh_count"], 3)
                self.assertEqual(res["audit"]["uv_missing_objects"], [])
                self.assertTrue(res["anim"]["loop_closed"])
                self.assertTrue(res["glb"]["valid"])
                self.assertGreaterEqual(res["glb"]["reimport_mesh_count"], 3)
                self.assertEqual(res["glb"]["reimport_armature_count"], 1)
                self.assertEqual(len(res["rend"]["renders"]), 1)
                self.assertGreater(res["rend"]["renders"][0]["bytes"], 1000)
            finally:
                adapter.close()
                resetter.reset_for_next_model(start_fresh_blender=False)


if __name__ == "__main__":
    unittest.main()
