#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: update.sh [--dry-run] [--reset-config] [--no-validate] <target_project_path>

Update an existing opencode-blender-mcp-lifecycle installation in
<target_project_path>/.opencode/mcp/blender/ from the current repository checkout,
preserving project-specific config.json unless --reset-config is passed.
EOF
}

DRY_RUN=0
RESET_CONFIG=0
VALIDATE=1
TARGET_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
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

if [[ ! -d "$TARGET_DIR" ]]; then
  echo "Error: Target project directory does not exist: $TARGET_DIR" >&2
  exit 1
fi

python3 - "$REPO_ROOT" "$TARGET_DIR" "$DRY_RUN" "$RESET_CONFIG" "$VALIDATE" <<'PYEOF'
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
dry_run = sys.argv[3] == "1"
reset_config = sys.argv[4] == "1"
validate = sys.argv[5] == "1"

src_dir = repo_root / ".opencode" / "mcp" / "blender"
dst_dir = target_root / ".opencode" / "mcp" / "blender"
manifest_path = dst_dir / ".managed-manifest.json"

if not manifest_path.is_file():
    raise SystemExit(
        f"Error: No managed installation found at {dst_dir} (.managed-manifest.json missing). "
        "Run scripts/install.sh first."
    )

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
prev_version = str(manifest.get("version") or (dst_dir / "VERSION").read_text(encoding="utf-8").strip() if (dst_dir / "VERSION").is_file() else "unknown")
new_version = (src_dir / "VERSION").read_text(encoding="utf-8").strip()

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
ORCHESTRATION_REL_FILES = [
    "blender_orchestration/__init__.py",
    "blender_orchestration/skill_registry.py",
    "blender_orchestration/contracts.py",
    "blender_orchestration/mcp_adapter.py",
    "blender_orchestration/scene_ops.py",
    "blender_orchestration/pipeline.py",
    "blender_orchestration/qa_profiles.py",
    "blender_orchestration/visual_qa.py",
    "blender_orchestration/production_pipeline.py",
    "blender_orchestration/regression_fixtures.py",
    "blender_orchestration/character_modeling.py",
    "blender_orchestration/repair_strategies.py",
    "blender_orchestration/multimodal_qa.py",
    "blender_orchestration/opencode_runner.py",
    "blender_orchestration/sequential_benchmark.py",
    "scripts/benchmark_preflight.py",
]


def deep_merge_defaults(existing: dict, defaults: dict) -> dict:
    out = dict(existing)
    for k, v in defaults.items():
        if k not in out:
            out[k] = v
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge_defaults(out[k], v)
    return out


def atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def copy_atomic(src: Path, dst: Path, executable: bool = False) -> None:
    tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
    shutil.copy2(src, tmp)
    if executable:
        tmp.chmod(tmp.stat().st_mode | 0o755)
    os.replace(tmp, dst)


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


if dry_run:
    print("DRY-RUN update plan:")
    print(f"  Target Project   : {target_root}")
    print(f"  Previous Version : {prev_version}")
    print(f"  New Version      : {new_version}")
    print(f"  Config Handling  : {'reset to defaults' if reset_config else 'preserve user values + merge new defaults'}")
    raise SystemExit(0)

ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_root = target_root / ".opencode" / "mcp" / ".backups" / f"blender-update-{ts}"
backup_root.mkdir(parents=True, exist_ok=True)
shutil.copytree(dst_dir, backup_root / "blender_dir", symlinks=True)


def rollback() -> None:
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
    shutil.copytree(backup_root / "blender_dir", dst_dir, symlinks=True)


try:
    for fname in MANAGED_FILES:
        if fname == "config.json" and not reset_config and (dst_dir / "config.json").is_file():
            existing_cfg = json.loads((dst_dir / "config.json").read_text(encoding="utf-8"))
            default_cfg = json.loads((src_dir / "config.json").read_text(encoding="utf-8"))
            merged_cfg = deep_merge_defaults(existing_cfg, default_cfg)
            atomic_write_json(dst_dir / "config.json", merged_cfg)
            continue
        copy_atomic(src_dir / fname, dst_dir / fname, executable=(fname in EXECUTABLE_FILES))

    if manifest.get("with_orchestration"):
        orch_dir = target_root / ".opencode" / "orchestration"
        for rel in ORCHESTRATION_REL_FILES:
            if (repo_root / rel).is_file():
                copy_atomic(repo_root / rel, orch_dir / rel, executable=rel.endswith("benchmark_preflight.py"))
                if validate:
                    py_compile.compile(str(orch_dir / rel), doraise=True)

    manifest["version"] = new_version
    manifest["previous_version"] = prev_version
    manifest["updated_at"] = ts
    manifest["source_commit"] = read_source_commit()
    manifest["managed_files"] = MANAGED_FILES
    atomic_write_json(manifest_path, manifest)

    if os.getenv("BLENDER_MCP_TEST_FAIL_STAGE") == "post_update":
        raise RuntimeError("Simulated failure at post_update stage")

    if validate:
        for fname in MANAGED_FILES:
            if fname.endswith(".py"):
                py_compile.compile(str(dst_dir / fname), doraise=True)
        for fname in EXECUTABLE_FILES:
            if not os.access(dst_dir / fname, os.X_OK):
                raise RuntimeError(f"Updated file is not executable: {dst_dir / fname}")
        check_env = os.environ.copy()
        check_env["PYTHONPATH"] = str(target_root / ".opencode" / "mcp")
        check_env["BLENDER_MCP_PROJECT_ROOT"] = str(target_root)
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "from blender.config_loader import load_config; cfg = load_config(); assert cfg.project_root.resolve() == __import__('pathlib').Path(r'''" + str(target_root) + "''').resolve()",
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
except Exception as exc:
    rollback()
    raise SystemExit(f"Update failed and was rolled back cleanly: {exc}")

print("Update Report:")
print(f"  Target Project   : {target_root}")
print(f"  Previous Version : {prev_version}")
print(f"  New Version      : {new_version}")
print(f"  Config Handling  : {'reset to defaults' if reset_config else 'preserved + merged defaults'}")
print(f"  Rollback Backup  : {backup_root}")
print(f"  Validation       : {'PASSED' if validate else 'SKIPPED'}")
PYEOF
