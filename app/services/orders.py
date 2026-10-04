# app/services/orders.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from loguru import logger
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    CartItem,
    Order,
    OrderItem,
    OrderStatus,
    Product,
)
from app.services.balance import (
    InsufficientBalanceError,
    BalanceError,
    balance_service,
)


# ============================================================
# Константы
# ============================================================

MONEY_QUANT = Decimal("0.00000001")
ZERO = Decimal("0")


# ============================================================
# Исключения
# ============================================================


class OrderServiceError(Exception):
    """Базовая ошибка сервиса заказов."""


class OrderNotFoundError(OrderServiceError):
    """Заказ не найден."""


class CartEmptyError(OrderServiceError):
    """Корзина пользователя пуста."""


class ProductNotFoundError(OrderServiceError):
    """Товар не найден."""


class ProductInactiveError(OrderServiceError):
    """Товар недоступен для покупки."""


class InsufficientStockError(OrderServiceError):
    """Недостаточно товара на складе."""


class InvalidOrderAmountError(OrderServiceError):
    """Некорректная сумма заказа."""


class OrderAlreadyPaidError(OrderServiceError):
    """Заказ уже полностью оплачен."""


class OrderCancelledError(OrderServiceError):
    """Заказ отменён и не может быть изменён."""


class OrderExpiredError(OrderServiceError):
    """Заказ просрочен."""


class InvalidOrderStateError(OrderServiceError):
    """Недопустимое состояние заказа."""


class PaymentAmountExceededError(OrderServiceError):
    """Попытка зачислить больше остатка заказа."""


# ============================================================
# Результаты
# ============================================================


@dataclass(slots=True)
class OrderTotals:
    """Расчёт итоговой суммы заказа."""

    subtotal_usd: Decimal
    discount_usd: Decimal
    total_usd: Decimal


@dataclass(slots=True)
class PaymentApplicationResult:
    """Результат применения платежа к заказу."""

    order_id: int
    payment_amount_usd: Decimal
    applied_amount_usd: Decimal
    remaining_amount_usd: Decimal
    is_fully_paid: bool
    already_applied: bool = False


@dataclass(slots=True)
class OrderCreateResult:
    """Результат создания заказа."""

    order: Order
    totals: OrderTotals


# ============================================================
# Вспомогательные функции
# ============================================================


def _utcnow() -> datetime:
    """Возвращает текущее время UTC."""
    return datetime.now(timezone.utc)


