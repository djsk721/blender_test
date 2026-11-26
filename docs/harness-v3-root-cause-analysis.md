# Harness V3 — Root-cause analysis (2026-11-26)

Scope: why the previous Qwen Zelda run plateaued at independent modeling QA 54 → 56 → 58 / 100
and never passed the modeling gate. Every claim below cites code or a measurement taken on
2026-11-26 against branch `feat/gpt-supervised-qwen-production` @ `46fa59a` plus the
uncommitted multi-reference work (checkpointed unmodified as `83da549`).

Previous run artifacts were deleted, so per-render findings from that run cannot be re-verified;
the causes below are those reproducible from the code and from live capability probes.

## Category map

| # | Category | Finding | Evidence |
|---|---|---|---|
| 1 | Correction-loop control | Geometry and lookdev repair are hard-capped at two passes regardless of `--max-stage-corrections`. | `autonomous_harness.py:1463` `max_geo_fixes = min(2, self.max_stage_corrections)`; same at `:1663`. |
| 2 | Correction-loop control | No per-defect tracking, stagnation or regression detection. "Best" candidate is chosen by a name heuristic (`hair`/`slate`/`prop` substrings), so a candidate can win on mesh names rather than review score. | `_scene_completeness_tier` (`:141`), used at `:1499`. |
| 3 | Work-order design | All Stage-3 detail (face, hair, costume, hands, accessories) is crammed into **two** Qwen scripts (`component_passes[:2]`) under a "<180 lines" instruction. Face, hair and costume never get a dedicated build/review/correct cycle. | `:1380`, prompt text at `:693`, `:705`. |
| 4 | Work-order design / role separation | The harness prompts embed asset-specific modeling decisions authored outside Qwen: iris/skin/tunic hex codes, a "runic slate" prop with fixed dimensions, garment Z ranges, mandatory `ALWAYS use bt.build_stylized_head_base(...)`. This both violates "Qwen owns asset-specific decisions" and pins the face to one generic template, which caps likeness. | `TOOLKIT_API_CHEATSHEET`/`BASE_MESH_API_CHEATSHEET` (`:67-138`), Stage 3/4 prompts (`:1349-1375`, `:1624`, `:1735`). |
| 5 | Role separation | The design specification (Stage 1) is authored by the *supervisor* model, not by Qwen. | `:1051-1057` (`self.model_roles["supervisor"]`). |
| 6 | Modeling strategy | There is no gated silhouette phase: the blockout is reviewed once and the result is recorded but never blocks or corrects (`blk_rev` is stored, then Stage 3 starts unconditionally). Local fixes were then applied on top of wrong primary proportions. | `:1307-1318`, no gate before `:1337`. |
| 7 | QA / reference delivery | Stage reviews receive only the primary reference; `additional_references` is never passed by the harness, so face/costume/turnaround sheets never reach the modeling reviewer. | calls at `:1307`, `:1484`, `:1685` omit `additional_references`. |
| 8 | Reference delivery | Original references (up to 2700×1800 px, 4 files) are attached at full size to every producer/reviewer call; no bounded copies. | `attach_p = [ref_file]`, `files=ref_files`. |
| 9 | Checkpoint/state | `rig_validated` and `animation_validated` default to **True** before any rigging runs; for `static` mode they stay True without ever executing. | `:1113-1114`. |
| 10 | Checkpoint/state | Skill contracts record fabricated evidence: `hair-groom` gets `scalp_coverage_verified: True`, `penetration_checked: True`; `rigging` gets `weights_verified: True, bone_count: 10` with no measurement. | `:1590`, `:1946`. |
| 11 | Checkpoint/state | `production_state.json` is written by `_record_stage`, but the "best" checkpoint restored at `:1567` is not written back into state, so state and the live scene can disagree. Stage status is boolean only (no NOT_VERIFIED / NOT_APPLICABLE). | `:1137-1153`, `:1567-1579`. |
| 12 | Blender MCP execution | `StdioMCPTransport.call_rpc` has no timeout; a hung Blender script blocks the harness indefinitely. | `mcp_adapter.py:135-149`. |
| 13 | Blender MCP execution | No guard against geometry accumulation or duplicate `.001` objects at execution time (only prompt instructions). | `_exec_blender_wrapped` (`:426-517`). |
| 14 | Resource contention | Strata serves **one** slot (`/props` → `total_slots: 1`). The global OpenCode config routes the `title` and `compaction` agents to the same Qwen model, and the isolated harness config copies that, so any hidden title/compaction request queues behind (or in front of) production calls. | `curl :8080/props`; `~/.config/opencode/opencode.json` `agents.title/compaction`; `build_isolated_opencode_env` copies the config. |
| 15 | Session/context management | The reported ~99 compaction attempts cannot be attributed to a specific session from the surviving evidence. Legacy producer calls already start fresh, but use the global configuration's compaction agent, and the outer session can also compete for that same slot. | compaction model in global config; `_run_code_agent_with_retry` uses `session_id=None`. |
| 16 | Model capability | Not the primary blocker. Measured: Qwen decodes images both at the Strata endpoint and through the harness's `opencode run --file` transport (synthetic probe with random text and shapes answered exactly). Fixed overhead is ~12k input tokens per call. Code-generation quality limits remain and are measured in the live run. | probes in this session. |

## Test-suite baseline (fresh, not assumed)

Full suite on the working tree: **103 passed, 8 failed** (previous report said 105/6).

| Failing tests | Cause | Classification |
|---|---|---|
| `test_unit_lifecycle` GPU policy ×2 | Fail only when `BLENDER_RENDER_DEVICE=CPU` is exported; pass (17/17) under default config, as `docs/autonomous-production-recovery.md` prescribes. | Environment, not code. |
| `test_zelda_regression::test_02` | Fixture needs untracked `images.jpg`, which no longer exists. Also fails on clean `46fa59a` (verified in a separate worktree). | Pre-existing, proven. Left unchanged; today's references cannot substitute for missing historical evidence. |
| `test_integration_qa` ×5 | Four failures count a pre-existing Blender PID in host-wide checks; one reports `IDLE` rather than expected `STOPPED`. Suite also reloads the shared OpenCode service. | Reproduced before edits; PID failures are host-contention dependent. Lifecycle status mismatch remains unresolved. Not re-run after edits. |
