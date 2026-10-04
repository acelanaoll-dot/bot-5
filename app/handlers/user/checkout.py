from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.callbacks.orders import OrderCB
from app.callbacks.payments import PaymentCB
from app.callbacks.user import UserCB
from app.config import settings
from app.database.models import OrderStatus
from app.keyboards.checkout import (
    checkout_keyboard,
    payment_methods_keyboard,
)
from app.keyboards.payment import (
    invoice_keyboard,
    payment_status_keyboard,
)
from app.services.balance import (
    InsufficientBalanceError,
    balance_service,
)
from app.services.cart import (
    CartServiceError,
    cart_service,
)
from app.services.orders import (
    EmptyCartError,
    InsufficientStockError,
    OrderAlreadyPaidError,
    OrderServiceError,
    order_service,
)
from app.services.payment_manager import (
    PaymentManagerError,
    payment_manager,
)
from app.utils.money import format_usd

router = Router(name="user_checkout")


# ============================================================
# Вспомогательные функции
# ============================================================


async def _get_user_balance(
    session: Any,
    user_id: int,
) -> Decimal:
    """Получить текущий баланс пользователя."""

    balance = await balance_service.get_balance(
        session,
        user_id,
    )

    return Decimal(str(balance))


def _order_text(order: Any) -> str:
    """Сформировать экран оформления заказа."""

    subtotal = Decimal(str(order.subtotal_usd))
    discount = Decimal(str(order.discount_usd))
    total = Decimal(str(order.total_usd))

    paid_balance = Decimal(
        str(order.balance_paid_usd)
    )
    paid_crypto = Decimal(
        str(order.crypto_paid_usd)
    )

    paid = paid_balance + paid_crypto
    remaining = max(
        Decimal("0"),
        total - paid,
    )

    lines = [
        "📦 <b>Оформление заказа</b>",
        "",
        f"🧾 Заказ: <code>{escape(str(order.order_number))}</code>",
        "",
        f"Сумма товаров: <b>{format_usd(subtotal)}</b>",
    ]

    if discount > 0:
        lines.append(
            f"Скидка: <b>-{format_usd(discount)}</b>"
        )

    lines.extend(
        [
            f"💵 Итого: <b>{format_usd(total)}</b>",
            "",
            f"💳 С баланса: <b>{format_usd(paid_balance)}</b>",
            f"₿ Криптовалютой: <b>{format_usd(paid_crypto)}</b>",
            f"⏳ Осталось: <b>{format_usd(remaining)}</b>",
        ]
    )

    if order.status == OrderStatus.PAID:
        lines.extend(
            [
                "",
                "✅ <b>Заказ полностью оплачен.</b>",
            ]
        )
    elif order.status == OrderStatus.DELIVERED:
        lines.extend(
            [
                "",
                "✅ <b>Заказ уже выдан.</b>",
            ]
        )
    elif order.status == OrderStatus.CANCELLED:
        lines.extend(
            [
                "",
                "❌ <b>Заказ отменён.</b>",
            ]
        )
    elif order.status == OrderStatus.EXPIRED:
        lines.extend(
            [
                "",
                "⌛ <b>Срок оплаты заказа истёк.</b>",
            ]
        )

    return "\n".join(lines)


async def _show_checkout(
    message: Message,
    session: Any,
    user_id: int,
    order_id: int,
) -> None:
    """Показать экран оформления существующего заказа."""

    order = await order_service.get_user_order(
        session,
        user_id=user_id,
        order_id=order_id,
    )

    if order.status == OrderStatus.DELIVERED:
        await message.answer(
            _order_text(order)
        )
        return

    balance = await _get_user_balance(
        session,
        user_id,
    )

    remaining = await order_service.get_remaining_amount(
        session,
        order.id,
    )

    await message.answer(
        _order_text(order),
        reply_markup=checkout_keyboard(
            order_id=order.id,
            has_balance=(
                balance > Decimal("0")
                and remaining > Decimal("0")
            ),
        ),
    )


