from __future__ import annotations

from typing import Any

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select

from app.config import settings
from app.database.models import Admin, User, UserStatus
from app.database.session import SessionLocal


def _extract_telegram_id(event: TelegramObject) -> int | None:
    """
    Получает Telegram ID пользователя из Message или CallbackQuery.
    """

    if isinstance(event, Message):
        return event.from_user.id if event.from_user else None

    if isinstance(event, CallbackQuery):
        return event.from_user.id if event.from_user else None

    return None


class IsAdminFilter(BaseFilter):
    """
    Проверяет наличие пользователя в списке администраторов.

    Проверка выполняется по двум источникам:

    1. ADMIN_IDS из конфигурации;
    2. таблица admins в БД.

    Дополнительно проверяется статус пользователя.
    Заблокированный пользователь не считается администратором.

    Результат:
        True  — доступ разрешён;
        False — доступ запрещён.
    """

    async def __call__(self, event: TelegramObject, **kwargs: Any) -> bool:
        del kwargs

        telegram_id = _extract_telegram_id(event)

        if telegram_id is None:
            return False

        try:
            configured_admin_ids = set(settings.admin_ids)

            if telegram_id in configured_admin_ids:
                return await self._check_user_status(telegram_id)

            async with SessionLocal() as session:
                result = await session.execute(
                    select(Admin.id)
                    .join(User, User.id == Admin.user_id)
                    .where(
                        User.telegram_id == telegram_id,
                        User.status == UserStatus.ACTIVE,
                    )
                    .limit(1)
                )

                return result.scalar_one_or_none() is not None

        except Exception:
            return False

    @staticmethod
    async def _check_user_status(telegram_id: int) -> bool:
        """
        Для администратора из ADMIN_IDS дополнительно проверяем,
        что пользователь не заблокирован в БД.
        """

        try:
            async with SessionLocal() as session:
                result = await session.execute(
                    select(User.status)
                    .where(User.telegram_id == telegram_id)
                    .limit(1)
                )

                status = result.scalar_one_or_none()

                # Если пользователь ещё не существует в БД,
                # доступ по ADMIN_IDS всё равно разрешаем.
                if status is None:
                    return True

                return status == UserStatus.ACTIVE

        except Exception:
            return False


class AdminFilter(IsAdminFilter):
    """
    Алиас для IsAdminFilter.

    Можно использовать оба варианта:

        AdminFilter()

    или:

        IsAdminFilter()
    """

    pass


__all__ = [
    "AdminFilter",
    "IsAdminFilter",
]