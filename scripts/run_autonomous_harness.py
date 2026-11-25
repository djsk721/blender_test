#!/usr/bin/env python3
"""CLI runner for Autonomous Blender Design Harness cycles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blender_orchestration.autonomous_harness import AutonomousBlenderHarness  # noqa: E402
from blender_orchestration.rubric_evaluator import DEFAULT_LIGHT_MODEL_ID, verify_model_id_lock  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one autonomous Blender design harness production cycle.")
    parser.add_argument("--run-dir", type=Path, required=True, help="Output directory for this cycle/run.")
    parser.add_argument("--asset-name", default="HeroineCharacter")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--reference-mode", choices=["TEXT_ONLY", "REFERENCE_GUIDED"], default="REFERENCE_GUIDED")
    parser.add_argument("--reference-image", type=Path, default=PROJECT_ROOT / "images.jpg")
    parser.add_argument("--animation-mode", choices=["static", "rigged", "animated"], default="animated")
    parser.add_argument("--model-id", default=DEFAULT_LIGHT_MODEL_ID)
    parser.add_argument("--expected-model-id", default=None, help="Optional strict model lock ID.")
    parser.add_argument("--geometry-strategy", choices=["FROM_SCRATCH", "BASE_MESH"], default="FROM_SCRATCH")
    parser.add_argument("--base-mesh-manifest", type=Path, default=None)
    parser.add_argument("--blender-port", type=int, default=9878)
    parser.add_argument("--max-stage-corrections", type=int, default=3)
    parser.add_argument("--stage-timeout", type=int, default=180)
    parser.add_argument("--render-samples-stage", type=int, default=20)
    parser.add_argument("--render-samples-final", type=int, default=48)
    parser.add_argument("--extra-guidance", default="")
    parser.add_argument("--resume-checkpoint", type=Path, default=None, help="Optional verified .blend checkpoint to resume from.")
    parser.add_argument("--start-stage", type=int, choices=[1, 2, 3, 4, 5, 6], default=1, help="Stage to start/resume from (1..6).")
    parser.add_argument("--history-jsonl", type=Path, default=PROJECT_ROOT / "output" / "autonomous_harness" / "EXPERIMENT_HISTORY.jsonl")
    parser.add_argument("--label", default="")
    args = parser.parse_args(argv)

    verify_model_id_lock(args.model_id, args.expected_model_id)
    render_res_stage = (512, 640) if args.geometry_strategy == "BASE_MESH" else (768, 960)
    render_res_final = (1024, 1280)

    harness = AutonomousBlenderHarness(
        model_id=args.model_id,
        expected_model_id=args.expected_model_id,
        project_root=PROJECT_ROOT,
        blender_port=args.blender_port,
        max_stage_corrections=args.max_stage_corrections,
        stage_timeout_sec=args.stage_timeout,
        render_res_stage=render_res_stage,
        render_samples_stage=args.render_samples_stage,
        render_res_final=render_res_final,
        render_samples_final=args.render_samples_final,
    )
    report = harness.run_production(
        prompt=args.prompt,
        run_dir=args.run_dir,
        asset_name=args.asset_name,
        reference_mode=args.reference_mode,
        approved_reference_path=args.reference_image if args.reference_mode == "REFERENCE_GUIDED" else None,
        animation_mode=args.animation_mode,
        geometry_strategy=args.geometry_strategy,
        base_mesh_manifest=args.base_mesh_manifest,
        extra_guidance=args.extra_guidance,
        resume_checkpoint=args.resume_checkpoint,
        start_stage=args.start_stage,
    )

    fe = report.get("final_evaluation") or {}
    if args.history_jsonl:
        args.history_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.history_jsonl.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "iteration": args.label or args.run_dir.name,
                        "timestamp_utc": report.get("timestamp_utc"),
                        "git_rev": report.get("git_revision"),
                        "model_id": report.get("model_id"),
                        "geometry_strategy": report.get("geometry_strategy"),
                        "asset_name": args.asset_name,
                        "reference_mode": args.reference_mode,
                        "verdict": fe.get("verdict"),
                        "total_score": fe.get("total_score"),
                        "category_scores": fe.get("category_scores"),
                        "critical_defect_count": fe.get("critical_defect_count"),
                        "technical_gates": report.get("technical_gates"),
                        "total_runtime_sec": report.get("total_runtime_sec"),
                        "token_usage": report.get("token_usage"),
                        "reviewer_session_id": fe.get("reviewer_session_id"),
                        "summary": fe.get("summary"),
                    }
                )
                + "\n"
            )

    print(
        json.dumps(
            {
                "run_dir": str(args.run_dir),
                "model_id": report.get("model_id"),
                "geometry_strategy": report.get("geometry_strategy"),
                "verdict": fe.get("verdict"),
                "total_score": fe.get("total_score"),
                "category_scores": fe.get("category_scores"),
                "critical_defect_count": fe.get("critical_defect_count"),
                "reasons": fe.get("reasons"),
                "technical_gates": report.get("technical_gates"),
                "total_runtime_sec": report.get("total_runtime_sec"),
                "token_usage": report.get("token_usage"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
