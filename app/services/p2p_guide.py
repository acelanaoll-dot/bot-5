# app/services/p2p_guide.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import GuideContentType, P2PGuideItem


# ======================================================================
# Исключения
# ======================================================================


class P2PGuideError(Exception):
    """Базовая ошибка сервиса P2P-инструкции."""


class P2PGuideItemNotFoundError(P2PGuideError):
    """Элемент инструкции не найден."""


class P2PGuideValidationError(P2PGuideError):
    """Некорректные данные элемента инструкции."""


class P2PGuideAlreadyExistsError(P2PGuideError):
    """Конфликт при создании элемента инструкции."""


# ======================================================================
# DTO
# ======================================================================


@dataclass(slots=True, frozen=True)
class P2PGuideItemData:
    """Нормализованные данные элемента инструкции."""

    id: int
    language: str
    content_type: GuideContentType
    title: str | None
    text: str | None
    photo_file_id: str | None
    sort_order: int
    is_active: bool


# ======================================================================
# Сервис
# ======================================================================


class P2PGuideService:
    """Сервис управления инструкцией P2P."""

    MAX_LANGUAGE_LENGTH = 10
    MAX_TITLE_LENGTH = 255
    MAX_TEXT_LENGTH = 10000
    MAX_FILE_ID_LENGTH = 512
    MAX_ITEMS_LIMIT = 500

    DEFAULT_LANGUAGE = "ru"

    def _normalize_language(
        self,
        language: str | None,
    ) -> str:
        """
        Нормализует язык.

        Поддерживаются произвольные короткие locale-коды,
        например ru, en, ru-RU, en-US.
        """

        value = (
            language
            or self.DEFAULT_LANGUAGE
        ).strip().lower()

        if not value:
            value = self.DEFAULT_LANGUAGE

        if len(value) > self.MAX_LANGUAGE_LENGTH:
            raise P2PGuideValidationError(
                "Код языка слишком длинный."
            )

        return value

    def _normalize_title(
        self,
        title: str | None,
    ) -> str | None:
        """Нормализует заголовок."""

        if title is None:
            return None

        value = title.strip()

        if not value:
            return None

        if len(value) > self.MAX_TITLE_LENGTH:
            raise P2PGuideValidationError(
                "Заголовок инструкции слишком длинный."
            )

        return value

    def _normalize_text(
        self,
        text: str | None,
    ) -> str | None:
        """Нормализует текст инструкции."""

        if text is None:
            return None

        value = text.strip()

        if not value:
            return None

        if len(value) > self.MAX_TEXT_LENGTH:
            raise P2PGuideValidationError(
                "Текст инструкции слишком длинный."
            )

        return value

    def _normalize_photo_file_id(
        self,
        photo_file_id: str | None,
    ) -> str | None:
        """Нормализует Telegram file_id фотографии."""

        if photo_file_id is None:
            return None

        value = photo_file_id.strip()

        if not value:
            return None

        if len(value) > self.MAX_FILE_ID_LENGTH:
            raise P2PGuideValidationError(
                "Telegram file_id слишком длинный."
            )

        return value

    def _normalize_content_type(
        self,
        content_type: GuideContentType | str,
    ) -> GuideContentType:
        """Нормализует тип содержимого."""

        if isinstance(content_type, GuideContentType):
            return content_type

        try:
            return GuideContentType(
                str(content_type).strip().lower()
            )
        except ValueError as exc:
            raise P2PGuideValidationError(
                "Недопустимый тип содержимого инструкции."
            ) from exc

    def _normalize_sort_order(
        self,
        sort_order: int,
    ) -> int:
        """Проверяет порядок отображения."""

        try:
            value = int(sort_order)
        except (TypeError, ValueError) as exc:
            raise P2PGuideValidationError(
                "Порядок инструкции должен быть целым числом."
            ) from exc

        if value < 0:
            raise P2PGuideValidationError(
                "Порядок инструкции не может быть отрицательным."
            )

        return value

    def _validate_content(
        self,
        *,
        content_type: GuideContentType,
        text: str | None,
        photo_file_id: str | None,
    ) -> None:
        """
        Проверяет соответствие типа содержимого данным.

        TEXT:
            требуется текст.

        PHOTO:
            требуется photo_file_id.
        """

        if content_type == GuideContentType.TEXT:
            if not text:
                raise P2PGuideValidationError(
                    "Для текстового шага необходимо указать текст."
                )

        elif content_type == GuideContentType.PHOTO:
            if not photo_file_id:
                raise P2PGuideValidationError(
                    "Для шага с фотографией необходимо указать Telegram file_id."
                )

    def _to_data(
        self,
        item: P2PGuideItem,
    ) -> P2PGuideItemData:
        """Преобразует ORM-модель в DTO."""

        return P2PGuideItemData(
            id=item.id,
            language=item.language,
            content_type=GuideContentType(
                item.content_type
            ),
            title=item.title,
            text=item.text,
            photo_file_id=item.photo_file_id,
            sort_order=item.sort_order,
            is_active=item.is_active,
        )

    # ==================================================================
    # Получение
    # ==================================================================

    async def get(
        self,
        session: AsyncSession,
        item_id: int,
    ) -> P2PGuideItem | None:
        """Возвращает элемент инструкции по ID."""

        if item_id <= 0:
            raise P2PGuideValidationError(
                "ID элемента должен быть положительным."
            )

        return await session.get(
            P2PGuideItem,
            item_id,
        )

    async def get_or_raise(
        self,
        session: AsyncSession,
        item_id: int,
    ) -> P2PGuideItem:
        """Возвращает элемент или выбрасывает ошибку."""

        item = await self.get(
            session=session,
            item_id=item_id,
        )

        if item is None:
            raise P2PGuideItemNotFoundError(
                f"Элемент P2P-инструкции {item_id} не найден."
            )

        return item

    async def list_items(
        self,
        session: AsyncSession,
        *,
        language: str | None = None,
        is_active: bool | None = True,
        limit: int = 500,
        offset: int = 0,
    ) -> list[P2PGuideItem]:
        """
        Возвращает элементы инструкции.

        Основной метод используется пользовательским topup handler.
        По умолчанию возвращаются только активные элементы.
        """

        if limit <= 0:
            limit = 1

        limit = min(
            limit,
            self.MAX_ITEMS_LIMIT,
        )

        offset = max(
            0,
            offset,
        )

        query = select(P2PGuideItem)

        if language is not None:
            normalized_language = self._normalize_language(
                language
            )

            query = query.where(
                P2PGuideItem.language
                == normalized_language
            )

        if is_active is not None:
            query = query.where(
                P2PGuideItem.is_active
                == bool(is_active)
            )

        query = (
            query
            .order_by(
                P2PGuideItem.sort_order.asc(),
                P2PGuideItem.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await session.execute(query)

        return list(
            result.scalars().all()
        )

    async def list_all(
        self,
        session: AsyncSession,
        *,
        language: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[P2PGuideItem]:
        """Возвращает все элементы, включая отключённые."""

        return await self.list_items(
            session=session,
            language=language,
            is_active=None,
            limit=limit,
            offset=offset,
        )

    async def count(
        self,
        session: AsyncSession,
        *,
        language: str | None = None,
        is_active: bool | None = None,
    ) -> int:
        """Возвращает количество элементов."""

        query = select(
            func.count(P2PGuideItem.id)
        )

        if language is not None:
            normalized_language = self._normalize_language(
                language
            )

            query = query.where(
                P2PGuideItem.language
                == normalized_language
            )

        if is_active is not None:
            query = query.where(
                P2PGuideItem.is_active
                == bool(is_active)
            )

        result = await session.execute(query)

        return int(
            result.scalar_one()
        )

    # ==================================================================
    # Создание
    # ==================================================================

    async def create(
        self,
        session: AsyncSession,
        *,
        language: str,
        content_type: GuideContentType | str,
        title: str | None = None,
        text: str | None = None,
        photo_file_id: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> P2PGuideItem:
        """Создаёт новый элемент инструкции."""

        normalized_language = self._normalize_language(
            language
        )

        normalized_type = self._normalize_content_type(
            content_type
        )

        normalized_title = self._normalize_title(
            title
        )

        normalized_text = self._normalize_text(
            text
        )

        normalized_photo = self._normalize_photo_file_id(
            photo_file_id
        )

        normalized_order = self._normalize_sort_order(
            sort_order
        )

        self._validate_content(
            content_type=normalized_type,
            text=normalized_text,
            photo_file_id=normalized_photo,
        )

        item = P2PGuideItem(
            language=normalized_language,
            content_type=normalized_type,
            title=normalized_title,
            text=normalized_text,
            photo_file_id=normalized_photo,
            sort_order=normalized_order,
            is_active=bool(is_active),
        )

        session.add(item)

        try:
            await session.flush()
        except IntegrityError as exc:
            logger.exception(
                "Ошибка создания P2PGuideItem."
            )

            raise P2PGuideAlreadyExistsError(
                "Не удалось создать элемент P2P-инструкции."
            ) from exc

        logger.info(
            "Создан P2PGuideItem: id={}, language={}, type={}",
            item.id,
            item.language,
            item.content_type,
        )

        return item

    async def create_text(
        self,
        session: AsyncSession,
        *,
        language: str,
        text: str,
        title: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> P2PGuideItem:
        """Создаёт текстовый шаг."""

        return await self.create(
            session=session,
            language=language,
            content_type=GuideContentType.TEXT,
            title=title,
            text=text,
            sort_order=sort_order,
            is_active=is_active,
        )

    async def create_photo(
        self,
        session: AsyncSession,
        *,
        language: str,
        photo_file_id: str,
        text: str | None = None,
        title: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
    ) -> P2PGuideItem:
        """Создаёт шаг с фотографией."""

        return await self.create(
            session=session,
            language=language,
            content_type=GuideContentType.PHOTO,
            title=title,
            text=text,
            photo_file_id=photo_file_id,
            sort_order=sort_order,
            is_active=is_active,
        )

    # ==================================================================
    # Изменение
    # ==================================================================

    async def update(
        self,
        session: AsyncSession,
        item_id: int,
        *,
        language: str | None = None,
        content_type: GuideContentType | str | None = None,
        title: str | None = None,
        text: str | None = None,
        photo_file_id: str | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
    ) -> P2PGuideItem:
        """
        Изменяет элемент инструкции.

        None означает «не изменять поле» для параметров,
        кроме title/text/photo_file_id, где передача пустой
        строки очищает соответствующее поле.
        """

        item = await self.get_or_raise(
            session=session,
            item_id=item_id,
        )

        if language is not None:
            item.language = self._normalize_language(
                language
            )

        if content_type is not None:
            item.content_type = self._normalize_content_type(
                content_type
            )

        if title is not None:
            item.title = self._normalize_title(
                title
            )

        if text is not None:
            item.text = self._normalize_text(
                text
            )

        if photo_file_id is not None:
            item.photo_file_id = self._normalize_photo_file_id(
                photo_file_id
            )

        if sort_order is not None:
            item.sort_order = self._normalize_sort_order(
                sort_order
            )

        if is_active is not None:
            item.is_active = bool(
                is_active
            )

        self._validate_content(
            content_type=GuideContentType(
                item.content_type
            ),
            text=item.text,
            photo_file_id=item.photo_file_id,
        )

        await session.flush()

        logger.info(
            "Обновлён P2PGuideItem: id={}",
            item.id,
        )

        return item

    async def update_text(
        self,
        session: AsyncSession,
        item_id: int,
        *,
        text: str,
        title: str | None = None,
        language: str | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
    ) -> P2PGuideItem:
        """Изменяет текстовый шаг."""

        return await self.update(
            session=session,
            item_id=item_id,
            language=language,
            content_type=GuideContentType.TEXT,
            title=title,
            text=text,
            sort_order=sort_order,
            is_active=is_active,
        )

    async def update_photo(
        self,
        session: AsyncSession,
        item_id: int,
        *,
        photo_file_id: str,
        text: str | None = None,
        title: str | None = None,
        language: str | None = None,
        sort_order: int | None = None,
        is_active: bool | None = None,
    ) -> P2PGuideItem:
        """Изменяет шаг с фотографией."""

        return await self.update(
            session=session,
            item_id=item_id,
            language=language,
            content_type=GuideContentType.PHOTO,
            title=title,
            text=text,
            photo_file_id=photo_file_id,
            sort_order=sort_order,
            is_active=is_active,
        )

    # ==================================================================
    # Состояние
    # ==================================================================

    async def set_active(
        self,
        session: AsyncSession,
        item_id: int,
        is_active: bool,
    ) -> P2PGuideItem:
        """Включает или отключает элемент."""

        item = await self.get_or_raise(
            session=session,
            item_id=item_id,
        )

        item.is_active = bool(
            is_active
        )

        await session.flush()

        logger.info(
            "Изменено состояние P2PGuideItem: id={}, active={}",
            item.id,
            item.is_active,
        )

        return item

    async def activate(
        self,
        session: AsyncSession,
        item_id: int,
    ) -> P2PGuideItem:
        """Активирует элемент."""

        return await self.set_active(
            session=session,
            item_id=item_id,
            is_active=True,
        )

    async def deactivate(
        self,
        session: AsyncSession,
        item_id: int,
    ) -> P2PGuideItem:
        """Отключает элемент."""

        return await self.set_active(
            session=session,
            item_id=item_id,
            is_active=False,
        )

    # ==================================================================
    # Порядок
    # ==================================================================

    async def set_sort_order(
        self,
        session: AsyncSession,
        item_id: int,
        sort_order: int,
    ) -> P2PGuideItem:
        """Устанавливает порядок элемента."""

        item = await self.get_or_raise(
            session=session,
            item_id=item_id,
        )

        item.sort_order = self._normalize_sort_order(
            sort_order
        )

        await session.flush()

        return item

    async def move(
        self,
        session: AsyncSession,
        item_id: int,
        new_sort_order: int,
    ) -> P2PGuideItem:
        """Перемещает элемент на указанную позицию."""

        return await self.set_sort_order(
            session=session,
            item_id=item_id,
            sort_order=new_sort_order,
        )

    async def normalize_sort_order(
        self,
        session: AsyncSession,
        *,
        language: str,
        is_active: bool | None = None,
    ) -> list[P2PGuideItem]:
        """
        Перенумеровывает элементы последовательно: 0, 1, 2...

        Используется после удаления или массового редактирования.
        """

        items = await self.list_items(
            session=session,
            language=language,
            is_active=is_active,
            limit=self.MAX_ITEMS_LIMIT,
            offset=0,
        )

        for index, item in enumerate(items):
            item.sort_order = index

        await session.flush()

        return items

    # ==================================================================
    # Удаление
    # ==================================================================

    async def delete(
        self,
        session: AsyncSession,
        item_id: int,
    ) -> bool:
        """Удаляет элемент инструкции."""

        item = await self.get_or_raise(
            session=session,
            item_id=item_id,
        )

        language = item.language

        await session.delete(item)
        await session.flush()

        logger.info(
            "Удалён P2PGuideItem: id={}",
            item_id,
        )

        # После удаления восстанавливаем последовательность.
        await self.normalize_sort_order(
            session=session,
            language=language,
            is_active=None,
        )

        return True

    async def delete_many(
        self,
        session: AsyncSession,
        item_ids: Sequence[int],
    ) -> int:
        """Удаляет несколько элементов."""

        normalized_ids = {
            int(item_id)
            for item_id in item_ids
            if int(item_id) > 0
        }

        if not normalized_ids:
            return 0

        result = await session.execute(
            delete(P2PGuideItem).where(
                P2PGuideItem.id.in_(
                    normalized_ids
                )
            )
        )

        deleted_count = int(
            result.rowcount or 0
        )

        if deleted_count:
            logger.info(
                "Удалено P2PGuideItem: count={}",
                deleted_count,
            )

        return deleted_count

    # ==================================================================
    # Удобные методы
    # ==================================================================

    async def get_first(
        self,
        session: AsyncSession,
        *,
        language: str = DEFAULT_LANGUAGE,
        is_active: bool = True,
    ) -> P2PGuideItem | None:
        """Возвращает первый шаг инструкции."""

        items = await self.list_items(
            session=session,
            language=language,
            is_active=is_active,
            limit=1,
            offset=0,
        )

        if not items:
            return None

        return items[0]

    async def get_last(
        self,
        session: AsyncSession,
        *,
        language: str = DEFAULT_LANGUAGE,
        is_active: bool = True,
    ) -> P2PGuideItem | None:
        """Возвращает последний шаг инструкции."""

        query = select(P2PGuideItem).where(
            P2PGuideItem.language
            == self._normalize_language(language),
        )

        if is_active is not None:
            query = query.where(
                P2PGuideItem.is_active
                == bool(is_active)
            )

        query = query.order_by(
            P2PGuideItem.sort_order.desc(),
            P2PGuideItem.id.desc(),
        ).limit(1)

        result = await session.execute(query)

        return result.scalar_one_or_none()

    async def get_step(
        self,
        session: AsyncSession,
        *,
        language: str,
        index: int,
        is_active: bool = True,
    ) -> P2PGuideItem | None:
        """
        Возвращает шаг по нулевой позиции.

        Например:
        index=0 -> первый шаг.
        index=1 -> второй шаг.
        """

        if index < 0:
            return None

        items = await self.list_items(
            session=session,
            language=language,
            is_active=is_active,
            limit=self.MAX_ITEMS_LIMIT,
            offset=0,
        )

        if index >= len(items):
            return None

        return items[index]

    async def get_step_count(
        self,
        session: AsyncSession,
        *,
        language: str,
        is_active: bool = True,
    ) -> int:
        """Возвращает количество шагов конкретного языка."""

        return await self.count(
            session=session,
            language=language,
            is_active=is_active,
        )

    def to_data(
        self,
        item: P2PGuideItem,
    ) -> P2PGuideItemData:
        """Публичное преобразование модели в DTO."""

        return self._to_data(item)


# ======================================================================
# Глобальный экземпляр
# ======================================================================


p2p_guide_service = P2PGuideService()


__all__ = [
    "P2PGuideError",
    "P2PGuideItemNotFoundError",
    "P2PGuideValidationError",
    "P2PGuideAlreadyExistsError",
    "P2PGuideItemData",
    "P2PGuideService",
    "p2p_guide_service",
]