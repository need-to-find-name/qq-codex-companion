from __future__ import annotations

import asyncio
import logging
import os
import random
import hashlib
import shutil
import sys
import time
import json
from datetime import datetime, timedelta
from pathlib import Path

from .codex_client import CodexAppServer
from .config import Settings, load_settings
from .onebot import ImageAttachment, OneBotReverseServer
from .replies import ReplyPlan, parse_reply_plan
from .state import ThreadState
from .weather import OpenMeteoWeather, detect_weather_location
from .memory import CompanionMemoryStore


LOGGER = logging.getLogger(__name__)


def _is_active_timestamp(timestamp: float, start_minute: int, end_minute: int) -> bool:
    local = datetime.fromtimestamp(timestamp).astimezone()
    minute = local.hour * 60 + local.minute
    return start_minute <= minute < end_minute


def _next_active_timestamp(timestamp: float, start_minute: int, jitter_seconds: float) -> float:
    local = datetime.fromtimestamp(timestamp).astimezone()
    hour, minute = divmod(start_minute, 60)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.timestamp() + jitter_seconds


def _choose_proactive_interaction(
    *,
    image_probability: float,
    selfie_probability: float,
    image_count_today: int,
    image_daily_limit: int,
) -> str:
    if image_count_today >= image_daily_limit or random.random() >= image_probability:
        return "proactive"
    return "proactive_selfie" if random.random() < selfie_probability else "proactive_image"


class CompanionBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = ThreadState(settings.runtime.state_file)
        self.memory = (
            CompanionMemoryStore(settings.memory) if settings.memory.enabled else None
        )
        self.codex = CodexAppServer(settings.codex, self.state, self.memory)
        self.onebot = OneBotReverseServer(
            settings.onebot,
            self.enqueue_message,
            self.enqueue_poke,
        )
        self._pending_text: dict[str, list[str]] = {}
        self._pending_images: dict[str, list[ImageAttachment]] = {}
        self._debounce_tasks: dict[str, asyncio.Task[None]] = {}
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        self._last_outgoing_poke: dict[int, float] = {}
        self.weather = OpenMeteoWeather(settings.weather)
        self._weather_retry_after = 0.0

    async def run(self) -> None:
        await self.codex.start()
        proactive_task: asyncio.Task[None] | None = None
        weather_task: asyncio.Task[None] | None = None
        if self.settings.proactive.enabled:
            proactive_task = asyncio.create_task(self._proactive_loop())
        if self.settings.weather.enabled:
            weather_task = asyncio.create_task(self._weather_loop())
        try:
            await self.onebot.run()
        finally:
            tasks = [task for task in (proactive_task, weather_task) if task is not None]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.codex.close()

    def _schedule_after_user_activity(
        self,
        conversation_id: str,
        *,
        now: float | None = None,
    ) -> None:
        if not self.settings.proactive.enabled:
            return
        current = time.time() if now is None else now
        if not _is_active_timestamp(
            current,
            self.settings.proactive.active_start_minute,
            self.settings.proactive.active_end_minute,
        ):
            self._schedule_quiet_window_exit(conversation_id, current)
            return
        delay = random.uniform(
            self.settings.proactive.min_idle_seconds,
            self.settings.proactive.max_idle_seconds,
        )
        self.state.set_next_proactive(conversation_id, current + delay)

    async def enqueue_message(
        self,
        conversation_id: str,
        user_id: int,
        text: str,
        images: list[ImageAttachment],
    ) -> None:
        LOGGER.info(
            "Received QQ message from %s (%s chars, %s image(s))",
            user_id,
            len(text),
            len(images),
        )
        detected_location = detect_weather_location(
            text,
            self.settings.weather.location_aliases,
        )
        if detected_location is not None:
            self.state.set_weather_location(conversation_id, detected_location)
            LOGGER.info("Updated the weather location for QQ %s", user_id)
        self._schedule_after_user_activity(conversation_id)
        if text:
            self._pending_text.setdefault(conversation_id, []).append(text)
        if images:
            self._pending_images.setdefault(conversation_id, []).extend(images)
        previous = self._debounce_tasks.get(conversation_id)
        if previous is not None:
            previous.cancel()
        task = asyncio.create_task(self._flush_after_delay(conversation_id, user_id))
        self._debounce_tasks[conversation_id] = task

    async def enqueue_poke(self, conversation_id: str, user_id: int) -> None:
        if not self.settings.poke.enabled:
            return
        LOGGER.info("Received private poke from %s", user_id)
        self._schedule_after_user_activity(conversation_id)
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            try:
                result = await self.codex.chat(
                    conversation_id,
                    user_id,
                    "",
                    interaction="poke",
                )
                plan = parse_reply_plan(
                    result.text,
                    max_parts=2,
                    max_chars=self.settings.reply.max_chars_per_part,
                    allow_empty=True,
                )
                await self._send_plan(
                    user_id,
                    plan,
                    poke_probability=self.settings.poke.poke_back_probability,
                )
            except Exception:
                LOGGER.exception("Failed to handle private poke")

    async def _proactive_loop(self) -> None:
        now = time.time()
        for user_id in self.settings.onebot.allowed_user_ids:
            conversation_id = f"private:{user_id}"
            if not _is_active_timestamp(
                now,
                self.settings.proactive.active_start_minute,
                self.settings.proactive.active_end_minute,
            ):
                self._schedule_quiet_window_exit(conversation_id, now)
            elif self.state.get_next_proactive(conversation_id) is None:
                self._schedule_after_user_activity(conversation_id)
        while True:
            await asyncio.sleep(self.settings.proactive.poll_seconds)
            if not self.onebot.connected.is_set():
                continue
            for user_id in self.settings.onebot.allowed_user_ids:
                try:
                    await self._maybe_send_proactive(user_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception("Failed to handle proactive QQ message")
                    conversation_id = f"private:{user_id}"
                    self.state.set_next_proactive(
                        conversation_id,
                        time.time()
                        + random.uniform(
                            self.settings.proactive.retry_min_seconds,
                            self.settings.proactive.retry_max_seconds,
                        ),
                    )

    async def _weather_loop(self) -> None:
        while True:
            if self.onebot.connected.is_set():
                for user_id in self.settings.onebot.allowed_user_ids:
                    try:
                        await self._maybe_send_morning_weather(user_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        self._weather_retry_after = time.time() + 10 * 60
                        LOGGER.exception("Failed to send the morning weather message")
            await asyncio.sleep(self.settings.weather.poll_seconds)

    async def _maybe_send_morning_weather(self, user_id: int) -> None:
        now = time.time()
        if now < self._weather_retry_after:
            return
        local = datetime.fromtimestamp(now).astimezone()
        minute = local.hour * 60 + local.minute
        start = self.settings.weather.daily_time_minute
        if minute < start or minute >= start + self.settings.weather.grace_minutes:
            return

        conversation_id = f"private:{user_id}"
        today = local.date().isoformat()
        if self.state.get_last_weather_date(conversation_id) == today:
            return
        if self._pending_text.get(conversation_id) or self._pending_images.get(conversation_id):
            return
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        if lock.locked():
            return

        async with lock:
            if self.state.get_last_weather_date(conversation_id) == today:
                return
            location = (
                self.state.get_weather_location(conversation_id)
                or self.settings.weather.location
            )
            brief = await self.weather.fetch_today(location)
            result = await self.codex.chat(
                conversation_id,
                user_id,
                json.dumps(brief.as_prompt_data(), ensure_ascii=False),
                interaction="weather",
            )
            plan = parse_reply_plan(
                result.text,
                max_parts=2,
                max_chars=self.settings.reply.max_chars_per_part,
            )
            await self._send_plan(user_id, plan, poke_probability=0.0)
            sent_at = time.time()
            self.state.set_last_weather_date(conversation_id, today)
            if self.settings.proactive.enabled:
                self.state.set_next_proactive(
                    conversation_id,
                    sent_at
                    + random.uniform(
                        self.settings.proactive.followup_min_seconds,
                        self.settings.proactive.followup_max_seconds,
                    ),
                )
            LOGGER.info("Sent the morning weather message to %s", user_id)

    def _schedule_quiet_window_exit(self, conversation_id: str, now: float) -> None:
        jitter = random.uniform(20 * 60, 90 * 60)
        next_time = _next_active_timestamp(
            now,
            self.settings.proactive.active_start_minute,
            jitter,
        )
        self.state.set_next_proactive(conversation_id, next_time)

    def _proactive_count_today(self, conversation_id: str, now: float) -> int:
        today = datetime.fromtimestamp(now).astimezone().date()
        return sum(
            1
            for timestamp in self.state.get_proactive_history(conversation_id)
            if datetime.fromtimestamp(timestamp).astimezone().date() == today
        )

    def _proactive_image_count_today(self, conversation_id: str, now: float) -> int:
        today = datetime.fromtimestamp(now).astimezone().date()
        return sum(
            1
            for timestamp in self.state.get_proactive_image_history(conversation_id)
            if datetime.fromtimestamp(timestamp).astimezone().date() == today
        )

    async def _maybe_send_proactive(self, user_id: int) -> None:
        conversation_id = f"private:{user_id}"
        now = time.time()
        due = self.state.get_next_proactive(conversation_id)
        if due is None:
            self._schedule_after_user_activity(conversation_id, now=now)
            return
        if due > now:
            return
        if not _is_active_timestamp(
            now,
            self.settings.proactive.active_start_minute,
            self.settings.proactive.active_end_minute,
        ):
            self._schedule_quiet_window_exit(conversation_id, now)
            return
        if self._proactive_count_today(conversation_id, now) >= self.settings.proactive.daily_limit:
            self._schedule_quiet_window_exit(conversation_id, now)
            return
        if self._pending_text.get(conversation_id) or self._pending_images.get(conversation_id):
            self.state.set_next_proactive(conversation_id, now + 10 * 60)
            return
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        if lock.locked():
            self.state.set_next_proactive(conversation_id, now + 10 * 60)
            return
        if random.random() >= self.settings.proactive.send_probability:
            self.state.set_next_proactive(
                conversation_id,
                now
                + random.uniform(
                    self.settings.proactive.retry_min_seconds,
                    self.settings.proactive.retry_max_seconds,
                ),
            )
            LOGGER.info("Proactive message candidate deferred by randomness")
            return

        async with lock:
            refreshed_due = self.state.get_next_proactive(conversation_id)
            if refreshed_due is None or refreshed_due > time.time():
                return
            interaction = _choose_proactive_interaction(
                image_probability=self.settings.proactive.image_probability,
                selfie_probability=self.settings.proactive.selfie_probability,
                image_count_today=self._proactive_image_count_today(conversation_id, now),
                image_daily_limit=self.settings.proactive.image_daily_limit,
            )
            reference_images: tuple[Path, ...] = ()
            if (
                interaction == "proactive_selfie"
                and self.settings.proactive.selfie_reference_path is not None
            ):
                reference_images = (self.settings.proactive.selfie_reference_path,)
            result = await self.codex.chat(
                conversation_id,
                user_id,
                "",
                interaction=interaction,
                image_paths=reference_images,
            )
            plan = parse_reply_plan(
                result.text,
                max_parts=2,
                max_chars=self.settings.reply.max_chars_per_part,
            )
            refreshed_due = self.state.get_next_proactive(conversation_id)
            if refreshed_due is None or refreshed_due > time.time():
                LOGGER.info("Cancelled proactive message because new user activity arrived")
                return
            await self._send_plan(
                user_id,
                plan,
                poke_probability=self.settings.proactive.poke_probability,
                image_paths=result.image_paths,
            )
            sent_at = time.time()
            self.state.record_proactive(conversation_id, sent_at)
            if result.image_paths:
                self.state.record_proactive_image(conversation_id, sent_at)
            elif interaction != "proactive":
                LOGGER.warning(
                    "Proactive image was requested but Codex produced no deliverable image"
                )
            self.state.set_next_proactive(
                conversation_id,
                sent_at
                + random.uniform(
                    self.settings.proactive.followup_min_seconds,
                    self.settings.proactive.followup_max_seconds,
                ),
            )
            LOGGER.info(
                "Sent a proactive QQ message to %s (%s, %s image(s))",
                user_id,
                interaction,
                len(result.image_paths),
            )

    async def _flush_after_delay(self, conversation_id: str, user_id: int) -> None:
        try:
            await asyncio.sleep(self.settings.reply.debounce_seconds)
            messages = self._pending_text.pop(conversation_id, [])
            images = self._pending_images.pop(conversation_id, [])
            if not messages and not images:
                return
            lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())
            async with lock:
                combined = "\n".join(messages)
                image_paths, failed_images = await self._materialize_images(images)
                if failed_images:
                    failure_note = f"[{failed_images} 张图片未能读取]"
                    combined = "\n".join(part for part in (combined, failure_note) if part)
                result = await self.codex.chat(
                    conversation_id,
                    user_id,
                    combined,
                    image_paths=image_paths,
                )
                plan = parse_reply_plan(
                    result.text,
                    max_parts=self.settings.reply.max_parts,
                    max_chars=self.settings.reply.max_chars_per_part,
                )
                if self.memory is not None:
                    changed, removed = self.memory.apply(
                        plan.memory_updates,
                        plan.memory_forget,
                    )
                    if changed or removed:
                        LOGGER.info(
                            "Applied companion memory changes (%s added/updated, %s removed)",
                            changed,
                            removed,
                        )
                LOGGER.info("Codex produced %s QQ message part(s)", len(plan.messages))
                await self._send_plan(
                    user_id,
                    plan,
                    poke_probability=self.settings.poke.after_reply_probability,
                    image_paths=result.image_paths,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Failed to generate or send the QQ reply")
            try:
                await self.onebot.send_private_message(user_id, "我刚才走神了一下，你再说一次好吗？")
            except Exception:
                LOGGER.exception("Failed to send the fallback QQ reply")
        finally:
            current = asyncio.current_task()
            if self._debounce_tasks.get(conversation_id) is current:
                self._debounce_tasks.pop(conversation_id, None)

    async def _materialize_images(
        self,
        images: list[ImageAttachment],
    ) -> tuple[list[Path], int]:
        paths: list[Path] = []
        failed = 0
        for attachment in images[:4]:
            try:
                source_text = await self.onebot.get_image_path(attachment)
                source = Path(source_text)
                if not source.is_file():
                    raise FileNotFoundError(source)
                if source.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("image exceeds the 20 MiB safety limit")
                suffix = source.suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
                    raise ValueError(f"unsupported image extension: {suffix}")
                key = hashlib.sha256(
                    (attachment.file or attachment.url).encode("utf-8")
                ).hexdigest()
                destination = self.settings.runtime.image_dir / f"{key}{suffix}"
                if not destination.is_file():
                    await asyncio.to_thread(shutil.copy2, source, destination)
                paths.append(destination.resolve())
            except Exception:
                failed += 1
                LOGGER.exception("Failed to retrieve an incoming QQ image")
        if len(images) > 4:
            failed += len(images) - 4
            LOGGER.warning("Only the first 4 images in one message batch are accepted")
        if paths:
            LOGGER.info("Prepared %s QQ image(s) for Codex", len(paths))
        return paths, failed

    async def _send_plan(
        self,
        user_id: int,
        plan: ReplyPlan,
        *,
        poke_probability: float,
        image_paths: tuple[Path, ...] = (),
    ) -> None:
        for index, path in enumerate(image_paths):
            if index:
                await asyncio.sleep(0.4)
            await self.onebot.send_private_image(user_id, path)
            LOGGER.info("Sent a generated image to QQ %s", user_id)

        if image_paths and plan.messages:
            await asyncio.sleep(0.5)
        for index, part in enumerate(plan.messages):
            if index:
                await asyncio.sleep(
                    random.uniform(
                        self.settings.reply.min_delay_seconds,
                        self.settings.reply.max_delay_seconds,
                    )
                )
            await self.onebot.send_private_message(user_id, part)

        if not (self.settings.poke.enabled and plan.poke_user):
            return
        now = time.monotonic()
        last = self._last_outgoing_poke.get(user_id, float("-inf"))
        if now - last < self.settings.poke.cooldown_seconds:
            LOGGER.info("Skipping requested poke because cooldown is active")
            return
        if random.random() >= poke_probability:
            LOGGER.info("Skipping requested poke because the spontaneity gate declined")
            return
        if plan.messages:
            await asyncio.sleep(random.uniform(0.4, 1.2))
        await self.onebot.send_poke(user_id)
        self._last_outgoing_poke[user_id] = time.monotonic()
        LOGGER.info("Sent a spontaneous private poke to %s", user_id)


def _configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[stream, file_handler])


async def _async_main() -> None:
    config_path = Path(os.environ.get("QQ_CODEX_CONFIG", "config.local.toml"))
    settings = load_settings(config_path)
    _configure_logging(settings.runtime.log_file)
    LOGGER.info("Starting QQ Codex Companion for %s trusted QQ account(s)", len(settings.onebot.allowed_user_ids))
    await CompanionBridge(settings).run()


def main() -> None:
    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
