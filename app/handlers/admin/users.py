from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import (
    BalanceTransaction,
    User,
    UserStatus,
)
from app.keyboards.admin import admin_main_keyboard
from app.services.balance import BalanceService
from app.states.admin import AdminUserStates
from app.utils.money import format_usd


router = Router(name="admin_users")


def _is_admin(user_id: int) -> bool:
    """Проверяет права администратора."""
    return user_id in settings.admin_ids


def _format_user(user: User) -> str:
    """Формирует краткую информацию о пользователе."""
    username = (
        f"@{user.username}"
        if user.username
        else "без username"
    )

    full_name = " ".join(
        part
        for part in (
            user.first_name,
            user.last_name,
        )
        if part
    ).strip()

    if not full_name:
        full_name = "Без имени"

    status = (
        "🚫 Заблокирован"
        if user.status == UserStatus.BANNED
        else "✅ Активен"
    )

    return (
        f"👤 <b>{full_name}</b>\n"
        f"🆔 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"🔗 {username}\n"
        f"📌 Статус: {status}\n"
        f"💰 Баланс: <b>{format_usd(user.balance_usd)}</b>\n"
        f"🌐 Язык: <code>{user.language}</code>\n"
        f"📅 Регистрация: "
        f"<code>{user.created_at:%Y-%m-%d %H:%M}</code>\n"
        f"🕐 Последняя активность: "
        f"<code>{user.last_seen_at:%Y-%m-%d %H:%M}</code>"
    )


async def _get_user_by_id(
    session: AsyncSession,
    user_id: int,
) -> User | None:
    """Получает пользователя по внутреннему ID."""
    return await session.scalar(
        select(User).where(User.id == user_id)
    )


async def _get_user_by_telegram_id(
    session: AsyncSession,
    telegram_id: int,
) -> User | None:
    """Получает пользователя по Telegram ID."""
    return await session.scalar(
        select(User).where(
            User.telegram_id == telegram_id
        )
    )


async def _show_user(
    message: Message | CallbackQuery,
    user: User,
) -> None:
    """Показывает карточку пользователя."""
    text = _format_user(user)

    if isinstance(message, CallbackQuery):
        if message.message is not None:
            await message.message.edit_text(
                text,
                reply_markup=admin_main_keyboard(),
            )
        await message.answer()
        return

    await message.answer(
        text,
        reply_markup=admin_main_keyboard(),
    )


