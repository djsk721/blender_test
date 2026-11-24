#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: install.sh [OPTIONS] <target_project_path>

Install opencode-blender-mcp-lifecycle into a target OpenCode project under
<target_project_path>/.opencode/mcp/blender/ and register the server in
<target_project_path>/opencode.json without modifying ~/.config/opencode/opencode.json.

Options:
  --force                 Overwrite unmanaged files or conflicting registrations (with backup)
  --dry-run               Preview planned changes without writing files
  --reset-config          Replace existing .opencode/mcp/blender/config.json with defaults
  --no-validate           Skip post-install validation
  --with-orchestration    Also install blender_orchestration/ and benchmark_preflight.py into .opencode/orchestration/
  --with-policy           Install .opencode/AGENTS.md if missing (writes AGENTS.blender-skills.md if already present)
  --merge-policy          Safely merge managed policy block into an existing .opencode/AGENTS.md (creates backup)
  --skills-source <dir>   Copy OpenCode skills + shared references/ into .opencode/skills/
EOF
}

FORCE=0
DRY_RUN=0
RESET_CONFIG=0
VALIDATE=1
WITH_ORCHESTRATION=0
WITH_POLICY=0
MERGE_POLICY=0
SKILLS_SOURCE=""
TARGET_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --reset-config) RESET_CONFIG=1; shift ;;
    --no-validate) VALIDATE=0; shift ;;
    --with-orchestration) WITH_ORCHESTRATION=1; shift ;;
    --with-policy) WITH_POLICY=1; shift ;;
    --merge-policy) WITH_POLICY=1; MERGE_POLICY=1; shift ;;
    --skills-source)
      if [[ $# -lt 2 ]]; then
        echo "Error: --skills-source requires a directory path." >&2
        exit 2
      fi
      SKILLS_SOURCE="$2"
      shift 2
      ;;
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

python3 - "$REPO_ROOT" "$TARGET_DIR" "$FORCE" "$DRY_RUN" "$RESET_CONFIG" "$VALIDATE" "$WITH_ORCHESTRATION" "$WITH_POLICY" "$MERGE_POLICY" "$SKILLS_SOURCE" <<'PYEOF'
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import py_compile
import re
import shutil
import subprocess
import sys

repo_root = Path(sys.argv[1]).resolve()
target_root = Path(sys.argv[2]).resolve()
force = sys.argv[3] == "1"
dry_run = sys.argv[4] == "1"
reset_config = sys.argv[5] == "1"
validate = sys.argv[6] == "1"
with_orchestration = sys.argv[7] == "1"
with_policy = sys.argv[8] == "1"
merge_policy = sys.argv[9] == "1"
skills_source_arg = sys.argv[10].strip()

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
    "scripts/benchmark_preflight.py",
]

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


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
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

if with_orchestration:
    for rel in ORCHESTRATION_REL_FILES:
        if not (repo_root / rel).is_file():
            raise SystemExit(f"Error: Missing required orchestration source file: {repo_root / rel}")

src_policy_path = repo_root / ".opencode" / "AGENTS.md"
if with_policy and not src_policy_path.is_file():
    raise SystemExit(f"Error: Source policy file not found: {src_policy_path}")

src_skills_dir: Path | None = None
if skills_source_arg:
    src_skills_dir = Path(skills_source_arg).resolve()
    if not src_skills_dir.is_dir():
        raise SystemExit(f"Error: Skills source directory not found: {src_skills_dir}")
    if not (src_skills_dir / "blender-director" / "SKILL.md").is_file():
        raise SystemExit(f"Error: Skills source missing blender-director/SKILL.md: {src_skills_dir}")
    if not (src_skills_dir / "references").is_dir():
        raise SystemExit(f"Error: Skills source missing shared references/ directory: {src_skills_dir}")

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

target_skills_dir = target_root / ".opencode" / "skills"
if src_skills_dir is not None and target_skills_dir.is_dir() and any(target_skills_dir.iterdir()) and not force:
    raise SystemExit(
        f"Error: Existing skills directory already populated at {target_skills_dir}. "
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
    print(f"  With orchestration: {with_orchestration}")
    print(f"  With policy       : {with_policy} (merge={merge_policy})")
    print(f"  Skills source     : {src_skills_dir if src_skills_dir else 'none'}")
    raise SystemExit(0)

# 3. Create backups before modification
ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_root = target_root / ".opencode" / "mcp" / ".backups" / f"blender-install-{ts}"
had_dst_dir = dst_dir.exists()
target_agents_md = target_root / ".opencode" / "AGENTS.md"
had_agents_md = target_agents_md.is_file()
orch_dir = target_root / ".opencode" / "orchestration"
had_orch_dir = orch_dir.exists()
backup_created = False

if had_dst_dir or had_opencode_json or (with_policy and had_agents_md) or (with_orchestration and had_orch_dir):
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_created = True
    if had_dst_dir:
        shutil.copytree(dst_dir, backup_root / "blender_dir", symlinks=True)
    if had_opencode_json:
        shutil.copy2(opencode_json_path, backup_root / "opencode.json")
    if with_policy and had_agents_md:
        shutil.copy2(target_agents_md, backup_root / "AGENTS.md")
    if with_orchestration and had_orch_dir:
        shutil.copytree(orch_dir, backup_root / "orchestration_dir", symlinks=True)


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

    if with_policy and had_agents_md and (backup_root / "AGENTS.md").is_file():
        shutil.copy2(backup_root / "AGENTS.md", target_agents_md)
    elif with_policy and not had_agents_md and target_agents_md.exists():
        target_agents_md.unlink()


optional_managed_files: list[str] = []
policy_action = "none"
skills_action = "unchanged"

try:
    dst_dir.mkdir(parents=True, exist_ok=True)
    preserved_config = (dst_dir / "config.json").is_file() and not reset_config

    for fname in MANAGED_FILES:
        if fname == "config.json" and preserved_config:
            continue
        copy_atomic(src_dir / fname, dst_dir / fname, executable=(fname in EXECUTABLE_FILES))

    # Optional: install orchestration + preflight package into .opencode/orchestration/
    if with_orchestration:
        for rel in ORCHESTRATION_REL_FILES:
            copy_atomic(repo_root / rel, orch_dir / rel, executable=rel.endswith("benchmark_preflight.py"))
            optional_managed_files.append(f".opencode/orchestration/{rel}")

    # Optional: safe AGENTS.md policy installation / opt-in merge
    if with_policy:
        src_policy_text = src_policy_path.read_text(encoding="utf-8")
        if not had_agents_md:
            atomic_write_text(target_agents_md, src_policy_text)
            optional_managed_files.append(".opencode/AGENTS.md")
            policy_action = f"installed {target_agents_md}"
        elif merge_policy:
            begin_marker = "<!-- BEGIN BLENDER-SKILLS MANAGED POLICY -->"
            end_marker = "<!-- END BLENDER-SKILLS MANAGED POLICY -->"
            existing_txt = target_agents_md.read_text(encoding="utf-8")
            block = f"{begin_marker}\n{src_policy_text.rstrip()}\n{end_marker}\n"
            if begin_marker in existing_txt and end_marker in existing_txt:
                merged_txt = re.sub(
                    rf"{re.escape(begin_marker)}.*?{re.escape(end_marker)}\n?",
                    block,
                    existing_txt,
                    flags=re.DOTALL,
                )
            else:
                sep = "\n\n" if existing_txt and not existing_txt.endswith("\n\n") else ""
                merged_txt = existing_txt + sep + block
            atomic_write_text(target_agents_md, merged_txt)
            policy_action = f"merged managed policy block into {target_agents_md} (backup in {backup_root / 'AGENTS.md'})"
        else:
            sidecar_policy = target_root / ".opencode" / "AGENTS.blender-skills.md"
            atomic_write_text(sidecar_policy, src_policy_text)
            optional_managed_files.append(".opencode/AGENTS.blender-skills.md")
            policy_action = f"preserved existing AGENTS.md; wrote sidecar {sidecar_policy}"

    # Optional: copy OpenCode skills tree including shared references/
    if src_skills_dir is not None:
        if target_skills_dir.exists() and force:
            shutil.rmtree(target_skills_dir)
        shutil.copytree(src_skills_dir, target_skills_dir, symlinks=False)
        skills_action = f"installed from {src_skills_dir} -> {target_skills_dir}"

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
        "optional_managed_files": optional_managed_files,
        "with_orchestration": with_orchestration,
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
        if with_orchestration:
            for rel in ORCHESTRATION_REL_FILES:
                py_compile.compile(str(orch_dir / rel), doraise=True)
except Exception as exc:
    rollback()
    raise SystemExit(f"Installation failed and was rolled back cleanly: {exc}")

skill_count = len(list(target_skills_dir.glob("*/SKILL.md"))) if target_skills_dir.is_dir() else 0
has_shared_refs = (target_skills_dir / "references").is_dir()

print("Installation Report:")
print(f"  Package         : opencode-blender-mcp-lifecycle v{version}")
print(f"  Target Project  : {target_root}")
print(f"  Installed Path  : {dst_dir}")
print(f"  OpenCode Config : {opencode_json_path} ({'updated' if had_opencode_json else 'created'})")
print(f"  Config Policy   : {'preserved existing config.json' if preserved_config else 'wrote default config.json'}")
print(f"  Orchestration   : {'installed under .opencode/orchestration' if with_orchestration else 'skipped (opt-in via --with-orchestration)'}")
print(f"  Policy AGENTS.md: {policy_action}")
print(f"  Skills Setup    : {skills_action} (discovered={skill_count}, shared_references={has_shared_refs})")
print(f"  Backup Path     : {backup_root if backup_created else 'none (fresh install)'}")
print(f"  Validation      : {'PASSED' if validate else 'SKIPPED'}")
PYEOF