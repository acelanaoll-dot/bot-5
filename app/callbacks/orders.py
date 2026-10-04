from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class OrderCB(CallbackData, prefix="ord"):
    """Пользовательские действия с заказами."""

    action: str
    order_id: int = 0
    page: int = 1


class OrderAdminCB(CallbackData, prefix="ordadm"):
    """Административные действия с заказами."""

    action: str
    order_id: int = 0
    page: int = 1


__all__ = [
    "OrderCB",
    "OrderAdminCB",
]