# 11-Stage Character Production Pipeline (`blender_orchestration/production_pipeline.py`)

## 1. Overview

To eliminate single-script monolithic character generation and enforce hard quality barriers between modeling, rigging, animation, and export, character production is structured into **11 discrete stages** orchestrated by `CharacterProductionPipeline` (`blender_orchestration/production_pipeline.py`).

Each stage declares explicit:
- **Inputs**
- **Outputs**
- **Required Skills** (`SkillExecutionContract` evidence tracking in `blender_orchestration/contracts.py`)
- **Validation Result & Status** (`PASS` | `FAIL` | `BLOCKED` | `NOT_VERIFIED` | `SKIPPED_WITH_REASON`)

---

## 2. Stage Breakdown

| # | Stage ID | Primary Skills | Inputs | Outputs | Blocking Rule |
|---|---|---|---|---|---|
| 1 | `production_brief_and_identity` | `blender-director`, `character-archetypes`, `stylized-style` | Prompt, preflight policy, approved refs (if `REFERENCE_GUIDED`) | Production Brief, `QAProfile`, `reference_analysis.md` | `BLOCKED` if preflight blocks or Skills missing |
| 2 | `silhouette_and_proportion_blockout` | `character-artist` | Brief, clean Blender scene | `SM_Char_Body`, `SM_Char_Head`, `SM_Char_Hands`, proportions | Builds baseline meshes incrementally |
| 3 | `face_body_costume_modeling` | `character-artist` | Blockout meshes, live scene state | Sculpted facial planes, recessed eyes, articulated hands/grip, fitted costume & contoured boots | Preserves existing meshes (`SM_Char_Head`, `SM_Char_Body`) |
| 4 | `hair_and_detail_sculpting` | `hair-groom`, `sculpting` | Refined head & costume | `SM_Char_Hair_Scalp`, `SM_Char_Hair_Clumps`, `SM_Char_Hair_Braid`, `before_after_geometry` | Rejects Subsurf-only & tube-only hair |
| 5 | `modeling_visual_qa` (**Gate A**) | `rendering`, `qa-review` | Modeled character scene, `QAProfile`, `RepairLoopPolicy` | Multi-angle renders, `VisualReviewEvidence`, repair loop history, Gate A verdict | **Hard blocks Stages 6–11 on `FAIL` or `NOT_VERIFIED`** |
| 6 | `topology_uv_material_validation` | `retopology`, `uv-workflow`, `materials`, `texture-workflow` | Gate A passed character | Quad/budget audit, UV unwrap/pack report, `MAT_*` shaders & packed textures | Runs only after Stage 5 passes (unless explicit technical demo flag) |
| 7 | `rigging_and_skin_weights` | `rigging` | Validated topology & UV meshes | `ARM_Character`, symmetric `.L`/`.R` bones, vertex weights | `SKIPPED_WITH_REASON` if `animation_mode == "static"` |
| 8 | `joint_deformation_qa` (**Gate B**) | `qa-review`, `rigging` | Rigged character & pose test | Pose deformation renders, Gate B verdict | **Blocks Stages 9–11 if `FAIL` or `NOT_VERIFIED`** |
| 9 | `animation_production` | `animation` | Gate B passed rigged character | `ACT_RunCycle` Action & keyframes | `SKIPPED_WITH_REASON` if `static` or `rigged` |
| 10 | `animation_visual_qa` (**Gate C**) | `qa-review`, `animation` | Animated character | Playback frame renders, Gate C verdict | **Blocks Stage 11 if `FAIL` or `NOT_VERIFIED`** |
| 11 | `final_optimization_and_export` | `asset-optimization`, `export-pipeline` | QA-approved character | Editable `.blend`, `.glb`/`.fbx`, fresh-scene reimport check | Only ships when Gates A/B/C and technical audit pass |

---

## 3. Incremental Blender Scene Operations (`blender_orchestration/scene_ops.py`)

Rather than regenerating an entire character script on every fix, `scene_ops.py` provides stage-scoped, idempotent `bpy` builders executed via `MCPAdapter.execute_blender_json()`:
- `build_character_scene_init_code`: initializes clean collections (`COL_Character_Body`, `COL_Character_Face`, `COL_Character_Hair`, `COL_Character_Costume`, `COL_Rig`, `COL_Lights`, `COL_Export`, `COL_Collision`).
- `build_character_blockout_code` -> `build_character_face_body_costume_code` -> `build_character_hair_sculpt_code`: refine existing meshes in-place across stages.
- `build_character_targeted_fix_code`: modifies **only** the `target_objects` named in a `BlenderFixAction`, verifying `scene_objects_preserved == True` and reporting `metrics_before` vs. `metrics_after`.
- `build_character_scene_audit_code`: inspects `bmesh` manifold/loose-vert/quad metrics and `BVHTree` spatial checks (scalp coverage rays, eye protrusion depth, hand-to-prop contact distance).

---

## 4. Six Independent Production Verdicts (`ProductionVerdictSummary`)

Technical execution never masks visual defects. Every pipeline run reports 6 separate statuses:
1. `mcp_connection`
2. `blender_execution`
3. `skill_application`
4. `geometry_and_technical_validation`
5. `visual_quality_validation`
6. `final_asset_production` — `PASS` **only** when layers 1–5 are all `PASS`; `FAIL` if any layer is `FAIL`; `BLOCKED` if any layer is `BLOCKED`; otherwise `NOT_VERIFIED`.

---

## 5. Strengthened Skill Execution Contracts (`blender_orchestration/contracts.py`)

In addition to `sculpting` (rejects Subsurf-only and `polygon_inflation_only`), `retopology` (rejects `decimate_only`/`raw_primitive_stack` and budget overruns), `texture-workflow` (rejects base-color-only), `rigging`, and `animation`:
- `hair-groom`: explicitly rejects `rope_or_tube_only` and `scalp_exposure_detected`; requires `scalp_coverage_verified`, `clump_volume_verified`, and `penetration_checked`.
- `character-artist`: rejects `primitive_stacking_only` and fails when `facial_defects_unresolved`, `hand_grip_defects_unresolved`, or `costume_fit_defects_unresolved` remain open.
- `qa-review`: supports `strict_evidence=True` / `review_evidence` validation via `verify_visual_review_evidence()`.
