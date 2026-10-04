from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class CartCB(CallbackData, prefix="cart"):
    """
    CallbackData корзины пользователя.

    Все действия корзины проходят через этот класс.
    """

    action: str
    product_id: int = 0
    cart_item_id: int = 0
    quantity: int = 1
    page: int = 1

    # Возможные action:
    #
    # open       — открыть корзину
    # add        — добавить товар
    # remove     — удалить товар
    # increase   — увеличить количество
    # decrease   — уменьшить количество
    # clear      — очистить корзину
    # checkout   — перейти к оформлению
    # refresh    — обновить корзину
    # back       — назад
    # home       — главное меню


__all__ = [
    "CartCB",
]