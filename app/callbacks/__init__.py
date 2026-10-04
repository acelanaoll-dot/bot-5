"""Единая точка экспорта CallbackData-фабрик Telegram-бота."""

from app.callbacks.admin import (
    AdminCB,
    AdminProductCB,
    AdminCategoryCB,
    AdminOrderCB,
    AdminUserCB,
    AdminFinanceCB,
    AdminGatewayCB,
    AdminSettingsCB,
    AdminBackupCB,
    AdminBroadcastCB,
    AdminPromoCB,
    AdminCBAction,
)
from app.callbacks.catalog import CatalogCB
from app.callbacks.cart import CartCB
from app.callbacks.checkout import CheckoutCB
from app.callbacks.orders import OrderCB
from app.callbacks.payments import PaymentCB, TopupCB, PaymentAdminCB
from app.callbacks.user import UserCB, ReferralCB, PromoCB, LanguageCB

__all__ = [
    "AdminCB",
    "AdminProductCB",
    "AdminCategoryCB",
    "AdminOrderCB",
    "AdminUserCB",
    "AdminFinanceCB",
    "AdminGatewayCB",
    "AdminSettingsCB",
    "AdminBackupCB",
    "AdminBroadcastCB",
    "AdminPromoCB",
    "AdminCBAction",
    "CatalogCB",
    "CartCB",
    "CheckoutCB",
    "OrderCB",
    "PaymentCB",
    "TopupCB",
    "PaymentAdminCB",
    "UserCB",
    "ReferralCB",
    "PromoCB",
    "LanguageCB",
]