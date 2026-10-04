from __future__ import annotations

"""
Административный раздел журнала действий.

Возможности:
- просмотр журнала действий администраторов;
- постраничная навигация;
- поиск по действию, описанию, типу объекта,
  ID объекта, username и Telegram ID администратора;
- просмотр подробной записи;
- очистка поиска.

Журнал не удаляется из этого раздела намеренно:
он нужен для аудита административных действий.
"""

import json
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger
from sqlalchemy import String, cast, func, or_, select

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import Admin, AdminLog, User
from app.database.session import get_session
from app.states.admin import AdminLogsStates


router = Router(name="admin_logs")


# ============================================================================
# Константы
# ============================================================================

LOGS_PER_PAGE = 10


# ============================================================================
# Общие helpers
# ============================================================================


def _is_admin(telegram_id: int) -> bool:
    """Базовая проверка Telegram ID администратора."""

    return telegram_id in settings.admin_ids


def _format_action(action: object) -> str:
    """Возвращает человекочитаемое название действия."""

    value = getattr(action, "value", action)

    labels = {
        "create": "➕ Создание",
        "update": "✏️ Изменение",
        "delete": "🗑 Удаление",
        "view": "👁 Просмотр",
        "ban": "🚫 Бан",
        "unban": "✅ Разбан",
        "credit": "💰 Зачисление",
        "debit": "💸 Списание",
        "refund": "↩️ Возврат",
        "deliver": "📦 Выдача",
        "cancel": "❌ Отмена",
        "broadcast": "📢 Рассылка",
        "backup": "💾 Backup",
        "login": "🔐 Вход",
        "setting_change": "⚙️ Настройка",
    }

    return labels.get(
        str(value),
        f"🔹 {escape(str(value))}",
    )


def _format_target(
    target_type: str | None,
    target_id: int | None,
) -> str:
    """Форматирует объект административного действия."""

    if not target_type and target_id is None:
        return "—"

    target = escape(target_type or "object")

    if target_id is None:
        return f"<code>{target}</code>"

    return (
        f"<code>{target}</code> "
        f"#{target_id}"
    )


def _format_admin(
    user: User | None,
) -> str:
    """Форматирует администратора."""

    if user is None:
        return "Неизвестный администратор"

    name_parts = [
        user.first_name,
        user.last_name,
    ]

    full_name = " ".join(
        part.strip()
        for part in name_parts
        if part and part.strip()
    )

    if not full_name:
        full_name = "Без имени"

    username = (
        f"@{user.username}"
        if user.username
        else None
    )

    if username:
        return (
            f"{escape(full_name)} "
            f"({escape(username)})"
        )

    return escape(full_name)


def _format_metadata(
    metadata: object,
) -> str:
    """Форматирует JSON metadata для Telegram."""

    if metadata is None:
        return ""

    try:
        if isinstance(metadata, str):
            parsed = json.loads(metadata)
        else:
            parsed = metadata

        if not isinstance(parsed, dict):
            return escape(str(parsed))

        pretty = json.dumps(
            parsed,
            ensure_ascii=False,
            indent=2,
            default=str,
        )

        return escape(pretty)

    except Exception:
        return escape(str(metadata))


def _format_log_short(
    log: AdminLog,
    admin_user: User | None,
) -> str:
    """Краткое представление записи журнала."""

    description = (
        log.description
        or "Без описания"
    )

    if len(description) > 120:
        description = description[:117] + "..."

    return (
        f"<b>#{log.id}</b> "
        f"{_format_action(log.action)}\n"
        f"👤 {_format_admin(admin_user)}\n"
        f"🎯 {_format_target(log.target_type, log.target_id)}\n"
        f"📝 {escape(description)}\n"
        f"🕐 {log.created_at.strftime('%d.%m.%Y %H:%M:%S')}"
    )


