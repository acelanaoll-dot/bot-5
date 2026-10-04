# app/services/media.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from loguru import logger
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    P2PGuideItem,
    Product,
    ProductMediaGroup,
    ProductPhoto,
)
from app.database.session import write_transaction


# ======================================================================
# Константы
# ======================================================================

MAX_PHOTOS_PER_PRODUCT = 20
MAX_MEDIA_PER_GROUP = 10
MAX_FILE_ID_LENGTH = 512
MAX_MEDIA_GROUP_ID_LENGTH = 255
MAX_MEDIA_TYPE_LENGTH = 32


ALLOWED_MEDIA_TYPES = {
    "photo",
    "video",
    "document",
    "audio",
    "animation",
}


# ======================================================================
# Исключения
# ======================================================================


class MediaServiceError(Exception):
    """Базовая ошибка медиасервиса."""


class MediaValidationError(MediaServiceError):
    """Ошибка проверки медиаданных."""


class MediaNotFoundError(MediaServiceError):
    """Медиафайл не найден."""


class MediaProductNotFoundError(MediaServiceError):
    """Товар не найден."""


class MediaLimitError(MediaServiceError):
    """Превышен лимит медиа."""


class MediaAlreadyExistsError(MediaServiceError):
    """Медиа уже существует."""


# ======================================================================
# DTO
# ======================================================================


@dataclass(slots=True, frozen=True)
class ProductPhotoInfo:
    """Информация о фотографии товара."""

    id: int
    product_id: int
    file_id: str
    file_unique_id: str | None
    sort_order: int
    is_cover: bool


@dataclass(slots=True, frozen=True)
class ProductMediaInfo:
    """Информация об элементе медиагруппы."""

    id: int
    product_id: int
    media_group_id: str
    media_type: str
    file_id: str
    sort_order: int


@dataclass(slots=True, frozen=True)
class MediaGroupInfo:
    """Медиагруппа товара."""

    media_group_id: str
    items: tuple[ProductMediaInfo, ...]


@dataclass(slots=True, frozen=True)
class MediaStats:
    """Статистика медиа товара."""

    photos_count: int
    media_groups_count: int
    media_items_count: int


# ======================================================================
# Вспомогательные функции
# ======================================================================


def _validate_id(
    value: int,
    *,
    field_name: str,
) -> int:
    """Проверяет идентификатор."""

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise MediaValidationError(
            f"Некорректный {field_name}."
        )

    return value


def _validate_file_id(
    file_id: str,
) -> str:
    """Проверяет Telegram file_id."""

    if not isinstance(file_id, str):
        raise MediaValidationError(
            "file_id должен быть строкой."
        )

    normalized = file_id.strip()

    if not normalized:
        raise MediaValidationError(
            "file_id не может быть пустым."
        )

    if len(normalized) > MAX_FILE_ID_LENGTH:
        raise MediaValidationError(
            "file_id слишком длинный."
        )

    return normalized


def _validate_file_unique_id(
    file_unique_id: str | None,
) -> str | None:
    """Проверяет Telegram file_unique_id."""

    if file_unique_id is None:
        return None

    if not isinstance(file_unique_id, str):
        raise MediaValidationError(
            "file_unique_id должен быть строкой."
        )

    normalized = file_unique_id.strip()

    if not normalized:
        return None

    if len(normalized) > MAX_FILE_ID_LENGTH:
        raise MediaValidationError(
            "file_unique_id слишком длинный."
        )

    return normalized


def _validate_media_group_id(
    media_group_id: str,
) -> str:
    """Проверяет идентификатор медиагруппы."""

    if not isinstance(
        media_group_id,
        str,
    ):
        raise MediaValidationError(
            "media_group_id должен быть строкой."
        )

    normalized = media_group_id.strip()

    if not normalized:
        raise MediaValidationError(
            "media_group_id не может быть пустым."
        )

    if len(normalized) > MAX_MEDIA_GROUP_ID_LENGTH:
        raise MediaValidationError(
            "media_group_id слишком длинный."
        )

    return normalized


