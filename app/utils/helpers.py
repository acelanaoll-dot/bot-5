from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> datetime:
    """
    Возвращает текущее время UTC.
    """

    return datetime.now(timezone.utc)


def generate_uuid() -> str:
    """
    Генерирует UUID4.
    """

    return str(uuid4())


def normalize_username(
    username: str | None,
) -> str | None:
    """
    Нормализует Telegram username.
    """

    if not username:
        return None

    return username.strip().lstrip("@") or None


def truncate_text(
    text: str,
    max_length: int,
) -> str:
    """
    Безопасно сокращает текст.
    """

    if len(text) <= max_length:
        return text

    if max_length <= 3:
        return text[:max_length]

    return (
        text[: max_length - 3]
        + "..."
    )


__all__ = [
    "utc_now",
    "generate_uuid",
    "normalize_username",
    "truncate_text",
]