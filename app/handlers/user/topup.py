from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.payments import TopupCB
from app.config import settings
from app.database.models import (
    PaymentGateway,
    PaymentInvoice,
    PaymentStatus,
    TopupStatus,
    TopupTransaction,
    User,
)
from app.keyboards.payment import (
    p2p_guide_keyboard,
    topup_asset_keyboard,
    topup_history_keyboard,
    topup_invoice_keyboard,
    topup_keyboard,
    topup_payment_keyboard,
)
from app.services.p2p_guide import p2p_guide_service
from app.services.payment_manager import (
    InvalidPaymentAmountError,
    PaymentGatewayUnavailableError,
    PaymentManagerError,
    payment_manager,
)
from app.states.topup import TopupStates


user_topup = Router(name="user_topup")


# ======================================================================
# Вспомогательные функции
# ======================================================================


async def _get_user(
    session: AsyncSession,
    telegram_id: int,
) -> User | None:
    """Получить пользователя по Telegram ID."""

    result = await session.execute(
        select(User).where(
            User.telegram_id == telegram_id,
        )
    )

    return result.scalar_one_or_none()


def _get_language(user: User | None) -> str:
    """Получить язык пользователя."""

    if user is None:
        return settings.default_language

    language = (
        user.language_code
        or settings.default_language
    ).lower()

    if language.startswith("en"):
        return "en"

    return "ru"


def _parse_amount(
    value: str,
) -> Decimal | None:
    """
    Безопасно разобрать сумму пополнения.

    Внутренний баланс работает в USD.
    Для пользовательского ввода разрешаем максимум
    два знака после десятичного разделителя.
    """

    value = (
        value
        .strip()
        .replace(",", ".")
    )

    if not value:
        return None

    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError):
        return None

    if not amount.is_finite():
        return None

    if amount <= Decimal("0"):
        return None

    if amount.as_tuple().exponent < -2:
        return None

    return amount.quantize(
        Decimal("0.01")
    )


def _format_money(
    amount: Decimal,
) -> str:
    """Форматировать USD."""

    return f"{amount:.2f}"


def _utcnow() -> datetime:
    """Текущее время UTC."""

    return datetime.now(timezone.utc)


def _get_min_topup() -> Decimal:
    """Получить минимальную сумму пополнения."""

    value = getattr(
        settings,
        "min_topup_usd",
        None,
    )

    if value is None:
        value = getattr(
            settings,
            "topup_min_usd",
            None,
        )

    if value is None:
        return Decimal("5.00")

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("5.00")

    if not result.is_finite() or result <= 0:
        return Decimal("5.00")

    return result.quantize(
        Decimal("0.01")
    )


def _get_max_topup() -> Decimal | None:
    """Получить максимальную сумму пополнения."""

    value = getattr(
        settings,
        "max_topup_usd",
        None,
    )

    if value is None:
        value = getattr(
            settings,
            "topup_max_usd",
            None,
        )

    if value is None:
        return None

    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None

    if not result.is_finite() or result <= 0:
        return None

    return result.quantize(
        Decimal("0.01")
    )


def _supported_assets() -> list[str]:
    """Получить список разрешённых криптоактивов."""

    raw = getattr(
        settings,
        "supported_crypto_currencies",
        "",
    )

    if isinstance(raw, str):
        return list(
            dict.fromkeys(
                item.strip().upper()
                for item in raw.split(",")
                if item.strip()
            )
        )

    if isinstance(
        raw,
        (list, tuple, set),
    ):
        return list(
            dict.fromkeys(
                str(item).strip().upper()
                for item in raw
                if str(item).strip()
            )
        )

    return []


def _payment_url_from_result(
    result: Any,
) -> str | None:
    """Получить ссылку оплаты из PaymentCreationResult."""

    value = getattr(
        result,
        "payment_url",
        None,
    )

    if value:
        return str(value)

    payment_invoice = getattr(
        result,
        "payment_invoice",
        None,
    )

    if payment_invoice is not None:
        value = getattr(
            payment_invoice,
            "payment_url",
            None,
        )

        if value:
            return str(value)

    return None


