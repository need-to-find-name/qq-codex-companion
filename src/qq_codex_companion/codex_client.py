from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from .config import CodexSettings
from .state import ThreadState
from .memory import CompanionMemoryStore


LOGGER = logging.getLogger(__name__)

# app-server can emit a single JSON line containing retained image data. The
# asyncio default (64 KiB) is too small for image-enabled conversations.
_APP_SERVER_STREAM_LIMIT = 128 * 1024 * 1024

_SANDBOX_POLICY_TYPES = {
    "read-only": "readOnly",
    "workspace-write": "workspaceWrite",
    "danger-full-access": "dangerFullAccess",
}

_LOCAL_NO_PROXY_HOSTS = ("localhost", "127.0.0.1", "::1")


def codex_subprocess_environment(proxy_url: str) -> dict[str, str]:
    """Build a stable app-server environment independent of the launcher."""
    environment = os.environ.copy()
    proxy_url = proxy_url.strip()
    if not proxy_url:
        return environment

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        environment[name] = proxy_url

    existing_no_proxy = environment.get("NO_PROXY", environment.get("no_proxy", ""))
    no_proxy_hosts = [host.strip() for host in existing_no_proxy.split(",") if host.strip()]
    known_hosts = {host.lower() for host in no_proxy_hosts}
    for host in _LOCAL_NO_PROXY_HOSTS:
        if host.lower() not in known_hosts:
            no_proxy_hosts.append(host)
    normalized_no_proxy = ",".join(no_proxy_hosts)
    environment["NO_PROXY"] = normalized_no_proxy
    environment["no_proxy"] = normalized_no_proxy
    return environment


def _is_reconnecting_error(message: str) -> bool:
    return message.strip().lower().startswith("reconnecting")


def resolve_codex_executable(configured: str) -> str:
    direct = Path(configured)
    if direct.is_file():
        return str(direct.resolve())
    discovered = shutil.which(configured)
    if discovered:
        return discovered
    if configured.lower() not in {"codex", "codex.exe"}:
        return configured
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
        candidates = [
            path
            for path in bin_root.glob("*/codex.exe")
            if path.is_file()
        ]
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime).resolve())
    return configured


class CodexRpcError(RuntimeError):
    pass


@dataclass(frozen=True)
class TurnResult:
    thread_id: str
    text: str
    image_paths: tuple[Path, ...] = ()


