from __future__ import annotations

from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_DOWN,
    ROUND_HALF_UP,
)


MONEY_QUANT = Decimal("0.01")
CRYPTO_QUANT = Decimal("0.00000001")


def to_decimal(
    value: Decimal | int | float | str,
) -> Decimal:
    """
    Безопасно преобразует значение в Decimal.
    """

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"Некорректное денежное значение: {value!r}"
        ) from exc


def money(
    value: Decimal | int | float | str,
) -> Decimal:
    """
    Нормализует сумму в USD до двух знаков.
    """

    amount = to_decimal(value)

    return amount.quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )


def crypto_amount(
    value: Decimal | int | float | str,
) -> Decimal:
    """
    Нормализует криптовалютную сумму
    до 8 знаков после запятой.
    """

    amount = to_decimal(value)

    return amount.quantize(
        CRYPTO_QUANT,
        rounding=ROUND_DOWN,
    )


def is_positive(
    value: Decimal | int | float | str,
) -> bool:
    return to_decimal(value) > 0


def is_non_negative(
    value: Decimal | int | float | str,
) -> bool:
    return to_decimal(value) >= 0


def format_usd(
    value: Decimal | int | float | str,
) -> str:
    """
    Форматирует USD для пользователя.
    """

    return f"{money(value):.2f} USD"


def format_crypto(
    value: Decimal | int | float | str,
    currency: str,
) -> str:
    """
    Форматирует криптовалютную сумму.
    """

    return f"{crypto_amount(value):f} {currency.upper()}"


def calculate_crypto_amount(
    usd_amount: Decimal | int | float | str,
    rate: Decimal | int | float | str,
) -> Decimal:
    """
    Переводит USD в криптовалюту.

    rate означает:
        сколько единиц криптовалюты приходится
        на 1 USD.
    """

    usd = money(usd_amount)
    crypto_rate = to_decimal(rate)

    if crypto_rate <= 0:
        raise ValueError(
            "Курс криптовалюты должен быть больше нуля."
        )

    return crypto_amount(
        usd * crypto_rate
    )


def calculate_usd_amount(
    crypto_value: Decimal | int | float | str,
    rate: Decimal | int | float | str,
) -> Decimal:
    """
    Переводит криптовалюту в USD.
    """

    crypto = to_decimal(crypto_value)
    crypto_rate = to_decimal(rate)

    if crypto_rate <= 0:
        raise ValueError(
            "Курс криптовалюты должен быть больше нуля."
        )

    return money(
        crypto / crypto_rate
    )


__all__ = [
    "MONEY_QUANT",
    "CRYPTO_QUANT",
    "to_decimal",
    "money",
    "crypto_amount",
    "is_positive",
    "is_non_negative",
    "format_usd",
    "format_crypto",
    "calculate_crypto_amount",
    "calculate_usd_amount",
]