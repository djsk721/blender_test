# Character Quality v2 Validation Report

Date: 2026-11-25
Branch: `feat/blender-character-quality-v2`

## 1. Live Sequential GPT -> Full Reset -> Qwen E2E Run

Command executed:

```bash
python3 scripts/run_sequential_gpt_qwen_e2e.py \
  --reference-mode REFERENCE_GUIDED \
  --reference-image images.jpg \
  --stage-timeout 150 \
  --blender-port 9877
```

Observed top-level output (`exit code 0`):

- `state`: `"COMPLETE"`
- `isolation_verdict`: `"PASS"`
- `reports_dir`: `/home/jhkim/tools/blender_test/reports`

Generated report & comparison artifacts:

- `reports/session_isolation.json` (14,781 bytes)
- `reports/gpt_vs_qwen_comparison.md` (671 bytes)
- `reports/gpt_vs_qwen_comparison.png` (97,770 bytes)
- `reports/before_after_structural_repair.png` (252,649 bytes, generated from `e2e_runs/character_e2e/20261125_094556_harness_character` live harness renders)

### 1.1 Phase A — GPT (`openai/gpt-6.2-sol`)

- Run directory: `e2e_runs/sequential_benchmarks/20261125_101039_gpt/`
- Time window: `2026-11-25T01:10:39.775575+00:00` to `2026-11-25T01:17:21.298204+00:00` (`401.48s`)
- Blender PID / Boot ID: `82626` / `managed-82626-fe3b146b` (port `9877`, isolated runtime `e2e_runs/sequential_benchmarks/mcp_runtime`)
- OpenCode Producer Session: `ses_defbe7846ffew6pX3WR1mrZbU2`
  - `stage_01_brief`: `rc=0`, `duration=23.078s`, `timed_out=False`
  - `stage_03_modeling_plan`: `rc=0`, `duration=9.013s`, `timed_out=False`
- Independent Visual QA Reviewer Session: `ses_defb9782efferCde5B7YhdLewg` (`reviewer_context_id != producer_session_id`)
- Execution & Repair Telemetry:
  - `mcp_tool_call_count`: `43`, `blender_error_count`: `0`
  - Visual QA passes: `4` (`gate_a_pass_0`..`gate_a_pass_3`), structural fix iterations: `3`, QA failures: `4`
  - Reviewer reference score: `[2, 10]` against `images.jpg`
  - Unresolved defects at termination: `D1(facial_proportion_error, BLOCKER)`, `D2(rope_like_hair, MAJOR)`, `D3(stiff_fingers_bad_grip, MAJOR)`, `D4(bulbous_boots, MAJOR)`, `D5(reference_match_gap, BLOCKER)`
  - Shortcut detector flag recorded on `character_modeling.py`: `uv_sphere_without_orbital_socket`
- Pipeline Gate & Layer Verdicts:
  - `mcp_connection`: `PASS`, `blender_execution`: `PASS`
  - `skill_application`: `FAIL`, `geometry_and_technical_validation`: `FAIL`, `visual_quality_validation`: `FAIL`
  - `final_asset_production`: `FAIL` (`gate_a: FAIL`, `gate_b: NOT_VERIFIED`, `gate_c: NOT_VERIFIED`)
  - Stages 6–11: `BLOCKED`
  - Artifacts: `1` `DIAGNOSTIC_CHECKPOINT`, `0` `APPROVED_RELEASE`

### 1.2 Phase B — Managed Blender Lifecycle Full Reset

- Time window: `2026-11-25T01:17:21.381198+00:00` to `2026-11-25T01:17:41.759426+00:00`
- Implemented via `BlenderLifecycleManager` (`BlenderSessionResetter` on port `9877`, without `pkill`/`killall`/`kill -9`):
  - `1_prior_outputs_saved`: `true`
  - `2_no_inflight_mcp_jobs`: `true`
  - `3_memory_datablocks_purged`: `true`
  - `4_managed_blender_stopped`: `true` (`graceful_quit_used: true`, `blender_forced_kill: false`, `xvfb_stopped: true`)
  - `5_mcp_session_closed`: `true`
  - `6_xvfb_cleaned_up`: `true`
  - `7_old_pid_terminated`: `true`
  - `8_gpu_memory_released`: `true` (`remaining_mib: 0`, `matched_apps: []`)
  - `9_new_blender_started_fresh_pid`: `true` (`new_pid: 91424`, `new_boot_id: managed-91424-2827de9a`)
  - `10_clean_empty_scene_verified`: `true` (`scene_name: "Scene"`, `object_count: 3`, `["Cube", "Light", "Camera"]`, `residual_objects_found: []`)

### 1.3 Phase C — Qwen (`strata/sc117-qwen38-abliterated-iq3_s`)

- Run directory: `e2e_runs/sequential_benchmarks/20261125_101741_qwen/`
- Time window: `2026-11-25T01:17:41.760055+00:00` to `2026-11-25T01:28:26.197862+00:00` (`644.41s`)
- Blender PID: `91424` (port `9877`)
- OpenCode Producer Session: `ses_defb84c4cffeA4z88z3X9WqzTq`
  - `stage_01_brief`: `rc=124`, `duration=150.019s`, `timed_out=True` -> Stage 1 marked `FAIL`
  - `stage_03_modeling_plan`: `rc=124`, `duration=150.012s`, `timed_out=True`
