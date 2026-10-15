from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from .config import WeatherSettings


_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
@dataclass(frozen=True)
class WeatherBrief:
    location: str
    condition: str
    current_temperature_c: float
    apparent_temperature_c: float
    low_c: float
    high_c: float
    precipitation_probability_percent: int
    precipitation_mm: float
    max_wind_kmh: float

    def as_prompt_data(self) -> dict[str, Any]:
        return asdict(self)


def detect_weather_location(
    text: str,
    aliases: Iterable[tuple[str, str]],
) -> str | None:
    compact = re.sub(r"\s+", "", text)
    future_only = ("明天", "过几天", "可能", "打算", "准备", "想回", "要回")
    if any(marker in compact for marker in future_only):
        return None
    prefix = r"(?:我)?(?:已经|刚刚|刚|现在)?(?:回到|回|到了|到|在)"
    suffix = r"(?:了|啦|咯)?(?:[，。！？!?,].*)?$"
    for alias, location in sorted(aliases, key=lambda item: len(item[0]), reverse=True):
        normalized_alias = re.sub(r"\s+", "", alias)
        if normalized_alias and re.search(prefix + re.escape(normalized_alias) + suffix, compact):
            return location
    return None


def weather_code_description(code: int) -> str:
    if code == 0:
        return "晴"
    if code in {1, 2}:
        return "晴间多云"
    if code == 3:
        return "阴或多云"
    if code in {45, 48}:
        return "有雾"
    if code in {51, 53, 55, 56, 57}:
        return "有毛毛雨"
    if code in {61, 63, 65, 66, 67}:
        return "有雨"
    if code in {71, 73, 75, 77, 85, 86}:
        return "有雪"
    if code in {80, 81, 82}:
        return "有阵雨"
    if code in {95, 96, 99}:
        return "有雷雨"
    return "天气状况不明"


class OpenMeteoWeather:
    def __init__(self, settings: WeatherSettings) -> None:
        self.settings = settings
        self._coordinates: dict[str, tuple[float, float, str]] = {}

    def _opener(self) -> urllib.request.OpenerDirector:
        if not self.settings.proxy_url:
            return urllib.request.build_opener()
        return urllib.request.build_opener(
            urllib.request.ProxyHandler(
                {
                    "http": self.settings.proxy_url,
                    "https": self.settings.proxy_url,
                }
            )
        )

    def _get_json(self, base_url: str, params: dict[str, object]) -> dict[str, Any]:
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "qq-codex-companion/0.1"},
        )
        with self._opener().open(request, timeout=self.settings.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("error"):
            raise RuntimeError(f"weather service returned an error: {payload!r}")
        return payload

    def _resolve_location(self, location: str) -> tuple[float, float, str]:
        if location in self._coordinates:
            return self._coordinates[location]
        payload = self._get_json(
            _GEOCODING_URL,
            {
                "name": location,
                "count": 1,
                "language": "zh",
                "format": "json",
            },
        )
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            raise RuntimeError(f"weather location not found: {location}")
        match = results[0]
        latitude = float(match["latitude"])
        longitude = float(match["longitude"])
        name = str(match.get("name") or location)
        admin1 = str(match.get("admin1") or "")
        label = f"{admin1}{name}" if admin1 and admin1 != name else name
        self._coordinates[location] = (latitude, longitude, label)
        return self._coordinates[location]

    def _fetch_sync(self, location: str) -> WeatherBrief:
        latitude, longitude, label = self._resolve_location(location)
        payload = self._get_json(
            _FORECAST_URL,
            {
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,apparent_temperature,weather_code",
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,precipitation_sum,wind_speed_10m_max"
                ),
                "timezone": "auto",
                "forecast_days": 1,
            },
        )
        current = payload.get("current")
        daily = payload.get("daily")
        if not isinstance(current, dict) or not isinstance(daily, dict):
            raise RuntimeError("weather service returned incomplete forecast data")

        def first(name: str) -> Any:
            values = daily.get(name)
            if not isinstance(values, list) or not values:
                raise RuntimeError(f"weather forecast is missing {name}")
            return values[0]

        return WeatherBrief(
            location=label,
            condition=weather_code_description(int(first("weather_code"))),
            current_temperature_c=round(float(current["temperature_2m"]), 1),
            apparent_temperature_c=round(float(current["apparent_temperature"]), 1),
            low_c=round(float(first("temperature_2m_min")), 1),
            high_c=round(float(first("temperature_2m_max")), 1),
            precipitation_probability_percent=int(first("precipitation_probability_max")),
            precipitation_mm=round(float(first("precipitation_sum")), 1),
            max_wind_kmh=round(float(first("wind_speed_10m_max")), 1),
        )

    async def fetch_today(self, location: str | None = None) -> WeatherBrief:
        return await asyncio.to_thread(self._fetch_sync, location or self.settings.location)
