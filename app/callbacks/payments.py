from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class PaymentCB(CallbackData, prefix="pay"):
    """Callback-кнопки пользовательских платежей."""

    action: str
    method: str = ""
    order_id: int = 0
    payment_id: int = 0
    page: int = 0


class TopupCB(CallbackData, prefix="topup"):
    """Callback-кнопки пополнения баланса."""

    action: str
    topup_id: int = 0
    payment_id: int = 0
    amount_cents: int = 0
    asset: str = ""
    page: int = 0


class PaymentAdminCB(CallbackData, prefix="payadm"):
    """Callback-кнопки администрирования платежей."""

    action: str
    payment_id: int = 0
    page: int = 0


__all__ = [
    "PaymentCB",
    "TopupCB",
    "PaymentAdminCB",
]