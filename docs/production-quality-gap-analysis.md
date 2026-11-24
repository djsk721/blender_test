# Production Quality & Harness Gap Analysis (Phase 1)

This report analyzes the existing `blender_test` repository (`feat/blender-director-orchestration` baseline at commit `2c9f916`), its orchestration code (`blender_orchestration/`, `scripts/`), OpenCode policy/skills (`.opencode/AGENTS.md`, `.opencode/skills/`), and the latest GPT Zelda reference study (`output/zelda_reference/`) across all 8 diagnostic questions.

---

## 1. Executive Summary of Root Causes

While the existing architecture established strong **process lifecycle safety** (Lazy Loading, detached idle watchdog, GPU/CPU fallback) and **policy-level guardrails** (Blender-Only policy, `TEXT_ONLY` vs. `REFERENCE_GUIDED`, `SkillExecutionContract` data structures), it lacked **runtime enforcement linking Blender modeling, multi-angle rendering, evidence-backed visual QA, and localized mesh repair**:

1. **Unenforced Gates & Export Leakage**: `evaluate_gate_a/b/c` and `evaluate_asset_qa` were passive functions rather than an execution barrier. Both `scripts/run_e2e_dual_mode.py` and `output/zelda_reference/` exported `.blend`/`.glb` deliverables even when visual QA was `NOT_VERIFIED` or `FAIL`.
2. **Parametric Primitive Stacking Masquerading as Character Modeling**: In `output/zelda_reference/steps/01_forms.py`–`06_final.py`, the character was built from 230 discrete parametric `sweep`/`ell`/`disk`/`strand` tubes and ellipsoids (`121,752` source triangles, `32` materials) while skipping `sculpting` and `retopology` (`SKIPPED_WITH_REASON`).
3. **Unstructured Defects Without Actionable Blender Repair Mapping**: Defects lived in free-form strings (`major_defects: list[str]`) or prose (`QA_report.md`) with no `VisualDefect` schema, no defect-to-`bpy` fix mapping, no before/after geometry delta check, and no stalled-loop prevention.
4. **Trust-Me Booleans Allowed QA Spoofing**: `evaluate_asset_qa()` and `evaluate_gate_a()` accepted bare `visually_inspected=True` and `independent_reviewer=True` without image SHA-256 hashes, reviewer/producer context separation, timestamps, asset hashes, or per-image observations.
5. **Hard-Surface/Ship Checklist Leakage into Character QA**: Upstream `.opencode/skills/references/validation-checklist.md` and `visual-match-checklist.md` embed ship/hull/metal-panel/greeble/nav-light rules (`"Windows, ports, vents placed on hull surfaces"`, `"red nav, green starboard"`, `"Reference requested full ship"`) with no asset-type QA profile filtering.

---

## 2. Diagnostic Answers to the 8 Core Questions

### Q1. Are MCP execution success and visual quality validation properly separated?
- **Policy: Yes. Runtime enforcement: Partial / Broken.**
- `.opencode/AGENTS.md` (Section 6) states that rendered files on disk do not equal visual inspection, and `output/zelda_reference/QA_report.md` honestly separated execution (`OpenCode executed the entire Blender workflow`) from visual approval (`FAIL / NO SHIP`, 7/10).
- **Gap**: In `scripts/run_e2e_dual_mode.py` (lines 517–556 and 797–830), the script records `pre_inspection_qa` as `NOT_VERIFIED` (`visually_inspected=False`) and immediately proceeds to checkpoint `.blend` and export/reimport `.glb` without blocking. Likewise, `output/zelda_reference/steps/07_export.py` generated `zelda_reference.glb` (98,398 tris) despite Gate A `FAIL`.

### Q2. Is loading a Skill cleanly separated from actually applying it to modeling?
- **Partially in `contracts.py`, weak for character/hair disciplines.**
- `blender_orchestration/contracts.py` prevents `loaded=True` with zero operations from becoming `APPLIED`, rejects Subsurf-only `sculpting` and Decimate-only `retopology`, and checks `texture-workflow` image nodes.
- **Gaps**:
  - `hair-groom` had **no dedicated validator** in `contracts.py` (fell into the generic `required_operations` string check), so rope/tube strands or scalp baldness could pass if operation names were listed.
  - `character-artist` only checked 4 operation name strings (`proportion_spec`, `body_blockout`, `facial_structure`, `topology_inspection`) without rejecting primitive stacking (`230` separate `sweep`/`ell`/`disk` meshes in `zelda_reference`) or unresolved facial/hand/costume defects.
  - `SkillExecutionContract` lacked `changed_objects`, `evidence_files`, `verification_result`, and `skill_md_sha256` fields.

