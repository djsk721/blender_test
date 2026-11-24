# Configuration Reference

Configuration is stored in `.opencode/mcp/blender/config.json` inside the target project and can be overridden by environment variables (including inside `opencode.json`'s `mcp.servers.blender.environment`).

## Core Lifecycle & Timeout Settings

| Config Key | Environment Variable | Default | Description |
|---|---|---|---|
| `lazy_loading` | `BLENDER_MCP_LAZY_LOADING` | `true` | Enable/disable lazy Blender startup |
| `blender_host` | `BLENDER_HOST` | `"127.0.0.1"` | Blender addon TCP host |
| `blender_port` | `BLENDER_PORT` | `9876` | Blender addon TCP port |
| `idle_timeout_sec` | `BLENDER_MCP_IDLE_TIMEOUT` | `600` | Seconds of inactivity before auto-shutdown |
| `startup_timeout_sec` | `BLENDER_MCP_STARTUP_TIMEOUT` | `60` | Max seconds to wait for addon readiness |
| `shutdown_timeout_sec` | `BLENDER_MCP_SHUTDOWN_TIMEOUT` | `30` | Wait before escalating `SIGTERM` to `SIGKILL` |
| `readiness_poll_interval_sec` | `BLENDER_MCP_READINESS_POLL` | `0.25` | Polling interval during startup |
| `watchdog_poll_interval_sec` | `BLENDER_MCP_WATCHDOG_POLL` | `2.0` | Detached watchdog polling interval |
| `work_lock_timeout_sec` | `BLENDER_MCP_WORK_LOCK_TIMEOUT` | `240` | Max wait for concurrent `work.lock` acquisition |
| `auto_recovery` | `BLENDER_MCP_AUTO_RECOVERY` | `true` | Auto-relaunch after unexpected Blender exit |
| `max_reconnect_retries` | — | `2` | Max mid-call recovery retries in wrapper |
| `auto_backup` | `BLENDER_MCP_AUTO_BACKUP` | `true` | Save verified `.blend` backup before dirty idle shutdown |
| `auto_restore_on_restart` | `BLENDER_MCP_AUTO_RESTORE` | `true` | Reopen and verify prior session on relaunch |
| `max_backup_files` | — | `10` | Retention limit for `autosave_*.blend` backups |
| `manage_external_blender` | `BLENDER_MCP_MANAGE_EXTERNAL` | `false` | If `false`, externally started Blender instances are never killed |
| `blender_executable` | `BLENDER_EXECUTABLE` | `"auto"` | Path or `"auto"` discovery |
| `xvfb_executable` | `XVFB_EXECUTABLE` | `"auto"` | Path or `"auto"` discovery |
| `xvfb_screen` | `BLENDER_XVFB_SCREEN` | `"1280x1024x24"` | Virtual framebuffer geometry |
| `upstream_mcp_command` | `BLENDER_MCP_UPSTREAM_CMD` | `["uvx", "mcp-for-blender"]` | Upstream MCP server command |
| `non_blender_tools` | — | `["record_trajectory_feedback", "open_viewport", "viewport_latest"]` | Tools forwarded without starting Blender |
| `runtime_dir` | `BLENDER_MCP_RUNTIME_DIR` | `".opencode/mcp/blender/runtime"` | Runtime state, locks, logs, and backups directory |
| `log_level` | `BLENDER_MCP_LOG_LEVEL` | `"INFO"` | File logger verbosity |

## GPU & Safety Settings (`gpu.*`)

| Config Key | Environment Variable | Default | Description |
|---|---|---|---|
| `gpu.enabled` | — | `true` | If `false`, forces `CPU` mode |
| `gpu.render_device` | `BLENDER_RENDER_DEVICE` | `"AUTO"` | `"AUTO"`, `"GPU"`, or `"CPU"` |
| `gpu.cycles_backend` | `BLENDER_CYCLES_BACKEND` | `"OPTIX"` | `"OPTIX"` or `"CUDA"` |
| `gpu.preferred_gpu_index` | `BLENDER_GPU_INDEX` | `"auto"` | Specific GPU index or `"auto"` (max free VRAM) |
| `gpu.isolate_cuda_device` | — | `true` | Export `CUDA_VISIBLE_DEVICES=<selected_index>` |
| `gpu.min_free_vram_warn_mib` | — | `1024` | Emit warning below this free VRAM threshold |
| `gpu.min_free_vram_error_mib` | `BLENDER_MIN_FREE_VRAM_MIB` | `512` | Threshold triggering `vram_low_policy` |
| `gpu.vram_low_policy` | `BLENDER_VRAM_LOW_POLICY` | `"cpu_fallback"` | `"cpu_fallback"`, `"warn"`, or `"fail"` |
| `gpu.unhealthy_gpu_policy` | `BLENDER_GPU_UNHEALTHY_POLICY` | `"cpu_fallback"` | `"cpu_fallback"` or `"fail"` when GPU health check fails |
| `gpu.max_safe_temp_c` | `BLENDER_GPU_MAX_TEMP_C` | `88` | Exclude GPUs at or above this temperature (°C) |
| `gpu.expected_gpu_count` | `BLENDER_EXPECTED_GPU_COUNT` | `null` | Optional expected healthy GPU count (`null` disables) |
| `gpu.cpu_software_viewport` | `BLENDER_CPU_SOFTWARE_VIEWPORT` | `false` | Force `LIBGL_ALWAYS_SOFTWARE=1` in explicit `CPU` mode |
