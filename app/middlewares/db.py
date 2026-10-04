from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import SessionLocal


class DatabaseMiddleware(BaseMiddleware):
    """
    Middleware для передачи AsyncSession в handlers.

    Для каждого входящего Telegram-события создаётся отдельная
    SQLAlchemy-сессия.

    Сессия передаётся в handler через аргумент:
        session: AsyncSession

    Если handler завершился успешно — изменения фиксируются.
    Если возникло исключение — выполняется rollback.

    После обработки события сессия гарантированно закрывается.
    """

    async def __call__(
        self,
        handler: Callable[
            [TelegramObject, dict[str, Any]],
            Awaitable[Any],
        ],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession = SessionLocal()

        # Не перезаписываем session, если middleware был вложен
        # поверх уже существующей сессии.
        existing_session = data.get("session")

        if isinstance(existing_session, AsyncSession):
            await session.close()
            return await handler(event, data)

        data["session"] = session

        try:
            result = await handler(event, data)

            # Handler мог самостоятельно выполнить commit.
            # Если транзакция всё ещё открыта — фиксируем изменения.
            if session.in_transaction():
                await session.commit()

            return result

        except Exception:
            try:
                if session.in_transaction():
                    await session.rollback()
            except Exception:
                logger.exception(
                    "Не удалось выполнить rollback сессии БД"
                )

            logger.exception(
                "Ошибка handler при работе с базой данных"
            )
            raise

        finally:
            try:
                await session.close()
            except Exception:
                logger.exception(
                    "Ошибка при закрытии сессии БД"
                )


__all__ = [
    "DatabaseMiddleware",
]