from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp
from loguru import logger

from app.config import settings
from app.services.proxy_manager import proxy_manager


class NowPaymentsError(Exception):
    """Базовая ошибка NOWPayments."""


class NowPaymentsDisabledError(NowPaymentsError):
    """NOWPayments отключён или не настроен."""


class NowPaymentsAPIError(NowPaymentsError):
    """Ошибка API NOWPayments."""


class NowPaymentsRateLimitError(NowPaymentsError):
    """Локальное ограничение частоты запросов."""


@dataclass(slots=True, frozen=True)
class NowPaymentsPayment:
    """Нормализованная информация о платеже NOWPayments."""

    payment_id: str
    payment_status: str
    pay_address: str | None
    payin_extra_id: str | None
    pay_amount: Decimal | None
    pay_currency: str | None
    actually_paid: Decimal | None
    actually_paid_at_fiat: Decimal | None
    price_amount: Decimal | None
    price_currency: str | None
    order_id: str | None
    order_description: str | None
    purchase_id: str | None
    outcome_amount: Decimal | None
    outcome_currency: str | None
    created_at: str | None
    updated_at: str | None
    raw: dict[str, Any]


@dataclass(slots=True, frozen=True)
class NowPaymentsEstimate:
    """Расчёт необходимого количества криптовалюты."""

    amount: Decimal
    currency_from: str
    currency_to: str
    estimated_amount: Decimal | None
    raw: dict[str, Any]


