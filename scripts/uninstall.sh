#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: uninstall.sh [--yes] [--dry-run] [--purge-runtime] <target_project_path>

Remove only files owned by opencode-blender-mcp-lifecycle and its managed
'blender' MCP server registration from <target_project_path>, preserving all
unrelated OpenCode settings, skills, agents, user files, and generated assets.
EOF
}

YES=0
DRY_RUN=0
PURGE_RUNTIME=0
TARGET_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --purge-runtime) PURGE_RUNTIME=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*)
      echo "Error: Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$TARGET_DIR" ]]; then
        echo "Error: Unexpected extra positional argument: $1" >&2
        usage >&2
        exit 2
      fi
      TARGET_DIR="$1"
      shift
      ;;
  esac
done

if [[ -z "$TARGET_DIR" ]]; then
  echo "Error: <target_project_path> is required." >&2
  usage >&2
  exit 2
fi

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: Target project directory does not exist: $TARGET_DIR" >&2
  exit 1
fi

python3 - "$TARGET_DIR" "$YES" "$DRY_RUN" "$PURGE_RUNTIME" <<'PYEOF'
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import shutil
import sys

target_root = Path(sys.argv[1]).resolve()
confirmed = sys.argv[2] == "1"
dry_run = sys.argv[3] == "1"
purge_runtime = sys.argv[4] == "1"

dst_dir = target_root / ".opencode" / "mcp" / "blender"
manifest_path = dst_dir / ".managed-manifest.json"
opencode_json_path = target_root / "opencode.json"

DEFAULT_MANAGED_FILES = [
    "VERSION",
    "__init__.py",
    "config.json",
    "config_loader.py",
    "gpu_manager.py",
    "process_manager.py",
    "lifecycle_manager.py",
    "mcp_wrapper.py",
    "start.sh",
    "stop.sh",
]

MANAGED_WRAPPER_CMD = [
    "python3",
    "-u",
    ".opencode/mcp/blender/mcp_wrapper.py",
]

manifest = None
if manifest_path.is_file():
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    managed_files = list(manifest.get("managed_files") or DEFAULT_MANAGED_FILES)
    managed_cmd = manifest.get("opencode_command") or MANAGED_WRAPPER_CMD
    server_key = manifest.get("opencode_server_key") or "blender"
else:
    if not dst_dir.exists():
        raise SystemExit(f"Nothing to uninstall: {dst_dir} does not exist.")
    raise SystemExit(
        f"Error: {manifest_path} not found. Refusing to remove unmanaged files in {dst_dir}."
    )

files_to_remove = [dst_dir / fname for fname in managed_files if (dst_dir / fname).is_file()]
for opt_rel in (manifest.get("optional_managed_files") or []):
    opt_path = target_root / opt_rel
    if opt_path.is_file():
        files_to_remove.append(opt_path)
files_to_remove.append(manifest_path)

remove_blender_entry = False
if opencode_json_path.is_file():
    oc_data = json.loads(opencode_json_path.read_text(encoding="utf-8"))
    srv = oc_data.get("mcp", {}).get("servers", {}).get(server_key) if isinstance(oc_data.get("mcp"), dict) else None
    if isinstance(srv, dict) and srv.get("command") == managed_cmd:
        remove_blender_entry = True

print("Uninstall Preview:")
print(f"  Target Project       : {target_root}")
print(f"  Managed Files        : {len(files_to_remove)} file(s)")
for p in files_to_remove:
    print(f"    - remove {p}")
if (dst_dir / "__pycache__").is_dir():
    print(f"    - remove {dst_dir / '__pycache__'}")
if purge_runtime and (dst_dir / "runtime").exists():
    print(f"    - purge runtime dir {dst_dir / 'runtime'}")
print(f"  OpenCode Config      : {'remove mcp.servers.' + server_key + ' from ' + str(opencode_json_path) if remove_blender_entry else 'no managed registration to remove'}")
print("  Preserved            : unrelated opencode.json keys, unrelated MCP servers, skills, agents, user files, generated assets")

if dry_run:
    print("DRY-RUN complete: no files were modified.")
    raise SystemExit(0)

if not confirmed:
    if sys.stdin.isatty():
        answer = input("Proceed with uninstall? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Aborted by user.")
            raise SystemExit(1)
    else:
        raise SystemExit("Error: Non-interactive uninstall requires --yes (or use --dry-run to preview).")

ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
if opencode_json_path.is_file() and remove_blender_entry:
    backup_dir = target_root / ".opencode" / "mcp" / ".backups" / f"blender-uninstall-{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(opencode_json_path, backup_dir / "opencode.json")

for p in files_to_remove:
    if p.exists():
        p.unlink()

pycache_dir = dst_dir / "__pycache__"
if pycache_dir.is_dir():
    shutil.rmtree(pycache_dir)

if purge_runtime and (dst_dir / "runtime").exists():
    shutil.rmtree(dst_dir / "runtime")

if dst_dir.is_dir() and not any(dst_dir.iterdir()):
    dst_dir.rmdir()

if remove_blender_entry and opencode_json_path.is_file():
    oc_data = json.loads(opencode_json_path.read_text(encoding="utf-8"))
    mcp_block = oc_data.get("mcp")
    if isinstance(mcp_block, dict) and isinstance(mcp_block.get("servers"), dict):
        mcp_block["servers"].pop(server_key, None)
        if not mcp_block["servers"]:
            mcp_block.pop("servers", None)
        if not mcp_block:
            oc_data.pop("mcp", None)
    tmp = opencode_json_path.with_name(f".{opencode_json_path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(oc_data, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, opencode_json_path)

print("Uninstall complete.")
PYEOF
