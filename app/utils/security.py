from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_token(
    length: int = 32,
) -> str:
    """
    Генерирует криптографически безопасный токен.
    """

    if length < 16:
        raise ValueError(
            "Длина токена должна быть не меньше 16."
        )

    return secrets.token_urlsafe(length)


def generate_idempotency_key(
    prefix: str,
) -> str:
    """
    Создаёт уникальный ключ идемпотентности.
    """

    safe_prefix = (
        prefix.strip()
        .lower()
        .replace(" ", "_")
    )

    return (
        f"{safe_prefix}_"
        f"{secrets.token_hex(16)}"
    )


def sha256(
    value: str,
) -> str:
    """
    SHA-256 для некритичных идентификаторов/контрольных значений.
    """

    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def verify_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """
    Проверяет HMAC-SHA256 подпись.
    """

    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected,
        signature,
    )


__all__ = [
    "generate_token",
    "generate_idempotency_key",
    "sha256",
    "verify_signature",
]