from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    Order,
    OrderStatus,
    PaymentInvoice,
    PaymentStatus,
    TopupStatus,
    TopupTransaction,
    User,
    UserStatus,
)
from app.keyboards.admin import admin_main_keyboard
from app.utils.money import format_usd


router = Router(name="admin_stats")


def _is_admin(user_id: int) -> bool:
    """Проверяет наличие пользователя в списке администраторов."""
    return user_id in settings.admin_ids


def _utc_now() -> datetime:
    """Возвращает текущее время в UTC."""
    return datetime.now(timezone.utc)


def _start_of_day() -> datetime:
    """Возвращает начало текущих суток в UTC."""
    now = _utc_now()
    return now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _start_of_month() -> datetime:
    """Возвращает начало текущего месяца в UTC."""
    now = _utc_now()
    return now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


async def _count_users(session: AsyncSession) -> dict[str, int]:
    """Собирает статистику пользователей."""
    total = await session.scalar(
        select(func.count(User.id))
    )

    active = await session.scalar(
        select(func.count(User.id)).where(
            User.status == UserStatus.ACTIVE
        )
    )

    banned = await session.scalar(
        select(func.count(User.id)).where(
            User.status == UserStatus.BANNED
        )
    )

    admins = await session.scalar(
        select(func.count(User.id)).where(
            User.is_admin.is_(True)
        )
    )

    return {
        "total": int(total or 0),
        "active": int(active or 0),
        "banned": int(banned or 0),
        "admins": int(admins or 0),
    }


async def _count_orders(session: AsyncSession) -> dict[str, int]:
    """Собирает статистику заказов."""
    total = await session.scalar(
        select(func.count(Order.id))
    )

    pending = await session.scalar(
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.PAYMENT_PENDING
        )
    )

    paid = await session.scalar(
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.PAID
        )
    )

    delivered = await session.scalar(
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.DELIVERED
        )
    )

    expired = await session.scalar(
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.EXPIRED
        )
    )

    cancelled = await session.scalar(
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.CANCELLED
        )
    )

    refunded = await session.scalar(
        select(func.count(Order.id)).where(
            Order.status == OrderStatus.REFUNDED
        )
    )

    return {
        "total": int(total or 0),
        "pending": int(pending or 0),
        "paid": int(paid or 0),
        "delivered": int(delivered or 0),
        "expired": int(expired or 0),
        "cancelled": int(cancelled or 0),
        "refunded": int(refunded or 0),
    }


async def _get_revenue(
    session: AsyncSession,
    start: datetime | None = None,
) -> Decimal:
    """
    Считает оборот по оплаченным заказам.

    PAID и DELIVERED считаются завершёнными продажами.
    REFUNDED и CANCELLED в оборот не включаются.
    """
    conditions = [
        Order.status.in_(
            (
                OrderStatus.PAID,
                OrderStatus.DELIVERED,
            )
        )
    ]

    if start is not None:
        conditions.append(Order.created_at >= start)

    result = await session.scalar(
        select(
            func.coalesce(
                func.sum(Order.total_usd),
                0,
            )
        ).where(*conditions)
    )

    return Decimal(str(result or 0))


async def _get_topup_stats(
    session: AsyncSession,
) -> dict[str, Decimal | int]:
    """Собирает статистику пополнений баланса."""
    total = await session.scalar(
        select(func.count(TopupTransaction.id))
    )

    pending = await session.scalar(
        select(func.count(TopupTransaction.id)).where(
            TopupTransaction.status == TopupStatus.PENDING
        )
    )

    paid = await session.scalar(
        select(func.count(TopupTransaction.id)).where(
            TopupTransaction.status == TopupStatus.PAID
        )
    )

    amount = await session.scalar(
        select(
            func.coalesce(
                func.sum(TopupTransaction.amount_usd),
                0,
            )
        ).where(
            TopupTransaction.status == TopupStatus.PAID
        )
    )

    return {
        "total": int(total or 0),
        "pending": int(pending or 0),
        "paid": int(paid or 0),
        "amount": Decimal(str(amount or 0)),
    }


async def _get_balance_stats(
    session: AsyncSession,
) -> dict[str, Decimal | int]:
    """Считает общий пользовательский баланс."""
    users_balance = await session.scalar(
        select(
            func.coalesce(
                func.sum(User.balance_usd),
                0,
            )
        )
    )

    transactions = await session.scalar(
        select(func.count(BalanceTransaction.id))
    )

    deposits = await session.scalar(
        select(
            func.coalesce(
                func.sum(BalanceTransaction.amount_usd),
                0,
            )
        ).where(
            BalanceTransaction.type
            == BalanceTransactionType.DEPOSIT
        )
    )

    return {
        "balance": Decimal(str(users_balance or 0)),
        "transactions": int(transactions or 0),
        "deposits": Decimal(str(deposits or 0)),
    }


