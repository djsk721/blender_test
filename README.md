# opencode-blender-mcp-lifecycle

Project-local lazy loading, process lifecycle management, unsaved-work protection, and conservative NVIDIA GPU safety for Blender MCP inside OpenCode projects.

## 1. What This Repository Does

By default, running `xvfb-run -a blender &` directly inside an OpenCode MCP server command launches Blender unconditionally on every OpenCode startup and `opencode reload`, keeping Blender and VRAM occupied even when Blender is not being used.

`opencode-blender-mcp-lifecycle` wraps `mcp-for-blender` behind a stdio proxy and detached lifecycle manager installed inside a target project's `.opencode/mcp/blender/` directory:

- **Lazy Loading**: OpenCode startup, `opencode reload`, MCP `initialize`, `tools/list`, and general coding sessions never start Blender. Blender starts on demand only when a Blender-backed `tools/call` or `blender://` resource request arrives.
- **Lifecycle Management**: Tracks 7 explicit states (`STOPPED`, `STARTING`, `READY`, `BUSY`, `IDLE`, `STOPPING`, `ERROR`), verifies readiness via `ping` + `get_addon_info`, prevents duplicate instances with `flock`, and recycles `mcp-for-blender` transparently whenever `boot_id` changes.
- **Idle Timeout & Unsaved Work Protection**: Automatically shuts down idle Blender + `Xvfb` after 600 seconds (configurable) to release GPU VRAM, while refusing to stop during active MCP jobs, Blender renders/bakes/playback, or when internal state is unverifiable. Dirty scenes are backed up with `copy=True`, SHA-256 verified, and restored/verified on relaunch without touching original `.blend` files.
- **Conservative GPU Safety**: Validates NVIDIA driver/NVML health, detects missing/lost GPUs and thermal bounds, falls back safely to CPU + software rendering when GPU health is uncertain, and never terminates or resets external LLM services (`strata`, `ollama`).
- **Project-Local Isolation**: Installs cleanly into any OpenCode project via `scripts/install.sh` without modifying `~/.config/opencode/opencode.json`.

## 2. Supported Environments

- **OS**: Linux (tested on Ubuntu/Linux x86_64 with `/proc` and advisory `flock`)
- **OpenCode**: `v2.2.7` (project-local `opencode.json` overriding global `mcp.servers.blender`)
- **Blender**: `5.3.0` with `MCP for Blender` addon (`v2.0`, protocol `16`)
- **MCP Server**: `mcp-for-blender` (`v2.3.1` via `uvx`)
- **Python**: Python `3.10+` standard library only (zero third-party runtime dependencies for the wrapper/manager)
- **Display**: `Xvfb` with `-displayfd` support

## 3. Dependencies

- `python3` (`3.10` or newer)
- `uv` / `uvx` (providing `mcp-for-blender`)
- `blender` (with the `blender_mcp` addon installed and auto-start enabled)
- `Xvfb` (X Virtual Framebuffer for headless GUI rendering)
- `nvidia-smi` (optional; used for GPU health and VRAM monitoring; degrades safely to CPU fallback if unavailable)
- `ss` (`iproute2`, optional; used to detect PIDs of externally launched Blender instances)

## 4. Installation Using `git clone`

```bash
git clone <REPOSITORY_URL> opencode-blender-mcp-lifecycle
cd opencode-blender-mcp-lifecycle
./scripts/install.sh /path/to/target/project
```

The installer:
1. Validates source repository files and target directory permissions.
2. Detects unmanaged `.opencode/mcp/blender/` directories or conflicting `mcp.servers.blender` entries (refusing without `--force`).
3. Creates backups under `<target>/.opencode/mcp/.backups/` before modifying existing files.
4. Copies managed files into `<target>/.opencode/mcp/blender/` and writes `.managed-manifest.json`.
5. Merges `mcp.servers.blender` into `<target>/opencode.json` while preserving all unrelated JSON keys, MCP servers, `.opencode/skills/`, and `.opencode/agents/`.
6. Validates Python syntax, executable bits, and dynamic `PROJECT_ROOT` resolution, rolling back automatically on error.

### Practical Deployment Example: Nightfall Survivors (`/home/jhkim/tools/survivor-game-3d`)

To deploy into an existing OpenCode project such as `/home/jhkim/tools/survivor-game-3d` without touching global `~/.config/opencode/opencode.json`:

```bash
git clone <REPOSITORY_URL> opencode-blender-mcp-lifecycle
cd opencode-blender-mcp-lifecycle

# Preview changes first (optional)
./scripts/install.sh --dry-run /home/jhkim/tools/survivor-game-3d

# Install into the target project
./scripts/install.sh /home/jhkim/tools/survivor-game-3d

# Reload OpenCode in the target project
cd /home/jhkim/tools/survivor-game-3d
opencode reload
opencode mcp list
```

