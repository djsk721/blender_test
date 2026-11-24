"""Regression tests T01-T14 + Blender-Only Policy + Installer Portability."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
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
    BlenderDirector,
    MCPAdapter,
    MockMCPTransport,
    SkillExecutionContract,
    SkillRegistry,
    StdioMCPTransport,
    ToolNotSupportedError,
    build_reproducibility_manifest,
    evaluate_asset_qa,
    evaluate_character_gates,
    evaluate_skill_contract,
)
from blender_orchestration.mcp_adapter import PolicyViolationError  # noqa: E402

SKILLS_ROOT = PROJECT_ROOT / ".opencode" / "skills"
AGENTS_MD = PROJECT_ROOT / ".opencode" / "AGENTS.md"
INSTALL_SH = PROJECT_ROOT / "scripts" / "install.sh"
GLOBAL_OPENCODE_JSON = Path.home() / ".config" / "opencode" / "opencode.json"


def _sample_mcp_tools(*, with_look: bool = True) -> list[dict]:
    capture_tool = (
        {
            "name": "look",
            "description": "See the scene as one image.",
            "inputSchema": {"type": "object", "properties": {"mode": {"type": "string"}, "shading": {"type": "string"}}, "required": []},
        }
        if with_look
        else {
            "name": "get_viewport_screenshot",
            "description": "Upstream screenshot name.",
            "inputSchema": {"type": "object", "properties": {}, "required": []},
        }
    )
    return [
        {"name": "get_addon_status", "inputSchema": {"type": "object", "properties": {}, "required": []}},
        {"name": "get_scene_info", "inputSchema": {"type": "object", "properties": {"fields": {"type": "array"}}, "required": []}},
        {"name": "execute_blender_code", "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
        capture_tool,
        {"name": "generate_3d", "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": []}},
        {"name": "generate_gaussian_world", "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": []}},
        {"name": "search_assets", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"]}},
        {"name": "import_asset", "inputSchema": {"type": "object", "properties": {"source": {"type": "string"}, "id": {"type": "string"}}, "required": ["source", "id"]}},
    ]


class TestOrchestrationArchitecture(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillRegistry(SKILLS_ROOT)
        self.director = BlenderDirector(self.registry)

    def test_T01_dynamic_skill_routing_stylized_character(self) -> None:
        plan = self.director.plan_production(
            "Create a stylized fantasy human character for Godot with a clean run animation, without reference images.",
            reference_mode="TEXT_ONLY",
            asset_name="HeroCharacter",
        )
        selected_ids = [s["skill_id"] for s in plan["selected_skills"]]
        self.assertEqual(selected_ids[0], "blender-director")
        for expected in (
            "character-artist",
            "stylized-style",
            "sculpting",
            "retopology",
            "uv-workflow",
            "materials",
            "qa-review",
            "rigging",
            "animation",
            "godot-export",
        ):
            self.assertIn(expected, selected_ids)

        for forbidden in ("vehicle-artist", "environment-artist", "vegetation-artist", "hard-surface", "reference-image-match"):
            self.assertNotIn(forbidden, selected_ids)

        omitted_ids = {o["skill_id"] for o in plan["omitted_skills"]}
        self.assertIn("vehicle-artist", omitted_ids)
        self.assertIn("environment-artist", omitted_ids)

    def test_T02_different_asset_type_hard_surface_prop(self) -> None:
        plan = self.director.plan_production(
            "Build a realistic sci-fi hard-surface weapon rifle for export to GLB.",
            reference_mode="TEXT_ONLY",
            asset_name="SciFiRifle",
        )
        selected_ids = [s["skill_id"] for s in plan["selected_skills"]]
        self.assertEqual(selected_ids[0], "blender-director")
        self.assertIn("hard-surface", selected_ids)
        self.assertIn("realistic-style", selected_ids)
        self.assertIn("uv-workflow", selected_ids)
        self.assertIn("materials", selected_ids)
        self.assertIn("export-pipeline", selected_ids)
        for unrelated in ("character-artist", "hair-groom", "rigging", "animation", "sculpting", "retopology"):
            self.assertNotIn(unrelated, selected_ids)

    def test_T03_native_skill_discovery(self) -> None:
        skills = self.registry.discover()
        self.assertEqual(len(skills), 94)
        self.assertTrue(self.registry.shared_references_dir().is_dir())
        for name in ("blender-director", "character-artist", "prop-artist", "sculpting", "retopology", "uv-workflow", "materials", "qa-review", "export-pipeline", "godot-export"):
            self.assertIn(name, skills)
            self.assertEqual(skills[name].broken_references, [], f"Broken references in {name}: {skills[name].broken_references}")

        # Verify on-demand loading includes both SKILL.md and shared references
        loaded = self.registry.load_skill("blender-director", include_references=["../references/asset-pipeline.md", "../references/mcp-integration.md"])
        self.assertIn("Blender Director", loaded["content"])
        self.assertEqual(loaded["missing_references"], [])
        self.assertIn("../references/asset-pipeline.md", loaded["loaded_references"])

    def test_T04_skill_loading_vs_application(self) -> None:
        contract = SkillExecutionContract(
            skill_id="materials",
            production_stage="materials",
            selection_reason="Stylized crate shading",
            loaded=True,
            required_operations=["master_materials_mat_prefix", "bsdf_parameters"],
            performed_operations=[],
            mcp_evidence=[],
            scene_evidence=[],
            validation_evidence=[],
            status="PENDING",
        )
        evaluated = evaluate_skill_contract(contract)
        self.assertNotEqual(evaluated.status, "APPLIED")
        self.assertEqual(evaluated.status, "NOT_VERIFIED")

    def test_T05_sculpting_evidence_rejects_subsurf_only(self) -> None:
        contract = SkillExecutionContract(
            skill_id="sculpting",
            production_stage="sculpt_refinement",
            selection_reason="Organic character detailing",
            loaded=True,
            required_operations=["sculpt_or_form_refinement", "before_after_geometry", "form_validation"],
            performed_operations=["subdivision_surface"],
            mcp_evidence=["execute_blender_code:subsurf"],
            scene_evidence=["Modifier SUBSURF added"],
            validation_evidence=["polycount=24000"],
            evidence_details={"subsurf_only": True},
        )
        evaluated = evaluate_skill_contract(contract)
        self.assertEqual(evaluated.status, "NOT_VERIFIED")
        self.assertTrue(any("Subdivision Surface" in note for note in evaluated.evaluation_notes))

    def test_T06_retopology_evidence_rejects_decimate_only(self) -> None:
        contract = SkillExecutionContract(
            skill_id="retopology",
            production_stage="topology",
            selection_reason="Animated hero character topology",
            loaded=True,
            required_operations=["topology_refinement", "quad_dominant_check", "joint_edge_loops", "poly_budget_validation"],
            performed_operations=["decimate"],
            mcp_evidence=["execute_blender_code:decimate"],
            scene_evidence=["tris=9000"],
            validation_evidence=["budget ok"],
            evidence_details={"decimate_only": True, "quad_ratio": 0.35},
        )
        evaluated = evaluate_skill_contract(contract, asset_tier="hero")
        self.assertEqual(evaluated.status, "NOT_VERIFIED")
        self.assertTrue(any("decimation" in note.lower() for note in evaluated.evaluation_notes))

    def test_T07_mcp_tool_compatibility_and_blender_only_guardrails(self) -> None:
        adapter = MCPAdapter(MockMCPTransport(tools=_sample_mcp_tools(with_look=True)))
        caps = adapter.discover_capabilities()
        self.assertIn("look", caps["tools"])
        self.assertNotIn("get_viewport_screenshot", caps["tools"])

        # Dynamically resolves visual_capture to live `look` rather than assuming `get_viewport_screenshot`
        resolved_vis = adapter.resolve_tool("visual_capture")
        self.assertEqual(resolved_vis["tool_name"], "look")

        # Calling non-existent upstream tool fails cleanly
        with self.assertRaises(ToolNotSupportedError):
            adapter.call_tool("get_viewport_screenshot", {})

        # External AI 3D generators and IMAGE_TO_3D are unconditionally blocked
        for tool, args in (
            ("generate_3d", {"prompt": "crate", "provider": "tripo"}),
            ("generate_gaussian_world", {"prompt": "room"}),
        ):
            with self.assertRaises(PolicyViolationError):
                adapter.call_tool(tool, args)

        with self.assertRaises(PolicyViolationError):
            adapter.call_tool("search_assets", {"source": "polyhaven"})

        self.assertEqual(adapter.ai_generator_calls_blocked, 2)
        self.assertEqual(adapter.external_asset_calls_blocked, 1)

        # IMAGE_TO_3D rejected in Director and Preflight
        with self.assertRaises(ValueError):
            self.director.plan_production("Create lantern", reference_mode="IMAGE_TO_3D")
        pf_img3d = bp.resolve_preflight_policy("Use image-to-3d with Tripo to make a lantern", agents_md_path=AGENTS_MD)
        self.assertEqual(pf_img3d["status"], "BLOCKED")
        self.assertFalse(pf_img3d["modeling_permitted"])
        self.assertFalse(pf_img3d["external_ai_3d_generators_allowed"])

    def test_T08_scene_persistence_across_calls(self) -> None:
        scene_state: dict[str, list[str]] = {"SCENE_Asset": []}

        def handler(name: str, args: dict) -> dict:
            if name == "execute_blender_code" and "create_crate" in args.get("code", ""):
                scene_state["SCENE_Asset"].extend(["SM_Crate_Body", "SM_Crate_Iron"])
                return {"content": [{"type": "text", "text": '__ORCH_JSON__:{"created":2}'}], "isError": False}
            if name == "get_scene_info":
                txt = f"Scene SCENE_Asset objects: {', '.join(scene_state['SCENE_Asset'])}"
                return {"content": [{"type": "text", "text": txt}], "isError": False}
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

        adapter = MCPAdapter(MockMCPTransport(tools=_sample_mcp_tools(), handler=handler))
        adapter.discover_capabilities()
        adapter.execute_blender_json("# create_crate", user_prompt="test", stage="modeling")
        info = adapter.call_tool("get_scene_info", {"fields": ["location", "size"]}, stage="verify")
        self.assertIn("SM_Crate_Body", info["text"])
        self.assertIn("SM_Crate_Iron", info["text"])

    def test_T09_scene_reinitialization_idempotent(self) -> None:
        scenes: dict[str, dict] = {}

        def handler(name: str, args: dict) -> dict:
            if name == "execute_blender_code":
                sc = scenes.setdefault("SCENE_Test", {"collections": set(), "dups": []})
                for col in ("COL_Reference", "COL_Export", "COL_Collision", "COL_Lights"):
                    sc["collections"].add(col)
                payload = {
                    "scene_name": "SCENE_Test",
                    "scene_count": len(scenes),
                    "collection_names": sorted(sc["collections"]),
                    "numeric_suffix_duplicates": sc["dups"],
                }
                return {"content": [{"type": "text", "text": "__ORCH_JSON__:" + json.dumps(payload)}], "isError": False}
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

        adapter = MCPAdapter(MockMCPTransport(tools=_sample_mcp_tools(), handler=handler))
        adapter.discover_capabilities()
        r1 = adapter.execute_blender_json("init", user_prompt="init", stage="init1")["parsed"]
        r2 = adapter.execute_blender_json("init", user_prompt="init", stage="init2")["parsed"]
        self.assertEqual(r1["scene_count"], 1)
        self.assertEqual(r2["scene_count"], 1)
        self.assertEqual(r1["collection_names"], r2["collection_names"])
        self.assertEqual(r2["numeric_suffix_duplicates"], [])

    def test_T10_quality_gate_enforcement(self) -> None:
        preflight = bp.resolve_preflight_policy("Create character without using any reference images.", agents_md_path=AGENTS_MD)

        # Gate A fail blocks Gate B and Gate C
        fail_a = {
            "rendered_images_inspected": True,
            "independent_reviewer": True,
            "major_defects": ["Asymmetric hand topology"],
            "dimensions": {d: "PASS" for d in bp.GATE_A_DIMENSIONS},
        }
        gates_a_fail = evaluate_character_gates(preflight, gate_a_review=fail_a)
        self.assertEqual(gates_a_fail["gate_a"]["modeling_quality_status"], "FAIL")
        self.assertEqual(gates_a_fail["gate_b"]["status"], "BLOCKED")
        self.assertEqual(gates_a_fail["gate_c"]["status"], "BLOCKED")
        self.assertFalse(gates_a_fail["final_approved"])

        # Gate B fail blocks Gate C even when Gate A passes
        pass_a = {
            "rendered_images_inspected": True,
            "independent_reviewer": True,
            "major_defects": [],
            "dimensions": {d: "PASS" for d in bp.GATE_A_DIMENSIONS},
        }
        fail_b = {
            "rendered_images_inspected": True,
            "independent_reviewer": True,
            "major_defects": ["Severe elbow mesh pinching"],
            "checks": {c: "PASS" for c in bp.GATE_B_CHECKS},
        }
        gates_b_fail = evaluate_character_gates(preflight, gate_a_review=pass_a, gate_b_review=fail_b)
        self.assertEqual(gates_b_fail["gate_a"]["modeling_quality_status"], "PASS")
        self.assertEqual(gates_b_fail["gate_b"]["status"], "FAIL")
        self.assertEqual(gates_b_fail["gate_c"]["status"], "BLOCKED")
        self.assertFalse(gates_b_fail["final_approved"])

        # Static asset QA: rendered file exists without visual inspection -> NOT_VERIFIED
        uninspected = evaluate_asset_qa(
            rendered_images=["crate.png"],
            visually_inspected=False,
            independent_reviewer=True,
            findings=["none"],
            major_defects=[],
            technical_checks_passed=True,
            within_poly_budget=True,
        )
        self.assertEqual(uninspected["status"], "NOT_VERIFIED")

    def test_T11_benchmark_reference_modes(self) -> None:
        pf_text = bp.resolve_preflight_policy("Create Princess Zelda without using any reference images.", agents_md_path=AGENTS_MD)
        self.assertEqual(pf_text["benchmark_mode"], "TEXT_ONLY")
        self.assertTrue(pf_text["modeling_permitted"])
        self.assertEqual(pf_text["status"], "READY")

        pf_ref_missing = bp.resolve_preflight_policy("Exact visual matching is required, but the approved references are missing.", agents_md_path=AGENTS_MD)
        self.assertEqual(pf_ref_missing["benchmark_mode"], "REFERENCE_GUIDED")
        self.assertFalse(pf_ref_missing["modeling_permitted"])
        self.assertEqual(pf_ref_missing["status"], "BLOCKED")

        # Validate reference-image-match contract rejects missing analysis/passes and accepts real evidence
        ref_contract = SkillExecutionContract(
            skill_id="reference-image-match",
            production_stage="reference_analysis_and_match",
            selection_reason="REFERENCE_GUIDED workflow",
            loaded=True,
            performed_operations=["reference_analysis", "camera_match", "blender_modeling_passes", "reference_comparison_loop"],
            mcp_evidence=["look:pass1", "look:pass2"],
            scene_evidence=["CAM_Lantern_Hero created before modeling"],
            validation_evidence=["comparison_ref_vs_renders.png inspected"],
            evidence_details={
                "approved_references": ["approved_references/ref_alchemy_lantern.png"],
                "reference_analysis_written": True,
                "camera_correspondence_set": True,
                "built_in_blender": True,
                "ai_3d_generators_used": False,
                "comparison_renders": ["render_pass1.png", "render_pass2.png"],
                "comparison_passes": 2,
                "visually_inspected": True,
            },
        )
        self.assertEqual(evaluate_skill_contract(ref_contract).status, "APPLIED")

    def test_T12_lifecycle_lazy_loading_and_global_config_regression(self) -> None:
        global_sha_before = hashlib.sha256(GLOBAL_OPENCODE_JSON.read_bytes()).hexdigest() if GLOBAL_OPENCODE_JSON.is_file() else None
        blender_before = subprocess.run(["pgrep", "-x", "blender"], capture_output=True, text=True, check=False).stdout.strip()

        adapter = MCPAdapter(StdioMCPTransport(cwd=PROJECT_ROOT))
        try:
            caps = adapter.discover_capabilities()
            self.assertEqual(caps["server_info"]["name"], "MCP for Blender")
            self.assertEqual(adapter.resolve_tool("visual_capture")["tool_name"], "look")
        finally:
            adapter.close()

        blender_after = subprocess.run(["pgrep", "-x", "blender"], capture_output=True, text=True, check=False).stdout.strip()
        self.assertEqual(blender_before, blender_after)
        if GLOBAL_OPENCODE_JSON.is_file():
            self.assertEqual(hashlib.sha256(GLOBAL_OPENCODE_JSON.read_bytes()).hexdigest(), global_sha_before)

    def test_T13_reproducibility_manifest(self) -> None:
        manifest = build_reproducibility_manifest(
            "Create a small game-ready stylized fantasy crate without reference images.",
            run_id="20261124_175000_test",
            project_root=PROJECT_ROOT,
            mcp_server_info={"name": "MCP for Blender", "version": "1.30.0"},
            discovered_tools=["get_addon_status", "get_scene_info", "execute_blender_code", "look"],
            model_id="test-model",
            provider="test-provider",
            output_dir="e2e_runs/test",
        )
        self.assertEqual(len(manifest["prompt_sha256"]), 64)
        self.assertEqual(len(manifest["agents_md_sha256"]), 64)
        self.assertEqual(len(manifest["skills_revision_hash"]), 64)
        self.assertEqual(manifest["skills_count"], 94)
        self.assertTrue(manifest["preflight"]["blender_only_production"])

    def test_T14_cross_model_consistency_no_hardcoded_routing(self) -> None:
        prompt = "Create Princess Zelda without using any reference images, animated with a run cycle for Godot."
        pf_gpt = bp.build_benchmark_metadata(prompt, run_id="r1", model_id="gpt-6.2-sol", provider="openai", agents_md_path=AGENTS_MD)
        pf_qwen = bp.build_benchmark_metadata(prompt, run_id="r2", model_id="sc117-qwen38-abliterated-iq3_s", provider="strata", agents_md_path=AGENTS_MD)
        self.assertEqual(pf_gpt["preflight"], pf_qwen["preflight"])

        plan_a = self.director.plan_production(prompt, reference_mode=pf_gpt["preflight"]["benchmark_mode"], asset_name="Zelda")
        plan_b = self.director.plan_production(prompt, reference_mode=pf_qwen["preflight"]["benchmark_mode"], asset_name="Zelda")
        self.assertEqual([s["skill_id"] for s in plan_a["selected_skills"]], [s["skill_id"] for s in plan_b["selected_skills"]])

        registry_src = (PROJECT_ROOT / "blender_orchestration" / "skill_registry.py").read_text(encoding="utf-8").lower()
        for model_tok in ("gpt-6", "qwen", "strata"):
            self.assertNotIn(model_tok, registry_src)

    def test_installer_portability_opt_in_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "portable_target"
            (target / ".opencode").mkdir(parents=True)
            existing_agents = "# Existing Project Rules\nKeep my custom rules.\n"
            (target / ".opencode" / "AGENTS.md").write_text(existing_agents, encoding="utf-8")

            # Mini OpenCode skill source fixture (not .claude/skills/)
            mini_skills = Path(tmp) / "src_skills"
            (mini_skills / "blender-director").mkdir(parents=True)
            (mini_skills / "references").mkdir(parents=True)
            (mini_skills / "blender-director" / "SKILL.md").write_text("---\nname: blender-director\n---\n# Director\n", encoding="utf-8")
            (mini_skills / "references" / "asset-pipeline.md").write_text("# Pipeline\n", encoding="utf-8")

            res = subprocess.run(
                [
                    str(INSTALL_SH),
                    "--with-orchestration",
                    "--with-policy",
                    "--skills-source",
                    str(mini_skills),
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            # Existing AGENTS.md must NOT be overwritten without --merge-policy
            self.assertEqual((target / ".opencode" / "AGENTS.md").read_text(encoding="utf-8"), existing_agents)
            self.assertTrue((target / ".opencode" / "AGENTS.blender-skills.md").is_file())
            self.assertTrue((target / ".opencode" / "orchestration" / "blender_orchestration" / "skill_registry.py").is_file())
            self.assertTrue((target / ".opencode" / "orchestration" / "scripts" / "benchmark_preflight.py").is_file())
            self.assertTrue((target / ".opencode" / "skills" / "blender-director" / "SKILL.md").is_file())
            self.assertTrue((target / ".opencode" / "skills" / "references" / "asset-pipeline.md").is_file())

            # Update & Uninstall work cleanly without touching user AGENTS.md or .opencode/skills/
            upd = subprocess.run([str(PROJECT_ROOT / "scripts" / "update.sh"), str(target)], capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(upd.returncode, 0, upd.stderr + upd.stdout)
            uni = subprocess.run([str(PROJECT_ROOT / "scripts" / "uninstall.sh"), "--yes", str(target)], capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(uni.returncode, 0, uni.stderr + uni.stdout)
            self.assertEqual((target / ".opencode" / "AGENTS.md").read_text(encoding="utf-8"), existing_agents)
            self.assertFalse((target / ".opencode" / "orchestration" / "scripts" / "benchmark_preflight.py").exists())
            self.assertTrue((target / ".opencode" / "skills" / "references" / "asset-pipeline.md").is_file())


if __name__ == "__main__":
    unittest.main()
