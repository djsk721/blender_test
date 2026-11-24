"""OpenCode native Skill discovery and Blender Director dynamic routing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_ROOT = PROJECT_ROOT / ".opencode" / "skills"
SUPPORTED_PRODUCTION_MODES = ("TEXT_ONLY", "REFERENCE_GUIDED")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_REL_MD_LINK_RE = re.compile(r"`((?:\.\./|references/)[^`\s]+\.md)`|\(((?:\.\./|references/)[^)\s]+\.md)\)")


@dataclass(frozen=True)
class SkillMetadata:
    """Discovered OpenCode skill metadata and reference validation."""

    skill_id: str
    description: str
    role: str
    version: str
    triggers: list[str]
    related_skills: list[str]
    skill_md_path: str
    references: list[str]
    broken_references: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse flat and metadata-nested YAML frontmatter using Python stdlib."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    block = match.group(1)
    result: dict[str, Any] = {}
    meta: dict[str, str] = {}
    in_metadata = False

    for raw_line in block.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if indent == 0:
            in_metadata = key == "metadata" and not val
            if not in_metadata:
                result[key] = val
        elif in_metadata:
            meta[key] = val
    if meta:
        result["metadata"] = meta
    return result


def _split_csv(val: str) -> list[str]:
    return [item.strip() for item in val.split(",") if item.strip()]


class SkillRegistry:
    """Discovers OpenCode Skills under .opencode/skills and loads them on demand."""

    def __init__(self, skills_root: Path | None = None) -> None:
        self.skills_root = Path(skills_root) if skills_root is not None else DEFAULT_SKILLS_ROOT
        self._skills: dict[str, SkillMetadata] | None = None
        self._loaded_cache: dict[str, dict[str, Any]] = {}

    def discover(self, *, refresh: bool = False) -> dict[str, SkillMetadata]:
        """Scan `<skills_root>/*/SKILL.md` and resolve supporting references."""
        if self._skills is not None and not refresh:
            return dict(self._skills)

        discovered: dict[str, SkillMetadata] = {}
        if not self.skills_root.is_dir():
            self._skills = discovered
            return discovered

        for skill_md in sorted(self.skills_root.glob("*/SKILL.md")):
            skill_dir = skill_md.parent
            skill_id = skill_dir.name
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(text)
            meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}

            refs: list[str] = []
            broken: list[str] = []
            seen: set[str] = set()
            for m in _REL_MD_LINK_RE.finditer(text):
                rel_ref = m.group(1) or m.group(2)
                if not rel_ref or rel_ref in seen:
                    continue
                seen.add(rel_ref)
                resolved = (skill_dir / rel_ref).resolve()
                if resolved.is_file():
                    refs.append(rel_ref)
                else:
                    broken.append(rel_ref)

            discovered[fm.get("name") or skill_id] = SkillMetadata(
                skill_id=fm.get("name") or skill_id,
                description=str(fm.get("description") or ""),
                role=str(meta.get("role") or "specialist"),
                version=str(meta.get("version") or ""),
                triggers=_split_csv(str(meta.get("triggers") or "")),
                related_skills=_split_csv(str(meta.get("related-skills") or "")),
                skill_md_path=str(skill_md),
                references=refs,
                broken_references=broken,
            )

        self._skills = discovered
        return dict(discovered)

    def has_skill(self, skill_id: str) -> bool:
        return skill_id in self.discover()

    def get(self, skill_id: str) -> SkillMetadata | None:
        return self.discover().get(skill_id)

    def shared_references_dir(self) -> Path:
        return self.skills_root / "references"

    def load_skill(
        self,
        skill_id: str,
        *,
        include_references: list[str] | None = None,
    ) -> dict[str, Any]:
        """Load complete SKILL.md and optional supporting references on demand (cached)."""
        cache_key = f"{skill_id}::{','.join(sorted(include_references or []))}"
        if cache_key in self._loaded_cache:
            return self._loaded_cache[cache_key]

        meta = self.get(skill_id)
        if meta is None:
            raise KeyError(f"Skill not found in {self.skills_root}: {skill_id}")

        skill_md_path = Path(meta.skill_md_path)
        content = skill_md_path.read_text(encoding="utf-8")

        loaded_refs: dict[str, str] = {}
        missing_refs: list[str] = []
        for rel_ref in include_references or []:
            candidate = (skill_md_path.parent / rel_ref).resolve()
            if not candidate.is_file():
                # Also resolve relative to shared skills_root
                alt = (self.skills_root / rel_ref).resolve()
                candidate = alt if alt.is_file() else candidate
            if candidate.is_file():
                loaded_refs[rel_ref] = candidate.read_text(encoding="utf-8")
            else:
                missing_refs.append(rel_ref)

        payload = {
            "skill_id": meta.skill_id,
            "skill_md_path": str(skill_md_path),
            "metadata": meta.to_dict(),
            "content": content,
            "loaded_references": loaded_refs,
            "missing_references": missing_refs,
        }
        self._loaded_cache[cache_key] = payload
        return payload

    def compute_revision_hash(self) -> str:
        """Compute deterministic SHA-256 across all files in skills_root."""
        if not self.skills_root.is_dir():
            return ""
        h = hashlib.sha256()
        for f in sorted(p for p in self.skills_root.rglob("*") if p.is_file()):
            rel = f.relative_to(self.skills_root).as_posix()
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            h.update(f"{rel}:{digest}\n".encode("utf-8"))
        return h.hexdigest()


_REQUIRED_OPS_BY_SKILL: dict[str, list[str]] = {
    "blender-director": ["production_brief", "budget_definition", "skill_routing"],
    "character-archetypes": ["archetype_proportions", "feature_spec"],
    "character-artist": ["proportion_spec", "body_blockout", "facial_structure", "topology_inspection"],
    "prop-artist": ["scale_blockout", "primary_volumes", "bevels_and_parts", "pivot_check"],
    "hard-surface": ["silhouette_blockout", "panel_breaks", "bevel_weights", "clean_normals"],
    "vehicle-artist": ["vehicle_proportions", "chassis_blockout", "subparts_hierarchy"],
    "environment-artist": ["modular_scale", "kit_blocking", "scene_hierarchy"],
    "scene-assembly": ["collection_linking", "layout_placement", "hierarchy_audit"],
    "fantasy-worlds": ["fantasy_shape_language", "world_material_palette"],
    "stylized-style": ["stylized_proportions", "broad_form_readability", "stylized_shading"],
    "realistic-style": ["real_world_scale", "pbr_roughness_calibration"],
    "lowpoly-style": ["faceted_silhouette", "strict_poly_budget"],
    "horror-style": ["horror_silhouette", "mood_contrast"],
    "sculpting": ["sculpt_or_form_refinement", "before_after_geometry", "form_validation"],
    "retopology": ["topology_refinement", "quad_dominant_check", "joint_edge_loops", "poly_budget_validation"],
    "hair-groom": ["hair_blockout_clumps", "scalp_attachment", "hair_silhouette_check"],
    "cloth-sim": ["garment_separation", "drape_or_collision_check"],
    "uv-workflow": ["uv_layer_created", "uv_unwrap", "seam_and_pack_check", "texel_density_check"],
    "materials": ["master_materials_mat_prefix", "bsdf_parameters", "slot_assignment", "budget_check"],
    "texture-workflow": ["texture_files_or_nodes", "uv_binding_check", "export_compatibility"],
    "lighting": ["three_point_or_world_rig", "shadow_exposure_check"],
    "rendering": ["camera_framing", "render_settings", "still_output_check"],
    "asset-optimization": ["polycount_check", "apply_transforms", "manifold_and_normals_check"],
    "rigging": ["armature_creation", "bone_hierarchy", "mesh_weights", "deformation_test"],
    "animation": ["action_creation", "keyframe_motion", "motion_playback_verification"],
    "qa-review": ["visual_capture", "visual_inspection", "defect_findings", "explicit_verdict"],
    "reference-image-match": ["reference_analysis", "camera_match", "blender_modeling_passes", "reference_comparison_loop"],
    "export-pipeline": ["axis_and_scale_config", "artifact_export", "reimport_validation"],
    "godot-export": ["glb_export_plus_y_up", "godot_node_naming_check", "reimport_validation"],
    "unity-export": ["fbx_or_gltf_scale_check", "reimport_validation"],
    "unreal-export": ["fbx_collision_ucx_check", "reimport_validation"],
}


class BlenderDirector:
    """Model-agnostic Blender Director orchestrator selecting stage-appropriate Skills."""

    def __init__(self, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def analyze_request(self, prompt: str, *, reference_mode: str = "TEXT_ONLY") -> dict[str, Any]:
        """Analyze asset request structurally without any model-specific branches."""
        p = (prompt or "").lower()

        # 1. Determine asset type
        if any(k in p for k in ("character", "heroine", "princess", "knight", "human", "paladin", "warrior", "npc")):
            asset_type = "character"
        elif any(k in p for k in ("creature", "monster", "dragon", "beast")):
            asset_type = "creature"
        elif any(k in p for k in ("car", "ship", "mech", "aircraft", "vehicle", "tank", "spaceship")):
            asset_type = "vehicle"
        elif any(k in p for k in ("environment", "corridor", "dungeon", "temple", "terrain", "level", "room", "modular kit")):
            asset_type = "environment"
        elif any(k in p for k in ("tree", "plant", "foliage", "forest", "grass")):
            asset_type = "vegetation"
        elif any(k in p for k in ("rifle", "sword", "dagger", "gun", "pistol", "weapon", "axe", "blaster", "turret")):
            asset_type = "weapon"
        elif any(k in p for k in ("sci-fi", "scifi", "industrial", "machinery", "console", "crate")) and any(
            hs in p for hs in ("sci-fi", "scifi", "industrial", "mechanical", "hard-surface", "hard surface")
        ):
            asset_type = "hard_surface_prop"
        else:
            asset_type = "everyday_prop"

        # 2. Target & engine
        if "godot" in p:
            target_engine = "godot"
            export_format = "GLB"
        elif "unreal" in p or "ue5" in p:
            target_engine = "unreal"
            export_format = "FBX"
        elif "unity" in p:
            target_engine = "unity"
            export_format = "GLB"
        elif any(k in p for k in ("game-ready", "game ready", "game asset", "glb", "gltf")):
            target_engine = "generic_game"
            export_format = "GLB"
        else:
            target_engine = "generic"
            export_format = "GLB"

        # 3. Art style & world theme
        if "psx" in p or "ps1" in p:
            style = "psx-horror-style"
        elif "horror" in p:
            style = "horror-style"
        elif "lowpoly" in p or "low-poly" in p or "low poly" in p:
            style = "lowpoly-style"
        elif "anime" in p or "cel" in p:
            style = "anime-style"
        elif "voxel" in p:
            style = "voxel-style"
        elif "realistic" in p or "photoreal" in p:
            style = "realistic-style"
        else:
            style = "stylized-style"

        world_theme: str | None = None
        if "fantasy" in p or "medieval" in p or "zelda" in p or "rune" in p:
            world_theme = "fantasy-worlds"
        elif "sci-fi" in p or "scifi" in p or "cyberpunk" in p:
            world_theme = "sci-fi-punk-worlds"

        # 4. Animation & rigging scope
        animated_cues = ("animat", "run cycle", "walk cycle", "idle", "rigged and animated")
        rigged_cues = ("rig", "rigging", "armature", "skinned", "deform")
        if any(k in p for k in animated_cues):
            animation_mode = "animated"
        elif any(k in p for k in rigged_cues):
            animation_mode = "rigged"
        else:
            animation_mode = "static"

        is_character_like = asset_type in ("character", "creature")
        needs_sculpting = is_character_like or ("sculpt" in p)
        needs_retopology = is_character_like or animation_mode != "static" or ("retopo" in p)
        needs_hair = asset_type == "character" and ("no hair" not in p)
        needs_cloth = "cloth sim" in p or "cape physics" in p
        needs_textures = "texture" in p or "bake" in p or is_character_like

        if asset_type in ("character", "creature"):
            poly_budget = 25000
            texture_budget = "2048x2048"
            mat_budget = 6
        elif asset_type == "environment":
            poly_budget = 50000
            texture_budget = "2048x2048"
            mat_budget = 8
        elif asset_type == "weapon":
            poly_budget = 8000
            texture_budget = "2048x2048"
            mat_budget = 3
        else:
            poly_budget = 4000
            texture_budget = "1024x1024"
            mat_budget = 3

        return {
            "asset_type": asset_type,
            "target_engine": target_engine,
            "style_skill": style,
            "world_theme_skill": world_theme,
            "animation_mode": animation_mode,
            "reference_mode": reference_mode,
            "needs_sculpting": needs_sculpting,
            "needs_retopology": needs_retopology,
            "needs_hair": needs_hair,
            "needs_cloth": needs_cloth,
            "needs_textures": needs_textures,
            "budgets": {
                "poly_budget_tris": poly_budget,
                "texture_budget": texture_budget,
                "material_budget": mat_budget,
                "export_format": export_format,
            },
        }

    def plan_production(
        self,
        prompt: str,
        *,
        reference_mode: str = "TEXT_ONLY",
        asset_name: str = "ProductionAsset",
    ) -> dict[str, Any]:
        """Select only relevant skills in upstream pipeline order and justify omissions."""
        if reference_mode not in SUPPORTED_PRODUCTION_MODES:
            raise ValueError(
                f"Unsupported production mode {reference_mode!r}. "
                f"Only Blender-only modes {SUPPORTED_PRODUCTION_MODES} are allowed (no IMAGE_TO_3D)."
            )
        discovered = self.registry.discover()
        analysis = self.analyze_request(prompt, reference_mode=reference_mode)
        asset_type = analysis["asset_type"]
        style_skill = analysis["style_skill"]
        world_skill = analysis["world_theme_skill"]
        anim_mode = analysis["animation_mode"]
        engine = analysis["target_engine"]

        selected: list[dict[str, Any]] = []
        omitted: list[dict[str, str]] = []

        def add_skill(skill_id: str, stage: str, reason: str, extra_refs: list[str] | None = None) -> None:
            meta = discovered.get(skill_id)
            refs = list(extra_refs or [])
            if meta is not None:
                for r in meta.references:
                    if r not in refs and "../references/reference-image-match" not in r:
                        pass
            selected.append(
                {
                    "skill_id": skill_id,
                    "production_stage": stage,
                    "selection_reason": reason,
                    "required_operations": list(_REQUIRED_OPS_BY_SKILL.get(skill_id, ["execute_workflow", "validate_stage"])),
                    "installed": meta is not None,
                    "skill_md_path": meta.skill_md_path if meta is not None else None,
                    "recommended_references": refs,
                }
            )

        director_refs = ["../references/asset-pipeline.md", "../references/mcp-integration.md", "../references/mcp-tools.md"]
        if reference_mode == "REFERENCE_GUIDED":
            director_refs.extend(
                [
                    "../references/reference-image-match.md",
                    "../references/reference-analysis-template.md",
                    "../references/visual-match-checklist.md",
                ]
            )

        add_skill(
            "blender-director",
            "planning",
            "Primary orchestrator defining production brief, budgets, and stage-specific skill routing.",
            extra_refs=director_refs,
        )

        if reference_mode == "REFERENCE_GUIDED":
            ref_doc = (self.registry.shared_references_dir() / "reference-image-match.md").resolve()
            selected.append(
                {
                    "skill_id": "reference-image-match",
                    "production_stage": "reference_analysis_and_match",
                    "selection_reason": "REFERENCE_GUIDED mode requires reference analysis, camera match, and Blender-only render comparison passes.",
                    "required_operations": list(_REQUIRED_OPS_BY_SKILL["reference-image-match"]),
                    "installed": ref_doc.is_file(),
                    "skill_md_path": str(ref_doc),
                    "recommended_references": [
                        "../references/reference-analysis-template.md",
                        "../references/visual-match-checklist.md",
                    ],
                }
            )

        # World / archetype context when directly applicable
        if world_skill and asset_type in ("environment", "everyday_prop", "character", "weapon") and world_skill in discovered:
            add_skill(world_skill, "art_direction", f"Prompt specifies {world_skill.replace('-worlds', '')} world direction.")
        if asset_type == "character" and ("fantasy" in (prompt or "").lower() or "zelda" in (prompt or "").lower() or "knight" in (prompt or "").lower()):
            add_skill("character-archetypes", "art_direction", "Named fantasy character archetype proportions and traits.")

        # Discipline modeling routing (exclusive to relevant discipline)
        if asset_type == "character":
            add_skill("character-artist", "modeling", "Human character anatomy, face, proportions, and costume construction.")
        elif asset_type == "creature":
            add_skill("creature-artist", "modeling", "Non-human creature anatomy and form design.")
        elif asset_type in ("weapon", "hard_surface_prop"):
            add_skill("hard-surface", "modeling", "Hard-surface silhouette, bevels, and mechanical construction.")
        elif asset_type == "vehicle":
            add_skill("vehicle-artist", "modeling", "Vehicle chassis and mechanical component hierarchy.")
        elif asset_type == "environment":
            add_skill("environment-artist", "modeling", "Environment kit architecture and modular construction.")
            add_skill("scene-assembly", "layout", "Large environment collection layout and scene composition.")
        elif asset_type == "vegetation":
            add_skill("vegetation-artist", "modeling", "Foliage/tree form construction.")
        else:
            add_skill("prop-artist", "modeling", "Grounded prop proportions, real-world scale, and bevel details.")

        # Style skill
        if style_skill in discovered:
            add_skill(style_skill, "art_direction", f"Requested visual style matches {style_skill}.")

        # Sculpting & retopology only when warranted
        if analysis["needs_sculpting"]:
            add_skill("sculpting", "sculpt_refinement", "Organic/character form refinement pass required before retopology.")
        if analysis["needs_retopology"]:
            add_skill("retopology", "topology", "Deformation/game-ready quad-dominant topology required before UVs and rigging.")
        if analysis["needs_hair"]:
            add_skill("hair-groom", "hair", "Character hair mass and silhouette construction.")
        if analysis["needs_cloth"]:
            add_skill("cloth-sim", "cloth", "Dynamic cloth simulation requested.")

        # UVs & Materials (for asset production)
        if asset_type != "environment":
            add_skill("uv-workflow", "uvs", "Unwrapping and packing UVs before material/texture assignment.")
        add_skill("materials", "materials", "Stylized/PBR material setup with MAT_* naming and budget control.")
        if analysis["needs_textures"]:
            add_skill("texture-workflow", "textures", "Texture map baking/binding required for hero/character asset.")

        if asset_type == "environment" or reference_mode == "REFERENCE_GUIDED":
            if "lighting" not in {s["skill_id"] for s in selected}:
                add_skill("lighting", "lighting", "Mood and key/fill/rim lighting setup matching art/reference direction.")
            if "rendering" not in {s["skill_id"] for s in selected}:
                add_skill("rendering", "rendering", "Camera framing and rendered image pass generation.")

        # Pre-rig / modeling QA gate
        add_skill(
            "qa-review",
            "qa_modeling",
            "Mandatory visual and checklist inspection (Gate A / look review) prior to rigging or export.",
            extra_refs=["../references/validation-checklist.md"],
        )

        # Rigging & Animation only when requested
        if anim_mode in ("rigged", "animated"):
            add_skill("rigging", "rigging", "Armature, bone hierarchy, and skin weight deformation.")
        if anim_mode == "animated":
            add_skill("animation", "animation", "Keyframe animation action and playback verification.")
        if anim_mode in ("rigged", "animated"):
            add_skill("qa-review", "qa_rig_anim", "Gate B/C deformation and animation playback visual inspection.")

        # Optimization & Export
        if asset_type != "environment" or engine != "generic":
            add_skill("asset-optimization", "optimization", "Polycount budget, applied transforms, and manifold/normal checks.")
            add_skill("export-pipeline", "export", f"Validated {analysis['budgets']['export_format']} export and reimport check.")
            if engine == "godot":
                add_skill("godot-export", "engine_export", "Godot GLB/node naming and compatibility check.")
            elif engine == "unity":
                add_skill("unity-export", "engine_export", "Unity scale and import verification.")
            elif engine == "unreal":
                add_skill("unreal-export", "engine_export", "Unreal FBX/collision verification.")

        selected_ids = {item["skill_id"] for item in selected}

        # Explicitly justify key omissions so routing decisions are auditable
        omission_catalog = [
            ("character-artist", "Asset is not a human character."),
            ("creature-artist", "Asset is not a creature or monster."),
            ("vehicle-artist", "Asset is not a vehicle or mech."),
            ("environment-artist", "Asset is a single prop/character rather than a modular environment."),
            ("vegetation-artist", "Asset is not foliage or vegetation."),
            ("hard-surface", "Asset uses prop/organic discipline rather than hard-surface sci-fi/weapon construction."),
            ("prop-artist", "Asset is not a grounded prop."),
            ("sculpting", "Selected static prop/hard-surface workflow does not require organic high-poly sculpting."),
            ("retopology", "Simple static prop modeling achieves target budget without separate retopology pass."),
            ("hair-groom", "No hair or fur elements apply to this asset."),
            ("cloth-sim", "No cloth simulation was requested."),
            ("rigging", "Asset is static; no skeletal rigging requested."),
            ("animation", "Asset is static; no keyframe animation requested."),
            ("texture-workflow", "Lightweight procedural/principled material workflow suffices without dedicated texture bake."),
            ("lod-pipeline", "LOD chain omitted unless specifically requested by engine budget."),
            ("horror-style", "Requested art direction is not horror."),
            ("psx-horror-style", "Requested art direction is not PSX horror."),
            ("godot-export", "Target engine is not specifically Godot."),
            ("unity-export", "Target engine is not Unity."),
            ("unreal-export", "Target engine is not Unreal."),
        ]
        for skill_id, reason in omission_catalog:
            if skill_id not in selected_ids:
                omitted.append({"skill_id": skill_id, "omission_reason": reason})

        if reference_mode == "TEXT_ONLY":
            omitted.append(
                {
                    "skill_id": "reference-image-match",
                    "omission_reason": "TEXT_ONLY mode explicitly prohibits reference images; evaluating against textual specification instead.",
                }
            )

        return {
            "asset_name": asset_name,
            "prompt": prompt,
            "reference_mode": reference_mode,
            "blender_only_production": True,
            "external_ai_3d_generators_allowed": False,
            "analysis": analysis,
            "selected_skills": selected,
            "omitted_skills": omitted,
            "brief_markdown": self._format_brief_markdown(asset_name, analysis, selected, omitted),
        }

    def _format_brief_markdown(
        self,
        asset_name: str,
        analysis: dict[str, Any],
        selected: list[dict[str, Any]],
        omitted: list[dict[str, str]],
    ) -> str:
        b = analysis["budgets"]
        lines = [
            f"## Production Brief: {asset_name}",
            "",
            f"**Type:** {analysis['asset_type']}",
            f"**Target:** {analysis['target_engine']}",
            f"**Style:** {analysis['style_skill']}",
            f"**Reference Mode:** {analysis['reference_mode']}",
            f"**Poly Budget:** {b['poly_budget_tris']} tris",
            f"**Texture Budget:** {b['texture_budget']}",
            f"**Material Budget:** max {b['material_budget']} materials",
            f"**Export:** {b['export_format']}",
            f"**Animation:** {analysis['animation_mode']}",
            "",
            "### Active Skills (Stage Order)",
        ]
        for idx, item in enumerate(selected, start=1):
            lines.append(f"{idx}. `{item['skill_id']}` ({item['production_stage']}) — {item['selection_reason']}")
        lines.extend(["", "### Key Omitted Skills"])
        for item in omitted[:8]:
            lines.append(f"- `{item['skill_id']}` — {item['omission_reason']}")
        return "\n".join(lines)
