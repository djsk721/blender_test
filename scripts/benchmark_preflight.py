#!/usr/bin/env python3
"""Dynamic Benchmark Preflight Validation and QA Gate evaluator.

Resolves benchmark reference policy (TEXT_ONLY vs REFERENCE_GUIDED) strictly from
project instructions (.opencode/AGENTS.md) and the user benchmark prompt without
any model-specific routing or hardcoded overrides.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AGENTS_MD = PROJECT_ROOT / ".opencode" / "AGENTS.md"

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


@dataclass(frozen=True)
class ProjectPolicy:
    """Parsed instruction capabilities from .opencode/AGENTS.md."""

    agents_md_path: str
    exists: bool
    supports_text_only: bool
    supports_reference_guided: bool
    defines_precedence: bool
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
            requires_gate_a=False,
            requires_gate_b=False,
            requires_gate_c=False,
            requires_independent_visual_qa=False,
            has_unconditional_reference_blocker=False,
        )

    text = path.read_text(encoding="utf-8")
    has_text_only = "TEXT_ONLY" in text and "Mode B: Text-Only Production" in text
    has_ref_guided = "REFERENCE_GUIDED" in text and "Mode A: Reference-Guided Production" in text
    defines_precedence = "## Instruction Precedence" in text
    requires_gate_a = "Gate A" in text
    requires_gate_b = "Gate B" in text
    requires_gate_c = "Gate C" in text
    requires_independent_qa = "## Independent Visual QA" in text

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

    has_supplied_refs = bool(supplied_references)
    explicitly_empty_refs = supplied_references is not None and len(supplied_references) == 0

    text_spec_available = bool((text_specification or "").strip() or prompt_text.strip())
    precedence_steps = [
        "1. Explicit user benchmark requirements determine reference mode",
        "2. Project instructions (.opencode/AGENTS.md) define mode execution",
        "3. Installed Blender Skills supply stage-specific workflows",
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


def evaluate_gate_a(preflight: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Evaluate Gate A (Modeling Quality) and separate Exact Identity Fidelity."""
    mode = preflight.get("benchmark_mode")
    reasons: list[str] = []

    if preflight.get("status") == "BLOCKED" or not preflight.get("modeling_permitted"):
        blocker = preflight.get("blocker_reason") or "Preflight blocked modeling."
        reasons.append(blocker)
        identity_status = "BLOCKED" if mode == "REFERENCE_GUIDED" else preflight.get("default_exact_identity_status", "NOT_VERIFIED")
        return {
            "gate": "GATE_A",
            "benchmark_mode": mode,
            "modeling_quality_status": "NOT_VERIFIED",
            "exact_identity_fidelity_status": identity_status,
            "rigging_eligible": False,
            "animation_eligible": False,
            "reasons": reasons,
        }

    rendered_inspected = bool(review.get("rendered_images_inspected"))
    independent_reviewer = bool(review.get("independent_reviewer"))
    major_defects = list(review.get("major_defects") or [])
    dimensions = dict(review.get("dimensions") or {})

    failed_dims = [dim for dim in GATE_A_DIMENSIONS if dimensions.get(dim) == "FAIL"]
    missing_dims = [dim for dim in GATE_A_DIMENSIONS if dimensions.get(dim) not in ("PASS", "FAIL")]

    if major_defects or failed_dims:
        modeling_status = "FAIL"
        if major_defects:
            reasons.append(f"Major modeling defects reported: {', '.join(major_defects)}")
        if failed_dims:
            reasons.append(f"Failed Gate A dimensions: {', '.join(failed_dims)}")
    elif not rendered_inspected or not independent_reviewer:
        modeling_status = "NOT_VERIFIED"
        if not rendered_inspected:
            reasons.append("Rendered images were not inspected (script execution alone cannot approve Gate A).")
        if not independent_reviewer:
            reasons.append("Independent visual reviewer is required; self-approval is disallowed.")
    elif missing_dims:
        modeling_status = "NOT_VERIFIED"
        reasons.append(f"Incomplete Gate A dimension evaluations: {', '.join(missing_dims)}")
    else:
        modeling_status = "PASS"

    # Evaluate exact original-character identity fidelity separately
    if mode == "TEXT_ONLY":
        # Never grant automatic PASS for exact original-character likeness in TEXT_ONLY mode
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
        "reasons": reasons,
    }