def _money(value: Decimal | int | str) -> Decimal:
    """Безопасно приводит значение к денежному Decimal."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidOrderAmountError("Некорректное денежное значение.") from exc

    if not result.is_finite():
        raise InvalidOrderAmountError("Денежное значение должно быть конечным.")

    return result.quantize(MONEY_QUANT)


def _positive_money(value: Decimal | int | str) -> Decimal:
    """Проверяет положительную денежную сумму."""
    result = _money(value)

    if result <= ZERO:
        raise InvalidOrderAmountError("Сумма должна быть больше нуля.")

    return result


def _order_number() -> str:
    """Генерирует человекочитаемый номер заказа: ORD-YYYYMMDD-XXXXXXXX"""
    now = _utcnow()
    return f"ORD-{now:%Y%m%d}-{uuid4().hex[:8].upper()}"


def _remaining(order: Order) -> Decimal:
    """Возвращает остаток суммы заказа."""
    total = _money(order.total_usd)
    paid = _money(order.balance_paid_usd) + _money(order.crypto_paid_usd)
    remaining = total - paid

    if remaining < ZERO:
        return ZERO

    return remaining


# ============================================================
# OrderService
# ============================================================


class OrderService:
    """
    Сервис жизненного цикла заказа.

    Важные правила:
    1. Создание заказа выполняется атомарно (с блокировкой товаров).
    2. Повторный платёж с тем же idempotency key игнорируется.
    3. Транзакции контролируются вызывающим кодом (middleware/handler).
    """

    # ========================================================
    # Расчёт
    # ========================================================

    @staticmethod
    def calculate_totals(
        items: list[CartItem],
        *,
        discount_usd: Decimal = ZERO,
    ) -> OrderTotals:
        """Рассчитывает subtotal / discount / total."""
        if not items:
            raise CartEmptyError("Корзина пуста.")

        subtotal = ZERO

        for cart_item in items:
            product = cart_item.product
            if product is None:
                raise ProductNotFoundError(f"Товар #{cart_item.product_id} не найден.")

            quantity = int(cart_item.quantity)
            if quantity <= 0:
                raise InvalidOrderAmountError(f"Некорректное количество товара #{product.id}.")

            price = _money(product.price_usd)
            if price < ZERO:
                raise InvalidOrderAmountError(f"Некорректная цена товара #{product.id}.")

            subtotal += price * quantity

        discount = _money(discount_usd)
        if discount < ZERO:
            raise InvalidOrderAmountError("Скидка не может быть отрицательной.")

        if discount > subtotal:
            discount = subtotal

        total = subtotal - discount

        return OrderTotals(
            subtotal_usd=_money(subtotal),
            discount_usd=_money(discount),
            total_usd=_money(total),
        )

    # ========================================================
    # Создание заказа
    # ========================================================

    async def create_from_cart(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        discount_usd: Decimal = ZERO,
        promo_code_id: int | None = None,
        notes: str | None = None,
    ) -> OrderCreateResult:
        """Создаёт заказ из корзины пользователя с блокировкой остатков."""
        if user_id <= 0:
            raise OrderServiceError("Некорректный user_id.")

        result = await session.execute(
            select(CartItem)
            .where(CartItem.user_id == user_id)
            .options(selectinload(CartItem.product))
            .order_by(CartItem.id.asc())
        )
        cart_items = list(result.scalars().all())

        if not cart_items:
            raise CartEmptyError("Корзина пользователя пуста.")

        product_ids = [item.product_id for item in cart_items]

        # Блокируем товары от конкурентных покупок
        await session.execute(
            select(Product.id)
            .where(Product.id.in_(product_ids))
            .with_for_update()
            .order_by(Product.id.asc())
        )

        locked_items: list[tuple[CartItem, Product]] = []

        for cart_item in cart_items:
            product = cart_item.product

            if product is None:
                raise ProductNotFoundError(f"Товар #{cart_item.product_id} больше не существует.")

            if not product.is_active or product.is_hidden or not product.allow_purchase:
                raise ProductInactiveError(f"Товар «{product.name}» недоступен для покупки.")

            quantity = int(cart_item.quantity)
            if quantity <= 0:
                raise InvalidOrderAmountError(f"Некорректное количество товара #{product.id}.")

            if product.stock < quantity:
                raise InsufficientStockError(
                    f"Недостаточно товара «{product.name}». Доступно: {product.stock}, нужно: {quantity}."
                )

            locked_items.append((cart_item, product))

        totals = self.calculate_totals(cart_items, discount_usd=discount_usd)

        order = Order(
            order_number=_order_number(),
            user_id=user_id,
            status=OrderStatus.PAYMENT_PENDING,
            subtotal_usd=totals.subtotal_usd,
            discount_usd=totals.discount_usd,
            total_usd=totals.total_usd,
            balance_paid_usd=ZERO,
            crypto_paid_usd=ZERO,
            promo_code_id=promo_code_id,
            payment_idempotency_key=None,
            notes=notes.strip() if notes and notes.strip() else None,
            delivery_data=None,
        )

        session.add(order)
        await session.flush()

        for cart_item, product in locked_items:
            quantity = int(cart_item.quantity)
            product.stock -= quantity

            unit_price = _money(product.price_usd)
            item_total = _money(unit_price * quantity)

            order_item = OrderItem(
                order_id=order.id,
                product_id=product.id,
                product_name=product.name,
                quantity=quantity,
                unit_price_usd=unit_price,
                total_price_usd=item_total,
                delivery_data=None,
            )
            session.add(order_item)

        await session.execute(delete(CartItem).where(CartItem.user_id == user_id))
        await session.flush()

        logger.info(
            "Создан заказ: order_id={}, order_number={}, user_id={}, total_usd={}",
            order.id,
            order.order_number,
            user_id,
            totals.total_usd,
        )

        return OrderCreateResult(order=order, totals=totals)

    # ========================================================
    # Получение заказа
    # ========================================================

    async def get(
        self,
        session: AsyncSession,
        order_id: int,
        *,
        with_items: bool = True,
        with_payments: bool = True,
    ) -> Order | None:
        """Получает заказ по ID."""
        if order_id <= 0:
            return None

        query = select(Order).where(Order.id == order_id)
        options: list[Any] = []

        if with_items:
            options.append(selectinload(Order.items))
        if with_payments:
            options.append(selectinload(Order.payments))

        if options:
            query = query.options(*options)

        result = await session.execute(query)
        return result.scalar_one_or_none()

    async def get_or_raise(
        self,
        session: AsyncSession,
        order_id: int,
        *,
        with_items: bool = True,
        with_payments: bool = True,
    ) -> Order:
        """Получает заказ или выбрасывает OrderNotFoundError."""
        order = await self.get(
            session, order_id, with_items=with_items, with_payments=with_payments
        )
        if order is None:
            raise OrderNotFoundError(f"Заказ #{order_id} не найден.")
        return order

    async def get_user_order(
        self,
        session: AsyncSession,
        *,
        order_id: int,
        user_id: int,
    ) -> Order | None:
        """Получает заказ только принадлежащий пользователю."""
        if order_id <= 0 or user_id <= 0:
            return None

        result = await session.execute(
            select(Order)
            .where(Order.id == order_id, Order.user_id == user_id)
            .options(selectinload(Order.items), selectinload(Order.payments))
        )
        return result.scalar_one_or_none()

    # ========================================================
    # Суммы
    # ========================================================

    async def get_remaining_amount(
        self,
        session: AsyncSession,
        *,
        order_id: int,
    ) -> Decimal:
        """Возвращает сумму, которую ещё нужно оплатить."""
        order = await self.get_or_raise(
            session, order_id, with_items=False, with_payments=False
        )
        return _remaining(order)

    async def get_paid_amount(
        self,
        session: AsyncSession,
        *,
        order_id: int,
    ) -> Decimal:
        """Возвращает уже оплаченную сумму."""
        order = await self.get_or_raise(
            session, order_id, with_items=False, with_payments=False
        )
        return _money(order.balance_paid_usd + order.crypto_paid_usd)

    # ========================================================
    # Оплата балансом
    # ========================================================

    async def add_balance_payment(
        self,
        session: AsyncSession,
        *,
        order_id: int,
        amount_usd: Decimal,
    ) -> PaymentApplicationResult:
        """
        Фиксирует оплату внутреннего баланса в заказе.
        """
        amount = _positive_money(amount_usd)

        result = await session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        order = result.scalar_one_or_none()

        if not order:
            raise OrderNotFoundError(f"Заказ #{order_id} не найден.")

        if order.status == OrderStatus.CANCELLED:
            raise OrderCancelledError("Заказ отменён.")
        if order.status == OrderStatus.EXPIRED:
            raise OrderExpiredError("Срок оплаты заказа истёк.")
        if order.status == OrderStatus.REFUNDED:
            raise InvalidOrderStateError("Заказ уже возвращён.")
        if order.status == OrderStatus.DELIVERED:
            raise OrderAlreadyPaidError("Заказ уже выдан.")

        remaining = _remaining(order)

        if remaining <= ZERO:
            raise OrderAlreadyPaidError("Заказ уже полностью оплачен.")

        if amount > remaining:
            raise PaymentAmountExceededError(
                f"Остаток заказа: {remaining}, попытка списать: {amount}."
            )

        order.balance_paid_usd = _money(order.balance_paid_usd + amount)
        remaining_after = _remaining(order)

        if remaining_after <= ZERO:
            order.status = OrderStatus.PAID
            order.paid_at = _utcnow()

        await session.flush()

        return PaymentApplicationResult(
            order_id=order.id,
            payment_amount_usd=amount,
            applied_amount_usd=amount,
            remaining_amount_usd=remaining_after,
            is_fully_paid=(remaining_after <= ZERO),
            already_applied=False,
        )

    # ========================================================
    # Криптооплата
    # ========================================================

    async def add_crypto_payment(
        self,
        session: AsyncSession,
        *,
        order_id: int,
        amount_usd: Decimal,
        idempotency_key: str,
    ) -> PaymentApplicationResult:
        """
        Фиксирует успешную криптооплату заказа.
        """
        amount = _positive_money(amount_usd)

        if not idempotency_key:
            raise OrderServiceError("idempotency_key обязателен.")

        result = await session.execute(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        order = result.scalar_one_or_none()

        if not order:
            raise OrderNotFoundError(f"Заказ #{order_id} не найден.")

        if order.status == OrderStatus.CANCELLED:
            raise OrderCancelledError("Заказ отменён.")
        if order.status == OrderStatus.EXPIRED:
            raise OrderExpiredError("Срок оплаты заказа истёк.")
        if order.status == OrderStatus.REFUNDED:
            raise InvalidOrderStateError("Заказ возвращён.")
        if order.status == OrderStatus.DELIVERED:
            raise OrderAlreadyPaidError("Заказ уже выдан.")

        remaining = _remaining(order)

        # Парсим CSV ключей из колонки payment_idempotency_key
        existing_keys = order.payment_idempotency_key.split(",") if order.payment_idempotency_key else []
        
        if idempotency_key in existing_keys:
            return PaymentApplicationResult(
                order_id=order.id,
                payment_amount_usd=amount,
                applied_amount_usd=ZERO,
                remaining_amount_usd=remaining,
                is_fully_paid=(remaining <= ZERO),
                already_applied=True,
            )

        if remaining <= ZERO:
            raise OrderAlreadyPaidError("Заказ уже полностью оплачен.")

        if amount > remaining:
            raise PaymentAmountExceededError(
                f"Платёж {amount} превышает остаток заказа {remaining}."
            )

        # Сохраняем ключи, ограничивая длину строки 255 символами
        existing_keys.append(idempotency_key)
        order.payment_idempotency_key = ",".join(existing_keys)[-255:]
        order.crypto_paid_usd = _money(order.crypto_paid_usd + amount)

        remaining_after = _remaining(order)

        if remaining_after <= ZERO:
            order.status = OrderStatus.PAID
            order.paid_at = _utcnow()

        await session.flush()

        return PaymentApplicationResult(
            order_id=order.id,
            payment_amount_usd=amount,
            applied_amount_usd=amount,
            remaining_amount_usd=remaining_after,
            is_fully_paid=(remaining_after <= ZERO),
            already_applied=False,
        )

    # ========================================================
    # Отмена и Просрочка
    # ========================================================

    async def cancel(
        self,
        session: AsyncSession,
        *,
        order_id: int,
        reason: str | None = None,
    ) -> Order:
        """Отменяет заказ с возвратом товаров на склад."""
        order = await self.get_or_raise(
            session, order_id, with_items=True, with_payments=False
        )

        if order.status in {OrderStatus.PAID, OrderStatus.DELIVERED, OrderStatus.REFUNDED}:
            raise InvalidOrderStateError("Оплаченный, выданный или возвращенный заказ нельзя отменить. Используйте возврат.")

        if order.status == OrderStatus.CANCELLED:
            return order

        if order.status == OrderStatus.PAYMENT_PENDING:
            for item in order.items:
                if item.product_id is not None:
                    await session.execute(
                        update(Product)
                        .where(Product.id == item.product_id)
                        .values(stock=Product.stock + item.quantity)
                    )

        order.status = OrderStatus.CANCELLED
        order.cancelled_at = _utcnow()

        if reason and reason.strip():
            order.notes = (f"{order.notes}\n" if order.notes else "") + f"Отмена: {reason.strip()}"

        await session.flush()
        logger.info("Заказ отменён, остатки восстановлены: order_id={}, reason={}", order.id, reason)
        return order

    async def expire(
        self,
        session: AsyncSession,
        *,
        order_id: int,
    ) -> Order:
        """Переводит неоплаченный заказ в EXPIRED и возвращает товары."""
        order = await self.get_or_raise(
            session, order_id, with_items=True, with_payments=False
        )

        if order.status != OrderStatus.PAYMENT_PENDING:
            return order

        if _remaining(order) <= ZERO:
            order.status = OrderStatus.PAID
            order.paid_at = order.paid_at or _utcnow()
            await session.flush()
            return order

        order.status = OrderStatus.EXPIRED
        order.cancelled_at = _utcnow()

        for item in order.items:
            if item.product_id is not None:
                await session.execute(
                    update(Product)
                    .where(Product.id == item.product_id)
                    .values(stock=Product.stock + item.quantity)
                )

        await session.flush()
        logger.info("Заказ просрочен, остатки восстановлены: order_id={}", order.id)
        return order

    # ========================================================
    # Выдача и Возврат
    # ========================================================

    async def deliver(
        self,
        session: AsyncSession,
        *,
        order_id: int,
        delivery_data: dict[str, Any] | None = None,
    ) -> Order:
        """Помечает полностью оплаченный заказ как DELIVERED."""
        order = await self.get_or_raise(
            session, order_id, with_items=False, with_payments=False
        )

        if order.status == OrderStatus.DELIVERED:
            return order

        if order.status != OrderStatus.PAID:
            raise InvalidOrderStateError("Выдать можно только полностью оплаченный заказ.")

        order.status = OrderStatus.DELIVERED
        order.delivered_at = _utcnow()

        if delivery_data is not None:
            order.delivery_data = delivery_data

        await session.flush()
        logger.info("Заказ выдан: order_id={}", order.id)
        return order

    async def refund(
        self,
        session: AsyncSession,
        *,
        order_id: int,
        reason: str | None = None,
        refund_balance: bool = True,
    ) -> Order:
        """Возвращает оплаченный заказ и товары на склад."""
        order = await self.get_or_raise(
            session, order_id, with_items=True, with_payments=True
        )

        if order.status == OrderStatus.REFUNDED:
            return order

        if order.status not in {OrderStatus.PAID, OrderStatus.DELIVERED}:
            raise InvalidOrderStateError("Возврат доступен только для оплаченного или выданного заказа.")

        balance_refund = _money(order.balance_paid_usd)

        if refund_balance and balance_refund > ZERO:
            refund_key = f"refund:order:{order.id}:balance:{order.id}"
            await balance_service.refund(
                session,
                user_id=order.user_id,
                amount_usd=balance_refund,
                description=f"Возврат за заказ {order.order_number}",
                idempotency_key=refund_key,
                order_id=order.id,
            )

        order.status = OrderStatus.REFUNDED

        for item in order.items:
            if item.product_id is not None:
                await session.execute(
                    update(Product)
                    .where(Product.id == item.product_id)
                    .values(stock=Product.stock + item.quantity)
                )

        if reason and reason.strip():
            order.notes = (f"{order.notes}\n" if order.notes else "") + f"Возврат: {reason.strip()}"

        await session.flush()
        logger.info(
            "Заказ возвращён, остатки восстановлены: order_id={}, balance_refund_usd={}",
            order.id,
            balance_refund,
        )
        return order


# ============================================================
# Глобальный экземпляр
# ============================================================


order_service = OrderService()


__all__ = [
    "OrderService",
    "OrderServiceError",
    "OrderNotFoundError",
    "CartEmptyError",
    "ProductNotFoundError",
    "ProductInactiveError",
    "InsufficientStockError",
    "InvalidOrderAmountError",
    "OrderAlreadyPaidError",
    "OrderCancelledError",
    "OrderExpiredError",
    "InvalidOrderStateError",
    "PaymentAmountExceededError",
    "OrderTotals",
    "PaymentApplicationResult",
    "OrderCreateResult",
    "order_service",
]