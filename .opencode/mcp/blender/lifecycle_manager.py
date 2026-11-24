"""Blender Lifecycle Manager: state machine, locks, addon client, backup/recovery, and watchdog."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from enum import Enum
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
from typing import Any, Iterator
import uuid

# Allow direct execution (`python3 .opencode/mcp/blender/lifecycle_manager.py ...`)
if __package__ in {None, ""}:
    _MCP_DIR = str(Path(__file__).resolve().parents[1])
    if _MCP_DIR not in sys.path:
        sys.path.insert(0, _MCP_DIR)
    __package__ = "blender"

from .config_loader import BlenderMCPConfig, load_config, setup_logger
from .gpu_manager import (
    get_gpu_snapshot,
    get_pid_gpu_usage,
    select_launch_gpu_config,
    verify_gpu_released,
)
from .process_manager import (
    ProcessRecord,
    cleanup_stale_pids_check,
    find_listening_pid_for_port,
    is_same_process,
    spawn_detached,
    start_blender,
    start_xvfb,
    stop_managed_processes,
)


class LifecycleState(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    READY = "READY"
    BUSY = "BUSY"
    IDLE = "IDLE"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


VALID_BLEND_HEADERS = (
    b"BLENDER",
    b"\x1f\x8b",
    b"\x28\xb5\x2f\xfd",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_blend_file(path: Path, expected_sha256: str | None = None) -> tuple[bool, str, dict[str, Any]]:
    if not path.is_file():
        return False, f"Backup file missing: {path}", {}
    try:
        size = path.stat().st_size
        if size <= 0:
            return False, f"Backup file is empty: {path}", {"size_bytes": size}
        with path.open("rb") as fh:
            header = fh.read(16)
        if not any(header.startswith(sig) for sig in VALID_BLEND_HEADERS):
            return (
                False,
                f"Invalid .blend header in {path}: {header[:8]!r}",
                {"size_bytes": size},
            )
        digest = _sha256_file(path)
        if expected_sha256 and digest != expected_sha256:
            return (
                False,
                f"Checksum mismatch for {path}: expected {expected_sha256}, got {digest}",
                {"size_bytes": size, "sha256": digest},
            )
        return True, "ok", {"size_bytes": size, "sha256": digest}
    except OSError as exc:
        return False, f"Failed reading backup file {path}: {exc}", {}


class AddonClient:
    """Lightweight TCP JSON client communicating with the Blender MCP addon."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def send(self, command_type: str, params: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
        payload = json.dumps({"type": command_type, "params": params or {}}).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect((self.host, self.port))
            sock.sendall(payload)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    if not chunks:
                        raise ConnectionError("Connection closed before receiving any data")
                    break
                chunks.append(chunk)
                raw = b"".join(chunks)
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                    break
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            else:  # pragma: no cover
                parsed = json.loads(b"".join(chunks).decode("utf-8"))

            if isinstance(parsed, dict) and parsed.get("status") == "error":
                raise RuntimeError(parsed.get("message", "Unknown Blender addon error"))
            if isinstance(parsed, dict):
                return parsed.get("result", {})
            return {}
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def ping(self, timeout: float = 2.0) -> bool:
        try:
            res = self.send("ping", timeout=timeout)
            return bool(isinstance(res, dict) and res.get("pong") is True)
        except Exception:
            return False

    def get_addon_info(self, timeout: float = 3.0) -> dict[str, Any] | None:
        try:
            res = self.send("get_addon_info", timeout=timeout)
            return res if isinstance(res, dict) and "addon_version" in res else None
        except Exception:
            return None

    def execute_code(self, code: str, timeout: float = 10.0) -> dict[str, Any]:
        return self.send("execute_code", {"code": code}, timeout=timeout)

    def inspect_internal_state(self, timeout: float = 5.0) -> dict[str, Any]:
        code = (
            "import bpy, hashlib, json, os\n"
            "jobs = {}\n"
            "for k in ('RENDER', 'RENDER_PREVIEW', 'OBJECT_BAKE', 'COMPOSITE', 'SHADER_COMPILATION'):\n"
            "    try:\n"
            "        jobs[k] = bool(bpy.app.is_job_running(k))\n"
            "    except Exception:\n"
            "        jobs[k] = False\n"
            "playing = False\n"
            "try:\n"
            "    scr = getattr(bpy.context, 'screen', None)\n"
            "    playing = bool(getattr(scr, 'is_animation_playing', False))\n"
            "except Exception:\n"
            "    playing = False\n"
            "sc = getattr(bpy.context, 'scene', None)\n"
            "objs = sc.objects if sc else bpy.data.objects\n"
            "fp = bpy.data.filepath or ''\n"
            "fmtime = None\n"
            "if fp and os.path.exists(fp):\n"
            "    try:\n"
            "        fmtime = os.stat(fp).st_mtime\n"
            "    except OSError:\n"
            "        fmtime = None\n"
            "sig_items = [\n"
            "    [\n"
            "        o.name,\n"
            "        o.type,\n"
            "        [round(float(v), 4) for v in o.location],\n"
            "        [round(float(v), 4) for v in o.rotation_euler],\n"
            "        [round(float(v), 4) for v in o.scale],\n"
            "        len(o.data.vertices) if (o.type == 'MESH' and o.data) else 0,\n"
            "        len(o.data.polygons) if (o.type == 'MESH' and o.data) else 0,\n"
            "        [m.name for m in o.data.materials if m] if hasattr(o.data, 'materials') and o.data else [],\n"
            "    ]\n"
            "    for o in sorted(objs, key=lambda x: x.name)\n"
            "]\n"
            "sig_payload = json.dumps({'scene': sc.name if sc else '', 'objs': sig_items, 'mats': sorted(m.name for m in bpy.data.materials)}, sort_keys=True)\n"
            "sig = hashlib.sha256(sig_payload.encode('utf-8')).hexdigest()\n"
            "print(json.dumps({\n"
            "    'filepath': fp,\n"
            "    'file_mtime': fmtime,\n"
            "    'is_dirty': bool(bpy.data.is_dirty),\n"
            "    'jobs': jobs,\n"
            "    'is_animation_playing': playing,\n"
            "    'scene_name': sc.name if sc else '',\n"
            "    'object_count': len(objs),\n"
            "    'object_names': [o.name for o in objs],\n"
            "    'scene_signature': sig,\n"
            "}))\n"
        )
        res = self.execute_code(code, timeout=timeout)
        output = (res or {}).get("result", "").strip()
        last_line = output.splitlines()[-1] if output else "{}"
        data = json.loads(last_line)
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Blender state output: {output!r}")
        return data

    def create_verified_backup(self, backup_path: Path, timeout: float = 15.0) -> dict[str, Any]:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        escaped = json.dumps(str(backup_path))
        code = (
            "import bpy, json\n"
            f"target = {escaped}\n"
            "bpy.ops.wm.save_as_mainfile(filepath=target, copy=True, check_existing=False)\n"
            "print(json.dumps({'saved': True, 'filepath': bpy.data.filepath, 'backup_path': target}))\n"
        )
        res = self.execute_code(code, timeout=timeout)
        output = (res or {}).get("result", "").strip()
        _ = json.loads(output.splitlines()[-1]) if output else {}
        ok, reason, meta = _verify_blend_file(backup_path)
        if not ok:
            raise RuntimeError(f"Backup verification failed: {reason}")
        return {
            "backup_path": str(backup_path),
            "size_bytes": meta["size_bytes"],
            "sha256": meta["sha256"],
        }

    def restore_and_verify_session(self, session_meta: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
        is_dirty = bool(session_meta.get("is_dirty"))
        backup_path_raw = session_meta.get("backup_path")
        original_filepath = session_meta.get("original_filepath") or ""

        if is_dirty:
            restore_path = Path(backup_path_raw) if backup_path_raw else None
            if restore_path is None:
                return {
                    "status": "failed",
                    "verified": False,
                    "restored_from": None,
                    "original_filepath": original_filepath,
                    "reason": "Dirty session had no backup_path recorded.",
                }
            ok, reason, _ = _verify_blend_file(restore_path, expected_sha256=session_meta.get("sha256"))
            if not ok:
                return {
                    "status": "failed",
                    "verified": False,
                    "restored_from": str(restore_path),
                    "original_filepath": original_filepath,
                    "reason": reason,
                }
        elif original_filepath:
            restore_path = Path(original_filepath)
            ok, reason, _ = _verify_blend_file(restore_path)
            if not ok:
                return {
                    "status": "failed",
                    "verified": False,
                    "restored_from": str(restore_path),
                    "original_filepath": original_filepath,
                    "reason": reason,
                }
        else:
            return {
                "status": "not_needed",
                "verified": True,
                "restored_from": None,
                "original_filepath": "",
                "reason": "Previous session was an unmodified default scene.",
            }

        escaped = json.dumps(str(restore_path))
        code = (
            "import bpy, json\n"
            f"bpy.ops.wm.open_mainfile(filepath={escaped})\n"
            "sc = getattr(bpy.context, 'scene', None)\n"
            "objs = sc.objects if sc else bpy.data.objects\n"
            "print(json.dumps({\n"
            "    'filepath': bpy.data.filepath,\n"
            "    'scene_name': sc.name if sc else '',\n"
            "    'object_count': len(objs),\n"
            "    'object_names': [o.name for o in objs],\n"
            "}))\n"
        )
        try:
            res = self.execute_code(code, timeout=timeout)
            output = (res or {}).get("result", "").strip()
            opened = json.loads(output.splitlines()[-1]) if output else {}
            live_state = self.inspect_internal_state(timeout=timeout)
        except Exception as exc:
            return {
                "status": "failed",
                "verified": False,
                "restored_from": str(restore_path),
                "original_filepath": original_filepath,
                "reason": f"Failed opening or verifying restored scene: {exc}",
            }

        expected_scene = session_meta.get("scene_name") or ""
        expected_objects = set(session_meta.get("object_names") or [])
        actual_objects = set(live_state.get("object_names") or [])
        actual_scene = live_state.get("scene_name") or ""

        if expected_scene and actual_scene != expected_scene:
            return {
                "status": "failed",
                "verified": False,
                "restored_from": str(restore_path),
                "original_filepath": original_filepath,
                "reason": f"Scene mismatch after restore: expected {expected_scene!r}, got {actual_scene!r}",
            }
        if not expected_objects.issubset(actual_objects):
            missing = sorted(expected_objects - actual_objects)
            return {
                "status": "failed",
                "verified": False,
                "restored_from": str(restore_path),
                "original_filepath": original_filepath,
                "reason": f"Objects missing after restore: {missing}",
            }

        return {
            "status": "verified",
            "verified": True,
            "restored_from": str(restore_path),
            "original_filepath": original_filepath,
            "loaded_filepath": opened.get("filepath", live_state.get("filepath", "")),
            "scene_name": actual_scene,
            "object_count": live_state.get("object_count", len(actual_objects)),
        }

    def apply_cycles_config(
        self,
        effective_device: str,
        cycles_backend: str,
        selected_gpu_index: int | None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Configure Cycles compute preferences without overriding scene.render.engine."""
        code = (
            "import bpy, json\n"
            f"effective_device = {json.dumps(effective_device)}\n"
            f"backend = {json.dumps(cycles_backend)}\n"
            "res = {'configured': False}\n"
            "addon = bpy.context.preferences.addons.get('cycles')\n"
            "if addon:\n"
            "    cp = addon.preferences\n"
            "    if effective_device == 'CPU':\n"
            "        cp.compute_device_type = 'NONE'\n"
            "        cp.get_devices()\n"
            "        for d in cp.devices:\n"
            "            d.use = (d.type == 'CPU')\n"
            "        if hasattr(bpy.context.scene, 'cycles'):\n"
            "            bpy.context.scene.cycles.device = 'CPU'\n"
            "        res = {'configured': True, 'compute_device_type': 'NONE', 'scene_cycles_device': 'CPU'}\n"
            "    else:\n"
            "        target_type = backend if backend in {'OPTIX', 'CUDA'} else 'OPTIX'\n"
            "        try:\n"
            "            cp.compute_device_type = target_type\n"
            "        except Exception:\n"
            "            cp.compute_device_type = 'CUDA'\n"
            "        cp.get_devices()\n"
            "        active_type = cp.compute_device_type\n"
            "        for d in cp.devices:\n"
            "            d.use = (d.type == active_type)\n"
            "        if hasattr(bpy.context.scene, 'cycles'):\n"
            "            bpy.context.scene.cycles.device = 'GPU'\n"
            "        res = {'configured': True, 'compute_device_type': active_type, 'scene_cycles_device': 'GPU'}\n"
            "print(json.dumps(res))\n"
        )
        try:
            res = self.execute_code(code, timeout=timeout)
            output = (res or {}).get("result", "").strip()
            return json.loads(output.splitlines()[-1]) if output else {"configured": False}
        except Exception as exc:
            return {"configured": False, "error": str(exc)}

    def request_graceful_quit(self, timeout: float = 3.0) -> bool:
        code = (
            "import bpy\n"
            "def _delayed_quit():\n"
            "    try:\n"
            "        bpy.ops.wm.quit_blender()\n"
            "    except Exception:\n"
            "        pass\n"
            "    return None\n"
            "bpy.app.timers.register(_delayed_quit, first_interval=0.05)\n"
            "print('QUIT_SCHEDULED')\n"
        )
        try:
            self.execute_code(code, timeout=timeout)
            return True
        except Exception:
            return False


class JobMarker:
    """Process-crash-safe job marker file held with an exclusive advisory flock."""

    def __init__(self, directory: Path, job_id: str, tool_name: str, kind: str = "active") -> None:
        self.directory = directory
        self.job_id = job_id
        self.tool_name = tool_name
        self.kind = kind
        self.path = directory / f"{os.getpid()}_{uuid.uuid4().hex[:10]}.json"
        self._fd: int | None = None

    def acquire(self) -> "JobMarker":
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            payload = json.dumps(
                {
                    "pid": os.getpid(),
                    "job_id": self.job_id,
                    "tool_name": self.tool_name,
                    "kind": self.kind,
                    "created_at": time.time(),
                }
            ).encode("utf-8")
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, payload)
            self._fd = fd
            return self
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def release(self) -> None:
        fd = self._fd
        self._fd = None
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self) -> "JobMarker":
        return self.acquire()

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def list_live_markers(directory: Path) -> list[dict[str, Any]]:
    """Return live markers in `directory`, reaping stale markers whose owners died."""
    if not directory.is_dir():
        return []
    live: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        fd = None
        try:
            fd = os.open(str(path), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    path.unlink()
                except OSError:
                    pass
                fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                try:
                    raw = path.read_text(encoding="utf-8", errors="replace")
                    data = json.loads(raw) if raw.strip() else {"path": str(path)}
                except Exception:
                    data = {"path": str(path)}
                live.append(data)
        except OSError:
            continue
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
    return live


class BlenderLifecycleManager:
    """Manages Blender startup, readiness, concurrency locks, idle timeout, GPU checks, and recovery."""

    def __init__(self, cfg: BlenderMCPConfig | None = None) -> None:
        self.cfg = cfg or load_config()
        self.logger = setup_logger(
            "blender_lifecycle",
            self.cfg.logs_dir / "lifecycle.log",
            self.cfg.log_level,
        )
        self.addon = AddonClient(self.cfg.blender_host, self.cfg.blender_port)

    @contextmanager
    def _lifecycle_lock(self) -> Iterator[None]:
        self.cfg.lifecycle_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.cfg.lifecycle_lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    @contextmanager
    def work_lock(self, timeout_sec: float | None = None) -> Iterator[None]:
        """Serialize Blender tool calls across concurrent OpenCode sessions/wrappers."""
        timeout = self.cfg.work_lock_timeout_sec if timeout_sec is None else timeout_sec
        self.cfg.work_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.cfg.work_lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        deadline = time.monotonic() + max(0.5, timeout)
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out after {timeout:.1f}s waiting for another Blender operation to finish."
                        )
                    time.sleep(0.05)
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)

    def _is_work_lock_held(self) -> bool:
        if not self.cfg.work_lock_path.exists():
            return False
        fd = os.open(str(self.cfg.work_lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
        finally:
            os.close(fd)

    def _default_state(self) -> dict[str, Any]:
        return {
            "state": LifecycleState.STOPPED.value,
            "ownership": "none",
            "managed": False,
            "boot_id": None,
            "blender": None,
            "xvfb": None,
            "external_pid": None,
            "host": self.cfg.blender_host,
            "port": self.cfg.blender_port,
            "started_at": None,
            "last_work_started_at": None,
            "last_work_completed_at": None,
            "effective_idle_timeout_sec": self.cfg.idle_timeout_sec,
            "startup_duration_sec": None,
            "gpu_before_start": None,
            "gpu_after_start": None,
            "gpu_after_stop": None,
            "gpu_effective_device": None,
            "gpu_selected_index": None,
            "gpu_warnings": [],
            "gpu_decision": None,
            "session_backup": None,
            "session_recovery": None,
            "clean_baseline": None,
            "last_error": None,
            "transitions": [],
            "updated_at": time.time(),
        }

    def read_state(self) -> dict[str, Any]:
        if not self.cfg.state_path.is_file():
            return self._default_state()
        try:
            raw = self.cfg.state_path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                return self._default_state()
            base = self._default_state()
            base.update(loaded)
            return base
        except (OSError, json.JSONDecodeError):
            return self._default_state()

    def _write_state(self, state: dict[str, Any]) -> None:
        self.cfg.state_path.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = time.time()
        tmp_path = self.cfg.state_path.with_name(f".state_{os.getpid()}_{uuid.uuid4().hex[:8]}.tmp")
        data = json.dumps(state, indent=2, sort_keys=False)
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.cfg.state_path)

    def _transition(self, state: dict[str, Any], new_state: LifecycleState, reason: str) -> None:
        prev = state.get("state")
        state["state"] = new_state.value
        history = list(state.get("transitions") or [])
        history.append(
            {
                "from": prev,
                "to": new_state.value,
                "reason": reason,
                "timestamp": time.time(),
            }
        )
        state["transitions"] = history[-50:]
        if prev != new_state.value:
            self.logger.info("State transition: %s -> %s (%s)", prev, new_state.value, reason)

    def _prune_backups(self) -> None:
        max_files = max(1, self.cfg.max_backup_files)
        backups = sorted(
            self.cfg.backups_dir.glob("autosave_*.blend"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[max_files:]:
            try:
                old.unlink()
            except OSError:
                pass

    def status(self, probe_live: bool = True) -> dict[str, Any]:
        """Read-only status check. Never starts Blender and never resets the idle timer."""
        state = self.read_state()
        blender_rec = ProcessRecord.from_dict(state.get("blender"))
        xvfb_rec = ProcessRecord.from_dict(state.get("xvfb"))
        managed_alive = is_same_process(blender_rec)
        xvfb_alive = is_same_process(xvfb_rec)
        active_jobs = list_live_markers(self.cfg.active_jobs_dir)
        pending_jobs = list_live_markers(self.cfg.pending_jobs_dir)

        ping_ok = False
        addon_info = None
        if probe_live:
            ping_ok = self.addon.ping(timeout=1.5)
            if ping_ok:
                addon_info = self.addon.get_addon_info(timeout=2.0)

        external_pid = None
        if ping_ok and not managed_alive:
            external_pid = find_listening_pid_for_port(self.cfg.blender_port) or state.get("external_pid")

        anchor = state.get("last_work_completed_at") or state.get("started_at")
        idle_elapsed = max(0.0, time.time() - anchor) if (anchor and (managed_alive or ping_ok)) else 0.0

        return {
            "state": state.get("state"),
            "ownership": state.get("ownership"),
            "managed": state.get("managed"),
            "boot_id": state.get("boot_id"),
            "blender_alive": managed_alive,
            "blender_pid": blender_rec.pid if managed_alive and blender_rec else external_pid,
            "xvfb_alive": xvfb_alive,
            "xvfb_pid": xvfb_rec.pid if xvfb_alive and xvfb_rec else None,
            "display": xvfb_rec.display if xvfb_rec else None,
            "external_pid": external_pid,
            "addon_reachable": ping_ok,
            "addon_info": addon_info,
            "active_jobs": active_jobs,
            "pending_jobs": pending_jobs,
            "work_lock_held": self._is_work_lock_held(),
            "idle_elapsed_sec": round(idle_elapsed, 2),
            "idle_timeout_sec": float(state.get("effective_idle_timeout_sec") or self.cfg.idle_timeout_sec),
            "startup_duration_sec": state.get("startup_duration_sec"),
            "gpu_effective_device": state.get("gpu_effective_device"),
            "gpu_selected_index": state.get("gpu_selected_index"),
            "gpu_warnings": state.get("gpu_warnings", []),
            "gpu_decision": state.get("gpu_decision"),
            "gpu_after_start": state.get("gpu_after_start"),
            "gpu_after_stop": state.get("gpu_after_stop"),
            "session_backup": state.get("session_backup"),
            "session_recovery": state.get("session_recovery"),
            "last_error": state.get("last_error"),
            "watchdog_running": self.is_watchdog_running(),
            "defunct_children": cleanup_stale_pids_check(),
        }

    def is_watchdog_running(self) -> bool:
        if not self.cfg.watchdog_lock_path.exists():
            return False
        fd = os.open(str(self.cfg.watchdog_lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
        finally:
            os.close(fd)

    def ensure_watchdog_running(self) -> None:
        if self.is_watchdog_running():
            return
        cmd = [
            sys.executable,
            "-m",
            "blender.lifecycle_manager",
            "watchdog",
        ]
        env = os.environ.copy()
        mcp_dir = str(Path(__file__).resolve().parents[1])
        existing_py = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{mcp_dir}:{existing_py}" if existing_py else mcp_dir
        env["BLENDER_MCP_PROJECT_ROOT"] = str(self.cfg.project_root)
        env["BLENDER_MCP_CONFIG"] = str(self.cfg.config_path)
        env["BLENDER_MCP_RUNTIME_DIR"] = str(self.cfg.runtime_dir)
        env["BLENDER_HOST"] = self.cfg.blender_host
        env["BLENDER_PORT"] = str(self.cfg.blender_port)
        env["BLENDER_MCP_IDLE_TIMEOUT"] = str(self.cfg.idle_timeout_sec)
        env["BLENDER_MCP_STARTUP_TIMEOUT"] = str(self.cfg.startup_timeout_sec)
        env["BLENDER_MCP_SHUTDOWN_TIMEOUT"] = str(self.cfg.shutdown_timeout_sec)
        env["BLENDER_MCP_WATCHDOG_POLL"] = str(self.cfg.watchdog_poll_interval_sec)
        env["BLENDER_MCP_AUTO_BACKUP"] = "true" if self.cfg.auto_backup else "false"
        env["BLENDER_MCP_AUTO_RECOVERY"] = "true" if self.cfg.auto_recovery else "false"
        env["BLENDER_MCP_AUTO_RESTORE"] = "true" if self.cfg.auto_restore_on_restart else "false"
        env["BLENDER_MCP_MANAGE_EXTERNAL"] = "true" if self.cfg.manage_external_blender else "false"

        try:
            pid, _pgid, _st = spawn_detached(cmd, env, self.cfg.logs_dir / "watchdog_boot.log")
            self.logger.info("Started detached watchdog pid=%d", pid)
        except Exception as exc:
            self.logger.warning("Failed to start watchdog: %s", exc)

    def ensure_ready(self, reason: str = "tool_call", force_check: bool = False) -> dict[str, Any]:
        """Ensure Blender is running and the MCP addon is ready, launching lazily if needed."""
        with self._lifecycle_lock():
            state = self.read_state()
            blender_rec = ProcessRecord.from_dict(state.get("blender"))
            xvfb_rec = ProcessRecord.from_dict(state.get("xvfb"))

            # 1. Reuse healthy managed Blender if running
            if state.get("ownership") == "managed" and is_same_process(blender_rec):
                reusable = self.addon.ping(timeout=2.0) and (
                    not force_check or self.addon.get_addon_info(timeout=2.5) is not None
                )
                if not reusable and force_check:
                    retry_deadline = time.monotonic() + 3.0
                    while time.monotonic() < retry_deadline and is_same_process(blender_rec):
                        if self.addon.ping(timeout=1.0) and self.addon.get_addon_info(timeout=1.5) is not None:
                            reusable = True
                            break
                        time.sleep(0.2)

                if reusable:
                    if state.get("state") in {
                        LifecycleState.STOPPED.value,
                        LifecycleState.STARTING.value,
                        LifecycleState.ERROR.value,
                    }:
                        self._transition(state, LifecycleState.READY, f"reused:{reason}")
                        self._write_state(state)
                    self.ensure_watchdog_running()
                    return {
                        "status": "reused",
                        "ownership": "managed",
                        "boot_id": state.get("boot_id"),
                        "pid": blender_rec.pid if blender_rec else None,
                        "display": xvfb_rec.display if xvfb_rec else None,
                    }

                # Managed Blender process is alive but unresponsive -> stop before replacement
                self.logger.warning(
                    "Managed Blender pid=%s is alive but unresponsive; terminating before recovery launch.",
                    blender_rec.pid if blender_rec else None,
                )
                self._transition(state, LifecycleState.ERROR, f"unresponsive_before_{reason}")
                stop_managed_processes(
                    blender_rec,
                    xvfb_rec,
                    shutdown_timeout_sec=min(10.0, self.cfg.shutdown_timeout_sec),
                )
                gpu_rel = verify_gpu_released([blender_rec.pid] if blender_rec else [], timeout_sec=3.0)
                state["blender"] = None
                state["xvfb"] = None
                state["ownership"] = "none"
                state["managed"] = False
                state["gpu_after_stop"] = gpu_rel
                self._write_state(state)
                if not self.cfg.auto_recovery:
                    raise RuntimeError("Managed Blender became unresponsive and auto_recovery is disabled.")

            # 2. Clean up dead managed Blender / orphan Xvfb if crashed
            if state.get("ownership") == "managed" and not is_same_process(blender_rec):
                if state.get("state") not in {LifecycleState.STOPPED.value, LifecycleState.ERROR.value}:
                    self._transition(state, LifecycleState.ERROR, f"managed_blender_crashed_before_{reason}")
                    state["last_error"] = "Managed Blender process exited unexpectedly."
                old_pid = blender_rec.pid if blender_rec else None
                cleanup = stop_managed_processes(None, xvfb_rec, self.cfg.shutdown_timeout_sec)
                gpu_rel = verify_gpu_released([old_pid] if old_pid else [], timeout_sec=3.0)
                state["blender"] = None
                state["xvfb"] = None
                state["ownership"] = "none"
                state["managed"] = False
                state["gpu_after_stop"] = gpu_rel
                self._write_state(state)
                self.logger.warning(
                    "Cleaned up crashed managed Blender pid=%s (xvfb=%s, gpu_released=%s)",
                    old_pid,
                    cleanup,
                    gpu_rel.get("released"),
                )
                if not self.cfg.auto_recovery:
                    raise RuntimeError("Managed Blender exited unexpectedly and auto_recovery is disabled.")

            # 3. Adopt already-running external Blender if port is open and addon responds
            if self.addon.ping(timeout=1.5):
                addon_info = self.addon.get_addon_info(timeout=2.5)
                if addon_info is not None:
                    ext_pid = find_listening_pid_for_port(self.cfg.blender_port)
                    boot_id = (
                        state.get("boot_id")
                        if state.get("ownership") == "external" and state.get("external_pid") == ext_pid
                        else f"ext-{ext_pid or 'unknown'}-{int(time.time())}"
                    )
                    state["ownership"] = "external"
                    state["managed"] = False
                    state["external_pid"] = ext_pid
                    state["blender"] = None
                    state["xvfb"] = None
                    state["boot_id"] = boot_id
                    if not state.get("started_at"):
                        state["started_at"] = time.time()
                    state["last_error"] = None
                    self._transition(state, LifecycleState.READY, f"adopted_external:{reason}")
                    self._write_state(state)
                    self.logger.info(
                        "Reusing external Blender instance pid=%s on %s:%s",
                        ext_pid,
                        self.cfg.blender_host,
                        self.cfg.blender_port,
                    )
                    return {
                        "status": "external_reused",
                        "ownership": "external",
                        "boot_id": boot_id,
                        "pid": ext_pid,
                        "addon_info": addon_info,
                    }

            # 4. Pre-launch GPU health & VRAM check
            gpu_plan = select_launch_gpu_config(self.cfg)
            state["gpu_decision"] = gpu_plan.decision
            if not gpu_plan.ok:
                state["last_error"] = gpu_plan.error
                state["gpu_before_start"] = gpu_plan.snapshot
                self._transition(state, LifecycleState.ERROR, "gpu_check_failed")
                self._write_state(state)
                raise RuntimeError(gpu_plan.error or "GPU preflight check failed.")

            for warn in gpu_plan.warnings:
                self.logger.warning("GPU preflight: %s", warn)
            self.logger.info(
                "GPU decision: device=%s backend=%s index=%s health=%s fallback=%s",
                gpu_plan.effective_device,
                gpu_plan.effective_backend,
                gpu_plan.selected_gpu_index,
                gpu_plan.decision.get("health_status"),
                gpu_plan.decision.get("fallback_triggered"),
            )

            # 5. Launch Xvfb + Blender
            launch_t0 = time.monotonic()
            self._transition(state, LifecycleState.STARTING, f"launch:{reason}")
            state["ownership"] = "managed"
            state["managed"] = True
            state["external_pid"] = None
            state["last_error"] = None
            state["gpu_before_start"] = gpu_plan.snapshot
            state["gpu_effective_device"] = gpu_plan.effective_device
            state["gpu_selected_index"] = gpu_plan.selected_gpu_index
            state["gpu_warnings"] = gpu_plan.warnings
            self._write_state(state)

            new_xvfb: ProcessRecord | None = None
            new_blender: ProcessRecord | None = None
            try:
                new_xvfb = start_xvfb(self.cfg, self.cfg.logs_dir / "xvfb.log")
                state["xvfb"] = new_xvfb.to_dict()
                self._write_state(state)

                new_blender = start_blender(
                    self.cfg,
                    display=new_xvfb.display or ":0",
                    env_overrides=gpu_plan.env_overrides,
                    log_path=self.cfg.logs_dir / "blender.log",
                )
                state["blender"] = new_blender.to_dict()
                self._write_state(state)

                # 6. Poll for real addon readiness
                deadline = time.monotonic() + max(5.0, self.cfg.startup_timeout_sec)
                addon_info = None
                while time.monotonic() < deadline:
                    if not is_same_process(new_blender):
                        raise RuntimeError(
                            f"Blender process (pid={new_blender.pid}) exited during startup. "
                            f"Check {self.cfg.logs_dir / 'blender.log'}."
                        )
                    if self.addon.ping(timeout=1.5):
                        addon_info = self.addon.get_addon_info(timeout=2.5)
                        if addon_info is not None:
                            break
                    time.sleep(max(0.05, self.cfg.readiness_poll_interval_sec))

                if addon_info is None:
                    raise TimeoutError(
                        f"Blender addon did not become ready on {self.cfg.blender_host}:{self.cfg.blender_port} "
                        f"within {self.cfg.startup_timeout_sec}s."
                    )

                # 7. Apply Cycles GPU/CPU configuration (best-effort)
                cycles_cfg = self.addon.apply_cycles_config(
                    gpu_plan.effective_device,
                    gpu_plan.effective_backend,
                    gpu_plan.selected_gpu_index,
                    timeout=self.cfg.addon_probe_timeout_sec,
                )

                # 8. Restore prior session if enabled and recorded
                session_meta = state.get("session_backup")
                if self.cfg.auto_restore_on_restart and isinstance(session_meta, dict):
                    recovery = self.addon.restore_and_verify_session(session_meta, timeout=15.0)
                    state["session_recovery"] = recovery
                    state["session_backup"] = None
                    if recovery.get("status") == "failed":
                        self.logger.error("Session restore verification failed: %s", recovery)
                    else:
                        self.logger.info("Session restore status: %s", recovery)
                else:
                    state["session_recovery"] = None

                # 9. Record clean baseline and finalize READY state
                try:
                    post_internal = self.addon.inspect_internal_state(timeout=self.cfg.addon_probe_timeout_sec)
                    state["clean_baseline"] = {
                        "filepath": post_internal.get("filepath") or "",
                        "file_mtime": post_internal.get("file_mtime"),
                        "scene_signature": post_internal.get("scene_signature"),
                    }
                except Exception:
                    state["clean_baseline"] = None

                startup_sec = round(time.monotonic() - launch_t0, 3)
                now = time.time()
                boot_id = f"managed-{new_blender.pid}-{uuid.uuid4().hex[:8]}"
                state["boot_id"] = boot_id
                state["started_at"] = now
                state["last_work_completed_at"] = now
                state["effective_idle_timeout_sec"] = self.cfg.idle_timeout_sec
                state["startup_duration_sec"] = startup_sec
                state["gpu_after_start"] = get_pid_gpu_usage([new_blender.pid])
                self._transition(state, LifecycleState.READY, f"ready_in_{startup_sec}s")
                self._write_state(state)

                self.ensure_watchdog_running()
                self.logger.info(
                    "Blender ready pid=%d xvfb=%s(%s) boot_id=%s startup=%.3fs cycles=%s",
                    new_blender.pid,
                    new_xvfb.pid,
                    new_xvfb.display,
                    boot_id,
                    startup_sec,
                    cycles_cfg,
                )
                return {
                    "status": "started",
                    "ownership": "managed",
                    "boot_id": boot_id,
                    "pid": new_blender.pid,
                    "xvfb_pid": new_xvfb.pid,
                    "display": new_xvfb.display,
                    "startup_duration_sec": startup_sec,
                    "addon_info": addon_info,
                    "cycles_config": cycles_cfg,
                    "session_recovery": state.get("session_recovery"),
                    "gpu_warnings": gpu_plan.warnings,
                    "gpu_decision": gpu_plan.decision,
                }
            except Exception as exc:
                self.logger.error("Failed starting Blender: %s", exc)
                stop_managed_processes(new_blender, new_xvfb, shutdown_timeout_sec=5.0)
                state["blender"] = None
                state["xvfb"] = None
                state["ownership"] = "none"
                state["managed"] = False
                state["last_error"] = str(exc)
                self._transition(state, LifecycleState.ERROR, "startup_failed")
                self._write_state(state)
                raise

    def begin_work(self, job_id: str, tool_name: str) -> None:
        with self._lifecycle_lock():
            state = self.read_state()
            state["last_work_started_at"] = time.time()
            self._transition(state, LifecycleState.BUSY, f"begin:{tool_name}:{job_id}")
            self._write_state(state)

    def finish_work(self, job_id: str, tool_name: str) -> None:
        updated_baseline = None
        try:
            snap = self.read_state()
            work_started = float(snap.get("last_work_started_at") or 0.0)
            prev_base = snap.get("clean_baseline") if isinstance(snap.get("clean_baseline"), dict) else {}
            prev_fp = prev_base.get("filepath") or ""
            prev_mtime = prev_base.get("file_mtime")
            if self.addon.ping(timeout=1.5):
                internal = self.addon.inspect_internal_state(timeout=self.cfg.addon_probe_timeout_sec)
                fmtime = internal.get("file_mtime")
                fp = internal.get("filepath") or ""
                is_new_save = bool(
                    fp
                    and fmtime is not None
                    and not internal.get("is_dirty")
                    and (
                        (fp == prev_fp and (prev_mtime is None or float(fmtime) > float(prev_mtime)))
                        or (fp != prev_fp and float(fmtime) >= (work_started - 0.2))
                    )
                )
                if is_new_save:
                    updated_baseline = {
                        "filepath": fp,
                        "file_mtime": fmtime,
                        "scene_signature": internal.get("scene_signature"),
                    }
        except Exception:
            updated_baseline = None

        with self._lifecycle_lock():
            state = self.read_state()
            now = time.time()
            state["last_work_completed_at"] = now
            state["effective_idle_timeout_sec"] = self.cfg.idle_timeout_sec
            if updated_baseline is not None:
                state["clean_baseline"] = updated_baseline
            active_jobs = list_live_markers(self.cfg.active_jobs_dir)
            blender_rec = ProcessRecord.from_dict(state.get("blender"))
            if active_jobs:
                target_state = LifecycleState.BUSY
            elif state.get("ownership") == "managed" and is_same_process(blender_rec):
                target_state = LifecycleState.IDLE
            elif state.get("ownership") == "external" and self.addon.ping(timeout=1.0):
                target_state = LifecycleState.IDLE
            else:
                target_state = LifecycleState.STOPPED
            self._transition(state, target_state, f"finish:{tool_name}:{job_id}")
            self._write_state(state)

    def stop_blender(
        self,
        reason: str = "manual",
        force: bool = False,
        check_idle: bool = False,
        discard_session: bool = False,
    ) -> dict[str, Any]:
        """Safely stop managed Blender if idle/unbusy/safe, preserving external instances."""
        with self._lifecycle_lock():
            state = self.read_state()
            ownership = state.get("ownership", "none")
            blender_rec = ProcessRecord.from_dict(state.get("blender"))
            xvfb_rec = ProcessRecord.from_dict(state.get("xvfb"))

            if ownership == "external" and not self.cfg.manage_external_blender:
                return {
                    "stopped": False,
                    "deferred": True,
                    "reason": "external_blender_protected",
                    "external_pid": state.get("external_pid"),
                }

            if ownership == "none" and not is_same_process(blender_rec) and not is_same_process(xvfb_rec):
                if state.get("state") != LifecycleState.STOPPED.value:
                    self._transition(state, LifecycleState.STOPPED, f"already_stopped:{reason}")
                    self._write_state(state)
                return {"stopped": True, "already_stopped": True, "reason": "already_stopped"}

            if blender_rec and not is_same_process(blender_rec):
                cleanup = stop_managed_processes(None, xvfb_rec, self.cfg.shutdown_timeout_sec)
                gpu_rel = verify_gpu_released([blender_rec.pid], timeout_sec=3.0)
                state["blender"] = None
                state["xvfb"] = None
                state["ownership"] = "none"
                state["managed"] = False
                state["gpu_after_stop"] = gpu_rel
                self._transition(state, LifecycleState.STOPPED, f"cleanup_dead:{reason}")
                self._write_state(state)
                return {
                    "stopped": True,
                    "already_dead": True,
                    "cleanup": cleanup,
                    "gpu_released": gpu_rel.get("released"),
                }

            if not force:
                active_jobs = list_live_markers(self.cfg.active_jobs_dir)
                pending_jobs = list_live_markers(self.cfg.pending_jobs_dir)
                if active_jobs or pending_jobs or self._is_work_lock_held():
                    return {
                        "stopped": False,
                        "deferred": True,
                        "reason": "active_or_pending_mcp_work",
                        "active_jobs": len(active_jobs),
                        "pending_jobs": len(pending_jobs),
                    }

                if check_idle:
                    anchor = state.get("last_work_completed_at") or state.get("started_at") or 0.0
                    effective_idle = float(state.get("effective_idle_timeout_sec") or self.cfg.idle_timeout_sec)
                    elapsed = max(0.0, time.time() - anchor)
                    if elapsed < effective_idle:
                        return {
                            "stopped": False,
                            "deferred": True,
                            "reason": "idle_timeout_not_elapsed",
                            "idle_elapsed_sec": round(elapsed, 2),
                            "idle_timeout_sec": effective_idle,
                        }

                try:
                    internal = self.addon.inspect_internal_state(timeout=self.cfg.addon_probe_timeout_sec)
                except Exception as exc:
                    self.logger.warning("Deferring shutdown because Blender internal state could not be verified: %s", exc)
                    return {
                        "stopped": False,
                        "deferred": True,
                        "reason": f"internal_state_unverifiable: {exc}",
                    }

                jobs = internal.get("jobs") or {}
                busy_jobs = [name for name, running in jobs.items() if running]
                if busy_jobs or internal.get("is_animation_playing"):
                    return {
                        "stopped": False,
                        "deferred": True,
                        "reason": "blender_internal_job_running",
                        "busy_jobs": busy_jobs,
                        "animation_playing": bool(internal.get("is_animation_playing")),
                    }

                orig_file = internal.get("filepath") or ""
                baseline = state.get("clean_baseline") if isinstance(state.get("clean_baseline"), dict) else {}
                sig_changed = bool(
                    baseline.get("scene_signature")
                    and internal.get("scene_signature")
                    and internal.get("scene_signature") != baseline.get("scene_signature")
                )
                saved_after_baseline = bool(
                    orig_file
                    and internal.get("file_mtime") is not None
                    and baseline.get("file_mtime") is not None
                    and orig_file == baseline.get("filepath")
                    and float(internal["file_mtime"]) > float(baseline["file_mtime"])
                )
                is_dirty = bool(internal.get("is_dirty")) or (sig_changed and not saved_after_baseline)
                if is_dirty and not discard_session:
                    if not self.cfg.auto_backup:
                        return {
                            "stopped": False,
                            "deferred": True,
                            "reason": "unsaved_work_auto_backup_disabled",
                        }
                    boot_tag = (state.get("boot_id") or "session").replace("/", "_")
                    backup_name = f"autosave_{int(time.time())}_{boot_tag}.blend"
                    backup_path = self.cfg.backups_dir / backup_name
                    try:
                        backup_meta = self.addon.create_verified_backup(backup_path, timeout=15.0)
                    except Exception as exc:
                        self.logger.warning("Deferring shutdown because auto-backup failed: %s", exc)
                        return {
                            "stopped": False,
                            "deferred": True,
                            "reason": f"unsaved_work_backup_failed: {exc}",
                        }
                    self._prune_backups()
                    state["session_backup"] = {
                        "is_dirty": True,
                        "backup_path": backup_meta["backup_path"],
                        "size_bytes": backup_meta["size_bytes"],
                        "sha256": backup_meta["sha256"],
                        "original_filepath": orig_file,
                        "scene_name": internal.get("scene_name", ""),
                        "object_count": internal.get("object_count", 0),
                        "object_names": internal.get("object_names", []),
                        "created_at": time.time(),
                    }
                elif orig_file and not discard_session:
                    state["session_backup"] = {
                        "is_dirty": False,
                        "backup_path": None,
                        "original_filepath": orig_file,
                        "scene_name": internal.get("scene_name", ""),
                        "object_count": internal.get("object_count", 0),
                        "object_names": internal.get("object_names", []),
                        "created_at": time.time(),
                    }
                else:
                    state["session_backup"] = None

            self._transition(state, LifecycleState.STOPPING, reason)
            self._write_state(state)

            target_pids = [blender_rec.pid] if blender_rec else []
            cleanup = stop_managed_processes(
                blender_rec,
                xvfb_rec,
                shutdown_timeout_sec=self.cfg.shutdown_timeout_sec,
                graceful_quit_fn=(None if force else lambda: self.addon.request_graceful_quit(timeout=3.0)),
            )
            gpu_rel = verify_gpu_released(target_pids, timeout_sec=5.0)

            state["blender"] = None
            state["xvfb"] = None
            state["ownership"] = "none"
            state["managed"] = False
            state["boot_id"] = None
            state["gpu_after_stop"] = gpu_rel
            self._transition(state, LifecycleState.STOPPED, f"stopped:{reason}")
            self._write_state(state)

            self.logger.info(
                "Blender stopped (%s): cleanup=%s gpu_released=%s",
                reason,
                cleanup,
                gpu_rel.get("released"),
            )
            return {
                "stopped": bool(cleanup.get("blender_stopped") and cleanup.get("xvfb_stopped")),
                "deferred": False,
                "reason": reason,
                "cleanup": cleanup,
                "gpu_released": gpu_rel,
                "session_backup": state.get("session_backup"),
            }

    def run_watchdog(self) -> int:
        """Single-instance background watchdog managing idle timeout and crash cleanup."""
        self.cfg.watchdog_lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(self.cfg.watchdog_lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(lock_fd)
            return 0

        try:
            self.cfg.watchdog_pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        except OSError:
            pass

        self.logger.info(
            "Watchdog started (idle_timeout=%.1fs, poll=%.1fs)",
            self.cfg.idle_timeout_sec,
            self.cfg.watchdog_poll_interval_sec,
        )
        stopped_since: float | None = None
        poll_interval = max(0.25, self.cfg.watchdog_poll_interval_sec)

        try:
            while True:
                time.sleep(poll_interval)
                try:
                    active_jobs = list_live_markers(self.cfg.active_jobs_dir)
                    pending_jobs = list_live_markers(self.cfg.pending_jobs_dir)
                    state = self.read_state()
                    ownership = state.get("ownership", "none")
                    blender_rec = ProcessRecord.from_dict(state.get("blender"))
                    xvfb_rec = ProcessRecord.from_dict(state.get("xvfb"))

                    if ownership == "managed":
                        if not is_same_process(blender_rec):
                            with self._lifecycle_lock():
                                fresh = self.read_state()
                                f_blender = ProcessRecord.from_dict(fresh.get("blender"))
                                f_xvfb = ProcessRecord.from_dict(fresh.get("xvfb"))
                                if fresh.get("ownership") == "managed" and not is_same_process(f_blender):
                                    old_pid = f_blender.pid if f_blender else None
                                    stop_managed_processes(None, f_xvfb, self.cfg.shutdown_timeout_sec)
                                    gpu_rel = verify_gpu_released([old_pid] if old_pid else [], timeout_sec=3.0)
                                    fresh["blender"] = None
                                    fresh["xvfb"] = None
                                    fresh["ownership"] = "none"
                                    fresh["managed"] = False
                                    fresh["gpu_after_stop"] = gpu_rel
                                    fresh["last_error"] = "Managed Blender exited unexpectedly (detected by watchdog)."
                                    self._transition(fresh, LifecycleState.ERROR, "watchdog_detected_crash")
                                    self._write_state(fresh)
                            stopped_since = time.monotonic()
                            continue

                        stopped_since = None
                        if not active_jobs and not pending_jobs and not self._is_work_lock_held():
                            if state.get("state") == LifecycleState.READY.value:
                                with self._lifecycle_lock():
                                    fresh = self.read_state()
                                    if (
                                        fresh.get("state") == LifecycleState.READY.value
                                        and not list_live_markers(self.cfg.active_jobs_dir)
                                    ):
                                        self._transition(fresh, LifecycleState.IDLE, "watchdog_idle")
                                        self._write_state(fresh)

                            anchor = state.get("last_work_completed_at") or state.get("started_at") or 0.0
                            effective_idle = float(
                                state.get("effective_idle_timeout_sec") or self.cfg.idle_timeout_sec
                            )
                            if (time.time() - anchor) >= effective_idle:
                                res = self.stop_blender(reason="idle_timeout", check_idle=True)
                                if res.get("stopped"):
                                    self.logger.info("Watchdog idle-stopped Blender cleanly.")
                                    stopped_since = time.monotonic()
                    else:
                        if is_same_process(xvfb_rec):
                            with self._lifecycle_lock():
                                fresh = self.read_state()
                                if fresh.get("ownership") != "managed":
                                    stop_managed_processes(None, ProcessRecord.from_dict(fresh.get("xvfb")), 3.0)
                                    fresh["xvfb"] = None
                                    self._write_state(fresh)
                        if not active_jobs and not pending_jobs and ownership == "none":
                            if stopped_since is None:
                                stopped_since = time.monotonic()
                            elif (time.monotonic() - stopped_since) >= 10.0:
                                self.logger.info("Watchdog exiting cleanly after Blender remained stopped.")
                                break
                        else:
                            stopped_since = None
                except Exception as exc:
                    self.logger.exception("Watchdog loop error: %s", exc)
        finally:
            try:
                if self.cfg.watchdog_pid_path.exists():
                    self.cfg.watchdog_pid_path.unlink()
            except OSError:
                pass
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        return 0


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blender MCP Lifecycle Manager CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show lifecycle state without launching Blender")
    sub.add_parser("health", help="Probe addon health without resetting idle timer")
    sub.add_parser("gpu", help="Show NVIDIA GPU health and VRAM snapshot")
    sub.add_parser("start", help="Ensure Blender is started and ready")

    stop_p = sub.add_parser("stop", help="Safely stop managed Blender")
    stop_p.add_argument("--force", action="store_true", help="Force stop even if jobs/dirty checks exist")
    stop_p.add_argument("--discard-session", action="store_true", help="Skip unsaved backup before stop")

    sub.add_parser("watchdog", help="Run background lifecycle watchdog")

    args = parser.parse_args(argv)
    mgr = BlenderLifecycleManager()

    if args.command == "status":
        print(json.dumps(mgr.status(probe_live=True), indent=2))
        return 0
    if args.command == "health":
        st = mgr.status(probe_live=True)
        print(
            json.dumps(
                {
                    "healthy": bool(st["addon_reachable"]),
                    "state": st["state"],
                    "ownership": st["ownership"],
                    "blender_pid": st["blender_pid"],
                    "addon_info": st["addon_info"],
                },
                indent=2,
            )
        )
        return 0 if st["addon_reachable"] else 1
    if args.command == "gpu":
        snap = get_gpu_snapshot()
        plan = select_launch_gpu_config(mgr.cfg, snapshot=snap)
        print(json.dumps({"snapshot": snap, "launch_plan": plan.decision, "ok": plan.ok}, indent=2))
        return 0 if plan.ok else 1
    if args.command == "start":
        res = mgr.ensure_ready(reason="cli_start")
        print(json.dumps(res, indent=2))
        return 0
    if args.command == "stop":
        res = mgr.stop_blender(reason="cli_stop", force=args.force, discard_session=args.discard_session)
        print(json.dumps(res, indent=2))
        return 0 if res.get("stopped") else 1
    if args.command == "watchdog":
        return mgr.run_watchdog()
    return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
