"""Strict sequential GPT -> Full Blender Lifecycle Reset -> Qwen benchmark orchestrator.

Implements the 10-state FSM:
  IDLE -> GPT_RUNNING -> GPT_FINISHED -> RESETTING -> RESET_VERIFIED
  -> QWEN_RUNNING -> QWEN_FINISHED -> CLEANUP -> COMPARISON -> COMPLETE
  (with BLOCKED_RESET_FAILED and ABORTED failure states).

Uses .opencode/mcp/blender/lifecycle_manager.py exclusively (never pkill/killall/kill -9)
and enforces single-flight locking + BenchmarkIsolationGuard across phases.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
import fcntl
import json
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any, Callable
import zlib

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENCODE_MCP_DIR = PROJECT_ROOT / ".opencode" / "mcp"
if str(OPENCODE_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(OPENCODE_MCP_DIR))

from blender.config_loader import load_config  # type: ignore  # noqa: E402
from blender.gpu_manager import get_gpu_snapshot, verify_gpu_released  # type: ignore  # noqa: E402
from blender.lifecycle_manager import BlenderLifecycleManager, list_live_markers  # type: ignore  # noqa: E402
from blender.process_manager import ProcessRecord, is_same_process  # type: ignore  # noqa: E402

from .regression_fixtures import BenchmarkIsolationGuard


class SequentialBenchmarkState:
    IDLE = "IDLE"
    GPT_RUNNING = "GPT_RUNNING"
    GPT_FINISHED = "GPT_FINISHED"
    RESETTING = "RESETTING"
    RESET_VERIFIED = "RESET_VERIFIED"
    QWEN_RUNNING = "QWEN_RUNNING"
    QWEN_FINISHED = "QWEN_FINISHED"
    CLEANUP = "CLEANUP"
    COMPARISON = "COMPARISON"
    COMPLETE = "COMPLETE"
    BLOCKED_RESET_FAILED = "BLOCKED_RESET_FAILED"
    ABORTED = "ABORTED"

    ORDERED_MAIN = (
        IDLE,
        GPT_RUNNING,
        GPT_FINISHED,
        RESETTING,
        RESET_VERIFIED,
        QWEN_RUNNING,
        QWEN_FINISHED,
        CLEANUP,
        COMPARISON,
        COMPLETE,
    )

    VALID_TRANSITIONS: dict[str, tuple[str, ...]] = {
        IDLE: (GPT_RUNNING, ABORTED),
        GPT_RUNNING: (GPT_FINISHED, ABORTED),
        GPT_FINISHED: (RESETTING, ABORTED),
        RESETTING: (RESET_VERIFIED, BLOCKED_RESET_FAILED, ABORTED),
        RESET_VERIFIED: (QWEN_RUNNING, ABORTED),
        QWEN_RUNNING: (QWEN_FINISHED, ABORTED),
        QWEN_FINISHED: (CLEANUP, ABORTED),
        CLEANUP: (COMPARISON, ABORTED),
        COMPARISON: (COMPLETE, ABORTED),
        COMPLETE: (),
        BLOCKED_RESET_FAILED: (),
        ABORTED: (),
    }


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def composite_png_strip(image_paths: list[Path], out_path: Path, *, panel_w: int = 320, panel_h: int = 400) -> Path | None:
    """Create a side-by-side PNG comparison strip using stdlib zlib/struct (no Pillow dependency)."""
    valid_paths = [
        Path(p)
        for p in image_paths
        if Path(p).is_file() and Path(p).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    ]
    if not valid_paths:
        return None

    def _decode(path: Path) -> tuple[int, int, bytearray]:
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"Not a PNG file: {path}")
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
                    raise ValueError(f"Unsupported PNG bit depth {bit_depth}")
            elif ctype == b"IDAT":
                idat_chunks.append(chunk_data)
            elif ctype == b"IEND":
                break
        raw = zlib.decompress(b"".join(idat_chunks))
        bpp = 4 if color_type == 6 else 3
        stride = width * bpp
        recon = bytearray(height * width * 3)
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
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                    row[x] = (row[x] + pr) & 0xFF
            prev_row = row
            out_base = y * width * 3
            for px in range(width):
                recon[out_base + px * 3 : out_base + px * 3 + 3] = row[px * bpp : px * bpp + 3]
        return width, height, recon

    decoded = [_decode(p) for p in valid_paths]
    gap = 10
    total_w = len(decoded) * panel_w + (len(decoded) - 1) * gap
    out = bytearray([24, 26, 32] * (total_w * panel_h))
    for p_idx, (sw, sh, srgb) in enumerate(decoded):
        x_off = p_idx * (panel_w + gap)
        for dy in range(panel_h):
            sy = min(sh - 1, int(dy * sh / panel_h))
            for dx in range(panel_w):
                sx = min(sw - 1, int(dx * sw / panel_w))
                soff = (sy * sw + sx) * 3
                doff = (dy * total_w + (x_off + dx)) * 3
                out[doff : doff + 3] = srgb[soff : soff + 3]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_rows = bytearray()
    stride = total_w * 3
    for y in range(panel_h):
        raw_rows.append(0)
        raw_rows.extend(out[y * stride : (y + 1) * stride])

    def _chunk(ct: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + ct + d + struct.pack(">I", zlib.crc32(ct + d) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", total_w, panel_h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_rows), level=6)
    out_path.write_bytes(b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b""))
    return out_path


class BlenderSessionResetter:
    """Performs full Phase B managed Blender lifecycle reset & empty-scene verification."""

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        runtime_dir: Path | None = None,
        blender_port: int | None = None,
    ) -> None:
        self.project_root = Path(project_root) if project_root is not None else PROJECT_ROOT
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else (self.project_root / ".opencode" / "mcp" / "blender" / "runtime")
        self.blender_port = blender_port

    def _make_manager(self) -> BlenderLifecycleManager:
        cfg_path = self.project_root / ".opencode" / "mcp" / "blender" / "config.json"
        env_bk_rt = os.environ.get("BLENDER_MCP_RUNTIME_DIR")
        env_bk_ar = os.environ.get("BLENDER_MCP_AUTO_RESTORE")
        env_bk_pr = os.environ.get("BLENDER_MCP_PROJECT_ROOT")
        env_bk_bp = os.environ.get("BLENDER_PORT")
        try:
            os.environ["BLENDER_MCP_PROJECT_ROOT"] = str(self.project_root)
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = str(self.runtime_dir)
            os.environ["BLENDER_MCP_AUTO_RESTORE"] = "false"
            if self.blender_port is not None:
                os.environ["BLENDER_PORT"] = str(self.blender_port)
            cfg = load_config(config_path=cfg_path)
            return BlenderLifecycleManager(cfg=cfg)
        finally:
            if env_bk_pr is None:
                os.environ.pop("BLENDER_MCP_PROJECT_ROOT", None)
            else:
                os.environ["BLENDER_MCP_PROJECT_ROOT"] = env_bk_pr
            if env_bk_rt is None:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)
            else:
                os.environ["BLENDER_MCP_RUNTIME_DIR"] = env_bk_rt
            if env_bk_ar is None:
                os.environ.pop("BLENDER_MCP_AUTO_RESTORE", None)
            else:
                os.environ["BLENDER_MCP_AUTO_RESTORE"] = env_bk_ar
            if env_bk_bp is None:
                os.environ.pop("BLENDER_PORT", None)
            else:
                os.environ["BLENDER_PORT"] = env_bk_bp

    def inspect_status(self) -> dict[str, Any]:
        mgr = self._make_manager()
        return mgr.status(probe_live=True)

    def reset_for_next_model(
        self,
        *,
        expected_saved_files: list[Path] | None = None,
        forbidden_residual_objects: list[str] | None = None,
        start_fresh_blender: bool = True,
        preserve_backups: bool = False,
    ) -> dict[str, Any]:
        """Execute all 10 Phase B reset checks cleanly using BlenderLifecycleManager."""
        reset_started = _utc_now_iso()
        mgr = self._make_manager()
        checks: dict[str, Any] = {}

        # 1. Confirm previous model's result files exist on disk
        missing_files = [str(p) for p in (expected_saved_files or []) if not Path(p).is_file()]
        checks["1_prior_outputs_saved"] = len(missing_files) == 0
        checks["missing_prior_files"] = missing_files

        # 2. Confirm no active/pending Blender MCP jobs
        active_jobs = list_live_markers(mgr.cfg.active_jobs_dir)
        pending_jobs = list_live_markers(mgr.cfg.pending_jobs_dir)
        checks["2_no_inflight_mcp_jobs"] = (len(active_jobs) == 0 and len(pending_jobs) == 0)

        pre_status = mgr.status(probe_live=True)
        if pre_status.get("addon_reachable") and (
            pre_status.get("ownership") != "managed" or not pre_status.get("blender_alive")
        ):
            raise RuntimeError("The Blender MCP port belongs to a session outside this managed runtime.")
        if active_jobs or pending_jobs or pre_status.get("work_lock_held"):
            raise RuntimeError("The managed Blender session still has work in progress.")
        old_state = mgr.read_state()
        old_blender_rec = ProcessRecord.from_dict(old_state.get("blender"))
        old_pid = pre_status.get("blender_pid") or (old_blender_rec.pid if old_blender_rec else None)
        old_boot_id = pre_status.get("boot_id")
        gpu_before = get_gpu_snapshot()

        # 3. Purge in-memory Blender datablocks if addon is reachable
        purged_ok = True
        if pre_status.get("addon_reachable"):
            try:
                purge_code = (
                    "import bpy\n"
                    "for col in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.armatures, bpy.data.actions):\n"
                    "    for item in list(col):\n"
                    "        try:\n"
                    "            col.remove(item, do_unlink=True)\n"
                    "        except TypeError:\n"
                    "            try:\n"
                    "                col.remove(item)\n"
                    "            except Exception:\n"
                    "                pass\n"
                    "print('PURGED_OK')\n"
                )
                mgr.addon.execute_code(purge_code, timeout=10.0)
            except Exception:
                purged_ok = False
        checks["3_memory_datablocks_purged"] = purged_ok

        # 4-7. Gracefully stop managed Blender + Xvfb via BlenderLifecycleManager (NEVER pkill/killall)
        stop_res = mgr.stop_blender(reason="sequential_benchmark_reset", discard_session=True)
        # Ensure zero backup .blend files linger when config.json enables auto_restore_on_restart
        backups_dir = Path(mgr.cfg.backups_dir)
        cleared_backups: list[str] = []
        if not preserve_backups and backups_dir.is_dir():
            for bk in backups_dir.glob("*.blend*"):
                try:
                    bk.unlink()
                    cleared_backups.append(bk.name)
                except OSError:
                    pass
        checks["backups_cleared"] = cleared_backups
        post_stop_status = mgr.status(probe_live=True)
        old_pid_dead = not is_same_process(old_blender_rec) if old_blender_rec else not bool(post_stop_status.get("blender_alive"))

        checks["4_managed_blender_stopped"] = bool(stop_res.get("stopped")) or (not post_stop_status.get("blender_alive"))
        checks["5_mcp_session_closed"] = not bool(post_stop_status.get("addon_reachable"))
        checks["6_xvfb_cleaned_up"] = not bool(post_stop_status.get("xvfb_alive"))
        checks["7_old_pid_terminated"] = old_pid_dead

        # 8. GPU memory release check
        gpu_released = verify_gpu_released([old_pid] if old_pid else [], timeout_sec=5.0)
        gpu_after = get_gpu_snapshot()
        checks["8_gpu_memory_released"] = bool(gpu_released.get("released", True))

        # 9-10. Start fresh Blender process & verify completely clean empty scene
        new_pid = None
        new_boot_id = None
        new_scene_state: dict[str, Any] = {}
        residual_found: list[str] = []

        if start_fresh_blender and checks["4_managed_blender_stopped"] and checks["7_old_pid_terminated"]:
            ready_res = mgr.ensure_ready(reason="post_reset_fresh_session")
            new_pid = ready_res.get("pid")
            new_boot_id = ready_res.get("boot_id")
            checks["9_new_blender_started_fresh_pid"] = bool(new_pid) and (new_pid != old_pid) and (new_boot_id != old_boot_id)
            try:
                new_scene_state = mgr.addon.inspect_internal_state(timeout=8.0)
                # Also verify zero custom SM_Char_*, SM_Study_*, ARM_*, ACT_* datablocks remain
                probe_res = mgr.addon.execute_code(
                    "import bpy, json\n"
                    "names = [o.name for o in bpy.data.objects] + [m.name for m in bpy.data.meshes] + [a.name for a in bpy.data.armatures] + [act.name for act in bpy.data.actions]\n"
                    "print(json.dumps({'all_datablocks': names}))\n",
                    timeout=5.0,
                )
                out_line = (probe_res or {}).get("result", "").strip().splitlines()[-1]
                db_names = json.loads(out_line).get("all_datablocks", [])
                forbidden = set(forbidden_residual_objects or [])
                for nm in db_names:
                    if nm in forbidden or nm.startswith(("SM_Char_", "SM_Study_", "ARM_Character", "ACT_RunCycle")):
                        residual_found.append(nm)
                checks["10_clean_empty_scene_verified"] = len(residual_found) == 0
            except Exception:
                checks["10_clean_empty_scene_verified"] = False
        elif not start_fresh_blender:
            checks["9_new_blender_started_fresh_pid"] = True
            checks["10_clean_empty_scene_verified"] = True
        else:
            checks["9_new_blender_started_fresh_pid"] = False
            checks["10_clean_empty_scene_verified"] = False

        reset_finished = _utc_now_iso()
        all_passed = all(bool(v) for k, v in checks.items() if k[0].isdigit())

        return {
            "reset_passed": all_passed,
            "reset_started_at": reset_started,
            "reset_finished_at": reset_finished,
            "old_pid": old_pid,
            "old_boot_id": old_boot_id,
            "new_pid": new_pid,
            "new_boot_id": new_boot_id,
            "pre_reset_status": pre_status,
            "stop_result": stop_res,
            "post_stop_status": post_stop_status,
            "gpu_before": gpu_before,
            "gpu_after": gpu_after,
            "gpu_released": gpu_released,
            "new_scene_state": new_scene_state,
            "residual_objects_found": residual_found,
            "checks": checks,
        }

    def final_cleanup(self) -> dict[str, Any]:
        """Gracefully shut down managed Blender/Xvfb after Qwen finishes."""
        mgr = self._make_manager()
        st = mgr.status(probe_live=True)
        if not st.get("blender_alive") and not st.get("xvfb_alive"):
            return {"stopped": True, "already_stopped": True, "status": st}
        return mgr.stop_blender(reason="sequential_benchmark_complete", discard_session=True)


class SequentialBenchmarkOrchestrator:
    """Enforces sequential GPT -> Full Blender Reset -> Qwen execution with OS locking & isolation."""

    def __init__(
        self,
        *,
        output_root: Path | None = None,
        reports_dir: Path | None = None,
        runtime_dir: Path | None = None,
        resetter: BlenderSessionResetter | None = None,
    ) -> None:
        self.output_root = Path(output_root) if output_root is not None else (PROJECT_ROOT / "e2e_runs" / "sequential_benchmarks")
        self.reports_dir = Path(reports_dir) if reports_dir is not None else (PROJECT_ROOT / "reports")
        self.runtime_dir = Path(runtime_dir) if runtime_dir is not None else (self.output_root / "mcp_runtime")
        self.resetter = resetter or BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=self.runtime_dir)
        self.state = SequentialBenchmarkState.IDLE
        self.transitions: list[dict[str, Any]] = []
        self._lock_path = self.runtime_dir / "sequential_benchmark.lock"

    def _transition(self, target: str, reason: str) -> None:
        allowed = SequentialBenchmarkState.VALID_TRANSITIONS.get(self.state, ())
        if target not in allowed:
            raise RuntimeError(f"Illegal state transition {self.state} -> {target} ({reason})")
        self.transitions.append({
            "from": self.state,
            "to": target,
            "reason": reason,
            "timestamp": _utc_now_iso(),
        })
        self.state = target

    def run(
        self,
        *,
        gpt_runner_fn: Callable[[Path, BenchmarkIsolationGuard], dict[str, Any]],
        qwen_runner_fn: Callable[[Path, BenchmarkIsolationGuard], dict[str, Any]],
        compare_fn: Callable[[dict[str, Any], dict[str, Any], Path], dict[str, Any]] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Run GPT -> Full Blender Reset -> Qwen -> Cleanup -> Comparison under single-flight flock."""
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        lock_fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Concurrent sequential benchmark execution blocked by single-flight lock.") from exc

            ts_gpt = timestamp or datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            gpt_dir = self.output_root / f"{ts_gpt}_gpt"
            gpt_dir.mkdir(parents=True, exist_ok=True)
            gpt_guard = BenchmarkIsolationGuard(run_kind="INDEPENDENT_BENCHMARK", workspace_dir=gpt_dir)

            # Phase A: GPT
            self._transition(SequentialBenchmarkState.GPT_RUNNING, "Start Phase A GPT benchmark")
            gpt_started_at = _utc_now_iso()
            gpt_pre_status = self.resetter.inspect_status()
            gpt_result = dict(gpt_runner_fn(gpt_dir, gpt_guard) or {})
            gpt_finished_at = _utc_now_iso()
            gpt_post_status = self.resetter.inspect_status()
            self._transition(SequentialBenchmarkState.GPT_FINISHED, "Phase A GPT finished")

            # Phase B: Full Blender Reset
            self._transition(SequentialBenchmarkState.RESETTING, "Start Phase B Blender lifecycle reset")
            expected_files = [gpt_dir / "execution_trace.json"]
            forbidden_objs = [
                m.get("name", "")
                for m in (gpt_result.get("audit_latest") or {}).get("meshes", [])
                if m.get("name")
            ]
            reset_report = self.resetter.reset_for_next_model(
                expected_saved_files=expected_files,
                forbidden_residual_objects=forbidden_objs,
                start_fresh_blender=True,
            )

            if not reset_report.get("reset_passed"):
                self._transition(SequentialBenchmarkState.BLOCKED_RESET_FAILED, "Phase B reset verification failed; Qwen blocked")
                isolation_doc = self._build_isolation_report(
                    gpt_started_at=gpt_started_at,
                    gpt_finished_at=gpt_finished_at,
                    gpt_pre_status=gpt_pre_status,
                    gpt_post_status=gpt_post_status,
                    gpt_result=gpt_result,
                    reset_report=reset_report,
                    qwen_started_at=None,
                    qwen_finished_at=None,
                    qwen_result=None,
                    final_cleanup_report=None,
                    verdict="FAIL",
                )
                (self.reports_dir / "session_isolation.json").write_text(json.dumps(isolation_doc, indent=2) + "\n", encoding="utf-8")
                return {
                    "state": self.state,
                    "blocked_reason": "BLOCKED_RESET_FAILED",
                    "transitions": self.transitions,
                    "session_isolation": isolation_doc,
                    "gpt_result": gpt_result,
                    "qwen_result": None,
                }

            self._transition(SequentialBenchmarkState.RESET_VERIFIED, "Phase B reset verified clean")

            # Phase C: Qwen (only after RESET_VERIFIED!)
            ts_qwen = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            if ts_qwen == ts_gpt:
                # Ensure distinct directory timestamp
                ts_qwen = f"{ts_gpt[:-1]}{(int(ts_gpt[-1]) + 1) % 10}"
            qwen_dir = self.output_root / f"{ts_qwen}_qwen"
            qwen_dir.mkdir(parents=True, exist_ok=True)
            qwen_guard = BenchmarkIsolationGuard(run_kind="INDEPENDENT_BENCHMARK", workspace_dir=qwen_dir)

            self._transition(SequentialBenchmarkState.QWEN_RUNNING, "Start Phase C Qwen benchmark")
            qwen_started_at = _utc_now_iso()
            qwen_result = dict(qwen_runner_fn(qwen_dir, qwen_guard) or {})
            qwen_finished_at = _utc_now_iso()
            self._transition(SequentialBenchmarkState.QWEN_FINISHED, "Phase C Qwen finished")

            # Phase D: Cleanup & Comparison
            self._transition(SequentialBenchmarkState.CLEANUP, "Stop Qwen Blender session cleanly")
            final_cleanup_report = self.resetter.final_cleanup()

            self._transition(SequentialBenchmarkState.COMPARISON, "Run read-only GPT vs Qwen comparison")
            comp_report = compare_fn(gpt_result, qwen_result, self.reports_dir) if compare_fn else self._default_comparison(gpt_result, qwen_result)

            overlap = qwen_started_at <= gpt_finished_at
            cross_ref_detected = bool(qwen_result.get("cross_referenced_gpt"))
            isolation_verdict = "PASS" if (reset_report["reset_passed"] and not overlap and not cross_ref_detected) else "FAIL"

            isolation_doc = self._build_isolation_report(
                gpt_started_at=gpt_started_at,
                gpt_finished_at=gpt_finished_at,
                gpt_pre_status=gpt_pre_status,
                gpt_post_status=gpt_post_status,
                gpt_result=gpt_result,
                reset_report=reset_report,
                qwen_started_at=qwen_started_at,
                qwen_finished_at=qwen_finished_at,
                qwen_result=qwen_result,
                final_cleanup_report=final_cleanup_report,
                verdict=isolation_verdict,
            )
            (self.reports_dir / "session_isolation.json").write_text(json.dumps(isolation_doc, indent=2) + "\n", encoding="utf-8")

            self._transition(SequentialBenchmarkState.COMPLETE, "Sequential benchmark & comparison complete")
            return {
                "state": self.state,
                "transitions": self.transitions,
                "gpt_dir": str(gpt_dir),
                "qwen_dir": str(qwen_dir),
                "gpt_result": gpt_result,
                "qwen_result": qwen_result,
                "session_isolation": isolation_doc,
                "comparison": comp_report,
            }
        finally:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)

    def _build_isolation_report(
        self,
        *,
        gpt_started_at: str,
        gpt_finished_at: str,
        gpt_pre_status: dict[str, Any],
        gpt_post_status: dict[str, Any],
        gpt_result: dict[str, Any],
        reset_report: dict[str, Any],
        qwen_started_at: str | None,
        qwen_finished_at: str | None,
        qwen_result: dict[str, Any] | None,
        final_cleanup_report: dict[str, Any] | None,
        verdict: str,
    ) -> dict[str, Any]:
        overlap = bool(qwen_started_at and qwen_started_at <= gpt_finished_at)
        return {
            "gpt_started_at": gpt_started_at,
            "gpt_finished_at": gpt_finished_at,
            "gpt_blender_pid": reset_report.get("old_pid") or gpt_post_status.get("blender_pid"),
            "gpt_session_id": gpt_result.get("producer_session_id") or reset_report.get("old_boot_id"),
            "gpt_outputs_saved": bool(reset_report.get("checks", {}).get("1_prior_outputs_saved")),
            "reset_started_at": reset_report.get("reset_started_at"),
            "reset_finished_at": reset_report.get("reset_finished_at"),
            "process_state_before_reset": {
                "blender_pid": reset_report.get("old_pid"),
                "boot_id": reset_report.get("old_boot_id"),
                "pre_status_state": gpt_pre_status.get("state"),
                "post_gpt_state": gpt_post_status.get("state"),
            },
            "process_state_after_reset": {
                "stop_result": reset_report.get("stop_result"),
                "post_stop_status": reset_report.get("post_stop_status"),
                "new_scene_state": reset_report.get("new_scene_state"),
                "residual_objects_found": reset_report.get("residual_objects_found"),
            },
            "managed_process_and_xvfb_cleanup": {
                "managed_blender_stopped": reset_report.get("checks", {}).get("4_managed_blender_stopped"),
                "xvfb_cleaned_up": reset_report.get("checks", {}).get("6_xvfb_cleaned_up"),
                "old_pid_terminated": reset_report.get("checks", {}).get("7_old_pid_terminated"),
                "final_cleanup_after_qwen": final_cleanup_report,
            },
            "gpu_before_reset": reset_report.get("gpu_before"),
            "gpu_after_reset": reset_report.get("gpu_after"),
            "gpu_released": reset_report.get("gpu_released"),
            "qwen_blender_pid": reset_report.get("new_pid"),
            "qwen_session_id": (qwen_result or {}).get("producer_session_id") or reset_report.get("new_boot_id"),
            "qwen_started_at": qwen_started_at,
            "qwen_finished_at": qwen_finished_at,
            "execution_windows_overlapped": overlap,
            "cross_reference_between_models": bool((qwen_result or {}).get("cross_referenced_gpt")),
            "reset_checks": reset_report.get("checks"),
            "isolation_verdict": verdict,
        }

    def _default_comparison(self, gpt_res: dict[str, Any], qwen_res: dict[str, Any]) -> dict[str, Any]:
        """Generate read-only GPT vs Qwen comparison markdown & JSON summary."""
        def _row(name: str, res: dict[str, Any]) -> dict[str, Any]:
            verdicts = dict(res.get("verdicts") or {})
            domains = dict(res.get("domain_verdicts") or {})
            metrics = dict(res.get("metrics") or {})
            repair = dict(res.get("repair_loop") or {})
            return {
                "model": res.get("model_id", name),
                "face_and_body_sculpt_quality": domains.get("face_and_body_sculpt_quality", "NOT_VERIFIED"),
                "identity_or_reference_fidelity": domains.get("identity_or_reference_fidelity", "NOT_VERIFIED"),
                "rigging_and_deformation_quality": domains.get("rigging_and_deformation_quality", "NOT_VERIFIED"),
                "animation_quality": domains.get("animation_quality", "NOT_VERIFIED"),
                "qa_failures": metrics.get("qa_failure_count", 0),
                "fix_iterations": repair.get("fix_iteration_count", metrics.get("modeling_iteration_count", 0)),
                "final_verdict": verdicts.get("final_asset_production", "NOT_VERIFIED"),
                "blender_errors": metrics.get("blender_error_count", 0),
                "compactions": metrics.get("compaction_events", "unavailable"),
                "runtime_sec": metrics.get("total_runtime_sec", 0.0),
                "approved_export_count": len(res.get("approved_artifacts") or []),
                "diagnostic_checkpoint_count": len(res.get("diagnostic_artifacts") or []),
            }

        row_g = _row("GPT", gpt_res)
        row_q = _row("Qwen", qwen_res)

        md = "\n".join([
            "# Sequential GPT vs Qwen Character Benchmark Comparison",
            "",
            "| Dimension | GPT | Qwen |",
            "|---|---|---|",
            f"| Model ID | `{row_g['model']}` | `{row_q['model']}` |",
            f"| Face & Body Sculpt Quality | {row_g['face_and_body_sculpt_quality']} | {row_q['face_and_body_sculpt_quality']} |",
            f"| Reference / Identity Fidelity | {row_g['identity_or_reference_fidelity']} | {row_q['identity_or_reference_fidelity']} |",
            f"| Rigging & Deformation | {row_g['rigging_and_deformation_quality']} | {row_q['rigging_and_deformation_quality']} |",
            f"| Animation Quality | {row_g['animation_quality']} | {row_q['animation_quality']} |",
            f"| Visual QA Failures | {row_g['qa_failures']} | {row_q['qa_failures']} |",
            f"| Structural Fix Iterations | {row_g['fix_iterations']} | {row_q['fix_iterations']} |",
            f"| Final Production Verdict | **{row_g['final_verdict']}** | **{row_q['final_verdict']}** |",
            f"| Blender Errors / Compactions | {row_g['blender_errors']} / {row_g['compactions']} | {row_q['blender_errors']} / {row_q['compactions']} |",
            f"| Total Runtime (sec) | {row_g['runtime_sec']} | {row_q['runtime_sec']} |",
            f"| Approved Releases / Diagnostic Checkpoints | {row_g['approved_export_count']} / {row_g['diagnostic_checkpoint_count']} | {row_q['approved_export_count']} / {row_q['diagnostic_checkpoint_count']} |",
            "",
        ])
        (self.reports_dir / "gpt_vs_qwen_comparison.md").write_text(md, encoding="utf-8")
        return {"gpt": row_g, "qwen": row_q, "markdown_path": str(self.reports_dir / "gpt_vs_qwen_comparison.md")}
