from __future__ import annotations

"""
Менеджер платежей магазина.

Отвечает за:
- создание платежей для заказов;
- создание пополнений баланса;
- работу с CryptoPay;
- работу с NOWPayments;
- проверку статуса платежей;
- обработку webhook/IPN;
- идемпотентное зачисление баланса;
- автоматическое истечение неоплаченных платежей.
"""

import functools
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from cachetools import TTLCache
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import config
from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    Order,
    PaymentGateway,
    PaymentInvoice,
    PaymentStatus,
    TopupStatus,
    TopupTransaction,
)
from app.database.session import write_transaction
from app.services.balance import (
    BalanceIdempotencyConflictError,
    BalanceService,
)
from app.services.crypto_pay import (
    CryptoPayError,
    CryptoPayInvoice,
    CryptoPayService,
)
from app.services.nowpayments import (
    NowPaymentsError,
    NowPaymentsPayment,
    NowPaymentsService,
)
from app.services.orders import order_service


# ============================================================================
# Исключения
# ============================================================================


class PaymentManagerError(Exception):
    """Базовая ошибка менеджера платежей."""


class PaymentNotFoundError(PaymentManagerError):
    """Платёж не найден."""


class PaymentAlreadyProcessedError(PaymentManagerError):
    """Платёж уже обработан."""


class PaymentCreationError(PaymentManagerError):
    """Ошибка создания платежа."""


class PaymentWebhookError(PaymentManagerError):
    """Ошибка обработки webhook/IPN."""


class PaymentRateLimitError(PaymentManagerError):
    """Слишком частые запросы на создание invoice."""


class InvalidPaymentAmountError(PaymentManagerError):
    """Некорректная сумма платежа."""


class PaymentGatewayUnavailableError(PaymentManagerError):
    """Платёжный шлюз недоступен."""


# ============================================================================
# Результаты
# ============================================================================


@dataclass(slots=True)
class PaymentCreationResult:
    """Результат создания внешнего платежа."""

    payment_invoice: PaymentInvoice
    external_payment_id: str
    payment_url: str | None
    crypto_amount: Decimal | None
    crypto_currency: str | None
    exchange_rate: Decimal | None = None


@dataclass(slots=True)
class PaymentCheckResult:
    """Результат проверки внешнего платежа."""

    payment_invoice: PaymentInvoice
    status: PaymentStatus
    paid_usd: Decimal = Decimal("0")
    crypto_amount: Decimal | None = None
    crypto_currency: str | None = None
    exchange_rate: Decimal | None = None
    tx_hash: str | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(slots=True)
class PaymentWebhookResult:
    """Результат обработки webhook/IPN."""

    payment_id: int
    status: PaymentStatus
    credited_usd: Decimal
    already_processed: bool = False


# ============================================================================
# Вспомогательные функции
# ============================================================================


def _utcnow() -> datetime:
    """Текущее время UTC."""

    return datetime.now(timezone.utc)


def _decimal(
    value: Any,
    default: Decimal = Decimal("0"),
) -> Decimal:
    """Безопасно преобразует значение в Decimal."""

    if value is None:
        return default

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default

    if not result.is_finite():
        return default

    return result


def _positive_decimal(value: Any) -> Decimal:
    """Преобразует значение и проверяет положительность."""

    result = _decimal(value)

    if result <= 0:
        raise InvalidPaymentAmountError(
            "Сумма платежа должна быть больше нуля."
        )

    return result


@functools.lru_cache(maxsize=8)
def _get_method_params(method: Any) -> frozenset[str]:
    """Кэшированное получение параметров метода."""
    import inspect
    return frozenset(inspect.signature(method).parameters.keys())


def _extract_attr(
    obj: Any,
    *names: str,
) -> Any:
    """Получает первое существующее поле объекта."""

    for name in names:
        value = getattr(obj, name, None)

        if value is not None:
            return value

    return None


def _normalise_asset(
    asset: str | None,
) -> str | None:
    """Нормализует тикер криптовалюты."""

    if not asset:
        return None

    value = str(asset).strip().upper()

    return value or None


def _normalise_external_id(
    value: Any,
) -> str | None:
    """Нормализует внешний ID платежа."""

    if value is None:
        return None

    value = str(value).strip()

    return value or None


# ============================================================================
# PaymentManager
# ============================================================================


