# app/handlers/user/catalog.py

from __future__ import annotations

from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message

from app.callbacks.catalog import CatalogCB
from app.callbacks.user import UserCB
from app.keyboards.catalog import (
    catalog_categories_keyboard,
    catalog_category_keyboard,
    catalog_product_keyboard,
    catalog_search_keyboard,
)
from app.services.cart import (
    CartServiceError,
    ProductUnavailableError,
    cart_service,
)
from app.services.products import (
    ProductNotFoundError,
    product_service,
)
from app.states.catalog import CatalogStates
from app.utils.money import format_usd

router = Router(name="user_catalog")


# ============================================================
# Вспомогательные функции
# ============================================================


def _product_text(product: Any) -> str:
    """Сформировать карточку товара."""

    name = escape(str(product.name))
    description = escape(
        str(product.description or "Описание отсутствует.")
    )

    price = format_usd(product.price_usd)

    if product.stock > 0:
        stock_text = f"В наличии: {product.stock} шт."
    else:
        stock_text = "Нет в наличии"

    if not product.allow_purchase:
        purchase_text = "Покупка временно недоступна."
    elif product.stock <= 0:
        purchase_text = "Товар закончился."
    else:
        purchase_text = "Можно добавить в корзину."

    return (
        f"<b>{name}</b>\n\n"
        f"{description}\n\n"
        f"💵 Цена: <b>{price}</b>\n"
        f"📦 {stock_text}\n\n"
        f"{purchase_text}"
    )


def _category_text(category: Any) -> str:
    """Сформировать заголовок категории."""

    text = f"📁 <b>{escape(str(category.name))}</b>"

    if category.description:
        text += (
            f"\n\n{escape(str(category.description))}"
        )

    return text


async def _show_categories(
    message: Message,
    session: Any,
) -> None:
    """Показать корневые категории."""

    categories = await product_service.get_categories(
        session,
        active_only=True,
        parent_id=None,
    )

    if not categories:
        await message.answer(
            "📦 Каталог пока пуст."
        )
        return

    await message.answer(
        "🛍 <b>Каталог</b>\n\n"
        "Выберите категорию:",
        reply_markup=catalog_categories_keyboard(
            categories
        ),
    )


async def _show_category(
    message: Message,
    session: Any,
    category_id: int,
    *,
    page: int = 1,
) -> None:
    """Показать содержимое категории."""

    category = await product_service.get_category(
        session,
        category_id,
        active_only=True,
    )

    if category is None:
        await message.answer(
            "❌ Категория не найдена."
        )
        return

    children = await product_service.get_categories(
        session,
        active_only=True,
        parent_id=category_id,
    )

    products = await product_service.get_category_products(
        session,
        category_id,
        active_only=True,
        include_hidden=False,
    )

    await message.answer(
        _category_text(category),
        reply_markup=catalog_category_keyboard(
            category=category,
            children=children,
            products=products,
            page=page,
        ),
    )


async def _show_product(
    message: Message,
    session: Any,
    product_id: int,
) -> None:
    """Показать карточку товара."""

    product = await product_service.get_by_id(
        session,
        product_id,
    )

    if product is None:
        await message.answer(
            "❌ Товар не найден или был удалён."
        )
        return

    if not product.is_active or product.is_hidden:
        await message.answer(
            "❌ Этот товар сейчас недоступен."
        )
        return

    text = _product_text(product)

    keyboard = catalog_product_keyboard(
        product=product,
    )

    photos = await product_service.get_photos(
        session,
        product.id,
    )

    cover = next(
        (
            photo
            for photo in photos
            if photo.is_cover
        ),
        None,
    )

    if cover is None and photos:
        cover = photos[0]

    if cover is not None:
        await message.answer_photo(
            photo=cover.file_id,
            caption=text,
            reply_markup=keyboard,
        )
        return

    await message.answer(
        text,
        reply_markup=keyboard,
    )


# ============================================================
# Вход в каталог
# ============================================================


@router.message(Command("catalog"))
async def catalog_command(
    message: Message,
    session: Any,
    state: FSMContext,
) -> None:
    """Открыть каталог командой /catalog."""

    await state.clear()

    try:
        await _show_categories(
            message,
            session,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия каталога: user_id={}",
            message.from_user.id
            if message.from_user
            else None,
        )

        await message.answer(
            "⚠️ Не удалось открыть каталог. "
            "Попробуйте ещё раз."
        )


# ============================================================
# Каталог из главного меню
# ============================================================


@router.callback_query(
    UserCB.filter(F.action == "catalog")
)
async def catalog_from_menu(
    callback: CallbackQuery,
    session: Any,
    state: FSMContext,
) -> None:
    """Открыть каталог из пользовательского меню."""

    await callback.answer()
    await state.clear()

    if callback.message is None:
        return

    try:
        await _show_categories(
            callback.message,
            session,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия каталога из меню: user_id={}",
            callback.from_user.id,
        )

        await callback.message.answer(
            "⚠️ Не удалось открыть каталог."
        )


# ============================================================
# Список категорий
# ============================================================


@router.callback_query(
    CatalogCB.filter(F.action == "categories")
)
async def catalog_categories(
    callback: CallbackQuery,
    session: Any,
    state: FSMContext,
) -> None:
    """Вернуться к списку категорий."""

    await callback.answer()
    await state.clear()

    if callback.message is None:
        return

    try:
        await _show_categories(
            callback.message,
            session,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка отображения категорий: user_id={}",
            callback.from_user.id,
        )

        await callback.message.answer(
            "⚠️ Не удалось загрузить категории."
        )


# ============================================================
# Открытие категории
# ============================================================


