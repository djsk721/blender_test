#!/usr/bin/env python3
"""Mandatory Local Model & Coexistence Preflight for Qwen3.8-Flash-Next.

Inspects Strata inference server, OpenCode registry, GPU/RAM headroom,
exact MCP/context token overhead (explaining `cache.read: 66816`), and executes
8 small real capability tests sequentially on `strata/sc117-qwen38-abliterated-iq3_s`
and isolated Blender MCP port 9878 without disturbing port 9876 or Strata.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blender_orchestration.blender_toolkit import collect_resource_snapshot  # noqa: E402
from blender_orchestration.mcp_adapter import MCPAdapter, StdioMCPTransport  # noqa: E402
from blender_orchestration.multimodal_qa import _extract_json_object, parse_opencode_json_events  # noqa: E402
from blender_orchestration.rubric_evaluator import (  # noqa: E402
    ISOLATED_NO_MCP_OPENCODE_CONFIG,
    BlindRubricEvaluator,
    invoke_isolated_opencode_vision,
)
from blender_orchestration.sequential_benchmark import BlenderSessionResetter  # noqa: E402

EXPECTED_OPENCODE_MODEL_ID = "strata/sc117-qwen38-abliterated-iq3_s"
STRATA_MODELS_URL = "http://127.0.0.1:8080/v1/models"
STRATA_PROFILE_PATH = Path.home() / ".local/share/strata/install/strata-sc117-iq3_s-clean.json"
GLOBAL_OPENCODE_CONFIG_PATH = Path.home() / ".config/opencode/opencode.json"
ISOLATED_PORT = 9878
RUNTIME_DIR = PROJECT_ROOT / "output/autonomous_harness/_mcp_runtime"
PREFLIGHT_OUT = PROJECT_ROOT / "output/autonomous_harness/qwen_adaptive_base_mesh/preflight/preflight_report.json"
RESOURCE_OUT = PROJECT_ROOT / "output/autonomous_harness/RESOURCE_PROFILE.json"


def _fetch_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_blender_only_opencode_config(port: int, runtime_dir: Path) -> dict[str, Any]:
    cfg = json.loads(json.dumps(ISOLATED_NO_MCP_OPENCODE_CONFIG))
    cfg["mcp"]["servers"]["blender"] = {
        "type": "local",
        "command": ["python3", "-u", str(PROJECT_ROOT / ".opencode/mcp/blender/mcp_wrapper.py")],
        "cwd": str(PROJECT_ROOT),
        "environment": {
            "BLENDER_HOST": "127.0.0.1",
            "BLENDER_PORT": str(port),
            "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode/mcp/blender/config.json"),
            "BLENDER_MCP_RUNTIME_DIR": str(runtime_dir),
            "BLENDER_MCP_IDLE_TIMEOUT": "600",
            "BLENDER_MCP_WATCHDOG_POLL": "1.0",
            "BLENDER_MCP_AUTO_RESTORE": "false",
        },
        "disabled": False,
        "timeout": {"startup": 60000, "catalog": 120000, "execution": 180000},
    }
    return cfg


def _run_opencode(
    prompt: str,
    *,
    model_id: str,
    cwd: Path,
    files: list[Path] | None = None,
    timeout_sec: int = 120,
    title: str = "Qwen Preflight",
) -> dict[str, Any]:
    cmd = [
        "opencode",
        "run",
        "--standalone",
        "--archived",
        "--format",
        "json",
        "-m",
        model_id,
        "--title",
        title,
    ]
    for f in (files or []):
        rp = Path(f).resolve()
        if rp.is_file():
            cmd.extend(["--file", str(rp)])
    cmd.append(prompt)
    t0 = time.monotonic()
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout_sec, check=False)
    dur = round(time.monotonic() - t0, 3)
    parsed = parse_opencode_json_events(res.stdout)
    return {
        "returncode": res.returncode,
        "duration_sec": dur,
        "session_id": parsed["session_id"],
        "text": parsed["text"],
        "tool_uses": parsed["tool_uses"],
        "tokens": parsed["tokens"],
        "compactions": parsed["compactions"],
        "errors": parsed["errors"],
        "stderr": (res.stderr or "")[-300:],
    }


def _mcp_env(port: int, runtime_dir: Path) -> dict[str, str]:
    return {
        "BLENDER_HOST": "127.0.0.1",
        "BLENDER_PORT": str(port),
        "BLENDER_MCP_CONFIG": str(PROJECT_ROOT / ".opencode/mcp/blender/config.json"),
        "BLENDER_MCP_RUNTIME_DIR": str(runtime_dir),
        "BLENDER_MCP_IDLE_TIMEOUT": "600",
        "BLENDER_MCP_WATCHDOG_POLL": "1.0",
        "BLENDER_MCP_AUTO_RESTORE": "false",
    }


def main() -> int:
    PREFLIGHT_OUT.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "expected_opencode_model_id": EXPECTED_OPENCODE_MODEL_ID,
        "tests": {},
    }

    # 0. Inspect Strata & Global OpenCode Registry (read-only)
    strata_models = _fetch_json(STRATA_MODELS_URL)
    strata_profile = json.loads(STRATA_PROFILE_PATH.read_text(encoding="utf-8")) if STRATA_PROFILE_PATH.is_file() else {}
    global_cfg = json.loads(GLOBAL_OPENCODE_CONFIG_PATH.read_text(encoding="utf-8")) if GLOBAL_OPENCODE_CONFIG_PATH.is_file() else {}
    strata_provider_cfg = ((global_cfg.get("provider") or global_cfg.get("providers") or {}).get("strata") or {}).get("models", {}).get("sc117-qwen38-abliterated-iq3_s", {})
    report["backend_inspection"] = {
        "strata_api_models": strata_models,
        "strata_profile_summary": {
            "model_name": strata_profile.get("model_name"),
            "backend": strata_profile.get("backend"),
            "gpu": strata_profile.get("gpu"),
            "gpu_roles": strata_profile.get("gpu_roles"),
            "parallel": strata_profile.get("parallel"),
            "vision": strata_profile.get("vision"),
            "reasoning_effort": strata_profile.get("reasoning_effort"),
            "reasoning_budget_tokens": strata_profile.get("reasoning_budget_tokens"),
            "args": strata_profile.get("args"),
        },
        "opencode_model_cfg": strata_provider_cfg,
        "context_discrepancy": {
            "opencode_declared_context": (strata_provider_cfg.get("limit") or {}).get("context"),
            "opencode_declared_output": (strata_provider_cfg.get("limit") or {}).get("output"),
            "strata_actual_max_context": (strata_models.get("data") or [{}])[0].get("max_context_length"),
            "strata_kv_resident": 16384,
            "recommended_prompt_budget_tokens": 12000,
        },
    }

    res_before = collect_resource_snapshot()

    # Context Overhead Measurement: Global vs Isolated No-MCP vs Blender-Only
    context_bench: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="qwen_nomcp_") as nomcp_dir:
        (Path(nomcp_dir) / "opencode.json").write_text(json.dumps(ISOLATED_NO_MCP_OPENCODE_CONFIG, indent=2), encoding="utf-8")
        r_nomcp = _run_opencode('Respond ONLY with {"ok": true, "mode": "no_mcp"}', model_id=EXPECTED_OPENCODE_MODEL_ID, cwd=Path(nomcp_dir), title="Preflight Context NoMCP")
        context_bench["isolated_no_mcp"] = {
            "returncode": r_nomcp["returncode"],
            "session_id": r_nomcp["session_id"],
            "tokens": r_nomcp["tokens"],
            "compactions": r_nomcp["compactions"],
            "duration_sec": r_nomcp["duration_sec"],
            "text_preview": r_nomcp["text"][:160],
        }

    with tempfile.TemporaryDirectory(prefix="qwen_blendermcp_") as bmcp_dir:
        (Path(bmcp_dir) / "opencode.json").write_text(
            json.dumps(_build_blender_only_opencode_config(ISOLATED_PORT, RUNTIME_DIR), indent=2),
            encoding="utf-8",
        )
        r_bmcp = _run_opencode(
            'Do not call tools unless requested; respond ONLY with {"ok": true, "mode": "blender_only_mcp"}',
            model_id=EXPECTED_OPENCODE_MODEL_ID,
            cwd=Path(bmcp_dir),
            title="Preflight Context BlenderOnly",
        )
        context_bench["isolated_blender_only_mcp"] = {
            "returncode": r_bmcp["returncode"],
            "session_id": r_bmcp["session_id"],
            "tokens": r_bmcp["tokens"],
            "compactions": r_bmcp["compactions"],
            "duration_sec": r_bmcp["duration_sec"],
            "text_preview": r_bmcp["text"][:160],
        }

        # Test 2: Structured tool calling with Blender-Only MCP
        r_tool = _run_opencode(
            "Call the `get_addon_status` MCP tool with `user_prompt='Preflight check'`, then return ONLY JSON {\"tool_called\": true, \"summary\": \"...\"}.",
            model_id=EXPECTED_OPENCODE_MODEL_ID,
            cwd=Path(bmcp_dir),
            timeout_sec=150,
            title="Preflight Test 2 Tool Call",
        )
        report["tests"]["test_02_structured_tool_calling"] = {
            "passed": r_tool["returncode"] == 0 and len(r_tool["tool_uses"]) >= 1,
            "session_id": r_tool["session_id"],
            "tool_count": len(r_tool["tool_uses"]),
            "tools_invoked": [t.get("tool") for t in r_tool["tool_uses"]],
            "tokens": r_tool["tokens"],
            "compactions": r_tool["compactions"],
            "text_preview": r_tool["text"][:250],
            "errors": r_tool["errors"],
        }

    r_global = _run_opencode(
        'Do NOT call any tools. Respond ONLY with {"ok": true, "mode": "project_global_mcps"}',
        model_id=EXPECTED_OPENCODE_MODEL_ID,
        cwd=PROJECT_ROOT,
        title="Preflight Context Global",
    )
    context_bench["project_root_with_global_mcps"] = {
        "returncode": r_global["returncode"],
        "session_id": r_global["session_id"],
        "tokens": r_global["tokens"],
        "compactions": r_global["compactions"],
        "duration_sec": r_global["duration_sec"],
    }
    report["context_overhead_comparison"] = context_bench

    # Test 1, 3, 8 via isolated MCPAdapter on port 9878
    resetter = BlenderSessionResetter(project_root=PROJECT_ROOT, runtime_dir=RUNTIME_DIR, blender_port=ISOLATED_PORT)
    resetter.reset_for_next_model(start_fresh_blender=True)
    adapter = MCPAdapter(StdioMCPTransport(cwd=PROJECT_ROOT, env_overrides=_mcp_env(ISOLATED_PORT, RUNTIME_DIR)), reference_mode="TEXT_ONLY")
    res_during: dict[str, Any] = {}
    try:
        caps = adapter.discover_capabilities()
        report["tests"]["test_01_blender_mcp_discovery"] = {
            "passed": "execute_blender_code" in caps["tools"] and "get_addon_status" in caps["tools"],
            "server_info": caps["server_info"],
            "tools": caps["tools"],
        }

        # Test 3 + Test 4: Qwen authors multi-step bpy code + structured JSON handoff (no-MCP fast context)
        with tempfile.TemporaryDirectory(prefix="qwen_code_") as cdir:
            (Path(cdir) / "opencode.json").write_text(json.dumps(ISOLATED_NO_MCP_OPENCODE_CONFIG, indent=2), encoding="utf-8")
            r_handoff = _run_opencode(
                'Output ONLY valid JSON matching {"asset": "PreflightCrate", "height_m": 0.8, "parts": ["base", "bevel_rim"], "material_hex": "#3B6EA5"}.',
                model_id=EXPECTED_OPENCODE_MODEL_ID,
                cwd=Path(cdir),
                title="Preflight Test 4 JSON Handoff",
            )
            parsed_h = _extract_json_object(r_handoff["text"])
            report["tests"]["test_04_structured_handoff"] = {
                "passed": bool(parsed_h and "asset" in parsed_h and "parts" in parsed_h),
                "session_id": r_handoff["session_id"],
                "parsed": parsed_h,
                "tokens": r_handoff["tokens"],
                "compactions": r_handoff["compactions"],
            }

            r_code = _run_opencode(
                "Return ONLY one ```python ... ``` code block (no prose, no tools). Write Blender Python code using prebound `scene`, `col_export`, `bt` (`import blender_orchestration.blender_toolkit as bt`) to: "
                "1) create a quad sphere `SM_PreflightCore` centered at (0,0,0.4) with radii (0.25,0.25,0.35), "
                "2) create a brushed steel material `MAT_PreflightSteel` (`#6E7F91`, metallic=0.85, roughness=0.3) and assign it, "
                "3) unwrap UVs via `bt.smart_uv_unwrap(obj)`.",
                model_id=EXPECTED_OPENCODE_MODEL_ID,
                cwd=Path(cdir),
                title="Preflight Test 3 MultiStep bpy",
            )
            import re
            m = re.search(r"```(?:python|py)?\s*(.*?)```", r_code["text"] or "", re.DOTALL | re.IGNORECASE)
            py_code = m.group(1).strip() if m else ""
            exec_ok = False
            audit_data: dict[str, Any] = {}
            if py_code:
                wrapper = (
                    "import json, sys, traceback\n"
                    f"if {json.dumps(str(PROJECT_ROOT))} not in sys.path:\n"
                    f"    sys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
                    "import bpy\nfrom blender_orchestration import blender_toolkit as bt\n"
                    "scene = bt.ensure_clean_scene('SCENE_Preflight')\n"
                    "col_export = bt.get_or_create_collection(scene, 'COL_Export')\n"
                    f"_c = {json.dumps(py_code)}\n"
                    "exec(_c, {'bpy': bpy, 'bt': bt, 'scene': scene, 'col_export': col_export})\n"
                    "print('__ORCH_JSON__:' + json.dumps({'ok': True, 'audit': bt.get_scene_audit(scene)}))\n"
                )
                exec_res = adapter.execute_blender_json(wrapper, user_prompt="Preflight Test 3", stage="preflight_test_03")["parsed"]
                audit_data = exec_res.get("audit") or {}
                exec_ok = bool(exec_res.get("ok") and audit_data.get("mesh_count", 0) >= 1 and audit_data.get("material_count", 0) >= 1)
            report["tests"]["test_03_multistep_blender_ops"] = {
                "passed": exec_ok,
                "session_id": r_code["session_id"],
                "tokens": r_code["tokens"],
                "compactions": r_code["compactions"],
                "audit_summary": {k: audit_data.get(k) for k in ("mesh_count", "total_triangles", "material_count", "uv_missing_objects")},
            }

        # Test 8: Light Clay Render + Strata Coexistence
        ren_dir = PREFLIGHT_OUT.parent / "coexistence_render"
        ren_dir.mkdir(parents=True, exist_ok=True)
        res_during = collect_resource_snapshot()
        ren_res = adapter.execute_blender_json(
            f"import sys\nsys.path.insert(0, {json.dumps(str(PROJECT_ROOT))})\n"
            "import bpy, json\nfrom blender_orchestration import blender_toolkit as bt\n"
            "s = bpy.data.scenes['SCENE_Preflight']\n"
            f"r = bt.render_standard_views(s, {json.dumps(str(ren_dir))}, 'preflight', views=['three_quarter'], mode='clay', res=(384, 480), samples=8, use_gpu=True)\n"
            "print('__ORCH_JSON__:' + json.dumps(r))\n",
            user_prompt="Preflight coexistence render",
            stage="preflight_test_08_render",
        )["parsed"]
        strata_post = _fetch_json(STRATA_MODELS_URL)
        render_file = Path(((ren_res.get("renders") or [{}])[0]).get("path") or "")
        report["tests"]["test_08_blender_strata_coexistence"] = {
            "passed": render_file.is_file() and render_file.stat().st_size > 1000 and bool(strata_post.get("data")),
            "render_engine_device": ren_res.get("engine_device"),
            "render_path": str(render_file),
            "render_bytes": render_file.stat().st_size if render_file.is_file() else 0,
            "strata_healthy_after": bool(strata_post.get("data")),
        }
    finally:
        adapter.close()
        resetter.reset_for_next_model(start_fresh_blender=False)

    res_after = collect_resource_snapshot()

    # Test 5: Actual Image Understanding on images.jpg
    ref_img = PROJECT_ROOT / "images.jpg"
    vis_resp = invoke_isolated_opencode_vision(
        EXPECTED_OPENCODE_MODEL_ID,
        'Look closely at the attached image ONLY. Return ONLY JSON: {"subject_type": "...", "hair_color_and_style": "...", "main_clothing_colors": ["..."], "ear_shape": "...", "held_object_and_hand": "..."}',
        [ref_img],
        timeout_sec=150,
        title="Preflight Test 5 Vision Check",
    )
    vis_json = _extract_json_object(vis_resp["text"])
    vis_blob = json.dumps(vis_json or vis_resp["text"]).lower()
    vision_accurate = bool(
        vis_resp["returncode"] == 0
        and vis_json
        and any(k in vis_blob for k in ("blue", "azure", "cyan", "royal"))
        and any(k in vis_blob for k in ("blonde", "gold", "yellow"))
    )
    report["tests"]["test_05_actual_image_understanding"] = {
        "passed": vision_accurate,
        "session_id": vis_resp["session_id"],
        "tokens": vis_resp["tokens"],
        "compactions": vis_resp["compactions"],
        "parsed": vis_json,
        "raw_preview": vis_resp["text"][:300],
        "errors": vis_resp["errors"],
    }

    # Test 6: Independent Visual Review on historical iteration_003 renders
    i3_renders = [
        {"view": "front", "path": str(PROJECT_ROOT / "output/autonomous_harness/iteration_003/renders/final_studio/final_front.png")},
        {"view": "three_quarter", "path": str(PROJECT_ROOT / "output/autonomous_harness/iteration_003/renders/final_studio/final_three_quarter.png")},
    ]
    if all(Path(r["path"]).is_file() for r in i3_renders):
        ev = BlindRubricEvaluator(model_id=EXPECTED_OPENCODE_MODEL_ID, timeout_sec=180)
        rev = ev.evaluate(
            renders=i3_renders,
            producer_session_ids={"dummy_producer"},
            reference_mode="REFERENCE_GUIDED",
            approved_reference_path=ref_img,
            frozen_design_spec="Stylized blue-tunic blonde heroine holding a glowing cyan slate.",
            technical_gates_passed=True,
        )
        report["tests"]["test_06_independent_visual_review"] = {
            "passed": rev.get("verdict") in ("PASS", "FAIL") and bool(rev.get("observations_by_view")) and bool(rev.get("reviewer_session_id")),
            "verdict": rev.get("verdict"),
            "total_score": rev.get("total_score"),
            "reviewer_session_id": rev.get("reviewer_session_id"),
            "observations_count": len(rev.get("observations_by_view") or {}),
            "defects_count": len(rev.get("defects") or []),
            "tokens": rev.get("tokens"),
            "reason": rev.get("reason"),
        }
    else:
        report["tests"]["test_06_independent_visual_review"] = {"passed": False, "reason": "iteration_003 renders missing"}

    # Test 7: Context management check
    nomcp_in = int((context_bench["isolated_no_mcp"].get("tokens") or {}).get("input", 0))
    nomcp_cache = int(((context_bench["isolated_no_mcp"].get("tokens") or {}).get("cache") or {}).get("read", 0))
    all_compactions = sum(
        int(x.get("compactions", 0))
        for x in (
            context_bench["isolated_no_mcp"],
            context_bench["isolated_blender_only_mcp"],
            report["tests"]["test_04_structured_handoff"],
            report["tests"]["test_05_actual_image_understanding"],
        )
    )
    report["tests"]["test_07_context_management"] = {
        "passed": (nomcp_in + nomcp_cache) < 12000 and all_compactions == 0,
        "isolated_no_mcp_total_prompt_tokens": nomcp_in + nomcp_cache,
        "total_compactions": all_compactions,
    }

    report["all_tests_passed"] = all(bool(t.get("passed")) for t in report["tests"].values())

    resource_profile = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "before_preflight": res_before,
        "during_blender_session": res_during,
        "after_preflight": res_after,
        "gpu_safe_vram_threshold_mib": 6000,
        "recommended_blender_render_device": "CPU" if (res_before.get("min_free_vram_mib") or 0) < 6000 else "GPU",
        "recommended_qwen_execution": "sequential (parallel=1, prompt<=12000 tokens)",
    }

    PREFLIGHT_OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    RESOURCE_OUT.write_text(json.dumps(resource_profile, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_tests_passed": report["all_tests_passed"], "tests": {k: v.get("passed") for k, v in report["tests"].items()}, "context_overhead": context_bench}, indent=2))
    return 0 if report["all_tests_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