def _plain_error_text(
    value: str,
) -> str:
    """Убрать простые HTML-теги из текста alert."""

    return (
        value
        .replace("<b>", "")
        .replace("</b>", "")
        .replace("<i>", "")
        .replace("</i>", "")
        .replace("<code>", "")
        .replace("</code>", "")
    )


def _payment_status_text(
    status: PaymentStatus | Any,
) -> str:
    """Человекочитаемый статус PaymentInvoice."""

    mapping = {
        PaymentStatus.PENDING: "⏳ Ожидает оплату",
        PaymentStatus.PAID: "✅ Оплачен",
        PaymentStatus.EXPIRED: "⌛ Истёк",
        PaymentStatus.FAILED: "❌ Ошибка",
        PaymentStatus.CANCELLED: "❌ Отменён",
    }

    return mapping.get(
        status,
        str(
            getattr(
                status,
                "value",
                status,
            )
        ),
    )


def _topup_status_text(
    status: TopupStatus | Any,
) -> str:
    """Человекочитаемый статус пополнения."""

    mapping = {
        TopupStatus.PENDING: "⏳ Ожидает",
        TopupStatus.PAID: "✅ Оплачено",
        TopupStatus.EXPIRED: "⌛ Истёк",
        TopupStatus.FAILED: "❌ Ошибка",
        TopupStatus.CANCELLED: "❌ Отменено",
    }

    return mapping.get(
        status,
        str(
            getattr(
                status,
                "value",
                status,
            )
        ),
    )


def _validate_topup_amount(
    amount: Decimal,
) -> str | None:
    """Проверить ограничения суммы."""

    minimum = _get_min_topup()

    if amount < minimum:
        return (
            "Сумма слишком маленькая.\n\n"
            f"Минимальное пополнение: "
            f"<b>${_format_money(minimum)}</b>."
        )

    maximum = _get_max_topup()

    if (
        maximum is not None
        and amount > maximum
    ):
        return (
            "Сумма слишком большая.\n\n"
            f"Максимальное пополнение: "
            f"<b>${_format_money(maximum)}</b>."
        )

    return None


async def _get_payment_invoice_for_topup(
    session: AsyncSession,
    topup: TopupTransaction,
) -> PaymentInvoice | None:
    """
    Найти платёжный invoice пополнения.

    В текущей модели PaymentInvoice нет topup_id,
    поэтому связываем записи через gateway + external_invoice_id.
    """

    if not topup.external_invoice_id:
        return None

    result = await session.execute(
        select(PaymentInvoice)
        .where(
            PaymentInvoice.gateway == topup.gateway,
            PaymentInvoice.external_invoice_id
            == topup.external_invoice_id,
        )
        .order_by(
            PaymentInvoice.id.desc()
        )
        .limit(1)
    )

    return result.scalar_one_or_none()


async def _get_topup_by_id(
    session: AsyncSession,
    *,
    topup_id: int,
    user_id: int,
) -> TopupTransaction | None:
    """Получить пополнение с обязательной проверкой владельца."""

    result = await session.execute(
        select(TopupTransaction)
        .where(
            TopupTransaction.id == topup_id,
            TopupTransaction.user_id == user_id,
        )
    )

    return result.scalar_one_or_none()


