"""Multi-reference discovery, loading, validation and delivery tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from blender_orchestration.reference_manifest import (
    PRIMARY_ROLE,
    ROLE_PRIMARY,
    ROLE_SUPPLEMENTARY,
    SUPPLEMENTARY_ROLE,
    ReferenceManifestError,
    build_manifest_from_directory,
    discover_reference_images,
    load_reference_manifest,
    snapshot_references,
    write_reference_manifest,
)


def _make_image(path: Path, color: tuple[int, int, int] = (10, 120, 200)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (7, 5), color=color).save(path)
    return path


@pytest.fixture()
def ref_dir(tmp_path: Path) -> Path:
    root = tmp_path / "references" / "hero"
    _make_image(root / "turntable.png")
    _make_image(root / "face.jpg")
    (root / "notes.txt").write_text("not an image", encoding="utf-8")  # must be ignored
    return root


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_finds_images_in_sorted_order_and_ignores_other_files(ref_dir):
    found = discover_reference_images(ref_dir)
    assert [p.name for p in found] == ["face.jpg", "turntable.png"]


def test_discovery_rejects_missing_directory(tmp_path):
    with pytest.raises(ReferenceManifestError, match="not a directory"):
        discover_reference_images(tmp_path / "nope")


def test_discovery_rejects_directory_without_images(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "readme.md").write_text("hi")
    with pytest.raises(ReferenceManifestError, match="no reference images"):
        discover_reference_images(empty)


# ---------------------------------------------------------------------------
# Manifest building and validation
# ---------------------------------------------------------------------------


def test_build_manifest_marks_first_image_primary_and_hashes_every_entry(ref_dir):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")

    assert manifest.primary.path == ref_dir / "face.jpg"
    assert [e.path for e in manifest.supplementary] == [ref_dir / "turntable.png"]
    for entry in manifest.entries:
        digest = hashlib.sha256(entry.path.read_bytes()).hexdigest()
        assert entry.sha256 == digest
        assert (entry.width, entry.height) == (7, 5)
    # one bracketed line per reference, primary first
    lines = [line for line in manifest.prompt_block().splitlines() if line.strip().startswith("[")]
    assert len(lines) == 2 and lines[0].startswith("[PRIMARY") and lines[1].startswith("[SUPPLEMENTARY")


def test_snapshot_copies_every_reference_and_verifies_hashes(ref_dir, tmp_path):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    run_refs = tmp_path / "run" / "references" / "hero"

    snapshot, snapshot_manifest = snapshot_references(manifest, run_refs)

    assert sorted(p.name for p in snapshot) == ["face.jpg", "turntable.png"]
    assert snapshot_manifest.primary.path == run_refs / "face.jpg"
    for entry in snapshot_manifest.entries:
        assert entry.path.is_file()
        assert entry.sha256 == hashlib.sha256(entry.path.read_bytes()).hexdigest()


def test_snapshot_refuses_tampered_source(ref_dir, tmp_path):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    manifest.primary.path.write_bytes(b"corrupted after discovery")
    with pytest.raises(ReferenceManifestError, match="hash mismatch"):
        snapshot_references(manifest, tmp_path / "run_refs")


def test_manifest_requires_exactly_one_primary(ref_dir):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    manifest.entries[0].role = SUPPLEMENTARY_ROLE
    with pytest.raises(ReferenceManifestError, match="exactly one"):
        manifest.validate()
    manifest.entries[1].role = PRIMARY_ROLE
    with pytest.raises(ReferenceManifestError, match="exactly one"):
        manifest.validate()


def test_manifest_rejects_duplicate_paths(ref_dir):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    manifest.entries[1].path = manifest.entries[0].path
    with pytest.raises(ReferenceManifestError, match="duplicate"):
        manifest.validate()


def test_manifest_rejects_unknown_role(ref_dir):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    manifest.entries[0].role = "inspiration"
    with pytest.raises(ReferenceManifestError, match="unknown reference role"):
        manifest.validate()


# ---------------------------------------------------------------------------
# Loading (round-trip and failure modes)
# ---------------------------------------------------------------------------


def test_manifest_round_trips_through_json(ref_dir, tmp_path):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    target = tmp_path / "reference_manifest.json"
    write_reference_manifest(manifest, target)

    loaded = load_reference_manifest(target)
    assert loaded.asset == "hero"
    assert loaded.primary.path == manifest.primary.path
    assert [e.sha256 for e in loaded.entries] == [e.sha256 for e in manifest.entries]
    assert [e.role for e in loaded.entries] == [PRIMARY_ROLE, SUPPLEMENTARY_ROLE]


def test_loader_resolves_relative_paths_against_manifest_dir(ref_dir, tmp_path):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    target = tmp_path / "reference_manifest.json"
    write_reference_manifest(manifest, target)
    raw = json.loads(target.read_text())
    for entry in raw["references"]:
        entry["path"] = str(Path(entry["path"]).relative_to(tmp_path))
    target.write_text(json.dumps(raw))

    loaded = load_reference_manifest(target)
    assert all(e.path.is_file() for e in loaded.entries)


def test_loader_rejects_missing_file_and_bad_hash(ref_dir, tmp_path):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    target = tmp_path / "reference_manifest.json"
    write_reference_manifest(manifest, target)

    raw = json.loads(target.read_text())
    raw["references"][0]["path"] = str(tmp_path / "gone.png")
    target.write_text(json.dumps(raw))
    with pytest.raises(ReferenceManifestError, match="does not exist"):
        load_reference_manifest(target)

    raw = json.loads(target.read_text())
    raw["references"][0]["sha256"] = "0" * 64
    target.write_text(json.dumps(raw))
    with pytest.raises(ReferenceManifestError, match="hash mismatch"):
        load_reference_manifest(target)


def test_loader_rejects_manifest_without_primary(ref_dir, tmp_path):
    manifest = build_manifest_from_directory(ref_dir, asset="hero")
    target = tmp_path / "reference_manifest.json"
    write_reference_manifest(manifest, target)
    raw = json.loads(target.read_text())
    for entry in raw["references"]:
        entry["role"] = SUPPLEMENTARY_ROLE
    target.write_text(json.dumps(raw))
    with pytest.raises(ReferenceManifestError, match="exactly one"):
        load_reference_manifest(target)


# ---------------------------------------------------------------------------
# Delivery: every reference reaches the reviewers and the harness
# ---------------------------------------------------------------------------


def test_stage_reviewer_attaches_all_references(tmp_path, monkeypatch):
    from blender_orchestration.rubric_evaluator import StageVisualReviewer

    render = tmp_path / "front.png"
    render.touch()
    monkeypatch.setattr(
        "blender_orchestration.rubric_evaluator.compose_review_sheets", lambda renders: [render]
    )
    calls = []

    def invoke(model, prompt, **kwargs):
        calls.append((prompt, kwargs))
        return {
            "returncode": 0,
            "session_id": "rev",
            "text": json.dumps(
                {
                    "stage_score_100": 91,
                    "passed": True,
                    "observations": ["matches the sheet"],
                    "reference_checks": [
                        {"reference": "face.jpg", "consistent": True, "notes": "ok"}
                    ],
                }
            ),
        }

    primary = tmp_path / "primary.png"
    extra = tmp_path / "extra.jpg"
    for path in (primary, extra):
        path.touch()
    reviewer = StageVisualReviewer(invoke_fn=invoke)
    verdict = reviewer.review_stage(
        stage_name="detailed_modeling",
        renders=[{"path": str(render), "view": "front"}],
        design_spec_summary="Princess Zelda",
        producer_session_ids={"prod"},
        approved_reference_path=primary,
        additional_references=[extra],
    )
    assert verdict["verdict"] == "PASS"
    attached = calls[0][1]["files"]
    assert primary in attached and extra in attached
    assert "FULL reference set" in calls[0][0]


def test_final_evaluator_attaches_all_references(tmp_path):
    from blender_orchestration.rubric_evaluator import BlindRubricEvaluator

    render = tmp_path / "front.png"
    render.touch()
    calls = []

    def invoke(model, prompt, **kwargs):
        calls.append((prompt, kwargs))
        return {
            "returncode": 0,
            "session_id": "eval",
            "text": json.dumps(
                {
                    "total_score_100": 90,
                    "domain_scores": {
                        "proportions": 20,
                        "likeness": 25,
                        "costume": 18,
                        "craft": 15,
                        "presentation": 12,
                    },
                    "passed": True,
                    "blocking_issues": [],
                    "reference_checks": [
                        {"reference": "extra.jpg", "consistent": True, "notes": "ok"}
                    ],
                }
            ),
        }

    primary = tmp_path / "primary.png"
    extra = tmp_path / "extra.jpg"
    for path in (primary, extra):
        path.touch()
    evaluator = BlindRubricEvaluator(invoke_fn=invoke)
    result = evaluator.evaluate(
        renders=[{"path": str(render), "view": "front"}],
        design_spec_summary="Princess Zelda",
        producer_session_ids={"prod"},
        approved_reference_path=primary,
        additional_references=[extra],
    )
    assert result["passed"] is True
    attached = calls[0][1]["files"]
    assert primary in attached and extra in attached
    assert "FULL reference set" in calls[0][0]


def test_harness_requires_manifest_primary_to_match_reference_image(tmp_path):
    from blender_orchestration.autonomous_harness import AutonomousBlenderHarness
    from blender_orchestration.contracts import ReferenceMode
    from blender_orchestration.reference_manifest import build_manifest_from_directory

    refs = tmp_path / "refs"
    _make_image(refs / "a.png")
    _make_image(refs / "b.png")
    manifest = build_manifest_from_directory(refs, asset="hero")
    other = _make_image(tmp_path / "other.png")

    harness = AutonomousBlenderHarness(
        producer_model_id="strata/producer",
        supervisor_model_id="openai/supervisor",
        reviewer_model_id="openai/reviewer",
        agent_invoke_fn=lambda *args, **kwargs: {"returncode": 1, "errors": ["unused"]},
    )
    with pytest.raises(ValueError, match="primary reference"):
        harness.run_production(
            "hero",
            reference_mode=ReferenceMode.REFERENCE_GUIDED,
            approved_reference_path=other,
            reference_manifest=manifest.path or tmp_path / "m.json",
            run_dir=tmp_path / "run",
        )
