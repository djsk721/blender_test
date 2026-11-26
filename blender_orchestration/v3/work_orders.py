"""Compact Qwen work orders.

Rules for this module (enforced by tests): it contains no asset-specific modeling values. No
colours, coordinates, object dimensions, character names or prop designs are written here; those
come from the goal, the references and the producer's own plan. The harness contributes only the
execution contract, the naming contract, generic tool documentation and reviewer feedback.
"""

from __future__ import annotations

import json
from typing import Any

EXECUTION_CONTRACT = """\
EXECUTION CONTRACT (harness rules, not design guidance):
- Reply with exactly ONE fenced ```python block and nothing that needs tools. Do not call tools.
- Pre-bound globals: bpy, bmesh, math, json, Vector, Euler, Matrix, bt (toolkit), scene (target scene),
  col_export (link every asset mesh here), col_rig (armatures), PHASE (str).
- Units metres. -Y is the character's FRONT (faces the camera), +X is the character's LEFT, +Z up, ground z=0.
- Name every asset mesh SM_<Region>_<Part> with Region in: Body, Head, Eye, Brow, Lash, Ear, Nose, Mouth,
  Hair, Top, Sleeve, Collar, Bottom, Belt, Glove, Hand, Boot, Acc. The head mesh must start with SM_Head.
- Before rebuilding a part, delete the old object first (bt.remove_object_if_exists(name)); duplicate
  `.001` objects, unnamed default primitives, >40 new objects per script or >90 export meshes are rejected.
- Never render, save, open or reset files, never add cameras/lights/scenes, never import or download meshes,
  never use network/subprocess. The harness renders and checkpoints after your script.
- Blender 4.x/5.x API only (no bone.layers, no use_auto_smooth, use Matrix.decompose()).
- No top-level return. Use normal imports, never __import__. When calling make_pbr_material,
  the color parameter is `base_color`, never `color`. A join invalidates its consumed source objects.
- Optionally define LANDMARKS = {"name": (x, y, z), ...} for the key points you used (reported back to you).
"""

TOOLKIT_REFERENCE = """\
TOOLKIT `bt` (generic geometry helpers; plain bpy/bmesh is equally allowed):
- bt.remove_object_if_exists(name); bt.link_to_collection(obj, col_export); bt.set_smooth(obj, True)
- bt.join_objects(objects, name, collection=col_export)  # consumes sources; use only the returned object afterward
- bt.loft_sections(name, sections, segments=24, closed_u=True, cap_start=True, cap_end=True, smooth=True, subsurf=1, collection=col_export)
    sections: list of dicts {"center":(x,y,z), "rx":..., "ry":...} (elliptical rings bottom->top) or explicit vertex rings
- bt.create_lathe_mesh(name, profile_rz, center=(0,0,0), rot=(0,0,0), scale_xy=(1,1), segments=28, cap_start=True, cap_end=True, smooth=True, subsurf=1, collection=col_export)
- bt.create_quad_sphere(name, center, radii, rotation=(0,0,0), subdivisions=3, smooth=True, collection=col_export)
- bt.deform_mesh_sculpt(obj, [{"center":(x,y,z), "radius":r, "delta":(dx,dy,dz), "mirror_x":True}, ...])  # Gaussian push/pull sculpt
- bt.sculpt_tapered_ribbon(name, spine_pts, widths, thicknesses, arch=0.35, twist_deg=None, up_hint=(0,-1,0), smooth=True, subsurf=1, collection=col_export)
    flat tapered crescent-section strips (hair locks, ears, sashes) - not round tubes
- bt.build_sculpted_hair_sheet(name, root_curve, tip_curve, mid_curve=None, rows=10, cols=24, thickness=0.024, wave_amp=0.007, wave_freq=6.0, tip_scallop=0.10, outward_hint=(0,1,0.15), subsurf=1, collection=col_export)
    a broad connected hair mass between a root curve and a tip curve
- bt.build_interwoven_braid(name, spine_pts, radius=0.015, twists=7.0, samples=44, segments=10, collection=col_export)
- bt.create_tube_along_curve(name, points, radii, segments=14, cap_ends=True, smooth=True, subsurf=1, collection=col_export)
- bt.create_beveled_panel(name, profile_2d, depth, location=(0,0,0), rotation=(0,0,0), bevel_width=0.003, bevel_segments=2, smooth=True, collection=col_export)
- bt.add_modifiers(obj, solidify=None, solidify_offset=1.0, bevel=None, bevel_segments=2, subsurf=None, mirror_x=False)
- bt.smart_uv_unwrap(obj); bt.make_pbr_material(name, base_color="#RRGGBB", roughness=0.5, metallic=0.0, subsurface=0.0, surface_type="skin|fabric|leather|hair|metal"); bt.assign_material(obj, mat)
- bt.create_painted_image_texture(name, width, height, ops, base_color=(r,g,b,1.0))  # ops in UV 0..1: rect, ellipse, line, polyline, ring, linear_gradient
OPTIONAL generic humanoid starters (they are templates, not a likeness; reshape them with deform_mesh_sculpt/bmesh
or replace them when the reviewer reports a structural mismatch):
- bt.build_stylized_head_base(name_prefix, center, height=0.22, jaw_taper=0.72, eye_scale=1.0, ear_style='pointed', ear_length=0.055, head_tilt_deg=(0,0,0), collection=col_export) -> dict with landmarks
- bt.build_articulated_arm_chain(name_prefix, shoulder, elbow, wrist, side='R', upper_radii=(0.056,0.042,0.036), forearm_radii=(0.035,0.023), hand_scale=1.0, collection=col_export) -> dict with joints
- bt.build_articulated_hand(name_prefix, wrist_pos, forward_dir, up_dir=(0,0,1), side='R', scale=1.0, collection=col_export)
- bt.create_rig_and_bind(scene, bone_specs, target_meshes=None, armature_name="ARM_Asset") -> dict (then bpy.data.objects["ARM_Asset"])
- bt.create_cycle_animation(armature_obj, {frame: {bone: (rx_deg, ry_deg, rz_deg)}}, action_name="ACT_Cycle", frame_start=1, frame_end=24)
"""

