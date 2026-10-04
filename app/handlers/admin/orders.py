from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.orders import OrderAdminCB
from app.config import settings
from app.database.models import (
    Order,
    OrderStatus,
    User,
)
from app.keyboards.admin import admin_main_keyboard
from app.services.orders import order_service
from app.states.admin import AdminOrderStates
from app.utils.money import format_usd


router = Router(name="admin_orders")


def _is_admin(user_id: int) -> bool:
    """Проверяет права администратора."""
    return user_id in settings.admin_ids


def _enum_value(value: object) -> str:
    """Безопасно получает строковое значение enum."""
    return str(getattr(value, "value", value))


def _status_text(status: OrderStatus) -> str:
    """Возвращает русское название статуса заказа."""
    names = {
        OrderStatus.PAYMENT_PENDING: "Ожидает оплаты",
        OrderStatus.PAID: "Оплачен",
        OrderStatus.DELIVERED: "Доставлен",
        OrderStatus.EXPIRED: "Истёк",
        OrderStatus.CANCELLED: "Отменён",
        OrderStatus.REFUNDED: "Возвращён",
    }

    return names.get(
        status,
        _enum_value(status),
    )


def _order_text(order: Order) -> str:
    """Формирует карточку заказа."""
    created_at = (
        order.created_at.strftime("%d.%m.%Y %H:%M")
        if order.created_at
        else "—"
    )

    paid_at = (
        order.paid_at.strftime("%d.%m.%Y %H:%M")
        if order.paid_at
        else "—"
    )

    return (
        f"📦 <b>Заказ #{order.id}</b>\n\n"
        f"👤 Пользователь: "
        f"<code>{order.user_id}</code>\n"
        f"💰 Сумма: "
        f"<b>{format_usd(order.total_usd)}</b>\n"
        f"📌 Статус: "
        f"<b>{_status_text(order.status)}</b>\n"
        f"📅 Создан: "
        f"<code>{created_at}</code>\n"
        f"💵 Оплачен: "
        f"<code>{paid_at}</code>"
    )


async def _get_order(
    session: AsyncSession,
    order_id: int,
) -> Order | None:
    """Получает заказ по ID."""
    return await session.scalar(
        select(Order).where(
            Order.id == order_id
        )
    )


async def _show_order(
    callback: CallbackQuery,
    order: Order,
) -> None:
    """Показывает заказ."""
    if callback.message is not None:
        await callback.message.edit_text(
            _order_text(order),
            reply_markup=admin_main_keyboard(),
        )

    await callback.answer()


@router.message(Command("orders"))
async def orders_command(
    message: Message,
    session: AsyncSession,
) -> None:
    """Показывает последние заказы."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    try:
        result = await session.scalars(
            select(Order)
            .order_by(Order.created_at.desc())
            .limit(20)
        )

        orders = list(result)

        if not orders:
            await message.answer(
                "📦 Заказов пока нет.",
                reply_markup=admin_main_keyboard(),
            )
            return

        lines = [
            "📦 <b>Последние заказы</b>\n"
        ]

        for order in orders:
            lines.append(
                f"#{order.id} — "
                f"<b>{format_usd(order.total_usd)}</b> — "
                f"{_status_text(order.status)}\n"
                f"👤 User ID: "
                f"<code>{order.user_id}</code>\n"
            )

        await message.answer(
            "\n".join(lines),
            reply_markup=admin_main_keyboard(),
        )

    except Exception:
        logger.exception(
            "Ошибка загрузки заказов: admin={}",
            message.from_user.id,
        )
        await message.answer(
            "❌ Не удалось загрузить заказы."
        )


@router.callback_query(
    OrderAdminCB.filter(F.action == "list")
)
async def orders_list_callback(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Показывает список заказов."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        result = await session.scalars(
            select(Order)
            .order_by(Order.created_at.desc())
            .limit(20)
        )

        orders = list(result)

        if not orders:
            text = "📦 <b>Заказы</b>\n\nЗаказов пока нет."
        else:
            lines = ["📦 <b>Последние заказы</b>\n"]

            for order in orders:
                lines.append(
                    f"#{order.id} — "
                    f"<b>{format_usd(order.total_usd)}</b>\n"
                    f"Статус: "
                    f"{_status_text(order.status)}\n"
                )

            text = "\n".join(lines)

        if callback.message is not None:
            await callback.message.edit_text(
                text,
                reply_markup=admin_main_keyboard(),
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка списка заказов: admin={}",
            callback.from_user.id,
        )
        await callback.answer(
            "Ошибка загрузки заказов.",
            show_alert=True,
        )


@router.callback_query(
    OrderAdminCB.filter(F.action == "open")
)
async def order_open_callback(
    callback: CallbackQuery,
    callback_data: OrderAdminCB,
    session: AsyncSession,
) -> None:
    """Открывает конкретный заказ."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    if not callback_data.order_id:
        await callback.answer(
            "Заказ не указан.",
            show_alert=True,
        )
        return

    try:
        order = await _get_order(
            session,
            callback_data.order_id,
        )

        if order is None:
            await callback.answer(
                "Заказ не найден.",
                show_alert=True,
            )
            return

        await _show_order(
            callback,
            order,
        )

    except Exception:
        logger.exception(
            "Ошибка открытия заказа: "
            "admin={}, order_id={}",
            callback.from_user.id,
            callback_data.order_id,
        )
        await callback.answer(
            "Ошибка загрузки заказа.",
            show_alert=True,
        )


