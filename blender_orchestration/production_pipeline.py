"""11-stage character production pipeline with hard QA gate blocking and 6-layer verdicts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import time
from typing import Any, Callable

from .qa_profiles import QAProfile, get_qa_profile
from .visual_qa import RepairLoopPolicy

PRODUCTION_STAGES: tuple[dict[str, Any], ...] = (
    {
        "stage_number": 1,
        "stage_id": "production_brief_and_identity",
        "title": "Production Brief & Character Identity",
        "inputs": ["prompt", "preflight_policy", "approved_references_if_guided"],
        "outputs": ["production_brief", "qa_profile", "reference_analysis_if_guided"],
        "required_skills": ["blender-director", "character-archetypes", "stylized-style"],
    },
    {
        "stage_number": 2,
        "stage_id": "silhouette_and_proportion_blockout",
        "title": "Overall Proportions & Silhouette Blockout",
        "inputs": ["production_brief", "clean_blender_scene"],
        "outputs": ["SM_Char_Body", "SM_Char_Head", "SM_Char_Hands", "proportion_metrics"],
        "required_skills": ["character-artist"],
    },
    {
        "stage_number": 3,
        "stage_id": "face_body_costume_modeling",
        "title": "Face, Body, Hands & Costume Modeling",
        "inputs": ["blockout_meshes", "existing_scene_state"],
        "outputs": ["refined_face_eyes", "articulated_hands_grip", "fitted_costume_and_boots"],
        "required_skills": ["character-artist"],
    },
    {
        "stage_number": 4,
        "stage_id": "hair_and_detail_sculpting",
        "title": "Hair Groom & Form-Refinement Sculpting",
        "inputs": ["refined_head_and_costume"],
        "outputs": ["SM_Char_Hair_Scalp", "SM_Char_Hair_Clumps", "SM_Char_Hair_Braid", "sculpt_before_after_metrics"],
        "required_skills": ["hair-groom", "sculpting"],
    },
    {
        "stage_number": 5,
        "stage_id": "modeling_visual_qa",
        "title": "Modeling Visual QA & Bounded Repair Loop (Gate A)",
        "inputs": ["modeled_character_scene", "qa_profile", "repair_loop_policy"],
        "outputs": ["multi_angle_renders", "visual_review_evidence", "repair_iterations", "gate_a_verdict"],
        "required_skills": ["rendering", "qa-review"],
    },
    {
        "stage_number": 6,
        "stage_id": "topology_uv_material_validation",
        "title": "Topology, UV & Material Validation",
        "inputs": ["gate_a_passed_character_scene"],
        "outputs": ["topology_audit", "uv_unwrap_report", "MAT_shaders_and_textures"],
        "required_skills": ["retopology", "uv-workflow", "materials", "texture-workflow"],
    },
    {
        "stage_number": 7,
        "stage_id": "rigging_and_skin_weights",
        "title": "Rigging & Skin Weights",
        "inputs": ["validated_topology_and_uv_character"],
        "outputs": ["ARM_Character", "bone_hierarchy", "skin_weights"],
        "required_skills": ["rigging"],
    },
    {
        "stage_number": 8,
        "stage_id": "joint_deformation_qa",
        "title": "Joint Deformation Visual QA (Gate B)",
        "inputs": ["rigged_character", "test_poses"],
        "outputs": ["pose_renders", "gate_b_verdict"],
        "required_skills": ["qa-review", "rigging"],
    },
    {
        "stage_number": 9,
        "stage_id": "animation_production",
        "title": "Animation Production",
        "inputs": ["gate_b_passed_rigged_character"],
        "outputs": ["ACT_RunCycle", "animation_keyframes"],
        "required_skills": ["animation"],
    },
    {
        "stage_number": 10,
        "stage_id": "animation_visual_qa",
        "title": "Animation Visual QA (Gate C)",
        "inputs": ["animated_character", "playback_frames"],
        "outputs": ["playback_renders", "gate_c_verdict"],
        "required_skills": ["qa-review", "animation"],
    },
    {
        "stage_number": 11,
        "stage_id": "final_optimization_and_export",
        "title": "Final Optimization & Export Validation",
        "inputs": ["qa_approved_character"],
        "outputs": ["editable_blend", "exported_glb_or_fbx", "fresh_scene_reimport_report"],
        "required_skills": ["asset-optimization", "export-pipeline"],
    },
)


@dataclass
class StageExecutionResult:
    """Explicit inputs, outputs, contracts, and validation status for one production stage."""

    stage_number: int
    stage_id: str
    title: str
    inputs: list[str]
    outputs: list[str]
    required_skills: list[str]
    status: str  # "PASS" | "FAIL" | "BLOCKED" | "NOT_VERIFIED" | "SKIPPED_WITH_REASON"
    validation_result: dict[str, Any] = field(default_factory=dict)
    skill_contracts: list[dict[str, Any]] = field(default_factory=list)
    blocker_reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    duration_sec: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductionVerdictSummary:
    """6 independent verification layers separating technical execution from visual approval."""

    mcp_connection: str = "NOT_VERIFIED"
    blender_execution: str = "NOT_VERIFIED"
    skill_application: str = "NOT_VERIFIED"
    geometry_and_technical_validation: str = "NOT_VERIFIED"
    visual_quality_validation: str = "NOT_VERIFIED"
    final_asset_production: str = "NOT_VERIFIED"
    reasons: list[str] = field(default_factory=list)

    def compute_final_verdict(self) -> "ProductionVerdictSummary":
        """Derive final_asset_production strictly without letting technical passes mask visual failures."""
        layers = (
            ("mcp_connection", self.mcp_connection),
            ("blender_execution", self.blender_execution),
            ("skill_application", self.skill_application),
            ("geometry_and_technical_validation", self.geometry_and_technical_validation),
            ("visual_quality_validation", self.visual_quality_validation),
        )
        reasons = list(self.reasons)
        failed = [name for name, st in layers if st == "FAIL"]
        blocked = [name for name, st in layers if st == "BLOCKED"]
        unverified = [name for name, st in layers if st not in ("PASS", "FAIL", "BLOCKED")]

        if failed:
            self.final_asset_production = "FAIL"
            reasons.append(f"Final production FAIL due to failed layer(s): {', '.join(failed)}")
        elif blocked:
            self.final_asset_production = "BLOCKED"
            reasons.append(f"Final production BLOCKED due to blocked layer(s): {', '.join(blocked)}")
        elif unverified:
            self.final_asset_production = "NOT_VERIFIED"
            reasons.append(f"Final production NOT_VERIFIED due to unverified layer(s): {', '.join(unverified)}")
        else:
            self.final_asset_production = "PASS"
        self.reasons = reasons
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


StageHandler = Callable[[dict[str, Any]], dict[str, Any]]


class CharacterProductionPipeline:
    """Executes the 11-stage character pipeline while blocking downstream stages on QA failures."""

    def __init__(
        self,
        preflight: dict[str, Any],
        *,
        asset_type: str = "character",
        animation_mode: str = "static",
        qa_profile: QAProfile | None = None,
        repair_policy: RepairLoopPolicy | None = None,
        allow_technical_stage_demo_when_unverified: bool = False,
    ) -> None:
        self.preflight = dict(preflight)
        self.asset_type = asset_type
        self.animation_mode = animation_mode
        self.qa_profile = qa_profile or get_qa_profile(
            asset_type,
            animated=(animation_mode == "animated"),
            reference_mode=str(preflight.get("benchmark_mode") or "TEXT_ONLY"),
        )
        self.repair_policy = repair_policy or RepairLoopPolicy()
        self.allow_technical_stage_demo_when_unverified = allow_technical_stage_demo_when_unverified

    def _blocked_stage(self, spec: dict[str, Any], reason: str) -> StageExecutionResult:
        return StageExecutionResult(
            stage_number=int(spec["stage_number"]),
            stage_id=str(spec["stage_id"]),
            title=str(spec["title"]),
            inputs=list(spec["inputs"]),
            outputs=[],
            required_skills=list(spec["required_skills"]),
            status="BLOCKED",
            validation_result={"status": "BLOCKED", "reason": reason},
            blocker_reasons=[reason],
        )

    def _skipped_stage(self, spec: dict[str, Any], reason: str) -> StageExecutionResult:
        return StageExecutionResult(
            stage_number=int(spec["stage_number"]),
            stage_id=str(spec["stage_id"]),
            title=str(spec["title"]),
            inputs=list(spec["inputs"]),
            outputs=[],
            required_skills=list(spec["required_skills"]),
            status="SKIPPED_WITH_REASON",
            validation_result={"status": "SKIPPED_WITH_REASON", "reason": reason},
            blocker_reasons=[reason],
        )

    def run(self, handlers: dict[str, StageHandler]) -> dict[str, Any]:
        """Run stages 1..11 sequentially, enforcing Gate A/B/C hard blockers and scene continuity."""
        results: list[StageExecutionResult] = []
        context: dict[str, Any] = {
            "preflight": self.preflight,
            "qa_profile": self.qa_profile,
            "repair_policy": self.repair_policy,
            "stage_outputs": {},
        }

        if self.preflight.get("status") == "BLOCKED" or not self.preflight.get("modeling_permitted"):
            blocker = str(self.preflight.get("blocker_reason") or "Preflight blocked modeling.")
            for spec in PRODUCTION_STAGES:
                results.append(self._blocked_stage(spec, blocker))
            verdict = ProductionVerdictSummary(
                mcp_connection="NOT_VERIFIED",
                blender_execution="BLOCKED",
                skill_application="BLOCKED",
                geometry_and_technical_validation="BLOCKED",
                visual_quality_validation="BLOCKED",
            ).compute_final_verdict()
            return {
                "stages": [r.to_dict() for r in results],
                "verdicts": verdict.to_dict(),
                "scene_continuity_preserved": True,
                "blocked_at_stage": 1,
            }

        gate_a_status = "NOT_VERIFIED"
        gate_b_status = "NOT_VERIFIED"
        gate_c_status = "NOT_VERIFIED"
        modeling_wiped = False
        prev_scene_objects: set[str] | None = None

        for spec in PRODUCTION_STAGES:
            s_num = int(spec["stage_number"])
            s_id = str(spec["stage_id"])

            # Hard Gate A blocker: Stages 6..11 cannot run when Modeling Visual QA fails or is unverified
            # (unless explicitly running a technical-only rig/anim demo where visual QA stays NOT_VERIFIED).
            if s_num >= 6:
                gate_a_blocks = gate_a_status == "FAIL" or (
                    gate_a_status != "PASS" and not self.allow_technical_stage_demo_when_unverified
                )
                if gate_a_blocks:
                    results.append(
                        self._blocked_stage(
                            spec,
                            f"Blocked by Stage 5 (modeling_visual_qa) status={gate_a_status}. Downstream production halted.",
                        )
                    )
                    continue

            # Skip rigging/animation stages cleanly when static asset
            if s_num in (7, 8, 9, 10) and self.animation_mode == "static":
                results.append(self._skipped_stage(spec, "Static character scope: rigging/animation not requested."))
                continue
            if s_num in (9, 10) and self.animation_mode == "rigged":
                results.append(self._skipped_stage(spec, "Rigged-only scope: keyframe animation not requested."))
                continue

            # Gate B blocker before Stages 9..11 (for rigged/animated assets)
            if s_num >= 9 and self.animation_mode in ("rigged", "animated"):
                gate_b_blocks = gate_b_status == "FAIL" or (
                    gate_b_status != "PASS" and not self.allow_technical_stage_demo_when_unverified
                )
                if gate_b_blocks:
                    results.append(
                        self._blocked_stage(
                            spec,
                            f"Blocked by Stage 8 (joint_deformation_qa) status={gate_b_status}.",
                        )
                    )
                    continue

            # Gate C blocker before Stage 11 (for animated assets)
            if s_num == 11 and self.animation_mode == "animated":
                gate_c_blocks = gate_c_status == "FAIL" or (
                    gate_c_status != "PASS" and not self.allow_technical_stage_demo_when_unverified
                )
                if gate_c_blocks:
                    results.append(
                        self._blocked_stage(
                            spec,
                            f"Blocked by Stage 10 (animation_visual_qa) status={gate_c_status}.",
                        )
                    )
                    continue

            handler = handlers.get(s_id)
            if handler is None:
                res = StageExecutionResult(
                    stage_number=s_num,
                    stage_id=s_id,
                    title=str(spec["title"]),
                    inputs=list(spec["inputs"]),
                    outputs=[],
                    required_skills=list(spec["required_skills"]),
                    status="NOT_VERIFIED",
                    validation_result={"status": "NOT_VERIFIED", "reason": f"No handler supplied for {s_id}."},
                    blocker_reasons=[f"Missing stage handler: {s_id}"],
                )
                results.append(res)
                continue

            t0 = time.monotonic()
            out = dict(handler(context) or {})
            dur = round(time.monotonic() - t0, 3)

            status = str(out.get("status") or "NOT_VERIFIED")
            evidence = dict(out.get("evidence") or {})
            context["stage_outputs"][s_id] = out

            # Track incremental scene continuity across Stages 2..5
            if s_num in (2, 3, 4, 5) and "scene_objects" in evidence:
                curr_objs = set(evidence["scene_objects"] or [])
                if prev_scene_objects and prev_scene_objects.isdisjoint(curr_objs):
                    modeling_wiped = True
                prev_scene_objects = curr_objs

            if s_id == "modeling_visual_qa":
                gate_a_status = status
            elif s_id == "joint_deformation_qa":
                gate_b_status = status
            elif s_id == "animation_visual_qa":
                gate_c_status = status

            results.append(
                StageExecutionResult(
                    stage_number=s_num,
                    stage_id=s_id,
                    title=str(spec["title"]),
                    inputs=list(spec["inputs"]),
                    outputs=list(out.get("outputs") or spec["outputs"]),
                    required_skills=list(spec["required_skills"]),
                    status=status,
                    validation_result=dict(out.get("validation_result") or {"status": status}),
                    skill_contracts=list(out.get("skill_contracts") or []),
                    blocker_reasons=list(out.get("blocker_reasons") or []),
                    evidence=evidence,
                    duration_sec=dur,
                )
            )

        # Compute 6 independent verdicts
        verdict = ProductionVerdictSummary()
        if any(r.evidence.get("mcp_connected") is False for r in results):
            verdict.mcp_connection = "FAIL"
        elif any(r.evidence.get("mcp_connected") is True for r in results):
            verdict.mcp_connection = "PASS"

        exec_stages = [r for r in results if r.stage_number in (2, 3, 4, 6, 11) and r.status != "SKIPPED_WITH_REASON"]
        if any(r.evidence.get("blender_exec_ok") is False for r in results):
            verdict.blender_execution = "FAIL"
        elif exec_stages and all(r.evidence.get("blender_exec_ok") is True for r in exec_stages if r.status not in ("BLOCKED",)):
            verdict.blender_execution = "PASS"

        all_contracts = [c for r in results for c in r.skill_contracts]
        if all_contracts:
            c_statuses = [c.get("status") for c in all_contracts]
            if any(st == "FAILED" for st in c_statuses):
                verdict.skill_application = "FAIL"
            elif all(st in ("APPLIED", "SKIPPED_WITH_REASON") for st in c_statuses):
                verdict.skill_application = "PASS"
            else:
                verdict.skill_application = "NOT_VERIFIED"

        tech_checks = [r.evidence.get("technical_passed") for r in results if "technical_passed" in r.evidence]
        if any(t is False for t in tech_checks):
            verdict.geometry_and_technical_validation = "FAIL"
        elif tech_checks and all(t is True for t in tech_checks):
            verdict.geometry_and_technical_validation = "PASS"

        qa_statuses = [gate_a_status]
        if self.animation_mode in ("rigged", "animated"):
            qa_statuses.append(gate_b_status)
        if self.animation_mode == "animated":
            qa_statuses.append(gate_c_status)
        if any(s == "FAIL" for s in qa_statuses):
            verdict.visual_quality_validation = "FAIL"
        elif all(s == "PASS" for s in qa_statuses):
            verdict.visual_quality_validation = "PASS"
        else:
            verdict.visual_quality_validation = "NOT_VERIFIED"

        verdict.compute_final_verdict()

        return {
            "stages": [r.to_dict() for r in results],
            "verdicts": verdict.to_dict(),
            "gate_statuses": {
                "gate_a": gate_a_status,
                "gate_b": gate_b_status,
                "gate_c": gate_c_status,
            },
            "scene_continuity_preserved": not modeling_wiped,
            "qa_profile_id": self.qa_profile.profile_id,
        }
