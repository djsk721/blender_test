# Character Visual QA & Bounded Repair Loop (`qa_profiles.py` & `visual_qa.py`)

## 1. Asset-Type QA Profiles (`blender_orchestration/qa_profiles.py`)

To stop ship/hull/mechanical checklists from leaking into character evaluations, `get_qa_profile()` selects one of 5 dedicated profiles:
- `HUMANOID_CHARACTER`
- `ANIMATED_CHARACTER`
- `CREATURE`
- `HARD_SURFACE_PROP`
- `ENVIRONMENT`

For `HUMANOID_CHARACTER` and `ANIMATED_CHARACTER`:
- `validate_profile_criteria_hygiene()` rejects any checklist item containing hard-surface/ship keywords (`hull`, `ship`, `starboard`, `nav light`, `metal panel`, `greeble`, `spacecraft`, `cockpit`, `machinery`, `fuselage`, `bulkhead`, `thruster`).
- Required render views: `front`, `three_quarter`, `side`, `back`, `face_closeup`, `hands_closeup`, `boots_closeup` (plus `pose_run_strip` when animated, and `reference_matched` in `REFERENCE_GUIDED`).

### Character Modeling Quality Criteria

1. **Face**: `face_3d_surface_continuity`, `facial_feature_placement`, `non_protruding_eyes_and_almond_contour`, `forehead_cheek_jaw_proportions`, `front_and_profile_silhouette`.
2. **Hair**: `scalp_coverage_no_bald_exposure` (`BLOCKER`), `sculpted_clump_volume_not_rope_tube`, `hair_flow_direction_and_silhouette`, `hair_face_shoulder_no_penetration`, `tapered_tips_and_braid_structure`.
3. **Body & Costume**: `shoulder_waist_pelvis_leg_proportions`, `torso_costume_natural_fit`, `articulated_hand_palm_finger_connection`, `held_object_finger_wrap_contact`, `thigh_knee_ankle_seam_attachment_no_floating_or_clipping`, `boot_instep_heel_ankle_anatomy`, `no_bloated_or_ballooned_costume_and_boots`.
4. **Form & Topology**: `sculpted_form_vs_primitive_stacking`, `silhouette_and_surface_continuity`, `localized_mesh_density_distribution`, `deformation_ready_joint_topology`, `no_raw_polygon_inflation_without_form_gain`, `triangle_budget_within_limit`.

### Mode Separation
- **`REFERENCE_GUIDED`**: adds `reference_camera_match`, `reference_silhouette_and_pose_match`, `reference_palette_and_material_match`, `reference_identity_match` (requires `>= 8/10` Match/Close).
- **`TEXT_ONLY`**: adds `text_specification_compliance`; exact original-character likeness is reported separately as `NOT_VERIFIED` without blocking Gate B when character quality passes.

---

## 2. Anti-Forging Visual Review Evidence (`blender_orchestration/visual_qa.py`)

Bare Boolean flags (`visually_inspected=True`, `independent_reviewer=True`) can no longer approve QA in `evaluate_asset_qa()`, `evaluate_verified_character_gates()`, or `VisualQARepairLoop`.

`verify_visual_review_evidence()` requires a `VisualReviewEvidence` artifact containing:
- `reviewer_id`, `reviewer_context_id`, `producer_context_id` (**self-approval `reviewer_context_id == producer_context_id` is rejected as `NOT_VERIFIED`**)
- `reviewed_at` ISO timestamp
- `target_asset_path` & `target_asset_sha256` (verified against disk when `verify_files_on_disk=True`)
- `image_observations`: non-empty list of `ImageObservation(image_path, image_sha256, view_angle, observations, compared_reference_path, compared_reference_sha256)` verified against live PNG hashes on disk
- `approval_policy_digest`: SHA-256 of immutable `QAProfile` + `RepairLoopPolicy` thresholds computed via `compute_approval_policy_digest()` (tampering returns `FAIL`)
- In `REFERENCE_GUIDED` mode: `approved_references_used=True`, `reference_comparison_performed=True`, and at least one observation linked to a hashed reference image on disk.

If evidence is absent or a reviewer is unavailable, the verdict is strictly **`NOT_VERIFIED`**, never `PASS`.

---

## 3. Defect-to-Fix Mapping & Bounded Repair Loop (`VisualQARepairLoop`)

Every issue is recorded as a `VisualDefect` (`defect_id`, `defect_type`, `severity`, `affected_objects`, `source_render`, `visual_problem`, `required_fix_action`, `before_after_evidence`, `resolved`, `qa_criterion_id`).

`plan_blender_fixes_for_defects()` maps open defects to `BlenderFixAction`s with `preserve_existing_mesh=True` (e.g., `scalp_exposure` -> `close_scalp_crown_coverage`, `round_protruding_eyes` -> `recess_eyes_and_contour_eyelids`, `floating_thigh_seam` -> `snap_costume_trim_to_surface`, `bulbous_boots` -> `contour_boot_ankle_instep_heel`, `stiff_fingers_bad_grip` -> `curl_fingers_and_seat_palm_grip`).

### Repair Loop Policy (`RepairLoopPolicy`)
- `max_fix_iterations = 3` (configurable per benchmark policy)
- `max_wall_time_sec = 1800.0` (configurable)
- Pure re-renders (`mesh_modified=False`) increment `rerender_count` only and **do not consume** a fix iteration.
- `forbid_stalled_repeat=True`: if the exact same `(defect_type, operation_type, target_objects)` signature persists after a real mesh modification, the loop halts immediately with `FAIL` instead of repeating ineffective edits.
- Exhausting `max_fix_iterations` with unresolved `BLOCKER`/`MAJOR` defects returns `FAIL` and blocks downstream stages.

---

## 4. Polygon Inflation & Scene Metrics Guards

- `analyze_character_scene_metrics()` detects triangle budget overruns, primitive stacking (`mesh_objects > max_fragment_objects`), material sprawl, non-manifold/loose verts, missing UVs, unapplied scale, scalp gaps, floating thigh seams, eye protrusion, hair penetration, and hand grip gaps.
- `detect_polygon_inflation()` flags iterations that grow triangle counts by `> 20%` without reducing open defects or improving quality score.
