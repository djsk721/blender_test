"""Frozen 100-point blind independent visual evaluator and stage reviewers.

Uses exclusively the configured fixed model (default: `openai/gpt-6.2-sol#low`)
in isolated `--standalone --archived` OpenCode sessions so visual reviewers never
share producer context and never see model/experiment labels during final scoring.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable

from .multimodal_qa import _extract_json_object, parse_opencode_json_events
from .visual_qa import hash_file_sha256

DEFAULT_LIGHT_MODEL_ID = "openai/gpt-6.2-sol#low"

FROZEN_RUBRIC_MAX_SCORES: dict[str, float] = {
    "reference_fidelity": 25.0,
    "silhouette_anatomy_proportions": 20.0,
    "geometry_modeling_detail": 20.0,
    "materials_and_textures": 15.0,
    "artistic_coherence_polish": 15.0,
    "game_asset_readability": 5.0,
}

FROZEN_MIN_TOTAL_SCORE = 85.0
FROZEN_MIN_CATEGORY_RATIO = 0.70

REQUIRED_DEFECT_FIELDS: tuple[str, ...] = (
    "affected_component",
    "visible_problem",
    "severity",
    "supporting_image_evidence",
    "suspected_cause",
    "recommended_correction",
    "required_production_stage",
    "relevant_skill",
    "acceptance_criteria",
)


@dataclass
class StructuredVisualDefect:
    """9-field structured defect record required by Section 8."""

    affected_component: str
    visible_problem: str
    severity: str  # CRITICAL | MAJOR | MINOR
    supporting_image_evidence: str
    suspected_cause: str
    recommended_correction: str
    required_production_stage: str
    relevant_skill: str
    acceptance_criteria: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ISOLATED_NO_MCP_OPENCODE_CONFIG: dict[str, Any] = {
    "$schema": "https://opencode.ai/config.json",
    "mcp": {
        "servers": {
            name: {"type": "local", "command": ["true"], "disabled": True}
            for name in ("photocraft", "effectcraft", "vectorcraft", "soundcraft", "aseprite", "blender", "rea")
        }
    },
}


def detect_quota_error(errors: list[str] | None) -> dict[str, Any] | None:
    """Detect provider HTTP 429 / usage_limit_reached quota exhaustion cleanly."""
    for err in errors or []:
        s = str(err)
        if "provider.quota" in s or "usage_limit_reached" in s or '"status_code":429' in s or '"status_code": 429' in s:
            import re

            m_sec = re.search(r'resets_in_seconds[^0-9]*([0-9]+)', s)
            m_at = re.search(r'resets_at[^0-9]*([0-9]+)', s)
            return {
                "quota_exceeded": True,
                "resets_in_seconds": int(m_sec.group(1)) if m_sec else None,
                "resets_at": int(m_at.group(1)) if m_at else None,
                "raw_excerpt": s[:500],
            }
    return None


def invoke_isolated_opencode_vision(
    model_id: str,
    prompt: str,
    image_paths: list[Path],
    *,
    timeout_sec: int = 120,
    title: str = "Independent Visual Evaluation",
) -> dict[str, Any]:
    """Run a fresh isolated OpenCode CLI session attaching actual images via --file.

    Writes an ephemeral project-local opencode.json inside the temp sandbox to disable
    unused global MCP tool schemas (~72K token savings per call) without touching global config.
    """
    cmd = [
        "opencode",
        "run",
        "--standalone",
        "--archived",
        "--format",
        "json",
        "--title",
        title,
        "-m",
        model_id,
    ]
    for p in image_paths:
        rp = Path(p).resolve()
        if rp.is_file():
            cmd.extend(["--file", str(rp)])
    cmd.append(prompt)

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="oc_eval_isolated_") as tmpdir:
        (Path(tmpdir) / "opencode.json").write_text(
            json.dumps(ISOLATED_NO_MCP_OPENCODE_CONFIG, indent=2),
            encoding="utf-8",
        )
        res = subprocess.run(
            cmd,
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    dur = round(time.monotonic() - t0, 3)
    parsed = parse_opencode_json_events(res.stdout)
    quota_info = detect_quota_error(parsed["errors"])
    return {
        "returncode": res.returncode,
        "duration_sec": dur,
        "session_id": parsed["session_id"],
        "text": parsed["text"],
        "tokens": parsed["tokens"],
        "compactions": parsed["compactions"],
        "errors": parsed["errors"],
        "quota_blocked": quota_info,
        "stderr": (res.stderr or "")[-400:],
    }


def _normalize_defect(raw: dict[str, Any], default_image: str) -> StructuredVisualDefect:
    sev = str(raw.get("severity") or "MAJOR").strip().upper()
    if sev not in ("CRITICAL", "MAJOR", "MINOR"):
        sev = "CRITICAL" if sev == "BLOCKER" else "MAJOR"
    return StructuredVisualDefect(
        affected_component=str(raw.get("affected_component") or "unspecified_component"),
        visible_problem=str(raw.get("visible_problem") or "Unspecified visual defect"),
        severity=sev,
        supporting_image_evidence=str(raw.get("supporting_image_evidence") or default_image),
        suspected_cause=str(raw.get("suspected_cause") or "Geometry or material mismatch"),
        recommended_correction=str(raw.get("recommended_correction") or "Refine component geometry/material"),
        required_production_stage=str(raw.get("required_production_stage") or "detailed_modeling"),
        relevant_skill=str(raw.get("relevant_skill") or "character-artist"),
        acceptance_criteria=str(raw.get("acceptance_criteria") or "Defect no longer visible in follow-up renders"),
    )


def evaluate_rubric_acceptance(
    scores: dict[str, float],
    defects: list[dict[str, Any]],
    *,
    technical_gates_passed: bool = True,
) -> dict[str, Any]:
    """Apply frozen acceptance thresholds without softening."""
    clamped: dict[str, float] = {}
    failed_categories: list[str] = []
    for cat, max_s in FROZEN_RUBRIC_MAX_SCORES.items():
        val = max(0.0, min(max_s, float(scores.get(cat, 0.0))))
        clamped[cat] = round(val, 2)
        if val < (max_s * FROZEN_MIN_CATEGORY_RATIO):
            failed_categories.append(f"{cat} ({val:.1f}/{max_s:.0f} < {max_s * FROZEN_MIN_CATEGORY_RATIO:.1f})")

    total = round(sum(clamped.values()), 2)
    critical_defects = [d for d in defects if str(d.get("severity", "")).upper() == "CRITICAL"]

    reasons: list[str] = []
    if total < FROZEN_MIN_TOTAL_SCORE:
        reasons.append(f"Total score {total:.1f}/100 < {FROZEN_MIN_TOTAL_SCORE:.0f}/100.")
    if failed_categories:
        reasons.append("Categories below 70% threshold: " + ", ".join(failed_categories))
    if critical_defects:
        reasons.append(f"{len(critical_defects)} unresolved CRITICAL defect(s).")
    if not technical_gates_passed:
        reasons.append("Mandatory technical gates did not pass.")

    passed = len(reasons) == 0
    return {
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL",
        "total_score": total,
        "category_scores": clamped,
        "failed_categories": failed_categories,
        "critical_defect_count": len(critical_defects),
        "reasons": reasons,
    }


class BlindRubricEvaluator:
    """Blind independent 100-point visual evaluator for final & baseline scoring."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_LIGHT_MODEL_ID,
        timeout_sec: int = 120,
        invoke_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.model_id = model_id
        self.timeout_sec = timeout_sec
        self._invoke = invoke_fn or invoke_isolated_opencode_vision

    def evaluate(
        self,
        *,
        renders: list[dict[str, Any]],
        producer_session_ids: set[str] | list[str] | None = None,
        reference_mode: str = "REFERENCE_GUIDED",
        approved_reference_path: Path | None = None,
        frozen_design_spec: str = "",
        technical_gates_passed: bool = True,
    ) -> dict[str, Any]:
        """Inspect actual rendered images blindly and compute anchored 100-point score."""
        image_paths: list[Path] = []
        image_hashes: dict[str, str] = {}
        view_labels: list[str] = []

        for r in renders:
            p = Path(str(r.get("path") or ""))
            if not p.is_file() or p.stat().st_size == 0:
                return {
                    "verdict": "NOT_VERIFIED",
                    "passed": False,
                    "reason": f"Render missing or empty on disk: {p}",
                    "total_score": None,
                    "category_scores": {},
                    "defects": [],
                }
            image_paths.append(p)
            image_hashes[p.name] = hash_file_sha256(p)
            view_labels.append(f"{r.get('view', p.stem)}: {p.name}")

        if not image_paths:
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "reason": "No rendered images supplied for independent visual evaluation.",
                "total_score": None,
                "category_scores": {},
                "defects": [],
            }

        attach = list(image_paths)
        ref_clause = ""
        if reference_mode == "REFERENCE_GUIDED":
            if approved_reference_path is None or not Path(approved_reference_path).is_file():
                return {
                    "verdict": "NOT_VERIFIED",
                    "passed": False,
                    "reason": "Approved reference image missing for REFERENCE_GUIDED evaluation.",
                    "total_score": None,
                    "category_scores": {},
                    "defects": [],
                }
            attach.append(Path(approved_reference_path))
            ref_clause = (
                f"REFERENCE_GUIDED mode: compare the rendered 3D asset strictly against the attached "
                f"reference image `{Path(approved_reference_path).name}` for the Reference fidelity (25) category."
            )
        else:
            ref_clause = (
                "TEXT_ONLY mode: evaluate the first category (`reference_fidelity`, max 25) strictly as "
                "compliance with the frozen textual design specification below."
            )

        # Blind prompt: intentionally omits model names, iteration numbers, previous scores, and implementation details
        prompt = "\n".join(
            [
                "You are a strict, blind, independent Senior Game Art Director performing visual QA on a 3D asset.",
                "Do NOT call any tools, bash, or MCP servers. Inspect ONLY the attached images.",
                "Be completely honest and critical. Do not inflate scores for primitive mannequins, untextured clay, missing facial features, or clipping.",
                ref_clause,
                f"Asset requirements / frozen specification:\n{frozen_design_spec[:1400]}" if frozen_design_spec else "",
                f"Attached rendered views: {', '.join(view_labels)}",
                "",
                "Score the asset on the FROZEN 100-point rubric:",
                "1. reference_fidelity (0..25): match to reference (or text spec) across face, hair/silhouette, costume/details, colors, and hero prop.",
                "   - 0-6: unrecognizable mannequin/wrong colors; 7-13: loose resemblance/missing iconic features; 14-17: recognizable with noticeable mismatches; 18-21: strong visual match; 22-25: faithful cohesive 3D realization.",
                "2. silhouette_anatomy_proportions (0..20): multi-view proportions, natural joint/part transitions, grounded stance, dynamic silhouette.",
                "3. geometry_modeling_detail (0..20): sculpted/layered 3D detail vs stacked primitives, facial relief/eyes/eyelids, volumetric hair clumps/braids, hands, layered clothing/trims.",
                "4. materials_and_textures (0..15): distinct stylized PBR response, painted/crisp eye & trim/emblem detail, AO depth, zero flat untextured clay.",
                "5. artistic_coherence_polish (0..15): unified aesthetic, clean joins, zero distracting clipping/seams, clean lighting and presentation.",
                "6. game_asset_readability (0..5): readable silhouette, grounded pivot, clear value hierarchy at gameplay distance.",
                "",
                "Return ONLY a single valid JSON object in this exact schema:",
                "{",
                '  "observations_by_view": {"<view_name>": ["concrete visual observation 1", "..."]},',
                '  "category_scores": {',
                '    "reference_fidelity": 0,',
                '    "silhouette_anatomy_proportions": 0,',
                '    "geometry_modeling_detail": 0,',
                '    "materials_and_textures": 0,',
                '    "artistic_coherence_polish": 0,',
                '    "game_asset_readability": 0',
                "  },",
                '  "category_justifications": {"reference_fidelity": "...", "silhouette_anatomy_proportions": "...", "geometry_modeling_detail": "...", "materials_and_textures": "...", "artistic_coherence_polish": "...", "game_asset_readability": "..."},',
                '  "defects": [',
                "    {",
                '      "affected_component": "...",',
                '      "visible_problem": "...",',
                '      "severity": "CRITICAL|MAJOR|MINOR",',
                '      "supporting_image_evidence": "<filename or view>",',
                '      "suspected_cause": "...",',
                '      "recommended_correction": "...",',
                '      "required_production_stage": "blockout|detailed_modeling|materials_and_textures|rigging|lighting_rendering",',
                '      "relevant_skill": "character-artist|hair-groom|sculpting|materials|texture-workflow|uv-workflow|lighting|prop-artist|creature-artist",',
                '      "acceptance_criteria": "..."',
                "    }",
                "  ],",
                '  "summary": "concise overall visual assessment"',
                "}",
            ]
        )

        try:
            raw = self._invoke(
                self.model_id,
                prompt,
                attach,
                timeout_sec=self.timeout_sec,
                title="Blind 100-Point Visual QA",
            )
        except Exception as exc:
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "reason": f"Independent evaluator invocation failed: {exc}",
                "total_score": None,
                "category_scores": {},
                "defects": [],
            }

        reviewer_sid = str(raw.get("session_id") or "").strip()
        if int(raw.get("returncode", 1)) != 0 or not reviewer_sid:
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "reason": f"Reviewer session failed (rc={raw.get('returncode')}, errors={raw.get('errors')}).",
                "total_score": None,
                "category_scores": {},
                "defects": [],
            }

        prod_set = set(producer_session_ids or [])
        if reviewer_sid in prod_set:
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "reason": f"Context isolation violation: reviewer session {reviewer_sid} matched producer session.",
                "total_score": None,
                "category_scores": {},
                "defects": [],
            }

        parsed = _extract_json_object(str(raw.get("text") or ""))
        if not parsed or not isinstance(parsed.get("category_scores"), dict):
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "reason": "Reviewer did not return structured JSON with category_scores.",
                "raw_text": str(raw.get("text") or "")[:600],
                "reviewer_session_id": reviewer_sid,
                "total_score": None,
                "category_scores": {},
                "defects": [],
            }

        obs = parsed.get("observations_by_view") if isinstance(parsed.get("observations_by_view"), dict) else {}
        if not obs:
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "reason": "Reviewer returned empty observations_by_view; image inspection not verified.",
                "reviewer_session_id": reviewer_sid,
                "total_score": None,
                "category_scores": {},
                "defects": [],
            }

        default_img = image_paths[0].name
        defects = [
            _normalize_defect(d, default_img).to_dict()
            for d in (parsed.get("defects") or [])
            if isinstance(d, dict)
        ]

        decision = evaluate_rubric_acceptance(
            parsed["category_scores"],
            defects,
            technical_gates_passed=technical_gates_passed,
        )
        return {
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "model_id": self.model_id,
            "reviewer_session_id": reviewer_sid,
            "duration_sec": raw.get("duration_sec"),
            "tokens": raw.get("tokens") or {},
            "reference_mode": reference_mode,
            "inspected_images": image_hashes,
            "observations_by_view": obs,
            "category_justifications": parsed.get("category_justifications") or {},
            "summary": str(parsed.get("summary") or ""),
            "defects": defects,
            **decision,
        }