### Q3. When modeling QA fails, are downstream production stages actually blocked?
- **No runtime blocking existed.**
- `scripts/benchmark_preflight.py` (`evaluate_gate_b`, `evaluate_gate_c`) and `blender_orchestration/pipeline.py` (`evaluate_character_gates`) return `BLOCKED` status dictionaries when called manually, **but there was no stage orchestrator executing Stage 1..11 sequentially that refused to run Stages 6–11 when Stage 5 (`Modeling Visual QA` / Gate A) failed or was `NOT_VERIFIED`**.
- In `output/zelda_reference/preflight.json` (lines 64–68), `qa_gates` remained `{gate_a: null, gate_b: null, gate_c: null}` even after `QA_report.md` reported Gate A failure.

### Q4. Are defects found in QA connected to concrete modification commands for the next modeling pass?
- **No programmatic linkage existed.**
- `evaluate_asset_qa()` and `evaluate_gate_a()` only accepted `major_defects: list[str]` and `findings: list[str]`.
- In `scripts/run_e2e_dual_mode.py` (lines 738–773), Pass 2 (`build_lantern_pass2_refinement_code`) was hardcoded rather than derived from structured QA defect objects.
- In `output/zelda_reference/steps/05_refine.py` and `06_final.py`, fixes were ad-hoc LLM edits that left critical defects unresolved (`Hair_Scalpcap` gap, round protruding eye shells, segmented fingers, bulbous boots, detached thigh trim) and had no before/after defect resolution matrix or stall detector.

### Q5. Does an independent visual reviewer inspect actual rendered images?
- **Only ad-hoc in the Zelda study; NOT enforced in code or E2E.**
- In `output/zelda_reference/QA_report.md` (line 24), an independent read-only OpenCode subagent (`ses_df21568fdffeTFCCm5JetCN3nL`) inspected `comparison.png`, `hero.png`, `detail.png`, and `three_quarter.png`, catching real defects (7/10 match, scalp patch, round eyes, stiff fingers, bulbous boots, floating thigh seam).
- **Gap**: In `blender_orchestration/pipeline.py`, `scripts/benchmark_preflight.py`, and `scripts/run_e2e_dual_mode.py`, there was no `VisualReviewEvidence` schema binding per-image SHA-256 hashes, explicit visual observations per render angle, reviewer context ID vs. producer context ID, or timestamp. `run_e2e_dual_mode.py` only ran `analyze_png_features()` (mean RGB and amber pixel ratio) and never performed or verified semantic visual inspection.

### Q6. Can QA results be forged via bare Boolean flags or report-only PASS strings?
- **Yes — high spoofing risk.**
- In `blender_orchestration/pipeline.py` (`evaluate_asset_qa`, lines 152–195), passing `rendered_images=["a.png"]`, `visually_inspected=True`, `independent_reviewer=True`, `findings=["looks good"]`, `major_defects=[]`, `technical_checks_passed=True`, `within_poly_budget=True` returned `status="PASS"` **even if `a.png` did not exist on disk, had no SHA-256, had no reviewer ID, had the same context ID as the producer, or contradicted an asset-type policy**.
- Similarly, `scripts/benchmark_preflight.py` (`evaluate_gate_a`, lines 393–423) trusted `rendered_images_inspected=True` and `independent_reviewer=True` without verifying any evidence payload or immutable approval policy digest.

### Q7. Do existing E2E tests verify actual character production quality, or only Blender MCP connectivity and props?
- **Only MCP connectivity and deterministic static props.**
- `scripts/run_e2e_dual_mode.py` only tests two hardcoded prop builders: `StylizedFantasyCrate` (`TEXT_ONLY`) and `AlchemyLantern` (`REFERENCE_GUIDED`).
- `tests/blender_mcp/test_orchestration.py` uses `MockMCPTransport` for `T08`/`T09` and only checks `StdioMCPTransport.discover_capabilities()` in `T12`.
- Neither the E2E script nor the test suite verified:
  - an 11-stage character pipeline,
  - character modeling quality criteria (face/hair/hands/costume/boots/topology),
  - localized mesh modification preserving existing Blender scene state during QA repair,
  - rigging deformation + run-cycle animation QA,
  - independent 6-layer verdicts (`MCP` / `Blender Exec` / `Skill Applied` / `Geometry+Tech` / `Visual Quality` / `Final Production`),
  - or regression detection against `output/zelda_reference/`.

