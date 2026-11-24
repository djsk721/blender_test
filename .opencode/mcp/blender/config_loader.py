"""Configuration loading, dynamic path resolution, and file-only logging."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shlex
import shutil
from typing import Any


def _detect_project_root() -> Path:
    override = os.getenv("BLENDER_MCP_PROJECT_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    # Standard installed layout: <project_root>/.opencode/mcp/blender/config_loader.py
    if len(here.parents) >= 4 and here.parent.parent.name == "mcp" and here.parents[2].name == ".opencode":
        return here.parents[3]
    if len(here.parents) >= 3:
        return here.parents[2]
    return here.parent


PROJECT_ROOT = _detect_project_root()
DEFAULT_CONFIG_PATH = Path(__file__).resolve().with_name("config.json")


def _parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _parse_int(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_optional_int(value: Any) -> int | None:
    if value is None or str(value).strip().lower() in {"", "none", "null", "auto"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_float(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_cmd(value: Any, default: list[str]) -> list[str]:
    if not value:
        return list(default)
    if isinstance(value, list):
        return [str(x) for x in value if str(x)]
    raw = str(value).strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if str(x)]
        except json.JSONDecodeError:
            pass
    return shlex.split(raw)


def resolve_executable(name: str, configured: str = "auto", project_root: Path | None = None) -> str:
    """Resolve an executable path dynamically without hardcoded user paths."""
    base = project_root or PROJECT_ROOT
    if configured and configured != "auto":
        cfg_path = Path(configured)
        if not cfg_path.is_absolute():
            candidate = base / cfg_path
            if candidate.exists():
                return str(candidate)
        if cfg_path.exists():
            return str(cfg_path)
        found = shutil.which(configured)
        if found:
            return found
        raise FileNotFoundError(f"Configured executable not found: {configured}")

    found = shutil.which(name)
    if found:
        return found

    candidates = [
        Path.home() / ".local" / "bin" / name,
        Path("/usr/local/bin") / name,
        Path("/usr/bin") / name,
        Path("/bin") / name,
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise FileNotFoundError(f"Executable '{name}' not found in PATH or standard directories")


@dataclass
class GPUConfig:
    enabled: bool = True
    render_device: str = "AUTO"
    cycles_backend: str = "OPTIX"
    preferred_gpu_index: str | int = "auto"
    isolate_cuda_device: bool = True
    min_free_vram_warn_mib: int = 1024
    min_free_vram_error_mib: int = 512
    vram_low_policy: str = "cpu_fallback"
    unhealthy_gpu_policy: str = "cpu_fallback"
    max_safe_temp_c: int = 88
    expected_gpu_count: int | None = None
    cpu_software_viewport: bool = False


@dataclass
class BlenderMCPConfig:
    project_root: Path = PROJECT_ROOT
    config_path: Path = DEFAULT_CONFIG_PATH
    lazy_loading: bool = True
    blender_host: str = "127.0.0.1"
    blender_port: int = 9876
    idle_timeout_sec: float = 600.0
    startup_timeout_sec: float = 60.0
    shutdown_timeout_sec: float = 30.0
    readiness_poll_interval_sec: float = 0.25
    watchdog_poll_interval_sec: float = 2.0
    work_lock_timeout_sec: float = 240.0
    addon_probe_timeout_sec: float = 5.0
    auto_recovery: bool = True
    max_reconnect_retries: int = 2
    auto_backup: bool = True
    auto_restore_on_restart: bool = True
    max_backup_files: int = 10
    manage_external_blender: bool = False
    blender_executable: str = "auto"
    xvfb_executable: str = "auto"
    xvfb_screen: str = "1280x1024x24"
    upstream_mcp_command: list[str] = field(default_factory=lambda: ["uvx", "mcp-for-blender"])
    non_blender_tools: list[str] = field(
        default_factory=lambda: [
            "record_trajectory_feedback",
            "open_viewport",
            "viewport_latest",
        ]
    )
    gpu: GPUConfig = field(default_factory=GPUConfig)
    runtime_dir: Path = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "runtime"
    log_level: str = "INFO"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def active_jobs_dir(self) -> Path:
        return self.runtime_dir / "active_jobs"

    @property
    def pending_jobs_dir(self) -> Path:
        return self.runtime_dir / "pending_jobs"

    @property
    def backups_dir(self) -> Path:
        return self.runtime_dir / "backups"

    @property
    def state_path(self) -> Path:
        return self.runtime_dir / "state.json"

    @property
    def lifecycle_lock_path(self) -> Path:
        return self.runtime_dir / "lifecycle.lock"

    @property
    def work_lock_path(self) -> Path:
        return self.runtime_dir / "work.lock"

    @property
    def watchdog_lock_path(self) -> Path:
        return self.runtime_dir / "watchdog.lock"

    @property
    def watchdog_pid_path(self) -> Path:
        return self.runtime_dir / "watchdog.pid"


def _resolve_path(raw: str | Path, base: Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (base / p).resolve()


def load_config(config_path: str | Path | None = None) -> BlenderMCPConfig:
    """Load config.json and apply environment variable overrides."""
    project_root = _detect_project_root()
    env_cfg = os.getenv("BLENDER_MCP_CONFIG")
    raw_cfg_path = config_path or env_cfg or DEFAULT_CONFIG_PATH
    resolved_cfg_path = _resolve_path(raw_cfg_path, project_root)

    data: dict[str, Any] = {}
    if resolved_cfg_path.is_file():
        with resolved_cfg_path.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded

    gpu_raw = data.get("gpu") if isinstance(data.get("gpu"), dict) else {}

    gpu = GPUConfig(
        enabled=_parse_bool(gpu_raw.get("enabled"), True),
        render_device=str(
            os.getenv("BLENDER_RENDER_DEVICE", gpu_raw.get("render_device", "AUTO"))
        ).strip().upper(),
        cycles_backend=str(
            os.getenv("BLENDER_CYCLES_BACKEND", gpu_raw.get("cycles_backend", "OPTIX"))
        ).strip().upper(),
        preferred_gpu_index=os.getenv(
            "BLENDER_GPU_INDEX", str(gpu_raw.get("preferred_gpu_index", "auto"))
        ).strip(),
        isolate_cuda_device=_parse_bool(gpu_raw.get("isolate_cuda_device"), True),
        min_free_vram_warn_mib=_parse_int(gpu_raw.get("min_free_vram_warn_mib"), 1024),
        min_free_vram_error_mib=_parse_int(
            os.getenv("BLENDER_MIN_FREE_VRAM_MIB"),
            _parse_int(gpu_raw.get("min_free_vram_error_mib"), 512),
        ),
        vram_low_policy=str(
            os.getenv("BLENDER_VRAM_LOW_POLICY", gpu_raw.get("vram_low_policy", "cpu_fallback"))
        ).strip().lower(),
        unhealthy_gpu_policy=str(
            os.getenv("BLENDER_GPU_UNHEALTHY_POLICY", gpu_raw.get("unhealthy_gpu_policy", "cpu_fallback"))
        ).strip().lower(),
        max_safe_temp_c=_parse_int(
            os.getenv("BLENDER_GPU_MAX_TEMP_C"),
            _parse_int(gpu_raw.get("max_safe_temp_c"), 88),
        ),
        expected_gpu_count=_parse_optional_int(
            os.getenv("BLENDER_EXPECTED_GPU_COUNT", gpu_raw.get("expected_gpu_count"))
        ),
        cpu_software_viewport=_parse_bool(
            os.getenv("BLENDER_CPU_SOFTWARE_VIEWPORT"),
            bool(gpu_raw.get("cpu_software_viewport", False)),
        ),
    )

    if not gpu.enabled:
        gpu.render_device = "CPU"

    raw_runtime = os.getenv(
        "BLENDER_MCP_RUNTIME_DIR",
        data.get("runtime_dir", ".opencode/mcp/blender/runtime"),
    )
    runtime_dir = _resolve_path(raw_runtime, project_root)

    cfg = BlenderMCPConfig(
        project_root=project_root,
        config_path=resolved_cfg_path,
        lazy_loading=_parse_bool(os.getenv("BLENDER_MCP_LAZY_LOADING"), bool(data.get("lazy_loading", True))),
        blender_host=str(os.getenv("BLENDER_HOST", data.get("blender_host", "127.0.0.1"))).strip(),
        blender_port=_parse_int(os.getenv("BLENDER_PORT"), _parse_int(data.get("blender_port"), 9876)),
        idle_timeout_sec=_parse_float(
            os.getenv("BLENDER_MCP_IDLE_TIMEOUT"), _parse_float(data.get("idle_timeout_sec"), 600.0)
        ),
        startup_timeout_sec=_parse_float(
            os.getenv("BLENDER_MCP_STARTUP_TIMEOUT"), _parse_float(data.get("startup_timeout_sec"), 60.0)
        ),
        shutdown_timeout_sec=_parse_float(
            os.getenv("BLENDER_MCP_SHUTDOWN_TIMEOUT"), _parse_float(data.get("shutdown_timeout_sec"), 30.0)
        ),
        readiness_poll_interval_sec=_parse_float(
            os.getenv("BLENDER_MCP_READINESS_POLL"),
            _parse_float(data.get("readiness_poll_interval_sec"), 0.25),
        ),
        watchdog_poll_interval_sec=_parse_float(
            os.getenv("BLENDER_MCP_WATCHDOG_POLL"),
            _parse_float(data.get("watchdog_poll_interval_sec"), 2.0),
        ),
        work_lock_timeout_sec=_parse_float(
            os.getenv("BLENDER_MCP_WORK_LOCK_TIMEOUT"),
            _parse_float(data.get("work_lock_timeout_sec"), 240.0),
        ),
        addon_probe_timeout_sec=_parse_float(data.get("addon_probe_timeout_sec"), 5.0),
        auto_recovery=_parse_bool(os.getenv("BLENDER_MCP_AUTO_RECOVERY"), bool(data.get("auto_recovery", True))),
        max_reconnect_retries=_parse_int(data.get("max_reconnect_retries"), 2),
        auto_backup=_parse_bool(os.getenv("BLENDER_MCP_AUTO_BACKUP"), bool(data.get("auto_backup", True))),
        auto_restore_on_restart=_parse_bool(
            os.getenv("BLENDER_MCP_AUTO_RESTORE"), bool(data.get("auto_restore_on_restart", True))
        ),
        max_backup_files=_parse_int(data.get("max_backup_files"), 10),
        manage_external_blender=_parse_bool(
            os.getenv("BLENDER_MCP_MANAGE_EXTERNAL"), bool(data.get("manage_external_blender", False))
        ),
        blender_executable=str(os.getenv("BLENDER_EXECUTABLE", data.get("blender_executable", "auto"))).strip(),
        xvfb_executable=str(os.getenv("XVFB_EXECUTABLE", data.get("xvfb_executable", "auto"))).strip(),
        xvfb_screen=str(os.getenv("BLENDER_XVFB_SCREEN", data.get("xvfb_screen", "1280x1024x24"))).strip(),
        upstream_mcp_command=_parse_cmd(
            os.getenv("BLENDER_MCP_UPSTREAM_CMD"),
            data.get("upstream_mcp_command", ["uvx", "mcp-for-blender"]),
        ),
        non_blender_tools=list(
            data.get(
                "non_blender_tools",
                ["record_trajectory_feedback", "open_viewport", "viewport_latest"],
            )
        ),
        gpu=gpu,
        runtime_dir=runtime_dir,
        log_level=str(os.getenv("BLENDER_MCP_LOG_LEVEL", data.get("log_level", "INFO"))).strip().upper(),
    )
    ensure_runtime_dirs(cfg)
    return cfg


def ensure_runtime_dirs(cfg: BlenderMCPConfig) -> None:
    """Create runtime subdirectories if missing."""
    for directory in (
        cfg.runtime_dir,
        cfg.logs_dir,
        cfg.active_jobs_dir,
        cfg.pending_jobs_dir,
        cfg.backups_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def setup_logger(name: str, log_path: Path, level: str = "INFO") -> logging.Logger:
    """Create a file-only logger that never writes to stdout or stderr."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)
    logger.propagate = False

    resolved = str(log_path.resolve())
    for existing in logger.handlers:
        if isinstance(existing, RotatingFileHandler) and existing.baseFilename == resolved:
            existing.setLevel(numeric_level)
            return logger

    handler = RotatingFileHandler(resolved, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setLevel(numeric_level)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s (pid=%(process)d): %(message)s"))
    logger.addHandler(handler)
    return logger
