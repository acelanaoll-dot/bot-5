from __future__ import annotations

"""
FSM-состояния административной панели.

Все многошаговые действия администратора должны проходить
через FSM, чтобы ввод пользователя не смешивался между диалогами.
"""

from aiogram.fsm.state import State, StatesGroup


class AdminMenuStates(StatesGroup):
    """Общие состояния административного меню."""

    waiting_for_action = State()


class AdminManagementStates(StatesGroup):
    """Управление администраторами."""

    waiting_for_telegram_id = State()


class AdminProductStates(StatesGroup):
    """Создание и редактирование товаров."""

    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_price = State()
    waiting_for_stock = State()
    waiting_for_category = State()
    waiting_for_photo = State()
    waiting_for_photos = State()


class AdminCategoryStates(StatesGroup):
    """Создание и редактирование категорий."""

    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_parent = State()
    waiting_for_sort_order = State()


class AdminOrderStates(StatesGroup):
    """Операции с заказами."""

    waiting_for_delivery = State()
    waiting_for_refund_amount = State()
    waiting_for_cancel_reason = State()


class AdminUserStates(StatesGroup):
    """Поиск и изменение пользователей."""

    waiting_for_search = State()
    waiting_for_balance_amount = State()
    waiting_for_ban_reason = State()


class AdminBroadcastStates(StatesGroup):
    """Создание и отправка рассылок."""

    waiting_for_text = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_confirmation = State()


class AdminPromoStates(StatesGroup):
    """Создание и редактирование промокодов."""

    waiting_for_code = State()
    waiting_for_discount_type = State()
    waiting_for_discount_value = State()
    waiting_for_max_uses = State()
    waiting_for_expires_at = State()


class AdminSettingsStates(StatesGroup):
    """Изменение общих настроек магазина."""

    waiting_for_value = State()


class AdminTopupSettingsStates(StatesGroup):
    """Изменение настроек пополнения баланса."""

    waiting_for_value = State()


class AdminGatewayStates(StatesGroup):
    """Управление платёжными шлюзами."""

    waiting_for_value = State()


class AdminP2PGuideStates(StatesGroup):
    """Редактирование P2P-инструкции."""

    waiting_for_title = State()
    waiting_for_text = State()
    waiting_for_photo = State()
    waiting_for_sort_order = State()


class AdminBackupStates(StatesGroup):
    """Операции с резервными копиями."""

    waiting_for_confirmation = State()


class AdminLogsStates(StatesGroup):
    """Работа с логами."""

    waiting_for_search = State()


__all__ = [
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