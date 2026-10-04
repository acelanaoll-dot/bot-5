"""
Middleware Telegram-бота.

Модули:
    db.py          — передача AsyncSession в handlers;
    throttling.py  — защита от спама и flood;
    proxy.py       — инфраструктура прокси;
    admin_check.py — дополнительная защита административных роутеров.

Middleware не должен содержать бизнес-логику магазина.
"""

from app.middlewares.admin_check import AdminCheckMiddleware
from app.middlewares.db import DatabaseMiddleware
from app.middlewares.proxy import ProxyMiddleware
from app.middlewares.throttling import ThrottlingMiddleware

__all__ = [
    "DatabaseMiddleware",
    "ThrottlingMiddleware",
    "ProxyMiddleware",
    "AdminCheckMiddleware",
]