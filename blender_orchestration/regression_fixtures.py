"""Read-only regression fixtures from output/zelda_reference/ and benchmark isolation guard."""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from .qa_profiles import AssetQAProfileType, analyze_character_scene_metrics, get_qa_profile
from .visual_qa import (
    ImageObservation,
    RepairLoopPolicy,
    VisualDefect,
    VisualReviewEvidence,
    compute_approval_policy_digest,
    hash_file_sha256,
    plan_blender_fixes_for_defects,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ZELDA_REF_DIR = PROJECT_ROOT / "output" / "zelda_reference"


class BenchmarkIsolationError(PermissionError):
    """Raised when an independent model benchmark attempts to inspect another run or regression reference."""


class BenchmarkIsolationGuard:
    """Enforces strict isolation between REGRESSION_REPLAY and INDEPENDENT_BENCHMARK runs."""

    def __init__(
        self,
        *,
        run_kind: str,
        workspace_dir: Path,
        regression_dir: Path | None = None,
    ) -> None:
        if run_kind not in ("REGRESSION_REPLAY", "INDEPENDENT_BENCHMARK"):
            raise ValueError(f"Unsupported run_kind: {run_kind!r}")
        self.run_kind = run_kind
        self.workspace_dir = Path(workspace_dir).resolve()
        self.regression_dir = (Path(regression_dir) if regression_dir else DEFAULT_ZELDA_REF_DIR).resolve()

    def validate_read_path(self, path: Path | str) -> Path:
        target = Path(path).resolve()
        if self.run_kind == "INDEPENDENT_BENCHMARK":
            if target == self.regression_dir or self.regression_dir in target.parents:
                raise BenchmarkIsolationError(
                    f"INDEPENDENT_BENCHMARK cannot read regression baseline directory: {target}"
                )
            # Also block reading sibling model run directories under the same parent
            parent = self.workspace_dir.parent
            if parent in target.parents and self.workspace_dir not in target.parents and target != self.workspace_dir:
                raise BenchmarkIsolationError(
                    f"INDEPENDENT_BENCHMARK cannot inspect sibling benchmark run output: {target}"
                )
        return target


def _read_git_blob_text(commit: str, rel_path: str) -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "show", f"{commit}:{rel_path}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout
    except OSError:
        pass
    return None


def load_zelda_regression_snapshot(zelda_dir: Path | None = None) -> dict[str, Any]:
    """Load read-only Zelda reference artifacts from disk or historical git commit 2c9f916."""
    root = Path(zelda_dir) if zelda_dir is not None else DEFAULT_ZELDA_REF_DIR
    text_files = [
        "QA_report.md",
        "technical_audit.json",
        "export_validation.json",
        "preflight.json",
    ]
    binary_files = [
        "comparison.png",
        "hero.png",
        "detail.png",
        "three_quarter.png",
        "zelda_reference.blend",
    ]
    required_files = text_files + binary_files
    all_on_disk = all((root / name).is_file() for name in required_files)

    if all_on_disk:
        audit = json.loads((root / "technical_audit.json").read_text(encoding="utf-8"))
        export_val = json.loads((root / "export_validation.json").read_text(encoding="utf-8"))
        preflight_meta = json.loads((root / "preflight.json").read_text(encoding="utf-8"))
        qa_report = (root / "QA_report.md").read_text(encoding="utf-8")
        file_hashes = {name: hash_file_sha256(root / name) for name in required_files}
        source = "disk"
    else:
        # Read-only fallback to historical baseline commit 2c9f916 after f74d861 gitignored output/
        hist_texts = {
            name: _read_git_blob_text("2c9f916", f"output/zelda_reference/{name}")
            for name in text_files
        }
        if not all(hist_texts.values()):
            missing = [name for name in required_files if not (root / name).is_file()]
            raise FileNotFoundError(f"Missing expected Zelda regression files in {root} and git 2c9f916: {missing}")

        audit = json.loads(str(hist_texts["technical_audit.json"]))
        export_val = json.loads(str(hist_texts["export_validation.json"]))
        preflight_meta = json.loads(str(hist_texts["preflight.json"]))
        qa_report = str(hist_texts["QA_report.md"])
        file_hashes = {
            name: hashlib.sha256(str(hist_texts[name]).encode("utf-8")).hexdigest()
            for name in text_files
        }
        for bname in binary_files:
            file_hashes[bname] = hashlib.sha256(f"2c9f916:output/zelda_reference/{bname}".encode("utf-8")).hexdigest()
        source = "git_history_2c9f916"

    ref_img = PROJECT_ROOT / "images.jpg"
    if not ref_img.is_file():
        ref_img = root.parent.parent / "images.jpg"
    if ref_img.is_file():
        file_hashes["images.jpg"] = hash_file_sha256(ref_img)

    return {
        "root": str(root),
        "source": source,
        "qa_report_excerpt": qa_report[:800],
        "technical_audit": audit,
        "export_validation": export_val,
        "preflight_meta": preflight_meta,
        "file_hashes": file_hashes,
        "reference_image_path": str(ref_img) if ref_img.is_file() else None,
    }


def build_zelda_regression_defects(zelda_dir: Path | None = None) -> list[VisualDefect]:
    """Construct structured VisualDefect records for all 9 documented GPT Zelda failures."""
    root = Path(zelda_dir) if zelda_dir is not None else DEFAULT_ZELDA_REF_DIR
    detail_png = str(root / "detail.png")
    three_q_png = str(root / "three_quarter.png")
    comp_png = str(root / "comparison.png")

    return [
        VisualDefect(
            defect_id="ZLD-001",
            defect_type="reference_match_gap",
            severity="BLOCKER",
            affected_objects=["Zelda_Reference_Study"],
            source_render=comp_png,
            visual_problem="Overall Reference Match rated 7/10 (< 8/10 minimum threshold).",
            required_fix_action="Resolve facial, hair, hand, and lower-body discrepancies to reach >= 8/10.",
            qa_criterion_id="reference_identity_match",
        ),
        VisualDefect(
            defect_id="ZLD-002",
            defect_type="round_protruding_eyes",
            severity="MAJOR",
            affected_objects=["SM_Study_Head_Base", "SM_Study_Eye_Near_Sclera", "SM_Study_Eye_Far_Sclera"],
            source_render=detail_png,
            visual_problem="Overly round eyes with heavy flat outlines, broad forehead, and soft nose/cheek planes.",
            required_fix_action="Recess eyes into orbital sockets, reshape almond eyelid wrap, and refine cheek/nose planes.",
            qa_criterion_id="non_protruding_eyes_and_almond_contour",
        ),
        VisualDefect(
            defect_id="ZLD-003",
            defect_type="scalp_exposure",
            severity="BLOCKER",
            affected_objects=["SM_Study_Hair_Scalpcap", "SM_Study_Hair_Braid_A00"],
            source_render=detail_png,
            visual_problem="Skin-colored bald patch breaks the scalp cap behind the crown braid.",
            required_fix_action="Close Hair_Scalpcap gap behind crown braid and pull root hair clumps over exposed skin.",
            qa_criterion_id="scalp_coverage_no_bald_exposure",
        ),
        VisualDefect(
            defect_id="ZLD-004",
            defect_type="rope_like_hair",
            severity="MAJOR",
            affected_objects=["SM_Study_Hair_LeftMain", "SM_Study_Hair_LeftBlade", "SM_Study_Hair_Braid_A00"],
            source_render=three_q_png,
            visual_problem="Hair locks read as uniform rope-like tubes and braid reads as stacked spheres.",
            required_fix_action="Flatten/taper hair clumps into sculpted ribbon volumes with varied silhouette flow.",
            qa_criterion_id="sculpted_clump_volume_not_rope_tube",
        ),
        VisualDefect(
            defect_id="ZLD-005",
            defect_type="stiff_fingers_bad_grip",
            severity="MAJOR",
            affected_objects=["SM_Study_Finger_Glove_0", "SM_Study_Slate"],
            source_render=detail_png,
            visual_problem="Fingers are stiff segmented tubes lying parallel across the Sheikah Slate instead of wrapping.",
            required_fix_action="Join fingers to sculpted palm knuckles and curl phalanges around the Sheikah Slate edges.",
            qa_criterion_id="held_object_finger_wrap_contact",
        ),
        VisualDefect(
            defect_id="ZLD-006",
            defect_type="bulbous_boots",
            severity="MAJOR",
            affected_objects=["SM_Study_Long_Boot", "SM_Study_Raised_Boot", "SM_Study_Long_BootCollar"],
            source_render=three_q_png,
            visual_problem="Boots are overly rounded/bulbous without distinct heel, instep arch, or fitted ankle strap.",
            required_fix_action="Pinch ankle collars, flatten sole/heel planes, and sculpt angular toe-cap silhouettes.",
            qa_criterion_id="boot_instep_heel_ankle_anatomy",
        ),
        VisualDefect(
            defect_id="ZLD-007",
            defect_type="floating_thigh_seam",
            severity="MAJOR",
            affected_objects=["SM_Study_Leg_Long_Trouser", "SM_Study_TunicTailB_Edge"],
            source_render=three_q_png,
            visual_problem="A costume seam strip stands away and floats off the outer thigh in the three-quarter view.",
            required_fix_action="Snap floating seam trim vertices onto the outer trouser surface with controlled offset.",
            qa_criterion_id="thigh_knee_ankle_seam_attachment_no_floating_or_clipping",
        ),
        VisualDefect(
            defect_id="ZLD-008",
            defect_type="stiff_shoulders_knees",
            severity="MAJOR",
            affected_objects=["SM_Study_Sleeve_Left", "SM_Study_Sleeve_Right", "SM_Study_Leg_Long_Trouser"],
            source_render=comp_png,
            visual_problem="Bulky rounded upper sleeves and stiff shoulder/knee contours.",
            required_fix_action="Refine shoulder slope and knee anatomical inflection with proper joint edge flow.",
            qa_criterion_id="shoulder_waist_pelvis_leg_proportions",
        ),
        VisualDefect(
            defect_id="ZLD-009",
            defect_type="triangle_budget_overrun",
            severity="BLOCKER",
            affected_objects=["Zelda_Reference_Study"],
            source_render=comp_png,
            visual_problem="Source mesh evaluated at 121,752 triangles (> 100,000 budget) across 230 fragmented meshes and 32 materials.",
            required_fix_action="Merge contiguous primitive fragments and reduce non-deforming decorative triangles within budget.",
            qa_criterion_id="triangle_budget_within_limit",
        ),
    ]


def evaluate_zelda_regression_case(zelda_dir: Path | None = None) -> dict[str, Any]:
    """Evaluate output/zelda_reference/ deterministically through the improved QA harness."""
    snap = load_zelda_regression_snapshot(zelda_dir)
    root = Path(snap["root"])
    audit = snap["technical_audit"]
    profile = get_qa_profile("character", animated=False, reference_mode="REFERENCE_GUIDED")
    policy = RepairLoopPolicy(max_fix_iterations=3)
    policy_digest = compute_approval_policy_digest(profile, policy)

    # 1. Automated geometry & budget analysis from technical_audit.json
    # Build synthetic mesh list summary from audit if per-mesh breakdown is absent
    mesh_summary = {
        "mesh_objects": audit.get("mesh_objects", 0),
        "triangles": audit.get("triangles", 0),
        "material_count": audit.get("material_count", 0),
        "meshes": [],
    }
    geom_analysis = analyze_character_scene_metrics(
        mesh_summary,
        triangle_budget=int(audit.get("triangle_budget", 100000)),
        material_budget=12,
        max_fragment_objects=60,
    )

    # 2. Structured visual defects + verifiable evidence bound to real PNG & .blend hashes
    defects = build_zelda_regression_defects(root)
    fix_actions = plan_blender_fixes_for_defects(defects)

    ref_path = snap["reference_image_path"]
    ref_hash = snap["file_hashes"].get("images.jpg")

    observations = [
        ImageObservation(
            image_path=str(root / "comparison.png"),
            image_sha256=snap["file_hashes"]["comparison.png"],
            view_angle="reference_matched",
            observations=[
                "Reference match 7/10; upper sleeves overly bulky and shoulders/knees stiff.",
                "Overall silhouette/pose close, but face, hair, hands, and lower body fail likeness.",
            ],
            compared_reference_path=ref_path,
            compared_reference_sha256=ref_hash,
        ),
        ImageObservation(
            image_path=str(root / "detail.png"),
            image_sha256=snap["file_hashes"]["detail.png"],
            view_angle="face_closeup",
            observations=[
                "Round doll-like eyes with heavy outlines and flat nose/cheek planes.",
                "Skin-colored bald patch exposed behind crown braid on upper-left scalp.",
                "Free and gripping fingers look stiff/segmented and lie parallel on slate.",
            ],
            compared_reference_path=ref_path,
            compared_reference_sha256=ref_hash,
        ),
        ImageObservation(
            image_path=str(root / "three_quarter.png"),
            image_sha256=snap["file_hashes"]["three_quarter.png"],
            view_angle="three_quarter",
            observations=[
                "Hair locks appear rope-like and uniform.",
                "Outer thigh seam strip floats detached from trouser surface.",
                "Boots appear overly rounded/bulbous without fitted ankle collar.",
            ],
            compared_reference_path=ref_path,
            compared_reference_sha256=ref_hash,
        ),
    ]

    verdicts = {c.criterion_id: "PASS" for c in profile.criteria}
    for d in defects:
        if d.qa_criterion_id:
            verdicts[d.qa_criterion_id] = "FAIL"
    verdicts["facial_feature_placement"] = "FAIL"
    verdicts["face_3d_surface_continuity"] = "FAIL"
    verdicts["hair_flow_direction_and_silhouette"] = "FAIL"
    verdicts["no_bloated_or_ballooned_costume_and_boots"] = "FAIL"
    verdicts["sculpted_form_vs_primitive_stacking"] = "FAIL"

    evidence = VisualReviewEvidence(
        reviewer_id="regression_fixture_independent_reviewer",
        reviewer_context_id="ses_df21568fdffeTFCCm5JetCN3nL",
        producer_context_id="zelda_reference_producer_gpt",
        reviewed_at="2026-11-24T23:18:00+09:00",
        target_asset_path=str(root / "zelda_reference.blend"),
        target_asset_sha256=snap["file_hashes"]["zelda_reference.blend"],
        profile_id=AssetQAProfileType.HUMANOID_CHARACTER,
        reference_mode="REFERENCE_GUIDED",
        image_observations=observations,
        defects=defects,
        criteria_verdicts=verdicts,
        approval_policy_digest=policy_digest,
        reference_score=(7, 10),
        reference_comparison_performed=True,
        approved_references_used=True,
    )

    return {
        "snapshot": snap,
        "profile": profile,
        "policy_digest": policy_digest,
        "geometry_analysis": geom_analysis,
        "defects": defects,
        "fix_actions": fix_actions,
        "review_evidence": evidence,
        "gate_a_review": {
            "rendered_images_inspected": True,
            "independent_reviewer": True,
            "reference_comparison_performed": True,
            "approved_references_used": True,
            "reference_identity_verdict": "FAIL",
            "major_defects": [f"{d.defect_id}: {d.visual_problem}" for d in defects if d.severity in ("BLOCKER", "MAJOR")],
            "dimensions": {
                "silhouette": "PASS",
                "facial_proportions": "FAIL",
                "eyes_nose_mouth_ears": "FAIL",
                "hair_construction": "FAIL",
                "anatomy": "FAIL",
                "costume": "FAIL",
                "materials": "PASS",
                "character_readability": "PASS",
                "text_spec_compliance": "PASS",
            },
            "visual_evidence": evidence.to_dict(),
            "expected_policy_digest": policy_digest,
            "verify_files_on_disk": snap["source"] == "disk",
        },
    }
