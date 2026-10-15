from __future__ import annotations

import json
from pathlib import Path
from threading import Lock


class ThreadState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self._threads: dict[str, str] = {}
        self._last_interactions: dict[str, float] = {}
        self._next_proactive: dict[str, float] = {}
        self._proactive_history: dict[str, list[float]] = {}
        self._proactive_image_history: dict[str, list[float]] = {}
        self._last_weather_dates: dict[str, str] = {}
        self._weather_locations: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        threads = raw.get("threads", {}) if isinstance(raw, dict) else {}
        if isinstance(threads, dict):
            self._threads = {
                str(key): str(value)
                for key, value in threads.items()
                if isinstance(value, str) and value
            }
        last_interactions = raw.get("last_interactions", {}) if isinstance(raw, dict) else {}
        if isinstance(last_interactions, dict):
            self._last_interactions = {
                str(key): float(value)
                for key, value in last_interactions.items()
                if isinstance(value, (int, float))
            }
        next_proactive = raw.get("next_proactive", {}) if isinstance(raw, dict) else {}
        if isinstance(next_proactive, dict):
            self._next_proactive = {
                str(key): float(value)
                for key, value in next_proactive.items()
                if isinstance(value, (int, float))
            }
        proactive_history = raw.get("proactive_history", {}) if isinstance(raw, dict) else {}
        if isinstance(proactive_history, dict):
            self._proactive_history = {
                str(key): [float(item) for item in value if isinstance(item, (int, float))]
                for key, value in proactive_history.items()
                if isinstance(value, list)
            }
        proactive_image_history = (
            raw.get("proactive_image_history", {}) if isinstance(raw, dict) else {}
        )
        if isinstance(proactive_image_history, dict):
            self._proactive_image_history = {
                str(key): [float(item) for item in value if isinstance(item, (int, float))]
                for key, value in proactive_image_history.items()
                if isinstance(value, list)
            }
        last_weather_dates = raw.get("last_weather_dates", {}) if isinstance(raw, dict) else {}
        if isinstance(last_weather_dates, dict):
            self._last_weather_dates = {
                str(key): str(value)
                for key, value in last_weather_dates.items()
                if isinstance(value, str) and value
            }
        weather_locations = raw.get("weather_locations", {}) if isinstance(raw, dict) else {}
        if isinstance(weather_locations, dict):
            self._weather_locations = {
                str(key): str(value)
                for key, value in weather_locations.items()
                if isinstance(value, str) and value
            }

    def _save(self) -> None:
        payload = json.dumps(
            {
                "threads": self._threads,
                "last_interactions": self._last_interactions,
                "next_proactive": self._next_proactive,
                "proactive_history": self._proactive_history,
                "proactive_image_history": self._proactive_image_history,
                "last_weather_dates": self._last_weather_dates,
                "weather_locations": self._weather_locations,
            },
            ensure_ascii=False,
            indent=2,
        )
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def get(self, conversation_id: str) -> str | None:
        return self._threads.get(conversation_id)

    def set(self, conversation_id: str, thread_id: str) -> None:
        with self._lock:
            self._threads[conversation_id] = thread_id
            self._save()

    def get_last_interaction(self, conversation_id: str) -> float | None:
        return self._last_interactions.get(conversation_id)

    def set_last_interaction(self, conversation_id: str, timestamp: float) -> None:
        with self._lock:
            self._last_interactions[conversation_id] = timestamp
            self._save()

    def get_next_proactive(self, conversation_id: str) -> float | None:
        return self._next_proactive.get(conversation_id)

    def set_next_proactive(self, conversation_id: str, timestamp: float) -> None:
        with self._lock:
            self._next_proactive[conversation_id] = timestamp
            self._save()

    def get_proactive_history(self, conversation_id: str) -> tuple[float, ...]:
        return tuple(self._proactive_history.get(conversation_id, []))

    def record_proactive(self, conversation_id: str, timestamp: float) -> None:
        with self._lock:
            history = self._proactive_history.setdefault(conversation_id, [])
            history.append(timestamp)
            cutoff = timestamp - 3 * 24 * 60 * 60
            self._proactive_history[conversation_id] = [
                item for item in history if item >= cutoff
            ]
            self._save()

    def get_proactive_image_history(self, conversation_id: str) -> tuple[float, ...]:
        return tuple(self._proactive_image_history.get(conversation_id, []))

    def record_proactive_image(self, conversation_id: str, timestamp: float) -> None:
        with self._lock:
            history = self._proactive_image_history.setdefault(conversation_id, [])
            history.append(timestamp)
            cutoff = timestamp - 3 * 24 * 60 * 60
            self._proactive_image_history[conversation_id] = [
                item for item in history if item >= cutoff
            ]
            self._save()

    def get_last_weather_date(self, conversation_id: str) -> str | None:
        return self._last_weather_dates.get(conversation_id)

    def set_last_weather_date(self, conversation_id: str, date: str) -> None:
        with self._lock:
            self._last_weather_dates[conversation_id] = date
            self._save()

    def get_weather_location(self, conversation_id: str) -> str | None:
        return self._weather_locations.get(conversation_id)

    def set_weather_location(self, conversation_id: str, location: str) -> None:
        with self._lock:
            self._weather_locations[conversation_id] = location
            self._save()

    def remove(self, conversation_id: str) -> None:
        with self._lock:
            if self._threads.pop(conversation_id, None) is None:
                return
            self._last_interactions.pop(conversation_id, None)
            self._next_proactive.pop(conversation_id, None)
            self._proactive_history.pop(conversation_id, None)
            self._proactive_image_history.pop(conversation_id, None)
            self._last_weather_dates.pop(conversation_id, None)
            self._weather_locations.pop(conversation_id, None)
            self._save()
