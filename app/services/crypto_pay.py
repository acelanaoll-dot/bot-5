from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import aiohttp
from loguru import logger

from app.config import settings
from app.services.proxy_manager import proxy_manager


class CryptoPayError(Exception):
    """Базовая ошибка Crypto Pay."""


class CryptoPayDisabledError(CryptoPayError):
    """Crypto Pay отключён в конфигурации."""


class CryptoPayAPIError(CryptoPayError):
    """Ошибка ответа API Crypto Pay."""


class CryptoPayRateLimitError(CryptoPayError):
    """Локальное ограничение частоты создания invoice."""


@dataclass(slots=True, frozen=True)
class CryptoPayInvoice:
    """Нормализованная информация о счёте Crypto Pay."""

    invoice_id: int
    hash: str | None
    status: str
    currency_type: str | None
    asset: str | None
    fiat: str | None
    amount: Decimal
    bot_invoice_url: str | None
    mini_app_invoice_url: str | None
    web_app_invoice_url: str | None
    description: str | None
    payload: str | None
    created_at: datetime | None
    expiration_date: datetime | None
    paid_at: datetime | None
    paid_asset: str | None
    paid_amount: Decimal | None
    paid_usd_rate: Decimal | None
    paid_fiat_rate: Decimal | None
    fee_asset: str | None
    fee_amount: Decimal | None
    fee_usd_rate: Decimal | None
    accepted_assets: str | None
    raw: dict[str, Any]


@dataclass(slots=True, frozen=True)
class CryptoPayUser:
    """Информация о приложении/боте Crypto Pay."""

    app_id: int | None
    name: str | None
    payment_processing_bot: str | None
    raw: dict[str, Any]


