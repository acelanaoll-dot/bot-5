from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.callbacks.cart import CartCB
from app.callbacks.orders import OrderCB
from app.callbacks.user import UserCB
from app.keyboards.cart import (
    cart_clear_confirm_keyboard,
    cart_item_keyboard,
    cart_keyboard,
    empty_cart_keyboard,
)
from app.services.cart import (
    CartItemNotFoundError,
    CartOwnershipError,
    CartServiceError,
    InvalidCartQuantityError,
    ProductUnavailableError,
    cart_service,
)
from app.utils.money import format_usd

router = Router(name="user_cart")


# ============================================================
# Вспомогательные функции
# ============================================================


def _format_cart_item(item: Any) -> str:
    """Сформировать строку одной позиции корзины."""

    product = item.product

    name = escape(str(product.name))
    quantity = int(item.quantity)
    price = Decimal(str(product.price_usd))
    total = price * quantity

    return (
        f"• <b>{name}</b>\n"
        f"  {format_usd(price)} × {quantity} = "
        f"<b>{format_usd(total)}</b>"
    )


def _format_cart_text(
    items: list[Any],
    subtotal: Decimal,
    total_quantity: int,
) -> str:
    """Сформировать содержимое корзины."""

    if not items:
        return (
            "🛒 <b>Корзина пуста</b>\n\n"
            "Добавьте товары из каталога."
        )

    lines = [
        "🛒 <b>Ваша корзина</b>",
        "",
    ]

    for item in items:
        lines.append(
            _format_cart_item(item)
        )
        lines.append("")

    lines.extend(
        [
            f"📦 Количество: <b>{total_quantity}</b>",
            f"💵 Итого: <b>{format_usd(subtotal)}</b>",
        ]
    )

    return "\n".join(lines)


async def _load_cart(
    session: Any,
    user_id: int,
) -> tuple[list[Any], Decimal, int]:
    """Получить актуальное содержимое корзины."""

    items = await cart_service.get_cart(
        session,
        user_id,
    )

    subtotal = await cart_service.calculate_subtotal(
        session,
        user_id,
    )

    total_quantity = await cart_service.calculate_total_quantity(
        session,
        user_id,
    )

    return (
        items,
        Decimal(str(subtotal)),
        int(total_quantity),
    )


async def _show_cart(
    message: Message,
    session: Any,
    user_id: int,
) -> None:
    """Показать корзину."""

    items, subtotal, total_quantity = await _load_cart(
        session,
        user_id,
    )

    text = _format_cart_text(
        items,
        subtotal,
        total_quantity,
    )

    if not items:
        await message.answer(
            text,
            reply_markup=empty_cart_keyboard(),
        )
        return

    await message.answer(
        text,
        reply_markup=cart_keyboard(
            items=items,
            subtotal=subtotal,
        ),
    )


async def _show_cart_item(
    callback: CallbackQuery,
    session: Any,
    cart_item_id: int,
) -> None:
    """Обновить отображение конкретной позиции."""

    if callback.message is None:
        return

    item = await cart_service.get_item(
        session,
        cart_item_id,
    )

    if item is None:
        await callback.message.answer(
            "❌ Позиция корзины не найдена."
        )
        return

    product = item.product

    if product is None:
        await callback.message.answer(
            "❌ Товар больше недоступен."
        )
        return

    price = Decimal(str(product.price_usd))
    total = price * int(item.quantity)

    text = (
        "🛒 <b>Позиция корзины</b>\n\n"
        f"<b>{escape(str(product.name))}</b>\n"
        f"Цена: {format_usd(price)}\n"
        f"Количество: <b>{item.quantity}</b>\n"
        f"Сумма: <b>{format_usd(total)}</b>"
    )

    await callback.message.answer(
        text,
        reply_markup=cart_item_keyboard(
            item=item,
        ),
    )


# ============================================================
# Открытие корзины
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "open")
)
async def cart_open_callback(
    callback: CallbackQuery,
    session: Any,
) -> None:
    """Открыть корзину."""

    await callback.answer()

    if callback.message is None:
        return

    try:
        await _show_cart(
            callback.message,
            session,
            callback.from_user.id,
        )

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия корзины: user_id={}",
            callback.from_user.id,
        )

        await callback.message.answer(
            "⚠️ Не удалось открыть корзину."
        )


@router.callback_query(
    UserCB.filter(F.action == "cart")
)
async def cart_open_from_menu(
    callback: CallbackQuery,
    session: Any,
) -> None:
    """Открыть корзину из главного меню."""

    await callback.answer()

    if callback.message is None:
        return

    try:
        await _show_cart(
            callback.message,
            session,
            callback.from_user.id,
        )

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия корзины из меню: user_id={}",
            callback.from_user.id,
        )

        await callback.message.answer(
            "⚠️ Не удалось открыть корзину."
        )