def evaluate_gate_b(gate_a_result: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Evaluate Gate B (Rigging and Deformation)."""
    reasons: list[str] = []
    if not gate_a_result.get("rigging_eligible"):
        reasons.append("Gate B blocked: Gate A modeling quality has not passed eligibility.")
        return {
            "gate": "GATE_B",
            "status": "BLOCKED",
            "animation_eligible": False,
            "reasons": reasons,
        }

    rendered_inspected = bool(review.get("rendered_images_inspected"))
    independent_reviewer = bool(review.get("independent_reviewer"))
    major_defects = list(review.get("major_defects") or [])
    checks = dict(review.get("checks") or {})

    failed = [c for c in GATE_B_CHECKS if checks.get(c) == "FAIL"]
    missing = [c for c in GATE_B_CHECKS if checks.get(c) not in ("PASS", "FAIL")]

    if major_defects or failed:
        status = "FAIL"
        reasons.append(f"Rigging/deformation defects: {', '.join(major_defects + failed)}")
    elif not rendered_inspected or not independent_reviewer or missing:
        status = "NOT_VERIFIED"
        reasons.append("Gate B requires independent rendered deformation inspection across all checks.")
    else:
        status = "PASS"

    return {
        "gate": "GATE_B",
        "status": status,
        "animation_eligible": status == "PASS",
        "reasons": reasons,
    }


def evaluate_gate_c(gate_b_result: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Evaluate Gate C (Animation)."""
    reasons: list[str] = []
    if not gate_b_result.get("animation_eligible"):
        reasons.append("Gate C blocked: Gate B rigging and deformation has not passed.")
        return {
            "gate": "GATE_C",
            "status": "BLOCKED",
            "export_eligible": False,
            "reasons": reasons,
        }

    rendered_inspected = bool(review.get("rendered_images_inspected"))
    independent_reviewer = bool(review.get("independent_reviewer"))
    major_defects = list(review.get("major_defects") or [])
    checks = dict(review.get("checks") or {})

    failed = [c for c in GATE_C_CHECKS if checks.get(c) == "FAIL"]
    missing = [c for c in GATE_C_CHECKS if checks.get(c) not in ("PASS", "FAIL")]

    if major_defects or failed:
        status = "FAIL"
        reasons.append(f"Animation defects: {', '.join(major_defects + failed)}")
    elif not rendered_inspected or not independent_reviewer or missing:
        status = "NOT_VERIFIED"
        reasons.append("Gate C requires independent full-playback inspection across all checks.")
    else:
        status = "PASS"

    return {
        "gate": "GATE_C",
        "status": status,
        "export_eligible": status == "PASS",
        "reasons": reasons,
    }


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
    agents_md_path: Path | None = None,
    supplied_references: list[str] | None = None,
    text_specification: str | None = None,
) -> dict[str, Any]:
    """Build benchmark run metadata storing preflight resolution and run parameters."""
    # Preflight is computed without model/provider to guarantee model independence.
    preflight = resolve_preflight_policy(
        prompt,
        agents_md_path=agents_md_path,
        supplied_references=supplied_references,
        text_specification=text_specification,
    )
    return {
        "run_id": run_id,
        "preflight": preflight,
        "run_environment": {
            "model_id": model_id,
            "provider": provider,
            "variant": variant,
            "reasoning_effort": reasoning_effort,
            "inference_backend": inference_backend,
            "hardware_allocation": hardware_allocation,
            "runtime_environment": runtime_environment,
        },
        "qa_gates": {
            "gate_a": None,
            "gate_b": None,
            "gate_c": None,
        },
    }


def compare_run_environments(meta_a: dict[str, Any], meta_b: dict[str, Any]) -> dict[str, Any]:
    """Compare uncontrollable execution factors across two benchmark runs."""
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

    return {
        "identical_preflight_policy": meta_a.get("preflight") == meta_b.get("preflight"),
        "uncontrolled_differences": differences,
        "strict_experimental_equivalence_claimable": len(differences) == 0,
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
