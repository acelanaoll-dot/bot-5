from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Category,
    Product,
    ProductPhoto,
)
from app.database.session import write_transaction


# ============================================================
# Константы
# ============================================================

MONEY_QUANT = Decimal("0.00000001")
MAX_PRODUCT_NAME_LENGTH = 255
MAX_PRODUCT_SLUG_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 20_000
MAX_PHOTO_FILE_ID_LENGTH = 512
MAX_PAGE_SIZE = 100


# ============================================================
# Исключения
# ============================================================

class ProductServiceError(Exception):
    """Базовая ошибка сервиса товаров."""


class ProductNotFoundError(ProductServiceError):
    """Товар не найден."""


class ProductAlreadyExistsError(ProductServiceError):
    """Товар с таким идентификатором или slug уже существует."""


class ProductValidationError(ProductServiceError):
    """Ошибка проверки данных товара."""


class ProductUnavailableError(ProductServiceError):
    """Товар недоступен для покупки."""


class ProductStockError(ProductServiceError):
    """Ошибка изменения остатка товара."""


class ProductPhotoError(ProductServiceError):
    """Ошибка работы с фотографиями товара."""


# ============================================================
# Валидация
# ============================================================

def _normalize_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
    allow_empty: bool = False,
) -> str:
    """Очищает и проверяет текстовое поле."""

    if not isinstance(value, str):
        raise ProductValidationError(
            f"Поле «{field_name}» должно быть строкой."
        )

    normalized = value.strip()

    if not normalized and not allow_empty:
        raise ProductValidationError(
            f"Поле «{field_name}» не может быть пустым."
        )

    if len(normalized) > max_length:
        raise ProductValidationError(
            f"Поле «{field_name}» слишком длинное. "
            f"Максимум: {max_length} символов."
        )

    return normalized


def _normalize_slug(value: str) -> str:
    """Нормализует slug для URL и поиска."""

    slug = _normalize_text(
        value,
        field_name="slug",
        max_length=MAX_PRODUCT_SLUG_LENGTH,
    )

    slug = slug.lower()
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"[^a-zа-яё0-9_-]", "", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-_")

    if not slug:
        raise ProductValidationError(
            "Не удалось сформировать slug товара."
        )

    return slug


def _make_slug(value: str) -> str:
    """
    Создаёт slug из названия.

    Для названий на русском языке допускаются кириллические
    символы. При необходимости латинский slug можно передать
    отдельно через параметр slug.
    """

    return _normalize_slug(value)


def _normalize_price(
    value: Decimal | int | float | str,
) -> Decimal:
    """Проверяет цену и приводит её к Decimal."""

    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ProductValidationError(
            "Цена должна быть корректным числом."
        ) from exc

    if not price.is_finite():
        raise ProductValidationError(
            "Цена должна быть конечным числом."
        )

    if price < 0:
        raise ProductValidationError(
            "Цена не может быть отрицательной."
        )

    return price.quantize(MONEY_QUANT)


def _normalize_stock(value: int) -> int:
    """Проверяет количество товара на складе."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductValidationError(
            "Количество товара должно быть целым числом."
        )

    if value < 0:
        raise ProductValidationError(
            "Остаток товара не может быть отрицательным."
        )

    return value


def _normalize_quantity(value: int) -> int:
    """Проверяет количество товара для операции."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ProductValidationError(
            "Количество должно быть целым числом."
        )

    if value <= 0:
        raise ProductValidationError(
            "Количество должно быть больше нуля."
        )

    return value


# ============================================================
# Сервис товаров
# ============================================================