def _logs_keyboard(
    *,
    page: int,
    total_pages: int,
    search: str | None,
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру журнала."""

    rows: list[list[InlineKeyboardButton]] = []

    rows.append(
        [
            InlineKeyboardButton(
                text="🔎 Поиск",
                callback_data=AdminCB(
                    action="logs_search",
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data=AdminCB(
                    action="logs",
                    page=page,
                ).pack(),
            ),
        ]
    )

    if search:
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Сбросить поиск",
                    callback_data=AdminCB(
                        action="logs_clear_search",
                    ).pack(),
                )
            ]
        )

    if total_pages > 1:
        navigation: list[InlineKeyboardButton] = []

        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="⬅️",
                    callback_data=AdminCB(
                        action="logs",
                        page=page - 1,
                        setting_key=search or "",
                    ).pack(),
                )
            )

        navigation.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=AdminCB(
                    action="logs",
                    page=page,
                    setting_key=search or "",
                ).pack(),
            )
        )

        if page < total_pages - 1:
            navigation.append(
                InlineKeyboardButton(
                    text="➡️",
                    callback_data=AdminCB(
                        action="logs",
                        page=page + 1,
                        setting_key=search or "",
                    ).pack(),
                )
            )

        rows.append(navigation)

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def _log_detail_keyboard(
    page: int,
    search: str | None,
) -> InlineKeyboardMarkup:
    """Клавиатура подробного просмотра записи."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ К журналу",
                    callback_data=AdminCB(
                        action="logs",
                        page=page,
                        setting_key=search or "",
                    ).pack(),
                )
            ]
        ]
    )


def _search_condition(
    search: str,
):
    """Формирует условие поиска."""

    pattern = f"%{search}%"

    return or_(
        cast(AdminLog.id, String).ilike(pattern),
        cast(AdminLog.action, String).ilike(pattern),
        AdminLog.target_type.ilike(pattern),
        cast(AdminLog.target_id, String).ilike(pattern),
        AdminLog.description.ilike(pattern),
        User.username.ilike(pattern),
        User.first_name.ilike(pattern),
        User.last_name.ilike(pattern),
        cast(User.telegram_id, String).ilike(pattern),
    )


# ============================================================================
# Получение журнала
# ============================================================================


async def _get_logs(
    *,
    page: int = 0,
    search: str | None = None,
) -> tuple[list[tuple[AdminLog, User | None]], int]:
    """Возвращает страницу журнала и общее количество страниц."""

    page = max(page, 0)

    async with get_session() as session:
        base_query = (
            select(AdminLog.id)
            .join(Admin, AdminLog.admin_id == Admin.id)
            .join(User, Admin.user_id == User.id)
        )

        if search:
            base_query = base_query.where(
                _search_condition(search)
            )

        count_result = await session.execute(
            select(func.count()).select_from(
                base_query.subquery()
            )
        )

        total = int(
            count_result.scalar_one() or 0
        )

        total_pages = max(
            1,
            (total + LOGS_PER_PAGE - 1)
            // LOGS_PER_PAGE,
        )

        if page >= total_pages:
            page = total_pages - 1

        query = (
            select(AdminLog, User)
            .join(
                Admin,
                AdminLog.admin_id == Admin.id,
            )
            .join(
                User,
                Admin.user_id == User.id,
            )
            .order_by(
                AdminLog.created_at.desc(),
                AdminLog.id.desc(),
            )
            .offset(page * LOGS_PER_PAGE)
            .limit(LOGS_PER_PAGE)
        )

        if search:
            query = query.where(
                _search_condition(search)
            )

        result = await session.execute(query)

        rows = list(result.all())

        return rows, total_pages


async def _get_log(
    log_id: int,
) -> tuple[AdminLog, User | None] | None:
    """Возвращает одну запись журнала."""

    async with get_session() as session:
        result = await session.execute(
            select(AdminLog, User)
            .join(
                Admin,
                AdminLog.admin_id == Admin.id,
            )
            .join(
                User,
                Admin.user_id == User.id,
            )
            .where(AdminLog.id == log_id)
        )

        return result.first()


