from __future__ import annotations

"""
Административный раздел резервных копий.

Возможности:
- просмотр информации о backup;
- создание новой резервной копии;
- скачивание backup-файла;
- удаление конкретной копии;
- очистка старых копий;
- просмотр последней копии;
- просмотр общего размера backup-каталога.

Все операции доступны только администраторам.
"""

from datetime import datetime, timezone
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import Admin, AdminAction, AdminLog, User
from app.database.session import get_session
from app.services.backup import (
    BackupCreationError,
    BackupDisabledError,
    BackupError,
    BackupNotFoundError,
    BackupInfo,
    backup_service,
)
from app.states.admin import AdminBackupStates


router = Router(name="admin_backups")


# ============================================================================
# Общие helpers
# ============================================================================


def _is_admin(user_id: int) -> bool:
    """Базовая проверка Telegram ID администратора."""

    return user_id in settings.admin_ids


async def _get_admin(
    telegram_id: int,
) -> Admin | None:
    """Возвращает активную запись администратора."""

    async with get_session() as session:
        result = await session.execute(
            __import__("sqlalchemy").select(Admin)
            .join(User, Admin.user_id == User.id)
            .where(
                User.telegram_id == telegram_id,
                User.is_admin.is_(True),
                Admin.is_active.is_(True),
            )
        )

        return result.scalar_one_or_none()


async def _write_admin_log(
    telegram_id: int,
    action: AdminAction,
    description: str,
    target_id: int | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    """Записывает действие администратора."""

    try:
        async with get_session() as session:
            result = await session.execute(
                __import__("sqlalchemy").select(Admin)
                .join(User, Admin.user_id == User.id)
                .where(
                    User.telegram_id == telegram_id,
                    User.is_admin.is_(True),
                    Admin.is_active.is_(True),
                )
            )

            admin = result.scalar_one_or_none()

            if admin is None:
                return

            log_entry = AdminLog(
                admin_id=admin.id,
                action=action,
                target_type="backup",
                target_id=target_id,
                description=description,
                metadata_json=metadata,
            )

            session.add(log_entry)
            await session.commit()

    except Exception:
        logger.exception(
            "Не удалось записать admin log для backup"
        )


def _format_size(size_bytes: int) -> str:
    """Форматирует размер файла."""

    if size_bytes < 1024:
        return f"{size_bytes} Б"

    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} КБ"

    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} МБ"

    return f"{size_bytes / (1024 * 1024 * 1024):.2f} ГБ"


def _format_datetime(value: datetime) -> str:
    """Форматирует дату backup в UTC."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    value = value.astimezone(timezone.utc)

    return value.strftime("%d.%m.%Y %H:%M:%S UTC")


def _backup_button(
    text: str,
    action: str,
    *,
    setting_key: str = "",
) -> InlineKeyboardButton:
    """Создаёт callback-кнопку backup-раздела."""

    return InlineKeyboardButton(
        text=text,
        callback_data=AdminCB(
            action=action,
            setting_key=setting_key,
        ).pack(),
    )


def _backup_keyboard(
    backups: list[BackupInfo],
) -> InlineKeyboardMarkup:
    """Основная клавиатура backup-раздела."""

    rows: list[list[InlineKeyboardButton]] = []

    rows.append(
        [
            _backup_button(
                "💾 Создать backup",
                "backup_create",
            ),
            _backup_button(
                "🔄 Обновить",
                "backups",
            ),
        ]
    )

    if backups:
        latest = backups[0]

        rows.append(
            [
                _backup_button(
                    "📥 Скачать последний",
                    "backup_download",
                    setting_key=latest.filename,
                ),
            ]
        )

        for backup in backups[:10]:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"📦 {backup.filename} "
                            f"({_format_size(backup.size_bytes)})"
                        ),
                        callback_data=AdminCB(
                            action="backup_open",
                            setting_key=backup.filename,
                        ).pack(),
                    )
                ]
            )

    rows.append(
        [
            _backup_button(
                "🧹 Очистить старые",
                "backup_cleanup",
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def _backup_delete_keyboard(
    filename: str,
) -> InlineKeyboardMarkup:
    """Клавиатура конкретной резервной копии."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _backup_button(
                    "📥 Скачать",
                    "backup_download",
                    setting_key=filename,
                ),
            ],
            [
                _backup_button(
                    "🗑 Удалить",
                    "backup_delete",
                    setting_key=filename,
                ),
            ],
            [
                _backup_button(
                    "⬅️ К списку",
                    "backups",
                ),
            ],
        ]
    )


