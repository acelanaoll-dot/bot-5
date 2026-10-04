from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from loguru import logger
from sqlalchemy import select

from app.callbacks.admin import AdminCB
from app.config import settings
from app.database.models import (
    Admin,
    AdminAction,
    AdminLog,
    Broadcast,
    User,
    UserStatus,
)
from app.database.session import get_session
from app.states.admin import AdminBroadcastStates
from app.utils.validation import validate_text_length


router = Router(name="admin_broadcast")


# ============================================================
# Проверка администратора
# ============================================================

def _is_admin(telegram_id: int) -> bool:
    """Проверяет администратора по Telegram ID."""
    return telegram_id in settings.admin_ids


# ============================================================
# Вспомогательные функции
# ============================================================

async def _get_or_create_admin(
    session: Any,
    telegram_id: int,
) -> Admin | None:
    """
    Возвращает запись Admin для Telegram-пользователя.

    Для администраторов из .env запись создаётся автоматически,
    если её ещё нет в БД.
    """
    result = await session.execute(
        select(Admin)
        .join(User, User.id == Admin.user_id)
        .where(User.telegram_id == telegram_id)
        .where(Admin.is_active.is_(True))
    )
    admin = result.scalar_one_or_none()

    if admin is not None:
        return admin

    result = await session.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        return None

    admin = Admin(
        user_id=user.id,
        role="admin",
        is_active=True,
    )

    user.is_admin = True

    session.add(admin)
    await session.flush()

    return admin


async def _write_admin_log(
    session: Any,
    admin_id: int,
    action: AdminAction,
    description: str,
    target_id: int | None = None,
) -> None:
    """Записывает действие администратора в журнал."""
    session.add(
        AdminLog(
            admin_id=admin_id,
            action=action,
            target_type="broadcast",
            target_id=target_id,
            description=description,
        )
    )


def _confirmation_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения рассылки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📤 Отправить",
                    callback_data=AdminCB(
                        action="broadcast_send",
                        broadcast_id=broadcast_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data=AdminCB(
                        action="broadcast_cancel",
                        broadcast_id=broadcast_id,
                    ).pack(),
                ),
            ],
        ]
    )


def _preview_text(broadcast: Broadcast) -> str:
    """Формирует предпросмотр рассылки."""
    lines = [
        "📢 <b>Предпросмотр рассылки</b>",
        "",
        broadcast.text,
        "",
    ]

    if broadcast.button_text and broadcast.button_url:
        lines.extend(
            [
                "🔘 <b>Кнопка:</b>",
                f"{broadcast.button_text}",
                f"{broadcast.button_url}",
                "",
            ]
        )

    lines.extend(
        [
            f"🆔 Рассылка: <code>{broadcast.id}</code>",
            "",
            "Проверь текст и нажми «Отправить».",
        ]
    )

    return "\n".join(lines)


def _broadcast_keyboard(broadcast: Broadcast) -> InlineKeyboardMarkup | None:
    """Создаёт inline-кнопку для пользователей."""
    if not broadcast.button_text or not broadcast.button_url:
        return None

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=broadcast.button_text,
                    url=broadcast.button_url,
                )
            ]
        ]
    )


async def _get_broadcast(
    session: Any,
    broadcast_id: int,
) -> Broadcast | None:
    """Получает рассылку по ID."""
    result = await session.execute(
        select(Broadcast).where(Broadcast.id == broadcast_id)
    )
    return result.scalar_one_or_none()


