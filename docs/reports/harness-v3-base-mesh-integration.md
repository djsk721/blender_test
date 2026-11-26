# Harness V3: verified human base mesh integration

## Architecture

V3 previously hard-coded `FROM_SCRATCH` in its CLI and production identity. The existing stage ledger, correction controller, scene executor, checkpoint registry and independent visual reviewer remain in place. `BASE_MESH` adds a verified source identity, exact-object inspection, a source-bound bridge to the legacy loader, and before/after topology evidence. It does not add an alternate generator or an alternate gate. Qwen (`strata/sc117-qwen38-abliterated-iq3_s`) is still the only production script author; GPT (`openai/gpt-6.2-sol` for this trial) receives the renders and references and returns findings, not executable scripts. No Claude or reviewer fallback was invoked.

Silhouette/modeling/materials/rigging/final thresholds remain **75/80/86/65/85**. A valid import does not approve a gate. A failed render review cannot advance to later stages or publish an export.

## Source and license

- Manifest: `assets/approved_base_meshes/blender_human_base_meshes/provenance.json`.
- Publisher: Blender Foundation / Blender Studio; Human Base Meshes Bundle 1.0.0; recorded license `CC0-1.0`.
- [Official distribution](https://download.blender.org/demo/bundles/bundles-3.6/human-base-meshes-bundle-v1.0.0.zip): archive SHA-256 `46a912c0524072ac3b78c35d5d2471df7b8df102394a050ca8cd7184e3393648`, verified against both local bytes and a streamed copy from the publisher's server.
- Library SHA-256 `660e245812ef56768bc71293bd4c8bc1fd19a63710e16aafc8258a7351cbc35e`, verified against the manifest, the installed library, and the ZIP member. Archive size is 34,586,814 bytes. The library is 62,172,376 bytes; its `bytes` field records 62,506,480. This stale size was surfaced in preflight metadata; the digest and publisher archive agree. The tracked approval manifest was left unchanged.
- The manifest identifies the [Blender demo-files page](https://www.blender.org/download/demo-files/). Direct access to that page returned 403 in this session. The license check is against the recorded approval metadata, not an independent legal certification.

## Blender inventory and selection

Blender 5.3.0 enumerated 52 objects: 35 meshes and 17 preview cameras. The full inventory, including exact names, updated world bounds, UVs, modifiers, materials, parent/child relationships, armatures and topology, is in `output/zelda-v3-base-inventory-20261126-182451/inventory.json`. Full-body candidates:

| Blender mesh | Vertices | Faces | Quad share | Extents X/Y/Z, m |
|---|---:|---:|---:|---|
| `GEO-body_female_stylized` | 12,502 | 12,500 | 100% | 0.7209 / 0.2732 / 1.6169 |
| `GEO-body_male_stylized` | 12,502 | 12,500 | 100% | 0.8793 / 0.3084 / 1.7980 |
| `GEO-body_female_realistic` | 10,582 | 10,590 | 99.81% | 0.8422 / 0.2826 / 1.6393 |
| `GEO-body_male_realistic` | 15,093 | 15,043 | 99.95% | 0.9074 / 0.3290 / 1.8003 |

Other meshes are companion eyes and separate eye, head, hand, foot, jaw and skull assets. The stylized bodies are suitable for stylized humanoids, but the reference determines the choice and target proportions. The selected object is `GEO-body_female_stylized`, not the `stylized_body_female` camera. It has an Auto Smooth geometry nodes modifier, no assigned source materials and companion `.eye.L` and `.eye.R` child meshes. It is one connected component with zero boundary, non-manifold or inconsistent-winding edges and zero degenerate faces in the raw source. It has no armature or parent; this is an anatomical start, not a rigged Zelda.

## Reproduction

Run from the repository root. Choose unused ports and keep the two arms sequential. Do not resume or overwrite `output/zelda-v3-gpt-20261126-173844/` (historical diagnostic silhouette 44/100).

```bash
stamp=$(date +%Y%m%d-%H%M%S)
goal=$(cat docs/zelda-production-goal.txt; printf x)
common=(--prompt "${goal%x}" --reference-manifest output/v3-inputs/zelda.json
        --asset-name Zelda --animation-mode animated --reviewer-model-id openai/gpt-6.2-sol
        --max-stage-corrections 3 --stage-timeout 360 --samples 8)
python -u scripts/run_harness_v3.py "${common[@]}" --geometry-strategy FROM_SCRATCH \
  --blender-port 19880 --run-dir "output/zelda-v3-from_scratch-$stamp"
python -u scripts/run_harness_v3.py "${common[@]}" --geometry-strategy BASE_MESH \
  --base-mesh-manifest assets/approved_base_meshes/blender_human_base_meshes/provenance.json \
  --base-mesh-name GEO-body_female_stylized --blender-port 19881 --run-dir "output/zelda-v3-base_mesh-$stamp"
```

Both runs use the CLI's CPU rendering and software viewport, four identical hash-verified reference inputs and independent sessions. The render evidence is kept beside its review and checkpoint, never substituted with the historical scores.

## Software and live checks

- Focused V3 and lifecycle tests including the opt-in isolated live MCP test: **35 passed**. Blender returned the exact selected body's expected polygon and vertex counts, rejected a prefix and a preview camera, and left the production scene empty.
- The unrestricted suite was started, then interrupted on finding that the shared-host lifecycle module can reload the shared OpenCode server and assumes exclusive host Blender ownership. At interruption: 34 passed, two failed. `test_01_b01_to_b05_opencode_and_lazy_init` expected no host Blender PIDs and found two; `test_02_b06_to_b12_launch_reuse_concurrency_idle_gpu_relaunch` expected one and found two. See `output/zelda-v3-integration-tests-20261126-182209.log`. These are the same host-wide assumptions recorded in the previous V3 audit. No assertion was changed. `test_unit_lifecycle.py` was also run separately without forced CPU test overrides (17 passed).
- Remaining compatible suite: 100 passed, one skipped, one failed (`output/zelda-v3-safe-tests-final-20261126.log`). The unchanged historical `TestZeldaReferenceRegression.test_02_zelda_verified_evidence_fails_gate_a_and_blocks_downstream` failed because its evidence does not include a valid observation compared against a hashed approved reference. The previous V3 audit traces this to the missing historical `images.jpg`. The legacy live base-mesh MCP test passed in this suite; it is not evidence that V3's Qwen producer transformed Zelda.

## Controlled production evidence

- **A: `output/zelda-v3-from_scratch-20261126-182209/` (port 19880).** Qwen analyzed the four references and planned the asset. It made two MCP edit attempts on the initial silhouette. The first passed an unsupported `color` keyword to `make_pbr_material`; the retry passed a removed Blender object to `join_objects` and raised `ReferenceError: StructRNA of type Object has been removed`. Both exact responses/scripts and MCP errors are preserved under `candidates/silhouette/00/`. No candidate was saved, no renders existed for GPT, and no visual correction or downstream gate ran. No checkpoint is available for A.
- **B: `output/zelda-v3-base_mesh-20261126-182209/` (port 19881).** Started only after A exited. Source and exact Blender object preflight succeeded. Qwen's reference analysis mentioned all four references but closed the `authority` object with `]`; the invalid JSON blocked planning. There were no geometry edits, renders, visual corrections or checkpoints in this arm.

Both arms have a bounded reference-analysis retry on the current revision: the producer must return valid JSON containing each exact image filename once, and each attempt still receives all four images. The malformed original response remains on disk. The follow-up pair uses the same command above with fresh ports (19883 for B, 19882 for A) and timestamp `20261126-183001`; B runs first, then A. Do not combine candidates from different revisions into one purported controlled score comparison.

**Follow-up BASE_MESH:** planning passed. `candidates/silhouette/00/producer_1/mcp.json` records a syntax rejection (`return` outside a function). In `producer_2/asset.py`, line 4 called the pinned loader successfully, line 11 deleted the returned `SM_BaseBody` via the cleanup list, and line 14 dereferenced the removed object. Blender MCP returned `ReferenceError: StructRNA of type Object has been removed`. No transformation survived, no candidate was checkpointed, and no review was possible. The work order now explicitly excludes the base from deletion and join lists and clarifies that companion eyes are returned as names.

**Follow-up FROM_SCRATCH:** `output/zelda-v3-from_scratch-20261126-183001/` blocked before a checkpoint. Validation rejected dynamic `__import__` on attempt one. Blender rejected the unsupported `color` keyword to `make_pbr_material` on attempt two.

**Targeted BASE_MESH diagnostic:** `output/zelda-v3-base-diagnostic-20261126-183956/` (port 19884) also blocked before a checkpoint. On attempt two the recorded `base_mesh_attempt.loaded` is `true`; the script then failed at `vg.vertices` with `AttributeError: 'VertexGroup' object has no attribute 'vertices'`. This was an API usage failure, not a bad source hash or missing mesh. It is not an A/B arm.

The subsequent controlled pair uses a common upper bound of four technical attempts per edit (separate from the unchanged budget of three visual corrections). Each retry gets the exact prior source plus the accumulated errors, rather than discarding the script when a syntax error could be fixed locally. The producer prompt spells out the exact loader return keys, vertex-group membership API, and material argument names. No supervisor-authored correction script is executed.

On that revision the focused suite, including isolated live MCP, passed **36 tests**. The compatible suite passed **101 tests** with one opt-in skip and the same historical evidence failure, recorded in `output/zelda-v3-safe-tests-retries-20261126.log`. The new sequential pair uses `output/zelda-v3-controlled-base_mesh-20261126-191851/` (19885) and `output/zelda-v3-controlled-from_scratch-20261126-191851/` (19886), with the common arguments above and no historical checkpoints.

| Evaluation, completed original pair | FROM_SCRATCH | BASE_MESH |
|---|---|---|
| Silhouette, anatomy, head/body ratio, shoulder/torso and limb proportions | Not measured | Not measured |
| Costume integration, reference similarity and transformed mesh quality | Not measured | Not measured |
| Blender execution | Two asset script failures via MCP | Preflight passed; blocked before asset execution |
| Visual corrections | 0 (one technical retry) | 0 |
| Best reviewed checkpoint | None | None |

GPU/RAM boundary snapshots are in sibling `*.resources.log` files; isolated MCP state/call traces and OpenCode token/compaction counts are in the run artifacts. No resource failure was the reported cause of either completed original arm. Neither arm justified downstream production or confirmed a rigged, animated `.blend`/`.glb` release. The new release checks compare source and fresh reimport for mesh, scale, materials, textures, skin/armature and expected animation clips; their rejection logic is unit-tested but they have not been exercised on a releasable Zelda asset.

**Recommendation:** Keep `FROM_SCRATCH` as the default. Treat `BASE_MESH` as ready for further commissioning, not as an established improvement. The observed BASE_MESH failures are planning/producer-script reliability failures; they do not support a judgment that the selected topology or stylization strategy is visually better or worse. Continue from fresh run directories and require reviewed four-view renders from both arms before drawing a quality conclusion.