async def _backups_text() -> str:
    """Формирует текст главного экрана backup-раздела."""

    if not backup_service.enabled:
        return (
            "💾 <b>Резервные копии</b>\n\n"
            "❌ Резервное копирование отключено "
            "в настройках приложения."
        )

    try:
        backups = await backup_service.list_backups()
        total_size = await backup_service.get_backup_directory_size()
        latest = backups[0] if backups else None

    except Exception:
        logger.exception(
            "Ошибка получения информации о backup"
        )

        return (
            "💾 <b>Резервные копии</b>\n\n"
            "❌ Не удалось получить информацию "
            "о резервных копиях."
        )

    lines = [
        "💾 <b>Резервные копии</b>",
        "",
        f"📦 Количество: <b>{len(backups)}</b>",
        f"💽 Общий размер: <b>{_format_size(total_size)}</b>",
    ]

    if latest is not None:
        lines.extend(
            [
                "",
                "🕐 <b>Последняя копия:</b>",
                f"<code>{escape(latest.filename)}</code>",
                f"Размер: {_format_size(latest.size_bytes)}",
                f"Создана: {_format_datetime(latest.created_at)}",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "ℹ️ Резервных копий пока нет.",
            ]
        )

    if not settings.is_sqlite:
        lines.extend(
            [
                "",
                "⚠️ Файловые backup поддерживаются "
                "только для SQLite.",
            ]
        )

    return "\n".join(lines)


# ============================================================================
# /backups
# ============================================================================


@router.message(Command("backups"))
async def backups_command(
    message: Message,
) -> None:
    """Открывает раздел резервных копий."""

    if not message.from_user or not _is_admin(
        message.from_user.id
    ):
        await message.answer("❌ Нет доступа.")
        return

    backups = await backup_service.list_backups()

    await message.answer(
        await _backups_text(),
        reply_markup=_backup_keyboard(backups),
    )


# ============================================================================
# Открытие раздела из админ-меню
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "backups")
)
async def backups_open(
    callback: CallbackQuery,
) -> None:
    """Открывает backup-раздел."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        backups = await backup_service.list_backups()

        text = await _backups_text()

        if callback.message:
            await callback.message.edit_text(
                text,
                reply_markup=_backup_keyboard(backups),
            )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка открытия backup-раздела"
        )

        await callback.answer(
            "Ошибка загрузки backup",
            show_alert=True,
        )


# ============================================================================
# Создание backup
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "backup_create")
)
async def backup_create_confirm(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запрашивает подтверждение создания backup."""

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
        AdminBackupStates.waiting_for_confirmation
    )

    await callback.message.edit_text(
        "💾 <b>Создание резервной копии</b>\n\n"
        "Будет создана новая копия SQLite-базы.\n"
        "После создания файл можно будет скачать из этого раздела.\n\n"
        "Подтвердить создание?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    _backup_button(
                        "✅ Создать",
                        "backup_create_confirmed",
                    ),
                    _backup_button(
                        "❌ Отмена",
                        "backups",
                    ),
                ]
            ]
        ),
    )

    await callback.answer()


