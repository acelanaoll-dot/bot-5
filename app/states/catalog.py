from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class CatalogStates(StatesGroup):
    """Состояния пользовательского каталога."""

    searching = State()


__all__ = [
    "CatalogStates",
]