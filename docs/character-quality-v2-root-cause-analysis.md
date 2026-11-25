# Character Quality v2 — Root Cause Analysis of GPT & Qwen Execution Failures

This analysis documents the 8 concrete defects observed in prior GPT (`openai/gpt-6.2-sol`) and Qwen (`strata/sc117-qwen38-abliterated-iq3_s`) character production runs, mapping each failure to exact code locations, root causes, concrete fixes, and verification methods.

---

## 1. Primitive-Composed Face & Body
- **Code Locations**:
  - Historical Zelda baseline (`git show 2c9f916:output/zelda_reference/steps/01_forms.py`, `02_face_hair.py`): built body/head/limbs via `sweep()` elliptical lofts and `ell()` ellipsoids across 230 discrete objects.
  - `blender_orchestration/scene_ops.py` (`build_character_blockout_code`, `build_character_face_body_costume_code`): initial v1 character builders used segmented loft cylinders for limbs/palms and UV-sphere primitives for eyeballs/eyelids.
- **Root Cause**: No anatomical edge-loop topology (orbital rings, nasolabial fold, mouth/jaw loops, deltoid/elbow/knee 3-loop joints) or unified BMesh facial/limb construction was enforced or supplied.
- **Improvement**: Implemented `blender_orchestration/character_modeling.py` with cohesive 3D BMesh + edge-loop anatomical builders (`build_anatomical_head_face_bpy`, `build_anatomical_hands_bpy`, `build_anatomical_body_costume_bpy`) and added `detect_2d_projection_or_primitive_shortcuts()` in `blender_orchestration/qa_profiles.py`.
- **Verification**: `tests/blender_mcp/test_sequential_isolation_and_repair.py` + live Blender 5.3.0 render & `bmesh` audit in `scripts/run_e2e_character_production.py`.

## 2. Unnatural Face, Eyes, Hands, Hair & Boots
- **Code Locations**:
  - `2c9f916:output/zelda_reference/steps/02_face_hair.py` (stacked eye disks `_LashBase/_Sclera/_Limbal/_Iris/_Pupil/_Glint`, parametric `Hair_Scalpcap` gap, `ell('Hair_Braid_A/B')` beads).
  - `2c9f916:output/zelda_reference/steps/03_costume.py` / `06_final.py` (parallel finger cylinders, bulbous `sweep` boots, floating outer-thigh `strand` trim).
- **Root Cause**: Eyes sat proud of the facial plane without orbital sockets or eyelid wrap; fingers lacked palm-knuckle continuity and wrap around held objects; boots lacked flat soles, heel blocks, instep arches, and pinched ankle collars; scalp cap left cranium skin exposed behind the crown braid.
- **Improvement**: Built anatomical recessed orbital eye sockets + contoured eyelid wraps, palm-connected 3-phalanx fingers wrapping held props without crossover, manifold full-coverage scalp mesh + flattened crescent ribbon hair clumps, and heeled/instep-contoured boots (`character_modeling.py`).
- **Verification**: Multi-angle renders (`front`, `side`, `three_quarter`, `face_closeup`, `hands_closeup`, `boots_closeup`) + `BVHTree` spatial checks + independent multimodal visual QA.

## 3. Dependency on 2D Reference Pixel Coordinates + Arbitrary Depth
- **Code Locations**: `2c9f916:output/zelda_reference/steps/01_forms.py` (`P(x, z, d)` pixel-to-meter projection + `BVHTree.ray_cast` front projection `face(x, z)`).
- **Root Cause**: In `REFERENCE_GUIDED` runs, the agent projected 2D `(x, z)` image pixel landmarks directly with guessed scalar depth `d`, causing front/side/3-4 inconsistency, flat facial planes, and detached side trims.
- **Improvement**: `detect_2d_projection_or_primitive_shortcuts()` flags pixel-coordinate depth-extrusion patterns; `IndependentMultimodalReviewer` explicitly inspects `front`, `side`, `back`, and `three_quarter` consistency; `.opencode/AGENTS.md` forbids completing 3D characters via 2D pixel depth projection.
- **Verification**: Unit test for shortcut detection + multi-view silhouette check in `qa_profiles.py`.

## 4. Repair Loops Changing Color, Lights, or Positions Instead of Structure
- **Code Locations**:
  - `2c9f916:output/zelda_reference/steps/05_refine.py` (mainly darkened texture tiles via `adjust_tile`, lowered world background, adjusted exposure/rim light, shifted `Hair_Contour` `location.y`).
  - `blender_orchestration/visual_qa.py` (`VisualQARepairLoop` lacked strategy-tier enforcement rejecting `MATERIAL_ONLY` or `PLACEMENT_ONLY` edits on structural defects).
