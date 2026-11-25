# OpenCode Blender Production Policy & Orchestration

Concise orchestration entry point aligned with the upstream [blender-skills](https://github.com/arjun988/blender-skills) `blender-director` workflow and OpenCode native Skill discovery (`.opencode/skills/`). Detailed guides live under `docs/orchestration-architecture.md` and `docs/upstream-skills-audit.md`.

## 1. Blender-Only Asset Production Policy

All 3D asset creation MUST be performed through Blender controlled by OpenCode and Blender MCP:

- Supported production modes ONLY: `TEXT_ONLY` and `REFERENCE_GUIDED`.
- **Never introduce an `IMAGE_TO_3D` production mode.**
- **Strictly forbid** external AI 3D generation services or tools (`generate_3d`, `generate_gaussian_world`, Tripo, Hunyuan3D, Hyper3D, Meshy, or equivalent).
- **Prohibit premade meshes or downloaded 3D assets** (`search_assets`, `import_asset`, downloaded finished characters, unauthorized costumes/hair/props) unless covered by the narrowly scoped approved Base Mesh exception below.
- **Approved Generic Anatomical Base Mesh Exception**: Verified generic anatomical starting meshes from official CC0 sources (such as Blender Foundation's official **Human Base Meshes** bundle) may be loaded natively inside Blender (`bpy.data.libraries.load`) via Blender MCP **only after** recording official source, CC0 license, version, SHA-256 hash, original object IDs, Blender version compatibility, and topology/UV inspection. This exception applies identically across `TEXT_ONLY` and `REFERENCE_GUIDED`.
- Blender must own modeling, semantic stylization, garments, hair/grooming, sculpting/form refinement, retopology, UVs, materials/textures, rigging, animation, rendering/visual QA, and export/validation. Reference images are visual specifications only, never externally generated geometry.

## 2. Blender Director Orchestration & Dynamic Routing

Every new Blender asset task MUST start with `blender-director` as primary orchestrator:

1. Analyze asset type, target engine, art style, animation needs, and polygon/texture/material budgets.
2. Dynamically select only relevant specialist Skills under `.opencode/skills/` in dependency order — **never enforce a fixed universal checklist across unrelated disciplines**.
3. Output a structured Production Brief documenting selected Skills and explicit reasons for omitted Skills.
4. Load each selected `SKILL.md` completely **on demand for the current stage only** (plus required `references/`). Never preload all 94 Skills at startup, and never re-read unchanged Skill docs repeatedly.
5. Record native skill discovery status; if native Skill loading fails, diagnose discovery/permissions and read the full `SKILL.md` only as a reported fallback.

Preserve vendor Skill files under `.opencode/skills/` unmodified wherever possible; resolve mode/MCP differences through this policy and `blender_orchestration/`.

## 3. Skill Execution Contracts (Loading != Applied)

A Skill loading call never proves the workflow was applied. Track every active Skill via `SkillExecutionContract` (`PENDING` | `IN_PROGRESS` | `APPLIED` | `SKIPPED_WITH_REASON` | `FAILED` | `NOT_VERIFIED`):

- Mark `APPLIED` only when required operations, real MCP/scene evidence, and validation checks exist.
- `execute_blender_code` is a transport mechanism, not proof a discipline was completed.
- Enforce real discipline evidence:
  - `sculpting`: actual sculpt/form-refinement pass + before/after geometry; Subdivision Surface alone -> `NOT_VERIFIED`.
  - `retopology`: distinct retopology pass, quad-dominant hero flow, joint edge loops, and topology check; simple decimation or primitive creation alone -> `NOT_VERIFIED`.
  - `texture-workflow`: actual texture files/image nodes; base colors alone cannot claim baking -> `NOT_VERIFIED`.
  - `qa-review`: real image captures + explicit visual inspection + findings + `PASS`/`FAIL`.

## 4. MCP-First Execution, Scene Hygiene & Bounded Recovery

1. Discover live MCP tool schemas before planning execution; never assume upstream example tool names exist (live `mcp-for-blender` exposes `look` for visual capture instead of `get_viewport_screenshot`, and uses `execute_blender_code` for mesh/UV/material/rig/export operations).
2. Query scene state (`get_scene_info`) before and after meaningful stages.
3. Build reusable, stage-specific, idempotent `bpy` operations (`COL_*`, `SM_*`, `MAT_*`) instead of regenerating monolithic scripts or accumulating duplicate `.001` objects/scenes/cameras.
4. Cap repeated failures at 3 attempts: (1) execute + collect evidence -> (2) diagnose + targeted fix -> (3) justified alternative -> else save checkpoint, report unresolved defect, and halt dependent stages. Never regenerate an entire hero asset for a localized defect.

## 5. Instruction Precedence & Reference Policy

Apply identically across all LLMs/providers (no model-specific routing):

1. Explicit user requirements determine `TEXT_ONLY` vs `REFERENCE_GUIDED`.
2. This `.opencode/AGENTS.md` defines execution and Blender-only guardrails.
3. Installed `.opencode/skills/` provide stage-specific workflows (project `TEXT_ONLY` rules override vendor reference assumptions without editing third-party `SKILL.md` files).
4. Mandatory QA gates apply in both modes.

- **Mode A (`REFERENCE_GUIDED`)**: Requires approved references -> (1) write Reference Analysis before modeling -> (2) establish camera correspondence (`CAM_*`) -> (3) build geometry in Blender via MCP -> (4) render comparison views -> (5) compare renders vs references -> (6) fix discrepancies via Blender operations (<= 3 passes) -> (7) preserve evidence & report unverifiable details honestly. Missing mandatory references -> `BLOCKED` (no fabricated scores).
- **Mode B (`TEXT_ONLY`)**: Uses supplied textual specification -> forbids searching/downloading/generating/requiring references or reusing prior runs -> proceeds with Blender modeling without asking extra authorization -> evaluates modeling quality against textual spec -> reports exact original-character likeness separately as `NOT_VERIFIED` (`NOT_VERIFIED` likeness != `FAIL` modeling quality, while visible defects still produce `FAIL`).

Run `scripts/benchmark_preflight.py` (or `blender_orchestration`) before modeling to record preflight JSON without hardcoding results.

## 6. 11-Stage Character Pipeline, QA Profiles & Mandatory Quality Gates

Execute character assets through 11 explicit stages with clear inputs, outputs, and validation gates (`blender_orchestration/production_pipeline.py`):
1. Production Brief & Character Identity -> 2. Silhouette & Proportion Blockout -> 3. Face, Body, Hands & Costume Modeling -> 4. Hair Groom & Sculpt Form Refinement -> 5. Modeling Visual QA & Bounded Repair Loop (**Gate A**) -> 6. Topology, UV & Material Validation -> 7. Rigging & Skin Weights -> 8. Joint Deformation QA (**Gate B**) -> 9. Animation Production -> 10. Animation Visual QA (**Gate C**) -> 11. Final Optimization & Export.

- **Asset-Type QA Profiles (`blender_orchestration/qa_profiles.py`)**: Select `HUMANOID_CHARACTER`, `ANIMATED_CHARACTER`, `CREATURE`, `HARD_SURFACE_PROP`, or `ENVIRONMENT`. Never apply ship/hull/metal-panel/greeble/mechanical criteria to characters.
- **Real 3D Anatomical Modeling (No Shortcuts)**: Never finalize a character using UV-sphere eyeballs pasted onto smooth heads, circular tube/rope hair, unjoined finger cylinders, 2D reference pixel-to-depth projection, or Subdivision-only smoothing. Construct recessed orbital eye sockets + eyelid wraps, continuous palm/knuckle/phalanx hands, full-coverage manifold scalp + sculpted directional clumps, contoured heeled boots, and deformation-ready joint loops (`blender_orchestration/character_modeling.py`). Increasing polygon count without resolving defects fails QA.
- **Gate A — Modeling Quality** (renders required): silhouette, face/eyes, hair, body proportions, hands/wrists, costume/boots, materials, topology/poly budget, text-spec compliance (plus reference match >= 8/10 in `REFERENCE_GUIDED`). **Any Gate A `FAIL` or `NOT_VERIFIED` hard-blocks Stages 6–11, Gate B, Gate C, and Approved Release Export.** Failed runs may save `DIAGNOSTIC_CHECKPOINT` artifacts only.
- **Gate B — Rigging & Deformation** (only after Gate A passes): bone hierarchy, skin weights, joint deformation, mesh continuity, cloth/hair deformation. Fails block Gate C and export.
- **Gate C — Animation** (only after Gate B passes): playback evidence, contact/flight phases, root stability, loop continuity, arm/leg coordination, foot sliding/clipping.
- **Structural Repair Strategy Engine (`blender_orchestration/repair_strategies.py`, `visual_qa.py`)**: Record every defect as a structured `VisualDefect` -> map to `BlenderFixAction` enforcing required `RepairStrategyTier` (`STRUCTURAL_REMODEL` / `ROLLBACK_TO_BLOCKOUT` for anatomical/silhouette defects; color/light/offset tweaks alone are rejected as `SUPERFICIAL_FIX_REJECTED`) -> re-render -> verify 3D geometry delta and before/after visual improvement (max 3 fix iterations; pure re-renders do not count; persistent defects escalate strategy or halt as `FAIL` while preserving `best_checkpoint`).
- **Final Technical Validation**: editable `.blend`, GLB/FBX export, skeleton/animation check, fresh-scene reimport check, engine check where available.
- **Independent Multimodal Visual QA (`blender_orchestration/multimodal_qa.py`)**: Bare `visually_inspected=true` / `independent_reviewer=true` booleans cannot pass QA. Attach actual rendered PNGs to a separate archived OpenCode session (`reviewer_context_id != producer_context_id`), cross-check against deterministic scene/mesh audits, and bind SHA-256 render/asset hashes plus immutable `approval_policy_digest`. Unavailable reviewer or unreadable images -> `NOT_VERIFIED`; major or technical defects -> `FAIL`.

## 7. Benchmark Isolation, Sequential Reset & Reproducibility

- Keep Blender MCP Lazy Loading, detached watchdog cleanup, GPU health/CPU fallback, and global `~/.config/opencode/opencode.json` unchanged. Never disturb unrelated GPU services (`strata`, `ollama`) or external user Blender processes, and never use `pkill`/`killall`/`kill -9`.
- Run cross-model benchmarks strictly sequentially (`IDLE -> GPT_RUNNING -> GPT_FINISHED -> RESETTING -> RESET_VERIFIED -> QWEN_RUNNING -> QWEN_FINISHED -> CLEANUP -> COMPARISON -> COMPLETE` via `BlenderLifecycleManager`) with a full Blender process + Xvfb + GPU + empty-scene reset between models (`reports/session_isolation.json`).
- Start every benchmark in a clean scene & isolated `YYYYMMDD_HHMMSS_MODELNAME` workspace; never inspect/reuse prior model runs or `output/zelda_reference/`. Verify `.opencode/skills/` via `benchmark_preflight.py` before execution. Record prompt hash, `AGENTS.md` hash, Skills revision hash, Blender/MCP versions, model/provider/variant, reasoning/hardware settings, skill/MCP efficiency metrics, QA fix history, and stage timings honestly; never claim direct cross-model comparison when prompts or references differ.
