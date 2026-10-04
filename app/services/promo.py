# app/services/promo.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Sequence

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PromoCode, PromoCodeUsage


# ======================================================================
# Исключения
# ======================================================================


class PromoError(Exception):
    """Базовая ошибка сервиса промокодов."""


class PromoNotFoundError(PromoError):
    """Промокод не найден."""


class PromoInactiveError(PromoError):
    """Промокод отключён."""


class PromoExpiredError(PromoError):
    """Срок действия промокода истёк."""


class PromoUsageLimitError(PromoError):
    """Достигнут лимит использования промокода."""


class PromoAlreadyUsedError(PromoError):
    """Пользователь уже использовал этот промокод."""


class PromoMinimumOrderError(PromoError):
    """Заказ не достигает минимальной суммы промокода."""


class PromoValidationError(PromoError):
    """Некорректные данные промокода."""


class PromoAlreadyExistsError(PromoError):
    """Промокод с таким кодом уже существует."""


class PromoDiscountError(PromoError):
    """Ошибка расчёта скидки."""


# ======================================================================
# DTO
# ======================================================================


@dataclass(slots=True, frozen=True)
class PromoDiscountResult:
    """Результат расчёта скидки."""

    promo_code: PromoCode
    subtotal_usd: Decimal
    discount_usd: Decimal
    total_usd: Decimal


@dataclass(slots=True, frozen=True)
class PromoApplyResult:
    """Результат применения промокода к заказу."""

    promo_code: PromoCode
    usage: PromoCodeUsage
    discount_usd: Decimal
    total_usd: Decimal


@dataclass(slots=True, frozen=True)
class PromoStats:
    """Статистика использования промокода."""

    promo_code_id: int
    total_uses: int
    total_discount_usd: Decimal


# ======================================================================
# Сервис
# ======================================================================