@router.message(Command("users"))
async def users_command(
    message: Message,
    state: FSMContext,
) -> None:
    """Открывает поиск пользователей."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    await state.set_state(
        AdminUserStates.waiting_for_search
    )

    await message.answer(
        "👥 <b>Пользователи</b>\n\n"
        "Введите Telegram ID, username или имя "
        "для поиска.\n\n"
        "Для отмены отправьте /cancel."
    )


@router.callback_query(
    AdminCB.filter(F.action == "users")
)
async def users_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Открывает поиск пользователей из меню."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    await state.set_state(
        AdminUserStates.waiting_for_search
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "👥 <b>Поиск пользователя</b>\n\n"
            "Введите Telegram ID, username или имя.\n\n"
            "Для отмены отправьте /cancel."
        )

    await callback.answer()


@router.message(
    AdminUserStates.waiting_for_search,
    F.text,
)
async def search_users(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Ищет пользователей по Telegram ID, username или имени."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    query = message.text.strip()

    if query.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "Поиск отменён.",
            reply_markup=admin_main_keyboard(),
        )
        return

    if not query:
        await message.answer(
            "Введите поисковый запрос."
        )
        return

    try:
        conditions = []

        if query.isdigit():
            telegram_id = int(query)
            conditions.append(
                User.telegram_id == telegram_id
            )

        normalized = query.lstrip("@")

        conditions.extend(
            [
                User.username.ilike(
                    f"%{normalized}%"
                ),
                User.first_name.ilike(
                    f"%{query}%"
                ),
                User.last_name.ilike(
                    f"%{query}%"
                ),
            ]
        )

        result = await session.scalars(
            select(User)
            .where(or_(*conditions))
            .order_by(
                User.last_seen_at.desc(),
                User.id.desc(),
            )
            .limit(20)
        )

        users = list(result)

        if not users:
            await message.answer(
                "❌ Пользователи не найдены."
            )
            return

        lines = [
            "👥 <b>Результаты поиска</b>\n"
        ]

        for user in users:
            status = (
                "🚫"
                if user.status == UserStatus.BANNED
                else "✅"
            )

            username = (
                f"@{user.username}"
                if user.username
                else "без username"
            )

            lines.append(
                f"{status} "
                f"<b>{user.first_name or 'Без имени'}</b> "
                f"— {username}\n"
                f"ID: <code>{user.telegram_id}</code> | "
                f"Баланс: <b>{format_usd(user.balance_usd)}</b>\n"
            )

        await message.answer(
            "\n".join(lines),
            reply_markup=admin_main_keyboard(),
        )

        await state.clear()

    except Exception:
        logger.exception(
            "Ошибка поиска пользователей: admin={}",
            message.from_user.id,
        )
        await message.answer(
            "❌ Ошибка при поиске пользователей."
        )


@router.callback_query(
    AdminCB.filter(F.action == "user_open")
)
async def user_open_callback(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    """Открывает карточку пользователя."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    if not callback_data.user_id:
        await callback.answer(
            "Пользователь не указан.",
            show_alert=True,
        )
        return

    try:
        user = await _get_user_by_id(
            session,
            callback_data.user_id,
        )

        if user is None:
            await callback.answer(
                "Пользователь не найден.",
                show_alert=True,
            )
            return

        await _show_user(callback, user)

    except Exception:
        logger.exception(
            "Ошибка открытия пользователя: "
            "admin={}, user_id={}",
            callback.from_user.id,
            callback_data.user_id,
        )
        await callback.answer(
            "Ошибка загрузки пользователя.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "user_ban")
)
async def user_ban_callback(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    """Блокирует пользователя."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    user = await _get_user_by_id(
        session,
        callback_data.user_id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    if user.telegram_id in settings.admin_ids:
        await callback.answer(
            "Нельзя заблокировать администратора.",
            show_alert=True,
        )
        return

    try:
        user.status = UserStatus.BANNED
        await session.flush()

        await callback.answer(
            "Пользователь заблокирован."
        )

        if callback.message is not None:
            await _show_user(
                callback,
                user,
            )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка блокировки пользователя: "
            "admin={}, user_id={}",
            callback.from_user.id,
            user.id,
        )
        await callback.answer(
            "Ошибка блокировки.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "user_unban")
)
async def user_unban_callback(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    """Разблокирует пользователя."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    user = await _get_user_by_id(
        session,
        callback_data.user_id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    try:
        user.status = UserStatus.ACTIVE
        await session.flush()

        await callback.answer(
            "Пользователь разблокирован."
        )

        if callback.message is not None:
            await _show_user(
                callback,
                user,
            )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка разблокировки пользователя: "
            "admin={}, user_id={}",
            callback.from_user.id,
            user.id,
        )
        await callback.answer(
            "Ошибка разблокировки.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "user_balance")
)
async def user_balance_callback(
    callback: CallbackQuery,
    callback_data: AdminCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Запрашивает сумму изменения баланса."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    user = await _get_user_by_id(
        session,
        callback_data.user_id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    await state.set_state(
        AdminUserStates.waiting_for_balance_amount
    )
    await state.update_data(
        target_user_id=user.id,
    )

    if callback.message is not None:
        await callback.message.edit_text(
            "💰 <b>Изменение баланса</b>\n\n"
            f"Пользователь: "
            f"<code>{user.telegram_id}</code>\n"
            f"Текущий баланс: "
            f"<b>{format_usd(user.balance_usd)}</b>\n\n"
            "Введите сумму:\n"
            "• положительное число — зачислить;\n"
            "• отрицательное число — списать.\n\n"
            "Пример: <code>10</code> или "
            "<code>-5.50</code>\n\n"
            "Для отмены: /cancel"
        )

    await callback.answer()


@router.message(
    AdminUserStates.waiting_for_balance_amount,
    F.text,
)
async def process_balance_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Изменяет баланс пользователя."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    value = message.text.strip()

    if value.lower() == "/cancel":
        await state.clear()
        await message.answer(
            "Изменение баланса отменено.",
            reply_markup=admin_main_keyboard(),
        )
        return

    try:
        amount = Decimal(value.replace(",", "."))

        if amount == 0:
            await message.answer(
                "Сумма не может быть равна нулю."
            )
            return

        if not amount.is_finite():
            raise ValueError

        amount = amount.quantize(
            Decimal("0.00000001")
        )

        if abs(amount) > Decimal("1000000"):
            await message.answer(
                "Сумма слишком большая."
            )
            return

        data = await state.get_data()
        target_user_id = int(
            data.get("target_user_id", 0)
        )

        if not target_user_id:
            await state.clear()
            await message.answer(
                "❌ Пользователь не указан.",
                reply_markup=admin_main_keyboard(),
            )
            return

        user = await _get_user_by_id(
            session,
            target_user_id,
        )

        if user is None:
            await state.clear()
            await message.answer(
                "❌ Пользователь не найден.",
                reply_markup=admin_main_keyboard(),
            )
            return

        balance_service = BalanceService(session)

        await balance_service.change_balance(
            user_id=user.id,
            amount_usd=amount,
            transaction_type=(
                BalanceTransactionType.ADMIN_ADJUSTMENT
            ),
            description=(
                f"Изменение баланса администратором "
                f"{message.from_user.id}"
            ),
            idempotency_key=(
                f"admin_balance:"
                f"{message.from_user.id}:"
                f"{user.id}:"
                f"{message.message_id}"
            ),
        )

        await session.flush()

        new_balance = user.balance_usd

        await state.clear()

        await message.answer(
            "✅ <b>Баланс изменён</b>\n\n"
            f"Пользователь: "
            f"<code>{user.telegram_id}</code>\n"
            f"Изменение: "
            f"<b>{format_usd(amount)}</b>\n"
            f"Новый баланс: "
            f"<b>{format_usd(new_balance)}</b>",
            reply_markup=admin_main_keyboard(),
        )

    except ValueError:
        await message.answer(
            "❌ Некорректная сумма.\n"
            "Пример: <code>10</code> или "
            "<code>-5.50</code>"
        )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка изменения баланса: "
            "admin={}, message_id={}",
            message.from_user.id,
            message.message_id,
        )
        await message.answer(
            "❌ Не удалось изменить баланс."
        )


