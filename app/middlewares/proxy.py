from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from loguru import logger

from app.config import settings
from app.services.proxy_manager import proxy_manager


class ProxyMiddleware(BaseMiddleware):
    """
    Middleware для инфраструктуры прокси.

    Важно:
        aiogram сам выполняет HTTP-запросы к Telegram через
        Bot session. Поэтому этот middleware не пытается
        подменять HTTP transport непосредственно во время
        обработки каждого update.

    Основная задача middleware:
        - передать актуальную информацию о прокси в data;
        - при необходимости выполнить health-check;
        - предоставить handler/service доступ к ProxyManager;
        - не вмешиваться в обычную обработку update.

    Реальное применение прокси к внешним API выполняется
    непосредственно в соответствующих сервисах:
        - CryptoPay;
        - NOWPayments;
        - другие внешние HTTP API.

    Telegram-прокси настраивается при создании Bot/session
    в app/main.py.
    """

    def __init__(
        self,
        *,
        health_check: bool = False,
    ) -> None:
        super().__init__()

        self.health_check_enabled = health_check

    async def __call__(
        self,
        handler: Callable[
            [TelegramObject, dict[str, Any]],
            Awaitable[Any],
        ],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Передаём единый ProxyManager в контекст handler.
        data["proxy_manager"] = proxy_manager

        # Не выполняем health-check на каждом update:
        # это создаёт лишнюю сетевую нагрузку.
        if self.health_check_enabled:
            try:
                current_proxy = proxy_manager.current()

                if current_proxy:
                    state = proxy_manager.get_state(current_proxy)

                    if state is not None:
                        data["proxy"] = current_proxy
                        data["proxy_state"] = state

            except Exception:
                logger.exception(
                    "Ошибка получения состояния прокси"
                )

        return await handler(event, data)

    @staticmethod
    def get_current_proxy() -> str | None:
        """
        Возвращает текущий прокси из ProxyManager.
        """

        try:
            return proxy_manager.current()
        except Exception:
            logger.exception(
                "Ошибка получения текущего прокси"
            )
            return None

    @staticmethod
    def is_enabled() -> bool:
        """
        Проверяет, включён ли пул прокси.
        """

        return bool(
            settings.proxy_rotation_enabled
            or settings.crypto_proxy_enabled
            or settings.telegram_proxy
        )

    @staticmethod
    def get_telegram_proxy() -> str | None:
        """
        Возвращает прокси Telegram API.

        Используется при создании Bot/session, а не внутри
        каждого handler.
        """

        try:
            proxy = settings.telegram_proxy

            if proxy:
                return proxy

            if settings.use_global_proxy_for_telegram:
                return proxy_manager.current()

            return None

        except Exception:
            logger.exception(
                "Ошибка получения Telegram proxy"
            )
            return None

    @staticmethod
    def get_crypto_proxy() -> str | None:
        """
        Возвращает текущий прокси для внешних crypto API.
        """

        try:
            if not settings.crypto_proxy_enabled:
                return None

            return proxy_manager.current()

        except Exception:
            logger.exception(
                "Ошибка получения crypto proxy"
            )
            return None

    @staticmethod
    def report_success(
        proxy: str | None = None,
    ) -> None:
        """
        Сообщает ProxyManager об успешном запросе.
        """

        try:
            proxy_manager.report_success(proxy)
        except Exception:
            logger.exception(
                "Ошибка регистрации успешного запроса прокси"
            )

    @staticmethod
    def report_failure(
        status_code: int | None = None,
        proxy: str | None = None,
    ) -> bool:
        """
        Сообщает ProxyManager о неудачном запросе.

        Возвращает True, если ProxyManager выполнил ротацию.
        """

        try:
            return proxy_manager.report_failure(
                status_code=status_code,
                proxy=proxy,
            )
        except Exception:
            logger.exception(
                "Ошибка регистрации сбоя прокси"
            )
            return False


__all__ = [
    "ProxyMiddleware",
]