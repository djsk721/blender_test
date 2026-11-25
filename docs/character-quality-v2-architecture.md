# Character Quality v2 — Architecture & Integration Guide

## 1. Overview

`feat/blender-character-quality-v2` closes the gap between passive QA helper classes and real OpenCode agent execution by wiring:

1. **Real 3D Anatomical Modeling** ([character_modeling.py](file:///home/jhkim/tools/blender_test/blender_orchestration/character_modeling.py)) into [scene_ops.py](file:///home/jhkim/tools/blender_test/blender_orchestration/scene_ops.py) and [qa_profiles.py](file:///home/jhkim/tools/blender_test/blender_orchestration/qa_profiles.py).
2. **Structural Repair Strategy Engine** ([repair_strategies.py](file:///home/jhkim/tools/blender_test/blender_orchestration/repair_strategies.py)) into [visual_qa.py](file:///home/jhkim/tools/blender_test/blender_orchestration/visual_qa.py).
3. **Independent Multimodal Visual QA** ([multimodal_qa.py](file:///home/jhkim/tools/blender_test/blender_orchestration/multimodal_qa.py)) using isolated OpenCode sessions with attached PNG renders and deterministic scene cross-checks.
4. **Live OpenCode Execution Harness** ([opencode_runner.py](file:///home/jhkim/tools/blender_test/blender_orchestration/opencode_runner.py)) binding OpenCode sessions directly to the 11-stage `CharacterProductionPipeline` ([production_pipeline.py](file:///home/jhkim/tools/blender_test/blender_orchestration/production_pipeline.py)).
5. **Sequential GPT -> Full Blender Lifecycle Reset -> Qwen Orchestrator** ([sequential_benchmark.py](file:///home/jhkim/tools/blender_test/blender_orchestration/sequential_benchmark.py), [run_sequential_gpt_qwen_e2e.py](file:///home/jhkim/tools/blender_test/scripts/run_sequential_gpt_qwen_e2e.py)).

---

## 2. Real 3D Anatomical Modeling (`character_modeling.py`)

Instead of projecting 2D reference pixel coordinates (`P(x, z, d)`), pasting UV spheres onto smooth heads, or extruding circular hair ropes:
- `build_anatomical_head_face_bpy()`: constructs continuous 3D facial geometry with recessed orbital eye sockets, contoured upper/lower eyelid wrap loops, brow ridge overhang, 3D nasal bridge/tip/alae, zygomatic cheeks, lips, tapered jaw, and pointed ears.
- `build_anatomical_hands_bpy()`: constructs wrist-to-palm metacarpal arch, 4 knuckle-connected 3-phalanx fingers with lateral spacing (no crossover), opposed thumb, and wrapped contact around `SM_Char_HeldSlate`.
- `build_anatomical_hair_bpy()`: constructs a closed manifold scalp cap covering the cranium down to the nape, flattened crescent-section sculpted hair clumps (`aspect <= 0.45`, tapered tips), tucked hairline roots, raised interlocking crown braid, and a recorded vertex sculpt pass.
- `build_anatomical_body_costume_bpy()`: constructs fitted royal tunic with hip-flare clearance and contoured boots with distinct grounded outsole (`Z=0`), heel block, instep arch, and pinched ankle clearing the leg shin.
- `detect_2d_projection_or_primitive_shortcuts()` (`qa_profiles.py`): flags pixel-depth projections, bare UV-sphere eyes, circular tube hair, Subsurf-only shortcuts, and >60-object fragmentation.

---

## 3. Structural Repair Strategy Engine (`repair_strategies.py`)

Every defect class maps to a mandatory minimum `RepairStrategyTier` (`MATERIAL_ONLY` < `PLACEMENT_ONLY` < `SILHOUETTE_RESHAPE` < `STRUCTURAL_REMODEL` < `ROLLBACK_TO_BLOCKOUT`):
- `validate_repair_effectiveness()` rejects `MATERIAL_ONLY` or `PLACEMENT_ONLY` edits or `geometry_changed == False` on structural defects as `SUPERFICIAL_FIX_REJECTED`.
- `VisualQARepairLoop` tracks `best_checkpoint` across passes, escalates strategies when `escalate_strategy_on_repeat=True`, rejects stalled repeats, and preserves `best_checkpoint` on `FAIL` without unblocking downstream stages.

---

## 4. Independent Multimodal Visual QA (`multimodal_qa.py`)

`IndependentMultimodalReviewer` evaluates 7 separated domains (`technical_mesh_validity`, `face_and_body_sculpt_quality`, `identity_or_reference_fidelity`, `texture_and_material_quality`, `rigging_and_deformation_quality`, `animation_quality`, `engine_export_compatibility`):
- Attaches real PNG renders (`--file`) to a separate `--archived` OpenCode CLI session (`reviewer_context_id != producer_context_id`).
- Fails closed as `NOT_VERIFIED` if any render is missing, unreadable, non-multimodal, or lacks per-view observations.
- Cross-checks prose against `analyze_character_scene_metrics(scene_audit)`: any technical failure or open `BLOCKER`/`MAJOR` defect forces `FAIL` even if the reviewer text claims `PASS`.

---

## 5. 11-Stage `OpenCodeCharacterRunner` & Diagnostic vs. Approved Artifacts

- Connects live OpenCode sessions and Blender MCP (`MCPAdapter`) through all 11 stages of `CharacterProductionPipeline`.
- If Stage 5 (`modeling_visual_qa` / Gate A) fails or is `NOT_VERIFIED`, Stages 6–11 are `BLOCKED` and `.blend`/renders are saved under `checkpoints/diagnostic_gate_a_failed/` tagged `DIAGNOSTIC_CHECKPOINT` (never `APPROVED_RELEASE`).

---

## 6. Strict Sequential GPT -> Reset -> Qwen FSM (`sequential_benchmark.py`)

```text
IDLE -> GPT_RUNNING -> GPT_FINISHED -> RESETTING -> RESET_VERIFIED
  -> QWEN_RUNNING -> QWEN_FINISHED -> CLEANUP -> COMPARISON -> COMPLETE
```

- Single-flight OS lock (`fcntl.flock`) prevents GPT and Qwen from ever overlapping.
- `BlenderSessionResetter` uses `.opencode/mcp/blender/lifecycle_manager.py` (`BlenderLifecycleManager`) exclusively (no `pkill`/`killall`/`kill -9`, no disturbance to Strata/Ollama/external Blender):
  1. Verifies prior outputs saved on disk & 0 active/pending MCP jobs.
  2. Purges in-memory datablocks, calls `stop_blender(discard_session=True)`, verifies old PID dead, Xvfb stopped, and GPU memory released.
  3. Calls `ensure_ready()`, verifies new Blender PID != old PID, new `boot_id` != old `boot_id`, and `0` residual custom objects/meshes/armatures/actions in the new empty scene.
- If any check fails, transitions to `BLOCKED_RESET_FAILED` and halts Qwen.
- Phase D compares saved results read-only and outputs `reports/session_isolation.json` and `reports/gpt_vs_qwen_comparison.md`.
