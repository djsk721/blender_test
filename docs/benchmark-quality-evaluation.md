# Benchmark Quality Evaluation & Cross-Model Parity (`benchmark_preflight.py` & `pipeline.py`)

## 1. Fair Cross-Model Environment & Isolation

To evaluate different LLMs (e.g., GPT and Qwen variants) under identical conditions without cross-contamination:

1. **Isolated Workspaces (`YYYYMMDD_HHMMSS_MODELNAME`)**:
   - `validate_benchmark_run_id()` enforces the `YYYYMMDD_HHMMSS_MODELNAME` naming pattern.
   - `create_isolated_benchmark_workspace()` provisions isolated `run_dir`, `mcp_runtime`, `renders`, and `checkpoints` directories.
2. **Strict Isolation Guard (`BenchmarkIsolationGuard`)**:
   - `INDEPENDENT_BENCHMARK` runs raise `BenchmarkIsolationError` if any path under `output/zelda_reference/` or a sibling model run directory is accessed.
   - `REGRESSION_REPLAY` is explicitly separated for validating known historical failures against `output/zelda_reference/`.
3. **Skills Presence Preflight (`validate_required_skills`)**:
   - Because `.opencode/skills/` is excluded by `.gitignore`, `validate_required_skills()` verifies at preflight that `.opencode/skills/`, shared `references/`, and all 15 required core `SKILL.md` files exist on disk. Missing skills return `status="SKILLS_MISSING"` with explicit `./scripts/install.sh --skills-source ...` remediation steps rather than silently claiming application.

---

## 2. Recorded Benchmark Metadata (`build_benchmark_metadata` & `build_reproducibility_manifest`)

Every run records:
- `model_id`, `provider`, `variant`, `reasoning_effort` / `reasoning_settings`
- `inference_backend`, `hardware_allocation`, `runtime_environment`
- `prompt_sha256`, `agents_md_sha256`, `skills_revision_hash`, `skills_version`, `skills_preflight`
- `blender_version`, `mcp_server_version`, `mcp_lifecycle_version`, `mcp_config_sha256`
- `reference_assets_sha256`
- `repair_policy` (`max_fix_iterations`, `max_wall_time_sec`, `min_reference_match_ratio`)
- Runtime telemetry (`PerformanceMetrics`):
  - `skill_activations`, `skill_applications`, `skill_call_count`
  - `mcp_calls_by_operation`, `mcp_tool_call_count`, `python_execution_count`
  - `blender_error_count`, `retry_count`, `compaction_events`
  - `modeling_iteration_count`, `qa_failure_count`, `qa_fix_history`
  - `stage_durations_sec`, `render_durations_sec`, `total_runtime_sec`
  - `final_quality_summary`

---

## 3. Separated Comparison Metrics & Fairness Gate (`compare_run_environments`)

`compare_run_environments(meta_a, meta_b)` separates comparison output into:
- **Quality**: `gate_a`, `gate_b`, `gate_c`, `final_quality_summary`, `qa_failure_count`, `qa_fix_history`
- **Runtime**: `total_runtime_sec`, `stage_durations_sec`, `compaction_events`
- **Tool-Call Efficiency**: `skill_call_count`, `mcp_tool_call_count`, `python_execution_count`, `blender_error_count`, `retry_count`, `modeling_iteration_count`

And enforces a **fairness disqualification check** (`fair_direct_comparison_allowed`):
- Automatically sets `fair_direct_comparison_allowed = False` with explicit `disqualification_reasons` if `prompt_sha256`, `reference_assets_sha256`, `preflight`, or `agents_md_sha256` / `skills_revision_hash` differ between runs.
- Preserves `strict_experimental_equivalence_claimable = False` whenever hardware, backend, reasoning effort, or runtime environments differ.
