from __future__ import annotations

"""
Административные настройки магазина.

Важно:
- секреты и BOT_TOKEN здесь не изменяются;
- разрешён только заранее определённый список настроек;
- значения хранятся в ShopSetting;
- каждое изменение пишется в AdminLog;
- тип и описание настройки сохраняются вместе со значением.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger
from sqlalchemy import select

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import (
    Admin,
    AdminAction,
    AdminLog,
    ShopSetting,
    User,
)
from app.database.session import get_session
from app.states.admin import AdminSettingsStates
from app.utils.validation import validate_text_length


router = Router(name="admin_settings")


# ============================================================
# Проверка администратора
# ============================================================

def _is_admin(telegram_id: int) -> bool:
    """Проверяет права администратора."""
    return telegram_id in settings.admin_ids


# ============================================================
# Разрешённые настройки
# ============================================================

EDITABLE_SETTINGS: dict[str, dict[str, object]] = {
    "shop_name": {
        "title": "Название магазина",
        "type": "text",
        "default": settings.app_name,
        "description": "Отображаемое название магазина.",
        "is_public": True,
    },
    "default_language": {
        "title": "Язык по умолчанию",
        "type": "choice",
        "default": settings.default_language,
        "description": "Язык новых пользователей.",
        "is_public": True,
    },
    "min_topup_usd": {
        "title": "Минимальное пополнение",
        "type": "money",
        "default": settings.min_topup_usd,
        "description": "Минимальная сумма пополнения в USD.",
        "is_public": False,
    },
    "referral_enabled": {
        "title": "Реферальная система",
        "type": "bool",
        "default": settings.referral_enabled,
        "description": "Включена ли реферальная система.",
        "is_public": False,
    },
    "broadcast_enabled": {
        "title": "Рассылки",
        "type": "bool",
        "default": settings.broadcast_enabled,
        "description": "Разрешены ли массовые рассылки.",
        "is_public": False,
    },
    "subscription_required": {
        "title": "Обязательная подписка",
        "type": "bool",
        "default": settings.subscription_required,
        "description": "Требовать подписку на канал.",
        "is_public": False,
    },
    "invoice_poll_interval": {
        "title": "Интервал проверки платежей",
        "type": "int",
        "default": settings.invoice_poll_interval,
        "description": "Интервал проверки платежей в секундах.",
        "is_public": False,
    },
}


# ============================================================
# Вспомогательные функции
# ============================================================

def _default_value(key: str) -> str:
    """Возвращает значение настройки по умолчанию."""
    config = EDITABLE_SETTINGS[key]
    value = config["default"]

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


def _display_value(
    key: str,
    value: str | None,
) -> str:
    """Форматирует значение для интерфейса администратора."""
    if value is None or value == "":
        value = _default_value(key)

    setting_type = EDITABLE_SETTINGS[key]["type"]

    if setting_type == "bool":
        return (
            "🟢 Включено"
            if value.lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            else "🔴 Выключено"
        )

    if setting_type == "money":
        try:
            return f"${Decimal(value):.2f}"
        except (InvalidOperation, ValueError):
            return value

    return value


async def _get_setting(
    key: str,
) -> str | None:
    """Получает значение настройки из БД."""
    async with get_session() as session:
        setting = await session.scalar(
            select(ShopSetting).where(
                ShopSetting.key == key
            )
        )

        if setting is None:
            return None

        return setting.value


async def _get_admin(
    session: Any,
    telegram_id: int,
) -> Admin | None:
    """Получает активную запись администратора."""
    result = await session.execute(
        select(Admin)
        .join(User, User.id == Admin.user_id)
        .where(User.telegram_id == telegram_id)
        .where(Admin.is_active.is_(True))
    )

    return result.scalar_one_or_none()


async def _save_setting(
    key: str,
    value: str,
    telegram_id: int,
) -> None:
    """
    Сохраняет настройку и записывает действие в AdminLog.
    """
    config = EDITABLE_SETTINGS[key]

    async with get_session() as session:
        setting = await session.scalar(
            select(ShopSetting).where(
                ShopSetting.key == key
            )
        )

        if setting is None:
            setting = ShopSetting(
                key=key,
                value=value,
                value_type=str(config["type"]),
                description=str(config["description"]),
                is_public=bool(config.get("is_public", False)),
            )
            session.add(setting)
        else:
            setting.value = value
            setting.value_type = str(config["type"])
            setting.description = str(config["description"])
            setting.is_public = bool(
                config.get("is_public", False)
            )

        admin = await _get_admin(
            session=session,
            telegram_id=telegram_id,
        )

        if admin is None:
            raise RuntimeError(
                "Активная запись администратора не найдена."
            )

        session.add(
            AdminLog(
                admin_id=admin.id,
                action=AdminAction.SETTING_CHANGE,
                target_type="shop_setting",
                target_id=setting.id if setting.id else None,
                description=(
                    f"Изменена настройка "
                    f"'{key}' на значение '{value}'."
                ),
                metadata_json={
                    "key": key,
                    "value": value,
                    "value_type": str(config["type"]),
                },
            )
        )

        await session.commit()


async def _delete_setting(
    key: str,
    telegram_id: int,
) -> None:
    """
    Удаляет пользовательское значение из БД.

    После удаления приложение возвращается к значению из .env.
    """
    async with get_session() as session:
        setting = await session.scalar(
            select(ShopSetting).where(
                ShopSetting.key == key
            )
        )

        if setting is None:
            return

        admin = await _get_admin(
            session=session,
            telegram_id=telegram_id,
        )

        if admin is None:
            raise RuntimeError(
                "Активная запись администратора не найдена."
            )

        session.add(
            AdminLog(
                admin_id=admin.id,
                action=AdminAction.SETTING_CHANGE,
                target_type="shop_setting",
                target_id=setting.id,
                description=(
                    f"Настройка '{key}' сброшена "
                    "к значению конфигурации."
                ),
                metadata_json={
                    "key": key,
                    "action": "reset",
                    "value": _default_value(key),
                },
            )
        )

        await session.delete(setting)
        await session.commit()


async def _settings_text() -> str:
    """Формирует список настроек."""
    lines = [
        "⚙️ <b>Настройки магазина</b>",
        "",
    ]

    for key, config in EDITABLE_SETTINGS.items():
        value = await _get_setting(key)

        lines.extend(
            [
                f"<b>{config['title']}</b>",
                f"Ключ: <code>{key}</code>",
                f"Значение: <code>{_display_value(key, value)}</code>",
                "",
            ]
        )

    lines.append(
        "⚠️ BOT_TOKEN и API-ключи не изменяются через этот раздел."
    )

    return "\n".join(lines)


async def _send_settings(
    message: Message,
) -> None:
    """Отправляет экран настроек."""
    try:
        text = await _settings_text()
        await message.answer(text)
    except Exception:
        logger.exception(
            "Ошибка отображения настроек магазина"
        )
        await message.answer(
            "❌ Не удалось загрузить настройки."
        )


# ============================================================
# Открытие
# ============================================================

@router.message(Command("settings"))
async def settings_command(
    message: Message,
) -> None:
    """Открывает настройки через команду."""
    if not message.from_user:
        return

    if not _is_admin(message.from_user.id):
        return

    await _send_settings(message)


@router.callback_query(
    AdminCB.filter(F.action == "settings")
)
async def settings_open(
    callback: CallbackQuery,
) -> None:
    """Открывает настройки из админ-панели."""
    if not callback.from_user:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        text = await _settings_text()

        if callback.message:
            await callback.message.edit_text(text)

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия настроек администратором {}",
            callback.from_user.id,
        )

        await callback.answer(
            "Ошибка загрузки настроек.",
            show_alert=True,
        )


# ============================================================
# Начало редактирования
# ============================================================

@router.callback_query(
    AdminCB.filter(F.action == "settings_edit")
)
async def settings_edit_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начинает изменение настройки."""
    if not callback.from_user:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(callback.data)
        key = str(parsed.get("setting_key", ""))
    except (ValueError, TypeError, KeyError):
        key = ""

    if key not in EDITABLE_SETTINGS:
        await callback.answer(
            "Эта настройка недоступна.",
            show_alert=True,
        )
        return

    config = EDITABLE_SETTINGS[key]
    setting_type = config["type"]

    # Boolean переключается сразу.
    if setting_type == "bool":
        current = await _get_setting(key)

        current_enabled = (
            current is not None
            and current.lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        )

        # Если значения ещё нет в БД,
        # берём реальное значение из .env.
        if current is None:
            current_enabled = bool(config["default"])

        new_value = not current_enabled

        try:
            await _save_setting(
                key=key,
                value="true" if new_value else "false",
                telegram_id=callback.from_user.id,
            )
        except Exception:
            logger.exception(
                "Ошибка изменения boolean-настройки {}",
                key,
            )

            await callback.answer(
                "Не удалось сохранить настройку.",
                show_alert=True,
            )
            return

        await callback.answer(
            "Включено" if new_value else "Выключено"
        )

        if callback.message:
            await callback.message.edit_text(
                await _settings_text()
            )

        return

    await state.clear()

    await state.update_data(
        setting_key=key,
    )

    await state.set_state(
        AdminSettingsStates.waiting_for_value
    )

    current = await _get_setting(key)

    if callback.message:
        await callback.message.edit_text(
            "⚙️ <b>Изменение настройки</b>\n\n"
            f"<b>{config['title']}</b>\n"
            f"{config['description']}\n\n"
            "Текущее значение:\n"
            f"<code>{_display_value(key, current)}</code>\n\n"
            "Отправь новое значение.\n"
            "Для отмены: /cancel"
        )

    await callback.answer()