PHASES: dict[str, dict[str, Any]] = {
    "silhouette": {
        "title": "Phase 1 - Silhouette and proportions",
        "task": "Build the complete full-body base: body with correct head-to-body ratio, shoulder/hip widths, torso and limb lengths, "
                "a neutral rigging-friendly pose (arms slightly away from the body), the head as SM_Head (correct size and shape, features "
                "may be blocked in), and the OUTER silhouettes of hair and costume as simple masses. Proportions first; detail later.",
        "plan_keys": ["target_proportions", "structural_geometry"],
    },
    "face": {
        "title": "Phase 2 - Facial structure",
        "task": "Remodel the head and face: skull and facial silhouette (front and profile), eye sockets with eyes seated in them, eyelids, "
                "brows, nose, mouth, jaw and chin, and the ears. Keep facial parts attached to the head surface (no floating or buried parts). "
                "Do not change the body, hair or costume except where they intersect the head.",
        "plan_keys": ["face_landmarks"],
    },
    "hair": {
        "title": "Phase 3 - Hair geometry",
        "task": "Replace the hair block with modeled hair: scalp coverage, the main masses, front fringe/bangs, side locks, braids, and the "
                "back silhouette, built as layered tapered masses or strand groups (not round ropes or tubes). Keep the face and ears readable.",
        "plan_keys": ["hair_masses"],
    },
    "costume": {
        "title": "Phase 4 - Costume construction",
        "task": "Replace the costume blocks with real garment layers that follow the body: upper garment, sleeves, collar/neckline, "
                "undergarment, belt and attachments, lower garment, boots, gloves and accessories. Layers sit just outside the layer "
                "beneath (no z-fighting, no gaps, no floating parts). Keep hands anatomically sized.",
        "plan_keys": ["costume_layers", "accessories"],
    },
    "modeling_gate": {
        "title": "Modeling gate corrections",
        "task": "Correct the defects below on the complete model. Do not regress approved regions.",
        "plan_keys": [],
    },
    "materials": {
        "title": "Phase 5 - Materials and look development",
        "task": "UV-unwrap every export mesh lacking UVs and assign distinct materials for skin, eyes, hair, each fabric, leather and "
                "metal according to your plan's palette; painted textures for facial details or decoration where useful. Do not change "
                "geometry, cameras or lights.",
        "plan_keys": ["materials"],
    },
    "rigging": {
        "title": "Phase 6a - Rigging and deformation",
        "task": "Create one armature ARM_Asset fitted to the model's actual joint positions, bind all export meshes with automatic weights "
                "or explicit groups. Leave a non-neutral deformation test pose active for review, without creating animation. Do not change mesh shapes.",
        "plan_keys": ["rigging_requirements", "animation_requirements"],
    },
    "animation": {
        "title": "Phase 6b - Animation",
        "task": "On the approved armature, create the requested looping clips (idle, walk, run), using exact action names "
                "ACT_Idle, ACT_Walk and ACT_Run. Ensure all actions are retained and exportable. Do not change mesh shapes or the rig.",
        "plan_keys": ["animation_requirements"],
    },
}

