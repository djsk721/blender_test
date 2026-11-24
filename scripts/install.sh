#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install.sh [--force] [--dry-run] [--reset-config] [--no-validate] <target_project_path>

Install opencode-blender-mcp-lifecycle into a target OpenCode project under
<target_project_path>/.opencode/mcp/blender/ and register the server in
<target_project_path>/opencode.json without modifying ~/.config/opencode/opencode.json.
EOF
}

FORCE=0
DRY_RUN=0
RESET_CONFIG=0
VALIDATE=1
TARGET_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --reset-config) RESET_CONFIG=1; shift ;;
    --no-validate) VALIDATE=0; shift ;;
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_BLENDER_DIR="$REPO_ROOT/.opencode/mcp/blender"

if [[ ! -d "$SRC_BLENDER_DIR" ]]; then
  echo "Error: Source directory not found: $SRC_BLENDER_DIR" >&2
  exit 1
fi

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: Target project directory does not exist: $TARGET_DIR" >&2
  exit 1
fi

python3 - "$REPO_ROOT" "$TARGET_DIR" "$FORCE" "$DRY_RUN" "$RESET_CONFIG" "$VALIDATE" <<'PYEOF'
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys

repo_root = Path(sys.argv[1]).resolve()
target_root = Path(sys.argv[2]).resolve()
force = sys.argv[3] == "1"
dry_run = sys.argv[4] == "1"
reset_config = sys.argv[5] == "1"
validate = sys.argv[6] == "1"

src_dir = repo_root / ".opencode" / "mcp" / "blender"
dst_dir = target_root / ".opencode" / "mcp" / "blender"
manifest_path = dst_dir / ".managed-manifest.json"
opencode_json_path = target_root / "opencode.json"

