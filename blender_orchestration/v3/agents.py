"""Isolated, bounded, single-flight agent invocation.

Every call is a fresh OpenCode session (no context carried between tasks), with no MCP servers
and all built-in tools denied, so the model can only answer. Two resource fixes over the legacy
invocation:

* The global config routes the ``title`` and ``compaction`` agents to the local Qwen model,
  which Strata serves with a single slot. The isolated config reroutes both to the session's own
  model and disables auto-compaction, so a reviewer call can never queue a hidden Qwen request.
* Calls are serialized with a file lock so the harness never issues concurrent model requests.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from ..multimodal_qa import parse_opencode_json_events
from ..rubric_evaluator import ISOLATED_NO_MCP_OPENCODE_CONFIG, build_isolated_opencode_env, detect_quota_error

LOCK_PATH = Path(tempfile.gettempdir()) / "blender_harness_v3_model.lock"


def response_failure(raw: dict[str, Any], model_id: str) -> str | None:
    """Fail closed even if OpenCode returns exit zero with a provider error event."""
    if raw.get('model_id') != model_id:
        return 'missing or mismatched model identity'
    failure = raw.get('errors') or raw.get('quota_blocked') or raw.get('stderr')
    if (raw.get('returncode') != 0 or not raw.get('session_id') or raw.get('errors')
            or raw.get('quota_blocked') or raw.get('timed_out')):
        return f"rc={raw.get('returncode')}: {str(failure or 'missing session')[:1500]}"
    return None


def isolated_config_overrides(model_id: str) -> dict[str, Any]:
    return {
        "agents": {"title": {"model": model_id}, "compaction": {"model": model_id}},
        "compaction": {"auto": False},
        "mcp": {"servers": {}},
    }


def strata_health(base_url: str = "http://127.0.0.1:8080", timeout: float = 5.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{base_url}/v1/models", timeout=timeout) as resp:
            data = json.load(resp)
        model = (data.get("data") or [{}])[0]
        return {"ok": (model.get("status") or {}).get("value") == "loaded", "model": model.get("id")}
    except Exception as exc:  # network errors are reported, not raised
        return {"ok": False, "error": str(exc)[:200]}


def invoke_isolated(model_id: str, prompt: str, *, files: list[Path] | None = None, timeout_sec: int = 600,
                    title: str = "Harness V3", lock_path: Path = LOCK_PATH) -> dict[str, Any]:
    cmd = ["opencode", "run", "--standalone", "--archived", "--format", "json", "-m", model_id, "--title", title]
    attached: list[str] = []
    for fp in files or []:
        rp = Path(fp).resolve()
        if not rp.is_file():
            raise ValueError(f"Missing model attachment: {rp}")
        cmd.extend(["--file", str(rp)])
        attached.append(str(rp))
    cmd.append(prompt)
    t0 = time.monotonic()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lock:
        deadline = t0 + timeout_sec
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Timed out waiting for the isolated model request lock')
                time.sleep(0.15)
        waited = round(time.monotonic() - t0, 3)
        try:
            with tempfile.TemporaryDirectory(prefix="oc_v3_") as tmpdir:
                env = build_isolated_opencode_env(tmpdir)
                cfg_path = Path(env["XDG_CONFIG_HOME"]) / "opencode" / "opencode.json"
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                cfg.update(isolated_config_overrides(model_id))
                cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
                (Path(tmpdir) / "opencode.json").write_text(json.dumps(ISOLATED_NO_MCP_OPENCODE_CONFIG), encoding="utf-8")
                res = subprocess.run(cmd, cwd=tmpdir, env=env, capture_output=True, text=True,
                                     timeout=timeout_sec, check=False)
            stdout, rc, timed_out, stderr = res.stdout, res.returncode, False, res.stderr or ""
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            rc, timed_out, stderr = 124, True, ""
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    parsed = parse_opencode_json_events(stdout)
    errors = list(parsed["errors"]) + ([f"Timed out after {timeout_sec}s"] if timed_out else [])
    return {
        "returncode": rc, "duration_sec": round(time.monotonic() - t0, 3), "lock_wait_sec": waited,
        "session_id": parsed["session_id"] or "", "text": parsed["text"], "tokens": parsed["tokens"],
        "compactions": parsed["compactions"], "errors": errors, "quota_blocked": detect_quota_error(errors),
        "stderr": stderr[-400:], "timed_out": timed_out, "attached_files": attached, "model_id": model_id,
    }
