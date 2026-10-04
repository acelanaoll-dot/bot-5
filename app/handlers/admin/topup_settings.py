from __future__ import annotations

"""
Административные настройки пополнения.

Через этот раздел можно управлять:
- включением пополнений;
- минимальной суммой;
- максимальной суммой;
- доступными криптоактивами;
- текстом поддержки;
- отображением P2P-инструкции.

Секреты Crypto Pay / NOWPayments / BOT_TOKEN здесь не изменяются.
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
from app.states.admin import AdminTopupSettingsStates
from app.utils.validation import validate_text_length


router = Router(name="admin_topup_settings")


# ============================================================
# Проверка администратора
# ============================================================

def _is_admin(telegram_id: int) -> bool:
    """Проверяет администратора по Telegram ID."""
    return telegram_id in settings.admin_ids


# ============================================================
# Конфигурация настроек
# ============================================================

def _crypto_defaults() -> str:
    """
    Приводит список криптоактивов из config к строке БД.

    Поддерживаются как list/tuple/set, так и уже готовая строка.
    """
    assets = settings.supported_crypto_currencies

    if isinstance(assets, str):
        values = [
            item.strip().upper()
            for item in assets.split(",")
            if item.strip()
        ]
    else:
        values = [
            str(item).strip().upper()
            for item in assets
            if str(item).strip()
        ]

    # Убираем дубли с сохранением порядка.
    values = list(dict.fromkeys(values))

    return ",".join(values)


TOPUP_SETTINGS: dict[str, dict[str, object]] = {
    "topup_enabled": {
        "title": "Пополнение баланса",
        "type": "bool",
        "default": True,
        "description": (
            "Разрешает пользователям создавать новые пополнения."
        ),
        "is_public": False,
    },
    "min_topup_usd": {
        "title": "Минимальная сумма",
        "type": "money",
        "default": settings.min_topup_usd,
        "description": (
            "Минимальная сумма одного пополнения в USD."
        ),
        "is_public": False,
    },
    "max_topup_usd": {
        "title": "Максимальная сумма",
        "type": "money",
        "default": settings.max_topup_usd,
        "description": (
            "Максимальная сумма одного пополнения в USD."
        ),
        "is_public": False,
    },
    "topup_assets": {
        "title": "Криптоактивы",
        "type": "text",
        "default": _crypto_defaults(),
        "description": (
            "Список доступных активов через запятую. "
            "Например: USDT,TON,BTC,ETH."
        ),
        "is_public": True,
    },
    "topup_support_text": {
        "title": "Текст поддержки",
        "type": "text",
        "default": settings.support_text,
        "description": (
            "Текст, который показывается пользователю "
            "при обращении в поддержку."
        ),
        "is_public": False,
    },
    "topup_p2p_enabled": {
        "title": "P2P-инструкция",
        "type": "bool",
        "default": settings.p2p_guide_enabled,
        "description": (
            "Показывать пользователям инструкцию по P2P."
        ),
        "is_public": True,
    },
}


# ============================================================
# Значения по умолчанию
# ============================================================

def _default_value(key: str) -> str:
    """Возвращает значение настройки по умолчанию."""
    config = TOPUP_SETTINGS[key]
    value = config["default"]

    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


# ============================================================
# Отображение
# ============================================================

def _display_value(
    key: str,
    value: str | None,
) -> str:
    """Форматирует значение настройки для администратора."""
    if value is None or value == "":
        value = _default_value(key)

    setting_type = TOPUP_SETTINGS[key]["type"]

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


# ============================================================
# Работа с БД
# ============================================================

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
        .join(
            User,
            User.id == Admin.user_id,
        )
        .where(
            User.telegram_id == telegram_id
        )
        .where(
            Admin.is_active.is_(True)
        )
    )

    return result.scalar_one_or_none()


async def _save_setting(
    key: str,
    value: str,
    telegram_id: int,
) -> None:
    """
    Сохраняет настройку и пишет действие в AdminLog.
    """
    config = TOPUP_SETTINGS[key]

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
                description=str(
                    config["description"]
                ),
                is_public=bool(
                    config.get("is_public", False)
                ),
            )
            session.add(setting)
            await session.flush()
        else:
            setting.value = value
            setting.value_type = str(
                config["type"]
            )
            setting.description = str(
                config["description"]
            )
            setting.is_public = bool(
                config.get("is_public", False)
            )

        admin = await _get_admin(
            session=session,
            telegram_id=telegram_id,
        )

        if admin is None:
            raise RuntimeError(
                "Активная запись администратора "
                "не найдена."
            )

        session.add(
            AdminLog(
                admin_id=admin.id,
                action=AdminAction.SETTING_CHANGE,
                target_type="shop_setting",
                target_id=setting.id,
                description=(
                    f"Изменена настройка пополнения "
                    f"'{key}'."
                ),
                metadata_json={
                    "key": key,
                    "value": value,
                    "value_type": str(
                        config["type"]
                    ),
                },
            )
        )

        await session.commit()


async def _reset_setting(
    key: str,
    telegram_id: int,
) -> None:
    """
    Удаляет override из БД.

    После этого используется значение из .env/config.py.
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
                "Активная запись администратора "
                "не найдена."
            )

        session.add(
            AdminLog(
                admin_id=admin.id,
                action=AdminAction.SETTING_CHANGE,
                target_type="shop_setting",
                target_id=setting.id,
                description=(
                    f"Настройка '{key}' "
                    "сброшена к значению .env."
                ),
                metadata_json={
                    "key": key,
                    "action": "reset",
                    "default_value": _default_value(
                        key
                    ),
                },
            )
        )

        await session.delete(setting)
        await session.commit()