- **Root Cause**: Visual QA defects were not bound to mandatory `RepairStrategyTier` requirements, allowing superficial material/lighting/offset tweaks to count as repair iterations.
- **Improvement**: Created `blender_orchestration/repair_strategies.py` (`RepairStrategyTier`, `REQUIRED_STRATEGY_BY_DEFECT`, `validate_repair_effectiveness()`) rejecting superficial fixes (`SUPERFICIAL_FIX_REJECTED`) when `STRUCTURAL_REMODEL` or `ROLLBACK_TO_BLOCKOUT` is required, plus automatic strategy escalation on persistent defects and `best_checkpoint` preservation.
- **Verification**: `test_sequential_isolation_and_repair.py` + before/after structural repair comparison in live Blender 5.3.0.

## 5. Triangle & Object Count Inflation Without Quality Gain
- **Code Locations**: `2c9f916:output/zelda_reference/technical_audit.json` (`121,752` triangles > `100,000` budget, `230` mesh objects, `32` materials) and `07_export.py` (last-minute `DECIMATE` only during export staging).
- **Root Cause**: Additive primitive fragments accumulated across passes instead of remodeling existing meshes cleanly, and subdivision/decimation proxies masked structural flaws.
- **Improvement**: `analyze_character_scene_metrics()` + `detect_polygon_inflation()` + `build_structural_remodel_bpy()` edit targeted meshes in-place while holding triangle, object-fragmentation, and material budgets.
- **Verification**: `test_character_qa_and_pipeline.py` & `test_zelda_regression.py`.

## 6. Independent QA `FAIL` Did Not Trigger True Remodeling or Block Release
- **Code Locations**: `2c9f916:output/zelda_reference/QA_report.md` (Gate A `FAIL` 7/10, yet `.glb` exported in `07_export.py`), `blender_orchestration/production_pipeline.py` (existed only as a Python library class not wired to live OpenCode execution).
- **Root Cause**: OpenCode agents ran ad-hoc bash/MCP commands outside `CharacterProductionPipeline` and had no execution harness gating Stages 6–11 behind Stage 5 (`modeling_visual_qa`).
- **Improvement**: Created `blender_orchestration/opencode_runner.py` (`OpenCodeCharacterRunner`) binding live OpenCode sessions directly to the 11-stage `CharacterProductionPipeline`, saving failed Gate A `.blend`/PNGs strictly as `DIAGNOSTIC_CHECKPOINT` and blocking Stages 6–11 (`APPROVED_RELEASE` prohibited).
- **Verification**: `test_sequential_isolation_and_repair.py` + live `OpenCodeCharacterRunner` execution.

## 7. Qwen Self-QA Rubber-Stamp `PASS`
- **Code Locations**: Prior `scripts/benchmark_preflight.py` legacy boolean inputs and missing live OpenCode vision reviewer integration in `scripts/run_e2e_character_production.py`.
- **Root Cause**: When Qwen evaluated its own text output or checked file existence on disk without an independent multimodal vision reviewer session and technical cross-check, it emitted `PASS` despite severe anatomical defects.
- **Improvement**: Created `blender_orchestration/multimodal_qa.py` (`IndependentMultimodalReviewer`) invoking an isolated `--archived` OpenCode reviewer session with real `--file` PNG attachments, distinct `reviewer_context_id != producer_context_id`, SHA-256 verification, fail-closed `NOT_VERIFIED` if vision/images fail, and hard technical cross-check override (`FAIL` if `analyze_character_scene_metrics` or `BLOCKER`/`MAJOR` defects exist regardless of model prose).
- **Verification**: `test_sequential_isolation_and_repair.py` (anti-rubber-stamp tests) + live `IndependentMultimodalReviewer` invocation.

## 8. Unverified `live-agent` Path & Missing Sequential GPT -> Reset -> Qwen Isolation
- **Code Locations**: `scripts/run_e2e_character_production.py` (`check_live_agent_readiness` stub) and absence of a sequential lifecycle-reset benchmark runner.
- **Root Cause**: `--mode live-agent` never invoked OpenCode CLI, and there was no finite state machine enforcing `IDLE -> GPT_RUNNING -> GPT_FINISHED -> RESETTING -> RESET_VERIFIED -> QWEN_RUNNING -> QWEN_FINISHED -> CLEANUP -> COMPARISON -> COMPLETE` with managed `BlenderLifecycleManager` PID/Xvfb/GPU reset verification between models.
- **Improvement**: Wired `run_e2e_character_production.py --mode live-agent` to `OpenCodeCharacterRunner`, and created `blender_orchestration/sequential_benchmark.py` + `scripts/run_sequential_gpt_qwen_e2e.py` enforcing `fcntl.flock` single-flight execution, `BlenderSessionResetter` via `BlenderLifecycleManager`, `BenchmarkIsolationGuard`, and `reports/session_isolation.json` + `reports/gpt_vs_qwen_comparison.md`.
- **Verification**: State-machine unit tests + real sequential GPT -> Blender Reset -> Qwen E2E execution.
