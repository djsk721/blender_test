# Migration Guide

## Migrating from Legacy `mcp/blender/` to `.opencode/mcp/blender/`

If your project previously used an implementation under `mcp/blender/`:

1. **Stop any running managed Blender instance**:
   ```bash
   ./mcp/blender/stop.sh --force --discard-session || true
   ```
2. **Preserve custom settings** (optional): Note any custom values in `mcp/blender/config.json` (e.g., `idle_timeout_sec`, `gpu.*`).
3. **Retire old directory** (after backing up if needed):
   Remove `mcp/blender/` so no duplicate active implementation remains alongside `.opencode/mcp/blender/`.
4. **Install from `opencode-blender-mcp-lifecycle`**:
   ```bash
   ./scripts/install.sh --force /path/to/your/project
   ```
   (Use `--force` if `opencode.json` still holds the old `mcp/blender/mcp_wrapper.py` command; a backup of the old `opencode.json` is automatically saved under `.opencode/mcp/.backups/`.)
5. **Reapply any custom `config.json` overrides** into `.opencode/mcp/blender/config.json` and update `runtime_dir` to `".opencode/mcp/blender/runtime"`.
6. **Reload and verify OpenCode**:
   ```bash
   cd /path/to/your/project
   opencode reload
   opencode mcp list
   ```

## Migrating from Global `~/.config/opencode/opencode.json` Eager Launch

If you previously ran Blender globally via `~/.config/opencode/opencode.json` (`xvfb-run -a blender & sleep 5; exec uvx mcp-for-blender`):

1. **Leave `~/.config/opencode/opencode.json` untouched** if other projects depend on it.
2. Run `./scripts/install.sh /path/to/target/project` to add a project-local `opencode.json` override.
3. OpenCode v2.2.7 automatically gives project-local `opencode.json` precedence over global `opencode.json`.
4. Verify with `opencode debug config` and `opencode mcp list` inside the project directory.

## Python Import Path Changes

Because `.opencode` begins with a dot, Python code should add `<project_root>/.opencode/mcp` to `sys.path` / `PYTHONPATH` and import from package `blender`:

```bash
PYTHONPATH=.opencode/mcp python3 -m blender.lifecycle_manager status
```
