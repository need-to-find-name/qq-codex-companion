from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OneBotSettings:
    host: str
    port: int
    path: str
    access_token: str
    bot_qq: int
    allowed_user_ids: frozenset[int]


@dataclass(frozen=True)
class CodexSettings:
    executable: str
    workspace: Path
    proxy_url: str
    model: str
    effort: str
    approval_policy: str
    sandbox: str
    turn_timeout_seconds: float
    skill_path: Path
    memory_core_path: Path
    memory_archive_path: Path


@dataclass(frozen=True)
class ReplySettings:
    max_parts: int
    max_chars_per_part: int
    min_delay_seconds: float
    max_delay_seconds: float
    debounce_seconds: float


@dataclass(frozen=True)
class PokeSettings:
    enabled: bool
    after_reply_probability: float
    poke_back_probability: float
    cooldown_seconds: float


@dataclass(frozen=True)
class ProactiveSettings:
    enabled: bool
    active_start_minute: int
    active_end_minute: int
    min_idle_seconds: float
    max_idle_seconds: float
    retry_min_seconds: float
    retry_max_seconds: float
    followup_min_seconds: float
    followup_max_seconds: float
    send_probability: float
    daily_limit: int
    image_probability: float
    image_daily_limit: int
    selfie_probability: float
    selfie_reference_path: Path | None
    poke_probability: float
    poll_seconds: float


@dataclass(frozen=True)
class WeatherSettings:
    enabled: bool
    location: str
    location_aliases: tuple[tuple[str, str], ...]
    daily_time_minute: int
    grace_minutes: int
    proxy_url: str
    timeout_seconds: float
    poll_seconds: float


@dataclass(frozen=True)
class MemorySettings:
    enabled: bool
    store_file: Path
    max_entries: int
    max_retrieved: int
    max_prompt_chars: int


@dataclass(frozen=True)
class RuntimeSettings:
    state_file: Path
    log_file: Path
    image_dir: Path


@dataclass(frozen=True)
class Settings:
    root: Path
    onebot: OneBotSettings
    codex: CodexSettings
    reply: ReplySettings
    poke: PokeSettings
    proactive: ProactiveSettings
    weather: WeatherSettings
    memory: MemorySettings
    runtime: RuntimeSettings


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _clock_minute(value: object, label: str) -> int:
    text = str(value).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{label} must use HH:MM format") from error
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{label} must be a valid local time")
    return hour * 60 + minute