class PaymentManager:
    """Центральный менеджер платежей."""

    def __init__(
        self,
        *,
        balance_service: BalanceService | None = None,
        crypto_pay: CryptoPayService | None = None,
        nowpayments: NowPaymentsService | None = None,
    ) -> None:
        self.balance_service = (
            balance_service
            or BalanceService()
        )

        self.crypto_pay = (
            crypto_pay
            or CryptoPayService()
        )

        self.nowpayments = (
            nowpayments
            or NowPaymentsService()
        )

        self._invoice_creation_times: TTLCache = TTLCache(
            maxsize=10_000,
            ttl=60,
        )

    def _check_amount(
        self,
        amount_usd: Any,
    ) -> Decimal:
        """Проверяет сумму платежа."""

        amount = _positive_decimal(amount_usd)

        max_amount = getattr(
            config,
            "max_topup_usd",
            Decimal("5000"),
        )

        if max_amount is not None:
            max_decimal = _decimal(max_amount)

            if max_decimal > 0 and amount > max_decimal:
                raise InvalidPaymentAmountError(
                    f"Сумма превышает максимальное значение "
                    f"${max_decimal:.2f}."
                )

        return amount

    def _check_invoice_rate_limit(
        self,
        user_id: int,
    ) -> None:
        """Не позволяет создавать invoice чаще одного раза за 30 секунд."""

        now = time.monotonic()
        last = self._invoice_creation_times.get(user_id)

        if last is not None:
            elapsed = now - last

            if elapsed < 30:
                remaining = max(
                    1,
                    int(30 - elapsed),
                )

                raise PaymentRateLimitError(
                    f"Повторное создание счёта будет доступно "
                    f"через {remaining} сек."
                )

        self._invoice_creation_times[user_id] = now

    def _gateway_enabled(
        self,
        gateway: PaymentGateway,
    ) -> bool:
        """Проверяет доступность шлюза."""

        if gateway == PaymentGateway.CRYPTOPAY:
            return getattr(
                self.crypto_pay,
                "enabled",
                True,
            )

        if gateway == PaymentGateway.NOWPAYMENTS:
            return getattr(
                self.nowpayments,
                "enabled",
                True,
            )

        return False

    def _ensure_gateway(
        self,
        gateway: PaymentGateway,
    ) -> None:
        """Проверяет, что шлюз включён."""

        if not self._gateway_enabled(gateway):
            raise PaymentGatewayUnavailableError(
                f"Платёжный шлюз {gateway.value} недоступен."
            )

    async def create_order_payment(
        self,
        session: AsyncSession,
        *,
        order: Order,
        asset: str | None = None,
        gateway: PaymentGateway | None = None,
    ) -> PaymentCreationResult:
        """
        Создаёт внешний платёж для заказа.
        Rate limit НЕ применяется — только для topup.
        """

        if order.id is None:
            raise PaymentCreationError(
                "Нельзя создать платёж для заказа без ID."
            )

        amount_usd = _positive_decimal(
            order.total_usd
            - order.balance_paid_usd
            - order.crypto_paid_usd
        )

        return await self._create_payment(
            session,
            user_id=order.user_id,
            amount_usd=amount_usd,
            order_id=order.id,
            topup=None,
            asset=asset,
            gateway=gateway,
            description=(
                f"Оплата заказа #{order.order_number}"
            ),
        )

    async def create_topup_payment(
        self,
        session: AsyncSession,
        *,
        topup: TopupTransaction,
        crypto_currency: str | None = None,
        gateway: PaymentGateway = PaymentGateway.CRYPTOPAY,
    ) -> PaymentCreationResult:
        """Создаёт внешний платёж для пополнения баланса."""

        if topup.id is None:
            raise PaymentCreationError(
                "Нельзя создать платёж для topup без ID."
            )

        if topup.status != TopupStatus.PENDING:
            raise PaymentAlreadyProcessedError(
                "Пополнение уже не находится в состоянии pending."
            )

        amount_usd = self._check_amount(
            topup.amount_usd
        )

        self._check_invoice_rate_limit(
            topup.user_id
        )

        return await self._create_payment(
            session,
            user_id=topup.user_id,
            amount_usd=amount_usd,
            order_id=None,
            topup=topup,
            asset=crypto_currency,
            gateway=gateway,
            description=(
                f"Пополнение баланса #{topup.id}"
            ),
        )

    async def _create_payment(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        amount_usd: Decimal,
        order_id: int | None,
        topup: TopupTransaction | None,
        asset: str | None,
        gateway: PaymentGateway | None,
        description: str,
    ) -> PaymentCreationResult:
        """Создаёт PaymentInvoice через выбранный шлюз."""

        amount_usd = self._check_amount(
            amount_usd
        )

        asset = _normalise_asset(asset)

        if gateway is None:
            gateway = self._select_gateway()

        self._ensure_gateway(gateway)

        if gateway == PaymentGateway.CRYPTOPAY:
            result = await self._create_cryptopay_invoice(
                session,
                user_id=user_id,
                amount_usd=amount_usd,
                order_id=order_id,
                topup=topup,
                asset=asset,
                description=description,
            )
        elif gateway == PaymentGateway.NOWPAYMENTS:
            result = await self._create_nowpayments_invoice(
                session,
                user_id=user_id,
                amount_usd=amount_usd,
                order_id=order_id,
                topup=topup,
                asset=asset,
                description=description,
            )
        else:
            raise PaymentGatewayUnavailableError(
                f"Неизвестный шлюз: {gateway!r}"
            )

        await session.flush()

        return result

    def _select_gateway(self) -> PaymentGateway:
        """Выбирает основной доступный шлюз."""

        if self._gateway_enabled(
            PaymentGateway.CRYPTOPAY
        ):
            return PaymentGateway.CRYPTOPAY

        if self._gateway_enabled(
            PaymentGateway.NOWPAYMENTS
        ):
            return PaymentGateway.NOWPAYMENTS

        raise PaymentGatewayUnavailableError(
            "Нет доступных платёжных шлюзов."
        )

    async def _create_cryptopay_invoice(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        amount_usd: Decimal,
        order_id: int | None,
        topup: TopupTransaction | None,
        asset: str | None,
        description: str,
    ) -> PaymentCreationResult:
        """Создаёт invoice через CryptoPay."""

        try:
            method = self.crypto_pay.create_invoice

            kwargs: dict[str, Any] = {
                "amount": amount_usd,
                "fiat": "USD",
                "currency_type": "fiat",
                "description": description,
            }

            parameters = _get_method_params(method)

            if "asset" in parameters and asset:
                kwargs["asset"] = asset

            if "payload" in parameters:
                kwargs["payload"] = json.dumps(
                    {
                        "user_id": user_id,
                        "order_id": order_id,
                        "topup_id": topup.id if topup is not None else None,
                    },
                    separators=(",", ":"),
                )

            invoice = await method(
                **kwargs
            )

        except CryptoPayError as exc:
            logger.warning(
                "CryptoPay: ошибка создания invoice: {}",
                exc,
            )

            raise PaymentCreationError(
                "CryptoPay не смог создать платёж."
            ) from exc

        except Exception as exc:
            logger.exception(
                "CryptoPay: критическая ошибка создания invoice"
            )

            raise PaymentCreationError(
                "Ошибка создания платежа через CryptoPay."
            ) from exc

        if not isinstance(
            invoice,
            CryptoPayInvoice,
        ):
            raise PaymentCreationError(
                "CryptoPay вернул некорректный ответ."
            )

        external_id = _normalise_external_id(
            invoice.invoice_id
        )

        if not external_id:
            raise PaymentCreationError(
                "CryptoPay не вернул ID invoice."
            )

        payment_url = (
            invoice.bot_invoice_url
            or invoice.mini_app_invoice_url
            or invoice.web_app_invoice_url
        )

        crypto_amount = _decimal(
            invoice.amount,
            default=Decimal("0"),
        )

        if crypto_amount <= 0:
            crypto_amount = None

        crypto_currency = _normalise_asset(
            invoice.asset
            or asset
        )

        raw_response = invoice.raw

        payment_invoice = PaymentInvoice(
            order_id=order_id,
            user_id=user_id,
            gateway=PaymentGateway.CRYPTOPAY,
            external_invoice_id=external_id,
            external_payment_id=external_id,
            status=PaymentStatus.PENDING,
            amount_usd=amount_usd,
            crypto_amount=crypto_amount,
            crypto_currency=crypto_currency,
            exchange_rate=None,
            payment_url=payment_url,
            tx_hash=None,
            raw_response=raw_response,
            expires_at=(
                invoice.expire_at
                or invoice.created_at
                and invoice.created_at + timedelta(minutes=30)
            ),
        )

        session.add(payment_invoice)

        if topup is not None:
            topup.gateway = PaymentGateway.CRYPTOPAY
            topup.external_invoice_id = external_id
            topup.external_payment_id = external_id
            topup.payment_url = payment_url
            topup.crypto_amount = crypto_amount
            topup.crypto_currency = crypto_currency
            topup.expires_at = (
                payment_invoice.expires_at
            )
            topup.raw_response = raw_response

        await session.flush()

        return PaymentCreationResult(
            payment_invoice=payment_invoice,
            external_payment_id=external_id,
            payment_url=payment_url,
            crypto_amount=crypto_amount,
            crypto_currency=crypto_currency,
        )

    async def _create_nowpayments_invoice(
        self,
        session: AsyncSession,
        *,
        user_id: int,
        amount_usd: Decimal,
        order_id: int | None,
        topup: TopupTransaction | None,
        asset: str | None,
        description: str,
    ) -> PaymentCreationResult:
        """Создаёт payment через NOWPayments."""

        if not asset:
            raise PaymentCreationError(
                "Для NOWPayments необходимо указать криптовалюту."
            )

        try:
            method = self.nowpayments.create_payment

            parameters = _get_method_params(method)

            kwargs: dict[str, Any] = {}

            candidates: dict[str, Any] = {
                "price_amount": amount_usd,
                "amount_usd": amount_usd,
                "pay_amount": None,
                "pay_currency": asset,
                "currency": asset,
                "order_id": (
                    str(order_id)
                    if order_id is not None
                    else (
                        f"topup_{topup.id}"
                        if topup is not None
                        else None
                    )
                ),
                "order_description": description,
                "description": description,
            }

            for name, value in candidates.items():
                if name in parameters and value is not None:
                    kwargs[name] = value

            payment = await method(
                **kwargs
            )

        except NowPaymentsError as exc:
            logger.warning(
                "NOWPayments: ошибка создания payment: {}",
                exc,
            )

            raise PaymentCreationError(
                "NOWPayments не смог создать платёж."
            ) from exc

        except Exception as exc:
            logger.exception(
                "NOWPayments: критическая ошибка создания payment"
            )

            raise PaymentCreationError(
                "Ошибка создания платежа через NOWPayments."
            ) from exc

        if not isinstance(
            payment,
            NowPaymentsPayment,
        ):
            raise PaymentCreationError(
                "NOWPayments вернул некорректный ответ."
            )

        external_id = _normalise_external_id(
            _extract_attr(
                payment,
                "payment_id",
                "id",
            )
        )

        if not external_id:
            raise PaymentCreationError(
                "NOWPayments не вернул ID платежа."
            )

        payment_url = _extract_attr(
            payment,
            "invoice_url",
            "payment_url",
        )

        crypto_amount_value = _extract_attr(
            payment,
            "pay_amount",
            "amount",
        )

        crypto_amount = _decimal(
            crypto_amount_value,
            default=Decimal("0"),
        )

        if crypto_amount <= 0:
            crypto_amount = None

        crypto_currency = _normalise_asset(
            _extract_attr(
                payment,
                "pay_currency",
                "currency",
            )
            or asset
        )

        raw_response = _extract_attr(
            payment,
            "raw",
            "raw_response",
        )

        created_at = _extract_attr(
            payment,
            "created_at",
        )

        expires_at = _extract_attr(
            payment,
            "expires_at",
        )

        if expires_at is None and created_at is not None:
            expires_at = (
                created_at
                + timedelta(minutes=30)
            )

        payment_invoice = PaymentInvoice(
            order_id=order_id,
            user_id=user_id,
            gateway=PaymentGateway.NOWPAYMENTS,
            external_invoice_id=external_id,
            external_payment_id=external_id,
            status=PaymentStatus.PENDING,
            amount_usd=amount_usd,
            crypto_amount=crypto_amount,
            crypto_currency=crypto_currency,
            exchange_rate=None,
            payment_url=payment_url,
            tx_hash=None,
            raw_response=raw_response,
            expires_at=expires_at,
        )

        session.add(payment_invoice)

        if topup is not None:
            topup.gateway = PaymentGateway.NOWPAYMENTS
            topup.external_invoice_id = external_id
            topup.external_payment_id = external_id
            topup.payment_url = payment_url
            topup.crypto_amount = crypto_amount
            topup.crypto_currency = crypto_currency
            topup.expires_at = expires_at
            topup.raw_response = raw_response

        await session.flush()

        return PaymentCreationResult(
            payment_invoice=payment_invoice,
            external_payment_id=external_id,
            payment_url=payment_url,
            crypto_amount=crypto_amount,
            crypto_currency=crypto_currency,
        )

    @staticmethod
    def _cryptopay_status(
        invoice: CryptoPayInvoice,
    ) -> PaymentStatus:
        """Преобразует статус CryptoPay в статус БД."""

        status = str(
            invoice.status
            or ""
        ).lower()

        if status == "paid":
            return PaymentStatus.PAID

        if status in {
            "expired",
        }:
            return PaymentStatus.EXPIRED

        if status in {
            "failed",
            "error",
        }:
            return PaymentStatus.FAILED

        return PaymentStatus.PENDING

    @staticmethod
    def _nowpayments_status(
        payment: Any,
    ) -> PaymentStatus:
        """Преобразует статус NOWPayments."""

        status = str(
            _extract_attr(
                payment,
                "payment_status",
                "status",
            )
            or ""
        ).lower()

        if status in {
            "finished",
            "confirmed",
            "complete",
        }:
            return PaymentStatus.PAID

        if status in {
            "expired",
        }:
            return PaymentStatus.EXPIRED

        if status in {
            "failed",
            "refunded",
        }:
            return PaymentStatus.FAILED

        return PaymentStatus.PENDING

    async def _check_cryptopay(
        self,
        session: AsyncSession,
        payment_invoice: PaymentInvoice,
    ) -> PaymentCheckResult:
        """Проверяет CryptoPay invoice."""

        if not payment_invoice.external_invoice_id:
            raise PaymentNotFoundError(
                "У платежа отсутствует внешний ID."
            )

        try:
            invoice = await self.crypto_pay.get_invoice(
                payment_invoice.external_invoice_id
            )

        except CryptoPayError as exc:
            logger.warning(
                "Ошибка проверки CryptoPay invoice {}: {}",
                payment_invoice.id,
                exc,
            )

            raise PaymentManagerError(
                "Не удалось проверить CryptoPay invoice."
            ) from exc

        status = self._cryptopay_status(
            invoice
        )

        paid_usd = Decimal("0")

        if status == PaymentStatus.PAID:
            paid_usd = payment_invoice.amount_usd

        crypto_amount = _decimal(
            invoice.amount,
            default=Decimal("0"),
        )

        if crypto_amount <= 0:
            crypto_amount = None

        crypto_currency = _normalise_asset(
            invoice.asset
        )

        tx_hash = _extract_attr(
            invoice,
            "tx_hash",
            "transaction_hash",
        )

        payment_invoice.last_checked_at = _utcnow()

        payment_invoice.raw_response = invoice.raw

        if crypto_amount is not None:
            payment_invoice.crypto_amount = (
                crypto_amount
            )

        if crypto_currency:
            payment_invoice.crypto_currency = (
                crypto_currency
            )

        if tx_hash:
            payment_invoice.tx_hash = str(
                tx_hash
            )

        if status == PaymentStatus.PAID:
            payment_invoice.status = PaymentStatus.PAID
            payment_invoice.paid_at = (
                payment_invoice.paid_at
                or _utcnow()
            )

        elif status in {
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }:
            payment_invoice.status = status

        await session.flush()

        return PaymentCheckResult(
            payment_invoice=payment_invoice,
            status=status,
            paid_usd=paid_usd,
            crypto_amount=crypto_amount,
            crypto_currency=crypto_currency,
            exchange_rate=(
                payment_invoice.exchange_rate
            ),
            tx_hash=(
                str(tx_hash)
                if tx_hash
                else None
            ),
            raw_response=invoice.raw,
        )

    async def _check_nowpayments(
        self,
        session: AsyncSession,
        payment_invoice: PaymentInvoice,
    ) -> PaymentCheckResult:
        """Проверяет платёж NOWPayments."""

        if not payment_invoice.external_payment_id:
            raise PaymentNotFoundError(
                "У платежа отсутствует внешний ID."
            )

        try:
            payment = await self.nowpayments.get_payment(
                payment_invoice.external_payment_id
            )

        except NowPaymentsError as exc:
            logger.warning(
                "Ошибка проверки NOWPayments payment {}: {}",
                payment_invoice.id,
                exc,
            )

            raise PaymentManagerError(
                "Не удалось проверить NOWPayments payment."
            ) from exc

        status = self._nowpayments_status(
            payment
        )

        paid_usd = Decimal("0")

        if status == PaymentStatus.PAID:
            paid_usd = _decimal(
                _extract_attr(
                    payment,
                    "actually_paid",
                    "paid_amount_usd",
                ),
                default=Decimal("0"),
            )

            if paid_usd <= 0:
                try:
                    paid_usd = await (
                        self.nowpayments.get_paid_amount_usd(
                            payment
                        )
                    )
                except Exception:
                    paid_usd = payment_invoice.amount_usd

            if paid_usd <= 0:
                paid_usd = payment_invoice.amount_usd

        crypto_amount = _decimal(
            _extract_attr(
                payment,
                "actually_paid",
                "pay_amount",
            ),
            default=Decimal("0"),
        )

        if crypto_amount <= 0:
            crypto_amount = None

        crypto_currency = _normalise_asset(
            _extract_attr(
                payment,
                "pay_currency",
                "currency",
            )
        )

        tx_hash = _extract_attr(
            payment,
            "payin_hash",
            "tx_hash",
            "transaction_hash",
        )

        raw_response = _extract_attr(
            payment,
            "raw",
            "raw_response",
        )

        payment_invoice.last_checked_at = _utcnow()

        payment_invoice.raw_response = raw_response

        if crypto_amount is not None:
            payment_invoice.crypto_amount = (
                crypto_amount
            )

        if crypto_currency:
            payment_invoice.crypto_currency = (
                crypto_currency
            )

        if tx_hash:
            payment_invoice.tx_hash = str(
                tx_hash
            )

        if status == PaymentStatus.PAID:
            payment_invoice.status = PaymentStatus.PAID
            payment_invoice.paid_at = (
                payment_invoice.paid_at
                or _utcnow()
            )

        elif status in {
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }:
            payment_invoice.status = status

        await session.flush()

        return PaymentCheckResult(
            payment_invoice=payment_invoice,
            status=status,
            paid_usd=paid_usd,
            crypto_amount=crypto_amount,
            crypto_currency=crypto_currency,
            exchange_rate=(
                payment_invoice.exchange_rate
            ),
            tx_hash=(
                str(tx_hash)
                if tx_hash
                else None
            ),
            raw_response=raw_response,
        )

    async def poll_payment(
        self,
        session: AsyncSession,
        *,
        payment_invoice: PaymentInvoice,
    ) -> PaymentCheckResult:
        """Проверяет внешний платёж."""

        if payment_invoice.status in {
            PaymentStatus.PAID,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
            PaymentStatus.CANCELLED,
        }:
            return PaymentCheckResult(
                payment_invoice=payment_invoice,
                status=payment_invoice.status,
                paid_usd=(
                    payment_invoice.amount_usd
                    if payment_invoice.status
                    == PaymentStatus.PAID
                    else Decimal("0")
                ),
                crypto_amount=(
                    payment_invoice.crypto_amount
                ),
                crypto_currency=(
                    payment_invoice.crypto_currency
                ),
                exchange_rate=(
                    payment_invoice.exchange_rate
                ),
                tx_hash=payment_invoice.tx_hash,
            )

        if payment_invoice.gateway == PaymentGateway.CRYPTOPAY:
            result = await self._check_cryptopay(
                session,
                payment_invoice,
            )

        elif payment_invoice.gateway == PaymentGateway.NOWPAYMENTS:
            result = await self._check_nowpayments(
                session,
                payment_invoice,
            )

        else:
            raise PaymentGatewayUnavailableError(
                "Неизвестный платёжный шлюз."
            )

        if result.status == PaymentStatus.PAID:
            await self.process_successful_payment(
                session,
                payment_invoice=payment_invoice,
                paid_usd=result.paid_usd,
                crypto_amount=result.crypto_amount,
                crypto_currency=result.crypto_currency,
                exchange_rate=result.exchange_rate,
                tx_hash=result.tx_hash,
                raw_response=result.raw_response,
            )

        return result

    async def process_successful_payment(
        self,
        session: AsyncSession,
        *,
        payment_invoice: PaymentInvoice,
        paid_usd: Decimal | None = None,
        crypto_amount: Decimal | None = None,
        crypto_currency: str | None = None,
        exchange_rate: Decimal | None = None,
        tx_hash: str | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> Decimal:
        """
        Финализирует успешный платёж.
        """

        if payment_invoice.status == PaymentStatus.CANCELLED:
            raise PaymentAlreadyProcessedError(
                "Отменённый платёж нельзя провести."
            )

        amount = _decimal(
            paid_usd,
            default=payment_invoice.amount_usd,
        )

        if amount <= 0:
            amount = payment_invoice.amount_usd

        if amount <= 0:
            raise InvalidPaymentAmountError(
                "Сумма успешного платежа равна нулю."
            )

        payment_invoice.status = PaymentStatus.PAID
        payment_invoice.paid_at = (
            payment_invoice.paid_at
            or _utcnow()
        )
        payment_invoice.webhook_processed_at = (
            payment_invoice.webhook_processed_at
            or _utcnow()
        )

        if crypto_amount is not None:
            payment_invoice.crypto_amount = (
                crypto_amount
            )

        if crypto_currency:
            payment_invoice.crypto_currency = (
                _normalise_asset(
                    crypto_currency
                )
            )

        if exchange_rate is not None:
            payment_invoice.exchange_rate = (
                exchange_rate
            )

        if tx_hash:
            payment_invoice.tx_hash = str(
                tx_hash
            )

        if raw_response is not None:
            payment_invoice.raw_response = raw_response

        # ------------------------------------------------------------------
        # TOPUP
        # ------------------------------------------------------------------

        if payment_invoice.order_id is None:
            topup = await self._find_topup_for_invoice(
                session,
                payment_invoice,
            )

            if topup is None:
                raise PaymentNotFoundError(
                    "Для платежа пополнения не найден TopupTransaction."
                )

            if topup.status == TopupStatus.PAID:
                existing_tx = await session.scalar(
                    select(BalanceTransaction).where(
                        BalanceTransaction.idempotency_key
                        == f"topup_payment_{payment_invoice.id}"
                    )
                )
                if existing_tx is not None:
                    logger.info(
                        "Topup {} уже зачислен, возвращаю сумму.",
                        topup.id,
                    )
                    return Decimal(str(existing_tx.amount_usd))
                return Decimal("0")

            if topup.status in {
                TopupStatus.EXPIRED,
                TopupStatus.FAILED,
                TopupStatus.CANCELLED,
            }:
                raise PaymentAlreadyProcessedError(
                    "Пополнение уже завершено неуспешным статусом."
                )

            topup.status = TopupStatus.PAID
            topup.paid_at = (
                topup.paid_at
                or _utcnow()
            )

            if crypto_amount is not None:
                topup.crypto_amount = (
                    crypto_amount
                )

            if crypto_currency:
                topup.crypto_currency = (
                    _normalise_asset(
                        crypto_currency
                    )
                )

            if exchange_rate is not None:
                topup.exchange_rate = (
                    exchange_rate
                )

            if tx_hash:
                topup.tx_hash = str(
                    tx_hash
                )

            if raw_response is not None:
                topup.raw_response = raw_response

            idempotency_key = f"topup_payment_{payment_invoice.id}"

            try:
                await self.balance_service.credit(
                    session,
                    user_id=payment_invoice.user_id,
                    amount_usd=amount,
                    transaction_type=(
                        BalanceTransactionType.TOPUP
                    ),
                    idempotency_key=idempotency_key,
                    topup_id=topup.id,
                    description=(
                        f"Пополнение баланса "
                        f"через {payment_invoice.gateway.value}"
                    ),
                    metadata={
                        "payment_invoice_id": payment_invoice.id,
                        "external_invoice_id": (
                            payment_invoice.external_invoice_id
                        ),
                        "external_payment_id": (
                            payment_invoice.external_payment_id
                        ),
                        "crypto_currency": (
                            payment_invoice.crypto_currency
                        ),
                        "crypto_amount": (
                            str(
                                payment_invoice.crypto_amount
                            )
                            if payment_invoice.crypto_amount
                            is not None
                            else None
                        ),
                        "tx_hash": (
                            payment_invoice.tx_hash
                        ),
                    },
                )

            except BalanceIdempotencyConflictError:
                logger.info(
                    "Повторное зачисление topup {} "
                    "по idempotency key {}.",
                    topup.id,
                    idempotency_key,
                )

            await session.flush()

            logger.info(
                "Пополнение зачислено: "
                "topup_id={}, payment_id={}, "
                "user_id={}, amount_usd={}",
                topup.id,
                payment_invoice.id,
                payment_invoice.user_id,
                amount,
            )

            return amount

        # ------------------------------------------------------------------
        # ORDER
        # ------------------------------------------------------------------

        order = await session.scalar(
            select(Order)
            .where(
                Order.id
                == payment_invoice.order_id
            )
            .with_for_update()
        )

        if order is None:
            raise PaymentNotFoundError(
                f"Заказ {payment_invoice.order_id} не найден."
            )

        if order.user_id != payment_invoice.user_id:
            raise PaymentManagerError(
                f"Заказ {order.id} не принадлежит "
                f"пользователю {payment_invoice.user_id}."
            )

        external_ref = (
            payment_invoice.external_invoice_id
            or payment_invoice.external_payment_id
        )
        if not external_ref:
            raise PaymentManagerError(
                f"У платежа {payment_invoice.id} нет внешнего ID."
            )

        idempotency_key = (
            f"{payment_invoice.gateway.value}:{external_ref}"
        )

        application_result = await order_service.add_crypto_payment(
            session,
            order_id=order.id,
            amount_usd=amount,
            idempotency_key=idempotency_key
        )

        applied_amount = application_result.applied_amount_usd

        logger.info(
            "Платёж заказа учтён: "
            "order_id={}, payment_id={}, "
            "amount_usd={}, applied={}",
            order.id,
            payment_invoice.id,
            amount,
            applied_amount,
        )

        return applied_amount

    async def _find_topup_for_invoice(
        self,
        session: AsyncSession,
        payment_invoice: PaymentInvoice,
    ) -> TopupTransaction | None:
        """Находит пополнение по внешнему ID."""

        if not payment_invoice.external_invoice_id:
            return None

        result = await session.execute(
            select(TopupTransaction)
            .where(
                TopupTransaction.gateway
                == payment_invoice.gateway,
                TopupTransaction.external_invoice_id
                == payment_invoice.external_invoice_id,
            )
            .order_by(
                TopupTransaction.id.desc()
            )
            .limit(1)
            .with_for_update()
        )

        return result.scalar_one_or_none()

    async def _mark_payment_expired(
        self,
        session: AsyncSession,
        payment_invoice: PaymentInvoice,
    ) -> bool:
        """Помечает неоплаченный платёж как expired."""

        if payment_invoice.status != PaymentStatus.PENDING:
            return False

        payment_invoice.status = (
            PaymentStatus.EXPIRED
        )

        if payment_invoice.order_id is not None:
            await order_service.expire(session, order_id=payment_invoice.order_id)
        else:
            topup = await self._find_topup_for_invoice(
                session,
                payment_invoice,
            )

            if (
                topup is not None
                and topup.status
                == TopupStatus.PENDING
            ):
                topup.status = (
                    TopupStatus.EXPIRED
                )

        await session.flush()

        logger.info(
            "Платёж истёк: payment_id={}, gateway={}",
            payment_invoice.id,
            payment_invoice.gateway.value,
        )

        return True

    async def expire_old_payments(
        self,
        session: AsyncSession,
        *,
        limit: int = 500,
    ) -> int:
        """
        Помечает просроченные платежи.
        """

        now = _utcnow()
        count = 0

        async with write_transaction(session):
            result = await session.execute(
                select(PaymentInvoice)
                .where(
                    PaymentInvoice.status
                    == PaymentStatus.PENDING,
                    PaymentInvoice.expires_at.is_not(None),
                    PaymentInvoice.expires_at <= now,
                )
                .order_by(
                    PaymentInvoice.id.asc()
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )

            invoices = list(
                result.scalars().all()
            )

            for invoice in invoices:
                try:
                    if await self._mark_payment_expired(
                        session,
                        invoice,
                    ):
                        count += 1

                except Exception:
                    logger.exception(
                        "Ошибка истечения payment_invoice {}",
                        invoice.id,
                    )

            await session.flush()

        if count:
            logger.info(
                "Истекло платежей: {}",
                count,
            )

        return count

    async def handle_cryptopay_webhook(
        self,
        session: AsyncSession,
        *,
        payload: dict[str, Any],
        signature: str,
    ) -> PaymentWebhookResult:
        """Обрабатывает webhook CryptoPay."""

        try:
            valid = self.crypto_pay.verify_webhook_signature(
                payload,
                signature,
            )
        except Exception as exc:
            logger.exception(
                "Ошибка проверки подписи CryptoPay webhook"
            )

            raise PaymentWebhookError(
                "Не удалось проверить подпись webhook."
            ) from exc

        if not valid:
            raise PaymentWebhookError(
                "Неверная подпись CryptoPay webhook."
            )

        update = payload.get(
            "update",
            payload,
        )

        invoice_data = update.get(
            "payload",
            update,
        )

        external_id = _normalise_external_id(
            _extract_attr(
                invoice_data,
                "invoice_id",
            )
            if not isinstance(
                invoice_data,
                dict,
            )
            else invoice_data.get("invoice_id")
        )

        if not external_id:
            raise PaymentWebhookError(
                "Webhook не содержит invoice_id."
            )

        payment_invoice = await session.scalar(
            select(PaymentInvoice)
            .where(
                PaymentInvoice.gateway
                == PaymentGateway.CRYPTOPAY,
                PaymentInvoice.external_invoice_id
                == external_id,
            )
            .with_for_update()
        )

        if payment_invoice is None:
            raise PaymentNotFoundError(
                f"CryptoPay invoice {external_id} не найден."
            )

        if payment_invoice.status == PaymentStatus.PAID:
            return PaymentWebhookResult(
                payment_id=payment_invoice.id,
                status=PaymentStatus.PAID,
                credited_usd=Decimal("0"),
                already_processed=True,
            )

        invoice = self._invoice_from_webhook_payload(
            invoice_data
        )

        status = self._cryptopay_status(
            invoice
        )

        payment_invoice.webhook_processed_at = (
            _utcnow()
        )

        if status == PaymentStatus.PAID:
            credited = await self.process_successful_payment(
                session,
                payment_invoice=payment_invoice,
                paid_usd=payment_invoice.amount_usd,
                crypto_amount=(
                    _decimal(
                        invoice.amount,
                        default=Decimal("0"),
                    )
                    or None
                ),
                crypto_currency=invoice.asset,
                tx_hash=_extract_attr(
                    invoice,
                    "tx_hash",
                    "transaction_hash",
                ),
                raw_response=invoice.raw,
            )

            return PaymentWebhookResult(
                payment_id=payment_invoice.id,
                status=PaymentStatus.PAID,
                credited_usd=credited,
            )

        if status in {
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }:
            payment_invoice.status = status

        else:
            payment_invoice.status = (
                PaymentStatus.PENDING
            )

        await session.flush()

        return PaymentWebhookResult(
            payment_id=payment_invoice.id,
            status=payment_invoice.status,
            credited_usd=Decimal("0"),
        )

    @staticmethod
    def _invoice_from_webhook_payload(
        payload: dict[str, Any],
    ) -> CryptoPayInvoice:
        """Собирает минимальный CryptoPayInvoice из webhook payload."""

        return CryptoPayInvoice(
            invoice_id=payload.get(
                "invoice_id"
            ),
            hash=payload.get(
                "hash"
            ),
            status=payload.get(
                "status"
            ),
            currency_type=payload.get(
                "currency_type"
            ),
            asset=payload.get(
                "asset"
            ),
            fiat=payload.get(
                "fiat"
            ),
            amount=payload.get(
                "amount"
            ),
            bot_invoice_url=payload.get(
                "bot_invoice_url"
            ),
            mini_app_invoice_url=payload.get(
                "mini_app_invoice_url"
            ),
            web_app_invoice_url=payload.get(
                "web_app_invoice_url"
            ),
            description=payload.get(
                "description"
            ),
            payload=payload.get(
                "payload"
            ),
            created_at=payload.get(
                "created_at"
            ),
            expire_at=payload.get(
                "expire_at"
            ),
            paid_at=payload.get(
                "paid_at"
            ),
            raw=payload,
        )

    async def handle_nowpayments_ipn(
        self,
        session: AsyncSession,
        *,
        payload: dict[str, Any],
        signature: str,
    ) -> PaymentWebhookResult:
        """Обрабатывает NOWPayments IPN."""

        try:
            valid = self.nowpayments.verify_ipn_signature(
                payload,
                signature,
            )
        except Exception as exc:
            logger.exception(
                "Ошибка проверки NOWPayments IPN"
            )

            raise PaymentWebhookError(
                "Не удалось проверить подпись IPN."
            ) from exc

        if not valid:
            raise PaymentWebhookError(
                "Неверная подпись NOWPayments IPN."
            )

        external_id = _normalise_external_id(
            payload.get(
                "payment_id"
            )
        )

        if not external_id:
            raise PaymentWebhookError(
                "IPN не содержит payment_id."
            )

        payment_invoice = await session.scalar(
            select(PaymentInvoice)
            .where(
                PaymentInvoice.gateway
                == PaymentGateway.NOWPAYMENTS,
                PaymentInvoice.external_payment_id
                == external_id,
            )
            .with_for_update()
        )

        if payment_invoice is None:
            raise PaymentNotFoundError(
                f"NOWPayments payment {external_id} не найден."
            )

        if payment_invoice.status == PaymentStatus.PAID:
            return PaymentWebhookResult(
                payment_id=payment_invoice.id,
                status=PaymentStatus.PAID,
                credited_usd=Decimal("0"),
                already_processed=True,
            )

        payment_invoice.webhook_processed_at = (
            _utcnow()
        )

        status = self._nowpayments_status(
            SimpleNamespace(
                payment_status=payload.get("payment_status")
            )
        )

        if status == PaymentStatus.PAID:
            paid_usd = _decimal(
                payload.get(
                    "actually_paid"
                ),
                default=Decimal("0"),
            )

            if paid_usd <= 0:
                paid_usd = payment_invoice.amount_usd

            credited = await self.process_successful_payment(
                session,
                payment_invoice=payment_invoice,
                paid_usd=paid_usd,
                crypto_amount=_decimal(
                    payload.get(
                        "actually_paid"
                    ),
                    default=Decimal("0"),
                ),
                crypto_currency=payload.get(
                    "pay_currency"
                ),
                tx_hash=(
                    payload.get(
                        "payin_hash"
                    )
                    or payload.get(
                        "tx_hash"
                    )
                ),
                raw_response=payload,
            )

            return PaymentWebhookResult(
                payment_id=payment_invoice.id,
                status=PaymentStatus.PAID,
                credited_usd=credited,
            )

        if status in {
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        }:
            payment_invoice.status = status

        else:
            payment_invoice.status = (
                PaymentStatus.PENDING
            )

        payment_invoice.raw_response = payload

        await session.flush()

        return PaymentWebhookResult(
            payment_id=payment_invoice.id,
            status=payment_invoice.status,
            credited_usd=Decimal("0"),
        )

    async def manual_sync(
        self,
        session: AsyncSession,
        *,
        payment_id: int,
    ) -> PaymentCheckResult:
        """Принудительно проверяет конкретный платёж."""

        payment_invoice = await session.scalar(
            select(PaymentInvoice)
            .where(
                PaymentInvoice.id
                == payment_id
            )
        )

        if payment_invoice is None:
            raise PaymentNotFoundError(
                f"Платёж {payment_id} не найден."
            )

        return await self.poll_payment(
            session,
            payment_invoice=payment_invoice,
        )

    async def cancel_payment(
        self,
        session: AsyncSession,
        *,
        payment_id: int,
    ) -> PaymentInvoice | None:
        """Отменяет pending-платёж."""

        async with write_transaction(session):
            payment_invoice = await session.scalar(
                select(PaymentInvoice)
                .where(
                    PaymentInvoice.id
                    == payment_id
                )
                .with_for_update()
            )

            if payment_invoice is None:
                raise PaymentNotFoundError(
                    f"Платёж {payment_id} не найден."
                )

            if payment_invoice.status == PaymentStatus.PAID:
                raise PaymentAlreadyProcessedError(
                    "Оплаченный платёж нельзя отменить."
                )

            p_id = payment_invoice.id

            if payment_invoice.status not in {
                PaymentStatus.EXPIRED,
                PaymentStatus.FAILED,
                PaymentStatus.CANCELLED,
            }:
                payment_invoice.status = (
                    PaymentStatus.CANCELLED
                )

                if payment_invoice.order_id is not None:
                    await order_service.cancel(session, order_id=payment_invoice.order_id)
                else:
                    topup = await self._find_topup_for_invoice(
                        session,
                        payment_invoice,
                    )

                    if (
                        topup is not None
                        and topup.status
                        == TopupStatus.PENDING
                    ):
                        topup.status = (
                            TopupStatus.CANCELLED
                        )

            await session.flush()

        logger.info(
            "Платёж отменён: payment_id={}",
            p_id,
        )

        return await session.get(PaymentInvoice, p_id)


# ============================================================================
# Глобальный экземпляр
# ============================================================================


payment_manager = PaymentManager()


__all__ = [
    "PaymentManager",
    "PaymentCreationResult",
    "PaymentCheckResult",
    "PaymentWebhookResult",
    "PaymentManagerError",
    "PaymentNotFoundError",
    "PaymentAlreadyProcessedError",
    "PaymentCreationError",
    "PaymentWebhookError",
    "PaymentRateLimitError",
    "InvalidPaymentAmountError",
    "PaymentGatewayUnavailableError",
    "payment_manager",
]