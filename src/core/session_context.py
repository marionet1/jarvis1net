"""
Session memory: latest conversation turns (user / assistant) per Telegram chat or CLI session key.
Stored in a single JSON file; context is derived from normal message history.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_MAX_CHAT_PAIRS = 12
_MAX_USER_MSG_CHARS = 4000
_MAX_ASSISTANT_MSG_CHARS = 6000
_MAX_CHAT_TOTAL_CHARS = 14000


def _clip_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n...[message truncated]"


class ChatHistory:
    """Latest turns in order: user -> assistant (only user-visible conversation)."""

    __slots__ = ("_messages",)

    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = []

    def to_serializable(self) -> list[dict[str, str]]:
        return list(self._messages)

    def load_from(self, raw: Any) -> None:
        self._messages = []
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role not in ("user", "assistant") or not isinstance(content, str):
                continue
            if not content.strip():
                continue
            self._messages.append({"role": role, "content": content})

    def as_openai_messages(self) -> list[dict[str, str]]:
        return [dict(m) for m in self._messages]

    def _total_chars(self) -> int:
        return sum(len(m.get("content") or "") for m in self._messages)

    def _trim(self) -> None:
        while len(self._messages) > _MAX_CHAT_PAIRS * 2:
            self._messages.pop(0)
            self._messages.pop(0)
        while self._messages and self._total_chars() > _MAX_CHAT_TOTAL_CHARS and len(self._messages) >= 2:
            self._messages.pop(0)
            self._messages.pop(0)

    def append_turn(self, user_text: str, assistant_text: str) -> None:
        u = _clip_text(user_text, _MAX_USER_MSG_CHARS)
        a = (
            _clip_text(assistant_text, _MAX_ASSISTANT_MSG_CHARS)
            if assistant_text.strip()
            else "(short model reply with no content)"
        )
        self._messages.append({"role": "user", "content": u})
        self._messages.append({"role": "assistant", "content": a})
        self._trim()


class SessionState:
    __slots__ = ("chat",)

    def __init__(self) -> None:
        self.chat = ChatHistory()

    def to_serializable(self) -> dict[str, Any]:
        return {"messages": self.chat.to_serializable()}

    def load_from(self, data: dict[str, Any]) -> None:
        self.chat.load_from(data.get("messages"))


class SessionStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self._by_key: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.file_path.is_file():
            return
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            blob = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(blob, dict):
            return
        with self._lock:
            for key, val in blob.items():
                if not isinstance(key, str) or not isinstance(val, dict):
                    continue
                st = SessionState()
                st.load_from(val)
                self._by_key[key] = st

    def save(self) -> None:
        with self._lock:
            out: dict[str, Any] = {k: v.to_serializable() for k, v in self._by_key.items()}
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        tmp = self.file_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(out, ensure_ascii=False, indent=0), encoding="utf-8")
            tmp.replace(self.file_path)
        except OSError:
            try:
                if tmp.is_file():
                    tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def session(self, session_key: str) -> SessionState:
        with self._lock:
            if session_key not in self._by_key:
                self._by_key[session_key] = SessionState()
            return self._by_key[session_key]

    def clear_key(self, session_key: str) -> None:
        with self._lock:
            self._by_key.pop(session_key, None)


_store: SessionStore | None = None
_store_resolved: str | None = None


def get_session_store(file_path: str) -> SessionStore:
    global _store, _store_resolved
    resolved = str(Path(file_path).expanduser().resolve())
    if _store is None or _store_resolved != resolved:
        _store = SessionStore(Path(resolved))
        _store_resolved = resolved
    return _store
