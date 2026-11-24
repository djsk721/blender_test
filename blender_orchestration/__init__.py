"""Reusable OpenCode Blender Skills orchestration package.

Implements upstream blender-skills Director routing, OpenCode native skill
discovery, skill execution evidence contracts, MCP schema adaptation,
asset-type QA profiles, evidence-backed visual QA repair loops, 11-stage
character production pipeline, and benchmark reproducibility manifests.
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
    evaluate_verified_character_gates,
)
from .production_pipeline import (
    PRODUCTION_STAGES,
    CharacterProductionPipeline,
    ProductionVerdictSummary,
    StageExecutionResult,
)
from .qa_profiles import (
    AssetQAProfileType,
    QACriterion,
    QAProfile,
    analyze_character_scene_metrics,
    detect_polygon_inflation,
    get_qa_profile,
    validate_profile_criteria_hygiene,
)
from .regression_fixtures import (
    BenchmarkIsolationError,
    BenchmarkIsolationGuard,
    build_zelda_regression_defects,
    evaluate_zelda_regression_case,
    load_zelda_regression_snapshot,
)
from .skill_registry import (
    BlenderDirector,
    SkillMetadata,
    SkillRegistry,
)
from .visual_qa import (
    BlenderFixAction,
    ImageObservation,
    RepairLoopPolicy,
    VisualDefect,
    VisualQARepairLoop,
    VisualReviewEvidence,
    compute_approval_policy_digest,
    hash_file_sha256,
    plan_blender_fixes_for_defects,
    verify_visual_review_evidence,
)

__version__ = "0.3.0"

__all__ = [
    "__version__",
    "AssetQAProfileType",
    "BenchmarkIsolationError",
    "BenchmarkIsolationGuard",
    "BlenderDirector",
    "BlenderFixAction",
    "CharacterProductionPipeline",
    "ImageObservation",
    "MCPAdapter",
    "MCPAdapterError",
    "MCPTransport",
    "MockMCPTransport",
    "PRODUCTION_STAGES",
    "PerformanceMetrics",
    "ProductionVerdictSummary",
    "QACriterion",
    "QAProfile",
    "RepairLoopPolicy",
    "SKILL_STATUSES",
    "SkillExecutionContract",
    "SkillMetadata",
    "SkillRegistry",
    "StageExecutionResult",
    "StdioMCPTransport",
    "ToolNotSupportedError",
    "VisualDefect",
    "VisualQARepairLoop",
    "VisualReviewEvidence",
    "analyze_character_scene_metrics",
    "build_contract_from_plan_item",
    "build_reproducibility_manifest",
    "build_zelda_regression_defects",
    "compute_approval_policy_digest",
    "detect_polygon_inflation",
    "evaluate_asset_qa",
    "evaluate_character_gates",
    "evaluate_skill_contract",
    "evaluate_verified_character_gates",
    "evaluate_zelda_regression_case",
    "get_qa_profile",
    "hash_file_sha256",
    "load_zelda_regression_snapshot",
    "plan_blender_fixes_for_defects",
    "validate_profile_criteria_hygiene",
    "verify_visual_review_evidence",
]
