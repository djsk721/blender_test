# GPT-supervised Blender production

For Qwen-only, reference-guided character production with configurable independent vision review, use the separate [V3 CLI](harness-v3.md). The following instructions describe the legacy workflow.

The runner uses GPT for the design brief and bounded repair work orders, Qwen for asset-specific Blender Python, and a separate GPT session for image review. OpenCode sessions use isolated configuration and fresh context. The harness validates and executes Qwen's scripts through Blender MCP. Stage review and the frozen 100-point final rubric retain their original thresholds.

## Preflight and foreground execution

From the repository root, select a new output path and an unused Blender port for each run. The CPU and software viewport settings below avoid allocating Blender VRAM on the host that also serves Strata.

```bash
export BLENDER_RENDER_DEVICE=CPU BLENDER_CPU_SOFTWARE_VIEWPORT=true
python3 scripts/qwen_capability_preflight.py \
  --production-output output/preflight-001 --port 9887 \
  --base-mesh-manifest assets/approved_base_meshes/blender_human_base_meshes/provenance.json
```

The manifest option verifies and loads the local CC0 Blender library through MCP. A missing library requires obtaining the approved bundle and verifying the archive and library SHA-256 values recorded in the manifest.

Save the original goal in a text file so restarts use the identical text. This example assumes the goal is an original asset and uses text-only production. For an approved reference, use `--reference-mode REFERENCE_GUIDED --reference-image /absolute/path/to/approved-image`.

```bash
run_dir=output/original-asset-001
asset_name=OriginalAsset
python3 scripts/run_autonomous_harness.py \
  --run-dir "$run_dir" --asset-name "$asset_name" \
  --prompt "$(cat /absolute/path/to/original-goal.txt)" \
  --reference-mode TEXT_ONLY --animation-mode static --blender-port 9888 \
  --producer-model-id strata/sc117-qwen38-abliterated-iq3_s \
  --expected-model-id strata/sc117-qwen38-abliterated-iq3_s \
  --supervisor-model-id 'openai/gpt-6.2-sol#low' \
  --reviewer-model-id 'openai/gpt-6.2-sol#low'
```

For a run that must survive terminal disconnects, use `tmux new-session -s blender-production`, run the same command inside the session, and optionally pipe output through `tee` with `set -o pipefail`. Detach with Ctrl-b then d and reconnect using `tmux attach -t blender-production`.

## Resume and adjudication

Read `production_state.json` in the original run directory. Repeat the original command with the recorded `--resume-checkpoint` and `--start-stage` values. The harness compares the checkpoint and locked design-spec hashes, goal, reference and base-mesh identity, and model roles before loading the checkpoint. Completed Stage 3 modeling passes are saved with the checkpoint that contains them. Files in an older run without this metadata require manual scene and evidence verification before migration. A new empty run directory is required for a fresh launch.

A failed stage retains the last technically verified resume point and any diagnostic candidate. Inspect `iteration_report.json`, `handoffs/`, `scripts/`, `renders/`, and `reviews/` for the actual result. If provider health fails, validate Strata and GPU availability before resuming. An unresolved mandatory modeling or lookdev gate blocks rigging and export. The final GLB is measured and reimported as a diagnostic candidate and promoted to `final/` only after technical validation and independent rubric approval. The CLI exits nonzero when there is no approved export.
