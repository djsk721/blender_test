"""Structural repair strategy engine preventing superficial material/offset fixes on 3D geometry defects."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class RepairStrategyTier:
    """Ordered severity tiers for Blender repair strategies."""

    MATERIAL_ONLY = "MATERIAL_ONLY"
    PLACEMENT_ONLY = "PLACEMENT_ONLY"
    SILHOUETTE_RESHAPE = "SILHOUETTE_RESHAPE"
    STRUCTURAL_REMODEL = "STRUCTURAL_REMODEL"
    ROLLBACK_TO_BLOCKOUT = "ROLLBACK_TO_BLOCKOUT"

    RANK = {
        MATERIAL_ONLY: 1,
        PLACEMENT_ONLY: 2,
        SILHOUETTE_RESHAPE: 3,
        STRUCTURAL_REMODEL: 4,
        ROLLBACK_TO_BLOCKOUT: 5,
    }


REQUIRED_STRATEGY_BY_DEFECT: dict[str, str] = {
    "facial_proportion_error": RepairStrategyTier.STRUCTURAL_REMODEL,
    "round_protruding_eyes": RepairStrategyTier.STRUCTURAL_REMODEL,
    "rope_like_hair": RepairStrategyTier.STRUCTURAL_REMODEL,
    "scalp_exposure": RepairStrategyTier.STRUCTURAL_REMODEL,
    "hair_face_penetration": RepairStrategyTier.STRUCTURAL_REMODEL,
    "stiff_fingers_bad_grip": RepairStrategyTier.STRUCTURAL_REMODEL,
    "bulbous_boots": RepairStrategyTier.STRUCTURAL_REMODEL,
    "floating_thigh_seam": RepairStrategyTier.STRUCTURAL_REMODEL,
    "costume_penetration": RepairStrategyTier.STRUCTURAL_REMODEL,
    "stiff_shoulders_knees": RepairStrategyTier.SILHOUETTE_RESHAPE,
    "overall_silhouette_failure": RepairStrategyTier.ROLLBACK_TO_BLOCKOUT,
    "reference_match_gap": RepairStrategyTier.SILHOUETTE_RESHAPE,
    "primitive_stacking": RepairStrategyTier.STRUCTURAL_REMODEL,
    "triangle_budget_overrun": RepairStrategyTier.STRUCTURAL_REMODEL,
    "albedo_or_roughness_mismatch": RepairStrategyTier.MATERIAL_ONLY,
    "rigid_prop_offset": RepairStrategyTier.PLACEMENT_ONLY,
}

ESCALATION_CHAIN: tuple[str, ...] = (
    RepairStrategyTier.STRUCTURAL_REMODEL,
    RepairStrategyTier.ROLLBACK_TO_BLOCKOUT,
)


@dataclass(frozen=True)
class StrategyValidationResult:
    """Result verifying whether a fix genuinely addressed structural geometry vs superficial tweaks."""

    effective: bool
    required_tier: str
    applied_tier: str
    geometry_changed: bool
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_strategy_for_defect(defect_type: str, *, attempt_index: int = 0) -> str:
    """Select the required repair tier, escalating if prior structural attempts failed."""
    base_tier = REQUIRED_STRATEGY_BY_DEFECT.get(defect_type, RepairStrategyTier.STRUCTURAL_REMODEL)
    if attempt_index <= 0:
        return base_tier
    if RepairStrategyTier.RANK.get(base_tier, 4) < RepairStrategyTier.RANK[RepairStrategyTier.STRUCTURAL_REMODEL]:
        return RepairStrategyTier.STRUCTURAL_REMODEL
    return RepairStrategyTier.ROLLBACK_TO_BLOCKOUT


def infer_applied_strategy_tier(fix_actions: list[Any], fix_result: dict[str, Any]) -> str:
    """Infer what strategy tier was actually executed based on fix actions and Blender output."""
    explicit = str(fix_result.get("strategy_tier") or "").strip()
    if explicit in RepairStrategyTier.RANK:
        return explicit

    if fix_result.get("rolled_back_to_blockout"):
        return RepairStrategyTier.ROLLBACK_TO_BLOCKOUT

    if fix_result.get("material_only") or fix_result.get("lighting_only"):
        return RepairStrategyTier.MATERIAL_ONLY

    if fix_result.get("placement_only"):
        return RepairStrategyTier.PLACEMENT_ONLY

    ops = []
    for a in fix_actions:
        op = a.get("operation_type") if isinstance(a, dict) else getattr(a, "operation_type", "")
        ops.append(str(op).lower())

    if any("rollback" in op or "reblock" in op for op in ops):
        return RepairStrategyTier.ROLLBACK_TO_BLOCKOUT
    if any(
        tok in op
        for op in ops
        for tok in ("remodel", "recess", "sculpt", "scalp", "curl", "contour", "snap", "merge", "decimate", "soften")
    ):
        if fix_result.get("geometry_changed", fix_result.get("mesh_modified", False)):
            return RepairStrategyTier.STRUCTURAL_REMODEL
    if fix_result.get("mesh_modified"):
        return RepairStrategyTier.SILHOUETTE_RESHAPE
    return RepairStrategyTier.PLACEMENT_ONLY


def validate_repair_effectiveness(
    fix_actions: list[Any],
    fix_result: dict[str, Any],
    *,
    before_review: dict[str, Any] | None = None,
    after_review: dict[str, Any] | None = None,
) -> StrategyValidationResult:
    """Reject superficial color/light/offset fixes when open defects demand 3D structural remodeling."""
    highest_req = RepairStrategyTier.MATERIAL_ONLY
    for a in fix_actions:
        dtype = a.get("defect_type") if isinstance(a, dict) else getattr(a, "defect_type", "")
        req = REQUIRED_STRATEGY_BY_DEFECT.get(str(dtype), RepairStrategyTier.STRUCTURAL_REMODEL)
        if RepairStrategyTier.RANK[req] > RepairStrategyTier.RANK[highest_req]:
            highest_req = req

    applied = infer_applied_strategy_tier(fix_actions, fix_result)
    geom_changed = bool(fix_result.get("geometry_changed", fix_result.get("mesh_modified", False)))

    # Reject image-byte-only claims without actual 3D geometry change for structural issues
    if RepairStrategyTier.RANK[highest_req] >= RepairStrategyTier.RANK[RepairStrategyTier.SILHOUETTE_RESHAPE]:
        if not geom_changed:
            return StrategyValidationResult(
                effective=False,
                required_tier=highest_req,
                applied_tier=applied,
                geometry_changed=False,
                failure_reason="SUPERFICIAL_FIX_REJECTED: Structural defect requires 3D geometry change, not color/light/rerender only.",
            )
        if RepairStrategyTier.RANK[applied] < RepairStrategyTier.RANK[highest_req]:
            return StrategyValidationResult(
                effective=False,
                required_tier=highest_req,
                applied_tier=applied,
                geometry_changed=geom_changed,
                failure_reason=(
                    f"SUPERFICIAL_FIX_REJECTED: Defect requires {highest_req}, but only {applied} was performed."
                ),
            )

    # Check if independent visual review confirms reduction in open defects
    if before_review is not None and after_review is not None:
        b_open = sum(
            1
            for d in (before_review.get("defects") or [])
            if not (d.get("resolved") if isinstance(d, dict) else getattr(d, "resolved", False))
            and (d.get("severity") if isinstance(d, dict) else getattr(d, "severity", "")) in ("BLOCKER", "MAJOR")
        )
        a_open = sum(
            1
            for d in (after_review.get("defects") or [])
            if not (d.get("resolved") if isinstance(d, dict) else getattr(d, "resolved", False))
            and (d.get("severity") if isinstance(d, dict) else getattr(d, "severity", "")) in ("BLOCKER", "MAJOR")
        )
        if a_open >= b_open and b_open > 0:
            return StrategyValidationResult(
                effective=False,
                required_tier=highest_req,
                applied_tier=applied,
                geometry_changed=geom_changed,
                failure_reason=(
                    f"DEFECT_NOT_REDUCED: Open BLOCKER/MAJOR defects remained {a_open} (was {b_open}) after repair."
                ),
            )

    return StrategyValidationResult(
        effective=True,
        required_tier=highest_req,
        applied_tier=applied,
        geometry_changed=geom_changed,
        failure_reason=None,
    )
