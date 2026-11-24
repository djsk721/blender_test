"""Detached process spawning, PID/start_time identity verification, and cleanup."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import time
from typing import Any, Callable

from .config_loader import BlenderMCPConfig, resolve_executable


@dataclass
class ProcessRecord:
    pid: int
    pgid: int
    start_time: int
    cmdline: str = ""
    display: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProcessRecord | None":
        if not isinstance(data, dict):
            return None
        pid = data.get("pid")
        start_time = data.get("start_time")
        if not isinstance(pid, int) or pid <= 0 or not isinstance(start_time, int) or start_time <= 0:
            return None
        pgid = data.get("pgid")
        return cls(
            pid=pid,
            pgid=pgid if isinstance(pgid, int) and pgid > 0 else pid,
            start_time=start_time,
            cmdline=str(data.get("cmdline") or ""),
            display=data.get("display"),
        )


def read_proc_stat(pid: int) -> tuple[str, int] | None:
    """Return (state, start_time_ticks) from /proc/<pid>/stat, or None if missing."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    rparen = raw.rfind(")")
    if rparen == -1:
        return None
    rest = raw[rparen + 2 :].split()
    if len(rest) < 20:
        return None
    state = rest[0]
    try:
        start_time = int(rest[19])
    except ValueError:
        return None
    return state, start_time


def get_proc_start_time(pid: int) -> int | None:
    info = read_proc_stat(pid)
    return info[1] if info else None


def is_pid_alive(pid: int) -> bool:
    """Return True if pid exists and is not a zombie/dead task."""
    if not pid or pid <= 0:
        return False
    info = read_proc_stat(pid)
    if info is None:
        return False
    state, _ = info
    return state not in {"Z", "X", "x"}


def is_same_process(record: ProcessRecord | None) -> bool:
    """Verify the PID is alive, not a zombie, and matches its recorded kernel start time."""
    if record is None or record.pid <= 0:
        return False
    info = read_proc_stat(record.pid)
    if info is None:
        return False
    state, start_time = info
    if state in {"Z", "X", "x"}:
        return False
    return start_time == record.start_time


def read_proc_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def get_process_record(pid: int, display: str | None = None) -> ProcessRecord | None:
    info = read_proc_stat(pid)
    if info is None or info[0] in {"Z", "X", "x"}:
        return None
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = pid
    return ProcessRecord(
        pid=pid,
        pgid=pgid,
        start_time=info[1],
        cmdline=read_proc_cmdline(pid),
        display=display,
    )


