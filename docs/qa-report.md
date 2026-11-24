# QA Verification Report (Post-Refactor)

## 1. Test Execution Summary

All verification suites ran after refactoring the implementation into `.opencode/mcp/blender/` and retiring the legacy `mcp/blender/` directory.

| Suite | Command | Result | Exit Code | Notes |
|---|---|---|---|---|
| Unit & GPU Safety + Installer | `python3 -m unittest -v tests/blender_mcp/test_unit_lifecycle.py tests/blender_mcp/test_installer.py` | **25/25 PASS** (`3.075s`, 0 skipped) | `0` | Includes `test_08_fresh_git_clone_deploy_smoke_update_uninstall` against committed `main` |
| Full Suite (Unit + Installer pre-commit + B01–B22) | `python3 -m unittest -v tests/blender_mcp/test_unit_lifecycle.py tests/blender_mcp/test_installer.py tests/blender_mcp/test_integration_qa.py` | **34/34 runnable PASS** (`177.170s`, 1 skipped pre-`git init`) | `0` | Covers B01–B22 real Blender/OpenCode/GPU checks |
| Real OpenCode E2E + Idle Auto-Shutdown | `BLENDER_MCP_IDLE_TIMEOUT=8 BLENDER_MCP_WATCHDOG_POLL=1 opencode --standalone run --auto --model strata/sc117-qwen38-abliterated-iq3_s#none --format json ...` | **PASS** | `0` | On-demand launch (`2.966s`), valid `get_scene_info` response, auto-stopped after 8s idle |

## 2. B01–B22 Integration Test Results

Observed in `test_integration_qa.py` against OpenCode `v2.2.7`, `mcp-for-blender` `v2.3.1`, Blender `5.3.0`, Xvfb, and dual NVIDIA RTX 3090:

| ID | Test Case | Status | Observed Evidence |
|---|---|---|---|
| B01 | Fresh OpenCode startup | **PASS** | `opencode --standalone run` succeeded with 0 Blender processes |
| B02 | OpenCode reload | **PASS** | `opencode reload` kept `blender` connected with 0 Blender processes |
| B03 | MCP `initialize` | **PASS** | `initialize` + `notifications/initialized` succeeded with 0 Blender processes |
| B04 | MCP `tools/list` | **PASS** | Returned 10 tools with original schemas and 0 Blender processes |
| B05 | General coding task | **PASS** | General Python query succeeded with 0 Blender processes |
| B06 | First Blender tool call | **PASS** | Auto-launched `pid=58285` (`startup=2.999s`, `first_call=4.129s`) |
| B07 | Basic 3D object creation | **PASS** | Created `QA_Test_Sphere` and verified via `get_scene_info` |
| B08 | Consecutive tool calls | **PASS** | Reused `pid=58285`, `boot_id=managed-58285-35007b8f` |
| B09 | Concurrent Blender requests | **PASS** | Serialized 2 parallel wrappers via `work.lock` on single Blender PID |
| B10 | Idle timeout exceeded | **PASS** | Health/`tools/list` probes did not reset timer; stopped after 12s idle |
| B11 | GPU VRAM release | **PASS** | `nvidia-smi` confirmed 0 Blender GPU processes after shutdown |
| B12 | Call after auto-shutdown | **PASS** | Same wrapper session transparently relaunched `pid=59119` |
| B13 | Idle timeout during active work | **PASS** | Stayed alive during 11.8s job, then stopped after idle |
| B14 | Unsaved work protection | **PASS** | Created verified backup when `auto_backup=true`; deferred stop when `false` |
| B15 | Unexpected Blender crash | **PASS** | After `SIGKILL(60838)`, next call auto-recovered with `pid=61187` |
| B16 | OpenCode reload mid-session | **PASS** | `opencode reload` preserved active `pid=61187` with no duplicates |
| B17 | Blender Skills workflow | **PASS** | `COL_Props`, `SM_QA_Crate_01`, `MAT_QA_Crate`, and `look` PNG capture succeeded |
| B18 | Xvfb & child cleanup | **PASS** | Zero managed Blender, Xvfb, or defunct processes after stop |
| B19 | Concurrent Local LLMs | **PASS** | `strata` (`30856`, `30884`) and `ollama` (`1611`) PIDs and HTTP APIs intact |
| B20 | External Blender instance | **PASS** | Adopted external Blender (`pid=61929`) and protected it from idle/stop |
| B21 | Wrapper crash & restart | **PASS** | Wrapper `SIGKILL` reaped stale marker and reused active `pid=62372` |
| B22 | Work file preservation & recovery | **PASS** | Original `.blend` SHA-256/mtime unchanged; verified restore + corrupt backup `failed` |

## 3. Real OpenCode E2E & GPU/VRAM Measurements

- **Pre-E2E**: `blender` processes = `0`; GPU 0 used `22,014 MiB` (`2,114 MiB` free); GPU 1 used `21,920 MiB` (`2,207 MiB` free); `strata` (`30856`, `30884`) & `ollama` (`1611`) running.
- **During E2E (`tools.blender.get_scene_info({})`)**:
  - Blender `pid=65343` started lazily on GPU 1 (`CUDA_VISIBLE_DEVICES=1`) in `2.966s`.
  - GPU 1 VRAM during Blender: `22,226 MiB` used (`1,901 MiB` free), Blender allocation `286 MiB` (`C+G`).
  - OpenCode response: `Scene: **Scene** — 3 objects total: 1 mesh (Cube, active/selected), 1 light, 1 camera.`
- **Post-E2E (12s after exit, 8s test idle timeout)**:
  - Watchdog stopped `pid=65343` and `Xvfb` `pid=65337`.
  - `blender` processes = `0`; `gpu_after_stop.released = true`, `remaining_mib = 0`.
  - GPU 1 returned to `21,920 MiB` used / `2,207 MiB` free.
  - `strata` (`30856`, `30884`) and `ollama` (`1611`) remained untouched.

## 4. Fresh-Clone Deployment & Global Config Isolation

- `test_08_fresh_git_clone_deploy_smoke_update_uninstall`: Cloned the committed repository to a temporary `opencode-blender-mcp-lifecycle` directory, ran `scripts/install.sh` into a target directory with spaces (`"Cloned Target With Spaces"`), verified `mcp_wrapper.py` answered `initialize` and `tools/list` without launching Blender, ran `scripts/update.sh`, and cleanly uninstalled with `scripts/uninstall.sh --yes`.
- Global `/home/jhkim/.config/opencode/opencode.json` SHA-256 verified unchanged before and after refactoring (`ebb9dccd2b296e7f68194cb385d3891de19bbc4968980e27bab5b35557fbac57`).
