"""Evidence-backed visual QA, anti-forging verification, and bounded Blender repair loop."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Callable

from .qa_profiles import QAProfile
from .repair_strategies import (
    RepairStrategyTier,
    select_strategy_for_defect,
    validate_repair_effectiveness,
)

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

DEFECT_SEVERITIES = ("BLOCKER", "MAJOR", "MINOR", "NOTE")


def hash_file_sha256(path: Path | str) -> str:
    """Compute SHA-256 hex digest of a file on disk."""
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest()


@dataclass
class VisualDefect:
    """Structured defect linking visual inspection evidence to a concrete Blender repair action."""

    defect_id: str
    defect_type: str
    severity: str
    affected_objects: list[str]
    source_render: str
    visual_problem: str
    required_fix_action: str
    before_after_evidence: dict[str, Any] = field(default_factory=dict)
    resolved: bool = False
    qa_criterion_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImageObservation:
    """Concrete visual inspection record bound to a rendered PNG SHA-256 hash."""

    image_path: str
    image_sha256: str
    view_angle: str
    observations: list[str]
    compared_reference_path: str | None = None
    compared_reference_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VisualReviewEvidence:
    """Anti-forging visual QA artifact binding reviewer identity, hashes, and policy digest."""

    reviewer_id: str
    reviewer_context_id: str
    producer_context_id: str
    reviewed_at: str
    target_asset_path: str
    target_asset_sha256: str
    profile_id: str
    reference_mode: str
    image_observations: list[ImageObservation]
    defects: list[VisualDefect] = field(default_factory=list)
    criteria_verdicts: dict[str, str] = field(default_factory=dict)
    approval_policy_digest: str = ""
    reference_score: tuple[int, int] | list[int] | None = None
    reference_comparison_performed: bool = False
    approved_references_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["image_observations"] = [
            obs.to_dict() if isinstance(obs, ImageObservation) else dict(obs)
            for obs in self.image_observations
        ]
        data["defects"] = [
            d.to_dict() if isinstance(d, VisualDefect) else dict(d)
            for d in self.defects
        ]
        return data


@dataclass
class BlenderFixAction:
    """Concrete localized Blender operation derived from a VisualDefect."""

    action_id: str
    defect_id: str
    defect_type: str
    target_objects: list[str]
    operation_type: str
    preserve_existing_mesh: bool = True
    parameters: dict[str, Any] = field(default_factory=dict)
    verification_views: list[str] = field(default_factory=list)
    expected_delta: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RepairLoopPolicy:
    """Benchmark-configurable policy controlling bounded visual QA fix iterations."""

    max_fix_iterations: int = 3
    max_wall_time_sec: float = 1800.0
    min_reference_match_ratio: float = 0.8
    forbid_stalled_repeat: bool = True
    escalate_strategy_on_repeat: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_approval_policy_digest(profile: QAProfile, policy: RepairLoopPolicy) -> str:
    """Compute immutable SHA-256 digest of approval thresholds so agents cannot weaken QA rules."""
    payload = {
        "profile_id": profile.profile_id,
        "reference_mode": profile.reference_mode,
        "required_criteria": [c.criterion_id for c in profile.criteria if c.required],
        "min_reference_match_score": profile.min_reference_match_score,
        "exact_likeness_required": profile.exact_likeness_required,
        "max_fix_iterations": policy.max_fix_iterations,
        "min_reference_match_ratio": policy.min_reference_match_ratio,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coerce_evidence_dict(evidence: VisualReviewEvidence | dict[str, Any]) -> dict[str, Any]:
    if isinstance(evidence, VisualReviewEvidence):
        return evidence.to_dict()
    return dict(evidence)


def verify_visual_review_evidence(
    evidence: VisualReviewEvidence | dict[str, Any] | None,
    *,
    expected_policy_digest: str | None = None,
    verify_files_on_disk: bool = True,
) -> dict[str, Any]:
    """Validate that visual QA evidence is real, independent, hashed, and untampered.

    Prevents spoofing PASS via bare booleans like `visually_inspected=True`.
    """
    if not evidence:
        return {
            "valid": False,
            "status_if_invalid": "NOT_VERIFIED",
            "reasons": ["Missing VisualReviewEvidence artifact; bare boolean flags cannot pass QA."],
        }

    data = _coerce_evidence_dict(evidence)
    reasons: list[str] = []

    reviewer_id = str(data.get("reviewer_id") or "").strip()
    reviewer_ctx = str(data.get("reviewer_context_id") or "").strip()
    producer_ctx = str(data.get("producer_context_id") or "").strip()
    reviewed_at = str(data.get("reviewed_at") or "").strip()
    asset_hash = str(data.get("target_asset_sha256") or "").strip().lower()
    policy_digest = str(data.get("approval_policy_digest") or "").strip().lower()
    ref_mode = str(data.get("reference_mode") or "TEXT_ONLY")

    if not reviewer_id:
        reasons.append("reviewer_id is required.")
    if not reviewer_ctx:
        reasons.append("reviewer_context_id is required.")
    if not producer_ctx:
        reasons.append("producer_context_id is required.")
    if reviewer_ctx and producer_ctx and reviewer_ctx == producer_ctx:
        reasons.append("Independent visual QA violated: reviewer_context_id matches producer_context_id (self-approval).")
    if not reviewed_at:
        reasons.append("reviewed_at timestamp is required.")
    if not _SHA256_HEX_RE.match(asset_hash):
        reasons.append("target_asset_sha256 must be a valid 64-character SHA-256 hex digest.")

    status_if_invalid = "NOT_VERIFIED"
    if expected_policy_digest is not None:
        if not policy_digest:
            reasons.append("approval_policy_digest is missing.")
        elif policy_digest != expected_policy_digest.lower():
            reasons.append("approval_policy_digest mismatch: final approval thresholds cannot be altered by producer.")
            status_if_invalid = "FAIL"

    observations = list(data.get("image_observations") or [])
    if not observations:
        reasons.append("At least one rendered image observation with image_sha256 is required.")

    ref_compared_obs = False
    for idx, raw_obs in enumerate(observations):
        obs = raw_obs.to_dict() if isinstance(raw_obs, ImageObservation) else dict(raw_obs)
        img_path_str = str(obs.get("image_path") or "").strip()
        img_hash = str(obs.get("image_sha256") or "").strip().lower()
        obs_notes = [str(x).strip() for x in (obs.get("observations") or []) if str(x).strip()]

        if not img_path_str:
            reasons.append(f"image_observations[{idx}] missing image_path.")
        if not _SHA256_HEX_RE.match(img_hash):
            reasons.append(f"image_observations[{idx}] has invalid image_sha256.")
        if not obs_notes:
            reasons.append(f"image_observations[{idx}] must include non-empty visual inspection observations.")

        if verify_files_on_disk and img_path_str:
            img_path = Path(img_path_str)
            if not img_path.is_file():
                reasons.append(f"Rendered image file does not exist on disk: {img_path_str}")
            elif _SHA256_HEX_RE.match(img_hash):
                actual = hash_file_sha256(img_path)
                if actual != img_hash:
                    reasons.append(f"SHA-256 mismatch for rendered image {img_path_str} (expected {img_hash}, got {actual}).")

        comp_ref_path = obs.get("compared_reference_path")
        comp_ref_hash = str(obs.get("compared_reference_sha256") or "").strip().lower()
        if comp_ref_path:
            ref_compared_obs = True
            if not _SHA256_HEX_RE.match(comp_ref_hash):
                reasons.append(f"image_observations[{idx}] compared_reference_sha256 must be a valid SHA-256 hex digest.")
            elif verify_files_on_disk:
                rp = Path(str(comp_ref_path))
                if not rp.is_file():
                    reasons.append(f"Compared reference image not found on disk: {comp_ref_path}")
                elif hash_file_sha256(rp) != comp_ref_hash:
                    reasons.append(f"SHA-256 mismatch for compared reference image {comp_ref_path}.")

    if ref_mode == "REFERENCE_GUIDED":
        if not data.get("approved_references_used"):
            reasons.append("REFERENCE_GUIDED evidence must confirm approved_references_used=True.")
        if not data.get("reference_comparison_performed") or not ref_compared_obs:
            reasons.append("REFERENCE_GUIDED evidence requires at least one image observation compared against a hashed approved reference.")

    if verify_files_on_disk:
        asset_path_str = str(data.get("target_asset_path") or "").strip()
        if asset_path_str:
            asset_path = Path(asset_path_str)
            if not asset_path.is_file():
                reasons.append(f"Target asset file missing on disk: {asset_path_str}")
            elif _SHA256_HEX_RE.match(asset_hash):
                actual_asset = hash_file_sha256(asset_path)
                if actual_asset != asset_hash:
                    reasons.append(f"Target asset SHA-256 mismatch for {asset_path_str}.")

    return {
        "valid": len(reasons) == 0,
        "status_if_invalid": "VALID" if not reasons else status_if_invalid,
        "reasons": reasons,
    }


_DEFECT_TO_FIX_SPEC: dict[str, dict[str, Any]] = {
    "scalp_exposure": {
        "operation_type": "close_scalp_crown_coverage",
        "default_targets": ["SM_Char_Hair_Scalp", "SM_Char_Hair_Braid"],
        "parameters": {"crown_expand_m": 0.008, "back_drop_m": 0.014, "cover_braid_gap": True},
        "verification_views": ["back", "three_quarter", "face_closeup"],
        "expected_delta": "Zero skin exposure behind braid or crown hairline.",
    },
    "round_protruding_eyes": {
        "operation_type": "recess_eyes_and_contour_eyelids",
        "default_targets": ["SM_Char_Head", "SM_Char_Eyes"],
        "parameters": {"recess_depth_m": 0.006, "almond_aspect": 0.62, "add_eyelid_fold": True},
        "verification_views": ["face_closeup", "side", "front"],
        "expected_delta": "Recessed orbital sockets, almond contour, natural eyelid wrap.",
    },
    "facial_proportion_error": {
        "operation_type": "sculpt_facial_planes_and_jaw",
        "default_targets": ["SM_Char_Head"],
        "parameters": {"cheek_plane_refine": True, "jaw_taper_factor": 0.92, "brow_ridge_define": True},
        "verification_views": ["face_closeup", "front", "side"],
        "expected_delta": "Clear brow/cheek/nose/chin planes and natural profile.",
    },
    "rope_like_hair": {
        "operation_type": "sculpt_tapered_hair_clumps",
        "default_targets": ["SM_Char_Hair_Clumps"],
        "parameters": {"ribbon_flatten_ratio": 0.65, "tip_taper_ratio": 0.18, "directional_flow": True},
        "verification_views": ["three_quarter", "back", "side"],
        "expected_delta": "Cohesive sculpted tapered hair masses instead of uniform cylindrical tubes.",
    },
    "hair_face_penetration": {
        "operation_type": "resolve_hair_body_penetration",
        "default_targets": ["SM_Char_Hair_Clumps"],
        "parameters": {"clearance_offset_m": 0.005},
        "verification_views": ["three_quarter", "face_closeup", "side"],
        "expected_delta": "Hair locks clear face, ears, and shoulders without clipping.",
    },
    "stiff_fingers_bad_grip": {
        "operation_type": "curl_fingers_and_seat_palm_grip",
        "default_targets": ["SM_Char_Hands", "SM_Char_HeldSlate"],
        "parameters": {"curl_angle_deg": 34.0, "palm_contact_snap": True},
        "verification_views": ["hands_closeup", "three_quarter"],
        "expected_delta": "Connected palm/knuckles and wrapped finger grip on held item.",
    },
    "bulbous_boots": {
        "operation_type": "contour_boot_ankle_instep_heel",
        "default_targets": ["SM_Char_Boots"],
        "parameters": {"ankle_pinch_ratio": 0.78, "flat_sole": True, "define_heel_and_collar": True},
        "verification_views": ["boots_closeup", "side", "three_quarter"],
        "expected_delta": "Articulated boot sole, heel, instep arch, and fitted ankle collar.",
    },
    "floating_thigh_seam": {
        "operation_type": "snap_costume_trim_to_surface",
        "default_targets": ["SM_Char_Costume_Trim", "SM_Char_Trousers"],
        "parameters": {"surface_offset_m": 0.0025},
        "verification_views": ["three_quarter", "side", "boots_closeup"],
        "expected_delta": "Costume seams and trims seat flush along leg/thigh surface.",
    },
    "stiff_shoulders_knees": {
        "operation_type": "soften_joint_silhouette_and_add_loops",
        "default_targets": ["SM_Char_Body", "SM_Char_Sleeves", "SM_Char_Trousers"],
        "parameters": {"shoulder_slope_deg": 12.0, "joint_loop_relax": True},
        "verification_views": ["front", "three_quarter", "side"],
        "expected_delta": "Natural shoulder slope, elbow/knee crease inflection, and joint loops.",
    },
    "triangle_budget_overrun": {
        "operation_type": "decimate_non_deforming_decor_within_budget",
        "default_targets": ["COL_Export"],
        "parameters": {"preserve_face_and_joints": True, "target_reduction": "budget"},
        "verification_views": ["three_quarter", "front"],
        "expected_delta": "Total triangles within budget while preserving face/joint form.",
    },
    "primitive_stacking": {
        "operation_type": "merge_and_retopologize_subparts",
        "default_targets": ["COL_Export"],
        "parameters": {"merge_by_region": True, "preserve_existing_silhouette": True},
        "verification_views": ["three_quarter", "front"],
        "expected_delta": "Cohesive multi-region meshes replacing excessive discrete primitives.",
    },
    "reference_match_gap": {
        "operation_type": "align_silhouette_and_palette_to_reference",
        "default_targets": ["SM_Char_Body", "SM_Char_Costume", "SM_Char_Hair_Clumps"],
        "parameters": {"adjust_silhouette_and_albedo": True},
        "verification_views": ["reference_matched", "three_quarter"],
        "expected_delta": "Improved reference match >= 8/10 categories.",
    },
}


def plan_blender_fixes_for_defects(
    defects: list[VisualDefect | dict[str, Any]],
    *,
    attempt_index: int = 0,
) -> list[BlenderFixAction]:
    """Map open visual/technical defects onto localized BlenderFixAction objects."""
    actions: list[BlenderFixAction] = []
    for idx, raw in enumerate(defects, start=1):
        d = raw if isinstance(raw, VisualDefect) else VisualDefect(**raw)
        if d.resolved or d.severity == "NOTE":
            continue
        tier = select_strategy_for_defect(d.defect_type, attempt_index=attempt_index)
        spec = _DEFECT_TO_FIX_SPEC.get(
            d.defect_type,
            {
                "operation_type": "localized_mesh_and_material_refinement",
                "default_targets": d.affected_objects or ["SM_Char_Body"],
                "parameters": {"defect_type": d.defect_type},
                "verification_views": ["three_quarter", "front"],
                "expected_delta": d.required_fix_action or "Resolve visual defect.",
            },
        )
        op_type = str(spec["operation_type"])
        if tier == RepairStrategyTier.ROLLBACK_TO_BLOCKOUT and attempt_index > 0:
            op_type = f"rollback_and_reblock_{op_type}"
        targets = list(d.affected_objects) if d.affected_objects else list(spec["default_targets"])
        params = dict(spec["parameters"])
        params["strategy_tier"] = tier
        actions.append(
            BlenderFixAction(
                action_id=f"FIX-{idx:03d}-{d.defect_id}",
                defect_id=d.defect_id,
                defect_type=d.defect_type,
                target_objects=targets,
                operation_type=op_type,
                preserve_existing_mesh=(tier != RepairStrategyTier.ROLLBACK_TO_BLOCKOUT),
                parameters=params,
                verification_views=list(spec["verification_views"]),
                expected_delta=str(spec["expected_delta"]),
            )
        )
    return actions


def _review_meets_approval(
    evidence_dict: dict[str, Any],
    profile: QAProfile,
    policy: RepairLoopPolicy,
) -> tuple[bool, list[str]]:
    """Determine whether verified review evidence satisfies all profile criteria and thresholds."""
    reasons: list[str] = []
    defects = [
        d if isinstance(d, VisualDefect) else VisualDefect(**d)
        for d in (evidence_dict.get("defects") or [])
    ]
    open_blocking = [d for d in defects if not d.resolved and d.severity in ("BLOCKER", "MAJOR")]
    if open_blocking:
        reasons.append(
            f"{len(open_blocking)} unresolved BLOCKER/MAJOR defect(s): "
            + ", ".join(f"{d.defect_id}({d.defect_type})" for d in open_blocking)
        )

    verdicts = dict(evidence_dict.get("criteria_verdicts") or {})
    failed_criteria = [cid for cid, v in verdicts.items() if v == "FAIL"]
    if failed_criteria:
        reasons.append(f"Failed QA criteria: {', '.join(failed_criteria)}")

    missing_required = [
        c.criterion_id
        for c in profile.criteria
        if c.required and verdicts.get(c.criterion_id) not in ("PASS", "FAIL")
    ]
    if missing_required:
        reasons.append(f"Missing required criterion verdicts: {', '.join(missing_required[:6])}")

    if profile.reference_mode == "REFERENCE_GUIDED":
        ref_score = evidence_dict.get("reference_score")
        if not ref_score or len(ref_score) != 2 or int(ref_score[1]) <= 0:
            reasons.append("REFERENCE_GUIDED review must provide reference_score [matched, total].")
        else:
            ratio = float(ref_score[0]) / float(ref_score[1])
            threshold = profile.min_reference_match_score or policy.min_reference_match_ratio
            if ratio < threshold:
                reasons.append(f"Reference match score {ref_score[0]}/{ref_score[1]} ({ratio:.2f}) < threshold ({threshold:.2f}).")

    return len(reasons) == 0, reasons


class VisualQARepairLoop:
    """Bounded visual inspection -> localized Blender repair -> before/after comparison loop."""

    def __init__(
        self,
        profile: QAProfile,
        policy: RepairLoopPolicy | None = None,
        *,
        verify_files_on_disk: bool = True,
    ) -> None:
        self.profile = profile
        self.policy = policy or RepairLoopPolicy()
        self.verify_files_on_disk = verify_files_on_disk
        self.policy_digest = compute_approval_policy_digest(self.profile, self.policy)

    def run(
        self,
        *,
        render_fn: Callable[[int, list[str]], dict[str, Any]],
        review_fn: Callable[[dict[str, Any], int, str], VisualReviewEvidence | dict[str, Any] | None],
        apply_fix_fn: Callable[[list[BlenderFixAction], int], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Execute render -> inspect -> localized fix -> re-render loop up to max_fix_iterations."""
        t0 = time.monotonic()
        iterations: list[dict[str, Any]] = []
        before_after_comparisons: list[dict[str, Any]] = []
        fix_iteration_count = 0
        rerender_count = 0
        consecutive_noop_rerenders = 0
        seen_stalled_signatures: set[tuple[str, ...]] = set()
        last_evidence: dict[str, Any] | None = None
        last_renders: dict[str, Any] | None = None
        best_checkpoint: dict[str, Any] | None = None
        best_rank: tuple[int, int] | None = None

        views = list(self.profile.required_views)
        pass_index = 0

        while True:
            elapsed = time.monotonic() - t0
            if elapsed > self.policy.max_wall_time_sec:
                return {
                    "status": "FAIL",
                    "profile_id": self.profile.profile_id,
                    "policy_digest": self.policy_digest,
                    "fix_iteration_count": fix_iteration_count,
                    "rerender_count": rerender_count,
                    "iterations": iterations,
                    "before_after_comparisons": before_after_comparisons,
                    "best_checkpoint": best_checkpoint,
                    "unresolved_defects": list((last_evidence or {}).get("defects") or []),
                    "reasons": [f"Visual QA loop exceeded max_wall_time_sec ({self.policy.max_wall_time_sec}s)."],
                }

            render_bundle = render_fn(pass_index, views)
            rerender_count += 1
            raw_evidence = review_fn(render_bundle, pass_index, self.policy_digest)

            ev_check = verify_visual_review_evidence(
                raw_evidence,
                expected_policy_digest=self.policy_digest,
                verify_files_on_disk=self.verify_files_on_disk,
            )
            if not ev_check["valid"]:
                return {
                    "status": ev_check["status_if_invalid"],
                    "profile_id": self.profile.profile_id,
                    "policy_digest": self.policy_digest,
                    "fix_iteration_count": fix_iteration_count,
                    "rerender_count": rerender_count,
                    "iterations": iterations,
                    "before_after_comparisons": before_after_comparisons,
                    "best_checkpoint": best_checkpoint,
                    "unresolved_defects": [],
                    "reasons": ev_check["reasons"],
                }

            evidence_dict = _coerce_evidence_dict(raw_evidence)  # type: ignore[arg-type]
            approved, gate_reasons = _review_meets_approval(evidence_dict, self.profile, self.policy)

            open_blocking_count = sum(
                1
                for d in (evidence_dict.get("defects") or [])
                if not d.get("resolved") and d.get("severity") in ("BLOCKER", "MAJOR")
            )
            pass_criteria_count = sum(
                1 for v in (evidence_dict.get("criteria_verdicts") or {}).values() if v == "PASS"
            )
            curr_rank = (-open_blocking_count, pass_criteria_count)
            if best_rank is None or curr_rank > best_rank:
                best_rank = curr_rank
                best_checkpoint = {
                    "pass_index": pass_index,
                    "fix_iteration": fix_iteration_count,
                    "renders": render_bundle.get("renders") or render_bundle,
                    "open_blocking_defects": open_blocking_count,
                    "pass_criteria_count": pass_criteria_count,
                    "target_asset_path": evidence_dict.get("target_asset_path"),
                    "defects": list(evidence_dict.get("defects") or []),
                }

            if last_evidence is not None and last_renders is not None:
                prev_open = [
                    d.get("defect_type")
                    for d in (last_evidence.get("defects") or [])
                    if not d.get("resolved") and d.get("severity") in ("BLOCKER", "MAJOR")
                ]
                curr_open = [
                    d.get("defect_type")
                    for d in (evidence_dict.get("defects") or [])
                    if not d.get("resolved") and d.get("severity") in ("BLOCKER", "MAJOR")
                ]
                before_after_comparisons.append({
                    "fix_iteration": fix_iteration_count,
                    "before_renders": last_renders.get("renders") or last_renders,
                    "after_renders": render_bundle.get("renders") or render_bundle,
                    "before_open_major_defects": prev_open,
                    "after_open_major_defects": curr_open,
                    "resolved_count": max(0, len(prev_open) - len(curr_open)),
                    "improved": len(curr_open) < len(prev_open) or approved,
                })

            iter_record: dict[str, Any] = {
                "pass_index": pass_index,
                "fix_iteration": fix_iteration_count,
                "approved": approved,
                "reasons": gate_reasons,
                "defects": list(evidence_dict.get("defects") or []),
                "criteria_verdicts": dict(evidence_dict.get("criteria_verdicts") or {}),
                "image_observations": list(evidence_dict.get("image_observations") or []),
                "fix_actions": [],
                "fix_result": None,
                "strategy_validation": None,
            }

            if approved:
                iterations.append(iter_record)
                return {
                    "status": "PASS",
                    "profile_id": self.profile.profile_id,
                    "policy_digest": self.policy_digest,
                    "fix_iteration_count": fix_iteration_count,
                    "rerender_count": rerender_count,
                    "iterations": iterations,
                    "before_after_comparisons": before_after_comparisons,
                    "best_checkpoint": best_checkpoint,
                    "final_evidence": evidence_dict,
                    "unresolved_defects": [
                        d for d in (evidence_dict.get("defects") or []) if not d.get("resolved")
                    ],
                    "reasons": ["All required visual and technical criteria verified PASS."],
                }

            # Check if we have exhausted the fix iteration budget
            if fix_iteration_count >= self.policy.max_fix_iterations or apply_fix_fn is None:
                iterations.append(iter_record)
                return {
                    "status": "FAIL",
                    "profile_id": self.profile.profile_id,
                    "policy_digest": self.policy_digest,
                    "fix_iteration_count": fix_iteration_count,
                    "rerender_count": rerender_count,
                    "iterations": iterations,
                    "before_after_comparisons": before_after_comparisons,
                    "best_checkpoint": best_checkpoint,
                    "final_evidence": evidence_dict,
                    "unresolved_defects": [
                        d for d in (evidence_dict.get("defects") or []) if not d.get("resolved")
                    ],
                    "reasons": gate_reasons + [
                        f"Reached max_fix_iterations ({self.policy.max_fix_iterations}) with unresolved defects."
                    ],
                }

            defects_objs = [
                d if isinstance(d, VisualDefect) else VisualDefect(**d)
                for d in (evidence_dict.get("defects") or [])
            ]
            attempt_for_escalation = fix_iteration_count if self.policy.escalate_strategy_on_repeat else 0
            fix_actions = plan_blender_fixes_for_defects(defects_objs, attempt_index=attempt_for_escalation)
            if not fix_actions:
                iterations.append(iter_record)
                return {
                    "status": "FAIL",
                    "profile_id": self.profile.profile_id,
                    "policy_digest": self.policy_digest,
                    "fix_iteration_count": fix_iteration_count,
                    "rerender_count": rerender_count,
                    "iterations": iterations,
                    "before_after_comparisons": before_after_comparisons,
                    "best_checkpoint": best_checkpoint,
                    "final_evidence": evidence_dict,
                    "unresolved_defects": [d.to_dict() for d in defects_objs if not d.resolved],
                    "reasons": gate_reasons + ["Criteria failed with no actionable open defects to repair."],
                }

            sig = tuple(sorted(f"{a.defect_type}:{a.operation_type}:{','.join(a.target_objects)}" for a in fix_actions))
            if self.policy.forbid_stalled_repeat and sig in seen_stalled_signatures:
                iterations.append(iter_record)
                return {
                    "status": "FAIL",
                    "profile_id": self.profile.profile_id,
                    "policy_digest": self.policy_digest,
                    "fix_iteration_count": fix_iteration_count,
                    "rerender_count": rerender_count,
                    "iterations": iterations,
                    "before_after_comparisons": before_after_comparisons,
                    "best_checkpoint": best_checkpoint,
                    "final_evidence": evidence_dict,
                    "unresolved_defects": [d.to_dict() for d in defects_objs if not d.resolved],
                    "reasons": gate_reasons + [
                        "Stalled repair loop detected: identical fix signature repeated without resolving defects."
                    ],
                }

            fix_result = apply_fix_fn(fix_actions, fix_iteration_count + 1) or {}
            mesh_modified = bool(fix_result.get("mesh_modified", True))
            if mesh_modified:
                strat_val = validate_repair_effectiveness(fix_actions, fix_result)
                iter_record["strategy_validation"] = strat_val.to_dict()
                if not strat_val.effective:
                    iter_record["fix_actions"] = [a.to_dict() for a in fix_actions]
                    iter_record["fix_result"] = fix_result
                    iterations.append(iter_record)
                    return {
                        "status": "FAIL",
                        "profile_id": self.profile.profile_id,
                        "policy_digest": self.policy_digest,
                        "fix_iteration_count": fix_iteration_count,
                        "rerender_count": rerender_count,
                        "iterations": iterations,
                        "before_after_comparisons": before_after_comparisons,
                        "best_checkpoint": best_checkpoint,
                        "final_evidence": evidence_dict,
                        "unresolved_defects": [d.to_dict() for d in defects_objs if not d.resolved],
                        "reasons": gate_reasons + [str(strat_val.failure_reason)],
                    }
                fix_iteration_count += 1
                consecutive_noop_rerenders = 0
                seen_stalled_signatures.add(sig)
            else:
                consecutive_noop_rerenders += 1
                if consecutive_noop_rerenders > 2:
                    iterations.append(iter_record)
                    return {
                        "status": "FAIL",
                        "profile_id": self.profile.profile_id,
                        "policy_digest": self.policy_digest,
                        "fix_iteration_count": fix_iteration_count,
                        "rerender_count": rerender_count,
                        "iterations": iterations,
                        "before_after_comparisons": before_after_comparisons,
                        "best_checkpoint": best_checkpoint,
                        "final_evidence": evidence_dict,
                        "unresolved_defects": [d.to_dict() for d in defects_objs if not d.resolved],
                        "reasons": gate_reasons + [
                            "Repair loop halted after repeated re-renders without mesh modification."
                        ],
                    }
            # Note: pure re-renders with mesh_modified=False do NOT consume a fix iteration or stall signature!
            iter_record["fix_actions"] = [a.to_dict() for a in fix_actions]
            iter_record["fix_result"] = fix_result
            iterations.append(iter_record)

            last_evidence = evidence_dict
            last_renders = render_bundle
            pass_index += 1