async def _send_broadcast(
    bot: Any,
    broadcast: Broadcast,
) -> tuple[int, int, int]:
    """
    Выполняет рассылку.

    Возвращает:
    sent_count, failed_count, blocked_count
    """
    sent_count = 0
    failed_count = 0
    blocked_count = 0

    async with get_session() as session:
        result = await session.execute(
            select(User.telegram_id)
            .where(User.status == UserStatus.ACTIVE)
            .order_by(User.id.asc())
        )
        telegram_ids = list(result.scalars().all())

    keyboard = _broadcast_keyboard(broadcast)

    for telegram_id in telegram_ids:
        try:
            if broadcast.media_type and broadcast.media_file_id:
                media_type = broadcast.media_type.lower()

                if media_type == "photo":
                    await bot.send_photo(
                        chat_id=telegram_id,
                        photo=broadcast.media_file_id,
                        caption=broadcast.text,
                        reply_markup=keyboard,
                    )
                elif media_type == "video":
                    await bot.send_video(
                        chat_id=telegram_id,
                        video=broadcast.media_file_id,
                        caption=broadcast.text,
                        reply_markup=keyboard,
                    )
                elif media_type == "document":
                    await bot.send_document(
                        chat_id=telegram_id,
                        document=broadcast.media_file_id,
                        caption=broadcast.text,
                        reply_markup=keyboard,
                    )
                else:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=broadcast.text,
                        reply_markup=keyboard,
                    )
            else:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=broadcast.text,
                    reply_markup=keyboard,
                )

            sent_count += 1

            # Держим безопасную скорость отправки.
            await asyncio.sleep(0.05)

        except TelegramRetryAfter as exc:
            logger.warning(
                "Telegram попросил подождать {} сек. при рассылке {}",
                exc.retry_after,
                broadcast.id,
            )

            await asyncio.sleep(float(exc.retry_after))

            try:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=broadcast.text,
                    reply_markup=keyboard,
                )
                sent_count += 1
            except TelegramForbiddenError:
                blocked_count += 1
            except Exception:
                failed_count += 1
                logger.exception(
                    "Повторная отправка рассылки {} пользователю {} не удалась",
                    broadcast.id,
                    telegram_id,
                )

        except TelegramForbiddenError:
            blocked_count += 1

        except TelegramBadRequest as exc:
            failed_count += 1

            logger.warning(
                "Telegram BadRequest для рассылки {} пользователю {}: {}",
                broadcast.id,
                telegram_id,
                exc,
            )

        except Exception:
            failed_count += 1

            logger.exception(
                "Ошибка отправки рассылки {} пользователю {}",
                broadcast.id,
                telegram_id,
            )

    return sent_count, failed_count, blocked_count


# ============================================================
# Открытие рассылки
# ============================================================

@router.message(Command("broadcast"))
async def cmd_broadcast(
    message: Message,
    state: FSMContext,
) -> None:
    """Запускает создание рассылки через /broadcast."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    await state.clear()
    await state.set_state(AdminBroadcastStates.waiting_for_text)

    await message.answer(
        "📢 <b>Создание рассылки</b>\n\n"
        "Отправь текст сообщения, которое получат пользователи.\n\n"
        "Для отмены: /cancel"
    )


@router.callback_query(
    AdminCB.filter(F.action == "broadcast")
)
async def open_broadcast(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Открывает создание рассылки из админ-меню."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    await state.clear()
    await state.set_state(AdminBroadcastStates.waiting_for_text)

    await callback.answer()

    if callback.message:
        await callback.message.answer(
            "📢 <b>Создание рассылки</b>\n\n"
            "Отправь текст сообщения.\n\n"
            "Для отмены: /cancel"
        )


# ============================================================
# Текст рассылки
# ============================================================

@router.message(AdminBroadcastStates.waiting_for_text)
async def broadcast_text(
    message: Message,
    state: FSMContext,
) -> None:
    """Принимает текст рассылки."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Создание рассылки отменено.")
        return

    if not message.text:
        await message.answer(
            "❌ Нужен именно текст сообщения.\n"
            "Отправь текст ещё раз."
        )
        return

    try:
        text_value = validate_text_length(
            message.text.strip(),
            max_length=settings.security_max_text_length,
            field_name="Текст рассылки",
        )
    except Exception as exc:
        await message.answer(f"❌ {exc}")
        return

    await state.update_data(
        text=text_value,
        button_text=None,
        button_url=None,
    )

    await state.set_state(AdminBroadcastStates.waiting_for_button_text)

    await message.answer(
        "🔘 Теперь можно добавить inline-кнопку.\n\n"
        "Отправь текст кнопки.\n"
        "Или отправь <code>-</code>, чтобы рассылка была без кнопки.\n\n"
        "Для отмены: /cancel"
    )


# ============================================================
# Текст кнопки
# ============================================================

