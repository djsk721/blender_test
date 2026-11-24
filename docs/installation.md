# Installation Guide

## Prerequisites

- Linux host with `python3` (>= 3.10), `uv`/`uvx`, `blender`, and `Xvfb`
- `MCP for Blender` addon installed in Blender
- Optional: `nvidia-smi` for GPU health checks and VRAM monitoring

## Fresh Installation

```bash
git clone <REPOSITORY_URL> opencode-blender-mcp-lifecycle
cd opencode-blender-mcp-lifecycle
./scripts/install.sh /path/to/target/project
```

### Installer Flags

- `--dry-run`: Print planned changes without writing files
- `--force`: Overwrite an existing unmanaged `.opencode/mcp/blender/` directory or conflicting `mcp.servers.blender` registration (after creating a backup)
- `--reset-config`: Replace existing `.opencode/mcp/blender/config.json` with repository defaults
- `--no-validate`: Skip post-install syntax and config validation

## Installing into Nightfall Survivors (Future Example)

To install into `/home/jhkim/tools/survivor-game-3d` later:

```bash
cd /path/to/opencode-blender-mcp-lifecycle
./scripts/install.sh /home/jhkim/tools/survivor-game-3d
cd /home/jhkim/tools/survivor-game-3d
opencode reload
opencode mcp list
```

## Updating an Existing Installation

```bash
cd /path/to/opencode-blender-mcp-lifecycle
git pull
./scripts/update.sh /path/to/target/project
```

- Preserves custom settings in `<target>/.opencode/mcp/blender/config.json` while deep-merging any newly added default keys (use `--reset-config` to reset).
- Creates a rollback backup under `<target>/.opencode/mcp/.backups/blender-update-<UTC>/`.
- Does not terminate active Blender processes unnecessarily.

## Uninstalling

```bash
cd /path/to/opencode-blender-mcp-lifecycle

# Preview what will be removed
./scripts/uninstall.sh --dry-run /path/to/target/project

# Remove managed files and the managed 'blender' server entry
./scripts/uninstall.sh --yes /path/to/target/project
```

Add `--purge-runtime` if you also want to remove `.opencode/mcp/blender/runtime/` logs and backups. Unrelated `opencode.json` settings, other MCP servers, `.opencode/skills/`, `.opencode/agents/`, user files, and `.blend`/`.fbx`/`.glb` assets are always preserved.
