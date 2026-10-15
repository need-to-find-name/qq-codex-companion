import unittest

from qq_codex_companion.weather import detect_weather_location, weather_code_description


class WeatherLocationTests(unittest.TestCase):
    aliases = (("家", "北京市海淀区"), ("学校", "北京市朝阳区"))

    def test_detects_current_home_location(self) -> None:
        self.assertEqual(
            detect_weather_location("现在在家啦", self.aliases),
            "北京市海淀区",
        )

    def test_detects_current_campus_location(self) -> None:
        self.assertEqual(
            detect_weather_location("我回学校了", self.aliases),
            "北京市朝阳区",
        )

    def test_does_not_apply_future_or_uncertain_location(self) -> None:
        self.assertIsNone(detect_weather_location("我明天回家", self.aliases))
        self.assertIsNone(detect_weather_location("过几天可能回家", self.aliases))

    def test_weather_codes_have_human_description(self) -> None:
        self.assertEqual(weather_code_description(0), "晴")
        self.assertEqual(weather_code_description(63), "有雨")
        self.assertEqual(weather_code_description(95), "有雷雨")


if __name__ == "__main__":
    unittest.main()