def load_settings(path: Path) -> Settings:
    path = path.resolve()
    root = path.parent
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    onebot = raw.get("onebot", {})
    codex = raw.get("codex", {})
    reply = raw.get("reply", {})
    poke = raw.get("poke", {})
    proactive = raw.get("proactive", {})
    weather = raw.get("weather", {})
    memory = raw.get("memory", {})
    runtime = raw.get("runtime", {})

    token = os.environ.get("ONEBOT_ACCESS_TOKEN", str(onebot.get("access_token", ""))).strip()
    if not token or token == "replace-me":
        raise ValueError("onebot.access_token is required")

    allowed = frozenset(int(value) for value in onebot.get("allowed_user_ids", []))
    if not allowed:
        raise ValueError("onebot.allowed_user_ids must contain at least one trusted QQ number")

    min_delay = float(reply.get("min_delay_seconds", 0.7))
    max_delay = float(reply.get("max_delay_seconds", 1.4))
    if min_delay < 0 or max_delay < min_delay:
        raise ValueError("reply delays must satisfy 0 <= min_delay <= max_delay")

    max_parts = int(reply.get("max_parts", 4))
    max_chars = int(reply.get("max_chars_per_part", 160))
    if not 1 <= max_parts <= 8:
        raise ValueError("reply.max_parts must be between 1 and 8")
    if max_chars < 20:
        raise ValueError("reply.max_chars_per_part must be at least 20")

    after_reply_probability = float(poke.get("after_reply_probability", 0.35))
    poke_back_probability = float(poke.get("poke_back_probability", 0.70))
    for label, probability in (
        ("poke.after_reply_probability", after_reply_probability),
        ("poke.poke_back_probability", poke_back_probability),
    ):
        if not 0 <= probability <= 1:
            raise ValueError(f"{label} must be between 0 and 1")
    poke_cooldown = float(poke.get("cooldown_seconds", 300))
    if poke_cooldown < 0:
        raise ValueError("poke.cooldown_seconds must be non-negative")

    active_start = _clock_minute(
        proactive.get("active_start", "09:30"),
        "proactive.active_start",
    )
    active_end = _clock_minute(
        proactive.get("active_end", "23:00"),
        "proactive.active_end",
    )
    if active_start >= active_end:
        raise ValueError("proactive active window must start before it ends")

    def minute_range(min_key: str, max_key: str, defaults: tuple[float, float]) -> tuple[float, float]:
        minimum = float(proactive.get(min_key, defaults[0]))
        maximum = float(proactive.get(max_key, defaults[1]))
        if minimum < 0 or maximum < minimum:
            raise ValueError(f"proactive {min_key}/{max_key} range is invalid")
        return minimum * 60, maximum * 60

    min_idle, max_idle = minute_range("min_idle_minutes", "max_idle_minutes", (60, 120))
    retry_min, retry_max = minute_range("retry_min_minutes", "retry_max_minutes", (35, 75))
    followup_min, followup_max = minute_range(
        "followup_min_minutes",
        "followup_max_minutes",
        (180, 300),
    )
    proactive_probability = float(proactive.get("send_probability", 0.65))
    proactive_image_probability = float(proactive.get("image_probability", 0.22))
    proactive_selfie_probability = float(proactive.get("selfie_probability", 0.35))
    proactive_poke_probability = float(proactive.get("poke_probability", 0.15))
    for label, probability in (
        ("proactive.send_probability", proactive_probability),
        ("proactive.image_probability", proactive_image_probability),
        ("proactive.selfie_probability", proactive_selfie_probability),
        ("proactive.poke_probability", proactive_poke_probability),
    ):
        if not 0 <= probability <= 1:
            raise ValueError(f"{label} must be between 0 and 1")
    proactive_daily_limit = int(proactive.get("daily_limit", 3))
    if not 1 <= proactive_daily_limit <= 12:
        raise ValueError("proactive.daily_limit must be between 1 and 12")
    proactive_image_daily_limit = int(proactive.get("image_daily_limit", 1))
    if not 0 <= proactive_image_daily_limit <= proactive_daily_limit:
        raise ValueError(
            "proactive.image_daily_limit must be between 0 and proactive.daily_limit"
        )
    selfie_reference_value = str(proactive.get("selfie_reference", "")).strip()
    selfie_reference_path = (
        _resolve(root, selfie_reference_value) if selfie_reference_value else None
    )
    if selfie_reference_path is not None and not selfie_reference_path.is_file():
        raise FileNotFoundError(
            f"proactive selfie reference not found: {selfie_reference_path}"
        )
    proactive_poll_seconds = float(proactive.get("poll_seconds", 30))
    if proactive_poll_seconds < 5:
        raise ValueError("proactive.poll_seconds must be at least 5")

    weather_enabled = bool(weather.get("enabled", False))
    weather_location = str(weather.get("location", "")).strip()
    if weather_enabled and not weather_location:
        raise ValueError("weather.location is required when weather is enabled")
    raw_location_aliases = weather.get("location_aliases", {})
    if not isinstance(raw_location_aliases, dict):
        raise ValueError("weather.location_aliases must be a TOML table")
    weather_location_aliases = tuple(
        (str(alias).strip(), str(location).strip())
        for alias, location in raw_location_aliases.items()
        if str(alias).strip() and str(location).strip()
    )
    weather_daily_time = _clock_minute(
        weather.get("daily_time", "07:30"),
        "weather.daily_time",
    )
    weather_grace_minutes = int(weather.get("grace_minutes", 180))
    if not 1 <= weather_grace_minutes <= 720:
        raise ValueError("weather.grace_minutes must be between 1 and 720")
    weather_timeout_seconds = float(weather.get("timeout_seconds", 15))
    if weather_timeout_seconds <= 0:
        raise ValueError("weather.timeout_seconds must be positive")
    weather_poll_seconds = float(weather.get("poll_seconds", 30))
    if weather_poll_seconds < 5:
        raise ValueError("weather.poll_seconds must be at least 5")

    memory_enabled = bool(memory.get("enabled", False))
    memory_max_entries = int(memory.get("max_entries", 500))
    memory_max_retrieved = int(memory.get("max_retrieved", 8))
    memory_max_prompt_chars = int(memory.get("max_prompt_chars", 1800))
    if not 20 <= memory_max_entries <= 5000:
        raise ValueError("memory.max_entries must be between 20 and 5000")
    if not 1 <= memory_max_retrieved <= 30:
        raise ValueError("memory.max_retrieved must be between 1 and 30")
    if not 300 <= memory_max_prompt_chars <= 8000:
        raise ValueError("memory.max_prompt_chars must be between 300 and 8000")

    skill_path = root / "skills" / "qq-companion" / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(f"companion skill not found: {skill_path}")

    memory_core_path = _resolve(
        root,
        str(codex.get("memory_core", "./workspace/memory/ACTIVE_PROFILE.md")),
    )
    memory_archive_path = _resolve(
        root,
        str(
            codex.get(
                "memory_archive",
                "./workspace/memory/ARCHIVE.md",
            )
        ),
    )
    if memory_enabled:
        for label, memory_path in (
            ("active companion memory", memory_core_path),
            ("companion memory archive", memory_archive_path),
        ):
            if not memory_path.is_file():
                raise FileNotFoundError(f"{label} not found: {memory_path}")

    settings = Settings(
        root=root,
        onebot=OneBotSettings(
            host=str(onebot.get("host", "127.0.0.1")),
            port=int(onebot.get("port", 8643)),
            path=str(onebot.get("path", "/ws")),
            access_token=token,
            bot_qq=int(onebot.get("bot_qq", 0)),
            allowed_user_ids=allowed,
        ),
        codex=CodexSettings(
            executable=str(codex.get("executable", "codex")),
            workspace=_resolve(root, str(codex.get("workspace", "./workspace"))),
            proxy_url=str(codex.get("proxy_url", "")).strip(),
            model=str(codex.get("model", "")).strip(),
            effort=str(codex.get("effort", "medium")),
            approval_policy=str(codex.get("approval_policy", "never")),
            sandbox=str(codex.get("sandbox", "read-only")),
            turn_timeout_seconds=float(codex.get("turn_timeout_seconds", 240)),
            skill_path=skill_path.resolve(),
            memory_core_path=memory_core_path,
            memory_archive_path=memory_archive_path,
        ),
        reply=ReplySettings(
            max_parts=max_parts,
            max_chars_per_part=max_chars,
            min_delay_seconds=min_delay,
            max_delay_seconds=max_delay,
            debounce_seconds=float(reply.get("debounce_seconds", 0.8)),
        ),
        poke=PokeSettings(
            enabled=bool(poke.get("enabled", True)),
            after_reply_probability=after_reply_probability,
            poke_back_probability=poke_back_probability,
            cooldown_seconds=poke_cooldown,
        ),
        proactive=ProactiveSettings(
            enabled=bool(proactive.get("enabled", True)),
            active_start_minute=active_start,
            active_end_minute=active_end,
            min_idle_seconds=min_idle,
            max_idle_seconds=max_idle,
            retry_min_seconds=retry_min,
            retry_max_seconds=retry_max,
            followup_min_seconds=followup_min,
            followup_max_seconds=followup_max,
            send_probability=proactive_probability,
            daily_limit=proactive_daily_limit,
            image_probability=proactive_image_probability,
            image_daily_limit=proactive_image_daily_limit,
            selfie_probability=proactive_selfie_probability,
            selfie_reference_path=selfie_reference_path,
            poke_probability=proactive_poke_probability,
            poll_seconds=proactive_poll_seconds,
        ),
        weather=WeatherSettings(
            enabled=weather_enabled,
            location=weather_location,
            location_aliases=weather_location_aliases,
            daily_time_minute=weather_daily_time,
            grace_minutes=weather_grace_minutes,
            proxy_url=str(weather.get("proxy_url", "")).strip(),
            timeout_seconds=weather_timeout_seconds,
            poll_seconds=weather_poll_seconds,
        ),
        memory=MemorySettings(
            enabled=memory_enabled,
            store_file=_resolve(
                root,
                str(memory.get("store_file", "./data/companion_memory.json")),
            ),
            max_entries=memory_max_entries,
            max_retrieved=memory_max_retrieved,
            max_prompt_chars=memory_max_prompt_chars,
        ),
        runtime=RuntimeSettings(
            state_file=_resolve(root, str(runtime.get("state_file", "./data/state.json"))),
            log_file=_resolve(root, str(runtime.get("log_file", "./logs/bridge.log"))),
            image_dir=_resolve(
                root,
                str(runtime.get("image_dir", "./workspace/incoming-images")),
            ),
        ),
    )
    settings.codex.workspace.mkdir(parents=True, exist_ok=True)
    settings.runtime.state_file.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime.log_file.parent.mkdir(parents=True, exist_ok=True)
    settings.runtime.image_dir.mkdir(parents=True, exist_ok=True)
    settings.memory.store_file.parent.mkdir(parents=True, exist_ok=True)
    return settings
