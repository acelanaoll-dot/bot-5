from __future__ import annotations

"""
Администрирование платёжных шлюзов.

Важно:
- секретные ключи/API-токены не редактируются и не показываются через бота;
- состояние шлюзов хранится в PaymentGatewaySetting;
- секреты берутся только из .env / settings;
- каждое действие повторно проверяет права администратора;
- действия пишутся в AdminLog.
"""

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import (
    Admin,
    AdminAction,
    AdminLog,
    PaymentGateway,
    PaymentGatewaySetting,
    User,
)
from app.states.admin import AdminGatewayStates
from app.utils.validation import validate_text_length


router = Router(name="admin_gateways")


# ============================================================
# Вспомогательные функции
# ============================================================


async def _get_user(
    session: AsyncSession,
    telegram_id: int,
) -> User | None:
    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def _is_admin(
    session: AsyncSession,
    telegram_id: int,
) -> bool:
    user = await _get_user(session, telegram_id)

    if user is None:
        return False

    configured_admin_ids = set(settings.admin_ids)

    if telegram_id in configured_admin_ids:
        return True

    if not user.is_admin:
        return False

    result = await session.execute(
        select(Admin).where(
            Admin.user_id == user.id,
            Admin.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none() is not None


async def _get_admin(
    session: AsyncSession,
    telegram_id: int,
) -> Admin | None:
    user = await _get_user(session, telegram_id)

    if user is None:
        return None

    result = await session.execute(
        select(Admin).where(
            Admin.user_id == user.id,
            Admin.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def _write_admin_log(
    session: AsyncSession,
    telegram_id: int,
    action: AdminAction,
    gateway: PaymentGateway,
    description: str,
) -> None:
    try:
        admin = await _get_admin(session, telegram_id)

        if admin is None:
            return

        session.add(
            AdminLog(
                admin_id=admin.id,
                action=action,
                target_type="payment_gateway",
                description=description,
                metadata_json={
                    "gateway": gateway.value,
                },
            )
        )
    except Exception:
        logger.exception("Не удалось записать AdminLog для платёжного шлюза")


def _gateway_title(gateway: PaymentGateway) -> str:
    if gateway == PaymentGateway.CRYPTOPAY:
        return "CryptoPay"

    if gateway == PaymentGateway.NOWPAYMENTS:
        return "NOWPayments"

    return gateway.value


def _gateway_configured(gateway: PaymentGateway) -> bool:
    """
    Проверяет наличие соответствующей конфигурации.

    Сами секреты здесь никогда не выводятся.
    """

    try:
        if gateway == PaymentGateway.CRYPTOPAY:
            return settings.has_cryptopay

        if gateway == PaymentGateway.NOWPAYMENTS:
            return settings.has_nowpayments

    except Exception:
        logger.exception(
            "Ошибка проверки конфигурации шлюза %s",
            gateway.value,
        )

    return False


def _gateway_keyboard(
    settings_map: dict[PaymentGateway, PaymentGatewaySetting],
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    for gateway in (
        PaymentGateway.CRYPTOPAY,
        PaymentGateway.NOWPAYMENTS,
    ):
        item = settings_map.get(gateway)

        if item is None:
            enabled = False
            priority = 100
        else:
            enabled = bool(item.is_enabled)
            priority = item.priority

        configured = _gateway_configured(gateway)

        state = "🟢 включён" if enabled else "🔴 выключен"
        config_state = "✅ настроен" if configured else "⚠️ нет ключа"

        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{_gateway_title(gateway)} · "
                        f"{state}"
                    ),
                    callback_data=AdminCB(
                        action="gateway_open",
                        gateway=gateway.value,
                    ).pack(),
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"⚙️ {config_state} · приоритет {priority}",
                    callback_data=AdminCB(
                        action="gateway_status",
                        gateway=gateway.value,
                    ).pack(),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=AdminCB(
                    action="gateways_refresh",
                ).pack(),
            ),
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=AdminCB(
                    action="back",
                ).pack(),
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _gateway_detail_keyboard(
    gateway: PaymentGateway,
    enabled: bool,
) -> InlineKeyboardMarkup:
    toggle_text = (
        "🔴 Выключить"
        if enabled
        else "🟢 Включить"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=AdminCB(
                        action="gateway_toggle",
                        gateway=gateway.value,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔢 Изменить приоритет",
                    callback_data=AdminCB(
                        action="gateway_priority",
                        gateway=gateway.value,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К шлюзам",
                    callback_data=AdminCB(
                        action="gateways",
                    ).pack(),
                ),
            ],
        ]
    )


def _gateway_text(
    gateway: PaymentGateway,
    item: PaymentGatewaySetting | None,
) -> str:
    enabled = bool(item.is_enabled) if item else False
    priority = item.priority if item else 100
    configured = _gateway_configured(gateway)

    state = "🟢 Включён" if enabled else "🔴 Выключен"
    config_state = (
        "✅ API-ключ присутствует"
        if configured
        else "⚠️ API-ключ не настроен"
    )

    return (
        f"💳 <b>{_gateway_title(gateway)}</b>\n\n"
        f"Статус: {state}\n"
        f"Конфигурация: {config_state}\n"
        f"Приоритет: <b>{priority}</b>\n\n"
        "Секретные ключи через бота не показываются.\n"
        "Для изменения API-ключей используй .env.\n\n"
        "Приоритет используется менеджером платежей "
        "при выборе доступного шлюза."
    )


async def _load_gateway_settings(
    session: AsyncSession,
) -> dict[PaymentGateway, PaymentGatewaySetting]:
    result = await session.execute(
        select(PaymentGatewaySetting).order_by(
            PaymentGatewaySetting.priority.asc(),
            PaymentGatewaySetting.id.asc(),
        )
    )

    rows = result.scalars().all()

    return {
        PaymentGateway(row.gateway): row
        for row in rows
    }


async def _get_or_create_gateway_setting(
    session: AsyncSession,
    gateway: PaymentGateway,
) -> PaymentGatewaySetting:
    result = await session.execute(
        select(PaymentGatewaySetting).where(
            PaymentGatewaySetting.gateway == gateway.value
        )
    )

    item = result.scalar_one_or_none()

    if item is not None:
        return item

    item = PaymentGatewaySetting(
        gateway=gateway.value,
        is_enabled=False,
        priority=100,
        settings_json={},
    )

    session.add(item)
    await session.flush()

    return item


def _parse_priority(value: str) -> int:
    value = value.strip()

    if not value:
        raise ValueError("Приоритет не указан.")

    try:
        priority = int(value)
    except ValueError as exc:
        raise ValueError(
            "Приоритет должен быть целым числом."
        ) from exc

    if priority < 1:
        raise ValueError("Приоритет должен быть не меньше 1.")

    if priority > 10000:
        raise ValueError(
            "Приоритет не должен превышать 10000."
        )

    return priority


# ============================================================
# Главное меню шлюзов
# ============================================================


async def _show_gateways(
    message: Message,
    session: AsyncSession,
) -> None:
    gateway_settings = await _load_gateway_settings(session)

    text = (
        "💳 <b>Платёжные шлюзы</b>\n\n"
        "Управление доступностью CryptoPay и NOWPayments.\n\n"
        "Секретные API-ключи не отображаются в админке."
    )

    await message.answer(
        text,
        reply_markup=_gateway_keyboard(gateway_settings),
    )


@router.message(Command("gateways"))
async def gateways_command(
    message: Message,
    session: AsyncSession,
) -> None:
    if not await _is_admin(session, message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    try:
        await _show_gateways(message, session)
    except Exception:
        logger.exception("Ошибка открытия раздела платёжных шлюзов")
        await message.answer(
            "❌ Не удалось открыть настройки платёжных шлюзов."
        )


@router.callback_query(AdminCB.filter(F.action == "gateways"))
async def gateways_open(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    del callback_data

    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    try:
        gateway_settings = await _load_gateway_settings(session)

        text = (
            "💳 <b>Платёжные шлюзы</b>\n\n"
            "Выбери шлюз для управления:"
        )

        if callback.message:
            await callback.message.edit_text(
                text,
                reply_markup=_gateway_keyboard(gateway_settings),
            )

        await callback.answer()

    except Exception:
        logger.exception("Ошибка открытия шлюзов")
        await callback.answer(
            "❌ Ошибка",
            show_alert=True,
        )


@router.callback_query(AdminCB.filter(F.action == "gateways_refresh"))
async def gateways_refresh(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    del callback_data

    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    try:
        gateway_settings = await _load_gateway_settings(session)

        if callback.message:
            await callback.message.edit_reply_markup(
                reply_markup=_gateway_keyboard(gateway_settings),
            )

        await callback.answer("Обновлено.")

    except Exception:
        logger.exception("Ошибка обновления списка шлюзов")
        await callback.answer(
            "❌ Ошибка",
            show_alert=True,
        )


# ============================================================
# Открытие конкретного шлюза
# ============================================================


@router.callback_query(AdminCB.filter(F.action == "gateway_open"))
async def gateway_open(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    try:
        gateway = PaymentGateway(callback_data.gateway)

        item = await _get_or_create_gateway_setting(
            session,
            gateway,
        )

        await session.commit()

        if callback.message:
            await callback.message.edit_text(
                _gateway_text(gateway, item),
                reply_markup=_gateway_detail_keyboard(
                    gateway,
                    item.is_enabled,
                ),
            )

        await callback.answer()

    except ValueError:
        await callback.answer(
            "❌ Неизвестный платёжный шлюз.",
            show_alert=True,
        )
    except Exception:
        await session.rollback()
        logger.exception("Ошибка открытия шлюза")
        await callback.answer(
            "❌ Ошибка",
            show_alert=True,
        )


# ============================================================
# Статус / конфигурация
# ============================================================


@router.callback_query(AdminCB.filter(F.action == "gateway_status"))
async def gateway_status(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    try:
        gateway = PaymentGateway(callback_data.gateway)

        item = await _get_or_create_gateway_setting(
            session,
            gateway,
        )

        await session.commit()

        configured = _gateway_configured(gateway)

        if configured:
            message = (
                f"{_gateway_title(gateway)}: "
                "конфигурация найдена."
            )
        else:
            message = (
                f"{_gateway_title(gateway)}: "
                "API-ключ не настроен в .env."
            )

        await callback.answer(
            message,
            show_alert=True,
        )

        if callback.message:
            await callback.message.edit_text(
                _gateway_text(gateway, item),
                reply_markup=_gateway_detail_keyboard(
                    gateway,
                    item.is_enabled,
                ),
            )

    except ValueError:
        await callback.answer(
            "❌ Неизвестный шлюз.",
            show_alert=True,
        )
    except Exception:
        await session.rollback()
        logger.exception("Ошибка проверки шлюза")
        await callback.answer(
            "❌ Ошибка",
            show_alert=True,
        )


# ============================================================
# Включение / выключение
# ============================================================


@router.callback_query(AdminCB.filter(F.action == "gateway_toggle"))
async def gateway_toggle(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    try:
        gateway = PaymentGateway(callback_data.gateway)

        item = await _get_or_create_gateway_setting(
            session,
            gateway,
        )

        item.is_enabled = not item.is_enabled

        await _write_admin_log(
            session=session,
            telegram_id=callback.from_user.id,
            action=AdminAction.SETTING_CHANGE,
            gateway=gateway,
            description=(
                f"Шлюз {_gateway_title(gateway)} "
                f"{'включён' if item.is_enabled else 'выключен'}."
            ),
        )

        await session.commit()

        if callback.message:
            await callback.message.edit_text(
                _gateway_text(gateway, item),
                reply_markup=_gateway_detail_keyboard(
                    gateway,
                    item.is_enabled,
                ),
            )

        await callback.answer(
            "Статус изменён."
        )

    except ValueError:
        await callback.answer(
            "❌ Неизвестный шлюз.",
            show_alert=True,
        )
    except Exception:
        await session.rollback()
        logger.exception("Ошибка изменения статуса шлюза")
        await callback.answer(
            "❌ Не удалось изменить статус.",
            show_alert=True,
        )


# ============================================================
# Изменение приоритета
# ============================================================


@router.callback_query(AdminCB.filter(F.action == "gateway_priority"))
async def gateway_priority(
    callback: CallbackQuery,
    callback_data: AdminCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not await _is_admin(session, callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    try:
        gateway = PaymentGateway(callback_data.gateway)

        await state.set_state(
            AdminGatewayStates.waiting_for_value
        )
        await state.update_data(
            gateway=gateway.value,
            setting_type="priority",
        )

        await callback.answer()

        if callback.message:
            await callback.message.answer(
                f"🔢 Введи новый приоритет для "
                f"<b>{_gateway_title(gateway)}</b>.\n\n"
                "Допустимый диапазон: 1–10000.\n"
                "Чем меньше число — тем выше приоритет.\n\n"
                "Для отмены: /cancel"
            )

    except ValueError:
        await callback.answer(
            "❌ Неизвестный шлюз.",
            show_alert=True,
        )
    except Exception:
        logger.exception("Ошибка запуска изменения приоритета")
        await callback.answer(
            "❌ Ошибка",
            show_alert=True,
        )


@router.message(AdminGatewayStates.waiting_for_value)
async def gateway_priority_value(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not await _is_admin(session, message.from_user.id):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    try:
        raw_value = (message.text or "").strip()

        if not raw_value:
            await message.answer(
                "❌ Введи число от 1 до 10000."
            )
            return

        if len(raw_value) > settings.security_max_text_length:
            await message.answer(
                "❌ Слишком длинное значение."
            )
            return

        validation_result = validate_text_length(
            raw_value,
            max_length=32,
        )

        if not validation_result.is_valid:
            await message.answer(
                "❌ Некорректное значение."
            )
            return

        data = await state.get_data()

        gateway_raw = data.get("gateway")
        setting_type = data.get("setting_type")

        if setting_type != "priority":
            await state.clear()
            await message.answer(
                "❌ Состояние настройки устарело. "
                "Открой раздел шлюзов заново."
            )
            return

        gateway = PaymentGateway(gateway_raw)
        priority = _parse_priority(raw_value)

        item = await _get_or_create_gateway_setting(
            session,
            gateway,
        )

        old_priority = item.priority
        item.priority = priority

        await _write_admin_log(
            session=session,
            telegram_id=message.from_user.id,
            action=AdminAction.SETTING_CHANGE,
            gateway=gateway,
            description=(
                f"Изменён приоритет шлюза "
                f"{_gateway_title(gateway)}: "
                f"{old_priority} -> {priority}."
            ),
        )

        await session.commit()
        await state.clear()

        await message.answer(
            f"✅ Приоритет "
            f"<b>{_gateway_title(gateway)}</b> "
            f"изменён на <b>{priority}</b>."
        )

        await _show_gateways(
            message,
            session,
        )

    except ValueError as exc:
        await session.rollback()

        await message.answer(
            f"❌ {str(exc)}"
        )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка сохранения приоритета платёжного шлюза"
        )

        await message.answer(
            "❌ Не удалось сохранить приоритет."
        )


# ============================================================
# Отмена FSM
# ============================================================


@router.message(Command("cancel"))
async def cancel_gateway_state(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    current_state = await state.get_state()

    if current_state != AdminGatewayStates.waiting_for_value.state:
        return

    if not await _is_admin(session, message.from_user.id):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    await state.clear()

    await message.answer(
        "❌ Изменение настройки отменено."
    )


__all__ = ["router"]