# ============================================================
# Создание заказа из корзины
# ============================================================


@router.callback_query(
    F.data == "cart:checkout"
)
async def checkout_from_legacy_cart(
    callback: CallbackQuery,
    session: Any,
) -> None:
    """
    Совместимость со старой callback-строкой.

    Основной путь использует CartCB в cart.py.
    """

    await _create_order_from_cart(
        callback,
        session,
    )


async def _create_order_from_cart(
    callback: CallbackQuery,
    session: Any,
) -> None:
    """Создать заказ из текущей корзины."""

    try:
        order = await order_service.create_from_cart(
            session,
            user_id=callback.from_user.id,
        )

    except EmptyCartError:
        await callback.answer(
            "🛒 Корзина пуста.",
            show_alert=True,
        )
        return

    except InsufficientStockError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except CartServiceError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except OrderServiceError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка создания заказа: user_id={}",
            callback.from_user.id,
        )

        await callback.answer(
            "⚠️ Не удалось создать заказ.",
            show_alert=True,
        )
        return

    await callback.answer(
        "✅ Заказ создан."
    )

    if callback.message is None:
        return

    await _show_checkout(
        callback.message,
        session,
        callback.from_user.id,
        order.id,
    )


# ============================================================
# Открытие заказа
# ============================================================


@router.callback_query(
    OrderCB.filter(F.action == "checkout")
)
async def order_checkout(
    callback: CallbackQuery,
    callback_data: OrderCB,
    session: Any,
) -> None:
    """Открыть оформление заказа."""

    await callback.answer()

    if callback.message is None:
        return

    try:
        await _show_checkout(
            callback.message,
            session,
            callback.from_user.id,
            callback_data.order_id,
        )

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия checkout: "
            "user_id={}, order_id={}",
            callback.from_user.id,
            callback_data.order_id,
        )

        await callback.message.answer(
            "⚠️ Не удалось открыть заказ."
        )


# ============================================================
# Оплата внутренним балансом
# ============================================================


@router.callback_query(
    PaymentCB.filter(F.action == "balance")
)
async def pay_from_balance(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: Any,
) -> None:
    """Оплатить заказ доступным внутренним балансом."""

    order_id = callback_data.order_id

    try:
        order = await order_service.get_user_order(
            session,
            user_id=callback.from_user.id,
            order_id=order_id,
        )

        remaining = await order_service.get_remaining_amount(
            session,
            order_id,
        )

        if remaining <= Decimal("0"):
            await callback.answer(
                "✅ Заказ уже оплачен.",
                show_alert=True,
            )
            return

        balance = await _get_user_balance(
            session,
            callback.from_user.id,
        )

        if balance <= Decimal("0"):
            await callback.answer(
                "❌ На балансе нет средств.",
                show_alert=True,
            )
            return

        amount = min(
            balance,
            remaining,
        )

        if amount <= Decimal("0"):
            await callback.answer(
                "❌ Недостаточно средств.",
                show_alert=True,
            )
            return

        idempotency_key = (
            f"order:{order.id}:balance:"
            f"{amount}"
        )

        result = await balance_service.debit(
            session,
            user_id=callback.from_user.id,
            amount_usd=amount,
            description=(
                f"Оплата заказа "
                f"{order.order_number}"
            ),
            idempotency_key=idempotency_key,
            order_id=order.id,
            source_type="order",
            source_id=order.id,
        )

        if not result.already_processed:
            await order_service.add_balance_payment(
                session,
                order_id=order.id,
                amount_usd=amount,
            )

    except InsufficientBalanceError:
        await callback.answer(
            "❌ Недостаточно средств на балансе.",
            show_alert=True,
        )
        return

    except OrderAlreadyPaidError:
        await callback.answer(
            "✅ Заказ уже оплачен.",
            show_alert=True,
        )
        return

    except OrderServiceError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка оплаты балансом: "
            "user_id={}, order_id={}",
            callback.from_user.id,
            order_id,
        )

        await callback.answer(
            "⚠️ Не удалось списать средства.",
            show_alert=True,
        )
        return

    await callback.answer(
        "✅ Оплата балансом выполнена."
    )

    if callback.message is None:
        return

    try:
        await _show_checkout(
            callback.message,
            session,
            callback.from_user.id,
            order_id,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка обновления checkout после оплаты: "
            "order_id={}",
            order_id,
        )