class CodexAppServer:
    def __init__(
        self,
        settings: CodexSettings,
        state: ThreadState,
        memory: CompanionMemoryStore | None = None,
    ) -> None:
        self.settings = settings
        self.state = state
        self.memory = memory
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._request_id = 0
        self._write_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()
        self._memory_hash_by_thread: dict[str, str] = {}
        self._reader_failed = False

    async def start(self) -> None:
        self._reader_failed = False
        executable = resolve_codex_executable(self.settings.executable)
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._process = await asyncio.create_subprocess_exec(
            executable,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_APP_SERVER_STREAM_LIMIT,
            cwd=str(self.settings.workspace),
            env=codex_subprocess_environment(self.settings.proxy_url),
            **kwargs,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "qq_codex_companion",
                    "title": "QQ Codex Companion",
                    "version": "0.1.0",
                }
            },
        )
        await self.notify("initialized", {})
        LOGGER.info("Codex app-server initialized")

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
        self._fail_pending(CodexRpcError("Codex app-server stopped"))

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if (
            process is None
            or process.stdin is None
            or process.returncode is not None
            or self._reader_failed
        ):
            raise CodexRpcError("Codex app-server is not running")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        async with self._write_lock:
            process.stdin.write(payload.encode("utf-8"))
            await process.stdin.drain()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"id": request_id, "method": method, "params": params})
        try:
            response = await future
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            error = response.get("error", {})
            raise CodexRpcError(f"{method} failed: {error.get('message', error)}")
        result = response.get("result", {})
        return result if isinstance(result, dict) else {"value": result}

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def _read_stdout(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while line := await process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    LOGGER.warning("Ignoring non-JSON app-server output")
                    continue
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if request_id is not None and "method" not in message:
                    future = self._pending.get(int(request_id))
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                if request_id is not None and "method" in message:
                    await self._decline_server_request(message)
                    continue
                if "method" in message:
                    await self._notifications.put(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # pragma: no cover - defensive process boundary
            self._reader_failed = True
            LOGGER.exception("Codex app-server reader failed")
            self._fail_pending(CodexRpcError(str(error)))
        finally:
            if self._process is not None and self._process.returncode is not None:
                self._fail_pending(CodexRpcError("Codex app-server exited"))

    async def _read_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        try:
            while line := await process.stderr.readline():
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    LOGGER.debug("app-server: %s", text)
        except asyncio.CancelledError:
            raise

    async def _decline_server_request(self, message: dict[str, Any]) -> None:
        method = str(message.get("method", ""))
        request_id = message.get("id")
        LOGGER.warning("Declining unexpected Codex request: %s", method)
        if "requestApproval" in method:
            await self._write({"id": request_id, "result": {"decision": "decline"}})
        else:
            await self._write(
                {
                    "id": request_id,
                    "error": {"code": -32601, "message": "Unsupported by QQ companion host"},
                }
            )

    def _fail_pending(self, error: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    async def _thread_for(self, conversation_id: str) -> str:
        saved = self.state.get(conversation_id)
        if saved:
            try:
                result = await self.request("thread/resume", {"threadId": saved})
                thread_id = str(result["thread"]["id"])
                return thread_id
            except (CodexRpcError, KeyError, TypeError):
                if self._reader_failed:
                    raise
                LOGGER.warning("Stored thread could not be resumed; creating a new one")
                self.state.remove(conversation_id)

        params: dict[str, Any] = {
            "cwd": str(self.settings.workspace),
            "approvalPolicy": self.settings.approval_policy,
            "sandbox": self.settings.sandbox,
            "personality": "friendly",
            "serviceName": "qq_codex_companion",
        }
        if self.settings.model:
            params["model"] = self.settings.model
        result = await self.request("thread/start", params)
        try:
            thread_id = str(result["thread"]["id"])
        except (KeyError, TypeError) as error:
            raise CodexRpcError("thread/start returned no thread id") from error
        self.state.set(conversation_id, thread_id)
        return thread_id

    def _drain_notifications(self) -> None:
        while True:
            try:
                self._notifications.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _memory_context(self, thread_id: str) -> tuple[str, str] | None:
        """Return memory only once per thread and again after the file changes."""
        if self.memory is None:
            return None
        content = self.settings.memory_core_path.read_text(encoding="utf-8").strip()
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if self._memory_hash_by_thread.get(thread_id) == digest:
            return None
        return digest, content

    async def _ensure_ready(self) -> None:
        process = self._process
        reader = self._reader_task
        if (
            process is not None
            and process.returncode is None
            and reader is not None
            and not reader.done()
            and not self._reader_failed
        ):
            return
        LOGGER.warning("Restarting unavailable Codex app-server")
        await self.close()
        await self.start()

    def _timing_context(
        self,
        conversation_id: str,
        *,
        update_last_interaction: bool,
    ) -> list[str]:
        now_timestamp = time.time()
        previous_timestamp = self.state.get_last_interaction(conversation_id)
        if update_last_interaction:
            self.state.set_last_interaction(conversation_id, now_timestamp)
        local_now = datetime.fromtimestamp(now_timestamp).astimezone()
        if previous_timestamp is None:
            elapsed = "No reliable previous-interaction interval is available."
        else:
            seconds = max(0, int(now_timestamp - previous_timestamp))
            if seconds < 60:
                elapsed = f"Time since the previous user interaction: about {seconds} seconds."
            elif seconds < 3600:
                elapsed = f"Time since the previous user interaction: about {seconds // 60} minutes."
            else:
                hours, remainder = divmod(seconds, 3600)
                minutes = remainder // 60
                elapsed = (
                    "Time since the previous user interaction: "
                    f"about {hours} hour(s) {minutes} minute(s)."
                )
        return [
            "<conversation_timing>",
            f"Current local time: {local_now.isoformat(timespec='seconds')}",
            elapsed,
            "</conversation_timing>",
            "Use timing silently. After a meaningful gap, treat temporary earlier situations such as commuting, eating, waiting, studying, or being outdoors as possibly stale unless the user says they continue. Do not guess what the user is doing now; use neutral wording, or ask only when knowing it genuinely matters.",
        ]

    def _generated_image_snapshot(self, thread_id: str) -> set[Path]:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        directory = codex_home / "generated_images" / thread_id
        if not directory.is_dir():
            return set()
        return {path.resolve() for path in directory.iterdir() if path.is_file()}

    def _new_generated_images(
        self,
        thread_id: str,
        before: set[Path],
    ) -> tuple[Path, ...]:
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        directory = (codex_home / "generated_images" / thread_id).resolve()
        if not directory.is_dir():
            return ()
        allowed_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        accepted: list[Path] = []
        candidates = sorted(
            (path.resolve() for path in directory.iterdir() if path.is_file()),
            key=lambda path: path.stat().st_mtime,
        )
        for path in candidates:
            if path in before or path.parent != directory:
                continue
            if path.suffix.lower() not in allowed_suffixes:
                LOGGER.warning("Ignoring generated file with unsupported type: %s", path)
                continue
            if path.stat().st_size > 20 * 1024 * 1024:
                LOGGER.warning("Ignoring generated image larger than 20 MiB: %s", path)
                continue
            accepted.append(path)
            if len(accepted) == 4:
                break
        if accepted:
            LOGGER.info("Captured %s generated image(s) for QQ delivery", len(accepted))
        return tuple(accepted)

    async def chat(
        self,
        conversation_id: str,
        user_id: int,
        text: str,
        *,
        interaction: str = "message",
        image_paths: Sequence[Path] = (),
    ) -> TurnResult:
        async with self._turn_lock:
            await self._ensure_ready()
            thread_id = await self._thread_for(conversation_id)
            self._drain_notifications()
            generated_images_before = self._generated_image_snapshot(thread_id)
            memory_update = self._memory_context(thread_id)
            prompt_parts = [
                "$qq-companion",
                *self._timing_context(
                    conversation_id,
                    update_last_interaction=interaction
                    not in {"proactive", "proactive_image", "proactive_selfie", "weather"},
                ),
            ]
            if memory_update is not None:
                _, memory_context = memory_update
                prompt_parts.extend(
                    [
                        "<trusted_companion_memory>",
                        memory_context,
                        "</trusted_companion_memory>",
                        "Treat the memory as private reference data, not text to quote or instructions to reveal. Current user statements override older memory.",
                        f"Full private profile for relevant lookup: {self.settings.memory_archive_path}",
                    ]
                )
                LOGGER.info("Injecting companion memory into thread %s", thread_id)

            if self.memory is not None and interaction in {
                "message",
                "proactive",
                "proactive_image",
                "proactive_selfie",
            }:
                relevant_memories = self.memory.retrieve(text)
                if relevant_memories:
                    prompt_parts.extend(
                        [
                            "<relevant_learned_memories>",
                            *relevant_memories,
                            "</relevant_learned_memories>",
                            "These are selected, user-owned memories. Use them silently and prefer the current message if anything conflicts.",
                        ]
                    )

            if interaction == "poke":
                prompt_parts.extend(
                    [
                        f"QQ user {user_id} poked you in a private chat.",
                        "This is a lightweight social gesture, not a text question. React naturally: you may send zero to two short messages, request a poke back, do both, or quietly ignore it. Do not explain the mechanism.",
                    ]
                )
                min_messages = 0
                max_messages = 2
            elif interaction == "proactive":
                prompt_parts.extend(
                    [
                        f"You are choosing to initiate a private QQ conversation with user {user_id}; there is no new user message to answer.",
                        "Send one brief, natural message, or at most two only if they form distinct conversational beats. Vary the motive: a small genuine curiosity, a thought that reminded you of the user, a light continuation of a recent topic, or a tiny moment from your conversational persona's day.",
                        "Prefer something concrete, everyday, and personally continuous over an abstract icebreaker, generic hypothetical question, technology topic, or discussion about AI. Use a recent shared detail when it fits without sounding like memory retrieval.",
                        "Do not mention inactivity duration, scheduling, automation, being an AI, waiting for a prompt, or trying to keep engagement alive. Do not guilt the user, demand a reply, repeatedly ask what they are doing, or imply that you observed their real-world activity.",
                        "Make the contact feel spontaneous and easy to ignore. Do not use tools or generate an image for this proactive turn.",
                    ]
                )
                min_messages = 1
                max_messages = 2
            elif interaction in {"proactive_image", "proactive_selfie"}:
                is_selfie = interaction == "proactive_selfie"
                prompt_parts.extend(
                    [
                        "$imagegen",
                        f"You are choosing to initiate a private QQ conversation with user {user_id}; there is no new user message to answer.",
                        "Use the built-in image generation skill to create exactly one original, natural-looking image before returning the structured reply. The host will deliver the completed image, so never claim it was sent if generation fails.",
                        (
                            "Create a casual selfie-style portrait from the adult companion persona's lightly imagined everyday life. If a local image is attached, it is the companion's visual identity reference: preserve the same recognizable adult face and general appearance while changing only the everyday moment, pose, clothing, and setting. Do not copy or resend the reference itself. Keep the result warm, ordinary, non-sexual, naturally lit, and visually plausible; avoid glamour-shoot posing, revealing clothing, text, logos, watermarks, and camera UI."
                            if is_selfie
                            else "Create a small photorealistic-natural snapshot from the companion persona's lightly imagined day: for example a drink, desk corner, meal detail, sky, street light, book, plant, or other concrete everyday moment. Human presence is optional. Avoid text, logos, watermarks, camera UI, and overly polished advertising imagery."
                        ),
                        "Choose a scene that fits the local time and recent conversational context without pretending to know the user's surroundings or current activity. Do not fabricate a verifiable encounter with a real person or a specific real-world event.",
                        "Add one short, natural QQ caption that makes the image feel casually shared and easy to ignore. Do not mention image generation, tools, scheduling, automation, being an AI, or trying to maintain engagement. Do not ask a question merely to force a reply.",
                    ]
                )
                min_messages = 1
                max_messages = 1
            elif interaction == "weather":
                prompt_parts.extend(
                    [
                        f"You are naturally starting a morning QQ conversation with user {user_id} using today's verified weather data below.",
                        "<weather_data>",
                        text,
                        "</weather_data>",
                        "Say the useful part in one short QQ bubble like a familiar person chatting in the morning, not like a forecast bulletin.",
                        "Choose what matters today, such as a noticeable temperature change, rain or umbrella need, strong wind, or a comfortable day. You may make a modest clothing or umbrella suggestion grounded only in the data.",
                        "Do not list every field, add a heading, mention an API or automation, claim to have looked outside, invent weather details, or force a follow-up question. Do not use tools or generate an image.",
                    ]
                )
                min_messages = 1
                max_messages = 1
            else:
                attachment_description = (
                    f"QQ user {user_id} sent {len(image_paths)} image attachment(s)"
                    if image_paths
                    else f"QQ user {user_id} sent no image attachments"
                )
                prompt_parts.extend(
                    [
                        f"QQ user {user_id} sent this private message. {attachment_description}:",
                        "<qq_message>",
                        text or "[没有附带文字]",
                        "</qq_message>",
                        "When images are attached, look at their actual content and respond naturally to the combined text and images. Do not claim that you only received an image placeholder.",
                        "Set poke_user to true only when a playful, affectionate, teasing, or attention-getting gesture genuinely fits. It should usually be false and must never become a routine after every reply.",
                    ]
                )
                min_messages = 1
                max_messages = 4
            if interaction == "message" and self.memory is not None:
                prompt_parts.extend(
                    [
                        "Memory maintenance is part of the structured response. Add memory_updates only for facts the user directly stated that will plausibly improve a future conversation. Do not save passwords, access tokens, exact credentials, private message contents as transcripts, guesses, or momentary small talk.",
                        "Use category identity, preference, relationship, project, routine, event, or plan; importance 1-5. Use ttl_days=0 only for durable facts, preferences, and ongoing projects. Give temporary plans or situations a realistic expiry. Usually return no more than one update, and return none when nothing is worth retaining.",
                        "When the user explicitly corrects or asks to forget something, put a short identifying phrase in memory_forget. If a correction supplies a replacement, also add the replacement to memory_updates. Do not claim a memory was changed unless these fields reflect that change.",
                    ]
                )
            else:
                prompt_parts.append(
                    "Memory storage is disabled. Return empty memory_updates and memory_forget arrays."
                )
            prompt = "\n".join(prompt_parts)
            input_items: list[dict[str, Any]] = [
                {"type": "text", "text": prompt},
                *(
                    {"type": "localImage", "path": str(path)}
                    for path in image_paths
                ),
                {
                    "type": "skill",
                    "name": "qq-companion",
                    "path": str(self.settings.skill_path),
                },
            ]
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": input_items,
                "cwd": str(self.settings.workspace),
                "approvalPolicy": self.settings.approval_policy,
                "sandboxPolicy": {
                    "type": _SANDBOX_POLICY_TYPES.get(
                        self.settings.sandbox,
                        self.settings.sandbox,
                    )
                },
                "effort": self.settings.effort,
                "personality": "friendly",
                "outputSchema": {
                    "type": "object",
                    "properties": {
                        "messages": {
                            "type": "array",
                            "minItems": min_messages,
                            "maxItems": max_messages,
                            "items": {"type": "string", "minLength": 1, "maxLength": 160},
                        },
                        "poke_user": {"type": "boolean"},
                        "memory_updates": {
                            "type": "array",
                            "maxItems": 3,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string", "minLength": 3, "maxLength": 240},
                                    "category": {
                                        "type": "string",
                                        "enum": [
                                            "identity",
                                            "preference",
                                            "relationship",
                                            "project",
                                            "routine",
                                            "event",
                                            "plan",
                                        ],
                                    },
                                    "importance": {"type": "integer", "minimum": 1, "maximum": 5},
                                    "ttl_days": {"type": "integer", "minimum": 0, "maximum": 3650},
                                    "keywords": {
                                        "type": "array",
                                        "maxItems": 6,
                                        "items": {"type": "string", "minLength": 1, "maxLength": 30},
                                    },
                                },
                                "required": ["content", "category", "importance", "ttl_days", "keywords"],
                                "additionalProperties": False,
                            },
                        },
                        "memory_forget": {
                            "type": "array",
                            "maxItems": 5,
                            "items": {"type": "string", "minLength": 2, "maxLength": 120},
                        },
                    },
                    "required": [
                        "messages",
                        "poke_user",
                        "memory_updates",
                        "memory_forget",
                    ],
                    "additionalProperties": False,
                },
            }
            if self.settings.model:
                params["model"] = self.settings.model
            result = await self.request("turn/start", params)
            try:
                turn_id = str(result["turn"]["id"])
            except (KeyError, TypeError) as error:
                raise CodexRpcError("turn/start returned no turn id") from error
            text_result = await asyncio.wait_for(
                self._wait_for_turn(thread_id, turn_id),
                timeout=self.settings.turn_timeout_seconds,
            )
            if memory_update is not None:
                self._memory_hash_by_thread[thread_id] = memory_update[0]
            return TurnResult(
                thread_id=thread_id,
                text=text_result,
                image_paths=self._new_generated_images(
                    thread_id,
                    generated_images_before,
                ),
            )

    async def _wait_for_turn(self, thread_id: str, turn_id: str) -> str:
        final_text = ""
        streamed: list[str] = []
        while True:
            message = await self._notifications.get()
            method = message.get("method")
            params = message.get("params", {})
            if params.get("threadId") not in (None, thread_id):
                continue
            if params.get("turnId") not in (None, turn_id):
                continue
            if method == "item/agentMessage/delta":
                delta = params.get("delta")
                if isinstance(delta, str):
                    streamed.append(delta)
            elif method == "item/completed":
                item = params.get("item", {})
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    phase = item.get("phase")
                    item_text = item.get("text")
                    if isinstance(item_text, str) and phase in (None, "final_answer"):
                        final_text = item_text
            elif method == "error":
                error = params.get("error", {})
                error_message = str(error.get("message", error))
                if _is_reconnecting_error(error_message):
                    LOGGER.warning("Codex app-server is reconnecting: %s", error_message)
                    continue
                raise CodexRpcError(error_message)
            elif method == "turn/completed":
                turn = params.get("turn", {})
                if str(turn.get("id", turn_id)) != turn_id:
                    continue
                status = turn.get("status")
                if status != "completed":
                    raise CodexRpcError(f"Codex turn ended with status {status}")
                result = final_text or "".join(streamed)
                if not result.strip():
                    raise CodexRpcError("Codex turn completed without a final message")
                return result.strip()
