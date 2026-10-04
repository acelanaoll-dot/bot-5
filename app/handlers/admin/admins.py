from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.admin import AdminCB
from app.database.models import Admin, AdminAction, AdminLog, User
from app.states.admin import AdminManagementStates


router = Router(name="admin_admins")


# ============================================================
# Вспомогательные функции
# ============================================================


async def _get_current_admin(
    session: AsyncSession,
    telegram_id: int,
) -> Admin | None:
    """
    Получает активную запись администратора по Telegram ID.
    """

    result = await session.execute(
        select(Admin)
        .join(User, User.id == Admin.user_id)
        .where(
            User.telegram_id == telegram_id,
            User.is_admin.is_(True),
            Admin.is_active.is_(True),
        )
        .limit(1)
    )

    return result.scalar_one_or_none()


async def _get_admin_by_user_id(
    session: AsyncSession,
    user_id: int,
) -> Admin | None:
    result = await session.execute(
        select(Admin)
        .where(Admin.user_id == user_id)
        .limit(1)
    )

    return result.scalar_one_or_none()


async def _get_user_by_telegram_id(
    session: AsyncSession,
    telegram_id: int,
) -> User | None:
    result = await session.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .limit(1)
    )

    return result.scalar_one_or_none()


async def _count_active_admins(
    session: AsyncSession,
) -> int:
    result = await session.execute(
        select(func.count(Admin.id))
        .join(User, User.id == Admin.user_id)
        .where(
            Admin.is_active.is_(True),
            User.is_admin.is_(True),
        )
    )

    return int(result.scalar_one() or 0)


async def _write_admin_log(
    session: AsyncSession,
    *,
    admin_id: int,
    action: AdminAction,
    target_id: int | None = None,
    description: str | None = None,
    metadata: dict | None = None,
) -> None:
    log_entry = AdminLog(
        admin_id=admin_id,
        action=action,
        target_type="user" if target_id is not None else None,
        target_id=target_id,
        description=description,
        metadata_json=metadata,
    )

    session.add(log_entry)


def _admins_keyboard(
    admins: list[tuple[Admin, User]],
) -> InlineKeyboardMarkup:
    """
    Клавиатура управления администраторами.
    """

    rows: list[list[InlineKeyboardButton]] = []

    for admin, user in admins:
        name = (
            user.username
            or user.first_name
            or str(user.telegram_id)
        )

        status = "🟢" if admin.is_active and user.is_admin else "🔴"

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{status} {name}",
                    callback_data=AdminCB(
                        action="admin_open",
                        user_id=user.id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌",
                    callback_data=AdminCB(
                        action="admin_remove",
                        user_id=user.id,
                    ).pack(),
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить администратора",
                callback_data=AdminCB(
                    action="admin_add",
                ).pack(),
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=AdminCB(
                    action="back",
                ).pack(),
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _admin_profile_keyboard(
    user_id: int,
    is_active: bool,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    "❌ Удалить администратора"
                    if is_active
                    else "✅ Восстановить администратора"
                ),
                callback_data=AdminCB(
                    action=(
                        "admin_remove"
                        if is_active
                        else "admin_restore"
                    ),
                    user_id=user_id,
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ К списку",
                callback_data=AdminCB(
                    action="admins",
                ).pack(),
            )
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_admins(
    message: Message,
    session: AsyncSession,
) -> None:
    result = await session.execute(
        select(Admin, User)
        .join(User, User.id == Admin.user_id)
        .order_by(
            Admin.is_active.desc(),
            Admin.created_at.asc(),
        )
    )

    rows = list(result.all())

    text = (
        "👮 <b>Администраторы</b>\n\n"
        f"Всего записей: <b>{len(rows)}</b>\n\n"
    )

    if not rows:
        text += "Администраторов пока нет."
    else:
        for index, (admin, user) in enumerate(rows, start=1):
            name = (
                f"@{user.username}"
                if user.username
                else (
                    user.first_name
                    or str(user.telegram_id)
                )
            )

            status = (
                "🟢 активен"
                if admin.is_active and user.is_admin
                else "🔴 отключён"
            )

            text += (
                f"{index}. {name}\n"
                f"   ID: <code>{user.telegram_id}</code>\n"
                f"   Роль: <code>{admin.role}</code>\n"
                f"   Статус: {status}\n\n"
            )

    await message.edit_text(
        text,
        reply_markup=_admins_keyboard(rows),
    )


# ============================================================
# Проверка доступа
# ============================================================


async def _require_admin(
    event: Message | CallbackQuery,
    session: AsyncSession,
) -> Admin | None:
    telegram_id = event.from_user.id

    admin = await _get_current_admin(
        session,
        telegram_id,
    )

    if admin is None:
        if isinstance(event, CallbackQuery):
            await event.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
        else:
            await event.answer("⛔ Доступ запрещён.")

        return None

    return admin


# ============================================================
# Команда /admins
# ============================================================


@router.message(Command("admins"))
async def admins_command(
    message: Message,
    session: AsyncSession,
) -> None:
    admin = await _require_admin(message, session)

    if admin is None:
        return

    try:
        await _show_admins(
            message,
            session,
        )
    except Exception:
        logger.exception(
            "Ошибка открытия управления администраторами"
        )
        await message.answer(
            "❌ Не удалось загрузить список администраторов."
        )


# ============================================================
# Открытие раздела
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "admins")
)
async def admins_open(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    del callback_data

    admin = await _require_admin(
        callback,
        session,
    )

    if admin is None:
        return

    try:
        await _show_admins(
            callback.message,
            session,
        )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия списка администраторов"
        )

        await callback.answer(
            "❌ Ошибка.",
            show_alert=True,
        )


