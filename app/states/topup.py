from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class TopupStates(StatesGroup):
    """FSM-состояния пользовательского пополнения баланса."""

    waiting_for_amount = State()
    waiting_for_asset = State()
    waiting_for_payment = State()
    checking_payment = State()


__all__ = [
    "TopupStates",
]