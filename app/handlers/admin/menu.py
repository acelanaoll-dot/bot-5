from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from loguru import logger

from app.callbacks.admin import AdminCB
from app.config import settings
from app.keyboards.admin import admin_main_keyboard


router = Router(name="admin_menu")


def _is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return user_id in settings.admin_ids


def _admin_menu_text() -> str:
    """Текст главного административного меню."""
    return (
        "⚙️ <b>Административная панель</b>\n\n"
        "Выберите нужный раздел:"
    )


async def _show_admin_menu(
    message: Message | CallbackQuery,
) -> None:
    """Показывает главное меню администратора."""
    text = _admin_menu_text()
    keyboard = admin_main_keyboard()

    if isinstance(message, CallbackQuery):
        if message.message is not None:
            await message.message.edit_text(
                text,
                reply_markup=keyboard,
            )

        await message.answer()
        return

    await message.answer(
        text,
        reply_markup=keyboard,
    )


@router.message(Command("admin"))
async def admin_command(
    message: Message,
) -> None:
    """Открывает административную панель."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    try:
        await _show_admin_menu(message)

    except Exception:
        logger.exception(
            "Ошибка открытия админ-панели: admin={}",
            message.from_user.id,
        )

        await message.answer(
            "❌ Не удалось открыть административную панель."
        )


@router.callback_query(
    AdminCB.filter(F.action == "admin_menu")
)
async def admin_menu_callback(
    callback: CallbackQuery,
) -> None:
    """Возвращает пользователя в главное админ-меню."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        await _show_admin_menu(callback)

    except Exception:
        logger.exception(
            "Ошибка возврата в админ-меню: admin={}",
            callback.from_user.id,
        )

        await callback.answer(
            "Ошибка открытия меню.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "back")
)
async def admin_back_callback(
    callback: CallbackQuery,
) -> None:
    """
    Обрабатывает возврат в главное админ-меню.

    Этот callback используется только для кнопки
    возврата самого административного меню.
    """
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer(
            "Нет доступа.",
            show_alert=True,
        )
        return

    try:
        await _show_admin_menu(callback)

    except Exception:
        logger.exception(
            "Ошибка возврата из админ-раздела: admin={}",
            callback.from_user.id,
        )

        await callback.answer(
            "Ошибка возврата.",
            show_alert=True,
        )


__all__ = ["router"]