MANAGED_FILES = [
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

EXECUTABLE_FILES = {"mcp_wrapper.py", "start.sh", "stop.sh"}

MANAGED_SERVER_ENTRY = {
    "type": "local",
    "command": [
        "python3",
        "-u",
        ".opencode/mcp/blender/mcp_wrapper.py",
    ],
    "cwd": ".",
    "environment": {
        "BLENDER_HOST": "127.0.0.1",
        "BLENDER_PORT": "9876",
        "BLENDER_MCP_CONFIG": ".opencode/mcp/blender/config.json",
    },
    "disabled": False,
    "timeout": {
        "startup": 60000,
        "catalog": 120000,
        "execution": 300000,
    },
}


def read_source_commit() -> str | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except OSError:
        pass
    return None


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    content = json.dumps(data, indent=2) + "\n"
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def copy_atomic(src: Path, dst: Path, executable: bool = False) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    shutil.copy2(src, tmp)
    if executable:
        mode = tmp.stat().st_mode | 0o755
        tmp.chmod(mode)
    os.replace(tmp, dst)


# 1. Validate source repository contents
for fname in MANAGED_FILES:
    if not (src_dir / fname).is_file():
        raise SystemExit(f"Error: Missing required source file: {src_dir / fname}")

version = (src_dir / "VERSION").read_text(encoding="utf-8").strip()

# 2. Validate target writability & detect conflicts
if not os.access(target_root, os.W_OK):
    raise SystemExit(f"Error: Target project directory is not writable: {target_root}")

has_manifest = manifest_path.is_file()
existing_dir_nonempty = dst_dir.is_dir() and any(dst_dir.iterdir())
if existing_dir_nonempty and not has_manifest and not force:
    raise SystemExit(
        f"Error: Unmanaged directory already exists at {dst_dir}. "
        "Refusing to overwrite without --force."
    )

existing_opencode: dict = {}
had_opencode_json = opencode_json_path.is_file()
if had_opencode_json:
    try:
        parsed = json.loads(opencode_json_path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("Top-level opencode.json must be a JSON object")
        existing_opencode = parsed
    except Exception as exc:
        raise SystemExit(f"Error: Invalid JSON in {opencode_json_path}: {exc}")

existing_blender_cfg = (
    existing_opencode.get("mcp", {})
    .get("servers", {})
    .get("blender")
    if isinstance(existing_opencode.get("mcp"), dict)
    and isinstance(existing_opencode.get("mcp", {}).get("servers"), dict)
    else None
)

if existing_blender_cfg is not None:
    same_managed = (
        isinstance(existing_blender_cfg, dict)
        and existing_blender_cfg.get("command") == MANAGED_SERVER_ENTRY["command"]
    )
    if not same_managed and not has_manifest and not force:
        raise SystemExit(
            f"Error: Conflicting 'mcp.servers.blender' entry already exists in {opencode_json_path}. "
            "Refusing to overwrite without --force."
        )

if dry_run:
    print("DRY-RUN install plan:")
    print(f"  Source repository : {repo_root} (version {version})")
    print(f"  Target project    : {target_root}")
    print(f"  Install directory : {dst_dir}")
    print(f"  OpenCode config   : {opencode_json_path} ({'merge' if had_opencode_json else 'create'})")
    print(f"  Managed files     : {len(MANAGED_FILES)}")
    raise SystemExit(0)

# 3. Create backups before modification
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_root = target_root / ".opencode" / "mcp" / ".backups" / f"blender-install-{ts}"
had_dst_dir = dst_dir.exists()
backup_created = False

if had_dst_dir or had_opencode_json:
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_created = True
    if had_dst_dir:
        shutil.copytree(dst_dir, backup_root / "blender_dir", symlinks=True)
    if had_opencode_json:
        shutil.copy2(opencode_json_path, backup_root / "opencode.json")


def rollback() -> None:
    if had_dst_dir and (backup_root / "blender_dir").exists():
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(backup_root / "blender_dir", dst_dir, symlinks=True)
    elif not had_dst_dir and dst_dir.exists():
        shutil.rmtree(dst_dir)

    if had_opencode_json and (backup_root / "opencode.json").is_file():
        shutil.copy2(backup_root / "opencode.json", opencode_json_path)
    elif not had_opencode_json and opencode_json_path.exists():
        opencode_json_path.unlink()


try:
    dst_dir.mkdir(parents=True, exist_ok=True)
    preserved_config = (dst_dir / "config.json").is_file() and not reset_config

    for fname in MANAGED_FILES:
        if fname == "config.json" and preserved_config:
            continue
        copy_atomic(src_dir / fname, dst_dir / fname, executable=(fname in EXECUTABLE_FILES))

    # Merge opencode.json preserving all unrelated settings
    merged = json.loads(json.dumps(existing_opencode)) if existing_opencode else {}
    if "$schema" not in merged:
        merged["$schema"] = "https://opencode.ai/config.json"
    mcp_block = merged.get("mcp")
    if not isinstance(mcp_block, dict):
        mcp_block = {}
    servers_block = mcp_block.get("servers")
    if not isinstance(servers_block, dict):
        servers_block = {}
    servers_block["blender"] = MANAGED_SERVER_ENTRY
    mcp_block["servers"] = servers_block
    merged["mcp"] = mcp_block
    atomic_write_json(opencode_json_path, merged)

    manifest = {
        "package": "opencode-blender-mcp-lifecycle",
        "version": version,
        "installed_at": ts,
        "source_commit": read_source_commit(),
        "managed_files": MANAGED_FILES,
        "opencode_server_key": "blender",
        "opencode_command": MANAGED_SERVER_ENTRY["command"],
    }
    atomic_write_json(manifest_path, manifest)

    # Optional deterministic failure hook for rollback verification tests
    if os.getenv("BLENDER_MCP_TEST_FAIL_STAGE") == "post_merge":
        raise RuntimeError("Simulated failure at post_merge stage")

    # 4. Post-install validation
    if validate:
        for fname in MANAGED_FILES:
            if fname.endswith(".py"):
                py_compile.compile(str(dst_dir / fname), doraise=True)
        for fname in EXECUTABLE_FILES:
            if not os.access(dst_dir / fname, os.X_OK):
                raise RuntimeError(f"Installed file is not executable: {dst_dir / fname}")
        check_env = os.environ.copy()
        check_env["PYTHONPATH"] = str(target_root / ".opencode" / "mcp")
        check_env["BLENDER_MCP_PROJECT_ROOT"] = str(target_root)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json; "
                    "from blender.config_loader import load_config; "
                    "cfg = load_config(); "
                    "print(json.dumps({'project_root': str(cfg.project_root), 'runtime_dir': str(cfg.runtime_dir)}))"
                ),
            ],
            cwd=str(target_root),
            env=check_env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if probe.returncode != 0:
            raise RuntimeError(f"Config validation failed: {probe.stderr or probe.stdout}")
        info = json.loads(probe.stdout.strip().splitlines()[-1])
        if Path(info["project_root"]).resolve() != target_root:
            raise RuntimeError(
                f"Resolved project_root {info['project_root']} != {target_root}"
            )
        reloaded_oc = json.loads(opencode_json_path.read_text(encoding="utf-8"))
        cmd_rel = reloaded_oc["mcp"]["servers"]["blender"]["command"][-1]
        if not (target_root / cmd_rel).is_file():
            raise RuntimeError(f"MCP entrypoint missing: {target_root / cmd_rel}")
except Exception as exc:
    rollback()
    raise SystemExit(f"Installation failed and was rolled back cleanly: {exc}")

print("Installation Report:")
print(f"  Package         : opencode-blender-mcp-lifecycle v{version}")
print(f"  Target Project  : {target_root}")
print(f"  Installed Path  : {dst_dir}")
print(f"  OpenCode Config : {opencode_json_path} ({'updated' if had_opencode_json else 'created'})")
print(f"  Config Policy   : {'preserved existing config.json' if preserved_config else 'wrote default config.json'}")
print(f"  Backup Path     : {backup_root if backup_created else 'none (fresh install)'}")
print(f"  Validation      : {'PASSED' if validate else 'SKIPPED'}")
PYEOF
