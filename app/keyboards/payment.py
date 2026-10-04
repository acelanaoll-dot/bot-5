from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks.payments import (
    PaymentAdminCB,
    PaymentCB,
    TopupCB,
)
from app.config import settings


def invoice_keyboard(
    payment_id: int,
    *,
    order_id: int = 0,
    payment_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура криптовалютного счёта."""

    builder = InlineKeyboardBuilder()

    if payment_url:
        builder.row(
            InlineKeyboardButton(
                text="💳 Открыть счёт",
                url=payment_url,
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Проверить оплату",
            callback_data=PaymentCB(
                action="check",
                payment_id=payment_id,
                order_id=order_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=PaymentCB(
                action="back",
                payment_id=payment_id,
                order_id=order_id,
            ),
        ),
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=PaymentCB(
                action="cancel_order",
                payment_id=payment_id,
                order_id=order_id,
            ),
        ),
    )

    return builder.as_markup()


def payment_status_keyboard(
    payment_id: int,
    order_id: int = 0,
) -> InlineKeyboardMarkup:
    """Клавиатура проверки статуса платежа."""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🔄 Проверить ещё раз",
            callback_data=PaymentCB(
                action="check",
                payment_id=payment_id,
                order_id=order_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ К заказу",
            callback_data=PaymentCB(
                action="back",
                payment_id=payment_id,
                order_id=order_id,
            ),
        )
    )

    return builder.as_markup()


# ============================================================
# Пополнение баланса
# ============================================================


def topup_menu_keyboard(
    *,
    enabled: bool = True,
) -> InlineKeyboardMarkup:
    """Главное меню пополнения."""

    builder = InlineKeyboardBuilder()

    if enabled:
        builder.row(
            InlineKeyboardButton(
                text="💰 Пополнить баланс",
                callback_data=TopupCB(
                    action="start",
                ),
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="📜 История пополнений",
            callback_data=TopupCB(
                action="history",
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="📖 Как пополнить",
            callback_data=TopupCB(
                action="guide",
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🆘 Поддержка",
            callback_data=TopupCB(
                action="support",
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=TopupCB(
                action="back",
            ),
        )
    )

    return builder.as_markup()


def topup_amount_keyboard(
    *,
    amounts: Iterable[Decimal | int | float] | None = None,
) -> InlineKeyboardMarkup:
    """Выбор суммы пополнения."""

    builder = InlineKeyboardBuilder()

    if amounts is None:
        amounts = (
            Decimal("5"),
            Decimal("10"),
            Decimal("25"),
            Decimal("50"),
            Decimal("100"),
        )

    for raw_amount in amounts:
        amount = Decimal(str(raw_amount))

        if amount <= 0:
            continue

        cents = int(
            amount.quantize(Decimal("0.01"))
            * 100
        )

        builder.button(
            text=f"${amount:.2f}",
            callback_data=TopupCB(
                action="amount",
                amount_cents=cents,
            ),
        )

    builder.adjust(2)

    builder.row(
        InlineKeyboardButton(
            text="✏️ Другая сумма",
            callback_data=TopupCB(
                action="custom_amount",
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=TopupCB(
                action="back",
            ),
        )
    )

    return builder.as_markup()


def topup_asset_keyboard(
    assets: Iterable[str] | None = None,
) -> InlineKeyboardMarkup:
    """Выбор криптоактива."""

    builder = InlineKeyboardBuilder()

    if assets is None:
        configured = settings.supported_crypto_currencies

        if isinstance(configured, str):
            assets = (
                item.strip().upper()
                for item in configured.split(",")
                if item.strip()
            )
        else:
            assets = configured

    for asset in assets:
        normalized = str(asset).strip().upper()

        if not normalized:
            continue

        builder.button(
            text=f"💎 {normalized}",
            callback_data=TopupCB(
                action="asset",
                asset=normalized,
            ),
        )

    builder.adjust(2)

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=TopupCB(
                action="back",
            ),
        )
    )

    return builder.as_markup()


def topup_payment_keyboard(
    *,
    amount_cents: int = 0,
    asset: str = "",
) -> InlineKeyboardMarkup:
    """Подтверждение создания пополнения."""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="💳 Создать счёт",
            callback_data=TopupCB(
                action="create",
                amount_cents=amount_cents,
                asset=asset,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=TopupCB(
                action="back",
            ),
        )
    )

    return builder.as_markup()


def topup_invoice_keyboard(
    topup_id: int,
    payment_id: int = 0,
    *,
    payment_url: str | None = None,
) -> InlineKeyboardMarkup:
    """Клавиатура счёта пополнения."""

    builder = InlineKeyboardBuilder()

    if payment_url:
        builder.row(
            InlineKeyboardButton(
                text="💳 Открыть счёт",
                url=payment_url,
            )
        )

    builder.row(
        InlineKeyboardButton(
            text="🔄 Проверить оплату",
            callback_data=TopupCB(
                action="check",
                topup_id=topup_id,
                payment_id=payment_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🆘 Поддержка",
            callback_data=TopupCB(
                action="support",
                topup_id=topup_id,
                payment_id=payment_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=TopupCB(
                action="cancel",
                topup_id=topup_id,
                payment_id=payment_id,
            ),
        )
    )

    return builder.as_markup()


def topup_history_keyboard(
    page: int = 1,
) -> InlineKeyboardMarkup:
    """История пополнений."""

    builder = InlineKeyboardBuilder()

    if page > 1:
        builder.button(
            text="⬅️",
            callback_data=TopupCB(
                action="history",
                page=page - 1,
            ),
        )

    builder.button(
        text=f"📄 {page}",
        callback_data=TopupCB(
            action="history",
            page=page,
        ),
    )

    builder.button(
        text="➡️",
        callback_data=TopupCB(
            action="history",
            page=page + 1,
        ),
    )

    builder.adjust(3)

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=TopupCB(
                action="back",
            ),
        )
    )

    return builder.as_markup()


def topup_support_keyboard(
    *,
    topup_id: int = 0,
) -> InlineKeyboardMarkup:
    """Кнопка обращения в поддержку."""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🆘 Поддержка",
            callback_data=TopupCB(
                action="support",
                topup_id=topup_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=TopupCB(
                action="back",
            ),
        )
    )

    return builder.as_markup()


# ============================================================
# P2P
# ============================================================


def p2p_guide_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура P2P-инструкции."""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📖 Инструкция",
            callback_data=TopupCB(
                action="guide",
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="✅ Я пополнил",
            callback_data=TopupCB(
                action="confirm",
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="🆘 Поддержка",
            callback_data=TopupCB(
                action="support",
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=TopupCB(
                action="back",
            ),
        )
    )

    return builder.as_markup()


# ============================================================
# Администратор
# ============================================================


def admin_payment_keyboard(
    payment_id: int,
) -> InlineKeyboardMarkup:
    """Админские действия с платежом."""

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="🔄 Синхронизировать",
            callback_data=PaymentAdminCB(
                action="sync",
                payment_id=payment_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=PaymentAdminCB(
                action="cancel",
                payment_id=payment_id,
            ),
        )
    )

    builder.row(
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=PaymentAdminCB(
                action="back",
                payment_id=payment_id,
            ),
        )
    )

    return builder.as_markup()


__all__ = [
    "admin_payment_keyboard",
    "invoice_keyboard",
    "payment_status_keyboard",
    "topup_amount_keyboard",
    "topup_asset_keyboard",
    "topup_history_keyboard",
    "topup_invoice_keyboard",
    "topup_menu_keyboard",
    "topup_payment_keyboard",
    "topup_support_keyboard",
    "p2p_guide_keyboard",
]