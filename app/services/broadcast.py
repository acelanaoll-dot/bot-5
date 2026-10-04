# app/services/broadcast.py

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Admin,
    Broadcast,
    User,
    UserStatus,
)


# ======================================================================
# Исключения
# ======================================================================


class BroadcastError(Exception):
    """Базовая ошибка рассылки."""


class BroadcastNotFoundError(BroadcastError):
    """Рассылка не найдена."""


class BroadcastValidationError(BroadcastError):
    """Некорректные параметры рассылки."""


class BroadcastAlreadyStartedError(BroadcastError):
    """Рассылка уже была запущена."""


class BroadcastCancelledError(BroadcastError):
    """Рассылка отменена."""


class BroadcastSendError(BroadcastError):
    """Ошибка отправки сообщения."""


# ======================================================================
# Константы
# ======================================================================


MAX_TEXT_LENGTH = 4096
MAX_BUTTON_TEXT_LENGTH = 255
MAX_BUTTON_URL_LENGTH = 2048
MAX_BATCH_SIZE = 100
MIN_DELAY = 0.05
MAX_DELAY = 10.0


# ======================================================================
# DTO
# ======================================================================


@dataclass(slots=True, frozen=True)
class BroadcastResult:
    """Результат выполнения рассылки."""

    broadcast_id: int
    total_users: int
    sent_count: int
    failed_count: int
    blocked_count: int
    cancelled: bool = False


@dataclass(slots=True, frozen=True)
class BroadcastPreview:
    """Данные для предпросмотра рассылки."""

    text: str
    media_type: str | None
    media_file_id: str | None
    button_text: str | None
    button_url: str | None


# ======================================================================
# Сервис
# ======================================================================


