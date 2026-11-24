# OpenCode Blender Skills Orchestration Architecture

## 1. Overview & Blender-Only Policy

This architecture aligns OpenCode + Blender MCP with the upstream [arjun988/blender-skills](https://github.com/arjun988/blender-skills) `blender-director` workflow while preserving every project-local lifecycle, GPU safety, lazy loading, and QA guarantee under `.opencode/mcp/blender/`.

### Strict Blender-Only Asset Production Policy

- **All 3D asset creation runs strictly in Blender** through OpenCode and Blender MCP.
- **Only two production modes exist**: `TEXT_ONLY` and `REFERENCE_GUIDED`. There is **no `IMAGE_TO_3D` mode**.
- External AI 3D generators (`generate_3d`, `generate_gaussian_world`, Tripo, Hunyuan3D, Hyper3D, Meshy) are **unconditionally blocked** in both [scripts/benchmark_preflight.py](../scripts/benchmark_preflight.py) and [blender_orchestration/mcp_adapter.py](../blender_orchestration/mcp_adapter.py).
- Premade/downloaded 3D meshes (`search_assets`, `import_asset`) are blocked unless explicitly authorized.

---

## 2. Logical Architecture

```text
User Prompt + Approved References (if REFERENCE_GUIDED)
    │
    ▼
[1] benchmark_preflight.py (TEXT_ONLY vs REFERENCE_GUIDED; no IMAGE_TO_3D)
    │
    ▼
[2] blender_orchestration.BlenderDirector (.opencode/skills/blender-director)
    ├── SkillRegistry (.opencode/skills/*/SKILL.md + shared references/)
    ├── Dynamic Specialist Selection + Explicit Omission Reasons
    └── Stage-by-Stage Production Brief & Budgets
    │
    ▼
[3] Stage Execution + SkillExecutionContracts (loading != APPLIED)
    ├── blender_orchestration.scene_ops (idempotent bpy stage builders)
    ├── blender_orchestration.mcp_adapter (schema discovery, `look`, 3-attempt retry)
    └── .opencode/mcp/blender/mcp_wrapper.py (Lazy Loading + Watchdog + GPU Safety)
    │
    ▼
[4] Visual QA & Technical Validation
    ├── Independent Rendered Visual Inspection (file existing != inspected)
    ├── Gate A -> Gate B -> Gate C (characters) or Asset QA (props/envs)
    └── .blend Checkpoint + GLB/FBX Export + Reimport Verification
```

---

## 3. Upstream Recommendations vs. Custom OpenCode Integration

| Aspect | Official Upstream (`blender-skills`) | OpenCode Custom Integration (`blender_orchestration/`) |
|---|---|---|
| Skill directory | `.claude/skills/` | `.opencode/skills/` (all 94 skills + `references/` preserved) |
| Config file | `.mcp.json` / `.cursor/mcp.json` | Project-local `opencode.json` (`mcp.servers.blender`) |
| Director routing | Prompt-driven `blender-director/SKILL.md` tables | Programmatic `BlenderDirector` + concise `.opencode/AGENTS.md` entry point |
| Execution proof | Honor-system checklists in markdown | `SkillExecutionContract` validators (`APPLIED` requires real MCP/scene/validation evidence) |
| Screenshot tool | Assumes `get_viewport_screenshot` | `MCPAdapter.resolve_tool("visual_capture")` maps to live `look` tool via `tools/list` |
| Geometry tools | Assumes dedicated object/UV/material MCP tools | `scene_ops.py` executes small, idempotent stage `bpy` builders via `execute_blender_code` |
| Process & GPU safety | Direct running Blender expected | `.opencode/mcp/blender/` Lazy Loading, `flock`, Xvfb, idle watchdog, NVIDIA GPU fallback |
| Generator guardrails | Mentions optional Hyper3D/Hunyuan3D/Poly Haven | `MCPAdapter` & preflight strictly forbid AI 3D generators and default-block external meshes |

---

## 4. Blender Director Dynamic Routing & Stage Examples

`BlenderDirector` never hardcodes model or provider names and emits identical routes for GPT, Qwen, or any other model.

### Example 1: Game Character (Stylized + Animated for Godot)
```text
blender-director -> character-archetypes -> character-artist -> stylized-style
  -> sculpting -> retopology -> hair-groom -> uv-workflow -> materials -> texture-workflow
  -> qa-review (Gate A) -> rigging -> animation -> qa-review (Gate B/C)
  -> asset-optimization -> export-pipeline -> godot-export
```
*Omitted*: `vehicle-artist`, `environment-artist`, `vegetation-artist`, `hard-surface`, `prop-artist`, `unity-export`, `unreal-export`.

### Example 2: Hard-Surface Weapon (Realistic Sci-Fi Rifle)
```text
blender-director -> hard-surface -> realistic-style -> uv-workflow -> materials
  -> asset-optimization -> qa-review -> export-pipeline
```
*Omitted*: `character-artist`, `sculpting`, `retopology`, `hair-groom`, `rigging`, `animation`, `environment-artist`.

### Example 3: Fantasy Environment
```text
blender-director -> fantasy-worlds -> environment-artist -> scene-assembly
  -> materials -> lighting -> rendering -> qa-review
```

### Example 4: Godot Export Pipeline
When `target_engine == "godot"` (e.g., character or hero prop with `"for Godot"`), routing appends `asset-optimization -> export-pipeline -> godot-export`, enforcing `+Y` up `.glb` export, applied transforms, `COL_Export` isolation, and fresh-scene GLB reimport verification.

---

## 5. Skill Execution Contracts & Evidence Rules

Tracked fields per selected Skill (`blender_orchestration/contracts.py`):
`skill_id`, `production_stage`, `selection_reason`, `loaded`, `required_operations`, `performed_operations`, `mcp_evidence`, `scene_evidence`, `validation_evidence`, `status` (`PENDING` | `IN_PROGRESS` | `APPLIED` | `SKIPPED_WITH_REASON` | `FAILED` | `NOT_VERIFIED`).

- **Loading != APPLIED**: Loading `SKILL.md` with zero performed operations/evidence yields `NOT_VERIFIED`.
- **Sculpting**: Requires real sculpt/form-refinement operation + before/after geometry metrics; `Subdivision Surface` alone -> `NOT_VERIFIED`.
- **Retopology**: Hero assets require quad-dominant topology (`quad_ratio >= 0.70`), joint edge loops, and topology validation; simple `Decimate` or primitive creation alone -> `NOT_VERIFIED`.
- **Texture Workflow**: Requires actual image files or assigned image texture nodes; base color assignment alone cannot claim baking.
- **Reference Image Match (`REFERENCE_GUIDED`)**: Requires approved reference provenance, written `reference_analysis.md` before modeling, `CAM_*` camera correspondence, Blender-built geometry (`ai_3d_generators_used=False`), 1–3 visually inspected comparison renders, and discrepancy resolution.
- **QA Review**: Rendered image file existing on disk != visual inspection (`visually_inspected=True` required).

---

## 6. MCP Adaptation, Scene Hygiene & Bounded Recovery

- **Runtime schema discovery**: `MCPAdapter.discover_capabilities()` runs `initialize` + `tools/list` (keeping 0 Blender processes running until the first Blender-backed call).
- **Idempotent naming**: `scene_ops.py` organizes scenes with `COL_Reference`, `COL_Blockout`, `COL_HighPoly`, `COL_LowPoly`, `COL_Collision`, `COL_Lights`, `COL_Export`, `SM_*`, `MAT_*`, and `CAM_*`, removing replaced stage datablocks so reruns never accumulate `.001` duplicates.
- **Bounded 3-attempt failure loop**: `MCPAdapter.execute_with_retry(..., max_attempts=3, checkpoint_fn=...)` executes Attempt 1 (collect evidence) -> Attempt 2 (targeted diagnosis/fix) -> Attempt 3 (justified alternative) -> saves `.blend` checkpoint and halts dependent stages rather than regenerating whole scenes or burning tokens.

---

## 7. Benchmark Reproducibility & Portability

`build_reproducibility_manifest()` records:
- `prompt_sha256`, `agents_md_sha256`, `git_revision`, `skills_revision_hash` (SHA-256 across all 269 skill files), `mcp_config_sha256`, `mcp_lifecycle_version`, `mcp_server_info`, `discovered_tools`, `preflight`, and `run_environment` (`model_id`, `provider`, `variant`, `reasoning_settings`, `hardware_info`).
- Unavailable metrics (`model_input_tokens`, `model_output_tokens`, `compaction_events`) are recorded explicitly as `"unavailable"` rather than fabricated.
- Portable installation into other OpenCode projects uses `./scripts/install.sh --with-orchestration --with-policy --skills-source <skills_dir> <target>` (see [installation.md](installation.md)).