# ============================================================
# Экран настроек
# ============================================================

async def _settings_text() -> str:
    """Формирует экран настроек пополнения."""
    lines = [
        "💰 <b>Настройки пополнения</b>",
        "",
    ]

    for key, config in TOPUP_SETTINGS.items():
        value = await _get_setting(key)

        lines.extend(
            [
                f"<b>{config['title']}</b>",
                f"Ключ: <code>{key}</code>",
                (
                    "Значение: "
                    f"<code>{_display_value(key, value)}</code>"
                ),
                "",
            ]
        )

    lines.append(
        "⚠️ API-ключи, BOT_TOKEN и другие секреты "
        "не изменяются здесь."
    )

    return "\n".join(lines)


# ============================================================
# Открытие раздела
# ============================================================

@router.message(Command("topup_settings"))
async def topup_settings_command(
    message: Message,
) -> None:
    """Открывает настройки пополнения."""
    if not message.from_user:
        return

    if not _is_admin(message.from_user.id):
        return

    try:
        text = await _settings_text()
        await message.answer(text)

    except Exception:
        logger.exception(
            "Ошибка открытия настроек пополнения"
        )

        await message.answer(
            "❌ Не удалось загрузить настройки пополнения."
        )


@router.callback_query(
    AdminCB.filter(
        F.action == "topup_settings"
    )
)
async def topup_settings_open(
    callback: CallbackQuery,
) -> None:
    """Открывает настройки пополнения из админ-панели."""
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
            await callback.message.edit_text(
                text
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия настроек пополнения "
            "администратором {}",
            callback.from_user.id,
        )

        await callback.answer(
            "Ошибка загрузки настроек.",
            show_alert=True,
        )


# ============================================================
# Начало изменения
# ============================================================

