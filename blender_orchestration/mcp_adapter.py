"""MCP-first adapter with runtime schema discovery, Blender-only guardrails, and bounded retry."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WRAPPER = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "mcp_wrapper.py"
DEFAULT_MCP_CONFIG = PROJECT_ROOT / ".opencode" / "mcp" / "blender" / "config.json"

EXTERNAL_ASSET_TOOLS = {"search_assets", "import_asset"}
AI_GENERATOR_TOOLS = {"generate_3d", "generate_gaussian_world"}
DISALLOWED_GENERATOR_MARKERS = ("tripo", "hunyuan", "hyper3d", "meshy", "rodin", "image_to_3d", "image-to-3d")
EXTERNAL_AI_3D_GENERATORS_FORBIDDEN = True


def verify_approved_base_mesh_manifest(manifest_path: Path | str, *, require_archive: bool = False) -> dict[str, Any]:
    """Fail-closed provenance verification for approved generic CC0 anatomical Base Meshes."""
    import hashlib

    mpath = Path(manifest_path).resolve()
    if not mpath.is_file():
        raise PolicyViolationError(f"Base mesh provenance manifest missing: {mpath}")
    data = json.loads(mpath.read_text(encoding="utf-8"))
    if not data.get("generic_starting_mesh_confirmed") or not data.get("finished_character_prohibited"):
        raise PolicyViolationError("Base mesh manifest must confirm generic starting mesh and prohibit finished characters.")
    if str(data.get("license") or "").upper() not in ("CC0", "CC0-1.0"):
        raise PolicyViolationError(f"Unapproved base mesh license: {data.get('license')!r}")
    blend_meta = ((data.get("files") or {}).get("blend_library")) or {}
    rel_blend = str(blend_meta.get("rel_path") or "")
    expected_sha = str(blend_meta.get("sha256") or "").lower()
    blend_path = (mpath.parent / rel_blend).resolve()
    if not blend_path.is_file() or not expected_sha:
        raise PolicyViolationError(f"Base mesh blend file or expected SHA-256 missing: {blend_path}")
    h = hashlib.sha256()
    with blend_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    actual_sha = h.hexdigest().lower()
    if actual_sha != expected_sha:
        raise PolicyViolationError(
            f"Base mesh SHA-256 mismatch for {blend_path}: expected {expected_sha}, got {actual_sha}"
        )
    archive = {}
    if require_archive:
        from urllib.parse import urlsplit
        from zipfile import BadZipFile, ZipFile

        source = urlsplit(data.get('source_url') or '')
        page = urlsplit(data.get('official_source_page') or '')
        if (source.scheme != 'https' or source.netloc != 'download.blender.org'
                or not source.path.startswith('/demo/bundles/') or not source.path.endswith('.zip')
                or page.scheme != 'https' or page.netloc != 'www.blender.org' or page.path.rstrip('/') != '/download/demo-files'
                or data.get('publisher') != 'Blender Foundation / Blender Studio' or not data.get('bundle_id')):
            raise PolicyViolationError('Unapproved base mesh distribution provenance')
        meta = (data.get('files') or {}).get('archive_zip') or {}
        archive_path = (mpath.parent / (meta.get('rel_path') or '')).resolve()
        if not archive_path.is_file() or not meta.get('sha256'):
            raise PolicyViolationError(f'Base mesh archive file or expected SHA-256 missing: {archive_path}')

        def digest(stream):
            h = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk)
            return h.hexdigest()

        with archive_path.open('rb') as stream:
            archive_sha = digest(stream)
        if archive_sha != meta['sha256'].lower():
            raise PolicyViolationError(f"Base mesh archive SHA-256 mismatch for {archive_path}: expected {meta['sha256']}, got {archive_sha}")
        try:
            with ZipFile(archive_path) as bundle, bundle.open(rel_blend) as member:
                if digest(member) != actual_sha:
                    raise PolicyViolationError(f'Base mesh library does not match archive member {rel_blend!r}')
        except (BadZipFile, KeyError) as exc:
            raise PolicyViolationError(f'Cannot verify archive member {rel_blend!r} in {archive_path}: {exc}') from exc
        archive = {'archive_path': str(archive_path), 'archive_sha256': archive_sha,
                   'source_url': data['source_url'], 'official_source_page': data['official_source_page'],
                   'manifest_sha256': hashlib.sha256(mpath.read_bytes()).hexdigest(),
                   'library_bytes': blend_path.stat().st_size, 'recorded_library_bytes': blend_meta.get('bytes')}
    return {
        "verified": True,
        "manifest_path": str(mpath),
        "blend_path": str(blend_path),
        "sha256": actual_sha,
        "bundle_id": data.get("bundle_id"),
        "license": data.get("license"),
        "publisher": data.get("publisher"),
        **archive,
    }


class MCPAdapterError(RuntimeError):
    """Base error for MCP adapter failures."""


class ToolNotSupportedError(MCPAdapterError):
    """Raised when a requested MCP tool or capability is unavailable."""


class PolicyViolationError(MCPAdapterError):
    """Raised when a tool call violates Blender-only, TEXT_ONLY, or isolation rules."""


class MCPTransport(Protocol):
    """Transport interface for JSON-RPC MCP servers."""

    def start(self) -> None: ...
    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None: ...
    def call_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def close(self) -> None: ...


class StdioMCPTransport:
    """Line-delimited JSON-RPC stdio transport targeting .opencode/mcp/blender/mcp_wrapper.py."""

    def __init__(
        self,
        *,
        wrapper_path: Path | None = None,
        cwd: Path | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> None:
        self.wrapper_path = Path(wrapper_path) if wrapper_path is not None else DEFAULT_WRAPPER
        self.cwd = Path(cwd) if cwd is not None else PROJECT_ROOT
        self.env_overrides = dict(env_overrides or {})
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1

    def start(self) -> None:
        if self._proc is not None:
            return
        env = os.environ.copy()
        env.setdefault("BLENDER_MCP_CONFIG", str(DEFAULT_MCP_CONFIG))
        env.update(self.env_overrides)
        self._proc = subprocess.Popen(
            [sys.executable, "-u", str(self.wrapper_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.cwd),
            env=env,
            text=True,
        )

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._proc is None:
            self.start()
        assert self._proc is not None and self._proc.stdin is not None
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def call_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._proc is None:
            self.start()
        assert self._proc is not None and self._proc.stdin is not None and self._proc.stdout is not None
        req_id = self._next_id
        self._next_id += 1
        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()

        while True:
            line = self._proc.stdout.readline()
            if not line:
                stderr_tail = ""
                if self._proc.stderr is not None:
                    stderr_tail = self._proc.stderr.read()
                raise MCPAdapterError(f"MCP server closed stdout unexpectedly during {method}: {stderr_tail}")
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("id") == req_id:
                if "error" in msg and msg["error"] is not None:
                    raise MCPAdapterError(f"RPC error for {method}: {msg['error']}")
                return msg

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=10)
        except Exception:
            self._proc.kill()
        finally:
            if self._proc.stdout:
                self._proc.stdout.close()
            if self._proc.stderr:
                self._proc.stderr.close()
            self._proc = None


class MockMCPTransport:
    """Deterministic in-memory MCP transport for unit tests."""

    def __init__(
        self,
        *,
        server_info: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.server_info = server_info or {"name": "MCP for Blender", "version": "1.30.0"}
        self.tools = tools or []
        self.handler = handler
        self.started = False
        self.notifications: list[tuple[str, dict[str, Any] | None]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def start(self) -> None:
        self.started = True

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.notifications.append((method, params))

    def call_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, params or {}))
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": self.server_info, "capabilities": {}}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": 2, "result": {"tools": self.tools}}
        if method == "tools/call":
            p = params or {}
            name = str(p.get("name") or "")
            args = dict(p.get("arguments") or {})
            if self.handler is not None:
                return {"jsonrpc": "2.0", "id": 3, "result": self.handler(name, args)}
            return {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "ok"}], "isError": False}}
        raise MCPAdapterError(f"Unsupported mock RPC method: {method}")

    def close(self) -> None:
        self.started = False


@dataclass
class MCPAdapter:
    """Schema-aware MCP adapter enforcing Blender-only production and bounded retries."""

    transport: MCPTransport
    reference_mode: str = "TEXT_ONLY"
    allow_external_assets: bool = False
    server_info: dict[str, Any] = field(default_factory=dict)
    tool_schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_calls_by_operation: dict[str, int] = field(default_factory=dict)
    python_exec_count: int = 0
    blender_errors: list[dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    ai_generator_calls_blocked: int = 0
    external_asset_calls_blocked: int = 0
    call_trace: list[dict[str, Any]] = field(default_factory=list)

    def discover_capabilities(self) -> dict[str, Any]:
        """Run initialize + tools/list without spawning Blender (under Lazy Loading)."""
        self.transport.start()
        init_res = self.transport.call_rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "blender-orchestration", "version": "0.2.0"},
            },
        )
        self.transport.send_notification("notifications/initialized")
        list_res = self.transport.call_rpc("tools/list", {})
        self.server_info = dict((init_res.get("result") or {}).get("serverInfo") or {})
        tools = (list_res.get("result") or {}).get("tools") or []
        self.tool_schemas = {t["name"]: t for t in tools if isinstance(t, dict) and "name" in t}
        return {
            "server_info": self.server_info,
            "tools": sorted(self.tool_schemas.keys()),
            "tool_schemas": self.tool_schemas,
        }

    def resolve_tool(self, capability: str) -> dict[str, Any]:
        """Map a logical Blender capability onto the live discovered MCP tool schema."""
        if not self.tool_schemas:
            self.discover_capabilities()

        if capability == "visual_capture":
            if "look" in self.tool_schemas:
                return {"capability": capability, "tool_name": "look", "fallback_used": False, "schema": self.tool_schemas["look"]}
            if "get_viewport_screenshot" in self.tool_schemas:
                return {
                    "capability": capability,
                    "tool_name": "get_viewport_screenshot",
                    "fallback_used": False,
                    "schema": self.tool_schemas["get_viewport_screenshot"],
                }
            if "execute_blender_code" in self.tool_schemas:
                return {
                    "capability": capability,
                    "tool_name": "execute_blender_code",
                    "fallback_used": True,
                    "schema": self.tool_schemas["execute_blender_code"],
                }
            raise ToolNotSupportedError("No visual capture or code execution tool exposed by MCP server.")

        if capability == "scene_info":
            if "get_scene_info" in self.tool_schemas:
                return {"capability": capability, "tool_name": "get_scene_info", "fallback_used": False, "schema": self.tool_schemas["get_scene_info"]}
            if "execute_blender_code" in self.tool_schemas:
                return {
                    "capability": capability,
                    "tool_name": "execute_blender_code",
                    "fallback_used": True,
                    "schema": self.tool_schemas["execute_blender_code"],
                }
            raise ToolNotSupportedError("No scene inspection tool exposed by MCP server.")

        if capability == "addon_status":
            if "get_addon_status" in self.tool_schemas:
                return {"capability": capability, "tool_name": "get_addon_status", "fallback_used": False, "schema": self.tool_schemas["get_addon_status"]}
            raise ToolNotSupportedError("get_addon_status is not exposed by this MCP server.")

        if capability in ("execute_code", "modeling", "uvs", "materials", "rigging", "animation", "export"):
            if "execute_blender_code" in self.tool_schemas:
                return {
                    "capability": capability,
                    "tool_name": "execute_blender_code",
                    "fallback_used": (capability != "execute_code"),
                    "schema": self.tool_schemas["execute_blender_code"],
                }
            raise ToolNotSupportedError(f"Capability {capability!r} requires a dedicated tool or execute_blender_code.")

        if capability in self.tool_schemas:
            return {"capability": capability, "tool_name": capability, "fallback_used": False, "schema": self.tool_schemas[capability]}

        raise ToolNotSupportedError(f"Unsupported MCP capability or tool: {capability!r}")

    def _check_blender_only_policy(self, tool_name: str, args: dict[str, Any]) -> None:
        name_l = tool_name.lower()
        provider_l = str(args.get("provider") or "").lower()
        combined = f"{name_l} {provider_l}"
        if EXTERNAL_AI_3D_GENERATORS_FORBIDDEN and (
            tool_name in AI_GENERATOR_TOOLS or any(m in combined for m in DISALLOWED_GENERATOR_MARKERS)
        ):
            self.ai_generator_calls_blocked += 1
            raise PolicyViolationError(
                f"Blender-Only Policy violation: external AI 3D generator call {tool_name!r} is unconditionally forbidden."
            )
        if tool_name in EXTERNAL_ASSET_TOOLS and not self.allow_external_assets:
            self.external_asset_calls_blocked += 1
            raise PolicyViolationError(
                f"Premade/downloaded asset tool {tool_name!r} is disabled (allow_external_assets=False)."
            )

    def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None, *, stage: str = "general") -> dict[str, Any]:
        """Validate policy & schema, invoke MCP tool, and record structured telemetry."""
        args = dict(arguments or {})
        self._check_blender_only_policy(tool_name, args)

        if not self.tool_schemas:
            self.discover_capabilities()

        if tool_name not in self.tool_schemas:
            raise ToolNotSupportedError(
                f"Tool {tool_name!r} is not in discovered MCP catalog ({sorted(self.tool_schemas.keys())})."
            )

        schema = self.tool_schemas[tool_name].get("inputSchema") or {}
        required_params = schema.get("required") or []
        missing_params = [p for p in required_params if p not in args]
        if missing_params:
            raise MCPAdapterError(f"Tool {tool_name!r} missing required arguments: {missing_params}")

        t0 = time.monotonic()
        rpc_resp = self.transport.call_rpc("tools/call", {"name": tool_name, "arguments": args})
        duration = round(time.monotonic() - t0, 4)

        self.mcp_calls_by_operation[tool_name] = self.mcp_calls_by_operation.get(tool_name, 0) + 1
        if tool_name == "execute_blender_code":
            self.python_exec_count += 1

        result = rpc_resp.get("result") or {}
        is_error = bool(result.get("isError"))
        text_parts = [
            item.get("text", "")
            for item in (result.get("content") or [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        image_parts = [
            item
            for item in (result.get("content") or [])
            if isinstance(item, dict) and item.get("type") == "image"
        ]
        combined_text = "\n".join(text_parts)

        trace_entry = {
            "stage": stage,
            "tool": tool_name,
            "duration_sec": duration,
            "is_error": is_error,
            "image_count": len(image_parts),
            "text_preview": combined_text[:300],
        }
        self.call_trace.append(trace_entry)

        if is_error:
            err = {"stage": stage, "tool": tool_name, "error": combined_text}
            self.blender_errors.append(err)
            raise MCPAdapterError(f"MCP tool {tool_name} returned error: {combined_text}")

        return {
            "tool": tool_name,
            "duration_sec": duration,
            "text": combined_text,
            "image_count": len(image_parts),
            "image_mime_types": [img.get("mimeType") for img in image_parts],
            "raw_result": result,
        }

    def execute_blender_json(self, code: str, *, user_prompt: str, stage: str) -> dict[str, Any]:
        """Run execute_blender_code expecting a JSON marker line (`__ORCH_JSON__:{...}`)."""
        res = self.call_tool("execute_blender_code", {"code": code, "user_prompt": user_prompt}, stage=stage)
        text = res["text"]
        for line in reversed(text.splitlines()):
            if "__ORCH_JSON__:" in line:
                payload_str = line.split("__ORCH_JSON__:", 1)[1].strip()
                parsed = json.loads(payload_str)
                return {"parsed": parsed, "duration_sec": res["duration_sec"], "raw_text": text}
        raise MCPAdapterError(f"Expected __ORCH_JSON__ marker in stage {stage!r}, got: {text[:400]}")

    def execute_with_retry(
        self,
        stage: str,
        fn: Callable[[int, Exception | None], Any],
        *,
        max_attempts: int = 3,
        checkpoint_fn: Callable[[], Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a stage with bounded 3-attempt recovery and checkpoint preservation."""
        last_exc: Exception | None = None
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, max_attempts + 1):
            try:
                out = fn(attempt, last_exc)
                attempts.append({"attempt": attempt, "status": "ok"})
                return {"status": "APPLIED", "attempt_count": attempt, "attempts": attempts, "result": out}
            except Exception as exc:
                last_exc = exc
                if attempt > 1:
                    self.retry_count += 1
                attempts.append({"attempt": attempt, "status": "error", "error": str(exc)})

        checkpoint_saved = None
        if checkpoint_fn is not None:
            try:
                checkpoint_saved = checkpoint_fn()
            except Exception as cp_exc:
                checkpoint_saved = {"error": str(cp_exc)}

        return {
            "status": "FAILED",
            "stage": stage,
            "attempt_count": max_attempts,
            "attempts": attempts,
            "unresolved_defect": str(last_exc) if last_exc else "Unknown failure",
            "checkpoint": checkpoint_saved,
        }

    def close(self) -> None:
        self.transport.close()
