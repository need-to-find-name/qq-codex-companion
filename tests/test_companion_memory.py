import tempfile
import unittest
from pathlib import Path

from qq_codex_companion.config import MemorySettings
from qq_codex_companion.memory import CompanionMemoryStore, MemoryCandidate


class CompanionMemoryTests(unittest.TestCase):
    def _store(self, root: Path) -> CompanionMemoryStore:
        return CompanionMemoryStore(
            MemorySettings(
                enabled=True,
                store_file=root / "memory.json",
                max_entries=50,
                max_retrieved=4,
                max_prompt_chars=800,
            )
        )

    def test_add_retrieve_and_persist_relevant_memory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = self._store(root)
            changed, removed = store.apply(
                [
                    MemoryCandidate(
                        "用户平时会打羽毛球",
                        "preference",
                        3,
                        0,
                        ("羽毛球", "运动"),
                    )
                ],
                [],
                now=1000,
            )
            self.assertEqual((changed, removed), (1, 0))
            restored = self._store(root)
            self.assertEqual(restored.count(now=1001), 1)
            self.assertIn("羽毛球", "\n".join(restored.retrieve("想聊聊羽毛球", now=1001)))
            self.assertEqual(restored.retrieve("今天早餐吃什么", now=1001), ())

    def test_similar_update_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.apply(
                [MemoryCandidate("用户喜欢打羽毛球", "preference", 3, 0, ("羽毛球",))],
                [],
                now=1000,
            )
            store.apply(
                [MemoryCandidate("用户很喜欢打羽毛球", "preference", 4, 0, ("运动",))],
                [],
                now=1100,
            )
            self.assertEqual(store.count(now=1101), 1)

    def test_temporary_memory_expires(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.apply(
                [MemoryCandidate("用户这周末准备回家", "plan", 2, 1, ("回家",))],
                [],
                now=1000,
            )
            self.assertEqual(store.count(now=1000 + 86399), 1)
            self.assertEqual(store.count(now=1000 + 86401), 0)

    def test_explicit_forget_removes_matching_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.apply(
                [MemoryCandidate("用户喜欢打羽毛球", "preference", 3, 0, ("羽毛球",))],
                [],
                now=1000,
            )
            changed, removed = store.apply([], ["羽毛球"], now=1100)
            self.assertEqual(changed, 0)
            self.assertEqual(removed, 1)
            self.assertEqual(store.count(now=1101), 0)

    def test_credentials_are_rejected_even_if_model_proposes_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            changed, removed = store.apply(
                [MemoryCandidate("OneBot access token 是 abc123", "project", 5, 0, ())],
                [],
                now=1000,
            )
            self.assertEqual((changed, removed), (0, 0))
            self.assertEqual(store.count(now=1001), 0)


if __name__ == "__main__":
    unittest.main()