# ============================================================
# Получение нового значения
# ============================================================

@router.message(
    AdminSettingsStates.waiting_for_value
)
async def settings_receive_value(
    message: Message,
    state: FSMContext,
) -> None:
    """Получает новое значение настройки."""
    if not message.from_user:
        return

    if not _is_admin(message.from_user.id):
        return

    if not message.text:
        await message.answer(
            "❌ Отправь значение текстом."
        )
        return

    if message.text.strip().lower() == "/cancel":
        await state.clear()

        await message.answer(
            "❌ Изменение настройки отменено."
        )
        return

    data = await state.get_data()
    key = data.get("setting_key")

    if key not in EDITABLE_SETTINGS:
        await state.clear()

        await message.answer(
            "❌ Настройка больше недоступна."
        )
        return

    raw_value = message.text.strip()
    setting_type = EDITABLE_SETTINGS[key]["type"]

    # --------------------------------------------------------
    # Текст
    # --------------------------------------------------------

    if setting_type == "text":
        try:
            validate_text_length(
                raw_value,
                max_length=255,
                field_name="Значение настройки",
            )
        except (ValueError, TypeError) as exc:
            await message.answer(
                f"❌ Некорректное значение:\n{exc}"
            )
            return

        value = raw_value

    # --------------------------------------------------------
    # Язык
    # --------------------------------------------------------

    elif setting_type == "choice":
        value = raw_value.lower()

        if value not in {"ru", "en"}:
            await message.answer(
                "❌ Допустимые языки: "
                "<code>ru</code> или <code>en</code>."
            )
            return

    # --------------------------------------------------------
    # Деньги
    # --------------------------------------------------------

    elif setting_type == "money":
        normalized = raw_value.replace(",", ".")

        try:
            amount = Decimal(normalized)
        except InvalidOperation:
            await message.answer(
                "❌ Введи корректную сумму."
            )
            return

        if amount <= 0:
            await message.answer(
                "❌ Сумма должна быть больше нуля."
            )
            return

        if amount > Decimal("1000000"):
            await message.answer(
                "❌ Максимальное значение — $1 000 000."
            )
            return

        value = f"{amount:.2f}"

    # --------------------------------------------------------
    # Целое число
    # --------------------------------------------------------

    elif setting_type == "int":
        try:
            number = int(raw_value)
        except ValueError:
            await message.answer(
                "❌ Введи целое число."
            )
            return

        if key == "invoice_poll_interval":
            # По требованиям платёжной системы проверки
            # не должны происходить чаще 15 секунд.
            if number < 15:
                await message.answer(
                    "❌ Минимальный интервал — 15 секунд."
                )
                return

            if number > 3600:
                await message.answer(
                    "❌ Максимальный интервал — 3600 секунд."
                )
                return

        value = str(number)

    else:
        await message.answer(
            "❌ Неподдерживаемый тип настройки."
        )
        return

    # --------------------------------------------------------
    # Сохранение
    # --------------------------------------------------------

    try:
        await _save_setting(
            key=key,
            value=value,
            telegram_id=message.from_user.id,
        )
    except Exception:
        logger.exception(
            "Ошибка сохранения настройки {}",
            key,
        )

        await message.answer(
            "❌ Не удалось сохранить настройку."
        )
        return

    await state.clear()

    title = EDITABLE_SETTINGS[key]["title"]

    await message.answer(
        "✅ <b>Настройка изменена</b>\n\n"
        f"{title}: "
        f"<code>{_display_value(key, value)}</code>"
    )


