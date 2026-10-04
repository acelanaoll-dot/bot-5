from __future__ import annotations

import secrets
import string
from typing import Optional

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, UserStatus
from app.keyboards.user import main_menu_keyboard
from app.services.referrals import (
    InvalidReferralError,
    ReferralAlreadyExistsError,
    ReferralDisabledError,
    referral_service,
)

router = Router(name="user_start")


# ============================================================
# Константы
# ============================================================

REFERRAL_CODE_LENGTH = 10
REFERRAL_CODE_ALPHABET = string.ascii_letters + string.digits

MAX_START_PAYLOAD_LENGTH = 128


# ============================================================
# Вспомогательные функции
# ============================================================


def _normalize_text(value: Optional[str], max_length: int = 255) -> Optional[str]:
    """Безопасно нормализует строковое значение Telegram-профиля."""

    if value is None:
        return None

    value = value.strip()

    if not value:
        return None

    return value[:max_length]


def _generate_referral_code() -> str:
    """Генерирует случайный реферальный код."""

    return "".join(
        secrets.choice(REFERRAL_CODE_ALPHABET)
        for _ in range(REFERRAL_CODE_LENGTH)
    )


def _extract_referral_code(args: Optional[str]) -> Optional[str]:
    """
    Извлекает реферальный код из payload команды /start.

    Поддерживаются:
    /start ref_XXXXXXXX
    /start refXXXXXXXX
    /start XXXXXXXX

    Остальные payload игнорируются.
    """

    if not args:
        return None

    payload = args.strip()

    if not payload:
        return None

    payload = payload[:MAX_START_PAYLOAD_LENGTH]

    if payload.startswith("ref_"):
        payload = payload[4:]
    elif payload.startswith("ref"):
        payload = payload[3:]

    payload = payload.strip()

    if not payload:
        return None

    allowed = set(REFERRAL_CODE_ALPHABET)

    if len(payload) > 128:
        return None

    if any(character not in allowed for character in payload):
        return None

    return payload


async def _generate_unique_referral_code(
    session: AsyncSession,
) -> str:
    """Гарантирует уникальность referral_code."""

    for _ in range(20):
        code = _generate_referral_code()

        result = await session.execute(
            select(User.id).where(
                User.referral_code == code,
            )
        )

        if result.scalar_one_or_none() is None:
            return code

    raise RuntimeError(
        "Не удалось сгенерировать уникальный реферальный код."
    )


async def _get_user(
    session: AsyncSession,
    telegram_id: int,
) -> Optional[User]:
    """Ищет пользователя по Telegram ID."""

    result = await session.execute(
        select(User).where(
            User.telegram_id == telegram_id,
        )
    )

    return result.scalar_one_or_none()


async def _create_user(
    session: AsyncSession,
    message: Message,
) -> User:
    """Создаёт нового пользователя."""

    if message.from_user is None:
        raise ValueError("Telegram user отсутствует.")

    telegram_user = message.from_user

    referral_code = await _generate_unique_referral_code(
        session=session,
    )

    user = User(
        telegram_id=telegram_user.id,
        username=_normalize_text(telegram_user.username),
        first_name=_normalize_text(telegram_user.first_name),
        last_name=_normalize_text(telegram_user.last_name),
        language_code=(
            telegram_user.language_code
            if telegram_user.language_code in {"ru", "en"}
            else "ru"
        ),
        status=UserStatus.ACTIVE,
        referral_code=referral_code,
    )

    session.add(user)

    try:
        await session.flush()
    except IntegrityError as exc:
        logger.warning(
            "Конфликт при создании пользователя telegram_id={}",
            telegram_user.id,
        )
        raise RuntimeError(
            "Пользователь уже был создан параллельно."
        ) from exc

    logger.info(
        "Зарегистрирован новый пользователь: "
        "telegram_id={}, user_id={}",
        telegram_user.id,
        user.id,
    )

    return user


async def _update_user_profile(
    user: User,
    message: Message,
) -> None:
    """Обновляет данные Telegram-профиля."""

    if message.from_user is None:
        return

    telegram_user = message.from_user

    user.username = _normalize_text(
        telegram_user.username,
    )

    user.first_name = _normalize_text(
        telegram_user.first_name,
    )

    user.last_name = _normalize_text(
        telegram_user.last_name,
    )

    if (
        telegram_user.language_code in {"ru", "en"}
        and not user.language_code
    ):
        user.language_code = telegram_user.language_code


async def _touch_user(
    session: AsyncSession,
    user: User,
) -> None:
    """Обновляет last_seen_at."""

    from datetime import datetime, timezone

    user.last_seen_at = datetime.now(timezone.utc)

    if user.status != UserStatus.ACTIVE:
        return

    await session.flush()


