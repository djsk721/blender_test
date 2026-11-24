"""Policy regression tests T01-T07 for benchmark mode resolution, QA gates, and MCP safety."""

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
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import benchmark_preflight as bp  # noqa: E402

AGENTS_MD = PROJECT_ROOT / ".opencode" / "AGENTS.md"
PROMPT_DOC = PROJECT_ROOT / "docs" / "benchmarks" / "princess-zelda-text-only.md"
PROJECT_OPENCODE_JSON = PROJECT_ROOT / "opencode.json"
WRAPPER_PATH = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "mcp_wrapper.py"
GLOBAL_OPENCODE_JSON = Path.home() / ".config" / "opencode" / "opencode.json"


def _passing_gate_a_review() -> dict:
    return {
        "rendered_images_inspected": True,
        "independent_reviewer": True,
        "major_defects": [],
        "dimensions": {dim: "PASS" for dim in bp.GATE_A_DIMENSIONS},
    }


def _passing_gate_b_review() -> dict:
    return {
        "rendered_images_inspected": True,
        "independent_reviewer": True,
        "major_defects": [],
        "checks": {check: "PASS" for check in bp.GATE_B_CHECKS},
    }


def _passing_gate_c_review() -> dict:
    return {
        "rendered_images_inspected": True,
        "independent_reviewer": True,
        "major_defects": [],
        "checks": {check: "PASS" for check in bp.GATE_C_CHECKS},
    }


