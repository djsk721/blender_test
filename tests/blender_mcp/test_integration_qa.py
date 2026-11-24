"""Real-environment integration QA suite (B01-B22) for Blender MCP lazy loading and lifecycle."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = PROJECT_ROOT / ".opencode" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from blender.config_loader import load_config
from blender.gpu_manager import get_gpu_snapshot
from blender.lifecycle_manager import AddonClient, BlenderLifecycleManager

CONFIG_PATH = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json"
WRAPPER_PATH = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "mcp_wrapper.py"
START_SH = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "start.sh"
STOP_SH = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "stop.sh"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_blender_pids() -> list[int]:
    pids: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
            if comm == "blender":
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
                rp = stat.rfind(")")
                state = stat[rp + 2 :].split()[0]
                if state not in {"Z", "X"}:
                    pids.append(pid)
        except OSError:
            continue
    return sorted(pids)


def _find_defunct_pids() -> list[int]:
    defunct: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
            rp = stat.rfind(")")
            state = stat[rp + 2 :].split()[0]
            if state == "Z":
                comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
                if comm in {"blender", "Xvfb", "mcp_wrapper.py", "mcp-for-blender", "uvx", "python3"}:
                    defunct.append(pid)
        except OSError:
            continue
    return sorted(defunct)


class MCPStdioClient:
    """Real newline-delimited JSON-RPC stdio client driving .opencode/mcp/blender/mcp_wrapper.py."""

    def __init__(self, env_overrides: dict[str, str] | None = None) -> None:
        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        self.proc = subprocess.Popen(
            [sys.executable, "-u", str(WRAPPER_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
            env=env,
            bufsize=0,
        )
        self._seq = 0
        self._lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict]] = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            msg = json.loads(line)
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    q = self._pending.pop(msg["id"], None)
                if q is not None:
                    q.put(msg)

    def request(self, method: str, params: dict | None = None, timeout: float = 90.0) -> dict:
        with self._lock:
            self._seq += 1
            req_id = self._seq
            q: queue.Queue[dict] = queue.Queue(maxsize=1)
            self._pending[req_id] = q
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return q.get(timeout=timeout)

    def notify(self, method: str, params: dict | None = None) -> None:
        payload: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def initialize(self) -> dict:
        resp = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "qa-harness", "version": "1.0.0"},
            },
            timeout=30.0,
        )
        self.notify("notifications/initialized")
        return resp

    def list_tools(self) -> list[dict]:
        resp = self.request("tools/list", {}, timeout=30.0)
        return resp["result"]["tools"]

    def call_tool(self, name: str, arguments: dict | None = None, timeout: float = 120.0) -> dict:
        return self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.proc.terminate()
                self.proc.wait(timeout=3.0)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass


def _extract_text(resp: dict) -> str:
    content = resp.get("result", {}).get("content", [])
    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")


class TestBlenderMCPIntegrationQA(unittest.TestCase):
    qa_results: dict[str, dict] = {}
    metrics: dict[str, object] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_config(CONFIG_PATH)
        cls.mgr = BlenderLifecycleManager(cls.cfg)
        cls.mgr.stop_blender(reason="qa_setup", force=True, discard_session=True)
        cls.metrics["baseline_gpu"] = get_gpu_snapshot()
        cls.metrics["llm_pids_initial"] = cls._snapshot_llm_pids()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.mgr.stop_blender(reason="qa_teardown", force=True, discard_session=True)
        report_path = cls.cfg.runtime_dir / "qa_report.json"
        report_path.write_text(
            json.dumps({"results": cls.qa_results, "metrics": cls.metrics}, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _snapshot_llm_pids() -> dict[str, list[int]]:
        out: dict[str, list[int]] = {"strata": [], "ollama": []}
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                comm = Path(f"/proc/{pid}/comm").read_text(encoding="utf-8", errors="replace").strip()
                if comm.startswith("strata"):
                    out["strata"].append(pid)
                elif comm == "ollama":
                    out["ollama"].append(pid)
            except OSError:
                continue
        return {k: sorted(v) for k, v in out.items()}

    def _record(self, qid: str, passed: bool, detail: str, **extra: object) -> None:
        self.qa_results[qid] = {"passed": passed, "detail": detail, **extra}

    def _wait_for_stopped(self, mgr: BlenderLifecycleManager, timeout: float = 25.0) -> dict:
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            last = mgr.status(probe_live=False)
            if last.get("state") == "STOPPED" and not last.get("blender_alive") and not _find_blender_pids():
                return last
            time.sleep(0.5)
        return last

    def test_00_layout_retired_old_path(self) -> None:
        self.assertFalse((PROJECT_ROOT / "mcp").exists(), "Old mcp/ directory must not exist")
        oc = json.loads((PROJECT_ROOT / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(
            oc["mcp"]["servers"]["blender"]["command"],
            ["python3", "-u", ".opencode/mcp/blender/mcp_wrapper.py"],
        )

    def test_01_b01_to_b05_opencode_and_lazy_init(self) -> None:
        proc1 = subprocess.run(
            [
                "opencode",
                "--standalone",
                "run",
                "--model",
                "strata/sc117-qwen38-abliterated-iq3_s#none",
                "--format",
                "json",
                "Reply with exactly READY and nothing else. Do not call any tools.",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        self.assertEqual(_find_blender_pids(), [], "B01: Blender must NOT run on fresh OpenCode start")
        self._record("B01", True, "Fresh OpenCode standalone run succeeded with 0 blender processes")

        rel = subprocess.run(["opencode", "reload"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(rel.returncode, 0, rel.stderr)
        time.sleep(2.5)
        mcp_list = subprocess.run(["opencode", "mcp", "list"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30)
        self.assertEqual(mcp_list.returncode, 0, mcp_list.stderr)
        self.assertIn("blender", mcp_list.stdout)
        self.assertIn("connected", mcp_list.stdout)
        self.assertEqual(_find_blender_pids(), [], "B02: Blender must NOT run on OpenCode reload")
        self._record("B02", True, "opencode reload kept blender connected with 0 blender processes")

        client = MCPStdioClient()
        try:
            init_resp = client.initialize()
            self.assertIn("result", init_resp)
            self.assertEqual(init_resp["result"]["serverInfo"]["name"], "MCP for Blender")
            self.assertEqual(_find_blender_pids(), [], "B03: Blender must NOT run on initialize")
            self._record("B03", True, "MCP initialize succeeded with 0 blender processes")

            tools = client.list_tools()
            tool_names = {t["name"] for t in tools}
            for expected in ("get_addon_status", "get_scene_info", "execute_blender_code", "look", "search_assets", "import_asset"):
                self.assertIn(expected, tool_names)
            self.assertEqual(_find_blender_pids(), [], "B04: Blender must NOT run on tools/list")
            self._record("B04", True, f"tools/list returned {len(tools)} tools with 0 blender processes")
        finally:
            client.close()

        proc5 = subprocess.run(
            [
                "opencode",
                "--standalone",
                "run",
                "--model",
                "strata/sc117-qwen38-abliterated-iq3_s#none",
                "--format",
                "json",
                "Write a 1-line Python lambda for adding two numbers in your response text only. Do not call any tools.",
            ],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(proc5.returncode, 0, proc5.stderr)
        self.assertEqual(_find_blender_pids(), [], "B05: Blender must NOT run during general coding task")
        self._record("B05", True, "General coding task completed with 0 blender processes")

    def test_02_b06_to_b12_launch_reuse_concurrency_idle_gpu_relaunch(self) -> None:
        env_short = {"BLENDER_MCP_IDLE_TIMEOUT": "12", "BLENDER_MCP_WATCHDOG_POLL": "1"}
        mgr_short = BlenderLifecycleManager(load_config(CONFIG_PATH))
        mgr_short.cfg.idle_timeout_sec = 12.0

        client = MCPStdioClient(env_overrides=env_short)
        c2 = MCPStdioClient(env_overrides=env_short)
        try:
            client.initialize()
            c2.initialize()
            t0 = time.monotonic()
            resp6 = client.call_tool("get_addon_status", {})
            call_sec = round(time.monotonic() - t0, 3)
            self.assertFalse(resp6.get("result", {}).get("isError", False), resp6)
            txt6 = _extract_text(resp6)
            self.assertIn("5.3.0", txt6)
            st6 = mgr_short.status(probe_live=True)
            self.assertTrue(st6["blender_alive"])
            self.assertTrue(st6["addon_reachable"])
            pid1 = st6["blender_pid"]
            boot1 = st6["boot_id"]
            self.metrics["cold_start_sec"] = st6["startup_duration_sec"]
            self.metrics["first_call_total_sec"] = call_sec
            self.metrics["gpu_during_blender"] = get_gpu_snapshot()
            self._record("B06", True, f"Auto-launched pid={pid1}, startup={st6['startup_duration_sec']}s, first_call={call_sec}s")

            resp7 = client.call_tool(
                "execute_blender_code",
                {
                    "code": (
                        "import bpy\n"
                        "bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(2.0, 0.0, 1.0))\n"
                        "bpy.context.active_object.name = 'QA_Test_Sphere'\n"
                        "print('CREATED_SPHERE')\n"
                    )
                },
            )
            self.assertIn("CREATED_SPHERE", _extract_text(resp7))
            scene7 = client.call_tool("get_scene_info", {})
            self.assertIn("QA_Test_Sphere", _extract_text(scene7))
            self._record("B07", True, "Created QA_Test_Sphere and verified via get_scene_info")

            st8 = mgr_short.status(probe_live=True)
            self.assertEqual(st8["blender_pid"], pid1)
            self.assertEqual(st8["boot_id"], boot1)
            self._record("B08", True, f"Consecutive calls reused pid={pid1}, boot_id={boot1}")

            def run_job(cli: MCPStdioClient, obj_name: str) -> str:
                r = cli.call_tool(
                    "execute_blender_code",
                    {
                        "code": (
                            "import bpy, time\n"
                            "time.sleep(1.0)\n"
                            "bpy.ops.mesh.primitive_cube_add(size=0.5)\n"
                            f"bpy.context.active_object.name = '{obj_name}'\n"
                            f"print('OK_{obj_name}')\n"
                        )
                    },
                )
                return _extract_text(r)

            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(run_job, client, "QA_Conc_1")
                f2 = pool.submit(run_job, c2, "QA_Conc_2")
                out1 = f1.result(timeout=60)
                out2 = f2.result(timeout=60)
            self.assertIn("OK_QA_Conc_1", out1)
            self.assertIn("OK_QA_Conc_2", out2)
            self.assertEqual(len(_find_blender_pids()), 1)
            self.assertEqual(_find_blender_pids()[0], pid1)
            self._record("B09", True, "Concurrent requests serialized cleanly on single Blender PID")

            client.call_tool(
                "execute_blender_code",
                {
                    "code": (
                        "import bpy\n"
                        "for n in ('QA_Test_Sphere', 'QA_Conc_1', 'QA_Conc_2'):\n"
                        "    o = bpy.data.objects.get(n)\n"
                        "    if o:\n"
                        "        bpy.data.objects.remove(o, do_unlink=True)\n"
                        "print('CLEANED')\n"
                    )
                },
            )

            time.sleep(3.5)
            health_env = os.environ.copy()
            health_env["PYTHONPATH"] = str(MCP_DIR)
            health_res = subprocess.run(
                [sys.executable, "-m", "blender.lifecycle_manager", "health"],
                cwd=str(PROJECT_ROOT),
                env=health_env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(health_res.returncode, 0, health_res.stderr)
            tools_again = client.list_tools()
            self.assertGreater(len(tools_again), 5)
            st_after = self._wait_for_stopped(mgr_short, timeout=24.0)
            self.assertEqual(st_after["state"], "STOPPED", st_after)
            self.assertEqual(_find_blender_pids(), [])
            self._record("B10", True, "Health check did not reset idle timer; Blender stopped after 12s idle")

            gpu_after = get_gpu_snapshot()
            self.metrics["gpu_after_stop"] = gpu_after
            blender_gpu_apps = [a for a in gpu_after.get("apps", []) if "blender" in str(a.get("process_name", "")).lower()]
            self.assertEqual(blender_gpu_apps, [], f"Blender still on GPU: {blender_gpu_apps}")
            self.assertTrue((st_after.get("gpu_after_stop") or {}).get("released"))
            self._record("B11", True, "Blender GPU VRAM completely released after shutdown")

            resp12 = client.call_tool("get_scene_info", {})
            self.assertFalse(resp12.get("result", {}).get("isError", False), resp12)
            st12 = mgr_short.status(probe_live=True)
            self.assertTrue(st12["blender_alive"])
            self.assertNotEqual(st12["boot_id"], boot1)
            self._record("B12", True, f"Relaunched transparently on same wrapper session: new pid={st12['blender_pid']}, boot_id={st12['boot_id']}")
        finally:
            c2.close()
            client.close()
            self.mgr.stop_blender(reason="b12_cleanup", force=True, discard_session=True)

    def test_03_b13_active_work_no_idle_kill(self) -> None:
        env = {"BLENDER_MCP_IDLE_TIMEOUT": "5", "BLENDER_MCP_WATCHDOG_POLL": "1"}
        client = MCPStdioClient(env_overrides=env)
        try:
            client.initialize()
            t0 = time.monotonic()
            resp = client.call_tool(
                "execute_blender_code",
                {
                    "code": (
                        "import time, bpy\n"
                        "time.sleep(8.0)\n"
                        "bpy.ops.mesh.primitive_cube_add(size=1.0)\n"
                        "bpy.context.active_object.name = 'QA_LongJob_Cube'\n"
                        "print('LONG_JOB_DONE')\n"
                    )
                },
                timeout=120.0,
            )
            elapsed = time.monotonic() - t0
            self.assertGreaterEqual(elapsed, 8.0)
            self.assertIn("LONG_JOB_DONE", _extract_text(resp))
            st_mid = self.mgr.status(probe_live=True)
            self.assertTrue(st_mid["blender_alive"], "B13: Blender must NOT be killed during active work")
            st_end = self._wait_for_stopped(self.mgr, timeout=18.0)
            self.assertEqual(st_end["state"], "STOPPED")
            self._record("B13", True, f"Blender stayed alive during {elapsed:.1f}s job, then stopped after idle")
        finally:
            client.close()
            self.mgr.stop_blender(reason="b13_cleanup", force=True, discard_session=True)

    def test_04_b14_unsaved_work_backup_and_defer(self) -> None:
        env_a = {"BLENDER_MCP_IDLE_TIMEOUT": "5", "BLENDER_MCP_WATCHDOG_POLL": "1", "BLENDER_MCP_AUTO_BACKUP": "true"}
        c_a = MCPStdioClient(env_overrides=env_a)
        try:
            c_a.initialize()
            c_a.call_tool(
                "execute_blender_code",
                {
                    "code": (
                        "import bpy\n"
                        "bpy.ops.mesh.primitive_monkey_add()\n"
                        "bpy.context.active_object.name = 'QA_Dirty_Suzanne'\n"
                        "print('DIRTY:', bpy.data.is_dirty)\n"
                    )
                },
            )
            st_stop = self._wait_for_stopped(self.mgr, timeout=18.0)
            self.assertEqual(st_stop["state"], "STOPPED")
            sb = st_stop.get("session_backup") or {}
            self.assertTrue(sb.get("is_dirty"))
            bpath = Path(sb.get("backup_path", ""))
            self.assertTrue(bpath.is_file(), f"Backup missing: {bpath}")
            self.assertEqual(_sha256(bpath), sb.get("sha256"))
        finally:
            c_a.close()
            self.mgr.stop_blender(reason="b14a_cleanup", force=True, discard_session=True)

        env_b = {"BLENDER_MCP_IDLE_TIMEOUT": "60", "BLENDER_MCP_AUTO_BACKUP": "false"}
        c_b = MCPStdioClient(env_overrides=env_b)
        try:
            c_b.initialize()
            c_b.call_tool(
                "execute_blender_code",
                {
                    "code": (
                        "import bpy\n"
                        "bpy.ops.mesh.primitive_cone_add()\n"
                        "bpy.context.active_object.name = 'QA_Dirty_NoBackup'\n"
                        "print('DIRTY_NOBACKUP')\n"
                    )
                },
            )
            cfg_no_bak = load_config(CONFIG_PATH)
            cfg_no_bak.auto_backup = False
            mgr_no_bak = BlenderLifecycleManager(cfg_no_bak)
            stop_res = mgr_no_bak.stop_blender(reason="b14b_test", force=False)
            self.assertFalse(stop_res.get("stopped"))
            self.assertTrue(stop_res.get("deferred"))
            self.assertEqual(stop_res.get("reason"), "unsaved_work_auto_backup_disabled")
            self.assertTrue(mgr_no_bak.status(probe_live=True)["blender_alive"])
            self._record("B14", True, "Dirty scene created verified backup when enabled, and deferred stop when auto_backup=false")
        finally:
            c_b.close()
            self.mgr.stop_blender(reason="b14b_cleanup", force=True, discard_session=True)

    def test_05_b15_b16_b17_crash_reload_and_skills(self) -> None:
        client = MCPStdioClient({"BLENDER_MCP_IDLE_TIMEOUT": "120", "BLENDER_MCP_WATCHDOG_POLL": "1"})
        try:
            client.initialize()
            r1 = client.call_tool("get_scene_info", {})
            self.assertFalse(r1.get("result", {}).get("isError", False))
            st1 = self.mgr.status(probe_live=True)
            pid1 = st1["blender_pid"]
            boot1 = st1["boot_id"]

            os.kill(pid1, signal.SIGKILL)
            time.sleep(1.5)
            r_rec = client.call_tool("get_scene_info", {})
            self.assertFalse(r_rec.get("result", {}).get("isError", False), r_rec)
            st_rec = self.mgr.status(probe_live=True)
            self.assertTrue(st_rec["blender_alive"])
            self.assertNotEqual(st_rec["blender_pid"], pid1)
            self.assertNotEqual(st_rec["boot_id"], boot1)
            pid2 = st_rec["blender_pid"]
            self._record("B15", True, f"Recovered automatically from SIGKILL pid={pid1} -> new pid={pid2}")

            rel = subprocess.run(["opencode", "reload"], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=30)
            self.assertEqual(rel.returncode, 0)
            time.sleep(2.5)
            pids_now = _find_blender_pids()
            self.assertEqual(pids_now, [pid2], f"B16: Expected single pid {pid2}, got {pids_now}")
            self._record("B16", True, f"opencode reload preserved active Blender pid={pid2} with no duplicates")

            skill_code = (
                "import bpy\n"
                "col = bpy.data.collections.new('COL_Props')\n"
                "bpy.context.scene.collection.children.link(col)\n"
                "bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.5))\n"
                "obj = bpy.context.active_object\n"
                "obj.name = 'SM_QA_Crate_01'\n"
                "obj.data.name = 'SM_QA_Crate_01'\n"
                "for c in list(obj.users_collection):\n"
                "    c.objects.unlink(obj)\n"
                "col.objects.link(obj)\n"
                "mat = bpy.data.materials.new(name='MAT_QA_Crate')\n"
                "mat.use_nodes = True\n"
                "bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')\n"
                "bsdf.inputs['Base Color'].default_value = (0.45, 0.28, 0.12, 1.0)\n"
                "obj.data.materials.append(mat)\n"
                "print('SKILL_ASSET_OK', obj.name, mat.name)\n"
            )
            r_asset = client.call_tool("execute_blender_code", {"code": skill_code})
            self.assertIn("SKILL_ASSET_OK SM_QA_Crate_01 MAT_QA_Crate", _extract_text(r_asset))
            r_scene = client.call_tool("get_scene_info", {})
            self.assertIn("SM_QA_Crate_01", _extract_text(r_scene))
            r_look = client.call_tool("look", {"mode": "viewport", "shading": "solid", "max_size": 320})
            content = r_look.get("result", {}).get("content", [])
            images = [c for c in content if isinstance(c, dict) and c.get("type") == "image"]
            self.assertTrue(len(images) >= 1, f"B17: look returned no image: {r_look}")
            img_bytes = base64.b64decode(images[0]["data"])
            self.assertTrue(img_bytes.startswith(b"\x89PNG"), "look image is not PNG")
            self._record("B17", True, "Blender Skills COL_/SM_/MAT_ workflow + look PNG capture succeeded")
        finally:
            client.close()
            self.mgr.stop_blender(reason="b17_cleanup", force=True, discard_session=True)

    def test_06_b18_b19_cleanup_and_llm_integrity(self) -> None:
        stop_proc = subprocess.run(
            [str(STOP_SH), "--force", "--discard-session"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(stop_proc.returncode, 0, stop_proc.stderr)
        time.sleep(1.0)
        self.assertEqual(_find_blender_pids(), [])
        self.assertEqual(_find_defunct_pids(), [])
        st = self.mgr.status(probe_live=False)
        self.assertFalse(st["xvfb_alive"])
        self._record("B18", True, "Zero managed Blender, zero managed Xvfb, zero defunct children after stop")

        llm_now = self._snapshot_llm_pids()
        self.assertEqual(llm_now, self.metrics["llm_pids_initial"], f"LLM PIDs changed: {llm_now}")
        with urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=5) as r_strata:
            self.assertEqual(r_strata.status, 200)
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r_ollama:
            self.assertEqual(r_ollama.status, 200)
        self._record("B19", True, f"Local LLM PIDs unchanged ({llm_now}) and endpoints responsive")

    def test_07_b20_external_blender_protection(self) -> None:
        ext_env = os.environ.copy()
        ext_env["BLENDER_HOST"] = "127.0.0.1"
        ext_env["BLENDER_PORT"] = "9876"
        ext_proc = subprocess.Popen(
            ["xvfb-run", "-a", "blender"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=ext_env,
            start_new_session=True,
        )
        client = None
        try:
            addon = AddonClient("127.0.0.1", 9876)
            deadline = time.monotonic() + 30.0
            ready = False
            while time.monotonic() < deadline:
                if addon.ping(timeout=1.5) and addon.get_addon_info(timeout=2.0):
                    ready = True
                    break
                time.sleep(0.25)
            self.assertTrue(ready, "External test Blender failed to become ready")

            client = MCPStdioClient({"BLENDER_MCP_IDLE_TIMEOUT": "4", "BLENDER_MCP_WATCHDOG_POLL": "1"})
            client.initialize()
            resp = client.call_tool("get_scene_info", {})
            self.assertFalse(resp.get("result", {}).get("isError", False), resp)
            st = self.mgr.status(probe_live=True)
            self.assertEqual(st["ownership"], "external")
            ext_pid = st["external_pid"]
            self.assertIsNotNone(ext_pid)

            time.sleep(6.5)
            stop_res = self.mgr.stop_blender(reason="b20_check", force=False)
            self.assertFalse(stop_res.get("stopped"))
            self.assertEqual(stop_res.get("reason"), "external_blender_protected")
            self.assertTrue(addon.ping(timeout=2.0), "External Blender was unexpectedly killed")
            self._record("B20", True, f"Adopted external Blender pid={ext_pid} and protected it from idle/stop")
        finally:
            if client is not None:
                client.close()
            try:
                os.killpg(os.getpgid(ext_proc.pid), signal.SIGTERM)
            except OSError:
                pass
            try:
                ext_proc.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(ext_proc.pid), signal.SIGKILL)
                except OSError:
                    pass
            self.mgr.stop_blender(reason="b20_cleanup", force=True, discard_session=True)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and _find_blender_pids():
                time.sleep(0.25)

    def test_08_b21_wrapper_crash_and_recovery(self) -> None:
        c1 = MCPStdioClient({"BLENDER_MCP_IDLE_TIMEOUT": "120", "BLENDER_MCP_WATCHDOG_POLL": "1"})
        c1.initialize()
        r1 = c1.call_tool("get_scene_info", {})
        self.assertFalse(r1.get("result", {}).get("isError", False))
        st1 = self.mgr.status(probe_live=True)
        pid1 = st1["blender_pid"]

        stale_marker = self.cfg.active_jobs_dir / "999999_crashed.json"
        stale_marker.write_text('{"pid": 999999}', encoding="utf-8")
        os.kill(c1.proc.pid, signal.SIGKILL)
        c1.proc.wait(timeout=5.0)
        c1.close()
        time.sleep(1.0)

        c2 = MCPStdioClient({"BLENDER_MCP_IDLE_TIMEOUT": "120", "BLENDER_MCP_WATCHDOG_POLL": "1"})
        try:
            c2.initialize()
            r2 = c2.call_tool("get_scene_info", {})
            self.assertFalse(r2.get("result", {}).get("isError", False))
            st2 = self.mgr.status(probe_live=True)
            self.assertEqual(st2["blender_pid"], pid1)
            self.assertFalse(stale_marker.exists(), "Stale marker should be reaped automatically")
            self._record("B21", True, f"Wrapper SIGKILL recovered cleanly; stale marker reaped; Blender pid={pid1} preserved")
        finally:
            c2.close()
            self.mgr.stop_blender(reason="b21_cleanup", force=True, discard_session=True)

    def test_09_b22_file_preservation_and_verified_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orig_blend = Path(tmp) / "qa_original.blend"
            env = {
                "BLENDER_MCP_IDLE_TIMEOUT": "5",
                "BLENDER_MCP_WATCHDOG_POLL": "1",
                "BLENDER_MCP_AUTO_BACKUP": "true",
                "BLENDER_MCP_AUTO_RESTORE": "true",
            }
            c = MCPStdioClient(env_overrides=env)
            try:
                c.initialize()
                c.call_tool(
                    "execute_blender_code",
                    {
                        "code": (
                            "import bpy\n"
                            "bpy.ops.mesh.primitive_torus_add()\n"
                            "bpy.context.active_object.name = 'QA_Original_Torus'\n"
                            f"bpy.ops.wm.save_as_mainfile(filepath={json.dumps(str(orig_blend))})\n"
                            "print('SAVED_ORIG')\n"
                        )
                    },
                )
                orig_sha = _sha256(orig_blend)
                orig_mtime = orig_blend.stat().st_mtime

                c.call_tool(
                    "execute_blender_code",
                    {
                        "code": (
                            "import bpy\n"
                            "bpy.ops.mesh.primitive_cylinder_add()\n"
                            "bpy.context.active_object.name = 'QA_Unsaved_Cylinder'\n"
                            "print('DIRTY:', bpy.data.is_dirty)\n"
                        )
                    },
                )

                st_stop = self._wait_for_stopped(self.mgr, timeout=18.0)
                self.assertEqual(st_stop["state"], "STOPPED")
                self.assertEqual(_sha256(orig_blend), orig_sha, "Original .blend must NEVER be overwritten")
                self.assertEqual(orig_blend.stat().st_mtime, orig_mtime)

                r_re = c.call_tool("get_scene_info", {})
                txt_re = _extract_text(r_re)
                self.assertIn("QA_Original_Torus", txt_re)
                self.assertIn("QA_Unsaved_Cylinder", txt_re)
                st_re = self.mgr.status(probe_live=True)
                rec = st_re.get("session_recovery") or {}
                self.assertEqual(rec.get("status"), "verified", rec)
                self.assertTrue(rec.get("verified"))

                c.call_tool(
                    "execute_blender_code",
                    {
                        "code": (
                            "import bpy\n"
                            "bpy.ops.mesh.primitive_ico_sphere_add()\n"
                            "bpy.context.active_object.name = 'QA_Corrupt_Check'\n"
                        )
                    },
                )
                st_stop2 = self._wait_for_stopped(self.mgr, timeout=18.0)
                self.assertEqual(st_stop2["state"], "STOPPED")
                bak2 = Path((st_stop2.get("session_backup") or {})["backup_path"])
                bak2.write_bytes(b"CORRUPTED_BYTES")

                c.call_tool("get_scene_info", {})
                st_fail = self.mgr.status(probe_live=True)
                rec_fail = st_fail.get("session_recovery") or {}
                self.assertEqual(rec_fail.get("status"), "failed", rec_fail)
                self.assertFalse(rec_fail.get("verified"))
                self._record("B22", True, "Original .blend preserved untouched; verified restore succeeded; corrupt backup properly reported failed")
            finally:
                c.close()
                self.mgr.stop_blender(reason="b22_cleanup", force=True, discard_session=True)


if __name__ == "__main__":
    unittest.main()