See [docs/installation.md](docs/installation.md) for conflict handling and verification steps.

## 5. Enabling Blender MCP in an Existing OpenCode Project

OpenCode loads `~/.config/opencode/opencode.json` first and overrides it with the project-root `opencode.json`. Installing into a project writes only:

```json
{
  "mcp": {
    "servers": {
      "blender": {
        "type": "local",
        "command": [
          "python3",
          "-u",
          ".opencode/mcp/blender/mcp_wrapper.py"
        ],
        "cwd": ".",
        "environment": {
          "BLENDER_HOST": "127.0.0.1",
          "BLENDER_PORT": "9876",
          "BLENDER_MCP_CONFIG": ".opencode/mcp/blender/config.json"
        },
        "disabled": false,
        "timeout": {
          "startup": 60000,
          "catalog": 120000,
          "execution": 300000
        }
      }
    }
  }
}
```

Unrelated MCP servers (such as `photocraft`, `effectcraft`, `vectorcraft`, `soundcraft`, `aseprite`) and custom `agents`/`permissions` remain untouched.

## 6. Configuration Options

Default settings live in `.opencode/mcp/blender/config.json` and can be overridden via environment variables (`BLENDER_MCP_IDLE_TIMEOUT`, `BLENDER_MCP_STARTUP_TIMEOUT`, `BLENDER_RENDER_DEVICE`, `BLENDER_GPU_UNHEALTHY_POLICY`, `BLENDER_VRAM_LOW_POLICY`, etc.).

See [docs/configuration.md](docs/configuration.md) for the complete reference table.

## 7. Updating and Uninstalling

### Update

```bash
cd opencode-blender-mcp-lifecycle
git pull
./scripts/update.sh /path/to/target/project
```

`update.sh` preserves project-specific `config.json` overrides (merging any newly added defaults), creates a rollback backup, leaves running Blender instances undisturbed, and reports `previous_version -> new_version`.

### Uninstall

```bash
# Preview removal
./scripts/uninstall.sh --dry-run /path/to/target/project

# Remove only managed files and our mcp.servers.blender entry
./scripts/uninstall.sh --yes /path/to/target/project
```

## 8. GPU Safety Considerations

Before launching Blender on GPU, `gpu_manager.py` inspects `nvidia-smi` and `/proc/driver/nvidia/gpus` for driver/NVML failures (`NV_ERR_GPU_IS_LOST`, timeouts, missing devices, unreadable VRAM, thermal limits). Uncertain or degraded GPUs trigger safe CPU + software rendering fallback (or block launch when `unhealthy_gpu_policy="fail"`). Existing LLM services (`strata`, `ollama`) are never modified or terminated.

See [docs/gpu-safety.md](docs/gpu-safety.md) for isolation boundaries across CUDA, OpenGL, Vulkan, and Cycles.

## 9. QA Verification

```bash
# Unit, GPU safety, and installer tests
python3 -m unittest -v tests/blender_mcp/test_unit_lifecycle.py tests/blender_mcp/test_installer.py

# Real-environment integration QA (B01-B22)
python3 -m unittest -v tests/blender_mcp/test_integration_qa.py
```

Latest verified measurements and exit codes are recorded in [docs/qa-report.md](docs/qa-report.md).

Additional orchestration & policy documentation:
- [docs/orchestration-architecture.md](docs/orchestration-architecture.md) — Blender Director routing, Skill Execution Contracts, Blender-Only policy (`TEXT_ONLY` / `REFERENCE_GUIDED`), MCP tool adaptation, and workflow examples
- [docs/upstream-skills-audit.md](docs/upstream-skills-audit.md) — Audit comparing upstream `arjun988/blender-skills` against the local `.opencode/skills/` pack and live `mcp-for-blender` schemas
- [docs/benchmark-policy.md](docs/benchmark-policy.md) — Benchmark reference policy resolution and preflight CLI usage

## 10. Troubleshooting

- **Check lifecycle state without launching Blender**:
  `PYTHONPATH=.opencode/mcp python3 -m blender.lifecycle_manager status`
- **Check GPU health and launch decision**:
  `PYTHONPATH=.opencode/mcp python3 -m blender.lifecycle_manager gpu`
- **Inspect logs**: Check `.opencode/mcp/blender/runtime/logs/{wrapper,lifecycle,blender,xvfb,upstream_mcp}.log`.
- **Force stop a stuck managed Blender**: `./.opencode/mcp/blender/stop.sh --force --discard-session`.
- **Global config side effect**: If another directory without a local `opencode.json` is active in `opencode serve --service`, `opencode reload` may still invoke the global `~/.config/opencode/opencode.json` command for that other directory.
