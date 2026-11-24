## Blender Skill Orchestration

For every Blender production task:

1. Load blender-director using the native skill tool.
2. Identify all relevant specialist skills.
3. Load each required SKILL.md completely.
4. Read referenced documentation when required.
5. Never rely only on the first 45 lines of a Skill.
6. Apply the actual workflows, not just their descriptions.
7. Verify that Blender MCP operations succeeded.

## Character Production

Use relevant skills in this order:

- blender-director
- character-archetypes
- stylized-style
- character-artist
- sculpting
- retopology
- hair-groom
- uv-workflow
- texture-workflow
- materials
- qa-review
- rigging
- qa-review
- animation
- qa-review
- export-pipeline
- godot-export

Load supporting references on demand.

## Instruction Precedence

Apply this precedence order consistently regardless of model or provider:

1. Explicit user-provided benchmark requirements determine whether reference images are permitted or required.
2. Project instructions in `.opencode/AGENTS.md` define how to execute the selected benchmark mode.
3. Blender Skills provide stage-specific production workflows.
4. Quality gates remain mandatory in both modes.

Resolution rules:

- If the benchmark explicitly specifies text-only creation (for example, `"Do not use reference images"` or `Benchmark Mode: TEXT_ONLY`), select `TEXT_ONLY` mode and proceed without stopping to ask the user for references.
- If the benchmark supplies visual references, requires approved references, or mandates exact visual matching, select `REFERENCE_GUIDED` mode.
- If the reference policy is genuinely ambiguous, explain the uncertainty rather than inventing requirements.
- Never use this precedence policy to bypass safety constraints or unrelated mandatory project requirements.

## Reference Policy

### Mode A: Reference-Guided Production (`REFERENCE_GUIDED`)

Activate when:

- The user supplies reference images.
- The benchmark explicitly requires visual references.
- Exact visual matching is a mandatory acceptance criterion.

Requirements:

1. Use only approved reference images.
2. Preserve image provenance and benchmark isolation.
3. Apply the `reference-image-match` workflow.
4. Compare modeling renders directly against the approved references.
5. Block identity approval (`BLOCKED`) and stop reference-dependent production if required references are missing.
6. Never fabricate reference-based identity scores or claim unverified visual fidelity.

### Mode B: Text-Only Production (`TEXT_ONLY`)

Activate when:

- The user or benchmark prompt explicitly requests creation without reference images.

Requirements:

1. Use the supplied textual identity specification as the authorized production reference.
2. Do not search, download, generate, or require reference images.
3. Do not reuse previous models, scripts, renders, or generated assets.
4. Proceed with Blender modeling without requesting additional authorization.
5. Evaluate visual modeling quality against the textual specification.
6. Separate text-specification compliance from verified fidelity to the original character.
7. Mark exact original-character likeness as `NOT_VERIFIED` when it cannot be independently established against approved references.
8. Do not automatically fail or block the modeling pipeline merely because reference images are absent.
9. Preserve all technical and visual quality gates.

**Important distinction:**

- `Identity fidelity = NOT_VERIFIED` does not automatically imply `Modeling quality = FAIL`.
- Serious visible modeling defects must still produce `FAIL` in Gate A and block progression.

## Benchmark Preflight Validation

Before modeling begins, dynamically derive the benchmark policy from `.opencode/AGENTS.md` and the benchmark prompt (never hardcode a fixed result across runs) and record it in the benchmark metadata:

```json
{
  "benchmark_mode": "TEXT_ONLY | REFERENCE_GUIDED",
  "reference_images_required": false,
  "reference_images_allowed": false,
  "text_specification_available": true,
  "modeling_permitted": true,
  "exact_identity_fidelity_verifiable": false,
  "modeling_quality_gate_required": true,
  "independent_visual_review_required": true
}
```

- Run `scripts/benchmark_preflight.py` when executing automated or audited benchmark preflight checks.
- When `TEXT_ONLY` mode is explicitly requested, do not emit `BLOCKED` solely due to missing reference images.
- When `REFERENCE_GUIDED` mode lacks mandatory approved references, block modeling/identity approval (`BLOCKED`) and report the missing-reference blocker.

## Mandatory Quality Gates

All quality gates remain mandatory in both `REFERENCE_GUIDED` and `TEXT_ONLY` modes:

### Gate A — Modeling Quality

Evaluate across actual rendered images:

- Silhouette
- Facial proportions
- Eyes, nose, mouth, and ears
- Hair construction
- Anatomy
- Costume
- Materials
- Character readability
- Compliance with the textual specification (plus approved-reference visual match in `REFERENCE_GUIDED` mode)

Allowed Gate A statuses: `PASS`, `FAIL`, `NOT_VERIFIED`.

- Finish modeling and inspect rendered images before starting rigging.
- Fix major facial, anatomy, hair, costume, and silhouette defects before advancing.
- Do not proceed to Gate B if Gate A modeling quality is `FAIL` or `NOT_VERIFIED`.
- In `TEXT_ONLY` mode, report exact original-character resemblance separately (`NOT_VERIFIED` when unverifiable); `exact_identity_fidelity = NOT_VERIFIED` does not block Gate B when Gate A modeling quality passes.
- In `REFERENCE_GUIDED` mode, both Gate A modeling quality and approved-reference identity QA must pass before rigging begins.

### Gate B — Rigging and Deformation

Proceed only when Gate A passes. Check:

