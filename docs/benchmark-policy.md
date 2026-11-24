# Blender Benchmark Execution & Reference Policy

This document summarizes the resolved policy in [.opencode/AGENTS.md](../../.opencode/AGENTS.md) and the preflight/gate validator in [scripts/benchmark_preflight.py](../../scripts/benchmark_preflight.py).

## Why This Policy Exists

In earlier Princess Zelda text-only benchmark runs, GPT-6.2 Sol halted before modeling while Qwen3.8 proceeded under the same prompt.

The root cause was an instruction conflict in `.opencode/AGENTS.md`:
- The benchmark prompt explicitly prohibited reference images and external assets.
- `.opencode/AGENTS.md` previously imposed an unconditional rule requiring explicit approved reference images before any named character modeling and marking identity QA `BLOCKED` if references were unavailable.

That ambiguity invalidates cross-model timing and outcome comparisons.

## Instruction Precedence

All LLMs follow the same 4-tier resolution order:

1. **Explicit user benchmark requirements** determine whether reference images are permitted or required (`TEXT_ONLY` vs. `REFERENCE_GUIDED`).
2. **Project instructions** (`.opencode/AGENTS.md`) define how the selected mode is executed.
3. **Installed Blender Skills** provide stage-specific workflows in the fixed 17-step production order.
4. **Quality Gates (Gate A, Gate B, Gate C, Final Technical Validation)** remain mandatory in both modes.

No model or provider receives custom routing or policy exemptions.

## Two Production Modes

| Aspect | Mode A: `REFERENCE_GUIDED` | Mode B: `TEXT_ONLY` |
|---|---|---|
| Trigger | User supplies references, requires approved references, or requires exact visual matching | User explicitly requests creation without reference images |
| Reference images | Required & allowed (approved only) | Prohibited (must not search, download, generate, or require) |
| Primary comparison target | Approved references + textual spec | Supplied textual character specification |
| `reference-image-match` skill | Required | Not loaded |
| Missing reference behavior | `BLOCKED` (stops modeling/identity approval; never fabricate scores) | Modeling proceeds without asking additional permission |
| Exact original-character likeness | Verified via independent reference-vs-render comparison | Reported separately as `NOT_VERIFIED` if unverifiable; never auto-`PASS` |
| Gate A -> Gate B -> Gate C | Both modeling quality & reference identity QA must `PASS` | Gate A modeling quality `PASS` unlocks Gate B even though exact original likeness is `NOT_VERIFIED` |

## Dynamic Preflight Validation

Before modeling begins, [scripts/benchmark_preflight.py](../../scripts/benchmark_preflight.py) dynamically inspects `.opencode/AGENTS.md` and the benchmark prompt to produce the preflight JSON stored in run metadata:

```bash
python3 scripts/benchmark_preflight.py \
  --prompt-file docs/benchmarks/princess-zelda-text-only.md \
  --run-id 20261124_160000_modelname \
  --model-id "<model-id>" \
  --output /tmp/preflight-meta.json
```

If an instruction file is genuinely ambiguous or introduces a legacy unconditional reference blocker, preflight surfaces the conflict instead of hardcoding `READY`.

## Benchmark Reproducibility

- Every run must start from an empty Blender scene in an isolated directory (`YYYYMMDD_HHMMSS_MODELNAME`) and never inspect or reuse other runs' scripts, renders, `.blend`, or `.glb` files.
- Uncontrolled cross-run factors (`model_id`, `provider`, `variant`, `reasoning_effort`, `inference_backend`, `hardware_allocation`, `runtime_environment`, duration, tool errors, compactions, QA statuses) must be recorded in metadata. Strict experimental equivalence must never be claimed when these differ.
