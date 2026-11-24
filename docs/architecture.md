# Architecture

## Overview

`opencode-blender-mcp-lifecycle` decouples the lifetime of the OpenCode MCP stdio server (`mcp-for-blender`) from the lifetime of the Blender GUI + Xvfb process and NVIDIA GPU allocations.

```text
OpenCode Client
    │
    │ stdio (newline-delimited UTF-8 JSON-RPC 2.0)
    ▼
.opencode/mcp/blender/mcp_wrapper.py
    ├── Immediate stdout isolation (_PROTO_OUT; stray prints -> /dev/null)
    ├── Non-Blender requests:
    │     initialize, notifications/initialized, tools/list, ping,
    │     resources/list, prompts/list, record_trajectory_feedback,
    │     open_viewport, viewport_latest, ui://*
    │     └──► Forwarded straight to upstream mcp-for-blender (0 Blender launches)
    │
    └── Blender-backed requests:
          tools/call (get_scene_info, execute_blender_code, look, ...),
          resources/read (blender://*)
          │
          ├── 1. pending_jobs JobMarker (flock)
          ├── 2. work.lock (cross-session serialization)
          ├── 3. active_jobs JobMarker (flock)
          ├── 4. lifecycle.ensure_ready() ──► returns boot_id
          ├── 5. lifecycle.begin_work()
          ├── 6. Recycle upstream mcp-for-blender if boot_id changed
          ├── 7. Forward request + mid-call crash recovery retry
          └── 8. Release active_jobs JobMarker -> lifecycle.finish_work() -> release work.lock
```

## Module Responsibilities

| Module | Path | Responsibility |
|---|---|---|
| Package entry | `.opencode/mcp/blender/__init__.py` | Lazy attribute exports (`__version__`, config, lifecycle classes) |
| Config loader | `.opencode/mcp/blender/config_loader.py` | Dynamic `PROJECT_ROOT` detection, JSON + env loading, binary discovery, file-only logger |
| GPU manager | `.opencode/mcp/blender/gpu_manager.py` | `nvidia-smi` + `/proc/driver/nvidia/gpus` health inspection, GPU selection, CPU fallback, VRAM release verification |
| Process manager | `.opencode/mcp/blender/process_manager.py` | Zombie-free double-fork `spawn_detached`, `Xvfb -displayfd`, `/proc/<pid>/stat` identity checks, cleanup |
| Lifecycle manager | `.opencode/mcp/blender/lifecycle_manager.py` | 7-state atomic `state.json`, `AddonClient`, `JobMarker`, `scene_signature` dirty tracking, backup/restore, detached watchdog |
| MCP stdio wrapper | `.opencode/mcp/blender/mcp_wrapper.py` | JSON-RPC proxy, tool/resource routing, upstream `boot_id` recycling, crash recovery |

## Why Upstream `mcp-for-blender` Recycling Is Essential

`mcp-for-blender` caches a global `_blender_connection` TCP socket inside its process. If Blender shuts down due to idle timeout or crashes and a new Blender instance starts later, an unrecycled `mcp-for-blender` child attempts to write to the stale socket and fails the tool call. By tracking `boot_id` in `state.json`, `mcp_wrapper.py` detects any instance change, restarts the `mcp-for-blender` subprocess, replays the cached `initialize` + `notifications/initialized` handshake, and executes the request seamlessly.

## Why Scene-Signature Dirty Tracking Is Essential

When `execute_blender_code` modifies the Blender scene via `bpy.ops` or `bpy.data` inside an addon timer without pushing an undo step, Blender often leaves `bpy.data.is_dirty == False`. `AddonClient.inspect_internal_state()` computes a deterministic SHA-256 `scene_signature` (objects, transforms, mesh vertex/polygon counts, materials) alongside `file_mtime`, allowing `LifecycleManager` to detect programmatic unsaved edits reliably.