- Bone hierarchy
- Weight assignments
- Joint deformation
- Clothing clipping
- Hair deformation
- Mesh continuity

Validate joint deformation across poses before starting animation.

### Gate C — Animation

Proceed only when Gate B passes. Check:

- Running cycle
- Foot contact
- Root stability
- Arm swing
- Secondary motion
- Loop continuity

Inspect animation frames and full playback; fix foot sliding, clipping, and unnatural motion.

### Final Technical Validation

Require:

- Editable Blender file (`.blend`)
- GLB export (`.glb`)
- Skeleton validation
- Animation validation
- GLB reimport verification
- Godot compatibility verification where available

General gate rules:

- Never equate successful script execution with visual quality approval.
- Preserve evidence (renders, reviewer reports, validation logs) and report actual QA outcomes.

If the native skill tool fails:
- Diagnose skill discovery, permissions and ID conflicts.
- Do not silently skip required instructions.
- Reading the complete SKILL.md is a temporary fallback.
- Report that native Skill loading did not succeed.

## Character Identity and Reference Lock

For any named character task:

1. Determine the benchmark mode (`REFERENCE_GUIDED` or `TEXT_ONLY`) using the Instruction Precedence and Reference Policy sections before starting.
2. Identify the exact character version, costume, and traits specified by the prompt.
3. Write a structured character identity specification covering:
   - face shape and proportions
   - eyes, brows, nose, and mouth
   - hairstyle and hairline
   - body proportions and silhouette
   - outfit structure and colors
   - distinctive accessories and symbols
4. Do not substitute generic fantasy features for the specified character identity traits.
5. In `REFERENCE_GUIDED` mode:
   - Require explicit approved reference images before modeling.
   - Use the exact same approved references across all benchmark models.
   - Load `reference-image-match` and relevant visual references.
   - If required approved references are missing, mark identity QA and modeling `BLOCKED` and do not fabricate visual fidelity scores.
6. In `TEXT_ONLY` mode:
   - Do not require, search, download, generate, or load visual reference images.
   - Use the textual specification as the authorized production reference and proceed directly to modeling.
   - Record exact original-character likeness separately as `NOT_VERIFIED` unless independently verifiable against approved references.

## Modeling Quality Enforcement

- Validate silhouette, proportions, and specified facial structure at blockout.
- Inspect front, side, back, and 3/4 renders.
- Compare renders directly against approved references in `REFERENCE_GUIDED` mode, or against the textual identity specification in `TEXT_ONLY` mode.
- Reject primitive-looking, blocky, or anatomically broken models.
- Do not skip a required modeling process merely because a Python procedural mesh was successfully generated.
- Keep modeling QA separate from technical mesh validation.
- Do not begin rigging while Gate A modeling quality is `FAIL` or `NOT_VERIFIED`, or (in `REFERENCE_GUIDED` mode) while required identity QA is `FAIL` or `BLOCKED`.

## Independent Visual QA

- The implementation agent must not approve its own final visual quality as the sole reviewer.
- Use an independent visual reviewer capable of inspecting actual rendered images.
- Evaluate modeling quality (silhouette, face, hair, anatomy, costume, materials, text-spec compliance), exact original-character identity fidelity, deformation, and animation separately.
- If rendered visual evidence or independent review is unavailable, mark modeling QA as `NOT_VERIFIED` (never `PASS`).
- Major visual defects must result in `FAIL`.
- In `TEXT_ONLY` mode, never grant an automatic `PASS` for exact original-character likeness, and never fail modeling quality solely because reference images were prohibited.
- A passing export or valid animation does not override failed modeling or character identity QA.
- Preserve the reviewer's findings together with reference-versus-render comparisons (`REFERENCE_GUIDED`) or text-specification-versus-render comparisons (`TEXT_ONLY`).

## Benchmark Isolation

- Every model must receive the identical user benchmark prompt, `.opencode/AGENTS.md` instructions, installed Blender Skills, Blender MCP capabilities, scene initialization rules, asset restrictions, quality gates, artifact naming requirements (`YYYYMMDD_HHMMSS_MODELNAME`), and rendering/export specifications.
- No model or provider receives special policy exemptions or custom routing.
- Start each run in a clean Blender scene and isolated workspace (`YYYYMMDD_HHMMSS_MODELNAME`).
- Never read, import, inspect, or refine another run's reports, images, scripts, `.blend` files, or exported models.
- Store the dynamically resolved preflight policy JSON in the benchmark metadata.
- Record all run parameters and factors that cannot be fully controlled across runs:
  - model ID and variant
  - provider and inference backend
  - reasoning effort
  - hardware allocation and runtime environment
  - run duration, tool errors, compactions, and final QA statuses
- Never claim strict experimental equivalence if any of those uncontrollable factors differ across compared runs.
- Report protocol violations (such as cross-run contamination or unauthorized external/reference image lookup in `TEXT_ONLY` mode) as `INVALID_BENCHMARK`.

## Skill Execution Policy

- Load only the skills relevant to the current stage.
- Read the required skill and supporting references completely before executing that stage.
- Load `reference-image-match` only in `REFERENCE_GUIDED` mode when approved reference images are supplied.
- Do not preload all skills at the start of a run.
- Record actual execution evidence, not just successful skill-loading calls.
- If a required skill workflow is skipped, explain why.
- A skill load is not evidence of production quality.
