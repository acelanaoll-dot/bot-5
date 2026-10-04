from __future__ import annotations

"""
Административные handlers.

Каждый раздел админ-панели находится в отдельном модуле.
Здесь только экспорт общих объектов и документация структуры.
"""

from aiogram import Router


admin_router = Router(name="admin")


__all__ = [
    "admin_router",
]