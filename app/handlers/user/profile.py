from __future__ import annotations

from decimal import Decimal
from typing import Optional

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.user import (
    LanguageCB,
    ReferralCB,
    UserCB,
)
from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    User,
)
from app.keyboards.user import (
    balance_keyboard,
    language_keyboard,
    main_menu_keyboard,
    profile_keyboard,
    referral_keyboard,
)
from app.services.referrals import referral_service

router = Router(name="user_profile")


# ============================================================
# Константы
# ============================================================

HISTORY_PAGE_SIZE = 8


# ============================================================
# Вспомогательные функции
# ============================================================


async def _get_user(
    session: AsyncSession,
    telegram_id: int,
) -> Optional[User]:
    """Возвращает пользователя по Telegram ID."""

    result = await session.execute(
        select(User).where(
            User.telegram_id == telegram_id,
        )
    )

    return result.scalar_one_or_none()


def _money(value: Decimal | int | float | None) -> str:
    """Форматирует сумму в USD."""

    if value is None:
        return "0.00"

    amount = Decimal(str(value))

    return f"{amount:.2f}"


def _transaction_label(
    transaction_type: BalanceTransactionType,
) -> str:
    """Возвращает понятное название операции."""

    labels = {
        BalanceTransactionType.TOPUP: "Пополнение",
        BalanceTransactionType.PURCHASE: "Покупка",
        BalanceTransactionType.REFUND: "Возврат",
        BalanceTransactionType.REFERRAL: "Реферальное начисление",
        BalanceTransactionType.PROMO: "Промокод",
        BalanceTransactionType.ADMIN_CREDIT: "Пополнение администратором",
        BalanceTransactionType.ADMIN_DEBIT: "Списание администратором",
        BalanceTransactionType.ADJUSTMENT: "Корректировка",
    }

    return labels.get(
        transaction_type,
        "Операция",
    )


async def _safe_edit(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
) -> None:
    """Безопасно редактирует сообщение."""

    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def _answer_callback(
    callback: CallbackQuery,
) -> None:
    """Закрывает loader callback."""

    try:
        await callback.answer()
    except Exception:
        pass


# ============================================================
# Профиль
# ============================================================


@router.callback_query(
    UserCB.filter(
        lambda callback: callback.action == "profile"
    )
)
async def profile_callback(
    callback: CallbackQuery,
    callback_data: UserCB,
    session: AsyncSession,
) -> None:
    """Открывает профиль пользователя."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден.\n"
            "Отправьте /start."
        )
        return

    name = (
        user.first_name
        or user.username
        or "пользователь"
    )

    username = (
        f"@{user.username}"
        if user.username
        else "не указан"
    )

    text = (
        "👤 <b>Ваш профиль</b>\n\n"
        f"Имя: <b>{name}</b>\n"
        f"Username: {username}\n"
        f"ID: <code>{user.telegram_id}</code>\n\n"
        f"💰 Баланс: <b>${_money(user.balance_usd)}</b>\n"
        f"💳 Пополнено: <b>${_money(user.total_topup_usd)}</b>\n"
        f"🛒 Потрачено: <b>${_money(user.total_spent_usd)}</b>\n"
        f"📦 Покупок: <b>{user.purchases_count}</b>"
    )

    await _safe_edit(
        callback=callback,
        text=text,
        reply_markup=profile_keyboard(),
    )


# ============================================================
# Баланс
# ============================================================


@router.callback_query(
    UserCB.filter(
        lambda callback: callback.action == "balance"
    )
)
async def balance_callback(
    callback: CallbackQuery,
    callback_data: UserCB,
    session: AsyncSession,
) -> None:
    """Открывает баланс пользователя."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден.\n"
            "Отправьте /start."
        )
        return

    text = (
        "💰 <b>Баланс</b>\n\n"
        f"Текущий баланс: "
        f"<b>${_money(user.balance_usd)}</b>\n\n"
        "Баланс используется автоматически при оформлении "
        "заказа."
    )

    await _safe_edit(
        callback=callback,
        text=text,
        reply_markup=balance_keyboard(),
    )


# ============================================================
# История баланса
# ============================================================