def _validate_media_type(
    media_type: str,
) -> str:
    """Проверяет тип Telegram-медиа."""

    if not isinstance(
        media_type,
        str,
    ):
        raise MediaValidationError(
            "media_type должен быть строкой."
        )

    normalized = media_type.strip().lower()

    if normalized not in ALLOWED_MEDIA_TYPES:
        raise MediaValidationError(
            "Неподдерживаемый тип медиа: "
            f"{normalized}"
        )

    if len(normalized) > MAX_MEDIA_TYPE_LENGTH:
        raise MediaValidationError(
            "media_type слишком длинный."
        )

    return normalized


def _validate_sort_order(
    sort_order: int,
) -> int:
    """Проверяет порядок сортировки."""

    if (
        isinstance(sort_order, bool)
        or not isinstance(sort_order, int)
        or sort_order < 0
    ):
        raise MediaValidationError(
            "sort_order должен быть целым числом >= 0."
        )

    return sort_order


def _photo_to_info(
    photo: ProductPhoto,
) -> ProductPhotoInfo:
    """Преобразует модель фотографии в DTO."""

    return ProductPhotoInfo(
        id=photo.id,
        product_id=photo.product_id,
        file_id=photo.file_id,
        file_unique_id=photo.file_unique_id,
        sort_order=photo.sort_order,
        is_cover=photo.is_cover,
    )


def _media_to_info(
    media: ProductMediaGroup,
) -> ProductMediaInfo:
    """Преобразует модель медиа в DTO."""

    return ProductMediaInfo(
        id=media.id,
        product_id=media.product_id,
        media_group_id=media.media_group_id,
        media_type=media.media_type,
        file_id=media.file_id,
        sort_order=media.sort_order,
    )


# ======================================================================
# Сервис
# ======================================================================