@router.callback_query(
    AdminCB.filter(F.action == "backup_create_confirmed")
)
async def backup_create_confirmed(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Создаёт резервную копию после подтверждения."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await state.clear()

        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    await state.clear()

    await callback.answer(
        "Создаю backup…",
        show_alert=False,
    )

    try:
        backup = await backup_service.create_backup()

        await _write_admin_log(
            callback.from_user.id,
            AdminAction.BACKUP,
            f"Создан backup: {backup.filename}",
            metadata={
                "filename": backup.filename,
                "size_bytes": backup.size_bytes,
            },
        )

        await callback.message.edit_text(
            "✅ <b>Backup создан</b>\n\n"
            f"📦 Файл:\n"
            f"<code>{escape(backup.filename)}</code>\n\n"
            f"💽 Размер: {_format_size(backup.size_bytes)}\n"
            f"🕐 Создан: {_format_datetime(backup.created_at)}",
            reply_markup=_backup_delete_keyboard(
                backup.filename
            ),
        )

    except BackupDisabledError:
        await callback.message.edit_text(
            "❌ <b>Backup отключён</b>\n\n"
            "Включи резервное копирование в настройках."
        )

    except BackupCreationError as exc:
        logger.exception(
            "Ошибка создания backup"
        )

        await callback.message.edit_text(
            "❌ <b>Не удалось создать backup</b>\n\n"
            f"{escape(str(exc))}"
        )

    except Exception:
        logger.exception(
            "Непредвиденная ошибка создания backup"
        )

        await callback.message.edit_text(
            "❌ Произошла ошибка при создании резервной копии."
        )


# ============================================================================
# Открытие конкретного backup
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "backup_open")
)
async def backup_open(
    callback: CallbackQuery,
) -> None:
    """Показывает информацию о конкретном backup."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(callback.data)
        filename = str(
            parsed.get("setting_key", "")
        )

        backup = await backup_service.get_backup(
            filename
        )

        await callback.message.edit_text(
            "📦 <b>Резервная копия</b>\n\n"
            f"Файл:\n"
            f"<code>{escape(backup.filename)}</code>\n\n"
            f"Размер: {_format_size(backup.size_bytes)}\n"
            f"Дата: {_format_datetime(backup.created_at)}",
            reply_markup=_backup_delete_keyboard(
                backup.filename
            ),
        )

        await callback.answer()

    except BackupNotFoundError:
        await callback.answer(
            "Backup не найден",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка открытия backup"
        )

        await callback.answer(
            "Ошибка",
            show_alert=True,
        )


# ============================================================================
# Скачивание backup
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "backup_download")
)
async def backup_download(
    callback: CallbackQuery,
) -> None:
    """Отправляет backup-файл администратору."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(callback.data)

        filename = str(
            parsed.get("setting_key", "")
        )

        backup = await backup_service.get_backup(
            filename
        )

        await callback.answer(
            "Отправляю файл…",
            show_alert=False,
        )

        document = FSInputFile(
            backup.path,
            filename=backup.filename,
        )

        await callback.message.answer_document(
            document=document,
            caption=(
                "💾 <b>Резервная копия БД</b>\n\n"
                f"Файл: <code>{escape(backup.filename)}</code>\n"
                f"Размер: {_format_size(backup.size_bytes)}\n"
                f"Создан: {_format_datetime(backup.created_at)}"
            ),
        )

        await _write_admin_log(
            callback.from_user.id,
            AdminAction.BACKUP,
            f"Скачан backup: {backup.filename}",
            metadata={
                "filename": backup.filename,
                "size_bytes": backup.size_bytes,
                "operation": "download",
            },
        )

    except BackupNotFoundError:
        await callback.answer(
            "Backup не найден",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка отправки backup"
        )

        await callback.answer(
            "Не удалось отправить backup",
            show_alert=True,
        )