### Q8. Are GPT and Qwen given identical production environments and validation criteria?
- **Preflight policy & Director routing: Yes. Full benchmark harness & Skills presence check: Incomplete.**
- `scripts/benchmark_preflight.py` (`resolve_preflight_policy`) and `blender_orchestration/skill_registry.py` (`BlenderDirector`) are model-agnostic (verified by `test_T06` and `test_T14`).
- **Gaps**:
  - `compare_run_environments()` only compared `run_environment` fields and `preflight`, without checking `prompt_sha256` or `reference_assets_sha256` to reject mismatched-input cross-model comparisons as unfair, and without separating **quality**, **runtime**, and **tool-call efficiency** metrics.
  - `build_benchmark_metadata()` did not enforce `YYYYMMDD_HHMMSS_MODELNAME` directory/run-id validation, did not separate `REGRESSION_REPLAY` from `INDEPENDENT_BENCHMARK` isolation, and did not verify `.opencode/skills/` installation completeness in preflight (critical because `.opencode/skills/` is gitignored in `.gitignore`).
  - `PerformanceMetrics` lacked structured `qa_failure_count`, `qa_fix_history`, `modeling_iteration_count`, `skill_call_count`, and `mcp_tool_call_count` separation.

---

## 3. Visual & Technical Audit of `output/zelda_reference/` (Baseline Regression Evidence)

Direct inspection of `output/zelda_reference/comparison.png`, `detail.png`, `three_quarter.png`, `QA_report.md`, `technical_audit.json`, `export_validation.json`, and `steps/*.py` confirms the following concrete defects that the improved harness must detect, block, and map to localized Blender fixes:

| ID | Category | Observed Defect in `output/zelda_reference/` | Root Cause in Step Scripts |
|---|---|---|---|
| `ZLD-001` | Reference Match | Overall Reference Match `7/10` (`< 8/10` pass threshold) -> Gate A `FAIL` | Broad silhouette/costume approximated, but face, hair, hands, trousers, and boots diverge |
| `ZLD-002` | Face & Eyes | Overly round, flat-outline doll-like eyes, broad forehead, soft nose/cheek planes (`detail.png`) | `02_face_hair.py`: eyes built as stacked offset disks (`_LashBase`, `_Sclera`, `_Limbal`, `_Iris`, `_Pupil`, `_Glint`) projected onto a smooth `sweep` head ellipsoid with shallow Gaussian vertex offsets |
| `ZLD-003` | Scalp Coverage | Skin-colored bald patch visible behind the crown braid (`detail.png`, `three_quarter.png`) | `02_face_hair.py` lines 55–66: `Hair_Scalpcap` parametric angular formula undershoots the upper-back cranium edge behind `Hair_Braid_*` |
| `ZLD-004` | Hair Mass | Uniform, rope/tube-like hair locks and repetitive `ell` braid spheres | `01_forms.py`/`02_face_hair.py`: `sweep(..., flute=0.075)` tubes + `Hair_Contour_*` strands + `ell('Hair_Braid_A/B')` spheres instead of sculpted hair volume and tapered flow |
| `ZLD-005` | Hands & Grip | Stiff segmented fingers; fingers lie parallel on Sheikah Slate instead of wrapping (`detail.png`) | Discrete finger cylinders (`Finger_Glove_*`, `SlateFingerCap*`) lacking articulated palm/knuckle bridge or wrapped contact around `SM_Study_Slate` |
| `ZLD-006` | Boots | Bulbous, ballooned brown boots lacking ankle/instep/heel structure (`three_quarter.png`) | `01_forms.py` lines 84–85 + `06_final.py`: thick fluted `sweep` tubes for `Raised_Boot`/`Long_Boot` and puffy `BootCollar` |
| `ZLD-007` | Costume Seam | Outer thigh seam strip detached/floating away from leg (`three_quarter.png`) | `strand()` trim coordinates in `03_costume.py` offset away from the swept trouser surface without surface snapping/shrinking |
| `ZLD-008` | Pose / Joints | Stiff shoulders and knees (`comparison.png`, `three_quarter.png`) | Continuous `sweep` sleeves/trousers with no joint edge-loop topology or deltoid/knee anatomical inflection |
| `ZLD-009` | Topology & Budget | `121,752` source triangles (`> 100,000` budget), `230` discrete mesh objects, `32` materials | `technical_audit.json` lines 2–6: `within_budget: false`; primitive stacking across 230 unmerged objects and 32 materials; `07_export.py` used `DECIMATE` only at export staging |

---

## 4. Upstream Checklist Contamination Found in `.opencode/skills/references/`

- `.opencode/skills/references/validation-checklist.md` (line 39) & `visual-match-checklist.md` (lines 38, 42–46, 108–109) assume ship/hard-surface assets (`hull surfaces`, `metal reads as metal`, `greeble density`, `red nav, green starboard`, `Reference requested full ship`).
- **Resolution**: Preserve `.opencode/skills/` vendor files untouched per policy, and implement asset-type QA profiles in `blender_orchestration/qa_profiles.py` (`HUMANOID_CHARACTER`, `CREATURE`, `HARD_SURFACE_PROP`, `ENVIRONMENT`, `ANIMATED_CHARACTER`) that enforce character-specific criteria and reject ship/hull/mechanical criteria on character tasks.
