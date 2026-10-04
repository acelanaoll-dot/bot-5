from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class UserCB(CallbackData, prefix="usr"):
    """
    CallbackData для общих пользовательских действий.

    Здесь находятся действия верхнего уровня:
    профиль, баланс, история, рефералы, промокоды,
    настройки языка и возврат в главное меню.
    """

    action: str
    page: int = 1
    user_id: int = 0

    # Возможные action:
    #
    # profile       — открыть профиль
    # balance       — открыть баланс
    # balance_history — история операций по балансу
    # orders        — мои заказы
    # referrals     — реферальная программа
    # promo         — ввод/использование промокода
    # language      — выбор языка
    # language_set  — установить язык
    # help          — помощь
    # support       — поддержка
    # back          — назад
    # home          — главное меню
    #
    # page используется для постраничных списков.
    # user_id оставлен только для служебных сценариев,
    # где callback формируется самим ботом.


class ReferralCB(CallbackData, prefix="ref"):
    """
    CallbackData реферального раздела.
    """

    action: str
    page: int = 1

    # Возможные action:
    #
    # open       — открыть реферальный раздел
    # stats      — статистика приглашений
    # history    — история начислений
    # link       — получить реферальную ссылку
    # back       — назад


class PromoCB(CallbackData, prefix="promo"):
    """
    CallbackData для пользовательских промокодов.
    """

    action: str
    page: int = 1

    # Возможные action:
    #
    # open       — открыть раздел промокодов
    # activate   — активировать промокод
    # history    — история использованных промокодов
    # back       — назад


class LanguageCB(CallbackData, prefix="lang"):
    """
    CallbackData выбора языка интерфейса.
    """

    action: str
    language: str = ""

    # Возможные action:
    #
    # open       — открыть выбор языка
    # set        — установить язык
    # back       — назад


__all__ = [
    "UserCB",
    "ReferralCB",
    "PromoCB",
    "LanguageCB",
]