async def _get_payment_stats(
    session: AsyncSession,
) -> dict[str, int]:
    """Собирает статистику платёжных инвойсов."""
    total = await session.scalar(
        select(func.count(PaymentInvoice.id))
    )

    pending = await session.scalar(
        select(func.count(PaymentInvoice.id)).where(
            PaymentInvoice.status == PaymentStatus.PENDING
        )
    )

    paid = await session.scalar(
        select(func.count(PaymentInvoice.id)).where(
            PaymentInvoice.status == PaymentStatus.PAID
        )
    )

    return {
        "total": int(total or 0),
        "pending": int(pending or 0),
        "paid": int(paid or 0),
    }


def _build_stats_text(
    users: dict[str, int],
    orders: dict[str, int],
    revenue_total: Decimal,
    revenue_today: Decimal,
    revenue_month: Decimal,
    topups: dict[str, Decimal | int],
    balances: dict[str, Decimal | int],
    payments: dict[str, int],
) -> str:
    """Формирует текст статистики администратора."""
    return (
        "📊 <b>Статистика магазина</b>\n\n"

        "👥 <b>Пользователи</b>\n"
        f"├ Всего: <b>{users['total']}</b>\n"
        f"├ Активных: <b>{users['active']}</b>\n"
        f"├ Заблокированных: <b>{users['banned']}</b>\n"
        f"└ Администраторов: <b>{users['admins']}</b>\n\n"

        "📦 <b>Заказы</b>\n"
        f"├ Всего: <b>{orders['total']}</b>\n"
        f"├ Ожидают оплаты: <b>{orders['pending']}</b>\n"
        f"├ Оплачены: <b>{orders['paid']}</b>\n"
        f"├ Доставлены: <b>{orders['delivered']}</b>\n"
        f"├ Истекли: <b>{orders['expired']}</b>\n"
        f"├ Отменены: <b>{orders['cancelled']}</b>\n"
        f"└ Возвращены: <b>{orders['refunded']}</b>\n\n"

        "💰 <b>Оборот</b>\n"
        f"├ Всего: <b>{format_usd(revenue_total)}</b>\n"
        f"├ Сегодня: <b>{format_usd(revenue_today)}</b>\n"
        f"└ За месяц: <b>{format_usd(revenue_month)}</b>\n\n"

        "💳 <b>Пополнения</b>\n"
        f"├ Всего операций: <b>{topups['total']}</b>\n"
        f"├ Ожидают: <b>{topups['pending']}</b>\n"
        f"├ Успешных: <b>{topups['paid']}</b>\n"
        f"└ Сумма: <b>{format_usd(topups['amount'])}</b>\n\n"

        "💵 <b>Баланс пользователей</b>\n"
        f"├ На балансах: <b>{format_usd(balances['balance'])}</b>\n"
        f"├ Пополнений: <b>{format_usd(balances['deposits'])}</b>\n"
        f"└ Транзакций: <b>{balances['transactions']}</b>\n\n"

        "💸 <b>Платёжные инвойсы</b>\n"
        f"├ Всего: <b>{payments['total']}</b>\n"
        f"├ Ожидают: <b>{payments['pending']}</b>\n"
        f"└ Оплачены: <b>{payments['paid']}</b>"
    )


async def _load_stats(
    session: AsyncSession,
) -> str:
    """Загружает всю статистику одним логическим набором запросов."""
    users = await _count_users(session)
    orders = await _count_orders(session)

    revenue_total = await _get_revenue(session)
    revenue_today = await _get_revenue(
        session,
        _start_of_day(),
    )
    revenue_month = await _get_revenue(
        session,
        _start_of_month(),
    )

    topups = await _get_topup_stats(session)
    balances = await _get_balance_stats(session)
    payments = await _get_payment_stats(session)

    return _build_stats_text(
        users=users,
        orders=orders,
        revenue_total=revenue_total,
        revenue_today=revenue_today,
        revenue_month=revenue_month,
        topups=topups,
        balances=balances,
        payments=payments,
    )


@router.message(Command("stats"))
async def stats_command(
    message: Message,
    session: AsyncSession,
) -> None:
    """Открывает статистику по команде /stats."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    try:
        text = await _load_stats(session)

        await message.answer(
            text,
            reply_markup=admin_main_keyboard(),
        )
    except Exception:
        logger.exception(
            "Ошибка открытия статистики: telegram_id={}",
            message.from_user.id,
        )
        await message.answer(
            "❌ Не удалось загрузить статистику."
        )


@router.callback_query(
    AdminCB.filter(F.action == "stats")
)
async def stats_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Открывает статистику из административного меню."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        text = await _load_stats(session)

        if callback.message is not None:
            await callback.message.edit_text(
                text,
                reply_markup=admin_main_keyboard(),
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия статистики: telegram_id={}",
            callback.from_user.id,
        )
        await callback.answer(
            "Ошибка загрузки статистики.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "stats_refresh")
)
async def stats_refresh_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Обновляет статистику."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        text = await _load_stats(session)

        if callback.message is not None:
            await callback.message.edit_text(
                text,
                reply_markup=admin_main_keyboard(),
            )

        await callback.answer(
            "Статистика обновлена."
        )

    except Exception:
        logger.exception(
            "Ошибка обновления статистики: telegram_id={}",
            callback.from_user.id,
        )
        await callback.answer(
            "Ошибка обновления статистики.",
            show_alert=True,
        )


__all__ = ["router"]