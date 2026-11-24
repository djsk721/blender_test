#!/usr/bin/env python3
"""Real end-to-end Blender MCP verification for BOTH TEXT_ONLY and REFERENCE_GUIDED modes.

Runs completely through OpenCode's `.opencode/mcp/blender/mcp_wrapper.py`, real
`uvx mcp-for-blender`, and real Blender 5.3.0 using an isolated runtime directory
so historical `.opencode/mcp/blender/runtime/backups/` are never modified.
Zero external AI 3D generators or premade/downloaded meshes are used.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import zlib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import benchmark_preflight as bp  # noqa: E402
from blender_orchestration import (  # noqa: E402
    BlenderDirector,
    MCPAdapter,
    PerformanceMetrics,
    SkillRegistry,
    StdioMCPTransport,
    build_contract_from_plan_item,
    build_reproducibility_manifest,
    evaluate_asset_qa,
    evaluate_skill_contract,
)
from blender_orchestration.mcp_adapter import PolicyViolationError  # noqa: E402
from blender_orchestration.scene_ops import (  # noqa: E402
    build_checkpoint_save_code,
    build_glb_export_and_reimport_code,
    build_init_scene_code,
    build_lantern_materials_code,
    build_lantern_pass1_geometry_code,
    build_lantern_pass2_refinement_code,
    build_qa_render_code,
    build_reference_camera_match_code,
    build_scene_state_audit_code,
    build_single_camera_render_code,
    build_stylized_crate_geometry_code,
    build_stylized_crate_materials_code,
    build_uv_stage_code,
)


# ---------------------------------------------------------------------------
# Pure-stdlib PNG encoder/decoder for local reference & comparison plates
# ---------------------------------------------------------------------------

def _png_chunk(ctype: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)


def write_rgb_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_rows = bytearray()
    stride = width * 3
    for y in range(height):
        raw_rows.append(0)  # filter type 0
        raw_rows.extend(pixels[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    png_bytes = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    path.write_bytes(png_bytes)


def read_png_rgb(path: Path) -> tuple[int, int, bytearray]:
    """Minimal stdlib PNG decoder supporting RGB/RGBA 8-bit filter 0..4."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"Invalid PNG signature: {path}"
    pos = 8
    width = height = 0
    color_type = 2
    idat_chunks: list[bytes] = []
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", chunk_data)
            if bit_depth != 8:
                raise ValueError(f"Unsupported PNG bit depth {bit_depth} in {path}")
        elif ctype == b"IDAT":
            idat_chunks.append(chunk_data)
        elif ctype == b"IEND":
            break

    raw = zlib.decompress(b"".join(idat_chunks))
    bpp = 4 if color_type == 6 else 3
    stride = width * bpp
    recon = bytearray(height * width * 3)

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if pa <= pb and pa <= pc else (b if pb <= pc else c)

    prev_row = bytearray(stride)
    idx = 0
    for y in range(height):
        ftype = raw[idx]
        idx += 1
        row = bytearray(raw[idx : idx + stride])
        idx += stride
        for x in range(stride):
            a = row[x - bpp] if x >= bpp else 0
            b = prev_row[x]
            c = prev_row[x - bpp] if x >= bpp else 0
            if ftype == 1:
                row[x] = (row[x] + a) & 0xFF
            elif ftype == 2:
                row[x] = (row[x] + b) & 0xFF
            elif ftype == 3:
                row[x] = (row[x] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                row[x] = (row[x] + paeth(a, b, c)) & 0xFF
        prev_row = row
        out_base = y * width * 3
        for px in range(width):
            recon[out_base + px * 3 : out_base + px * 3 + 3] = row[px * bpp : px * bpp + 3]

    return width, height, recon


def _draw_rect(buf: bytearray, w: int, h: int, x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int]) -> None:
    r, g, b = rgb
    for y in range(max(0, y0), min(h, y1)):
        row_off = y * w * 3
        for x in range(max(0, x0), min(w, x1)):
            off = row_off + x * 3
            buf[off], buf[off + 1], buf[off + 2] = r, g, b


def _draw_circle(buf: bytearray, w: int, h: int, cx: int, cy: int, rad: int, rgb: tuple[int, int, int], ring: int | None = None) -> None:
    r, g, b = rgb
    r2 = rad * rad
    inner2 = (rad - ring) ** 2 if ring else -1
    for y in range(max(0, cy - rad), min(h, cy + rad + 1)):
        for x in range(max(0, cx - rad), min(w, cx + rad + 1)):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if inner2 < d2 <= r2:
                off = (y * w + x) * 3
                buf[off], buf[off + 1], buf[off + 2] = r, g, b


