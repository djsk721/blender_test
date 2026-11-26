"""Resolve geometry inputs before starting any agent or Blender session."""
from ..mcp_adapter import verify_approved_base_mesh_manifest

STRATEGIES = ('FROM_SCRATCH', 'BASE_MESH')


def resolve_geometry(strategy, manifest=None, name=None):
    if strategy not in STRATEGIES:
        raise ValueError(f'Unknown geometry strategy: {strategy!r}')
    if strategy == 'FROM_SCRATCH':
        if manifest is not None or name is not None:
            raise ValueError('Base-mesh options require BASE_MESH')
        return {'geometry_strategy': strategy}
    if manifest is None:
        raise ValueError('BASE_MESH requires --base-mesh-manifest')
    if not name or not name.strip():
        raise ValueError('BASE_MESH requires --base-mesh-name (exact Blender object name)')
    return {'geometry_strategy': strategy, 'base_mesh': {
        **verify_approved_base_mesh_manifest(manifest, require_archive=True), 'object_name': name,
    }}


BASE_BRIEF = """
The selected base is a generic starting structure, not the character. Match reference landmarks
(crown/chin, shoulders, chest/waist/hips, elbows/wrists, knees/ankles, feet and weight distribution).
Build a coherent stylized female silhouette and preserve joint loops and connected surfaces.
Fit costume to the modified body; use the references for face, pointed ears, scalp/hair/ braid,
garment construction and accessories, not the generic source anatomy. Check -Y front and Z up.
Preserve the continuous body named SM_BaseBody across stages. Its head is part of the same mesh:
do not add a second SM_Head or detach the head to satisfy the generic naming instructions.
Never include SM_BaseBody in deletion, rebuild, or join lists; Blender invalidates references on deletion.
Preserve the ANAT_Head vertex group for measurement. Do not import other libraries.
"""

BASE_FIRST_EDIT = """
On this empty scene call `loaded = load_approved_body()` exactly once (no arguments).
It returns the already-linked `object`, `landmarks`, companion eye **names**, and source metadata.
Look up eye names in `bpy.data.objects`. There are no previous garments or shells to delete here.
Transform `loaded['object']`
in this same edit; loading, renaming, or adding clothing alone will not pass. `bmo` is the legacy
toolkit; `bmo.apply_semantic_stylization(obj, controls)` can reshape head, shoulders, waist, hips
and limbs. Use your own measured landmark adjustments where needed, including elbow/knee placement
and arm repose; check sockets after moving the head. Block out fitted garments, boots and hair masses
so the body is clothed and the four renders read as the target, not a bare generic T-pose.
"""
