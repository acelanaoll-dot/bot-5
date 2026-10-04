from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks.user import (
    LanguageCB,
    ReferralCB,
    UserCB,
)


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Главное меню пользователя."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🛍 Каталог",
        callback_data="catalog:categories",
    )
    builder.button(
        text="🛒 Корзина",
        callback_data="cart:open",
    )
    builder.button(
        text="👤 Профиль",
        callback_data=UserCB(
            action="profile",
        ).pack(),
    )
    builder.button(
        text="💰 Баланс",
        callback_data=UserCB(
            action="balance",
        ).pack(),
    )
    builder.button(
        text="📦 Мои заказы",
        callback_data=UserCB(
            action="orders",
        ).pack(),
    )
    builder.button(
        text="👥 Рефералы",
        callback_data=ReferralCB(
            action="open",
        ).pack(),
    )
    builder.button(
        text="🎟 Промокод",
        callback_data="promo:open",
    )
    builder.button(
        text="🆘 Поддержка",
        callback_data=UserCB(
            action="support",
        ).pack(),
    )

    builder.adjust(2, 2, 2, 2)

    return builder.as_markup()


def profile_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура профиля."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="💰 Баланс",
        callback_data=UserCB(
            action="balance",
        ).pack(),
    )
    builder.button(
        text="📦 Мои заказы",
        callback_data=UserCB(
            action="orders",
        ).pack(),
    )
    builder.button(
        text="👥 Рефералы",
        callback_data=ReferralCB(
            action="open",
        ).pack(),
    )
    builder.button(
        text="🌐 Язык",
        callback_data=LanguageCB(
            action="open",
        ).pack(),
    )
    builder.button(
        text="🆘 Поддержка",
        callback_data=UserCB(
            action="support",
        ).pack(),
    )
    builder.button(
        text="⬅️ Назад",
        callback_data=UserCB(
            action="home",
        ).pack(),
    )

    builder.adjust(2, 2, 1, 1)

    return builder.as_markup()


def balance_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура баланса."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="💳 Пополнить баланс",
        callback_data="topup:start",
    )
    builder.button(
        text="📜 История операций",
        callback_data=UserCB(
            action="balance_history",
        ).pack(),
    )
    builder.button(
        text="📦 Мои заказы",
        callback_data=UserCB(
            action="orders",
        ).pack(),
    )
    builder.button(
        text="⬅️ Назад",
        callback_data=UserCB(
            action="profile",
        ).pack(),
    )

    builder.adjust(1, 2, 1)

    return builder.as_markup()


def referral_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура реферальной программы."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🔗 Моя ссылка",
        callback_data=ReferralCB(
            action="link",
        ).pack(),
    )
    builder.button(
        text="📊 Статистика",
        callback_data=ReferralCB(
            action="stats",
        ).pack(),
    )
    builder.button(
        text="📜 История начислений",
        callback_data=ReferralCB(
            action="history",
        ).pack(),
    )
    builder.button(
        text="⬅️ Назад",
        callback_data=UserCB(
            action="profile",
        ).pack(),
    )

    builder.adjust(1, 2, 1)

    return builder.as_markup()


def language_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора языка."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="🇷🇺 Русский",
        callback_data=LanguageCB(
            action="set",
            language="ru",
        ).pack(),
    )
    builder.button(
        text="🇬🇧 English",
        callback_data=LanguageCB(
            action="set",
            language="en",
        ).pack(),
    )
    builder.button(
        text="⬅️ Назад",
        callback_data=UserCB(
            action="profile",
        ).pack(),
    )

    builder.adjust(2, 1)

    return builder.as_markup()


def support_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура раздела поддержки."""

    builder = InlineKeyboardBuilder()

    builder.button(
        text="💬 Написать в поддержку",
        callback_data=UserCB(
            action="support",
        ).pack(),
    )
    builder.button(
        text="⬅️ Назад",
        callback_data=UserCB(
            action="home",
        ).pack(),
    )

    builder.adjust(1)

    return builder.as_markup()


__all__ = [
    "main_menu_keyboard",
    "profile_keyboard",
    "balance_keyboard",
    "referral_keyboard",
    "language_keyboard",
    "support_keyboard",
]