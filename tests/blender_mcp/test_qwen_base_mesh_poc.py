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
            perms = {p["action"]: p["effect"] for p in iso_cfg.get("permissions", [])}
            for act in ("edit", "write", "shell", "read", "glob", "grep", "subagent", "webfetch"):
                self.assertEqual(perms.get(act), "deny")

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
                    "eye_l_pts = [v.co for v in bpy.data.objects['SM_BaseBody_Eye_L'].data.vertices]\n"
                    "actual_eye_z = 0.5 * (min(v.z for v in eye_l_pts) + max(v.z for v in eye_l_pts))\n"
                    "lm_eye_z = res['landmarks']['landmarks']['eye_L'][2]\n"
                    "hand_y_before = res['landmarks']['landmarks']['hand_L'][1]\n"
                    "sty = bmo.apply_semantic_stylization(body, {'head_scale': 0.1, 'eye_size': 0.2, 'ear_pointedness': 0.8, 'waist_taper': -0.15})\n"
                    "arm_pose = bmo.pose_arm_for_prop_hold(body, side='L', shoulder_pitch_deg=32.0, shoulder_inward_deg=18.0, elbow_bend_deg=72.0)\n"
                    "hand_y_after = arm_pose['hand_L'][1]\n"
                    "face_res = bmo.style_face_features(body, collection=col)\n"
                    "sleeve_l = bmo.extract_sleeve_from_arm(body, 'SM_TestSleeveL', side='L', collection=col)\n"
                    "sleeve_min_y = min((sleeve_l.matrix_world @ v.co).y for v in sleeve_l.data.vertices)\n"
                    "tunic = bmo.extract_garment_from_body(body, 'SM_TestTunic', z_min=0.95, z_max=1.32, x_abs_max=0.18, surface_offset_m=0.008, thickness_m=0.004, collection=col)\n"
                    "skirt = bmo.create_flared_skirt_from_waist(body, 'SM_TestSkirt', hem_z=0.64, flare_radius_add=0.095, collection=col)\n"
                    "skirt_xs = [(skirt.matrix_world @ v.co).x for v in skirt.data.vertices]\n"
                    "skirt_zs = [(skirt.matrix_world @ v.co).z for v in skirt.data.vertices]\n"
                    "hem_trim = bmo.extract_hem_trim_from_garment(skirt, 'SM_TestHemTrim', edge='bottom', collection=col)\n"
                    "boots_res = bmo.create_fitted_boots(body, 'SM_Boots', shaft_top_z=0.30, collection=col)\n"
                    "boots_zs = [(boots_res['boots'].matrix_world @ v.co).z for v in boots_res['boots'].data.vertices]\n"
                    "hair = bmo.build_layered_curve_hair('SM_Hair', body, auto_crown_braid=True, auto_flowing_back={'length_m': 0.52}, auto_side_locks={'length_m': 0.30}, collection=col)\n"
                    "braid_obj = bpy.data.objects['SM_Hair_Braid_0']\n"
                    "braid_xs = [(braid_obj.matrix_world @ v.co).x for v in braid_obj.data.vertices]\n"
                    "braid_ys = [(braid_obj.matrix_world @ v.co).y for v in braid_obj.data.vertices]\n"
                    "prop = bt.create_beveled_panel('SM_TestPropSlate', [(-0.06,-0.09),(0.06,-0.09),(0.06,0.09),(-0.06,0.09)], depth=0.014, collection=col)\n"
                    "bt.smart_uv_unwrap(prop)\n"
                    "grip = bmo.pose_hand_and_attach_prop(body, side='L', prop_obj=prop)\n"
                    "integ_ok = bmo.audit_base_mesh_integrity(scene)\n"
                    "# Check fail-closed detection on extreme spike + chest-lips + eye-occluding hair card + studio backdrop purge\n"
                    "spike_obj = bt.create_beveled_panel('SM_SpikeTmp', [(-0.02,0),(0.02,0),(0.02,3.2),(-0.02,3.2)], depth=0.01, collection=col)\n"
                    "integ_spike = bmo.audit_base_mesh_integrity(scene)\n"
                    "bt.remove_object_if_exists('SM_SpikeTmp')\n"
                    "chest_lip = bt.create_beveled_panel('GEO_Mouth_Lips', [(-0.01,-0.01),(0.01,-0.01),(0.01,0.01),(-0.01,0.01)], depth=0.01, location=(0.0,-0.1,1.15), collection=col)\n"
                    "integ_chest_lip = bmo.audit_base_mesh_integrity(scene)\n"
                    "bt.remove_object_if_exists('GEO_Mouth_Lips')\n"
                    "eyez = sty['landmarks_after']['eye_L'][2]\n"
                    "eyey = sty['landmarks_after']['eye_L'][1]\n"
                    "bad_card = bt.create_beveled_panel('SM_Hair_ManeCards', [(-0.06,-0.06),(0.06,-0.06),(0.06,0.06),(-0.06,0.06)], depth=0.14, location=(0.0, eyey - 0.06, eyez - 0.07), collection=col)\n"
                    "integ_eye_occ = bmo.audit_base_mesh_integrity(scene)\n"
                    "bt.remove_object_if_exists('SM_Hair_ManeCards')\n"
                    "eye_l_obj = bpy.data.objects['SM_BaseBody_Eye_L']\n"
                    "orig_eye_mats = list(eye_l_obj.data.materials)\n"
                    "eye_l_obj.data.materials.append(bt.make_pbr_material('MAT_EyeDummy2', '#FFFFFF'))\n"
                    "integ_eye_split = bmo.audit_base_mesh_integrity(scene)\n"
                    "eye_l_obj.data.materials.clear()\n"
                    "for em in orig_eye_mats:\n"
                    "    eye_l_obj.data.materials.append(em)\n"
                    "custom_col = bt.get_or_create_collection(scene, 'COL_STUDIO_LOOKDEV')\n"
                    "rogue_bg = bt.create_beveled_panel('SM_Studio_Backdrop', [(-3.0,-3.0),(3.0,-3.0),(3.0,3.0),(-3.0,3.0)], depth=0.05, location=(0,-2.4,1.0), collection=custom_col)\n"
                    "rogue_lgt_d = bpy.data.lights.new('Rogue_Light', type='SUN')\n"
                    "rogue_lgt = bpy.data.objects.new('Rogue_Light', rogue_lgt_d)\n"
                    "custom_col.objects.link(rogue_lgt)\n"
                    "bt._setup_studio_lights_and_world(scene, bt._compute_collection_bounds(scene))\n"
                    "backdrop_purged = (bpy.data.objects.get('SM_Studio_Backdrop') is None and bpy.data.objects.get('Rogue_Light') is None)\n"
                    "studio_dummy = bpy.data.objects.new('SM_Studio_ShadowCatcher', bpy.data.meshes.new('SM_Studio_DummyMesh'))\n"
                    "studio_dummy.is_shadow_catcher = True\n"
                    "col.objects.link(studio_dummy)\n"
                    "bt._ensure_export_meshes_renderable(scene)\n"
                    "studio_evicted = (studio_dummy.name not in [o.name for o in col.all_objects])\n"
                    "m_skin = bt.make_pbr_material('MAT_Skin', '#F4C9AA', roughness=0.52)\n"
                    "m_tunic = bt.make_pbr_material('MAT_Tunic', '#1F5FC4', roughness=0.78)\n"
                    "m_hair = bt.make_pbr_material('MAT_Hair', '#E6B84D', roughness=0.38)\n"
                    "m_boot = bt.make_pbr_material('MAT_Leather_Brown', '#523520', roughness=0.60)\n"
                    "m_gold = bt.make_pbr_material('MAT_Gold', '#D8A62E', roughness=0.28, metallic=0.9)\n"
                    "bt.assign_material(body, m_skin)\n"
                    "bt.assign_material(tunic, m_tunic)\n"
                    "bt.assign_material(skirt, m_tunic)\n"
                    "bt.assign_material(sleeve_l, m_tunic)\n"
                    "bt.assign_material(hem_trim, m_gold)\n"
                    "bt.assign_material(boots_res['boots'], m_boot)\n"
                    "bt.assign_material(boots_res['cuff'], m_skin)\n"
                    "for hn in hair['created_objects']:\n"
                    "    bt.assign_material(bpy.data.objects[hn], m_hair)\n"
                    "bt.assign_material(prop, m_tunic)\n"
                    "bt.create_rig_and_bind(scene, [{'name': 'Root', 'head': (0,0,0), 'tail': (0,0,0.2), 'parent': None}, {'name': 'Hand_L', 'head': tuple(arm_pose['wrist_L']), 'tail': tuple(arm_pose['hand_L']), 'parent': 'Root'}])\n"
                    "# Move armature into lowercase col_export to verify fallback armature export selection!\n"
                    "arm_o = bpy.data.objects['ARM_Asset']\n"
                    "lc_col = bt.get_or_create_collection(scene, 'col_export')\n"
                    "bt.link_to_collection(arm_o, lc_col)\n"
                    "prop_vgs = [g.name for g in prop.vertex_groups if any(vg.group == g.index and vg.weight > 0.99 for v in prop.data.vertices for vg in v.groups)]\n"
                    f"exp = bt.export_and_verify_glb(scene, {json.dumps(str(glb_path))})\n"
                    "audit = bt.get_scene_audit(scene)\n"
                    "print('__ORCH_JSON__:' + json.dumps({'rot_deg': res['rotate_z_deg'], 'actual_eye_z': actual_eye_z, 'lm_eye_z': lm_eye_z, 'face_res': face_res, 'sleeve_min_y': sleeve_min_y, 'skirt_min_z': min(skirt_zs), 'skirt_x_span': max(skirt_xs)-min(skirt_xs), 'boots_min_z': min(boots_zs), 'boots_max_z': max(boots_zs), 'braid_x_span': max(braid_xs)-min(braid_xs), 'braid_y_span': max(braid_ys)-min(braid_ys), 'hand_y_before': hand_y_before, 'hand_y_after': hand_y_after, 'integ_ok': integ_ok, 'integ_spike_ok': integ_spike['ok'], 'integ_chest_lip_ok': integ_chest_lip['ok'], 'integ_eye_occ_ok': integ_eye_occ['ok'], 'integ_eye_split_ok': integ_eye_split['ok'], 'backdrop_purged': backdrop_purged, 'studio_evicted': studio_evicted, 'prop_vgs': prop_vgs, 'load_eyes': res['companion_eyes'], 'sty_moved': sty['vertices_modified'], 'hair': hair, 'grip': grip, 'exp': exp, 'audit': audit}))\n"
                )
                out = adapter.execute_blender_json(code, user_prompt="Test Base Mesh PoC", stage="test_base_mesh")["parsed"]
                self.assertEqual(out["rot_deg"], 0.0)
                self.assertAlmostEqual(out["lm_eye_z"], out["actual_eye_z"], delta=0.005)
                self.assertTrue(out["face_res"]["ok"])
                self.assertLess(out["sleeve_min_y"], -0.10)
                self.assertAlmostEqual(out["skirt_min_z"], 0.64, delta=0.025)
                self.assertGreater(out["skirt_x_span"], 0.30)
                self.assertGreaterEqual(out["boots_min_z"], -0.005)
                self.assertLessEqual(out["boots_max_z"], 0.36)
                self.assertGreater(out["braid_x_span"], out["braid_y_span"])
                self.assertLess(out["hand_y_after"], out["hand_y_before"] - 0.05)
                self.assertTrue(out["integ_ok"]["ok"], out["integ_ok"])
                self.assertFalse(out["integ_spike_ok"])
                self.assertFalse(out["integ_chest_lip_ok"])
                self.assertFalse(out["integ_eye_occ_ok"])
                self.assertFalse(out["integ_eye_split_ok"])
                self.assertTrue(out["backdrop_purged"])
                self.assertGreaterEqual(out["exp"]["reimport_armature_count"], 1)
                self.assertTrue(out["studio_evicted"])
                self.assertEqual(out["prop_vgs"], ["Hand_L"])
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
