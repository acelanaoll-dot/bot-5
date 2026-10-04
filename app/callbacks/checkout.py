from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class CheckoutCB(CallbackData, prefix="checkout"):
    """
    Действия на этапе оформления заказа.

    action:
        open          — открыть оформление;
        promo         — ввести промокод;
        promo_apply   — применить промокод;
        promo_remove  — убрать промокод;
        confirm       — подтвердить заказ;
        payment       — перейти к выбору оплаты;
        balance       — оплатить балансом;
        crypto        — оплатить криптовалютой;
        cancel        — отменить оформление;
        back          — вернуться в корзину;
    """

    action: str
    order_id: int = 0
    page: int = 0


__all__ = [
    "CheckoutCB",
]