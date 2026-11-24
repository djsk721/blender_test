# Princess Zelda Text-Only Character Production Benchmark

```text
Benchmark Mode: TEXT_ONLY

This benchmark intentionally prohibits reference images.

The supplied textual character specification is the
authorized production reference.

Missing images must not block modeling.

Exact fidelity to the original character must be
reported separately if it cannot be verified.

All modeling, rigging, animation, and technical quality
gates remain mandatory.

Do not stop after pre-production planning merely
because reference images are unavailable.
```

## Objective

Create a stylized, production-ready, game-compatible 3D character model of Princess Zelda in Blender using Blender MCP and the project's Blender Skills, starting from an empty scene and using only the textual specification below.

## Asset & Isolation Constraints

- **No reference images**: Do not search, download, generate, or require any reference images.
- **No external premade assets**: Build original geometry, materials, rig, and animations inside Blender without downloading external meshes, rigs, or animations.
- **Strict run isolation**: Start from a clean Blender scene in an isolated workspace named `YYYYMMDD_HHMMSS_MODELNAME`. Never read, import, inspect, or reuse files from previous GPT, Qwen, or other benchmark runs.
- **Mandatory workflow order**: Follow the 17-step production order in `.opencode/AGENTS.md` (`blender-director` through `godot-export`), loading only stage-relevant skills as needed.

## Textual Character Specification (Authorized Production Reference)

- **Identity & Archetype**: Princess Zelda — poised royal Hylian adventurer and magic user with youthful regal proportions (approximately 1.65 m tall, slender athletic royal build, ~7 to 7.25 heads tall in a clean stylized proportion).
- **Face & Ears**: Heart-shaped royal face, clear brow ridge, large expressive blue-emerald eyes with crisp iris/highlight detail, delicate straight nose, balanced gentle lips, and iconic elongated pointed Hylian ears angled naturally backward.
- **Hair & Crown**: Long golden-blonde hair parted cleanly at the forehead with face-framing side locks (braided or wrapped), flowing back hair strands grouped into sculptural hair clumps, and an ornate golden circlet/tiara featuring a royal crest jewel at the center of the forehead.
- **Costume & Silhouette**:
  - Fitted royal bodice/tunic in ivory/cream and deep royal blue with gold filigree trim.
  - High collar / regal shoulder mantle or pauldrons in deep blue and gold.
  - Ornate golden waist belt/sash with royal crest buckle and layered flowing front/side/back skirt panels split cleanly for leg articulation.
  - Fitted sleeves with gold cuffs and royal gauntlets/bracers, plus knee-high leather/armored boots with subtle heel/sole definition.
  - Royal crest emblem (winged motif / Triforce-inspired triangle crest) cleanly sculpted or textured on torso, belt, and tiara.
- **Materials & Lookdev**: Distinct stylized PBR material separation (skin with subtle subsurface/roughness variation, multi-layered hair sheen, satin/woven cloth, brushed/polished gold trim, matte leather boots, emissive-free crisp eye shaders).

## Rigging & Animation Requirements

- Clean humanoid armature rooted cleanly at origin (`+Y` or consistent forward axis documented for export), symmetric `.L` / `.R` bone naming, clean parent-child hierarchy, painted/verified skin weights on face, hair clumps, skirt panels, torso, and limbs.
- Include a clean looping **Run Cycle** animation (`Action` properly assigned and baked cleanly for GLB export) demonstrating natural stride, clear foot contact without ground penetration or skating, balanced arm swing, stable root motion, and controlled secondary motion on hair and skirt panels.

## Mandatory Quality Gates & Reporting

All quality gates in `.opencode/AGENTS.md` remain mandatory:

1. **Preflight Metadata**: Resolve and store the preflight policy JSON (`benchmark_mode: "TEXT_ONLY"`, `modeling_permitted: true`) and runtime environment metadata (`model_id`, `provider`, `variant`, `reasoning_effort`, `inference_backend`, `hardware_allocation`, `runtime_environment`) before modeling.
2. **Gate A — Modeling Quality**: Render front, side, back, and 3/4 views. Have an independent visual reviewer inspect actual renders against silhouette, facial proportions, eyes/nose/mouth/ears, hair construction, anatomy, costume, materials, character readability, and this textual specification. Report exact original-character likeness separately as `NOT_VERIFIED` without blocking Gate B when Gate A modeling quality is `PASS`. Serious visible defects must produce `FAIL`.
3. **Gate B — Rigging and Deformation**: Proceed only after Gate A modeling quality passes. Verify bone hierarchy, weights, joint deformation, clothing clipping, hair deformation, and mesh continuity via rendered poses.
4. **Gate C — Animation**: Proceed only after Gate B passes. Inspect rendered frames and full playback for running cycle, foot contact, root stability, arm swing, secondary motion, and loop continuity.
5. **Final Technical Validation & Outputs**: Save editable `.blend`, export `.glb`, validate skeleton and animation, reimport `.glb` into a fresh Blender scene to verify integrity, and run Godot compatibility checks where available.
