from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.config import settings


_USERNAME_RE = re.compile(
    r"^[A-Za-z0-9_]{1,32}$"
)

_SLUG_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
)


class ValidatedText(str):
    """Строка, прошедшая проверку длины."""

    @property
    def is_valid(self) -> bool:
        """Совместимость с текущими handlers."""

        return True


def validate_text(
    value: str,
    *,
    field_name: str = "значение",
    min_length: int = 1,
    max_length: int | None = None,
) -> str:
    """Проверяет обычный текстовый ввод."""

    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} должен быть строкой."
        )

    if isinstance(min_length, bool) or not isinstance(
        min_length,
        int,
    ):
        raise TypeError(
            "min_length должен быть целым числом."
        )

    if max_length is not None and (
        isinstance(max_length, bool)
        or not isinstance(max_length, int)
    ):
        raise TypeError(
            "max_length должен быть целым числом."
        )

    if min_length < 0:
        raise ValueError(
            "min_length не может быть отрицательным."
        )

    value = value.strip()

    limit = (
        max_length
        if max_length is not None
        else settings.security_max_text_length
    )

    if limit < min_length:
        raise ValueError(
            f"Максимальная длина {field_name} "
            "не может быть меньше минимальной."
        )

    if len(value) < min_length:
        raise ValueError(
            f"{field_name} слишком короткий."
        )

    if len(value) > limit:
        raise ValueError(
            f"{field_name} слишком длинный."
        )

    return value


def validate_text_length(
    value: str,
    min_length: int = 1,
    max_length: int | None = None,
    *,
    field_name: str = "значение",
) -> ValidatedText:
    """
    Проверяет длину текста.

    Возвращает строку с атрибутом is_valid.
    При ошибке выбрасывает исключение.
    """

    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} должен быть строкой."
        )

    if isinstance(min_length, bool) or not isinstance(
        min_length,
        int,
    ):
        raise TypeError(
            "min_length должен быть целым числом."
        )

    if max_length is not None and (
        isinstance(max_length, bool)
        or not isinstance(max_length, int)
    ):
        raise TypeError(
            "max_length должен быть целым числом."
        )

    if min_length < 0:
        raise ValueError(
            "min_length не может быть отрицательным."
        )

    value = value.strip()

    limit = (
        max_length
        if max_length is not None
        else settings.security_max_text_length
    )

    if limit < min_length:
        raise ValueError(
            f"Максимальная длина {field_name} "
            "не может быть меньше минимальной."
        )

    if len(value) < min_length:
        raise ValueError(
            f"{field_name} слишком короткий. "
            f"Минимум: {min_length} символов."
        )

    if len(value) > limit:
        raise ValueError(
            f"{field_name} слишком длинный. "
            f"Максимум: {limit} символов."
        )

    return ValidatedText(value)


def validate_username(
    username: str,
) -> str:
    """Проверяет Telegram username без @."""

    if not isinstance(username, str):
        raise TypeError(
            "Username должен быть строкой."
        )

    username = username.strip().lstrip("@")

    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(
            "Некорректный Telegram username."
        )

    return username


def validate_slug(
    slug: str,
) -> str:
    """Проверяет slug."""

    if not isinstance(slug, str):
        raise TypeError(
            "Slug должен быть строкой."
        )

    slug = slug.strip().lower()

    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            "Некорректный slug."
        )

    return slug


def validate_positive_decimal(
    value: str | Decimal,
    *,
    field_name: str = "сумма",
) -> Decimal:
    """Проверяет положительное число."""

    try:
        number = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{field_name} должно быть числом."
        ) from exc

    if not number.is_finite():
        raise ValueError(
            f"{field_name} должно быть конечным числом."
        )

    if number <= 0:
        raise ValueError(
            f"{field_name} должно быть больше нуля."
        )

    return number


def validate_non_negative_decimal(
    value: str | Decimal,
    *,
    field_name: str = "сумма",
) -> Decimal:
    """Проверяет число >= 0."""

    try:
        number = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{field_name} должно быть числом."
        ) from exc

    if not number.is_finite():
        raise ValueError(
            f"{field_name} должно быть конечным числом."
        )

    if number < 0:
        raise ValueError(
            f"{field_name} не может быть отрицательным."
        )

    return number


def validate_telegram_id(
    telegram_id: int,
) -> int:
    """Проверяет Telegram ID."""

    if isinstance(telegram_id, bool) or not isinstance(
        telegram_id,
        int,
    ):
        raise ValueError(
            "Telegram ID должен быть целым числом."
        )

    if telegram_id <= 0:
        raise ValueError(
            "Некорректный Telegram ID."
        )

    return telegram_id


__all__ = [
    "ValidatedText",
    "validate_text",
    "validate_text_length",
    "validate_username",
    "validate_slug",
    "validate_positive_decimal",
    "validate_non_negative_decimal",
    "validate_telegram_id",
]