@router.callback_query(
    AdminCB.filter(
        F.action == "topup_settings_edit"
    )
)
async def topup_settings_edit(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Начинает изменение конкретной настройки."""
    if not callback.from_user:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(
            callback.data
        )
        key = str(
            parsed.get(
                "setting_key",
                "",
            )
        )
    except (
        ValueError,
        TypeError,
        KeyError,
    ):
        key = ""

    if key not in TOPUP_SETTINGS:
        await callback.answer(
            "Настройка не найдена.",
            show_alert=True,
        )
        return

    config = TOPUP_SETTINGS[key]
    setting_type = config["type"]

    # Boolean переключаем сразу.
    if setting_type == "bool":
        current = await _get_setting(key)

        if current is None:
            enabled = bool(
                config["default"]
            )
        else:
            enabled = current.lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        new_value = not enabled

        try:
            await _save_setting(
                key=key,
                value=(
                    "true"
                    if new_value
                    else "false"
                ),
                telegram_id=callback.from_user.id,
            )
        except Exception:
            logger.exception(
                "Ошибка изменения "
                "boolean-настройки {}",
                key,
            )

            await callback.answer(
                "Не удалось сохранить настройку.",
                show_alert=True,
            )
            return

        await callback.answer(
            (
                "Включено"
                if new_value
                else "Выключено"
            )
        )

        if callback.message:
            await callback.message.edit_text(
                await _settings_text()
            )

        return

    await state.clear()

    await state.update_data(
        setting_key=key
    )

    await state.set_state(
        AdminTopupSettingsStates.waiting_for_value
    )

    current = await _get_setting(key)

    if callback.message:
        await callback.message.edit_text(
            "💰 <b>Изменение настройки</b>\n\n"
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
    AdminTopupSettingsStates.waiting_for_value
)
async def topup_settings_receive_value(
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

    if key not in TOPUP_SETTINGS:
        await state.clear()

        await message.answer(
            "❌ Настройка больше недоступна."
        )
        return

    raw_value = message.text.strip()
    setting_type = TOPUP_SETTINGS[key]["type"]

    # --------------------------------------------------------
    # Деньги
    # --------------------------------------------------------

    if setting_type == "money":
        normalized = raw_value.replace(
            ",",
            ".",
        )

        try:
            amount = Decimal(
                normalized
            )
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
                "❌ Максимальное значение — "
                "$1 000 000."
            )
            return

        # Проверяем min <= max с учётом
        # ещё не сохранённых override.
        if key == "min_topup_usd":
            current_max = await _get_setting(
                "max_topup_usd"
            )

            max_value = Decimal(
                current_max
                or _default_value(
                    "max_topup_usd"
                )
            )

            if amount > max_value:
                await message.answer(
                    "❌ Минимальная сумма не может "
                    "быть больше максимальной."
                )
                return

        elif key == "max_topup_usd":
            current_min = await _get_setting(
                "min_topup_usd"
            )

            min_value = Decimal(
                current_min
                or _default_value(
                    "min_topup_usd"
                )
            )

            if amount < min_value:
                await message.answer(
                    "❌ Максимальная сумма не может "
                    "быть меньше минимальной."
                )
                return

        value = f"{amount:.2f}"

    # --------------------------------------------------------
    # Текст
    # --------------------------------------------------------

    elif setting_type == "text":
        try:
            validate_text_length(
                raw_value,
                max_length=2048,
                field_name="Значение настройки",
            )
        except (
            ValueError,
            TypeError,
        ) as exc:
            await message.answer(
                f"❌ Некорректное значение:\n{exc}"
            )
            return

        if key == "topup_assets":
            assets = [
                item.strip().upper()
                for item in raw_value.split(",")
                if item.strip()
            ]

            if not assets:
                await message.answer(
                    "❌ Нужно указать хотя бы "
                    "один актив."
                )
                return

            allowed_assets = {
                "USDT",
                "TON",
                "BTC",
                "ETH",
            }

            invalid_assets = [
                asset
                for asset in assets
                if asset not in allowed_assets
            ]

            if invalid_assets:
                await message.answer(
                    "❌ Неподдерживаемые активы:\n"
                    + ", ".join(
                        invalid_assets
                    )
                    + "\n\n"
                    "Разрешены: "
                    + ", ".join(
                        sorted(
                            allowed_assets
                        )
                    )
                )
                return

            assets = list(
                dict.fromkeys(assets)
            )

            value = ",".join(assets)

        else:
            value = raw_value

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
            "Ошибка сохранения настройки "
            "пополнения {}",
            key,
        )

        await message.answer(
            "❌ Не удалось сохранить настройку."
        )
        return

    await state.clear()

    title = TOPUP_SETTINGS[key]["title"]

    await message.answer(
        "✅ <b>Настройка изменена</b>\n\n"
        f"{title}: "
        f"<code>{_display_value(key, value)}</code>"
    )


# ============================================================
# Сброс
# ============================================================

@router.callback_query(
    AdminCB.filter(
        F.action == "topup_settings_reset"
    )
)
async def topup_settings_reset(
    callback: CallbackQuery,
) -> None:
    """Сбрасывает настройку к значению из .env."""
    if not callback.from_user:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(
            callback.data
        )
        key = str(
            parsed.get(
                "setting_key",
                "",
            )
        )
    except (
        ValueError,
        TypeError,
        KeyError,
    ):
        key = ""

    if key not in TOPUP_SETTINGS:
        await callback.answer(
            "Настройка не найдена.",
            show_alert=True,
        )
        return

    try:
        await _reset_setting(
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
    AdminCB.filter(
        F.action == "topup_settings_refresh"
    )
)
async def topup_settings_refresh(
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

        await callback.answer(
            "Обновлено"
        )

    except Exception:
        logger.exception(
            "Ошибка обновления настроек пополнения"
        )

        await callback.answer(
            "Ошибка обновления.",
            show_alert=True,
        )


# ============================================================
# Отмена FSM
# ============================================================

@router.message(Command("cancel"))
async def topup_settings_cancel(
    message: Message,
    state: FSMContext,
) -> None:
    """Отменяет изменение настройки."""
    if not message.from_user:
        return

    if not _is_admin(message.from_user.id):
        return

    current_state = await state.get_state()

    if (
        current_state
        != AdminTopupSettingsStates.waiting_for_value.state
    ):
        return

    await state.clear()

    await message.answer(
        "❌ Изменение настройки отменено."
    )


__all__ = ["router"]