"""Asset-type QA profiles and character modeling quality evaluators.

Prevents hard-surface, ship/hull, or mechanical checklist criteria from
leaking into humanoid/creature character evaluations, and provides real
scene-geometry and polygon-inflation diagnostics.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class AssetQAProfileType:
    """Supported asset-type QA profile identifiers."""

    HUMANOID_CHARACTER = "HUMANOID_CHARACTER"
    CREATURE = "CREATURE"
    HARD_SURFACE_PROP = "HARD_SURFACE_PROP"
    ENVIRONMENT = "ENVIRONMENT"
    ANIMATED_CHARACTER = "ANIMATED_CHARACTER"

    ALL = (
        HUMANOID_CHARACTER,
        CREATURE,
        HARD_SURFACE_PROP,
        ENVIRONMENT,
        ANIMATED_CHARACTER,
    )


CHARACTER_FORBIDDEN_CRITERIA_KEYWORDS: tuple[str, ...] = (
    "hull",
    "ship",
    "starboard",
    "nav light",
    "metal panel",
    "greeble",
    "spacecraft",
    "cockpit",
    "machinery",
    "fuselage",
    "bulkhead",
    "thruster",
)


@dataclass(frozen=True)
class QACriterion:
    """Single verifiable QA criterion belonging to an asset-type profile."""

    criterion_id: str
    domain: str
    description: str
    evidence_source: str  # "blender_scene" | "rendered_image" | "both"
    severity_on_fail: str = "MAJOR"  # "BLOCKER" | "MAJOR" | "MINOR"
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QAProfile:
    """Asset-type-specific QA profile with explicit scope and anti-contamination rules."""

    profile_id: str
    asset_type: str
    reference_mode: str
    required_views: tuple[str, ...]
    criteria: tuple[QACriterion, ...]
    forbidden_criteria_keywords: tuple[str, ...] = ()
    min_reference_match_score: float | None = None
    exact_likeness_required: bool = False
    triangle_budget_default: int = 25000
    material_budget_default: int = 8
    max_fragment_objects: int = 40

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["criteria"] = [c.to_dict() for c in self.criteria]
        return data

    def criterion_ids(self) -> list[str]:
        return [c.criterion_id for c in self.criteria]


_HUMANOID_FACE_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="face_3d_surface_continuity",
        domain="face",
        description="Face exhibits cohesive 3D planes (brow, cheekbones, nose bridge, chin) without flat/floating patches.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="facial_feature_placement",
        domain="face",
        description="Eyelids, eyes, nose, lips, ears, and chin are naturally aligned in anatomical facial proportions.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="non_protruding_eyes_and_almond_contour",
        domain="face",
        description="Eyes sit naturally within recessed eye sockets with eyelid wrap; no bulging or overly round doll-like disks.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="forehead_cheek_jaw_proportions",
        domain="face",
        description="Forehead width, cheek volume, and jaw taper balance cleanly without bloated cheeks or oversized forehead.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="front_and_profile_silhouette",
        domain="face",
        description="Facial silhouette reads naturally in both front and side/profile views.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
)

_HUMANOID_HAIR_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="scalp_coverage_no_bald_exposure",
        domain="hair",
        description="Scalp cap and hair roots fully cover the cranium behind braids, parts, and bangs without skin/bald patches.",
        evidence_source="both",
        severity_on_fail="BLOCKER",
    ),
    QACriterion(
        criterion_id="sculpted_clump_volume_not_rope_tube",
        domain="hair",
        description="Hair forms sculpted, layered clumps and natural braids rather than uniform cylindrical ropes or stacked spheres.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="hair_flow_direction_and_silhouette",
        domain="hair",
        description="Hair exhibits coherent directional flow, layered volume, and clean silhouette readability.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="hair_face_shoulder_no_penetration",
        domain="hair",
        description="Hair locks clear face, ears, and shoulders without severe interpenetration or clipping.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="tapered_tips_and_braid_structure",
        domain="hair",
        description="Hair tips taper organically and braided strands interlock cleanly.",
        evidence_source="rendered_image",
        severity_on_fail="MINOR",
    ),
)

_HUMANOID_BODY_COSTUME_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="shoulder_waist_pelvis_leg_proportions",
        domain="body_and_costume",
        description="Shoulders, torso, waist, pelvis, and legs satisfy coherent stylized/realistic human proportions.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="torso_costume_natural_fit",
        domain="body_and_costume",
        description="Tunic, bodice, belt, and sleeves hug underlying body contours naturally without awkward shoulder bulk.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="articulated_hand_palm_finger_connection",
        domain="body_and_costume",
        description="Palms, thumbs, and fingers connect with continuous 3D knuckle/webbing volume (no disconnected sticks).",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="held_object_finger_wrap_contact",
        domain="body_and_costume",
        description="When holding an item, fingers wrap naturally around its surfaces with verified contact.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="thigh_knee_ankle_seam_attachment_no_floating_or_clipping",
        domain="body_and_costume",
        description="Costume trims, seams, and wraps stay attached to thighs/knees/ankles without floating gaps or clipping.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="boot_instep_heel_ankle_anatomy",
        domain="body_and_costume",
        description="Boots clearly articulate sole, heel, instep arch, toe box, and fitted ankle collar.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="no_bloated_or_ballooned_costume_and_boots",
        domain="body_and_costume",
        description="Sleeves, trousers, wraps, and boots avoid overly inflated or tubular balloon silhouettes.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
)

_HUMANOID_FORM_TOPOLOGY_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="sculpted_form_vs_primitive_stacking",
        domain="form_and_topology",
        description="Asset reflects cohesive modeled/sculpted forms rather than excessive stacked primitive tubes/ellipsoids.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="silhouette_and_surface_continuity",
        domain="form_and_topology",
        description="Primary and secondary silhouettes read cleanly with smooth surface continuity and valid normals.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="localized_mesh_density_distribution",
        domain="form_and_topology",
        description="Mesh resolution is concentrated in high-detail areas (face, hands, joints) rather than uniform bloat.",
        evidence_source="blender_scene",
        severity_on_fail="MINOR",
    ),
    QACriterion(
        criterion_id="deformation_ready_joint_topology",
        domain="form_and_topology",
        description="Quad-dominant topology with edge loops around eyes, mouth, shoulders, elbows, hips, and knees.",
        evidence_source="blender_scene",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="no_raw_polygon_inflation_without_form_gain",
        domain="form_and_topology",
        description="Quality iterations do not merely subdivide/inflate polygon count without resolving geometric/visual defects.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="triangle_budget_within_limit",
        domain="form_and_topology",
        description="Evaluated and export triangle counts stay within the Production Brief budget.",
        evidence_source="blender_scene",
        severity_on_fail="BLOCKER",
    ),
)

_REFERENCE_MODE_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="reference_camera_match",
        domain="reference_match",
        description="Hero render camera angle and framing correspond to the approved reference image.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="reference_silhouette_and_pose_match",
        domain="reference_match",
        description="Character pose, limb proportions, and silhouette match the approved reference at matching scale.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="reference_palette_and_material_match",
        domain="reference_match",
        description="Base albedo values, material contrast, and lighting response match the approved reference.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="reference_identity_match",
        domain="reference_match",
        description="Overall visual identity against approved reference achieves >= 8/10 categories Match or Close.",
        evidence_source="rendered_image",
        severity_on_fail="BLOCKER",
    ),
)

_TEXT_ONLY_MODE_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="text_specification_compliance",
        domain="text_spec",
        description="Character geometry, costume, and materials fulfill all requirements in the textual specification.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
)

_ANIMATION_DEFORMATION_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="rig_hierarchy_and_skin_weights",
        domain="rigging",
        description="Symmetric humanoid armature hierarchy and clean vertex weights across limbs, torso, hair, and cloth.",
        evidence_source="blender_scene",
        severity_on_fail="BLOCKER",
    ),
    QACriterion(
        criterion_id="joint_deformation_no_pinching_or_clipping",
        domain="rigging",
        description="Shoulder, elbow, hip, knee, and ankle poses deform without severe volume collapse or costume clipping.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="animation_cycle_contact_and_loop",
        domain="animation",
        description="Animation playback shows crisp stride, ground contact without skating, root stability, and seamless loop.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
)

_CREATURE_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="creature_anatomical_silhouette",
        domain="anatomy",
        description="Creature head, spine, limbs/wings/tail proportions read coherently across angles.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="creature_head_eyes_jaw_structure",
        domain="face",
        description="Eyes, snout/jaw, horns/ears integrate smoothly with skull planes without floating shells.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="creature_skin_scales_fur_readability",
        domain="materials",
        description="Surface breakup, scales/hide/fur clumps, and material roughness contrast read cleanly.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="triangle_budget_within_limit",
        domain="form_and_topology",
        description="Evaluated triangle count stays within creature budget with quad-dominant deforming zones.",
        evidence_source="blender_scene",
        severity_on_fail="BLOCKER",
    ),
)

_HARD_SURFACE_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="hard_surface_silhouette_and_bevels",
        domain="hard_surface",
        description="Primary silhouette, chamfered/beveled edges, and panel breaks read cleanly without 90-degree razor edges.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="subpart_attachment_and_pivot",
        domain="hard_surface",
        description="Mechanical subparts, fasteners, and brackets attach cleanly with logical floor/grip pivot.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="pbr_metal_roughness_and_uvs",
        domain="materials",
        description="Metal/dielectric separation, roughness variation, and non-overlapping UVs are verified.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="triangle_budget_within_limit",
        domain="form_and_topology",
        description="Triangle budget, applied transforms, and manifold checks pass.",
        evidence_source="blender_scene",
        severity_on_fail="BLOCKER",
    ),
)

_ENVIRONMENT_CRITERIA: tuple[QACriterion, ...] = (
    QACriterion(
        criterion_id="modular_scale_and_grounding",
        domain="environment",
        description="Modular kit pieces snap cleanly on metric grid with zero unintended floating geometry.",
        evidence_source="both",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="composition_lighting_and_depth",
        domain="lighting",
        description="Foreground/midground/background depth separation, shadow exposure, and focal readability hold.",
        evidence_source="rendered_image",
        severity_on_fail="MAJOR",
    ),
    QACriterion(
        criterion_id="collection_hierarchy_and_budget",
        domain="form_and_topology",
        description="Scene collections and instanced meshes stay within environment draw-call and triangle budgets.",
        evidence_source="blender_scene",
        severity_on_fail="MAJOR",
    ),
)


def get_qa_profile(
    asset_type: str,
    *,
    animated: bool = False,
    reference_mode: str = "TEXT_ONLY",
) -> QAProfile:
    """Return the dedicated QAProfile for the given asset type, animation scope, and reference mode."""
    norm = (asset_type or "").strip().lower()
    is_ref = reference_mode == "REFERENCE_GUIDED"
    mode_criteria = _REFERENCE_MODE_CRITERIA if is_ref else _TEXT_ONLY_MODE_CRITERIA

    if norm in ("animated_character", "animated") or (norm in ("character", "humanoid", "humanoid_character") and animated):
        views = ("front", "three_quarter", "side", "back", "face_closeup", "hands_closeup", "boots_closeup", "pose_run_strip")
        if is_ref:
            views = views + ("reference_matched",)
        return QAProfile(
            profile_id=AssetQAProfileType.ANIMATED_CHARACTER,
            asset_type="character",
            reference_mode=reference_mode,
            required_views=views,
            criteria=(
                _HUMANOID_FACE_CRITERIA
                + _HUMANOID_HAIR_CRITERIA
                + _HUMANOID_BODY_COSTUME_CRITERIA
                + _HUMANOID_FORM_TOPOLOGY_CRITERIA
                + _ANIMATION_DEFORMATION_CRITERIA
                + mode_criteria
            ),
            forbidden_criteria_keywords=CHARACTER_FORBIDDEN_CRITERIA_KEYWORDS,
            min_reference_match_score=0.8 if is_ref else None,
            exact_likeness_required=is_ref,
            triangle_budget_default=25000,
            material_budget_default=8,
            max_fragment_objects=40,
        )

    if norm in ("character", "humanoid", "humanoid_character"):
        views = ("front", "three_quarter", "side", "back", "face_closeup", "hands_closeup", "boots_closeup")
        if is_ref:
            views = views + ("reference_matched",)
        return QAProfile(
            profile_id=AssetQAProfileType.HUMANOID_CHARACTER,
            asset_type="character",
            reference_mode=reference_mode,
            required_views=views,
            criteria=(
                _HUMANOID_FACE_CRITERIA
                + _HUMANOID_HAIR_CRITERIA
                + _HUMANOID_BODY_COSTUME_CRITERIA
                + _HUMANOID_FORM_TOPOLOGY_CRITERIA
                + mode_criteria
            ),
            forbidden_criteria_keywords=CHARACTER_FORBIDDEN_CRITERIA_KEYWORDS,
            min_reference_match_score=0.8 if is_ref else None,
            exact_likeness_required=is_ref,
            triangle_budget_default=25000,
            material_budget_default=8,
            max_fragment_objects=40,
        )

    if norm == "creature":
        views = ("front", "three_quarter", "side", "back", "head_closeup")
        if is_ref:
            views = views + ("reference_matched",)
        extra = _ANIMATION_DEFORMATION_CRITERIA if animated else ()
        return QAProfile(
            profile_id=AssetQAProfileType.CREATURE,
            asset_type="creature",
            reference_mode=reference_mode,
            required_views=views,
            criteria=_CREATURE_CRITERIA + extra + mode_criteria,
            forbidden_criteria_keywords=CHARACTER_FORBIDDEN_CRITERIA_KEYWORDS,
            min_reference_match_score=0.8 if is_ref else None,
            exact_likeness_required=is_ref,
            triangle_budget_default=25000,
            material_budget_default=6,
            max_fragment_objects=40,
        )

    if norm == "environment":
        views = ("hero_wide", "three_quarter", "detail", "top_layout")
        return QAProfile(
            profile_id=AssetQAProfileType.ENVIRONMENT,
            asset_type="environment",
            reference_mode=reference_mode,
            required_views=views,
            criteria=_ENVIRONMENT_CRITERIA + mode_criteria,
            forbidden_criteria_keywords=(),
            min_reference_match_score=0.8 if is_ref else None,
            exact_likeness_required=is_ref,
            triangle_budget_default=50000,
            material_budget_default=8,
            max_fragment_objects=150,
        )

    # Hard surface prop / weapon / vehicle / everyday prop
    views = ("three_quarter", "front", "side", "back")
    if is_ref:
        views = views + ("reference_matched",)
    return QAProfile(
        profile_id=AssetQAProfileType.HARD_SURFACE_PROP,
        asset_type=norm or "prop",
        reference_mode=reference_mode,
        required_views=views,
        criteria=_HARD_SURFACE_CRITERIA + mode_criteria,
        forbidden_criteria_keywords=(),
        min_reference_match_score=0.8 if is_ref else None,
        exact_likeness_required=is_ref,
        triangle_budget_default=8000,
        material_budget_default=4,
        max_fragment_objects=25,
    )


def validate_profile_criteria_hygiene(profile: QAProfile, checklist_items: list[str] | None = None) -> dict[str, Any]:
    """Ensure unrelated ship/hull/hard-surface criteria never contaminate character QA checklists."""
    items_to_check = list(checklist_items) if checklist_items is not None else [
        f"{c.criterion_id}: {c.description}" for c in profile.criteria
    ]
    violations: list[dict[str, str]] = []
    for item in items_to_check:
        lowered = item.lower()
        for kw in profile.forbidden_criteria_keywords:
            if kw in lowered:
                violations.append({"item": item, "forbidden_keyword": kw})
    return {
        "profile_id": profile.profile_id,
        "hygienic": len(violations) == 0,
        "violations": violations,
    }


def analyze_character_scene_metrics(
    scene_audit: dict[str, Any],
    *,
    triangle_budget: int = 25000,
    material_budget: int = 8,
    max_fragment_objects: int = 40,
) -> dict[str, Any]:
    """Derive deterministic geometric and structural character QA findings from a Blender scene audit."""
    mesh_objects = int(scene_audit.get("mesh_objects") or scene_audit.get("export_object_count") or 0)
    triangles = int(scene_audit.get("triangles") or scene_audit.get("total_export_tris") or 0)
    material_count = int(scene_audit.get("material_count") or 0)
    meshes = list(scene_audit.get("meshes") or [])
    spatial = dict(scene_audit.get("character_spatial_checks") or {})

    non_manifold_total = sum(int(m.get("non_manifold_edges", 0)) for m in meshes)
    loose_verts_total = sum(int(m.get("loose_verts", 0)) for m in meshes)
    missing_uvs = [m.get("name", "unknown") for m in meshes if int(m.get("uv_layers", 0)) < 1]
    unapplied_scale = [m.get("name", "unknown") for m in meshes if m.get("scale_applied") is False]

    quad_ratios = [float(m["quad_ratio"]) for m in meshes if "quad_ratio" in m]
    avg_quad_ratio = round(sum(quad_ratios) / len(quad_ratios), 4) if quad_ratios else None

    detected_defects: list[dict[str, Any]] = []

    if triangles > triangle_budget:
        detected_defects.append({
            "defect_type": "triangle_budget_overrun",
            "severity": "BLOCKER",
            "criterion_id": "triangle_budget_within_limit",
            "visual_problem": f"Scene evaluated triangle count ({triangles}) exceeds budget ({triangle_budget}).",
            "required_fix_action": "Selectively simplify high-density decorative subparts or retopologize dense meshes within budget.",
            "affected_objects": [m.get("name", "") for m in sorted(meshes, key=lambda x: int(x.get("tris", 0)), reverse=True)[:5] if m.get("name")],
        })

    if mesh_objects > max_fragment_objects:
        detected_defects.append({
            "defect_type": "primitive_stacking",
            "severity": "MAJOR",
            "criterion_id": "sculpted_form_vs_primitive_stacking",
            "visual_problem": f"Character is fragmented into {mesh_objects} discrete mesh objects (threshold <= {max_fragment_objects}), indicating stacked primitives rather than cohesive sculpted parts.",
            "required_fix_action": "Merge and retopologize contiguous body, hair clump, and costume sub-parts into clean anatomical meshes.",
            "affected_objects": ["COL_Export"],
        })

    if material_count > material_budget:
        detected_defects.append({
            "defect_type": "material_sprawl",
            "severity": "MINOR",
            "criterion_id": "localized_mesh_density_distribution",
            "visual_problem": f"Assigned material count ({material_count}) exceeds budget ({material_budget}).",
            "required_fix_action": "Consolidate redundant materials into shared MAT_* shaders and UV texture atlases.",
            "affected_objects": ["COL_Export"],
        })

    if non_manifold_total > 0 or loose_verts_total > 0:
        detected_defects.append({
            "defect_type": "mesh_topology_errors",
            "severity": "MAJOR",
            "criterion_id": "silhouette_and_surface_continuity",
            "visual_problem": f"Mesh audit found {non_manifold_total} non-manifold edges and {loose_verts_total} loose vertices.",
            "required_fix_action": "Merge by distance, remove loose vertices, and close non-manifold boundary edges.",
            "affected_objects": [m.get("name", "") for m in meshes if int(m.get("non_manifold_edges", 0)) > 0 or int(m.get("loose_verts", 0)) > 0],
        })

    if missing_uvs:
        detected_defects.append({
            "defect_type": "missing_uv_layers",
            "severity": "MAJOR",
            "criterion_id": "silhouette_and_surface_continuity",
            "visual_problem": f"Meshes missing UV layers: {', '.join(missing_uvs[:6])}.",
            "required_fix_action": "Create UVMap and unwrap affected meshes cleanly.",
            "affected_objects": missing_uvs,
        })

    if unapplied_scale:
        detected_defects.append({
            "defect_type": "unapplied_transforms",
            "severity": "MINOR",
            "criterion_id": "deformation_ready_joint_topology",
            "visual_problem": f"Unapplied non-unit scale on: {', '.join(unapplied_scale[:6])}.",
            "required_fix_action": "Apply object scale/rotation transforms cleanly.",
            "affected_objects": unapplied_scale,
        })

    # Optional spatial checks emitted by build_character_scene_audit_code
    if spatial:
        if spatial.get("scalp_gap_detected"):
            detected_defects.append({
                "defect_type": "scalp_exposure",
                "severity": "BLOCKER",
                "criterion_id": "scalp_coverage_no_bald_exposure",
                "visual_problem": "Exposed cranium/scalp rays behind hairline or braid without hair coverage.",
                "required_fix_action": "Extend Hair_Scalpcap coverage and pull root hair clumps over exposed crown vertices.",
                "affected_objects": spatial.get("scalp_affected_objects") or ["SM_Char_Hair_Scalp"],
            })
        if float(spatial.get("max_thigh_seam_gap_m", 0.0)) > 0.015:
            detected_defects.append({
                "defect_type": "floating_thigh_seam",
                "severity": "MAJOR",
                "criterion_id": "thigh_knee_ankle_seam_attachment_no_floating_or_clipping",
                "visual_problem": f"Costume trim/seam floats {spatial.get('max_thigh_seam_gap_m'):.4f} m away from underlying trouser/leg mesh.",
                "required_fix_action": "Snap/shrinkwrap floating trim vertices onto the leg surface with controlled outward offset.",
                "affected_objects": spatial.get("floating_seam_objects") or ["SM_Char_Costume_Trim"],
            })
        if float(spatial.get("eye_protrusion_m", 0.0)) > 0.012:
            detected_defects.append({
                "defect_type": "round_protruding_eyes",
                "severity": "MAJOR",
                "criterion_id": "non_protruding_eyes_and_almond_contour",
                "visual_problem": f"Eye geometry protrudes {spatial.get('eye_protrusion_m'):.4f} m beyond orbital socket rim.",
                "required_fix_action": "Recess eyeballs into orbital sockets, add upper/lower eyelid wrap, and refine almond contour.",
                "affected_objects": spatial.get("eye_objects") or ["SM_Char_Head", "SM_Char_Eyes"],
            })
        if float(spatial.get("hair_face_penetration_ratio", 0.0)) > 0.08:
            detected_defects.append({
                "defect_type": "hair_face_penetration",
                "severity": "MAJOR",
                "criterion_id": "hair_face_shoulder_no_penetration",
                "visual_problem": "Hair clump vertices penetrate head/shoulder surface.",
                "required_fix_action": "Offset penetrating hair clump vertices outward along surface normal outside head/shoulder bounds.",
                "affected_objects": spatial.get("hair_objects") or ["SM_Char_Hair"],
            })
        if float(spatial.get("hand_prop_gap_m", 0.0)) > 0.02:
            detected_defects.append({
                "defect_type": "stiff_fingers_bad_grip",
                "severity": "MAJOR",
                "criterion_id": "held_object_finger_wrap_contact",
                "visual_problem": f"Held item grip gap ({spatial.get('hand_prop_gap_m'):.4f} m) indicates fingers do not wrap around object.",
                "required_fix_action": "Curl finger phalanges around held object bounds and seat palm contact.",
                "affected_objects": spatial.get("hand_objects") or ["SM_Char_Hands"],
            })

    return {
        "mesh_objects": mesh_objects,
        "triangles": triangles,
        "triangle_budget": triangle_budget,
        "within_triangle_budget": triangles <= triangle_budget,
        "material_count": material_count,
        "material_budget": material_budget,
        "within_material_budget": material_count <= material_budget,
        "fragmentation_ok": mesh_objects <= max_fragment_objects,
        "avg_quad_ratio": avg_quad_ratio,
        "non_manifold_edges": non_manifold_total,
        "loose_verts": loose_verts_total,
        "detected_defects": detected_defects,
        "passed_technical_geometry_checks": len(detected_defects) == 0,
    }


def detect_polygon_inflation(before_metrics: dict[str, Any], after_metrics: dict[str, Any]) -> dict[str, Any]:
    """Detect invalid quality iterations that only increase polygon count without solving defects."""
    tris_before = int(before_metrics.get("triangles") or before_metrics.get("total_export_tris") or 0)
    tris_after = int(after_metrics.get("triangles") or after_metrics.get("total_export_tris") or 0)
    defects_before = int(before_metrics.get("open_defect_count", 0))
    defects_after = int(after_metrics.get("open_defect_count", 0))
    score_before = float(before_metrics.get("quality_score", 0.0))
    score_after = float(after_metrics.get("quality_score", 0.0))

    if tris_before <= 0:
        ratio = 1.0
    else:
        ratio = tris_after / tris_before

    significant_poly_increase = ratio > 1.20
    form_improved = (defects_after < defects_before) or (score_after > score_before)
    inflation_only = significant_poly_increase and not form_improved

    return {
        "triangles_before": tris_before,
        "triangles_after": tris_after,
        "triangle_growth_ratio": round(ratio, 3),
        "defects_before": defects_before,
        "defects_after": defects_after,
        "inflation_only_detected": inflation_only,
        "reason": (
            f"Triangle count increased by {(ratio - 1.0) * 100:.1f}% without reducing defects or improving form score."
            if inflation_only
            else "Polygon delta acceptable."
        ),
    }


def detect_2d_projection_or_primitive_shortcuts(
    script_text: str,
    scene_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect forbidden 2D pixel-depth projection, UV-sphere stacking, tube hair, and Subsurf-only shortcuts."""
    src = (script_text or "").lower()
    flags: list[dict[str, str]] = []

    if ("ray_cast" in src and ("def face(" in src or "250" in src)) or (
        "def p(" in src and "782-" in src
    ):
        flags.append({
            "shortcut": "2d_pixel_depth_projection",
            "reason": "Script projects 2D image pixel coordinates onto depth scalars instead of building consistent 3D forms.",
        })

    if "create_uvsphere" in src and "eyelid" not in src and "socket" not in src:
        flags.append({
            "shortcut": "uv_sphere_without_orbital_socket",
            "reason": "Raw UV sphere eyes/joints placed without sculpted orbital socket or eyelid connection.",
        })

    if ("sweep(" in src or "cylinder" in src) and "hair" in src and "ribbon" not in src and "crescent" not in src:
        flags.append({
            "shortcut": "rope_tube_hair",
            "reason": "Hair constructed from uniform circular sweep/cylinder tubes rather than sculpted directional clumps.",
        })

    if "subsurf" in src and "sculpt" not in src and "bmesh" not in src and "vertices" not in src:
        flags.append({
            "shortcut": "subsurf_only_polish",
            "reason": "Relies solely on Subdivision Surface modifier without structural geometry sculpting.",
        })

    if scene_audit:
        mesh_count = int(scene_audit.get("mesh_objects") or 0)
        if mesh_count > 60:
            flags.append({
                "shortcut": "excessive_primitive_fragmentation",
                "reason": f"Character split into {mesh_count} fragmented primitive objects (> 60 threshold).",
            })

    return {
        "clean": len(flags) == 0,
        "shortcuts_detected": flags,
    }
