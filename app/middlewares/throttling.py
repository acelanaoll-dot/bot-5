from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from loguru import logger

from app.config import settings


@dataclass(slots=True)
class _UserRateState:
    """
    Состояние ограничения запросов одного пользователя.
    """

    timestamps: deque[float]


class ThrottlingMiddleware(BaseMiddleware):
    """
    Защита бота от слишком частых запросов.

    Ограничение применяется отдельно к каждому Telegram user_id.

    Для callback-запросов и сообщений используется один общий
    счётчик пользователя, чтобы нельзя было обходить лимит
    переключением между типами событий.

    Внутри процесса используется asyncio.Lock, поэтому операции
    со структурой rate-limit выполняются безопасно для нескольких
    параллельных coroutine.

    Настройки берутся из:
        settings.antiflood_enabled
        settings.antiflood_rate
        settings.antiflood_burst
        settings.antiflood_cleanup_interval
    """

    def __init__(
        self,
        rate: float | None = None,
        burst: int | None = None,
    ) -> None:
        super().__init__()

        configured_rate = getattr(
            settings,
            "antiflood_rate",
            1.0,
        )
        configured_burst = getattr(
            settings,
            "antiflood_burst",
            5,
        )

        self.rate = max(
            float(rate if rate is not None else configured_rate),
            0.01,
        )
        self.burst = max(
            int(burst if burst is not None else configured_burst),
            1,
        )

        self._users: dict[int, _UserRateState] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()

        cleanup_interval = getattr(
            settings,
            "antiflood_cleanup_interval",
            300,
        )
        self._cleanup_interval = max(
            float(cleanup_interval),
            30.0,
        )

    async def __call__(
        self,
        handler: Callable[
            [TelegramObject, dict[str, Any]],
            Awaitable[Any],
        ],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not getattr(
            settings,
            "antiflood_enabled",
            True,
        ):
            return await handler(event, data)

        user_id = self._extract_user_id(event)

        # События без пользователя не ограничиваем.
        if user_id is None:
            return await handler(event, data)

        allowed, retry_after = await self._check_rate_limit(
            user_id
        )

        if not allowed:
            await self._handle_throttled_event(
                event=event,
                retry_after=retry_after,
            )
            return None

        return await handler(event, data)

    @staticmethod
    def _extract_user_id(
        event: TelegramObject,
    ) -> int | None:
        """
        Получает Telegram user_id из поддерживаемых событий.
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

    async def _check_rate_limit(
        self,
        user_id: int,
    ) -> tuple[bool, float]:
        """
        Проверяет лимит и регистрирует запрос.

        Возвращает:
            (True, 0.0) — запрос разрешён;
            (False, retry_after) — запрос отклонён.
        """

        now = time.monotonic()
        window = 1.0 / self.rate

        async with self._lock:
            state = self._users.get(user_id)

            if state is None:
                state = _UserRateState(
                    timestamps=deque()
                )
                self._users[user_id] = state

            # Удаляем устаревшие запросы.
            while state.timestamps:
                if now - state.timestamps[0] < window:
                    break
                state.timestamps.popleft()

            # Проверяем burst.
            if len(state.timestamps) >= self.burst:
                oldest = state.timestamps[0]
                retry_after = max(
                    window - (now - oldest),
                    0.1,
                )

                await self._cleanup_if_needed(now)

                return False, retry_after

            state.timestamps.append(now)

            await self._cleanup_if_needed(now)

            return True, 0.0

    async def _cleanup_if_needed(
        self,
        now: float,
    ) -> None:
        """
        Периодически удаляет старые записи пользователей.

        Это предотвращает бесконтрольный рост памяти при большом
        количестве уникальных Telegram пользователей.
        """

        if now - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = now

        expired_users: list[int] = []

        for user_id, state in self._users.items():
            if not state.timestamps:
                expired_users.append(user_id)
                continue

            # Если последний запрос старше cleanup interval,
            # состояние больше не представляет интереса.
            if now - state.timestamps[-1] > self._cleanup_interval:
                expired_users.append(user_id)

        for user_id in expired_users:
            self._users.pop(user_id, None)

    async def _handle_throttled_event(
        self,
        event: TelegramObject,
        retry_after: float,
    ) -> None:
        """
        Обрабатывает превышение лимита.

        CallbackQuery получает короткий answer без отправки
        дополнительного сообщения в чат.

        Для Message отправляется предупреждение с ограничением
        частоты. Повторный flood не должен приводить к генерации
        большого количества сообщений.
        """

        retry_seconds = max(
            int(retry_after) + 1,
            1,
        )

        try:
            if isinstance(event, CallbackQuery):
                await event.answer(
                    text=(
                        f"Слишком много запросов. "
                        f"Повторите через {retry_seconds} сек."
                    ),
                    show_alert=False,
                )
                return

            if isinstance(event, Message):
                await event.answer(
                    text=(
                        f"Слишком много запросов. "
                        f"Повторите через {retry_seconds} сек."
                    )
                )
                return

        except Exception:
            logger.debug(
                "Не удалось отправить throttling-уведомление "
                "пользователю"
            )

    async def reset_user(
        self,
        user_id: int,
    ) -> None:
        """
        Сбрасывает throttling-состояние пользователя.
        """

        async with self._lock:
            self._users.pop(user_id, None)

    async def reset_all(self) -> None:
        """
        Полностью очищает локальное состояние throttling.
        """

        async with self._lock:
            self._users.clear()
            self._last_cleanup = time.monotonic()

    async def get_user_state(
        self,
        user_id: int,
    ) -> dict[str, Any]:
        """
        Возвращает диагностическую информацию о пользователе.

        Используется для административной диагностики.
        """

        now = time.monotonic()

        async with self._lock:
            state = self._users.get(user_id)

            if state is None:
                return {
                    "user_id": user_id,
                    "requests_in_window": 0,
                    "burst": self.burst,
                    "rate": self.rate,
                    "blocked": False,
                }

            window = 1.0 / self.rate

            while state.timestamps:
                if now - state.timestamps[0] < window:
                    break
                state.timestamps.popleft()

            return {
                "user_id": user_id,
                "requests_in_window": len(state.timestamps),
                "burst": self.burst,
                "rate": self.rate,
                "blocked": len(state.timestamps) >= self.burst,
            }

    @property
    def active_users(self) -> int:
        """
        Количество пользователей, для которых хранится состояние.
        """

        return len(self._users)


__all__ = [
    "ThrottlingMiddleware",
]