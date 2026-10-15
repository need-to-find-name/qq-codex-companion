import unittest
from datetime import datetime
from unittest.mock import patch

from qq_codex_companion.main import (
    _choose_proactive_interaction,
    _is_active_timestamp,
    _next_active_timestamp,
)


class ProactiveTimingTests(unittest.TestCase):
    def test_active_window_excludes_night(self) -> None:
        daytime = datetime.now().astimezone().replace(hour=14, minute=0, second=0, microsecond=0)
        night = daytime.replace(hour=2)
        self.assertTrue(_is_active_timestamp(daytime.timestamp(), 9 * 60 + 30, 23 * 60))
        self.assertFalse(_is_active_timestamp(night.timestamp(), 9 * 60 + 30, 23 * 60))

    def test_next_active_time_moves_to_morning(self) -> None:
        night = datetime.now().astimezone().replace(hour=23, minute=30, second=0, microsecond=0)
        result = datetime.fromtimestamp(
            _next_active_timestamp(night.timestamp(), 9 * 60 + 30, 30 * 60)
        ).astimezone()
        self.assertEqual((result.hour, result.minute), (10, 0))
        self.assertGreater(result.date(), night.date())

    def test_proactive_image_respects_daily_limit(self) -> None:
        with patch("qq_codex_companion.main.random.random", return_value=0.0):
            interaction = _choose_proactive_interaction(
                image_probability=1.0,
                selfie_probability=1.0,
                image_count_today=1,
                image_daily_limit=1,
            )
        self.assertEqual(interaction, "proactive")

    def test_proactive_image_can_choose_selfie_or_life_snapshot(self) -> None:
        with patch(
            "qq_codex_companion.main.random.random",
            side_effect=(0.1, 0.2),
        ):
            selfie = _choose_proactive_interaction(
                image_probability=0.5,
                selfie_probability=0.5,
                image_count_today=0,
                image_daily_limit=1,
            )
        with patch(
            "qq_codex_companion.main.random.random",
            side_effect=(0.1, 0.8),
        ):
            snapshot = _choose_proactive_interaction(
                image_probability=0.5,
                selfie_probability=0.5,
                image_count_today=0,
                image_daily_limit=1,
            )
        self.assertEqual(selfie, "proactive_selfie")
        self.assertEqual(snapshot, "proactive_image")


if __name__ == "__main__":
    unittest.main()