- Independent Visual QA Reviewer Session: `ses_defaf692bffejyLeNDEWVy7NdH`
- Execution & Repair Telemetry:
  - `mcp_tool_call_count`: `41`, `blender_error_count`: `0`
  - Visual QA passes: `4` (`gate_a_pass_0`..`gate_a_pass_3`), structural fix iterations: `3`, QA failures: `4`
  - Reviewer reference score: `[2, 10]` against `images.jpg`
  - Unresolved defects: `D1(facial_proportion_error, BLOCKER)`, `D2(rope_like_hair, MAJOR)`, `D3(scalp_exposure, MAJOR)`, `D4(hair_face_penetration, MAJOR)`, `D5(stiff_fingers_bad_grip, MAJOR)`, `D6(bulbous_boots, MAJOR)`, `D7(reference_match_gap, BLOCKER)`
- Pipeline Gate & Layer Verdicts:
  - `mcp_connection`: `PASS`, `blender_execution`: `PASS`
  - `skill_application`: `FAIL`, `geometry_and_technical_validation`: `FAIL`, `visual_quality_validation`: `FAIL`
  - `final_asset_production`: `FAIL` (`gate_a: FAIL`, `gate_b: NOT_VERIFIED`, `gate_c: NOT_VERIFIED`)
  - Stages 6–11: `BLOCKED`
  - Artifacts: `1` `DIAGNOSTIC_CHECKPOINT`, `0` `APPROVED_RELEASE`

### 1.4 Phase D — Isolation & Cleanup

- `execution_windows_overlapped`: `false`
- `cross_reference_between_models`: `false`
- `final_cleanup_after_qwen`: `stopped: true`, `graceful_quit_used: true`, `blender_forced_kill: false`, `xvfb_stopped: true`, `gpu_released: true`
- Post-run `lifecycle_manager.py status` on `e2e_runs/sequential_benchmarks/mcp_runtime` (`port=9877`): `state: "STOPPED"`, `blender_alive: false`, `xvfb_alive: false`.

## 2. Pytest Suite Verification

Full 8-module run executed after all code edits:

```bash
pytest -v tests/blender_mcp/test_*.py
```

Observed result: **`70 passed, 2 failed in 449.13s (0:07:29)`** (72 tests collected across 8 files).

Per-module breakdown:

- `tests/blender_mcp/test_benchmark_policy.py`: 7/7 PASSED
- `tests/blender_mcp/test_character_qa_and_pipeline.py`: 7/7 PASSED
- `tests/blender_mcp/test_installer.py`: 8/8 PASSED
- `tests/blender_mcp/test_orchestration.py`: 15/15 PASSED
- `tests/blender_mcp/test_sequential_isolation_and_repair.py`: 5/5 PASSED
- `tests/blender_mcp/test_unit_lifecycle.py`: 17/17 PASSED
- `tests/blender_mcp/test_zelda_regression.py`: 3/3 PASSED
- `tests/blender_mcp/test_integration_qa.py`: 8/10 PASSED, 2 FAILED:
  1. `test_01_b01_to_b05_opencode_and_lazy_init`: timed out at `120s` running `opencode --standalone run --model strata/sc117-qwen38-abliterated-iq3_s#none`. Captured stdout reported host Strata VRAM/embedding error: `"native embedding: cannot pin 322 MiB: unknown error, nor place it in VRAM; the next request restarts it"` (consistent with NVML degraded state where `/proc/driver/nvidia/gpus` reports 2 GPUs while `nvidia-smi` returned 1).
  2. `test_07_b20_external_blender_protection`: failed both in the full run and on isolated re-run (`pytest -v tests/blender_mcp/test_integration_qa.py -k test_07_b20_external_blender_protection`, `exit code 1`, `30.30s`) because raw `xvfb-run -a blender` (without `gpu_manager` CPU+software-GL fallback environment variables) did not bring up addon port `9876` within `30.0s` on this host. Pre-existing `test_integration_qa.py` was left unmodified.

## 3. Honest Remaining Gaps & Limitations

1. **Structural/Visual Fidelity Gap Against `images.jpg`**: While real 3D anatomical BMesh construction eliminated raw 2D pixel projection and initial shin/boot z-fighting, Gate A multimodal inspection still graded the untextured clay silhouette/face (`gate_a_pass_3`) at `2/10` (`FAIL`) with remaining facial feature articulation, hair clump sculpt definition, grip contact, and boot-cuff transition gaps.
2. **Primitive Shortcut Heuristic Flag**: `detect_2d_projection_or_primitive_shortcuts()` still matches `bmesh.ops.create_uvsphere` used for recessed eyeball/braid segments in `blender_orchestration/character_modeling.py`, keeping `shortcut_flags` non-empty.
3. **Host GPU / Local Strata Runtime Stability**: Under the current host driver/NVML condition (1 of 2 GPUs visible to `nvidia-smi`), Strata local inference (`strata/sc117-qwen38-abliterated-iq3_s`) emitted VRAM pinning errors and timed out in both E2E (`150s`) and `test_integration_qa.py::test_01` (`120s`).
