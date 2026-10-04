from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.callbacks.admin import AdminCB
from app.database.models import Product
from app.database.session import get_session
from app.keyboards.admin import admin_products_keyboard
from app.services.products import ProductService
from app.states.admin import AdminProductStates
from app.utils.money import format_usd
from app.utils.validation import validate_text_length


router = Router(name="admin_products")

product_service = ProductService()


def _is_admin(user_id: int) -> bool:
    """
    Проверяет права администратора.

    Список ID берём только из конфигурации.
    """
    from app.config import settings

    return user_id in settings.admin_ids


def _products_text(products: list[Product]) -> str:
    """
    Формирует список товаров.
    """
    if not products:
        return (
            "📦 <b>Товары</b>\n\n"
            "Товаров пока нет."
        )

    lines = [
        "📦 <b>Товары</b>",
        "",
    ]

    for product in products:
        status_parts: list[str] = []

        if product.active:
            status_parts.append("🟢")
        else:
            status_parts.append("🔴")

        if product.hidden:
            status_parts.append("👁‍🗨")

        stock = product.stock if product.stock is not None else 0

        lines.append(
            f"{''.join(status_parts)} "
            f"<b>#{product.id}</b> {product.name}\n"
            f"   💵 {format_usd(product.price_usd)} | "
            f"📦 остаток: {stock}"
        )

    return "\n".join(lines)


async def _get_products(session: AsyncSession) -> list[Product]:
    """
    Получает товары для административного списка.
    """
    return await product_service.list_products(
        session=session,
        active_only=False,
        include_hidden=True,
    )


async def _show_products(
    message: Message,
    session: AsyncSession,
) -> None:
    """
    Показывает список товаров.
    """
    products = await _get_products(session)

    await message.answer(
        _products_text(products),
        reply_markup=admin_products_keyboard(
            products=products,
        ),
    )


@router.message(Command("products"))
async def products_command(message: Message) -> None:
    """
    Быстрый вход в управление товарами через /products.
    """
    try:
        user_id = message.from_user.id if message.from_user else 0

        if not _is_admin(user_id):
            await message.answer("⛔ Доступ запрещён.")
            return

        async with get_session() as session:
            await _show_products(message, session)

    except Exception:
        logger.exception("Ошибка открытия списка товаров")
        await message.answer(
            "❌ Не удалось открыть список товаров."
        )


@router.callback_query(
    AdminCB.filter(F.action == "products")
)
async def products_callback(
    callback: CallbackQuery,
) -> None:
    """
    Открывает список товаров.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        async with get_session() as session:
            products = await _get_products(session)

        await callback.message.edit_text(
            _products_text(products),
            reply_markup=admin_products_keyboard(
                products=products,
            ),
        )

        await callback.answer()

    except Exception:
        logger.exception("Ошибка отображения товаров")
        await callback.answer(
            "❌ Не удалось загрузить товары.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "product_create")
)
async def product_create_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Начинает создание товара.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        await state.clear()
        await state.set_state(
            AdminProductStates.waiting_for_name
        )

        await callback.message.answer(
            "📦 <b>Создание товара</b>\n\n"
            "Введите название товара:"
        )

        await callback.answer()

    except Exception:
        logger.exception("Ошибка запуска создания товара")
        await callback.answer(
            "❌ Не удалось начать создание товара.",
            show_alert=True,
        )


@router.message(AdminProductStates.waiting_for_name)
async def product_create_name(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Получает название нового товара.
    """
    try:
        if not _is_admin(message.from_user.id):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return

        name = (message.text or "").strip()

        if not name:
            await message.answer(
                "❌ Название не может быть пустым."
            )
            return

        if not validate_text_length(name, 1, 255):
            await message.answer(
                "❌ Название должно содержать от 1 до 255 символов."
            )
            return

        await state.update_data(name=name)
        await state.set_state(
            AdminProductStates.waiting_for_description
        )

        await message.answer(
            "📝 Введите описание товара.\n\n"
            "Если описание не нужно — отправьте <code>-</code>."
        )

    except Exception:
        logger.exception("Ошибка получения названия товара")
        await message.answer(
            "❌ Ошибка обработки названия."
        )


