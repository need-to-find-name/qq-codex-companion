from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .memory import MemoryCandidate


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[。！？!?…；;])")


@dataclass(frozen=True)
class ReplyPlan:
    messages: list[str]
    poke_user: bool = False
    memory_updates: tuple[MemoryCandidate, ...] = ()
    memory_forget: tuple[str, ...] = ()


def _decode_plan(text: str) -> ReplyPlan | None:
    candidate = _FENCE.sub("", text.strip()).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("messages"), list):
        return None
    messages = [str(item).strip() for item in value["messages"] if str(item).strip()]
    updates: list[MemoryCandidate] = []
    raw_updates = value.get("memory_updates", [])
    if isinstance(raw_updates, list):
        for item in raw_updates[:3]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            keywords = item.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            if content:
                updates.append(
                    MemoryCandidate(
                        content=content,
                        category=str(item.get("category", "event")),
                        importance=int(item.get("importance", 1)),
                        ttl_days=int(item.get("ttl_days", 0)),
                        keywords=tuple(str(keyword) for keyword in keywords[:6]),
                    )
                )
    raw_forget = value.get("memory_forget", [])
    forget = (
        tuple(str(item).strip() for item in raw_forget[:5] if str(item).strip())
        if isinstance(raw_forget, list)
        else ()
    )
    return ReplyPlan(
        messages=messages,
        poke_user=value.get("poke_user") is True,
        memory_updates=tuple(updates),
        memory_forget=forget,
    )


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    if len(sentences) <= 1:
        return [text[index : index + limit] for index in range(0, len(text), limit)]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(
                sentence[index : index + limit]
                for index in range(0, len(sentence), limit)
            )
        elif not current:
            current = sentence
        elif len(current) + len(sentence) <= limit:
            current += sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def parse_reply_plan(
    text: str,
    *,
    max_parts: int,
    max_chars: int,
    allow_empty: bool = False,
) -> ReplyPlan:
    decoded = _decode_plan(text)
    messages = decoded.messages if decoded is not None else []
    poke_user = decoded.poke_user if decoded is not None else False
    memory_updates = decoded.memory_updates if decoded is not None else ()
    memory_forget = decoded.memory_forget if decoded is not None else ()
    if not messages and decoded is None:
        messages = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if not messages:
        if allow_empty:
            return ReplyPlan(
                messages=[],
                poke_user=poke_user,
                memory_updates=memory_updates,
                memory_forget=memory_forget,
            )
        return ReplyPlan(messages=["我刚才没组织好语言，你再和我说一次好吗？"])

    expanded: list[str] = []
    for message in messages:
        expanded.extend(_split_long(message, max_chars))

    if len(expanded) <= max_parts:
        return ReplyPlan(
            messages=expanded,
            poke_user=poke_user,
            memory_updates=memory_updates,
            memory_forget=memory_forget,
        )

    head = expanded[: max_parts - 1]
    tail = "".join(expanded[max_parts - 1 :])
    return ReplyPlan(
        messages=head + [tail],
        poke_user=poke_user,
        memory_updates=memory_updates,
        memory_forget=memory_forget,
    )


def parse_reply(text: str, *, max_parts: int, max_chars: int) -> list[str]:
    return parse_reply_plan(text, max_parts=max_parts, max_chars=max_chars).messages
