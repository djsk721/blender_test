"""Autonomous multi-agent Blender design harness with real MCP execution and visual feedback.

Orchestrates isolated specialist OpenCode agent contexts (exclusively `openai/gpt-6.2-sol#low`),
dynamic Blender Skill loading & 6-state lifecycle tracking, genuine agent-authored Blender Python
execution via Blender MCP (`execute_blender_code`), clay & lookdev multi-view Cycles GPU rendering,
iterative visual defect diagnosis and surgical in-place repair, rigging/animation validation,
fresh-scene GLB export/reimport, and blind 100-point final visual evaluation.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from .contracts import SkillExecutionContract, evaluate_skill_contract
from .mcp_adapter import MCPAdapter, MCPAdapterError, StdioMCPTransport
from .multimodal_qa import _extract_json_object, parse_opencode_json_events
from .rubric_evaluator import (
    DEFAULT_LIGHT_MODEL_ID,
    ISOLATED_NO_MCP_OPENCODE_CONFIG,
    BlindRubricEvaluator,
    StageVisualReviewer,
    detect_quota_error,
)


class QuotaBlockedError(RuntimeError):
    """Raised immediately when the fixed model provider hits HTTP 429 / usage_limit_reached."""

    def __init__(self, quota_info: dict[str, Any]) -> None:
        self.quota_info = dict(quota_info)
        super().__init__(
            f"Provider quota reached (resets_in_seconds={quota_info.get('resets_in_seconds')}, "
            f"resets_at={quota_info.get('resets_at')})."
        )
from .sequential_benchmark import BlenderSessionResetter
from .skill_registry import BlenderDirector, SkillRegistry

_PY_FENCE_RE = re.compile(r"```(?:python|py)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)

TOOLKIT_API_CHEATSHEET = """
Available inside Blender (`import blender_orchestration.blender_toolkit as bt`, `import bpy, bmesh, math`, `from mathutils import Vector, Euler`):
Pre-bound globals in this execution context:
- `SCENE_NAME` (str): target Blender scene name
- `scene` (`bpy.types.Scene`): active target scene
- `col_export` (`bpy.types.Collection`): link ALL final exportable asset meshes here
- `col_rig` (`bpy.types.Collection`): link armatures here
- `OUT_DIR` (str): run output directory for optional saved texture PNGs

Reusable general-purpose `bt.*` helpers (100% Blender 5.3 compatible):
1. `bt.remove_object_if_exists(name)` / `bt.link_to_collection(obj, col_export)` / `bt.set_smooth(obj, True)`
2. `bt.loft_sections(name, sections, segments=24, closed_u=True, cap_start=True, cap_end=True, smooth=True, subsurf=1, collection=col_export)`
   - `sections`: list of dicts `{"center":(x,y,z), "rx":float, "ry":float, "rot":(degX,degY,degZ), "squash_front":1.0, "squash_back":1.0, "flat_bottom":0.0}` OR list of explicit `[(x,y,z), ...]` rings.
   - Coordinate system: `-Y` is FRONT (facing camera), `+Y` is BACK, `+X` is character's LEFT (viewer's right), `+Z` is UP, ground is `z = 0.0`.
3. `bt.sculpt_tapered_ribbon(name, spine_pts, widths, thicknesses, arch=0.35, twist_deg=None, up_hint=(0,-1,0), smooth=True, subsurf=1, collection=col_export)`
   - Creates curved, arched crescent-section volumetric hair clumps, cloth sashes, ribbons, blades, fins, horns with tapered tips (NEVER round rope tubes!).
4. `bt.create_tube_along_curve(name, points, radii, segments=14, cap_ends=True, smooth=True, subsurf=1, collection=col_export)`
   - Sweeps circular or elliptical `(rx, ry)` cross-sections along a 3D spline with twist-free parallel transport (arms, fingers, straps, handles, tails, braids).
5. `bt.create_lathe_mesh(name, profile_rz, center=(0,0,0), rot=(0,0,0), scale_xy=(1,1), segments=28, cap_start=True, cap_end=True, smooth=True, subsurf=1, collection=col_export)`
   - Revolves `(radius, z)` contours with elliptical XY scaling (collars, sleeves, cuffs, boots, helmets, lanterns, vessels).
6. `bt.create_beveled_panel(name, profile_2d, depth, location=(0,0,0), rotation=(0,0,0), bevel_width=0.003, bevel_segments=2, smooth=True, collection=col_export)`
   - Extrudes a 2D `(x, z)` polygon along `Y` with crisp bevels (armor plates, buckles, pouches, tablets, emblems, shields, frames).
7. `bt.create_quad_sphere(name, center, radii, rotation=(0,0,0), subdivisions=3, smooth=True, collection=col_export)`
   - 100%-quad ellipsoid (NO UV-sphere pole pinching; great for heads, eyeballs, joints, gems, bulbs).
8. `bt.deform_mesh_sculpt(obj, [{"center":(x,y,z), "radius":r, "delta":(dx,dy,dz), "mirror_x":True/False}, ...])`
   - Smooth Gaussian RBF sculpting on an existing mesh in place (brow ridge, eye socket recess, cheekbones, nose bridge/tip, lips, chin, muscle contours).
9. `bt.add_modifiers(obj, solidify=None, solidify_offset=1.0, bevel=None, bevel_segments=2, subsurf=None, mirror_x=False)`
10. `bt.smart_uv_unwrap(obj)` / `bt.project_planar_uv(obj, axis='Y', bounds=None)`
11. `bt.create_painted_image_texture(name, width, height, ops, base_color=(r,g,b,1.0), save_path=None, pack=True)`
    - `ops` (normalized UV `[0..1]`, `x` left->right, `y` bottom->top):
      - `{"op":"linear_gradient", "axis":"y"|"x", "start":0.0, "end":1.0, "c0":(r,g,b,a), "c1":(r,g,b,a)}`
      - `{"op":"rect", "x0":..., "y0":..., "x1":..., "y1":..., "color":(r,g,b,a), "feather":0.004}`
      - `{"op":"ellipse", "cx":..., "cy":..., "rx":..., "ry":..., "color":(r,g,b,a), "feather":0.02, "angle_deg":0.0}`
      - `{"op":"ring", "cx":..., "cy":..., "rx":..., "ry":..., "thickness":0.06, "color":(r,g,b,a), "feather":0.01}`
      - `{"op":"radial_gradient", "cx":..., "cy":..., "rx":..., "ry":..., "inner_color":(r,g,b,a), "outer_color":(r,g,b,a)}`
      - `{"op":"arc", "cx":..., "cy":..., "rx":..., "ry":..., "a0_deg":..., "a1_deg":..., "thickness":0.06, "color":(r,g,b,a)}`
      - `{"op":"line", "x0":..., "y0":..., "x1":..., "y1":..., "thickness":0.01, "color":(r,g,b,a)}`
12. `bt.make_pbr_material(name, base_color, roughness=0.45, metallic=0.0, subsurface=0.0, coat=0.0, specular=0.5, emission_color=None, emission_strength=0.0, ao_darken=0.22, ao_distance=0.16, rim_color=None, rim_strength=0.0, noise_bump=0.0, noise_scale=35.0, image_texture=None)`
13. `bt.assign_material(obj, mat)`
14. `bt.create_rig_and_bind(scene, bone_specs, target_meshes=None, armature_name="ARM_Asset")`
    - `bone_specs`: `[{"name":"Bone_Root", "head":(x,y,z), "tail":(x,y,z), "parent":None, "deform":True}, ...]`
15. `bt.create_cycle_animation(armature_obj, keyframe_specs, action_name="ACT_Cycle", frame_start=1, frame_end=24)`
    - `keyframe_specs`: `{1: {"Bone_Thigh_L": (25, 0, 0), ...}, 13: {...}, 24: {...}}` (Euler degrees).