PLAN_SCHEMA = """\
{
  "target_proportions": {"height_m": <float>, "heads_tall": <float>, "landmark_z_m": {"ground": 0.0, "ankle": .., "knee": .., "crotch": .., "waist": .., "chest": .., "shoulder": .., "chin": .., "eye_line": .., "crown": ..},
                         "widths_m": {"shoulder": .., "waist": .., "hip": .., "head": ..}, "pose": "<neutral pose description>"},
  "structural_geometry": [{"object": "SM_...", "construction": "<how you will build it>"}],
  "face_landmarks": {"eye_spacing_m": .., "eye_width_m": .., "eye_shape": "..", "nose": "..", "mouth": "..", "jaw_chin": "..", "ears": ".."},
  "hair_masses": [{"object": "SM_Hair_...", "description": "..", "technique": ".."}],
  "costume_layers": [{"object": "SM_...", "layer_order": <int>, "description": "..", "colour_hex": "#.."}],
  "accessories": [{"object": "SM_Acc_...", "description": "..", "attach_to": ".."}],
  "materials": [{"name": "MAT_...", "hex": "#..", "surface_type": "skin|fabric|leather|hair|metal", "roughness": ..}],
  "rigging_requirements": "..", "animation_requirements": ["idle", "walk", "run"],
  "assumptions": ["anything the references do not show and how you decided it"]
}"""


def plan_work_order(goal: str, reference_lines: list[str], reference_notes: str, revision_feedback: str | None = None) -> str:
    parts = [
        "You are the 3D production artist for this asset. You alone make every modeling decision and later write every Blender script.",
        "Task now: write your production plan as ONE JSON object (no code, no tools). Study the attached reference images yourself.",
        "PRODUCTION GOAL:\n" + goal.strip(),
        "Attached references:\n" + "\n".join(reference_lines),
        "Your earlier reference-analysis handoff (cross-check with the attached pixels):\n" + reference_notes.strip()[:5000],
        "Plan schema (fill every field with concrete values derived from the references):\n" + PLAN_SCHEMA,
    ]
    if revision_feedback:
        parts.append("Revise your previous plan to fix these omissions/inconsistencies found by the reviewer:\n" + revision_feedback[:3000])
    return "\n\n".join(parts)


def _plan_excerpt(plan: dict[str, Any], keys: list[str], limit: int = 4500) -> str:
    subset = {k: plan.get(k) for k in keys if k in plan} if keys else {}
    subset["target_proportions"] = plan.get("target_proportions")
    return json.dumps(subset, indent=1)[:limit]


def build_phase_work_order(*, phase: str, goal: str, plan: dict[str, Any], inventory: str,
                           reference_lines: list[str], automated_findings: list[dict[str, Any]] | None = None) -> str:
    spec = PHASES[phase]
    parts = [
        f"You are the 3D production artist for this asset. {spec['title']}.",
        "TASK: " + spec["task"],
        "GOAL (excerpt):\n" + goal.strip()[:2200],
        "YOUR OWN PLAN (relevant part):\n" + _plan_excerpt(plan, spec["plan_keys"]),
        "Attached: bounded copies of the references and, when available, the latest renders of the current model.\n" + "\n".join(reference_lines),
        "CURRENT SCENE INVENTORY:\n" + inventory[:3500],
    ]
    if automated_findings:
        parts.append("AUTOMATED GEOMETRY FINDINGS (measured in Blender):\n" + json.dumps(automated_findings, indent=1)[:1800])
    parts += [EXECUTION_CONTRACT, TOOLKIT_REFERENCE]
    return "\n\n".join(parts)


STRATEGY_TEXT = {
    "ADJUST": "Strategy ADJUST: edit the existing objects of the listed regions (reshape vertices, sculpt, re-proportion, reposition). Keep everything else.",
    "REMODEL": "Strategy REMODEL (required - local tweaks did not fix these defects): delete every object of the listed regions and rebuild "
               "them with a DIFFERENT construction method than your previous code used. Do not merely move, rescale or recolour.",
}


