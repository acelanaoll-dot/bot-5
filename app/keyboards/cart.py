from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks.cart import CartCB
from app.callbacks.catalog import CatalogCB
from app.callbacks.orders import OrderCB
from app.callbacks.user import UserCB
from app.database.models import CartItem


def cart_keyboard(
    items: Sequence[CartItem],
    *,
    page: int = 1,
) -> InlineKeyboardMarkup:
    """
    Основная клавиатура корзины.

    Для каждой позиции доступны увеличение, уменьшение
    и удаление. Все значения количества дополнительно
    проверяются в сервисе корзины.
    """

    builder = InlineKeyboardBuilder()

    for item in items:
        product = item.product

        if product is None:
            continue

        quantity = max(1, int(item.quantity))

        builder.button(
            text=f"➖ {product.name}",
            callback_data=CartCB(
                action="decrease",
                cart_item_id=item.id,
                product_id=product.id,
                quantity=quantity,
                page=page,
            ).pack(),
        )

        builder.button(
            text=f"{quantity} шт.",
            callback_data=CartCB(
                action="refresh",
                cart_item_id=item.id,
                product_id=product.id,
                quantity=quantity,
                page=page,
            ).pack(),
        )

        builder.button(
            text="➕",
            callback_data=CartCB(
                action="increase",
                cart_item_id=item.id,
                product_id=product.id,
                quantity=quantity,
                page=page,
            ).pack(),
        )

        builder.button(
            text="🗑 Удалить",
            callback_data=CartCB(
                action="remove",
                cart_item_id=item.id,
                product_id=product.id,
                quantity=quantity,
                page=page,
            ).pack(),
        )

    if items:
        builder.button(
            text="🧹 Очистить корзину",
            callback_data=CartCB(
                action="clear",
                page=page,
            ).pack(),
        )

        builder.button(
            text="💳 Оформить заказ",
            callback_data=CartCB(
                action="checkout",
                page=page,
            ).pack(),
        )

    builder.button(
        text="🛍 Каталог",
        callback_data=CatalogCB(
            action="categories",
        ).pack(),
    )

    builder.button(
        text="📦 Мои заказы",
        callback_data=OrderCB(
            action="list",
        ).pack(),
    )

    builder.button(
        text="🏠 Главное меню",
        callback_data=UserCB(
            action="home",
        ).pack(),
    )

    if items:
        builder.adjust(3, 1, 1, 1, 1)
    else:
        builder.adjust(1, 1, 1)

    return builder.as_markup()


def cart_item_keyboard(
    item: CartItem,
    *,
    page: int = 1,
) -> InlineKeyboardMarkup:
    """Клавиатура отдельной позиции корзины."""

    builder = InlineKeyboardBuilder()

    product_id = (
        item.product.id
        if item.product is not None
        else 0
    )

    quantity = max(1, int(item.quantity))

    builder.button(
        text="➖",
        callback_data=CartCB(
            action="decrease",
            cart_item_id=item.id,
            product_id=product_id,
            quantity=quantity,
            page=page,
        ).pack(),
    )

    builder.button(
        text=f"{quantity} шт.",
        callback_data=CartCB(
            action="refresh",
            cart_item_id=item.id,
            product_id=product_id,
            quantity=quantity,
            page=page,
        ).pack(),
    )

    builder.button(
        text="➕",
        callback_data=CartCB(
            action="increase",
            cart_item_id=item.id,
            product_id=product_id,
            quantity=quantity,
            page=page,
        ).pack(),
    )

    builder.button(
        text="🗑 Удалить",
        callback_data=CartCB(
            action="remove",
            cart_item_id=item.id,
            product_id=product_id,
            quantity=quantity,
            page=page,
        ).pack(),
    )

    builder.button(
        text="⬅️ Корзина",
        callback_data=CartCB(
            action="open",
            page=page,
        ).pack(),
    )

    builder.adjust(3, 1, 1)

    return builder.as_markup()


def empty_cart_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура пустой корзины."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🛍 Открыть каталог",
        callback_data=CatalogCB(
            action="categories",
        ).pack(),
    )

    builder.button(
        text="📦 Мои заказы",
        callback_data=OrderCB(
            action="list",
        ).pack(),
    )

    builder.button(
        text="🏠 Главное меню",
        callback_data=UserCB(
            action="home",
        ).pack(),
    )

    builder.adjust(1)

    return builder.as_markup()


def cart_clear_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение очистки корзины."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Да, очистить",
        callback_data=CartCB(
            action="clear",
        ).pack(),
    )

    builder.button(
        text="❌ Отмена",
        callback_data=CartCB(
            action="open",
        ).pack(),
    )

    builder.adjust(1)

    return builder.as_markup()


__all__ = [
    "cart_keyboard",
    "cart_item_keyboard",
    "empty_cart_keyboard",
    "cart_clear_confirm_keyboard",
]