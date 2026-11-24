"""Blender MCP lazy-loading and lifecycle management package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

_VERSION_FILE = Path(__file__).resolve().with_name("VERSION")
__version__ = (
    _VERSION_FILE.read_text(encoding="utf-8").strip()
    if _VERSION_FILE.is_file()
    else "0.1.0"
)

__all__ = [
    "BlenderLifecycleManager",
    "BlenderMCPConfig",
    "LifecycleState",
    "__version__",
    "load_config",
]


def __getattr__(name: str) -> Any:
    if name in {"BlenderMCPConfig", "load_config"}:
        from . import config_loader

        return getattr(config_loader, name)
    if name in {"BlenderLifecycleManager", "LifecycleState"}:
        from . import lifecycle_manager

        return getattr(lifecycle_manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
