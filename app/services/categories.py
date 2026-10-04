from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Category
from app.database.session import write_transaction


# ============================================================
# Константы
# ============================================================

MAX_CATEGORY_NAME_LENGTH = 255
MAX_CATEGORY_SLUG_LENGTH = 255
MAX_DESCRIPTION_LENGTH = 10_000
MAX_PAGE_SIZE = 100


# ============================================================
# Исключения
# ============================================================

class CategoryServiceError(Exception):
    """Базовая ошибка сервиса категорий."""


class CategoryNotFoundError(CategoryServiceError):
    """Категория не найдена."""


class CategoryAlreadyExistsError(CategoryServiceError):
    """Категория с таким slug уже существует."""


class CategoryValidationError(CategoryServiceError):
    """Ошибка проверки данных категории."""


class CategoryHierarchyError(CategoryServiceError):
    """Ошибка структуры дерева категорий."""


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
    """Проверяет и нормализует текст."""

    if not isinstance(value, str):
        raise CategoryValidationError(
            f"Поле «{field_name}» должно быть строкой."
        )

    value = value.strip()

    if not value and not allow_empty:
        raise CategoryValidationError(
            f"Поле «{field_name}» не может быть пустым."
        )

    if len(value) > max_length:
        raise CategoryValidationError(
            f"Поле «{field_name}» слишком длинное. "
            f"Максимум: {max_length} символов."
        )

    return value


def _normalize_slug(value: str) -> str:
    """Нормализует slug категории."""

    value = _normalize_text(
        value,
        field_name="slug",
        max_length=MAX_CATEGORY_SLUG_LENGTH,
    )

    value = value.lower()
    value = value.replace(" ", "-")

    result: list[str] = []

    for char in value:
        if char.isalnum() or char in {"-", "_"}:
            result.append(char)

    slug = "".join(result)

    while "--" in slug:
        slug = slug.replace("--", "-")

    slug = slug.strip("-_")

    if not slug:
        raise CategoryValidationError(
            "Не удалось сформировать slug категории."
        )

    return slug


def _make_slug(name: str) -> str:
    """Создаёт slug из названия."""

    return _normalize_slug(name)