def compose_review_sheets(renders: list[dict[str, Any]], out_dir: Path | None = None) -> list[Path]:
    """Pack up to 7 multi-view renders into 2 crisp labeled contact sheets (turnaround 2x2 + closeups 3x1).

    Reduces intermediate vision token usage by ~60% while showing all views side-by-side at readable detail.
    Falls back cleanly to individual PNG paths if Pillow is unavailable or <= 3 images exist.
    """
    valid = [(str(r.get("view") or Path(str(r["path"])).stem), Path(str(r["path"]))) for r in renders if Path(str(r.get("path") or "")).is_file()]
    if len(valid) <= 3:
        return [p for _, p in valid]
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return [p for _, p in valid]

    save_dir = Path(out_dir) if out_dir else valid[0][1].parent
    save_dir.mkdir(parents=True, exist_ok=True)
    wide = [(v, p) for v, p in valid if not v.startswith("closeup")]
    close = [(v, p) for v, p in valid if v.startswith("closeup")]
    if not wide:
        wide, close = valid[:4], valid[4:]

    def _make_grid(items: list[tuple[str, Path]], cols: int, tile_w: int, tile_h: int, fname: str) -> Path | None:
        if not items:
            return None
        rows = (len(items) + cols - 1) // cols
        canvas = Image.new("RGB", (cols * tile_w, rows * tile_h), (210, 214, 220))
        draw = ImageDraw.Draw(canvas)
        for idx, (label, path) in enumerate(items):
            r, c = divmod(idx, cols)
            try:
                im = Image.open(path).convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS)
            except Exception:
                im = Image.open(path).convert("RGB").resize((tile_w, tile_h))
            x0, y0 = c * tile_w, r * tile_h
            canvas.paste(im, (x0, y0))
            draw.rectangle([x0 + 6, y0 + 6, x0 + 180, y0 + 30], fill=(24, 28, 34))
            draw.text((x0 + 12, y0 + 11), f"{label} ({path.name})", fill=(245, 248, 252))
        out_p = save_dir / fname
        canvas.save(out_p, format="PNG")
        return out_p

    sheets: list[Path] = []
    s1 = _make_grid(wide[:4], 2, 512, 640, "_sheet_turnaround.png")
    if s1:
        sheets.append(s1)
    s2 = _make_grid(close[:3], min(3, max(1, len(close))), 448, 560, "_sheet_closeups.png")
    if s2:
        sheets.append(s2)
    return sheets or [p for _, p in valid]


