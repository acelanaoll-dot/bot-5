from __future__ import annotations

"""
Администрирование промокодов.

Поддерживает:
- список промокодов;
- создание;
- включение/выключение;
- просмотр;
- удаление;
- процентную скидку;
- фиксированную скидку в USD;
- максимальное количество использований;
- минимальную сумму заказа;
- срок действия.

Все изменения выполняются через БД и повторно проверяют права администратора.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import (
    Admin,
    AdminAction,
    AdminLog,
    PromoCode,
    PromoCodeUsage,
    User,
)
from app.states.admin import AdminPromoStates


router = Router(name="admin_promo")


# ============================================================
# Администратор
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

    if telegram_id in set(settings.admin_ids):
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
    promo_id: int | None,
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
                target_type="promo_code",
                target_id=promo_id,
                description=description,
                metadata_json={
                    "promo_id": promo_id,
                },
            )
        )

    except Exception:
        logger.exception(
            "Не удалось записать AdminLog для промокода"
        )


# ============================================================
# Валидация
# ============================================================


def _normalize_code(value: str) -> str:
    code = value.strip().upper()

    if not code:
        raise ValueError("Промокод не может быть пустым.")

    if len(code) > 128:
        raise ValueError(
            "Промокод не должен быть длиннее 128 символов."
        )

    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "0123456789"
        "_-"
    )

    if any(char not in allowed for char in code):
        raise ValueError(
            "Промокод может содержать только "
            "латинские буквы, цифры, '_' и '-'."
        )

    return code


def _parse_decimal(
    value: str,
    *,
    field_name: str,
) -> Decimal:
    normalized = value.strip().replace(",", ".")

    try:
        number = Decimal(normalized)
    except InvalidOperation as exc:
        raise ValueError(
            f"{field_name}: введи корректное число."
        ) from exc

    if not number.is_finite():
        raise ValueError(
            f"{field_name}: некорректное число."
        )

    return number


def _parse_percent(value: str) -> Decimal:
    number = _parse_decimal(
        value,
        field_name="Процент",
    )

    if number <= 0:
        raise ValueError(
            "Процент скидки должен быть больше 0."
        )

    if number > 100:
        raise ValueError(
            "Процент скидки не может быть больше 100."
        )

    return number.quantize(Decimal("0.0001"))


def _parse_amount(value: str) -> Decimal:
    number = _parse_decimal(
        value,
        field_name="Сумма",
    )

    if number <= 0:
        raise ValueError(
            "Сумма скидки должна быть больше 0."
        )

    if number > Decimal("1000000"):
        raise ValueError(
            "Сумма скидки слишком большая."
        )

    return number.quantize(Decimal("0.00000001"))


def _parse_max_uses(value: str) -> int | None:
    normalized = value.strip().lower()

    if normalized in {
        "",
        "0",
        "нет",
        "none",
        "unlimited",
        "безлимит",
    }:
        return None

    try:
        number = int(normalized)
    except ValueError as exc:
        raise ValueError(
            "Количество использований должно быть целым числом."
        ) from exc

    if number <= 0:
        raise ValueError(
            "Количество использований должно быть больше 0."
        )

    if number > 10_000_000:
        raise ValueError(
            "Количество использований слишком большое."
        )

    return number


def _parse_expires_at(value: str) -> datetime | None:
    normalized = value.strip()

    if normalized.lower() in {
        "",
        "нет",
        "none",
        "без срока",
        "безлимит",
    }:
        return None

    formats = (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    )

    parsed: datetime | None = None

    for date_format in formats:
        try:
            parsed = datetime.strptime(
                normalized,
                date_format,
            )
            break
        except ValueError:
            continue

    if parsed is None:
        raise ValueError(
            "Дата должна быть в формате "
            "ДД.ММ.ГГГГ или ДД.ММ.ГГГГ ЧЧ:ММ."
        )

    return parsed.replace(tzinfo=timezone.utc)


# ============================================================
# Форматирование
# ============================================================


def _discount_text(promo: PromoCode) -> str:
    if promo.discount_percent is not None:
        return f"{promo.discount_percent}%"

    if promo.discount_amount_usd is not None:
        return f"${promo.discount_amount_usd}"

    return "не задана"


def _status_text(promo: PromoCode) -> str:
    return "🟢 активен" if promo.is_active else "🔴 выключен"


def _promo_text(
    promo: PromoCode,
    uses_count: int,
) -> str:
    if promo.max_uses is None:
        uses = f"{uses_count} / ∞"
    else:
        uses = f"{uses_count} / {promo.max_uses}"

    if promo.expires_at is None:
        expires = "без срока"
    else:
        expires = promo.expires_at.astimezone(
            timezone.utc
        ).strftime("%d.%m.%Y %H:%M UTC")

    minimum = f"${promo.min_order_amount_usd}"

    return (
        f"🎟 <b>{promo.code}</b>\n\n"
        f"Статус: {_status_text(promo)}\n"
        f"Скидка: <b>{_discount_text(promo)}</b>\n"
        f"Использований: <b>{uses}</b>\n"
        f"Мин. заказ: <b>{minimum}</b>\n"
        f"Срок: <b>{expires}</b>\n"
        f"Создан: "
        f"{promo.created_at.astimezone(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"
    )


# ============================================================
# Клавиатуры
# ============================================================


def _main_keyboard(
    promos: list[PromoCode],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for promo in promos:
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{'🟢' if promo.is_active else '🔴'} "
                        f"{promo.code} · {_discount_text(promo)}"
                    ),
                    callback_data=AdminCB(
                        action="promo_open",
                        promo_id=promo.id,
                    ).pack(),
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Создать промокод",
                callback_data=AdminCB(
                    action="promo_create",
                ).pack(),
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=AdminCB(
                    action="promo",
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

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _detail_keyboard(
    promo: PromoCode,
) -> InlineKeyboardMarkup:
    toggle_text = (
        "🔴 Выключить"
        if promo.is_active
        else "🟢 Включить"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=toggle_text,
                    callback_data=AdminCB(
                        action="promo_toggle",
                        promo_id=promo.id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=AdminCB(
                        action="promo_delete",
                        promo_id=promo.id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К промокодам",
                    callback_data=AdminCB(
                        action="promo",
                    ).pack(),
                )
            ],
        ]
    )


# ============================================================
# Получение списка
# ============================================================


async def _get_promos(
    session: AsyncSession,
) -> list[PromoCode]:
    result = await session.execute(
        select(PromoCode)
        .order_by(
            PromoCode.is_active.desc(),
            PromoCode.created_at.desc(),
        )
        .limit(100)
    )

    return list(result.scalars().all())


async def _get_promo(
    session: AsyncSession,
    promo_id: int,
) -> PromoCode | None:
    result = await session.execute(
        select(PromoCode).where(
            PromoCode.id == promo_id
        )
    )

    return result.scalar_one_or_none()


async def _get_usage_count(
    session: AsyncSession,
    promo_id: int,
) -> int:
    result = await session.execute(
        select(func.count(PromoCodeUsage.id)).where(
            PromoCodeUsage.promo_code_id == promo_id
        )
    )

    return int(result.scalar_one() or 0)


# ============================================================
# Главное меню
# ============================================================


async def _show_promo_menu(
    message: Message,
    session: AsyncSession,
) -> None:
    promos = await _get_promos(session)

    if not promos:
        text = (
            "🎟 <b>Промокоды</b>\n\n"
            "Промокодов пока нет."
        )
    else:
        text = (
            "🎟 <b>Промокоды</b>\n\n"
            f"Всего отображается: <b>{len(promos)}</b>\n\n"
            "Выбери промокод:"
        )

    await message.answer(
        text,
        reply_markup=_main_keyboard(promos),
    )


@router.message(Command("promo"))
async def promo_command(
    message: Message,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        message.from_user.id,
    ):
        await message.answer("⛔ Доступ запрещён.")
        return

    try:
        await _show_promo_menu(
            message,
            session,
        )
    except Exception:
        logger.exception(
            "Ошибка открытия раздела промокодов"
        )
        await message.answer(
            "❌ Не удалось открыть промокоды."
        )


@router.callback_query(AdminCB.filter(F.action == "promo"))
async def promo_menu(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    del callback_data

    if not await _is_admin(
        session,
        callback.from_user.id,
    ):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return

    try:
        promos = await _get_promos(session)

        text = (
            "🎟 <b>Промокоды</b>\n\n"
            f"Всего: <b>{len(promos)}</b>\n\n"
            "Выбери промокод:"
        )

        if callback.message:
            await callback.message.edit_text(
                text,
                reply_markup=_main_keyboard(promos),
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка обновления промокодов"
        )
        await callback.answer(
            "❌ Ошибка",
            show_alert=True,
        )


# ============================================================
# Создание
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "promo_create")
)
async def promo_create_start(
    callback: CallbackQuery,
    callback_data: AdminCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    del callback_data

    if not await _is_admin(
        session,
        callback.from_user.id,
    ):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(
        AdminPromoStates.waiting_for_code
    )

    await callback.answer()

    if callback.message:
        await callback.message.answer(
            "🎟 <b>Создание промокода</b>\n\n"
            "Введи код промокода.\n"
            "Например: <code>WELCOME10</code>\n\n"
            "Разрешены латинские буквы, цифры, "
            "<code>_</code> и <code>-</code>.\n\n"
            "Для отмены: /cancel"
        )


@router.message(AdminPromoStates.waiting_for_code)
async def promo_create_code(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        message.from_user.id,
    ):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    try:
        code = _normalize_code(
            message.text or ""
        )

        exists = await session.execute(
            select(PromoCode.id).where(
                func.upper(PromoCode.code) == code
            )
        )

        if exists.scalar_one_or_none() is not None:
            await message.answer(
                "❌ Такой промокод уже существует."
            )
            return

        await state.update_data(code=code)
        await state.set_state(
            AdminPromoStates.waiting_for_discount_type
        )

        await message.answer(
            "Выбери тип скидки:\n\n"
            "1 — процентная\n"
            "2 — фиксированная в USD\n\n"
            "Напиши <code>1</code> или <code>2</code>."
        )

    except ValueError as exc:
        await message.answer(f"❌ {exc}")
    except Exception:
        logger.exception(
            "Ошибка создания промокода на этапе code"
        )
        await message.answer(
            "❌ Не удалось обработать промокод."
        )


@router.message(
    AdminPromoStates.waiting_for_discount_type
)
async def promo_create_discount_type(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        message.from_user.id,
    ):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    value = (message.text or "").strip().lower()

    if value not in {
        "1",
        "2",
        "процент",
        "процентная",
        "фикс",
        "фиксированная",
        "usd",
    }:
        await message.answer(
            "❌ Введи <code>1</code> "
            "для процентов или <code>2</code> "
            "для фиксированной скидки."
        )
        return

    is_percent = value in {
        "1",
        "процент",
        "процентная",
    }

    await state.update_data(
        discount_type=(
            "percent"
            if is_percent
            else "amount"
        )
    )

    await state.set_state(
        AdminPromoStates.waiting_for_discount_value
    )

    if is_percent:
        await message.answer(
            "Введи размер скидки в процентах.\n"
            "Например: <code>10</code>\n\n"
            "Диапазон: больше 0 и до 100."
        )
    else:
        await message.answer(
            "Введи размер скидки в USD.\n"
            "Например: <code>5.50</code>"
        )


@router.message(
    AdminPromoStates.waiting_for_discount_value
)
async def promo_create_discount_value(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        message.from_user.id,
    ):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    data = await state.get_data()

    discount_type = data.get("discount_type")

    try:
        if discount_type == "percent":
            value = _parse_percent(
                message.text or ""
            )
        elif discount_type == "amount":
            value = _parse_amount(
                message.text or ""
            )
        else:
            await state.clear()
            await message.answer(
                "❌ Состояние создания промокода "
                "устарело. Начни заново."
            )
            return

        await state.update_data(
            discount_value=str(value)
        )

        await state.set_state(
            AdminPromoStates.waiting_for_max_uses
        )

        await message.answer(
            "🔢 Введи максимальное количество "
            "использований.\n\n"
            "Например: <code>100</code>\n"
            "Или <code>0</code> для безлимитного."
        )

    except ValueError as exc:
        await message.answer(f"❌ {exc}")


@router.message(
    AdminPromoStates.waiting_for_max_uses
)
async def promo_create_max_uses(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        message.from_user.id,
    ):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    try:
        max_uses = _parse_max_uses(
            message.text or ""
        )

        await state.update_data(
            max_uses=max_uses
        )

        await state.set_state(
            AdminPromoStates.waiting_for_expires_at
        )

        await message.answer(
            "📅 Введи срок действия.\n\n"
            "Форматы:\n"
            "<code>31.12.2026</code>\n"
            "<code>31.12.2026 23:59</code>\n\n"
            "Или напиши <code>нет</code> "
            "для бессрочного промокода."
        )

    except ValueError as exc:
        await message.answer(f"❌ {exc}")


@router.message(
    AdminPromoStates.waiting_for_expires_at
)
async def promo_create_expires_at(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        message.from_user.id,
    ):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    try:
        expires_at = _parse_expires_at(
            message.text or ""
        )

        data = await state.get_data()

        code = data.get("code")
        discount_type = data.get("discount_type")
        discount_value_raw = data.get("discount_value")
        max_uses = data.get("max_uses")

        if not code or not discount_type:
            await state.clear()
            await message.answer(
                "❌ Данные создания промокода потеряны."
            )
            return

        discount_value = Decimal(
            str(discount_value_raw)
        )

        if discount_type == "percent":
            discount_percent = discount_value
            discount_amount_usd = None
        else:
            discount_percent = None
            discount_amount_usd = discount_value

        promo = PromoCode(
            code=code,
            discount_percent=discount_percent,
            discount_amount_usd=discount_amount_usd,
            max_uses=max_uses,
            min_order_amount_usd=Decimal("0"),
            expires_at=expires_at,
            is_active=True,
        )

        session.add(promo)

        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            await state.clear()

            await message.answer(
                "❌ Такой промокод уже существует."
            )
            return

        await _write_admin_log(
            session=session,
            telegram_id=message.from_user.id,
            action=AdminAction.CREATE,
            promo_id=promo.id,
            description=(
                f"Создан промокод {promo.code} "
                f"со скидкой {_discount_text(promo)}."
            ),
        )

        await session.commit()
        await state.clear()

        await message.answer(
            "✅ Промокод создан.\n\n"
            f"Код: <b>{promo.code}</b>\n"
            f"Скидка: <b>{_discount_text(promo)}</b>\n"
            f"Использований: "
            f"<b>{'∞' if max_uses is None else max_uses}</b>"
        )

        await _show_promo_menu(
            message,
            session,
        )

    except (InvalidOperation, ValueError) as exc:
        await session.rollback()
        await message.answer(
            f"❌ Некорректные данные: {exc}"
        )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка создания промокода"
        )
        await state.clear()

        await message.answer(
            "❌ Не удалось создать промокод."
        )


# ============================================================
# Открытие
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "promo_open")
)
async def promo_open(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        callback.from_user.id,
    ):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return

    promo = await _get_promo(
        session,
        callback_data.promo_id,
    )

    if promo is None:
        await callback.answer(
            "❌ Промокод не найден.",
            show_alert=True,
        )
        return

    try:
        uses_count = await _get_usage_count(
            session,
            promo.id,
        )

        text = _promo_text(
            promo,
            uses_count,
        )

        if callback.message:
            await callback.message.edit_text(
                text,
                reply_markup=_detail_keyboard(promo),
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия промокода"
        )
        await callback.answer(
            "❌ Ошибка",
            show_alert=True,
        )


# ============================================================
# Включение / выключение
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "promo_toggle")
)
async def promo_toggle(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        callback.from_user.id,
    ):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return

    try:
        promo = await _get_promo(
            session,
            callback_data.promo_id,
        )

        if promo is None:
            await callback.answer(
                "❌ Промокод не найден.",
                show_alert=True,
            )
            return

        promo.is_active = not promo.is_active

        await _write_admin_log(
            session=session,
            telegram_id=callback.from_user.id,
            action=AdminAction.UPDATE,
            promo_id=promo.id,
            description=(
                f"Промокод {promo.code} "
                f"{'включён' if promo.is_active else 'выключен'}."
            ),
        )

        await session.commit()

        uses_count = await _get_usage_count(
            session,
            promo.id,
        )

        if callback.message:
            await callback.message.edit_text(
                _promo_text(
                    promo,
                    uses_count,
                ),
                reply_markup=_detail_keyboard(
                    promo
                ),
            )

        await callback.answer(
            "Статус изменён."
        )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка переключения промокода"
        )
        await callback.answer(
            "❌ Не удалось изменить статус.",
            show_alert=True,
        )


# ============================================================
# Удаление
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "promo_delete")
)
async def promo_delete(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    if not await _is_admin(
        session,
        callback.from_user.id,
    ):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return

    try:
        promo = await _get_promo(
            session,
            callback_data.promo_id,
        )

        if promo is None:
            await callback.answer(
                "❌ Промокод уже удалён.",
                show_alert=True,
            )
            return

        uses_count = await _get_usage_count(
            session,
            promo.id,
        )

        if uses_count > 0:
            await callback.answer(
                "❌ Промокод уже использовался. "
                "Удаление запрещено — выключи его.",
                show_alert=True,
            )
            return

        promo_id = promo.id
        promo_code = promo.code

        await _write_admin_log(
            session=session,
            telegram_id=callback.from_user.id,
            action=AdminAction.DELETE,
            promo_id=promo_id,
            description=(
                f"Удалён промокод {promo_code}."
            ),
        )

        await session.delete(promo)
        await session.commit()

        promos = await _get_promos(session)

        if callback.message:
            await callback.message.edit_text(
                "🎟 <b>Промокоды</b>\n\n"
                "Промокод удалён.\n\n"
                "Выбери другой промокод:",
                reply_markup=_main_keyboard(promos),
            )

        await callback.answer(
            "Промокод удалён."
        )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка удаления промокода"
        )
        await callback.answer(
            "❌ Не удалось удалить промокод.",
            show_alert=True,
        )


# ============================================================
# Отмена FSM
# ============================================================


@router.message(Command("cancel"))
async def promo_cancel(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    current_state = await state.get_state()

    promo_states = {
        AdminPromoStates.waiting_for_code.state,
        AdminPromoStates.waiting_for_discount_type.state,
        AdminPromoStates.waiting_for_discount_value.state,
        AdminPromoStates.waiting_for_max_uses.state,
        AdminPromoStates.waiting_for_expires_at.state,
    }

    if current_state not in promo_states:
        return

    if not await _is_admin(
        session,
        message.from_user.id,
    ):
        await state.clear()
        await message.answer("⛔ Доступ запрещён.")
        return

    await state.clear()

    await message.answer(
        "❌ Создание промокода отменено."
    )


__all__ = ["router"]