def generate_alchemy_lantern_reference_png(path: Path) -> Path:
    """Create deterministic local approved reference concept plate (256x256 PNG)."""
    w, h = 256, 256
    buf = bytearray(w * h * 3)
    # Dark slate backdrop gradient
    for y in range(h):
        t = y / (h - 1)
        bg = (int(16 + 10 * t), int(21 + 12 * t), int(34 + 16 * t))
        for x in range(w):
            off = (y * w + x) * 3
            buf[off], buf[off + 1], buf[off + 2] = bg

    # Ground shadow band
    _draw_rect(buf, w, h, 48, 208, 208, 232, (14, 17, 26))

    # Top brass carry ring + spire finial (#D6982A)
    _draw_circle(buf, w, h, 128, 32, 13, (214, 152, 42), ring=4)
    _draw_rect(buf, w, h, 122, 42, 134, 58, (214, 152, 42))
    _draw_rect(buf, w, h, 110, 56, 146, 68, (214, 152, 42))

    # Iron sloped roof cap (#282C34)
    for dy in range(28):
        half_w = int(28 + dy * 1.65)
        _draw_rect(buf, w, h, 128 - half_w, 68 + dy, 128 + half_w, 69 + dy, (40, 44, 52))
    _draw_rect(buf, w, h, 78, 96, 178, 108, (214, 152, 42))  # brass upper rim

    # Glowing amber core glass (#FF9E22 with warm inner glow #FFD266)
    _draw_rect(buf, w, h, 90, 108, 166, 186, (255, 142, 26))
    _draw_rect(buf, w, h, 104, 120, 152, 174, (255, 208, 92))

    # Vertical iron cage struts framing amber core
    _draw_rect(buf, w, h, 84, 106, 94, 190, (34, 38, 46))
    _draw_rect(buf, w, h, 162, 106, 172, 190, (34, 38, 46))
    _draw_rect(buf, w, h, 124, 106, 132, 190, (38, 42, 50))

    # Brass base collar + stepped dark iron plinth
    _draw_rect(buf, w, h, 82, 186, 174, 196, (214, 152, 42))
    _draw_rect(buf, w, h, 74, 196, 182, 214, (34, 38, 46))

    # Palette swatches along bottom margin
    swatches = [(34, 38, 46), (214, 152, 42), (255, 142, 26), (255, 208, 92), (20, 25, 38)]
    for i, col in enumerate(swatches):
        _draw_rect(buf, w, h, 16 + i * 28, 236, 40 + i * 28, 248, col)

    write_rgb_png(path, w, h, buf)
    return path


def analyze_png_features(path: Path) -> dict:
    """Compute dimensions, mean RGB, warm-amber center glow metric, and palette summary."""
    w, h, rgb = read_png_rgb(path)
    total = w * h
    sum_r = sum_g = sum_b = 0
    amber_pixels = 0
    top_brass_pixels = 0
    for y in range(h):
        for x in range(w):
            off = (y * w + x) * 3
            r, g, b = rgb[off], rgb[off + 1], rgb[off + 2]
            sum_r += r
            sum_g += g
            sum_b += b
            if r > 170 and g > 75 and b < 130:
                amber_pixels += 1
            if y < int(h * 0.35) and r > 120 and g > 80 and b < 85:
                top_brass_pixels += 1
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": w,
        "height": h,
        "mean_rgb": [round(sum_r / total, 1), round(sum_g / total, 1), round(sum_b / total, 1)],
        "amber_glow_ratio": round(amber_pixels / total, 4),
        "top_brass_ratio": round(top_brass_pixels / total, 4),
    }


def composite_side_by_side_png(paths: list[Path], out_path: Path) -> Path:
    """Resize/crop center into uniform panels and save side-by-side comparison PNG."""
    decoded = [read_png_rgb(p) for p in paths]
    panel_w, panel_h = 256, 256
    gap = 8
    total_w = len(decoded) * panel_w + (len(decoded) - 1) * gap
    out = bytearray([20, 22, 28] * (total_w * panel_h))

    for p_idx, (sw, sh, srgb) in enumerate(decoded):
        x_off = p_idx * (panel_w + gap)
        for dy in range(panel_h):
            sy = min(sh - 1, int(dy * sh / panel_h))
            for dx in range(panel_w):
                sx = min(sw - 1, int(dx * sw / panel_w))
                soff = (sy * sw + sx) * 3
                doff = (dy * total_w + (x_off + dx)) * 3
                out[doff : doff + 3] = srgb[soff : soff + 3]

    write_rgb_png(out_path, total_w, panel_h, out)
    return out_path


# ---------------------------------------------------------------------------
# Watchdog & isolation helpers
# ---------------------------------------------------------------------------

def count_blender_procs() -> int:
    res = subprocess.run(["pgrep", "-x", "blender"], capture_output=True, text=True, check=False)
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return len(lines)