# ============================================================
# Создание криптоплатежа
# ============================================================


@router.callback_query(
    PaymentCB.filter(F.action == "create")
)
async def create_crypto_payment(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: Any,
) -> None:
    """Создать криптоплатёж на оставшуюся сумму."""

    order_id = callback_data.order_id

    try:
        order = await order_service.get_user_order(
            session,
            user_id=callback.from_user.id,
            order_id=order_id,
        )

        remaining = await order_service.get_remaining_amount(
            session,
            order_id,
        )

        if remaining <= Decimal("0"):
            await callback.answer(
                "✅ Заказ уже полностью оплачен.",
                show_alert=True,
            )
            return

        payment_result = (
            await payment_manager.create_order_payment(
                session,
                order_id=order.id,
                user_id=callback.from_user.id,
                amount_usd=remaining,
                method=callback_data.method or "USDT",
            )
        )

    except OrderAlreadyPaidError:
        await callback.answer(
            "✅ Заказ уже оплачен.",
            show_alert=True,
        )
        return

    except PaymentManagerError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except OrderServiceError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка создания криптоплатежа: "
            "user_id={}, order_id={}",
            callback.from_user.id,
            order_id,
        )

        await callback.answer(
            "⚠️ Не удалось создать платёж.",
            show_alert=True,
        )
        return

    await callback.answer(
        "💳 Счёт создан."
    )

    if callback.message is None:
        return

    payment_id = getattr(
        payment_result,
        "payment_id",
        None,
    )

    payment_url = getattr(
        payment_result,
        "payment_url",
        None,
    )

    amount_usd = getattr(
        payment_result,
        "amount_usd",
        remaining,
    )

    text = (
        "💳 <b>Оплата криптовалютой</b>\n\n"
        f"Заказ: <code>{escape(str(order.order_number))}</code>\n"
        f"Сумма: <b>{format_usd(Decimal(str(amount_usd)))}</b>\n\n"
        "Откройте счёт и выполните оплату. "
        "После этого нажмите «Проверить оплату»."
    )

    markup = None

    if payment_id is not None:
        markup = invoice_keyboard(
            payment_id=int(payment_id),
            payment_url=payment_url,
        )

    await callback.message.answer(
        text,
        reply_markup=markup,
    )


# ============================================================
# Проверка платежа
# ============================================================


@router.callback_query(
    PaymentCB.filter(F.action == "check")
)
async def check_payment(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: Any,
) -> None:
    """Проверить состояние криптоплатежа."""

    payment_id = callback_data.payment_id

    if payment_id <= 0:
        await callback.answer(
            "❌ Некорректный платёж.",
            show_alert=True,
        )
        return

    try:
        result = await payment_manager.poll_payment(
            session,
            payment_id=payment_id,
        )

    except PaymentManagerError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка проверки платежа: "
            "user_id={}, payment_id={}",
            callback.from_user.id,
            payment_id,
        )

        await callback.answer(
            "⚠️ Не удалось проверить платёж.",
            show_alert=True,
        )
        return

    await callback.answer(
        "🔄 Статус обновлён."
    )

    if callback.message is None:
        return

    status = getattr(
        result,
        "status",
        None,
    )

    status_text = str(
        getattr(
            status,
            "value",
            status or "unknown",
        )
    )

    if status_text in {
        "paid",
        "completed",
    }:
        await callback.message.answer(
            "✅ <b>Оплата подтверждена.</b>\n\n"
            "Заказ передан на дальнейшую обработку."
        )
        return

    await callback.message.answer(
        "⏳ <b>Платёж пока не подтверждён.</b>\n\n"
        "Если вы уже оплатили счёт, "
        "подождите немного и повторите проверку.",
        reply_markup=payment_status_keyboard(
            payment_id=payment_id,
            order_id=callback_data.order_id,
        ),
    )


