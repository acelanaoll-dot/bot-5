from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import ShopSetting


DEFAULT_LANGUAGE = "ru"
SUPPORTED_LANGUAGES = frozenset({"ru", "en"})

TEXT_OVERRIDE_PREFIX = "text_override:"


class I18nError(Exception):
    """Базовая ошибка локализации."""


def normalize_language(language: str | None) -> str:
    """
    Нормализует Telegram language_code.

    Например:
        ru-RU -> ru
        en-US -> en
        None  -> ru
    """

    if not language:
        return DEFAULT_LANGUAGE

    normalized = language.strip().lower().replace("_", "-")
    base_language = normalized.split("-", 1)[0]

    if base_language in SUPPORTED_LANGUAGES:
        return base_language

    return DEFAULT_LANGUAGE


def _locales_directory() -> Path:
    """Возвращает абсолютный путь к каталогу локализаций."""

    path = Path(settings.localization_dir)

    if not path.is_absolute():
        path = Path.cwd() / path

    return path


def _flatten(
    data: dict[str, Any],
    prefix: str = "",
) -> dict[str, str]:
    """
    Преобразует вложенный JSON в flat-словарь.

    Было:

        {
            "menu": {
                "welcome": "Добро пожаловать"
            }
        }

    Станет:

        {
            "menu.welcome": "Добро пожаловать"
        }
    """

    result: dict[str, str] = {}

    for key, value in data.items():
        full_key = (
            f"{prefix}.{key}"
            if prefix
            else key
        )

        if isinstance(value, dict):
            result.update(
                _flatten(
                    value,
                    full_key,
                )
            )
            continue

        if isinstance(value, str):
            result[full_key] = value
            continue

        logger.warning(
            "Пропущено значение локализации {}: "
            "ожидалась строка, получен {}",
            full_key,
            type(value).__name__,
        )

    return result


@lru_cache(maxsize=len(SUPPORTED_LANGUAGES))
def _load_language(
    language: str,
) -> dict[str, str]:
    """
    Загружает JSON локализации и преобразует его
    в flat-словарь.
    """

    language = normalize_language(language)

    path = (
        _locales_directory()
        / f"{language}.json"
    )

    if not path.is_file():
        logger.error(
            "Файл локализации не найден: {}",
            path,
        )

        if language != DEFAULT_LANGUAGE:
            return _load_language(
                DEFAULT_LANGUAGE,
            )

        raise I18nError(
            f"Файл локализации не найден: {path}"
        )

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

    except json.JSONDecodeError as exc:
        logger.exception(
            "Некорректный JSON локализации: {}",
            path,
        )

        raise I18nError(
            f"Некорректный JSON локализации: {path}"
        ) from exc

    except OSError as exc:
        logger.exception(
            "Не удалось прочитать локализацию: {}",
            path,
        )

        raise I18nError(
            f"Не удалось прочитать локализацию: {path}"
        ) from exc

    if not isinstance(data, dict):
        raise I18nError(
            f"Корень локализации должен быть объектом: {path}"
        )

    return _flatten(data)


def _get_json_translation(
    key: str,
    language: str,
) -> str | None:
    """Получает перевод из JSON с fallback на русский."""

    translations = _load_language(language)

    value = translations.get(key)

    if value is not None:
        return value

    if language != DEFAULT_LANGUAGE:
        default_translations = _load_language(
            DEFAULT_LANGUAGE,
        )

        return default_translations.get(key)

    return None


def _override_key(
    key: str,
    language: str,
) -> str:
    """Формирует ключ override в ShopSetting."""

    return (
        f"{TEXT_OVERRIDE_PREFIX}"
        f"{language}:"
        f"{key}"
    )


async def get_override(
    session: AsyncSession,
    key: str,
    language: str | None = None,
) -> str | None:
    """
    Получает override текста из БД.

    Формат ключа:

        text_override:ru:menu.welcome
    """

    if not key:
        raise ValueError(
            "Ключ локализации не может быть пустым"
        )

    lang = normalize_language(language)

    setting_key = _override_key(
        key,
        lang,
    )

    result = await session.execute(
        select(ShopSetting.value).where(
            ShopSetting.key == setting_key,
        )
    )

    value = result.scalar_one_or_none()

    if value is None:
        return None

    value = value.strip()

    return value or None


async def at(
    key: str,
    language: str | None = None,
    *,
    session: AsyncSession,
    **kwargs: Any,
) -> str:
    """
    Асинхронно получает перевод.

    Приоритет:

        1. override из БД;
        2. JSON выбранного языка;
        3. JSON русского языка;
        4. сам ключ.

    Пример:

        text = await at(
            "menu.welcome",
            "ru",
            session=session,
            name=user.first_name,
        )
    """

    if not key:
        raise ValueError(
            "Ключ локализации не может быть пустым"
        )

    language_code = normalize_language(language)

    # 1. Проверяем override выбранного языка.
    override = await get_override(
        session=session,
        key=key,
        language=language_code,
    )

    # 2. Если override нет, пробуем JSON.
    if override is None:
        value = _get_json_translation(
            key,
            language_code,
        )
    else:
        value = override

    # 3. Если вообще ничего нет — возвращаем ключ.
    if value is None:
        logger.warning(
            "Ключ локализации не найден: {} ({})",
            key,
            language_code,
        )

        value = key

    if not kwargs:
        return value

    try:
        return value.format(**kwargs)

    except (
        KeyError,
        IndexError,
        ValueError,
    ):
        logger.exception(
            "Ошибка форматирования локализации: {} ({})",
            key,
            language_code,
        )

        return value


