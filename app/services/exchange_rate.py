from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import httpx
from loguru import logger

from app.config import settings
from app.utils.money import to_decimal


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """
    Информация о полученном валютном курсе.
    """

    pair: str
    rate: Decimal
    fetched_at: datetime
    source: str

    @property
    def is_fresh(self) -> bool:
        """
        Проверяет актуальность курса.
        """

        max_age = timedelta(
            seconds=settings.exchange_rate_max_age
        )

        return (
            datetime.now(timezone.utc)
            - self.fetched_at
            <= max_age
        )


class ExchangeRateService:
    """
    Сервис получения курса USD/RUB.

    Важно:
    этот сервис используется для отображения/расчётов
    магазина и не является источником истины
    для суммы уже созданного криптоплатежа.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        """
        Возвращает HTTP-клиент.
        """

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.http_timeout,
            )

        return self._client

    async def get_usd_rub(self) -> ExchangeRate:
        """
        Получает текущий курс USD/RUB.

        При недоступности внешнего API используется
        резервное значение из настроек.
        """

        url = settings.exchange_rate_url

        if not url:
            logger.warning(
                "URL сервиса курса не настроен. "
                "Используется fallback."
            )

            return self._fallback_rate()

        try:
            client = await self._get_client()

            response = await client.get(url)
            response.raise_for_status()

            data: dict[str, Any] = response.json()

            rate = self._extract_rate(data)

            if rate <= 0:
                raise ValueError(
                    "API вернул некорректный курс."
                )

            result = ExchangeRate(
                pair="USD/RUB",
                rate=rate,
                fetched_at=datetime.now(
                    timezone.utc
                ),
                source="external_api",
            )

            logger.info(
                "Получен курс USD/RUB: {}",
                result.rate,
            )

            return result

        except Exception:
            logger.exception(
                "Ошибка получения курса USD/RUB."
            )

            return self._fallback_rate()

    def _extract_rate(
        self,
        data: dict[str, Any],
    ) -> Decimal:
        """
        Пытается извлечь курс из распространённых
        форматов JSON-ответа.
        """

        candidates: list[Any] = [
            data.get("rate"),
            data.get("value"),
            data.get("price"),
        ]

        rates = data.get("rates")

        if isinstance(rates, dict):
            candidates.extend(
                [
                    rates.get("RUB"),
                    rates.get("USD"),
                ]
            )

        for value in candidates:
            if value is None:
                continue

            try:
                decimal_value = to_decimal(value)

                if decimal_value > 0:
                    return decimal_value

            except ValueError:
                continue

        raise ValueError(
            "Не удалось найти курс в ответе API."
        )

    def _fallback_rate(self) -> ExchangeRate:
        """
        Возвращает резервный курс из .env.
        """

        rate = to_decimal(
            settings.exchange_rate_default_usd_rub
        )

        if rate <= 0:
            raise RuntimeError(
                "Fallback USD/RUB должен быть больше нуля."
            )

        return ExchangeRate(
            pair="USD/RUB",
            rate=rate,
            fetched_at=datetime.now(
                timezone.utc
            ),
            source="config_fallback",
        )

    async def close(self) -> None:
        """
        Закрывает HTTP-клиент, если он создан сервисом.
        """

        if (
            self._client is not None
            and self._owns_client
        ):
            await self._client.aclose()
            self._client = None


exchange_rate_service = ExchangeRateService()


__all__ = [
    "ExchangeRate",
    "ExchangeRateService",
    "exchange_rate_service",
]