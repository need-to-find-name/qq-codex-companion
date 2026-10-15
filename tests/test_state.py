import json
import tempfile
import unittest
from pathlib import Path

from qq_codex_companion.state import ThreadState


class ThreadStateTests(unittest.TestCase):
    def test_persists_thread_and_last_interaction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = ThreadState(path)
            state.set("private:1", "thread-1")
            state.set_last_interaction("private:1", 1234.5)
            state.set_next_proactive("private:1", 2345.5)
            state.record_proactive("private:1", 2000.0)
            state.record_proactive_image("private:1", 2100.0)
            state.set_last_weather_date("private:1", "2026-10-13")
            state.set_weather_location("private:1", "北京市朝阳区")

            restored = ThreadState(path)
            self.assertEqual(restored.get("private:1"), "thread-1")
            self.assertEqual(restored.get_last_interaction("private:1"), 1234.5)
            self.assertEqual(restored.get_next_proactive("private:1"), 2345.5)
            self.assertEqual(restored.get_proactive_history("private:1"), (2000.0,))
            self.assertEqual(
                restored.get_proactive_image_history("private:1"),
                (2100.0,),
            )
            self.assertEqual(restored.get_last_weather_date("private:1"), "2026-10-13")
            self.assertEqual(restored.get_weather_location("private:1"), "北京市朝阳区")

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("threads", payload)
            self.assertIn("last_interactions", payload)
            self.assertIn("next_proactive", payload)
            self.assertIn("proactive_history", payload)
            self.assertIn("proactive_image_history", payload)
            self.assertIn("last_weather_dates", payload)
            self.assertIn("weather_locations", payload)


if __name__ == "__main__":
    unittest.main()
