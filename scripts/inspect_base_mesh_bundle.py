#!/usr/bin/env python3
"""Inspect verified Blender Human Base Meshes bundle inside Blender 5.3 via MCP."""

from __future__ import annotations

import json
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
    verified = verify_approved_base_mesh_manifest(MANIFEST)
    resetter = BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=RUNTIME_DIR, blender_port=PORT)
    resetter.reset_for_next_model(start_fresh_blender=True)
    env = {
        "BLENDER_HOST": "127.0.0.1",
        "BLENDER_PORT": str(PORT),
        "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode/mcp/blender/config.json"),
        "BLENDER_MCP_RUNTIME_DIR": str(RUNTIME_DIR),
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
            "items = []\n"
            "for obj in list(data_to.objects):\n"
            "    if obj is None:\n"
            "        continue\n"
            "    scene.collection.objects.link(obj)\n"
            "    entry = {'name': obj.name, 'type': obj.type}\n"
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
            "            'bbox': bbox,\n"
            "        })\n"
            "    items.append(entry)\n"
            "print('__ORCH_JSON__:' + json.dumps({'ok': True, 'blender_version': bpy.app.version_string, 'object_count': len(items), 'objects': items}))\n"
        )
        res = adapter.execute_blender_json(code, user_prompt="Inspect official Human Base Meshes bundle", stage="inspect_base_meshes")["parsed"]
        payload = {"provenance": verified, "inspection": res}
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": res.get("ok"), "blender_version": res.get("blender_version"), "object_count": res.get("object_count"), "names": [o["name"] for o in res.get("objects", [])]}, indent=2))
        return 0
    finally:
        adapter.close()
        resetter.reset_for_next_model(start_fresh_blender=False)


if __name__ == "__main__":
    raise SystemExit(main())
