# Upstream `arjun988/blender-skills` vs. OpenCode Installation Audit

## 1. Audited Revisions

| Item | Value |
|---|---|
| Upstream repository | `https://github.com/arjun988/blender-skills` |
| Upstream `HEAD` commit | `904f095d8f36a6e72adaebb08d07f884a074743d` |
| Upstream release (`version.json`) | `1.3.0` (`date`: `2026-07-10`, `skills`: `94`, `references`: `175`) |
| Upstream skill root | `.claude/skills/` (`269` files across `94` skill directories + shared `references/`) |
| Local OpenCode skill root | `/home/jhkim/tools/blender_test/.opencode/skills/` (`269` files, `94` skill directories) |
| Active Blender MCP server | `mcp-for-blender v2.3.1` (`serverInfo`: `MCP for Blender v1.30.0`, `10` tools) |
| Host Blender runtime | `Blender 5.3.0` (`build date: 2026-11-10`) via Xvfb + `.opencode/mcp/blender/` |

---

## 2. Structural Comparison & Integrity Findings

Comparing every file under upstream `.claude/skills/` against local `.opencode/skills/` by Git blob SHA-1:

- **Skill directories**: `94 / 94` present locally (`0` missing, `0` extra).
- **Total files**: `269 / 269` present locally (`0` missing, `0` extra).
- **YAML frontmatter**: `94 / 94` `SKILL.md` files contain valid frontmatter (`0` missing).
- **Shared references (`.opencode/skills/references/`)**: All `9` shared documents (`asset-pipeline.md`, `mcp-integration.md`, `mcp-tools.md`, `naming-conventions.md`, `polycount-budgets.md`, `reference-analysis-template.md`, `reference-image-match.md`, `validation-checklist.md`, `visual-match-checklist.md`) are present verbatim.
- **Relative `.md` links**: `0` broken relative links (`references/*.md` and `../references/*.md`) across all `269` markdown files.
- **Modified vendor files**: Exactly `1` file differs from upstream commit `904f095d`:
  - `.opencode/skills/blender-director/SKILL.md`: removes a duplicate `role: specialist` line in YAML frontmatter, preserving `role: orchestrator`.

**Conclusion**: The OpenCode `.opencode/skills/` installation is structurally complete and intact. Vendor Skill files do not need reinstallation and are preserved unmodified.

---

## 3. Architectural Differences & Incompatibilities Identified

| Area | Upstream Documentation (`arjun988/blender-skills`) | Actual Live OpenCode + `mcp-for-blender` Environment | Remediation Implemented |
|---|---|---|---|
| **Skill discovery directory** | `.claude/skills/` or `.cursor/skills/`, `.mcp.json` / `.cursor/mcp.json` | `.opencode/skills/` + project-local `opencode.json` + `.opencode/AGENTS.md` | `SkillRegistry` discovers `.opencode/skills/*/SKILL.md`; `install.sh` targets `.opencode/skills/` and `opencode.json` without modifying global config |
| **Director vs. fixed checklist** | `SKILLS_GUIDE.md` & `blender-director/SKILL.md` route dynamically per asset type, style, and animation needs | Prior `.opencode/AGENTS.md` imposed a fixed 17-step character sequence | Replaced fixed sequence with `BlenderDirector` dynamic routing + explicit omission reasons |
| **Visual capture tool name** | `qa-review/SKILL.md` & `references/mcp-integration.md` cite `get_viewport_screenshot` | `mcp-for-blender v1.30.0` exposes `look` (not `get_viewport_screenshot`) | `MCPAdapter.resolve_tool("visual_capture")` inspects live `tools/list` schemas and maps dynamically (`look` -> `get_viewport_screenshot` -> `execute_blender_code`) |
| **Object/material/export MCP tools** | `mcp-integration.md` assumes dedicated object/mesh/modifier/UV/material/export MCP tools | Live `mcp-for-blender` provides `10` tools (`get_addon_status`, `disable_telemetry`, `get_scene_info`, `execute_blender_code`, `record_trajectory_feedback`, `look`, `generate_3d`, `generate_gaussian_world`, `search_assets`, `import_asset`) | Domain operations execute via small, idempotent, stage-scoped `bpy` builders in `blender_orchestration/scene_ops.py` through `execute_blender_code`, audited via `get_scene_info` |
| **AI 3D & premade asset tools** | `mcp-tools.md` notes optional `Poly Haven`, `Hyper3D` / `Hunyuan3D` | Project enforces strict **Blender-Only Asset Production Policy** (no `IMAGE_TO_3D`, no Tripo/Hunyuan3D/Hyper3D/Meshy) | `MCPAdapter` unconditionally blocks `generate_3d`, `generate_gaussian_world`, and generator providers; `search_assets`/`import_asset` blocked by default |
| **Text-only vs. reference-guided** | Vendor docs assume attached photo -> `reference-image-match`, or standard pipeline when absent | Benchmarks require explicit separation of `TEXT_ONLY` vs `REFERENCE_GUIDED` without invalidating vendor `SKILL.md` files | Handled centrally via `.opencode/AGENTS.md` + `scripts/benchmark_preflight.py` + `BlenderDirector` |
| **Superficial completion risk** | No programmatic evidence gate checking whether sculpt/retopo/UV/texture/QA actually ran | Previous runs substituted monolithic scripts or subsurf/decimation proxies and duplicated objects (`.006`–`.167` suffixes) | Added `SkillExecutionContract` validators, idempotent scene ops, 3-attempt bounded recovery, and Gate A/B/C verification |
