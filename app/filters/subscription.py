from __future__ import annotations

from typing import Any

from aiogram.enums import ChatMemberStatus
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.config import settings


def _get_user_id(event: TelegramObject) -> int | None:
    """
    Получает Telegram ID пользователя из события.
    """

    if isinstance(event, Message):
        if event.from_user is None:
            return None

        return event.from_user.id

    if isinstance(event, CallbackQuery):
        if event.from_user is None:
            return None

        return event.from_user.id

    return None


class SubscriptionRequiredFilter(BaseFilter):
    """
    Проверка обязательной подписки на канал.

    Если обязательная подписка отключена в настройках,
    фильтр всегда пропускает пользователя.

    Проверяются статусы:
        - creator;
        - administrator;
        - member.

    Статусы:
        - left;
        - kicked;
        - restricted без возможности писать
          считаются отсутствием необходимой подписки.
    """

    async def __call__(
        self,
        event: TelegramObject,
        **kwargs: Any,
    ) -> bool:
        bot = kwargs.get("bot")

        if bot is None:
            return False

        if not settings.subscription_required:
            return True

        channel_id = settings.required_channel_id

        if channel_id is None:
            # Некорректная конфигурация не должна случайно
            # открыть доступ к магазину.
            return False

        user_id = _get_user_id(event)

        if user_id is None:
            return False

        try:
            member = await bot.get_chat_member(
                chat_id=channel_id,
                user_id=user_id,
            )

            if member.status in {
                ChatMemberStatus.CREATOR,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.MEMBER,
            }:
                return True

            if member.status == ChatMemberStatus.RESTRICTED:
                return bool(
                    getattr(member, "is_member", False)
                )

            return False

        except Exception:
            # При ошибке Telegram API доступ не выдаём.
            return False


class SubscriptionFilter(SubscriptionRequiredFilter):
    """
    Короткий алиас для SubscriptionRequiredFilter.
    """

    pass


__all__ = [
    "SubscriptionFilter",
    "SubscriptionRequiredFilter",
]