def t(
    key: str,
    language: str | None = None,
    **kwargs: Any,
) -> str:
    """
    Синхронный быстрый перевод.

    Использует JSON-кеш.

    Важно:
    override из БД здесь НЕ читается.

    Для текста, который должен учитывать изменения
    администратора в БД, использовать:

        await at(...)
    """

    if not key:
        raise ValueError(
            "Ключ локализации не может быть пустым"
        )

    language_code = normalize_language(language)

    value = _get_json_translation(
        key,
        language_code,
    )

    if value is None:
        logger.warning(
            "Ключ локализации не найден: {} ({})",
            key,
            language_code,
        )

        value = key

    if not kwargs:
        return value

    try:
        return value.format(**kwargs)

    except (
        KeyError,
        IndexError,
        ValueError,
    ):
        logger.exception(
            "Ошибка форматирования локализации: {} ({})",
            key,
            language_code,
        )

        return value


async def set_override(
    session: AsyncSession,
    key: str,
    language: str,
    value: str,
) -> ShopSetting:
    """
    Создаёт или обновляет override текста.

    Админская панель будет использовать этот метод.

    Пример ключа:

        menu.welcome

    В БД будет:

        text_override:ru:menu.welcome
    """

    if not key:
        raise ValueError(
            "Ключ локализации не может быть пустым"
        )

    if not value or not value.strip():
        raise ValueError(
            "Текст override не может быть пустым"
        )

    lang = normalize_language(language)

    setting_key = _override_key(
        key,
        lang,
    )

    result = await session.execute(
        select(ShopSetting).where(
            ShopSetting.key == setting_key,
        )
    )

    setting = result.scalar_one_or_none()

    if setting is None:
        setting = ShopSetting(
            key=setting_key,
            value=value.strip(),
            value_type="string",
            description=(
                f"Переопределение текста "
                f"локализации: {lang}:{key}"
            ),
            is_public=False,
        )

        session.add(setting)

    else:
        setting.value = value.strip()
        setting.value_type = "string"

    await session.flush()

    logger.info(
        "Обновлён i18n override: {}",
        setting_key,
    )

    return setting


async def delete_override(
    session: AsyncSession,
    key: str,
    language: str,
) -> bool:
    """Удаляет override текста."""

    if not key:
        raise ValueError(
            "Ключ локализации не может быть пустым"
        )

    lang = normalize_language(language)

    setting_key = _override_key(
        key,
        lang,
    )

    result = await session.execute(
        select(ShopSetting).where(
            ShopSetting.key == setting_key,
        )
    )

    setting = result.scalar_one_or_none()

    if setting is None:
        return False

    await session.delete(setting)
    await session.flush()

    logger.info(
        "Удалён i18n override: {}",
        setting_key,
    )

    return True


async def list_overrides(
    session: AsyncSession,
    language: str | None = None,
) -> dict[str, str]:
    """
    Возвращает overrides.

    Результат:

        {
            "menu.welcome": "Новый текст",
            "buttons.catalog": "Каталог"
        }
    """

    lang = normalize_language(language)

    prefix = f"{TEXT_OVERRIDE_PREFIX}{lang}:"

    result = await session.execute(
        select(
            ShopSetting.key,
            ShopSetting.value,
        )
        .where(
            ShopSetting.key.like(
                f"{prefix}%"
            )
        )
        .order_by(
            ShopSetting.key.asc()
        )
    )

    overrides: dict[str, str] = {}

    for setting_key, value in result.all():
        key = setting_key.removeprefix(prefix)

        overrides[key] = value

    return overrides


def clear_translation_cache() -> None:
    """Очищает кеш JSON локализаций."""

    _load_language.cache_clear()


def get_supported_languages() -> tuple[str, ...]:
    """Возвращает поддерживаемые языки."""

    return tuple(
        sorted(SUPPORTED_LANGUAGES)
    )


def get_translation_keys(
    language: str = DEFAULT_LANGUAGE,
) -> tuple[str, ...]:
    """Возвращает список ключей JSON локализации."""

    translations = _load_language(
        normalize_language(language),
    )

    return tuple(
        sorted(translations.keys())
    )


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "TEXT_OVERRIDE_PREFIX",
    "I18nError",
    "normalize_language",
    "t",
    "at",
    "get_override",
    "set_override",
    "delete_override",
    "list_overrides",
    "clear_translation_cache",
    "get_supported_languages",
    "get_translation_keys",
]