class TestBenchmarkPolicyRegression(unittest.TestCase):
    def test_T01_text_only_named_character(self) -> None:
        prompt = "Create Princess Zelda without using any reference images."
        preflight = bp.resolve_preflight_policy(prompt, agents_md_path=AGENTS_MD)

        self.assertEqual(preflight["benchmark_mode"], "TEXT_ONLY")
        self.assertTrue(preflight["modeling_permitted"])
        self.assertFalse(preflight["reference_images_required"])
        self.assertFalse(preflight["reference_images_allowed"])
        self.assertTrue(preflight["text_specification_available"])
        self.assertFalse(preflight["exact_identity_fidelity_verifiable"])
        self.assertTrue(preflight["modeling_quality_gate_required"])
        self.assertTrue(preflight["independent_visual_review_required"])
        self.assertEqual(preflight["status"], "READY")
        self.assertNotEqual(preflight["status"], "BLOCKED")
        self.assertEqual(preflight["default_exact_identity_status"], "NOT_VERIFIED")

        # Also validate canonical Princess Zelda benchmark prompt document resolves cleanly to TEXT_ONLY
        canonical_text = PROMPT_DOC.read_text(encoding="utf-8")
        canonical_preflight = bp.resolve_preflight_policy(canonical_text, agents_md_path=AGENTS_MD)
        self.assertEqual(canonical_preflight["benchmark_mode"], "TEXT_ONLY")
        self.assertTrue(canonical_preflight["modeling_permitted"])
        self.assertEqual(canonical_preflight["status"], "READY")

        # Verify dynamic derivation: legacy AGENTS.md without TEXT_ONLY mode surfaces conflict instead of hardcoding READY
        with tempfile.TemporaryDirectory() as tmp:
            legacy_agents = Path(tmp) / "AGENTS.md"
            legacy_agents.write_text(
                "## Character Identity and Reference Lock\n2. Require explicit reference images before modeling.\n7. If references are missing, mark identity QA BLOCKED.\n",
                encoding="utf-8",
            )
            legacy_preflight = bp.resolve_preflight_policy(prompt, agents_md_path=legacy_agents)
            self.assertFalse(legacy_preflight["modeling_permitted"])
            self.assertEqual(legacy_preflight["status"], "BLOCKED")

    def test_T02_approved_reference_benchmark(self) -> None:
        prompt = "Recreate Princess Zelda using the supplied approved references."
        preflight = bp.resolve_preflight_policy(
            prompt,
            agents_md_path=AGENTS_MD,
            supplied_references=["refs/zelda_front.png", "refs/zelda_side.png"],
        )

        self.assertEqual(preflight["benchmark_mode"], "REFERENCE_GUIDED")
        self.assertTrue(preflight["reference_images_required"])
        self.assertTrue(preflight["reference_images_allowed"])
        self.assertTrue(preflight["modeling_permitted"])
        self.assertTrue(preflight["exact_identity_fidelity_verifiable"])
        self.assertEqual(preflight["status"], "READY")

        # Without actual reference comparison evidence, approval must NOT be granted
        no_evidence = bp.evaluate_gate_a(
            preflight,
            {
                **_passing_gate_a_review(),
                "reference_comparison_performed": False,
                "approved_references_used": True,
                "reference_identity_verdict": "PASS",
            },
        )
        self.assertEqual(no_evidence["exact_identity_fidelity_status"], "NOT_VERIFIED")
        self.assertFalse(no_evidence["rigging_eligible"])

        # With independent reference comparison and approved references, Gate A passes
        verified = bp.evaluate_gate_a(
            preflight,
            {
                **_passing_gate_a_review(),
                "reference_comparison_performed": True,
                "approved_references_used": True,
                "reference_identity_verdict": "PASS",
            },
        )
        self.assertEqual(verified["modeling_quality_status"], "PASS")
        self.assertEqual(verified["exact_identity_fidelity_status"], "PASS")
        self.assertTrue(verified["rigging_eligible"])

    def test_T03_missing_mandatory_references(self) -> None:
        prompt = "Exact visual matching is required, but the approved references are missing."
        preflight = bp.resolve_preflight_policy(prompt, agents_md_path=AGENTS_MD)

        self.assertEqual(preflight["benchmark_mode"], "REFERENCE_GUIDED")
        self.assertTrue(preflight["reference_images_required"])
        self.assertFalse(preflight["modeling_permitted"])
        self.assertEqual(preflight["status"], "BLOCKED")
        self.assertIn("missing", (preflight["blocker_reason"] or "").lower())
        self.assertEqual(preflight["default_exact_identity_status"], "BLOCKED")

        # Even if a review attempts to assert PASS, missing mandatory references prevent fabricated approval
        gate_a = bp.evaluate_gate_a(
            preflight,
            {
                **_passing_gate_a_review(),
                "reference_comparison_performed": True,
                "approved_references_used": True,
                "reference_identity_verdict": "PASS",
            },
        )
        self.assertEqual(gate_a["exact_identity_fidelity_status"], "BLOCKED")
        self.assertFalse(gate_a["rigging_eligible"])

        # Also check explicit empty reference list in REFERENCE_GUIDED prompt
        preflight_empty = bp.resolve_preflight_policy(
            "Recreate Princess Zelda using the supplied approved references.",
            agents_md_path=AGENTS_MD,
            supplied_references=[],
        )
        self.assertEqual(preflight_empty["status"], "BLOCKED")
        self.assertFalse(preflight_empty["modeling_permitted"])

    def test_T04_text_only_quality_failure(self) -> None:
        prompt = "Create Princess Zelda without using any reference images."
        preflight = bp.resolve_preflight_policy(prompt, agents_md_path=AGENTS_MD)

        bad_review = _passing_gate_a_review()
        bad_review["major_defects"] = ["Severely distorted facial proportions and floating hair mesh"]
        bad_review["dimensions"]["facial_proportions"] = "FAIL"

        gate_a = bp.evaluate_gate_a(preflight, bad_review)
        self.assertEqual(gate_a["modeling_quality_status"], "FAIL")
        self.assertEqual(gate_a["exact_identity_fidelity_status"], "NOT_VERIFIED")
        self.assertFalse(gate_a["rigging_eligible"])
        self.assertFalse(gate_a["animation_eligible"])

        gate_b = bp.evaluate_gate_b(gate_a, _passing_gate_b_review())
        self.assertEqual(gate_b["status"], "BLOCKED")
        self.assertFalse(gate_b["animation_eligible"])

        gate_c = bp.evaluate_gate_c(gate_b, _passing_gate_c_review())
        self.assertEqual(gate_c["status"], "BLOCKED")
        self.assertFalse(gate_c["export_eligible"])

        # Self-approval or script execution without rendered image inspection also cannot pass
        uninspected = _passing_gate_a_review()
        uninspected["rendered_images_inspected"] = False
        uninspected["independent_reviewer"] = False
        gate_a_unverified = bp.evaluate_gate_a(preflight, uninspected)
        self.assertEqual(gate_a_unverified["modeling_quality_status"], "NOT_VERIFIED")
        self.assertFalse(gate_a_unverified["rigging_eligible"])

    def test_T05_text_only_successful_modeling(self) -> None:
        prompt = "Create Princess Zelda without using any reference images."
        preflight = bp.resolve_preflight_policy(prompt, agents_md_path=AGENTS_MD)

        review = _passing_gate_a_review()
        # Even if a caller erroneously attempts to auto-pass exact identity, TEXT_ONLY enforces NOT_VERIFIED
        review["exact_identity_claimed_pass"] = True

        gate_a = bp.evaluate_gate_a(preflight, review)
        self.assertEqual(gate_a["modeling_quality_status"], "PASS")
        self.assertEqual(gate_a["exact_identity_fidelity_status"], "NOT_VERIFIED")
        self.assertTrue(gate_a["rigging_eligible"])

        gate_b = bp.evaluate_gate_b(gate_a, _passing_gate_b_review())
        self.assertEqual(gate_b["status"], "PASS")
        self.assertTrue(gate_b["animation_eligible"])

        gate_c = bp.evaluate_gate_c(gate_b, _passing_gate_c_review())
        self.assertEqual(gate_c["status"], "PASS")
        self.assertTrue(gate_c["export_eligible"])

    def test_T06_multiple_llm_consistency(self) -> None:
        prompt = PROMPT_DOC.read_text(encoding="utf-8")

        gpt_meta = bp.build_benchmark_metadata(
            prompt,
            run_id="20261124_160000_gpt-6.2-sol",
            model_id="gpt-6.2-sol",
            provider="openai-compatible",
            variant="default",
            reasoning_effort="high",
            inference_backend="cloud-api",
            hardware_allocation="remote",
            runtime_environment="opencode-v2.2.7",
            agents_md_path=AGENTS_MD,
        )
        qwen_meta = bp.build_benchmark_metadata(
            prompt,
            run_id="20261124_160000_qwen3.8",
            model_id="sc117-qwen38-abliterated-iq3_s",
            provider="strata",
            variant="iq3_s",
            reasoning_effort="medium",
            inference_backend="llama.cpp-strata",
            hardware_allocation="dual-rtx-3090",
            runtime_environment="opencode-v2.2.7",
            agents_md_path=AGENTS_MD,
        )

        # Identical resolved policy regardless of model/provider
        self.assertEqual(gpt_meta["preflight"], qwen_meta["preflight"])
        self.assertEqual(gpt_meta["preflight"]["benchmark_mode"], "TEXT_ONLY")
        self.assertTrue(gpt_meta["preflight"]["modeling_permitted"])

        comparison = bp.compare_run_environments(gpt_meta, qwen_meta)
        self.assertTrue(comparison["identical_preflight_policy"])
        self.assertFalse(comparison["strict_experimental_equivalence_claimable"])
        self.assertIn("model_id", comparison["uncontrolled_differences"])
        self.assertIn("provider", comparison["uncontrolled_differences"])

        # Ensure neither .opencode/AGENTS.md nor preflight module contains model-specific routing
        agents_text = AGENTS_MD.read_text(encoding="utf-8").lower()
        preflight_code = Path(bp.__file__).read_text(encoding="utf-8").lower()
        for forbidden in ("gpt-6.2", "gpt-5", "qwen3.8", "sc117-qwen"):
            self.assertNotIn(forbidden, agents_text)
            self.assertNotIn(forbidden, preflight_code)

    def test_T07_existing_infrastructure_regression(self) -> None:
        # 1. Verify AGENTS.md consistency and preserved 17-step production order
        policy = bp.parse_project_policy(AGENTS_MD)
        self.assertTrue(policy.exists)
        self.assertTrue(policy.supports_text_only)
        self.assertTrue(policy.supports_reference_guided)
        self.assertTrue(policy.defines_precedence)
        self.assertFalse(policy.has_unconditional_reference_blocker)

        agents_text = AGENTS_MD.read_text(encoding="utf-8")
        expected_order = [
            "- blender-director",
            "- character-archetypes",
            "- stylized-style",
            "- character-artist",
            "- sculpting",
            "- retopology",
            "- hair-groom",
            "- uv-workflow",
            "- texture-workflow",
            "- materials",
            "- qa-review",
            "- rigging",
            "- qa-review",
            "- animation",
            "- qa-review",
            "- export-pipeline",
            "- godot-export",
        ]
        # Extract the bullet list under ## Character Production
        prod_section = agents_text.split("## Character Production", 1)[1].split("Load supporting references on demand.", 1)[0]
        actual_order = [line.strip() for line in prod_section.splitlines() if line.strip().startswith("- ")]
        self.assertEqual(actual_order, expected_order)

        # 2. Verify project-local opencode.json still configures lazy MCP wrapper
        oc = json.loads(PROJECT_OPENCODE_JSON.read_text(encoding="utf-8"))
        self.assertEqual(
            oc["mcp"]["servers"]["blender"]["command"],
            ["python3", "-u", ".opencode/mcp/blender/mcp_wrapper.py"],
        )

        # 3. Record global ~/.config/opencode/opencode.json hash before & after MCP wrapper smoke check
        global_sha_before = hashlib.sha256(GLOBAL_OPENCODE_JSON.read_bytes()).hexdigest() if GLOBAL_OPENCODE_JSON.is_file() else None
        blender_procs_before = subprocess.run(["pgrep", "-x", "blender"], capture_output=True, text=True, check=False).stdout.strip()

        env = os.environ.copy()
        env["BLENDER_MCP_CONFIG"] = ".opencode/mcp/blender/config.json"
        proc = subprocess.Popen(
            [sys.executable, "-u", str(WRAPPER_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=env,
            text=True,
        )
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "policy-regression", "version": "1.0"},
                        },
                    }
                )
                + "\n"
            )
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
            proc.stdin.flush()

            init_resp = json.loads(proc.stdout.readline())
            list_resp = json.loads(proc.stdout.readline())
            self.assertEqual(init_resp["result"]["serverInfo"]["name"], "MCP for Blender")
            tools = {t["name"] for t in list_resp["result"]["tools"]}
            self.assertIn("get_scene_info", tools)
            self.assertIn("execute_blender_code", tools)
        finally:
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=10)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()

        # Lazy Loading: initialize + tools/list must not spawn any new Blender process
        blender_procs_after = subprocess.run(["pgrep", "-x", "blender"], capture_output=True, text=True, check=False).stdout.strip()
        self.assertEqual(blender_procs_after, blender_procs_before)

        if GLOBAL_OPENCODE_JSON.is_file():
            global_sha_after = hashlib.sha256(GLOBAL_OPENCODE_JSON.read_bytes()).hexdigest()
            self.assertEqual(global_sha_after, global_sha_before)


if __name__ == "__main__":
    unittest.main()
