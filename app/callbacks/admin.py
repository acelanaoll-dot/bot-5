from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class AdminCB(CallbackData, prefix="adm"):
    """Основные действия административной панели."""

    action: str
    page: int = 1


class AdminMenuCB(CallbackData, prefix="adm_menu"):
    """
    Навигация верхнего уровня админки.

    Оставлена отдельным классом, чтобы не ломать уже существующие
    клавиатуры. На следующем шаге действия будут унифицированы
    с handler-слоем.
    """

    action: str
    page: int = 1


class AdminProductCB(CallbackData, prefix="admp"):
    """Административные действия с товарами."""

    action: str
    product_id: int = 0
    category_id: int = 0
    page: int = 1


class AdminCategoryCB(CallbackData, prefix="admc"):
    """Административные действия с категориями."""

    action: str
    category_id: int = 0
    parent_id: int = 0
    page: int = 1


class AdminOrderCB(CallbackData, prefix="admo"):
    """Административные действия с заказами."""

    action: str
    order_id: int = 0
    page: int = 1


class AdminUserCB(CallbackData, prefix="admu"):
    """Административные действия с пользователями."""

    action: str
    user_id: int = 0
    page: int = 1


class AdminStatsCB(CallbackData, prefix="admst"):
    """Действия раздела статистики."""

    action: str
    page: int = 1


class AdminFinanceCB(CallbackData, prefix="admf"):
    """Административные финансовые операции."""

    action: str
    user_id: int = 0
    payment_id: int = 0
    topup_id: int = 0
    page: int = 1


class AdminGatewayCB(CallbackData, prefix="admg"):
    """Управление платёжными шлюзами."""

    action: str
    gateway: str = ""
    page: int = 1


class AdminSettingsCB(CallbackData, prefix="adms"):
    """Административные настройки магазина."""

    action: str
    key: str = ""
    page: int = 1


class AdminTopupCB(CallbackData, prefix="admt"):
    """Настройки пополнения и P2P."""

    action: str
    topup_id: int = 0
    page: int = 1


class AdminBackupCB(CallbackData, prefix="admb"):
    """Управление резервными копиями."""

    action: str
    backup_id: int = 0
    page: int = 1


class AdminLogCB(CallbackData, prefix="adml"):
    """Действия раздела административных логов."""

    action: str
    log_id: int = 0
    page: int = 1


class AdminTextCB(CallbackData, prefix="admtxt"):
    """Управление текстами и переводами."""

    action: str
    key: str = ""
    page: int = 1


class AdminBroadcastCB(CallbackData, prefix="admbc"):
    """Административные действия с рассылками."""

    action: str
    broadcast_id: int = 0
    page: int = 1


class AdminPromoCB(CallbackData, prefix="admpromo"):
    """Административные действия с промокодами."""

    action: str
    promo_id: int = 0
    page: int = 1


class AdminCBAction(CallbackData, prefix="adma"):
    """Универсальные подтверждения административных действий."""

    action: str
    target_id: int = 0
    page: int = 1


__all__ = [
    "AdminCB",
    "AdminMenuCB",
    "AdminProductCB",
    "AdminCategoryCB",
    "AdminOrderCB",
    "AdminUserCB",
    "AdminStatsCB",
    "AdminFinanceCB",
    "AdminGatewayCB",
    "AdminSettingsCB",
    "AdminTopupCB",
    "AdminBackupCB",
    "AdminLogCB",
    "AdminTextCB",
    "AdminBroadcastCB",
    "AdminPromoCB",
    "AdminCBAction",
]