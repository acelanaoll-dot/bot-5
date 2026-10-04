from __future__ import annotations

import asyncio
from contextlib import suppress

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from loguru import logger

from app.config import settings
from app.database.session import dispose_database, init_database, SessionLocal
from app.handlers.admin import admin_router
from app.handlers.user import (
    user_cart,
    user_catalog,
    user_checkout,
    user_profile,
    user_start,
    user_topup,
)
from app.middlewares.db import DatabaseMiddleware
from app.middlewares.proxy import ProxyMiddleware
from app.middlewares.throttling import ThrottlingMiddleware
from app.services.payment_manager import payment_manager
from app.utils.logging import setup_logging


# ============================================================================
# WEBHOOK SERVERS (CryptoPay / NOWPayments IPN)
# ============================================================================

async def cryptopay_webhook_handler(request: web.Request) -> web.Response:
    """Обработчик вебхуков от CryptoPay."""
    signature = request.headers.get("crypto-pay-api-signature", "")
    
    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    session = SessionLocal()
    try:
        await payment_manager.handle_cryptopay_webhook(
            session, payload=payload, signature=signature
        )
        if session.in_transaction():
            await session.commit()
    except Exception as e:
        if session.in_transaction():
            await session.rollback()
        logger.error("Ошибка при обработке вебхука CryptoPay: {}", e)
    finally:
        await session.close()
        
    return web.Response(status=200)


async def nowpayments_ipn_handler(request: web.Request) -> web.Response:
    """Обработчик вебхуков (IPN) от NOWPayments."""
    signature = request.headers.get("x-nowpayments-sig", "")
    
    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    session = SessionLocal()
    try:
        await payment_manager.handle_nowpayments_ipn(
            session, payload=payload, signature=signature
        )
        if session.in_transaction():
            await session.commit()
    except Exception as e:
        if session.in_transaction():
            await session.rollback()
        logger.error("Ошибка при обработке IPN NOWPayments: {}", e)
    finally:
        await session.close()
        
    return web.Response(status=200)


async def start_web_server() -> web.AppRunner:
    """Запускает aiohttp веб-сервер для приема вебхуков."""
    app = web.Application()
    
    # Роуты для платежек
    app.router.add_post("/webhooks/cryptopay", cryptopay_webhook_handler)
    app.router.add_post("/webhooks/nowpayments", nowpayments_ipn_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(
        runner, 
        host=settings.healthcheck_host, 
        port=settings.healthcheck_port
    )
    await site.start()
    
    logger.info("Webhook-сервер запущен на {}:{}", settings.healthcheck_host, settings.healthcheck_port)
    return runner


# ============================================================================
# BOT JOBS & INITIALIZATION
# ============================================================================

async def _expire_payments_job() -> None:
    """Периодически переводит просроченные платежи в expired."""

    session = SessionLocal()

    try:
        count = await payment_manager.expire_old_payments(session)
        if session.in_transaction():
            await session.commit()

        if count:
            logger.info("Автоматически обработано просроченных платежей: {}", count)

    except Exception:
        if session.in_transaction():
            await session.rollback()
        logger.exception("Ошибка фоновой проверки просроченных платежей")

    finally:
        await session.close()


async def _set_bot_commands(bot: Bot) -> None:
    """Устанавливает основные команды Telegram-бота."""

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть магазин"),
            BotCommand(command="admin", description="Административная панель"),
        ]
    )


def _build_bot() -> Bot:
    """Создаёт Bot с настройками проекта."""

    session = AiohttpSession(
        proxy=settings.telegram_proxy,
    )

    return Bot(
        token=settings.bot_token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )


def _build_dispatcher() -> Dispatcher:
    """Создаёт Dispatcher и подключает middleware/routers."""

    dp = Dispatcher()

    dp.update.outer_middleware(
        ThrottlingMiddleware()
    )
    dp.update.outer_middleware(
        ProxyMiddleware()
    )
    dp.update.outer_middleware(
        DatabaseMiddleware()
    )

    dp.include_router(user_start)
    dp.include_router(user_catalog)
    dp.include_router(user_cart)
    dp.include_router(user_checkout)
    dp.include_router(user_topup)
    dp.include_router(user_profile)
    dp.include_router(admin_router)

    return dp


async def main() -> None:
    """Основная точка запуска Telegram-бота."""

    setup_logging()

    logger.info(
        "Запуск {} в окружении {}",
        settings.app_name,
        settings.app_env,
    )

    await init_database()

    bot = _build_bot()
    dp = _build_dispatcher()
    
    # 1. Запуск Web-сервера для вебхуков (параллельно боту)
    web_runner = await start_web_server()
    
    cleanup_task: asyncio.Task[None] | None = None

    try:
        await _set_bot_commands(bot)

        cleanup_interval = max(
            settings.cleanup_interval_minutes * 60,
            60,
        )

        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(cleanup_interval)
                await _expire_payments_job()

        cleanup_task = asyncio.create_task(
            cleanup_loop(),
            name="payment-expiration-cleanup",
        )

        logger.info("Бот запущен и ожидает обновления (Polling)")
        
        # 2. Запуск бота (Polling)
        await dp.start_polling(bot)

    finally:
        # Корректное завершение всех процессов
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

        # Останавливаем Webhook-сервер
        logger.info("Остановка Webhook-сервера...")
        await web_runner.cleanup()

        with suppress(Exception):
            await bot.session.close()

        await dispose_database()

        logger.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())