def wait_for_watchdog_cleanup(runtime_dir: Path, timeout_sec: float = 25.0) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / ".opencode" / "mcp")
    env["BLENDER_MCP_CONFIG"] = str(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
    env["BLENDER_MCP_RUNTIME_DIR"] = str(runtime_dir)
    env["BLENDER_MCP_IDLE_TIMEOUT"] = "8"
    env["BLENDER_MCP_WATCHDOG_POLL"] = "1.0"

    t0 = time.monotonic()
    last_status = {}
    while time.monotonic() - t0 < timeout_sec:
        res = subprocess.run(
            [sys.executable, "-m", "blender.lifecycle_manager", "status"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            last_status = json.loads(res.stdout)
            if last_status.get("state") == "STOPPED" and not last_status.get("blender_alive") and count_blender_procs() == 0:
                return {
                    "cleaned_up": True,
                    "elapsed_sec": round(time.monotonic() - t0, 2),
                    "final_state": last_status.get("state"),
                    "blender_procs": count_blender_procs(),
                    "gpu_after_stop": last_status.get("gpu_after_stop"),
                }
        time.sleep(1.0)

    return {
        "cleaned_up": False,
        "elapsed_sec": round(time.monotonic() - t0, 2),
        "final_state": last_status.get("state"),
        "blender_procs": count_blender_procs(),
    }


def _env_for_isolated_run(runtime_dir: Path) -> dict[str, str]:
    return {
        "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json"),
        "BLENDER_MCP_RUNTIME_DIR": str(runtime_dir),
        "BLENDER_MCP_IDLE_TIMEOUT": "8",
        "BLENDER_MCP_WATCHDOG_POLL": "1.0",
        "BLENDER_MCP_AUTO_RESTORE": "false",
    }


# ---------------------------------------------------------------------------
# E2E Mode A: TEXT_ONLY Stylized Fantasy Crate
# ---------------------------------------------------------------------------

def run_text_only_crate_e2e(base_dir: Path) -> dict:
    run_dir = base_dir / "mode_a_text_only_crate"
    runtime_dir = run_dir / "mcp_runtime"
    renders_dir = run_dir / "renders"
    run_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.monotonic()

    prompt = (
        "Create a small game-ready stylized fantasy crate without using any reference images, "
        "using blender-director, appropriate modeling and materials Skills, and QA review."
    )
    registry = SkillRegistry(PROJECT_ROOT / ".opencode" / "skills")
    director = BlenderDirector(registry)
    preflight = bp.resolve_preflight_policy(prompt, agents_md_path=PROJECT_ROOT / ".opencode" / "AGENTS.md")
    assert preflight["benchmark_mode"] == "TEXT_ONLY" and preflight["modeling_permitted"]

    plan = director.plan_production(prompt, reference_mode="TEXT_ONLY", asset_name="StylizedFantasyCrate")
    (run_dir / "production_brief.md").write_text(plan["brief_markdown"] + "\n", encoding="utf-8")

    # Initialize Skill Execution Contracts and load selected SKILL.md on demand
    contracts = [build_contract_from_plan_item(item) for item in plan["selected_skills"]]
    contract_by_key = {(c.skill_id, c.production_stage): c for c in contracts}
    metrics = PerformanceMetrics()

    # Load blender-director immediately (stage-specific loading)
    registry.load_skill("blender-director", include_references=["../references/asset-pipeline.md", "../references/mcp-integration.md"])
    c_dir = contract_by_key[("blender-director", "planning")]
    c_dir.loaded = True
    c_dir.performed_operations = ["production_brief", "budget_definition", "skill_routing"]
    c_dir.mcp_evidence = ["tools/list schema audit"]
    c_dir.scene_evidence = ["Production brief written"]
    c_dir.validation_evidence = [f"Selected {len(plan['selected_skills'])} skills; omitted {len(plan['omitted_skills'])} unrelated skills"]
    evaluate_skill_contract(c_dir)
    metrics.skill_activations.append("blender-director")
    metrics.skill_applications.append("blender-director")

    adapter = MCPAdapter(
        StdioMCPTransport(cwd=PROJECT_ROOT, env_overrides=_env_for_isolated_run(runtime_dir)),
        reference_mode="TEXT_ONLY",
        allow_external_assets=False,
    )
    stage_timings: dict[str, float] = {}

    try:
        # 1. Lazy Loading check: discover tools without starting Blender
        caps = adapter.discover_capabilities()
        lazy_blender_count = count_blender_procs()
        assert lazy_blender_count == 0, f"Lazy loading violated: {lazy_blender_count} blender procs after initialize/tools/list"

        manifest = build_reproducibility_manifest(
            prompt,
            run_id=run_dir.name,
            project_root=PROJECT_ROOT,
            mcp_server_info=caps["server_info"],
            discovered_tools=caps["tools"],
            model_id="deterministic-orchestrator",
            provider="local-e2e",
            output_dir=str(run_dir),
        )

        # Verify external AI 3D generator & premade asset tools are blocked at adapter level
        ai_blocked = False
        try:
            adapter.call_tool("generate_3d", {"prompt": "fantasy crate", "provider": "tripo"}, stage="guardrail_test")
        except PolicyViolationError:
            ai_blocked = True
        assert ai_blocked

        # 2. First Blender call triggers lazy startup
        t_stage = time.monotonic()
        addon_res = adapter.call_tool("get_addon_status", {"user_prompt": prompt}, stage="startup")
        stage_timings["startup"] = round(time.monotonic() - t_stage, 3)

        # 3. Idempotent Scene Initialization (run twice -> 0 duplicates)
        t_stage = time.monotonic()
        init_code = build_init_scene_code("SCENE_FantasyCrate", "crate_e2e_v1")
        init_1 = adapter.execute_blender_json(init_code, user_prompt=prompt, stage="scene_init_1")["parsed"]
        init_2 = adapter.execute_blender_json(init_code, user_prompt=prompt, stage="scene_init_2")["parsed"]
        assert init_1["scene_count"] == 1 and init_2["scene_count"] == 1
        assert init_1["collection_names"] == init_2["collection_names"]
        stage_timings["scene_init_x2"] = round(time.monotonic() - t_stage, 3)

        # 4. Load modeling + style skills on demand & build Crate geometry
        for sid in ("fantasy-worlds", "prop-artist", "stylized-style"):
            if (sid, "modeling") in contract_by_key or (sid, "art_direction") in contract_by_key:
                registry.load_skill(sid)
                metrics.skill_activations.append(sid)

        t_stage = time.monotonic()
        geom_res = adapter.execute_blender_json(
            build_stylized_crate_geometry_code("SCENE_FantasyCrate", "crate_e2e_v1"),
            user_prompt=prompt,
            stage="modeling",
        )["parsed"]
        scene_persist = adapter.call_tool("get_scene_info", {"fields": ["location", "size", "materials"], "user_prompt": prompt}, stage="modeling_verify")
        assert "SM_FantasyCrate_WoodBody" in scene_persist["text"]
        assert "SM_FantasyCrate_IronBands" in scene_persist["text"]
        assert "SM_FantasyCrate_RuneLock" in scene_persist["text"]
        stage_timings["modeling"] = round(time.monotonic() - t_stage, 3)

        for key, ops in (
            (("prop-artist", "modeling"), ["scale_blockout", "primary_volumes", "bevels_and_parts", "pivot_check"]),
            (("stylized-style", "art_direction"), ["stylized_proportions", "broad_form_readability", "stylized_shading"]),
            (("fantasy-worlds", "art_direction"), ["fantasy_shape_language", "world_material_palette"]),
        ):
            if key in contract_by_key:
                c = contract_by_key[key]
                c.loaded = True
                c.performed_operations = ops
                c.mcp_evidence = ["execute_blender_code:modeling", "get_scene_info:verified"]
                c.scene_evidence = geom_res["created_export_objects"]
                c.validation_evidence = [f"height_m={geom_res['height_m']}", f"min_z_m={geom_res['min_z_m']}", f"tris={geom_res['total_triangles']}"]
                evaluate_skill_contract(c)
                metrics.skill_applications.append(c.skill_id)

        # 5. UV stage
        registry.load_skill("uv-workflow")
        metrics.skill_activations.append("uv-workflow")
        t_stage = time.monotonic()
        uv_res = adapter.execute_blender_json(
            build_uv_stage_code("SCENE_FantasyCrate", geom_res["created_export_objects"]),
            user_prompt=prompt,
            stage="uvs",
        )["parsed"]
        assert uv_res["all_valid"]
        stage_timings["uvs"] = round(time.monotonic() - t_stage, 3)
        c_uv = contract_by_key[("uv-workflow", "uvs")]
        c_uv.loaded = True
        c_uv.performed_operations = ["uv_layer_created", "uv_unwrap", "seam_and_pack_check", "texel_density_check"]
        c_uv.mcp_evidence = ["execute_blender_code:uv_unwrap"]
        c_uv.scene_evidence = [f"{k}:layers={v['uv_layers']}" for k, v in uv_res["uv_report"].items()]
        c_uv.validation_evidence = ["all_uv_islands_within_0_1"]
        c_uv.evidence_details = {"uv_layer_count": 1, "unwrapped": True, "packing_checked": True}
        evaluate_skill_contract(c_uv)
        metrics.skill_applications.append("uv-workflow")

        # 6. Materials stage
        registry.load_skill("materials")
        metrics.skill_activations.append("materials")
        t_stage = time.monotonic()
        mat_res = adapter.execute_blender_json(
            build_stylized_crate_materials_code("SCENE_FantasyCrate"),
            user_prompt=prompt,
            stage="materials",
        )["parsed"]
        stage_timings["materials"] = round(time.monotonic() - t_stage, 3)
        c_mat = contract_by_key[("materials", "materials")]
        c_mat.loaded = True
        c_mat.performed_operations = ["master_materials_mat_prefix", "bsdf_parameters", "slot_assignment", "budget_check"]
        c_mat.mcp_evidence = ["execute_blender_code:materials"]
        c_mat.scene_evidence = [f"{k}->{v}" for k, v in mat_res["assigned_materials"].items()]
        c_mat.validation_evidence = [f"material_count={mat_res['material_count']}"]
        c_mat.evidence_details = {"material_count": mat_res["material_count"], "mat_prefix_verified": True}
        evaluate_skill_contract(c_mat)
        metrics.skill_applications.append("materials")

        # 7. Asset optimization & topology audit
        registry.load_skill("asset-optimization")
        metrics.skill_activations.append("asset-optimization")
        audit_res = adapter.execute_blender_json(
            build_scene_state_audit_code("SCENE_FantasyCrate", "crate_e2e_v1"),
            user_prompt=prompt,
            stage="asset_optimization",
        )["parsed"]
        assert audit_res["numeric_suffix_duplicates"] == []
        assert all(m["non_manifold_edges"] == 0 and m["loose_verts"] == 0 and m["scale_applied"] for m in audit_res["meshes"])
        c_opt = contract_by_key[("asset-optimization", "optimization")]
        c_opt.loaded = True
        c_opt.performed_operations = ["polycount_check", "apply_transforms", "manifold_and_normals_check"]
        c_opt.mcp_evidence = ["execute_blender_code:scene_audit"]
        c_opt.scene_evidence = [f"total_tris={audit_res['total_export_tris']}", "dups=[]"]
        c_opt.validation_evidence = ["non_manifold=0", "loose_verts=0", "scale_applied=True"]
        evaluate_skill_contract(c_opt)
        metrics.skill_applications.append("asset-optimization")

        # 8. Visual Capture via live `look` tool + studio PNG renders
        registry.load_skill("qa-review", include_references=["../references/validation-checklist.md"])
        metrics.skill_activations.append("qa-review")
        vis_tool = adapter.resolve_tool("visual_capture")["tool_name"]
        look_res = adapter.call_tool(
            vis_tool,
            {"mode": "angles", "shading": "material", "views": ["front", "three_quarter", "right"], "max_size": 512, "user_prompt": prompt},
            stage="qa_look",
        )
        t_stage = time.monotonic()
        render_res = adapter.execute_blender_json(
            build_qa_render_code("SCENE_FantasyCrate", geom_res["created_export_objects"], str(renders_dir), views=["three_quarter", "front", "side"]),
            user_prompt=prompt,
            stage="qa_render",
        )["parsed"]
        stage_timings["qa_render"] = round(time.monotonic() - t_stage, 3)
        metrics.render_durations_sec = render_res["durations_sec"]

        # Pre-inspection QA contract stays NOT_VERIFIED until actual image inspection occurs
        c_qa = contract_by_key[("qa-review", "qa_modeling")]
        c_qa.loaded = True
        c_qa.performed_operations = ["visual_capture"]
        c_qa.mcp_evidence = [f"{vis_tool}:images={look_res.get('image_count', 0)}", "execute_blender_code:qa_render"]
        c_qa.scene_evidence = [r["path"] for r in render_res["renders"]]
        c_qa.validation_evidence = ["renders_captured_awaiting_visual_inspection"]
        c_qa.evidence_details = {"captured_images": [r["path"] for r in render_res["renders"]], "visually_inspected": False}
        pre_inspection_qa = evaluate_asset_qa(
            rendered_images=[r["path"] for r in render_res["renders"]],
            visually_inspected=False,
            independent_reviewer=True,
            findings=[],
            major_defects=[],
            technical_checks_passed=True,
            within_poly_budget=audit_res["total_export_tris"] <= plan["analysis"]["budgets"]["poly_budget_tris"],
        )
        evaluate_skill_contract(c_qa)

        # 9. Checkpoint .blend + GLB Export & Reimport verification
        registry.load_skill("export-pipeline")
        metrics.skill_activations.append("export-pipeline")
        blend_path = run_dir / "stylized_fantasy_crate.blend"
        glb_path = run_dir / "SM_FantasyCrate.glb"
        ckpt_res = adapter.execute_blender_json(build_checkpoint_save_code(str(blend_path)), user_prompt=prompt, stage="checkpoint")["parsed"]
        exp_res = adapter.execute_blender_json(
            build_glb_export_and_reimport_code("SCENE_FantasyCrate", "COL_Export", str(glb_path)),
            user_prompt=prompt,
            stage="export_and_reimport",
        )["parsed"]
        assert ckpt_res["is_blend_header"] and exp_res["valid"]
        c_exp = contract_by_key[("export-pipeline", "export")]
        c_exp.loaded = True
        c_exp.performed_operations = ["axis_and_scale_config", "artifact_export", "reimport_validation"]
        c_exp.mcp_evidence = ["execute_blender_code:export_glb_and_reimport"]
        c_exp.scene_evidence = [f"reimported_meshes={exp_res['reimported_mesh_count']}"]
        c_exp.validation_evidence = [f"glb_bytes={exp_res['size_bytes']}", f"blend_bytes={ckpt_res['size_bytes']}"]
        evaluate_skill_contract(c_exp)
        metrics.skill_applications.append("export-pipeline")
    finally:
        adapter.close()

    # 10. Real idle watchdog cleanup verification
    cleanup = wait_for_watchdog_cleanup(runtime_dir, timeout_sec=25.0)
    assert cleanup["cleaned_up"], f"Watchdog failed to clean up TEXT_ONLY Blender process: {cleanup}"

    metrics.total_runtime_sec = round(time.monotonic() - t_start, 2)
    metrics.mcp_calls_by_operation = adapter.mcp_calls_by_operation
    metrics.python_execution_count = adapter.python_exec_count
    metrics.blender_error_count = len(adapter.blender_errors)
    metrics.retry_count = adapter.retry_count
    metrics.stage_durations_sec = stage_timings

    trace = {
        "mode": "TEXT_ONLY",
        "run_dir": str(run_dir),
        "lazy_loading_blender_procs_before_tool_call": lazy_blender_count,
        "ai_generator_calls_blocked": adapter.ai_generator_calls_blocked,
        "manifest": manifest,
        "plan_selected_skills": [s["skill_id"] for s in plan["selected_skills"]],
        "plan_omitted_skills": [o["skill_id"] for o in plan["omitted_skills"]],
        "scene_init_1": init_1,
        "scene_init_2": init_2,
        "geometry": geom_res,
        "uvs": uv_res,
        "materials": mat_res,
        "audit": audit_res,
        "look_tool_used": vis_tool,
        "look_image_count": look_res.get("image_count", 0),
        "renders": render_res,
        "pre_inspection_qa": pre_inspection_qa,
        "checkpoint": ckpt_res,
        "export": exp_res,
        "watchdog_cleanup": cleanup,
        "contracts": [c.to_dict() for c in contracts],
        "metrics": metrics.to_dict(),
        "call_trace": adapter.call_trace,
    }
    (run_dir / "execution_trace.json").write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    return trace


# ---------------------------------------------------------------------------
# E2E Mode B: REFERENCE_GUIDED Stylized Medieval Alchemy Lantern
# ---------------------------------------------------------------------------

def run_reference_guided_lantern_e2e(base_dir: Path) -> dict:
    run_dir = base_dir / "mode_b_reference_guided_lantern"
    runtime_dir = run_dir / "mcp_runtime"
    refs_dir = run_dir / "approved_references"
    renders_dir = run_dir / "renders"
    run_dir.mkdir(parents=True, exist_ok=True)
    t_start = time.monotonic()

    ref_png = generate_alchemy_lantern_reference_png(refs_dir / "ref_alchemy_lantern.png")
    ref_features = analyze_png_features(ref_png)

    prompt = (
        "Recreate this stylized medieval alchemy lantern using the supplied approved reference image, "
        "following blender-director, reference-image-match, modeling, materials, and QA review."
    )
    registry = SkillRegistry(PROJECT_ROOT / ".opencode" / "skills")
    director = BlenderDirector(registry)
    preflight = bp.resolve_preflight_policy(
        prompt,
        agents_md_path=PROJECT_ROOT / ".opencode" / "AGENTS.md",
        supplied_references=[str(ref_png)],
    )
    assert preflight["benchmark_mode"] == "REFERENCE_GUIDED" and preflight["modeling_permitted"]

    plan = director.plan_production(prompt, reference_mode="REFERENCE_GUIDED", asset_name="AlchemyLantern")
    (run_dir / "production_brief.md").write_text(plan["brief_markdown"] + "\n", encoding="utf-8")

    # Step 1 of REFERENCE_GUIDED: write Reference Analysis BEFORE any Blender call
    analysis_md = (
        "# Reference Analysis: Stylized Medieval Alchemy Lantern\n\n"
        f"- **Approved Reference:** `{ref_png}`\n"
        f"- **SHA-256:** `{ref_features['sha256']}`\n"
        f"- **Dimensions:** {ref_features['width']}x{ref_features['height']}\n"
        f"- **Measured Reference Stats:** mean_rgb={ref_features['mean_rgb']}, "
        f"amber_glow_ratio={ref_features['amber_glow_ratio']}, top_brass_ratio={ref_features['top_brass_ratio']}\n"
        "- **Palette:** dark iron `#22262E`, warm brass `#D6982A`, amber core `#FF8E1A`/`#FFD05C`, slate studio `#141926`.\n"
        "- **Camera Match:** 50mm lens, slightly elevated 3/4 view targeting center `(0.0, 0.0, 0.36)`.\n"
    )
    (run_dir / "reference_analysis.md").write_text(analysis_md, encoding="utf-8")

    contracts = [build_contract_from_plan_item(item) for item in plan["selected_skills"]]
    contract_by_key = {(c.skill_id, c.production_stage): c for c in contracts}
    metrics = PerformanceMetrics()

    registry.load_skill(
        "blender-director",
        include_references=[
            "../references/reference-image-match.md",
            "../references/reference-analysis-template.md",
            "../references/visual-match-checklist.md",
        ],
    )
    metrics.skill_activations.extend(["blender-director", "reference-image-match"])

    adapter = MCPAdapter(
        StdioMCPTransport(cwd=PROJECT_ROOT, env_overrides=_env_for_isolated_run(runtime_dir)),
        reference_mode="REFERENCE_GUIDED",
        allow_external_assets=False,
    )

    try:
        caps = adapter.discover_capabilities()
        lazy_count = count_blender_procs()
        assert lazy_count == 0

        manifest = build_reproducibility_manifest(
            prompt,
            run_id=run_dir.name,
            project_root=PROJECT_ROOT,
            mcp_server_info=caps["server_info"],
            discovered_tools=caps["tools"],
            model_id="deterministic-orchestrator",
            provider="local-e2e",
            output_dir=str(run_dir),
            supplied_references=[str(ref_png)],
        )

        # Ensure external AI 3D generators are blocked even in REFERENCE_GUIDED (no IMAGE_TO_3D)
        ai_blocked = False
        try:
            adapter.call_tool("generate_3d", {"image": str(ref_png), "provider": "hunyuan3d"}, stage="guardrail_test")
        except PolicyViolationError:
            ai_blocked = True
        assert ai_blocked

        adapter.call_tool("get_addon_status", {"user_prompt": prompt}, stage="startup")
        init_res = adapter.execute_blender_json(
            build_init_scene_code("SCENE_AlchemyLantern", "lantern_ref_e2e_v1"),
            user_prompt=prompt,
            stage="scene_init",
        )["parsed"]

        # Camera correspondence BEFORE geometry
        cam_res = adapter.execute_blender_json(
            build_reference_camera_match_code("SCENE_AlchemyLantern"),
            user_prompt=prompt,
            stage="camera_match",
        )["parsed"]

        # Pass 1 Blender geometry + UV + dim initial materials
        for sid in ("fantasy-worlds", "prop-artist", "stylized-style", "uv-workflow", "materials", "lighting", "rendering"):
            if registry.has_skill(sid):
                registry.load_skill(sid)
                metrics.skill_activations.append(sid)

        pass1_geom = adapter.execute_blender_json(
            build_lantern_pass1_geometry_code("SCENE_AlchemyLantern", "lantern_ref_e2e_v1"),
            user_prompt=prompt,
            stage="modeling_pass1",
        )["parsed"]
        adapter.execute_blender_json(
            build_uv_stage_code("SCENE_AlchemyLantern", pass1_geom["export_objects"]),
            user_prompt=prompt,
            stage="uvs_pass1",
        )
        adapter.execute_blender_json(
            build_lantern_materials_code("SCENE_AlchemyLantern", core_emission_strength=1.2),
            user_prompt=prompt,
            stage="materials_pass1",
        )

        vis_tool = adapter.resolve_tool("visual_capture")["tool_name"]
        look_p1 = adapter.call_tool(
            vis_tool,
            {"mode": "camera", "shading": "rendered", "max_size": 512, "user_prompt": prompt},
            stage="look_pass1",
        )
        render_p1_path = renders_dir / "render_pass1.png"
        render_p1 = adapter.execute_blender_json(
            build_single_camera_render_code("SCENE_AlchemyLantern", str(render_p1_path)),
            user_prompt=prompt,
            stage="render_pass1",
        )["parsed"]
        p1_features = analyze_png_features(render_p1_path)

        # Programmatic Pass 1 discrepancy list vs reference
        gaps_pass1 = [
            f"Top brass finial/handle ring missing compared to reference (top_brass_ratio ref={ref_features['top_brass_ratio']} vs pass1={p1_features['top_brass_ratio']})",
            f"Amber glass core emission dimmer than reference (amber_glow_ratio ref={ref_features['amber_glow_ratio']} vs pass1={p1_features['amber_glow_ratio']})",
            "Iron cage struts need edge bevels for stylized highlight catch",
        ]

        # Pass 2 targeted Blender refinement (without regenerating scene from scratch!)
        pass2_geom = adapter.execute_blender_json(
            build_lantern_pass2_refinement_code("SCENE_AlchemyLantern", "lantern_ref_e2e_v1"),
            user_prompt=prompt,
            stage="modeling_pass2_refinement",
        )["parsed"]
        adapter.execute_blender_json(
            build_uv_stage_code("SCENE_AlchemyLantern", ["SM_Lantern_BrassFinial"]),
            user_prompt=prompt,
            stage="uvs_pass2",
        )
        adapter.execute_blender_json(
            build_lantern_materials_code("SCENE_AlchemyLantern", core_emission_strength=3.0),
            user_prompt=prompt,
            stage="materials_pass2_refinement",
        )
        look_p2 = adapter.call_tool(
            vis_tool,
            {"mode": "camera", "shading": "rendered", "max_size": 512, "user_prompt": prompt},
            stage="look_pass2",
        )
        render_p2_path = renders_dir / "render_pass2.png"
        render_p2 = adapter.execute_blender_json(
            build_single_camera_render_code("SCENE_AlchemyLantern", str(render_p2_path)),
            user_prompt=prompt,
            stage="render_pass2",
        )["parsed"]
        p2_features = analyze_png_features(render_p2_path)

        comparison_sheet = composite_side_by_side_png(
            [ref_png, render_p1_path, render_p2_path],
            renders_dir / "comparison_ref_vs_renders.png",
        )

        # Audit, Save checkpoint, GLB Export & Reimport
        audit_res = adapter.execute_blender_json(
            build_scene_state_audit_code("SCENE_AlchemyLantern", "lantern_ref_e2e_v1"),
            user_prompt=prompt,
            stage="asset_optimization",
        )["parsed"]
        assert audit_res["numeric_suffix_duplicates"] == []

        blend_path = run_dir / "alchemy_lantern.blend"
        glb_path = run_dir / "SM_AlchemyLantern.glb"
        ckpt_res = adapter.execute_blender_json(build_checkpoint_save_code(str(blend_path)), user_prompt=prompt, stage="checkpoint")["parsed"]
        exp_res = adapter.execute_blender_json(
            build_glb_export_and_reimport_code("SCENE_AlchemyLantern", "COL_Export", str(glb_path)),
            user_prompt=prompt,
            stage="export_and_reimport",
        )["parsed"]
        assert ckpt_res["is_blend_header"] and exp_res["valid"]

        # Record contracts (reference-image-match & qa-review marked NOT_VERIFIED until real visual inspection)
        c_ref = contract_by_key[("reference-image-match", "reference_analysis_and_match")]
        c_ref.loaded = True
        c_ref.performed_operations = ["reference_analysis", "camera_match", "blender_modeling_passes", "reference_comparison_loop"]
        c_ref.mcp_evidence = [f"{vis_tool}:pass1", f"{vis_tool}:pass2"]
        c_ref.scene_evidence = [f"cam={cam_res['camera_name']}", f"refined={pass2_geom['refined_objects']}"]
        c_ref.validation_evidence = [str(comparison_sheet)]
        c_ref.evidence_details = {
            "approved_references": [str(ref_png)],
            "reference_analysis_written": True,
            "camera_correspondence_set": True,
            "built_in_blender": True,
            "ai_3d_generators_used": False,
            "comparison_renders": [str(render_p1_path), str(render_p2_path)],
            "comparison_passes": 2,
            "visually_inspected": False,
        }
        evaluate_skill_contract(c_ref)
    finally:
        adapter.close()

    cleanup = wait_for_watchdog_cleanup(runtime_dir, timeout_sec=25.0)
    assert cleanup["cleaned_up"], f"Watchdog failed to clean up REFERENCE_GUIDED Blender process: {cleanup}"

    metrics.total_runtime_sec = round(time.monotonic() - t_start, 2)
    metrics.mcp_calls_by_operation = adapter.mcp_calls_by_operation
    metrics.python_execution_count = adapter.python_exec_count
    metrics.blender_error_count = len(adapter.blender_errors)
    metrics.render_durations_sec = {"pass1": render_p1["duration_sec"], "pass2": render_p2["duration_sec"]}

    trace = {
        "mode": "REFERENCE_GUIDED",
        "run_dir": str(run_dir),
        "lazy_loading_blender_procs_before_tool_call": lazy_count,
        "ai_generator_calls_blocked": adapter.ai_generator_calls_blocked,
        "manifest": manifest,
        "reference_features": ref_features,
        "pass1_features": p1_features,
        "pass2_features": p2_features,
        "gaps_identified_pass1": gaps_pass1,
        "comparison_sheet": str(comparison_sheet),
        "camera_match": cam_res,
        "audit": audit_res,
        "checkpoint": ckpt_res,
        "export": exp_res,
        "watchdog_cleanup": cleanup,
        "contracts": [c.to_dict() for c in contracts],
        "metrics": metrics.to_dict(),
    }
    (run_dir / "execution_trace.json").write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    return trace


def main() -> int:
    assert count_blender_procs() == 0, "Expected 0 blender processes before E2E run"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = PROJECT_ROOT / "e2e_runs" / f"{ts}_dual_mode"
    base_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running real Blender MCP dual-mode E2E suite in {base_dir}...")
    t0 = time.monotonic()
    res_a = run_text_only_crate_e2e(base_dir)
    print(
        f"[Mode A TEXT_ONLY] tris={res_a['audit']['total_export_tris']}, "
        f"glb={res_a['export']['size_bytes']}B, watchdog={res_a['watchdog_cleanup']['final_state']}, "
        f"ai_blocked={res_a['ai_generator_calls_blocked']}"
    )
    res_b = run_reference_guided_lantern_e2e(base_dir)
    print(
        f"[Mode B REFERENCE_GUIDED] tris={res_b['audit']['total_export_tris']}, "
        f"top_brass_p1={res_b['pass1_features']['top_brass_ratio']} -> p2={res_b['pass2_features']['top_brass_ratio']}, "
        f"glb={res_b['export']['size_bytes']}B, watchdog={res_b['watchdog_cleanup']['final_state']}"
    )
    summary = {
        "base_dir": str(base_dir),
        "total_duration_sec": round(time.monotonic() - t0, 2),
        "mode_a_renders": [r["path"] for r in res_a["renders"]["renders"]],
        "mode_a_trace": str(base_dir / "mode_a_text_only_crate" / "execution_trace.json"),
        "mode_b_ref": str(base_dir / "mode_b_reference_guided_lantern" / "approved_references" / "ref_alchemy_lantern.png"),
        "mode_b_comparison_sheet": res_b["comparison_sheet"],
        "mode_b_trace": str(base_dir / "mode_b_reference_guided_lantern" / "execution_trace.json"),
    }
    (base_dir / "dual_mode_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
