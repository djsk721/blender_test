# Approved anatomical base meshes in Harness V3

The operator selects a trusted local manifest and an exact Blender object name. V3 extends the legacy manifest verifier with original ZIP hashing, Blender library hashing, ZIP-member equality and recorded Blender distribution/license checks. The digests attest integrity against the supplied approval manifest; they are not an upstream digital signature. The recorded library byte count in the existing manifest is stale; the actual `.blend` and the member of the approved archive both have the recorded SHA-256. See the [integration report](reports/harness-v3-base-mesh-integration.md).

Inspect an installed library without using the production run's scene or port:

```bash
stamp=$(date +%Y%m%d-%H%M%S)
python scripts/inspect_base_mesh_bundle.py --port 19879 \
  --manifest assets/approved_base_meshes/blender_human_base_meshes/provenance.json \
  --output "output/base-inspection-$stamp/inventory.json" \
  --runtime "output/base-inspection-$stamp/runtime"
```

Use a unique empty runtime path and an unused port. Examine the exact object **type**, names, bounds, topology, materials, modifiers and relationships. Some friendly names refer to preview cameras. `GEO-body_female_stylized` is the full stylized female body, not `stylized_body_female`. A torso, hand or head alone cannot substitute for a full-body base.

During production the isolated Blender MCP session checks the selected object before producer work begins. The supervisor binds the exact verified source to a no-argument loader; only a provenance-authorized Qwen silhouette script invokes it. That bridge uses the legacy native append/normalize path and re-verifies the source. The first edit must actually reshape the imported geometry, preserve the body in `COL_Export`, and avoid regressions in component count, boundary edges, non-manifold edges, winding and degenerate faces. These raw topology checks do not prove the absence of self-intersections or a correct deformation under animation. Visual gates still govern anatomy, likeness, clearance and fit, and the existing motion stages govern pose and animation quality.

`base_mesh_preflight.json`, checkpoint registry entries, `production_state.json`, the provenance ledger, candidate `mcp.json` files and `iteration_report.json` bind the run to the selected library and geometry strategy. Corrections use the best reviewed candidate, not an unrestricted fresh import. A switched strategy or selected mesh requires a new run. `FROM_SCRATCH` never imports an approved base and remains the default.
