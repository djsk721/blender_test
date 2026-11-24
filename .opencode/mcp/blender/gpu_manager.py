"""Conservative NVIDIA GPU health, VRAM monitoring, and launch policy selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

from .config_loader import BlenderMCPConfig

_DRIVER_ERROR_MARKERS = (
    "unable to determine the device handle",
    "nv_err_gpu_is_lost",
    "gpu is lost",
    "failed to initialize nvml",
    "driver/library version mismatch",
    "no devices were found",
    "falling off the bus",
    "gsp rpc",
    "err!",
    "unknown error",
)

_ISOLATION_LIMITATION_NOTE = (
    "CUDA_VISIBLE_DEVICES scopes CUDA/OptiX compute devices only; it does not isolate "
    "OpenGL/GLX/EGL or Vulkan device selection on multi-GPU systems unless software "
    "rendering environment variables are explicitly applied."
)

_SOFTWARE_VULKAN_ICD = Path("/usr/share/vulkan/icd.d/lvp_icd.x86_64.json")


def _safe_int(raw: str, default: int = 0) -> int:
    text = raw.strip()
    if not text or text.upper() in {"N/A", "[N/A]", "-"}:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _safe_opt_int(raw: str) -> int | None:
    text = raw.strip()
    if not text or text.upper() in {"N/A", "[N/A]", "-", "[NOT SUPPORTED]"}:
        return None
    if any(m in text.lower() for m in _DRIVER_ERROR_MARKERS):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _count_proc_nvidia_gpus() -> int | None:
    gpu_dir = Path("/proc/driver/nvidia/gpus")
    if not gpu_dir.is_dir():
        return None
    try:
        return sum(1 for entry in gpu_dir.iterdir() if entry.is_dir())
    except OSError:
        return None


def _run_nvidia_smi(args: list[str], timeout_sec: float = 5.0) -> tuple[bool, str, str]:
    nvsmi = shutil.which("nvidia-smi")
    if not nvsmi:
        return False, "", "nvidia-smi not found in PATH"
    try:
        proc = subprocess.run(
            [nvsmi, *args],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if proc.returncode != 0:
            err = (stderr or stdout or f"nvidia-smi exited with code {proc.returncode}").strip()
            return False, stdout, err
        return True, stdout, stderr
    except subprocess.TimeoutExpired:
        return False, "", f"nvidia-smi timed out after {timeout_sec:.1f}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "", f"nvidia-smi execution failed: {exc}"


def _contains_driver_error(text: str) -> str | None:
    lower = text.lower()
    for marker in _DRIVER_ERROR_MARKERS:
        if marker in lower:
            return marker
    return None


def get_gpu_snapshot(timeout_sec: float = 5.0) -> dict[str, Any]:
    """Capture a lightweight GPU health, VRAM, and process snapshot via nvidia-smi."""
    ts = time.time()
    proc_gpu_count = _count_proc_nvidia_gpus()
    ok, gpu_out, gpu_err = _run_nvidia_smi(
        [
            "--query-gpu=index,name,uuid,driver_version,pci.bus_id,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory,temperature.gpu,pstate",
            "--format=csv,noheader,nounits",
        ],
        timeout_sec=timeout_sec,
    )
    if not ok:
        unavailable = "not found in path" in gpu_err.lower()
        return {
            "timestamp": ts,
            "nvidia_smi_available": False,
            "gpus": [],
            "apps": [],
            "health": {
                "status": "unavailable" if unavailable else "unhealthy",
                "reasons": [gpu_err or "nvidia-smi query failed"],
                "proc_nvidia_gpu_count": proc_gpu_count,
                "reported_gpu_count": 0,
                "healthy_gpu_count": 0,
            },
        }

    health_reasons: list[str] = []
    err_marker = _contains_driver_error(gpu_out + "\n" + gpu_err)
    if err_marker:
        health_reasons.append(f"nvidia-smi output reported driver/NVML error marker: {err_marker}")

    gpus: list[dict[str, Any]] = []
    uuid_to_index: dict[str, int] = {}
    for line in gpu_out.splitlines():
        raw_line = line.strip()
        if not raw_line:
            continue
        line_err = _contains_driver_error(raw_line)
        parts = [p.strip() for p in raw_line.split(",")]
        if len(parts) < 12:
            if line_err:
                health_reasons.append(f"Unparseable GPU line with error: {raw_line}")
            continue
        idx = _safe_int(parts[0], -1)
        if idx < 0:
            health_reasons.append(f"Invalid GPU index in row: {raw_line}")
            continue
        uuid = parts[2]
        uuid_to_index[uuid] = idx

        mem_total = _safe_opt_int(parts[5])
        mem_used = _safe_opt_int(parts[6])
        mem_free = _safe_opt_int(parts[7])
        util_gpu = _safe_opt_int(parts[8])
        util_mem = _safe_opt_int(parts[9])
        temp_c = _safe_opt_int(parts[10])
        pstate = parts[11]

        row_issues: list[str] = []
        if line_err:
            row_issues.append(f"driver error marker '{line_err}'")
        if mem_total is None or mem_total <= 0:
            row_issues.append("unreadable total VRAM")
        if mem_free is None or mem_free < 0:
            row_issues.append("unreadable free VRAM")
        if _contains_driver_error(pstate):
            row_issues.append(f"invalid pstate '{pstate}'")

        row_healthy = len(row_issues) == 0
        if not row_healthy:
            health_reasons.append(f"GPU {idx} unhealthy: {', '.join(row_issues)}")

        gpus.append(
            {
                "index": idx,
                "name": parts[1],
                "uuid": uuid,
                "driver_version": parts[3],
                "pci_bus_id": parts[4],
                "memory_total_mib": mem_total or 0,
                "memory_used_mib": mem_used or 0,
                "memory_free_mib": mem_free if row_healthy and mem_free is not None else 0,
                "utilization_gpu_pct": util_gpu or 0,
                "utilization_memory_pct": util_mem or 0,
                "temperature_c": temp_c,
                "pstate": pstate,
                "healthy": row_healthy,
                "health_issues": row_issues,
            }
        )

    if proc_gpu_count is not None and proc_gpu_count > len(gpus):
        health_reasons.append(
            f"/proc/driver/nvidia/gpus reports {proc_gpu_count} GPU(s), but nvidia-smi only returned {len(gpus)}."
        )

    healthy_count = sum(1 for g in gpus if g["healthy"])
    if not gpus or healthy_count == 0 or err_marker:
        status = "unhealthy"
    elif health_reasons or healthy_count < len(gpus):
        status = "degraded"
    else:
        status = "healthy"

    apps_by_key: dict[tuple[int, int | None], dict[str, Any]] = {}
    ok_apps, apps_out, _ = _run_nvidia_smi(
        [
            "--query-compute-apps=pid,process_name,used_memory,gpu_uuid",
            "--format=csv,noheader,nounits",
        ],
        timeout_sec=timeout_sec,
    )
    if ok_apps:
        for line in apps_out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            pid = _safe_int(parts[0], -1)
            if pid <= 0:
                continue
            gpu_uuid = parts[3]
            gpu_idx = uuid_to_index.get(gpu_uuid)
            apps_by_key[(pid, gpu_idx)] = {
                "pid": pid,
                "process_name": parts[1],
                "used_memory_mib": _safe_int(parts[2]),
                "gpu_uuid": gpu_uuid,
                "gpu_index": gpu_idx,
                "type": "C",
            }

    ok_pmon, pmon_out, _ = _run_nvidia_smi(["pmon", "-c", "1", "-s", "m"], timeout_sec=timeout_sec)
    if ok_pmon:
        for line in pmon_out.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cols = stripped.split()
            if len(cols) < 5:
                continue
            gpu_idx = _safe_int(cols[0], -1)
            pid = _safe_int(cols[1], -1)
            if gpu_idx < 0 or pid <= 0:
                continue
            proc_type = cols[2]
            fb_mib = _safe_int(cols[3], 0)
            cmd = " ".join(cols[5:]) if len(cols) > 5 else cols[-1]
            key = (pid, gpu_idx)
            if key in apps_by_key:
                apps_by_key[key]["type"] = proc_type
                if apps_by_key[key]["used_memory_mib"] == 0 and fb_mib > 0:
                    apps_by_key[key]["used_memory_mib"] = fb_mib
            else:
                apps_by_key[key] = {
                    "pid": pid,
                    "process_name": cmd,
                    "used_memory_mib": fb_mib,
                    "gpu_uuid": next((g["uuid"] for g in gpus if g["index"] == gpu_idx), ""),
                    "gpu_index": gpu_idx,
                    "type": proc_type,
                }

    return {
        "timestamp": ts,
        "nvidia_smi_available": True,
        "gpus": gpus,
        "apps": sorted(apps_by_key.values(), key=lambda item: (item["gpu_index"] or 0, item["pid"])),
        "health": {
            "status": status,
            "reasons": health_reasons,
            "proc_nvidia_gpu_count": proc_gpu_count,
            "reported_gpu_count": len(gpus),
            "healthy_gpu_count": healthy_count,
        },
    }


@dataclass
class LaunchGPUPlan:
    ok: bool
    effective_device: str
    effective_backend: str
    selected_gpu_index: int | None
    env_overrides: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    decision: dict[str, Any] = field(default_factory=dict)


def _format_gpu_summary(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    for gpu in snapshot.get("gpus", []):
        tag = "healthy" if gpu.get("healthy", True) else "UNHEALTHY"
        parts.append(
            f"GPU{gpu['index']}({gpu['name']}, {tag}): "
            f"{gpu['memory_used_mib']}/{gpu['memory_total_mib']} MiB used, "
            f"{gpu['memory_free_mib']} MiB free"
        )
    return "; ".join(parts) if parts else "no GPUs detected"


def _cpu_env(force_software_gl: bool) -> dict[str, str]:
    env = {"CUDA_VISIBLE_DEVICES": ""}
    if force_software_gl:
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        if _SOFTWARE_VULKAN_ICD.is_file():
            env["VK_ICD_FILENAMES"] = str(_SOFTWARE_VULKAN_ICD)
    return env


def _build_decision(
    *,
    requested_device: str,
    effective_device: str,
    effective_backend: str,
    selected_gpu: dict[str, Any] | None,
    health: dict[str, Any],
    fallback_triggered: bool,
    fallback_reason: str | None,
    warnings: list[str],
    error: str | None,
) -> dict[str, Any]:
    return {
        "timestamp": time.time(),
        "requested_device": requested_device,
        "effective_device": effective_device,
        "effective_backend": effective_backend,
        "selected_gpu_index": selected_gpu["index"] if selected_gpu else None,
        "selected_gpu_uuid": selected_gpu.get("uuid") if selected_gpu else None,
        "selected_gpu_free_vram_mib": selected_gpu.get("memory_free_mib") if selected_gpu else None,
        "health_status": health.get("status", "unknown"),
        "health_reasons": list(health.get("reasons") or []),
        "fallback_triggered": fallback_triggered,
        "fallback_reason": fallback_reason,
        "isolation_notes": _ISOLATION_LIMITATION_NOTE,
        "warnings": list(warnings),
        "error": error,
    }


def select_launch_gpu_config(
    cfg: BlenderMCPConfig,
    snapshot: dict[str, Any] | None = None,
) -> LaunchGPUPlan:
    """Conservatively check GPU health & VRAM before launching Blender.

    Never terminates, resets, or interferes with Strata, Ollama, or external GPU processes.
    """
    snap = snapshot if snapshot is not None else get_gpu_snapshot()
    gpu_cfg = cfg.gpu
    warnings: list[str] = []

    raw_health = snap.get("health") if isinstance(snap.get("health"), dict) else None
    gpus = list(snap.get("gpus", []))
    if raw_health is None:
        healthy_count = sum(1 for g in gpus if g.get("healthy", True))
        health = {
            "status": "healthy" if (snap.get("nvidia_smi_available") and healthy_count > 0) else "unavailable",
            "reasons": [],
            "proc_nvidia_gpu_count": None,
            "reported_gpu_count": len(gpus),
            "healthy_gpu_count": healthy_count,
        }
    else:
        health = {
            "status": raw_health.get("status", "unknown"),
            "reasons": list(raw_health.get("reasons") or []),
            "proc_nvidia_gpu_count": raw_health.get("proc_nvidia_gpu_count"),
            "reported_gpu_count": raw_health.get("reported_gpu_count", len(gpus)),
            "healthy_gpu_count": raw_health.get("healthy_gpu_count", 0),
        }

    if gpu_cfg.expected_gpu_count is not None:
        if len(gpus) < gpu_cfg.expected_gpu_count or health["healthy_gpu_count"] < gpu_cfg.expected_gpu_count:
            reason = (
                f"Expected {gpu_cfg.expected_gpu_count} healthy GPU(s), observed "
                f"{health['healthy_gpu_count']} healthy / {len(gpus)} reported."
            )
            if reason not in health["reasons"]:
                health["reasons"].append(reason)
            if health["status"] == "healthy":
                health["status"] = "degraded"

    if not gpu_cfg.enabled or gpu_cfg.render_device == "CPU":
        env = _cpu_env(gpu_cfg.cpu_software_viewport)
        decision = _build_decision(
            requested_device="CPU" if gpu_cfg.enabled else "DISABLED",
            effective_device="CPU",
            effective_backend="CPU",
            selected_gpu=None,
            health=health,
            fallback_triggered=False,
            fallback_reason=None,
            warnings=warnings,
            error=None,
        )
        return LaunchGPUPlan(
            ok=True,
            effective_device="CPU",
            effective_backend="CPU",
            selected_gpu_index=None,
            env_overrides=env,
            warnings=warnings,
            snapshot=snap,
            decision=decision,
        )

    # Reject or fall back to CPU when GPU health is uncertain/degraded/unhealthy/unavailable
    health_status = health.get("status", "unavailable")
    if not snap.get("nvidia_smi_available") or health_status in {"unhealthy", "degraded", "unavailable"}:
        reason_detail = "; ".join(health["reasons"]) or f"GPU health status is {health_status}."
        if gpu_cfg.render_device == "GPU" or gpu_cfg.unhealthy_gpu_policy == "fail":
            err = (
                f"Blocked GPU Blender launch because GPU health check failed ({health_status}): {reason_detail}. "
                "Existing LLM/GPU processes were preserved untouched."
            )
            decision = _build_decision(
                requested_device=gpu_cfg.render_device,
                effective_device="CPU",
                effective_backend="CPU",
                selected_gpu=None,
                health=health,
                fallback_triggered=False,
                fallback_reason=reason_detail,
                warnings=warnings,
                error=err,
            )
            return LaunchGPUPlan(
                ok=False,
                effective_device="CPU",
                effective_backend="CPU",
                selected_gpu_index=None,
                warnings=warnings,
                error=err,
                snapshot=snap,
                decision=decision,
            )

        warn_msg = (
            f"GPU health check reported '{health_status}' ({reason_detail}); "
            "falling back to CPU + software rendering without disturbing other GPU workloads."
        )
        warnings.append(warn_msg)
        env = _cpu_env(force_software_gl=True)
        decision = _build_decision(
            requested_device=gpu_cfg.render_device,
            effective_device="CPU",
            effective_backend="CPU",
            selected_gpu=None,
            health=health,
            fallback_triggered=True,
            fallback_reason=warn_msg,
            warnings=warnings,
            error=None,
        )
        return LaunchGPUPlan(
            ok=True,
            effective_device="CPU",
            effective_backend="CPU",
            selected_gpu_index=None,
            env_overrides=env,
            warnings=warnings,
            snapshot=snap,
            decision=decision,
        )

    # Filter out unhealthy or over-temperature GPUs
    eligible_gpus = []
    for g in gpus:
        if not g.get("healthy", True):
            continue
        temp = g.get("temperature_c")
        if temp is not None and temp >= gpu_cfg.max_safe_temp_c:
            warnings.append(
                f"Excluding GPU {g['index']} due to temperature {temp}C >= max_safe_temp_c {gpu_cfg.max_safe_temp_c}C."
            )
            continue
        eligible_gpus.append(g)

    if not eligible_gpus:
        reason_detail = "No healthy GPU within safe thermal bounds."
        if gpu_cfg.render_device == "GPU" or gpu_cfg.unhealthy_gpu_policy == "fail":
            err = f"Blocked GPU launch: {reason_detail}"
            decision = _build_decision(
                requested_device=gpu_cfg.render_device,
                effective_device="CPU",
                effective_backend="CPU",
                selected_gpu=None,
                health={**health, "status": "unhealthy", "reasons": [*health["reasons"], reason_detail]},
                fallback_triggered=False,
                fallback_reason=reason_detail,
                warnings=warnings,
                error=err,
            )
            return LaunchGPUPlan(
                ok=False,
                effective_device="CPU",
                effective_backend="CPU",
                selected_gpu_index=None,
                warnings=warnings,
                error=err,
                snapshot=snap,
                decision=decision,
            )
        warnings.append(f"{reason_detail} Falling back to CPU + software rendering.")
        env = _cpu_env(force_software_gl=True)
        decision = _build_decision(
            requested_device=gpu_cfg.render_device,
            effective_device="CPU",
            effective_backend="CPU",
            selected_gpu=None,
            health={**health, "status": "unhealthy", "reasons": [*health["reasons"], reason_detail]},
            fallback_triggered=True,
            fallback_reason=reason_detail,
            warnings=warnings,
            error=None,
        )
        return LaunchGPUPlan(
            ok=True,
            effective_device="CPU",
            effective_backend="CPU",
            selected_gpu_index=None,
            env_overrides=env,
            warnings=warnings,
            snapshot=snap,
            decision=decision,
        )

    selected: dict[str, Any] | None = None
    pref = str(gpu_cfg.preferred_gpu_index).strip().lower()
    if pref not in {"", "auto", "none"}:
        try:
            pref_idx = int(pref)
            selected = next((g for g in eligible_gpus if g["index"] == pref_idx), None)
            if selected is None:
                warnings.append(
                    f"Preferred GPU index {pref_idx} unavailable or unhealthy; auto-selecting healthy GPU with most free VRAM."
                )
        except ValueError:
            warnings.append(f"Invalid preferred_gpu_index {gpu_cfg.preferred_gpu_index!r}; auto-selecting.")

    if selected is None:
        selected = max(eligible_gpus, key=lambda g: (g["memory_free_mib"], -g["index"]))

    free_mib = int(selected["memory_free_mib"])
    idx = int(selected["index"])

    if free_mib < gpu_cfg.min_free_vram_error_mib:
        summary = _format_gpu_summary(snap)
        msg = (
            f"Insufficient free VRAM on selected GPU {idx} ({free_mib} MiB free < "
            f"minimum {gpu_cfg.min_free_vram_error_mib} MiB). Active GPUs: {summary}. "
            "Existing LLM/GPU processes were preserved untouched."
        )
        if gpu_cfg.vram_low_policy == "fail":
            decision = _build_decision(
                requested_device=gpu_cfg.render_device,
                effective_device="GPU",
                effective_backend=gpu_cfg.cycles_backend,
                selected_gpu=selected,
                health=health,
                fallback_triggered=False,
                fallback_reason=msg,
                warnings=warnings,
                error=msg,
            )
            return LaunchGPUPlan(
                ok=False,
                effective_device="GPU",
                effective_backend=gpu_cfg.cycles_backend,
                selected_gpu_index=idx,
                warnings=warnings,
                error=msg,
                snapshot=snap,
                decision=decision,
            )
        if gpu_cfg.vram_low_policy == "cpu_fallback":
            warnings.append(msg + " Falling back to CPU rendering.")
            env = _cpu_env(gpu_cfg.cpu_software_viewport)
            decision = _build_decision(
                requested_device=gpu_cfg.render_device,
                effective_device="CPU",
                effective_backend="CPU",
                selected_gpu=None,
                health=health,
                fallback_triggered=True,
                fallback_reason=msg,
                warnings=warnings,
                error=None,
            )
            return LaunchGPUPlan(
                ok=True,
                effective_device="CPU",
                effective_backend="CPU",
                selected_gpu_index=None,
                env_overrides=env,
                warnings=warnings,
                snapshot=snap,
                decision=decision,
            )
        warnings.append(msg + " Proceeding because vram_low_policy=warn.")
    elif free_mib < gpu_cfg.min_free_vram_warn_mib:
        warnings.append(
            f"Low free VRAM on GPU {idx}: {free_mib} MiB free "
            f"(warn threshold {gpu_cfg.min_free_vram_warn_mib} MiB)."
        )

    env_overrides: dict[str, str] = {}
    if gpu_cfg.isolate_cuda_device:
        env_overrides["CUDA_VISIBLE_DEVICES"] = str(idx)

    backend = gpu_cfg.cycles_backend if gpu_cfg.cycles_backend in {"CUDA", "OPTIX", "CPU"} else "OPTIX"
    decision = _build_decision(
        requested_device=gpu_cfg.render_device,
        effective_device="GPU",
        effective_backend=backend,
        selected_gpu=selected,
        health=health,
        fallback_triggered=False,
        fallback_reason=None,
        warnings=warnings,
        error=None,
    )
    return LaunchGPUPlan(
        ok=True,
        effective_device="GPU",
        effective_backend=backend,
        selected_gpu_index=idx,
        env_overrides=env_overrides,
        warnings=warnings,
        snapshot=snap,
        decision=decision,
    )


def get_pid_gpu_usage(pids: list[int], snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return GPU VRAM usage entries attributable to the given PIDs."""
    snap = snapshot if snapshot is not None else get_gpu_snapshot()
    pid_set = {p for p in pids if p and p > 0}
    matched = [app for app in snap.get("apps", []) if app.get("pid") in pid_set]
    total_mib = sum(int(app.get("used_memory_mib", 0)) for app in matched)
    return {
        "timestamp": snap.get("timestamp", time.time()),
        "nvidia_smi_available": snap.get("nvidia_smi_available", False),
        "matched_apps": matched,
        "total_mib": total_mib,
        "gpus": snap.get("gpus", []),
        "health": snap.get("health"),
    }


def verify_gpu_released(pids: list[int], timeout_sec: float = 5.0) -> dict[str, Any]:
    """Poll briefly after process exit to verify GPU allocations for `pids` are released."""
    pid_set = {p for p in pids if p and p > 0}
    if not pid_set:
        snap = get_gpu_snapshot()
        return {
            "released": True,
            "remaining_mib": 0,
            "matched_apps": [],
            "snapshot": snap,
        }

    deadline = time.monotonic() + max(0.5, timeout_sec)
    last_usage: dict[str, Any] = {}
    while True:
        snap = get_gpu_snapshot()
        usage = get_pid_gpu_usage(list(pid_set), snapshot=snap)
        last_usage = usage
        if not snap.get("nvidia_smi_available") or not usage["matched_apps"]:
            return {
                "released": True,
                "remaining_mib": 0,
                "matched_apps": [],
                "snapshot": snap,
            }
        if time.monotonic() >= deadline:
            break
        time.sleep(0.25)

    return {
        "released": False,
        "remaining_mib": last_usage.get("total_mib", 0),
        "matched_apps": last_usage.get("matched_apps", []),
        "snapshot": get_gpu_snapshot(),
    }