# ============================================================
# Обновление статуса заказа
# ============================================================


@router.callback_query(
    PaymentCB.filter(F.action == "refresh")
)
async def refresh_payment(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: Any,
) -> None:
    """Обновить состояние заказа/платежа."""

    await callback.answer(
        "🔄 Обновляю..."
    )

    if callback_data.payment_id > 0:
        try:
            await payment_manager.poll_payment(
                session,
                payment_id=callback_data.payment_id,
            )
        except Exception:
            from loguru import logger

            logger.exception(
                "Ошибка refresh payment: "
                "payment_id={}",
                callback_data.payment_id,
            )

    if callback.message is None:
        return

    try:
        await _show_checkout(
            callback.message,
            session,
            callback.from_user.id,
            callback_data.order_id,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка refresh checkout: order_id={}",
            callback_data.order_id,
        )


# ============================================================
# Отмена заказа
# ============================================================


@router.callback_query(
    PaymentCB.filter(F.action == "cancel_order")
)
async def cancel_order(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: Any,
) -> None:
    """Отменить неоплаченный заказ."""

    try:
        order = await order_service.get_user_order(
            session,
            user_id=callback.from_user.id,
            order_id=callback_data.order_id,
        )

        if order.status in {
            OrderStatus.PAID,
            OrderStatus.DELIVERED,
        }:
            await callback.answer(
                "❌ Оплаченный заказ нельзя отменить.",
                show_alert=True,
            )
            return

        await order_service.cancel(
            session,
            order_id=order.id,
            reason="Отменён пользователем.",
        )

    except OrderServiceError as exc:
        await callback.answer(
            f"❌ {exc}",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка отмены заказа: "
            "user_id={}, order_id={}",
            callback.from_user.id,
            callback_data.order_id,
        )

        await callback.answer(
            "⚠️ Не удалось отменить заказ.",
            show_alert=True,
        )
        return

    await callback.answer(
        "❌ Заказ отменён."
    )

    if callback.message is not None:
        await callback.message.answer(
            "❌ <b>Заказ отменён.</b>"
        )


# ============================================================
# Возврат к способам оплаты
# ============================================================


@router.callback_query(
    PaymentCB.filter(F.action == "back")
)
async def payment_back(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: Any,
) -> None:
    """Вернуться к экрану оформления."""

    await callback.answer()

    if callback.message is None:
        return

    try:
        await _show_checkout(
            callback.message,
            session,
            callback.from_user.id,
            callback_data.order_id,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка возврата из оплаты: order_id={}",
            callback_data.order_id,
        )


# ============================================================
# Выбор способа оплаты
# ============================================================


@router.callback_query(
    PaymentCB.filter(F.action == "methods")
)
async def payment_methods(
    callback: CallbackQuery,
    callback_data: PaymentCB,
    session: Any,
) -> None:
    """Показать доступные способы оплаты."""

    await callback.answer()

    if callback.message is None:
        return

    try:
        balance = await _get_user_balance(
            session,
            callback.from_user.id,
        )

        remaining = await order_service.get_remaining_amount(
            session,
            callback_data.order_id,
        )

        await callback.message.answer(
            "💳 <b>Способ оплаты</b>\n\n"
            f"К оплате: <b>{format_usd(remaining)}</b>\n"
            f"Баланс: <b>{format_usd(balance)}</b>",
            reply_markup=payment_methods_keyboard(
                order_id=callback_data.order_id,
                balance_available=(
                    balance > Decimal("0")
                    and remaining > Decimal("0")
                ),
            ),
        )

    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия способов оплаты: "
            "user_id={}, order_id={}",
            callback.from_user.id,
            callback_data.order_id,
        )

        await callback.message.answer(
            "⚠️ Не удалось загрузить способы оплаты."
        )


# ============================================================
# Экспорт
# ============================================================


__all__ = [
    "router",
]