@router.callback_query(
    AdminCB.filter(F.action == "user_history")
)
async def user_history_callback(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    """Показывает историю операций пользователя."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    user = await _get_user_by_id(
        session,
        callback_data.user_id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    try:
        result = await session.scalars(
            select(BalanceTransaction)
            .where(
                BalanceTransaction.user_id == user.id
            )
            .order_by(
                BalanceTransaction.created_at.desc()
            )
            .limit(20)
        )

        transactions = list(result)

        if not transactions:
            text = (
                "📜 <b>История баланса</b>\n\n"
                "Операций пока нет."
            )
        else:
            lines = [
                "📜 <b>История баланса</b>\n"
            ]

            for transaction in transactions:
                sign = (
                    "+"
                    if transaction.amount_usd >= 0
                    else ""
                )

                lines.append(
                    f"{transaction.created_at:%d.%m.%Y %H:%M} "
                    f"— <b>{sign}"
                    f"{format_usd(transaction.amount_usd)}"
                    f"</b>\n"
                    f"Тип: <code>{transaction.type.value}"
                    f"</code>\n"
                )

                if transaction.description:
                    lines.append(
                        f"└ {transaction.description}\n"
                    )

            text = "\n".join(lines)

        if callback.message is not None:
            await callback.message.edit_text(
                text,
                reply_markup=admin_main_keyboard(),
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка истории пользователя: "
            "admin={}, user_id={}",
            callback.from_user.id,
            user.id,
        )
        await callback.answer(
            "Ошибка загрузки истории.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "user_by_telegram")
)
async def user_by_telegram_callback(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    """Открывает пользователя по Telegram ID."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    if not callback_data.telegram_id:
        await callback.answer(
            "Telegram ID не указан.",
            show_alert=True,
        )
        return

    try:
        user = await _get_user_by_telegram_id(
            session,
            callback_data.telegram_id,
        )

        if user is None:
            await callback.answer(
                "Пользователь не найден.",
                show_alert=True,
            )
            return

        await _show_user(callback, user)

    except Exception:
        logger.exception(
            "Ошибка поиска пользователя по Telegram ID: "
            "admin={}, telegram_id={}",
            callback.from_user.id,
            callback_data.telegram_id,
        )
        await callback.answer(
            "Ошибка поиска пользователя.",
            show_alert=True,
        )


__all__ = ["router"]