# app/services/referrals.py

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Order, Referral, User
from app.services.balance import BalanceResult, balance_service


# ======================================================================
# Исключения
# ======================================================================


class ReferralError(Exception):
    """Базовая ошибка реферального сервиса."""


class ReferralDisabledError(ReferralError):
    """Реферальная система отключена."""


class ReferralUserNotFoundError(ReferralError):
    """Пользователь не найден."""


class ReferralAlreadyExistsError(ReferralError):
    """Реферальная связь уже существует."""


class InvalidReferralError(ReferralError):
    """Некорректная реферальная операция."""


# ======================================================================
# DTO
# ======================================================================


@dataclass(slots=True, frozen=True)
class ReferralInfo:
    """Информация о реферальной связи."""

    referral_id: int
    referrer_id: int
    referred_id: int
    reward_percent: Decimal
    reward_amount_usd: Decimal
    order_id: Optional[int]


@dataclass(slots=True, frozen=True)
class ReferralRewardResult:
    """Результат начисления реферального вознаграждения."""

    referral: ReferralInfo
    balance: BalanceResult
    rewarded: bool


# ======================================================================
# Сервис
# ======================================================================


class ReferralService:
    """Сервис реферальной программы."""

    REWARD_QUANT = Decimal("0.00000001")

    def __init__(self) -> None:
        self._enabled = bool(
            settings.referrals_enabled
        )

        self._default_percent = Decimal(
            str(settings.referrals_default_percent)
        )

        self._min_order_amount = Decimal(
            str(settings.referrals_min_order_usd)
        )

        self._validate_settings()

    def _validate_settings(self) -> None:
        """Проверяет настройки реферальной системы."""

        if self._default_percent < 0:
            raise ValueError(
                "referrals_default_percent не может быть отрицательным"
            )

        if self._default_percent > 100:
            raise ValueError(
                "referrals_default_percent не может быть больше 100"
            )

        if self._min_order_amount < 0:
            raise ValueError(
                "referrals_min_order_usd не может быть отрицательным"
            )

    # ==================================================================
    # Свойства
    # ==================================================================

    @property
    def enabled(self) -> bool:
        """Возвращает состояние реферальной программы."""

        return self._enabled

    @property
    def default_percent(self) -> Decimal:
        """Процент реферального вознаграждения по умолчанию."""

        return self._default_percent

    @property
    def min_order_amount(self) -> Decimal:
        """Минимальная сумма заказа для начисления."""

        return self._min_order_amount

    # ==================================================================
    # Пользователь / реферальный код
    # ==================================================================

    async def get_user_by_referral_code(
        self,
        session: AsyncSession,
        referral_code: str,
    ) -> Optional[User]:
        """Находит пользователя по реферальному коду."""

        normalized_code = referral_code.strip()

        if not normalized_code:
            return None

        result = await session.execute(
            select(User).where(
                User.referral_code == normalized_code,
            )
        )

        return result.scalar_one_or_none()

    async def get_user_referrer(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> Optional[User]:
        """Возвращает пользователя, который пригласил пользователя."""

        if user_id <= 0:
            raise ReferralUserNotFoundError(
                "Некорректный ID пользователя"
            )

        user = await session.get(
            User,
            user_id,
        )

        if user is None:
            raise ReferralUserNotFoundError(
                f"Пользователь {user_id} не найден"
            )

        if user.referred_by_id is None:
            return None

        referrer = await session.get(
            User,
            user.referred_by_id,
        )

        return referrer

    async def bind_referral(
        self,
        session: AsyncSession,
        referred_user_id: int,
        referral_code: str,
    ) -> User:
        """
        Привязывает пользователя к пригласившему.

        Повторная привязка запрещена.
        Нельзя пригласить самого себя.
        """

        if not self.enabled:
            raise ReferralDisabledError(
                "Реферальная программа отключена"
            )

        if referred_user_id <= 0:
            raise ReferralUserNotFoundError(
                "Некорректный ID пользователя"
            )

        referred_user = await session.get(
            User,
            referred_user_id,
        )

        if referred_user is None:
            raise ReferralUserNotFoundError(
                f"Пользователь {referred_user_id} не найден"
            )

        if referred_user.referred_by_id is not None:
            raise ReferralAlreadyExistsError(
                "Для пользователя уже установлен реферер"
            )

        referrer = await self.get_user_by_referral_code(
            session=session,
            referral_code=referral_code,
        )

        if referrer is None:
            raise InvalidReferralError(
                "Реферальный код не найден"
            )

        if referrer.id == referred_user.id:
            raise InvalidReferralError(
                "Нельзя использовать собственный реферальный код"
            )

        referred_user.referred_by_id = referrer.id

        await session.flush()

        logger.info(
            "Пользователь {} привязан к рефереру {}",
            referred_user.id,
            referrer.id,
        )

        return referrer

    # ==================================================================
    # Реферальные записи
    # ==================================================================

    async def get_referral(
        self,
        session: AsyncSession,
        referral_id: int,
    ) -> Optional[Referral]:
        """Возвращает реферальную запись."""

        if referral_id <= 0:
            return None

        return await session.get(
            Referral,
            referral_id,
        )

    async def get_order_referral(
        self,
        session: AsyncSession,
        order_id: int,
    ) -> Optional[Referral]:
        """Возвращает реферальную запись по заказу."""

        if order_id <= 0:
            return None

        result = await session.execute(
            select(Referral).where(
                Referral.order_id == order_id,
            )
        )

        return result.scalar_one_or_none()

    # ==================================================================
    # Расчёт
    # ==================================================================

    async def calculate_reward(
        self,
        session: AsyncSession,
        order: Order,
        percent: Optional[Decimal] = None,
    ) -> Decimal:
        """Рассчитывает размер реферального вознаграждения."""

        if not self.enabled:
            return Decimal("0")

        if order.id is None:
            return Decimal("0")

        total_usd = Decimal(
            str(order.total_usd)
        )

        if total_usd <= 0:
            return Decimal("0")

        if total_usd < self.min_order_amount:
            return Decimal("0")

        referrer = await self.get_user_referrer(
            session=session,
            user_id=order.user_id,
        )

        if referrer is None:
            return Decimal("0")

        reward_percent = (
            self._default_percent
            if percent is None
            else Decimal(str(percent))
        )

        if reward_percent < 0:
            raise InvalidReferralError(
                "Реферальный процент не может быть отрицательным"
            )

        if reward_percent == 0:
            return Decimal("0")

        if reward_percent > 100:
            raise InvalidReferralError(
                "Реферальный процент не может быть больше 100"
            )

        reward = (
            total_usd
            * reward_percent
            / Decimal("100")
        ).quantize(
            self.REWARD_QUANT
        )

        if reward <= 0:
            return Decimal("0")

        return reward

    # ==================================================================
    # Начисление
    # ==================================================================

    async def create_reward(
        self,
        session: AsyncSession,
        order: Order,
        percent: Optional[Decimal] = None,
    ) -> Optional[ReferralRewardResult]:
        """
        Создаёт реферальное начисление после успешной оплаты заказа.

        Метод рассчитан на вызов внутри внешней DB-транзакции.

        Защита от повторного начисления:
        1. проверка существующей записи;
        2. UNIQUE(order_id) на уровне БД;
        3. savepoint при конкурентном создании;
        4. идемпотентное начисление баланса.
        """

        if not self.enabled:
            return None

        if order.id is None:
            raise InvalidReferralError(
                "Нельзя создать реферальное начисление "
                "для заказа без ID"
            )

        # --------------------------------------------------------------
        # Первичная проверка
        # --------------------------------------------------------------

        existing = await self.get_order_referral(
            session=session,
            order_id=order.id,
        )

        if existing is not None:
            return await self._build_existing_result(
                session=session,
                referral=existing,
            )

        # --------------------------------------------------------------
        # Получаем пользователя заказа
        # --------------------------------------------------------------

        referred_user = await session.get(
            User,
            order.user_id,
        )

        if referred_user is None:
            raise ReferralUserNotFoundError(
                f"Пользователь {order.user_id} не найден"
            )

        if referred_user.referred_by_id is None:
            return None

        referrer = await session.get(
            User,
            referred_user.referred_by_id,
        )

        if referrer is None:
            logger.warning(
                "Реферер {} не найден для пользователя {}",
                referred_user.referred_by_id,
                referred_user.id,
            )
            return None

        # --------------------------------------------------------------
        # Процент
        # --------------------------------------------------------------

        reward_percent = (
            self._default_percent
            if percent is None
            else Decimal(str(percent))
        )

        if reward_percent < 0:
            raise InvalidReferralError(
                "Реферальный процент не может быть отрицательным"
            )

        if reward_percent > 100:
            raise InvalidReferralError(
                "Реферальный процент не может быть больше 100"
            )

        reward_amount = await self.calculate_reward(
            session=session,
            order=order,
            percent=reward_percent,
        )

        if reward_amount <= 0:
            return None

        # --------------------------------------------------------------
        # Создание записи.
        #
        # UNIQUE(order_id) в модели защищает от двойной записи.
        # SAVEPOINT позволяет безопасно пережить IntegrityError,
        # не ломая внешнюю транзакцию.
        # --------------------------------------------------------------

        referral = Referral(
            referrer_id=referrer.id,
            referred_id=referred_user.id,
            order_id=order.id,
            reward_percent=reward_percent,
            reward_amount_usd=reward_amount,
        )

        try:
            async with session.begin_nested():
                session.add(referral)
                await session.flush()

        except IntegrityError:
            logger.warning(
                "Конкурентное создание Referral для заказа {}",
                order.id,
            )

            existing = await self.get_order_referral(
                session=session,
                order_id=order.id,
            )

            if existing is None:
                raise

            return await self._build_existing_result(
                session=session,
                referral=existing,
            )

        # --------------------------------------------------------------
        # Идемпотентный ключ начисления
        # --------------------------------------------------------------

        idempotency_key = (
            f"referral_reward:"
            f"order:{order.id}:"
            f"user:{referrer.id}"
        )

        balance_result = await balance_service.referral_credit(
            session=session,
            user_id=referrer.id,
            amount=reward_amount,
            idempotency_key=idempotency_key,
            description=(
                "Реферальное вознаграждение "
                f"за заказ {order.order_number}"
            ),
            metadata={
                "referral_id": referral.id,
                "order_id": order.id,
                "referred_user_id": referred_user.id,
                "reward_percent": str(
                    reward_percent
                ),
            },
        )

        referral_info = ReferralInfo(
            referral_id=referral.id,
            referrer_id=referral.referrer_id,
            referred_id=referral.referred_id,
            reward_percent=reward_percent,
            reward_amount_usd=reward_amount,
            order_id=referral.order_id,
        )

        logger.info(
            "Реферальное вознаграждение начислено: "
            "referrer={} referred={} order={} amount={}",
            referrer.id,
            referred_user.id,
            order.id,
            reward_amount,
        )

        return ReferralRewardResult(
            referral=referral_info,
            balance=balance_result,
            rewarded=True,
        )

    async def _build_existing_result(
        self,
        session: AsyncSession,
        referral: Referral,
    ) -> ReferralRewardResult:
        """Формирует результат для уже существующего начисления."""

        referrer = await session.get(
            User,
            referral.referrer_id,
        )

        if referrer is None:
            raise ReferralUserNotFoundError(
                f"Реферер {referral.referrer_id} не найден"
            )

        balance = await balance_service.get_balance(
            session=session,
            user_id=referrer.id,
        )

        referral_info = ReferralInfo(
            referral_id=referral.id,
            referrer_id=referral.referrer_id,
            referred_id=referral.referred_id,
            reward_percent=Decimal(
                str(referral.reward_percent)
            ),
            reward_amount_usd=Decimal(
                str(referral.reward_amount_usd)
            ),
            order_id=referral.order_id,
        )

        return ReferralRewardResult(
            referral=referral_info,
            balance=balance,
            rewarded=False,
        )

    # ==================================================================
    # Список рефералов
    # ==================================================================

    async def get_referrals(
        self,
        session: AsyncSession,
        referrer_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Referral]:
        """Возвращает список рефералов."""

        if referrer_id <= 0:
            return []

        limit = max(
            1,
            min(
                int(limit),
                500,
            ),
        )

        offset = max(
            0,
            int(offset),
        )

        result = await session.execute(
            select(Referral)
            .where(
                Referral.referrer_id == referrer_id,
            )
            .order_by(
                Referral.created_at.desc(),
                Referral.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )

        return list(
            result.scalars().all()
        )

    async def count_referrals(
        self,
        session: AsyncSession,
        referrer_id: int,
    ) -> int:
        """Возвращает количество рефералов."""

        if referrer_id <= 0:
            return 0

        result = await session.execute(
            select(
                func.count(Referral.id)
            ).where(
                Referral.referrer_id == referrer_id,
            )
        )

        return int(
            result.scalar_one()
        )

    async def get_total_rewards(
        self,
        session: AsyncSession,
        referrer_id: int,
    ) -> Decimal:
        """Возвращает общую сумму начисленных вознаграждений."""

        if referrer_id <= 0:
            return Decimal("0")

        result = await session.execute(
            select(
                func.coalesce(
                    func.sum(
                        Referral.reward_amount_usd
                    ),
                    0,
                )
            ).where(
                Referral.referrer_id == referrer_id,
            )
        )

        value = result.scalar_one()

        if value is None:
            return Decimal("0")

        return Decimal(
            str(value)
        )

    async def get_referral_stats(
        self,
        session: AsyncSession,
        referrer_id: int,
    ) -> tuple[int, Decimal]:
        """Возвращает количество рефералов и сумму вознаграждений."""

        count = await self.count_referrals(
            session=session,
            referrer_id=referrer_id,
        )

        total_rewards = await self.get_total_rewards(
            session=session,
            referrer_id=referrer_id,
        )

        return count, total_rewards

    # ==================================================================
    # Изменение процента
    # ==================================================================

    async def set_referral_percent(
        self,
        session: AsyncSession,
        referral_id: int,
        percent: Decimal,
    ) -> Referral:
        """Изменяет процент в существующей реферальной записи."""

        if referral_id <= 0:
            raise InvalidReferralError(
                "Некорректный ID реферальной записи"
            )

        try:
            normalized_percent = Decimal(
                str(percent)
            )
        except Exception as exc:
            raise InvalidReferralError(
                "Некорректный реферальный процент"
            ) from exc

        if normalized_percent < 0:
            raise InvalidReferralError(
                "Процент не может быть отрицательным"
            )

        if normalized_percent > 100:
            raise InvalidReferralError(
                "Процент не может быть больше 100"
            )

        referral = await self.get_referral(
            session=session,
            referral_id=referral_id,
        )

        if referral is None:
            raise ReferralError(
                f"Реферальная запись "
                f"{referral_id} не найдена"
            )

        referral.reward_percent = normalized_percent

        await session.flush()

        logger.info(
            "Для реферальной записи {} "
            "установлен процент {}",
            referral_id,
            normalized_percent,
        )

        return referral


# ======================================================================
# Глобальный экземпляр
# ======================================================================


referral_service = ReferralService()


__all__ = [
    "ReferralError",
    "ReferralDisabledError",
    "ReferralUserNotFoundError",
    "ReferralAlreadyExistsError",
    "InvalidReferralError",
    "ReferralInfo",
    "ReferralRewardResult",
    "ReferralService",
    "referral_service",
]