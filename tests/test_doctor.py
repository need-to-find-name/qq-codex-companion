from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from qq_codex_companion.doctor import (
    _is_loopback,
    _proxy_check,
    codex_checks,
    configuration_checks,
    runtime_checks,
)


def _settings(root: Path, **overrides: object) -> SimpleNamespace:
    workspace = root / "workspace"
    skill = root / "skills" / "qq-companion" / "SKILL.md"
    workspace.mkdir(parents=True)
    skill.parent.mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("policy", encoding="utf-8")
    skill.write_text("skill", encoding="utf-8")
    values = {
        "root": root,
        "onebot": SimpleNamespace(
            host="127.0.0.1",
            port=8643,
            access_token="a" * 32,
            bot_qq=10001,
            allowed_user_ids=frozenset({10002}),
        ),
        "codex": SimpleNamespace(
            executable="codex",
            proxy_url="",
            workspace=workspace,
            skill_path=skill,
            sandbox="read-only",
            approval_policy="never",
        ),
        "memory": SimpleNamespace(enabled=False),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DoctorTests(unittest.TestCase):
    def test_loopback_detection_rejects_public_bind(self) -> None:
        self.assertTrue(_is_loopback("127.0.0.1"))
        self.assertTrue(_is_loopback("::1"))
        self.assertTrue(_is_loopback("localhost"))
        self.assertFalse(_is_loopback("0.0.0.0"))

    def test_safe_configuration_passes_without_warnings(self) -> None:
        with TemporaryDirectory() as directory:
            results = configuration_checks(_settings(Path(directory)))
        self.assertFalse([result for result in results if result.level != "PASS"])

    def test_unsafe_configuration_is_explained(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            onebot = SimpleNamespace(
                host="0.0.0.0",
                port=8643,
                access_token="short",
                bot_qq=10001,
                allowed_user_ids=frozenset({10001}),
            )
            results = configuration_checks(_settings(root, onebot=onebot))
        names = {(result.level, result.name) for result in results}
        self.assertIn(("FAIL", "OneBot bind address"), names)
        self.assertIn(("FAIL", "QQ allowlist"), names)
        self.assertIn(("WARN", "OneBot token"), names)

    @patch("qq_codex_companion.doctor.socket.create_connection")
    def test_proxy_check_accepts_reachable_http_proxy(self, connection) -> None:
        connection.return_value.__enter__.return_value = None
        result = _proxy_check("Codex proxy", "http://127.0.0.1:7890")
        self.assertEqual(result.level, "PASS")
        connection.assert_called_once_with(("127.0.0.1", 7890), timeout=1.5)

    @patch("qq_codex_companion.doctor._run_command")
    @patch("qq_codex_companion.doctor.resolve_codex_executable", return_value="codex.exe")
    def test_codex_check_reports_version_and_login(self, _resolve, run_command) -> None:
        run_command.side_effect = [
            SimpleNamespace(returncode=0, stdout="codex-cli 1.2.3\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="Logged in using ChatGPT\n", stderr=""),
        ]
        with TemporaryDirectory() as directory:
            results = codex_checks(_settings(Path(directory)))
        self.assertEqual([result.level for result in results], ["PASS", "PASS"])

    def test_runtime_check_validates_autostart_files_and_port(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            napcat = root / "NapCat"
            qq = root / "QQ.exe"
            scripts = root / "scripts"
            napcat.mkdir()
            scripts.mkdir()
            (napcat / "NapCatWinBootMain.exe").touch()
            (napcat / "NapCatWinBootHook.dll").touch()
            qq.touch()
            (scripts / "runtime.local.psd1").write_text(
                "@{\n"
                f"    NapCatRoot = '{napcat}'\n"
                f"    QQPath = '{qq}'\n"
                "    OneBotWsPort = 8643\n"
                "}\n",
                encoding="utf-8",
            )
            results = runtime_checks(_settings(root))
        self.assertEqual([result.level for result in results], ["PASS", "PASS"])


if __name__ == "__main__":
    unittest.main()
