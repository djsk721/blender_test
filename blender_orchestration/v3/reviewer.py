"""Independent visual review of actual Blender renders against the reference set.

The independent reviewer (never the producer) receives bounded contact sheets of the real
renders plus bounded copies of every reference image, and must return structured defects that
name a region, the reference and viewpoint used, what is observed, the expected correction and a
verification criterion. Output that does not prove inspection is NOT_VERIFIED, never PASS.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from .correction import REGION_PRIORITY
from .agents import response_failure

# Existing thresholds (unchanged from the frozen harness): blockout 75, detailed modeling 80,
# materials 86, rigging/animation 65; final asset 85 via the frozen 100-point rubric.
STAGE_THRESHOLDS = {"silhouette": 75.0, "modeling_gate": 80.0, "materials": 86.0, "rigging": 65.0, "animation": 65.0}
MIN_CATEGORY_RATIO = 0.70  # same constant as rubric_evaluator.FROZEN_MIN_CATEGORY_RATIO

MODELING_CATEGORIES = {
    "facial_likeness": 25, "silhouette_anatomy": 20, "hair": 15, "costume": 15,
    "hands_boots_accessories": 10, "geometry_quality": 15,
}
PHASE_REGIONS = {
    "silhouette": ["proportions", "silhouette", "torso", "arms", "legs", "head"],
    "face": ["face", "eyes", "ears", "head"],
    "hair": ["hair"],
    "costume": ["costume_top", "costume_bottom", "sleeves", "belt", "hands", "boots", "accessories"],
    "modeling_gate": list(REGION_PRIORITY),
    "materials": ["materials", "face", "eyes", "hair", "costume_top", "costume_bottom", "boots"],
    "rigging": ["rig", "animation", "arms", "legs", "hands"],
}
PHASE_CATEGORY = {"face": "facial_likeness", "hair": "hair", "costume": "costume", "silhouette": "silhouette_anatomy"}
PHASE_FOCUS = {
    "silhouette": "Judge ONLY overall proportions: total height vs head height (head-to-body ratio), shoulder and hip width, torso/leg/arm lengths, stance, and the outer costume/hair silhouette. Ignore face detail, colour and texture.",
    "face": "Judge the head and face: skull shape, facial silhouette in front and profile, eye socket depth, eye size/shape/spacing/placement, nose, mouth, jaw and chin, Hylian ear size/angle/position, and whether facial parts float or are buried.",
    "hair": "Judge hair: overall mass and volume, front fringe/bangs, side locks, crown braid(s), back length and silhouette, layering versus rope/tube look, scalp coverage, and intersection with face/ears.",
    "costume": "Judge costume construction: tunic/bodice, sleeves, collar/neckline, corset/undershirt, belt and pouch, trousers, boots, gloves and accessories; layer depth, intersections, disconnected parts, and scale of accessories and hands.",
    "modeling_gate": "Judge the whole clay model against the references in the goal's priority order: facial likeness, silhouette/anatomy, hair, costume, hands/boots/accessories, geometry quality. This is the mandatory modeling gate.",
    "materials": "Judge colour palette fidelity, material separation (skin, hair, fabric, leather, metal), painted facial features, and whether lighting reveals the form. Also report any remaining geometry defect.",
    "rigging": "Judge deformation in the posed/animated frames: joint collapse, pinching, clipping, foot contact and pose readability.",
    "animation": "Judge the sampled idle, walk and run frames for joint collapse, clipping, cadence and foot contact. Do not claim unsampled motion was verified.",
}

REQUIRED_DEFECT_FIELDS = ("defect_id", "region", "observed", "expected_correction", "severity", "verification_criterion")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compose_sheet(images: list[tuple[str, Path]], out_path: Path, *, tile: int = 512, cols: int = 4) -> Path | None:
    """Labelled contact sheet with bounded size (tile px per view)."""
    from PIL import Image, ImageDraw

    items = [(label, Path(p)) for label, p in images if Path(p).is_file()]
    if not items:
        return None
    cols = max(1, min(cols, len(items)))
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile, rows * (tile + 22)), (30, 30, 34))
    draw = ImageDraw.Draw(sheet)
    for i, (label, path) in enumerate(items):
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((tile, tile))
            x, y = (i % cols) * tile, (i // cols) * (tile + 22)
            sheet.paste(im, (x + (tile - im.width) // 2, y + 22 + (tile - im.height) // 2))
            draw.text((x + 6, y + 4), label, fill=(240, 240, 240))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "PNG")
    return out_path


def build_review_prompt(*, phase: str, goal_excerpt: str, plan_excerpt: str, render_names: list[str],
                        reference_lines: list[str], prior_summary: str | None, base_mesh: bool = False) -> str:
    regions = ", ".join(PHASE_REGIONS.get(phase, list(REGION_PRIORITY)))
    cats = ", ".join(f'"{k}": <0-{v}>' for k, v in MODELING_CATEGORIES.items())
    return "\n".join([
        f"You are the INDEPENDENT visual QA reviewer for phase `{phase}` of a reference-guided Blender character.",
        "You review; you never write Blender code. Do not call tools. Inspect ONLY the attached images.",
        PHASE_FOCUS.get(phase, PHASE_FOCUS["modeling_gate"]),
        "Attached renders (actual Blender output): " + ", ".join(render_names),
        "Attached references (approved):",
        *reference_lines,
        f"Production goal (excerpt): {goal_excerpt}",
        f"Producer's own plan (excerpt): {plan_excerpt}" if plan_excerpt else "",
        f"Previous candidate for comparison: {prior_summary}" if prior_summary else "",
        "Score honestly against the references; a technically clean but generic model is not a likeness.",
        ("Check whether the anatomical starting mesh has been sufficiently transformed to hit the target "
         "proportions, silhouette and character identity. Apply the same reference rubric and thresholds." if base_mesh else ""),
        f"Use region values from: {regions}. Use severity CRITICAL (identity- or structure-breaking), MAJOR, MINOR.",
        "Set structural=true when the defect needs the part rebuilt (wrong base shape/topology/placement), not a tweak.",
        "Return ONLY one JSON object:",
        "{",
        '  "stage_score_100": <0-100>,',
        f"  \"category_scores\": {{{cats}}},",
        '  "passed": <true only if this phase is acceptable for production>,',
        '  "improved_over_prior": <true|false|null>,',
        '  "observations_by_view": {"<render view>": ["what you see"]},',
        '  "reference_comparison": [{"reference": "<file>", "viewpoint": "<front|side|back|3/4|face>", "notes": "..."}],',
        '  "defects": [{"defect_id": "D1", "region": "<region>", "category": "<short slug>", "object": "<mesh name if visible>",',
        '     "reference_image": "<file>", "reference_viewpoint": "...", "observed": "...", "expected_correction": "<concrete geometric change with directions/approx metres>",',
        '     "severity": "CRITICAL|MAJOR|MINOR", "structural": <bool>, "verification_criterion": "<what the next render must show>"}],',
        '  "summary": "<two sentences>"',
        "}",
    ])


def _extract_json(text: str) -> dict[str, Any] | None:
    for start, c in enumerate(text or ""):
        if c != '{':
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(text, start)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def parse_review(text: str, *, phase: str, render_views: list[str]) -> dict[str, Any]:
    """Strictly parse reviewer JSON. Never returns PASS for output that does not prove inspection."""
    if '```python' in text.lower() or '```py\n' in text.lower():
        return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "reviewer supplied executable code instead of findings"}
    data = _extract_json(text)
    if not data:
        return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "unparseable reviewer output"}
    obs = data.get("observations_by_view")
    if not isinstance(obs, dict) or not any(isinstance(v, list) and any(str(x).strip() for x in v) for v in obs.values()):
        return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "no per-view observations"}
    try:
        score = float(data.get("stage_score_100"))
    except (TypeError, ValueError):
        return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "missing numeric score"}
    if not 0.0 <= score <= 100.0:
        return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "score out of range"}

    cats_raw = data.get("category_scores") if isinstance(data.get("category_scores"), dict) else {}
    categories: dict[str, float] = {}
    for name, maxv in MODELING_CATEGORIES.items():
        try:
            categories[name] = max(0.0, min(float(maxv), float(cats_raw.get(name))))
        except (TypeError, ValueError):
            pass
    notes: list[str] = []
    gating_score = score

    defects: list[dict[str, Any]] = []
    for i, raw in enumerate(data.get("defects") or []):
        if not isinstance(raw, dict):
            continue
        d = {k: raw.get(k) for k in (*REQUIRED_DEFECT_FIELDS, "category", "object", "reference_image", "reference_viewpoint", "structural")}
        d["defect_id"] = str(d.get("defect_id") or f"D{i + 1}")
        region = str(d.get("region") or "").strip().lower().replace(" ", "_")
        d["region"] = region if region in REGION_PRIORITY else "technical"
        sev = str(d.get("severity") or "MAJOR").upper()
        d["severity"] = sev if sev in ("CRITICAL", "MAJOR", "MINOR") else "MAJOR"
        d["structural"] = bool(d.get("structural"))
        d["category"] = str(d.get("category") or d["defect_id"]).strip().lower()[:40]
        missing = [k for k in ("observed", "expected_correction", "verification_criterion") if not str(d.get(k) or "").strip()]
        if missing:
            d["incomplete_fields"] = missing
        defects.append(d)

    threshold = STAGE_THRESHOLDS.get(phase)
    critical = [d for d in defects if d["severity"] == "CRITICAL"]
    if threshold is not None:
        passed = data.get("passed") is True and not critical and gating_score >= threshold
        basis = f"threshold {threshold}"
    else:
        cat = PHASE_CATEGORY.get(phase)
        region_set = set(PHASE_REGIONS.get(phase, []))
        blocking = [d for d in defects if d["region"] in region_set and d["severity"] in ("CRITICAL", "MAJOR")]
        cat_ok = cat is None or (cat in categories and categories[cat] >= MIN_CATEGORY_RATIO * MODELING_CATEGORIES[cat])
        passed = data.get("passed") is True and not blocking and cat_ok
        basis = f"phase progress check: no CRITICAL/MAJOR in {sorted(region_set)} and {cat} >= {int(MIN_CATEGORY_RATIO * 100)}%"
    return {
        "verdict": "PASS" if passed else "FAIL", "passed": passed, "score": round(gating_score, 2),
        "holistic_score": score, "category_scores": categories, "gate_basis": basis,
        "critical_defects": len(critical), "improved_over_prior": data.get("improved_over_prior"),
        "observations_by_view": obs, "reference_comparison": data.get("reference_comparison") or [],
        "defects": defects, "summary": str(data.get("summary") or "")[:800], "notes": notes,
        "render_views": render_views,
    }


class IndependentReviewer:
    """Runs one fresh reviewer session per review and binds the evidence it saw."""

    def __init__(self, model_id: str, invoke: Callable[..., dict[str, Any]], *, timeout_sec: int = 420) -> None:
        self.model_id = model_id
        self.invoke = invoke
        self.timeout_sec = timeout_sec

    def review(self, *, phase: str, renders: list[dict[str, Any]], reference_copies: list[dict[str, Any]],
               goal_excerpt: str, plan_excerpt: str, out_dir: Path, producer_session_ids: set[str],
               prior: dict[str, Any] | None = None, base_mesh: bool = False) -> dict[str, Any]:
        render_items = [(str(r["view"]), Path(r["path"])) for r in renders if Path(str(r.get("path") or "")).is_file()]
        if not render_items or len(render_items) != len(renders):
            return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "missing renders on disk"}
        out_dir.mkdir(parents=True, exist_ok=True)
        body = [x for x in render_items if not x[0].startswith("face")]
        face = [x for x in render_items if x[0].startswith("face")]
        sheets = [p for p in (
            compose_sheet(body, out_dir / f"{phase}_body_sheet.png", tile=480, cols=4),
            compose_sheet(face, out_dir / f"{phase}_face_sheet.png", tile=480, cols=3) if face else None,
        ) if p]
        if len(sheets) != bool(body) + bool(face):
            return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [],
                    "reason": "could not compose all Blender renders for image delivery"}
        if prior and prior.get("sheet") and Path(prior["sheet"]).is_file():
            sheets.append(Path(prior["sheet"]))
        refs = [Path(r["review_copy"]) for r in reference_copies if Path(r["review_copy"]).is_file()]
        if len(refs) != len(reference_copies) or any(_sha(p) != r['copy_sha256'] for p, r in zip(refs, reference_copies)):
            return {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [], "reason": "missing or changed reference attachment"}
        ref_lines = [f"- {Path(r['review_copy']).name}: role={r['role']}, purpose={r.get('purpose','')}" for r in reference_copies]
        prompt = build_review_prompt(
            phase=phase, goal_excerpt=goal_excerpt[:1400], plan_excerpt=plan_excerpt[:1400],
            render_names=[p.name for p in sheets], reference_lines=ref_lines,
            base_mesh=base_mesh,
            prior_summary=(f"score {prior.get('score')} - {prior.get('summary','')[:300]} (its sheet is the last attachment)" if prior else None),
        )
        raw = self.invoke(self.model_id, prompt, files=[*sheets, *refs], timeout_sec=self.timeout_sec,
                          title=f"V3 independent review ({phase})")
        sid = str(raw.get("session_id") or "")
        if failure := response_failure(raw, self.model_id):
            result = {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [],
                      "reason": f"reviewer failed: {failure}"}
        elif sid in producer_session_ids:
            result = {"verdict": "NOT_VERIFIED", "passed": False, "score": 0.0, "defects": [],
                      "reason": "reviewer session is not independent of the producer"}
        else:
            result = parse_review(str(raw.get("text") or ""), phase=phase, render_views=[v for v, _ in render_items])
        result.update({
            "phase": phase, "reviewer_model_id": self.model_id, "reviewer_session_id": sid,
            "duration_sec": raw.get("duration_sec"), "tokens": raw.get("tokens"),
            "sheet": str(sheets[0]) if sheets else None,
            "renders": [{"view": view, "path": str(path), "sha256": _sha(path)} for view, path in render_items],
            "evidence": [{"path": str(p), "sha256": _sha(p)} for p in [*sheets, *refs]],
        })
        return result
