"""Reproducibility manifests, performance metrics, and QA gate coordination."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
from pathlib import Path
import subprocess
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import benchmark_preflight as bp  # noqa: E402

from .skill_registry import SkillRegistry
from .visual_qa import VisualReviewEvidence, verify_visual_review_evidence


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(repo_root: Path) -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except OSError:
        pass
    return None


@dataclass
class PerformanceMetrics:
    """Execution telemetry separating quality, runtime, and tool efficiency."""

    total_runtime_sec: float = 0.0
    model_input_tokens: int | str = "unavailable"
    model_output_tokens: int | str = "unavailable"
    skill_activations: list[str] = field(default_factory=list)
    skill_applications: list[str] = field(default_factory=list)
    skill_call_count: int = 0
    mcp_calls_by_operation: dict[str, int] = field(default_factory=dict)
    mcp_tool_call_count: int = 0
    python_execution_count: int = 0
    blender_error_count: int = 0
    retry_count: int = 0
    modeling_iteration_count: int = 0
    qa_failure_count: int = 0
    qa_fix_history: list[dict[str, Any]] = field(default_factory=list)
    render_durations_sec: dict[str, float] = field(default_factory=dict)
    stage_durations_sec: dict[str, float] = field(default_factory=dict)
    compaction_events: int | str = "unavailable"
    final_quality_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_reproducibility_manifest(
    prompt: str,
    *,
    run_id: str,
    project_root: Path | None = None,
    agents_md_path: Path | None = None,
    skills_root: Path | None = None,
    mcp_config_path: Path | None = None,
    mcp_version_file: Path | None = None,
    mcp_server_info: dict[str, Any] | None = None,
    discovered_tools: list[str] | None = None,
    model_id: str = "unspecified",
    provider: str = "unspecified",
    variant: str = "unspecified",
    reasoning_settings: str = "unavailable",
    hardware_info: dict[str, Any] | str = "unavailable",
    blender_version: str = "unavailable",
    skills_version: str = "1.3.0",
    output_dir: str = "",
    supplied_references: list[str] | None = None,
) -> dict[str, Any]:
    """Capture prompt, AGENTS.md, Skills, MCP config/version, and run environment hashes."""
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    agents_path = Path(agents_md_path) if agents_md_path is not None else (root / ".opencode" / "AGENTS.md")
    skills_dir = Path(skills_root) if skills_root is not None else (root / ".opencode" / "skills")
    cfg_path = Path(mcp_config_path) if mcp_config_path is not None else (root / ".opencode" / "mcp" / "blender" / "config.json")
    ver_path = Path(mcp_version_file) if mcp_version_file is not None else (root / ".opencode" / "mcp" / "blender" / "VERSION")

    registry = SkillRegistry(skills_dir)
    discovered_skills = registry.discover()
    preflight = bp.resolve_preflight_policy(
        prompt,
        agents_md_path=agents_path,
        supplied_references=supplied_references,
    )
    skills_preflight = bp.validate_required_skills(skills_dir)
    ref_hashes = {
        ref: _sha256_file(Path(ref))
        for ref in (supplied_references or [])
    }

    mcp_version = ver_path.read_text(encoding="utf-8").strip() if ver_path.is_file() else None

    return {
        "run_id": run_id,
        "run_id_format_valid": bp.validate_benchmark_run_id(run_id),
        "output_dir": output_dir,
        "prompt_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest(),
        "agents_md_sha256": _sha256_file(agents_path),
        "git_revision": _git_head(root),
        "skills_root": str(skills_dir),
        "skills_count": len(discovered_skills),
        "skills_version": skills_version,
        "skills_revision_hash": registry.compute_revision_hash(),
        "skills_preflight": skills_preflight,
        "mcp_config_sha256": _sha256_file(cfg_path),
        "mcp_lifecycle_version": mcp_version,
        "mcp_server_info": dict(mcp_server_info or {}),
        "blender_version": blender_version,
        "discovered_tools": list(discovered_tools or []),
        "reference_assets_sha256": ref_hashes,
        "preflight": preflight,
        "run_environment": {
            "model_id": model_id,
            "provider": provider,
            "variant": variant,
            "reasoning_settings": reasoning_settings,
            "hardware_info": hardware_info,
            "blender_version": blender_version,
        },
    }


def evaluate_character_gates(
    preflight: dict[str, Any],
    *,
    gate_a_review: dict[str, Any],
    gate_b_review: dict[str, Any] | None = None,
    gate_c_review: dict[str, Any] | None = None,
    strict_evidence: bool = False,
) -> dict[str, Any]:
    """Coordinate mandatory Gate A -> Gate B -> Gate C evaluation."""
    gate_a = bp.evaluate_gate_a(preflight, gate_a_review, strict_evidence=strict_evidence)
    gate_b = bp.evaluate_gate_b(gate_a, gate_b_review or {}, strict_evidence=strict_evidence)
    gate_c = bp.evaluate_gate_c(gate_b, gate_c_review or {}, strict_evidence=strict_evidence)
    final_approved = (
        gate_a["modeling_quality_status"] == "PASS"
        and gate_b["status"] == "PASS"
        and gate_c["status"] == "PASS"
    )
    if preflight.get("benchmark_mode") == "REFERENCE_GUIDED":
        final_approved = final_approved and gate_a["exact_identity_fidelity_status"] == "PASS"

    return {
        "gate_a": gate_a,
        "gate_b": gate_b,
        "gate_c": gate_c,
        "final_approved": final_approved,
    }


def evaluate_verified_character_gates(
    preflight: dict[str, Any],
    *,
    gate_a_review: dict[str, Any],
    gate_b_review: dict[str, Any] | None = None,
    gate_c_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Strict Gate A -> B -> C coordinator requiring cryptographic VisualReviewEvidence."""
    return evaluate_character_gates(
        preflight,
        gate_a_review=gate_a_review,
        gate_b_review=gate_b_review,
        gate_c_review=gate_c_review,
        strict_evidence=True,
    )