# ============================================================
# Сброс настройки
# ============================================================

@router.callback_query(
    AdminCB.filter(F.action == "settings_reset")
)
async def settings_reset(
    callback: CallbackQuery,
) -> None:
    """Удаляет значение из БД и возвращает значение .env."""
    if not callback.from_user:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(callback.data)
        key = str(parsed.get("setting_key", ""))
    except (ValueError, TypeError, KeyError):
        key = ""

    if key not in EDITABLE_SETTINGS:
        await callback.answer(
            "Настройка не найдена.",
            show_alert=True,
        )
        return

    try:
        await _delete_setting(
            key=key,
            telegram_id=callback.from_user.id,
        )
    except Exception:
        logger.exception(
            "Ошибка сброса настройки {}",
            key,
        )

        await callback.answer(
            "Не удалось сбросить настройку.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Сброшено к значению .env."
    )

    if callback.message:
        await callback.message.edit_text(
            await _settings_text()
        )


# ============================================================
# Обновление
# ============================================================

@router.callback_query(
    AdminCB.filter(F.action == "settings_refresh")
)
async def settings_refresh(
    callback: CallbackQuery,
) -> None:
    """Обновляет экран настроек."""
    if not callback.from_user:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        if callback.message:
            await callback.message.edit_text(
                await _settings_text()
            )

        await callback.answer("Обновлено")

    except Exception:
        logger.exception(
            "Ошибка обновления настроек"
        )

        await callback.answer(
            "Ошибка обновления.",
            show_alert=True,
        )


# ============================================================
# Отмена FSM
# ============================================================

@router.message(Command("cancel"))
async def settings_cancel(
    message: Message,
    state: FSMContext,
) -> None:
    """Отменяет изменение настройки."""
    if not message.from_user:
        return

    if not _is_admin(message.from_user.id):
        return

    current_state = await state.get_state()

    if current_state != (
        AdminSettingsStates.waiting_for_value.state
    ):
        return

    await state.clear()

    await message.answer(
        "❌ Изменение настройки отменено."
    )


__all__ = ["router"]