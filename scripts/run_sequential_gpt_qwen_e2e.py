#!/usr/bin/env python3
"""Sequential GPT -> Full Blender Lifecycle Reset -> Qwen Character Production E2E Runner.

Executes Phase A (GPT), Phase B (Full BlenderLifecycleManager reset + empty scene check),
Phase C (Qwen), and Phase D (final cleanup + read-only comparison + session_isolation.json).
Never runs GPT and Qwen concurrently.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from blender_orchestration.opencode_runner import OpenCodeCharacterRunner  # noqa: E402
from blender_orchestration.regression_fixtures import BenchmarkIsolationGuard  # noqa: E402
from blender_orchestration.sequential_benchmark import (  # noqa: E402
    BlenderSessionResetter,
    SequentialBenchmarkOrchestrator,
    composite_png_strip,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sequential GPT -> Blender Reset -> Qwen character E2E.")
    parser.add_argument("--gpt-model", default="openai/gpt-6.2-sol")
    parser.add_argument("--qwen-model", default="strata/sc117-qwen38-abliterated-iq3_s")
    parser.add_argument("--reviewer-model", default="openai/gpt-6.2-sol")
    parser.add_argument("--reference-mode", choices=["TEXT_ONLY", "REFERENCE_GUIDED"], default="REFERENCE_GUIDED")
    parser.add_argument("--reference-image", type=Path, default=PROJECT_ROOT / "images.jpg")
    parser.add_argument("--animation-mode", choices=["static", "rigged", "animated"], default="animated")
    parser.add_argument("--stage-timeout", type=int, default=180)
    parser.add_argument("--blender-port", type=int, default=9877)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "e2e_runs" / "sequential_benchmarks")
    parser.add_argument("--reports-dir", type=Path, default=PROJECT_ROOT / "reports")
    args = parser.parse_args(argv)

    runtime_dir = args.output_root / "mcp_runtime"
    mcp_env = {
        "BLENDER_HOST": "127.0.0.1",
        "BLENDER_PORT": str(args.blender_port),
        "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json"),
        "BLENDER_MCP_RUNTIME_DIR": str(runtime_dir),
        "BLENDER_MCP_IDLE_TIMEOUT": str(max(600, args.stage_timeout * 2)),
        "BLENDER_MCP_WATCHDOG_POLL": "1.0",
        "BLENDER_MCP_AUTO_RESTORE": "false",
    }

    if args.reference_mode == "REFERENCE_GUIDED":
        prompt = (
            "Create an animated stylized Princess Zelda 3D character for Godot using the supplied approved "
            "reference image (`images.jpg`), following blender-director, 3D anatomical modeling, "
            "independent visual QA, structural defect repair, rigging, and run cycle animation."
        )
        ref_path = args.reference_image if args.reference_image.is_file() else None
    else:
        prompt = (
            "Create a stylized Princess Zelda 3D character with a clean run cycle animation for Godot "
            "without using any reference images, following blender-director and mandatory quality gates."
        )
        ref_path = None

    def _run_model(model_id: str, run_dir: Path, guard: BenchmarkIsolationGuard) -> dict[str, Any]:
        # Guard verifies own workspace and blocks reading sibling model outputs
        guard.validate_read_path(run_dir)
        runner = OpenCodeCharacterRunner(
            model_id=model_id,
            reviewer_model_id=args.reviewer_model,
            project_root=PROJECT_ROOT,
            stage_timeout_sec=args.stage_timeout,
            mcp_env_overrides=mcp_env,
        )
        return runner.run(
            prompt=prompt,
            run_dir=run_dir,
            reference_mode=args.reference_mode,
            approved_reference_path=ref_path,
            animation_mode=args.animation_mode,
            asset_name="HeroineCharacter",
        )

    def _compare(gpt_res: dict[str, Any], qwen_res: dict[str, Any], reports_dir: Path) -> dict[str, Any]:
        comp = orch._default_comparison(gpt_res, qwen_res)
        # Composite side-by-side PNG comparison strip if renders exist
        g_renders = list((Path(gpt_res["run_dir"]) / "renders").rglob("*_three_quarter.png"))
        q_renders = list((Path(qwen_res["run_dir"]) / "renders").rglob("*_three_quarter.png"))
        strip_inputs = []
        if ref_path and ref_path.is_file():
            strip_inputs.append(ref_path)
        if g_renders:
            strip_inputs.append(sorted(g_renders)[-1])
        if q_renders:
            strip_inputs.append(sorted(q_renders)[-1])
        if len(strip_inputs) >= 2:
            strip_out = composite_png_strip(strip_inputs, reports_dir / "gpt_vs_qwen_comparison.png")
            comp["comparison_png"] = str(strip_out) if strip_out else None
        return comp

    orch = SequentialBenchmarkOrchestrator(
        output_root=args.output_root,
        reports_dir=args.reports_dir,
        runtime_dir=runtime_dir,
        resetter=BlenderSessionResetter(
            project_root=PROJECT_ROOT,
            runtime_dir=runtime_dir,
            blender_port=args.blender_port,
        ),
    )
    res = orch.run(
        gpt_runner_fn=lambda d, g: _run_model(args.gpt_model, d, g),
        qwen_runner_fn=lambda d, g: _run_model(args.qwen_model, d, g),
        compare_fn=_compare,
    )
    print(json.dumps({
        "state": res.get("state"),
        "gpt_verdicts": (res.get("gpt_result") or {}).get("verdicts"),
        "qwen_verdicts": (res.get("qwen_result") or {}).get("verdicts"),
        "isolation_verdict": (res.get("session_isolation") or {}).get("isolation_verdict"),
        "reports_dir": str(args.reports_dir),
    }, indent=2))
    return 0 if res.get("state") == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