def _normalize_sort_order(value: int) -> int:
    """Проверяет порядок сортировки."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise CategoryValidationError(
            "sort_order должен быть целым числом."
        )

    return value


# ============================================================
# Сервис
# ============================================================

class CategoryService:
    """Асинхронный сервис управления категориями."""

    # --------------------------------------------------------
    # Получение категории
    # --------------------------------------------------------

    async def get_by_id(
        self,
        session: AsyncSession,
        category_id: int,
        *,
        include_children: bool = False,
    ) -> Category | None:
        """Получает категорию по ID."""

        if category_id <= 0:
            return None

        query = select(Category).where(
            Category.id == category_id
        )

        if include_children:
            query = query.options(
                selectinload(Category.children)
            )

        result = await session.execute(query)

        return result.scalar_one_or_none()

    async def get_category(
        self,
        session: AsyncSession,
        category_id: int,
    ) -> Category | None:
        """Совместимый алиас."""

        return await self.get_by_id(
            session,
            category_id,
        )

    async def get_or_raise(
        self,
        session: AsyncSession,
        category_id: int,
    ) -> Category:
        """Получает категорию или выбрасывает исключение."""

        category = await self.get_by_id(
            session,
            category_id,
        )

        if category is None:
            raise CategoryNotFoundError(
                f"Категория #{category_id} не найдена."
            )

        return category

    async def get_by_slug(
        self,
        session: AsyncSession,
        slug: str,
    ) -> Category | None:
        """Получает категорию по slug."""

        slug = _normalize_slug(slug)

        result = await session.execute(
            select(Category).where(
                Category.slug == slug
            )
        )

        return result.scalar_one_or_none()

    async def list_categories(
        self,
        session: AsyncSession,
        *,
        active_only: bool = True,
        include_hidden: bool = False,
        limit: int = MAX_PAGE_SIZE,
        offset: int = 0,
    ) -> list[Category]:
        """Возвращает категории общим списком."""

        if isinstance(limit, bool) or not isinstance(limit, int):
            raise CategoryValidationError(
                "limit должен быть целым числом."
            )

        if isinstance(offset, bool) or not isinstance(offset, int):
            raise CategoryValidationError(
                "offset должен быть целым числом."
            )

        limit = max(
            1,
            min(limit, MAX_PAGE_SIZE),
        )
        offset = max(
            0,
            offset,
        )

        query = select(Category)

        if active_only:
            query = query.where(
                Category.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Category.is_hidden.is_(False)
            )

        query = (
            query
            .order_by(
                Category.sort_order.asc(),
                Category.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )

        result = await session.execute(query)

        return list(result.scalars().all())

    # --------------------------------------------------------
    # Дерево категорий
    # --------------------------------------------------------

    async def list_root_categories(
        self,
        session: AsyncSession,
        *,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[Category]:
        """Возвращает корневые категории."""

        query = select(Category).where(
            Category.parent_id.is_(None)
        )

        if active_only:
            query = query.where(
                Category.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Category.is_hidden.is_(False)
            )

        query = query.order_by(
            Category.sort_order.asc(),
            Category.id.asc(),
        )

        result = await session.execute(query)

        return list(result.scalars().all())

    async def get_root_categories(
        self,
        session: AsyncSession,
        *,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[Category]:
        """Совместимый алиас корневых категорий."""

        return await self.list_root_categories(
            session,
            active_only=active_only,
            include_hidden=include_hidden,
        )

    async def list_children(
        self,
        session: AsyncSession,
        *,
        parent_id: int,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[Category]:
        """Возвращает дочерние категории."""

        if parent_id <= 0:
            return []

        query = select(Category).where(
            Category.parent_id == parent_id
        )

        if active_only:
            query = query.where(
                Category.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Category.is_hidden.is_(False)
            )

        query = query.order_by(
            Category.sort_order.asc(),
            Category.id.asc(),
        )

        result = await session.execute(query)

        return list(result.scalars().all())

    async def get_children(
        self,
        session: AsyncSession,
        parent_id: int,
        *,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[Category]:
        """Совместимый алиас дочерних категорий."""

        return await self.list_children(
            session,
            parent_id=parent_id,
            active_only=active_only,
            include_hidden=include_hidden,
        )

    async def get_tree(
        self,
        session: AsyncSession,
        *,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> list[Category]:
        """
        Загружает категории и возвращает
        плоский список в порядке дерева.
        """

        query = select(Category)

        if active_only:
            query = query.where(
                Category.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Category.is_hidden.is_(False)
            )

        query = query.order_by(
            Category.sort_order.asc(),
            Category.id.asc(),
        )

        result = await session.execute(query)

        categories = list(result.scalars().all())

        by_parent: dict[int | None, list[Category]] = {}

        for category in categories:
            by_parent.setdefault(
                category.parent_id,
                [],
            ).append(category)

        output: list[Category] = []

        def walk(
            parent_id: int | None,
        ) -> None:
            for category in by_parent.get(
                parent_id,
                [],
            ):
                output.append(category)
                walk(category.id)

        walk(None)

        return output

    # --------------------------------------------------------
    # Подсчёты
    # --------------------------------------------------------

    async def count(
        self,
        session: AsyncSession,
        *,
        parent_id: int | None = None,
        active_only: bool = True,
        include_hidden: bool = False,
    ) -> int:
        """Считает категории."""

        query = select(
            func.count(Category.id)
        )

        if parent_id is None:
            query = query.where(
                Category.parent_id.is_(None)
            )
        else:
            query = query.where(
                Category.parent_id == parent_id
            )

        if active_only:
            query = query.where(
                Category.is_active.is_(True)
            )

        if not include_hidden:
            query = query.where(
                Category.is_hidden.is_(False)
            )

        result = await session.execute(query)

        return int(result.scalar_one())

    # --------------------------------------------------------
    # Создание
    # --------------------------------------------------------

    async def create(
        self,
        session: AsyncSession,
        *,
        name: str,
        parent_id: int | None = None,
        slug: str | None = None,
        description: str | None = None,
        sort_order: int = 0,
        is_active: bool = True,
        is_hidden: bool = False,
        extra_data: dict[str, Any] | None = None,
    ) -> Category:
        """Создаёт категорию."""

        name = _normalize_text(
            name,
            field_name="название",
            max_length=MAX_CATEGORY_NAME_LENGTH,
        )

        slug = _normalize_slug(
            slug or _make_slug(name)
        )

        if description is not None:
            description = _normalize_text(
                description,
                field_name="описание",
                max_length=MAX_DESCRIPTION_LENGTH,
                allow_empty=True,
            )

        sort_order = _normalize_sort_order(
            sort_order
        )

        if not isinstance(is_active, bool):
            raise CategoryValidationError(
                "is_active должен быть bool."
            )

        if not isinstance(is_hidden, bool):
            raise CategoryValidationError(
                "is_hidden должен быть bool."
            )

        if extra_data is not None and not isinstance(
            extra_data,
            dict,
        ):
            raise CategoryValidationError(
                "extra_data должен быть словарём."
            )

        if parent_id is not None:
            if (
                isinstance(parent_id, bool)
                or not isinstance(parent_id, int)
                or parent_id <= 0
            ):
                raise CategoryValidationError(
                    "Некорректный parent_id."
                )

        async with write_transaction(session):
            if parent_id is not None:
                parent_result = await session.execute(
                    select(Category).where(
                        Category.id == parent_id
                    )
                )

                parent = parent_result.scalar_one_or_none()

                if parent is None:
                    raise CategoryNotFoundError(
                        "Родительская категория не найдена."
                    )

            duplicate_result = await session.execute(
                select(Category.id).where(
                    Category.slug == slug
                )
            )

            if duplicate_result.scalar_one_or_none() is not None:
                raise CategoryAlreadyExistsError(
                    "Категория с таким slug уже существует."
                )

            category = Category(
                name=name,
                slug=slug,
                description=description or None,
                parent_id=parent_id,
                sort_order=sort_order,
                is_active=is_active,
                is_hidden=is_hidden,
                extra_data=extra_data,
            )

            session.add(category)

            try:
                await session.flush()
            except IntegrityError as exc:
                raise CategoryAlreadyExistsError(
                    "Не удалось создать категорию: "
                    "возможно, такой slug уже используется."
                ) from exc

            logger.info(
                "Создана категория: id={}, name={!r}, parent_id={}",
                category.id,
                category.name,
                category.parent_id,
            )

            return category

    async def create_category(
        self,
        session: AsyncSession,
        **kwargs: Any,
    ) -> Category:
        """Совместимый алиас."""

        return await self.create(
            session,
            **kwargs,
        )

    # --------------------------------------------------------
    # Изменение
    # --------------------------------------------------------

    async def update(
        self,
        session: AsyncSession,
        category_id: int,
        **fields: Any,
    ) -> Category:
        """Обновляет категорию."""

        allowed_fields = {
            "name",
            "slug",
            "description",
            "parent_id",
            "sort_order",
            "is_active",
            "is_hidden",
            "extra_data",
        }

        unknown = set(fields) - allowed_fields

        if unknown:
            raise CategoryValidationError(
                "Неизвестные поля: "
                + ", ".join(sorted(unknown))
            )

        async with write_transaction(session):
            category = await self.get_or_raise(
                session,
                category_id,
            )

            if "name" in fields:
                category.name = _normalize_text(
                    fields["name"],
                    field_name="название",
                    max_length=MAX_CATEGORY_NAME_LENGTH,
                )

            if "slug" in fields:
                new_slug = _normalize_slug(
                    fields["slug"]
                )

                duplicate_result = await session.execute(
                    select(Category.id).where(
                        Category.slug == new_slug,
                        Category.id != category_id,
                    )
                )

                if (
                    duplicate_result.scalar_one_or_none()
                    is not None
                ):
                    raise CategoryAlreadyExistsError(
                        "Этот slug уже используется."
                    )

                category.slug = new_slug

            if "description" in fields:
                description = fields["description"]

                if description is not None:
                    description = _normalize_text(
                        description,
                        field_name="описание",
                        max_length=MAX_DESCRIPTION_LENGTH,
                        allow_empty=True,
                    )

                category.description = (
                    description or None
                )

            if "parent_id" in fields:
                parent_id = fields["parent_id"]

                if parent_id == category_id:
                    raise CategoryHierarchyError(
                        "Категория не может быть родителем самой себя."
                    )

                if parent_id is not None:
                    if (
                        isinstance(parent_id, bool)
                        or not isinstance(parent_id, int)
                        or parent_id <= 0
                    ):
                        raise CategoryValidationError(
                            "Некорректный parent_id."
                        )

                    parent = await self.get_by_id(
                        session,
                        parent_id,
                    )

                    if parent is None:
                        raise CategoryNotFoundError(
                            "Родительская категория не найдена."
                        )

                    current_id: int | None = parent.id
                    visited: set[int] = set()

                    while current_id is not None:
                        if current_id in visited:
                            break

                        visited.add(current_id)

                        if current_id == category_id:
                            raise CategoryHierarchyError(
                                "Нельзя переместить категорию "
                                "в собственного потомка."
                            )

                        current = await self.get_by_id(
                            session,
                            current_id,
                        )

                        if current is None:
                            break

                        current_id = current.parent_id

                category.parent_id = parent_id

            if "sort_order" in fields:
                category.sort_order = _normalize_sort_order(
                    fields["sort_order"]
                )

            if "is_active" in fields:
                value = fields["is_active"]

                if not isinstance(value, bool):
                    raise CategoryValidationError(
                        "is_active должен быть bool."
                    )

                category.is_active = value

            if "is_hidden" in fields:
                value = fields["is_hidden"]

                if not isinstance(value, bool):
                    raise CategoryValidationError(
                        "is_hidden должен быть bool."
                    )

                category.is_hidden = value

            if "extra_data" in fields:
                extra_data = fields["extra_data"]

                if extra_data is not None and not isinstance(
                    extra_data,
                    dict,
                ):
                    raise CategoryValidationError(
                        "extra_data должен быть словарём."
                    )

                category.extra_data = extra_data

            await session.flush()

            logger.info(
                "Изменена категория: id={}, fields={}",
                category.id,
                sorted(fields.keys()),
            )

            return category

    async def update_category(
        self,
        session: AsyncSession,
        category_id: int,
        **fields: Any,
    ) -> Category:
        """Совместимый алиас."""

        return await self.update(
            session,
            category_id,
            **fields,
        )

    # --------------------------------------------------------
    # Порядок
    # --------------------------------------------------------

    async def set_sort_order(
        self,
        session: AsyncSession,
        *,
        category_id: int,
        sort_order: int,
    ) -> Category:
        """Меняет порядок категории."""

        return await self.update(
            session,
            category_id,
            sort_order=sort_order,
        )

    async def move(
        self,
        session: AsyncSession,
        *,
        category_id: int,
        parent_id: int | None,
    ) -> Category:
        """Перемещает категорию в другую ветку."""

        return await self.update(
            session,
            category_id,
            parent_id=parent_id,
        )

    # --------------------------------------------------------
    # Активность / скрытие
    # --------------------------------------------------------

    async def set_active(
        self,
        session: AsyncSession,
        *,
        category_id: int,
        is_active: bool,
    ) -> Category:
        """Включает или отключает категорию."""

        if not isinstance(is_active, bool):
            raise CategoryValidationError(
                "is_active должен быть bool."
            )

        return await self.update(
            session,
            category_id,
            is_active=is_active,
        )

    async def set_hidden(
        self,
        session: AsyncSession,
        *,
        category_id: int,
        is_hidden: bool,
    ) -> Category:
        """Скрывает или показывает категорию."""

        if not isinstance(is_hidden, bool):
            raise CategoryValidationError(
                "is_hidden должен быть bool."
            )

        return await self.update(
            session,
            category_id,
            is_hidden=is_hidden,
        )

    # --------------------------------------------------------
    # Удаление
    # --------------------------------------------------------

    async def delete(
        self,
        session: AsyncSession,
        *,
        category_id: int,
    ) -> bool:
        """
        Удаляет категорию.

        Перед удалением проверяем наличие дочерних категорий
        и товаров.
        """

        async with write_transaction(session):
            category = await self.get_by_id(
                session,
                category_id,
            )

            if category is None:
                return False

            children_result = await session.execute(
                select(func.count(Category.id)).where(
                    Category.parent_id == category_id
                )
            )

            children_count = int(
                children_result.scalar_one()
            )

            if children_count > 0:
                raise CategoryHierarchyError(
                    "Нельзя удалить категорию, "
                    "у которой есть дочерние категории."
                )

            from app.database.models import Product

            products_result = await session.execute(
                select(func.count(Product.id)).where(
                    Product.category_id == category_id
                )
            )

            products_count = int(
                products_result.scalar_one()
            )

            if products_count > 0:
                raise CategoryServiceError(
                    "Нельзя удалить категорию, "
                    "в которой есть товары. "
                    "Сначала переместите или удалите товары."
                )

            await session.delete(category)
            await session.flush()

            logger.info(
                "Удалена категория: id={}",
                category_id,
            )

            return True

    async def delete_category(
        self,
        session: AsyncSession,
        *,
        category_id: int,
    ) -> bool:
        """Совместимый алиас."""

        return await self.delete(
            session,
            category_id=category_id,
        )


# ============================================================
# Глобальный экземпляр
# ============================================================

category_service = CategoryService()


__all__ = [
    "CategoryService",
    "CategoryServiceError",
    "CategoryNotFoundError",
    "CategoryAlreadyExistsError",
    "CategoryValidationError",
    "CategoryHierarchyError",
    "category_service",
]