# ======================================================================
# Главное меню
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "start")
)
async def topup_start(
    callback: CallbackQuery,
    callback_data: TopupCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Начать пополнение."""

    del callback_data

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    language = _get_language(user)

    await state.clear()

    await state.set_state(
        TopupStates.waiting_for_amount
    )

    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Введите сумму пополнения в USD.\n\n"
        f"Минимальная сумма: "
        f"<b>${_format_money(_get_min_topup())}</b>\n\n"
        "Например: <code>10</code> "
        "или <code>25.50</code>.",
    )

    await callback.answer()


# ======================================================================
# Ввод суммы
# ======================================================================


@user_topup.message(
    TopupStates.waiting_for_amount,
    F.text,
)
async def topup_amount(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Принять сумму пополнения."""

    amount = _parse_amount(
        message.text or ""
    )

    if amount is None:
        await message.answer(
            "❌ Некорректная сумма.\n\n"
            "Введите положительное число "
            "максимум с двумя знаками "
            "после запятой."
        )
        return

    validation_error = _validate_topup_amount(
        amount
    )

    if validation_error:
        await message.answer(
            f"❌ {validation_error}"
        )
        return

    assets = _supported_assets()

    if not assets:
        await state.clear()

        await message.answer(
            "❌ Сейчас не настроены доступные "
            "криптовалюты для пополнения.\n\n"
            "Обратитесь в поддержку."
        )
        return

    user = await _get_user(
        session,
        message.from_user.id,
    )

    language = _get_language(user)

    await state.update_data(
        amount_usd=str(amount),
    )

    await state.set_state(
        TopupStates.waiting_for_asset
    )

    await message.answer(
        f"💵 Сумма пополнения: "
        f"<b>${_format_money(amount)}</b>\n\n"
        "Выберите криптовалюту для оплаты:",
        reply_markup=topup_asset_keyboard(
            assets=assets,
            language=language,
        ),
    )


# ======================================================================
# Выбор актива
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "asset")
)
async def topup_select_asset(
    callback: CallbackQuery,
    callback_data: TopupCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Выбрать криптовалюту."""

    asset = (
        callback_data.asset
        or ""
    ).strip().upper()

    if asset not in _supported_assets():
        await callback.answer(
            "Недоступная криптовалюта.",
            show_alert=True,
        )
        return

    data = await state.get_data()

    amount = _parse_amount(
        str(
            data.get("amount_usd")
            or ""
        )
    )

    if amount is None:
        await state.clear()

        await callback.answer(
            "Сессия пополнения устарела.",
            show_alert=True,
        )
        return

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    language = _get_language(user)

    await state.update_data(
        asset=asset,
    )

    await state.set_state(
        TopupStates.waiting_for_payment
    )

    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        f"Сумма: <b>${_format_money(amount)}</b>\n"
        f"Валюта оплаты: <b>{asset}</b>\n\n"
        "Нажмите кнопку ниже, чтобы "
        "создать платёжный счёт.",
        reply_markup=topup_payment_keyboard(
            language=language,
        ),
    )

    await callback.answer()


# ======================================================================
# Создание счёта
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "create")
)
async def topup_create_invoice(
    callback: CallbackQuery,
    callback_data: TopupCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Создать CryptoPay invoice для пополнения."""

    del callback_data

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    language = _get_language(user)

    data = await state.get_data()

    amount = _parse_amount(
        str(
            data.get("amount_usd")
            or ""
        )
    )

    asset = (
        str(
            data.get("asset")
            or ""
        )
        .strip()
        .upper()
    )

    if amount is None:
        await state.clear()

        await callback.answer(
            "Некорректная сумма пополнения.",
            show_alert=True,
        )
        return

    if asset not in _supported_assets():
        await state.clear()

        await callback.answer(
            "Некорректная криптовалюта.",
            show_alert=True,
        )
        return

    validation_error = _validate_topup_amount(
        amount
    )

    if validation_error:
        await state.clear()

        await callback.answer(
            _plain_error_text(
                validation_error
            ),
            show_alert=True,
        )
        return

    if not settings.has_cryptopay:
        await state.clear()

        await callback.message.edit_text(
            "❌ Crypto Pay сейчас недоступен.\n\n"
            "Обратитесь в поддержку.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    # --------------------------------------------------------------
    # Ищем уже существующее активное пополнение.
    # --------------------------------------------------------------

    existing_result = await session.execute(
        select(TopupTransaction)
        .where(
            TopupTransaction.user_id == user.id,
            TopupTransaction.status == TopupStatus.PENDING,
            TopupTransaction.amount_usd == amount,
            TopupTransaction.crypto_currency == asset,
        )
        .order_by(
            TopupTransaction.id.desc()
        )
        .limit(1)
    )

    existing_topup = (
        existing_result.scalar_one_or_none()
    )

    if existing_topup is not None:
        payment_invoice = (
            await _get_payment_invoice_for_topup(
                session,
                existing_topup,
            )
        )

        if (
            existing_topup.payment_url
            and payment_invoice is not None
        ):
            await state.update_data(
                topup_id=existing_topup.id,
                payment_invoice_id=payment_invoice.id,
                amount_usd=str(amount),
                asset=asset,
            )

            await state.set_state(
                TopupStates.waiting_for_payment
            )

            await callback.message.edit_text(
                "💳 <b>Активный счёт уже существует</b>\n\n"
                f"Сумма: "
                f"<b>${_format_money(amount)}</b>\n"
                f"Валюта: <b>{asset}</b>\n\n"
                "Используйте существующий счёт.",
                reply_markup=topup_invoice_keyboard(
                    topup_id=existing_topup.id,
                    payment_id=payment_invoice.id,
                    payment_url=existing_topup.payment_url,
                    language=language,
                ),
            )

            await callback.answer()
            return

    # --------------------------------------------------------------
    # Создаём внутреннюю запись.
    # --------------------------------------------------------------

    topup = TopupTransaction(
        user_id=user.id,
        gateway=PaymentGateway.CRYPTOPAY,
        external_invoice_id=None,
        external_payment_id=None,
        status=TopupStatus.PENDING,
        amount_usd=amount,
        crypto_amount=None,
        crypto_currency=asset,
        exchange_rate=None,
        tx_hash=None,
        payment_url=None,
        raw_response=None,
        idempotency_key=(
            f"topup_{user.id}_"
            f"{int(_utcnow().timestamp() * 1000)}"
        ),
        expires_at=None,
        paid_at=None,
    )

    session.add(topup)

    try:
        await session.flush()

        creation = (
            await payment_manager.create_topup_payment(
                session,
                topup=topup,
                crypto_currency=asset,
                gateway=PaymentGateway.CRYPTOPAY,
            )
        )

    except (
        PaymentGatewayUnavailableError,
        InvalidPaymentAmountError,
        PaymentManagerError,
    ) as exc:
        logger.warning(
            "Не удалось создать topup payment: "
            "topup_id={}, user_id={}, error={}",
            topup.id,
            user.id,
            exc,
        )

        topup.status = TopupStatus.CANCELLED

        await session.flush()
        await state.clear()

        await callback.message.edit_text(
            "❌ <b>Не удалось создать платёж.</b>\n\n"
            "Попробуйте ещё раз или обратитесь "
            "в поддержку.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    except Exception:
        logger.exception(
            "Критическая ошибка создания topup payment: "
            "topup_id={}, user_id={}",
            topup.id,
            user.id,
        )

        topup.status = TopupStatus.CANCELLED

        await session.flush()
        await state.clear()

        await callback.message.edit_text(
            "❌ <b>Произошла ошибка при создании "
            "платежа.</b>\n\n"
            "Попробуйте ещё раз позже.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    # --------------------------------------------------------------
    # Проверяем результат создания.
    # --------------------------------------------------------------

    payment_url = _payment_url_from_result(
        creation
    )

    if not payment_url:
        logger.error(
            "PaymentManager создал invoice без URL: "
            "topup_id={}, payment_id={}",
            topup.id,
            getattr(
                creation,
                "external_payment_id",
                None,
            ),
        )

        topup.status = TopupStatus.CANCELLED

        await session.flush()
        await state.clear()

        await callback.message.edit_text(
            "❌ <b>Платёж создан некорректно.</b>\n\n"
            "Не получена ссылка на оплату.\n\n"
            "Обратитесь в поддержку.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    # --------------------------------------------------------------
    # Сохраняем данные invoice в topup.
    # --------------------------------------------------------------

    topup.payment_url = payment_url

    creation_crypto_amount = getattr(
        creation,
        "crypto_amount",
        None,
    )

    if creation_crypto_amount is not None:
        topup.crypto_amount = (
            creation_crypto_amount
        )

    topup.crypto_currency = (
        getattr(
            creation,
            "crypto_currency",
            None,
        )
        or asset
    )

    payment_invoice = getattr(
        creation,
        "payment_invoice",
        None,
    )

    if payment_invoice is not None:
        topup.external_invoice_id = (
            payment_invoice.external_invoice_id
        )

        if payment_invoice.expires_at:
            topup.expires_at = (
                payment_invoice.expires_at
            )

        topup.raw_response = (
            payment_invoice.raw_response
        )

    external_payment_id = getattr(
        creation,
        "external_payment_id",
        None,
    )

    if external_payment_id:
        topup.external_payment_id = str(
            external_payment_id
        )

    await session.flush()

    if payment_invoice is None:
        logger.error(
            "PaymentManager вернул результат "
            "без PaymentInvoice: topup_id={}",
            topup.id,
        )

        topup.status = TopupStatus.CANCELLED
        await session.flush()
        await state.clear()

        await callback.message.edit_text(
            "❌ <b>Не удалось сохранить платёж.</b>\n\n"
            "Обратитесь в поддержку.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    await state.update_data(
        topup_id=topup.id,
        payment_invoice_id=payment_invoice.id,
        amount_usd=str(amount),
        asset=asset,
    )

    await state.set_state(
        TopupStates.waiting_for_payment
    )

    logger.info(
        "Создано пополнение: "
        "topup_id={}, payment_invoice_id={}, "
        "user_id={}, amount_usd={}, asset={}",
        topup.id,
        payment_invoice.id,
        user.id,
        amount,
        asset,
    )

    await callback.message.edit_text(
        "💳 <b>Счёт на пополнение создан</b>\n\n"
        f"Сумма: <b>${_format_money(amount)}</b>\n"
        f"Валюта оплаты: <b>{asset}</b>\n\n"
        "Оплатите счёт по кнопке ниже.\n"
        "После оплаты нажмите "
        "«Проверить оплату».\n\n"
        "Баланс будет зачислен после подтверждения "
        "платежа.",
        reply_markup=topup_invoice_keyboard(
            topup_id=topup.id,
            payment_id=payment_invoice.id,
            payment_url=payment_url,
            language=language,
        ),
    )

    await callback.answer()


# ======================================================================
# Проверка оплаты
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "check")
)
async def topup_check_payment(
    callback: CallbackQuery,
    callback_data: TopupCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Проверить состояние внешнего платежа."""

    topup_id = callback_data.topup_id

    if topup_id <= 0:
        data = await state.get_data()

        try:
            topup_id = int(
                data.get("topup_id")
                or 0
            )
        except (TypeError, ValueError):
            topup_id = 0

    if topup_id <= 0:
        await callback.answer(
            "Операция пополнения не найдена.",
            show_alert=True,
        )
        return

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    language = _get_language(user)

    topup = await _get_topup_by_id(
        session,
        topup_id=topup_id,
        user_id=user.id,
    )

    if topup is None:
        await callback.answer(
            "Операция пополнения не найдена.",
            show_alert=True,
        )
        return

    if topup.status == TopupStatus.PAID:
        await state.clear()

        await callback.message.edit_text(
            "✅ <b>Пополнение уже зачислено.</b>\n\n"
            f"Сумма: <b>"
            f"${_format_money(Decimal(str(topup.amount_usd)))}"
            f"</b>\n\n"
            "Баланс успешно пополнен.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer(
            "Пополнение уже зачислено."
        )
        return

    payment_invoice = (
        await _get_payment_invoice_for_topup(
            session,
            topup,
        )
    )

    if payment_invoice is None:
        await callback.answer(
            "Платёж ещё не создан или не найден.",
            show_alert=True,
        )
        return

    await state.set_state(
        TopupStates.checking_payment
    )

    try:
        check_result = (
            await payment_manager.poll_payment(
                session,
                payment_invoice=payment_invoice,
            )
        )

    except Exception:
        logger.exception(
            "Ошибка проверки topup payment: "
            "topup_id={}, payment_invoice_id={}",
            topup.id,
            payment_invoice.id,
        )

        await state.set_state(
            TopupStates.waiting_for_payment
        )

        await callback.answer(
            "Не удалось проверить платёж. "
            "Попробуйте ещё раз через несколько секунд.",
            show_alert=True,
        )
        return

    status = getattr(
        check_result,
        "status",
        None,
    )

    # --------------------------------------------------------------
    # Оплата подтверждена.
    #
    # Само зачисление выполняет PaymentManager.
    # Здесь только обновляем интерфейс пользователя.
    # --------------------------------------------------------------

    if status == PaymentStatus.PAID:
        await session.refresh(topup)

        await state.clear()

        credited_amount = getattr(
            check_result,
            "paid_usd",
            None,
        )

        if credited_amount is None:
            credited_amount = Decimal(
                str(topup.amount_usd)
            )

        await callback.message.edit_text(
            "✅ <b>Пополнение подтверждено!</b>\n\n"
            f"На баланс зачислено: "
            f"<b>${_format_money(Decimal(str(credited_amount)))}</b>\n\n"
            "Спасибо за оплату.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer(
            "Баланс пополнен."
        )
        return

    # --------------------------------------------------------------
    # Истёк.
    # --------------------------------------------------------------

    if status == PaymentStatus.EXPIRED:
        topup.status = TopupStatus.EXPIRED

        await session.flush()
        await state.clear()

        await callback.message.edit_text(
            "⌛ <b>Счёт истёк.</b>\n\n"
            "Создайте новый счёт для пополнения.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer(
            "Счёт истёк."
        )
        return

    # --------------------------------------------------------------
    # Ошибка/отмена.
    # --------------------------------------------------------------

    if status in {
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
    }:
        if status == PaymentStatus.FAILED:
            topup.status = TopupStatus.FAILED
        else:
            topup.status = TopupStatus.CANCELLED

        await session.flush()

        await state.set_state(
            TopupStates.waiting_for_payment
        )

        await callback.message.edit_text(
            "❌ <b>Платёж не подтверждён.</b>\n\n"
            "Если вы уже отправили средства, "
            "не создавайте повторный платёж сразу.\n\n"
            "Проверьте платёж ещё раз или "
            "обратитесь в поддержку.",
            reply_markup=topup_invoice_keyboard(
                topup_id=topup.id,
                payment_id=payment_invoice.id,
                payment_url=topup.payment_url,
                language=language,
            ),
        )

        await callback.answer(
            "Платёж не подтверждён.",
            show_alert=True,
        )
        return

    # --------------------------------------------------------------
    # Pending / неизвестный промежуточный статус.
    # --------------------------------------------------------------

    await state.set_state(
        TopupStates.waiting_for_payment
    )

    await callback.answer(
        "Платёж пока не подтверждён. "
        "Попробуйте проверить ещё раз через "
        "несколько секунд.",
        show_alert=True,
    )


# ======================================================================
# История пополнений
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "history")
)
async def topup_history(
    callback: CallbackQuery,
    callback_data: TopupCB,
    session: AsyncSession,
) -> None:
    """Показать историю пополнений."""

    page = max(
        0,
        callback_data.page,
    )

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    language = _get_language(user)

    per_page = 10
    offset = page * per_page

    result = await session.execute(
        select(TopupTransaction)
        .where(
            TopupTransaction.user_id == user.id,
        )
        .order_by(
            TopupTransaction.id.desc()
        )
        .offset(offset)
        .limit(per_page + 1)
    )

    topups = list(
        result.scalars().all()
    )

    has_previous = page > 0
    has_next = len(topups) > per_page

    if has_next:
        topups = topups[:per_page]

    if not topups:
        await callback.message.edit_text(
            "📭 <b>История пополнений пуста.</b>",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    lines = [
        "💳 <b>История пополнений</b>",
        "",
    ]

    for topup in topups:
        amount = Decimal(
            str(topup.amount_usd)
        )

        asset = (
            topup.crypto_currency
            or "—"
        )

        lines.append(
            f"#{topup.id} — "
            f"<b>${_format_money(amount)}</b> — "
            f"{asset} — "
            f"{_topup_status_text(topup.status)}"
        )

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=topup_history_keyboard(
            page=page,
            has_previous=has_previous,
            has_next=has_next,
            language=language,
        ),
    )

    await callback.answer()


# ======================================================================
# P2P guide
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "guide")
)
async def topup_guide(
    callback: CallbackQuery,
    callback_data: TopupCB,
    session: AsyncSession,
) -> None:
    """Показать шаг инструкции P2P."""

    page = max(
        0,
        callback_data.page,
    )

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    if user is None:
        await callback.answer(
            "Пользователь не найден.",
            show_alert=True,
        )
        return

    language = _get_language(user)

    if not getattr(
        settings,
        "p2p_guide_enabled",
        True,
    ):
        await callback.message.edit_text(
            "ℹ️ Инструкция P2P отключена.\n\n"
            "Обратитесь в поддержку.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    items = await p2p_guide_service.list_items(
        session,
        language=language,
        is_active=True,
    )

    if not items:
        await callback.message.edit_text(
            "ℹ️ Инструкция P2P пока не настроена.\n\n"
            "Обратитесь в поддержку.",
            reply_markup=topup_keyboard(
                language=language,
            ),
        )

        await callback.answer()
        return

    index = min(
        page,
        len(items) - 1,
    )

    item = items[index]

    text = (
        getattr(item, "text", None)
        or ""
    )

    if not text:
        text = (
            f"Шаг {index + 1}\n\n"
            "Инструкция пока не заполнена."
        )

    await callback.message.edit_text(
        "📘 <b>Инструкция P2P</b>\n\n"
        f"{text}\n\n"
        f"<i>Шаг {index + 1} "
        f"из {len(items)}</i>",
        reply_markup=p2p_guide_keyboard(
            language=language,
        ),
    )

    await callback.answer()


# ======================================================================
# P2P: завершение инструкции
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "confirm")
)
async def topup_guide_confirm(
    callback: CallbackQuery,
    callback_data: TopupCB,
    state: FSMContext,
) -> None:
    """После инструкции перейти к вводу суммы."""

    del callback_data

    await state.clear()

    await state.set_state(
        TopupStates.waiting_for_amount
    )

    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Введите сумму пополнения в USD.\n\n"
        f"Минимум: "
        f"<b>${_format_money(_get_min_topup())}</b>."
    )

    await callback.answer()


# ======================================================================
# Назад
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "back")
)
async def topup_back(
    callback: CallbackQuery,
    callback_data: TopupCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Вернуться в меню пополнения."""

    del callback_data

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    language = _get_language(user)

    await state.clear()

    await callback.message.edit_text(
        "💳 <b>Пополнение баланса</b>\n\n"
        "Выберите нужное действие:",
        reply_markup=topup_keyboard(
            language=language,
        ),
    )

    await callback.answer()


# ======================================================================
# Поддержка
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "support")
)
async def topup_support(
    callback: CallbackQuery,
    callback_data: TopupCB,
    session: AsyncSession,
) -> None:
    """Показать информацию о поддержке."""

    del callback_data

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    language = _get_language(user)

    support_username = getattr(
        settings,
        "support_username",
        None,
    )

    support_text = getattr(
        settings,
        "support_text",
        None,
    )

    if support_text:
        text = str(support_text)
    else:
        text = (
            "🆘 <b>Поддержка</b>\n\n"
            "Если возникла проблема с пополнением, "
            "обратитесь в поддержку."
        )

    if support_username:
        username = str(
            support_username
        ).lstrip("@")

        text += (
            "\n\nКонтакт: "
            f"@{username}"
        )

    await callback.message.edit_text(
        text,
        reply_markup=topup_keyboard(
            language=language,
        ),
    )

    await callback.answer()


# ======================================================================
# Отмена
# ======================================================================


@user_topup.callback_query(
    TopupCB.filter(F.action == "cancel")
)
async def topup_cancel(
    callback: CallbackQuery,
    callback_data: TopupCB,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Отменить текущий экран пополнения."""

    del callback_data

    user = await _get_user(
        session,
        callback.from_user.id,
    )

    language = _get_language(user)

    await state.clear()

    await callback.message.edit_text(
        "❌ Пополнение отменено.",
        reply_markup=topup_keyboard(
            language=language,
        ),
    )

    await callback.answer()


__all__ = [
    "user_topup",
]