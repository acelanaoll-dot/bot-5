from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import CartItem, Product
from app.database.session import write_transaction


# ============================================================
# Константы
# ============================================================

MAX_CART_QUANTITY = 1_000


# ============================================================
# Исключения
# ============================================================

class CartServiceError(Exception):
    """Базовая ошибка сервиса корзины."""


class CartItemNotFoundError(CartServiceError):
    """Позиция корзины не найдена."""


class CartValidationError(CartServiceError):
    """Ошибка проверки данных корзины."""


class CartStockError(CartServiceError):
    """Недостаточно товара на складе."""


class CartProductUnavailableError(CartServiceError):
    """Товар недоступен для покупки."""


# ============================================================
# DTO
# ============================================================

@dataclass(slots=True)
class CartTotals:
    """Итоги корзины."""

    items_count: int
    products_count: int
    subtotal_usd: Decimal


# ============================================================
# Валидация
# ============================================================

def _validate_user_id(user_id: int) -> int:
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
    ):
        raise CartValidationError(
            "Некорректный ID пользователя."
        )

    return user_id


def _validate_product_id(product_id: int) -> int:
    if (
        isinstance(product_id, bool)
        or not isinstance(product_id, int)
        or product_id <= 0
    ):
        raise CartValidationError(
            "Некорректный ID товара."
        )

    return product_id


def _validate_quantity(
    quantity: int,
    *,
    allow_zero: bool = False,
) -> int:
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
    ):
        raise CartValidationError(
            "Количество должно быть целым числом."
        )

    minimum = 0 if allow_zero else 1

    if quantity < minimum:
        if allow_zero:
            raise CartValidationError(
                "Количество не может быть отрицательным."
            )

        raise CartValidationError(
            "Количество должно быть больше нуля."
        )

    if quantity > MAX_CART_QUANTITY:
        raise CartValidationError(
            f"Максимальное количество: {MAX_CART_QUANTITY}."
        )

    return quantity


# ============================================================
# Сервис
# ============================================================

