"""Automated unit & live Blender MCP integration tests for Qwen3.8-Flash-Next Base Mesh PoC."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blender_orchestration.blender_toolkit import collect_resource_snapshot  # noqa: E402
from blender_orchestration.mcp_adapter import (  # noqa: E402
    MCPAdapter,
    PolicyViolationError,
    StdioMCPTransport,
    verify_approved_base_mesh_manifest,
)
from blender_orchestration.rubric_evaluator import (  # noqa: E402
    QWEN_FLASH_NEXT_MODEL_ID,
    build_isolated_opencode_env,
    verify_model_id_lock,
)
from blender_orchestration.sequential_benchmark import BlenderSessionResetter  # noqa: E402

MANIFEST = PROJECT_ROOT / "assets/approved_base_meshes/blender_human_base_meshes/provenance.json"
TEST_PORT = 9878
RUNTIME_DIR = PROJECT_ROOT / "output/autonomous_harness/_mcp_runtime_test"


class TestQwenBaseMeshPoC(unittest.TestCase):
    def test_01_model_lock_blocks_silent_substitution(self) -> None:
        self.assertEqual(
            verify_model_id_lock(QWEN_FLASH_NEXT_MODEL_ID, QWEN_FLASH_NEXT_MODEL_ID),
            QWEN_FLASH_NEXT_MODEL_ID,
        )
        with self.assertRaises(ValueError):
            verify_model_id_lock("openai/gpt-6.2-sol#low", QWEN_FLASH_NEXT_MODEL_ID)
        with self.assertRaises(ValueError):
            verify_model_id_lock("", QWEN_FLASH_NEXT_MODEL_ID)

    def test_02_base_mesh_provenance_fail_closed(self) -> None:
        if not MANIFEST.is_file():
            self.skipTest(f"Official base mesh manifest not downloaded at {MANIFEST}")
        prov = verify_approved_base_mesh_manifest(MANIFEST)
        self.assertTrue(prov["verified"])
        self.assertEqual(prov["license"], "CC0-1.0")
        self.assertTrue(Path(prov["blend_path"]).is_file())

        with tempfile.TemporaryDirectory() as tmp:
            bad_manifest = Path(tmp) / "bad_provenance.json"
            bad_data = json.loads(MANIFEST.read_text(encoding="utf-8"))
            bad_data["files"]["blend_library"]["sha256"] = "0" * 64
            bad_manifest.write_text(json.dumps(bad_data), encoding="utf-8")
            with self.assertRaises(PolicyViolationError):
                verify_approved_base_mesh_manifest(bad_manifest)

            bad_license = Path(tmp) / "bad_license.json"
            bad_l = json.loads(MANIFEST.read_text(encoding="utf-8"))
            bad_l["license"] = "PROPRIETARY"
            bad_license.write_text(json.dumps(bad_l), encoding="utf-8")
            with self.assertRaises(PolicyViolationError):
                verify_approved_base_mesh_manifest(bad_license)

    def test_03_isolated_xdg_opencode_env_strips_global_mcps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_global = Path(tmp) / "global.json"
            fake_global.write_text(
                json.dumps(
                    {
                        "model": QWEN_FLASH_NEXT_MODEL_ID,
                        "mcp": {"servers": {"photocraft": {"command": ["photocraft"]}, "blender": {"command": ["blender"]}}},
                    }
                ),
                encoding="utf-8",
            )
            env = build_isolated_opencode_env(Path(tmp) / "iso", global_config_path=fake_global)
            iso_cfg = json.loads((Path(env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(iso_cfg["mcp"], {"servers": {}})
            self.assertEqual(iso_cfg["model"], QWEN_FLASH_NEXT_MODEL_ID)

    def test_04_resource_snapshot_health(self) -> None:
        snap = collect_resource_snapshot()
        self.assertIn("gpus", snap)
        self.assertIn("ram_total_mib", snap)
        self.assertIsInstance(snap["gpus"], list)

    def test_05_live_blender_base_mesh_semantic_garment_hair_grip_and_glb(self) -> None:
        if not MANIFEST.is_file():
            self.skipTest(f"Official base mesh manifest not downloaded at {MANIFEST}")

        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        resetter = BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=RUNTIME_DIR, blender_port=TEST_PORT)
        resetter.reset_for_next_model(start_fresh_blender=True)
        env = {
            "BLENDER_HOST": "127.0.0.1",
            "BLENDER_PORT": str(TEST_PORT),
            "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode/mcp/blender/config.json"),
            "BLENDER_MCP_RUNTIME_DIR": str(RUNTIME_DIR),
            "BLENDER_MCP_IDLE_TIMEOUT": "300",
            "BLENDER_MCP_WATCHDOG_POLL": "1.0",
            "BLENDER_MCP_AUTO_RESTORE": "false",
        }
        adapter = MCPAdapter(StdioMCPTransport(cwd=PROJECT_ROOT, env_overrides=env), reference_mode="TEXT_ONLY")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                glb_path = Path(tmp) / "test_base_mesh.glb"
                code = (
                    "import json, sys\n"
                    f"if {json.dumps(str(PROJECT_ROOT))} not in sys.path:\n"
                    f"    sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
                    "import bpy\n"
                    "from blender_orchestration import blender_toolkit as bt\n"
                    "from blender_orchestration import base_mesh_ops as bmo\n"
                    "scene = bt.ensure_clean_scene('SCENE_TestBaseMesh')\n"
                    "col = bt.get_or_create_collection(scene, 'COL_Export')\n"
                    f"res = bmo.verify_and_load_base_mesh(scene, {json.dumps(str(MANIFEST))}, 'GEO-body_female_stylized', target_height_m=1.65, collection=col, new_name='SM_BaseBody')\n"
                    "body = res['object']\n"
                    "sty = bmo.apply_semantic_stylization(body, {'head_scale': 0.1, 'eye_size': 0.2, 'ear_pointedness': 0.8, 'waist_taper': -0.15})\n"
                    "tunic = bmo.extract_garment_from_body(body, 'SM_TestTunic', z_min=0.72, z_max=1.32, x_abs_max=0.22, surface_offset_m=0.008, thickness_m=0.004, bottom_flare=0.06, collection=col)\n"
                    "hc = sty['landmarks_after']['head_center']\n"
                    "hair = bmo.build_layered_curve_hair('SM_Hair', body, primary_locks=[{'points': [(hc[0]+0.06, hc[1]-0.03, hc[2]+0.06), (hc[0]+0.09, hc[1]-0.04, hc[2]-0.05), (hc[0]+0.08, hc[1]-0.01, hc[2]-0.18)], 'radii': [0.9, 0.7, 0.2]}], collection=col)\n"
                    "prop = bt.create_beveled_panel('SM_TestSlate', [(-0.06,-0.09),(0.06,-0.09),(0.06,0.09),(-0.06,0.09)], depth=0.014, collection=col)\n"
                    "bt.smart_uv_unwrap(prop)\n"
                    "grip = bmo.pose_hand_and_attach_prop(body, side='L', prop_obj=prop)\n"
                    "m_skin = bt.make_pbr_material('MAT_Skin', '#F4C9AA', roughness=0.52)\n"
                    "m_tunic = bt.make_pbr_material('MAT_Tunic', '#1F5FC4', roughness=0.78)\n"
                    "m_hair = bt.make_pbr_material('MAT_Hair', '#E6B84D', roughness=0.38)\n"
                    "bt.assign_material(body, m_skin)\n"
                    "bt.assign_material(tunic, m_tunic)\n"
                    "for hn in hair['created_objects']:\n"
                    "    bt.assign_material(bpy.data.objects[hn], m_hair)\n"
                    "bt.assign_material(prop, m_tunic)\n"
                    f"exp = bt.export_and_verify_glb(scene, {json.dumps(str(glb_path))})\n"
                    "audit = bt.get_scene_audit(scene)\n"
                    "print('__ORCH_JSON__:' + json.dumps({'load_eyes': res['companion_eyes'], 'sty_moved': sty['vertices_modified'], 'hair': hair, 'grip': grip, 'exp': exp, 'audit': audit}))\n"
                )
                out = adapter.execute_blender_json(code, user_prompt="Test Base Mesh PoC", stage="test_base_mesh")["parsed"]
                self.assertGreater(len(out["load_eyes"]), 0)
                self.assertGreater(out["sty_moved"], 100)
                self.assertGreaterEqual(len(out["hair"]["created_objects"]), 2)
                self.assertGreater(out["grip"]["curled_vertices"], 5)
                self.assertTrue(out["exp"]["valid"])
                self.assertEqual(out["audit"]["uv_missing_objects"], [])
                self.assertAlmostEqual(out["audit"]["min_z_m"], 0.0, delta=0.02)
        finally:
            adapter.close()
            resetter.reset_for_next_model(start_fresh_blender=False)


if __name__ == "__main__":
    unittest.main()
