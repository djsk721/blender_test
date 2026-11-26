# V3 Zelda validation — blocked at the silhouette review

The live tmux run `output/zelda-v3-20261126-164252/` used port **19988**, CPU rendering, software viewport, `REFERENCE_GUIDED`, `FROM_SCRATCH`, and `animated` mode. Its saved `goal.txt` matched `docs/zelda-production-goal.txt` byte for byte. No pre-existing asset checkpoint was used.

| Evidence | Result |
|---|---|
| `references/reference_manifest.json` and `references/delivery.json` | Four originals validated and snapshotted. Official art is primary. The costume sheet overlaps the left half of the turnaround (0.999 luminance correlation), but both images were preserved. Qwen's saved reference-analysis and planning responses each record all four attachments. |
| `candidates/silhouette/00/producer_1/{response.json,asset.py,mcp.json}` | Qwen `strata/sc117-qwen38-abliterated-iq3_s`, session `ses_de92dfdacffeGbHomSQEK8JnVq`, authored the executed script. Source hash `47024cf7278d77e1e7d8f30bee1338717b135edd21d150674f847954431073c6`. Blender reported 18 export meshes, 26,796 evaluated triangles and 1.7047 m height. |
| `mcp_calls.json` | Five successful `execute_blender_code` calls: scene initialization, Qwen source, save, measurement, and standard rendering. No external generator or imported base mesh. |
| `checkpoints/001_silhouette_candidate_00.blend` | 490,242-byte diagnostic Blender file with SHA-256 `9cf5abcc425a6c54e9662874af12983bdd0d6be104ea03638f390f43e7d44e5d`. File hash matched the registry. Technically saved; not independently approved. |
| `candidates/silhouette/00/renders/` | `silhouette_front.png`, `silhouette_side.png`, `silhouette_rear.png`, and `silhouette_three_quarter.png`. The labeled contact sheet is one level above. |
| `candidates/silhouette/00/review.json` | `factchat-claude/claude-opus-5-5` returned HTTP 402 `insufficient_quota`. A separate `factchat-claude/claude-sonnet-5-5` availability probe on the real render also returned 402 (`claude-sonnet-availability.json`). Neither returned a verdict or a score. |
| `iteration_report.json` | `BLOCKED`, `export_approved: false`; silhouette `NOT_VERIFIED`; face through final `NOT_STARTED`. No correction, face close-up, or GLB. Earlier run `output/zelda-v3-20261126-164226/` stopped at the same provider error before modeling. |

The live run validated production transport and blockout capture, not the complete pipeline. Its state predates pending-review resume support and has no authorized resume pointer. Keep the file as diagnostic evidence; do not hand-edit state to treat it as an approved checkpoint. The pending-review recovery path is covered with fixtures, not yet live. A fresh production run with a working Claude reviewer is needed to validate real correction and downstream gates.

## Diagnostic render observations (not independent Claude QA)

These are suggested checks for the next review, not authority to advance or instructions already executed.

| Defect | Reference and view | Observation | Qwen correction and verification | Severity |
|---|---|---|---|---|
| SIL-01 · shoulders / sleeves | Turnaround, front and side | Sleeve caps form sharp lobes with abrupt pinch or seams where they meet the shoulder. | Rebuild the shoulder-to-upper-sleeve transition around the arm attachment. In front and three-quarter views, verify a continuous outer contour with no gap or pinched notch. | Major |
| SIL-02 · boots | Turnaround, side; fieldwork sheet, three-quarter | Thin forward-projecting sole silhouettes and abrupt ankle transitions do not read as the reference's blocked boot form. | Rebuild the instep, toe volume and heel as a grounded boot silhouette. In side view the upper must meet the shaft without a horizontal gap; confirm stable sole contact in front and rear. | Major |
| SIL-03 · torso / hem | Official art, three-quarter; turnaround, front | Near-cylindrical upper torso and weak waist-to-hip transition flatten the costume silhouette. | Shape the ribcage, narrow the waist and give the peplum a short outward break above the hips. Check the front and side silhouettes before adding trim. | Major |
| SIL-04 · back hair mass | Turnaround, rear and side | The back mass terminates in a straight, scalloped curtain rather than the rounded taper in the turnaround. | Rebuild the outer hair mass with a rounded taper and softer, broad strand groups. Verify the rear outline and separation from the shoulder in profile. | Major |

Score comparison with **54 → 56 → 58/100: NOT_VERIFIED**. That sequence was for detailed modeling, not silhouette. No measurable visual improvement over it can be claimed.

## Software validation

104 tests passed in the isolated-safe subset (including the new role/provenance, reference delivery, budget, stage gate, approval prerequisite and interrupted-state tests). The host-wide lifecycle and historical Zelda regression modules were excluded from this subset: see the [baseline audit](harness-v3-root-cause-analysis.md) for their separately reproduced failures. The GPU-policy tests passed without forcing CPU globally.