class CartService:
    """Асинхронный сервис корзины."""

    # --------------------------------------------------------
    # Получение корзины
    # --------------------------------------------------------

    async def get_item(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
    ) -> CartItem | None:
        """Получает позицию корзины по пользователю и товару."""

        user_id = _validate_user_id(user_id)
        product_id = _validate_product_id(product_id)

        result = await session.execute(
            select(CartItem)
            .where(
                CartItem.user_id == user_id,
                CartItem.product_id == product_id,
            )
            .options(
                selectinload(CartItem.product)
            )
        )

        return result.scalar_one_or_none()

    async def get_item_by_id(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        cart_item_id: int,
    ) -> CartItem | None:
        """Получает позицию корзины по ID позиции."""

        user_id = _validate_user_id(user_id)

        if (
            isinstance(cart_item_id, bool)
            or not isinstance(cart_item_id, int)
            or cart_item_id <= 0
        ):
            raise CartValidationError(
                "Некорректный ID позиции корзины."
            )

        result = await session.execute(
            select(CartItem)
            .where(
                CartItem.id == cart_item_id,
                CartItem.user_id == user_id,
            )
            .options(
                selectinload(CartItem.product)
            )
        )

        return result.scalar_one_or_none()

    async def get_or_raise(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
    ) -> CartItem:
        """Получает позицию или выбрасывает исключение."""

        item = await self.get_item(
            session,
            user_id=user_id,
            product_id=product_id,
        )

        if item is None:
            raise CartItemNotFoundError(
                "Товар отсутствует в корзине."
            )

        return item

    async def get_items(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> list[CartItem]:
        """Возвращает все позиции корзины."""

        user_id = _validate_user_id(user_id)

        result = await session.execute(
            select(CartItem)
            .where(
                CartItem.user_id == user_id
            )
            .options(
                selectinload(CartItem.product)
            )
            .order_by(
                CartItem.id.asc()
            )
        )

        return list(
            result.scalars().unique().all()
        )

    async def get_cart(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> list[CartItem]:
        """Совместимый метод получения корзины."""

        return await self.get_items(
            session,
            user_id=user_id,
        )

    # --------------------------------------------------------
    # Итоги
    # --------------------------------------------------------

    async def get_totals(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> CartTotals:
        """Рассчитывает текущие итоги корзины."""

        items = await self.get_items(
            session,
            user_id=user_id,
        )

        items_count = 0
        products_count = len(items)
        subtotal = Decimal("0")

        for item in items:
            quantity = int(item.quantity)
            price = Decimal(
                str(item.product.price_usd)
            )

            items_count += quantity
            subtotal += price * quantity

        return CartTotals(
            items_count=items_count,
            products_count=products_count,
            subtotal_usd=subtotal,
        )

    async def calculate_totals(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> CartTotals:
        """Совместимый алиас."""

        return await self.get_totals(
            session,
            user_id=user_id,
        )

    # --------------------------------------------------------
    # Добавление
    # --------------------------------------------------------

    async def add_item(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
        quantity: int = 1,
    ) -> CartItem:
        """
        Добавляет товар в корзину.

        Если товар уже есть, количество увеличивается.
        Остаток проверяется до изменения корзины.
        """

        user_id = _validate_user_id(user_id)
        product_id = _validate_product_id(product_id)
        quantity = _validate_quantity(quantity)

        async with write_transaction(session):
            product_result = await session.execute(
                select(Product).where(Product.id == product_id)
            )
            product = product_result.scalar_one_or_none()

            if product is None:
                raise CartItemNotFoundError("Товар не найден.")

            if not product.is_active or product.is_hidden or not product.allow_purchase:
                raise CartProductUnavailableError("Товар недоступен для покупки.")

            item_result = await session.execute(
                select(CartItem)
                .where(
                    CartItem.user_id == user_id,
                    CartItem.product_id == product_id,
                )
            )
            item = item_result.scalar_one_or_none()

            current_quantity = item.quantity if item is not None else 0
            new_quantity = current_quantity + quantity

            if new_quantity > product.stock:
                raise CartStockError(
                    f"Недостаточно товара на складе. Доступно: {product.stock}, в корзине уже: {current_quantity}."
                )

            if new_quantity > MAX_CART_QUANTITY:
                raise CartValidationError(f"Максимальное количество: {MAX_CART_QUANTITY}.")

            if item is None:
                item = CartItem(
                    user_id=user_id,
                    product_id=product_id,
                    quantity=quantity,
                )
                session.add(item)
                try:
                    # Внутренний сейвпоинт на случай, если 2 запроса одновременно пытаются вставить позицию корзины
                    async with session.begin_nested():
                        await session.flush()
                except IntegrityError as exc:
                    raise CartServiceError(
                        "Не удалось добавить товар в корзину. Повторите действие."
                    ) from exc
            else:
                item.quantity = new_quantity
                await session.flush()

            logger.info(
                "Товар добавлен в корзину: user_id={}, product_id={}, quantity={}, total_quantity={}",
                user_id,
                product_id,
                quantity,
                item.quantity,
            )

            return item

    async def add(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
        quantity: int = 1,
    ) -> CartItem:
        """Короткий алиас."""

        return await self.add_item(
            session,
            user_id=user_id,
            product_id=product_id,
            quantity=quantity,
        )

    # --------------------------------------------------------
    # Установка количества
    # --------------------------------------------------------

    async def set_quantity(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
        quantity: int,
    ) -> CartItem | None:
        """
        Устанавливает точное количество.

        quantity=0 удаляет позицию из корзины.
        """

        user_id = _validate_user_id(user_id)
        product_id = _validate_product_id(product_id)
        quantity = _validate_quantity(
            quantity,
            allow_zero=True,
        )

        async with write_transaction(session):
            item_result = await session.execute(
                select(CartItem)
                .where(
                    CartItem.user_id == user_id,
                    CartItem.product_id == product_id,
                )
            )

            item = item_result.scalar_one_or_none()

            if item is None:
                if quantity == 0:
                    return None

                raise CartItemNotFoundError(
                    "Товар отсутствует в корзине."
                )

            if quantity == 0:
                await session.delete(item)
                await session.flush()

                return None

            product_result = await session.execute(
                select(Product).where(
                    Product.id == product_id
                )
            )

            product = product_result.scalar_one_or_none()

            if product is None:
                raise CartItemNotFoundError(
                    "Товар больше не существует."
                )

            if (
                not product.is_active
                or product.is_hidden
                or not product.allow_purchase
            ):
                raise CartProductUnavailableError(
                    "Товар недоступен для покупки."
                )

            if quantity > product.stock:
                raise CartStockError(
                    "Недостаточно товара на складе. "
                    f"Доступно: {product.stock}."
                )

            item.quantity = quantity

            await session.flush()

            return item

    async def update_quantity(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
        quantity: int,
    ) -> CartItem | None:
        """Совместимый алиас."""

        return await self.set_quantity(
            session,
            user_id=user_id,
            product_id=product_id,
            quantity=quantity,
        )

    async def change_quantity(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
        delta: int,
    ) -> CartItem | None:
        """Увеличивает или уменьшает количество."""

        if (
            isinstance(delta, bool)
            or not isinstance(delta, int)
        ):
            raise CartValidationError(
                "Изменение количества должно быть целым числом."
            )

        if delta == 0:
            return await self.get_item(
                session,
                user_id=user_id,
                product_id=product_id,
            )

        async with write_transaction(session):
            item_result = await session.execute(
                select(CartItem)
                .where(
                    CartItem.user_id == user_id,
                    CartItem.product_id == product_id,
                )
            )

            item = item_result.scalar_one_or_none()

            if item is None:
                raise CartItemNotFoundError(
                    "Товар отсутствует в корзине."
                )

            new_quantity = item.quantity + delta

            if new_quantity <= 0:
                await session.delete(item)
                await session.flush()
                return None

            if new_quantity > MAX_CART_QUANTITY:
                raise CartValidationError(
                    f"Максимальное количество: "
                    f"{MAX_CART_QUANTITY}."
                )

            product_result = await session.execute(
                select(Product).where(
                    Product.id == product_id
                )
            )

            product = product_result.scalar_one_or_none()

            if product is None:
                raise CartItemNotFoundError(
                    "Товар больше не существует."
                )

            if (
                not product.is_active
                or product.is_hidden
                or not product.allow_purchase
            ):
                raise CartProductUnavailableError(
                    "Товар недоступен для покупки."
                )

            if new_quantity > product.stock:
                raise CartStockError(
                    "Недостаточно товара на складе. "
                    f"Доступно: {product.stock}."
                )

            item.quantity = new_quantity

            await session.flush()

            return item

    # --------------------------------------------------------
    # Удаление
    # --------------------------------------------------------

    async def remove_item(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
    ) -> bool:
        """Удаляет товар из корзины."""

        user_id = _validate_user_id(user_id)
        product_id = _validate_product_id(product_id)

        async with write_transaction(session):
            result = await session.execute(
                delete(CartItem).where(
                    CartItem.user_id == user_id,
                    CartItem.product_id == product_id,
                )
            )

            removed = result.rowcount == 1

            if removed:
                logger.info(
                    "Товар удалён из корзины: "
                    "user_id={}, product_id={}",
                    user_id,
                    product_id,
                )

            return removed

    async def remove(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        product_id: int,
    ) -> bool:
        """Короткий алиас."""

        return await self.remove_item(
            session,
            user_id=user_id,
            product_id=product_id,
        )

    async def remove_item_by_id(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        cart_item_id: int,
    ) -> bool:
        """Удаляет позицию по ID."""

        user_id = _validate_user_id(user_id)

        if (
            isinstance(cart_item_id, bool)
            or not isinstance(cart_item_id, int)
            or cart_item_id <= 0
        ):
            raise CartValidationError(
                "Некорректный ID позиции корзины."
            )

        async with write_transaction(session):
            result = await session.execute(
                delete(CartItem).where(
                    CartItem.id == cart_item_id,
                    CartItem.user_id == user_id,
                )
            )

            return result.rowcount == 1

    # --------------------------------------------------------
    # Очистка
    # --------------------------------------------------------

    async def clear(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> int:
        """Полностью очищает корзину."""

        user_id = _validate_user_id(user_id)

        async with write_transaction(session):
            result = await session.execute(
                delete(CartItem).where(
                    CartItem.user_id == user_id
                )
            )

            count = int(result.rowcount or 0)

            logger.info(
                "Корзина очищена: user_id={}, items={}",
                user_id,
                count,
            )

            return count

    async def clear_cart(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> int:
        """Совместимый алиас."""

        return await self.clear(
            session,
            user_id=user_id,
        )

    # --------------------------------------------------------
    # Проверка перед оформлением
    # --------------------------------------------------------

    async def validate(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> list[CartItem]:
        """
        Проверяет всю корзину перед созданием заказа.

        Возвращает актуальные позиции.
        """

        items = await self.get_items(
            session,
            user_id=user_id,
        )

        if not items:
            raise CartServiceError(
                "Корзина пуста."
            )

        errors: list[str] = []

        for item in items:
            product = item.product

            if product is None:
                errors.append(
                    f"Позиция #{item.id}: товар удалён."
                )
                continue

            if not product.is_active:
                errors.append(
                    f"«{product.name}»: товар отключён."
                )
                continue

            if product.is_hidden:
                errors.append(
                    f"«{product.name}»: товар скрыт."
                )
                continue

            if not product.allow_purchase:
                errors.append(
                    f"«{product.name}»: покупка запрещена."
                )
                continue

            if product.stock < item.quantity:
                errors.append(
                    f"«{product.name}»: "
                    f"доступно {product.stock}, "
                    f"в корзине {item.quantity}."
                )

        if errors:
            raise CartStockError(
                "Корзина изменилась:\n"
                + "\n".join(
                    f"• {error}"
                    for error in errors
                )
            )

        return items

    async def has_items(
        self,
        session: AsyncSession,
        *,
        user_id: int,
    ) -> bool:
        """Проверяет, есть ли товары в корзине."""

        user_id = _validate_user_id(user_id)

        result = await session.execute(
            select(CartItem.id)
            .where(
                CartItem.user_id == user_id
            )
            .limit(1)
        )

        return result.scalar_one_or_none() is not None


# ============================================================
# Глобальный экземпляр
# ============================================================

cart_service = CartService()


__all__ = [
    "CartService",
    "CartServiceError",
    "CartItemNotFoundError",
    "CartValidationError",
    "CartStockError",
    "CartProductUnavailableError",
    "CartTotals",
    "cart_service",
]