@router.callback_query(
    UserCB.filter(
        lambda callback: callback.action == "balance_history"
    )
)
async def balance_history_callback(
    callback: CallbackQuery,
    callback_data: UserCB,
    session: AsyncSession,
) -> None:
    """Показывает историю операций с балансом."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден."
        )
        return

    page = max(1, callback_data.page)
    offset = (page - 1) * HISTORY_PAGE_SIZE

    total_result = await session.execute(
        select(func.count(BalanceTransaction.id)).where(
            BalanceTransaction.user_id == user.id,
        )
    )

    total = int(total_result.scalar_one() or 0)

    result = await session.execute(
        select(BalanceTransaction)
        .where(
            BalanceTransaction.user_id == user.id,
        )
        .order_by(
            BalanceTransaction.created_at.desc(),
            BalanceTransaction.id.desc(),
        )
        .offset(offset)
        .limit(HISTORY_PAGE_SIZE)
    )

    transactions = list(result.scalars().all())

    if not transactions:
        text = (
            "📜 <b>История операций</b>\n\n"
            "Операций пока нет."
        )

        await _safe_edit(
            callback=callback,
            text=text,
            reply_markup=balance_keyboard(),
        )
        return

    lines = [
        "📜 <b>История операций</b>",
        "",
    ]

    for transaction in transactions:
        amount = Decimal(str(transaction.amount_usd))

        if amount >= 0:
            amount_text = f"+${amount:.2f}"
        else:
            amount_text = f"-${abs(amount):.2f}"

        label = _transaction_label(
            transaction.type,
        )

        date_text = transaction.created_at.strftime(
            "%d.%m.%Y %H:%M"
        )

        lines.append(
            f"• <b>{label}</b>\n"
            f"  {amount_text} → "
            f"${_money(transaction.balance_after_usd)}\n"
            f"  <i>{date_text}</i>"
        )

    total_pages = max(
        1,
        (total + HISTORY_PAGE_SIZE - 1)
        // HISTORY_PAGE_SIZE,
    )

    lines.extend(
        [
            "",
            f"Страница <b>{page}</b> из <b>{total_pages}</b>",
        ]
    )

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()

    if page > 1:
        builder.button(
            text="⬅️",
            callback_data=UserCB(
                action="balance_history",
                page=page - 1,
            ).pack(),
        )

    if page < total_pages:
        builder.button(
            text="➡️",
            callback_data=UserCB(
                action="balance_history",
                page=page + 1,
            ).pack(),
        )

    builder.button(
        text="💰 Баланс",
        callback_data=UserCB(
            action="balance",
        ).pack(),
    )

    builder.adjust(2, 1)

    await _safe_edit(
        callback=callback,
        text="\n".join(lines),
        reply_markup=builder.as_markup(),
    )


# ============================================================
# Язык
# ============================================================


@router.callback_query(
    LanguageCB.filter(
        lambda callback: callback.action == "open"
    )
)
async def language_open_callback(
    callback: CallbackQuery,
    callback_data: LanguageCB,
) -> None:
    """Открывает выбор языка."""

    await _answer_callback(callback)

    await _safe_edit(
        callback=callback,
        text=(
            "🌐 <b>Язык интерфейса</b>\n\n"
            "Выберите язык:"
        ),
        reply_markup=language_keyboard(),
    )


@router.callback_query(
    LanguageCB.filter(
        lambda callback: callback.action == "set"
    )
)
async def language_set_callback(
    callback: CallbackQuery,
    callback_data: LanguageCB,
    session: AsyncSession,
) -> None:
    """Устанавливает язык пользователя."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    language = callback_data.language.strip().lower()

    if language not in {"ru", "en"}:
        await callback.answer(
            "⚠️ Неподдерживаемый язык.",
            show_alert=True,
        )
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден."
        )
        return

    user.language_code = language

    await session.flush()

    language_name = (
        "Русский"
        if language == "ru"
        else "English"
    )

    await _safe_edit(
        callback=callback,
        text=(
            "🌐 <b>Язык изменён</b>\n\n"
            f"Текущий язык: <b>{language_name}</b>"
        ),
        reply_markup=profile_keyboard(),
    )

    logger.info(
        "Пользователь сменил язык: user_id={}, language={}",
        user.id,
        language,
    )


# ============================================================
# Рефералы
# ============================================================


@router.callback_query(
    ReferralCB.filter(
        lambda callback: callback.action == "open"
    )
)
async def referral_open_callback(
    callback: CallbackQuery,
    callback_data: ReferralCB,
    session: AsyncSession,
) -> None:
    """Открывает реферальный раздел."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден."
        )
        return

    count, total_rewards = (
        await referral_service.get_referral_stats(
            session=session,
            referrer_id=user.id,
        )
    )

    if referral_service.enabled:
        status_text = "🟢 Включена"
    else:
        status_text = "🔴 Выключена"

    text = (
        "👥 <b>Реферальная программа</b>\n\n"
        f"Статус: {status_text}\n"
        f"Приглашено: <b>{count}</b>\n"
        f"Начислено: <b>${_money(total_rewards)}</b>\n"
        f"Процент: <b>{referral_service.default_percent}%</b>"
    )

    await _safe_edit(
        callback=callback,
        text=text,
        reply_markup=referral_keyboard(),
    )


@router.callback_query(
    ReferralCB.filter(
        lambda callback: callback.action == "stats"
    )
)
async def referral_stats_callback(
    callback: CallbackQuery,
    callback_data: ReferralCB,
    session: AsyncSession,
) -> None:
    """Показывает статистику рефералов."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден."
        )
        return

    count, total_rewards = (
        await referral_service.get_referral_stats(
            session=session,
            referrer_id=user.id,
        )
    )

    text = (
        "📊 <b>Реферальная статистика</b>\n\n"
        f"👥 Приглашено пользователей: <b>{count}</b>\n"
        f"💰 Получено вознаграждений: "
        f"<b>${_money(total_rewards)}</b>\n"
        f"📈 Процент вознаграждения: "
        f"<b>{referral_service.default_percent}%</b>"
    )

    await _safe_edit(
        callback=callback,
        text=text,
        reply_markup=referral_keyboard(),
    )