# ============================================================================
# Текст журнала
# ============================================================================


async def _logs_text(
    *,
    page: int,
    search: str | None,
) -> tuple[str, InlineKeyboardMarkup]:
    """Формирует экран журнала."""

    rows, total_pages = await _get_logs(
        page=page,
        search=search,
    )

    total_text = (
        "🔎 "
        f"<b>Поиск:</b> "
        f"<code>{escape(search)}</code>\n\n"
        if search
        else ""
    )

    if not rows:
        text = (
            "📜 <b>Журнал действий</b>\n\n"
            f"{total_text}"
            "Записей не найдено."
        )

        return (
            text,
            _logs_keyboard(
                page=0,
                total_pages=1,
                search=search,
            ),
        )

    chunks = [
        "📜 <b>Журнал действий</b>",
        "",
    ]

    if search:
        chunks.extend(
            [
                "🔎 "
                f"<b>Поиск:</b> "
                f"<code>{escape(search)}</code>",
                "",
            ]
        )

    chunks.append(
        f"Страница: <b>{page + 1}/{total_pages}</b>"
    )
    chunks.append("")

    for index, (log, admin_user) in enumerate(
        rows
    ):
        chunks.append(
            _format_log_short(
                log,
                admin_user,
            )
        )

        if index != len(rows) - 1:
            chunks.append("")
            chunks.append("────────────")

    return (
        "\n".join(chunks),
        _logs_keyboard(
            page=page,
            total_pages=total_pages,
            search=search,
        ),
    )


# ============================================================================
# /logs
# ============================================================================


@router.message(Command("logs"))
async def logs_command(
    message: Message,
) -> None:
    """Открывает журнал административных действий."""

    if not message.from_user or not _is_admin(
        message.from_user.id
    ):
        await message.answer("❌ Нет доступа.")
        return

    try:
        text, keyboard = await _logs_text(
            page=0,
            search=None,
        )

        await message.answer(
            text,
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception(
            "Ошибка открытия журнала"
        )

        await message.answer(
            "❌ Не удалось загрузить журнал."
        )


# ============================================================================
# Открытие журнала из админ-меню / пагинация
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "logs")
)
async def logs_open(
    callback: CallbackQuery,
) -> None:
    """Открывает журнал или переключает страницу."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(
            callback.data
        )

        page = max(
            0,
            int(parsed.get("page", 0)),
        )

        search_raw = str(
            parsed.get("setting_key", "")
        ).strip()

        search = (
            search_raw[:200]
            if search_raw
            else None
        )

        text, keyboard = await _logs_text(
            page=page,
            search=search,
        )

        if callback.message:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия журнала"
        )

        await callback.answer(
            "Ошибка загрузки журнала",
            show_alert=True,
        )


# ============================================================================
# Открытие конкретной записи
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "log_open")
)
async def log_open(
    callback: CallbackQuery,
) -> None:
    """Показывает подробности записи журнала."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(
            callback.data
        )

        log_id = int(
            parsed.get("page", 0)
        )

        page = int(
            parsed.get("user_id", 0)
        )

        search_raw = str(
            parsed.get("setting_key", "")
        ).strip()

        search = (
            search_raw[:200]
            if search_raw
            else None
        )

        row = await _get_log(log_id)

        if row is None:
            await callback.answer(
                "Запись не найдена",
                show_alert=True,
            )
            return

        log, admin_user = row

        metadata_text = _format_metadata(
            log.metadata_json
        )

        lines = [
            "📜 <b>Детали административного действия</b>",
            "",
            f"🆔 ID записи: <b>{log.id}</b>",
            f"⚙️ Действие: {_format_action(log.action)}",
            f"👤 Администратор: {_format_admin(admin_user)}",
            (
                "🎯 Объект: "
                f"{_format_target(log.target_type, log.target_id)}"
            ),
            "",
            "📝 <b>Описание:</b>",
            escape(
                log.description
                or "Нет описания"
            ),
            "",
            (
                "🕐 Создано: "
                f"<code>{log.created_at.strftime('%d.%m.%Y %H:%M:%S')}</code>"
            ),
        ]

        if metadata_text:
            lines.extend(
                [
                    "",
                    "📦 <b>Metadata:</b>",
                    f"<pre>{metadata_text}</pre>",
                ]
            )

        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=_log_detail_keyboard(
                page=page,
                search=search,
            ),
        )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия записи admin log"
        )

        await callback.answer(
            "Ошибка загрузки записи",
            show_alert=True,
        )