# ============================================================
# Обновление корзины
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "refresh")
)
async def cart_refresh(
    callback: CallbackQuery,
    session: Any,
) -> None:
    """Обновить корзину."""

    await callback.answer(
        "🔄 Корзина обновлена."
    )

    if callback.message is None:
        return

    try:
        await _show_cart(
            callback.message,
            session,
            callback.from_user.id,
        )

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка обновления корзины: user_id={}",
            callback.from_user.id,
        )

        await callback.message.answer(
            "⚠️ Не удалось обновить корзину."
        )


# ============================================================
# Добавление товара
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "add")
)
async def cart_add_product(
    callback: CallbackQuery,
    callback_data: CartCB,
    session: Any,
) -> None:
    """Добавить товар в корзину."""

    try:
        await cart_service.add_item(
            session,
            user_id=callback.from_user.id,
            product_id=callback_data.product_id,
            quantity=max(callback_data.quantity, 1),
        )

    except ProductUnavailableError:
        await callback.answer(
            "❌ Товар закончился или недоступен.",
            show_alert=True,
        )
        return

    except InvalidCartQuantityError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except CartServiceError:
        from loguru import logger

        logger.exception(
            "Ошибка добавления товара: "
            "user_id={}, product_id={}",
            callback.from_user.id,
            callback_data.product_id,
        )

        await callback.answer(
            "⚠️ Не удалось добавить товар.",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Неожиданная ошибка добавления товара: "
            "user_id={}, product_id={}",
            callback.from_user.id,
            callback_data.product_id,
        )

        await callback.answer(
            "⚠️ Произошла ошибка.",
            show_alert=True,
        )
        return

    await callback.answer(
        "✅ Товар добавлен в корзину."
    )


# ============================================================
# Увеличение количества
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "increase")
)
async def cart_increase(
    callback: CallbackQuery,
    callback_data: CartCB,
    session: Any,
) -> None:
    """Увеличить количество товара."""

    try:
        item = await cart_service.increase_quantity(
            session,
            user_id=callback.from_user.id,
            cart_item_id=callback_data.cart_item_id,
        )

    except ProductUnavailableError:
        await callback.answer(
            "❌ Товар закончился или недоступен.",
            show_alert=True,
        )
        return

    except CartItemNotFoundError:
        await callback.answer(
            "❌ Позиция уже удалена.",
            show_alert=True,
        )
        return

    except (
        CartOwnershipError,
        InvalidCartQuantityError,
    ) as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except CartServiceError:
        from loguru import logger

        logger.exception(
            "Ошибка увеличения позиции: "
            "user_id={}, cart_item_id={}",
            callback.from_user.id,
            callback_data.cart_item_id,
        )

        await callback.answer(
            "⚠️ Не удалось изменить количество.",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Неожиданная ошибка увеличения позиции: "
            "user_id={}, cart_item_id={}",
            callback.from_user.id,
            callback_data.cart_item_id,
        )

        await callback.answer(
            "⚠️ Произошла ошибка.",
            show_alert=True,
        )
        return

    await callback.answer(
        f"Количество: {item.quantity}"
    )


# ============================================================
# Уменьшение количества
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "decrease")
)
async def cart_decrease(
    callback: CallbackQuery,
    callback_data: CartCB,
    session: Any,
) -> None:
    """Уменьшить количество товара."""

    try:
        item = await cart_service.decrease_quantity(
            session,
            user_id=callback.from_user.id,
            cart_item_id=callback_data.cart_item_id,
        )

    except CartItemNotFoundError:
        await callback.answer(
            "❌ Позиция уже удалена.",
            show_alert=True,
        )
        return

    except CartOwnershipError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except CartServiceError:
        from loguru import logger

        logger.exception(
            "Ошибка уменьшения позиции: "
            "user_id={}, cart_item_id={}",
            callback.from_user.id,
            callback_data.cart_item_id,
        )

        await callback.answer(
            "⚠️ Не удалось изменить количество.",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Неожиданная ошибка уменьшения позиции: "
            "user_id={}, cart_item_id={}",
            callback.from_user.id,
            callback_data.cart_item_id,
        )

        await callback.answer(
            "⚠️ Произошла ошибка.",
            show_alert=True,
        )
        return

    if item is None:
        await callback.answer(
            "🗑 Позиция удалена."
        )
        return

    await callback.answer(
        f"Количество: {item.quantity}"
    )


# ============================================================
# Удаление позиции
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "remove")
)
async def cart_remove(
    callback: CallbackQuery,
    callback_data: CartCB,
    session: Any,
) -> None:
    """Удалить позицию из корзины."""

    try:
        await cart_service.remove_item(
            session,
            user_id=callback.from_user.id,
            cart_item_id=callback_data.cart_item_id,
        )

    except CartItemNotFoundError:
        await callback.answer(
            "❌ Позиция уже удалена.",
            show_alert=True,
        )
        return

    except CartOwnershipError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except CartServiceError:
        from loguru import logger

        logger.exception(
            "Ошибка удаления позиции: "
            "user_id={}, cart_item_id={}",
            callback.from_user.id,
            callback_data.cart_item_id,
        )

        await callback.answer(
            "⚠️ Не удалось удалить товар.",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Неожиданная ошибка удаления позиции: "
            "user_id={}, cart_item_id={}",
            callback.from_user.id,
            callback_data.cart_item_id,
        )

        await callback.answer(
            "⚠️ Произошла ошибка.",
            show_alert=True,
        )
        return

    await callback.answer(
        "🗑 Товар удалён из корзины."
    )

    if callback.message is None:
        return

    try:
        await _show_cart(
            callback.message,
            session,
            callback.from_user.id,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка обновления корзины после удаления: "
            "user_id={}",
            callback.from_user.id,
        )


