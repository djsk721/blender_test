# Blender MCP Lazy Loading and Lifecycle Management

## 1. Root Causes and Issues in the Previous Setup

The global OpenCode configuration (`~/.config/opencode/opencode.json`) previously configured `mcp.servers.blender` with an inline shell wrapper that ran `nohup setsid xvfb-run -a blender ... & sleep 5; exec uvx mcp-for-blender`:

- **Unconditional Eager Launch**: On every OpenCode startup or `opencode reload`, connecting to the `blender` MCP server launched Blender immediately—even during non-Blender sessions or `tools/list` discovery.
- **Constant Idle GPU/VRAM Occupation**: Blender stayed resident in memory holding ~286 MiB of GPU VRAM indefinitely.
- **Fixed `sleep 5` Without Readiness Verification**: Startup blocked blindly for 5 seconds instead of polling the addon TCP socket (`127.0.0.1:9876`).
- **No Idle Shutdown or Lifecycle Controls**: No Xvfb cleanup, crash recovery, concurrency serialization, active-job protection, or unsaved-scene backup existed.
- **Stale Upstream Socket After Restart**: `mcp-for-blender` caches a global `_blender_connection` TCP socket; if Blender restarts, the upstream MCP subprocess fails unless recycled.

---

## 2. Architecture Overview

Detailed module responsibilities are documented in [architecture.md](architecture.md).

```text
OpenCode (v2.2.7)
    │  stdio (JSON-RPC 2.0)
    ▼
.opencode/mcp/blender/mcp_wrapper.py
    ├── initialize / tools/list / non-Blender tools ──► Upstream mcp-for-blender
    └── Blender-backed tools & blender:// resources
            │
            ▼
        .opencode/mcp/blender/lifecycle_manager.py
            ├── process_manager.py (double-fork, PID+start_time checks, Xvfb -displayfd)
            ├── gpu_manager.py (health checks, VRAM selection, CPU fallback, release checks)
            └── AddonClient + flock locks + JobMarker + watchdog
            │
            ▼
        Existing mcp-for-blender (v2.3.1) ◄──127.0.0.1:9876──► Blender 5.3.0 + Addon v2.0
```

---

## 3. Repository Layout

- `.opencode/mcp/blender/` — Managed lazy-loading MCP wrapper, lifecycle manager, process manager, GPU safety manager, `config.json`, `VERSION`, `start.sh`, `stop.sh`
- `scripts/` — `install.sh`, `update.sh`, `uninstall.sh`
- `opencode.json` — Project-local OpenCode MCP override
- `tests/blender_mcp/` — Unit, GPU safety, installer, and B01–B22 integration tests
- `docs/` — Architecture, installation, configuration, GPU safety, migration, and QA reports

---

## 4. Lifecycle State Machine

`LifecycleState` tracks 7 states persisted atomically (`fsync` + `os.replace`) in `.opencode/mcp/blender/runtime/state.json`:

```text
STOPPED ──(Blender tool/resource call or start.sh)──────────► STARTING
   ▲                                                            │
   │ (stop_blender / idle_timeout completed)                    │ (ping + get_addon_info succeeded)
   │                                                            ▼
STOPPING ◄──(idle timeout / manual stop, guards passed)───── READY / IDLE
   ▲                                                            │   ▲
   │                                            (begin_work)    │   │ (finish_work: 0 active markers)
   │                                                            ▼   │
   └──────────────(crash cleanup)───────── ERROR ◄──(launch fail / crash detected)── BUSY
                                            │
                                            └──(auto_recovery=true subsequent call)──► STARTING
```

- **OpenCode Reload Resilience**: Detached Blender, Xvfb, and Watchdog processes survive wrapper restarts across `opencode reload`, preserving PID, `boot_id`, and idle timers.
- **External Blender Protection**: Unmanaged Blender instances on port `9876` are adopted as `ownership="external"` and never terminated by idle timeout or normal `stop` when `manage_external_blender=false`.

---

## 5. CLI & Operations

```bash
# Status (never launches Blender, never resets idle timer)
PYTHONPATH=.opencode/mcp python3 -m blender.lifecycle_manager status

# Health check (never resets idle timer)
PYTHONPATH=.opencode/mcp python3 -m blender.lifecycle_manager health

# GPU health & launch policy inspection
PYTHONPATH=.opencode/mcp python3 -m blender.lifecycle_manager gpu

# Manual start / safe shutdown
./.opencode/mcp/blender/start.sh
./.opencode/mcp/blender/stop.sh
```

See [installation.md](installation.md), [configuration.md](configuration.md), [gpu-safety.md](gpu-safety.md), and [qa-report.md](qa-report.md) for deployment, tuning, GPU safety policies, and measured QA results.
