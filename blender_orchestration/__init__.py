"""Reusable OpenCode Blender Skills orchestration package.

Implements upstream blender-skills Director routing, OpenCode native skill
discovery, skill execution evidence contracts, MCP schema adaptation,
3D anatomical character modeling, structural repair strategy enforcement,
independent multimodal visual QA, 11-stage pipeline & sequential benchmarking.
"""

from __future__ import annotations

from .character_modeling import (
    build_anatomical_body_costume_bpy,
    build_anatomical_hair_bpy,
    build_anatomical_hands_bpy,
    build_anatomical_head_face_bpy,
    build_structural_remodel_bpy,
)
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
from .multimodal_qa import (
    SEVEN_QA_DOMAINS,
    IndependentMultimodalReviewer,
    parse_opencode_json_events,
)
from .opencode_runner import (
    OpenCodeCharacterRunner,
    OpenCodeSessionResult,
    run_opencode_prompt,
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
    detect_2d_projection_or_primitive_shortcuts,
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
from .repair_strategies import (
    REQUIRED_STRATEGY_BY_DEFECT,
    RepairStrategyTier,
    StrategyValidationResult,
    select_strategy_for_defect,
    validate_repair_effectiveness,
)
from .sequential_benchmark import (
    BlenderSessionResetter,
    SequentialBenchmarkOrchestrator,
    SequentialBenchmarkState,
    composite_png_strip,
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

__version__ = "0.4.0"

__all__ = [
    "__version__",
    "AssetQAProfileType",
    "BenchmarkIsolationError",
    "BenchmarkIsolationGuard",
    "BlenderDirector",
    "BlenderFixAction",
    "BlenderSessionResetter",
    "CharacterProductionPipeline",
    "ImageObservation",
    "IndependentMultimodalReviewer",
    "MCPAdapter",
    "MCPAdapterError",
    "MCPTransport",
    "MockMCPTransport",
    "OpenCodeCharacterRunner",
    "OpenCodeSessionResult",
    "PRODUCTION_STAGES",
    "PerformanceMetrics",
    "ProductionVerdictSummary",
    "QACriterion",
    "QAProfile",
    "REQUIRED_STRATEGY_BY_DEFECT",
    "RepairLoopPolicy",
    "RepairStrategyTier",
    "SEVEN_QA_DOMAINS",
    "SKILL_STATUSES",
    "SequentialBenchmarkOrchestrator",
    "SequentialBenchmarkState",
    "SkillExecutionContract",
    "SkillMetadata",
    "SkillRegistry",
    "StageExecutionResult",
    "StdioMCPTransport",
    "StrategyValidationResult",
    "ToolNotSupportedError",
    "VisualDefect",
    "VisualQARepairLoop",
    "VisualReviewEvidence",
    "analyze_character_scene_metrics",
    "build_anatomical_body_costume_bpy",
    "build_anatomical_hair_bpy",
    "build_anatomical_hands_bpy",
    "build_anatomical_head_face_bpy",
    "build_contract_from_plan_item",
    "build_reproducibility_manifest",
    "build_structural_remodel_bpy",
    "build_zelda_regression_defects",
    "composite_png_strip",
    "compute_approval_policy_digest",
    "detect_2d_projection_or_primitive_shortcuts",
    "detect_polygon_inflation",
    "evaluate_asset_qa",
    "evaluate_character_gates",
    "evaluate_skill_contract",
    "evaluate_verified_character_gates",
    "evaluate_zelda_regression_case",
    "get_qa_profile",
    "hash_file_sha256",
    "load_zelda_regression_snapshot",
    "parse_opencode_json_events",
    "plan_blender_fixes_for_defects",
    "run_opencode_prompt",
    "select_strategy_for_defect",
    "validate_profile_criteria_hygiene",
    "validate_repair_effectiveness",
    "verify_visual_review_evidence",
]