class NowPaymentsService:
    """
    Асинхронный клиент NOWPayments API.

    NOWPayments используется как резервный платёжный шлюз.
    Основной шлюз в проекте — Crypto Pay.

    Все запросы:
    - асинхронные;
    - идут через ProxyManager;
    - используют API-ключ из .env;
    - не содержат захардкоженных секретов.
    """

    def __init__(self) -> None:
        self._create_payment_lock = asyncio.Lock()
        self._last_payment_created_at = 0.0

    # ------------------------------------------------------------------
    # Конфигурация
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Проверяет, включён ли NOWPayments."""

        return (
            settings.nowpayments_enabled
            and settings.has_nowpayments
        )

    @property
    def api_url(self) -> str:
        """Базовый URL NOWPayments API."""

        return settings.nowpayments_api_url.rstrip("/")

    def _get_api_key(self) -> str:
        """Получает API-ключ."""

        if not self.enabled:
            raise NowPaymentsDisabledError(
                "NOWPayments отключён или API-ключ не задан."
            )

        api_key = (
            settings.nowpayments_api_key
            .get_secret_value()
            .strip()
        )

        if not api_key:
            raise NowPaymentsDisabledError(
                "NOWPayments API key не задан."
            )

        return api_key

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Выполняет асинхронный запрос к NOWPayments."""

        api_key = self._get_api_key()

        url = f"{self.api_url}/{endpoint.lstrip('/')}"

        request_headers = {
            "x-api-key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if headers:
            request_headers.update(headers)

        proxy_kwargs = proxy_manager.get_httpx_kwargs()
        proxy = proxy_kwargs.get("proxy")

        timeout = aiohttp.ClientTimeout(
            total=settings.http_timeout,
            connect=min(settings.http_timeout, 15.0),
        )

        logger.debug(
            "NOWPayments API request: {} {}",
            method.upper(),
            endpoint,
        )

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=request_headers,
            ) as session:
                request_kwargs: dict[str, Any] = {
                    "params": params,
                    "json": json_data,
                }

                if proxy:
                    request_kwargs["proxy"] = str(proxy)

                async with session.request(
                    method.upper(),
                    url,
                    **request_kwargs,
                ) as response:
                    response_text = await response.text()

                    if response.status >= 400:
                        logger.error(
                            "NOWPayments HTTP error: "
                            "status={}, body={}",
                            response.status,
                            response_text[:1000],
                        )

                        raise NowPaymentsAPIError(
                            f"HTTP {response.status}: "
                            f"{response_text[:500]}"
                        )

                    try:
                        data = await response.json(
                            content_type=None,
                        )
                    except (ValueError, TypeError) as exc:
                        logger.error(
                            "NOWPayments вернул некорректный JSON: {}",
                            response_text[:1000],
                        )
                        raise NowPaymentsAPIError(
                            "NOWPayments вернул некорректный JSON."
                        ) from exc

                    if not isinstance(data, dict):
                        raise NowPaymentsAPIError(
                            "Некорректный формат ответа NOWPayments."
                        )

                    return data

        except asyncio.TimeoutError as exc:
            logger.error(
                "Timeout NOWPayments: {}",
                endpoint,
            )
            raise NowPaymentsAPIError(
                "Таймаут запроса к NOWPayments."
            ) from exc

        except aiohttp.ClientError as exc:
            logger.exception(
                "Ошибка HTTP клиента NOWPayments."
            )
            raise NowPaymentsAPIError(
                f"Ошибка соединения с NOWPayments: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Преобразование данных
    # ------------------------------------------------------------------

    @staticmethod
    def _decimal(
        value: Any,
        default: Decimal | None = None,
    ) -> Decimal | None:
        """Безопасное преобразование в Decimal."""

        if value is None:
            return default

        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return default

    @classmethod
    def _parse_payment(
        cls,
        data: dict[str, Any],
    ) -> NowPaymentsPayment:
        """Преобразует ответ API в нормализованный объект."""

        payment_id = data.get("payment_id")

        if payment_id is None:
            raise NowPaymentsAPIError(
                "NOWPayments не вернул payment_id."
            )

        return NowPaymentsPayment(
            payment_id=str(payment_id),
            payment_status=str(
                data.get("payment_status", "")
            ),
            pay_address=data.get("pay_address"),
            payin_extra_id=data.get("payin_extra_id"),
            pay_amount=cls._decimal(
                data.get("pay_amount"),
            ),
            pay_currency=data.get("pay_currency"),
            actually_paid=cls._decimal(
                data.get("actually_paid"),
            ),
            actually_paid_at_fiat=cls._decimal(
                data.get("actually_paid_at_fiat"),
            ),
            price_amount=cls._decimal(
                data.get("price_amount"),
            ),
            price_currency=data.get("price_currency"),
            order_id=data.get("order_id"),
            order_description=data.get(
                "order_description",
            ),
            purchase_id=(
                str(data["purchase_id"])
                if data.get("purchase_id") is not None
                else None
            ),
            outcome_amount=cls._decimal(
                data.get("outcome_amount"),
            ),
            outcome_currency=data.get(
                "outcome_currency",
            ),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            raw=dict(data),
        )

    # ------------------------------------------------------------------
    # Служебные методы API
    # ------------------------------------------------------------------

    async def get_status(self) -> dict[str, Any]:
        """
        Проверяет доступность API.

        Используется для healthcheck и диагностики шлюза.
        """

        return await self._request(
            "GET",
            "status",
        )

    async def get_currencies(self) -> list[str]:
        """Получает список поддерживаемых валют."""

        result = await self._request(
            "GET",
            "currencies",
        )

        currencies = result.get("currencies", [])

        if not isinstance(currencies, list):
            raise NowPaymentsAPIError(
                "Некорректный список валют NOWPayments."
            )

        return [
            str(currency).upper()
            for currency in currencies
        ]

    async def get_minimum_payment_amount(
        self,
        *,
        currency_from: str,
        currency_to: str,
    ) -> Decimal | None:
        """Получает минимальную сумму платежа."""

        currency_from = currency_from.upper().strip()
        currency_to = currency_to.upper().strip()

        result = await self._request(
            "GET",
            "min-amount",
            params={
                "currency_from": currency_from,
                "currency_to": currency_to,
            },
        )

        return self._decimal(
            result.get("min_amount"),
        )

    async def estimate_payment(
        self,
        *,
        amount: Decimal | str | float,
        currency_from: str,
        currency_to: str,
    ) -> NowPaymentsEstimate:
        """Получает приблизительный crypto amount."""

        amount_decimal = self._decimal(amount)

        if amount_decimal is None or amount_decimal <= 0:
            raise ValueError(
                "Сумма должна быть больше нуля."
            )

        currency_from = currency_from.upper().strip()
        currency_to = currency_to.upper().strip()

        result = await self._request(
            "GET",
            "estimate",
            params={
                "amount": str(amount_decimal),
                "currency_from": currency_from,
                "currency_to": currency_to,
            },
        )

        return NowPaymentsEstimate(
            amount=amount_decimal,
            currency_from=currency_from,
            currency_to=currency_to,
            estimated_amount=self._decimal(
                result.get("estimated_amount"),
            ),
            raw=dict(result),
        )

    # ------------------------------------------------------------------
    # Создание платежа
    # ------------------------------------------------------------------

    async def create_payment(
        self,
        *,
        price_amount: Decimal | str | float,
        price_currency: str = "usd",
        pay_currency: str,
        order_id: str,
        order_description: str | None = None,
        ipn_callback_url: str | None = None,
    ) -> NowPaymentsPayment:
        """
        Создаёт платёж NOWPayments.

        price_amount — стоимость заказа в USD.
        pay_currency — валюта, которой пользователь платит.
        """

        amount_decimal = self._decimal(price_amount)

        if amount_decimal is None or amount_decimal <= 0:
            raise ValueError(
                "price_amount должна быть больше нуля."
            )

        if not order_id.strip():
            raise ValueError(
                "order_id не может быть пустым."
            )

        pay_currency = pay_currency.upper().strip()
        price_currency = price_currency.lower().strip()

        if not pay_currency:
            raise ValueError(
                "pay_currency не может быть пустым."
            )

        async with self._create_payment_lock:
            now = time.monotonic()

            elapsed = now - self._last_payment_created_at

            # Защита от случайного массового создания платежей.
            # Сам платёжный менеджер дополнительно контролирует
            # idempotency на уровне БД.
            rate_limit = max(
                1.0,
                float(settings.cryptopay_invoice_rate_limit),
            )

            if elapsed < rate_limit:
                wait_for = rate_limit - elapsed

                logger.warning(
                    "NOWPayments rate limit: "
                    "ожидание {:.2f} сек.",
                    wait_for,
                )

                raise NowPaymentsRateLimitError(
                    "Слишком частое создание платежей. "
                    f"Повторите через {wait_for:.1f} сек."
                )

            payload: dict[str, Any] = {
                "price_amount": str(amount_decimal),
                "price_currency": price_currency,
                "pay_currency": pay_currency,
                "order_id": order_id.strip(),
            }

            if order_description:
                payload["order_description"] = (
                    order_description.strip()
                )

            if ipn_callback_url:
                payload["ipn_callback_url"] = (
                    ipn_callback_url.strip()
                )

            try:
                result = await self._request(
                    "POST",
                    "payment",
                    json_data=payload,
                )

                payment = self._parse_payment(result)

                self._last_payment_created_at = time.monotonic()

                logger.info(
                    "Создан NOWPayments payment: "
                    "id={}, order_id={}, amount={} {}",
                    payment.payment_id,
                    payment.order_id,
                    payment.price_amount,
                    payment.price_currency,
                )

                return payment

            except Exception:
                logger.exception(
                    "Ошибка создания NOWPayments payment."
                )
                raise

    # ------------------------------------------------------------------
    # Получение платежа
    # ------------------------------------------------------------------

    async def get_payment(
        self,
        payment_id: str,
    ) -> NowPaymentsPayment:
        """Получает текущий статус платежа."""

        payment_id = str(payment_id).strip()

        if not payment_id:
            raise ValueError(
                "payment_id не может быть пустым."
            )

        result = await self._request(
            "GET",
            f"payment/{payment_id}",
        )

        return self._parse_payment(result)

    async def get_payment_status(
        self,
        payment_id: str,
    ) -> str:
        """Возвращает только статус платежа."""

        payment = await self.get_payment(
            payment_id,
        )

        return payment.payment_status

    # ------------------------------------------------------------------
    # Webhook / IPN
    # ------------------------------------------------------------------

    def verify_ipn_signature(
        self,
        raw_body: bytes,
        signature: str | None,
    ) -> bool:
        """
        Проверяет подпись NOWPayments IPN.

        Важно:
        подпись проверяется на исходном JSON body,
        а не на повторно сериализованном словаре.
        """

        if not signature:
            logger.warning(
                "NOWPayments IPN пришёл без подписи."
            )
            return False

        secret = settings.nowpayments_ipn_secret

        if secret is None:
            logger.error(
                "NOWPayments IPN secret не настроен."
            )
            return False

        secret_value = secret.get_secret_value().strip()

        if not secret_value:
            logger.error(
                "NOWPayments IPN secret пустой."
            )
            return False

        digest = hmac.new(
            secret_value.encode("utf-8"),
            raw_body,
            hashlib.sha512,
        ).hexdigest()

        received = signature.strip().lower()

        if len(received) != len(digest):
            return False

        is_valid = hmac.compare_digest(
            digest,
            received,
        )

        if not is_valid:
            logger.warning(
                "Неверная подпись NOWPayments IPN."
            )

        return is_valid

    @staticmethod
    def parse_ipn_body(
        raw_body: bytes,
    ) -> dict[str, Any]:
        """Безопасно разбирает тело IPN."""

        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise NowPaymentsAPIError(
                "NOWPayments IPN содержит некорректный JSON."
            ) from exc

        if not isinstance(data, dict):
            raise NowPaymentsAPIError(
                "NOWPayments IPN имеет некорректный формат."
            )

        return data

    def parse_ipn_payment(
        self,
        raw_body: bytes,
    ) -> NowPaymentsPayment:
        """Проверяет структуру IPN и возвращает платёж."""

        data = self.parse_ipn_body(
            raw_body,
        )

        return self._parse_payment(data)

    # ------------------------------------------------------------------
    # Статусы
    # ------------------------------------------------------------------

    @staticmethod
    def is_successful_status(
        status: str,
    ) -> bool:
        """Статусы, при которых платёж можно считать оплаченным."""

        return status.lower() in {
            "finished",
        }

    @staticmethod
    def is_waiting_status(
        status: str,
    ) -> bool:
        """Статусы ожидания оплаты."""

        return status.lower() in {
            "waiting",
            "confirming",
            "confirmed",
        }

    @staticmethod
    def is_failed_status(
        status: str,
    ) -> bool:
        """Неуспешные/завершённые отрицательно статусы."""

        return status.lower() in {
            "failed",
            "expired",
            "refunded",
        }

    @staticmethod
    def get_paid_amount_usd(
        payment: NowPaymentsPayment,
    ) -> Decimal | None:
        """
        Возвращает подтверждённую USD-сумму.

        Предпочитаем actually_paid_at_fiat, если шлюз её передал.
        """

        if not NowPaymentsService.is_successful_status(
            payment.payment_status,
        ):
            return None

        if payment.actually_paid_at_fiat is not None:
            return payment.actually_paid_at_fiat

        if (
            payment.price_currency
            and payment.price_currency.lower() == "usd"
            and payment.price_amount is not None
        ):
            return payment.price_amount

        return None


nowpayments_service = NowPaymentsService()


__all__ = [
    "NowPaymentsError",
    "NowPaymentsDisabledError",
    "NowPaymentsAPIError",
    "NowPaymentsRateLimitError",
    "NowPaymentsPayment",
    "NowPaymentsEstimate",
    "NowPaymentsService",
    "nowpayments_service",
]