@router.message(AdminProductStates.waiting_for_description)
async def product_create_description(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Получает описание товара.
    """
    try:
        if not _is_admin(message.from_user.id):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return

        description = (message.text or "").strip()

        if description == "-":
            description = ""

        if description and not validate_text_length(
            description,
            1,
            4000,
        ):
            await message.answer(
                "❌ Описание должно содержать не более 4000 символов."
            )
            return

        await state.update_data(
            description=description,
        )

        await state.set_state(
            AdminProductStates.waiting_for_price
        )

        await message.answer(
            "💵 Введите цену товара в USD.\n\n"
            "Например: <code>4.99</code>"
        )

    except Exception:
        logger.exception("Ошибка получения описания товара")
        await message.answer(
            "❌ Ошибка обработки описания."
        )


@router.message(AdminProductStates.waiting_for_price)
async def product_create_price(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Получает цену товара.
    """
    try:
        if not _is_admin(message.from_user.id):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return

        raw_price = (message.text or "").strip().replace(
            ",",
            ".",
        )

        try:
            price = Decimal(raw_price)
        except InvalidOperation:
            await message.answer(
                "❌ Некорректная цена.\n"
                "Введите число, например <code>9.99</code>."
            )
            return

        if price <= 0:
            await message.answer(
                "❌ Цена должна быть больше нуля."
            )
            return

        if price > Decimal("100000000"):
            await message.answer(
                "❌ Цена слишком большая."
            )
            return

        await state.update_data(
            price_usd=price,
        )

        await state.set_state(
            AdminProductStates.waiting_for_stock
        )

        await message.answer(
            "📦 Введите количество товара на складе.\n\n"
            "Для цифрового товара можно указать, например, "
            "<code>100</code>."
        )

    except Exception:
        logger.exception("Ошибка получения цены товара")
        await message.answer(
            "❌ Ошибка обработки цены."
        )


@router.message(AdminProductStates.waiting_for_stock)
async def product_create_stock(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Получает начальный остаток и создаёт товар.
    """
    try:
        if not _is_admin(message.from_user.id):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return

        raw_stock = (message.text or "").strip()

        try:
            stock = int(raw_stock)
        except ValueError:
            await message.answer(
                "❌ Остаток должен быть целым числом."
            )
            return

        if stock < 0:
            await message.answer(
                "❌ Остаток не может быть отрицательным."
            )
            return

        if stock > 1_000_000_000:
            await message.answer(
                "❌ Остаток слишком большой."
            )
            return

        data = await state.get_data()

        async with get_session() as session:
            product = await product_service.create_product(
                session=session,
                name=data["name"],
                description=data.get("description", ""),
                price_usd=data["price_usd"],
                stock=stock,
                category_id=None,
                active=True,
                hidden=False,
            )

            await session.commit()

        await state.clear()

        await message.answer(
            "✅ <b>Товар создан</b>\n\n"
            f"🆔 ID: <code>{product.id}</code>\n"
            f"📦 {product.name}\n"
            f"💵 {format_usd(product.price_usd)}\n"
            f"📊 Остаток: {product.stock}",
        )

    except Exception:
        logger.exception("Ошибка создания товара")
        await state.clear()
        await message.answer(
            "❌ Не удалось создать товар."
        )


@router.callback_query(
    AdminCB.filter(F.action == "product_delete")
)
async def product_delete(
    callback: CallbackQuery,
    callback_data: AdminCB,
) -> None:
    """
    Удаляет товар по ID.

    Удаление будет разрешено только если сервис товаров
    подтверждает отсутствие зависимых записей.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        product_id = _extract_id(callback_data)

        if product_id <= 0:
            await callback.answer(
                "❌ Некорректный ID товара.",
                show_alert=True,
            )
            return

        async with get_session() as session:
            await product_service.delete_product(
                session=session,
                product_id=product_id,
            )
            await session.commit()

        await callback.answer(
            "✅ Товар удалён.",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка удаления товара: id=%s",
            _extract_id(callback_data),
        )

        await callback.answer(
            "❌ Не удалось удалить товар.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "product_toggle")
)
async def product_toggle(
    callback: CallbackQuery,
    callback_data: AdminCB,
) -> None:
    """
    Переключает активность товара.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        product_id = _extract_id(callback_data)

        async with get_session() as session:
            product = await product_service.get_product(
                session=session,
                product_id=product_id,
            )

            if product is None:
                await callback.answer(
                    "❌ Товар не найден.",
                    show_alert=True,
                )
                return

            product.active = not product.active
            await session.commit()

            state_text = (
                "включён"
                if product.active
                else "выключен"
            )

        await callback.answer(
            f"✅ Товар {state_text}.",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка переключения активности товара"
        )
        await callback.answer(
            "❌ Не удалось изменить статус товара.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "product_hide")
)
async def product_hide(
    callback: CallbackQuery,
    callback_data: AdminCB,
) -> None:
    """
    Переключает скрытие товара.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        product_id = _extract_id(callback_data)

        async with get_session() as session:
            product = await product_service.get_product(
                session=session,
                product_id=product_id,
            )

            if product is None:
                await callback.answer(
                    "❌ Товар не найден.",
                    show_alert=True,
                )
                return

            product.hidden = not product.hidden
            await session.commit()

            state_text = (
                "скрыт"
                if product.hidden
                else "показан"
            )

        await callback.answer(
            f"✅ Товар {state_text}.",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка переключения скрытия товара"
        )
        await callback.answer(
            "❌ Не удалось изменить видимость товара.",
            show_alert=True,
        )


def _extract_id(callback_data: AdminCB) -> int:
    """
    Извлекает ID товара из callback_data.

    В проекте callback-схема может использовать разные поля
    для разных административных действий.
    """
    for field_name in (
        "product_id",
        "item_id",
        "object_id",
        "entity_id",
        "id",
    ):
        value = getattr(callback_data, field_name, 0)

        try:
            value_int = int(value)
        except (TypeError, ValueError):
            continue

        if value_int > 0:
            return value_int

    return 0


__all__ = [
    "router",
]