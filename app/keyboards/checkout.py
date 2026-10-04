from __future__ import annotations

from decimal import Decimal
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks.cart import CartCB
from app.callbacks.orders import OrderCB
from app.callbacks.payments import PaymentCB
from app.callbacks.user import UserCB


def checkout_keyboard(
    order_id: int,
    has_balance: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура оформления заказа."""

    builder = InlineKeyboardBuilder()

    if has_balance:
        builder.row(
            InlineKeyboardButton(
                text="💰 Оплатить с баланса",
                callback_data=PaymentCB(
                    action="balance",
                    order_id=order_id,
                ),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="💳 Способы оплаты",
            callback_data=PaymentCB(
                action="methods",
                order_id=order_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=PaymentCB(
                action="refresh",
                order_id=order_id,
            ),
        ),
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=PaymentCB(
                action="cancel_order",
                order_id=order_id,
            ),
        ),
    )

    builder.row(
        InlineKeyboardButton(
            text="📦 Мои заказы",
            callback_data=OrderCB(
                action="list",
            ),
        ),
        InlineKeyboardButton(
            text="🏠 Меню",
            callback_data=UserCB(
                action="home",
            ),
        ),
    )

    return builder.as_markup()


def payment_methods_keyboard(
    order_id: int,
    balance_available: bool = False,
) -> InlineKeyboardMarkup:
    """Выбор способа оплаты."""

    builder = InlineKeyboardBuilder()

    if balance_available:
        builder.row(
            InlineKeyboardButton(
                text="💰 Внутренний баланс",
                callback_data=PaymentCB(
                    action="balance",
                    order_id=order_id,
                ),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="💎 USDT",
            callback_data=PaymentCB(
                action="create",
                method="USDT",
                order_id=order_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Обновить",
            callback_data=PaymentCB(
                action="refresh",
                order_id=order_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=PaymentCB(
                action="back",
                order_id=order_id,
            ),
        )
    )

    return builder.as_markup()


def checkout_back_keyboard(
    order_id: int,
) -> InlineKeyboardMarkup:
    """Кнопка возврата к оформлению."""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад к заказу",
            callback_data=PaymentCB(
                action="back",
                order_id=order_id,
            ),
        )
    )

    return builder.as_markup()


__all__ = [
    "checkout_keyboard",
    "payment_methods_keyboard",
    "checkout_back_keyboard",
]