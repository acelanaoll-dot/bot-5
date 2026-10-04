from __future__ import annotations

from app.handlers.user.start import router as user_start
from app.handlers.user.catalog import router as user_catalog
from app.handlers.user.cart import router as user_cart
from app.handlers.user.checkout import router as user_checkout
from app.handlers.user.topup import user_topup
from app.handlers.user.profile import router as user_profile

__all__ = [
    "user_start",
    "user_catalog",
    "user_cart",
    "user_checkout",
    "user_topup",
    "user_profile",
]