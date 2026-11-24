"""Tests for scripts/install.sh, update.sh, uninstall.sh, and fresh git clone deployment."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = PROJECT_ROOT / "scripts" / "install.sh"
UPDATE_SH = PROJECT_ROOT / "scripts" / "update.sh"
UNINSTALL_SH = PROJECT_ROOT / "scripts" / "uninstall.sh"


def _run(cmd: list[str], cwd: Path | None = None, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, timeout=60, check=False)


class TestInstallerScripts(unittest.TestCase):
    def test_01_clean_target_install_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "fresh_project"
            target.mkdir()
            res = _run([str(INSTALL_SH), str(target)])
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            self.assertIn("Validation      : PASSED", res.stdout)
            self.assertTrue((target / ".opencode" / "mcp" / "blender" / "mcp_wrapper.py").is_file())
            self.assertTrue((target / ".opencode" / "mcp" / "blender" / ".managed-manifest.json").is_file())
            oc = json.loads((target / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(
                oc["mcp"]["servers"]["blender"]["command"],
                ["python3", "-u", ".opencode/mcp/blender/mcp_wrapper.py"],
            )

    def test_02_existing_opencode_and_dot_opencode_and_unrelated_mcps_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "existing_project"
            (target / ".opencode" / "skills" / "custom-skill").mkdir(parents=True)
            (target / ".opencode" / "agents").mkdir(parents=True)
            (target / ".opencode" / "skills" / "custom-skill" / "SKILL.md").write_text("# Keep me\n", encoding="utf-8")
            (target / ".opencode" / "agents" / "custom.md").write_text("# Agent\n", encoding="utf-8")
            (target / ".opencode" / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")

            existing_oc = {
                "$schema": "https://opencode.ai/config.json",
                "default_agent": "build",
                "mcp": {
                    "timeout": {"startup": 60000},
                    "servers": {
                        "photocraft": {"type": "local", "command": ["photocraft-cli"], "disabled": False},
                        "aseprite": {"type": "local", "command": ["python3", "-m", "aseprite_mcp"], "disabled": False},
                    },
                },
            }
            (target / "opencode.json").write_text(json.dumps(existing_oc, indent=2), encoding="utf-8")

            res = _run([str(INSTALL_SH), str(target)])
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            updated_oc = json.loads((target / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(updated_oc["default_agent"], "build")
            self.assertEqual(updated_oc["mcp"]["timeout"], {"startup": 60000})
            self.assertIn("photocraft", updated_oc["mcp"]["servers"])
            self.assertIn("aseprite", updated_oc["mcp"]["servers"])
            self.assertIn("blender", updated_oc["mcp"]["servers"])
            self.assertEqual((target / ".opencode" / "skills" / "custom-skill" / "SKILL.md").read_text(encoding="utf-8"), "# Keep me\n")
            self.assertEqual((target / ".opencode" / "agents" / "custom.md").read_text(encoding="utf-8"), "# Agent\n")
            self.assertEqual((target / ".opencode" / "AGENTS.md").read_text(encoding="utf-8"), "# Rules\n")

    def test_03_conflicting_blender_registration_requires_force_and_backs_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "conflict_project"
            target.mkdir()
            old_oc = {
                "mcp": {
                    "servers": {
                        "blender": {
                            "type": "local",
                            "command": ["uvx", "mcp-for-blender"],
                            "disabled": False,
                        }
                    }
                }
            }
            (target / "opencode.json").write_text(json.dumps(old_oc, indent=2), encoding="utf-8")

            res_no_force = _run([str(INSTALL_SH), str(target)])
            self.assertNotEqual(res_no_force.returncode, 0)
            self.assertIn("Conflicting", res_no_force.stderr)
            self.assertEqual(json.loads((target / "opencode.json").read_text(encoding="utf-8")), old_oc)

            res_force = _run([str(INSTALL_SH), "--force", str(target)])
            self.assertEqual(res_force.returncode, 0, res_force.stderr + res_force.stdout)
            backups = list((target / ".opencode" / "mcp" / ".backups").glob("blender-install-*/opencode.json"))
            self.assertTrue(len(backups) == 1)
            self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), old_oc)

    def test_04_update_preserves_custom_config_and_reports_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "update_project"
            target.mkdir()
            self.assertEqual(_run([str(INSTALL_SH), str(target)]).returncode, 0)

            cfg_path = target / ".opencode" / "mcp" / "blender" / "config.json"
            custom = json.loads(cfg_path.read_text(encoding="utf-8"))
            custom["idle_timeout_sec"] = 900
            custom["gpu"]["vram_low_policy"] = "fail"
            del custom["gpu"]["max_safe_temp_c"]
            cfg_path.write_text(json.dumps(custom, indent=2), encoding="utf-8")

            res = _run([str(UPDATE_SH), str(target)])
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            self.assertIn("Previous Version : 0.1.0", res.stdout)
            self.assertIn("New Version      : 0.1.0", res.stdout)
            merged = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["idle_timeout_sec"], 900)
            self.assertEqual(merged["gpu"]["vram_low_policy"], "fail")
            self.assertEqual(merged["gpu"]["max_safe_temp_c"], 88)
            backups = list((target / ".opencode" / "mcp" / ".backups").glob("blender-update-*"))
            self.assertTrue(len(backups) == 1)

    def test_05_failed_installation_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rollback_project"
            target.mkdir()
            orig_oc = {"default_agent": "build", "mcp": {"servers": {"other": {"type": "local", "command": ["echo"]}}}}
            (target / "opencode.json").write_text(json.dumps(orig_oc, indent=2), encoding="utf-8")

            res = _run([str(INSTALL_SH), str(target)], env_extra={"BLENDER_MCP_TEST_FAIL_STAGE": "post_merge"})
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("rolled back cleanly", res.stderr)
            self.assertEqual(json.loads((target / "opencode.json").read_text(encoding="utf-8")), orig_oc)
            self.assertFalse((target / ".opencode" / "mcp" / "blender").exists())

    def test_06_uninstall_preserves_unrelated_files_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "uninstall_project"
            target.mkdir()
            (target / "opencode.json").write_text(
                json.dumps({"default_agent": "build", "mcp": {"servers": {"photocraft": {"type": "local", "command": ["p"]}}}}),
                encoding="utf-8",
            )
            (target / "hero.blend").write_bytes(b"BLENDER-user-asset")
            self.assertEqual(_run([str(INSTALL_SH), str(target)]).returncode, 0)
            user_note = target / ".opencode" / "mcp" / "blender" / "user_notes.txt"
            user_note.write_text("do not delete\n", encoding="utf-8")

            # Dry-run makes no changes
            dry = _run([str(UNINSTALL_SH), "--dry-run", str(target)])
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertTrue((target / ".opencode" / "mcp" / "blender" / "mcp_wrapper.py").is_file())

            # Non-interactive without --yes refuses
            no_yes = _run([str(UNINSTALL_SH), str(target)])
            self.assertNotEqual(no_yes.returncode, 0)

            # Uninstall with --yes removes only managed files & blender server entry
            res = _run([str(UNINSTALL_SH), "--yes", str(target)])
            self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
            self.assertFalse((target / ".opencode" / "mcp" / "blender" / "mcp_wrapper.py").exists())
            self.assertTrue(user_note.is_file())
            self.assertTrue((target / "hero.blend").is_file())
            oc = json.loads((target / "opencode.json").read_text(encoding="utf-8"))
            self.assertEqual(oc["default_agent"], "build")
            self.assertIn("photocraft", oc["mcp"]["servers"])
            self.assertNotIn("blender", oc["mcp"]["servers"])

    def test_07_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Target Project (With Spaces)"
            target.mkdir()
            res_i = _run([str(INSTALL_SH), str(target)])
            self.assertEqual(res_i.returncode, 0, res_i.stderr + res_i.stdout)
            res_u = _run([str(UPDATE_SH), str(target)])
            self.assertEqual(res_u.returncode, 0, res_u.stderr + res_u.stdout)
            res_x = _run([str(UNINSTALL_SH), "--yes", str(target)])
            self.assertEqual(res_x.returncode, 0, res_x.stderr + res_x.stdout)

    def test_08_fresh_git_clone_deploy_smoke_update_uninstall(self) -> None:
        if not (PROJECT_ROOT / ".git").is_dir():
            self.skipTest("Git repository not yet initialized in source directory")

        with tempfile.TemporaryDirectory() as tmp:
            clone_dir = Path(tmp) / "opencode-blender-mcp-lifecycle"
            target = Path(tmp) / "Cloned Target With Spaces"
            target.mkdir()

            cl = _run(["git", "clone", str(PROJECT_ROOT), str(clone_dir)])
            self.assertEqual(cl.returncode, 0, cl.stderr + cl.stdout)

            inst = _run([str(clone_dir / "scripts" / "install.sh"), str(target)])
            self.assertEqual(inst.returncode, 0, inst.stderr + inst.stdout)

            # Verify installed MCP wrapper responds to initialize + tools/list without starting Blender
            wrapper_path = target / ".opencode" / "mcp" / "blender" / "mcp_wrapper.py"
            env = os.environ.copy()
            env["BLENDER_MCP_CONFIG"] = ".opencode/mcp/blender/config.json"
            proc = subprocess.Popen(
                [sys.executable, "-u", str(wrapper_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(target),
                env=env,
                text=True,
            )
            try:
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {},
                                "clientInfo": {"name": "clone-test", "version": "1.0"},
                            },
                        }
                    )
                    + "\n"
                )
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
                proc.stdin.flush()
                init_resp = json.loads(proc.stdout.readline())
                list_resp = json.loads(proc.stdout.readline())
                self.assertEqual(init_resp["result"]["serverInfo"]["name"], "MCP for Blender")
                tools = {t["name"] for t in list_resp["result"]["tools"]}
                self.assertIn("get_scene_info", tools)
                self.assertIn("execute_blender_code", tools)
            finally:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=10)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

            upd = _run([str(clone_dir / "scripts" / "update.sh"), str(target)])
            self.assertEqual(upd.returncode, 0, upd.stderr + upd.stdout)
            uni = _run([str(clone_dir / "scripts" / "uninstall.sh"), "--yes", str(target)])
            self.assertEqual(uni.returncode, 0, uni.stderr + uni.stdout)


if __name__ == "__main__":
    unittest.main()
