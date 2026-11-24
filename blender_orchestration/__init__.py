"""Reusable OpenCode Blender Skills orchestration package.

Implements upstream blender-skills Director routing, OpenCode native skill
discovery, skill execution evidence contracts, MCP schema adaptation,
idempotent scene operations, and benchmark reproducibility manifests.
"""

from __future__ import annotations

from .contracts import (
    SKILL_STATUSES,
    SkillExecutionContract,
    build_contract_from_plan_item,
    evaluate_skill_contract,
)
from .mcp_adapter import (
    MCPAdapter,
    MCPAdapterError,
    MCPTransport,
    MockMCPTransport,
    StdioMCPTransport,
    ToolNotSupportedError,
)
from .pipeline import (
    PerformanceMetrics,
    build_reproducibility_manifest,
    evaluate_asset_qa,
    evaluate_character_gates,
)
from .skill_registry import (
    BlenderDirector,
    SkillMetadata,
    SkillRegistry,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "BlenderDirector",
    "MCPAdapter",
    "MCPAdapterError",
    "MCPTransport",
    "MockMCPTransport",
    "PerformanceMetrics",
    "SKILL_STATUSES",
    "SkillExecutionContract",
    "SkillMetadata",
    "SkillRegistry",
    "StdioMCPTransport",
    "ToolNotSupportedError",
    "build_contract_from_plan_item",
    "build_reproducibility_manifest",
    "evaluate_asset_qa",
    "evaluate_character_gates",
    "evaluate_skill_contract",
]
