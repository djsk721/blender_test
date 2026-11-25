#!/usr/bin/env python3
"""Asset-agnostic direct Blender MCP stage runner CLI.

Executes specialist stage scripts through real Blender MCP (`MCPAdapter` +
`StdioMCPTransport`) on an isolated Blender port (default: 9878), reusing
`blender_orchestration.blender_toolkit`, `BlenderSessionResetter`, and
`compose_review_sheets` without any dependency on external LLM quotas.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blender_orchestration.mcp_adapter import MCPAdapter, StdioMCPTransport
from blender_orchestration.rubric_evaluator import compose_review_sheets
from blender_orchestration.sequential_benchmark import BlenderSessionResetter


def _default_runtime_dir(port: int) -> Path:
    return PROJECT_ROOT / "output" / "autonomous_harness" / f"_mcp_runtime_{port}"


def _build_mcp_env(port: int, runtime_dir: Path) -> dict[str, str]:
    return {
        "BLENDER_HOST": "127.0.0.1",
        "BLENDER_PORT": str(port),
        "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json"),
        "BLENDER_MCP_RUNTIME_DIR": str(runtime_dir),
        "BLENDER_MCP_IDLE_TIMEOUT": "900",
        "BLENDER_MCP_WATCHDOG_POLL": "1.0",
        "BLENDER_MCP_AUTO_RESTORE": "false",
    }


def _open_adapter(port: int, runtime_dir: Path) -> MCPAdapter:
    adapter = MCPAdapter(
        StdioMCPTransport(cwd=PROJECT_ROOT, env_overrides=_build_mcp_env(port, runtime_dir)),
        reference_mode="REFERENCE_GUIDED",
        allow_external_assets=False,
    )
    adapter.discover_capabilities()
    return adapter


def _run_blender_util(
    adapter: MCPAdapter,
    code_body: str,
    *,
    stage_label: str,
    user_prompt: str = "Direct MCP stage execution",
) -> dict[str, Any]:
    full = (
        "import importlib, json, math, sys, traceback\n"
        f"if {json.dumps(str(PROJECT_ROOT))} not in sys.path:\n"
        f"    sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
        "from pathlib import Path\n"
        "import bpy, bmesh\n"
        "from mathutils import Vector, Euler, Matrix\n"
        "from blender_orchestration import blender_toolkit as bt\n"
        "bt = importlib.reload(bt)\n"
        + code_body
    )
    return adapter.execute_blender_json(full, user_prompt=user_prompt, stage=stage_label)["parsed"]


def _save_verified_checkpoint(
    adapter: MCPAdapter,
    scene_name: str,
    blend_path: Path,
    *,
    stage_label: str,
) -> dict[str, Any]:
    blend_path = Path(blend_path).resolve()
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
        f"    p = Path({json.dumps(str(blend_path))})\n"
        "    hdr = p.read_bytes()[:7] if p.is_file() else b''\n"
        "    ok = p.is_file() and p.stat().st_size > 1024 and (\n"
        "        hdr[:7] == b'BLENDER' or hdr[:4] == b'\\x28\\xb5\\x2f\\xfd' or hdr[:2] == b'\\x1f\\x8b'\n"
        "    )\n"
        "    print('__ORCH_JSON__:' + json.dumps({'saved': ok, 'path': str(p), 'bytes': p.stat().st_size if p.is_file() else 0}))\n"
    )
    return _run_blender_util(adapter, code, stage_label=stage_label)


def _cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = Path(args.runtime_dir).resolve() if args.runtime_dir else _default_runtime_dir(args.port)
    reset_report = None
    if args.reset:
        resetter = BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=runtime_dir, blender_port=args.port)
        reset_report = resetter.reset_for_next_model(start_fresh_blender=True)

    adapter = _open_adapter(args.port, runtime_dir)
    try:
        status_res = adapter.call_tool("get_addon_status", {"user_prompt": "Initialize scene"}, stage="init_addon_status")
        init_res = _run_blender_util(
            adapter,
            f"s = bt.ensure_clean_scene({json.dumps(args.scene_name)})\n"
            "print('__ORCH_JSON__:' + json.dumps({'ok': True, 'scene_name': s.name}))\n",
            stage_label="init_scene",
        )
        save_res = None
        if args.checkpoint:
            save_res = _save_verified_checkpoint(adapter, args.scene_name, Path(args.checkpoint), stage_label="init_save_ckpt")
        return {
            "command": "init",
            "port": args.port,
            "scene_name": args.scene_name,
            "mcp_connected": True,
            "addon_status_ok": bool(status_res),
            "scene_initialized": bool(init_res.get("ok")),
            "checkpoint": save_res,
            "reset_report": reset_report,
        }
    finally:
        adapter.close()


def _cmd_exec(args: argparse.Namespace) -> dict[str, Any]:
    script_path = Path(args.script).resolve()
    if not script_path.is_file():
        raise FileNotFoundError(f"Script not found: {script_path}")
    user_code = script_path.read_text(encoding="utf-8")
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_in = str(Path(args.checkpoint).resolve()) if (args.checkpoint and Path(args.checkpoint).is_file()) else ""

    runtime_dir = Path(args.runtime_dir).resolve() if args.runtime_dir else _default_runtime_dir(args.port)
    adapter = _open_adapter(args.port, runtime_dir)
    try:
        adapter.call_tool("get_addon_status", {"user_prompt": f"Execute {script_path.name}"}, stage="exec_addon_status")
        code = (
            f"SCENE_NAME = {json.dumps(args.scene_name)}\n"
            f"OUT_DIR = {json.dumps(str(out_dir))}\n"
            f"_CKPT = {json.dumps(ckpt_in)}\n"
            "if _CKPT and SCENE_NAME not in bpy.data.scenes:\n"
            "    bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "elif _CKPT and " + repr(bool(args.reload_checkpoint)) + ":\n"
            "    bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "if SCENE_NAME not in bpy.data.scenes:\n"
            "    scene = bt.ensure_clean_scene(SCENE_NAME)\n"
            "else:\n"
            "    scene = bpy.data.scenes[SCENE_NAME]\n"
            "    if bpy.context.window:\n"
            "        bpy.context.window.scene = scene\n"
            "col_export = bt.get_or_create_collection(scene, 'COL_Export')\n"
            "col_rig = bt.get_or_create_collection(scene, 'COL_Rig')\n"
            "col_studio = bt.get_or_create_collection(scene, 'COL_Studio')\n"
            f"_user_code = {json.dumps(user_code)}\n"
            "_ns = {\n"
            "    'bpy': bpy, 'bmesh': bmesh, 'bt': bt, 'math': math, 'json': json, 'Path': Path,\n"
            "    'Vector': Vector, 'Euler': Euler, 'Matrix': Matrix,\n"
            "    'SCENE_NAME': SCENE_NAME, 'OUT_DIR': OUT_DIR,\n"
            "    'scene': scene, 'col_export': col_export, 'col_rig': col_rig, 'col_studio': col_studio,\n"
            "}\n"
            "try:\n"
            "    exec(_user_code, _ns)\n"
            "    hygiene = bt.clean_loose_geometry(scene)\n"
            "    audit = bt.get_scene_audit(scene)\n"
            "    anomalies = bt.audit_geometry_anomalies(scene)\n"
            "    print('__ORCH_JSON__:' + json.dumps({'ok': True, 'hygiene': hygiene, 'audit': audit, 'anomalies': anomalies}))\n"
            "except Exception as _exc:\n"
            "    tb = traceback.format_exc()\n"
            "    print('__ORCH_JSON__:' + json.dumps({'ok': False, 'error': str(_exc), 'traceback': tb[-2400:]}))\n"
        )
        res = _run_blender_util(adapter, code, stage_label=f"exec_{script_path.stem}")
        save_res = None
        if res.get("ok") and args.save_checkpoint:
            save_res = _save_verified_checkpoint(
                adapter,
                args.scene_name,
                Path(args.save_checkpoint),
                stage_label=f"save_{script_path.stem}",
            )
        return {
            "command": "exec",
            "script": str(script_path),
            "scene_name": args.scene_name,
            **res,
            "checkpoint": save_res,
        }
    finally:
        adapter.close()


def _cmd_render(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_in = str(Path(args.checkpoint).resolve()) if (args.checkpoint and Path(args.checkpoint).is_file()) else ""
    views = [v.strip() for v in args.views.split(",") if v.strip()]
    res_xy = [int(args.res[0]), int(args.res[1])]

    runtime_dir = Path(args.runtime_dir).resolve() if args.runtime_dir else _default_runtime_dir(args.port)
    adapter = _open_adapter(args.port, runtime_dir)
    try:
        frame_stmt = f"scene.frame_set({int(args.frame)})\n" if args.frame is not None else ""
        pose_stmt = ""
        if args.pose_position:
            pose_stmt = (
                "for o in scene.objects:\n"
                "    if o.type == 'ARMATURE':\n"
                f"        o.data.pose_position = {json.dumps(args.pose_position)}\n"
            )
        code = (
            f"_SN = {json.dumps(args.scene_name)}\n"
            f"_CKPT = {json.dumps(ckpt_in)}\n"
            "if _CKPT and _SN not in bpy.data.scenes:\n"
            "    bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "elif _CKPT and " + repr(bool(args.reload_checkpoint)) + ":\n"
            "    bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "scene = bpy.data.scenes[_SN]\n"
            "if bpy.context.window:\n"
            "    bpy.context.window.scene = scene\n"
            + pose_stmt
            + frame_stmt
            + "bpy.context.view_layer.update()\n"
            + "hygiene = bt.clean_loose_geometry(scene)\n"
            + f"r = bt.render_standard_views(scene, {json.dumps(str(out_dir))}, {json.dumps(args.prefix)}, "
            + f"views={json.dumps(views)}, mode={json.dumps(args.mode)}, res={json.dumps(res_xy)}, samples={int(args.samples)}, use_gpu=True)\n"
            + "audit = bt.get_scene_audit(scene)\n"
            + "anomalies = bt.audit_geometry_anomalies(scene)\n"
            + "print('__ORCH_JSON__:' + json.dumps({'render': r, 'audit': audit, 'anomalies': anomalies, 'hygiene': hygiene}))\n"
        )
        res = _run_blender_util(adapter, code, stage_label=f"render_{args.prefix}")
        renders = (res.get("render") or {}).get("renders") or []
        sheets = [str(p) for p in compose_review_sheets(renders, out_dir)]
        return {
            "command": "render",
            "mode": args.mode,
            "renders": renders,
            "contact_sheets": sheets,
            "anomalies": res.get("anomalies"),
            "hygiene": res.get("hygiene"),
            "audit_summary": {
                k: (res.get("audit") or {}).get(k)
                for k in ("scene_name", "mesh_count", "total_triangles", "quad_ratio", "material_count", "uv_missing_objects", "min_z_m", "max_z_m", "height_m", "armatures", "actions")
            },
        }
    finally:
        adapter.close()


def _cmd_export_verify(args: argparse.Namespace) -> dict[str, Any]:
    ckpt_in = str(Path(args.checkpoint).resolve()) if (args.checkpoint and Path(args.checkpoint).is_file()) else ""
    final_blend = Path(args.final_blend).resolve()
    final_glb = Path(args.final_glb).resolve()
    render_dir = Path(args.render_dir).resolve()
    final_blend.parent.mkdir(parents=True, exist_ok=True)
    final_glb.parent.mkdir(parents=True, exist_ok=True)
    render_dir.mkdir(parents=True, exist_ok=True)
    res_xy = [int(args.res[0]), int(args.res[1])]

    runtime_dir = Path(args.runtime_dir).resolve() if args.runtime_dir else _default_runtime_dir(args.port)
    adapter = _open_adapter(args.port, runtime_dir)
    try:
        hygiene_res = _run_blender_util(
            adapter,
            f"_SN = {json.dumps(args.scene_name)}\n"
            f"_CKPT = {json.dumps(ckpt_in)}\n"
            "if _CKPT and _SN not in bpy.data.scenes:\n"
            "    bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "elif _CKPT and " + repr(bool(args.reload_checkpoint)) + ":\n"
            "    bpy.ops.wm.open_mainfile(filepath=_CKPT)\n"
            "s = bpy.data.scenes[_SN]\n"
            "if bpy.context.window:\n"
            "    bpy.context.window.scene = s\n"
            "col = bt.get_or_create_collection(s, 'COL_Export')\n"
            "unwrapped = []\n"
            "for o in col.all_objects:\n"
            "    if o.type == 'MESH' and o.data is not None and len(o.data.uv_layers) == 0:\n"
            "        if bt.smart_uv_unwrap(o):\n"
            "            unwrapped.append(o.name)\n"
            "loose = bt.clean_loose_geometry(s)\n"
            "for o in s.objects:\n"
            "    if o.type == 'ARMATURE':\n"
            "        o.data.pose_position = 'REST'\n"
            "        for pb in o.pose.bones:\n"
            "            pb.rotation_euler = (0.0, 0.0, 0.0)\n"
            "            pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)\n"
            "            pb.location = (0.0, 0.0, 0.0)\n"
            "bpy.context.view_layer.update()\n"
            "print('__ORCH_JSON__:' + json.dumps({'unwrapped': unwrapped, 'loose': loose, 'audit': bt.get_scene_audit(s)}))\n",
            stage_label="stage_06_uv_hygiene",
        )
        blend_res = _save_verified_checkpoint(adapter, args.scene_name, final_blend, stage_label="stage_06_save_final_blend")
        export_res = _run_blender_util(
            adapter,
            f"s = bpy.data.scenes[{json.dumps(args.scene_name)}]\n"
            "for o in s.objects:\n"
            "    if o.type == 'ARMATURE':\n"
            "        o.data.pose_position = 'POSE'\n"
            "bpy.context.view_layer.update()\n"
            f"res = bt.export_and_verify_glb(s, {json.dumps(str(final_glb))})\n"
            "for o in s.objects:\n"
            "    if o.type == 'ARMATURE':\n"
            "        o.data.pose_position = 'REST'\n"
            "bpy.context.view_layer.update()\n"
            "print('__ORCH_JSON__:' + json.dumps(res))\n",
            stage_label="stage_06_glb_export_verify",
        )
        fin_look = _run_blender_util(
            adapter,
            f"s = bpy.data.scenes[{json.dumps(args.scene_name)}]\n"
            f"r = bt.render_standard_views(s, {json.dumps(str(render_dir))}, 'final', "
            "views=['front', 'three_quarter', 'side', 'rear', 'closeup_top', 'closeup_mid', 'closeup_bottom'], "
            f"mode='lookdev', res={json.dumps(res_xy)}, samples={int(args.samples)}, use_gpu=True)\n"
            "print('__ORCH_JSON__:' + json.dumps({'render': r, 'audit': bt.get_scene_audit(s), 'anomalies': bt.audit_geometry_anomalies(s)}))\n",
            stage_label="stage_06_final_lookdev",
        )
        fin_clay = _run_blender_util(
            adapter,
            f"s = bpy.data.scenes[{json.dumps(args.scene_name)}]\n"
            f"r = bt.render_standard_views(s, {json.dumps(str(render_dir))}, 'final_clay', "
            "views=['three_quarter', 'closeup_top'], mode='clay', res=[768, 960], samples=28, use_gpu=True)\n"
            "print('__ORCH_JSON__:' + json.dumps({'render': r}))\n",
            stage_label="stage_06_final_clay",
        )
        all_renders = list((fin_look.get("render") or {}).get("renders") or []) + list((fin_clay.get("render") or {}).get("renders") or [])
        return {
            "command": "export-verify",
            "uv_hygiene": hygiene_res,
            "blend_saved": bool(blend_res.get("saved")),
            "blend_info": blend_res,
            "glb_export_and_reimport": bool(export_res.get("valid")),
            "export_verification": export_res,
            "renders": all_renders,
            "audit": fin_look.get("audit"),
            "anomalies": fin_look.get("anomalies"),
        }
    finally:
        adapter.close()


def _cmd_shutdown(args: argparse.Namespace) -> dict[str, Any]:
    runtime_dir = Path(args.runtime_dir).resolve() if args.runtime_dir else _default_runtime_dir(args.port)
    resetter = BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=runtime_dir, blender_port=args.port)
    report = resetter.reset_for_next_model(start_fresh_blender=False)
    return {"command": "shutdown", "port": args.port, "report": report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Direct Blender MCP stage runner CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--port", type=int, default=9878)
        p.add_argument("--runtime-dir", default="")

    p_init = sub.add_parser("init", help="Initialize clean Blender scene and optional checkpoint")
    _add_common(p_init)
    p_init.add_argument("--scene-name", required=True)
    p_init.add_argument("--checkpoint", default="")
    p_init.add_argument("--reset", action="store_true")

    p_exec = sub.add_parser("exec", help="Execute a Blender Python script via MCP")
    _add_common(p_exec)
    p_exec.add_argument("--scene-name", required=True)
    p_exec.add_argument("--script", required=True)
    p_exec.add_argument("--out-dir", required=True)
    p_exec.add_argument("--checkpoint", default="")
    p_exec.add_argument("--reload-checkpoint", action="store_true")
    p_exec.add_argument("--save-checkpoint", default="")

    p_ren = sub.add_parser("render", help="Render multi-view studio PNGs and contact sheets")
    _add_common(p_ren)
    p_ren.add_argument("--scene-name", required=True)
    p_ren.add_argument("--out-dir", required=True)
    p_ren.add_argument("--prefix", required=True)
    p_ren.add_argument("--mode", choices=("clay", "lookdev"), default="lookdev")
    p_ren.add_argument("--views", default="front,three_quarter,side,rear,closeup_top,closeup_mid,closeup_bottom")
    p_ren.add_argument("--res", nargs=2, type=int, default=[768, 960])
    p_ren.add_argument("--samples", type=int, default=28)
    p_ren.add_argument("--checkpoint", default="")
    p_ren.add_argument("--reload-checkpoint", action="store_true")
    p_ren.add_argument("--frame", type=int, default=None)
    p_ren.add_argument("--pose-position", choices=("POSE", "REST"), default="")

    p_exp = sub.add_parser("export-verify", help="Run UV hygiene, save .blend, export/reimport .glb, and render high-res final suite")
    _add_common(p_exp)
    p_exp.add_argument("--scene-name", required=True)
    p_exp.add_argument("--checkpoint", default="")
    p_exp.add_argument("--reload-checkpoint", action="store_true")
    p_exp.add_argument("--final-blend", required=True)
    p_exp.add_argument("--final-glb", required=True)
    p_exp.add_argument("--render-dir", required=True)
    p_exp.add_argument("--res", nargs=2, type=int, default=[1024, 1280])
    p_exp.add_argument("--samples", type=int, default=64)

    p_shut = sub.add_parser("shutdown", help="Stop isolated managed Blender MCP process")
    _add_common(p_shut)

    args = parser.parse_args(argv)
    if args.cmd == "init":
        out = _cmd_init(args)
    elif args.cmd == "exec":
        out = _cmd_exec(args)
    elif args.cmd == "render":
        out = _cmd_render(args)
    elif args.cmd == "export-verify":
        out = _cmd_export_verify(args)
    elif args.cmd == "shutdown":
        out = _cmd_shutdown(args)
    else:
        raise SystemExit(2)

    print(json.dumps(out, indent=2))
    if args.cmd == "exec" and not out.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