class ProductService:
    """Асинхронный сервис управления товарами."""

    # --------------------------------------------------------
    # Получение товара
    # --------------------------------------------------------

    async def get_by_id(
        self,
        session: AsyncSession,
        product_id: int,
        *,
        include_photos: bool = True,
    ) -> Product | None:
        """Получает товар по внутреннему ID."""

        if product_id <= 0:
            return None

        query = select(Product).where(
            Product.id == product_id
        )

        if include_photos:
            query = query.options(
                selectinload(Product.photos)
            )

        result = await session.execute(query)

        return result.scalar_one_or_none()

    async def get_product(
        self,
        session: AsyncSession,
        product_id: int,
    ) -> Product | None:
        """Совместимый алиас получения товара."""

        return await self.get_by_id(
            session,
            product_id,
        )

    async def get_by_slug(
        self,
        session: AsyncSession,
        slug: str,
        *,
        include_photos: bool = True,
    ) -> Product | None:
        """Получает товар по slug."""

        normalized_slug = _normalize_slug(slug)

        query = select(Product).where(
            Product.slug == normalized_slug
        )

        if include_photos:
            query = query.options(
                selectinload(Product.photos)
            )

        result = await session.execute(query)

        return result.scalar_one_or_none()

    async def get_or_raise(
        self,
        session: AsyncSession,
        product_id: int,
        *,
        include_photos: bool = True,
    ) -> Product:
        """Получает товар или выбрасывает исключение."""

        product = await self.get_by_id(
            session,
            product_id,
            include_photos=include_photos,
        )

        if product is None:
            raise ProductNotFoundError(
                f"Товар #{product_id} не найден."
            )

        return product

    # --------------------------------------------------------
    # Список товаров
    # --------------------------------------------------------

    async def list_products(
        self,
        session: AsyncSession,
        *,
        category_id: int | None = None,
        active_only: bool = True,
        include_hidden: bool = False,
        purchasable_only: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Product]:
        """
        Возвращает товары с фильтрами.

        По умолчанию пользователю доступны только активные
        и не скрытые товары.
        """

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)

        query = select(Product)

        if category_id is not None:
            if category_id <= 0:
                return []

            query = query.where(
                Product.category_id == category_id
            )

        if active_only:
            query = query.where(
                Product.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Product.is_hidden.is_(False)
            )

        if purchasable_only:
            query = query.where(
                Product.allow_purchase.is_(True),
                Product.stock > 0,
            )

        query = (
            query
            .options(selectinload(Product.photos))
            .order_by(
                Product.sort_order.asc(),
                Product.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await session.execute(query)

        return list(result.scalars().unique().all())

    async def get_category_products(
        self,
        session: AsyncSession,
        *,
        category_id: int,
        page: int = 1,
        per_page: int = 10,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[Product]:
        """Получает страницу товаров выбранной категории."""

        page = max(1, page)
        per_page = max(1, min(per_page, MAX_PAGE_SIZE))

        return await self.list_products(
            session,
            category_id=category_id,
            active_only=active_only,
            include_hidden=include_hidden,
            limit=per_page,
            offset=(page - 1) * per_page,
        )

    async def count_products(
        self,
        session: AsyncSession,
        *,
        category_id: int | None = None,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> int:
        """Подсчитывает количество товаров с фильтрами."""

        query = select(func.count(Product.id))

        if category_id is not None:
            query = query.where(
                Product.category_id == category_id
            )

        if active_only:
            query = query.where(
                Product.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Product.is_hidden.is_(False)
            )

        result = await session.execute(query)

        return int(result.scalar_one())

    # --------------------------------------------------------
    # Поиск
    # --------------------------------------------------------

    async def search(
        self,
        session: AsyncSession,
        query_text: str,
        *,
        category_id: int | None = None,
        active_only: bool = True,
        include_hidden: bool = False,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Product]:
        """Ищет товары по названию, описанию и slug."""

        query_text = _normalize_text(
            query_text,
            field_name="поисковый запрос",
            max_length=255,
        )

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)

        # Экранируем специальные символы LIKE.
        escaped = (
            query_text
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

        pattern = f"%{escaped}%"

        conditions = [
            Product.name.ilike(pattern, escape="\\"),
            Product.slug.ilike(pattern, escape="\\"),
        ]

        conditions.append(
            Product.description.ilike(
                pattern,
                escape="\\",
            )
        )

        query = select(Product).where(
            or_(*conditions)
        )

        if category_id is not None:
            query = query.where(
                Product.category_id == category_id
            )

        if active_only:
            query = query.where(
                Product.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Product.is_hidden.is_(False)
            )

        query = (
            query
            .options(selectinload(Product.photos))
            .order_by(
                Product.sort_order.asc(),
                Product.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await session.execute(query)

        return list(result.scalars().unique().all())

    async def search_products(
        self,
        session: AsyncSession,
        query_text: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Product]:
        """Совместимый метод поиска для пользовательского каталога."""

        return await self.search(
            session,
            query_text,
            limit=limit,
            offset=offset,
        )

    # --------------------------------------------------------
    # Создание
    # --------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        category_id: int,
        name: str,
        price_usd: Decimal | int | float | str,
        slug: str | None = None,
        description: str | None = None,
        stock: int = 0,
        is_active: bool = True,
        is_hidden: bool = False,
        allow_purchase: bool = True,
        sort_order: int = 0,
        extra_data: dict[str, Any] | None = None,
    ) -> Product:
        """Создаёт товар."""

        if category_id <= 0:
            raise ProductValidationError(
                "Необходимо выбрать категорию."
            )

        normalized_name = _normalize_text(
            name,
            field_name="название",
            max_length=MAX_PRODUCT_NAME_LENGTH,
        )

        normalized_slug = _normalize_slug(
            slug or normalized_name
        )

        normalized_price = _normalize_price(
            price_usd
        )

        normalized_stock = _normalize_stock(stock)

        if description is not None:
            description = _normalize_text(
                description,
                field_name="описание",
                max_length=MAX_DESCRIPTION_LENGTH,
                allow_empty=True,
            )

        if extra_data is not None and not isinstance(
            extra_data,
            dict,
        ):
            raise ProductValidationError(
                "extra_data должен быть словарём."
            )

        if not isinstance(is_active, bool):
            raise ProductValidationError(
                "is_active должен быть bool."
            )

        if not isinstance(is_hidden, bool):
            raise ProductValidationError(
                "is_hidden должен быть bool."
            )

        if not isinstance(allow_purchase, bool):
            raise ProductValidationError(
                "allow_purchase должен быть bool."
            )

        async with write_transaction(session):
            category_result = await session.execute(
                select(Category.id).where(
                    Category.id == category_id,
                    Category.is_active.is_(True),
                )
            )

            if category_result.scalar_one_or_none() is None:
                raise ProductValidationError(
                    "Категория не найдена или отключена."
                )

            existing_result = await session.execute(
                select(Product.id).where(
                    Product.slug == normalized_slug
                )
            )

            if existing_result.scalar_one_or_none() is not None:
                raise ProductAlreadyExistsError(
                    "Товар с таким slug уже существует."
                )

            product = Product(
                category_id=category_id,
                name=normalized_name,
                slug=normalized_slug,
                description=description or None,
                price_usd=normalized_price,
                stock=normalized_stock,
                is_active=is_active,
                is_hidden=is_hidden,
                allow_purchase=allow_purchase,
                sort_order=sort_order,
                extra_data=extra_data,
            )

            session.add(product)

            try:
                await session.flush()
            except IntegrityError as exc:
                logger.warning(
                    "Не удалось создать товар: slug={}",
                    normalized_slug,
                )
                raise ProductAlreadyExistsError(
                    "Не удалось создать товар: "
                    "возможно, такой slug уже используется."
                ) from exc

            logger.info(
                "Создан товар: product_id={}, "
                "name={!r}, price_usd={}, stock={}",
                product.id,
                product.name,
                product.price_usd,
                product.stock,
            )

            return product

    async def create_product(
        self,
        session: AsyncSession,
        **kwargs: Any,
    ) -> Product:
        """Совместимый алиас создания товара."""

        return await self.create(
            session,
            **kwargs,
        )

    # --------------------------------------------------------
    # Изменение товара
    # --------------------------------------------------------

    async def update(
        self,
        session: AsyncSession,
        product_id: int,
        **fields: Any,
    ) -> Product:
        """
        Обновляет разрешённые поля товара.

        Неизвестные поля отклоняются, чтобы нельзя было
        случайно изменить служебные значения модели.
        """

        allowed_fields = {
            "category_id",
            "name",
            "slug",
            "description",
            "price_usd",
            "stock",
            "is_active",
            "is_hidden",
            "allow_purchase",
            "sort_order",
            "extra_data",
        }

        unknown = set(fields) - allowed_fields

        if unknown:
            raise ProductValidationError(
                "Неизвестные поля: "
                + ", ".join(sorted(unknown))
            )

        if not fields:
            return await self.get_or_raise(
                session,
                product_id,
            )

        async with write_transaction(session):
            product = await self.get_or_raise(
                session,
                product_id,
                include_photos=False,
            )

            if "category_id" in fields:
                category_id = fields["category_id"]

                if (
                    isinstance(category_id, bool)
                    or not isinstance(category_id, int)
                    or category_id <= 0
                ):
                    raise ProductValidationError(
                        "Некорректный ID категории."
                    )

                category_result = await session.execute(
                    select(Category.id).where(
                        Category.id == category_id,
                        Category.is_active.is_(True),
                    )
                )

                if category_result.scalar_one_or_none() is None:
                    raise ProductValidationError(
                        "Категория не найдена или отключена."
                    )

                product.category_id = category_id

            if "name" in fields:
                product.name = _normalize_text(
                    fields["name"],
                    field_name="название",
                    max_length=MAX_PRODUCT_NAME_LENGTH,
                )

            if "slug" in fields:
                new_slug = _normalize_slug(
                    fields["slug"]
                )

                duplicate_result = await session.execute(
                    select(Product.id).where(
                        Product.slug == new_slug,
                        Product.id != product_id,
                    )
                )

                if (
                    duplicate_result.scalar_one_or_none()
                    is not None
                ):
                    raise ProductAlreadyExistsError(
                        "Этот slug уже занят."
                    )

                product.slug = new_slug

            if "description" in fields:
                description = fields["description"]

                if description is not None:
                    description = _normalize_text(
                        description,
                        field_name="описание",
                        max_length=MAX_DESCRIPTION_LENGTH,
                        allow_empty=True,
                    )

                product.description = description or None

            if "price_usd" in fields:
                product.price_usd = _normalize_price(
                    fields["price_usd"]
                )

            if "stock" in fields:
                product.stock = _normalize_stock(
                    fields["stock"]
                )

            for field_name in (
                "is_active",
                "is_hidden",
                "allow_purchase",
            ):
                if field_name in fields:
                    value = fields[field_name]

                    if not isinstance(value, bool):
                        raise ProductValidationError(
                            f"{field_name} должен быть bool."
                        )

                    setattr(product, field_name, value)

            if "sort_order" in fields:
                sort_order = fields["sort_order"]

                if (
                    isinstance(sort_order, bool)
                    or not isinstance(sort_order, int)
                ):
                    raise ProductValidationError(
                        "sort_order должен быть целым числом."
                    )

                product.sort_order = sort_order

            if "extra_data" in fields:
                extra_data = fields["extra_data"]

                if extra_data is not None and not isinstance(
                    extra_data,
                    dict,
                ):
                    raise ProductValidationError(
                        "extra_data должен быть словарём."
                    )

                product.extra_data = extra_data

            await session.flush()

            logger.info(
                "Изменён товар: product_id={}, fields={}",
                product.id,
                sorted(fields.keys()),
            )

            return product

    async def update_product(
        self,
        session: AsyncSession,
        product_id: int,
        **fields: Any,
    ) -> Product:
        """Совместимый алиас обновления товара."""

        return await self.update(
            session,
            product_id,
            **fields,
        )

    # --------------------------------------------------------
    # Цена
    # --------------------------------------------------------

    async def set_price(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        price_usd: Decimal | int | float | str,
    ) -> Product:
        """Изменяет цену товара в USD."""

        return await self.update(
            session,
            product_id,
            price_usd=price_usd,
        )

    # --------------------------------------------------------
    # Остатки
    # --------------------------------------------------------

    async def set_stock(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        stock: int,
    ) -> Product:
        """Устанавливает точный остаток товара."""

        normalized_stock = _normalize_stock(stock)

        async with write_transaction(session):
            result = await session.execute(
                update(Product)
                .where(Product.id == product_id)
                .values(stock=normalized_stock)
            )

            if result.rowcount != 1:
                raise ProductNotFoundError(
                    f"Товар #{product_id} не найден."
                )

            product = await self.get_or_raise(
                session,
                product_id,
                include_photos=False,
            )

            logger.info(
                "Установлен остаток товара: "
                "product_id={}, stock={}",
                product_id,
                normalized_stock,
            )

            return product

    async def increase_stock(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        quantity: int,
    ) -> Product:
        """Увеличивает остаток товара."""

        quantity = _normalize_quantity(quantity)

        async with write_transaction(session):
            result = await session.execute(
                update(Product)
                .where(Product.id == product_id)
                .values(
                    stock=Product.stock + quantity
                )
            )

            if result.rowcount != 1:
                raise ProductNotFoundError(
                    f"Товар #{product_id} не найден."
                )

            product = await self.get_or_raise(
                session,
                product_id,
                include_photos=False,
            )

            logger.info(
                "Увеличен остаток: product_id={}, "
                "quantity={}, stock={}",
                product_id,
                quantity,
                product.stock,
            )

            return product

    async def decrease_stock(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        quantity: int,
    ) -> Product:
        """Уменьшает остаток с атомарной проверкой."""

        quantity = _normalize_quantity(quantity)

        async with write_transaction(session):
            result = await session.execute(
                update(Product)
                .where(
                    Product.id == product_id,
                    Product.stock >= quantity,
                )
                .values(
                    stock=Product.stock - quantity
                )
            )

            if result.rowcount != 1:
                exists_result = await session.execute(
                    select(Product.id).where(
                        Product.id == product_id
                    )
                )

                if exists_result.scalar_one_or_none() is None:
                    raise ProductNotFoundError(
                        f"Товар #{product_id} не найден."
                    )

                raise ProductStockError(
                    "Недостаточно товара на складе."
                )

            product = await self.get_or_raise(
                session,
                product_id,
                include_photos=False,
            )

            logger.info(
                "Уменьшен остаток: product_id={}, "
                "quantity={}, stock={}",
                product_id,
                quantity,
                product.stock,
            )

            return product

    # --------------------------------------------------------
    # Доступность покупки
    # --------------------------------------------------------

    async def is_available(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        quantity: int = 1,
    ) -> bool:
        """Проверяет, можно ли купить заданное количество."""

        quantity = _normalize_quantity(quantity)

        result = await session.execute(
            select(Product.id).where(
                Product.id == product_id,
                Product.is_active.is_(True),
                Product.is_hidden.is_(False),
                Product.allow_purchase.is_(True),
                Product.stock >= quantity,
            )
        )

        return result.scalar_one_or_none() is not None

    async def validate_purchase(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        quantity: int = 1,
    ) -> Product:
        """Проверяет доступность и возвращает товар."""

        quantity = _normalize_quantity(quantity)

        product = await self.get_or_raise(
            session,
            product_id,
            include_photos=True,
        )

        if (
            not product.is_active
            or product.is_hidden
            or not product.allow_purchase
        ):
            raise ProductUnavailableError(
                "Товар недоступен для покупки."
            )

        if product.stock < quantity:
            raise ProductStockError(
                f"Недостаточно товара на складе. "
                f"Доступно: {product.stock}."
            )

        return product

    # --------------------------------------------------------
    # Фотографии
    # --------------------------------------------------------

    async def add_photo(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        file_id: str,
        file_unique_id: str | None = None,
        sort_order: int = 0,
        is_cover: bool = False,
    ) -> ProductPhoto:
        """Добавляет фотографию по Telegram file_id."""

        file_id = _normalize_text(
            file_id,
            field_name="Telegram file_id",
            max_length=MAX_PHOTO_FILE_ID_LENGTH,
        )

        if file_unique_id is not None:
            file_unique_id = _normalize_text(
                file_unique_id,
                field_name="file_unique_id",
                max_length=MAX_PHOTO_FILE_ID_LENGTH,
            )

        if not isinstance(is_cover, bool):
            raise ProductValidationError(
                "is_cover должен быть bool."
            )

        async with write_transaction(session):
            product = await self.get_or_raise(
                session,
                product_id,
                include_photos=False,
            )

            if is_cover:
                # Снимаем признак обложки с остальных фото.
                await session.execute(
                    update(ProductPhoto)
                    .where(
                        ProductPhoto.product_id == product_id
                    )
                    .values(is_cover=False)
                )

            photo = ProductPhoto(
                product_id=product.id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                sort_order=sort_order,
                is_cover=is_cover,
            )

            session.add(photo)
            await session.flush()

            logger.info(
                "Добавлено фото товара: "
                "product_id={}, photo_id={}",
                product_id,
                photo.id,
            )

            return photo

    async def list_photos(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> list[ProductPhoto]:
        """Возвращает фотографии товара."""

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

        return list(result.scalars().all())

    async def set_cover_photo(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        photo_id: int,
    ) -> ProductPhoto:
        """Устанавливает выбранное фото обложкой товара."""

        async with write_transaction(session):
            product = await self.get_or_raise(
                session,
                product_id,
                include_photos=False,
            )

            result = await session.execute(
                select(ProductPhoto).where(
                    ProductPhoto.id == photo_id,
                    ProductPhoto.product_id == product.id,
                )
            )

            photo = result.scalar_one_or_none()

            if photo is None:
                raise ProductPhotoError(
                    "Фотография не найдена."
                )

            await session.execute(
                update(ProductPhoto)
                .where(
                    ProductPhoto.product_id == product_id
                )
                .values(is_cover=False)
            )

            photo.is_cover = True

            await session.flush()

            return photo

    async def delete_photo(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        photo_id: int,
    ) -> bool:
        """Удаляет фотографию товара."""

        async with write_transaction(session):
            result = await session.execute(
                select(ProductPhoto).where(
                    ProductPhoto.id == photo_id,
                    ProductPhoto.product_id == product_id,
                )
            )

            photo = result.scalar_one_or_none()

            if photo is None:
                return False

            await session.delete(photo)
            await session.flush()

            logger.info(
                "Удалено фото: product_id={}, photo_id={}",
                product_id,
                photo_id,
            )

            return True

    # --------------------------------------------------------
    # Скрытие и активация
    # --------------------------------------------------------

    async def set_active(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        is_active: bool,
    ) -> Product:
        """Включает или отключает товар."""

        if not isinstance(is_active, bool):
            raise ProductValidationError(
                "is_active должен быть bool."
            )

        return await self.update(
            session,
            product_id,
            is_active=is_active,
        )

    async def set_hidden(
        self,
        session: AsyncSession,
        *,
        product_id: int,
        is_hidden: bool,
    ) -> Product:
        """Скрывает товар или возвращает его в каталог."""

        if not isinstance(is_hidden, bool):
            raise ProductValidationError(
                "is_hidden должен быть bool."
            )

        return await self.update(
            session,
            product_id,
            is_hidden=is_hidden,
        )

    # --------------------------------------------------------
    # Удаление
    # --------------------------------------------------------

    async def delete(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> bool:
        """
        Удаляет товар.

        Если товар уже связан с заказами, ограничение внешнего
        ключа БД не позволит удалить историю покупки.
        """

        async with write_transaction(session):
            product = await self.get_by_id(
                session,
                product_id,
                include_photos=False,
            )

            if product is None:
                return False

            await session.delete(product)

            try:
                await session.flush()
            except IntegrityError as exc:
                logger.warning(
                    "Нельзя удалить товар #{}, "
                    "возможно, он участвует в заказах.",
                    product_id,
                )

                raise ProductServiceError(
                    "Товар нельзя удалить: он связан "
                    "с существующими данными. "
                    "Вместо удаления отключите его."
                ) from exc

            logger.info(
                "Удалён товар: product_id={}",
                product_id,
            )

            return True

    async def delete_product(
        self,
        session: AsyncSession,
        *,
        product_id: int,
    ) -> bool:
        """Совместимый алиас удаления товара."""

        return await self.delete(
            session,
            product_id=product_id,
        )


# ============================================================
# Глобальный экземпляр
# ============================================================

product_service = ProductService()


__all__ = [
    "ProductService",
    "ProductServiceError",
    "ProductNotFoundError",
    "ProductAlreadyExistsError",
    "ProductValidationError",
    "ProductUnavailableError",
    "ProductStockError",
    "ProductPhotoError",
    "ProductService",
    "product_service",
]