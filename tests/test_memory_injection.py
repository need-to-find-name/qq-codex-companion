import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qq_codex_companion.codex_client import (
    CodexAppServer,
    codex_subprocess_environment,
    resolve_codex_executable,
)
from qq_codex_companion.config import CodexSettings, MemorySettings
from qq_codex_companion.memory import CompanionMemoryStore
from qq_codex_companion.state import ThreadState


class MemoryInjectionTests(unittest.TestCase):
    def test_resolves_codex_from_desktop_install_when_path_is_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            executable = local / "OpenAI" / "Codex" / "bin" / "version-1" / "codex.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test")
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local), "PATH": ""}):
                self.assertEqual(resolve_codex_executable("codex"), str(executable.resolve()))

    def test_injects_once_then_again_only_after_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "active.md"
            archive = root / "archive.md"
            skill = root / "skill.md"
            memory.write_text("第一版", encoding="utf-8")
            archive.write_text("完整档案", encoding="utf-8")
            skill.write_text("---\nname: test\n---\n", encoding="utf-8")
            settings = CodexSettings(
                executable="codex",
                workspace=root,
                proxy_url="",
                model="",
                effort="medium",
                approval_policy="never",
                sandbox="read-only",
                turn_timeout_seconds=10,
                skill_path=skill,
                memory_core_path=memory,
                memory_archive_path=archive,
            )
            memory_store = CompanionMemoryStore(
                MemorySettings(
                    enabled=True,
                    store_file=root / "memory.json",
                    max_entries=500,
                    max_retrieved=8,
                    max_prompt_chars=1800,
                )
            )
            client = CodexAppServer(
                settings,
                ThreadState(root / "state.json"),
                memory_store,
            )

            first = client._memory_context("thread-1")
            self.assertIsNotNone(first)
            assert first is not None
            client._memory_hash_by_thread["thread-1"] = first[0]
            self.assertIsNone(client._memory_context("thread-1"))

            memory.write_text("第二版", encoding="utf-8")
            self.assertIsNotNone(client._memory_context("thread-1"))

    def test_memory_context_is_skipped_when_memory_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skill.md"
            skill.write_text("---\nname: test\n---\n", encoding="utf-8")
            settings = CodexSettings(
                executable="codex",
                workspace=root,
                proxy_url="",
                model="",
                effort="medium",
                approval_policy="never",
                sandbox="read-only",
                turn_timeout_seconds=10,
                skill_path=skill,
                memory_core_path=root / "missing-active.md",
                memory_archive_path=root / "missing-archive.md",
            )
            client = CodexAppServer(settings, ThreadState(root / "state.json"))

            self.assertIsNone(client._memory_context("thread-1"))

    def test_collects_only_new_generated_images_for_the_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "active.md"
            archive = root / "archive.md"
            skill = root / "skill.md"
            memory.write_text("profile", encoding="utf-8")
            archive.write_text("archive", encoding="utf-8")
            skill.write_text("---\nname: test\n---\n", encoding="utf-8")
            settings = CodexSettings(
                executable="codex",
                workspace=root,
                proxy_url="",
                model="",
                effort="medium",
                approval_policy="never",
                sandbox="read-only",
                turn_timeout_seconds=10,
                skill_path=skill,
                memory_core_path=memory,
                memory_archive_path=archive,
            )
            client = CodexAppServer(settings, ThreadState(root / "state.json"))
            codex_home = root / "codex-home"
            generated = codex_home / "generated_images" / "thread-1"
            generated.mkdir(parents=True)
            old_image = generated / "old.png"
            old_image.write_bytes(b"old")

            with patch.dict(os.environ, {"CODEX_HOME": str(codex_home)}):
                before = client._generated_image_snapshot("thread-1")
                new_image = generated / "new.png"
                new_image.write_bytes(b"new")
                (generated / "notes.txt").write_text("ignore", encoding="utf-8")

                self.assertEqual(
                    client._new_generated_images("thread-1", before),
                    (new_image.resolve(),),
                )

    def test_builds_explicit_proxy_environment_for_app_server(self) -> None:
        with patch.dict(
            os.environ,
            {"NO_PROXY": "releases.astral.sh"},
            clear=True,
        ):
            environment = codex_subprocess_environment("http://127.0.0.1:7890")

        self.assertEqual(environment["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(environment["HTTPS_PROXY"], "http://127.0.0.1:7890")
        self.assertIn("localhost", environment["NO_PROXY"])
        self.assertIn("127.0.0.1", environment["NO_PROXY"])
        self.assertIn("::1", environment["NO_PROXY"])


if __name__ == "__main__":
    unittest.main()
