# Zelda reference study — QA report

**Disposition: FAIL / NO SHIP.** This is an editable review checkpoint, not an approved reference match. Independent visual QA rated **7/10** categories Match or Close (minimum **8/10**), with a major scalp-coverage defect. OpenCode executed the entire Blender workflow; that demonstrates execution, not successful likeness.

**Mode:** `REFERENCE_GUIDED` · **Reference:** Project `images.jpg` only · **Scope:** Static posed character. See [reference analysis](reference_analysis.md) and [preflight](preflight.json).

## Visual validation

Actual Cycles PNGs were inspected, not just checked for file presence. [Comparison board](comparison.png) puts the reference, first render and latest portrait on white at the same viewing scale. Latest evidence: [hero](hero.png), [face and slate detail](detail.png), [alternate three-quarter](three_quarter.png). The first render was too pale and rope-like, with blue sleeve intersections. The second pass recolored textiles/hair, reworked cuffs and trouser ends, and simplified the hair ridges. The third pass replaced the waist overlays with a fitted embossed wrap, refined boot collars, shortened the temple lock and darkened the eyes. Three inspection passes were used; the work stops here with the unresolved defects recorded.

| Category | Independent rating | Visible finding |
|---|---|---|
| Pose and silhouette | Close | Swept hair, trailing hand, held slate and staggered feet read; shoulders and knees feel stiff. |
| Body proportions | Close | Waist and leg lengths broadly match; upper sleeves and shoulders are too bulky. |
| Face and expression | Miss | Heavy outlines, round eyes and broad forehead read doll-like; nose and cheek planes are too soft and the gaze is less focused. |
| Pointed ears | Match | Prominent ear placement and trailing point match. |
| Hair and color | Close | Braid and swept silhouette read; locks are too uniform and orange. **A skin-colored patch breaks the scalp cap behind the braid**, conspicuous in the detail and three-quarter renders. |
| Tunic and bodice | Close | Blue and ivory structure reads; sleeves are too rounded and stitched bodice detail is simplified. |
| Belt and hip accessories | Close | Belt, pouch and hip panels are recognizable; ornate scrollwork is reduced to repeated strokes. |
| Hands and grip | Miss | Free fingers are stiff and segmented; other fingers lie parallel across the slate instead of wrapping around it. |
| Trousers, wraps and boots | Miss | Tubular glossy trousers, bulky wraps and bulbous boots diverge from the reference. A seam strip stands away from the outer thigh in the alternate view. |
| Sheikah Slate | Match | Dark case, bronze relief and bright cyan eye match the signature appearance. |

Independent reviewer: separate read-only OpenCode subagent, session `ses_df21568fdffeTFCCm5JetCN3nL`, using source and actual images. Next priorities: fit crown cap and thigh trim in 3D, rebuild facial planes/eye contours, sculpt anatomically joined hands and a wrapped grip, reshape the ankle straps and angular toe caps. Exact side/back likeness remains unverifiable from the single image. Gate A failed; Gates B/C were not run and rig/animation were out of scope.

## Skill execution contracts

All listed specialist skills were discovered with OpenCode's native Skill loader, on demand, not inferred from filenames. Evidence below separates performed operations from approval of the result.

