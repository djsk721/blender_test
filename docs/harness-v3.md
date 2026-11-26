# V3: reference-guided character production

V3 is a separate CLI for from-scratch character production. It leaves the legacy harness and its workflows available. The producer is locked to `strata/sc117-qwen38-abliterated-iq3_s`. The default reviewer is `openai/gpt-6.2-sol`; override it with `--reviewer-model-id` or `BLENDER_V3_REVIEWER_MODEL_ID` and independently verify vision support before launch. No automatic fallback is used. Reviewers cannot submit executable asset code. The execution path records the producer session and hashes the exact source before dispatch. Static Python checks are diagnostics and policy guardrails, **not** a sandbox for arbitrary untrusted Python.

Generate an approved manifest outside the source image directory:

```bash
mkdir -p output/v3-inputs
python3 - <<'PY'
from pathlib import Path
from blender_orchestration.reference_manifest import build_manifest_with_primary
build_manifest_with_primary(
    Path('references/zelda'), 'zelda_official_art.png',
    asset='Princess Zelda (Breath of the Wild)',
    purposes={
        'zelda_official_art.png': 'official artwork and identity anchor',
        'zelda_front_side_back.png.jpg': 'geometry authority: front, side, rear',
        'zelda_fieldwork_costume.jpg': 'fieldwork costume construction',
        'zelda_face_hair.png': 'in-game face and hair close-up',
    }, out_path=Path('output/v3-inputs/zelda.json'),
)
PY
```

Inside a dedicated tmux session, from the project root:

```bash
goal=$(cat docs/zelda-production-goal.txt; printf x)
python3 -u scripts/run_harness_v3.py --prompt "${goal%x}" \
  --run-dir output/zelda-v3-new-run --asset-name Zelda \
  --reference-manifest output/v3-inputs/zelda.json --animation-mode animated \
  --geometry-strategy FROM_SCRATCH --blender-port 19989 --max-stage-corrections 3 \
  --reviewer-model-id openai/gpt-6.2-sol
```

Use a fresh run directory and an unused port. The CLI sets CPU rendering and software viewport. Original references are snapshotted byte-for-byte; bounded attachments retain a hash link to the source. Both Qwen and the reviewer receive pixels through `opencode run --file`. Pixel support was checked with synthetic colored shapes. Provider errors, even with a zero CLI exit code, cannot approve a gate.

Qwen analyzes the references, then owns the locked design and scripts. Independent silhouette approval precedes separate face, hair and costume phases. The combined modeling gate precedes materials, which precede rigging and then animation. Legacy aggregate score cutoffs are retained (75 silhouette, 80 modeling, 86 lookdev, 65 motion, 85 final with the frozen rubric/category floors). Face, hair and costume have separate phase progress checks. Only the workflow captures, reloads, and exports candidates; no interactive production edits are required. Each review iteration saves source, execution record, scene measurements, checkpoint, and render evidence. Reviews of regressions are kept, but corrections continue from the selected candidate. A failed or unavailable gate never approves release.

For recovery, inspect `production_state.json` and `checkpoints/registry.json`. With the same goal, models, source manifest, mode, asset and budget, repeat the command with the recorded `--resume-checkpoint` path; the stage is derived from the ledger. If review was interrupted after a checkpoint and renders were recorded, the pending evidence is rehashed and reviewed rather than promoted automatically. There is no checkpoint-based resume before any candidate has been saved. Do not copy an arbitrary `.blend` into the registry.

The root causes and baseline test failures are tracked in [the V3 audit](harness-v3-root-cause-analysis.md).