def evaluate_asset_qa(
    *,
    rendered_images: list[str],
    visually_inspected: bool,
    independent_reviewer: bool,
    findings: list[str],
    major_defects: list[str],
    technical_checks_passed: bool,
    within_poly_budget: bool,
    review_evidence: dict[str, Any] | VisualReviewEvidence | None = None,
    expected_policy_digest: str | None = None,
    verify_files_on_disk: bool = True,
) -> dict[str, Any]:
    """Evaluate static asset QA requiring verifiable visual review evidence (anti-forging)."""
    reasons: list[str] = []
    ev_check = verify_visual_review_evidence(
        review_evidence,
        expected_policy_digest=expected_policy_digest,
        verify_files_on_disk=verify_files_on_disk,
    )

    if not rendered_images:
        status = "NOT_VERIFIED"
        reasons.append("No rendered or viewport screenshots captured.")
    elif not visually_inspected:
        status = "NOT_VERIFIED"
        reasons.append("Rendered file exists on disk, but visual inspection has not been performed.")
    elif not independent_reviewer:
        status = "NOT_VERIFIED"
        reasons.append("Independent visual reviewer is required.")
    elif major_defects or not technical_checks_passed or not within_poly_budget:
        status = "FAIL"
        if major_defects:
            reasons.extend(major_defects)
        if not technical_checks_passed:
            reasons.append("Technical export/mesh checks failed.")
        if not within_poly_budget:
            reasons.append("Asset exceeds polygon budget.")
    elif not ev_check["valid"]:
        status = str(ev_check["status_if_invalid"])
        reasons.extend(ev_check["reasons"])
    elif not findings:
        status = "NOT_VERIFIED"
        reasons.append("Visual review findings must be recorded explicitly.")
    else:
        status = "PASS"

    return {
        "status": status,
        "evidence_verified": ev_check["valid"],
        "rendered_images": rendered_images,
        "visually_inspected": visually_inspected,
        "independent_reviewer": independent_reviewer,
        "findings": findings,
        "major_defects": major_defects,
        "reasons": reasons,
    }
