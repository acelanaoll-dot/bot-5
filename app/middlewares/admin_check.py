from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Admin, User, UserStatus


class AdminCheckMiddleware(BaseMiddleware):
    """
    Middleware дополнительной защиты административных роутеров.

    Проверяет администратора непосредственно перед выполнением
    handler.

    Источники административного доступа:

    1. Telegram ID из settings.admin_ids.
    2. Активная запись Admin, связанная с активным User.

    Middleware не заменяет AdminFilter:
        AdminFilter — фильтр маршрута;
        AdminCheckMiddleware — дополнительная защита перед handler.

    Это сделано намеренно: административные действия не должны
    зависеть только от одного уровня проверки.
    """

    def __init__(
        self,
        *,
        silent: bool = False,
    ) -> None:
        super().__init__()
        self.silent = silent

    async def __call__(
        self,
        handler: Callable[
            [TelegramObject, dict[str, Any]],
            Awaitable[Any],
        ],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_id = self._extract_telegram_id(event)

        if telegram_id is None:
            return await self._deny(
                event,
                "Не удалось определить пользователя.",
            )

        session = data.get("session")

        if not isinstance(session, AsyncSession):
            logger.error(
                "AdminCheckMiddleware: AsyncSession отсутствует "
                "в middleware data"
            )
            return await self._deny(
                event,
                "Ошибка проверки доступа.",
            )

        try:
            is_admin = await self._check_admin(
                session=session,
                telegram_id=telegram_id,
            )

            if not is_admin:
                logger.warning(
                    "Попытка доступа к admin handler: "
                    "telegram_id={}",
                    telegram_id,
                )

                return await self._deny(
                    event,
                    "Доступ запрещён.",
                )

            # Передаём результат дальше, чтобы handler при
            # необходимости не выполнял повторную проверку.
            data["is_admin"] = True
            data["admin_telegram_id"] = telegram_id

            return await handler(event, data)

        except Exception:
            logger.exception(
                "Ошибка проверки административного доступа: "
                "telegram_id={}",
                telegram_id,
            )

            return await self._deny(
                event,
                "Ошибка проверки доступа.",
            )

    @staticmethod
    def _extract_telegram_id(
        event: TelegramObject,
    ) -> int | None:
        """
        Получает Telegram ID пользователя из события.
        """

        if isinstance(event, Message):
            if event.from_user is None:
                return None

            return event.from_user.id

        if isinstance(event, CallbackQuery):
            if event.from_user is None:
                return None

            return event.from_user.id

        return None

    @staticmethod
    async def _check_admin(
        session: AsyncSession,
        telegram_id: int,
    ) -> bool:
        """
        Проверяет административный доступ через конфигурацию
        и таблицу Admin.

        Для ID из .env дополнительно проверяется статус User,
        если пользователь уже существует в БД.

        Для администратора из таблицы Admin одновременно
        проверяются:
            - User.telegram_id;
            - User.status == ACTIVE;
            - Admin.is_active == True.
        """

        configured_admin_ids = set(settings.admin_ids)

        if telegram_id in configured_admin_ids:
            result = await session.execute(
                select(User.status)
                .where(
                    User.telegram_id == telegram_id,
                )
                .limit(1)
            )

            status = result.scalar_one_or_none()

            # Если пользователь ещё не создан в БД, ID из .env
            # всё равно считается доверенным администратором.
            if status is None:
                return True

            return status == UserStatus.ACTIVE

        result = await session.execute(
            select(Admin.id)
            .join(
                User,
                User.id == Admin.user_id,
            )
            .where(
                User.telegram_id == telegram_id,
                User.status == UserStatus.ACTIVE,
                Admin.is_active.is_(True),
            )
            .limit(1)
        )

        return result.scalar_one_or_none() is not None

    async def _deny(
        self,
        event: TelegramObject,
        text: str,
    ) -> None:
        """
        Безопасно сообщает об отказе в доступе.

        Для callback используется answer(), чтобы Telegram
        не показывал вечный индикатор загрузки.

        Для сообщений ответ отправляется только если silent=False.
        """

        if self.silent:
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except Exception:
                    logger.debug(
                        "Не удалось закрыть callback query"
                    )
            return

        try:
            if isinstance(event, CallbackQuery):
                await event.answer(
                    text=text,
                    show_alert=True,
                )
                return

            if isinstance(event, Message):
                await event.answer(text)

        except Exception:
            logger.debug(
                "Не удалось отправить сообщение "
                "об отказе в административном доступе"
            )


__all__ = [
    "AdminCheckMiddleware",
]