class MediaService:
    """Сервис управления Telegram-медиа товаров и P2P."""

    # ==================================================================
    # Товар
    # ==================================================================

    async def product_exists(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> bool:
        """Проверяет существование товара."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        value = await session.scalar(
            select(Product.id)
            .where(Product.id == product_id)
            .limit(1)
        )

        return value is not None

    async def _require_product(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> Product:
        """Получает товар или выбрасывает ошибку."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        product = await session.scalar(
            select(Product)
            .where(Product.id == product_id)
            .limit(1)
        )

        if product is None:
            raise MediaProductNotFoundError(
                f"Товар #{product_id} не найден."
            )

        return product

    # ==================================================================
    # Фотографии товара
    # ==================================================================

    async def get_photo(
        self,
        session: AsyncSession,
        *,
        photo_id: int,
    ) -> ProductPhoto | None:
        """Получает фотографию по ID."""

        photo_id = _validate_id(
            photo_id,
            field_name="ID фотографии",
        )

        return await session.scalar(
            select(ProductPhoto)
            .where(ProductPhoto.id == photo_id)
            .limit(1)
        )

    async def get_photo_or_raise(
        self,
        session: AsyncSession,
        *,
        photo_id: int,
    ) -> ProductPhoto:
        """Получает фотографию или выбрасывает ошибку."""

        photo = await self.get_photo(
            session,
            photo_id=photo_id,
        )

        if photo is None:
            raise MediaNotFoundError(
                f"Фотография #{photo_id} не найдена."
            )

        return photo

    async def list_photos(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> list[ProductPhoto]:
        """Возвращает фотографии товара."""

        await self._require_product(
            session,
            product_id=product_id,
        )

        result = await session.execute(
            select(ProductPhoto)
            .where(
                ProductPhoto.product_id == product_id
            )
            .order_by(
                ProductPhoto.sort_order.asc(),
                ProductPhoto.id.asc(),
            )
        )

        return list(
            result.scalars().all()
        )

    async def get_photo_infos(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> list[ProductPhotoInfo]:
        """Возвращает DTO фотографий товара."""

        photos = await self.list_photos(
            session,
            product_id=product_id,
        )

        return [
            _photo_to_info(photo)
            for photo in photos
        ]

    async def count_photos(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> int:
        """Возвращает количество фотографий товара."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        value = await session.scalar(
            select(
                func.count(ProductPhoto.id)
            )
            .where(
                ProductPhoto.product_id == product_id
            )
        )

        return int(value or 0)

    async def add_photo(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        file_id: str,
        file_unique_id: str | None = None,
        sort_order: int | None = None,
        is_cover: bool = False,
    ) -> ProductPhoto:
        """Добавляет фотографию товару."""

        product = await self._require_product(
            session,
            product_id=product_id,
        )

        file_id = _validate_file_id(
            file_id
        )

        file_unique_id = _validate_file_unique_id(
            file_unique_id
        )

        if not isinstance(
            is_cover,
            bool,
        ):
            raise MediaValidationError(
                "is_cover должен быть bool."
            )

        current_count = await self.count_photos(
            session,
            product_id=product.id,
        )

        if current_count >= MAX_PHOTOS_PER_PRODUCT:
            raise MediaLimitError(
                "Превышен лимит фотографий товара: "
                f"{MAX_PHOTOS_PER_PRODUCT}."
            )

        if sort_order is None:
            sort_order = current_count
        else:
            sort_order = _validate_sort_order(
                sort_order
            )

        # Если фото назначается обложкой,
        # остальные обложки снимаем.
        if is_cover:
            await session.execute(
                update(ProductPhoto)
                .where(
                    ProductPhoto.product_id
                    == product.id
                )
                .values(
                    is_cover=False
                )
            )

        photo = ProductPhoto(
            product_id=product.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            sort_order=sort_order,
            is_cover=is_cover,
        )

        session.add(photo)

        try:
            await session.flush()
        except IntegrityError as exc:
            logger.exception(
                "Ошибка добавления фотографии товара "
                "product_id={}",
                product.id,
            )
            raise MediaAlreadyExistsError(
                "Не удалось добавить фотографию."
            ) from exc

        return photo

    async def add_photos(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        photos: Iterable[
            tuple[
                str,
                str | None,
            ]
        ],
    ) -> list[ProductPhoto]:
        """
        Добавляет несколько фотографий.

        Каждый элемент:
            (file_id, file_unique_id)
        """

        items = list(photos)

        if not items:
            return []

        current_count = await self.count_photos(
            session,
            product_id=product_id,
        )

        if (
            current_count + len(items)
            > MAX_PHOTOS_PER_PRODUCT
        ):
            raise MediaLimitError(
                "Превышен лимит фотографий товара."
            )

        result: list[ProductPhoto] = []

        for index, item in enumerate(items):
            if not isinstance(item, tuple):
                raise MediaValidationError(
                    "Элемент photos должен быть tuple."
                )

            if len(item) != 2:
                raise MediaValidationError(
                    "Элемент photos должен содержать "
                    "file_id и file_unique_id."
                )

            file_id, file_unique_id = item

            photo = await self.add_photo(
                session,
                product_id=product_id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                sort_order=current_count + index,
                is_cover=(
                    current_count == 0
                    and index == 0
                ),
            )

            result.append(photo)

        return result

    async def set_cover(
        self,
        session: AsyncSession,
        *,
        photo_id: int,
    ) -> ProductPhoto:
        """Назначает фотографию обложкой товара."""

        photo = await self.get_photo_or_raise(
            session,
            photo_id=photo_id,
        )

        await session.execute(
            update(ProductPhoto)
            .where(
                ProductPhoto.product_id
                == photo.product_id
            )
            .values(
                is_cover=False
            )
        )

        photo.is_cover = True

        await session.flush()

        return photo

    async def clear_cover(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> None:
        """Снимает обложку товара."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        await session.execute(
            update(ProductPhoto)
            .where(
                ProductPhoto.product_id == product_id
            )
            .values(
                is_cover=False
            )
        )

    async def update_photo_order(
        self,
        session: AsyncSession,
        *,
        photo_id: int,
        sort_order: int,
    ) -> ProductPhoto:
        """Изменяет порядок фотографии."""

        photo = await self.get_photo_or_raise(
            session,
            photo_id=photo_id,
        )

        photo.sort_order = _validate_sort_order(
            sort_order
        )

        await session.flush()

        return photo

    async def delete_photo(
        self,
        session: AsyncSession,
        *,
        photo_id: int,
    ) -> bool:
        """Удаляет фотографию товара."""

        photo = await self.get_photo_or_raise(
            session,
            photo_id=photo_id,
        )

        product_id = photo.product_id
        was_cover = photo.is_cover

        await session.delete(photo)
        await session.flush()

        # Если удалили обложку, назначаем первой
        # оставшейся фотографии новую обложку.
        if was_cover:
            next_photo = await session.scalar(
                select(ProductPhoto)
                .where(
                    ProductPhoto.product_id
                    == product_id
                )
                .order_by(
                    ProductPhoto.sort_order.asc(),
                    ProductPhoto.id.asc(),
                )
                .limit(1)
            )

            if next_photo is not None:
                next_photo.is_cover = True

                await session.flush()

        return True

    async def delete_all_photos(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> int:
        """Удаляет все фотографии товара."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        result = await session.execute(
            delete(ProductPhoto)
            .where(
                ProductPhoto.product_id == product_id
            )
        )

        return int(
            result.rowcount or 0
        )

    # ==================================================================
    # Медиагруппы товара
    # ==================================================================

    async def get_media(
        self,
        session: AsyncSession,
        *,
        media_id: int,
    ) -> ProductMediaGroup | None:
        """Получает элемент медиагруппы."""

        media_id = _validate_id(
            media_id,
            field_name="ID медиа",
        )

        return await session.scalar(
            select(ProductMediaGroup)
            .where(
                ProductMediaGroup.id == media_id
            )
            .limit(1)
        )

    async def get_media_or_raise(
        self,
        session: AsyncSession,
        *,
        media_id: int,
    ) -> ProductMediaGroup:
        """Получает медиа или выбрасывает ошибку."""

        media = await self.get_media(
            session,
            media_id=media_id,
        )

        if media is None:
            raise MediaNotFoundError(
                f"Медиа #{media_id} не найдено."
            )

        return media

    async def list_media(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        media_group_id: str | None = None,
    ) -> list[ProductMediaGroup]:
        """Возвращает медиа товара."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        statement = (
            select(ProductMediaGroup)
            .where(
                ProductMediaGroup.product_id
                == product_id
            )
        )

        if media_group_id is not None:
            media_group_id = _validate_media_group_id(
                media_group_id
            )

            statement = statement.where(
                ProductMediaGroup.media_group_id
                == media_group_id
            )

        statement = statement.order_by(
            ProductMediaGroup.sort_order.asc(),
            ProductMediaGroup.id.asc(),
        )

        result = await session.execute(
            statement
        )

        return list(
            result.scalars().all()
        )

    async def add_media(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        media_group_id: str,
        media_type: str,
        file_id: str,
        sort_order: int | None = None,
    ) -> ProductMediaGroup:
        """Добавляет элемент медиагруппы."""

        product = await self._require_product(
            session,
            product_id=product_id,
        )

        media_group_id = _validate_media_group_id(
            media_group_id
        )

        media_type = _validate_media_type(
            media_type
        )

        file_id = _validate_file_id(
            file_id
        )

        current_group_count = await session.scalar(
            select(
                func.count(
                    ProductMediaGroup.id
                )
            )
            .where(
                ProductMediaGroup.product_id
                == product.id,
                ProductMediaGroup.media_group_id
                == media_group_id,
            )
        )

        current_group_count = int(
            current_group_count or 0
        )

        if current_group_count >= MAX_MEDIA_PER_GROUP:
            raise MediaLimitError(
                "Превышен лимит элементов медиагруппы: "
                f"{MAX_MEDIA_PER_GROUP}."
            )

        # Защита от повторной записи одного и того же
        # Telegram file_id в одной группе.
        existing = await session.scalar(
            select(ProductMediaGroup)
            .where(
                ProductMediaGroup.product_id
                == product.id,
                ProductMediaGroup.media_group_id
                == media_group_id,
                ProductMediaGroup.file_id
                == file_id,
            )
            .limit(1)
        )

        if existing is not None:
            raise MediaAlreadyExistsError(
                "Это медиа уже добавлено в данную группу."
            )

        if sort_order is None:
            sort_order = current_group_count
        else:
            sort_order = _validate_sort_order(
                sort_order
            )

        media = ProductMediaGroup(
            product_id=product.id,
            media_group_id=media_group_id,
            media_type=media_type,
            file_id=file_id,
            sort_order=sort_order,
        )

        session.add(media)

        try:
            await session.flush()
        except IntegrityError as exc:
            logger.exception(
                "Ошибка добавления медиа: "
                "product_id={}, group_id={}",
                product.id,
                media_group_id,
            )

            raise MediaAlreadyExistsError(
                "Не удалось добавить медиа."
            ) from exc

        return media

    async def add_media_group(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        media_group_id: str,
        items: Iterable[
            tuple[
                str,
                str,
            ]
        ],
    ) -> list[ProductMediaGroup]:
        """
        Добавляет целую медиагруппу.

        Каждый элемент:
            (media_type, file_id)
        """

        items_list = list(items)

        if not items_list:
            return []

        if len(items_list) > MAX_MEDIA_PER_GROUP:
            raise MediaLimitError(
                "В медиагруппе слишком много элементов."
            )

        result: list[ProductMediaGroup] = []

        for index, item in enumerate(items_list):
            if not isinstance(item, tuple):
                raise MediaValidationError(
                    "Элемент items должен быть tuple."
                )

            if len(item) != 2:
                raise MediaValidationError(
                    "Элемент items должен содержать "
                    "media_type и file_id."
                )

            media_type, file_id = item

            media = await self.add_media(
                session,
                product_id=product_id,
                media_group_id=media_group_id,
                media_type=media_type,
                file_id=file_id,
                sort_order=index,
            )

            result.append(media)

        return result

    async def list_media_groups(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> list[MediaGroupInfo]:
        """Возвращает медиагруппы товара."""

        media_items = await self.list_media(
            session,
            product_id=product_id,
        )

        groups: dict[
            str,
            list[ProductMediaInfo],
        ] = {}

        for item in media_items:
            groups.setdefault(
                item.media_group_id,
                [],
            ).append(
                _media_to_info(item)
            )

        return [
            MediaGroupInfo(
                media_group_id=group_id,
                items=tuple(items),
            )
            for group_id, items in groups.items()
        ]

    async def delete_media(
        self,
        session: AsyncSession,
        *,
        media_id: int,
    ) -> bool:
        """Удаляет один элемент медиагруппы."""

        media = await self.get_media_or_raise(
            session,
            media_id=media_id,
        )

        await session.delete(media)
        await session.flush()

        return True

    async def delete_media_group(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        media_group_id: str,
    ) -> int:
        """Удаляет целую медиагруппу."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        media_group_id = _validate_media_group_id(
            media_group_id
        )

        result = await session.execute(
            delete(ProductMediaGroup)
            .where(
                ProductMediaGroup.product_id
                == product_id,
                ProductMediaGroup.media_group_id
                == media_group_id,
            )
        )

        return int(
            result.rowcount or 0
        )

    async def count_media(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> int:
        """Возвращает количество элементов медиа."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        value = await session.scalar(
            select(
                func.count(
                    ProductMediaGroup.id
                )
            )
            .where(
                ProductMediaGroup.product_id
                == product_id
            )
        )

        return int(value or 0)

    async def count_media_groups(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> int:
        """Возвращает количество медиагрупп."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        value = await session.scalar(
            select(
                func.count(
                    func.distinct(
                        ProductMediaGroup.media_group_id
                    )
                )
            )
            .where(
                ProductMediaGroup.product_id
                == product_id
            )
        )

        return int(value or 0)

    # ==================================================================
    # Общая статистика
    # ==================================================================

    async def get_stats(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> MediaStats:
        """Возвращает статистику медиа товара."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        photos_count = await self.count_photos(
            session,
            product_id=product_id,
        )

        media_count = await self.count_media(
            session,
            product_id=product_id,
        )

        groups_count = await self.count_media_groups(
            session,
            product_id=product_id,
        )

        return MediaStats(
            photos_count=photos_count,
            media_groups_count=groups_count,
            media_items_count=media_count,
        )

    # ==================================================================
    # Telegram payload
    # ==================================================================

    async def get_telegram_media(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> list[dict[str, str]]:
        """
        Возвращает медиа товара в простом формате,
        удобном для формирования Telegram MediaGroup.
        """

        media_items = await self.list_media(
            session,
            product_id=product_id,
        )

        result: list[dict[str, str]] = []

        for item in media_items:
            result.append(
                {
                    "type": item.media_type,
                    "media": item.file_id,
                }
            )

        if result:
            return result

        photos = await self.list_photos(
            session,
            product_id=product_id,
        )

        return [
            {
                "type": "photo",
                "media": photo.file_id,
            }
            for photo in photos
        ]

    async def get_cover_file_id(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> str | None:
        """Возвращает file_id обложки товара."""

        product_id = _validate_id(
            product_id,
            field_name="ID товара",
        )

        photo = await session.scalar(
            select(ProductPhoto)
            .where(
                ProductPhoto.product_id
                == product_id,
                ProductPhoto.is_cover.is_(True),
            )
            .order_by(
                ProductPhoto.sort_order.asc(),
                ProductPhoto.id.asc(),
            )
            .limit(1)
        )

        if photo is None:
            photo = await session.scalar(
                select(ProductPhoto)
                .where(
                    ProductPhoto.product_id
                    == product_id
                )
                .order_by(
                    ProductPhoto.sort_order.asc(),
                    ProductPhoto.id.asc(),
                )
                .limit(1)
            )

        if photo is None:
            return None

        return photo.file_id

    # ==================================================================
    # P2P guide
    # ==================================================================

    async def get_p2p_photo(
        self,
        session: AsyncSession,
        *,
        item_id: int,
    ) -> str | None:
        """Возвращает file_id фотографии P2P-инструкции."""

        item_id = _validate_id(
            item_id,
            field_name="ID элемента P2P-инструкции",
        )

        item = await session.scalar(
            select(P2PGuideItem)
            .where(
                P2PGuideItem.id == item_id
            )
            .limit(1)
        )

        if item is None:
            raise MediaNotFoundError(
                f"P2P-элемент #{item_id} не найден."
            )

        return item.photo_file_id

    async def set_p2p_photo(
        self,
        session: AsyncSession,
        *,
        item_id: int,
        file_id: str | None,
    ) -> P2PGuideItem:
        """Устанавливает или удаляет фото P2P-инструкции."""

        item_id = _validate_id(
            item_id,
            field_name="ID элемента P2P-инструкции",
        )

        if file_id is not None:
            file_id = _validate_file_id(
                file_id
            )

        item = await session.scalar(
            select(P2PGuideItem)
            .where(
                P2PGuideItem.id == item_id
            )
            .limit(1)
        )

        if item is None:
            raise MediaNotFoundError(
                f"P2P-элемент #{item_id} не найден."
            )

        item.photo_file_id = file_id

        await session.flush()

        return item

    # ==================================================================
    # Безопасные операции через transaction helper
    # ==================================================================

    async def add_photo_atomic(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        file_id: str,
        file_unique_id: str | None = None,
        sort_order: int | None = None,
        is_cover: bool = False,
    ) -> ProductPhoto:
        """Добавляет фотографию в транзакции."""

        async with write_transaction(
            session
        ):
            return await self.add_photo(
                session,
                product_id=product_id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                sort_order=sort_order,
                is_cover=is_cover,
            )

    async def add_media_atomic(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        media_group_id: str,
        media_type: str,
        file_id: str,
        sort_order: int | None = None,
    ) -> ProductMediaGroup:
        """Добавляет медиа в транзакции."""

        async with write_transaction(
            session
        ):
            return await self.add_media(
                session,
                product_id=product_id,
                media_group_id=media_group_id,
                media_type=media_type,
                file_id=file_id,
                sort_order=sort_order,
            )

    async def delete_photo_atomic(
        self,
        session: AsyncSession,
        *,
        photo_id: int,
    ) -> bool:
        """Удаляет фотографию в транзакции."""

        async with write_transaction(
            session
        ):
            return await self.delete_photo(
                session,
                photo_id=photo_id,
            )

    async def delete_media_atomic(
        self,
        session: AsyncSession,
        *,
        media_id: int,
    ) -> bool:
        """Удаляет медиа в транзакции."""

        async with write_transaction(
            session
        ):
            return await self.delete_media(
                session,
                media_id=media_id,
            )


# ======================================================================
# Глобальный экземпляр
# ======================================================================


media_service = MediaService()


__all__ = [
    "MediaServiceError",
    "MediaValidationError",
    "MediaNotFoundError",
    "MediaProductNotFoundError",
    "MediaLimitError",
    "MediaAlreadyExistsError",
    "ProductPhotoInfo",
    "ProductMediaInfo",
    "MediaGroupInfo",
    "MediaStats",
    "MediaService",
    "media_service",
]