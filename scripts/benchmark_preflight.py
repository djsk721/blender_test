#!/usr/bin/env python3
"""Dynamic Benchmark Preflight Validation and QA Gate evaluator.

Resolves benchmark reference policy strictly from project instructions
(.opencode/AGENTS.md) and the user benchmark prompt without any model-specific
routing, enforcing the Blender-Only Asset Production Policy (TEXT_ONLY and
REFERENCE_GUIDED only; no IMAGE_TO_3D or external AI 3D generators).
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DEFAULT_AGENTS_MD = PROJECT_ROOT / ".opencode" / "AGENTS.md"
DEFAULT_SKILLS_ROOT = PROJECT_ROOT / ".opencode" / "skills"
DEFAULT_VERSION_FILE = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "VERSION"

REQUIRED_CORE_SKILLS: tuple[str, ...] = (
    "blender-director",
    "character-artist",
    "sculpting",
    "retopology",
    "hair-groom",
    "uv-workflow",
    "materials",
    "texture-workflow",
    "lighting",
    "rendering",
    "rigging",
    "animation",
    "qa-review",
    "asset-optimization",
    "export-pipeline",
)

_RUN_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[A-Za-z0-9._-]+$")

SUPPORTED_PRODUCTION_MODES = ("TEXT_ONLY", "REFERENCE_GUIDED")

GATE_A_DIMENSIONS = (
    "silhouette",
    "facial_proportions",
    "eyes_nose_mouth_ears",
    "hair_construction",
    "anatomy",
    "costume",
    "materials",
    "character_readability",
    "text_spec_compliance",
)

GATE_B_CHECKS = (
    "bone_hierarchy",
    "weight_assignments",
    "joint_deformation",
    "clothing_clipping",
    "hair_deformation",
    "mesh_continuity",
)

GATE_C_CHECKS = (
    "running_cycle",
    "foot_contact",
    "root_stability",
    "arm_swing",
    "secondary_motion",
    "loop_continuity",
)

_TEXT_ONLY_PATTERNS = (
    re.compile(r"benchmark\s+mode\s*:\s*text_only", re.IGNORECASE),
    re.compile(r"without\s+(?:using\s+)?(?:any\s+)?reference\s+images?", re.IGNORECASE),
    re.compile(r"(?:do\s+not|don't|never|must\s+not)\s+(?:use|require|search|download|generate)\s+(?:any\s+)?reference\s+images?", re.IGNORECASE),
    re.compile(r"(?:prohibits?|forbids?|disallow(?:s|ed)?)\s+(?:any\s+)?reference\s+images?", re.IGNORECASE),
    re.compile(r"\bno\s+reference\s+images?\b", re.IGNORECASE),
    re.compile(r"\btext[- ]only\b", re.IGNORECASE),
)

_REFERENCE_GUIDED_PATTERNS = (
    re.compile(r"benchmark\s+mode\s*:\s*reference_guided", re.IGNORECASE),
    re.compile(r"(?:using|from|match(?:ing)?)\s+(?:the\s+)?(?:supplied|provided|attached|approved)\s+(?:visual\s+)?references?", re.IGNORECASE),
    re.compile(r"\bexact\s+visual\s+match(?:ing)?\s+(?:is\s+)?(?:required|mandatory)\b", re.IGNORECASE),
    re.compile(r"\b(?:requires?|mandatory)\s+(?:visual\s+|approved\s+)?reference(?:s|\s+images?)\b", re.IGNORECASE),
    re.compile(r"\bsupplied\s+approved\s+references?\b", re.IGNORECASE),
)

_MISSING_REF_PATTERNS = (
    re.compile(r"(?:approved\s+)?reference(?:s|\s+images?)\s+(?:are|is)\s+(?:missing|unavailable|absent)", re.IGNORECASE),
    re.compile(r"(?:approved\s+)?reference(?:s|\s+images?)\s+(?:have|were)\s+not\s+(?:been\s+)?(?:supplied|provided|available)", re.IGNORECASE),
    re.compile(r"\bmissing\s+(?:mandatory\s+|approved\s+)?reference(?:s|\s+images?)\b", re.IGNORECASE),
)

_CONTRAST_NON_MISSING_CLAUSE = re.compile(
    r"missing\s+(?:reference\s+)?images?\s+must\s+not\s+block\s+modeling",
    re.IGNORECASE,
)

_DISALLOWED_GENERATOR_PATTERNS = (
    re.compile(r"\bimage[-_ ]to[-_ ]3d\b", re.IGNORECASE),
    re.compile(r"\btripo(?:3d)?\b", re.IGNORECASE),
    re.compile(r"\bhunyuan\s*3d\b", re.IGNORECASE),
    re.compile(r"\bhyper3d\b", re.IGNORECASE),
    re.compile(r"\bmeshy\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ProjectPolicy:
    """Parsed instruction capabilities from .opencode/AGENTS.md."""

    agents_md_path: str
    exists: bool
    supports_text_only: bool
    supports_reference_guided: bool
    defines_precedence: bool
    enforces_blender_only: bool
    requires_director_routing: bool
    requires_gate_a: bool
    requires_gate_b: bool
    requires_gate_c: bool
    requires_independent_visual_qa: bool
    has_unconditional_reference_blocker: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_project_policy(agents_md_path: Path | None = None) -> ProjectPolicy:
    """Parse project execution policy dynamically from AGENTS.md."""
    path = Path(agents_md_path) if agents_md_path is not None else DEFAULT_AGENTS_MD
    if not path.is_file():
        return ProjectPolicy(
            agents_md_path=str(path),
            exists=False,
            supports_text_only=False,
            supports_reference_guided=False,
            defines_precedence=False,
            enforces_blender_only=False,
            requires_director_routing=False,
            requires_gate_a=False,
            requires_gate_b=False,
            requires_gate_c=False,
            requires_independent_visual_qa=False,
            has_unconditional_reference_blocker=False,
        )

    text = path.read_text(encoding="utf-8")
    has_text_only = "TEXT_ONLY" in text and ("Mode B" in text or "Text-Only" in text)
    has_ref_guided = "REFERENCE_GUIDED" in text and ("Mode A" in text or "Reference-Guided" in text)
    defines_precedence = "Instruction Precedence" in text
    enforces_blender_only = "Blender-Only" in text and "IMAGE_TO_3D" in text
    requires_director_routing = "blender-director" in text
    requires_gate_a = "Gate A" in text
    requires_gate_b = "Gate B" in text
    requires_gate_c = "Gate C" in text
    requires_independent_qa = "Independent Visual QA" in text

    # Legacy conflict signature: blanket requirement before any modeling without mode distinction
    legacy_unconditional_req = bool(
        re.search(r"^\s*2\.\s*Require explicit reference images before modeling\.", text, re.MULTILINE)
        or re.search(r"^\s*7\.\s*If references are missing,\s*mark identity QA BLOCKED\.", text, re.MULTILINE)
    )
    has_unconditional_blocker = legacy_unconditional_req or not has_text_only

    return ProjectPolicy(
        agents_md_path=str(path),
        exists=True,
        supports_text_only=has_text_only,
        supports_reference_guided=has_ref_guided,
        defines_precedence=defines_precedence,
        enforces_blender_only=enforces_blender_only,
        requires_director_routing=requires_director_routing,
        requires_gate_a=requires_gate_a,
        requires_gate_b=requires_gate_b,
        requires_gate_c=requires_gate_c,
        requires_independent_visual_qa=requires_independent_qa,
        has_unconditional_reference_blocker=has_unconditional_blocker,
    )


def _match_any(patterns: tuple[re.Pattern[str], ...], text: str) -> list[str]:
    hits: list[str] = []
    for pat in patterns:
        m = pat.search(text)
        if m:
            hits.append(m.group(0))
    return hits


def resolve_preflight_policy(
    prompt: str,
    *,
    agents_md_path: Path | None = None,
    supplied_references: list[str] | None = None,
    text_specification: str | None = None,
) -> dict[str, Any]:
    """Resolve benchmark preflight policy from prompt and project AGENTS.md.

    Intentionally takes no model or provider argument so every LLM receives the
    exact same resolved production policy for identical benchmark inputs.
    """
    policy = parse_project_policy(agents_md_path)
    prompt_text = prompt or ""
    cleaned_for_missing = _CONTRAST_NON_MISSING_CLAUSE.sub("", prompt_text)

    text_only_hits = _match_any(_TEXT_ONLY_PATTERNS, prompt_text)
    ref_guided_hits = _match_any(_REFERENCE_GUIDED_PATTERNS, prompt_text)
    missing_ref_hits = _match_any(_MISSING_REF_PATTERNS, cleaned_for_missing)
    disallowed_gen_hits = _match_any(_DISALLOWED_GENERATOR_PATTERNS, prompt_text)

    has_supplied_refs = bool(supplied_references)
    explicitly_empty_refs = supplied_references is not None and len(supplied_references) == 0

    text_spec_available = bool((text_specification or "").strip() or prompt_text.strip())
    precedence_steps = [
        "1. Explicit user benchmark requirements determine TEXT_ONLY vs REFERENCE_GUIDED (Blender-only; no IMAGE_TO_3D)",
        "2. Project instructions (.opencode/AGENTS.md) define mode execution and Blender-only guardrails",
        "3. Blender Director dynamically selects stage-specific Skills under .opencode/skills/",
        "4. Mandatory Quality Gates (A/B/C) and Independent Visual QA apply",
    ]

    def _base_payload(
        *,
        mode: str,
        ref_required: bool,
        ref_allowed: bool,
        modeling_permitted: bool,
        identity_verifiable: bool,
        status: str,
        blocker_reason: str | None,
        default_identity_status: str,
        detected_cues: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "benchmark_mode": mode,
            "supported_modes": list(SUPPORTED_PRODUCTION_MODES),
            "blender_only_production": True,
            "external_ai_3d_generators_allowed": False,
            "premade_assets_allowed": False,
            "reference_images_required": ref_required,
            "reference_images_allowed": ref_allowed,
            "text_specification_available": text_spec_available,
            "modeling_permitted": modeling_permitted,
            "exact_identity_fidelity_verifiable": identity_verifiable,
            "modeling_quality_gate_required": policy.requires_gate_a or True,
            "independent_visual_review_required": policy.requires_independent_visual_qa or True,
            "status": status,
            "blocker_reason": blocker_reason,
            "default_exact_identity_status": default_identity_status,
            "model_agnostic": True,
            "project_policy": policy.to_dict(),
            "detected_cues": detected_cues,
            "policy_precedence_applied": precedence_steps,
        }

    detected = {
        "text_only_cues": text_only_hits,
        "reference_guided_cues": ref_guided_hits,
        "missing_reference_cues": missing_ref_hits,
        "disallowed_generator_cues": disallowed_gen_hits,
        "supplied_references_count": len(supplied_references) if supplied_references is not None else None,
    }

    if not policy.exists:
        return _base_payload(
            mode="AMBIGUOUS",
            ref_required=False,
            ref_allowed=False,
            modeling_permitted=False,
            identity_verifiable=False,
            status="BLOCKED",
            blocker_reason=f"Project AGENTS.md not found at {policy.agents_md_path}",
            default_identity_status="NOT_VERIFIED",
            detected_cues=detected,
        )

    if disallowed_gen_hits:
        return _base_payload(
            mode="BLOCKED_DISALLOWED_MODE",
            ref_required=False,
            ref_allowed=False,
            modeling_permitted=False,
            identity_verifiable=False,
            status="BLOCKED",
            blocker_reason=(
                "Blender-Only Asset Production Policy prohibits IMAGE_TO_3D and external AI 3D generators "
                f"({', '.join(disallowed_gen_hits)})."
            ),
            default_identity_status="BLOCKED",
            detected_cues=detected,
        )

    # Ambiguous contradictory requirements: explicitly forbids references AND requires references / passes references
    if text_only_hits and (ref_guided_hits or has_supplied_refs):
        return _base_payload(
            mode="AMBIGUOUS",
            ref_required=False,
            ref_allowed=False,
            modeling_permitted=False,
            identity_verifiable=False,
            status="AMBIGUOUS",
            blocker_reason="Conflicting prompt instructions simultaneously prohibit and require/supply reference images.",
            default_identity_status="NOT_VERIFIED",
            detected_cues=detected,
        )

    # Mode B: Explicit TEXT_ONLY
    if text_only_hits:
        if policy.has_unconditional_reference_blocker or not policy.supports_text_only:
            return _base_payload(
                mode="TEXT_ONLY",
                ref_required=False,
                ref_allowed=False,
                modeling_permitted=False,
                identity_verifiable=False,
                status="BLOCKED",
                blocker_reason="Project AGENTS.md enforces an unconditional reference-image prerequisite incompatible with TEXT_ONLY mode.",
                default_identity_status="BLOCKED",
                detected_cues=detected,
            )
        if not text_spec_available:
            return _base_payload(
                mode="TEXT_ONLY",
                ref_required=False,
                ref_allowed=False,
                modeling_permitted=False,
                identity_verifiable=False,
                status="BLOCKED",
                blocker_reason="TEXT_ONLY mode requires a textual character specification.",
                default_identity_status="NOT_VERIFIED",
                detected_cues=detected,
            )
        return _base_payload(
            mode="TEXT_ONLY",
            ref_required=False,
            ref_allowed=False,
            modeling_permitted=True,
            identity_verifiable=False,
            status="READY",
            blocker_reason=None,
            default_identity_status="NOT_VERIFIED",
            detected_cues=detected,
        )

    # Mode A: REFERENCE_GUIDED (explicit ref-guided cues, missing mandatory ref cues, or supplied references)
    if ref_guided_hits or missing_ref_hits or has_supplied_refs:
        refs_missing = bool(missing_ref_hits) or explicitly_empty_refs
        if refs_missing:
            return _base_payload(
                mode="REFERENCE_GUIDED",
                ref_required=True,
                ref_allowed=True,
                modeling_permitted=False,
                identity_verifiable=False,
                status="BLOCKED",
                blocker_reason="Mandatory approved reference images are missing for REFERENCE_GUIDED mode.",
                default_identity_status="BLOCKED",
                detected_cues=detected,
            )
        return _base_payload(
            mode="REFERENCE_GUIDED",
            ref_required=True,
            ref_allowed=True,
            modeling_permitted=True,
            identity_verifiable=True,
            status="READY",
            blocker_reason=None,
            default_identity_status="PENDING_REFERENCE_REVIEW",
            detected_cues=detected,
        )

    # Genuinely ambiguous prompt (no reference cue in either direction)
    return _base_payload(
        mode="AMBIGUOUS",
        ref_required=False,
        ref_allowed=False,
        modeling_permitted=False,
        identity_verifiable=False,
        status="AMBIGUOUS",
        blocker_reason="Reference policy is ambiguous: clarify whether approved reference images are supplied (REFERENCE_GUIDED) or explicitly prohibited (TEXT_ONLY).",
        default_identity_status="NOT_VERIFIED",
        detected_cues=detected,
    )


def _verify_review_evidence_payload(review: dict[str, Any]) -> tuple[bool, str | None, list[str]]:
    """Validate embedded visual_evidence / review_evidence using anti-forging checks."""
    raw_ev = review.get("visual_evidence") if "visual_evidence" in review else review.get("review_evidence")
    try:
        from blender_orchestration.visual_qa import verify_visual_review_evidence
    except ImportError:
        return False, "NOT_VERIFIED", ["blender_orchestration.visual_qa is unavailable to verify visual evidence."]

    res = verify_visual_review_evidence(
        raw_ev,
        expected_policy_digest=review.get("expected_policy_digest"),
        verify_files_on_disk=bool(review.get("verify_files_on_disk", True)),
    )
    if res["valid"]:
        return True, None, []
    return False, str(res["status_if_invalid"]), list(res["reasons"])


def evaluate_gate_a(preflight: dict[str, Any], review: dict[str, Any], *, strict_evidence: bool = False) -> dict[str, Any]:
    """Evaluate Gate A (Modeling Quality) and separate Exact Identity Fidelity."""
    mode = preflight.get("benchmark_mode")
    reasons: list[str] = []

    if preflight.get("status") == "BLOCKED" or not preflight.get("modeling_permitted"):
        blocker = preflight.get("blocker_reason") or "Preflight blocked modeling."
        reasons.append(blocker)
        identity_status = "BLOCKED" if mode in ("REFERENCE_GUIDED", "BLOCKED_DISALLOWED_MODE") else preflight.get("default_exact_identity_status", "NOT_VERIFIED")
        return {
            "gate": "GATE_A",
            "benchmark_mode": mode,
            "modeling_quality_status": "NOT_VERIFIED",
            "exact_identity_fidelity_status": identity_status,
            "rigging_eligible": False,
            "animation_eligible": False,
            "evidence_verified": False,
            "reasons": reasons,
        }

    rendered_inspected = bool(review.get("rendered_images_inspected"))
    independent_reviewer = bool(review.get("independent_reviewer"))
    major_defects = list(review.get("major_defects") or [])
    dimensions = dict(review.get("dimensions") or {})

    has_embedded_evidence = ("visual_evidence" in review) or ("review_evidence" in review)
    evidence_verified = False
    evidence_invalid_status: str | None = None
    if strict_evidence or has_embedded_evidence:
        ev_ok, ev_status, ev_reasons = _verify_review_evidence_payload(review)
        evidence_verified = ev_ok
        if not ev_ok:
            evidence_invalid_status = ev_status or "NOT_VERIFIED"
            reasons.extend(ev_reasons)

    failed_dims = [dim for dim in GATE_A_DIMENSIONS if dimensions.get(dim) == "FAIL"]
    missing_dims = [dim for dim in GATE_A_DIMENSIONS if dimensions.get(dim) not in ("PASS", "FAIL")]

    # Support optional extra modeling checks (e.g., hands_and_wrists, topology_and_budget) when provided
    for opt_dim in ("hands_and_wrists", "topology_and_budget"):
        if dimensions.get(opt_dim) == "FAIL":
            failed_dims.append(opt_dim)

    if major_defects or failed_dims:
        modeling_status = "FAIL"
        if major_defects:
            reasons.append(f"Major modeling defects reported: {', '.join(major_defects)}")
        if failed_dims:
            reasons.append(f"Failed Gate A dimensions: {', '.join(failed_dims)}")
    elif evidence_invalid_status is not None:
        modeling_status = evidence_invalid_status
    elif not rendered_inspected or not independent_reviewer:
        modeling_status = "NOT_VERIFIED"
        if not rendered_inspected:
            reasons.append("Rendered images were not inspected (file existence or script execution alone cannot approve Gate A).")
        if not independent_reviewer:
            reasons.append("Independent visual reviewer is required; self-approval is disallowed.")
    elif missing_dims:
        modeling_status = "NOT_VERIFIED"
        reasons.append(f"Incomplete Gate A dimension evaluations: {', '.join(missing_dims)}")
    else:
        modeling_status = "PASS"

    if mode == "TEXT_ONLY":
        exact_identity_status = "NOT_VERIFIED"
        if review.get("exact_identity_claimed_pass"):
            reasons.append("Automatic exact-identity PASS rejected in TEXT_ONLY mode; recorded as NOT_VERIFIED.")
        rigging_eligible = modeling_status == "PASS"
    elif mode == "REFERENCE_GUIDED":
        ref_compared = bool(review.get("reference_comparison_performed"))
        approved_refs_used = bool(review.get("approved_references_used"))
        verdict = review.get("reference_identity_verdict")
        if not approved_refs_used:
            exact_identity_status = "BLOCKED"
            reasons.append("Approved reference images were not used in REFERENCE_GUIDED review.")
        elif evidence_invalid_status is not None:
            exact_identity_status = evidence_invalid_status
        elif not rendered_inspected or not independent_reviewer or not ref_compared:
            exact_identity_status = "NOT_VERIFIED"
            reasons.append("Independent rendered comparison against approved references is missing.")
        elif verdict == "FAIL":
            exact_identity_status = "FAIL"
            reasons.append("Approved-reference identity match failed.")
        elif verdict == "PASS":
            exact_identity_status = "PASS"
        else:
            exact_identity_status = "NOT_VERIFIED"
            reasons.append("Reference identity verdict missing or invalid.")
        rigging_eligible = modeling_status == "PASS" and exact_identity_status == "PASS"
    else:
        exact_identity_status = "NOT_VERIFIED"
        rigging_eligible = False
        reasons.append(f"Benchmark mode {mode!r} does not permit rigging progression.")

    return {
        "gate": "GATE_A",
        "benchmark_mode": mode,
        "modeling_quality_status": modeling_status,
        "exact_identity_fidelity_status": exact_identity_status,
        "rigging_eligible": rigging_eligible,
        "animation_eligible": False,
        "evidence_verified": evidence_verified,
        "reasons": reasons,
    }


def evaluate_gate_b(gate_a_result: dict[str, Any], review: dict[str, Any], *, strict_evidence: bool = False) -> dict[str, Any]:
    """Evaluate Gate B (Rigging and Deformation)."""
    reasons: list[str] = []
    if not gate_a_result.get("rigging_eligible"):
        reasons.append("Gate B blocked: Gate A modeling quality has not passed eligibility.")
        return {
            "gate": "GATE_B",
            "status": "BLOCKED",
            "animation_eligible": False,
            "evidence_verified": False,
            "reasons": reasons,
        }

    rendered_inspected = bool(review.get("rendered_images_inspected"))
    independent_reviewer = bool(review.get("independent_reviewer"))
    major_defects = list(review.get("major_defects") or [])
    checks = dict(review.get("checks") or {})

    has_embedded_evidence = ("visual_evidence" in review) or ("review_evidence" in review)
    evidence_verified = False
    evidence_invalid_status: str | None = None
    if strict_evidence or has_embedded_evidence:
        ev_ok, ev_status, ev_reasons = _verify_review_evidence_payload(review)
        evidence_verified = ev_ok
        if not ev_ok:
            evidence_invalid_status = ev_status or "NOT_VERIFIED"
            reasons.extend(ev_reasons)

    failed = [c for c in GATE_B_CHECKS if checks.get(c) == "FAIL"]
    missing = [c for c in GATE_B_CHECKS if checks.get(c) not in ("PASS", "FAIL")]

    if major_defects or failed:
        status = "FAIL"
        reasons.append(f"Rigging/deformation defects: {', '.join(major_defects + failed)}")
    elif evidence_invalid_status is not None:
        status = evidence_invalid_status
    elif not rendered_inspected or not independent_reviewer or missing:
        status = "NOT_VERIFIED"
        reasons.append("Gate B requires independent rendered deformation inspection across all checks.")
    else:
        status = "PASS"

    return {
        "gate": "GATE_B",
        "status": status,
        "animation_eligible": status == "PASS",
        "evidence_verified": evidence_verified,
        "reasons": reasons,
    }


def evaluate_gate_c(gate_b_result: dict[str, Any], review: dict[str, Any], *, strict_evidence: bool = False) -> dict[str, Any]:
    """Evaluate Gate C (Animation)."""
    reasons: list[str] = []
    if not gate_b_result.get("animation_eligible"):
        reasons.append("Gate C blocked: Gate B rigging and deformation has not passed.")
        return {
            "gate": "GATE_C",
            "status": "BLOCKED",
            "export_eligible": False,
            "evidence_verified": False,
            "reasons": reasons,
        }

    rendered_inspected = bool(review.get("rendered_images_inspected"))
    independent_reviewer = bool(review.get("independent_reviewer"))
    major_defects = list(review.get("major_defects") or [])
    checks = dict(review.get("checks") or {})

    has_embedded_evidence = ("visual_evidence" in review) or ("review_evidence" in review)
    evidence_verified = False
    evidence_invalid_status: str | None = None
    if strict_evidence or has_embedded_evidence:
        ev_ok, ev_status, ev_reasons = _verify_review_evidence_payload(review)
        evidence_verified = ev_ok
        if not ev_ok:
            evidence_invalid_status = ev_status or "NOT_VERIFIED"
            reasons.extend(ev_reasons)

    failed = [c for c in GATE_C_CHECKS if checks.get(c) == "FAIL"]
    missing = [c for c in GATE_C_CHECKS if checks.get(c) not in ("PASS", "FAIL")]

    if major_defects or failed:
        status = "FAIL"
        reasons.append(f"Animation defects: {', '.join(major_defects + failed)}")
    elif evidence_invalid_status is not None:
        status = evidence_invalid_status
    elif not rendered_inspected or not independent_reviewer or missing:
        status = "NOT_VERIFIED"
        reasons.append("Gate C requires independent full-playback inspection across all checks.")
    else:
        status = "PASS"

    return {
        "gate": "GATE_C",
        "status": status,
        "export_eligible": status == "PASS",
        "evidence_verified": evidence_verified,
        "reasons": reasons,
    }


def validate_required_skills(
    skills_root: Path | None = None,
    required_skills: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Verify gitignored .opencode/skills/ installation and surface remediation if missing."""
    root = Path(skills_root) if skills_root is not None else DEFAULT_SKILLS_ROOT
    req = list(required_skills) if required_skills is not None else list(REQUIRED_CORE_SKILLS)
    if not root.is_dir():
        return {
            "skills_root": str(root),
            "installed": False,
            "status": "SKILLS_MISSING",
            "missing_skills": req,
            "shared_references_present": False,
            "remediation_steps": [
                "Clone or locate upstream arjun988/blender-skills (.claude/skills).",
                f"Run ./scripts/install.sh --with-orchestration --skills-source <skills_dir> {PROJECT_ROOT}",
                "Do NOT claim skills were applied when .opencode/skills/ is absent.",
            ],
        }

    missing = [sid for sid in req if not (root / sid / "SKILL.md").is_file()]
    refs_ok = (root / "references" / "asset-pipeline.md").is_file()
    installed = (len(missing) == 0) and refs_ok
    return {
        "skills_root": str(root),
        "installed": installed,
        "status": "READY" if installed else "SKILLS_MISSING",
        "missing_skills": missing,
        "shared_references_present": refs_ok,
        "remediation_steps": (
            []
            if installed
            else [
                f"Missing required Blender Skills in {root}: {', '.join(missing) or 'shared references/'}",
                f"Install via ./scripts/install.sh --with-orchestration --skills-source <skills_dir> {PROJECT_ROOT}",
            ]
        ),
    }