@router.message(AdminBroadcastStates.waiting_for_button_text)
async def broadcast_button_text(
    message: Message,
    state: FSMContext,
) -> None:
    """Принимает текст inline-кнопки."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Создание рассылки отменено.")
        return

    if not message.text:
        await message.answer("❌ Отправь текст кнопки или <code>-</code>.")
        return

    value = message.text.strip()

    if value == "-":
        await state.update_data(
            button_text=None,
            button_url=None,
        )
        await _create_broadcast_draft(message, state)
        return

    if len(value) > 255:
        await message.answer(
            "❌ Текст кнопки слишком длинный. "
            "Максимум 255 символов."
        )
        return

    await state.update_data(button_text=value)
    await state.set_state(AdminBroadcastStates.waiting_for_button_url)

    await message.answer(
        "🔗 Теперь отправь URL кнопки.\n\n"
        "Разрешены только ссылки http:// или https://.\n\n"
        "Для отмены: /cancel"
    )


# ============================================================
# URL кнопки
# ============================================================

@router.message(AdminBroadcastStates.waiting_for_button_url)
async def broadcast_button_url(
    message: Message,
    state: FSMContext,
) -> None:
    """Принимает URL inline-кнопки."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    if message.text and message.text.strip().lower() == "/cancel":
        await state.clear()
        await message.answer("❌ Создание рассылки отменено.")
        return

    if not message.text:
        await message.answer("❌ Отправь URL.")
        return

    url = message.text.strip()

    if not (
        url.startswith("https://")
        or url.startswith("http://")
    ):
        await message.answer(
            "❌ Некорректный URL.\n"
            "Ссылка должна начинаться с http:// или https://."
        )
        return

    if len(url) > 2048:
        await message.answer("❌ URL слишком длинный.")
        return

    await state.update_data(button_url=url)

    await _create_broadcast_draft(message, state)


# ============================================================
# Создание черновика
# ============================================================

async def _create_broadcast_draft(
    message: Message,
    state: FSMContext,
) -> None:
    """Создаёт черновик рассылки и показывает предпросмотр."""
    if message.from_user is None:
        return

    data = await state.get_data()

    text_value = data.get("text")
    button_text = data.get("button_text")
    button_url = data.get("button_url")

    if not text_value:
        await state.clear()
        await message.answer(
            "❌ Не удалось получить текст рассылки."
        )
        return

    async with get_session() as session:
        admin = await _get_or_create_admin(
            session,
            message.from_user.id,
        )

        if admin is None:
            await state.clear()
            await message.answer(
                "❌ Не найдена запись администратора в базе данных."
            )
            return

        broadcast = Broadcast(
            admin_id=admin.id,
            text=text_value,
            media_type=None,
            media_file_id=None,
            button_text=button_text,
            button_url=button_url,
            status="draft",
            total_users=0,
            sent_count=0,
            failed_count=0,
            blocked_count=0,
        )

        session.add(broadcast)
        await session.flush()

        await _write_admin_log(
            session=session,
            admin_id=admin.id,
            action=AdminAction.CREATE,
            description="Создан черновик рассылки.",
            target_id=broadcast.id,
        )

        await session.commit()

        broadcast_id = broadcast.id

    await state.update_data(broadcast_id=broadcast_id)
    await state.set_state(AdminBroadcastStates.waiting_for_confirmation)

    async with get_session() as session:
        broadcast = await _get_broadcast(
            session,
            broadcast_id,
        )

        if broadcast is None:
            await state.clear()
            await message.answer(
                "❌ Черновик рассылки не найден."
            )
            return

        preview = _preview_text(broadcast)

    await message.answer(
        preview,
        reply_markup=_confirmation_keyboard(broadcast_id),
    )


# ============================================================
# Подтверждение
# ============================================================

