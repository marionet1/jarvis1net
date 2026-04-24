"""Natural-language phrases shared by CLI and Telegram (no slash commands)."""

from __future__ import annotations

# Phrases that clear in-memory chat history for the current session / chat.
CLEAR_HISTORY_PHRASES = frozenset(
    {
        "clear history",
        "clear chat history",
        "reset chat",
        "start over",
        "clear conversation",
    }
)