def validate_benchmark_run_id(run_id: str) -> bool:
    """Validate YYYYMMDD_HHMMSS_MODELNAME directory/run naming convention."""
    return bool(_RUN_ID_PATTERN.match((run_id or "").strip()))


def create_isolated_benchmark_workspace(
    base_dir: Path,
    model_name: str,
    *,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Create an isolated YYYYMMDD_HHMMSS_MODELNAME benchmark workspace and runtime dirs."""
    safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", (model_name or "model").strip()).strip("_") or "model"
    ts = timestamp or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id = f"{ts}_{safe_model}"
    run_dir = Path(base_dir) / run_id
    runtime_dir = run_dir / "mcp_runtime"
    renders_dir = run_dir / "renders"
    checkpoints_dir = run_dir / "checkpoints"
    for d in (run_dir, runtime_dir, renders_dir, checkpoints_dir):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "run_id": run_id,
        "run_id_format_valid": validate_benchmark_run_id(run_id),
        "run_dir": str(run_dir),
        "runtime_dir": str(runtime_dir),
        "renders_dir": str(renders_dir),
        "checkpoints_dir": str(checkpoints_dir),
    }


def _compute_skills_hash(skills_root: Path) -> str | None:
    if not skills_root.is_dir():
        return None
    h = hashlib.sha256()
    for f in sorted(p for p in skills_root.rglob("*") if p.is_file()):
        rel = f.relative_to(skills_root).as_posix()
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        h.update(f"{rel}:{digest}\n".encode("utf-8"))
    return h.hexdigest()


def build_benchmark_metadata(
    prompt: str,
    *,
    run_id: str,
    model_id: str,
    provider: str = "",
    variant: str = "",
    reasoning_effort: str = "",
    inference_backend: str = "",
    hardware_allocation: str = "",
    runtime_environment: str = "",
    blender_version: str = "",
    mcp_server_version: str = "",
    skills_version: str = "",
    agents_md_path: Path | None = None,
    skills_root: Path | None = None,
    supplied_references: list[str] | None = None,
    text_specification: str | None = None,
    runtime_metrics: dict[str, Any] | None = None,
    repair_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build benchmark run metadata storing preflight resolution, hashes, and run parameters."""
    # Preflight is computed without model/provider to guarantee model independence.
    resolved_agents = Path(agents_md_path) if agents_md_path is not None else DEFAULT_AGENTS_MD
    resolved_skills = Path(skills_root) if skills_root is not None else DEFAULT_SKILLS_ROOT
    preflight = resolve_preflight_policy(
        prompt,
        agents_md_path=resolved_agents,
        supplied_references=supplied_references,
        text_specification=text_specification,
    )
    skills_preflight = validate_required_skills(resolved_skills)
    ref_hashes: dict[str, str | None] = {}
    for ref_str in (supplied_references or []):
        rp = Path(ref_str)
        ref_hashes[ref_str] = hashlib.sha256(rp.read_bytes()).hexdigest() if rp.is_file() else None

    return {
        "run_id": run_id,
        "run_id_format_valid": validate_benchmark_run_id(run_id),
        "prompt_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest(),
        "agents_md_sha256": hashlib.sha256(resolved_agents.read_bytes()).hexdigest() if resolved_agents.is_file() else None,
        "skills_revision_hash": _compute_skills_hash(resolved_skills),
        "skills_version": skills_version or None,
        "skills_preflight": skills_preflight,
        "mcp_lifecycle_version": DEFAULT_VERSION_FILE.read_text(encoding="utf-8").strip() if DEFAULT_VERSION_FILE.is_file() else None,
        "blender_version": blender_version or None,
        "mcp_server_version": mcp_server_version or None,
        "reference_assets_sha256": ref_hashes,
        "preflight": preflight,
        "repair_policy": dict(repair_policy or {}),
        "run_environment": {
            "model_id": model_id,
            "provider": provider,
            "variant": variant,
            "reasoning_effort": reasoning_effort,
            "inference_backend": inference_backend,
            "hardware_allocation": hardware_allocation,
            "runtime_environment": runtime_environment,
            "blender_version": blender_version,
            "mcp_server_version": mcp_server_version,
        },
        "runtime_metrics": dict(runtime_metrics or {}),
        "qa_gates": {
            "gate_a": None,
            "gate_b": None,
            "gate_c": None,
        },
    }


def _extract_separated_metrics(meta: dict[str, Any]) -> dict[str, Any]:
    rt = dict(meta.get("runtime_metrics") or {})
    gates = dict(meta.get("qa_gates") or {})
    return {
        "quality": {
            "gate_a": gates.get("gate_a"),
            "gate_b": gates.get("gate_b"),
            "gate_c": gates.get("gate_c"),
            "final_quality_summary": rt.get("final_quality_summary"),
            "qa_failure_count": rt.get("qa_failure_count", 0),
            "qa_fix_history": rt.get("qa_fix_history", []),
        },
        "runtime": {
            "total_runtime_sec": rt.get("total_runtime_sec"),
            "stage_durations_sec": rt.get("stage_durations_sec", {}),
            "compaction_events": rt.get("compaction_events", "unavailable"),
        },
        "tool_efficiency": {
            "skill_call_count": rt.get("skill_call_count", 0),
            "mcp_tool_call_count": rt.get("mcp_tool_call_count", 0),
            "python_execution_count": rt.get("python_execution_count", 0),
            "blender_error_count": rt.get("blender_error_count", 0),
            "retry_count": rt.get("retry_count", 0),
            "modeling_iteration_count": rt.get("modeling_iteration_count", 0),
        },
    }


def compare_run_environments(meta_a: dict[str, Any], meta_b: dict[str, Any]) -> dict[str, Any]:
    """Compare benchmark runs across prompt/reference parity, uncontrolled factors, and separated metrics."""
    env_a = dict(meta_a.get("run_environment") or {})
    env_b = dict(meta_b.get("run_environment") or {})
    tracked_fields = (
        "model_id",
        "provider",
        "variant",
        "reasoning_effort",
        "inference_backend",
        "hardware_allocation",
        "runtime_environment",
    )
    differences: dict[str, dict[str, Any]] = {}
    for field in tracked_fields:
        va = env_a.get(field)
        vb = env_b.get(field)
        if va != vb:
            differences[field] = {"run_a": va, "run_b": vb}

    identical_preflight = meta_a.get("preflight") == meta_b.get("preflight")
    identical_prompt = meta_a.get("prompt_sha256") == meta_b.get("prompt_sha256")
    identical_refs = meta_a.get("reference_assets_sha256", {}) == meta_b.get("reference_assets_sha256", {})
    identical_agents_skills = (
        meta_a.get("agents_md_sha256") == meta_b.get("agents_md_sha256")
        and meta_a.get("skills_revision_hash") == meta_b.get("skills_revision_hash")
    )

    disq: list[str] = []
    if not identical_prompt:
        disq.append("Prompt SHA-256 differs between runs; direct comparison is unfair.")
    if not identical_refs:
        disq.append("Supplied reference assets differ between runs; direct comparison is unfair.")
    if not identical_preflight:
        disq.append("Resolved preflight policies differ between runs.")
    if not identical_agents_skills:
        disq.append("AGENTS.md or Blender Skills revision hash differs between runs.")

    return {
        "identical_preflight_policy": identical_preflight,
        "identical_prompt": identical_prompt,
        "identical_references": identical_refs,
        "identical_agents_and_skills": identical_agents_skills,
        "fair_direct_comparison_allowed": len(disq) == 0,
        "disqualification_reasons": disq,
        "uncontrolled_differences": differences,
        "strict_experimental_equivalence_claimable": len(differences) == 0 and len(disq) == 0,
        "separated_metrics": {
            "run_a": _extract_separated_metrics(meta_a),
            "run_b": _extract_separated_metrics(meta_b),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve Blender benchmark preflight policy.")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Benchmark prompt text.")
    prompt_group.add_argument("--prompt-file", type=Path, help="Path to a benchmark prompt markdown/text file.")
    parser.add_argument("--agents-md", type=Path, default=DEFAULT_AGENTS_MD, help="Path to .opencode/AGENTS.md")
    parser.add_argument("--reference", action="append", dest="references", help="Supplied approved reference image path (repeatable).")
    parser.add_argument("--no-references", action="store_true", help="Explicitly pass an empty reference list [].")
    parser.add_argument("--run-id", default="", help="Optional benchmark run directory ID (YYYYMMDD_HHMMSS_MODELNAME).")
    parser.add_argument("--model-id", default="", help="Model ID recorded in run metadata only (does not affect preflight).")
    parser.add_argument("--provider", default="", help="Provider recorded in run metadata only.")
    parser.add_argument("--reasoning-effort", default="", help="Reasoning effort recorded in run metadata only.")
    parser.add_argument("--inference-backend", default="", help="Inference backend recorded in run metadata only.")
    parser.add_argument("--hardware-allocation", default="", help="Hardware allocation recorded in run metadata only.")
    parser.add_argument("--runtime-environment", default="", help="Runtime environment recorded in run metadata only.")
    parser.add_argument("--output", type=Path, help="Optional JSON file path to write benchmark metadata.")
    args = parser.parse_args(argv)

    prompt_text = args.prompt if args.prompt is not None else args.prompt_file.read_text(encoding="utf-8")
    supplied_refs: list[str] | None
    if args.no_references:
        supplied_refs = []
    elif args.references:
        supplied_refs = list(args.references)
    else:
        supplied_refs = None

    metadata = build_benchmark_metadata(
        prompt_text,
        run_id=args.run_id,
        model_id=args.model_id,
        provider=args.provider,
        reasoning_effort=args.reasoning_effort,
        inference_backend=args.inference_backend,
        hardware_allocation=args.hardware_allocation,
        runtime_environment=args.runtime_environment,
        agents_md_path=args.agents_md,
        supplied_references=supplied_refs,
    )

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(metadata["preflight"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
