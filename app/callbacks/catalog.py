from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class CatalogCB(CallbackData, prefix="cat"):
    """
    CallbackData для каталога.

    Все действия каталога должны проходить через этот класс,
    чтобы не плодить вручную собранные callback-строки.
    """

    action: str
    category_id: int = 0
    product_id: int = 0
    page: int = 1
    query: str = ""

    # Возможные action:
    #
    # categories   — открыть список категорий
    # category     — открыть категорию
    # product      — открыть товар
    # page          — переключить страницу
    # search        — открыть поиск
    # search_page  — страница результатов поиска
    # back          — назад
    # home          — в каталог
    # photos        — просмотр фотографий товара
    # add           — добавить товар в корзину
    # stock         — показать наличие


__all__ = [
    "CatalogCB",
]