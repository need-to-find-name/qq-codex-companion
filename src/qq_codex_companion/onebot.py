from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve

from .config import OneBotSettings


LOGGER = logging.getLogger(__name__)

@dataclass(frozen=True)
class ImageAttachment:
    file: str
    url: str = ""
    summary: str = ""


MessageHandler = Callable[[str, int, str, list[ImageAttachment]], Awaitable[None]]
PokeHandler = Callable[[str, int], Awaitable[None]]
_CQ_CODE = re.compile(r"\[CQ:([^,\]]+)(?:,[^\]]*)?\]")


class OneBotError(RuntimeError):
    pass


class OneBotReverseServer:
    def __init__(
        self,
        settings: OneBotSettings,
        on_message: MessageHandler,
        on_poke: PokeHandler | None = None,
    ) -> None:
        self.settings = settings
        self.on_message = on_message
        self.on_poke = on_poke
        self._connection: ServerConnection | None = None
        self._connection_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._pending_actions: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._seen_ids: deque[int] = deque(maxlen=500)
        self._seen_set: set[int] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self.connected = asyncio.Event()

    async def run(self) -> None:
        async with serve(
            self._handle_connection,
            self.settings.host,
            self.settings.port,
            max_size=16 * 1024 * 1024,
            ping_interval=30,
            ping_timeout=30,
        ):
            LOGGER.info(
                "OneBot reverse WebSocket listening on ws://%s:%s%s",
                self.settings.host,
                self.settings.port,
                self.settings.path,
            )
            await asyncio.Future()

    def _authorized(self, connection: ServerConnection) -> bool:
        request = connection.request
        parsed = urlsplit(request.path)
        if parsed.path not in (self.settings.path, "/"):
            return False
        supplied = ""
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            supplied = header[7:].strip()
        if not supplied:
            supplied = parse_qs(parsed.query).get("access_token", [""])[0]
        return hmac.compare_digest(supplied, self.settings.access_token)

    async def _handle_connection(self, connection: ServerConnection) -> None:
        if not self._authorized(connection):
            LOGGER.warning("Rejected OneBot connection with invalid path or token")
            await connection.close(code=1008, reason="invalid path or token")
            return

        async with self._connection_lock:
            previous = self._connection
            self._connection = connection
            self.connected.set()
            if previous is not None and previous is not connection:
                await previous.close(code=1012, reason="replaced by a new OneBot connection")
        LOGGER.info("NapCat connected to the OneBot reverse WebSocket")

        try:
            async for frame in connection:
                if not isinstance(frame, str):
                    continue
                try:
                    payload = json.loads(frame)
                except json.JSONDecodeError:
                    LOGGER.warning("Ignoring invalid OneBot JSON frame")
                    continue
                if not isinstance(payload, dict):
                    continue
                echo = payload.get("echo")
                if echo is not None and "status" in payload:
                    future = self._pending_actions.get(str(echo))
                    if future is not None and not future.done():
                        future.set_result(payload)
                    continue
                await self._handle_event(payload)
        finally:
            async with self._connection_lock:
                if self._connection is connection:
                    self._connection = None
                    self.connected.clear()
            LOGGER.warning("NapCat disconnected from the OneBot reverse WebSocket")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        poke_user_id = _private_poke_sender(event, self.settings)
        if poke_user_id is not None:
            if self.on_poke is not None:
                conversation_id = f"private:{poke_user_id}"
                task = asyncio.create_task(self.on_poke(conversation_id, poke_user_id))
                self._tasks.add(task)
                task.add_done_callback(self._task_done)
            return

        if event.get("post_type") != "message" or event.get("message_type") != "private":
            return
        try:
            user_id = int(event.get("user_id", 0))
        except (TypeError, ValueError):
            return
        if user_id == self.settings.bot_qq:
            return
        if user_id not in self.settings.allowed_user_ids:
            LOGGER.warning("Ignoring private message from non-allowlisted QQ %s", user_id)
            return

        message_id = event.get("message_id")
        if isinstance(message_id, int):
            if message_id in self._seen_set:
                return
            if len(self._seen_ids) == self._seen_ids.maxlen:
                expired = self._seen_ids.popleft()
                self._seen_set.discard(expired)
            self._seen_ids.append(message_id)
            self._seen_set.add(message_id)

        text, images = _extract_content(event)
        text = text.strip()
        if not text and not images:
            return
        conversation_id = f"private:{user_id}"
        task = asyncio.create_task(
            self.on_message(conversation_id, user_id, text, images)
        )
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            LOGGER.exception("QQ message handling failed")

    async def send_private_message(self, user_id: int, message: str) -> dict[str, Any]:
        return await self._call_action(
            "send_private_msg",
            {"user_id": user_id, "message": message},
        )

    async def send_private_image(self, user_id: int, path: Path) -> dict[str, Any]:
        resolved = path.resolve(strict=True)
        return await self._call_action(
            "send_private_msg",
            {
                "user_id": user_id,
                "message": [
                    {
                        "type": "image",
                        "data": {"file": resolved.as_uri()},
                    }
                ],
            },
        )

    async def send_poke(self, user_id: int) -> dict[str, Any]:
        return await self._call_action("send_poke", {"user_id": user_id})

    async def get_image_path(self, attachment: ImageAttachment) -> str:
        reference = attachment.file or attachment.url
        if not reference:
            raise OneBotError("image attachment has no file reference")
        response = await self._call_action("get_image", {"file": reference})
        data = response.get("data", {})
        if not isinstance(data, dict):
            raise OneBotError("get_image returned invalid data")
        path = data.get("file")
        if not isinstance(path, str) or not path.strip():
            raise OneBotError("get_image returned no local file path")
        return path

    async def _call_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        connection = self._connection
        if connection is None:
            raise OneBotError("NapCat is not connected")
        echo = uuid.uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_actions[echo] = future
        request = {
            "action": action,
            "params": params,
            "echo": echo,
        }
        try:
            async with self._send_lock:
                await connection.send(json.dumps(request, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=15)
        finally:
            self._pending_actions.pop(echo, None)
        if response.get("status") != "ok":
            raise OneBotError(f"{action} failed: {response.get('message', response)}")
        return response


def _private_poke_sender(event: dict[str, Any], settings: OneBotSettings) -> int | None:
    if not (
        event.get("post_type") == "notice"
        and event.get("notice_type") == "notify"
        and event.get("sub_type") == "poke"
    ):
        return None
    try:
        target_id = int(event.get("target_id", 0))
        sender_id = int(event.get("sender_id", event.get("user_id", 0)))
    except (TypeError, ValueError):
        return None
    if target_id != settings.bot_qq or sender_id == settings.bot_qq:
        return None
    if sender_id not in settings.allowed_user_ids:
        LOGGER.warning("Ignoring poke from non-allowlisted QQ %s", sender_id)
        return None
    return sender_id


def _extract_content(event: dict[str, Any]) -> tuple[str, list[ImageAttachment]]:
    message = event.get("message")
    if isinstance(message, str):
        return _CQ_CODE.sub(lambda match: _cq_placeholder(match.group(1)), message), []
    if isinstance(message, list):
        parts: list[str] = []
        images: list[ImageAttachment] = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            kind = segment.get("type")
            data = segment.get("data", {})
            if kind == "text" and isinstance(data, dict):
                parts.append(str(data.get("text", "")))
            elif kind == "image" and isinstance(data, dict):
                reference = str(data.get("file", "")).strip()
                url = str(data.get("url", "")).strip()
                if reference or url:
                    images.append(
                        ImageAttachment(
                            file=reference,
                            url=url,
                            summary=str(data.get("summary", "")).strip(),
                        )
                    )
                else:
                    parts.append(_cq_placeholder("image"))
            elif kind in {"record", "video", "file"}:
                parts.append(_cq_placeholder(str(kind)))
        return "".join(parts), images
    raw = event.get("raw_message")
    return (str(raw) if raw is not None else ""), []


def _extract_text(event: dict[str, Any]) -> str:
    return _extract_content(event)[0]


def _cq_placeholder(kind: str) -> str:
    return {
        "image": "[图片]",
        "record": "[语音]",
        "video": "[视频]",
        "file": "[文件]",
        "face": "[表情]",
        "reply": "",
    }.get(kind, "")