def find_listening_pid_for_port(port: int) -> int | None:
    """Best-effort lookup of the PID listening on `port` via `ss -lntp`."""
    ss_bin = shutil.which("ss")
    if not ss_bin:
        return None
    try:
        proc = subprocess.run(
            [ss_bin, "-lntp"],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        if proc.returncode != 0:
            return None
        needle = f":{port}"
        for line in proc.stdout.splitlines():
            if needle not in line:
                continue
            if not re.search(rf":{port}(?:\s|$)", line):
                continue
            m = re.search(r"pid=(\d+)", line)
            if m:
                return int(m.group(1))
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def spawn_detached(
    cmd: list[str],
    env: dict[str, str],
    log_path: Path,
    pass_fds: tuple[int, ...] = (),
) -> tuple[int, int, int]:
    """Spawn a completely detached, session-leading process without leaving zombies."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    r_pipe, w_pipe = os.pipe()
    os.set_inheritable(w_pipe, False)
    os.set_inheritable(r_pipe, False)

    pid1 = os.fork()
    if pid1 == 0:
        try:
            os.close(r_pipe)
            os.setsid()
            pid2 = os.fork()
            if pid2 > 0:
                os.write(w_pipe, f"{pid2}\n".encode("ascii"))
                os.close(w_pipe)
                os._exit(0)

            os.close(w_pipe)
            devnull_fd = os.open(os.devnull, os.O_RDONLY)
            log_fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.dup2(devnull_fd, 0)
                os.dup2(log_fd, 1)
                os.dup2(log_fd, 2)
            finally:
                if devnull_fd > 2:
                    os.close(devnull_fd)
                if log_fd > 2:
                    os.close(log_fd)

            keep_fds = {0, 1, 2, *pass_fds}
            for fd in sorted(pass_fds):
                os.set_inheritable(fd, True)

            try:
                open_fds = [int(name) for name in os.listdir("/proc/self/fd") if name.isdigit()]
            except OSError:
                open_fds = list(range(3, 1024))
            for fd in open_fds:
                if fd not in keep_fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

            os.execvpe(cmd[0], cmd, env)
        except BaseException as exc:
            try:
                os.write(2, f"spawn_detached failed for {cmd!r}: {exc}\n".encode("utf-8", errors="replace"))
            except OSError:
                pass
            os._exit(127)

    os.close(w_pipe)
    buf = b""
    try:
        while True:
            chunk = os.read(r_pipe, 64)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
    finally:
        os.close(r_pipe)
        try:
            os.waitpid(pid1, 0)
        except ChildProcessError:
            pass

    line = buf.decode("ascii", errors="replace").strip()
    if not line:
        raise RuntimeError(f"Failed to launch detached process for {cmd!r}")
    pid2 = int(line.splitlines()[0])

    start_time = get_proc_start_time(pid2)
    if start_time is None:
        time.sleep(0.05)
        start_time = get_proc_start_time(pid2)
    if start_time is None:
        raise RuntimeError(f"Spawned process {pid2} exited before proc inspection")

    try:
        pgid = os.getpgid(pid2)
    except OSError:
        pgid = pid2
    return pid2, pgid, start_time


def start_xvfb(cfg: BlenderMCPConfig, log_path: Path) -> ProcessRecord:
    """Launch a dedicated Xvfb with -displayfd and return its ProcessRecord."""
    xvfb_bin = resolve_executable("Xvfb", cfg.xvfb_executable, project_root=cfg.project_root)
    r_fd, w_fd = os.pipe()
    os.set_inheritable(w_fd, True)
    env = os.environ.copy()

    pid = -1
    pgid = -1
    start_time = 0
    try:
        cmd = [
            xvfb_bin,
            "-displayfd",
            str(w_fd),
            "-screen",
            "0",
            cfg.xvfb_screen,
            "-nolisten",
            "tcp",
        ]
        pid, pgid, start_time = spawn_detached(cmd, env, log_path, pass_fds=(w_fd,))
    finally:
        os.close(w_fd)

    buf = b""
    deadline = time.monotonic() + 10.0
    try:
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            ready, _, _ = select.select([r_fd], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(r_fd, 64)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
    finally:
        os.close(r_fd)

    display_num = buf.decode("ascii", errors="replace").strip()
    if not display_num.isdigit():
        _terminate_pid_group(pid, pgid, start_time or None, timeout_sec=3.0)
        raise RuntimeError(f"Xvfb failed to report display number (got {display_num!r}); see {log_path}")

    display = f":{display_num}"
    record = ProcessRecord(
        pid=pid,
        pgid=pgid,
        start_time=start_time,
        cmdline=read_proc_cmdline(pid) or f"{xvfb_bin} {display}",
        display=display,
    )
    if not is_same_process(record):
        raise RuntimeError(f"Xvfb pid {pid} exited unexpectedly; see {log_path}")
    return record


def start_blender(
    cfg: BlenderMCPConfig,
    display: str,
    env_overrides: dict[str, str],
    log_path: Path,
) -> ProcessRecord:
    """Launch Blender under the given Xvfb display and GPU/port environment."""
    blender_bin = resolve_executable("blender", cfg.blender_executable, project_root=cfg.project_root)
    env = os.environ.copy()
    env.pop("XAUTHORITY", None)
    env["DISPLAY"] = display
    env["BLENDER_HOST"] = cfg.blender_host
    env["BLENDER_PORT"] = str(cfg.blender_port)
    env.update(env_overrides)

    pid, pgid, start_time = spawn_detached([blender_bin], env, log_path)
    return ProcessRecord(
        pid=pid,
        pgid=pgid,
        start_time=start_time,
        cmdline=read_proc_cmdline(pid) or blender_bin,
        display=display,
    )


def _signal_target(pid: int, pgid: int, sig: int) -> None:
    if pgid and pgid > 1:
        try:
            os.killpg(pgid, sig)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            pass
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _terminate_pid_group(
    pid: int,
    pgid: int,
    expected_start_time: int | None,
    timeout_sec: float,
) -> tuple[bool, bool]:
    """Send SIGTERM then SIGKILL after timeout to a verified PID/PGID."""
    if not pid or pid <= 0:
        return True, False
    rec = ProcessRecord(pid=pid, pgid=pgid or pid, start_time=expected_start_time or 0)
    if expected_start_time:
        if not is_same_process(rec):
            return True, False
    elif not is_pid_alive(pid):
        return True, False

    _signal_target(pid, pgid or pid, signal.SIGTERM)
    deadline = time.monotonic() + max(0.5, timeout_sec)
    while time.monotonic() < deadline:
        alive = is_same_process(rec) if expected_start_time else is_pid_alive(pid)
        if not alive:
            return True, False
        time.sleep(0.15)

    _signal_target(pid, pgid or pid, signal.SIGKILL)
    kill_deadline = time.monotonic() + 3.0
    while time.monotonic() < kill_deadline:
        alive = is_same_process(rec) if expected_start_time else is_pid_alive(pid)
        if not alive:
            return True, True
        time.sleep(0.1)

    still_alive = is_same_process(rec) if expected_start_time else is_pid_alive(pid)
    return (not still_alive), True


def _cleanup_x11_artifacts(display: str | None) -> None:
    if not display or not display.startswith(":"):
        return
    num = display[1:].split(".")[0]
    if not num.isdigit():
        return
    for artifact in (Path(f"/tmp/.X{num}-lock"), Path(f"/tmp/.X11-unix/X{num}")):
        try:
            if artifact.exists() or artifact.is_symlink():
                artifact.unlink()
        except OSError:
            pass


def stop_managed_processes(
    blender_rec: ProcessRecord | None,
    xvfb_rec: ProcessRecord | None,
    shutdown_timeout_sec: float,
    graceful_quit_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Safely shut down managed Blender and Xvfb processes using verified identity."""
    blender_stopped = True
    blender_forced = False
    graceful_used = False

    if blender_rec and is_same_process(blender_rec):
        if graceful_quit_fn is not None:
            try:
                if graceful_quit_fn():
                    graceful_used = True
                    grace_deadline = time.monotonic() + min(5.0, max(1.0, shutdown_timeout_sec * 0.25))
                    while time.monotonic() < grace_deadline:
                        if not is_same_process(blender_rec):
                            break
                        time.sleep(0.15)
            except Exception:
                pass

        if is_same_process(blender_rec):
            remaining_timeout = max(1.0, shutdown_timeout_sec - (5.0 if graceful_used else 0.0))
            blender_stopped, blender_forced = _terminate_pid_group(
                blender_rec.pid,
                blender_rec.pgid,
                blender_rec.start_time,
                timeout_sec=remaining_timeout,
            )

    xvfb_stopped = True
    xvfb_forced = False
    if xvfb_rec and is_same_process(xvfb_rec):
        xvfb_stopped, xvfb_forced = _terminate_pid_group(
            xvfb_rec.pid,
            xvfb_rec.pgid,
            xvfb_rec.start_time,
            timeout_sec=3.0,
        )

    if xvfb_stopped and xvfb_rec and xvfb_rec.display:
        _cleanup_x11_artifacts(xvfb_rec.display)

    return {
        "blender_stopped": blender_stopped,
        "blender_forced_kill": blender_forced,
        "graceful_quit_used": graceful_used,
        "xvfb_stopped": xvfb_stopped,
        "xvfb_forced_kill": xvfb_forced,
    }


def list_defunct_children() -> list[int]:
    """Inspect /proc to find any zombie children owned by the current process."""
    my_pid = os.getpid()
    zombies: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return zombies
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rparen = raw.rfind(")")
        if rparen == -1:
            continue
        rest = raw[rparen + 2 :].split()
        if len(rest) >= 2 and rest[0] == "Z":
            try:
                ppid = int(rest[1])
                if ppid == my_pid:
                    zombies.append(pid)
            except ValueError:
                pass
    return zombies


def cleanup_stale_pids_check() -> list[int]:
    """Return any defunct children for diagnostics."""
    return list_defunct_children()
