# Harness V3: GPT-6.2 reviewer validation

- Baseline: `752594a`, `feat/harness-v3-qwen-only`. The Claude-era `output/zelda-v3-20261126-164252/` is diagnostic only and was neither edited nor resumed. Claude was not retried.
- Producer: `strata/sc117-qwen38-abliterated-iq3_s`. Reviewer: `openai/gpt-6.2-sol`. Both exact IDs were listed by `opencode models` and verified by the isolated CLI registry check. No model fallback.
- Vision preflight: the production `invoke_isolated` path attached `output/v3-gpt-preflight/shapes.png`; GPT returned purple triangle (left) and orange circle (right), with exit 0 and no provider errors. Response: `output/v3-gpt-preflight/openai-gpt-6.2-sol.json`.
- `output/v3-inputs/zelda.json` was revalidated against all four source image SHA-256 values. Production uses the exact UTF-8 contents of `docs/zelda-production-goal.txt` (including its final newline).
- Focused V3 and reference tests: 22 passed. Isolated-safe suite: 104 passed. The host-wide lifecycle failures and missing historical Zelda reference are documented in [the baseline audit](../harness-v3-root-cause-analysis.md); neither module was counted in the isolated-safe result.

## Live runs

| Run (under `output/`) | MCP port | Result |
|---|---:|---|
| `zelda-v3-gpt-20261126-172318` | 19989 | Qwen analyzed the references and planned the asset. Both silhouette script attempts were rejected before dispatch: unclosed parenthesis on line 327, then unterminated string on line 352. No candidate or review, no resume. |
| `zelda-v3-gpt-20261126-173259` | 19990 | Shorter code compiled, but both MCP attempts failed on inconsistent tuple unpacking/indexing. No candidate or review. |
| `zelda-v3-gpt-20261126-173844` | 19991 | Named-field guidance replaced positional tuple advice. Qwen produced four real MCP candidates. GPT reviewed each: **32 → 41 → 44 → 43**. All failed the 75 silhouette gate. Three corrections used, exit 1. |

All runs used tmux, animated mode, three possible visual corrections, CPU rendering, a software viewport, and `REFERENCE_GUIDED` / `FROM_SCRATCH`. Thresholds were not changed.

## Evidence and blocker

Paths below are relative to `output/zelda-v3-gpt-20261126-173844/`.

- `candidates/silhouette/{00,01,02,03}/review.json`: four independent GPT reviews with observations and correction criteria. Verified SHA-256 of all four source images and bounded copies, all sixteen input renders, and every recorded review attachment. Each review had the full four-reference set.
- `candidates/silhouette/*/producer_*/{asset.py,response.json,mcp.json}` and `provenance.jsonl`: verified each recorded script hash against its saved source, with the exact Qwen producer ID. Successful executions: `00/producer_2`, `01/producer_2`, `02/producer_2`, and `03/producer_1`. Failed attempts were retained. Python validation now compiles the AST to reject an out-of-function `return` before dispatch.
- `checkpoints/003_silhouette_candidate_02.blend`: best reviewed diagnostic candidate, **44/100**, SHA-256 `37dc1534aab807277bae6310fd0202334f0d789b64ffc9ba573b4613f25708f4`. `candidates/silhouette/02/silhouette_body_sheet.png` provides the front, three-quarter, side, and rear comparison. Candidate 03 was not promoted after regressing to 43. All four checkpoint hashes matched the registry.
- The best candidate still had a small head envelope and spike-like ears, low knees, a straight bodice with bulky attachments instead of a wrapped hem, tubular arms, discontinuous boots, and a stiff hair mass that bowed away from the back. These are structural problems, not material problems.
- `iteration_report.json`: **BLOCKED** at silhouette; no stage approval, no authorized resume pointer, no final `.blend` or `.glb`, and no rig, idle/walk/run or GLB round-trip verification. The silhouette scores are not a comparison against the historical detailed-modeling scores.

The blocker is no longer reviewer availability. With the three-correction budget exhausted, another attempt requires a fresh run with a different Qwen blockout strategy (lock the turnaround's joint heights and head/body ratios before building coordinated garment, limb, and boot forms). Do not resume either diagnostic run or bypass the silhouette gate.