# ============================================================================
# Поиск
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "logs_search")
)
async def logs_search_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запускает поиск по журналу."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    await state.clear()
    await state.set_state(
        AdminLogsStates.waiting_for_search
    )

    await callback.message.edit_text(
        "🔎 <b>Поиск по журналу</b>\n\n"
        "Отправь текст для поиска.\n\n"
        "Можно искать по:\n"
        "• ID записи;\n"
        "• действию;\n"
        "• типу объекта;\n"
        "• ID объекта;\n"
        "• описанию;\n"
        "• username администратора;\n"
        "• имени администратора;\n"
        "• Telegram ID администратора.\n\n"
        "Для отмены отправь /cancel.",
    )

    await callback.answer()


@router.message(
    AdminLogsStates.waiting_for_search,
)
async def logs_search_message(
    message: Message,
    state: FSMContext,
) -> None:
    """Обрабатывает поисковый запрос."""

    if not message.from_user or not _is_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    search = (
        (message.text or "")
        .strip()
    )

    if not search:
        await message.answer(
            "❌ Поисковый запрос не может быть пустым."
        )
        return

    if search.startswith("/"):
        if search.lower() == "/cancel":
            await state.clear()

            text, keyboard = await _logs_text(
                page=0,
                search=None,
            )

            await message.answer(
                text,
                reply_markup=keyboard,
            )
            return

        await message.answer(
            "❌ Используй обычный текст для поиска "
            "или /cancel для отмены."
        )
        return

    if len(search) > 200:
        await message.answer(
            "❌ Поисковый запрос слишком длинный.\n"
            "Максимум — 200 символов."
        )
        return

    await state.clear()

    try:
        text, keyboard = await _logs_text(
            page=0,
            search=search,
        )

        await message.answer(
            text,
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception(
            "Ошибка поиска по admin logs"
        )

        await message.answer(
            "❌ Не удалось выполнить поиск."
        )


# ============================================================================
# Сброс поиска
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "logs_clear_search")
)
async def logs_clear_search(
    callback: CallbackQuery,
) -> None:
    """Сбрасывает фильтр поиска."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        text, keyboard = await _logs_text(
            page=0,
            search=None,
        )

        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
        )

        await callback.answer(
            "Поиск сброшен"
        )

    except Exception:
        logger.exception(
            "Ошибка сброса поиска logs"
        )

        await callback.answer(
            "Ошибка",
            show_alert=True,
        )


# ============================================================================
# Отмена поиска
# ============================================================================


@router.message(
    AdminLogsStates.waiting_for_search,
    Command("cancel"),
)
async def logs_cancel(
    message: Message,
    state: FSMContext,
) -> None:
    """Отменяет поиск."""

    if not message.from_user or not _is_admin(
        message.from_user.id
    ):
        await state.clear()
        return

    await state.clear()

    try:
        text, keyboard = await _logs_text(
            page=0,
            search=None,
        )

        await message.answer(
            text,
            reply_markup=keyboard,
        )

    except Exception:
        logger.exception(
            "Ошибка возврата из поиска logs"
        )

        await message.answer(
            "❌ Не удалось открыть журнал."
        )


__all__ = [
    "router",
]