@router.callback_query(
    OrderAdminCB.filter(F.action == "deliver")
)
async def order_deliver_callback(
    callback: CallbackQuery,
    callback_data: OrderAdminCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Запрашивает данные для ручной выдачи заказа."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    order = await _get_order(
        session,
        callback_data.order_id,
    )

    if order is None:
        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )
        return

    if order.status not in (
        OrderStatus.PAID,
    ):
        await callback.answer(
            "Ручная выдача доступна для оплаченного заказа.",
            show_alert=True,
        )
        return

    await state.set_state(
        AdminOrderStates.waiting_for_delivery
    )
    await state.update_data(
        order_id=order.id,
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "📤 <b>Ручная выдача</b>\n\n"
            f"Заказ: <b>#{order.id}</b>\n"
            f"Пользователь: "
            f"<code>{order.user_id}</code>\n\n"
            "Отправьте текст/данные, которые необходимо "
            "передать пользователю.\n\n"
            "Для отмены: /cancel"
        )

    await callback.answer()


@router.message(
    AdminOrderStates.waiting_for_delivery,
    F.text,
)
async def process_delivery(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Передаёт пользователю данные ручной выдачи."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    delivery_text = message.text.strip()

    if delivery_text.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "Выдача отменена.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if not delivery_text:
        await message.answer(
            "Данные для выдачи не могут быть пустыми."
        )
        return

    data = await state.get_data()
    order_id = int(data.get("order_id", 0))

    if not order_id:
        await state.clear()
        await message.answer(
            "❌ Заказ не найден.",
            reply_markup=admin_main_keyboard(),
        )
        return

    try:
        order = await _get_order(
            session,
            order_id,
        )

        if order is None:
            await state.clear()
            await message.answer(
                "❌ Заказ не найден.",
                reply_markup=admin_main_keyboard(),
            )
            return

        if order.status != OrderStatus.PAID:
            await state.clear()
            await message.answer(
                "❌ Заказ больше не находится "
                "в статусе «Оплачен».",
                reply_markup=admin_main_keyboard(),
            )
            return

        user = await session.scalar(
            select(User).where(
                User.id == order.user_id
            )
        )

        if user is None:
            await message.answer(
                "❌ Пользователь заказа не найден."
            )
            return

        await order_service.deliver(
            session,
            order_id=order.id,
            delivery_data={"manual_text": delivery_text},
        )

        await session.flush()

        try:
            await message.bot.send_message(
                chat_id=user.telegram_id,
                text=(
                    f"✅ <b>Заказ #{order.id} выдан</b>\n\n"
                    f"{delivery_text}"
                ),
            )
        except Exception:
            logger.exception(
                "Не удалось отправить выдачу пользователю: "
                "order_id={}, telegram_id={}",
                order.id,
                user.telegram_id,
            )

            await message.answer(
                "⚠️ Заказ отмечен как выданный, "
                "но отправить сообщение пользователю "
                "не удалось. Проверьте Telegram вручную.",
                reply_markup=admin_main_keyboard(),
            )
            await state.clear()
            return

        await state.clear()

        await message.answer(
            f"✅ Заказ <b>#{order.id}</b> выдан.\n"
            "Пользователь получил сообщение.",
            reply_markup=admin_main_keyboard(),
        )

    except Exception:
        await session.rollback()

        logger.exception(
            "Ошибка ручной выдачи: "
            "admin={}, order_id={}",
            message.from_user.id,
            order_id,
        )

        await message.answer(
            "❌ Не удалось выполнить выдачу."
        )


@router.callback_query(
    OrderAdminCB.filter(F.action == "refund")
)
async def order_refund_callback(
    callback: CallbackQuery,
    callback_data: OrderAdminCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Запрашивает подтверждение возврата."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    order = await _get_order(
        session,
        callback_data.order_id,
    )

    if order is None:
        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )
        return

    if order.status not in (
        OrderStatus.PAID,
        OrderStatus.DELIVERED,
    ):
        await callback.answer(
            "Возврат невозможен для текущего статуса.",
            show_alert=True,
        )
        return

    await state.set_state(
        AdminOrderStates.waiting_for_refund_amount
    )
    await state.update_data(
        order_id=order.id,
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "↩️ <b>Возврат заказа</b>\n\n"
            f"Заказ: <b>#{order.id}</b>\n"
            f"Сумма заказа: "
            f"<b>{format_usd(order.total_usd)}</b>\n\n"
            "Напишите причину возврата или отправьте <code>-</code>.\n\n"
            "Для отмены: /cancel"
        )

    await callback.answer()


