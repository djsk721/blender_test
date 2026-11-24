"""Unit tests for Blender MCP lazy loading, lifecycle manager, GPU safety, and wrapper."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_DIR = PROJECT_ROOT / ".opencode" / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from blender.config_loader import load_config, resolve_executable
from blender.gpu_manager import get_gpu_snapshot, get_pid_gpu_usage, select_launch_gpu_config
from blender.lifecycle_manager import (
    BlenderLifecycleManager,
    JobMarker,
    LifecycleState,
    _verify_blend_file,
    list_live_markers,
)
from blender.mcp_wrapper import BlenderMCPWrapper, _is_connection_error_response
from blender.process_manager import ProcessRecord, get_proc_start_time, is_same_process


class TestConfigAndResolution(unittest.TestCase):
    def test_project_root_and_default_runtime_under_dot_opencode(self) -> None:
        old_env = os.environ.copy()
        try:
            os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)
            os.environ.pop("BLENDER_MCP_PROJECT_ROOT", None)
            cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
            self.assertEqual(cfg.project_root.resolve(), PROJECT_ROOT.resolve())
            self.assertEqual(
                cfg.runtime_dir.resolve(),
                (PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "runtime").resolve(),
            )
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_defaults_and_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_env = os.environ.copy()
            try:
                os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
                os.environ["BLENDER_MCP_IDLE_TIMEOUT"] = "15"
                os.environ["BLENDER_MCP_STARTUP_TIMEOUT"] = "45"
                os.environ["BLENDER_MCP_SHUTDOWN_TIMEOUT"] = "12"
                os.environ["BLENDER_RENDER_DEVICE"] = "CPU"
                os.environ["BLENDER_PORT"] = "9888"
                os.environ["BLENDER_GPU_UNHEALTHY_POLICY"] = "fail"
                os.environ["BLENDER_GPU_MAX_TEMP_C"] = "82"
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                self.assertTrue(cfg.lazy_loading)
                self.assertEqual(cfg.idle_timeout_sec, 15.0)
                self.assertEqual(cfg.startup_timeout_sec, 45.0)
                self.assertEqual(cfg.shutdown_timeout_sec, 12.0)
                self.assertEqual(cfg.gpu.render_device, "CPU")
                self.assertEqual(cfg.gpu.unhealthy_gpu_policy, "fail")
                self.assertEqual(cfg.gpu.max_safe_temp_c, 82)
                self.assertEqual(cfg.blender_port, 9888)
                self.assertEqual(cfg.runtime_dir, Path(tmp).resolve())
            finally:
                os.environ.clear()
                os.environ.update(old_env)

    def test_resolve_executable_python3(self) -> None:
        resolved = resolve_executable("python3", "auto")
        self.assertTrue(Path(resolved).exists())


class TestGPUPolicyAndSafety(unittest.TestCase):
    def _mock_snapshot(self, free0: int, free1: int, temp0: int = 45, temp1: int = 50) -> dict:
        return {
            "timestamp": 1.0,
            "nvidia_smi_available": True,
            "gpus": [
                {
                    "index": 0,
                    "name": "RTX 3090",
                    "uuid": "GPU-0",
                    "memory_total_mib": 24576,
                    "memory_used_mib": 24576 - free0,
                    "memory_free_mib": free0,
                    "utilization_gpu_pct": 0,
                    "utilization_memory_pct": 0,
                    "temperature_c": temp0,
                    "pstate": "P8",
                    "healthy": True,
                    "health_issues": [],
                },
                {
                    "index": 1,
                    "name": "RTX 3090",
                    "uuid": "GPU-1",
                    "memory_total_mib": 24576,
                    "memory_used_mib": 24576 - free1,
                    "memory_free_mib": free1,
                    "utilization_gpu_pct": 0,
                    "utilization_memory_pct": 0,
                    "temperature_c": temp1,
                    "pstate": "P8",
                    "healthy": True,
                    "health_issues": [],
                },
            ],
            "apps": [
                {"pid": 3544125, "process_name": "strata", "used_memory_mib": 21920, "gpu_index": 0},
            ],
            "health": {
                "status": "healthy",
                "reasons": [],
                "proc_nvidia_gpu_count": 2,
                "reported_gpu_count": 2,
                "healthy_gpu_count": 2,
            },
        }

    def test_auto_picks_gpu_with_most_free_vram_and_records_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                cfg.gpu.render_device = "AUTO"
                cfg.gpu.preferred_gpu_index = "auto"
                plan = select_launch_gpu_config(cfg, snapshot=self._mock_snapshot(1800, 3500))
                self.assertTrue(plan.ok)
                self.assertEqual(plan.effective_device, "GPU")
                self.assertEqual(plan.selected_gpu_index, 1)
                self.assertEqual(plan.env_overrides.get("CUDA_VISIBLE_DEVICES"), "1")
                self.assertEqual(plan.decision["health_status"], "healthy")
                self.assertFalse(plan.decision["fallback_triggered"])
                self.assertIn("CUDA_VISIBLE_DEVICES", plan.decision["isolation_notes"])
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)

    def test_vram_low_policies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                cfg.gpu.min_free_vram_error_mib = 512
                snap = self._mock_snapshot(100, 200)

                cfg.gpu.vram_low_policy = "fail"
                plan_fail = select_launch_gpu_config(cfg, snapshot=snap)
                self.assertFalse(plan_fail.ok)
                self.assertIn("Insufficient free VRAM", plan_fail.error or "")

                cfg.gpu.vram_low_policy = "cpu_fallback"
                plan_cpu = select_launch_gpu_config(cfg, snapshot=snap)
                self.assertTrue(plan_cpu.ok)
                self.assertEqual(plan_cpu.effective_device, "CPU")
                self.assertEqual(plan_cpu.env_overrides.get("CUDA_VISIBLE_DEVICES"), "")
                self.assertTrue(plan_cpu.decision["fallback_triggered"])

                cfg.gpu.vram_low_policy = "warn"
                plan_warn = select_launch_gpu_config(cfg, snapshot=snap)
                self.assertTrue(plan_warn.ok)
                self.assertEqual(plan_warn.effective_device, "GPU")
                self.assertTrue(len(plan_warn.warnings) >= 1)
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)

    def test_unhealthy_gpu_nv_err_gpu_is_lost_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                with patch("blender.gpu_manager.shutil.which", return_value="/usr/bin/nvidia-smi"), patch(
                    "blender.gpu_manager._count_proc_nvidia_gpus", return_value=2
                ), patch(
                    "blender.gpu_manager.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=[],
                        returncode=1,
                        stdout="Unable to determine the device handle for GPU0000:1A:00.0: Unknown Error (NV_ERR_GPU_IS_LOST)\n",
                        stderr="",
                    ),
                ):
                    snap = get_gpu_snapshot()
                self.assertFalse(snap["nvidia_smi_available"])
                self.assertEqual(snap["health"]["status"], "unhealthy")

                cfg.gpu.render_device = "AUTO"
                cfg.gpu.unhealthy_gpu_policy = "cpu_fallback"
                plan = select_launch_gpu_config(cfg, snapshot=snap)
                self.assertTrue(plan.ok)
                self.assertEqual(plan.effective_device, "CPU")
                self.assertEqual(plan.env_overrides.get("CUDA_VISIBLE_DEVICES"), "")
                self.assertEqual(plan.env_overrides.get("LIBGL_ALWAYS_SOFTWARE"), "1")
                self.assertTrue(plan.decision["fallback_triggered"])

                cfg.gpu.unhealthy_gpu_policy = "fail"
                plan_fail = select_launch_gpu_config(cfg, snapshot=snap)
                self.assertFalse(plan_fail.ok)
                self.assertIn("unhealthy", plan_fail.error or "")
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)

    def test_proc_gpu_count_mismatch_marks_degraded_and_blocks_gpu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                row = "0, NVIDIA GeForce RTX 3090, GPU-0, 595.91.07, 0000:1A:00.0, 24576, 1000, 23576, 0, 0, 50, P8\n"
                with patch("blender.gpu_manager.shutil.which", return_value="/usr/bin/nvidia-smi"), patch(
                    "blender.gpu_manager._count_proc_nvidia_gpus", return_value=2
                ), patch(
                    "blender.gpu_manager.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, stdout=row, stderr=""),
                        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                    ],
                ):
                    snap = get_gpu_snapshot()
                self.assertEqual(snap["health"]["status"], "degraded")
                self.assertEqual(snap["health"]["proc_nvidia_gpu_count"], 2)
                self.assertEqual(snap["health"]["reported_gpu_count"], 1)

                cfg.gpu.render_device = "AUTO"
                cfg.gpu.unhealthy_gpu_policy = "cpu_fallback"
                plan = select_launch_gpu_config(cfg, snapshot=snap)
                self.assertTrue(plan.ok)
                self.assertEqual(plan.effective_device, "CPU")
                self.assertTrue(plan.decision["fallback_triggered"])
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)

    def test_nvidia_smi_timeout_treated_as_unhealthy(self) -> None:
        with patch("blender.gpu_manager.shutil.which", return_value="/usr/bin/nvidia-smi"), patch(
            "blender.gpu_manager.subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 5.0)
        ):
            snap = get_gpu_snapshot()
        self.assertEqual(snap["health"]["status"], "unhealthy")
        self.assertIn("timed out", snap["health"]["reasons"][0])

    def test_over_temperature_gpu_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                cfg.gpu.max_safe_temp_c = 85
                snap = self._mock_snapshot(5000, 2000, temp0=91, temp1=60)
                plan = select_launch_gpu_config(cfg, snapshot=snap)
                self.assertTrue(plan.ok)
                self.assertEqual(plan.effective_device, "GPU")
                self.assertEqual(plan.selected_gpu_index, 1)
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)

    def test_cpu_mode_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                cfg.gpu.render_device = "CPU"
                plan = select_launch_gpu_config(cfg, snapshot=self._mock_snapshot(5000, 5000))
                self.assertTrue(plan.ok)
                self.assertEqual(plan.effective_device, "CPU")
                self.assertEqual(plan.env_overrides.get("CUDA_VISIBLE_DEVICES"), "")
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)

    def test_pid_gpu_usage_filter(self) -> None:
        usage = get_pid_gpu_usage([3544125], snapshot=self._mock_snapshot(2000, 3000))
        self.assertEqual(usage["total_mib"], 21920)
        usage_none = get_pid_gpu_usage([99999999], snapshot=self._mock_snapshot(2000, 3000))
        self.assertEqual(usage_none["total_mib"], 0)


class TestLifecyclePrimitives(unittest.TestCase):
    def test_seven_states_defined(self) -> None:
        expected = {"STOPPED", "STARTING", "READY", "BUSY", "IDLE", "STOPPING", "ERROR"}
        self.assertEqual({s.value for s in LifecycleState}, expected)

    def test_process_identity_self_and_mismatch(self) -> None:
        pid = os.getpid()
        st = get_proc_start_time(pid)
        self.assertIsNotNone(st)
        rec = ProcessRecord(pid=pid, pgid=os.getpgid(pid), start_time=st or 0)
        self.assertTrue(is_same_process(rec))
        wrong = ProcessRecord(pid=pid, pgid=os.getpgid(pid), start_time=(st or 0) + 999)
        self.assertFalse(is_same_process(wrong))

    def test_verify_blend_headers_and_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for idx, sig in enumerate((b"BLENDER-v503", b"\x1f\x8b\x08\x00abc", b"\x28\xb5\x2f\xfdxyz")):
                p = root / f"ok_{idx}.blend"
                p.write_bytes(sig + b"payload")
                ok, reason, meta = _verify_blend_file(p)
                self.assertTrue(ok, reason)
                ok2, reason2, _ = _verify_blend_file(p, expected_sha256=meta["sha256"])
                self.assertTrue(ok2, reason2)
                ok_bad_sha, _, _ = _verify_blend_file(p, expected_sha256="0" * 64)
                self.assertFalse(ok_bad_sha)

            bad = root / "bad.blend"
            bad.write_bytes(b"NOT_A_BLEND_FILE")
            ok_bad, _, _ = _verify_blend_file(bad)
            self.assertFalse(ok_bad)

    def test_job_marker_live_and_stale_reap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            jobs_dir = Path(tmp) / "jobs"
            jobs_dir.mkdir(parents=True)
            stale = jobs_dir / "999999_stale.json"
            stale.write_text('{"pid": 999999}', encoding="utf-8")
            with JobMarker(jobs_dir, "job1", "execute_blender_code") as _jm:
                live = list_live_markers(jobs_dir)
                self.assertEqual(len(live), 1)
                self.assertEqual(live[0]["job_id"], "job1")
                self.assertFalse(stale.exists())
            self.assertEqual(list_live_markers(jobs_dir), [])

    def test_status_never_launches_blender(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            os.environ["BLENDER_PORT"] = "19877"
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                mgr = BlenderLifecycleManager(cfg)
                st = mgr.status(probe_live=True)
                self.assertEqual(st["state"], "STOPPED")
                self.assertFalse(st["blender_alive"])
                self.assertFalse(st["addon_reachable"])
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)
                os.environ.pop("BLENDER_PORT", None)

    def test_wrapper_tool_and_error_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BLENDER_MCP_RUNTIME_DIR"] = tmp
            try:
                cfg = load_config(PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json")
                wrapper = BlenderMCPWrapper(cfg)
                self.assertFalse(wrapper._tool_requires_blender("open_viewport"))
                self.assertFalse(wrapper._tool_requires_blender("viewport_latest"))
                self.assertFalse(wrapper._tool_requires_blender("record_trajectory_feedback"))
                self.assertTrue(wrapper._tool_requires_blender("get_scene_info"))
                self.assertTrue(wrapper._tool_requires_blender("execute_blender_code"))
                self.assertTrue(wrapper._resource_requires_blender("blender://object/Cube"))
                self.assertFalse(wrapper._resource_requires_blender("ui://blender/viewport.html"))
                self.assertTrue(
                    _is_connection_error_response(
                        {
                            "result": {
                                "isError": True,
                                "content": [{"type": "text", "text": "Connection to Blender lost: EOF"}],
                            }
                        }
                    )
                )
                self.assertFalse(
                    _is_connection_error_response(
                        {"result": {"content": [{"type": "text", "text": "Cube created"}]}}
                    )
                )
            finally:
                os.environ.pop("BLENDER_MCP_RUNTIME_DIR", None)


if __name__ == "__main__":
    unittest.main()
