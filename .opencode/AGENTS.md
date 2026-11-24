# OpenCode Blender Production Policy & Orchestration

Concise orchestration entry point aligned with the upstream [blender-skills](https://github.com/arjun988/blender-skills) `blender-director` workflow and OpenCode native Skill discovery (`.opencode/skills/`). Detailed guides live under `docs/orchestration-architecture.md` and `docs/upstream-skills-audit.md`.

## 1. Blender-Only Asset Production Policy

All 3D asset creation MUST be performed through Blender controlled by OpenCode and Blender MCP:

- Supported production modes ONLY: `TEXT_ONLY` and `REFERENCE_GUIDED`.
- **Never introduce an `IMAGE_TO_3D` production mode.**
- **Strictly forbid** external AI 3D generation services or tools (`generate_3d`, `generate_gaussian_world`, Tripo, Hunyuan3D, Hyper3D, Meshy, or equivalent).
- **Prohibit premade meshes or downloaded 3D assets** (`search_assets`, `import_asset`) unless explicitly authorized by the user.
- Blender must own modeling, sculpting/form refinement, retopology, UVs, materials/textures, hair/clothing, rigging, animation, rendering/visual QA, and export/validation. Reference images are visual specifications only, never externally generated geometry.

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

## 6. Mandatory Quality Gates & Independent Visual QA

- **Gate A — Modeling Quality** (renders required): silhouette, face/eyes, hair, body proportions, hands/wrists, costume/accessories, materials, topology/poly budget, text-spec compliance (plus reference match in `REFERENCE_GUIDED`). Fail or uninspected blocks Gate B.
- **Gate B — Rigging & Deformation** (only after Gate A passes): bone hierarchy, skin weights, joint deformation, mesh continuity, cloth/hair deformation. Fails block Gate C.
- **Gate C — Animation** (only after Gate B passes): playback evidence, contact/flight phases, root stability, loop continuity, arm/leg coordination, foot sliding/clipping.
- **Final Technical Validation**: editable `.blend`, GLB/FBX export, skeleton/animation check, fresh-scene reimport check, engine check where available.
- **Independent Visual QA**: Rendered file on disk != visually inspected. Implementation agent must not act as sole final visual approver; unavailable reviewer -> `NOT_VERIFIED`; major defects -> `FAIL`.

## 7. Benchmark Isolation, Lifecycle Safety & Reproducibility

- Keep Blender MCP Lazy Loading, detached watchdog cleanup, GPU health/CPU fallback, and global `~/.config/opencode/opencode.json` unchanged. Never disturb unrelated GPU services (`strata`, `ollama`).
- Start every benchmark in a clean scene & isolated `YYYYMMDD_HHMMSS_MODELNAME` workspace; never inspect/reuse prior GPT/Qwen runs. Record prompt hash, `AGENTS.md` hash, Skills revision hash, MCP config/version, model/provider/variant, reasoning/hardware settings, and runtime metrics honestly; never claim strict equivalence when uncontrolled factors differ.
