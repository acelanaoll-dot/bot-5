from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CheckoutStates(StatesGroup):
    """
    FSM оформления заказа.

    Основной сценарий:

        корзина
            ↓
        проверка данных
            ↓
        промокод (опционально)
            ↓
        подтверждение заказа
            ↓
        списание внутреннего баланса
            ↓
        доплата криптовалютой при необходимости
            ↓
        ожидание оплаты
            ↓
        завершение заказа

    Все значения, полученные от пользователя, должны
    дополнительно валидироваться в handler/service.
    """

    # Подтверждение содержимого корзины.
    confirming_cart = State()

    # Ввод промокода.
    waiting_for_promo = State()

    # Подтверждение применения промокода.
    confirming_promo = State()

    # Подтверждение создания заказа.
    confirming_order = State()

    # Ожидание подтверждения оплаты внутренним балансом.
    confirming_balance_payment = State()

    # Выбор способа доплаты, если внутреннего баланса
    # недостаточно для полной оплаты.
    selecting_payment_method = State()

    # Ожидание оплаты криптовалютой.
    waiting_for_crypto_payment = State()

    # Проверка статуса криптоплатежа.
    checking_payment = State()

    # Подтверждение отмены оформления.
    confirming_cancel = State()


__all__ = [
    "CheckoutStates",
]