class BroadcastService:
    """Сервис массовых рассылок Telegram."""

    STATUS_DRAFT = "draft"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELLED = "cancelled"
    STATUS_FAILED = "failed"

    MEDIA_TEXT = "text"
    MEDIA_PHOTO = "photo"
    MEDIA_VIDEO = "video"
    MEDIA_DOCUMENT = "document"
    MEDIA_AUDIO = "audio"
    MEDIA_ANIMATION = "animation"

    ALLOWED_MEDIA_TYPES = {
        MEDIA_TEXT,
        MEDIA_PHOTO,
        MEDIA_VIDEO,
        MEDIA_DOCUMENT,
        MEDIA_AUDIO,
        MEDIA_ANIMATION,
    }

    # --------------------------------------------------------------
    # Нормализация
    # --------------------------------------------------------------

    def _validate_admin_id(
        self,
        admin_id: int,
    ) -> int:
        """Проверяет ID администратора."""

        if (
            isinstance(admin_id, bool)
            or not isinstance(admin_id, int)
            or admin_id <= 0
        ):
            raise BroadcastValidationError(
                "Некорректный ID администратора."
            )

        return admin_id

    def _validate_text(
        self,
        text: str,
    ) -> str:
        """Проверяет текст рассылки."""

        if not isinstance(text, str):
            raise BroadcastValidationError(
                "Текст рассылки должен быть строкой."
            )

        text = text.strip()

        if not text:
            raise BroadcastValidationError(
                "Текст рассылки не может быть пустым."
            )

        if len(text) > MAX_TEXT_LENGTH:
            raise BroadcastValidationError(
                f"Текст слишком длинный. "
                f"Максимум: {MAX_TEXT_LENGTH} символов."
            )

        return text

    def _validate_media(
        self,
        media_type: str | None,
        media_file_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Проверяет медиа рассылки."""

        if media_type is None:
            if media_file_id is not None:
                raise BroadcastValidationError(
                    "Указан media_file_id без media_type."
                )

            return None, None

        media_type = media_type.strip().lower()

        if media_type not in self.ALLOWED_MEDIA_TYPES:
            raise BroadcastValidationError(
                "Неподдерживаемый тип медиа: "
                f"{media_type}"
            )

        if media_type == self.MEDIA_TEXT:
            return None, None

        if not media_file_id:
            raise BroadcastValidationError(
                "Для медиа необходимо указать file_id."
            )

        media_file_id = media_file_id.strip()

        if not media_file_id:
            raise BroadcastValidationError(
                "media_file_id не может быть пустым."
            )

        return media_type, media_file_id

    def _validate_button(
        self,
        button_text: str | None,
        button_url: str | None,
    ) -> tuple[str | None, str | None]:
        """Проверяет inline-кнопку."""

        if button_text is None and button_url is None:
            return None, None

        if not button_text or not button_url:
            raise BroadcastValidationError(
                "Для кнопки необходимо указать "
                "и текст, и URL."
            )

        button_text = button_text.strip()
        button_url = button_url.strip()

        if not button_text:
            raise BroadcastValidationError(
                "Текст кнопки не может быть пустым."
            )

        if len(button_text) > MAX_BUTTON_TEXT_LENGTH:
            raise BroadcastValidationError(
                "Текст кнопки слишком длинный."
            )

        if len(button_url) > MAX_BUTTON_URL_LENGTH:
            raise BroadcastValidationError(
                "URL кнопки слишком длинный."
            )

        if not (
            button_url.startswith("https://")
            or button_url.startswith("http://")
            or button_url.startswith("tg://")
        ):
            raise BroadcastValidationError(
                "URL кнопки должен начинаться с "
                "http://, https:// или tg://."
            )

        return button_text, button_url

    def _validate_delay(
        self,
        delay: float,
    ) -> float:
        """Проверяет задержку между сообщениями."""

        try:
            delay = float(delay)
        except (TypeError, ValueError) as exc:
            raise BroadcastValidationError(
                "Некорректная задержка рассылки."
            ) from exc

        if delay < MIN_DELAY:
            delay = MIN_DELAY

        if delay > MAX_DELAY:
            delay = MAX_DELAY

        return delay

    # ==================================================================
    # Получение
    # ==================================================================

    async def get(
        self,
        session: AsyncSession,
        broadcast_id: int,
    ) -> Broadcast | None:
        """Получает рассылку по ID."""

        if (
            isinstance(broadcast_id, bool)
            or not isinstance(broadcast_id, int)
            or broadcast_id <= 0
        ):
            return None

        return await session.get(
            Broadcast,
            broadcast_id,
        )

    async def get_or_raise(
        self,
        session: AsyncSession,
        broadcast_id: int,
    ) -> Broadcast:
        """Получает рассылку или выбрасывает ошибку."""

        broadcast = await self.get(
            session=session,
            broadcast_id=broadcast_id,
        )

        if broadcast is None:
            raise BroadcastNotFoundError(
                f"Рассылка #{broadcast_id} не найдена."
            )

        return broadcast

    async def list_broadcasts(
        self,
        session: AsyncSession,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Broadcast]:
        """Возвращает список рассылок."""

        limit = max(
            1,
            min(int(limit), 500),
        )

        offset = max(
            0,
            int(offset),
        )

        query = select(Broadcast)

        if status:
            query = query.where(
                Broadcast.status == status
            )

        query = (
            query
            .order_by(
                Broadcast.created_at.desc(),
                Broadcast.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await session.execute(
            query
        )

        return list(
            result.scalars().all()
        )

    # ==================================================================
    # Администратор
    # ==================================================================

    async def get_admin(
        self,
        session: AsyncSession,
        admin_id: int,
    ) -> Admin | None:
        """Возвращает администратора по ID."""

        admin_id = self._validate_admin_id(
            admin_id
        )

        return await session.get(
            Admin,
            admin_id,
        )

    async def validate_admin(
        self,
        session: AsyncSession,
        admin_id: int,
    ) -> Admin:
        """Проверяет существование администратора."""

        admin = await self.get_admin(
            session=session,
            admin_id=admin_id,
        )

        if admin is None:
            raise BroadcastValidationError(
                "Администратор не найден."
            )

        if not admin.is_active:
            raise BroadcastValidationError(
                "Администратор отключён."
            )

        return admin

    # ==================================================================
    # Создание
    # ==================================================================

    async def create(
        self,
        session: AsyncSession,
        *,
        admin_id: int,
        text: str,
        media_type: str | None = None,
        media_file_id: str | None = None,
        button_text: str | None = None,
        button_url: str | None = None,
    ) -> Broadcast:
        """Создаёт черновик рассылки."""

        admin_id = self._validate_admin_id(
            admin_id
        )

        text = self._validate_text(
            text
        )

        media_type, media_file_id = (
            self._validate_media(
                media_type,
                media_file_id,
            )
        )

        button_text, button_url = (
            self._validate_button(
                button_text,
                button_url,
            )
        )

        await self.validate_admin(
            session=session,
            admin_id=admin_id,
        )

        broadcast = Broadcast(
            admin_id=admin_id,
            text=text,
            media_type=media_type,
            media_file_id=media_file_id,
            button_text=button_text,
            button_url=button_url,
            status=self.STATUS_DRAFT,
            total_users=0,
            sent_count=0,
            failed_count=0,
            blocked_count=0,
        )

        session.add(
            broadcast
        )

        await session.flush()

        logger.info(
            "Создана рассылка: id={}, admin_id={}",
            broadcast.id,
            admin_id,
        )

        return broadcast

    # ==================================================================
    # Обновление черновика
    # ==================================================================

    async def update(
        self,
        session: AsyncSession,
        broadcast_id: int,
        *,
        text: str | None = None,
        media_type: str | None = None,
        media_file_id: str | None = None,
        button_text: str | None = None,
        button_url: str | None = None,
    ) -> Broadcast:
        """Обновляет черновик рассылки."""

        broadcast = await self.get_or_raise(
            session=session,
            broadcast_id=broadcast_id,
        )

        if broadcast.status != self.STATUS_DRAFT:
            raise BroadcastAlreadyStartedError(
                "Изменять можно только черновик рассылки."
            )

        if text is not None:
            broadcast.text = self._validate_text(
                text
            )

        if (
            media_type is not None
            or media_file_id is not None
        ):
            new_media_type, new_media_file_id = (
                self._validate_media(
                    media_type,
                    media_file_id,
                )
            )

            broadcast.media_type = (
                new_media_type
            )
            broadcast.media_file_id = (
                new_media_file_id
            )

        if (
            button_text is not None
            or button_url is not None
        ):
            new_button_text, new_button_url = (
                self._validate_button(
                    button_text,
                    button_url,
                )
            )

            broadcast.button_text = (
                new_button_text
            )
            broadcast.button_url = (
                new_button_url
            )

        await session.flush()

        return broadcast

    # ==================================================================
    # Предпросмотр
    # ==================================================================

    def preview(
        self,
        broadcast: Broadcast,
    ) -> BroadcastPreview:
        """Создаёт DTO предпросмотра."""

        return BroadcastPreview(
            text=broadcast.text,
            media_type=broadcast.media_type,
            media_file_id=broadcast.media_file_id,
            button_text=broadcast.button_text,
            button_url=broadcast.button_url,
        )

    def _build_keyboard(
        self,
        broadcast: Broadcast,
    ) -> InlineKeyboardMarkup | None:
        """Создаёт клавиатуру рассылки."""

        if (
            not broadcast.button_text
            or not broadcast.button_url
        ):
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

    # ==================================================================
    # Пользователи
    # ==================================================================

    async def count_recipients(
        self,
        session: AsyncSession,
    ) -> int:
        """Считает активных пользователей."""

        result = await session.execute(
            select(
                func.count(User.id)
            ).where(
                User.status == UserStatus.ACTIVE
            )
        )

        return int(
            result.scalar_one() or 0
        )

    async def iter_recipients(
        self,
        session: AsyncSession,
        *,
        batch_size: int = MAX_BATCH_SIZE,
    ) -> AsyncIterator[list[User]]:
        """
        Асинхронно выдаёт пользователей пачками.

        Используется вместо загрузки всей базы пользователей
        в память.
        """

        batch_size = max(
            1,
            min(
                int(batch_size),
                MAX_BATCH_SIZE,
            ),
        )

        last_id = 0

        while True:
            result = await session.execute(
                select(User)
                .where(
                    User.status
                    == UserStatus.ACTIVE,
                    User.id > last_id,
                )
                .order_by(
                    User.id.asc()
                )
                .limit(batch_size)
            )

            users = list(
                result.scalars().all()
            )

            if not users:
                break

            yield users

            last_id = users[-1].id

            if len(users) < batch_size:
                break

    # ==================================================================
    # Отправка одного сообщения
    # ==================================================================

    async def _send_one(
        self,
        bot: Bot,
        broadcast: Broadcast,
        telegram_id: int,
    ) -> str:
        """
        Отправляет одно сообщение.

        Возвращает:
        - sent
        - blocked
        - failed
        """

        reply_markup = (
            self._build_keyboard(
                broadcast
            )
        )

        media_type = (
            broadcast.media_type
        )
        media_file_id = (
            broadcast.media_file_id
        )

        try:
            if not media_type:
                await bot.send_message(
                    chat_id=telegram_id,
                    text=broadcast.text,
                    reply_markup=reply_markup,
                )

                return "sent"

            if not media_file_id:
                raise BroadcastSendError(
                    "Для медиа отсутствует file_id."
                )

            if media_type == self.MEDIA_PHOTO:
                await bot.send_photo(
                    chat_id=telegram_id,
                    photo=media_file_id,
                    caption=broadcast.text,
                    reply_markup=reply_markup,
                )

                return "sent"

            if media_type == self.MEDIA_VIDEO:
                await bot.send_video(
                    chat_id=telegram_id,
                    video=media_file_id,
                    caption=broadcast.text,
                    reply_markup=reply_markup,
                )

                return "sent"

            if media_type == self.MEDIA_DOCUMENT:
                await bot.send_document(
                    chat_id=telegram_id,
                    document=media_file_id,
                    caption=broadcast.text,
                    reply_markup=reply_markup,
                )

                return "sent"

            if media_type == self.MEDIA_AUDIO:
                await bot.send_audio(
                    chat_id=telegram_id,
                    audio=media_file_id,
                    caption=broadcast.text,
                    reply_markup=reply_markup,
                )

                return "sent"

            if media_type == self.MEDIA_ANIMATION:
                await bot.send_animation(
                    chat_id=telegram_id,
                    animation=media_file_id,
                    caption=broadcast.text,
                    reply_markup=reply_markup,
                )

                return "sent"

            raise BroadcastSendError(
                f"Неизвестный media_type: {media_type}"
            )

        except TelegramForbiddenError:
            # Пользователь заблокировал бота.
            return "blocked"

        except TelegramRetryAfter as exc:
            # Telegram просит подождать.
            retry_after = max(
                1,
                int(exc.retry_after),
            )

            logger.warning(
                "TelegramRetryAfter: "
                "user={}, wait={} sec",
                telegram_id,
                retry_after,
            )

            await asyncio.sleep(
                retry_after
            )

            try:
                if not media_type:
                    await bot.send_message(
                        chat_id=telegram_id,
                        text=broadcast.text,
                        reply_markup=reply_markup,
                    )

                elif media_type == self.MEDIA_PHOTO:
                    await bot.send_photo(
                        chat_id=telegram_id,
                        photo=media_file_id,
                        caption=broadcast.text,
                        reply_markup=reply_markup,
                    )

                elif media_type == self.MEDIA_VIDEO:
                    await bot.send_video(
                        chat_id=telegram_id,
                        video=media_file_id,
                        caption=broadcast.text,
                        reply_markup=reply_markup,
                    )

                elif media_type == self.MEDIA_DOCUMENT:
                    await bot.send_document(
                        chat_id=telegram_id,
                        document=media_file_id,
                        caption=broadcast.text,
                        reply_markup=reply_markup,
                    )

                elif media_type == self.MEDIA_AUDIO:
                    await bot.send_audio(
                        chat_id=telegram_id,
                        audio=media_file_id,
                        caption=broadcast.text,
                        reply_markup=reply_markup,
                    )

                elif media_type == self.MEDIA_ANIMATION:
                    await bot.send_animation(
                        chat_id=telegram_id,
                        animation=media_file_id,
                        caption=broadcast.text,
                        reply_markup=reply_markup,
                    )

                return "sent"

            except TelegramForbiddenError:
                return "blocked"

            except Exception:
                logger.exception(
                    "Повторная отправка не удалась: "
                    "user={}",
                    telegram_id,
                )

                return "failed"

        except (
            TelegramNetworkError,
            TelegramServerError,
        ):
            logger.warning(
                "Временная ошибка Telegram: "
                "user={}",
                telegram_id,
            )

            return "failed"

        except TelegramBadRequest as exc:
            logger.warning(
                "TelegramBadRequest: "
                "user={}, error={}",
                telegram_id,
                exc,
            )

            return "failed"

        except Exception:
            logger.exception(
                "Ошибка отправки рассылки: "
                "user={}",
                telegram_id,
            )

            return "failed"

    # ==================================================================
    # Запуск
    # ==================================================================

    async def start(
        self,
        session: AsyncSession,
        bot: Bot,
        broadcast_id: int,
        *,
        delay: float = 0.08,
        batch_size: int = MAX_BATCH_SIZE,
    ) -> BroadcastResult:
        """
        Запускает рассылку.

        Важно:
        сеть не выполняется внутри DB transaction.
        Статусы/счётчики только обновляются в текущей сессии.
        """

        broadcast = await self.get_or_raise(
            session=session,
            broadcast_id=broadcast_id,
        )

        if broadcast.status == self.STATUS_RUNNING:
            raise BroadcastAlreadyStartedError(
                "Рассылка уже выполняется."
            )

        if broadcast.status == self.STATUS_COMPLETED:
            raise BroadcastAlreadyStartedError(
                "Рассылка уже завершена."
            )

        if broadcast.status == self.STATUS_CANCELLED:
            raise BroadcastCancelledError(
                "Рассылка отменена."
            )

        if broadcast.status != self.STATUS_DRAFT:
            raise BroadcastValidationError(
                f"Нельзя запустить рассылку "
                f"со статусом {broadcast.status}."
            )

        delay = self._validate_delay(
            delay
        )

        batch_size = max(
            1,
            min(
                int(batch_size),
                MAX_BATCH_SIZE,
            ),
        )

        total_users = await self.count_recipients(
            session=session,
        )

        broadcast.total_users = (
            total_users
        )
        broadcast.sent_count = 0
        broadcast.failed_count = 0
        broadcast.blocked_count = 0
        broadcast.status = self.STATUS_RUNNING

        await session.flush()

        logger.info(
            "Запуск рассылки: "
            "id={}, recipients={}, delay={}",
            broadcast.id,
            total_users,
            delay,
        )

        try:
            async for users in self.iter_recipients(
                session=session,
                batch_size=batch_size,
            ):
                for user in users:
                    # Пользователь мог быть заблокирован
                    # после выборки из БД.
                    result = await self._send_one(
                        bot=bot,
                        broadcast=broadcast,
                        telegram_id=user.telegram_id,
                    )

                    if result == "sent":
                        broadcast.sent_count += 1

                    elif result == "blocked":
                        broadcast.blocked_count += 1

                        # Не меняем статус автоматически:
                        # блокировка Telegram и бан магазина —
                        # разные состояния.

                    else:
                        broadcast.failed_count += 1

                    await session.flush()

                    await asyncio.sleep(
                        delay
                    )

            broadcast.status = (
                self.STATUS_COMPLETED
            )

            await session.flush()

            logger.info(
                "Рассылка завершена: "
                "id={}, total={}, sent={}, "
                "failed={}, blocked={}",
                broadcast.id,
                broadcast.total_users,
                broadcast.sent_count,
                broadcast.failed_count,
                broadcast.blocked_count,
            )

        except asyncio.CancelledError:
            broadcast.status = (
                self.STATUS_CANCELLED
            )

            await session.flush()

            logger.warning(
                "Рассылка отменена: id={}",
                broadcast.id,
            )

            raise

        except Exception:
            broadcast.status = (
                self.STATUS_FAILED
            )

            await session.flush()

            logger.exception(
                "Критическая ошибка рассылки: id={}",
                broadcast.id,
            )

            raise

        return BroadcastResult(
            broadcast_id=broadcast.id,
            total_users=broadcast.total_users,
            sent_count=broadcast.sent_count,
            failed_count=broadcast.failed_count,
            blocked_count=broadcast.blocked_count,
            cancelled=False,
        )

    # ==================================================================
    # Остановка
    # ==================================================================

    async def cancel(
        self,
        session: AsyncSession,
        broadcast_id: int,
    ) -> Broadcast:
        """
        Помечает рассылку отменённой.

        Если start() уже выполняется отдельной asyncio-задачей,
        сама задача должна быть отменена вызывающим кодом.
        """

        broadcast = await self.get_or_raise(
            session=session,
            broadcast_id=broadcast_id,
        )

        if broadcast.status == self.STATUS_COMPLETED:
            raise BroadcastAlreadyStartedError(
                "Завершённую рассылку нельзя отменить."
            )

        if broadcast.status == self.STATUS_CANCELLED:
            return broadcast

        broadcast.status = (
            self.STATUS_CANCELLED
        )

        await session.flush()

        logger.info(
            "Рассылка помечена отменённой: id={}",
            broadcast.id,
        )

        return broadcast

    # ==================================================================
    # Сброс черновика
    # ==================================================================

    async def reset(
        self,
        session: AsyncSession,
        broadcast_id: int,
    ) -> Broadcast:
        """
        Возвращает рассылку в состояние draft.

        Разрешено только для failed/cancelled.
        """

        broadcast = await self.get_or_raise(
            session=session,
            broadcast_id=broadcast_id,
        )

        if broadcast.status not in {
            self.STATUS_FAILED,
            self.STATUS_CANCELLED,
        }:
            raise BroadcastValidationError(
                "Сбросить можно только "
                "неудачную или отменённую рассылку."
            )

        broadcast.status = (
            self.STATUS_DRAFT
        )
        broadcast.sent_count = 0
        broadcast.failed_count = 0
        broadcast.blocked_count = 0
        broadcast.total_users = 0

        await session.flush()

        return broadcast

    # ==================================================================
    # Удаление
    # ==================================================================

    async def delete(
        self,
        session: AsyncSession,
        broadcast_id: int,
    ) -> bool:
        """Удаляет рассылку."""

        broadcast = await self.get_or_raise(
            session=session,
            broadcast_id=broadcast_id,
        )

        if broadcast.status == self.STATUS_RUNNING:
            raise BroadcastValidationError(
                "Нельзя удалить выполняющуюся рассылку."
            )

        await session.delete(
            broadcast
        )

        await session.flush()

        logger.info(
            "Рассылка удалена: id={}",
            broadcast_id,
        )

        return True

    # ==================================================================
    # Статистика
    # ==================================================================

    async def get_result(
        self,
        session: AsyncSession,
        broadcast_id: int,
    ) -> BroadcastResult:
        """Возвращает текущий результат рассылки."""

        broadcast = await self.get_or_raise(
            session=session,
            broadcast_id=broadcast_id,
        )

        return BroadcastResult(
            broadcast_id=broadcast.id,
            total_users=broadcast.total_users,
            sent_count=broadcast.sent_count,
            failed_count=broadcast.failed_count,
            blocked_count=broadcast.blocked_count,
            cancelled=(
                broadcast.status
                == self.STATUS_CANCELLED
            ),
        )


# ======================================================================
# Глобальный экземпляр
# ======================================================================


broadcast_service = BroadcastService()


__all__ = [
    "BroadcastError",
    "BroadcastNotFoundError",
    "BroadcastValidationError",
    "BroadcastAlreadyStartedError",
    "BroadcastCancelledError",
    "BroadcastSendError",
    "BroadcastResult",
    "BroadcastPreview",
    "BroadcastService",
    "broadcast_service",
]