def build_correction_work_order(*, phase: str, goal: str, plan: dict[str, Any], inventory: str, strategy: str,
                                regions: list[str], defects: list[dict[str, Any]], reference_lines: list[str],
                                previous_code: str | None, automated_findings: list[dict[str, Any]] | None = None,
                                attempt: int = 1, budget: int = 1) -> str:
    compact = [{k: d.get(k) for k in ("defect_id", "region", "object", "severity", "observed", "expected_correction",
                                      "verification_criterion", "reference_image", "reference_viewpoint")} for d in defects]
    parts = [
        f"You are the 3D production artist for this asset. Correction {attempt}/{budget} for {PHASES[phase]['title']}.",
        STRATEGY_TEXT[strategy] + f" Regions: {', '.join(regions)}.",
        "DEFECTS FROM THE INDEPENDENT REVIEW OF THE ACTUAL RENDERS (fix these; each lists how it will be verified):\n" + json.dumps(compact, indent=1)[:5000],
        "The attached render sheets show the current model; compare them with the attached references.",
        "GOAL (excerpt):\n" + goal.strip()[:1500],
        "YOUR OWN PLAN (relevant part):\n" + _plan_excerpt(plan, PHASES[phase]["plan_keys"] or ["face_landmarks", "hair_masses", "costume_layers"], 3000),
        "CURRENT SCENE INVENTORY:\n" + inventory[:3500],
        "\n".join(reference_lines),
    ]
    if automated_findings:
        parts.append("AUTOMATED GEOMETRY FINDINGS:\n" + json.dumps(automated_findings, indent=1)[:1500])
    if previous_code:
        parts.append("YOUR PREVIOUS SCRIPT FOR THIS PHASE (already applied to the scene; do not re-run it blindly):\n```text\n"
                     + previous_code[:7000] + "\n```")
    parts += [EXECUTION_CONTRACT, TOOLKIT_REFERENCE]
    return "\n\n".join(parts)


def build_retry_work_order(base_prompt: str, error: str, failed_code: str | None) -> str:
    snippet = ""
    if failed_code:
        snippet = "\nFailed script:\n```text\n" + failed_code + "\n```"
    return ("YOUR PREVIOUS SCRIPT FAILED AND THE SCENE WAS RESTORED TO ITS STATE BEFORE IT.\nError:\n```\n" + error[-1500:]
            + "\n```" + snippet + "\nFix the exact failing statement first. Preserve the working code and avoid introducing new APIs. "
            "Check the rest of the script for repetitions of the same mistake. Return the complete corrected script "
            "(one ```python block).\n\n" + base_prompt)


def reference_analysis_prompt(goal: str, reference_lines: list[str], overlaps: list[dict[str, Any]]) -> str:
    return "\n\n".join([
        "You are the producer preparing a reference-analysis handoff for your own later Blender modeling sessions.",
        "Describe ONLY what the attached reference images show. Do not write code and do not choose construction methods.",
        "GOAL:\n" + goal.strip()[:2500],
        "References:\n" + "\n".join(reference_lines),
        "Known content overlaps between files: " + json.dumps(overlaps),
        "Return ONE valid JSON object under 1800 tokens. List each exact attached filename once; keep descriptions brief. "
        "Schema: {\"per_reference\": [{\"file\": .., \"shows\": .., \"views\": [..]}], "
        "\"proportions\": {\"heads_tall_estimate\": .., \"landmarks_as_fraction_of_height\": {\"chin\": .., \"shoulder\": .., \"chest\": .., \"waist\": .., \"crotch\": .., \"knee\": .., \"ankle\": ..}, \"shoulder_to_height\": .., \"hip_to_height\": ..}, "
        "\"face\": .., \"hair\": .., \"costume_layers\": [..], \"accessories\": [..], \"palette\": [{\"part\": .., \"hex\": ..}], "
        "\"conflicts\": [..], \"authority\": {\"identity\": <file>, \"geometry\": <file>, \"costume\": <file>, \"face_hair\": <file>}, \"not_shown\": [..]}",
    ])


def plan_review_prompt(goal: str, plan: dict[str, Any]) -> str:
    return "\n\n".join([
        "You are the independent reviewer. Check the 3D artist's production plan for omissions and inconsistencies against the goal "
        "and the attached references. Do not redesign it and do not write code.",
        "GOAL:\n" + goal.strip()[:2500],
        "PLAN:\n" + json.dumps(plan, indent=1)[:9000],
        "Return ONE JSON object: {\"acceptable\": <bool>, \"omissions\": [..], \"inconsistencies\": [..], \"notes\": \"..\"}",
    ])
