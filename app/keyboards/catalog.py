from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks.catalog import CatalogCB
from app.callbacks.cart import CartCB
from app.callbacks.user import UserCB
from app.database.models import Category, Product


def catalog_categories_keyboard(
    categories: Sequence[Category],
    *,
    page: int = 1,
    has_previous: bool = False,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура списка категорий.

    Для каждого пункта используется ID категории из БД.
    Никаких названий или других пользовательских данных
    в callback не передаём.
    """

    builder = InlineKeyboardBuilder()

    for category in categories:
        builder.button(
            text=category.name,
            callback_data=CatalogCB(
                action="category",
                category_id=category.id,
                page=1,
            ).pack(),
        )

    if has_previous:
        builder.button(
            text="⬅️",
            callback_data=CatalogCB(
                action="page",
                page=max(1, page - 1),
            ).pack(),
        )

    if has_next:
        builder.button(
            text="➡️",
            callback_data=CatalogCB(
                action="page",
                page=page + 1,
            ).pack(),
        )

    builder.button(
        text="🔎 Поиск",
        callback_data=CatalogCB(
            action="search",
        ).pack(),
    )
    builder.button(
        text="🛒 Корзина",
        callback_data=CartCB(
            action="open",
        ).pack(),
    )
    builder.button(
        text="🏠 Главное меню",
        callback_data=UserCB(
            action="home",
        ).pack(),
    )

    if has_previous or has_next:
        builder.adjust(1, 2, 2, 1)
    else:
        builder.adjust(1, 2, 2, 1)

    return builder.as_markup()


def catalog_category_keyboard(
    products: Sequence[Product],
    *,
    category_id: int,
    page: int = 1,
    has_previous: bool = False,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура товаров выбранной категории.
    """

    builder = InlineKeyboardBuilder()

    for product in products:
        stock = int(product.stock or 0)

        if not product.is_active or product.is_hidden:
            continue

        if stock > 0:
            stock_text = f" · {stock} шт."
        else:
            stock_text = " · нет в наличии"

        builder.button(
            text=f"{product.name}{stock_text}",
            callback_data=CatalogCB(
                action="product",
                category_id=category_id,
                product_id=product.id,
                page=page,
            ).pack(),
        )

    navigation_buttons = []

    if has_previous:
        navigation_buttons.append(
            CatalogCB(
                action="page",
                category_id=category_id,
                page=max(1, page - 1),
            ).pack()
        )

    if has_next:
        navigation_buttons.append(
            CatalogCB(
                action="page",
                category_id=category_id,
                page=page + 1,
            ).pack()
        )

    if navigation_buttons:
        if has_previous:
            builder.button(
                text="⬅️",
                callback_data=navigation_buttons[0],
            )

        if has_next:
            callback_index = 1 if has_previous else 0

            builder.button(
                text="➡️",
                callback_data=navigation_buttons[callback_index],
            )

    builder.button(
        text="🔎 Поиск",
        callback_data=CatalogCB(
            action="search",
        ).pack(),
    )
    builder.button(
        text="🛒 Корзина",
        callback_data=CartCB(
            action="open",
        ).pack(),
    )
    builder.button(
        text="⬅️ Категории",
        callback_data=CatalogCB(
            action="categories",
            page=1,
        ).pack(),
    )

    builder.adjust(1, 2, 1, 1, 1)

    return builder.as_markup()


def catalog_product_keyboard(
    product: Product,
    *,
    category_id: int = 0,
    page: int = 1,
    photo_count: int = 0,
) -> InlineKeyboardMarkup:
    """
    Клавиатура карточки товара.
    """

    builder = InlineKeyboardBuilder()

    stock = int(product.stock or 0)

    if product.is_active and not product.is_hidden and stock > 0:
        builder.button(
            text="🛒 Добавить в корзину",
            callback_data=CartCB(
                action="add",
                product_id=product.id,
                quantity=1,
            ).pack(),
        )
    else:
        builder.button(
            text="❌ Нет в наличии",
            callback_data=CatalogCB(
                action="stock",
                product_id=product.id,
                category_id=category_id,
                page=page,
            ).pack(),
        )

    if photo_count > 1:
        builder.button(
            text=f"🖼 Фото · {photo_count}",
            callback_data=CatalogCB(
                action="photos",
                product_id=product.id,
                category_id=category_id,
                page=page,
            ).pack(),
        )

    builder.button(
        text="🛒 Корзина",
        callback_data=CartCB(
            action="open",
        ).pack(),
    )
    builder.button(
        text="⬅️ Назад к товарам",
        callback_data=CatalogCB(
            action="category",
            category_id=category_id,
            page=page,
        ).pack(),
    )

    builder.adjust(1, 2, 1)

    return builder.as_markup()


def catalog_search_keyboard(
    *,
    page: int = 1,
    has_previous: bool = False,
    has_next: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура результатов поиска каталога.
    """

    builder = InlineKeyboardBuilder()

    if has_previous:
        builder.button(
            text="⬅️",
            callback_data=CatalogCB(
                action="search_page",
                page=max(1, page - 1),
            ).pack(),
        )

    if has_next:
        builder.button(
            text="➡️",
            callback_data=CatalogCB(
                action="search_page",
                page=page + 1,
            ).pack(),
        )

    builder.button(
        text="🔎 Новый поиск",
        callback_data=CatalogCB(
            action="search",
        ).pack(),
    )
    builder.button(
        text="📂 Категории",
        callback_data=CatalogCB(
            action="categories",
        ).pack(),
    )
    builder.button(
        text="🛒 Корзина",
        callback_data=CartCB(
            action="open",
        ).pack(),
    )

    builder.adjust(2, 1, 2)

    return builder.as_markup()


__all__ = [
    "catalog_categories_keyboard",
    "catalog_category_keyboard",
    "catalog_product_keyboard",
    "catalog_search_keyboard",
]