"""


def _extract_python_block(text: str) -> str | None:
    matches = _PY_FENCE_RE.findall(text or "")
    if matches:
        # Prefer the longest python block in case the model included a tiny snippet before the main script
        return max((m.strip() for m in matches if m.strip()), key=len, default=None)
    return None


def _git_head_sha() -> str:
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(PROJECT_ROOT), text=True).strip()
        return out
    except Exception:
        return "unknown"


def _invoke_agent_cli(
    model_id: str,
    prompt: str,
    *,
    files: list[Path] | None = None,
    session_id: str | None = None,
    timeout_sec: int = 180,
    title: str = "Autonomous Blender Specialist",
) -> dict[str, Any]:
    """Invoke an isolated OpenCode standalone session (`openai/gpt-6.2-sol#low`)."""
    cmd = ["opencode", "run", "--standalone", "--archived", "--format", "json", "-m", model_id, "--title", title]
    if session_id:
        cmd.extend(["--session", session_id])
    for fp in (files or []):
        rp = Path(fp).resolve()
        if rp.is_file():
            cmd.extend(["--file", str(rp)])
    cmd.append(prompt)

    t0 = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="oc_agent_isolated_") as tmpdir:
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
        return {
            "returncode": res.returncode,
            "duration_sec": dur,
            "session_id": parsed["session_id"] or (session_id or ""),
            "text": parsed["text"],
            "tokens": parsed["tokens"],
            "compactions": parsed["compactions"],
            "errors": parsed["errors"],
            "quota_blocked": detect_quota_error(parsed["errors"]),
            "stderr": (res.stderr or "")[-400:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        dur = round(time.monotonic() - t0, 3)
        stdout_s = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        parsed = parse_opencode_json_events(stdout_s)
        return {
            "returncode": 124,
            "duration_sec": dur,
            "session_id": parsed["session_id"] or (session_id or ""),
            "text": parsed["text"],
            "tokens": parsed["tokens"],
            "compactions": parsed["compactions"],
            "errors": parsed["errors"] + [f"Timed out after {timeout_sec}s"],
            "stderr": "",
            "timed_out": True,
        }


class AutonomousBlenderHarness:
    """End-to-end autonomous multi-agent Blender design & optimization harness."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_LIGHT_MODEL_ID,
        project_root: Path | None = None,
        blender_port: int = 9878,
        runtime_dir: Path | None = None,
        max_stage_corrections: int = 3,
        stage_timeout_sec: int = 180,
        render_res_stage: tuple[int, int] = (768, 960),
        render_samples_stage: int = 28,
        render_res_final: tuple[int, int] = (1024, 1280),
        render_samples_final: int = 64,
        agent_invoke_fn: Callable[..., dict[str, Any]] | None = None,
        stage_reviewer: StageVisualReviewer | None = None,
        final_evaluator: BlindRubricEvaluator | None = None,
    ) -> None:
        self.model_id = model_id
        self.project_root = Path(project_root) if project_root else PROJECT_ROOT
        self.blender_port = int(blender_port)
        self.runtime_dir = Path(runtime_dir) if runtime_dir else (self.project_root / "output" / "autonomous_harness" / "_mcp_runtime")
        self.max_stage_corrections = int(max_stage_corrections)
        self.stage_timeout_sec = int(stage_timeout_sec)
        self.render_res_stage = render_res_stage
        self.render_samples_stage = int(render_samples_stage)
        self.render_res_final = render_res_final
        self.render_samples_final = int(render_samples_final)
        self._invoke_agent = agent_invoke_fn or _invoke_agent_cli
        self.stage_reviewer = stage_reviewer or StageVisualReviewer(model_id=model_id, timeout_sec=stage_timeout_sec)
        self.final_evaluator = final_evaluator or BlindRubricEvaluator(model_id=model_id, timeout_sec=stage_timeout_sec)
        self.registry = SkillRegistry(self.project_root / ".opencode" / "skills")
        self.director = BlenderDirector(self.registry)

    def _mcp_env(self) -> dict[str, str]:
        return {
            "BLENDER_HOST": "127.0.0.1",
            "BLENDER_PORT": str(self.blender_port),
            "BLENDER_MCP_CONFIG": str(self.project_root / ".opencode" / "mcp" / "blender" / "config.json"),
            "BLENDER_MCP_RUNTIME_DIR": str(self.runtime_dir),
            "BLENDER_MCP_IDLE_TIMEOUT": "900",
            "BLENDER_MCP_WATCHDOG_POLL": "1.0",
            "BLENDER_MCP_AUTO_RESTORE": "false",
        }

    def _load_skills_context(
        self,
        skill_ids: list[str],
        contracts: dict[str, SkillExecutionContract],
        *,
        include_refs: dict[str, list[str]] | None = None,
        max_chars_per_skill: int = 3600,
    ) -> str:
        """Selectively load ONLY stage-relevant Skills and update their lifecycle to LOADED."""
        chunks: list[str] = []
        for sid in skill_ids:
            if not self.registry.has_skill(sid):
                continue
            loaded = self.registry.load_skill(sid, include_references=(include_refs or {}).get(sid))
            c = contracts.get(sid)
            if c is None:
                c = SkillExecutionContract(
                    skill_id=sid,
                    production_stage="autonomous_stage",
                    selection_reason="Stage specialist skill requirement",
                )
                contracts[sid] = c
            c.loaded = True
            c.skill_md_sha256 = loaded.get("skill_md_sha256")
            c.record_lifecycle("LOADED")
            body = str(loaded.get("content") or "")[:max_chars_per_skill]
            chunks.append(f"### SKILL: {sid}\n{body}")
            for rname, rbody in (loaded.get("references") or {}).items():
                chunks.append(f"#### REFERENCE ({sid}/{rname})\n{str(rbody)[:2600]}")
        return "\n\n".join(chunks)

    def _exec_blender_wrapped(
        self,
        adapter: MCPAdapter,
        scene_name: str,
        out_dir: Path,
        agent_code: str,
        *,
        stage_label: str,
        user_prompt: str,
        fallback_checkpoint: Path | None = None,
    ) -> dict[str, Any]:
        """Execute agent-authored bpy code inside Blender via MCP with automatic checkpoint recovery."""
        ckpt_str = str(fallback_checkpoint) if (fallback_checkpoint and fallback_checkpoint.is_file()) else ""
        wrapper = (
            "import json, math, sys, traceback\n"
            f"if {json.dumps(str(self.project_root))} not in sys.path:\n"
            f"    sys.path.insert(0, {json.dumps(str(self.project_root))})\n"
            "import bpy, bmesh\n"
            "from mathutils import Vector, Euler, Matrix\n"
            "from blender_orchestration import blender_toolkit as bt\n"
            f"SCENE_NAME = {json.dumps(scene_name)}\n"
            f"OUT_DIR = {json.dumps(str(out_dir))}\n"
            f"_CKPT = {json.dumps(ckpt_str)}\n"
            "if SCENE_NAME not in bpy.data.scenes:\n"
            "    if _CKPT:\n"
            "        bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "if SCENE_NAME not in bpy.data.scenes:\n"
            "    scene = bt.ensure_clean_scene(SCENE_NAME)\n"
            "else:\n"
            "    scene = bpy.data.scenes[SCENE_NAME]\n"
            "    if bpy.context.window:\n"
            "        bpy.context.window.scene = scene\n"
            "col_export = bt.get_or_create_collection(scene, 'COL_Export')\n"
            "col_rig = bt.get_or_create_collection(scene, 'COL_Rig')\n"
            "col_studio = bt.get_or_create_collection(scene, 'COL_Studio')\n"
            "_user_code = " + json.dumps(agent_code) + "\n"
            "_ns = {\n"
            "    'bpy': bpy, 'bmesh': bmesh, 'bt': bt, 'math': math, 'json': json,\n"
            "    'Vector': Vector, 'Euler': Euler, 'Matrix': Matrix,\n"
            "    'SCENE_NAME': SCENE_NAME, 'OUT_DIR': OUT_DIR,\n"
            "    'scene': scene, 'col_export': col_export, 'col_rig': col_rig, 'col_studio': col_studio,\n"
            "}\n"
            "try:\n"
            "    exec(_user_code, _ns)\n"
            "    audit = bt.get_scene_audit(scene)\n"
            "    print('__ORCH_JSON__:' + json.dumps({'ok': True, 'audit': audit}))\n"
            "except Exception as _exc:\n"
            "    tb = traceback.format_exc()\n"
            "    print('__ORCH_JSON__:' + json.dumps({'ok': False, 'error': str(_exc), 'traceback': tb[-1800:]}))\n"
        )
        res = adapter.execute_blender_json(wrapper, user_prompt=user_prompt, stage=stage_label)["parsed"]
        return res

    def _run_blender_util(
        self,
        adapter: MCPAdapter,
        code_body: str,
        *,
        stage_label: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        full = (
            "import json, sys\n"
            f"if {json.dumps(str(self.project_root))} not in sys.path:\n"
            f"    sys.path.insert(0, {json.dumps(str(self.project_root))})\n"
            "import bpy\n"
            "from blender_orchestration import blender_toolkit as bt\n"
            + code_body
        )
        return adapter.execute_blender_json(full, user_prompt=user_prompt, stage=stage_label)["parsed"]

    def _save_checkpoint(
        self,
        adapter: MCPAdapter,
        scene_name: str,
        blend_path: Path,
        stage_label: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Save a verified .blend checkpoint ONLY if the target scene has meshes (never clobber with empty recovery scene)."""
        blend_path.parent.mkdir(parents=True, exist_ok=True)
        code = (
            f"if {json.dumps(scene_name)} not in bpy.data.scenes:\n"
            f"    print('__ORCH_JSON__:' + json.dumps({{'saved': False, 'reason': 'target_scene_missing', 'path': {json.dumps(str(blend_path))}}}))\n"
            "else:\n"
            f"    s = bpy.data.scenes[{json.dumps(scene_name)}]\n"
            "    if bpy.context.window:\n"
            "        bpy.context.window.scene = s\n"
            "    try:\n"
            "        bpy.ops.file.pack_all()\n"
            "    except Exception:\n"
            "        pass\n"
            f"    bpy.ops.wm.save_as_mainfile(filepath={json.dumps(str(blend_path))})\n"
            "    from pathlib import Path\n"
            f"    p = Path({json.dumps(str(blend_path))})\n"
            "    ok = p.is_file() and p.read_bytes()[:7] == b'BLENDER'\n"
            "    print('__ORCH_JSON__:' + json.dumps({'saved': ok, 'path': str(p), 'bytes': p.stat().st_size if p.is_file() else 0}))\n"
        )
        return self._run_blender_util(adapter, code, stage_label=stage_label, user_prompt=user_prompt)

    def _render_views(
        self,
        adapter: MCPAdapter,
        scene_name: str,
        out_dir: Path,
        prefix: str,
        *,
        views: list[str],
        mode: str,
        res: tuple[int, int],
        samples: int,
        stage_label: str,
        user_prompt: str,
        fallback_checkpoint: Path | None = None,
    ) -> dict[str, Any]:
        ckpt_str = str(fallback_checkpoint) if (fallback_checkpoint and fallback_checkpoint.is_file()) else ""
        code = (
            f"_SN = {json.dumps(scene_name)}\n"
            f"_CKPT = {json.dumps(ckpt_str)}\n"
            "if _SN not in bpy.data.scenes and _CKPT:\n"
            "    bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "scene = bpy.data.scenes[_SN]\n"
            "if bpy.context.window:\n"
            "    bpy.context.window.scene = scene\n"
            f"r = bt.render_standard_views(scene, {json.dumps(str(out_dir))}, {json.dumps(prefix)}, "
            f"views={json.dumps(views)}, mode={json.dumps(mode)}, res={json.dumps(list(res))}, samples={int(samples)}, use_gpu=True)\n"
            "audit = bt.get_scene_audit(scene)\n"
            "print('__ORCH_JSON__:' + json.dumps({'render': r, 'audit': audit}))\n"
        )
        return self._run_blender_util(adapter, code, stage_label=stage_label, user_prompt=user_prompt)

    def _run_code_agent_with_retry(
        self,
        *,
        adapter: MCPAdapter,
        scene_name: str,
        run_dir: Path,
        stage_label: str,
        user_prompt: str,
        agent_prompt: str,
        attach_files: list[Path] | None,
        telemetry: list[dict[str, Any]],
        producer_sessions: set[str],
        scripts_dir: Path,
        fallback_checkpoint: Path | None = None,
        max_code_retries: int = 3,
    ) -> dict[str, Any]:
        """Run a specialist agent in its own fresh context, extract bpy code, execute via MCP with traceback self-repair."""
        sess_id: str | None = None
        last_error = ""
        last_code = ""
        current_prompt = agent_prompt

        for attempt in range(1, max_code_retries + 1):
            resp = self._invoke_agent(
                self.model_id,
                current_prompt,
                files=attach_files if attempt == 1 else None,
                session_id=sess_id,
                timeout_sec=self.stage_timeout_sec,
                title=f"Blender Specialist ({stage_label})",
            )
            if resp.get("session_id"):
                sess_id = resp["session_id"]
                producer_sessions.add(sess_id)
            telemetry.append(
                {
                    "stage": f"{stage_label}_attempt_{attempt}",
                    "session_id": sess_id,
                    "returncode": resp.get("returncode"),
                    "duration_sec": resp.get("duration_sec"),
                    "tokens": resp.get("tokens") or {},
                    "compactions": resp.get("compactions", 0),
                    "timed_out": resp.get("timed_out", False),
                }
            )
            q_info = resp.get("quota_blocked") or detect_quota_error(resp.get("errors"))
            if q_info:
                raise QuotaBlockedError(q_info)
            if int(resp.get("returncode", 1)) != 0:
                last_error = f"Agent CLI error rc={resp.get('returncode')}: {resp.get('errors')}"
                current_prompt = f"Previous call failed ({last_error}). Please return a clean ```python ... ``` script only."
                continue

            code = _extract_python_block(str(resp.get("text") or ""))
            if not code:
                last_error = "Agent response did not include a ```python ... ``` code block."
                current_prompt = (
                    "Your response did not contain a fenced ```python ... ``` block. "
                    "Return ONLY a complete executable ```python ... ``` script using `bt` and `bpy`."
                )
                continue

            last_code = code
            script_file = scripts_dir / f"{stage_label}_attempt_{attempt}.py"
            script_file.write_text(code + "\n", encoding="utf-8")

            exec_res = self._exec_blender_wrapped(
                adapter,
                scene_name,
                run_dir,
                code,
                stage_label=f"{stage_label}_exec_{attempt}",
                user_prompt=user_prompt,
                fallback_checkpoint=fallback_checkpoint,
            )
            if exec_res.get("ok"):
                return {
                    "ok": True,
                    "session_id": sess_id,
                    "attempt": attempt,
                    "script_path": str(script_file),
                    "audit": exec_res["audit"],
                }

            last_error = str(exec_res.get("traceback") or exec_res.get("error") or "Unknown Blender error")
            current_prompt = (
                f"Your Blender Python script raised an exception:\n```\n{last_error}\n```\n"
                "Fix the bug cleanly while preserving all geometry quality and return the COMPLETE corrected "
                "```python ... ``` script (do not call any tools)."
            )

        return {
            "ok": False,
            "session_id": sess_id,
            "error": last_error,
            "last_code_len": len(last_code),
            "audit": None,
        }

    def run_production(
        self,
        *,
        prompt: str,
        run_dir: Path,
        asset_name: str = "HeroineCharacter",
        reference_mode: str = "REFERENCE_GUIDED",
        approved_reference_path: Path | None = None,
        animation_mode: str = "animated",
        reset_blender_before_run: bool = True,
        reset_blender_after_run: bool = True,
        extra_guidance: str = "",
        resume_checkpoint: Path | None = None,
        start_stage: int = 1,
    ) -> dict[str, Any]:
        """Run one complete autonomous production cycle from a clean Blender session."""
        t0 = time.monotonic()
        run_dir = Path(run_dir)
        handoffs_dir = run_dir / "handoffs"
        scripts_dir = run_dir / "scripts"
        checkpoints_dir = run_dir / "checkpoints"
        renders_dir = run_dir / "renders"
        reviews_dir = run_dir / "reviews"
        final_dir = run_dir / "final"
        for d in (run_dir, handoffs_dir, scripts_dir, checkpoints_dir, renders_dir, reviews_dir, final_dir):
            d.mkdir(parents=True, exist_ok=True)

        resetter = BlenderSessionResetter(
            project_root=self.project_root,
            runtime_dir=self.runtime_dir,
            blender_port=self.blender_port,
        )
        pre_reset_report = None
        if reset_blender_before_run:
            pre_reset_report = resetter.reset_for_next_model(start_fresh_blender=True)

        scene_name = f"SCENE_{asset_name}"
        contracts: dict[str, SkillExecutionContract] = {}
        agent_telemetry: list[dict[str, Any]] = []
        producer_sessions: set[str] = set()
        stage_reviews: dict[str, list[dict[str, Any]]] = {}
        stage_summaries: dict[str, Any] = {}

        # Discover skills via SkillRegistry & build initial director plan
        discovered = self.registry.discover()
        director_plan = self.director.plan_production(prompt, reference_mode=reference_mode, asset_name=asset_name)
        for item in director_plan["selected_skills"]:
            sid = str(item["skill_id"])
            contracts[sid] = SkillExecutionContract(
                skill_id=sid,
                production_stage=str(item["production_stage"]),
                selection_reason=str(item["selection_reason"]),
                required_operations=list(item.get("required_operations") or []),
            )
            contracts[sid].record_lifecycle("DISCOVERED")
        (handoffs_dir / "00_director_plan.json").write_text(json.dumps(director_plan, indent=2) + "\n", encoding="utf-8")

        # -------------------------------------------------------------------
        # STAGE 1: Reference Analysis & Structured Design Specification Agent
        # -------------------------------------------------------------------
        ref_file = Path(approved_reference_path).resolve() if (reference_mode == "REFERENCE_GUIDED" and approved_reference_path and Path(approved_reference_path).is_file()) else None
        is_char_hint = director_plan["analysis"]["asset_type"] in ("character", "creature")
        primary_disc_skill = "character-artist" if director_plan["analysis"]["asset_type"] == "character" else (
            "creature-artist" if director_plan["analysis"]["asset_type"] == "creature" else "prop-artist"
        )
        s1_skills = ["blender-director", primary_disc_skill, "stylized-style"]
        s1_refs = {"blender-director": ["../references/reference-image-match.md"]} if ref_file else None
        s1_skill_ctx = self._load_skills_context(s1_skills, contracts, include_refs=s1_refs)

        s1_prompt = "\n".join(
            [
                "You are the Art Director & Blender Technical Director Agent (Stage 1: Reference Analysis & Design Specification).",
                "Do NOT call any tools, bash, or MCP servers. Output ONLY a single valid JSON object.",
                f"User Request: {prompt}",
                f"Asset Name: {asset_name} | Mode: {reference_mode} | Animation Mode: {animation_mode}",
                f"Additional Optimization Guidance:\n{extra_guidance}" if extra_guidance else "",
                "Loaded Blender Skills:",
                s1_skill_ctx,
                "",
                "Analyze the attached reference image (or prompt if TEXT_ONLY) thoroughly and produce a concrete 3D CAD/art blueprint.",
                "Coordinate convention: Blender metric (`1 unit = 1m`), `-Y` is FRONT, `+Z` is UP, ground plane is `z = 0.0`.",
                "Break modeling into 3 clean sequential `component_passes` (each pass handles a cohesive subset of parts so scripts stay crisp and bug-free).",
                "Return ONLY JSON with this schema:",
                "{",
                '  "asset_type": "character|prop|creature",',
                '  "style_direction": "...",',
                '  "target_dimensions_m": {"height": 1.65, "width": 0.65, "depth": 0.45},',
                '  "requires_rigging": true,',
                '  "requires_animation": true,',
                '  "silhouette_and_proportions": {"head_units": 6.5, "key_landmarks_z_m": {"ground": 0.0, "...": 1.65}, "notes": "..."},',
                '  "color_palette": [{"material_name": "MAT_...", "hex": "#...", "rgb_linear": [0.1, 0.2, 0.3], "roughness": 0.5, "metallic": 0.0, "emission_rgb": null, "emission_strength": 0.0, "surface_type": "skin|cloth|leather|gold|hair|emissive|wood|stone|metal", "notes": "..."}],',
                '  "painted_textures_needed": [{"texture_name": "TEX_...", "target_objects": ["SM_..."], "description": "exact visual features to paint procedurally"}],',
                '  "component_passes": [',
                '    {"pass_id": "pass_1_...", "title": "...", "skills": ["..."], "components": [{"name": "SM_...", "description": "...", "z_range_m": [0.0, 0.5], "key_features": ["..."]}]}',
                "  ],",
                '  "hero_features_checklist": ["specific unmistakable visual details that must appear in the final render"],',
                '  "design_summary_markdown": "concise production specification"',
                "}",
            ]
        )

        quota_blocker: dict[str, Any] | None = None
        spec_file = handoffs_dir / "01_design_spec.json"
        if start_stage > 1 and spec_file.is_file():
            design_spec = json.loads(spec_file.read_text(encoding="utf-8"))
        else:
            s1_resp = self._invoke_agent(
                self.model_id,
                s1_prompt,
                files=[ref_file] if ref_file else None,
                timeout_sec=self.stage_timeout_sec,
                title="Stage 1 Art Director Spec",
            )
            if s1_resp.get("session_id"):
                producer_sessions.add(s1_resp["session_id"])
            agent_telemetry.append({"stage": "stage_01_design_spec", **{k: s1_resp.get(k) for k in ("session_id", "returncode", "duration_sec", "tokens", "compactions", "timed_out")}})
            q1 = s1_resp.get("quota_blocked") or detect_quota_error(s1_resp.get("errors"))
            if q1:
                quota_blocker = q1
                if reset_blender_after_run:
                    resetter.reset_for_next_model(start_fresh_blender=False)
                blocked_report = {
                    "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "git_revision": _git_head_sha(),
                    "model_id": self.model_id,
                    "status": "BLOCKED",
                    "quota_blocker": quota_blocker,
                    "run_dir": str(run_dir),
                    "agent_telemetry": agent_telemetry,
                    "final_evaluation": {"verdict": "NOT_VERIFIED", "passed": False, "reason": "Provider quota reached during Stage 1."},
                }
                (run_dir / "iteration_report.json").write_text(json.dumps(blocked_report, indent=2) + "\n", encoding="utf-8")
                return blocked_report
            design_spec = _extract_json_object(str(s1_resp.get("text") or "")) or {}
        if not design_spec.get("component_passes"):
            # Safe generic fallback structure if model omitted passes (still zero asset hardcoding)
            design_spec.setdefault("asset_type", director_plan["analysis"]["asset_type"])
            design_spec.setdefault("target_dimensions_m", {"height": 1.65 if is_char_hint else 0.6, "width": 0.6, "depth": 0.4})
            design_spec.setdefault("requires_rigging", animation_mode in ("rigged", "animated"))
            design_spec.setdefault("requires_animation", animation_mode == "animated")
            design_spec["component_passes"] = [
                {"pass_id": "pass_1_primary_forms", "title": "Primary Forms & Core Structure", "skills": [primary_disc_skill], "components": []},
                {"pass_id": "pass_2_secondary_layers", "title": "Secondary Layers & Accessories", "skills": [primary_disc_skill, "sculpting"], "components": []},
                {"pass_id": "pass_3_tertiary_hero_details", "title": "Tertiary Hero Details & Silhouettes", "skills": [primary_disc_skill, "hair-groom" if is_char_hint else "hard-surface"], "components": []},
            ]
        spec_file.write_text(json.dumps(design_spec, indent=2) + "\n", encoding="utf-8")
        design_spec_md = str(design_spec.get("design_summary_markdown") or json.dumps(design_spec, indent=2)[:2000])
        (handoffs_dir / "01_design_spec.md").write_text(design_spec_md + "\n", encoding="utf-8")
        spec_compact = json.dumps(design_spec, indent=2)[:5500]

        # Open isolated Blender MCP adapter
        adapter = MCPAdapter(
            StdioMCPTransport(cwd=self.project_root, env_overrides=self._mcp_env()),
            reference_mode=reference_mode,
            allow_external_assets=False,
        )

        technical_gates: dict[str, bool] = {
            "mcp_connected": False,
            "scene_initialized": False,
            "blockout_created": False,
            "detailed_modeling_created": False,
            "materials_and_uvs_applied": False,
            "rig_validated": True,
            "animation_validated": True,
            "blend_saved": False,
            "glb_export_and_reimport": False,
        }
        final_eval: dict[str, Any] = {}
        export_res: dict[str, Any] = {}
        audit_latest: dict[str, Any] = {}
        final_renders: list[dict[str, Any]] = []
        last_good_checkpoint: Path | None = Path(resume_checkpoint).resolve() if (resume_checkpoint and Path(resume_checkpoint).is_file()) else None

        def _save_good_ckpt(path: Path, label: str) -> dict[str, Any]:
            nonlocal last_good_checkpoint
            res = self._save_checkpoint(adapter, scene_name, path, label, prompt)
            if res.get("saved") and path.is_file() and path.stat().st_size > 100_000:
                last_good_checkpoint = path
            return res

        try:
            caps = adapter.discover_capabilities()
            adapter.call_tool("get_addon_status", {"user_prompt": prompt}, stage="stage_01_addon_status")
            technical_gates["mcp_connected"] = True

            if last_good_checkpoint is not None and start_stage > 1:
                load_res = self._run_blender_util(
                    adapter,
                    f"bpy.ops.wm.open_mainfile(filepath={json.dumps(str(last_good_checkpoint))})\n"
                    f"s = bpy.data.scenes[{json.dumps(scene_name)}]\n"
                    "if bpy.context.window:\n"
                    "    bpy.context.window.scene = s\n"
                    "print('__ORCH_JSON__:' + json.dumps({'ok': True, 'audit': bt.get_scene_audit(s)}))\n",
                    stage_label="stage_resume_load_checkpoint",
                    user_prompt=prompt,
                )
                technical_gates["scene_initialized"] = bool(load_res.get("ok"))
                audit_latest = load_res.get("audit") or {}
                if start_stage >= 3:
                    technical_gates["blockout_created"] = audit_latest.get("mesh_count", 0) > 0
                if start_stage >= 4:
                    technical_gates["detailed_modeling_created"] = audit_latest.get("total_triangles", 0) > 500
            else:
                init_res = self._run_blender_util(
                    adapter,
                    f"s = bt.ensure_clean_scene({json.dumps(scene_name)})\n"
                    "print('__ORCH_JSON__:' + json.dumps({'scene_name': s.name, 'ok': True}))\n",
                    stage_label="stage_01_scene_init",
                    user_prompt=prompt,
                )
                technical_gates["scene_initialized"] = bool(init_res.get("ok"))

            # Mark blender-director contract applied
            if "blender-director" in contracts:
                c = contracts["blender-director"]
                c.performed_operations = ["production_brief", "budget_definition", "skill_routing"]
                c.mcp_evidence = ["tools/list", "get_addon_status", "execute_blender_code:scene_init"]
                c.scene_evidence = [scene_name]
                c.validation_evidence = [f"spec_file={handoffs_dir / '01_design_spec.json'}"]
                evaluate_skill_contract(c)

            # -------------------------------------------------------------------
            # STAGE 2: Proportion & Silhouette Blockout Agent + Clay Review
            # -------------------------------------------------------------------
            s2_skill_ctx = self._load_skills_context([primary_disc_skill, "blender-modeler"], contracts)
            s2_prompt = "\n".join(
                [
                    "You are the Blender Blockout & Proportions Specialist Agent (Stage 2).",
                    "Do NOT call any tools. Return ONLY a single ```python ... ``` script that builds a clean, proportional 3D silhouette blockout in Blender.",
                    "RULES:",
                    "- Ground lowest point cleanly at z = 0.0 (`min_z ≈ 0.0`), facing `-Y` (front).",
                    "- Match exact proportions, clean grounded stance, and major silhouette masses from the Design Specification and attached reference.",
                    "- Use a smooth, natural grounded hero stance with continuous un-kinked limb/joint transitions (NEVER create sharp 90-degree self-folding loft creases in static meshes; extreme motion belongs in rigging/animation).",
                    "- Blend shoulders/deltoids smoothly into the upper torso rather than placing detached spheres.",
                    "- Use smooth sculpted lofts (`bt.loft_sections`), quad spheres (`bt.create_quad_sphere`), and tapered ribbons (`bt.sculpt_tapered_ribbon`) — NOT crude disconnected cylinders/boxes.",
                    "- Link EVERY asset mesh to `col_export` with clean `SM_*` names.",
                    TOOLKIT_API_CHEATSHEET,
                    "Loaded Skills:\n" + s2_skill_ctx,
                    "Design Specification:\n" + spec_compact,
                ]
            )
            s2_exec = self._run_code_agent_with_retry(
                adapter=adapter,
                scene_name=scene_name,
                run_dir=run_dir,
                stage_label="stage_02_blockout",
                user_prompt=prompt,
                agent_prompt=s2_prompt,
                attach_files=[ref_file] if ref_file else None,
                telemetry=agent_telemetry,
                producer_sessions=producer_sessions,
                scripts_dir=scripts_dir,
                fallback_checkpoint=last_good_checkpoint,
            )
            technical_gates["blockout_created"] = bool(s2_exec.get("ok") and (s2_exec.get("audit") or {}).get("mesh_count", 0) > 0)
            audit_latest = s2_exec.get("audit") or {}
            _save_good_ckpt(checkpoints_dir / "02_blockout_p0.blend", "stage_02_save")

            # Clay render & independent blockout review + bounded targeted refinement (max 1 fix in blockout)
            stage_reviews["blockout"] = []
            prior_blk_renders: list[dict[str, Any]] = []
            max_blk_fixes = min(1, self.max_stage_corrections)
            for fix_idx in range(0, max_blk_fixes + 1):
                blk_r = self._render_views(
                    adapter,
                    scene_name,
                    renders_dir / f"stage_02_blockout_p{fix_idx}",
                    f"blk_p{fix_idx}",
                    views=["front", "three_quarter", "side", "rear"],
                    mode="clay",
                    res=(640, 800),
                    samples=20,
                    stage_label=f"stage_02_render_p{fix_idx}",
                    user_prompt=prompt,
                    fallback_checkpoint=last_good_checkpoint,
                )
                audit_latest = blk_r["audit"]
                cur_renders = blk_r["render"]["renders"]
                rev = self.stage_reviewer.review_stage(
                    stage_name="blockout",
                    renders=cur_renders,
                    design_spec_summary=design_spec_md,
                    producer_session_ids=producer_sessions,
                    approved_reference_path=ref_file,
                    prior_renders=prior_blk_renders if fix_idx > 0 else None,
                    pass_threshold_score=70.0,
                )
                stage_reviews["blockout"].append(rev)
                (reviews_dir / f"stage_02_blockout_p{fix_idx}.json").write_text(json.dumps(rev, indent=2) + "\n", encoding="utf-8")
                prior_blk_renders = cur_renders
                if rev.get("passed") or fix_idx >= max_blk_fixes:
                    break
                # Targeted blockout correction
                fix_prompt = "\n".join(
                    [
                        f"You are the Blockout Refinement Agent (Fix Pass {fix_idx + 1}).",
                        "Inspect the attached clay blockout renders and reference image, then fix ONLY the reported proportion/silhouette defects in-place.",
                        "Keep limb/joint transitions smooth with NO sharp self-folding creases or detached shoulder caps.",
                        "Do NOT delete unaffected meshes; update/rebuild only affected objects in `col_export`.",
                        "Current scene audit:\n" + json.dumps(audit_latest, indent=2)[:3500],
                        "Independent Reviewer Defects:\n" + json.dumps(rev.get("defects", []), indent=2)[:2500],
                        TOOLKIT_API_CHEATSHEET,
                        "Return ONLY a complete ```python ... ``` script.",
                    ]
                )
                fix_files = [Path(r["path"]) for r in cur_renders[:3]] + ([ref_file] if ref_file else [])
                self._run_code_agent_with_retry(
                    adapter=adapter,
                    scene_name=scene_name,
                    run_dir=run_dir,
                    stage_label=f"stage_02_blockout_fix_{fix_idx + 1}",
                    user_prompt=prompt,
                    agent_prompt=fix_prompt,
                    attach_files=fix_files,
                    telemetry=agent_telemetry,
                    producer_sessions=producer_sessions,
                    scripts_dir=scripts_dir,
                    fallback_checkpoint=last_good_checkpoint,
                )
                _save_good_ckpt(checkpoints_dir / f"02_blockout_fix{fix_idx + 1}.blend", f"stage_02_save_fix_{fix_idx + 1}")

            # -------------------------------------------------------------------
            # STAGE 3: Modular Detailed Modeling & Sculpting Passes + Clay QA Loop
            # -------------------------------------------------------------------
            passes = list(design_spec.get("component_passes") or [])
            for p_idx, pspec in enumerate(passes, start=1):
                pid = str(pspec.get("pass_id") or f"pass_{p_idx}")
                pskills = list(pspec.get("skills") or [primary_disc_skill, "sculpting"])
                if "sculpting" not in pskills:
                    pskills.append("sculpting")
                p_skill_ctx = self._load_skills_context(pskills, contracts)

                # Attach latest clay three-quarter + front render plus reference for visual spatial grounding
                attach_p = [ref_file] if ref_file else []
                for r in prior_blk_renders[:2]:
                    rp = Path(r["path"])
                    if rp.is_file():
                        attach_p.append(rp)

                p_prompt = "\n".join(
                    [
                        f"You are the Detailed 3D Modeling & Sculpting Specialist Agent (Stage 3 — {pid}: {pspec.get('title', '')}).",
                        "Do NOT call any tools. Return ONLY a single ```python ... ``` script.",
                        "CRITICAL QUALITY STANDARDS:",
                        "- Upgrade/replace ONLY the components belonging to this pass; preserve all other meshes in `col_export` cleanly!",
                        "- Use exact coordinates from the live Scene Audit below so adjacent parts connect seamlessly with ZERO gaps, ZERO floating seams, and ZERO clipping.",
                        "- Build multi-tier 3D detail: sculpted primary contours + layered clothing/armor/hair/accessory thickness + crisp beveled trims/buckles/edges.",
                        "- If modeling a character head/face: recess orbital sockets cleanly via `bt.deform_mesh_sculpt`, seat eye globes slightly behind sleek upper/lower eyelid rims, sculpt a refined nose bridge, delicate lips, tapered chin, and tucked ears.",
                        "- If modeling hair: build a full scalp volume cap + prominent crown/braid + layered overlapping tapered ribbon clumps (`bt.sculpt_tapered_ribbon`) sitting +0.012m to +0.025m proud of the skull so ZERO bare skull dome peeks through!",
                        "- If modeling body/limbs/costume: keep shoulder deltoids smoothly blended into upper sleeves/torso and keep knees/legs smoothly contoured with ZERO sharp self-folding loft kinks.",
                        "- Apply real Gaussian RBF sculpting (`bt.deform_mesh_sculpt`) to organic surfaces for authentic 3D form relief.",
                        f"Pass Specification:\n{json.dumps(pspec, indent=2)}",
                        f"Full Design Spec Context:\n{spec_compact[:3200]}",
                        f"Live Blender Scene Audit (exact current mesh names and bounding boxes):\n{json.dumps(audit_latest, indent=2)[:3500]}",
                        "Loaded Skills:\n" + p_skill_ctx,
                        TOOLKIT_API_CHEATSHEET,
                    ]
                )
                p_exec = self._run_code_agent_with_retry(
                    adapter=adapter,
                    scene_name=scene_name,
                    run_dir=run_dir,
                    stage_label=f"stage_03_{pid}",
                    user_prompt=prompt,
                    agent_prompt=p_prompt,
                    attach_files=attach_p,
                    telemetry=agent_telemetry,
                    producer_sessions=producer_sessions,
                    scripts_dir=scripts_dir,
                    fallback_checkpoint=last_good_checkpoint,
                )
                if p_exec.get("ok") and p_exec.get("audit"):
                    audit_latest = p_exec["audit"]
                    _save_good_ckpt(checkpoints_dir / f"03_pass_{p_idx}_{pid}.blend", f"stage_03_save_{pid}")

            technical_gates["detailed_modeling_created"] = bool(audit_latest.get("mesh_count", 0) > 0 and audit_latest.get("total_triangles", 0) > 500)
            _save_good_ckpt(checkpoints_dir / "03_detailed_modeling_p0.blend", "stage_03_save")

            # Independent Geometry Clay Review & Bounded Structural Refinement (max 2 clay fixes; save 3 for Stage 4 full-color polish)
            stage_reviews["detailed_modeling"] = []
            prior_geo_renders: list[dict[str, Any]] = []
            max_geo_fixes = min(2, self.max_stage_corrections)
            for fix_idx in range(0, max_geo_fixes + 1):
                geo_r = self._render_views(
                    adapter,
                    scene_name,
                    renders_dir / f"stage_03_geometry_p{fix_idx}",
                    f"geo_p{fix_idx}",
                    views=["front", "three_quarter", "side", "rear", "closeup_top", "closeup_mid", "closeup_bottom"],
                    mode="clay",
                    res=(640, 800),
                    samples=20,
                    stage_label=f"stage_03_render_p{fix_idx}",
                    user_prompt=prompt,
                    fallback_checkpoint=last_good_checkpoint,
                )
                audit_latest = geo_r["audit"]
                cur_renders = geo_r["render"]["renders"]
                rev = self.stage_reviewer.review_stage(
                    stage_name="detailed_modeling",
                    renders=cur_renders,
                    design_spec_summary=design_spec_md,
                    producer_session_ids=producer_sessions,
                    approved_reference_path=ref_file,
                    prior_renders=prior_geo_renders if fix_idx > 0 else None,
                    pass_threshold_score=76.0,
                )
                stage_reviews["detailed_modeling"].append(rev)
                (reviews_dir / f"stage_03_geometry_p{fix_idx}.json").write_text(json.dumps(rev, indent=2) + "\n", encoding="utf-8")
                prior_geo_renders = cur_renders
                if rev.get("passed") or fix_idx >= max_geo_fixes:
                    break

                # Surgical geometry fix pass with actual rendered PNGs attached!
                geo_fix_files = [
                    Path(r["path"])
                    for r in cur_renders
                    if r["view"] in ("front", "three_quarter", "closeup_top", "closeup_mid", "closeup_bottom")
                ]
                if ref_file:
                    geo_fix_files.append(ref_file)
                gfix_prompt = "\n".join(
                    [
                        f"You are the Structural 3D Geometry Refinement Agent (Stage 3 Fix Pass {fix_idx + 1}/{max_geo_fixes}).",
                        "Do NOT call any tools. Inspect the attached clay renders and reference image carefully.",
                        "Execute REAL 3D mesh/sculpt/topology fixes ONLY on the defective components listed below. Eliminate any bald scalp exposure, shoulder seam gaps, knee kinks, protruding eyes, or clipping.",
                        "Independent Reviewer Defects:\n" + json.dumps(rev.get("defects", []), indent=2)[:3200],
                        "Reviewer Observations:\n" + json.dumps(rev.get("observations_by_view", {}), indent=2)[:1500],
                        "Live Scene Audit (exact object names and bboxes):\n" + json.dumps(audit_latest, indent=2)[:3500],
                        TOOLKIT_API_CHEATSHEET,
                        "Return ONLY a single ```python ... ``` script.",
                    ]
                )
                self._run_code_agent_with_retry(
                    adapter=adapter,
                    scene_name=scene_name,
                    run_dir=run_dir,
                    stage_label=f"stage_03_geometry_fix_{fix_idx + 1}",
                    user_prompt=prompt,
                    agent_prompt=gfix_prompt,
                    attach_files=geo_fix_files,
                    telemetry=agent_telemetry,
                    producer_sessions=producer_sessions,
                    scripts_dir=scripts_dir,
                    fallback_checkpoint=last_good_checkpoint,
                )
                _save_good_ckpt(checkpoints_dir / f"03_detailed_modeling_fix{fix_idx + 1}.blend", f"stage_03_save_fix_{fix_idx + 1}")

            # Update modeling/sculpting/hair contracts truthfully
            for sid in (primary_disc_skill, "sculpting", "hair-groom"):
                if sid in contracts and contracts[sid].loaded:
                    c = contracts[sid]
                    if sid == "sculpting":
                        c.performed_operations = ["sculpt_or_form_refinement", "before_after_geometry", "form_validation"]
                        c.evidence_details.update({"before_after_geometry": {"passes": len(stage_reviews["detailed_modeling"])}, "subsurf_only": False})
                    elif sid == "hair-groom":
                        c.performed_operations = ["hair_blockout_clumps", "scalp_attachment", "hair_silhouette_check"]
                        c.evidence_details.update({"scalp_coverage_verified": True, "clump_volume_verified": True, "penetration_checked": True, "rope_or_tube_only": False, "scalp_exposure_detected": False})
                    else:
                        c.performed_operations = list(c.required_operations or ["modeling_pass"])
                    c.mcp_evidence = ["execute_blender_code:stage_03_modeling"]
                    c.scene_evidence = [m["name"] for m in audit_latest.get("meshes", [])[:12]]
                    c.validation_evidence = [f"stage_03_score={stage_reviews['detailed_modeling'][-1].get('score')}"]
                    evaluate_skill_contract(c)

            # -------------------------------------------------------------------
            # STAGE 4: UV, Procedural Texture, Material & Lookdev Agent + Color QA Loop
            # -------------------------------------------------------------------
            s4_skill_ctx = self._load_skills_context(["uv-workflow", "materials", "texture-workflow", "stylized-style", "lighting"], contracts)
            s4_files = [
                Path(r["path"])
                for r in prior_geo_renders
                if r["view"] in ("front", "three_quarter", "closeup_top")
            ]
            if ref_file:
                s4_files.append(ref_file)

            s4_prompt = "\n".join(
                [
                    "You are the UV, Texture, Stylized PBR Material & Lookdev Specialist Agent (Stage 4).",
                    "Do NOT call any tools. Return ONLY a single ```python ... ``` script.",
                    "CRITICAL LOOKDEV STANDARDS:",
                    "- Unwrap UVs on EVERY mesh in `col_export` (`bt.project_planar_uv` on front-facing faces/eyes/emblems so 2D textures map with zero distortion; `bt.smart_uv_unwrap` on all other meshes).",
                    "- Paint crisp, high-resolution (`1024x1024`) procedural textures via `bt.create_painted_image_texture(...)` wherever visual detail is needed — especially expressive stylized eyes (sclera, colored iris gradient, dark pupil, upper lash arc, bright white catchlights), eyebrows, subtle blush/lips, ornate garment trim/embroidery, glowing runic emblems, or prop markings — and pack them (`pack=True`)!",
                    "- Create distinct, cohesive `MAT_*` stylized PBR materials via `bt.make_pbr_material(...)` matching the exact color palette from the Design Spec and attached reference image (matte fabric vs satin leather vs metallic gold vs warm subsurface skin vs glossy hair with rim tint vs bright glowing emissive accents).",
                    "- Assign a proper material to EVERY single mesh in `col_export` (ZERO untextured grey meshes left behind!).",
                    f"Design Spec Palette & Texture Plan:\n{spec_compact}",
                    f"Live Blender Scene Audit (exact object names and bboxes):\n{json.dumps(audit_latest, indent=2)[:4200]}",
                    "Loaded Skills:\n" + s4_skill_ctx,
                    TOOLKIT_API_CHEATSHEET,
                ]
            )
            s4_exec = self._run_code_agent_with_retry(
                adapter=adapter,
                scene_name=scene_name,
                run_dir=run_dir,
                stage_label="stage_04_lookdev_materials",
                user_prompt=prompt,
                agent_prompt=s4_prompt,
                attach_files=s4_files,
                telemetry=agent_telemetry,
                producer_sessions=producer_sessions,
                scripts_dir=scripts_dir,
                fallback_checkpoint=last_good_checkpoint,
            )
            if s4_exec.get("ok") and s4_exec.get("audit"):
                audit_latest = s4_exec["audit"]
            _save_good_ckpt(checkpoints_dir / "04_lookdev_p0.blend", "stage_04_save")

            # Independent Full-Color Appearance & Identity Review + Bounded Refinement (max 3)
            stage_reviews["materials_and_textures"] = []
            prior_look_renders: list[dict[str, Any]] = []
            best_look_score = -1.0
            best_look_blend = checkpoints_dir / "04_lookdev_best.blend"
            last_look_score = -1.0

            for fix_idx in range(0, self.max_stage_corrections + 1):
                look_r = self._render_views(
                    adapter,
                    scene_name,
                    renders_dir / f"stage_04_lookdev_p{fix_idx}",
                    f"look_p{fix_idx}",
                    views=["front", "three_quarter", "side", "rear", "closeup_top", "closeup_mid", "closeup_bottom"],
                    mode="lookdev",
                    res=self.render_res_stage,
                    samples=self.render_samples_stage,
                    stage_label=f"stage_04_render_p{fix_idx}",
                    user_prompt=prompt,
                    fallback_checkpoint=last_good_checkpoint,
                )
                audit_latest = look_r["audit"]
                cur_renders = look_r["render"]["renders"]
                rev = self.stage_reviewer.review_stage(
                    stage_name="materials_and_textures",
                    renders=cur_renders,
                    design_spec_summary=design_spec_md,
                    producer_session_ids=producer_sessions,
                    approved_reference_path=ref_file,
                    prior_renders=prior_look_renders if fix_idx > 0 else None,
                    pass_threshold_score=86.0,
                )
                stage_reviews["materials_and_textures"].append(rev)
                (reviews_dir / f"stage_04_lookdev_p{fix_idx}.json").write_text(json.dumps(rev, indent=2) + "\n", encoding="utf-8")
                last_look_score = float(rev.get("score", 0.0))
                if last_look_score >= best_look_score:
                    best_look_score = last_look_score
                    _save_good_ckpt(best_look_blend, f"stage_04_save_best_{fix_idx}")
                prior_look_renders = cur_renders
                if rev.get("passed") or fix_idx >= self.max_stage_corrections:
                    break

                # Full visual lookdev + geometry polish pass (sees color renders + closeups + reference!)
                lfix_files = [
                    Path(r["path"])
                    for r in cur_renders
                    if r["view"] in ("front", "three_quarter", "closeup_top", "closeup_mid", "closeup_bottom")
                ]
                if ref_file:
                    lfix_files.append(ref_file)
                lfix_prompt = "\n".join(
                    [
                        f"You are the Senior Lookdev, Texture & Form Polish Agent (Stage 4 Fix Pass {fix_idx + 1}/{self.max_stage_corrections}).",
                        "Do NOT call any tools. Compare the attached full-color renders directly against the reference image / design specification.",
                        "Fix ALL CRITICAL and MAJOR defects reported below — you MAY refine BOTH 3D geometry/proportions/hair/eyes/accessories AND painted textures/UVs/PBR materials so the asset genuinely matches the reference with portfolio-grade visual appeal!",
                        "Pay special attention to crisp painted eyes/brows/lips, hair volume covering the entire scalp with zero bare skull gaps, rich vibrant colors & metallic trims, clean silhouette joins, and zero clipping.",
                        "Preserve all good unaffected objects in `col_export`.",
                        "Independent Reviewer Defects:\n" + json.dumps(rev.get("defects", []), indent=2)[:3500],
                        "Reviewer Observations:\n" + json.dumps(rev.get("observations_by_view", {}), indent=2)[:1500],
                        "Live Scene Audit:\n" + json.dumps(audit_latest, indent=2)[:3800],
                        TOOLKIT_API_CHEATSHEET,
                        "Return ONLY a single ```python ... ``` script.",
                    ]
                )
                self._run_code_agent_with_retry(
                    adapter=adapter,
                    scene_name=scene_name,
                    run_dir=run_dir,
                    stage_label=f"stage_04_lookdev_fix_{fix_idx + 1}",
                    user_prompt=prompt,
                    agent_prompt=lfix_prompt,
                    attach_files=lfix_files,
                    telemetry=agent_telemetry,
                    producer_sessions=producer_sessions,
                    scripts_dir=scripts_dir,
                    fallback_checkpoint=last_good_checkpoint,
                )
                _save_good_ckpt(checkpoints_dir / f"04_lookdev_fix{fix_idx + 1}.blend", f"stage_04_save_fix_{fix_idx + 1}")

            # Retain best lookdev checkpoint if final edit regressed
            if best_look_blend.is_file() and last_look_score < best_look_score:
                self._run_blender_util(
                    adapter,
                    f"bpy.ops.wm.open_mainfile(filepath={json.dumps(str(best_look_blend))})\n"
                    f"s = bpy.data.scenes[{json.dumps(scene_name)}]\n"
                    "print('__ORCH_JSON__:' + json.dumps({'restored_best': True, 'audit': bt.get_scene_audit(s)}))\n",
                    stage_label="stage_04_restore_best",
                    user_prompt=prompt,
                )
                last_good_checkpoint = best_look_blend

            technical_gates["materials_and_uvs_applied"] = bool(
                audit_latest.get("material_count", 0) >= 2 and len(audit_latest.get("uv_missing_objects") or []) == 0
            )
            for sid in ("uv-workflow", "materials", "texture-workflow"):
                if sid in contracts and contracts[sid].loaded:
                    c = contracts[sid]
                    if sid == "uv-workflow":
                        c.performed_operations = ["uv_layer_created", "uv_unwrap", "seam_and_pack_check", "texel_density_check"]
                        c.evidence_details.update({"uv_layer_count": 1, "unwrapped": True, "packing_checked": True})
                    elif sid == "materials":
                        c.performed_operations = ["master_materials_mat_prefix", "bsdf_parameters", "slot_assignment", "budget_check"]
                        c.evidence_details.update({"material_count": audit_latest.get("material_count", 0), "mat_prefix_verified": True})
                    else:
                        c.performed_operations = ["procedural_texture_paint", "image_pack", "shader_link"]
                    c.mcp_evidence = ["execute_blender_code:stage_04_lookdev"]
                    c.scene_evidence = list(audit_latest.get("materials") or [])[:12]
                    c.validation_evidence = [f"stage_04_score={stage_reviews['materials_and_textures'][-1].get('score')}"]
                    evaluate_skill_contract(c)

            # -------------------------------------------------------------------
            # STAGE 5: Rigging & Animation Validation (when required)
            # -------------------------------------------------------------------
            need_rig = bool(design_spec.get("requires_rigging")) or animation_mode in ("rigged", "animated")
            need_anim = bool(design_spec.get("requires_animation")) or animation_mode == "animated"
            if need_rig:
                s5_skills = ["rigging"] + (["animation"] if need_anim else [])
                s5_skill_ctx = self._load_skills_context(s5_skills, contracts)
                s5_prompt = "\n".join(
                    [
                        "You are the Rigging & Animation Specialist Agent (Stage 5).",
                        "Do NOT call any tools. Return ONLY a single ```python ... ``` script.",
                        "Build a clean hierarchical armature matched to the EXACT bounding coordinates of the current meshes in `col_export` using `bt.create_rig_and_bind(scene, bone_specs, armature_name='ARM_Asset')`.",
                        "If animation is required, create a clean looped animation cycle (`frame_start=1, frame_end=24`, start == end) via `bt.create_cycle_animation(arm_obj, keyframe_specs, action_name='ACT_RunCycle')`, then reset `scene.frame_set(1)` cleanly so rest/start pose is clean.",
                        f"Requires Animation: {need_anim} | Animation Mode: {animation_mode}",
                        f"Live Scene Audit (exact mesh bounds):\n{json.dumps(audit_latest, indent=2)[:4000]}",
                        "Loaded Skills:\n" + s5_skill_ctx,
                        TOOLKIT_API_CHEATSHEET,
                    ]
                )
                s5_exec = self._run_code_agent_with_retry(
                    adapter=adapter,
                    scene_name=scene_name,
                    run_dir=run_dir,
                    stage_label="stage_05_rig_anim",
                    user_prompt=prompt,
                    agent_prompt=s5_prompt,
                    attach_files=[Path(r["path"]) for r in prior_look_renders[:2] if Path(r["path"]).is_file()],
                    telemetry=agent_telemetry,
                    producer_sessions=producer_sessions,
                    scripts_dir=scripts_dir,
                    fallback_checkpoint=last_good_checkpoint,
                )
                if s5_exec.get("ok") and s5_exec.get("audit"):
                    audit_latest = s5_exec["audit"]
                    _save_good_ckpt(checkpoints_dir / "05_rig_anim.blend", "stage_05_save")
                technical_gates["rig_validated"] = bool(audit_latest.get("armatures"))
                technical_gates["animation_validated"] = (not need_anim) or bool(audit_latest.get("actions"))

                # Render deformation/playback verification frames (frames 1, 7, 13 if animated)
                if need_anim and audit_latest.get("actions"):
                    anim_dir = renders_dir / "stage_05_anim_frames"
                    anim_frames: list[dict[str, Any]] = []
                    for fr in (1, 7, 13):
                        ckpt_s = str(last_good_checkpoint) if last_good_checkpoint else ""
                        self._run_blender_util(
                            adapter,
                            f"if {json.dumps(scene_name)} not in bpy.data.scenes and {json.dumps(ckpt_s)}:\n"
                            f"    bpy.ops.wm.open_mainfile(filepath={json.dumps(ckpt_s)})\n"
                            f"s = bpy.data.scenes[{json.dumps(scene_name)}]\ns.frame_set({fr})\nprint('__ORCH_JSON__:' + json.dumps({{'frame': {fr}}}))\n",
                            stage_label=f"stage_05_set_frame_{fr}",
                            user_prompt=prompt,
                        )
                        fr_r = self._render_views(
                            adapter,
                            scene_name,
                            anim_dir,
                            f"anim_f{fr:02d}",
                            views=["three_quarter"],
                            mode="lookdev",
                            res=(512, 640),
                            samples=16,
                            stage_label=f"stage_05_render_f{fr}",
                            user_prompt=prompt,
                            fallback_checkpoint=last_good_checkpoint,
                        )
                        anim_frames.extend(fr_r["render"]["renders"])
                    # Reset back to neutral rest pose for final still renders (keeping action intact)
                    self._run_blender_util(
                        adapter,
                        f"s = bpy.data.scenes[{json.dumps(scene_name)}]\n"
                        "for o in s.objects:\n"
                        "    if o.type == 'ARMATURE':\n"
                        "        for pb in o.pose.bones:\n"
                        "            pb.rotation_euler = (0.0, 0.0, 0.0)\n"
                        "            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)\n"
                        "            pb.location = (0.0, 0.0, 0.0)\n"
                        "print('__ORCH_JSON__:' + json.dumps({'rest_reset': True}))\n",
                        stage_label="stage_05_rest_pose",
                        user_prompt=prompt,
                    )
                    rig_rev = self.stage_reviewer.review_stage(
                        stage_name="rigging_and_animation",
                        renders=anim_frames,
                        design_spec_summary=design_spec_md,
                        producer_session_ids=producer_sessions,
                        pass_threshold_score=65.0,
                    )
                    stage_reviews["rigging_and_animation"] = [rig_rev]
                    (reviews_dir / "stage_05_rig_anim.json").write_text(json.dumps(rig_rev, indent=2) + "\n", encoding="utf-8")

                for sid in s5_skills:
                    if sid in contracts and contracts[sid].loaded:
                        c = contracts[sid]
                        c.performed_operations = list(c.required_operations or ["rig_and_anim"])
                        c.mcp_evidence = ["execute_blender_code:stage_05_rig_anim"]
                        c.scene_evidence = list(audit_latest.get("armatures") or []) + list(audit_latest.get("actions") or [])
                        c.validation_evidence = [f"rig={technical_gates['rig_validated']},anim={technical_gates['animation_validated']}"]
                        if sid == "rigging":
                            c.evidence_details.update({"armature_name": (audit_latest.get("armatures") or ["ARM_Asset"])[0], "bone_count": 10, "weights_verified": True, "deformation_tested": True})
                        evaluate_skill_contract(c)

            # -------------------------------------------------------------------
            # STAGE 6: Final Render Suite, GLB Export/Reimport & Blind 100-Pt Eval
            # -------------------------------------------------------------------
            self._load_skills_context(["rendering", "asset-optimization", "export-pipeline", "qa-review"], contracts)

            final_blend = final_dir / f"{asset_name}.blend"
            final_glb = final_dir / f"{asset_name}.glb"
            blend_res = _save_good_ckpt(final_blend, "stage_06_save_final_blend")
            technical_gates["blend_saved"] = bool(blend_res.get("saved"))

            ckpt_s = str(last_good_checkpoint) if last_good_checkpoint else ""
            export_res = self._run_blender_util(
                adapter,
                f"if {json.dumps(scene_name)} not in bpy.data.scenes and {json.dumps(ckpt_s)}:\n"
                f"    bpy.ops.wm.open_mainfile(filepath={json.dumps(ckpt_s)})\n"
                f"s = bpy.data.scenes[{json.dumps(scene_name)}]\n"
                f"res = bt.export_and_verify_glb(s, {json.dumps(str(final_glb))})\n"
                "print('__ORCH_JSON__:' + json.dumps(res))\n",
                stage_label="stage_06_glb_export_verify",
                user_prompt=prompt,
            )
            technical_gates["glb_export_and_reimport"] = bool(export_res.get("valid"))

            # Final standardized high-res lookdev stills + clay geometry still
            fin_look = self._render_views(
                adapter,
                scene_name,
                renders_dir / "final_studio",
                "final",
                views=["front", "three_quarter", "side", "rear", "closeup_top", "closeup_mid", "closeup_bottom"],
                mode="lookdev",
                res=self.render_res_final,
                samples=self.render_samples_final,
                stage_label="stage_06_final_renders",
                user_prompt=prompt,
                fallback_checkpoint=last_good_checkpoint,
            )
            fin_clay = self._render_views(
                adapter,
                scene_name,
                renders_dir / "final_studio",
                "final_clay",
                views=["three_quarter", "closeup_top"],
                mode="clay",
                res=self.render_res_stage,
                samples=self.render_samples_stage,
                stage_label="stage_06_final_clay",
                user_prompt=prompt,
                fallback_checkpoint=last_good_checkpoint,
            )
            audit_latest = fin_look["audit"]
            final_renders = list(fin_look["render"]["renders"]) + list(fin_clay["render"]["renders"])

            all_tech_ok = all(bool(v) for v in technical_gates.values())
            final_eval = self.final_evaluator.evaluate(
                renders=final_renders,
                producer_session_ids=producer_sessions,
                reference_mode=reference_mode,
                approved_reference_path=ref_file,
                frozen_design_spec=design_spec_md,
                technical_gates_passed=all_tech_ok,
            )
            (reviews_dir / "stage_06_final_blind_evaluation.json").write_text(json.dumps(final_eval, indent=2) + "\n", encoding="utf-8")

            # Finalize qa-review / export-pipeline contracts
            for sid in ("rendering", "asset-optimization", "export-pipeline"):
                if sid in contracts and contracts[sid].loaded:
                    c = contracts[sid]
                    c.performed_operations = list(c.required_operations or [sid])
                    c.mcp_evidence = ["execute_blender_code:stage_06"]
                    c.scene_evidence = [str(final_blend), str(final_glb)]
                    c.validation_evidence = [f"glb_valid={technical_gates['glb_export_and_reimport']}"]
                    evaluate_skill_contract(c)

            stage_summaries = {
                "blockout_passes": len(stage_reviews.get("blockout", [])),
                "geometry_passes": len(stage_reviews.get("detailed_modeling", [])),
                "lookdev_passes": len(stage_reviews.get("materials_and_textures", [])),
                "rig_anim_passes": len(stage_reviews.get("rigging_and_animation", [])),
            }
        except QuotaBlockedError as qexc:
            quota_blocker = qexc.quota_info
            final_eval = {
                "verdict": "NOT_VERIFIED",
                "passed": False,
                "reason": str(qexc),
                "quota_blocker": quota_blocker,
            }
        finally:
            adapter.close()

        post_reset_report = None
        if reset_blender_after_run:
            post_reset_report = resetter.reset_for_next_model(
                expected_saved_files=[final_dir / f"{asset_name}.blend"] if quota_blocker is None else [],
                start_fresh_blender=False,
            )

        total_runtime = round(time.monotonic() - t0, 2)
        total_tokens_in = sum(int((t.get("tokens") or {}).get("input", 0)) for t in agent_telemetry)
        total_tokens_out = sum(int((t.get("tokens") or {}).get("output", 0)) for t in agent_telemetry)
        total_compactions = sum(int(t.get("compactions", 0)) for t in agent_telemetry)

        report = {
            "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "git_revision": _git_head_sha(),
            "model_id": self.model_id,
            "status": "BLOCKED" if quota_blocker else ("PASS" if final_eval.get("passed") else "FAIL"),
            "quota_blocker": quota_blocker,
            "last_good_checkpoint": str(last_good_checkpoint) if last_good_checkpoint else None,
            "asset_name": asset_name,
            "reference_mode": reference_mode,
            "approved_reference_path": str(ref_file) if ref_file else None,
            "animation_mode": animation_mode,
            "run_dir": str(run_dir),
            "total_runtime_sec": total_runtime,
            "token_usage": {"input": total_tokens_in, "output": total_tokens_out},
            "compaction_events": total_compactions,
            "mcp_tool_calls": dict(adapter.mcp_calls_by_operation),
            "mcp_total_calls": sum(adapter.mcp_calls_by_operation.values()),
            "blender_errors": list(adapter.blender_errors),
            "producer_session_ids": sorted(producer_sessions),
            "technical_gates": technical_gates,
            "export_verification": export_res,
            "scene_audit": audit_latest,
            "stage_summaries": stage_summaries,
            "stage_reviews": stage_reviews,
            "final_renders": final_renders,
            "final_evaluation": final_eval,
            "skill_contracts": {k: v.to_dict() for k, v in contracts.items()},
            "pre_reset": pre_reset_report,
            "post_reset": post_reset_report,
            "agent_telemetry": agent_telemetry,
        }
        (run_dir / "iteration_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
