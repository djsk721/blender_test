"""Independent multimodal visual QA reviewer wired to isolated OpenCode CLI sessions.

Prevents self-approval and rubber-stamping by:
1. Attaching actual rendered PNG files (plus optional approved reference image) to a
   separate, archived OpenCode session (`reviewer_context_id != producer_context_id`).
2. Failing closed (`NOT_VERIFIED`) whenever images are missing, unreadable, multimodal
   inspection fails, or observations are empty.
3. Cross-checking reviewer prose against deterministic Blender scene/mesh audits so
   technical failures or unresolved BLOCKER/MAJOR defects always block approval (`FAIL`).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Callable

from .qa_profiles import QAProfile, analyze_character_scene_metrics
from .visual_qa import (
    ImageObservation,
    VisualDefect,
    VisualReviewEvidence,
    hash_file_sha256,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

SEVEN_QA_DOMAINS: tuple[str, ...] = (
    "technical_mesh_validity",
    "face_and_body_sculpt_quality",
    "identity_or_reference_fidelity",
    "texture_and_material_quality",
    "rigging_and_deformation_quality",
    "animation_quality",
    "engine_export_compatibility",
)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the last valid JSON object from assistant text."""
    if not text:
        return None
    for match in reversed(list(_JSON_FENCE_RE.finditer(text))):
        try:
            obj = json.loads(match.group(1))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    # Fallback: try raw braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


def _clip(value: Any, limit: int = 500) -> str:
    """Coerce to a trimmed string clipped to `limit` characters."""
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = text.strip()
    return text[:limit]


def _normalise_reference_checks(raw: Any) -> list[dict[str, Any]]:
    """Normalise a reviewer's per-reference check list.

    Keeps only well-formed entries (a dict naming the attached reference file),
    clips long strings, and coerces ``checks_performed`` to a list of strings.
    Anything that is not a list of such dicts yields an empty list, so a missing
    or malformed field never raises.
    """
    if not isinstance(raw, list):
        return []
    checks: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        reference_file = _clip(entry.get("reference_file"), 200)
        if not reference_file:
            continue
        performed = entry.get("checks_performed")
        if isinstance(performed, list):
            performed = [_clip(item, 200) for item in performed if _clip(item)]
        else:
            performed = [_clip(performed, 200)] if _clip(performed) else []
        checks.append(
            {
                "reference_file": reference_file,
                "role": _clip(entry.get("role"), 40),
                "checks_performed": performed,
                "match": _clip(entry.get("match"), 20),
                "notes": _clip(entry.get("notes"), 500),
            }
        )
    return checks


def parse_opencode_json_events(stdout_text: str) -> dict[str, Any]:
    """Parse OpenCode `--format json` JSONL stream into session metadata & final assistant text."""
    session_id = ""
    text_chunks: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    errors: list[str] = []
    compactions = 0
    tokens: dict[str, Any] = {}

    for raw_line in (stdout_text or "").splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not session_id and ev.get("sessionID"):
            session_id = str(ev["sessionID"])
        etype = ev.get("type")
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        if etype == "text" and isinstance(part.get("text"), str):
            text_chunks.append(part["text"])
        elif etype == "tool_use":
            tool_uses.append(part)
        elif etype == "step_finish":
            if isinstance(ev.get("tokens"), dict):
                tokens = dict(ev["tokens"])
            elif isinstance(part.get("tokens"), dict):
                tokens = dict(part["tokens"])
        elif etype == "compaction":
            compactions += 1
        elif etype == "error":
            errors.append(json.dumps(ev))

    return {
        "session_id": session_id,
        "text": "\n".join(text_chunks).strip(),
        "tool_uses": tool_uses,
        "errors": errors,
        "compactions": compactions,
        "tokens": tokens,
    }