# ============================================================
# Добавление администратора
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "admin_add")
)
async def admin_add_start(
    callback: CallbackQuery,
    callback_data: AdminCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    del callback_data

    admin = await _require_admin(
        callback,
        session,
    )

    if admin is None:
        return

    await state.set_state(
        AdminManagementStates.waiting_for_telegram_id
    )

    await callback.message.edit_text(
        "➕ <b>Добавление администратора</b>\n\n"
        "Отправь Telegram ID пользователя.\n\n"
        "Например:\n"
        "<code>123456789</code>\n\n"
        "Для отмены отправь /cancel."
    )

    await callback.answer()


@router.message(
    AdminManagementStates.waiting_for_telegram_id
)
async def admin_add_process(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    admin = await _require_admin(
        message,
        session,
    )

    if admin is None:
        await state.clear()
        return

    raw_value = (message.text or "").strip()

    if raw_value.lower() == "/cancel":
        await state.clear()

        await message.answer(
            "❌ Добавление администратора отменено."
        )
        return

    try:
        telegram_id = int(raw_value)
    except ValueError:
        await message.answer(
            "❌ Telegram ID должен состоять только из цифр.\n\n"
            "Пример: <code>123456789</code>"
        )
        return

    if telegram_id <= 0:
        await message.answer(
            "❌ Некорректный Telegram ID."
        )
        return

    try:
        user = await _get_user_by_telegram_id(
            session,
            telegram_id,
        )

        if user is None:
            await message.answer(
                "❌ Пользователь с таким Telegram ID "
                "ещё не зарегистрирован в боте.\n\n"
                "Сначала пользователь должен открыть бота "
                "и выполнить /start."
            )
            return

        existing_admin = await _get_admin_by_user_id(
            session,
            user.id,
        )

        if existing_admin is not None:
            if existing_admin.is_active and user.is_admin:
                await message.answer(
                    "⚠️ Этот пользователь уже является "
                    "активным администратором."
                )
                return

            existing_admin.is_active = True
            existing_admin.role = "admin"
            user.is_admin = True

            await _write_admin_log(
                session,
                admin_id=admin.id,
                action=AdminAction.UPDATE,
                target_id=user.id,
                description=(
                    "Администратор восстановлен"
                ),
            )

            await session.commit()
            await state.clear()

            await message.answer(
                "✅ Администратор восстановлен.\n\n"
                f"Telegram ID: <code>{user.telegram_id}</code>"
            )
            return

        new_admin = Admin(
            user_id=user.id,
            role="admin",
            is_active=True,
        )

        session.add(new_admin)

        # Flush нужен, чтобы получить ID нового администратора
        # до создания записи аудита.
        await session.flush()

        user.is_admin = True

        await _write_admin_log(
            session,
            admin_id=admin.id,
            action=AdminAction.CREATE,
            target_id=user.id,
            description=(
                "Добавлен новый администратор"
            ),
            metadata={
                "telegram_id": user.telegram_id,
                "role": "admin",
            },
        )

        await session.commit()
        await state.clear()

        await message.answer(
            "✅ <b>Администратор добавлен.</b>\n\n"
            f"Пользователь: "
            f"<code>{user.telegram_id}</code>\n"
            f"Роль: <code>admin</code>"
        )

    except Exception:
        await session.rollback()
        logger.exception(
            "Ошибка добавления администратора: {}",
            telegram_id,
        )

        await message.answer(
            "❌ Не удалось добавить администратора.\n"
            "Проверь логи."
        )


# ============================================================
# Просмотр администратора
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "admin_open")
)
async def admin_open(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    admin = await _require_admin(
        callback,
        session,
    )

    if admin is None:
        return

    target_user = await session.get(
        User,
        callback_data.user_id,
    )

    if target_user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    target_admin = await _get_admin_by_user_id(
        session,
        target_user.id,
    )

    if target_admin is None:
        await callback.answer(
            "Запись администратора не найдена.",
            show_alert=True,
        )
        return

    name = (
        f"@{target_user.username}"
        if target_user.username
        else (
            target_user.first_name
            or "Без имени"
        )
    )

    status = (
        "🟢 активен"
        if target_admin.is_active and target_user.is_admin
        else "🔴 отключён"
    )

    text = (
        "👤 <b>Администратор</b>\n\n"
        f"Имя: <b>{name}</b>\n"
        f"Telegram ID: <code>{target_user.telegram_id}</code>\n"
        f"User ID: <code>{target_user.id}</code>\n"
        f"Роль: <code>{target_admin.role}</code>\n"
        f"Статус: {status}\n"
        f"Добавлен: <code>{target_admin.created_at}</code>"
    )

    await callback.message.edit_text(
        text,
        reply_markup=_admin_profile_keyboard(
            target_user.id,
            target_admin.is_active and target_user.is_admin,
        ),
    )

    await callback.answer()


# ============================================================
# Удаление администратора
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "admin_remove")
)
async def admin_remove(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    current_admin = await _require_admin(
        callback,
        session,
    )

    if current_admin is None:
        return

    target_user = await session.get(
        User,
        callback_data.user_id,
    )

    if target_user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    target_admin = await _get_admin_by_user_id(
        session,
        target_user.id,
    )

    if target_admin is None:
        await callback.answer(
            "Этот пользователь не является администратором.",
            show_alert=True,
        )
        return

    # Нельзя удалить самого себя.
    if target_admin.id == current_admin.id:
        await callback.answer(
            "⛔ Нельзя удалить самого себя.",
            show_alert=True,
        )
        return

    # Нельзя оставить систему вообще без администраторов.
    active_admins = await _count_active_admins(
        session
    )

    if active_admins <= 1:
        await callback.answer(
            "⛔ Нельзя удалить последнего администратора.",
            show_alert=True,
        )
        return

    if not target_admin.is_active and not target_user.is_admin:
        await callback.answer(
            "Администратор уже отключён.",
            show_alert=True,
        )
        return

    try:
        target_admin.is_active = False
        target_user.is_admin = False

        await _write_admin_log(
            session,
            admin_id=current_admin.id,
            action=AdminAction.DELETE,
            target_id=target_user.id,
            description=(
                "Администратор отключён"
            ),
            metadata={
                "telegram_id": target_user.telegram_id,
                "role": target_admin.role,
            },
        )

        await session.commit()

        await callback.answer(
            "✅ Администратор отключён."
        )

        await _show_admins(
            callback.message,
            session,
        )

    except Exception:
        await session.rollback()

        logger.exception(
            "Ошибка удаления администратора: {}",
            target_user.telegram_id,
        )

        await callback.answer(
            "❌ Не удалось отключить администратора.",
            show_alert=True,
        )


# ============================================================
# Восстановление администратора
# ============================================================


@router.callback_query(
    AdminCB.filter(F.action == "admin_restore")
)
async def admin_restore(
    callback: CallbackQuery,
    callback_data: AdminCB,
    session: AsyncSession,
) -> None:
    current_admin = await _require_admin(
        callback,
        session,
    )

    if current_admin is None:
        return

    target_user = await session.get(
        User,
        callback_data.user_id,
    )

    if target_user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    target_admin = await _get_admin_by_user_id(
        session,
        target_user.id,
    )

    if target_admin is None:
        await callback.answer(
            "Запись администратора не найдена.",
            show_alert=True,
        )
        return

    if target_admin.is_active and target_user.is_admin:
        await callback.answer(
            "Администратор уже активен."
        )
        return

    try:
        target_admin.is_active = True
        target_user.is_admin = True

        await _write_admin_log(
            session,
            admin_id=current_admin.id,
            action=AdminAction.UPDATE,
            target_id=target_user.id,
            description=(
                "Администратор восстановлен"
            ),
        )

        await session.commit()

        await callback.answer(
            "✅ Администратор восстановлен."
        )

        await _show_admins(
            callback.message,
            session,
        )

    except Exception:
        await session.rollback()

        logger.exception(
            "Ошибка восстановления администратора: {}",
            target_user.telegram_id,
        )

        await callback.answer(
            "❌ Не удалось восстановить администратора.",
            show_alert=True,
        )


# ============================================================
# Отмена FSM
# ============================================================


@router.message(
    AdminManagementStates.waiting_for_telegram_id,
    Command("cancel"),
)
async def admin_add_cancel(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    await message.answer(
        "❌ Добавление администратора отменено."
    )


__all__ = [
    "router",
]