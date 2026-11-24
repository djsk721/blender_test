#!/usr/bin/env python3
"""Lazy-loading MCP stdio wrapper for Blender MCP.

Protects MCP JSON-RPC stdio framing on stdout while delegating MCP requests to
`mcp-for-blender` and lazily launching/recovering Blender only for tool/resource
calls that actually require Blender.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
from typing import Any

# Capture raw binary stdout for MCP JSON-RPC protocol ONLY, and divert any stray
# Python `print()` calls before importing anything else.
_PROTO_OUT = sys.stdout.buffer
sys.stdout = open(os.devnull, "w", encoding="utf-8")

# Add `<project_root>/.opencode/mcp` to sys.path so `blender.*` imports cleanly.
_MCP_DIR = str(Path(__file__).resolve().parents[1])
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)

from blender.config_loader import BlenderMCPConfig, load_config, resolve_executable, setup_logger
from blender.lifecycle_manager import BlenderLifecycleManager, JobMarker


_CONNECTION_LOST_MARKERS = (
    "connection to blender lost",
    "could not connect to blender",
    "not connected to blender",
    "connection closed before receiving any data",
    "broken pipe",
    "connection reset",
    "communication error with blender",
)


def _is_connection_error_response(resp: dict[str, Any]) -> bool:
    if not isinstance(resp, dict):
        return False
    if "error" in resp and isinstance(resp["error"], dict):
        msg = str(resp["error"].get("message", "")).lower()
        if any(marker in msg for marker in _CONNECTION_LOST_MARKERS):
            return True
    result = resp.get("result")
    if isinstance(result, dict):
        content = result.get("content") or []
        text = " ".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        ).lower()
        if any(marker in text for marker in _CONNECTION_LOST_MARKERS):
            return True
    return False


class UpstreamMCPBridge:
    """Manages the upstream `mcp-for-blender` stdio subprocess and transparent restarts."""

    def __init__(
        self,
        cfg: BlenderMCPConfig,
        on_upstream_message,
        logger,
    ) -> None:
        self.cfg = cfg
        self.on_upstream_message = on_upstream_message
        self.logger = logger
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr_fh = None
        self._reader_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending: dict[Any, queue.Queue[dict[str, Any]]] = {}
        self._internal_seq = 0
        self.bound_boot_id: str | None = None
        self.cached_initialize_req: dict[str, Any] | None = None
        self.cached_initialized_notif: dict[str, Any] | None = None

    def _resolve_upstream_cmd(self) -> list[str]:
        cmd = list(self.cfg.upstream_mcp_command)
        if not cmd:
            raise RuntimeError("upstream_mcp_command is empty")
        cmd[0] = resolve_executable(cmd[0], "auto", project_root=self.cfg.project_root)
        return cmd

    def _spawn(self) -> None:
        cmd = self._resolve_upstream_cmd()
        env = os.environ.copy()
        env["BLENDER_HOST"] = self.cfg.blender_host
        env["BLENDER_PORT"] = str(self.cfg.blender_port)
        env["PYTHONUNBUFFERED"] = "1"
        self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        self._stderr_fh = (self.cfg.logs_dir / "upstream_mcp.log").open("ab")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_fh,
            env=env,
            cwd=str(self.cfg.project_root),
            bufsize=0,
        )
        self.bound_boot_id = None
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            args=(self._proc,),
            daemon=True,
        )
        self._reader_thread.start()
        self.logger.info("Started upstream MCP subprocess pid=%d cmd=%s", self._proc.pid, cmd)

    def ensure_started(self) -> None:
        with self._lifecycle_lock:
            if self._proc is None or self._proc.poll() is not None:
                self._stop_locked()
                self._spawn()

    def _stop_locked(self) -> None:
        proc = self._proc
        self._proc = None
        self.bound_boot_id = None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
        if self._stderr_fh is not None:
            try:
                self._stderr_fh.close()
            except OSError:
                pass
            self._stderr_fh = None
        with self._pending_lock:
            pending = list(self._pending.items())
            self._pending.clear()
        for req_id, q in pending:
            q.put(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32001,
                        "message": "Upstream mcp-for-blender process exited unexpectedly.",
                    },
                }
            )

    def close(self) -> None:
        with self._lifecycle_lock:
            self._stop_locked()

    def _read_loop(self, proc: subprocess.Popen[bytes]) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        try:
            for raw_line in stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self.logger.warning("Ignoring non-JSON line from upstream: %r", line[:200])
                    continue
                if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                    req_id = msg["id"]
                    with self._pending_lock:
                        waiter = self._pending.pop(req_id, None)
                    if waiter is not None:
                        waiter.put(msg)
                        continue
                self.on_upstream_message(msg)
        except Exception as exc:
            self.logger.debug("Upstream stdout reader ended: %s", exc)
        finally:
            with self._pending_lock:
                pending = list(self._pending.items())
                self._pending.clear()
            for req_id, q in pending:
                q.put(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32001,
                            "message": "Connection to Blender lost: upstream MCP stdout closed.",
                        },
                    }
                )

    def _write_message(self, msg: dict[str, Any]) -> None:
        self.ensure_started()
        assert self._proc is not None and self._proc.stdin is not None
        encoded = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._write_lock:
            self._proc.stdin.write(encoded)
            self._proc.stdin.flush()

    def send_notification(self, msg: dict[str, Any]) -> None:
        self._write_message(msg)

    def send_request(self, msg: dict[str, Any], timeout: float = 240.0) -> dict[str, Any]:
        req_id = msg["id"]
        waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[req_id] = waiter
        try:
            self._write_message(msg)
            return waiter.get(timeout=timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32002,
                    "message": f"Timed out waiting {timeout:.1f}s for upstream mcp-for-blender response.",
                },
            }

    def recycle_and_reinitialize(self, boot_id: str | None) -> None:
        """Restart upstream `mcp-for-blender` so it drops any stale Blender TCP socket."""
        with self._lifecycle_lock:
            self.logger.info(
                "Recycling upstream MCP subprocess (old boot_id=%s -> new boot_id=%s)",
                self.bound_boot_id,
                boot_id,
            )
            self._stop_locked()
            self._spawn()
            if self.cached_initialize_req is not None:
                self._internal_seq += 1
                init_id = f"__wrapper_reinit_{self._internal_seq}"
                reinit_msg = {
                    "jsonrpc": "2.0",
                    "id": init_id,
                    "method": "initialize",
                    "params": self.cached_initialize_req.get("params", {}),
                }
                resp = self.send_request(reinit_msg, timeout=30.0)
                if "error" in resp:
                    raise RuntimeError(f"Failed re-initializing recycled upstream MCP: {resp['error']}")
                if self.cached_initialized_notif is not None:
                    self.send_notification(self.cached_initialized_notif)
                else:
                    self.send_notification({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.bound_boot_id = boot_id


class BlenderMCPWrapper:
    """Stdio MCP proxy enforcing lazy loading, locking, GPU checks, and crash recovery."""

    def __init__(self, cfg: BlenderMCPConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self.logger = setup_logger(
            "blender_mcp_wrapper",
            self.cfg.logs_dir / "wrapper.log",
            self.cfg.log_level,
        )
        self.lifecycle = BlenderLifecycleManager(self.cfg)
        self._stdout_lock = threading.Lock()
        self._upstream = UpstreamMCPBridge(
            cfg=self.cfg,
            on_upstream_message=self._write_client_message,
            logger=self.logger,
        )
        self._stop_event = threading.Event()
        self._non_blender_tools = set(self.cfg.non_blender_tools)
        self._active_threads: set[threading.Thread] = set()
        self._threads_lock = threading.Lock()

    def _write_client_message(self, msg: dict[str, Any]) -> None:
        payload = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self._stdout_lock:
            try:
                _PROTO_OUT.write(payload)
                _PROTO_OUT.flush()
            except BrokenPipeError:
                self._stop_event.set()

    def _tool_requires_blender(self, tool_name: str) -> bool:
        return tool_name not in self._non_blender_tools

    def _resource_requires_blender(self, uri: str) -> bool:
        return uri.startswith("blender://")

    def _handle_blender_backed_request(
        self,
        msg: dict[str, Any],
        operation_label: str,
    ) -> dict[str, Any]:
        req_id = msg["id"]
        job_id = str(req_id)
        try:
            with JobMarker(self.cfg.pending_jobs_dir, job_id=job_id, tool_name=operation_label, kind="pending"):
                with self.lifecycle.work_lock(self.cfg.work_lock_timeout_sec):
                    work_started = False
                    try:
                        with JobMarker(self.cfg.active_jobs_dir, job_id=job_id, tool_name=operation_label, kind="active"):
                            ready_info = self.lifecycle.ensure_ready(reason=operation_label)
                            boot_id = ready_info.get("boot_id")
                            self.lifecycle.begin_work(job_id=job_id, tool_name=operation_label)
                            work_started = True
                            if (
                                self._upstream._proc is None
                                or self._upstream._proc.poll() is not None
                                or (
                                    self._upstream.bound_boot_id is not None
                                    and self._upstream.bound_boot_id != boot_id
                                )
                            ):
                                self._upstream.recycle_and_reinitialize(boot_id)
                            elif self._upstream.bound_boot_id is None:
                                self._upstream.bound_boot_id = boot_id

                            attempts = 0
                            max_attempts = 1 + (max(0, self.cfg.max_reconnect_retries) if self.cfg.auto_recovery else 0)
                            while True:
                                attempts += 1
                                resp = self._upstream.send_request(msg, timeout=240.0)
                                if (
                                    self.cfg.auto_recovery
                                    and attempts < max_attempts
                                    and _is_connection_error_response(resp)
                                ):
                                    self.logger.warning(
                                        "Detected Blender connection failure during %s (attempt %d/%d); recovering...",
                                        operation_label,
                                        attempts,
                                        max_attempts,
                                    )
                                    rec_info = self.lifecycle.ensure_ready(
                                        reason=f"recover:{operation_label}",
                                        force_check=True,
                                    )
                                    self._upstream.recycle_and_reinitialize(rec_info.get("boot_id"))
                                    continue
                                return resp
                    finally:
                        if work_started:
                            self.lifecycle.finish_work(job_id=job_id, tool_name=operation_label)
        except Exception as exc:
            self.logger.error("Blender operation %s failed: %s", operation_label, exc)
            if msg.get("method") == "tools/call":
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Blender MCP wrapper error during {operation_label}: {exc}",
                            }
                        ],
                        "isError": True,
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }

    def _dispatch_request(self, msg: dict[str, Any]) -> None:
        method = msg.get("method", "")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        try:
            if method == "initialize":
                self._upstream.cached_initialize_req = msg
                resp = self._upstream.send_request(msg, timeout=60.0)
                self._write_client_message(resp)
                return

            if method == "tools/call":
                tool_name = str(params.get("name", ""))
                if self.cfg.lazy_loading and not self._tool_requires_blender(tool_name):
                    resp = self._upstream.send_request(msg, timeout=120.0)
                    self._write_client_message(resp)
                    return
                resp = self._handle_blender_backed_request(msg, f"tool:{tool_name}")
                self._write_client_message(resp)
                return

            if method == "resources/read":
                uri = str(params.get("uri", ""))
                if self.cfg.lazy_loading and not self._resource_requires_blender(uri):
                    resp = self._upstream.send_request(msg, timeout=60.0)
                    self._write_client_message(resp)
                    return
                resp = self._handle_blender_backed_request(msg, f"resource:{uri}")
                self._write_client_message(resp)
                return

            resp = self._upstream.send_request(msg, timeout=60.0)
            self._write_client_message(resp)
        except Exception as exc:
            self.logger.exception("Unhandled error dispatching %s: %s", method, exc)
            if "id" in msg:
                self._write_client_message(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )

    def _handle_incoming_message(self, msg: dict[str, Any]) -> None:
        method = msg.get("method")
        if "id" not in msg:
            if method == "notifications/initialized":
                self._upstream.cached_initialized_notif = msg
            try:
                self._upstream.send_notification(msg)
            except Exception as exc:
                self.logger.warning("Failed forwarding notification %s: %s", method, exc)
            return

        if method == "initialize":
            self._dispatch_request(msg)
            return

        def runner() -> None:
            try:
                self._dispatch_request(msg)
            finally:
                with self._threads_lock:
                    self._active_threads.discard(threading.current_thread())

        t = threading.Thread(target=runner, daemon=True)
        with self._threads_lock:
            self._active_threads.add(t)
        t.start()

    def run(self) -> int:
        self.logger.info("BlenderMCPWrapper starting (lazy_loading=%s)", self.cfg.lazy_loading)
        self._upstream.ensure_started()

        if not self.cfg.lazy_loading:
            try:
                ready = self.lifecycle.ensure_ready(reason="eager_startup")
                self._upstream.bound_boot_id = ready.get("boot_id")
            except Exception as exc:
                self.logger.error("Eager Blender startup failed: %s", exc)

        try:
            while not self._stop_event.is_set():
                raw_line = sys.stdin.buffer.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    self.logger.warning("Invalid JSON from OpenCode: %s", exc)
                    continue
                if isinstance(msg, dict):
                    self._handle_incoming_message(msg)
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        self._stop_event.set()
        self._upstream.close()
        self.logger.info("BlenderMCPWrapper exited cleanly (managed Blender left untouched for idle watchdog).")


def main() -> int:
    wrapper = BlenderMCPWrapper()

    def _on_signal(signum: int, _frame: Any) -> None:
        wrapper.logger.info("Received signal %d; closing wrapper stdio bridge.", signum)
        wrapper._stop_event.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    return wrapper.run()


if __name__ == "__main__":
    raise SystemExit(main())
