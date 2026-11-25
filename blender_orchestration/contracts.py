"""Skill execution contracts and evidence validators.

Separates Skill loading (`loaded=True`) from actual workflow completion
(`status="APPLIED"`). Prevents superficial proxies such as Subdivision Surface
counting as sculpting, simple decimation counting as hero retopology, or base
color assignment counting as texture baking.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .visual_qa import verify_visual_review_evidence

SKILL_STATUSES = (
    "PENDING",
    "IN_PROGRESS",
    "APPLIED",
    "SKIPPED_WITH_REASON",
    "FAILED",
    "NOT_VERIFIED",
)

SKILL_LIFECYCLE_STATES = (
    "DISCOVERED",
    "LOADED",
    "APPLIED",
    "VALIDATED",
    "FAILED",
    "NOT_VERIFIED",
)


@dataclass
class SkillExecutionContract:
    """Tracks required operations, performed operations, and real MCP/scene evidence."""

    skill_id: str
    production_stage: str
    selection_reason: str
    loaded: bool = False
    required_operations: list[str] = field(default_factory=list)
    performed_operations: list[str] = field(default_factory=list)
    mcp_evidence: list[str] = field(default_factory=list)
    scene_evidence: list[str] = field(default_factory=list)
    validation_evidence: list[str] = field(default_factory=list)
    status: str = "PENDING"
    evidence_details: dict[str, Any] = field(default_factory=dict)
    evaluation_notes: list[str] = field(default_factory=list)
    changed_objects: list[str] = field(default_factory=list)
    evidence_files: dict[str, str] = field(default_factory=dict)
    verification_result: str = "NOT_VERIFIED"
    skill_md_sha256: str | None = None
    lifecycle_state: str = "DISCOVERED"
    lifecycle_history: list[str] = field(default_factory=lambda: ["DISCOVERED"])

    def record_lifecycle(self, state: str) -> None:
        if state not in SKILL_LIFECYCLE_STATES:
            raise ValueError(f"Unsupported skill lifecycle state: {state}")
        self.lifecycle_state = state
        if not self.lifecycle_history or self.lifecycle_history[-1] != state:
            self.lifecycle_history.append(state)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_contract_from_plan_item(item: dict[str, Any]) -> SkillExecutionContract:
    """Create an initial PENDING SkillExecutionContract from a BlenderDirector plan item."""
    return SkillExecutionContract(
        skill_id=str(item["skill_id"]),
        production_stage=str(item["production_stage"]),
        selection_reason=str(item["selection_reason"]),
        loaded=False,
        required_operations=list(item.get("required_operations") or []),
        performed_operations=[],
        mcp_evidence=[],
        scene_evidence=[],
        validation_evidence=[],
        status="PENDING",
    )


def _has_real_evidence(contract: SkillExecutionContract) -> bool:
    return bool(contract.mcp_evidence or contract.scene_evidence) and bool(contract.validation_evidence)


def evaluate_skill_contract(
    contract: SkillExecutionContract,
    *,
    asset_tier: str = "hero",
) -> SkillExecutionContract:
    """Evaluate contract evidence and set status truthfully.

    Never marks APPLIED purely because `loaded=True`.
    """
    notes: list[str] = []
    details = contract.evidence_details or {}
    ops_lower = {op.lower() for op in contract.performed_operations}

    if contract.status == "SKIPPED_WITH_REASON":
        if not contract.evaluation_notes and not details.get("skip_reason"):
            contract.status = "NOT_VERIFIED"
            contract.verification_result = "NOT_VERIFIED"
            contract.evaluation_notes = ["SKIPPED_WITH_REASON requires an explicit reason."]
        else:
            contract.verification_result = "SKIPPED"
        return contract

    if contract.status == "FAILED":
        contract.verification_result = "FAIL"
        contract.record_lifecycle("FAILED")
        return contract

    if not contract.loaded:
        contract.status = "NOT_VERIFIED"
        contract.verification_result = "NOT_VERIFIED"
        contract.evaluation_notes = ["Skill SKILL.md was not loaded before execution evaluation."]
        contract.record_lifecycle("NOT_VERIFIED")
        return contract

    if not contract.performed_operations:
        contract.status = "NOT_VERIFIED"
        contract.verification_result = "NOT_VERIFIED"
        contract.evaluation_notes = ["Skill was loaded but zero workflow operations were performed (loading != APPLIED)."]
        contract.record_lifecycle("NOT_VERIFIED")
        return contract

    if not _has_real_evidence(contract):
        contract.status = "NOT_VERIFIED"
        contract.verification_result = "NOT_VERIFIED"
        contract.evaluation_notes = ["Missing real MCP/scene evidence or validation evidence."]
        contract.record_lifecycle("NOT_VERIFIED")
        return contract

    if details.get("ai_3d_generators_used") or details.get("premade_assets_used"):
        contract.status = "FAILED"
        contract.verification_result = "FAIL"
        contract.evaluation_notes = ["Blender-Only Policy violation: external AI 3D generators or unauthorized premade assets used."]
        contract.record_lifecycle("FAILED")
        return contract

    sid = contract.skill_id

    if sid == "sculpting":
        subsurf_only = bool(details.get("subsurf_only")) or (
            ops_lower <= {"subdivision_surface", "subsurf", "add_subsurf"}
        )
        polygon_inflation_only = bool(details.get("polygon_inflation_only"))
        sculpt_ops = {"multires_sculpt", "dyntopo_sculpt", "sculpt_stroke", "form_refinement_pass", "sculpt_or_form_refinement"}
        has_sculpt_op = bool(ops_lower & sculpt_ops) or bool(details.get("sculpt_mode_used"))
        has_before_after = bool(details.get("before_after_geometry"))
        if subsurf_only or not has_sculpt_op:
            notes.append("Sculpting requires real sculpt/form-refinement operations; Subdivision Surface alone is rejected.")
            contract.status = "NOT_VERIFIED"
        elif polygon_inflation_only:
            notes.append("Sculpting rejected: polygon count increased without form/defect improvement.")
            contract.status = "NOT_VERIFIED"
        elif not has_before_after:
            notes.append("Sculpting requires before/after geometry metrics and form validation.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    elif sid == "retopology":
        decimate_only = bool(details.get("decimate_only")) or (ops_lower <= {"decimate", "decimate_modifier"})
        primitive_only = bool(details.get("primitive_only")) or bool(details.get("raw_primitive_stack"))
        quad_ratio = details.get("quad_ratio")
        has_joint_loops = bool(details.get("joint_edge_loops_verified", False))
        has_topo_validation = bool(details.get("topology_validated", False))
        tris = details.get("triangles")
        tri_budget = details.get("triangle_budget")
        if decimate_only or primitive_only:
            notes.append("Simple decimation or raw primitive generation alone is not accepted as hero-character retopology.")
            contract.status = "NOT_VERIFIED"
        elif asset_tier == "hero" and (quad_ratio is None or float(quad_ratio) < 0.70):
            notes.append("Hero retopology requires verified quad-dominant topology (quad_ratio >= 0.70).")
            contract.status = "NOT_VERIFIED"
        elif asset_tier == "hero" and not has_joint_loops:
            notes.append("Hero retopology requires verified edge loops supporting deforming joints.")
            contract.status = "NOT_VERIFIED"
        elif not has_topo_validation:
            notes.append("Retopology requires explicit triangle count and manifold/topology validation.")
            contract.status = "NOT_VERIFIED"
        elif tris is not None and tri_budget is not None and int(tris) > int(tri_budget):
            notes.append(f"Retopology triangle count ({tris}) exceeds budget ({tri_budget}).")
            contract.status = "FAILED"
        else:
            contract.status = "APPLIED"

    elif sid == "character-artist":
        needed = {"proportion_spec", "body_blockout", "facial_structure", "topology_inspection"}
        missing = sorted(needed - ops_lower)
        if missing:
            notes.append(f"Character artist missing required verified stages: {', '.join(missing)}.")
            contract.status = "NOT_VERIFIED"
        elif details.get("primitive_stacking_only"):
            notes.append("Character artist rejected: raw stacked primitives without cohesive anatomical form.")
            contract.status = "NOT_VERIFIED"
        elif (
            details.get("facial_defects_unresolved")
            or details.get("hand_grip_defects_unresolved")
            or details.get("costume_fit_defects_unresolved")
        ):
            notes.append("Character artist failed visual quality checks (unresolved facial, hand/grip, or costume-fit defects).")
            contract.status = "FAILED"
        else:
            contract.status = "APPLIED"

    elif sid == "hair-groom":
        rope_only = bool(details.get("rope_or_tube_only"))
        scalp_exposed = bool(details.get("scalp_exposure_detected"))
        scalp_ok = bool(details.get("scalp_coverage_verified", False))
        clump_ok = bool(details.get("clump_volume_verified", False))
        pen_checked = bool(details.get("penetration_checked", False))
        if rope_only:
            notes.append("Hair groom rejected: uniform cylindrical ropes/tubes without sculpted hair clump volume.")
            contract.status = "NOT_VERIFIED"
        elif scalp_exposed:
            notes.append("Hair groom failed: scalp exposure / bald patch detected behind hairline or braid.")
            contract.status = "FAILED"
        elif not (scalp_ok and clump_ok and pen_checked):
            notes.append("Hair groom requires verified scalp coverage, clump volume/flow, and face/shoulder penetration check.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    elif sid == "uv-workflow":
        has_uv_layer = bool(details.get("uv_layer_count", 0) >= 1)
        unwrapped = bool(details.get("unwrapped", "uv_unwrap" in ops_lower))
        packed = bool(details.get("packing_checked", "seam_and_pack_check" in ops_lower))
        if not (has_uv_layer and unwrapped and packed):
            notes.append("UV workflow requires created UV layer, unwrap operation, and island/packing inspection.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    elif sid == "texture-workflow":
        base_color_only = bool(details.get("base_color_only"))
        has_textures = bool(details.get("texture_files") or details.get("image_texture_nodes", 0) > 0)
        if base_color_only or not has_textures:
            notes.append("Texture workflow requires actual texture files or image texture nodes; base color alone cannot claim texture baking.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    elif sid == "materials":
        mat_count = int(details.get("material_count", 0))
        mat_prefix_ok = bool(details.get("mat_prefix_verified", True))
        if mat_count < 1 or not mat_prefix_ok:
            notes.append("Materials stage requires assigned MAT_* materials verified in scene state.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    elif sid == "rigging":
        has_arm = bool(details.get("armature_name"))
        has_bones = int(details.get("bone_count", 0)) > 0
        has_weights = bool(details.get("weights_verified"))
        has_deform = bool(details.get("deformation_tested"))
        if details.get("deformation_defects_unresolved"):
            notes.append("Rigging failed joint deformation QA (unresolved pinching/clipping defects).")
            contract.status = "FAILED"
        elif not (has_arm and has_bones and has_weights and has_deform):
            notes.append("Rigging requires actual armature, bone hierarchy, mesh weights, and deformation test evidence.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    elif sid == "animation":
        has_action = bool(details.get("action_name"))
        kf_count = int(details.get("keyframe_count", 0))
        motion_verified = bool(details.get("motion_visually_verified"))
        if details.get("animation_defects_unresolved"):
            notes.append("Animation failed playback QA (unresolved foot contact, root drift, or clipping).")
            contract.status = "FAILED"
        elif not (has_action and kf_count > 0 and motion_verified):
            notes.append("Animation requires actual Action keyframes and rendered/viewport motion verification.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    elif sid == "qa-review":
        captures = list(details.get("captured_images") or [])
        visually_inspected = bool(details.get("visually_inspected"))
        findings = list(details.get("findings") or [])
        verdict = details.get("verdict")
        strict_ev = bool(details.get("strict_evidence")) or ("review_evidence" in details)
        if not captures:
            notes.append("QA review requires actual screenshot or rendered image captures.")
            contract.status = "NOT_VERIFIED"
        elif not visually_inspected:
            notes.append("Rendered file existing is not proof of visual inspection; explicit visual inspection is required.")
            contract.status = "NOT_VERIFIED"
        elif strict_ev:
            ev_check = verify_visual_review_evidence(
                details.get("review_evidence"),
                expected_policy_digest=details.get("expected_policy_digest"),
                verify_files_on_disk=bool(details.get("verify_files_on_disk", True)),
            )
            if not ev_check["valid"]:
                notes.extend(ev_check["reasons"])
                contract.status = ev_check["status_if_invalid"]
            elif not findings or verdict not in ("PASS", "FAIL"):
                notes.append("QA review requires explicit findings and PASS/FAIL verdict.")
                contract.status = "NOT_VERIFIED"
            else:
                contract.status = "APPLIED" if verdict == "PASS" else "FAILED"
        elif not findings or verdict not in ("PASS", "FAIL"):
            notes.append("QA review requires explicit findings and PASS/FAIL verdict.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED" if verdict == "PASS" else "FAILED"

    elif sid == "reference-image-match":
        refs = list(details.get("approved_references") or [])
        analysis_written = bool(details.get("reference_analysis_written"))
        camera_matched = bool(details.get("camera_correspondence_set"))
        blender_built = bool(details.get("built_in_blender", True)) and not bool(details.get("ai_3d_generators_used"))
        comp_renders = list(details.get("comparison_renders") or [])
        passes = int(details.get("comparison_passes", 0))
        visually_inspected = bool(details.get("visually_inspected"))
        if not refs or not analysis_written or not camera_matched or not blender_built:
            notes.append("Reference-guided production requires approved references, pre-modeling reference analysis, camera match, and Blender-only geometry.")
            contract.status = "NOT_VERIFIED"
        elif not comp_renders or passes < 1 or passes > 3 or not visually_inspected:
            notes.append("Reference-guided QA requires 1-3 visually inspected comparison renders against approved references.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    else:
        # Generic contract validator extensible to all 94 skills
        missing_req = [req for req in contract.required_operations if req.lower() not in ops_lower]
        if missing_req:
            notes.append(f"Missing required operations: {', '.join(missing_req)}.")
            contract.status = "NOT_VERIFIED"
        else:
            contract.status = "APPLIED"

    contract.evaluation_notes = notes
    if contract.loaded and "LOADED" not in contract.lifecycle_history:
        contract.record_lifecycle("LOADED")
    if contract.status == "APPLIED":
        contract.verification_result = "PASS"
        contract.record_lifecycle("APPLIED")
        if contract.validation_evidence:
            contract.record_lifecycle("VALIDATED")
    elif contract.status == "FAILED":
        contract.verification_result = "FAIL"
        contract.record_lifecycle("FAILED")
    elif contract.status == "SKIPPED_WITH_REASON":
        contract.verification_result = "SKIPPED"
    else:
        contract.verification_result = "NOT_VERIFIED"
        contract.record_lifecycle("NOT_VERIFIED")
    return contract