class CryptoPayService:
    """
    Асинхронный клиент Crypto Pay API.

    Важные принципы:
    - токен берётся только из .env;
    - запросы выполняются через ProxyManager;
    - блокирующих HTTP-запросов нет;
    - ответы API сохраняются в raw;
    - создание invoice ограничивается локальным rate limit;
    - webhook можно проверить через verify_webhook_signature().
    """

    def __init__(self) -> None:
        self._create_invoice_lock = asyncio.Lock()
        self._last_invoice_created_at = 0.0

    # ------------------------------------------------------------------
    # Базовые свойства
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Проверяет, включён ли Crypto Pay."""
        return settings.cryptopay_enabled and settings.has_cryptopay

    @property
    def api_url(self) -> str:
        """Возвращает базовый URL Crypto Pay API."""
        return settings.cryptopay_api_url.rstrip("/")

    def _get_token(self) -> str:
        """Безопасно получает API-токен."""
        if not self.enabled:
            raise CryptoPayDisabledError(
                "Crypto Pay отключён или API-токен не задан."
            )

        token = settings.cryptopay_api_token.get_secret_value().strip()

        if not token:
            raise CryptoPayDisabledError(
                "Crypto Pay API token не задан."
            )

        return token

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
    ) -> dict[str, Any]:
        """
        Выполняет запрос к Crypto Pay API.

        Crypto Pay использует заголовок:
        Crypto-Pay-API-Token.
        """

        token = self._get_token()

        url = f"{self.api_url}/{endpoint.lstrip('/')}"

        headers = {
            "Crypto-Pay-API-Token": token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        proxy_kwargs = proxy_manager.get_httpx_kwargs()

        # ProxyManager хранит настройки в формате httpx.
        # Для aiohttp приводим proxy к строке отдельно.
        proxy = proxy_kwargs.get("proxy")

        timeout = aiohttp.ClientTimeout(
            total=settings.http_timeout,
            connect=min(settings.http_timeout, 15.0),
        )

        logger.debug(
            "Crypto Pay API request: {} {}",
            method.upper(),
            endpoint,
        )

        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
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
                            "Crypto Pay HTTP error: status={}, body={}",
                            response.status,
                            response_text[:1000],
                        )

                        raise CryptoPayAPIError(
                            f"HTTP {response.status}: "
                            f"{response_text[:500]}"
                        )

                    try:
                        data = await response.json(
                            content_type=None,
                        )
                    except (ValueError, TypeError) as exc:
                        logger.error(
                            "Crypto Pay returned invalid JSON: {}",
                            response_text[:1000],
                        )
                        raise CryptoPayAPIError(
                            "Crypto Pay вернул некорректный JSON."
                        ) from exc

                    if not isinstance(data, dict):
                        raise CryptoPayAPIError(
                            "Некорректный формат ответа Crypto Pay."
                        )

                    if data.get("ok") is not True:
                        error_name = data.get(
                            "error",
                            "unknown_error",
                        )
                        error_code = data.get(
                            "error_code",
                            "unknown",
                        )

                        logger.error(
                            "Crypto Pay API error: code={}, error={}",
                            error_code,
                            error_name,
                        )

                        raise CryptoPayAPIError(
                            f"Crypto Pay API error "
                            f"{error_code}: {error_name}"
                        )

                    result = data.get("result")

                    if result is None:
                        return {}

                    if not isinstance(result, dict):
                        raise CryptoPayAPIError(
                            "Поле result имеет неправильный формат."
                        )

                    return result

        except asyncio.TimeoutError as exc:
            logger.error(
                "Timeout при запросе к Crypto Pay: {}",
                endpoint,
            )
            raise CryptoPayAPIError(
                "Таймаут запроса к Crypto Pay."
            ) from exc

        except aiohttp.ClientError as exc:
            logger.exception(
                "Ошибка HTTP клиента Crypto Pay: {}",
                endpoint,
            )
            raise CryptoPayAPIError(
                f"Ошибка соединения с Crypto Pay: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Парсинг
    # ------------------------------------------------------------------

    @staticmethod
    def _decimal(
        value: Any,
        default: Decimal | None = None,
    ) -> Decimal | None:
        """Безопасно преобразует значение в Decimal."""

        if value is None:
            return default

        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return default

    @staticmethod
    def _datetime(value: Any) -> datetime | None:
        """Преобразует ISO datetime из API."""

        if not value:
            return None

        if isinstance(value, datetime):
            return value

        try:
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00"),
            )
        except (ValueError, TypeError):
            logger.warning(
                "Не удалось разобрать дату Crypto Pay: {}",
                value,
            )
            return None

    @classmethod
    def _parse_invoice(
        cls,
        data: dict[str, Any],
    ) -> CryptoPayInvoice:
        """Преобразует ответ API в CryptoPayInvoice."""

        invoice_id_raw = data.get("invoice_id")

        try:
            invoice_id = int(invoice_id_raw)
        except (ValueError, TypeError) as exc:
            raise CryptoPayAPIError(
                "Crypto Pay вернул некорректный invoice_id."
            ) from exc

        amount = cls._decimal(
            data.get("amount"),
            Decimal("0"),
        )

        paid_amount = cls._decimal(
            data.get("paid_amount"),
        )

        return CryptoPayInvoice(
            invoice_id=invoice_id,
            hash=data.get("hash"),
            status=str(data.get("status", "")),
            currency_type=data.get("currency_type"),
            asset=data.get("asset"),
            fiat=data.get("fiat"),
            amount=amount or Decimal("0"),
            bot_invoice_url=data.get("bot_invoice_url"),
            mini_app_invoice_url=data.get("mini_app_invoice_url"),
            web_app_invoice_url=data.get("web_app_invoice_url"),
            description=data.get("description"),
            payload=data.get("payload"),
            created_at=cls._datetime(data.get("created_at")),
            expiration_date=cls._datetime(
                data.get("expiration_date"),
            ),
            paid_at=cls._datetime(data.get("paid_at")),
            paid_asset=data.get("paid_asset"),
            paid_amount=paid_amount,
            paid_usd_rate=cls._decimal(
                data.get("paid_usd_rate"),
            ),
            paid_fiat_rate=cls._decimal(
                data.get("paid_fiat_rate"),
            ),
            fee_asset=data.get("fee_asset"),
            fee_amount=cls._decimal(
                data.get("fee_amount"),
            ),
            fee_usd_rate=cls._decimal(
                data.get("fee_usd_rate"),
            ),
            accepted_assets=data.get("accepted_assets"),
            raw=dict(data),
        )

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    async def get_me(self) -> CryptoPayUser:
        """Проверяет токен и получает информацию о приложении."""

        result = await self._request(
            "GET",
            "getMe",
        )

        app_id: int | None = None

        try:
            if result.get("app_id") is not None:
                app_id = int(result["app_id"])
        except (ValueError, TypeError):
            pass

        return CryptoPayUser(
            app_id=app_id,
            name=result.get("name"),
            payment_processing_bot=result.get(
                "payment_processing_bot",
            ),
            raw=dict(result),
        )

    async def create_invoice(
        self,
        *,
        amount: Decimal | str | float,
        currency_type: str = "fiat",
        fiat: str = "USD",
        asset: str | None = None,
        description: str | None = None,
        hidden_message: str | None = None,
        payload: str | None = None,
        paid_btn_name: str | None = None,
        paid_btn_url: str | None = None,
        allow_comments: bool = False,
        allow_anonymous: bool = False,
        expires_in: int | None = None,
    ) -> CryptoPayInvoice:
        """
        Создаёт invoice.

        Для расчёта заказа в USD по умолчанию используется:
            currency_type="fiat"
            fiat="USD"

        Если нужно выставить конкретный crypto asset:
            currency_type="crypto"
            asset="USDT"

        expires_in передаётся как expiration_in.
        """

        amount_decimal = self._decimal(amount)

        if amount_decimal is None or amount_decimal <= 0:
            raise ValueError(
                "Сумма invoice должна быть больше нуля."
            )

        if currency_type not in {"fiat", "crypto"}:
            raise ValueError(
                "currency_type должен быть 'fiat' или 'crypto'."
            )

        if currency_type == "fiat":
            fiat = fiat.upper().strip()

            if not fiat:
                raise ValueError(
                    "Для fiat invoice необходимо указать fiat."
                )

            asset = None

        if currency_type == "crypto":
            if not asset:
                raise ValueError(
                    "Для crypto invoice необходимо указать asset."
                )

            asset = asset.upper().strip()
            fiat = None

        if description is not None:
            description = str(description).strip()

        if payload is not None:
            payload = str(payload).strip()

        # Crypto Pay имеет собственные ограничения по частоте
        # создания invoice. Дополнительный локальный лимит защищает
        # бота от случайного спама invoice.
        async with self._create_invoice_lock:
            now = time.monotonic()

            elapsed = now - self._last_invoice_created_at
            rate_limit = max(
                1.0,
                float(settings.cryptopay_invoice_rate_limit),
            )

            if elapsed < rate_limit:
                wait_for = rate_limit - elapsed

                logger.warning(
                    "Локальный rate limit Crypto Pay: "
                    "ожидание {:.2f} сек.",
                    wait_for,
                )

                raise CryptoPayRateLimitError(
                    "Слишком частое создание invoice. "
                    f"Повторите через {wait_for:.1f} сек."
                )

            params: dict[str, Any] = {
                "currency_type": currency_type,
                "amount": str(amount_decimal),
                "allow_comments": allow_comments,
                "allow_anonymous": allow_anonymous,
            }

            if currency_type == "fiat":
                params["fiat"] = fiat

            if currency_type == "crypto" and asset:
                params["asset"] = asset

            if description:
                params["description"] = description

            if hidden_message:
                params["hidden_message"] = hidden_message

            if payload:
                params["payload"] = payload

            if paid_btn_name:
                params["paid_btn_name"] = paid_btn_name

            if paid_btn_url:
                params["paid_btn_url"] = paid_btn_url

            if expires_in is not None:
                if expires_in <= 0:
                    raise ValueError(
                        "expires_in должен быть больше нуля."
                    )

                params["expires_in"] = int(expires_in)

            try:
                result = await self._request(
                    "POST",
                    "createInvoice",
                    params=params,
                )

                invoice = self._parse_invoice(result)

                self._last_invoice_created_at = time.monotonic()

                logger.info(
                    "Создан Crypto Pay invoice: "
                    "id={}, amount={}, currency_type={}, "
                    "fiat={}, asset={}",
                    invoice.invoice_id,
                    invoice.amount,
                    invoice.currency_type,
                    invoice.fiat,
                    invoice.asset,
                )

                return invoice

            except Exception:
                logger.exception(
                    "Ошибка создания Crypto Pay invoice."
                )
                raise

    async def get_invoices(
        self,
        *,
        asset: str | None = None,
        fiat: str | None = None,
        invoice_ids: list[int] | None = None,
        status: str | None = None,
        offset: int | None = None,
        count: int | None = None,
    ) -> list[CryptoPayInvoice]:
        """
        Получает список invoice.

        Используется payment_manager для периодического polling.
        """

        params: dict[str, Any] = {}

        if asset:
            params["asset"] = asset.upper().strip()

        if fiat:
            params["fiat"] = fiat.upper().strip()

        if invoice_ids:
            params["invoice_ids"] = ",".join(
                str(invoice_id)
                for invoice_id in invoice_ids
            )

        if status:
            params["status"] = status

        if offset is not None:
            if offset < 0:
                raise ValueError("offset не может быть отрицательным.")

            params["offset"] = offset

        if count is not None:
            if count < 1 or count > 1000:
                raise ValueError(
                    "count должен находиться в диапазоне 1..1000."
                )

            params["count"] = count

        result = await self._request(
            "GET",
            "getInvoices",
            params=params or None,
        )

        items = result.get("items", [])

        if not isinstance(items, list):
            raise CryptoPayAPIError(
                "Crypto Pay вернул некорректный список invoices."
            )

        invoices: list[CryptoPayInvoice] = []

        for item in items:
            if not isinstance(item, dict):
                logger.warning(
                    "Пропущен некорректный invoice из Crypto Pay."
                )
                continue

            try:
                invoices.append(
                    self._parse_invoice(item),
                )
            except Exception:
                logger.exception(
                    "Не удалось разобрать invoice Crypto Pay."
                )

        return invoices

    async def get_invoice(
        self,
        invoice_id: int,
    ) -> CryptoPayInvoice | None:
        """Получает конкретный invoice по ID."""

        if invoice_id <= 0:
            raise ValueError(
                "invoice_id должен быть положительным."
            )

        invoices = await self.get_invoices(
            invoice_ids=[invoice_id],
            count=1,
        )

        if not invoices:
            return None

        return invoices[0]

    async def delete_invoice(
        self,
        invoice_id: int,
    ) -> bool:
        """Удаляет активный invoice."""

        if invoice_id <= 0:
            raise ValueError(
                "invoice_id должен быть положительным."
            )

        await self._request(
            "POST",
            "deleteInvoice",
            params={
                "invoice_id": invoice_id,
            },
        )

        logger.info(
            "Crypto Pay invoice удалён: {}",
            invoice_id,
        )

        return True

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------

    @staticmethod
    def webhook_secret() -> str | None:
        """Возвращает дополнительный webhook secret из настроек."""

        secret = settings.cryptopay_webhook_secret

        if secret is None:
            return None

        value = secret.get_secret_value().strip()

        return value or None

    def verify_webhook_signature(
        self,
        body: bytes,
        signature: str | None,
    ) -> bool:
        """
        Проверяет подпись webhook Crypto Pay.

        Crypto Pay использует HMAC-SHA256.

        Секрет вычисляется из SHA-256 API-токена, после чего
        этим секретом подписывается исходное тело HTTP-запроса.

        Важно:
        проверяем именно исходные bytes тела, а не повторно
        сериализованный JSON.
        """

        if not signature:
            logger.warning(
                "Crypto Pay webhook пришёл без подписи."
            )
            return False

        try:
            token = self._get_token()
        except CryptoPayDisabledError:
            return False

        token_hash = hashlib.sha256(
            token.encode("utf-8"),
        ).digest()

        expected_signature = hmac.new(
            token_hash,
            body,
            hashlib.sha256,
        ).hexdigest()

        received_signature = signature.strip().lower()

        if len(received_signature) != len(expected_signature):
            logger.warning(
                "Некорректная длина Crypto Pay webhook signature."
            )
            return False

        is_valid = hmac.compare_digest(
            expected_signature,
            received_signature,
        )

        if not is_valid:
            logger.warning(
                "Неверная подпись Crypto Pay webhook."
            )

        return is_valid

    def verify_webhook_signature_with_secret(
        self,
        body: bytes,
        signature: str | None,
    ) -> bool:
        """
        Дополнительная проверка через собственный webhook secret.

        Используется как второй уровень защиты, если
        CRYPTOPAY_WEBHOOK_SECRET задан в .env.

        Формат подписи:
            HMAC-SHA256(secret, body)
        """

        secret = self.webhook_secret()

        if not secret or not signature:
            return False

        expected_signature = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        received_signature = signature.strip().lower()

        if len(received_signature) != len(expected_signature):
            return False

        return hmac.compare_digest(
            expected_signature,
            received_signature,
        )

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    @staticmethod
    def is_paid(invoice: CryptoPayInvoice) -> bool:
        """Проверяет успешную оплату invoice."""

        return invoice.status.lower() == "paid"

    @staticmethod
    def is_active(invoice: CryptoPayInvoice) -> bool:
        """Проверяет, что invoice ещё активен."""

        return invoice.status.lower() == "active"

    @staticmethod
    def is_expired(invoice: CryptoPayInvoice) -> bool:
        """Проверяет истечение invoice."""

        status = invoice.status.lower()

        if status in {"expired", "cancelled"}:
            return True

        if invoice.expiration_date is None:
            return False

        current_time = datetime.now(
            invoice.expiration_date.tzinfo,
        )

        return current_time >= invoice.expiration_date

    @staticmethod
    def get_paid_amount_usd(
        invoice: CryptoPayInvoice,
    ) -> Decimal | None:
        """
        Возвращает USD-эквивалент фактически оплаченного invoice.

        Для fiat invoice исходная сумма уже выражена в USD.
        Для crypto invoice используем paid_usd_rate.
        """

        if not CryptoPayService.is_paid(invoice):
            return None

        if invoice.paid_amount is None:
            return None

        if invoice.currency_type == "fiat":
            if invoice.fiat == "USD":
                return invoice.paid_amount

            if invoice.paid_fiat_rate is not None:
                return (
                    invoice.paid_amount
                    * invoice.paid_fiat_rate
                )

        if invoice.paid_usd_rate is not None:
            return (
                invoice.paid_amount
                * invoice.paid_usd_rate
            )

        return None


crypto_pay_service = CryptoPayService()


__all__ = [
    "CryptoPayError",
    "CryptoPayDisabledError",
    "CryptoPayAPIError",
    "CryptoPayRateLimitError",
    "CryptoPayInvoice",
    "CryptoPayUser",
    "CryptoPayService",
    "crypto_pay_service",
]