#!/usr/bin/env python3
"""Inspect verified Blender Human Base Meshes bundle inside Blender 5.3 via MCP."""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blender_orchestration.mcp_adapter import (  # noqa: E402
    MCPAdapter,
    StdioMCPTransport,
    verify_approved_base_mesh_manifest,
)
from blender_orchestration.sequential_benchmark import BlenderSessionResetter  # noqa: E402

MANIFEST = PROJECT_ROOT / "assets/approved_base_meshes/blender_human_base_meshes/provenance.json"
OUT_PATH = PROJECT_ROOT / "output/autonomous_harness/qwen_adaptive_base_mesh/preflight/base_mesh_inspection.json"
RUNTIME_DIR = PROJECT_ROOT / "output/autonomous_harness/_mcp_runtime"
PORT = 9878


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    parser.add_argument('--output', type=Path, default=OUT_PATH)
    parser.add_argument('--runtime', type=Path, default=RUNTIME_DIR)
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    verified = verify_approved_base_mesh_manifest(args.manifest, require_archive=True)
    if args.output.exists():
        parser.error(f'Output already exists: {args.output}')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))
    os.environ.update(BLENDER_RENDER_DEVICE='CPU', BLENDER_CPU_SOFTWARE_VIEWPORT='true')
    resetter = BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=args.runtime.resolve(), blender_port=args.port)
    env = {
        "BLENDER_HOST": "127.0.0.1",
        "BLENDER_PORT": str(args.port),
        "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode/mcp/blender/config.json"),
        "BLENDER_MCP_RUNTIME_DIR": str(args.runtime.resolve()),
        "BLENDER_MCP_MANAGE_EXTERNAL": "false",
        "BLENDER_MCP_IDLE_TIMEOUT": "300",
        "BLENDER_MCP_WATCHDOG_POLL": "1.0",
        "BLENDER_MCP_AUTO_RESTORE": "false",
    }
    adapter = MCPAdapter(StdioMCPTransport(cwd=PROJECT_ROOT, env_overrides=env), reference_mode="TEXT_ONLY")
    try:
        adapter.discover_capabilities()
        code = (
            "import bpy, json\n"
            f"blend_path = {json.dumps(verified['blend_path'])}\n"
            "with bpy.data.libraries.load(blend_path, link=False) as (data_from, data_to):\n"
            "    obj_names = list(data_from.objects)\n"
            "    data_to.objects = list(obj_names)\n"
            "scene = bpy.data.scenes.new('SCENE_InspectBaseMeshes')\n"
            "if bpy.context.window:\n"
            "    bpy.context.window.scene = scene\n"
            "for obj in list(data_to.objects):\n"
            "    if obj is not None:\n"
            "        scene.collection.objects.link(obj)\n"
            "bpy.context.view_layer.update()\n"
            "items = []\n"
            "for obj in list(data_to.objects):\n"
            "    if obj is None:\n"
            "        continue\n"
            "    rig = obj.find_armature()\n"
            "    entry = {'name': obj.name, 'type': obj.type, 'parent': obj.parent.name if obj.parent else None,\n"
            "             'children': [c.name for c in obj.children], 'armature': rig.name if rig else None}\n"
            "    if obj.type == 'MESH' and obj.data:\n"
            "        me = obj.data\n"
            "        quads = sum(1 for p in me.polygons if len(p.vertices) == 4)\n"
            "        tris = sum(max(1, len(p.vertices) - 2) for p in me.polygons)\n"
            "        world_coords = [obj.matrix_world @ v.co for v in me.vertices]\n"
            "        if world_coords:\n"
            "            xs = [c.x for c in world_coords]\n"
            "            ys = [c.y for c in world_coords]\n"
            "            zs = [c.z for c in world_coords]\n"
            "            bbox = {'min': [round(min(xs),4), round(min(ys),4), round(min(zs),4)], 'max': [round(max(xs),4), round(max(ys),4), round(max(zs),4)], 'size': [round(max(xs)-min(xs),4), round(max(ys)-min(ys),4), round(max(zs)-min(zs),4)]}\n"
            "        else:\n"
            "            bbox = {}\n"
            "        entry.update({\n"
            "            'vertices': len(me.vertices),\n"
            "            'polygons': len(me.polygons),\n"
            "            'triangles': tris,\n"
            "            'quad_ratio': round(quads / max(1, len(me.polygons)), 4),\n"
            "            'uv_layers': [uv.name for uv in me.uv_layers],\n"
            "            'vertex_groups': [vg.name for vg in obj.vertex_groups],\n"
            "            'shape_keys': [kb.name for kb in me.shape_keys.key_blocks] if me.shape_keys else [],\n"
            "            'modifiers': [{'name': m.name, 'type': m.type} for m in obj.modifiers],\n"
            "            'materials': [m.name for m in me.materials if m],\n"
            "            'bbox': bbox, 'topology': bmo.body_structure(obj),\n"
            "        })\n"
            "    items.append(entry)\n"
            "print('__ORCH_JSON__:' + json.dumps({'ok': True, 'blender_version': bpy.app.version_string, 'object_count': len(items), 'objects': items}))\n"
        )
        code = (f'import sys\nsys.path.insert(0, {str(PROJECT_ROOT)!r})\n'
                'from blender_orchestration import base_mesh_ops as bmo\n') + code
        res = adapter.execute_blender_json(code, user_prompt="Inspect official Human Base Meshes bundle", stage="inspect_base_meshes")["parsed"]
        payload = {"provenance": verified, "inspection": res}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, indent=2)
            stream.write('\n')
        print(json.dumps({"ok": res.get("ok"), "blender_version": res.get("blender_version"), "object_count": res.get("object_count"), "names": [o["name"] for o in res.get("objects", [])]}, indent=2))
        return 0
    finally:
        adapter.close()
        resetter.reset_for_next_model(start_fresh_blender=False)


if __name__ == "__main__":
    raise SystemExit(main())