@router.message(
    AdminOrderStates.waiting_for_refund_amount,
    F.text,
)
async def process_refund_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Выполняет возврат заказа."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    reason = message.text.strip()

    if reason.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "Возврат отменён.",
            reply_markup=admin_main_keyboard(),
        )
        return

    data = await state.get_data()
    order_id = int(data.get("order_id", 0))

    try:
        order = await _get_order(session, order_id)
        if order is None:
            await state.clear()
            await message.answer("❌ Заказ не найден.", reply_markup=admin_main_keyboard())
            return

        await order_service.refund(
            session,
            order_id=order.id,
            reason=reason if reason != "-" else "Ручной возврат администратором",
            refund_balance=True,
        )

        await session.flush()
        await state.clear()

        await message.answer(
            "✅ <b>Возврат выполнен, остатки возвращены на склад.</b>\n\n"
            f"Заказ: <b>#{order.id}</b>",
            reply_markup=admin_main_keyboard(),
        )

    except Exception:
        await session.rollback()
        logger.exception("Ошибка возврата заказа: admin={}, order_id={}", message.from_user.id, order_id)
        await message.answer("❌ Не удалось выполнить возврат.")


@router.callback_query(
    OrderAdminCB.filter(F.action == "cancel")
)
async def order_cancel_callback(
    callback: CallbackQuery,
    callback_data: OrderAdminCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Отменяет заказ."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    order = await _get_order(
        session,
        callback_data.order_id,
    )

    if order is None:
        await callback.answer(
            "Заказ не найден.",
            show_alert=True,
        )
        return

    if order.status not in (
        OrderStatus.PAYMENT_PENDING,
    ):
        await callback.answer(
            "Этот заказ нельзя отменить вручную "
            "из текущего статуса.",
            show_alert=True,
        )
        return

    await state.set_state(
        AdminOrderStates.waiting_for_cancel_reason
    )
    await state.update_data(
        order_id=order.id,
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "❌ <b>Отмена заказа</b>\n\n"
            f"Заказ: <b>#{order.id}</b>\n\n"
            "Введите причину отмены.\n\n"
            "Для отмены действия: /cancel"
        )

    await callback.answer()


@router.message(
    AdminOrderStates.waiting_for_cancel_reason,
    F.text,
)
async def process_cancel_reason(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Отменяет заказ с указанной причиной."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    reason = message.text.strip()

    if reason.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "Отмена заказа прервана.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if not reason:
        await message.answer(
            "Причина не может быть пустой."
        )
        return

    data = await state.get_data()
    order_id = int(data.get("order_id", 0))

    if not order_id:
        await state.clear()
        await message.answer(
            "❌ Заказ не указан.",
            reply_markup=admin_main_keyboard(),
        )
        return

    try:
        order = await _get_order(session, order_id)
        if order is None:
            await state.clear()
            await message.answer("❌ Заказ не найден.", reply_markup=admin_main_keyboard())
            return

        await order_service.cancel(
            session,
            order_id=order.id,
            reason=reason,
        )

        await session.flush()
        await state.clear()

        await message.answer(
            "✅ <b>Заказ отменён, остатки возвращены на склад</b>\n\n"
            f"Заказ: <b>#{order.id}</b>\n"
            f"Причина: {reason}",
            reply_markup=admin_main_keyboard(),
        )

    except Exception:
        await session.rollback()
        logger.exception("Ошибка отмены заказа: admin={}, order_id={}", message.from_user.id, order_id)
        await message.answer("❌ Не удалось отменить заказ.")


@router.callback_query(
    OrderAdminCB.filter(F.action == "back")
)
async def orders_back_callback(
    callback: CallbackQuery,
) -> None:
    """Возвращает в административное меню."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    if callback.message is not None:
        await callback.message.edit_text(
            "⚙️ <b>Административная панель</b>",
            reply_markup=admin_main_keyboard(),
        )

    await callback.answer()


__all__ = ["router"]