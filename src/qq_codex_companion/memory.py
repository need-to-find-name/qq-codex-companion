from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .config import MemorySettings


_CATEGORIES = {
    "identity",
    "preference",
    "relationship",
    "project",
    "routine",
    "event",
    "plan",
}
_SPACE = re.compile(r"\s+")
_KEEP = re.compile(r"[^0-9a-z\u4e00-\u9fff+#.]+")
_ASCII_WORD = re.compile(r"[a-z0-9_+#.]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_SENSITIVE = re.compile(
    r"(?:密码|口令|访问令牌|access[ _-]?token|api[ _-]?key|secret[ _-]?key|bearer|onebot.{0,12}token)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    category: str
    importance: int
    ttl_days: int
    keywords: tuple[str, ...] = ()


def _normalized(text: str) -> str:
    return _KEEP.sub("", _SPACE.sub("", text.lower()))


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    terms = set(_ASCII_WORD.findall(lowered))
    for run in _CJK_RUN.findall(lowered):
        if len(run) <= 3:
            terms.add(run)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
        terms.update(run[index : index + 3] for index in range(len(run) - 2))
    return terms


class CompanionMemoryStore:
    def __init__(self, settings: MemorySettings) -> None:
        self.settings = settings
        self._lock = RLock()
        self._entries: list[dict[str, Any]] = []
        self._history: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.settings.store_file.is_file():
            return
        try:
            payload = json.loads(self.settings.store_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        entries = payload.get("entries", [])
        history = payload.get("history", [])
        if isinstance(entries, list):
            self._entries = [entry for entry in entries if isinstance(entry, dict)]
        if isinstance(history, list):
            self._history = [item for item in history if isinstance(item, dict)][-500:]

    def _save(self) -> None:
        payload = {
            "version": 1,
            "entries": self._entries,
            "history": self._history[-500:],
        }
        temporary = self.settings.store_file.with_suffix(
            self.settings.store_file.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.settings.store_file)

    @staticmethod
    def _valid_candidate(candidate: MemoryCandidate) -> MemoryCandidate | None:
        content = _SPACE.sub(" ", candidate.content).strip()[:240]
        category = candidate.category if candidate.category in _CATEGORIES else "event"
        importance = max(1, min(5, int(candidate.importance)))
        ttl_days = max(0, min(3650, int(candidate.ttl_days)))
        keywords = tuple(
            dict.fromkeys(
                _SPACE.sub(" ", str(item)).strip()[:30]
                for item in candidate.keywords[:6]
                if _SPACE.sub(" ", str(item)).strip()
            )
        )
        if len(content) < 3:
            return None
        if _SENSITIVE.search(" ".join((content, *keywords))):
            return None
        return MemoryCandidate(content, category, importance, ttl_days, keywords)

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        a = _normalized(left)
        b = _normalized(right)
        if not a or not b:
            return 0.0
        if a in b or b in a:
            return min(len(a), len(b)) / max(len(a), len(b)) + 0.25
        return SequenceMatcher(None, a, b).ratio()

    def _purge_expired(self, now: float) -> int:
        before = len(self._entries)
        self._entries = [
            entry
            for entry in self._entries
            if entry.get("expires_at") is None
            or float(entry.get("expires_at", 0)) > now
        ]
        return before - len(self._entries)

    def apply(
        self,
        updates: Iterable[MemoryCandidate],
        forget: Iterable[str],
        *,
        now: float | None = None,
    ) -> tuple[int, int]:
        if not self.settings.enabled:
            return 0, 0
        current = time.time() if now is None else now
        added_or_updated = 0
        removed = 0
        with self._lock:
            removed += self._purge_expired(current)
            for raw_term in forget:
                term = _SPACE.sub(" ", str(raw_term)).strip()[:120]
                if len(_normalized(term)) < 2:
                    continue
                kept: list[dict[str, Any]] = []
                for entry in self._entries:
                    searchable = " ".join(
                        [str(entry.get("content", "")), *entry.get("keywords", [])]
                    )
                    if self._similarity(term, searchable) >= 0.42:
                        removed += 1
                        self._history.append(
                            {"at": current, "operation": "forget", "entry": entry.copy()}
                        )
                    else:
                        kept.append(entry)
                self._entries = kept

            for raw_candidate in updates:
                candidate = self._valid_candidate(raw_candidate)
                if candidate is None:
                    continue
                duplicate: dict[str, Any] | None = None
                for entry in self._entries:
                    if entry.get("category") != candidate.category:
                        continue
                    if self._similarity(candidate.content, str(entry.get("content", ""))) >= 0.78:
                        duplicate = entry
                        break
                expires_at = (
                    current + candidate.ttl_days * 24 * 60 * 60
                    if candidate.ttl_days
                    else None
                )
                if duplicate is None:
                    entry = {
                        "id": uuid.uuid4().hex,
                        "content": candidate.content,
                        "category": candidate.category,
                        "importance": candidate.importance,
                        "keywords": list(candidate.keywords),
                        "created_at": current,
                        "updated_at": current,
                        "expires_at": expires_at,
                    }
                    self._entries.append(entry)
                    self._history.append(
                        {"at": current, "operation": "add", "entry": entry.copy()}
                    )
                else:
                    previous = duplicate.copy()
                    duplicate.update(
                        {
                            "content": candidate.content,
                            "importance": max(
                                int(duplicate.get("importance", 1)),
                                candidate.importance,
                            ),
                            "keywords": list(
                                dict.fromkeys(
                                    [*duplicate.get("keywords", []), *candidate.keywords]
                                )
                            )[:6],
                            "updated_at": current,
                            "expires_at": expires_at,
                        }
                    )
                    self._history.append(
                        {
                            "at": current,
                            "operation": "update",
                            "before": previous,
                            "entry": duplicate.copy(),
                        }
                    )
                added_or_updated += 1

            if len(self._entries) > self.settings.max_entries:
                self._entries.sort(
                    key=lambda entry: (
                        int(entry.get("importance", 1)),
                        float(entry.get("updated_at", 0)),
                    ),
                    reverse=True,
                )
                evicted = self._entries[self.settings.max_entries :]
                self._entries = self._entries[: self.settings.max_entries]
                removed += len(evicted)
                for entry in evicted:
                    self._history.append(
                        {"at": current, "operation": "evict", "entry": entry.copy()}
                    )
            if added_or_updated or removed:
                self._save()
        return added_or_updated, removed

    def retrieve(self, query: str, *, now: float | None = None) -> tuple[str, ...]:
        if not self.settings.enabled:
            return ()
        current = time.time() if now is None else now
        with self._lock:
            if self._purge_expired(current):
                self._save()
            query_normalized = _normalized(query)
            query_terms = _terms(query)
            generic_memory_query = any(
                phrase in query
                for phrase in ("记得我", "记住了什么", "我的记忆", "关于我的", "你记得")
            )
            ranked: list[tuple[float, dict[str, Any]]] = []
            for entry in self._entries:
                content = str(entry.get("content", ""))
                searchable = " ".join([content, *entry.get("keywords", [])])
                normalized = _normalized(searchable)
                entry_terms = _terms(searchable)
                overlap = len(query_terms & entry_terms) / max(1, len(query_terms))
                direct = 1.0 if query_normalized and query_normalized in normalized else 0.0
                age_days = max(0.0, current - float(entry.get("updated_at", current))) / 86400
                recency = math.exp(-age_days / 60)
                importance = int(entry.get("importance", 1)) / 5
                relevance = direct * 4 + overlap * 3
                if not generic_memory_query and query and relevance < 0.12:
                    continue
                score = relevance + importance * 0.65 + recency * 0.35
                ranked.append((score, entry))
            ranked.sort(
                key=lambda pair: (pair[0], float(pair[1].get("updated_at", 0))),
                reverse=True,
            )

            selected: list[str] = []
            used = 0
            for _, entry in ranked:
                date = time.strftime(
                    "%Y-%m-%d",
                    time.localtime(float(entry.get("updated_at", current))),
                )
                line = f"- [{entry.get('category', 'event')}, {date}] {entry.get('content', '')}"
                if used + len(line) > self.settings.max_prompt_chars:
                    continue
                selected.append(line)
                used += len(line)
                if len(selected) >= self.settings.max_retrieved:
                    break
            return tuple(selected)

    def count(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        with self._lock:
            if self._purge_expired(current):
                self._save()
            return len(self._entries)
