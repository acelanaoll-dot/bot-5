"""Единая точка экспорта FSM-состояний Telegram-бота."""

from app.states.admin import (
    AdminMenuStates,
    AdminManagementStates,
    AdminProductStates,
    AdminCategoryStates,
    AdminOrderStates,
    AdminUserStates,
    AdminBroadcastStates,
    AdminPromoStates,
    AdminSettingsStates,
    AdminTopupSettingsStates,
    AdminGatewayStates,
    AdminP2PGuideStates,
    AdminBackupStates,
    AdminLogsStates,
)
from app.states.catalog import CatalogStates
from app.states.checkout import CheckoutStates
from app.states.topup import TopupStates

__all__ = [
    "CatalogStates",
    "CheckoutStates",
    "TopupStates",
    "AdminMenuStates",
    "AdminManagementStates",
    "AdminProductStates",
    "AdminCategoryStates",
    "AdminOrderStates",
    "AdminUserStates",
    "AdminBroadcastStates",
    "AdminPromoStates",
    "AdminSettingsStates",
    "AdminTopupSettingsStates",
    "AdminGatewayStates",
    "AdminP2PGuideStates",
    "AdminBackupStates",
    "AdminLogsStates",
]