# ============================================================================
# Удаление backup
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "backup_delete")
)
async def backup_delete_request(
    callback: CallbackQuery,
) -> None:
    """Запрашивает подтверждение удаления backup."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(callback.data)

        filename = str(
            parsed.get("setting_key", "")
        )

        backup = await backup_service.get_backup(
            filename
        )

        await callback.message.edit_text(
            "⚠️ <b>Удаление резервной копии</b>\n\n"
            f"<code>{escape(backup.filename)}</code>\n\n"
            f"Размер: {_format_size(backup.size_bytes)}\n\n"
            "Удалить этот файл?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        _backup_button(
                            "🗑 Да, удалить",
                            "backup_delete_confirmed",
                            setting_key=backup.filename,
                        ),
                    ],
                    [
                        _backup_button(
                            "⬅️ Отмена",
                            "backup_open",
                            setting_key=backup.filename,
                        ),
                    ],
                ]
            ),
        )

        await callback.answer()

    except BackupNotFoundError:
        await callback.answer(
            "Backup не найден",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка подготовки удаления backup"
        )

        await callback.answer(
            "Ошибка",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "backup_delete_confirmed")
)
async def backup_delete_confirmed(
    callback: CallbackQuery,
) -> None:
    """Удаляет backup после подтверждения."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    try:
        parsed = AdminCB.unpack(callback.data)

        filename = str(
            parsed.get("setting_key", "")
        )

        backup = await backup_service.get_backup(
            filename
        )

        await backup_service.delete_backup(
            backup.filename
        )

        await _write_admin_log(
            callback.from_user.id,
            AdminAction.BACKUP,
            f"Удалён backup: {backup.filename}",
            metadata={
                "filename": backup.filename,
                "size_bytes": backup.size_bytes,
                "operation": "delete",
            },
        )

        backups = await backup_service.list_backups()

        await callback.message.edit_text(
            "✅ <b>Backup удалён</b>\n\n"
            f"<code>{escape(backup.filename)}</code>",
            reply_markup=_backup_keyboard(backups),
        )

        await callback.answer("Удалено")

    except BackupNotFoundError:
        await callback.answer(
            "Backup уже отсутствует",
            show_alert=True,
        )

    except BackupError:
        logger.exception(
            "Ошибка удаления backup"
        )

        await callback.answer(
            "Не удалось удалить backup",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Непредвиденная ошибка удаления backup"
        )

        await callback.answer(
            "Ошибка удаления",
            show_alert=True,
        )


# ============================================================================
# Очистка старых backup
# ============================================================================


@router.callback_query(
    AdminCB.filter(F.action == "backup_cleanup")
)
async def backup_cleanup(
    callback: CallbackQuery,
) -> None:
    """Удаляет backup-файлы старше retention_days."""

    if not callback.from_user or not _is_admin(
        callback.from_user.id
    ):
        await callback.answer(
            "Нет доступа",
            show_alert=True,
        )
        return

    retention_days = settings.backup_retention_days

    if retention_days <= 0:
        await callback.answer(
            "Очистка отключена настройкой retention_days",
            show_alert=True,
        )
        return

    try:
        deleted = await backup_service.cleanup_old_backups(
            retention_days=retention_days
        )

        await _write_admin_log(
            callback.from_user.id,
            AdminAction.BACKUP,
            (
                "Выполнена очистка старых backup: "
                f"удалено {deleted}"
            ),
            metadata={
                "operation": "cleanup",
                "retention_days": retention_days,
                "deleted": deleted,
            },
        )

        backups = await backup_service.list_backups()

        await callback.message.edit_text(
            "🧹 <b>Очистка backup завершена</b>\n\n"
            f"Срок хранения: <b>{retention_days} дн.</b>\n"
            f"Удалено: <b>{deleted}</b>\n\n"
            + await _backups_text(),
            reply_markup=_backup_keyboard(backups),
        )

        await callback.answer(
            f"Удалено: {deleted}"
        )

    except Exception:
        logger.exception(
            "Ошибка очистки старых backup"
        )

        await callback.answer(
            "Ошибка очистки",
            show_alert=True,
        )


# ============================================================================
# Отмена FSM
# ============================================================================


@router.message(
    AdminBackupStates.waiting_for_confirmation,
    Command("cancel"),
)
async def backup_cancel(
    message: Message,
    state: FSMContext,
) -> None:
    """Отменяет операцию создания backup."""

    if not message.from_user or not _is_admin(
        message.from_user.id
    ):
        return

    await state.clear()

    backups = await backup_service.list_backups()

    await message.answer(
        await _backups_text(),
        reply_markup=_backup_keyboard(backups),
    )


__all__ = [
    "router",
]