class StageVisualReviewer:
    """Independent intermediate stage reviewer (Blockout Clay, Geometry Clay, Lookdev, Rig/Anim)."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_LIGHT_MODEL_ID,
        timeout_sec: int = 120,
        invoke_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.model_id = model_id
        self.timeout_sec = timeout_sec
        self._invoke = invoke_fn or invoke_isolated_opencode_vision

    def review_stage(
        self,
        *,
        stage_name: str,
        renders: list[dict[str, Any]],
        design_spec_summary: str,
        producer_session_ids: set[str] | list[str] | None = None,
        approved_reference_path: Path | None = None,
        prior_renders: list[dict[str, Any]] | None = None,
        pass_threshold_score: float = 75.0,
    ) -> dict[str, Any]:
        """Inspect stage renders in a fresh isolated session and return actionable defect instructions."""
        image_paths = [Path(str(r["path"])) for r in renders if Path(str(r.get("path") or "")).is_file()]
        if not image_paths:
            return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "No stage renders found."}

        attach = compose_review_sheets(renders)
        if approved_reference_path and Path(approved_reference_path).is_file():
            attach.append(Path(approved_reference_path))
        prior_names = []
        for pr in (prior_renders or [])[:1]:
            pp = Path(str(pr.get("path") or ""))
            if pp.is_file() and pp not in attach:
                attach.append(pp)
                prior_names.append(pp.name)

        stage_guidance = {
            "blockout": "Focus ONLY on overall proportions, silhouette, major mass placement, grounded feet/base, and head-to-body ratio. Ignore missing textures/fine details.",
            "detailed_modeling": "Focus ONLY on 3D clay geometry: facial anatomy (recessed eyes/eyelids, nose, lips, chin, ears), hair volume/clumps/braid (NO rope tubes or bald scalp), articulated hands/grip, layered clothing silhouettes, boots/base, and zero clipping/gaps. Ignore colors/textures.",
            "materials_and_textures": "Focus on full-color reference/spec match: color palette accuracy, crisp painted eyes/brows/lips/emblems, fabric/leather/gold/emissive PBR separation, AO depth, lighting clarity, and any remaining geometry/silhouette issues.",
            "rigging_and_animation": "Focus on clean joint deformation without mesh collapse/pinching, natural pose balance, and coherent animation keyframes.",
        }.get(stage_name, "Inspect visual quality and technical alignment against the design specification.")

        prompt = "\n".join(
            [
                f"You are an INDEPENDENT Visual QA Reviewer inspecting stage `{stage_name}` renders.",
                "Do NOT call any tools, bash, or MCP servers. Inspect ONLY the attached PNG images.",
                stage_guidance,
                f"Design spec summary:\n{design_spec_summary[:1200]}",
                f"Current stage renders: {', '.join(p.name for p in image_paths)}",
                f"Approved reference image attached: {Path(approved_reference_path).name}" if (approved_reference_path and Path(approved_reference_path).is_file()) else "",
                f"Prior revision renders attached for comparison: {', '.join(prior_names)}" if prior_names else "",
                "Return ONLY a valid JSON object:",
                "{",
                '  "stage_score_100": 0,',
                '  "passed": false,',
                '  "improved_over_prior": true,',
                '  "observations_by_view": {"<view>": ["observation"]},',
                '  "defects": [',
                "    {",
                '      "affected_component": "<exact mesh/part name>",',
                '      "visible_problem": "<what looks wrong in the render>",',
                '      "severity": "CRITICAL|MAJOR|MINOR",',
                '      "supporting_image_evidence": "<render filename>",',
                '      "suspected_cause": "<geometric/coordinate/shader cause>",',
                '      "recommended_correction": "<specific 3D coordinate/shape/material fix in Blender>",',
                f'      "required_production_stage": "{stage_name}",',
                '      "relevant_skill": "<blender skill id>",',
                '      "acceptance_criteria": "<verifiable visual criterion>"',
                "    }",
                "  ],",
                '  "summary": "concise verdict"',
                "}",
            ]
        )

        raw = self._invoke(
            self.model_id,
            prompt,
            attach,
            timeout_sec=self.timeout_sec,
            title=f"Stage Visual QA ({stage_name})",
        )
        sid = str(raw.get("session_id") or "").strip()
        if raw.get("quota_blocked"):
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "score": 0.0,
                "reviewer_session_id": sid,
                "defects": [],
                "quota_blocked": raw.get("quota_blocked"),
                "reason": "Stage reviewer hit provider quota limit.",
            }
        if int(raw.get("returncode", 1)) != 0 or not sid or sid in set(producer_session_ids or []):
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "score": 0.0,
                "reviewer_session_id": sid,
                "defects": [],
                "reason": "Stage reviewer failed or violated context isolation.",
            }
        parsed = _extract_json_object(str(raw.get("text") or ""))
        if not parsed:
            return {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "score": 0.0,
                "reviewer_session_id": sid,
                "defects": [],
                "reason": "Stage reviewer returned unparseable output.",
            }
        score = max(0.0, min(100.0, float(parsed.get("stage_score_100", 0.0))))
        defects = [_normalize_defect(d, image_paths[0].name).to_dict() for d in (parsed.get("defects") or []) if isinstance(d, dict)]
        has_critical = any(d["severity"] == "CRITICAL" for d in defects)
        passed = bool(parsed.get("passed", False)) and (not has_critical) and (score >= pass_threshold_score)
        return {
            "verdict": "PASS" if passed else "FAIL",
            "passed": passed,
            "score": round(score, 2),
            "improved_over_prior": parsed.get("improved_over_prior"),
            "reviewer_session_id": sid,
            "duration_sec": raw.get("duration_sec"),
            "tokens": raw.get("tokens") or {},
            "observations_by_view": parsed.get("observations_by_view") or {},
            "defects": defects,
            "summary": str(parsed.get("summary") or ""),
        }
