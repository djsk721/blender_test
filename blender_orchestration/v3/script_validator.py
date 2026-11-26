"""Static validation of producer Blender Python before it reaches Blender.

Rejects code that cannot be valid (syntax), that would escape the production sandbox (render,
file reload/save, network, subprocess, external asset import or AI generators), or that uses
Blender APIs removed before Blender 4.x/5.x. Findings are returned as structured, short messages
that are fed back to the producer verbatim.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

_FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Exact call targets refused outright.
FORBIDDEN_CALLS: dict[str, str] = {
    "bpy.ops.render.render": "the harness renders; never render inside an edit script",
    "bpy.ops.render.opengl": "the harness renders; never render inside an edit script",
    "bpy.ops.wm.open_mainfile": "the harness owns scene loading and rollback",
    "bpy.ops.wm.save_as_mainfile": "the harness owns checkpoints",
    "bpy.ops.wm.save_mainfile": "the harness owns checkpoints",
    "bpy.ops.wm.read_factory_settings": "resetting Blender destroys the verified scene",
    "bpy.ops.wm.read_homefile": "resetting Blender destroys the verified scene",
    "bpy.ops.wm.quit_blender": "quitting Blender is not allowed",
    "bpy.ops.wm.append": "premade asset import is forbidden",
    "bpy.ops.wm.link": "premade asset import is forbidden",
    "bpy.data.libraries.load": "library loading is restricted to the approved base-mesh path",
    "os.system": "shell access is not allowed",
    "exec": "nested exec is not allowed",
    "eval": "eval is not allowed",
    "__import__": "dynamic import is not allowed",
}
FORBIDDEN_CALL_PREFIXES: dict[str, str] = {
    "bpy.ops.import_scene.": "premade mesh import is forbidden; model geometry in Blender",
    "bpy.ops.import_mesh.": "premade mesh import is forbidden; model geometry in Blender",
    "bpy.ops.wm.obj_import": "premade mesh import is forbidden",
    "bpy.ops.wm.stl_import": "premade mesh import is forbidden",
    "bpy.ops.wm.ply_import": "premade mesh import is forbidden",
    "bpy.ops.export_scene.": "the harness performs export",
    "bpy.ops.scene.new": "work in the provided `scene`; never create scenes",
    "bpy.ops.object.camera_add": "the harness owns cameras",
    "bpy.ops.object.light_add": "the harness owns lighting",
}
FORBIDDEN_IMPORTS = {"subprocess", "socket", "urllib", "requests", "http", "shutil", "ctypes", "multiprocessing", "threading"}
FORBIDDEN_NAMES = {"generate_3d", "generate_gaussian_world", "import_asset", "search_assets"}

# Attribute names removed/renamed in modern Blender (4.0+), with the replacement to suggest.
REMOVED_ATTRIBUTES: dict[str, str] = {
    "use_auto_smooth": "removed in Blender 4.1; use a Smooth by Angle modifier or obj.data.shade_smooth()",
    "auto_smooth_angle": "removed in Blender 4.1",
    "to_decomposition": "use Matrix.decompose()",
    "layers": None,  # handled contextually below (bone.layers removed in 4.0)
    "show_x_ray": "renamed show_in_front",
    "draw_type": "renamed display_type",
    "cursor_location": "use scene.cursor.location",
    "object_bases": "removed in 2.8",
    "user_preferences": "renamed preferences",
}

MAX_SCRIPT_LINES_DEFAULT = 420


@dataclass
class ValidationResult:
    ok: bool
    code: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def feedback(self) -> str:
        return "; ".join(self.errors[:8])


def extract_python_block(text: str) -> str | None:
    """Return the longest fenced Python block in ``text`` (verbatim), or None."""
    blocks = [m.strip("\n") for m in _FENCE_RE.findall(text or "") if m.strip()]
    if not blocks:
        return None
    return max(blocks, key=len)


def _dotted(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - ast.unparse is total for parsed trees
        return ""


def validate_blender_script(code: str, *, max_lines: int = MAX_SCRIPT_LINES_DEFAULT) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not code or not code.strip():
        return ValidationResult(False, code or "", ["empty script"])
    n_lines = code.count("\n") + 1
    if n_lines > max_lines:
        errors.append(f"script has {n_lines} lines (limit {max_lines}); split work across phases instead of one giant script")
    try:
        tree = ast.parse(code)
        compile(tree, '<producer_script>', 'exec')
    except SyntaxError as exc:
        return ValidationResult(False, code, [f"SyntaxError line {exc.lineno}: {exc.msg}"])

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                if mod.split(".")[0] in FORBIDDEN_IMPORTS:
                    errors.append(f"import of {mod!r} is not allowed")
        elif isinstance(node, ast.Call):
            target = _dotted(node.func)
            if target in FORBIDDEN_CALLS:
                errors.append(f"line {node.lineno}: {target}() refused - {FORBIDDEN_CALLS[target]}")
            for prefix, why in FORBIDDEN_CALL_PREFIXES.items():
                if target.startswith(prefix):
                    errors.append(f"line {node.lineno}: {target}() refused - {why}")
            leaf = target.rsplit(".", 1)[-1]
            if leaf == 'verify_and_load_base_mesh':
                errors.append(f"line {node.lineno}: call the source-bound load_approved_body() in BASE_MESH instead")
            if leaf in FORBIDDEN_NAMES:
                errors.append(f"line {node.lineno}: {leaf}() refused - external AI/asset generation is forbidden")
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr == "layers":
                owner = _dotted(node.value).lower()
                if "bone" in owner:
                    errors.append(f"line {node.lineno}: bone.layers was removed in Blender 4.0; use bone collections")
            elif attr in REMOVED_ATTRIBUTES and REMOVED_ATTRIBUTES[attr]:
                errors.append(f"line {node.lineno}: .{attr} - {REMOVED_ATTRIBUTES[attr]}")
        elif isinstance(node, ast.While):
            if isinstance(node.test, ast.Constant) and node.test.value is True and not any(
                isinstance(n, ast.Break) for n in ast.walk(node)
            ):
                errors.append(f"line {node.lineno}: unbounded `while True` without break")
    if "bpy." not in code and "bt." not in code:
        warnings.append("script does not reference bpy or bt; it may not edit the scene")
    return ValidationResult(not errors, code, errors, warnings)