| Skill | Contract status | Execution evidence and limitations |
|---|---|---|
| `blender-director` | APPLIED | Reference analysis and brief before scene edits, dynamic routing, staged MCP queries, bounded visual iteration and independent gate. |
| `camera-cinematography` | APPLIED | Camera/target saved in empty baseline, matched portrait framing before modeling; independent pose/framing close; hero/detail/alternate renders. |
| `character-artist` | FAILED | 12-mesh blockout; live head topology query; measured face deformation; explicit eyes, ears, separate clothing, fingers and slate. Face, hands and lower body did not meet QA. Not deformation-ready topology. |
| `stylized-style` | APPLIED | Larger almond-eye design, grouped tapered locks, restricted blue/ivory/gold palette, modeled shape hierarchy; checked in solid and rendered passes. Does not certify style/identity precision. |
| `hair-groom` | FAILED | Closed mesh clumps for the static stylized asset (not particle hair or cards), sweeps, braid, wisps and simplification pass; visible scalp coverage defect. |
| `materials` | APPLIED | Shared Principled materials, fabric sheen, leather/cloth variation, and separate emissive lens; inspected in renders. Some gloss is still inaccurate. |
| `texture-workflow` | APPLIED | Nine 512² original sRGB pigment tiles generated and saved inside Blender, assigned to UV-sampling image nodes, packed into `.blend` and embedded in GLB. This is a tile workflow, **not** a high-to-low bake, normal-map bake, or packed ORM production atlas. |
| `lighting` | APPLIED | Warm high-left key, cooler fill, back rim and controlled world; revised ratios checked in Cycles. |
| `rendering` | APPLIED | Real CPU Cycles/AgX PNGs produced and inspected; 700×1400 hero, 900×900 detail, 850×1220 alternate, earlier comparison renders saved. |
| `qa-review` | APPLIED | Independent visual QA executed successfully **as a check**; its asset verdict is FAIL. |
| `asset-optimization` | NOT_VERIFIED | Static export triangle budget and connectivity passed; 32 materials, 230 objects, UV packing efficiency, draw-call optimization and full runtime readiness are not cleared. Master remains above runtime triangle target. |
| `export-pipeline` | NOT_VERIFIED | GLB written and round-tripped in a fresh Blender scene as requested review evidence. No approved production release or target-engine verification. |

Sculpting and separate retopology workflows `SKIPPED_WITH_REASON`: directly modeled static surfaces with a vertex form-refinement pass, not a sculpt-and-retopo workflow. No simulation, rig, animation, collision, LOD, or platform-specific delivery requested.

## Technical and provenance evidence

- [Editable Blender file](zelda_reference.blend) contains only the new study scene and its dependencies. The managed session initially reported an unrelated restored scene; the study started using a newly created empty scene, and no objects or materials from the restored scene were used. 230 mesh objects, 121,752 evaluated triangles, 32 assigned materials, nine packed color images, zero nonmanifold evaluated edges and zero loose vertices; all meshes have UV layers. See [audit](technical_audit.json).
- [GLB review checkpoint](zelda_reference.glb): 98,398 triangles after selective simplification of decorative geometry, nine embedded images, meter scale, +Y up, 1.729 m high, lowest-toe pivot at zero. Fresh Blender scene reimport matched triangle count, object count and bounds. Raw reimport has split positions at attribute seams; diagnostic position weld at 0.1 μm left zero nonmanifold edges. Blender-only bump nodes are not exported as baked normal maps. See [reimport evidence](export_validation.json). Not engine-certified.
- OpenCode (openai/gpt-6.2-sol) used live Blender MCP (`get_addon_status`, `get_scene_info`, `look`, `execute_blender_code`); Blender 5.3.0, add-on v2.0, protocol 16. New stage-specific bpy sources and render checkpoints are under `steps/`. No E2E demo script, existing model, downloaded asset, or external AI generator was used. The comparison board is just a side-by-side layout of actual Blender renders and the source.
- No lifecycle, watchdog, GPU safety, global configuration, or quality gate changes. Managed wrapper recorded a healthy AUTO launch with GPU selected; the study explicitly rendered in Cycles on CPU. See [input hashes](inputs.sha256). Skill tree snapshot (sorted `SKILL.md` SHA-256 manifest hashed): `f1b2d70656f0cf876920e01bae1e00810ba8492603414652a9a57fa86b6280bd`. Project commit: `c4fb6a3f084a831f9248bcde25becffef41d64ad`. Local wrapper version `0.1.0`; wrapper config SHA-256 `cdf578e4db90232bbe16d55b984bef4f0fb22cdfe41477094ecd768d7df64fce`. No standardized benchmark timings or equivalence claimed.