def default_opencode_vision_invoke(
    reviewer_model: str,
    prompt: str,
    image_paths: list[Path],
    *,
    cwd: Path,
    timeout_sec: int,
) -> dict[str, Any]:
    """Invoke a separate archived OpenCode session attaching rendered PNGs via --file."""
    cmd = [
        "opencode",
        "run",
        "--standalone",
        "--archived",
        "--format",
        "json",
        "--title",
        "Independent Character Visual QA",
        "-m",
        reviewer_model,
    ]
    for p in image_paths:
        cmd.extend(["--file", str(Path(p).resolve())])
    cmd.append(prompt)

    with tempfile.TemporaryDirectory(prefix="oc_isolated_qa_") as tmpdir:
        res = subprocess.run(
            cmd,
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    parsed = parse_opencode_json_events(res.stdout)
    return {
        "returncode": res.returncode,
        "session_id": parsed["session_id"],
        "text": parsed["text"],
        "errors": parsed["errors"],
        "stderr": res.stderr[-500:] if res.stderr else "",
    }


class IndependentMultimodalReviewer:
    """Runs independent multimodal inspection over real Blender renders + scene audit."""

    def __init__(
        self,
        *,
        reviewer_model: str = "openai/gpt-6.2-sol",
        cwd: Path | None = None,
        timeout_sec: int = 120,
        invoke_fn: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.reviewer_model = reviewer_model
        self.cwd = Path(cwd) if cwd is not None else PROJECT_ROOT
        self.timeout_sec = timeout_sec
        self._invoke_fn = invoke_fn or default_opencode_vision_invoke

    def inspect_renders(
        self,
        *,
        renders: list[dict[str, Any]],
        target_asset_path: Path,
        producer_context_id: str,
        profile: QAProfile,
        policy_digest: str,
        scene_audit: dict[str, Any] | None = None,
        approved_reference_path: Path | None = None,
        additional_references: list[Path] | None = None,
        prior_renders: list[dict[str, Any]] | None = None,
        text_specification: str = "",
    ) -> dict[str, Any]:
        """Inspect actual rendered PNG files and return verified VisualReviewEvidence + 7-domain verdicts.

        `approved_reference_path` is the PRIMARY reference (its hash is recorded in
        the evidence chain); `additional_references` are supplementary approved
        references attached alongside it so every approved reference reaches the
        reviewer.
        """
        image_files: list[Path] = []
        render_meta_by_path: dict[str, dict[str, Any]] = {}
        for r in renders:
            p = Path(str(r.get("path") or ""))
            if not p.is_file() or p.stat().st_size == 0:
                return {
                    "status": "NOT_VERIFIED",
                    "reason": f"Rendered image missing or empty on disk: {p}",
                    "review_evidence": None,
                    "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
                }
            image_files.append(p)
            render_meta_by_path[str(p)] = dict(r)

        if not image_files:
            return {
                "status": "NOT_VERIFIED",
                "reason": "No rendered images provided for visual inspection.",
                "review_evidence": None,
                "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
            }

        if not target_asset_path.is_file():
            return {
                "status": "NOT_VERIFIED",
                "reason": f"Target .blend asset missing on disk: {target_asset_path}",
                "review_evidence": None,
                "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
            }

        attach_files = list(image_files)
        ref_hash: str | None = None
        supplementary_refs: list[Path] = []
        if profile.reference_mode == "REFERENCE_GUIDED":
            if approved_reference_path is None or not approved_reference_path.is_file():
                return {
                    "status": "NOT_VERIFIED",
                    "reason": "REFERENCE_GUIDED inspection requires an existing approved reference image on disk.",
                    "review_evidence": None,
                    "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
                }
            ref_hash = hash_file_sha256(approved_reference_path)
            attach_files.append(approved_reference_path)
            primary_resolved = approved_reference_path.resolve()
            for extra in additional_references or []:
                extra_path = Path(extra)
                if not extra_path.is_file():
                    return {
                        "status": "NOT_VERIFIED",
                        "reason": f"Supplementary reference image missing on disk: {extra_path}",
                        "review_evidence": None,
                        "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
                    }
                if extra_path.resolve() == primary_resolved:
                    continue
                supplementary_refs.append(extra_path)
                attach_files.append(extra_path)

        prior_files: list[Path] = []
        for pr in (prior_renders or []):
            pp = Path(str(pr.get("path") or ""))
            if pp.is_file() and pp not in attach_files:
                attach_files.append(pp)
                prior_files.append(pp)

        prompt = self._build_review_prompt(
            profile=profile,
            renders=renders,
            approved_reference_path=approved_reference_path,
            prior_files=prior_files,
            text_specification=text_specification,
            additional_references=supplementary_refs,
        )

        try:
            raw = self._invoke_fn(
                self.reviewer_model,
                prompt,
                attach_files,
                cwd=self.cwd,
                timeout_sec=self.timeout_sec,
            )
        except Exception as exc:
            return {
                "status": "NOT_VERIFIED",
                "reason": f"Independent multimodal reviewer invocation failed: {exc}",
                "review_evidence": None,
                "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
            }

        reviewer_ctx = str(raw.get("session_id") or "").strip()
        if int(raw.get("returncode", 1)) != 0 or not reviewer_ctx:
            return {
                "status": "NOT_VERIFIED",
                "reason": f"Independent reviewer session failed (rc={raw.get('returncode')}, err={raw.get('stderr', '')[:200]}).",
                "review_evidence": None,
                "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
            }

        if reviewer_ctx == producer_context_id:
            return {
                "status": "NOT_VERIFIED",
                "reason": "Independent reviewer session matched producer_context_id (self-approval rejected).",
                "review_evidence": None,
                "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
            }

        parsed_json = _extract_json_object(str(raw.get("text") or ""))
        if not parsed_json:
            return {
                "status": "NOT_VERIFIED",
                "reason": "Reviewer did not return structured JSON visual inspection findings.",
                "review_evidence": None,
                "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
            }

        if parsed_json.get("multimodal_access_failed") or parsed_json.get("images_unreadable"):
            return {
                "status": "NOT_VERIFIED",
                "reason": "Reviewer reported inability to access or inspect attached images.",
                "review_evidence": None,
                "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
            }

        # Build ImageObservation list bound to real SHA-256 hashes on disk
        obs_by_view = dict(parsed_json.get("observations_by_view") or {})
        observations: list[ImageObservation] = []
        for p in image_files:
            meta = render_meta_by_path[str(p)]
            view = str(meta.get("view") or p.stem)
            notes = obs_by_view.get(view) or obs_by_view.get(p.name) or parsed_json.get("general_observations") or []
            if isinstance(notes, str):
                notes = [notes]
            notes_clean = [str(n).strip() for n in notes if str(n).strip()]
            if not notes_clean:
                return {
                    "status": "NOT_VERIFIED",
                    "reason": f"Reviewer returned empty visual observations for view {view!r}.",
                    "review_evidence": None,
                    "domain_verdicts": {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS},
                }
            observations.append(
                ImageObservation(
                    image_path=str(p),
                    image_sha256=hash_file_sha256(p),
                    view_angle=view,
                    observations=notes_clean,
                    compared_reference_path=str(approved_reference_path) if approved_reference_path else None,
                    compared_reference_sha256=ref_hash,
                )
            )

        # Parse reviewer defects + cross-check with deterministic Blender scene audit (anti-rubber-stamp!)
        defects: list[VisualDefect] = []
        for idx, raw_d in enumerate(parsed_json.get("defects") or [], start=1):
            if not isinstance(raw_d, dict):
                continue
            defects.append(
                VisualDefect(
                    defect_id=str(raw_d.get("defect_id") or f"VQA-{idx:03d}"),
                    defect_type=str(raw_d.get("defect_type") or "facial_proportion_error"),
                    severity=str(raw_d.get("severity") or "MAJOR").upper(),
                    affected_objects=list(raw_d.get("affected_objects") or ["SM_Char_Head"]),
                    source_render=str(raw_d.get("source_render") or image_files[0]),
                    visual_problem=str(raw_d.get("visual_problem") or "Visual defect identified in render."),
                    required_fix_action=str(raw_d.get("required_fix_action") or "Remodel affected 3D geometry."),
                    resolved=bool(raw_d.get("resolved", False)),
                    qa_criterion_id=str(raw_d.get("qa_criterion_id") or ""),
                )
            )

        tech_analysis = None
        if scene_audit is not None:
            tech_analysis = analyze_character_scene_metrics(
                scene_audit,
                triangle_budget=int(scene_audit.get("triangle_budget") or profile.triangle_budget_default),
                material_budget=profile.material_budget_default,
                max_fragment_objects=profile.max_fragment_objects,
            )
            for t_idx, td in enumerate(tech_analysis["detected_defects"], start=1):
                if not any(d.defect_type == td["defect_type"] and not d.resolved for d in defects):
                    defects.append(
                        VisualDefect(
                            defect_id=f"TECH-{t_idx:03d}",
                            defect_type=str(td["defect_type"]),
                            severity=str(td["severity"]),
                            affected_objects=list(td.get("affected_objects") or ["COL_Export"]),
                            source_render=str(image_files[0]),
                            visual_problem=str(td["visual_problem"]),
                            required_fix_action=str(td["required_fix_action"]),
                            resolved=False,
                            qa_criterion_id=str(td.get("criterion_id") or ""),
                        )
                    )

        domain_verdicts = {d: "NOT_VERIFIED" for d in SEVEN_QA_DOMAINS}
        raw_domains = dict(parsed_json.get("domain_verdicts") or {})
        for d in SEVEN_QA_DOMAINS:
            val = str(raw_domains.get(d) or "NOT_VERIFIED").upper()
            if val in ("PASS", "FAIL", "NOT_VERIFIED"):
                domain_verdicts[d] = val

        # Anti-rubber-stamp: technical audit failure forces technical_mesh_validity=FAIL
        if tech_analysis is not None and not tech_analysis["passed_technical_geometry_checks"]:
            domain_verdicts["technical_mesh_validity"] = "FAIL"

        # Fill profile criteria verdicts conservatively
        raw_crit = dict(parsed_json.get("criteria_verdicts") or {})
        criteria_verdicts: dict[str, str] = {}
        for c in profile.criteria:
            v = str(raw_crit.get(c.criterion_id) or "").upper()
            if v not in ("PASS", "FAIL"):
                v = "FAIL" if domain_verdicts["face_and_body_sculpt_quality"] == "FAIL" else "PASS"
            criteria_verdicts[c.criterion_id] = v
        for d in defects:
            if not d.resolved and d.qa_criterion_id:
                criteria_verdicts[d.qa_criterion_id] = "FAIL"

        open_blocking = [d for d in defects if not d.resolved and d.severity in ("BLOCKER", "MAJOR")]
        if open_blocking and domain_verdicts["face_and_body_sculpt_quality"] == "PASS":
            # Prevent rubber-stamp PASS when major defects exist
            if any(d.defect_type in ("round_protruding_eyes", "facial_proportion_error", "scalp_exposure", "rope_like_hair", "stiff_fingers_bad_grip", "bulbous_boots") for d in open_blocking):
                domain_verdicts["face_and_body_sculpt_quality"] = "FAIL"

        ref_score = parsed_json.get("reference_score")
        if isinstance(ref_score, list) and len(ref_score) == 2:
            ref_score_val: tuple[int, int] | None = (int(ref_score[0]), int(ref_score[1]))
        else:
            ref_score_val = None

        evidence = VisualReviewEvidence(
            reviewer_id=f"opencode_vision:{self.reviewer_model}",
            reviewer_context_id=reviewer_ctx,
            producer_context_id=producer_context_id,
            reviewed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            target_asset_path=str(target_asset_path),
            target_asset_sha256=hash_file_sha256(target_asset_path),
            profile_id=profile.profile_id,
            reference_mode=profile.reference_mode,
            image_observations=observations,
            defects=defects,
            criteria_verdicts=criteria_verdicts,
            approval_policy_digest=policy_digest,
            reference_score=ref_score_val,
            reference_comparison_performed=(profile.reference_mode == "REFERENCE_GUIDED"),
            approved_references_used=(profile.reference_mode == "REFERENCE_GUIDED"),
        )

        # Final visual status: technical failure OR sculpt failure OR open blocking defect -> FAIL
        if (
            domain_verdicts["technical_mesh_validity"] == "FAIL"
            or domain_verdicts["face_and_body_sculpt_quality"] == "FAIL"
            or bool(open_blocking)
            or (profile.reference_mode == "REFERENCE_GUIDED" and domain_verdicts["identity_or_reference_fidelity"] == "FAIL")
        ):
            status = "FAIL"
        elif domain_verdicts["face_and_body_sculpt_quality"] == "PASS":
            status = "PASS"
        else:
            status = "NOT_VERIFIED"

        return {
            "status": status,
            "reviewer_session_id": reviewer_ctx,
            "review_evidence": evidence,
            "domain_verdicts": domain_verdicts,
            "reference_checks": _normalise_reference_checks(parsed_json.get("reference_checks")),
            "before_after_improvement": parsed_json.get("before_after_improvement"),
            "technical_analysis": tech_analysis,
        }

    def _build_review_prompt(
        self,
        *,
        profile: QAProfile,
        renders: list[dict[str, Any]],
        approved_reference_path: Path | None,
        prior_files: list[Path],
        text_specification: str,
        additional_references: list[Path] | None = None,
    ) -> str:
        view_list = [f"{r.get('view', 'unknown')}: {Path(str(r.get('path'))).name}" for r in renders]
        crit_list = [f"- {c.criterion_id}: {c.description}" for c in profile.criteria]
        extra_note = ""
        if additional_references:
            names = ", ".join(p.name for p in additional_references)
            extra_note = (
                f" {len(additional_references) + 1} approved reference images are attached in total: "
                f"`{approved_reference_path.name}` is the PRIMARY reference and these are supplementary "
                f"references of the SAME design covering other aspects ({names}). Judge the renders "
                f"against ALL attached references: a detail visible only in a supplementary view "
                f"(back view, close-up, material callout) is still a requirement. Report one entry per "
                f"attached reference in reference_checks. If references contradict each other, say so "
                f"in notes instead of guessing."
            )
        ref_note = (
            f"Compare against attached approved reference `{approved_reference_path.name}`. Rate reference_score [matched, 10]."
            + extra_note
            if profile.reference_mode == "REFERENCE_GUIDED" and approved_reference_path
            else "TEXT_ONLY mode: evaluate against textual specification; set identity_or_reference_fidelity='NOT_VERIFIED' (do not claim original character likeness)."
        )
        prior_note = (
            f"Prior pass renders attached ({', '.join(p.name for p in prior_files)}): explicitly evaluate whether 3D shape improved in before_after_improvement."
            if prior_files
            else ""
        )
        return "\n".join(
            [
                "You are an INDEPENDENT READ-ONLY Visual QA Reviewer inspecting actual attached PNG renders of a 3D character.",
                "Do NOT call any MCP tools or Blender tools, and do NOT modify any files or Blender scenes. Inspect ONLY the attached PNG images and specification.",
                "Do NOT assume modeling succeeded just because files exist. Inspect face/eyes/eyelids, scalp coverage, hair clumps, palm/fingers/grip, boots, costume seams, and multi-view silhouette.",
                f"Mode: {profile.reference_mode}. {ref_note}",
                f"Text spec: {text_specification[:600]}" if text_specification else "",
                f"Attached render views: {', '.join(view_list)}",
                prior_note,
                "Evaluate these criteria:",
                *crit_list,
                "Reply ONLY with a single JSON block containing:",
                "{",
                '  "observations_by_view": {"<view>": ["specific visual observation 1", "..."]},',
                '  "domain_verdicts": {"technical_mesh_validity": "PASS|FAIL|NOT_VERIFIED", "face_and_body_sculpt_quality": "PASS|FAIL", "identity_or_reference_fidelity": "PASS|FAIL|NOT_VERIFIED", "texture_and_material_quality": "PASS|FAIL|NOT_VERIFIED", "rigging_and_deformation_quality": "PASS|FAIL|NOT_VERIFIED", "animation_quality": "PASS|FAIL|NOT_VERIFIED", "engine_export_compatibility": "PASS|FAIL|NOT_VERIFIED"},',
                '  "criteria_verdicts": {"<criterion_id>": "PASS|FAIL"},',
                '  "reference_checks": [{"reference_file": "<attached reference filename>", "role": "primary|supplementary", "checks_performed": ["..."], "match": "PASS|FAIL", "notes": "..."}],',
                '  "defects": [{"defect_id": "D1", "defect_type": "round_protruding_eyes|facial_proportion_error|scalp_exposure|rope_like_hair|hair_face_penetration|stiff_fingers_bad_grip|bulbous_boots|floating_thigh_seam|stiff_shoulders_knees|reference_match_gap", "severity": "BLOCKER|MAJOR|MINOR", "affected_objects": ["SM_Char_..."], "visual_problem": "...", "required_fix_action": "...", "qa_criterion_id": "..."}],',
                '  "reference_score": [7, 10],',
                '  "before_after_improvement": {"improved": true, "summary": "..."}',
                "}",
            ]
        )