@router.callback_query(
    AdminCB.filter(F.action == "broadcast_send")
)
async def confirm_broadcast(
    callback: CallbackQuery,
    callback_data: AdminCB,
    state: FSMContext,
) -> None:
    """Подтверждает и запускает рассылку."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    broadcast_id = callback_data.broadcast_id

    if broadcast_id <= 0:
        await callback.answer(
            "Некорректный ID рассылки.",
            show_alert=True,
        )
        return

    await callback.answer("Рассылка запущена.")

    await state.clear()

    async with get_session() as session:
        broadcast = await _get_broadcast(
            session,
            broadcast_id,
        )

        if broadcast is None:
            if callback.message:
                await callback.message.answer(
                    "❌ Рассылка не найдена."
                )
            return

        if broadcast.status not in {"draft", "failed"}:
            if callback.message:
                await callback.message.answer(
                    f"❌ Нельзя запустить рассылку со статусом "
                    f"<code>{broadcast.status}</code>."
                )
            return

        result = await session.execute(
            select(User.telegram_id)
            .where(User.status == UserStatus.ACTIVE)
        )

        total_users = len(result.scalars().all())

        broadcast.status = "sending"
        broadcast.total_users = total_users

        await session.commit()

    if callback.message:
        await callback.message.answer(
            f"📤 Рассылка <code>#{broadcast_id}</code> запущена.\n"
            f"Получателей: <b>{total_users}</b>\n\n"
            "Это может занять некоторое время."
        )

    async with get_session() as session:
        broadcast = await _get_broadcast(
            session,
            broadcast_id,
        )

    if broadcast is None:
        return

    sent_count = 0
    failed_count = 0
    blocked_count = 0

    try:
        (
            sent_count,
            failed_count,
            blocked_count,
        ) = await _send_broadcast(
            bot=callback.bot,
            broadcast=broadcast,
        )

        final_status = "completed"

    except Exception:
        final_status = "failed"
        logger.exception(
            "Критическая ошибка при выполнении рассылки {}",
            broadcast_id,
        )

    async with get_session() as session:
        broadcast = await _get_broadcast(
            session,
            broadcast_id,
        )

        if broadcast is not None:
            broadcast.sent_count = sent_count
            broadcast.failed_count = failed_count
            broadcast.blocked_count = blocked_count
            broadcast.status = final_status

            admin = await _get_or_create_admin(
                session,
                callback.from_user.id,
            )

            if admin is not None:
                await _write_admin_log(
                    session=session,
                    admin_id=admin.id,
                    action=AdminAction.BROADCAST,
                    description=(
                        f"Рассылка завершена. "
                        f"Отправлено: {sent_count}, "
                        f"ошибок: {failed_count}, "
                        f"заблокировали бота: {blocked_count}."
                    ),
                    target_id=broadcast_id,
                )

            await session.commit()

    if callback.message:
        await callback.message.answer(
            "✅ <b>Рассылка завершена</b>\n\n"
            f"🆔 ID: <code>{broadcast_id}</code>\n"
            f"📨 Отправлено: <b>{sent_count}</b>\n"
            f"❌ Ошибок: <b>{failed_count}</b>\n"
            f"🚫 Заблокировали бота: <b>{blocked_count}</b>"
        )


# ============================================================
# Отмена
# ============================================================

@router.callback_query(
    AdminCB.filter(F.action == "broadcast_cancel")
)
async def cancel_broadcast(
    callback: CallbackQuery,
    callback_data: AdminCB,
    state: FSMContext,
) -> None:
    """Удаляет черновик рассылки."""
    if callback.from_user is None:
        return

    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    broadcast_id = callback_data.broadcast_id

    await state.clear()

    async with get_session() as session:
        broadcast = await _get_broadcast(
            session,
            broadcast_id,
        )

        if broadcast is not None and broadcast.status == "draft":
            admin = await _get_or_create_admin(
                session,
                callback.from_user.id,
            )

            if admin is not None:
                await _write_admin_log(
                    session=session,
                    admin_id=admin.id,
                    action=AdminAction.DELETE,
                    description="Черновик рассылки отменён.",
                    target_id=broadcast_id,
                )

            await session.delete(broadcast)
            await session.commit()

    await callback.answer("Отменено.")

    if callback.message:
        await callback.message.edit_text(
            "❌ Рассылка отменена."
        )


# ============================================================
# Отмена через /cancel
# ============================================================

@router.message(Command("cancel"))
async def cancel_broadcast_command(
    message: Message,
    state: FSMContext,
) -> None:
    """Отмена текущего диалога рассылки."""
    if message.from_user is None:
        return

    if not _is_admin(message.from_user.id):
        return

    current_state = await state.get_state()

    broadcast_states = {
        AdminBroadcastStates.waiting_for_text.state,
        AdminBroadcastStates.waiting_for_button_text.state,
        AdminBroadcastStates.waiting_for_button_url.state,
        AdminBroadcastStates.waiting_for_confirmation.state,
    }

    if current_state not in broadcast_states:
        return

    data = await state.get_data()
    broadcast_id = data.get("broadcast_id")

    await state.clear()

    if broadcast_id:
        async with get_session() as session:
            broadcast = await _get_broadcast(
                session,
                int(broadcast_id),
            )

            if broadcast is not None and broadcast.status == "draft":
                await session.delete(broadcast)
                await session.commit()

    await message.answer(
        "❌ Создание рассылки отменено."
    )


__all__ = ["router"]