class PromoService:
    """Сервис промокодов."""

    MONEY_QUANT = Decimal("0.00000001")

    MAX_CODE_LENGTH = 128
    MAX_LIMIT = 500

    # --------------------------------------------------------------
    # Нормализация
    # --------------------------------------------------------------

    def _normalize_code(
        self,
        code: str,
    ) -> str:
        """Нормализует код промокода."""

        if not isinstance(code, str):
            raise PromoValidationError(
                "Промокод должен быть строкой."
            )

        normalized = code.strip().upper()

        if not normalized:
            raise PromoValidationError(
                "Промокод не может быть пустым."
            )

        if len(normalized) > self.MAX_CODE_LENGTH:
            raise PromoValidationError(
                "Промокод слишком длинный."
            )

        return normalized

    def _to_decimal(
        self,
        value: Decimal | int | float | str | None,
        *,
        field_name: str,
    ) -> Decimal:
        """Безопасно преобразует значение в Decimal."""

        if value is None:
            raise PromoValidationError(
                f"Поле {field_name} не может быть пустым."
            )

        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise PromoValidationError(
                f"Некорректное значение поля {field_name}."
            ) from exc

        if not result.is_finite():
            raise PromoValidationError(
                f"Некорректное значение поля {field_name}."
            )

        return result

    def _normalize_money(
        self,
        value: Decimal | int | float | str,
        *,
        field_name: str,
    ) -> Decimal:
        """Проверяет денежное значение."""

        result = self._to_decimal(
            value,
            field_name=field_name,
        )

        if result < 0:
            raise PromoValidationError(
                f"{field_name} не может быть отрицательным."
            )

        return result.quantize(
            self.MONEY_QUANT
        )

    def _normalize_percent(
        self,
        value: Decimal | int | float | str,
    ) -> Decimal:
        """Проверяет процент скидки."""

        result = self._to_decimal(
            value,
            field_name="discount_percent",
        )

        if result < 0:
            raise PromoValidationError(
                "Процент скидки не может быть отрицательным."
            )

        if result > 100:
            raise PromoValidationError(
                "Процент скидки не может быть больше 100."
            )

        return result

    def _normalize_max_uses(
        self,
        value: int | None,
    ) -> int | None:
        """Проверяет лимит использования."""

        if value is None:
            return None

        if isinstance(value, bool):
            raise PromoValidationError(
                "Лимит использования должен быть целым числом."
            )

        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise PromoValidationError(
                "Некорректный лимит использования."
            ) from exc

        if result <= 0:
            raise PromoValidationError(
                "Лимит использования должен быть больше нуля."
            )

        return result

    def _normalize_datetime(
        self,
        value: datetime | None,
    ) -> datetime | None:
        """Приводит дату окончания к UTC."""

        if value is None:
            return None

        if value.tzinfo is None:
            return value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(
            timezone.utc
        )

    # --------------------------------------------------------------
    # Проверка модели
    # --------------------------------------------------------------

    def _validate_promo_model(
        self,
        promo: PromoCode,
    ) -> None:
        """Проверяет целостность промокода перед использованием."""

        percent = promo.discount_percent
        fixed = promo.discount_amount_usd

        # В текущей модели эти два поля взаимоисключающие.
        if percent is None and fixed is None:
            raise PromoValidationError(
                "У промокода не задан размер скидки."
            )

        if percent is not None and fixed is not None:
            raise PromoValidationError(
                "У промокода одновременно указаны "
                "процентная и фиксированная скидки."
            )

        if percent is not None:
            percent_decimal = Decimal(
                str(percent)
            )

            if percent_decimal < 0:
                raise PromoValidationError(
                    "Процент скидки отрицательный."
                )

            if percent_decimal > 100:
                raise PromoValidationError(
                    "Процент скидки больше 100."
                )

        if fixed is not None:
            fixed_decimal = Decimal(
                str(fixed)
            )

            if fixed_decimal < 0:
                raise PromoValidationError(
                    "Фиксированная скидка отрицательная."
                )

        min_order = Decimal(
            str(promo.min_order_amount_usd)
        )

        if min_order < 0:
            raise PromoValidationError(
                "Минимальная сумма заказа отрицательная."
            )

        if promo.max_uses is not None:
            if promo.max_uses <= 0:
                raise PromoValidationError(
                    "Лимит использования должен быть больше нуля."
                )

    # ==================================================================
    # Получение
    # ==================================================================

    async def get_by_id(
        self,
        session: AsyncSession,
        promo_id: int,
    ) -> PromoCode | None:
        """Возвращает промокод по ID."""

        if promo_id <= 0:
            return None

        return await session.get(
            PromoCode,
            promo_id,
        )

    async def get_or_raise(
        self,
        session: AsyncSession,
        promo_id: int,
    ) -> PromoCode:
        """Возвращает промокод или выбрасывает ошибку."""

        promo = await self.get_by_id(
            session=session,
            promo_id=promo_id,
        )

        if promo is None:
            raise PromoNotFoundError(
                f"Промокод {promo_id} не найден."
            )

        return promo

    async def get_by_code(
        self,
        session: AsyncSession,
        code: str,
    ) -> PromoCode | None:
        """Ищет промокод по коду."""

        normalized_code = self._normalize_code(
            code
        )

        result = await session.execute(
            select(PromoCode).where(
                PromoCode.code == normalized_code
            )
        )

        return result.scalar_one_or_none()

    async def get_by_code_or_raise(
        self,
        session: AsyncSession,
        code: str,
    ) -> PromoCode:
        """Ищет промокод по коду или выбрасывает ошибку."""

        promo = await self.get_by_code(
            session=session,
            code=code,
        )

        if promo is None:
            raise PromoNotFoundError(
                "Промокод не найден."
            )

        return promo

    async def list_promos(
        self,
        session: AsyncSession,
        *,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PromoCode]:
        """Возвращает список промокодов."""

        limit = max(
            1,
            min(
                int(limit),
                self.MAX_LIMIT,
            ),
        )

        offset = max(
            0,
            int(offset),
        )

        query = select(
            PromoCode
        )

        if is_active is not None:
            query = query.where(
                PromoCode.is_active
                == bool(is_active)
            )

        query = (
            query
            .order_by(
                PromoCode.created_at.desc(),
                PromoCode.id.desc(),
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
    # Создание
    # ==================================================================

    async def create(
        self,
        session: AsyncSession,
        *,
        code: str,
        discount_percent: Decimal | None = None,
        discount_amount_usd: Decimal | None = None,
        max_uses: int | None = None,
        min_order_amount_usd: Decimal = Decimal("0"),
        expires_at: datetime | None = None,
        is_active: bool = True,
    ) -> PromoCode:
        """Создаёт промокод."""

        normalized_code = self._normalize_code(
            code
        )

        if (
            discount_percent is None
            and discount_amount_usd is None
        ):
            raise PromoValidationError(
                "Необходимо указать скидку."
            )

        if (
            discount_percent is not None
            and discount_amount_usd is not None
        ):
            raise PromoValidationError(
                "Нельзя одновременно задать "
                "процентную и фиксированную скидку."
            )

        normalized_percent = None

        if discount_percent is not None:
            normalized_percent = (
                self._normalize_percent(
                    discount_percent
                )
            )

        normalized_fixed = None

        if discount_amount_usd is not None:
            normalized_fixed = (
                self._normalize_money(
                    discount_amount_usd,
                    field_name="discount_amount_usd",
                )
            )

            if normalized_fixed <= 0:
                raise PromoValidationError(
                    "Фиксированная скидка должна быть больше нуля."
                )

        normalized_max_uses = (
            self._normalize_max_uses(
                max_uses
            )
        )

        normalized_min_order = (
            self._normalize_money(
                min_order_amount_usd,
                field_name="min_order_amount_usd",
            )
        )

        normalized_expires = (
            self._normalize_datetime(
                expires_at
            )
        )

        existing = await self.get_by_code(
            session=session,
            code=normalized_code,
        )

        if existing is not None:
            raise PromoAlreadyExistsError(
                f"Промокод {normalized_code} уже существует."
            )

        promo = PromoCode(
            code=normalized_code,
            discount_percent=normalized_percent,
            discount_amount_usd=normalized_fixed,
            max_uses=normalized_max_uses,
            min_order_amount_usd=normalized_min_order,
            expires_at=normalized_expires,
            is_active=bool(is_active),
        )

        session.add(promo)

        try:
            await session.flush()
        except IntegrityError as exc:
            logger.exception(
                "Ошибка создания промокода {}",
                normalized_code,
            )

            raise PromoAlreadyExistsError(
                f"Промокод {normalized_code} уже существует."
            ) from exc

        logger.info(
            "Создан промокод: id={}, code={}",
            promo.id,
            promo.code,
        )

        return promo

    # ==================================================================
    # Изменение
    # ==================================================================

    async def update(
        self,
        session: AsyncSession,
        promo_id: int,
        *,
        code: str | None = None,
        discount_percent: Decimal | None = None,
        discount_amount_usd: Decimal | None = None,
        max_uses: int | None = None,
        min_order_amount_usd: Decimal | None = None,
        expires_at: datetime | None = None,
        is_active: bool | None = None,
    ) -> PromoCode:
        """Обновляет промокод."""

        promo = await self.get_or_raise(
            session=session,
            promo_id=promo_id,
        )

        if code is not None:
            normalized_code = self._normalize_code(
                code
            )

            existing = await self.get_by_code(
                session=session,
                code=normalized_code,
            )

            if (
                existing is not None
                and existing.id != promo.id
            ):
                raise PromoAlreadyExistsError(
                    f"Промокод {normalized_code} уже существует."
                )

            promo.code = normalized_code

        # Если передан хотя бы один параметр скидки,
        # пересобираем скидочную конфигурацию целиком.
        if (
            discount_percent is not None
            or discount_amount_usd is not None
        ):
            if (
                discount_percent is not None
                and discount_amount_usd is not None
            ):
                raise PromoValidationError(
                    "Нельзя одновременно задать "
                    "процентную и фиксированную скидку."
                )

            if discount_percent is not None:
                promo.discount_percent = (
                    self._normalize_percent(
                        discount_percent
                    )
                )
                promo.discount_amount_usd = None

            else:
                normalized_fixed = (
                    self._normalize_money(
                        discount_amount_usd,
                        field_name="discount_amount_usd",
                    )
                )

                if normalized_fixed <= 0:
                    raise PromoValidationError(
                        "Фиксированная скидка должна быть больше нуля."
                    )

                promo.discount_percent = None
                promo.discount_amount_usd = (
                    normalized_fixed
                )

        if max_uses is not None:
            normalized_max_uses = (
                self._normalize_max_uses(
                    max_uses
                )
            )

            promo.max_uses = normalized_max_uses

        if min_order_amount_usd is not None:
            promo.min_order_amount_usd = (
                self._normalize_money(
                    min_order_amount_usd,
                    field_name="min_order_amount_usd",
                )
            )

        if expires_at is not None:
            promo.expires_at = (
                self._normalize_datetime(
                    expires_at
                )
            )

        if is_active is not None:
            promo.is_active = bool(
                is_active
            )

        self._validate_promo_model(
            promo
        )

        await session.flush()

        logger.info(
            "Обновлён промокод: id={}, code={}",
            promo.id,
            promo.code,
        )

        return promo

    # ==================================================================
    # Состояние
    # ==================================================================

    async def set_active(
        self,
        session: AsyncSession,
        promo_id: int,
        is_active: bool,
    ) -> PromoCode:
        """Включает или отключает промокод."""

        promo = await self.get_or_raise(
            session=session,
            promo_id=promo_id,
        )

        promo.is_active = bool(
            is_active
        )

        await session.flush()

        return promo

    async def activate(
        self,
        session: AsyncSession,
        promo_id: int,
    ) -> PromoCode:
        """Активирует промокод."""

        return await self.set_active(
            session=session,
            promo_id=promo_id,
            is_active=True,
        )

    async def deactivate(
        self,
        session: AsyncSession,
        promo_id: int,
    ) -> PromoCode:
        """Деактивирует промокод."""

        return await self.set_active(
            session=session,
            promo_id=promo_id,
            is_active=False,
        )

    # ==================================================================
    # Удаление
    # ==================================================================

    async def delete(
        self,
        session: AsyncSession,
        promo_id: int,
    ) -> bool:
        """Удаляет промокод."""

        promo = await self.get_or_raise(
            session=session,
            promo_id=promo_id,
        )

        await session.delete(
            promo
        )

        await session.flush()

        logger.info(
            "Удалён промокод: id={}, code={}",
            promo.id,
            promo.code,
        )

        return True

    # ==================================================================
    # Проверка доступности
    # ==================================================================

    async def _check_available(
        self,
        session: AsyncSession,
        promo: PromoCode,
        *,
        user_id: int | None = None,
        order_amount_usd: Decimal | None = None,
    ) -> None:
        """Проверяет возможность использования промокода."""

        self._validate_promo_model(
            promo
        )

        if not promo.is_active:
            raise PromoInactiveError(
                "Промокод отключён."
            )

        now = datetime.now(
            timezone.utc
        )

        expires_at = self._normalize_datetime(
            promo.expires_at
        )

        if (
            expires_at is not None
            and expires_at <= now
        ):
            raise PromoExpiredError(
                "Срок действия промокода истёк."
            )

        if (
            order_amount_usd is not None
            and Decimal(str(order_amount_usd))
            < Decimal(
                str(promo.min_order_amount_usd)
            )
        ):
            raise PromoMinimumOrderError(
                "Сумма заказа меньше минимальной "
                "для этого промокода."
            )

        if user_id is not None:
            already_used = await self.has_user_used(
                session=session,
                promo_code_id=promo.id,
                user_id=user_id,
            )

            if already_used:
                raise PromoAlreadyUsedError(
                    "Вы уже использовали этот промокод."
                )

        if promo.max_uses is not None:
            usage_count = await self.count_usages(
                session=session,
                promo_code_id=promo.id,
            )

            if usage_count >= promo.max_uses:
                raise PromoUsageLimitError(
                    "Лимит использования промокода исчерпан."
                )

    async def validate(
        self,
        session: AsyncSession,
        *,
        code: str,
        user_id: int | None = None,
        order_amount_usd: Decimal | None = None,
    ) -> PromoCode:
        """Проверяет промокод и возвращает его."""

        promo = await self.get_by_code(
            session=session,
            code=code,
        )

        if promo is None:
            raise PromoNotFoundError(
                "Промокод не найден."
            )

        await self._check_available(
            session=session,
            promo=promo,
            user_id=user_id,
            order_amount_usd=order_amount_usd,
        )

        return promo

    # ==================================================================
    # Расчёт скидки
    # ==================================================================

    def calculate_discount(
        self,
        promo: PromoCode,
        order_amount_usd: Decimal,
    ) -> Decimal:
        """
        Рассчитывает скидку.

        Скидка никогда не превышает сумму заказа.
        """

        self._validate_promo_model(
            promo
        )

        amount = self._normalize_money(
            order_amount_usd,
            field_name="order_amount_usd",
        )

        if amount <= 0:
            return Decimal("0")

        minimum = Decimal(
            str(promo.min_order_amount_usd)
        )

        if amount < minimum:
            return Decimal("0")

        if promo.discount_percent is not None:
            percent = Decimal(
                str(promo.discount_percent)
            )

            discount = (
                amount
                * percent
                / Decimal("100")
            )

        elif promo.discount_amount_usd is not None:
            discount = Decimal(
                str(promo.discount_amount_usd)
            )

        else:
            raise PromoDiscountError(
                "У промокода отсутствует скидка."
            )

        discount = discount.quantize(
            self.MONEY_QUANT
        )

        if discount < 0:
            discount = Decimal("0")

        if discount > amount:
            discount = amount

        return discount

    async def calculate(
        self,
        session: AsyncSession,
        *,
        code: str,
        order_amount_usd: Decimal,
        user_id: int | None = None,
    ) -> PromoDiscountResult:
        """Проверяет промокод и рассчитывает итог."""

        amount = self._normalize_money(
            order_amount_usd,
            field_name="order_amount_usd",
        )

        promo = await self.validate(
            session=session,
            code=code,
            user_id=user_id,
            order_amount_usd=amount,
        )

        discount = self.calculate_discount(
            promo=promo,
            order_amount_usd=amount,
        )

        total = (
            amount - discount
        ).quantize(
            self.MONEY_QUANT
        )

        if total < 0:
            total = Decimal("0")

        return PromoDiscountResult(
            promo_code=promo,
            subtotal_usd=amount,
            discount_usd=discount,
            total_usd=total,
        )

    # Алиасы для checkout.

    async def calculate_discount_for_order(
        self,
        session: AsyncSession,
        *,
        code: str,
        order_amount_usd: Decimal,
        user_id: int | None = None,
    ) -> PromoDiscountResult:
        """Совместимый метод для checkout."""

        return await self.calculate(
            session=session,
            code=code,
            order_amount_usd=order_amount_usd,
            user_id=user_id,
        )

    # ==================================================================
    # Использование
    # ==================================================================

    async def has_user_used(
        self,
        session: AsyncSession,
        *,
        promo_code_id: int,
        user_id: int,
    ) -> bool:
        """Проверяет, использовал ли пользователь промокод."""

        if promo_code_id <= 0 or user_id <= 0:
            return False

        result = await session.execute(
            select(PromoCodeUsage.id)
            .where(
                PromoCodeUsage.promo_code_id
                == promo_code_id,
                PromoCodeUsage.user_id
                == user_id,
            )
            .limit(1)
        )

        return result.scalar_one_or_none() is not None

    async def get_user_usage(
        self,
        session: AsyncSession,
        *,
        promo_code_id: int,
        user_id: int,
    ) -> PromoCodeUsage | None:
        """Возвращает использование промокода пользователем."""

        result = await session.execute(
            select(PromoCodeUsage)
            .where(
                PromoCodeUsage.promo_code_id
                == promo_code_id,
                PromoCodeUsage.user_id
                == user_id,
            )
            .limit(1)
        )

        return result.scalar_one_or_none()

    async def apply(
        self,
        session: AsyncSession,
        *,
        promo_code_id: int,
        user_id: int,
        order_id: int,
        order_amount_usd: Decimal,
    ) -> PromoApplyResult:
        """
        Фиксирует использование промокода.

        Вызывается после создания заказа, когда уже известен order_id.
        """

        if promo_code_id <= 0:
            raise PromoValidationError(
                "Некорректный ID промокода."
            )

        if user_id <= 0:
            raise PromoValidationError(
                "Некорректный ID пользователя."
            )

        if order_id <= 0:
            raise PromoValidationError(
                "Некорректный ID заказа."
            )

        amount = self._normalize_money(
            order_amount_usd,
            field_name="order_amount_usd",
        )

        promo = await self.get_or_raise(
            session=session,
            promo_id=promo_code_id,
        )

        await self._check_available(
            session=session,
            promo=promo,
            user_id=user_id,
            order_amount_usd=amount,
        )

        discount = self.calculate_discount(
            promo=promo,
            order_amount_usd=amount,
        )

        total = (
            amount - discount
        ).quantize(
            self.MONEY_QUANT
        )

        usage = PromoCodeUsage(
            promo_code_id=promo.id,
            user_id=user_id,
            order_id=order_id,
            discount_usd=discount,
        )

        # Здесь намеренно используется SAVEPOINT:
        # UNIQUE(promo_code_id, user_id) и
        # UNIQUE(promo_code_id, order_id) должны защищать
        # от конкурентного двойного применения.
        try:
            async with session.begin_nested():
                session.add(usage)
                await session.flush()

        except IntegrityError:
            existing = await self.get_user_usage(
                session=session,
                promo_code_id=promo.id,
                user_id=user_id,
            )

            if existing is not None:
                raise PromoAlreadyUsedError(
                    "Вы уже использовали этот промокод."
                )

            raise PromoError(
                "Промокод уже применён к другому заказу."
            )

        logger.info(
            "Промокод применён: "
            "promo_id={}, user_id={}, order_id={}, discount={}",
            promo.id,
            user_id,
            order_id,
            discount,
        )

        return PromoApplyResult(
            promo_code=promo,
            usage=usage,
            discount_usd=discount,
            total_usd=total,
        )

    async def apply_code(
        self,
        session: AsyncSession,
        *,
        code: str,
        user_id: int,
        order_id: int,
        order_amount_usd: Decimal,
    ) -> PromoApplyResult:
        """Применяет промокод по текстовому коду."""

        promo = await self.validate(
            session=session,
            code=code,
            user_id=user_id,
            order_amount_usd=order_amount_usd,
        )

        return await self.apply(
            session=session,
            promo_code_id=promo.id,
            user_id=user_id,
            order_id=order_id,
            order_amount_usd=order_amount_usd,
        )

    # ==================================================================
    # Статистика
    # ==================================================================

    async def count_usages(
        self,
        session: AsyncSession,
        *,
        promo_code_id: int,
    ) -> int:
        """Возвращает количество использований промокода."""

        if promo_code_id <= 0:
            return 0

        result = await session.execute(
            select(
                func.count(
                    PromoCodeUsage.id
                )
            ).where(
                PromoCodeUsage.promo_code_id
                == promo_code_id
            )
        )

        return int(
            result.scalar_one()
        )

    async def get_stats(
        self,
        session: AsyncSession,
        *,
        promo_code_id: int,
    ) -> PromoStats:
        """Возвращает статистику промокода."""

        if promo_code_id <= 0:
            raise PromoValidationError(
                "Некорректный ID промокода."
            )

        result = await session.execute(
            select(
                func.count(
                    PromoCodeUsage.id
                ),
                func.coalesce(
                    func.sum(
                        PromoCodeUsage.discount_usd
                    ),
                    0,
                ),
            ).where(
                PromoCodeUsage.promo_code_id
                == promo_code_id
            )
        )

        count, total_discount = (
            result.one()
        )

        return PromoStats(
            promo_code_id=promo_code_id,
            total_uses=int(count or 0),
            total_discount_usd=Decimal(
                str(
                    total_discount or 0
                )
            ),
        )

    async def get_usage_list(
        self,
        session: AsyncSession,
        *,
        promo_code_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PromoCodeUsage]:
        """Возвращает историю использования промокода."""

        limit = max(
            1,
            min(
                int(limit),
                self.MAX_LIMIT,
            ),
        )

        offset = max(
            0,
            int(offset),
        )

        result = await session.execute(
            select(PromoCodeUsage)
            .where(
                PromoCodeUsage.promo_code_id
                == promo_code_id
            )
            .order_by(
                PromoCodeUsage.created_at.desc(),
                PromoCodeUsage.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        return list(
            result.scalars().all()
        )

    # ==================================================================
    # Массовые операции
    # ==================================================================

    async def deactivate_expired(
        self,
        session: AsyncSession,
    ) -> int:
        """
        Деактивирует истёкшие промокоды.

        Само наличие expires_at уже достаточно для проверки
        при использовании, но этот метод удобен для обслуживания БД.
        """

        now = datetime.now(
            timezone.utc
        )

        result = await session.execute(
            select(PromoCode).where(
                PromoCode.is_active.is_(True),
                PromoCode.expires_at.is_not(None),
                PromoCode.expires_at <= now,
            )
        )

        promos = list(
            result.scalars().all()
        )

        for promo in promos:
            promo.is_active = False

        if promos:
            await session.flush()

            logger.info(
                "Автоматически деактивировано "
                "просроченных промокодов: {}",
                len(promos),
            )

        return len(promos)

    async def delete_expired(
        self,
        session: AsyncSession,
    ) -> int:
        """
        Удаляет истёкшие промокоды.

        Использовать осторожно: вместе с промокодом
        удаляется история его применений из-за CASCADE.
        """

        now = datetime.now(
            timezone.utc
        )

        result = await session.execute(
            select(PromoCode).where(
                PromoCode.expires_at.is_not(None),
                PromoCode.expires_at <= now,
            )
        )

        promos = list(
            result.scalars().all()
        )

        for promo in promos:
            await session.delete(
                promo
            )

        if promos:
            await session.flush()

        return len(promos)


# ======================================================================
# Глобальный экземпляр
# ======================================================================


promo_service = PromoService()


__all__ = [
    "PromoError",
    "PromoNotFoundError",
    "PromoInactiveError",
    "PromoExpiredError",
    "PromoUsageLimitError",
    "PromoAlreadyUsedError",
    "PromoMinimumOrderError",
    "PromoValidationError",
    "PromoAlreadyExistsError",
    "PromoDiscountError",
    "PromoDiscountResult",
    "PromoApplyResult",
    "PromoStats",
    "PromoService",
    "promo_service",
]