@router.callback_query(
    ReferralCB.filter(
        lambda callback: callback.action == "link"
    )
)
async def referral_link_callback(
    callback: CallbackQuery,
    callback_data: ReferralCB,
    session: AsyncSession,
) -> None:
    """Показывает персональную реферальную ссылку."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден."
        )
        return

    if not user.referral_code:
        await callback.answer(
            "⚠️ Реферальный код отсутствует.",
            show_alert=True,
        )
        return

    bot_user = await callback.bot.get_me()

    if not bot_user.username:
        await callback.answer(
            "⚠️ У бота не установлен username.",
            show_alert=True,
        )
        return

    referral_link = (
        f"https://t.me/{bot_user.username}"
        f"?start=ref_{user.referral_code}"
    )

    text = (
        "🔗 <b>Ваша реферальная ссылка</b>\n\n"
        f"<code>{referral_link}</code>\n\n"
        "Отправьте эту ссылку человеку. "
        "После регистрации он будет привязан к вашей "
        "реферальной программе."
    )

    await _safe_edit(
        callback=callback,
        text=text,
        reply_markup=referral_keyboard(),
    )


@router.callback_query(
    ReferralCB.filter(
        lambda callback: callback.action == "history"
    )
)
async def referral_history_callback(
    callback: CallbackQuery,
    callback_data: ReferralCB,
    session: AsyncSession,
) -> None:
    """Показывает историю реферальных начислений."""

    await _answer_callback(callback)

    if callback.from_user is None:
        return

    user = await _get_user(
        session=session,
        telegram_id=callback.from_user.id,
    )

    if user is None:
        await callback.message.answer(
            "⚠️ Пользователь не найден."
        )
        return

    referrals = await referral_service.get_referrals(
        session=session,
        referrer_id=user.id,
        limit=10,
        offset=0,
    )

    if not referrals:
        text = (
            "📜 <b>История начислений</b>\n\n"
            "Начислений пока нет."
        )
    else:
        lines = [
            "📜 <b>История начислений</b>",
            "",
        ]

        for referral in referrals:
            date_text = referral.created_at.strftime(
                "%d.%m.%Y"
            )

            lines.append(
                f"• <b>+${_money(referral.reward_amount_usd)}</b> "
                f"— {referral.reward_percent}%\n"
                f"  {date_text}"
            )

        text = "\n".join(lines)

    await _safe_edit(
        callback=callback,
        text=text,
        reply_markup=referral_keyboard(),
    )


# ============================================================
# Главное меню
# ============================================================


@router.callback_query(
    UserCB.filter(
        lambda callback: callback.action == "home"
    )
)
async def home_callback(
    callback: CallbackQuery,
    callback_data: UserCB,
    session: AsyncSession,
) -> None:
    """Возвращает пользователя в главное меню."""

    await _answer_callback(callback)

    await _safe_edit(
        callback=callback,
        text=(
            "🏠 <b>Главное меню</b>\n\n"
            "Выберите нужный раздел:"
        ),
        reply_markup=main_menu_keyboard(),
    )


# ============================================================
# Поддержка
# ============================================================


@router.callback_query(
    UserCB.filter(
        lambda callback: callback.action == "support"
    )
)
async def support_callback(
    callback: CallbackQuery,
    callback_data: UserCB,
    session: AsyncSession,
) -> None:
    """Показывает раздел поддержки."""

    await _answer_callback(callback)

    from app.config import settings

    support_text = getattr(
        settings,
        "support_text",
        None,
    )

    if not support_text:
        support_text = (
            "🆘 <b>Поддержка</b>\n\n"
            "Если возникла проблема с заказом или оплатой, "
            "обратитесь к администратору."
        )

    await _safe_edit(
        callback=callback,
        text=support_text,
        reply_markup=profile_keyboard(),
    )


# ============================================================
# Экспорт
# ============================================================

__all__ = [
    "router",
]