# ============================================================
# Очистка корзины
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "clear")
)
async def cart_clear_request(
    callback: CallbackQuery,
) -> None:
    """Запросить подтверждение очистки корзины."""

    await callback.answer()

    if callback.message is None:
        return

    await callback.message.answer(
        "⚠️ <b>Очистить корзину?</b>\n\n"
        "Все товары будут удалены из корзины.",
        reply_markup=cart_clear_confirm_keyboard(),
    )


@router.callback_query(
    CartCB.filter(F.action == "clear_confirm")
)
async def cart_clear_confirm(
    callback: CallbackQuery,
    session: Any,
) -> None:
    """Подтвердить очистку корзины."""

    try:
        await cart_service.clear_cart(
            session,
            callback.from_user.id,
        )

    except CartServiceError:
        from loguru import logger

        logger.exception(
            "Ошибка очистки корзины: user_id={}",
            callback.from_user.id,
        )

        await callback.answer(
            "⚠️ Не удалось очистить корзину.",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Неожиданная ошибка очистки корзины: user_id={}",
            callback.from_user.id,
        )

        await callback.answer(
            "⚠️ Произошла ошибка.",
            show_alert=True,
        )
        return

    await callback.answer(
        "🗑 Корзина очищена."
    )

    if callback.message is None:
        return

    await callback.message.answer(
        "🛒 <b>Корзина пуста</b>\n\n"
        "Добавьте товары из каталога.",
        reply_markup=empty_cart_keyboard(),
    )


@router.callback_query(
    CartCB.filter(F.action == "clear_cancel")
)
async def cart_clear_cancel(
    callback: CallbackQuery,
) -> None:
    """Отменить очистку корзины."""

    await callback.answer(
        "Отменено."
    )


# ============================================================
# Переход к оформлению
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "checkout")
)
async def cart_checkout(
    callback: CallbackQuery,
    callback_data: CartCB,
    session: Any,
) -> None:
    """
    Перейти к оформлению заказа.

    Сам заказ создаёт checkout/order-слой.
    Здесь только проверяем, что корзина не пуста.
    """

    try:
        items = await cart_service.get_cart(
            session,
            callback.from_user.id,
        )

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка проверки корзины перед checkout: user_id={}",
            callback.from_user.id,
        )

        await callback.answer(
            "⚠️ Не удалось проверить корзину.",
            show_alert=True,
        )
        return

    if not items:
        await callback.answer(
            "🛒 Корзина пуста.",
            show_alert=True,
        )
        return

    await callback.answer()

    if callback.message is None:
        return

    await callback.message.answer(
        "📦 Переходим к оформлению заказа…"
    )

    # Передаём управление checkout-обработчику.
    # Фактическое создание заказа выполняется там,
    # чтобы не дублировать бизнес-логику.
    await callback.message.answer(
        "Нажмите кнопку оформления заказа в следующем сообщении."
    )


# ============================================================
# Открытие конкретной позиции
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "item")
)
async def cart_item(
    callback: CallbackQuery,
    callback_data: CartCB,
    session: Any,
) -> None:
    """Открыть конкретную позицию корзины."""

    try:
        item = await cart_service.get_item(
            session,
            callback_data.cart_item_id,
        )

        if item is None:
            await callback.answer(
                "❌ Позиция не найдена.",
                show_alert=True,
            )
            return

        if item.user_id != callback.from_user.id:
            await callback.answer(
                "❌ Нет доступа к этой позиции.",
                show_alert=True,
            )
            return

        await callback.answer()

        await _show_cart_item(
            callback,
            session,
            callback_data.cart_item_id,
        )

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия позиции корзины: "
            "user_id={}, cart_item_id={}",
            callback.from_user.id,
            callback_data.cart_item_id,
        )

        await callback.answer(
            "⚠️ Не удалось открыть позицию.",
            show_alert=True,
        )


# ============================================================
# Назад
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "back")
)
async def cart_back(
    callback: CallbackQuery,
) -> None:
    """Вернуться из корзины."""

    await callback.answer()

    if callback.message is None:
        return

    await callback.message.answer(
        "🏠 Главное меню",
        reply_markup=None,
    )


# ============================================================
# Заказы
# ============================================================


@router.callback_query(
    CartCB.filter(F.action == "orders")
)
async def cart_orders(
    callback: CallbackQuery,
) -> None:
    """Перейти к заказам."""

    await callback.answer()

    if callback.message is None:
        return

    await callback.message.answer(
        "📦 Открываю ваши заказы…",
    )


# ============================================================
# Экспорт
# ============================================================


__all__ = [
    "router",
]