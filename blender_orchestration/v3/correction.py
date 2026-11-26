"""Bounded, quality-driven correction control.

Replaces the legacy fixed two-pass loop. One controller instance governs one gate scope
(e.g. ``silhouette`` or ``modeling_gate``) and decides, after every independent review, whether
to stop (PASS), correct (with a focused defect selection and a required strategy), or halt
(BLOCKED / EXHAUSTED). It never lowers a threshold.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

# Region vocabulary shared with the reviewer prompt. Priority follows the production goal:
# facial likeness > silhouette/anatomy > hair > costume > hands/boots/accessories > materials > rig.
REGION_PRIORITY: dict[str, int] = {
    "face": 7, "eyes": 7, "ears": 6, "head": 6,
    "proportions": 6, "silhouette": 6, "torso": 5, "arms": 5, "legs": 5,
    "hair": 5,
    "costume_top": 4, "costume_bottom": 4, "sleeves": 4, "belt": 3,
    "hands": 3, "boots": 3, "accessories": 3,
    "materials": 2, "technical": 2, "rig": 1, "animation": 1,
}
SEVERITY_WEIGHT = {"CRITICAL": 3, "MAJOR": 2, "MINOR": 1}


class Strategy(str, enum.Enum):
    ADJUST = "ADJUST"            # edit existing geometry of the region (sculpt, reshape, reposition)
    REMODEL = "REMODEL"          # delete the region's objects and rebuild with a different construction


class ErrorClass(str, enum.Enum):
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PRODUCER_NO_CODE = "PRODUCER_NO_CODE"
    SCRIPT_INVALID = "SCRIPT_INVALID"
    BLENDER_RUNTIME_ERROR = "BLENDER_RUNTIME_ERROR"
    BLENDER_TIMEOUT = "BLENDER_TIMEOUT"
    GEOMETRY_GUARD = "GEOMETRY_GUARD"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    RENDER_FAILED = "RENDER_FAILED"
    REVIEW_UNAVAILABLE = "REVIEW_UNAVAILABLE"


FATAL_ERRORS = {ErrorClass.ROLLBACK_FAILED}


def classify_error(message: str) -> ErrorClass:
    m = (message or "").lower()
    if "rollback" in m and ("fail" in m or "could not" in m or "not verified" in m):
        return ErrorClass.ROLLBACK_FAILED
    if "timed out" in m or "timeout" in m:
        return ErrorClass.BLENDER_TIMEOUT if "blender" in m or "mcp" in m else ErrorClass.PROVIDER_UNAVAILABLE
    if "provider" in m or "quota" in m or "backend unavailable" in m or "out of memory" in m:
        return ErrorClass.PROVIDER_UNAVAILABLE
    if "no ```python" in m or "no python block" in m or "no code" in m:
        return ErrorClass.PRODUCER_NO_CODE
    if "syntaxerror" in m or "refused" in m or "not allowed" in m or "removed in blender" in m:
        return ErrorClass.SCRIPT_INVALID
    if "geometry guard" in m:
        return ErrorClass.GEOMETRY_GUARD
    if "render" in m:
        return ErrorClass.RENDER_FAILED
    if "review" in m:
        return ErrorClass.REVIEW_UNAVAILABLE
    return ErrorClass.BLENDER_RUNTIME_ERROR


def defect_key(defect: dict[str, Any]) -> str:
    region = str(defect.get("region") or "technical").strip().lower()
    category = str(defect.get("category") or defect.get("defect_id") or "general").strip().lower()
    return f"{region}:{category}"


def defect_impact(defect: dict[str, Any]) -> int:
    region = str(defect.get("region") or "technical").lower()
    return SEVERITY_WEIGHT.get(str(defect.get("severity") or "MINOR").upper(), 1) * REGION_PRIORITY.get(region, 2)


@dataclass
class DefectHistory:
    key: str
    region: str
    seen_in_reviews: list[int] = field(default_factory=list)
    corrected_in_attempts: list[int] = field(default_factory=list)
    strategies: list[str] = field(default_factory=list)
    last_severity: str = "MINOR"
    unresolved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Decision:
    action: str  # PASS | CORRECT | BLOCKED | EXHAUSTED
    reason: str
    defects: list[dict[str, Any]] = field(default_factory=list)
    strategy: Strategy | None = None
    regions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "reason": self.reason, "defects": self.defects,
                "strategy": self.strategy.value if self.strategy else None, "regions": self.regions}


class CorrectionController:
    def __init__(self, scope: str, *, max_attempts: int = 4, max_technical_errors: int = 4,
                 stagnation_limit: int = 2, min_improvement: float = 2.0, max_defects_per_order: int = 3,
                 max_strategy_escalations: int = 2) -> None:
        if max_attempts < 0:
            raise ValueError("max_attempts must be >= 0")
        self.scope = scope
        self.max_attempts = int(max_attempts)
        self.max_technical_errors = int(max_technical_errors)
        self.stagnation_limit = int(stagnation_limit)
        self.min_improvement = float(min_improvement)
        self.max_defects_per_order = int(max_defects_per_order)
        self.max_strategy_escalations = int(max_strategy_escalations)
        self.attempts = 0
        self.reviews = 0
        self.technical_errors: list[dict[str, str]] = []
        self.best_score: float | None = None
        self.score_history: list[float] = []
        self.no_progress = 0
        self.escalations = 0
        self.defects: dict[str, DefectHistory] = {}
        self.last_order_keys: list[str] = []
        self.events: list[dict[str, Any]] = []

    # -- observations ----------------------------------------------------
    def observe_review(self, *, score: float, passed: bool, defects: list[dict[str, Any]], accepted: bool) -> None:
        """Record one independent review. ``accepted`` = the candidate was kept (not rolled back)."""
        self.reviews += 1
        self.score_history.append(float(score))
        improved = self.best_score is None or score >= self.best_score + self.min_improvement
        if accepted and (self.best_score is None or score > self.best_score):
            self.best_score = float(score)
        if self.reviews > 1:
            self.no_progress = 0 if improved else self.no_progress + 1
        present = set()
        for d in defects:
            key = defect_key(d)
            present.add(key)
            hist = self.defects.setdefault(key, DefectHistory(key=key, region=str(d.get("region") or "technical").lower()))
            hist.seen_in_reviews.append(self.reviews)
            hist.last_severity = str(d.get("severity") or "MINOR").upper()
        self.events.append({"event": "review", "n": self.reviews, "score": score, "passed": passed,
                            "accepted": accepted, "improved": improved, "no_progress": self.no_progress,
                            "defect_keys": sorted(present)})

    def observe_technical_error(self, message: str) -> ErrorClass:
        err = classify_error(message)
        self.technical_errors.append({"class": err.value, "message": message[-500:]})
        self.events.append({"event": "technical_error", "class": err.value})
        return err

    # -- decisions -------------------------------------------------------
    def _required_strategy(self, d: dict[str, Any]) -> Strategy:
        hist = self.defects.get(defect_key(d))
        if str(d.get("strategy") or "").upper() == "REMODEL" or d.get("structural") is True:
            return Strategy.REMODEL
        if hist and hist.corrected_in_attempts:
            # Corrected before and still present: a further local tweak is the superficial-fix trap.
            return Strategy.REMODEL
        return Strategy.ADJUST

    def decide(self, *, passed: bool, defects: list[dict[str, Any]]) -> Decision:
        if passed:
            return self._log(Decision("PASS", "gate accepted the candidate"))
        if any(ErrorClass(e["class"]) in FATAL_ERRORS for e in self.technical_errors):
            return self._log(Decision("BLOCKED", "rollback could not be verified; verified work is protected"))
        if len(self.technical_errors) >= self.max_technical_errors:
            return self._log(Decision("BLOCKED", f"{len(self.technical_errors)} technical errors (budget {self.max_technical_errors})"))
        if self.attempts >= self.max_attempts:
            return self._log(Decision("EXHAUSTED", f"correction budget {self.max_attempts} used without passing"))
        if self.no_progress >= self.stagnation_limit:
            if self.escalations >= self.max_strategy_escalations:
                return self._log(Decision("BLOCKED", f"stagnation: no >= {self.min_improvement} pt improvement in "
                                          f"{self.no_progress} reviews after {self.escalations} strategy escalations"))
            self.escalations += 1
            self.no_progress = 0
            force_remodel = True
        else:
            force_remodel = False

        candidates = [d for d in defects if str(d.get("severity") or "").upper() in SEVERITY_WEIGHT]
        for d in candidates:
            hist = self.defects.get(defect_key(d))
            if hist and len([s for s in hist.strategies if s == Strategy.REMODEL.value]) >= 2:
                hist.unresolved = True
        candidates = [d for d in candidates if not self.defects.get(defect_key(d), DefectHistory("", "")).unresolved]
        if not candidates:
            return self._log(Decision("BLOCKED", "the gate failed but no correctable defect remains (all unresolved or none reported)"))
        candidates.sort(key=defect_impact, reverse=True)
        # Focus: the highest-impact region first, at most two regions per work order.
        regions: list[str] = []
        chosen: list[dict[str, Any]] = []
        for d in candidates:
            r = str(d.get("region") or "technical").lower()
            if r not in regions and len(regions) >= 2:
                continue
            if r not in regions:
                regions.append(r)
            chosen.append(d)
            if len(chosen) >= self.max_defects_per_order:
                break
        strategy = Strategy.REMODEL if force_remodel else max(
            (self._required_strategy(d) for d in chosen), key=lambda s: s == Strategy.REMODEL)
        self.attempts += 1
        self.last_order_keys = [defect_key(d) for d in chosen]
        for d in chosen:
            hist = self.defects.setdefault(defect_key(d), DefectHistory(key=defect_key(d), region=str(d.get("region") or "technical").lower()))
            hist.corrected_in_attempts.append(self.attempts)
            hist.strategies.append(strategy.value)
        reason = "stagnation escalation to REMODEL" if force_remodel else f"correct top {len(chosen)} defects"
        return self._log(Decision("CORRECT", reason, chosen, strategy, regions))

    def _log(self, decision: Decision) -> Decision:
        self.events.append({"event": "decision", **decision.to_dict(), "attempt": self.attempts})
        return decision

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope, "max_attempts": self.max_attempts, "attempts": self.attempts,
            "reviews": self.reviews, "best_score": self.best_score, "score_history": self.score_history,
            "no_progress": self.no_progress, "escalations": self.escalations,
            "technical_errors": self.technical_errors,
            "defects": {k: v.to_dict() for k, v in self.defects.items()}, "events": self.events[-60:],
        }