@router.callback_query(
    CatalogCB.filter(F.action == "category")
)
async def catalog_category(
    callback: CallbackQuery,
    callback_data: CatalogCB,
    session: Any,
    state: FSMContext,
) -> None:
    """Открыть категорию."""

    await callback.answer()
    await state.clear()

    if callback.message is None:
        return

    try:
        await _show_category(
            callback.message,
            session,
            callback_data.category_id,
            page=max(callback_data.page, 1),
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия категории: user_id={}, category_id={}",
            callback.from_user.id,
            callback_data.category_id,
        )

        await callback.message.answer(
            "⚠️ Не удалось открыть категорию."
        )


# ============================================================
# Пагинация категории
# ============================================================


@router.callback_query(
    CatalogCB.filter(F.action == "page")
)
async def catalog_page(
    callback: CallbackQuery,
    callback_data: CatalogCB,
    session: Any,
) -> None:
    """Переключить страницу категории."""

    await callback.answer()

    if callback.message is None:
        return

    try:
        await _show_category(
            callback.message,
            session,
            callback_data.category_id,
            page=max(callback_data.page, 1),
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка пагинации каталога: user_id={}, category_id={}, page={}",
            callback.from_user.id,
            callback_data.category_id,
            callback_data.page,
        )

        await callback.message.answer(
            "⚠️ Не удалось загрузить страницу."
        )


# ============================================================
# Открытие товара
# ============================================================


@router.callback_query(
    CatalogCB.filter(F.action == "product")
)
async def catalog_product(
    callback: CallbackQuery,
    callback_data: CatalogCB,
    session: Any,
) -> None:
    """Открыть карточку товара."""

    await callback.answer()

    if callback.message is None:
        return

    try:
        await _show_product(
            callback.message,
            session,
            callback_data.product_id,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка открытия товара: user_id={}, product_id={}",
            callback.from_user.id,
            callback_data.product_id,
        )

        await callback.message.answer(
            "⚠️ Не удалось открыть товар."
        )


# ============================================================
# Добавление товара в корзину
# ============================================================


@router.callback_query(
    CatalogCB.filter(F.action == "add")
)
async def catalog_add_to_cart(
    callback: CallbackQuery,
    callback_data: CatalogCB,
    session: Any,
) -> None:
    """Добавить товар в корзину."""

    if callback.message is None:
        await callback.answer()
        return

    try:
        await cart_service.add_item(
            session,
            user_id=callback.from_user.id,
            product_id=callback_data.product_id,
            quantity=1,
        )

    except ProductUnavailableError:
        await callback.answer(
            "❌ Товар закончился или недоступен.",
            show_alert=True,
        )
        return

    except CartServiceError:
        from loguru import logger

        logger.exception(
            "Ошибка добавления товара в корзину: "
            "user_id={}, product_id={}",
            callback.from_user.id,
            callback_data.product_id,
        )

        await callback.answer(
            "⚠️ Не удалось добавить товар.",
            show_alert=True,
        )
        return

    except Exception:
        from loguru import logger

        logger.exception(
            "Неожиданная ошибка добавления товара: "
            "user_id={}, product_id={}",
            callback.from_user.id,
            callback_data.product_id,
        )

        await callback.answer(
            "⚠️ Произошла ошибка.",
            show_alert=True,
        )
        return

    await callback.answer(
        "✅ Товар добавлен в корзину."
    )


# ============================================================
# Поиск
# ============================================================


@router.callback_query(
    CatalogCB.filter(F.action == "search")
)
async def catalog_search_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """Запросить поисковую фразу."""

    await callback.answer()

    if callback.message is None:
        return

    await state.set_state(
        CatalogStates.searching
    )

    await callback.message.answer(
        "🔎 <b>Поиск товара</b>\n\n"
        "Введите название или часть названия товара:"
    )


@router.message(
    CatalogStates.searching
)
async def catalog_search_message(
    message: Message,
    session: Any,
    state: FSMContext,
) -> None:
    """Выполнить поиск товара."""

    query = (
        (message.text or "")
        .strip()
    )

    if not query:
        await message.answer(
            "❌ Введите поисковый запрос."
        )
        return

    if len(query) > 100:
        await message.answer(
            "❌ Слишком длинный поисковый запрос."
        )
        return

    try:
        products = await product_service.search(
            session,
            query,
            active_only=True,
            include_hidden=False,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка поиска товаров: user_id={}, query={!r}",
            message.from_user.id
            if message.from_user
            else None,
            query,
        )

        await message.answer(
            "⚠️ Не удалось выполнить поиск."
        )
        return

    await state.clear()

    if not products:
        await message.answer(
            "🔎 Ничего не найдено.\n\n"
            "Попробуйте другой запрос.",
            reply_markup=catalog_search_keyboard(),
        )
        return

    await message.answer(
        f"🔎 <b>Результаты поиска</b>\n"
        f"Запрос: <code>{escape(query)}</code>\n\n"
        f"Найдено товаров: <b>{len(products)}</b>",
    )

    for product in products:
        await message.answer(
            _product_text(product),
            reply_markup=catalog_product_keyboard(
                product=product,
            ),
        )


# ============================================================
# Назад
# ============================================================


@router.callback_query(
    CatalogCB.filter(F.action == "back")
)
async def catalog_back(
    callback: CallbackQuery,
    session: Any,
    state: FSMContext,
) -> None:
    """Вернуться к каталогу."""

    await callback.answer()
    await state.clear()

    if callback.message is None:
        return

    try:
        await _show_categories(
            callback.message,
            session,
        )
    except Exception:
        from loguru import logger

        logger.exception(
            "Ошибка возврата в каталог: user_id={}",
            callback.from_user.id,
        )

        await callback.message.answer(
            "⚠️ Не удалось открыть каталог."
        )


# ============================================================
# Экспорт
# ============================================================


__all__ = [
    "router",
]