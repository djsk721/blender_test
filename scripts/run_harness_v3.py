#!/usr/bin/env python3
"""Run the V3 Qwen-only character pipeline against an approved manifest."""
import argparse
import fcntl
import os
from pathlib import Path
import re
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blender_orchestration.reference_manifest import load_reference_manifest
from blender_orchestration.sequential_benchmark import BlenderSessionResetter
from blender_orchestration.v3.agents import invoke_isolated
from blender_orchestration.v3.executor import BlenderExecutor
from blender_orchestration.v3.geometry import STRATEGIES
from blender_orchestration.v3.pipeline import Production
from blender_orchestration.v3.provenance import (DEFAULT_REVIEWER_MODEL_ID, REQUIRED_PRODUCER_MODEL_ID,
                                                  validate_model_roles)
from run_autonomous_harness import verify_registered_models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt', required=True, help='Exact UTF-8 production goal')
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--reference-manifest', type=Path, required=True)
    parser.add_argument('--asset-name', default='Character')
    parser.add_argument('--geometry-strategy', choices=STRATEGIES, default='FROM_SCRATCH')
    parser.add_argument('--base-mesh-manifest', type=Path)
    parser.add_argument('--base-mesh-name', help='Exact Blender object name from the approved library')
    parser.add_argument('--animation-mode', choices=['static', 'rigged', 'animated'], default='animated')
    parser.add_argument('--producer-model-id', default=REQUIRED_PRODUCER_MODEL_ID)
    parser.add_argument('--reviewer-model-id', default=os.environ.get('BLENDER_V3_REVIEWER_MODEL_ID', DEFAULT_REVIEWER_MODEL_ID),
                        help='Registered non-Qwen vision model; defaults to BLENDER_V3_REVIEWER_MODEL_ID or %(default)s')
    parser.add_argument('--blender-port', type=int, required=True)
    parser.add_argument('--max-stage-corrections', type=int, choices=range(7), default=3)
    parser.add_argument('--stage-timeout', type=int, default=240)
    parser.add_argument('--samples', type=int, choices=range(1, 33), default=8, metavar='1..32')
    parser.add_argument('--resume-checkpoint', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.asset_name) or not 1024 <= args.blender_port <= 65535:
        parser.error('Invalid asset name or Blender port')
    if not 10 <= args.stage_timeout <= 900:
        parser.error('Stage timeout must be 10–900 seconds')
    run = args.run_dir.resolve()
    runtime = run.with_name(run.name + '_mcp_runtime')
    run.parent.mkdir(parents=True, exist_ok=True)
    port_lock = Path(tempfile.gettempdir()) / f'blender_harness_v3_port_{args.blender_port}.lock'
    with run.with_name(run.name + '.lock').open('a') as lock, port_lock.open('a') as plock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(plock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', args.blender_port))
        roles = validate_model_roles(args.producer_model_id, args.reviewer_model_id)
        manifest = load_reference_manifest(args.reference_manifest)
        os.environ.update(BLENDER_RENDER_DEVICE='CPU', BLENDER_CPU_SOFTWARE_VIEWPORT='true')
        os.environ['BLENDER_MCP_MANAGE_EXTERNAL'] = 'false'
        production = Production(run=run, root=ROOT, goal=args.prompt, manifest=manifest, roles=roles,
                                mode=args.animation_mode, invoke=invoke_isolated, budget=args.max_stage_corrections,
                                timeout=args.stage_timeout, samples=args.samples, resume=args.resume_checkpoint,
                                asset=args.asset_name, port=args.blender_port, geometry_strategy=args.geometry_strategy,
                                base_mesh_manifest=args.base_mesh_manifest, base_mesh_name=args.base_mesh_name)
        verify_registered_models(set(roles.to_dict().values()))
        resetter = BlenderSessionResetter(project_root=ROOT, runtime_dir=runtime, blender_port=args.blender_port)
        ex = BlenderExecutor(project_root=ROOT, scene_name='SCENE_' + args.asset_name, run_dir=run,
                             mcp_env={'BLENDER_HOST': '127.0.0.1', 'BLENDER_PORT': str(args.blender_port),
                                      'BLENDER_MCP_RUNTIME_DIR': str(runtime), 'BLENDER_MCP_AUTO_RESTORE': 'false',
                                      'BLENDER_MCP_IDLE_TIMEOUT': '900'}, ledger=production.provenance,
                             checkpoints=production.manager, rpc_timeout_sec=300)
        try:
            return production.execute(ex)
        finally:
            if runtime.exists():
                resetter.reset_for_next_model(start_fresh_blender=False, preserve_backups=True)


if __name__ == '__main__':
    raise SystemExit(main())
