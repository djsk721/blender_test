"""Blender MCP executor for producer-authored scripts.

Only code authorized by ``ProvenanceLedger.authorize_execution`` reaches ``execute_producer_script``.
Everything else this class sends to Blender is harness infrastructure (scene setup, measurement,
rendering, checkpointing, export) and never creates asset geometry.
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import Any

from ..mcp_adapter import MCPAdapter, MCPAdapterError, StdioMCPTransport
from .checkpoints import CheckpointManager, is_blend_file
from .provenance import ProvenanceLedger, ProvenanceRecord
from .script_validator import validate_blender_script


class BlenderTimeoutError(MCPAdapterError):
    pass


class TimeoutStdioMCPTransport(StdioMCPTransport):
    """Stdio transport whose RPC calls are bounded (the legacy transport can block forever)."""

    def __init__(self, *args: Any, rpc_timeout_sec: float = 900.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rpc_timeout_sec = float(rpc_timeout_sec)
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        if self._proc is not None:
            return
        super().start()
        proc = self._proc

        def _pump() -> None:
            assert proc is not None and proc.stdout is not None
            for line in proc.stdout:
                self._lines.put(line)
            self._lines.put(None)

        self._reader = threading.Thread(target=_pump, daemon=True)
        self._reader.start()

    def call_rpc(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        if self._proc is None:
            self.start()
        assert self._proc is not None and self._proc.stdin is not None
        req_id = self._next_id
        self._next_id += 1
        self._proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}) + "\n")
        self._proc.stdin.flush()
        limit = float(timeout or self.rpc_timeout_sec)
        import time as _t
        deadline = _t.monotonic() + limit
        while True:
            remaining = deadline - _t.monotonic()
            if remaining <= 0:
                raise BlenderTimeoutError(f"Blender MCP call {method} timed out after {limit:.0f}s")
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise BlenderTimeoutError(f"Blender MCP call {method} timed out after {limit:.0f}s") from None
            if line is None:
                raise MCPAdapterError(f"MCP server closed stdout during {method}")
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == req_id:
                if msg.get("error") is not None:
                    raise MCPAdapterError(f"RPC error for {method}: {msg['error']}")
                return msg


_PRELUDE = (
    "import importlib, json, math, sys, traceback\n"
    "if {root!r} not in sys.path:\n"
    "    sys.path.insert(0, {root!r})\n"
    "import bpy, bmesh\n"
    "from mathutils import Vector, Euler, Matrix\n"
    "from blender_orchestration import blender_toolkit as bt\n"
    "from blender_orchestration.v3 import blender_checks as bc\n"
    "bt = importlib.reload(bt)\n"
    "bc = importlib.reload(bc)\n"
)


class BlenderExecutor:
    def __init__(self, *, project_root: Path, scene_name: str, run_dir: Path, mcp_env: dict[str, str],
                 ledger: ProvenanceLedger, checkpoints: CheckpointManager, rpc_timeout_sec: float = 900.0,
                 adapter: MCPAdapter | None = None) -> None:
        self.project_root = Path(project_root)
        self.scene_name = scene_name
        self.run_dir = Path(run_dir)
        self.mcp_env = dict(mcp_env)
        self.ledger = ledger
        self.checkpoints = checkpoints
        self.rpc_timeout_sec = rpc_timeout_sec
        self.adapter = adapter or self._new_adapter()
        self.calls: list[dict[str, Any]] = []

    def _new_adapter(self) -> MCPAdapter:
        transport = TimeoutStdioMCPTransport(cwd=self.project_root, env_overrides=self.mcp_env,
                                            rpc_timeout_sec=self.rpc_timeout_sec)
        return MCPAdapter(transport, reference_mode="REFERENCE_GUIDED", allow_external_assets=False)

    def close(self) -> None:
        try:
            self.adapter.close()
        except Exception:
            pass

    def reconnect(self) -> None:
        self.close()
        self.adapter = self._new_adapter()
        self.adapter.discover_capabilities()

    # -- harness utilities ------------------------------------------------
    def util(self, body: str, *, label: str) -> dict[str, Any]:
        code = _PRELUDE.format(root=str(self.project_root)) + body
        res = self.adapter.execute_blender_json(code, user_prompt=f"harness:{label}", stage=label)["parsed"]
        self.calls.append({"label": label, "role": "harness"})
        return res

    def _scene_expr(self) -> str:
        return (
            f"_SN = {self.scene_name!r}\n"
            "if _SN not in bpy.data.scenes:\n"
            "    raise RuntimeError('target scene missing: ' + _SN)\n"
            "scene = bpy.data.scenes[_SN]\n"
            "if bpy.context.window:\n"
            "    bpy.context.window.scene = scene\n"
        )

    def init_scene(self) -> dict[str, Any]:
        return self.util(
            f"s = bt.ensure_clean_scene({self.scene_name!r})\n"
            "for n in ('COL_Export', 'COL_Rig', 'COL_Studio'):\n"
            "    bt.get_or_create_collection(s, n)\n"
            "print('__ORCH_JSON__:' + json.dumps({'ok': True, 'scene': s.name, 'audit': bt.get_scene_audit(s)}))\n",
            label="init_scene")

    def inspect_base_mesh(self, source):
        return self.util(
            'from blender_orchestration import base_mesh_ops as bmo\n'
            f'r = bmo.inspect_library_selection({source["blend_path"]!r}, {source["object_name"]!r})\n'
            "print('__ORCH_JSON__:' + json.dumps(r))\n", label='inspect_approved_base_mesh')

    def load_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        entry = self.checkpoints.verify(checkpoint_id)
        res = self.util(
            f"bpy.ops.wm.open_mainfile(filepath={entry.path!r}, use_scripts=False)\n"
            + self._scene_expr()
            + "print('__ORCH_JSON__:' + json.dumps({'ok': True, 'audit': bt.get_scene_audit(scene), 'snap': bc.snapshot(scene)}))\n",
            label=f"load_{checkpoint_id}")
        if not res.get("ok") or not (res.get("audit") or {}).get("mesh_count"):
            raise MCPAdapterError(f"checkpoint {checkpoint_id} did not load an asset scene")
        return res

    def save_checkpoint(self, *, stage: str, label: str, kind: str = "technical") -> Any:
        path = self.checkpoints.new_path(stage, label)
        res = self.util(
            self._scene_expr()
            + "try:\n    bpy.ops.file.pack_all()\nexcept Exception:\n    pass\n"
            + f"_p = {str(path)!r}\n"
            "ok = any(o.type == 'MESH' for o in scene.objects)\n"
            "if ok:\n"
            "    bpy.ops.wm.save_as_mainfile(filepath=_p, copy=True, relative_remap=False)\n"
            "print('__ORCH_JSON__:' + json.dumps({'saved': ok, 'path': _p}))\n",
            label=f"save_{stage}_{label}")
        if not res.get("saved") or not is_blend_file(path):
            raise MCPAdapterError(f"checkpoint save failed for {stage}/{label}")
        return self.checkpoints.register(path, stage=stage, label=label, kind=kind, extra=self.ledger.geometry)

    # -- producer execution -------------------------------------------------
    def execute_producer_script(self, code: str, record: ProvenanceRecord, *, phase: str,
                                rollback_checkpoint_id: str | None) -> dict[str, Any]:
        """Validate, authorize and run producer code; on any failure restore and verify the pre-step scene."""
        self.ledger.authorize_execution(code, record)
        validation = validate_blender_script(code)
        if not validation.ok:
            return {"ok": False, "stage": "validation", "error": "script refused: " + validation.feedback(),
                    "rolled_back": True, "scene_unchanged": True}
        ckpt_path = ""
        if rollback_checkpoint_id:
            ckpt_path = self.checkpoints.verify(rollback_checkpoint_id).path
        source = self.ledger.geometry.get('base_mesh')
        base_setup = (
            'from blender_orchestration import base_mesh_ops as bmo\n'
            f'_source = {source!r}\n'
            '_body = col_export.all_objects.get("SM_BaseBody")\n'
            '_base = {"object": _body, "before": bmo.body_structure(_body)} if _body else {}\n'
            'def load_approved_body():\n'
            '    if _base or PHASE != "silhouette" or _CKPT:\n'
            '        raise RuntimeError("Approved body can only be loaded once in the initial silhouette edit")\n'
            '    loaded = bmo.verify_and_load_base_mesh(scene, _source["manifest_path"], _source["object_name"],\n'
            '        collection=col_export, expected_source=_source)\n'
            '    _base.update(object=loaded["object"], before=bmo.body_structure(loaded["object"]), loaded=True)\n'
            '    return loaded\n'
            '_ns.update(bmo=bmo, load_approved_body=load_approved_body)\n'
        ) if source else ''
        base_check = (
            '    _body = col_export.all_objects.get("SM_BaseBody")\n'
            '    if not _base or _body is None or _body != _base["object"] or _body.type != "MESH":\n'
            '        raise RuntimeError("Expected the verified SM_BaseBody in COL_Export; preserve the imported body object")\n'
            '    if PHASE == "silhouette" and not _CKPT and not _base.get("loaded"):\n'
            '        raise RuntimeError("Initial silhouette must load the pinned source using load_approved_body()")\n'
            '    _old, _new = _base["before"], bmo.body_structure(_body)\n'
            '    _changed = _new["shape_sha256"] != _old["shape_sha256"]\n'
            '    if _base.get("loaded") and not _changed:\n'
            '        raise RuntimeError("Approved body was loaded but its vertex geometry was not transformed")\n'
            '    if not _new["polygons"] or any(_new[k] > _old[k] for k in ("components", "boundary_edges",\n'
            '            "non_manifold_edges", "inconsistent_winding_edges", "degenerate_faces")):\n'
            '        raise RuntimeError("Base body topology regressed: " + str({"before": _old, "after": _new}))\n'
            '    _base_evidence = {"source": _source, "object": _body.name, "loaded": bool(_base.get("loaded")),\n'
            '                      "changed": _changed, "before": _old, "after": _new}\n'
        ) if source else ''
        wrapper = (
            _PRELUDE.format(root=str(self.project_root))
            + self._scene_expr()
            + "col_export = bt.get_or_create_collection(scene, 'COL_Export')\n"
            "col_rig = bt.get_or_create_collection(scene, 'COL_Rig')\n"
            f"PHASE = {phase!r}\n"
            f"_CKPT = {ckpt_path!r}\n"
            "_before = bc.snapshot(scene)\n"
            f"_user_code = {json.dumps(code)}\n"
            "_ns = {'bpy': bpy, 'bmesh': bmesh, 'bt': bt, 'math': math, 'json': json, 'Vector': Vector,\n"
            "       'Euler': Euler, 'Matrix': Matrix, 'scene': scene, 'col_export': col_export, 'col_rig': col_rig,\n"
            "       'SCENE_NAME': _SN, 'PHASE': PHASE}\n"
            + base_setup
            + "_base_evidence = None\n_base_attempt = None\n"
            "try:\n"
            "    exec(compile(_user_code, 'producer_script.py', 'exec'), _ns)\n"
            "    scene = bpy.data.scenes[_SN]\n"
            "    _moved = bc.relink_stray_meshes(scene, _before)\n"
            "    _guard = bc.geometry_guard(scene, _before)\n"
            "    if not _guard['ok']:\n"
            "        raise RuntimeError('geometry guard: ' + '; '.join(_guard['problems']))\n"
            + base_check
            + "    print('__ORCH_JSON__:' + json.dumps({'ok': True, 'base_mesh': _base_evidence, 'audit': bt.get_scene_audit(scene), 'guard': _guard,\n"
            "          'relinked': _moved, 'landmarks': _ns.get('LANDMARKS') if isinstance(_ns.get('LANDMARKS'), dict) else None}, default=str))\n"
            "except Exception as _exc:\n"
            "    if globals().get('_base', {}).get('loaded'):\n"
            "        _base_attempt = {'source': _source, 'loaded': True, 'before': _base['before']}\n"
            "    _tb = traceback.format_exc()[-1800:]\n"
            "    _rb = False\n"
            "    _same = False\n"
            "    if _CKPT:\n"
            "        try:\n"
            "            _rb = 'FINISHED' in bpy.ops.wm.open_mainfile(filepath=_CKPT, use_scripts=False)\n"
            "            _s2 = bpy.data.scenes.get(_SN)\n"
            "            _same = bool(_s2) and bc.snapshot(_s2)['objects'] == _before['objects']\n"
            "        except Exception:\n"
            "            pass\n"
            "    print('__ORCH_JSON__:' + json.dumps({'ok': False, 'error': str(_exc)[:900], 'traceback': _tb,\n"
            "          'rolled_back': _rb, 'scene_unchanged': _same, 'base_mesh_attempt': _base_attempt}))\n"
        )
        try:
            res = self.adapter.execute_blender_json(wrapper, user_prompt=f"producer:{phase}", stage=f"producer_{phase}")["parsed"]
        except BlenderTimeoutError as exc:
            return {"ok": False, "stage": "execution", "error": f"Blender timeout: {exc}", "rolled_back": False,
                    "scene_unchanged": False, "needs_recovery": True}
        except MCPAdapterError as exc:
            return {"ok": False, "stage": "execution", "error": f"Blender MCP error: {exc}", "rolled_back": False,
                    "scene_unchanged": False, "needs_recovery": True}
        self.calls.append({"label": f"producer_{phase}", "role": "producer", "record_id": record.record_id})
        res["validation_warnings"] = validation.warnings
        return res

    # -- measurement & rendering ---------------------------------------------
    def measure(self) -> dict[str, Any]:
        return self.util(
            self._scene_expr()
            + "print('__ORCH_JSON__:' + json.dumps({'audit': bt.get_scene_audit(scene), 'anomalies': bt.audit_geometry_anomalies(scene),\n"
            "      'face': bc.face_report(scene), 'proportions': bc.proportion_report(scene)}, default=str))\n",
            label="measure")

    def render(self, out_dir: Path, prefix: str, *, mode: str, res: tuple[int, int], samples: int,
               face: bool = True, body_views: tuple[str, ...] = ("front", "three_quarter", "side", "rear")) -> dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        views = list(body_views) + (["face_front", "face_three_quarter", "face_side"] if face else [])
        return self.util(
            self._scene_expr()
            + "fb = bc.face_focus_boxes(scene)\n"
            f"r = bt.render_standard_views(scene, {str(out_dir)!r}, {prefix!r}, views={views!r}, mode={mode!r},\n"
            f"    res={list(res)!r}, samples={int(samples)}, focus_boxes=fb, use_gpu=False)\n"
            "print('__ORCH_JSON__:' + json.dumps({'render': r}, default=str))\n",
            label=f"render_{prefix}")

    def export_glb(self, path: Path, *, mode: str) -> dict[str, Any]:
        return self.util(
            self._scene_expr()
            + "for o in scene.objects:\n    if o.type == 'ARMATURE':\n        o.data.pose_position = 'POSE'\n"
            "bpy.context.view_layer.update()\n"
            "source = bc.character_export_inventory(bc._export_meshes(scene))\n"
            f"res = bt.export_and_verify_glb(scene, {str(path)!r}, verify_reimport=lambda fresh: "
            f"bc.compare_character_export(source, bc.character_export_inventory(fresh.objects), {mode!r}))\n"
            "for o in scene.objects:\n    if o.type == 'ARMATURE':\n        o.data.pose_position = 'REST'\n"
            "print('__ORCH_JSON__:' + json.dumps(res, default=str))\n",
            label="export_glb")

    def render_motion(self, out_dir: Path, *, animation: bool, samples: int) -> list[dict[str, Any]]:
        result = self.util(self._scene_expr() +
            f"r = bc.motion_review(scene, {str(out_dir)!r}, animation={animation!r}, samples={samples})\n"
            "print('__ORCH_JSON__:' + json.dumps(r))\n", label="motion_review")
        if not result.get("ok"):
            raise MCPAdapterError(str(result.get("reason")))
        return result["renders"]