async def _bind_referral_if_possible(
    session: AsyncSession,
    user: User,
    referral_code: Optional[str],
) -> bool:
    """Пытается привязать реферала при первой регистрации."""

    if not referral_code:
        return False

    if user.referred_by_id is not None:
        return False

    if not referral_service.enabled:
        return False

    try:
        await referral_service.bind_referral(
            session=session,
            referred_user_id=user.id,
            referral_code=referral_code,
        )

        logger.info(
            "Реферальная связь создана: user_id={}, code={}",
            user.id,
            referral_code,
        )

        return True

    except ReferralDisabledError:
        return False

    except ReferralAlreadyExistsError:
        return False

    except InvalidReferralError:
        logger.warning(
            "Некорректный referral code={} для user_id={}",
            referral_code,
            user.id,
        )
        return False

    except Exception:
        logger.exception(
            "Ошибка привязки реферала: user_id={}, code={}",
            user.id,
            referral_code,
        )
        return False


def _welcome_text(user: User, is_new: bool) -> str:
    """Формирует приветственное сообщение."""

    name = (
        user.first_name
        or user.username
        or "пользователь"
    )

    if is_new:
        return (
            f"👋 Привет, <b>{name}</b>!\n\n"
            "Добро пожаловать в магазин.\n"
            "Выбирай нужный товар в каталоге или "
            "открой профиль."
        )

    return (
        f"👋 С возвращением, <b>{name}</b>!\n\n"
        "Главное меню магазина:"
    )


# ============================================================
# /start
# ============================================================


@router.message(CommandStart())
async def command_start(
    message: Message,
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Обрабатывает /start и регистрацию пользователя."""

    if message.from_user is None:
        return

    telegram_id = message.from_user.id

    args = None

    if message.text:
        parts = message.text.split(maxsplit=1)

        if len(parts) == 2:
            args = parts[1]

    referral_code = _extract_referral_code(args)

    try:
        user = await _get_user(
            session=session,
            telegram_id=telegram_id,
        )

        is_new = user is None

        if user is None:
            user = await _create_user(
                session=session,
                message=message,
            )

            # Привязываем referral только для нового пользователя.
            await _bind_referral_if_possible(
                session=session,
                user=user,
                referral_code=referral_code,
            )
        else:
            await _update_user_profile(
                user=user,
                message=message,
            )

            await _touch_user(
                session=session,
                user=user,
            )

        await session.flush()

        await message.answer(
            _welcome_text(
                user=user,
                is_new=is_new,
            ),
            reply_markup=main_menu_keyboard(),
        )

        logger.info(
            "/start обработан: telegram_id={}, user_id={}, new={}",
            telegram_id,
            user.id,
            is_new,
        )

    except Exception:
        logger.exception(
            "Ошибка обработки /start: telegram_id={}",
            telegram_id,
        )

        await message.answer(
            "⚠️ Не удалось открыть магазин.\n"
            "Попробуйте ещё раз через несколько секунд."
        )


# ============================================================
# /menu
# ============================================================


@router.message(Command("menu"))
async def command_menu(
    message: Message,
    session: AsyncSession,
) -> None:
    """Возвращает пользователя в главное меню."""

    if message.from_user is None:
        return

    try:
        user = await _get_user(
            session=session,
            telegram_id=message.from_user.id,
        )

        if user is None:
            user = await _create_user(
                session=session,
                message=message,
            )

        await _touch_user(
            session=session,
            user=user,
        )

        await session.flush()

        await message.answer(
            "🏠 <b>Главное меню</b>\n\n"
            "Выберите нужный раздел:",
            reply_markup=main_menu_keyboard(),
        )

    except Exception:
        logger.exception(
            "Ошибка команды /menu: telegram_id={}",
            message.from_user.id,
        )

        await message.answer(
            "⚠️ Не удалось открыть меню."
        )


# ============================================================
# /help
# ============================================================


@router.message(Command("help"))
async def command_help(message: Message) -> None:
    """Показывает краткую справку."""

    await message.answer(
        "ℹ️ <b>Помощь</b>\n\n"
        "🛍 Каталог — просмотр товаров.\n"
        "🛒 Корзина — выбранные товары.\n"
        "💰 Баланс — пополнение и история операций.\n"
        "📦 Мои заказы — история заказов.\n"
        "👥 Рефералы — ваша реферальная программа.\n\n"
        "Если нужна помощь оператора — откройте раздел "
        "«🆘 Поддержка»."
    )


# ============================================================
# Экспорт
# ============================================================

__all__ = [
    "router",
]