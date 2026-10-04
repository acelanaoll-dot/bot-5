from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from loguru import logger

from app.callbacks.admin import AdminCB
from app.database.session import get_session
from app.keyboards.admin import admin_categories_keyboard
from app.services.categories import CategoryService
from app.states.admin import AdminCategoryStates
from app.utils.validation import validate_text_length


router = Router(name="admin_categories")

category_service = CategoryService()


def _is_admin(user_id: int) -> bool:
    """
    Проверяет права администратора.
    """
    from app.config import settings

    return user_id in settings.admin_ids


def _categories_text(categories: list) -> str:
    """
    Формирует список категорий для администратора.
    """
    if not categories:
        return (
            "🗂 <b>Категории</b>\n\n"
            "Категорий пока нет."
        )

    lines = [
        "🗂 <b>Категории</b>",
        "",
    ]

    for category in categories:
        status = "🟢" if category.active else "🔴"

        hidden = " 👁‍🗨" if category.hidden else ""

        parent = ""
        if category.parent_id:
            parent = f" | родитель #{category.parent_id}"

        lines.append(
            f"{status}{hidden} "
            f"<b>#{category.id}</b> "
            f"{category.name}"
            f" — порядок: {category.sort_order}"
            f"{parent}"
        )

    return "\n".join(lines)


async def _show_categories(
    message: Message,
) -> None:
    """
    Показывает административный список категорий.
    """
    async with get_session() as session:
        categories = await category_service.list_categories(
            session=session,
            active_only=False,
            include_hidden=True,
        )

    await message.answer(
        _categories_text(categories),
        reply_markup=admin_categories_keyboard(
            categories=categories,
        ),
    )


@router.message(Command("categories"))
async def categories_command(
    message: Message,
) -> None:
    """
    Открывает управление категориями через /categories.
    """
    try:
        if not _is_admin(message.from_user.id):
            await message.answer("⛔ Доступ запрещён.")
            return

        await _show_categories(message)

    except Exception:
        logger.exception(
            "Ошибка открытия административных категорий"
        )
        await message.answer(
            "❌ Не удалось загрузить категории."
        )


@router.callback_query(
    AdminCB.filter(F.action == "categories")
)
async def categories_callback(
    callback: CallbackQuery,
) -> None:
    """
    Открывает список категорий.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        async with get_session() as session:
            categories = await category_service.list_categories(
                session=session,
                active_only=False,
                include_hidden=True,
            )

        await callback.message.edit_text(
            _categories_text(categories),
            reply_markup=admin_categories_keyboard(
                categories=categories,
            ),
        )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка отображения административных категорий"
        )
        await callback.answer(
            "❌ Не удалось загрузить категории.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "category_create")
)
async def category_create_start(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """
    Начинает создание категории.
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
            AdminCategoryStates.waiting_for_name
        )

        await callback.message.answer(
            "🗂 <b>Создание категории</b>\n\n"
            "Введите название категории:"
        )

        await callback.answer()

    except Exception:
        logger.exception(
            "Ошибка запуска создания категории"
        )
        await callback.answer(
            "❌ Не удалось начать создание категории.",
            show_alert=True,
        )


@router.message(
    AdminCategoryStates.waiting_for_name
)
async def category_create_name(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Получает название категории.
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

        if not validate_text_length(
            name,
            1,
            255,
        ):
            await message.answer(
                "❌ Название должно содержать от 1 до 255 символов."
            )
            return

        await state.update_data(name=name)

        await state.set_state(
            AdminCategoryStates.waiting_for_description
        )

        await message.answer(
            "📝 Введите описание категории.\n\n"
            "Если описание не требуется — отправьте <code>-</code>."
        )

    except Exception:
        logger.exception(
            "Ошибка получения названия категории"
        )
        await message.answer(
            "❌ Ошибка обработки названия."
        )


@router.message(
    AdminCategoryStates.waiting_for_description
)
async def category_create_description(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Получает описание категории.
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
            2000,
        ):
            await message.answer(
                "❌ Описание должно содержать не более 2000 символов."
            )
            return

        await state.update_data(
            description=description,
        )

        await state.set_state(
            AdminCategoryStates.waiting_for_sort_order
        )

        await message.answer(
            "🔢 Введите порядок категории.\n\n"
            "Например: <code>10</code>."
        )

    except Exception:
        logger.exception(
            "Ошибка получения описания категории"
        )
        await message.answer(
            "❌ Ошибка обработки описания."
        )


@router.message(
    AdminCategoryStates.waiting_for_sort_order
)
async def category_create_sort_order(
    message: Message,
    state: FSMContext,
) -> None:
    """
    Получает порядок категории и создаёт её.
    """
    try:
        if not _is_admin(message.from_user.id):
            await state.clear()
            await message.answer("⛔ Доступ запрещён.")
            return

        raw_order = (message.text or "").strip()

        try:
            sort_order = int(raw_order)
        except ValueError:
            await message.answer(
                "❌ Порядок должен быть целым числом."
            )
            return

        if sort_order < 0:
            await message.answer(
                "❌ Порядок не может быть отрицательным."
            )
            return

        if sort_order > 1_000_000:
            await message.answer(
                "❌ Слишком большое значение порядка."
            )
            return

        data = await state.get_data()

        async with get_session() as session:
            category = await category_service.create_category(
                session=session,
                name=data["name"],
                description=data.get(
                    "description",
                    "",
                ),
                parent_id=None,
                sort_order=sort_order,
                active=True,
                hidden=False,
            )

            await session.commit()

        await state.clear()

        await message.answer(
            "✅ <b>Категория создана</b>\n\n"
            f"🆔 ID: <code>{category.id}</code>\n"
            f"🗂 {category.name}\n"
            f"🔢 Порядок: {category.sort_order}",
        )

    except Exception:
        logger.exception(
            "Ошибка создания категории"
        )
        await state.clear()
        await message.answer(
            "❌ Не удалось создать категорию."
        )


@router.callback_query(
    AdminCB.filter(F.action == "category_toggle")
)
async def category_toggle(
    callback: CallbackQuery,
    callback_data: AdminCB,
) -> None:
    """
    Включает/выключает категорию.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        category_id = _extract_category_id(
            callback_data
        )

        if category_id <= 0:
            await callback.answer(
                "❌ Некорректный ID категории.",
                show_alert=True,
            )
            return

        async with get_session() as session:
            category = await category_service.get_category(
                session=session,
                category_id=category_id,
            )

            if category is None:
                await callback.answer(
                    "❌ Категория не найдена.",
                    show_alert=True,
                )
                return

            category.active = not category.active
            await session.commit()

            status = (
                "включена"
                if category.active
                else "выключена"
            )

        await callback.answer(
            f"✅ Категория {status}.",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка изменения активности категории"
        )
        await callback.answer(
            "❌ Не удалось изменить статус категории.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "category_hide")
)
async def category_hide(
    callback: CallbackQuery,
    callback_data: AdminCB,
) -> None:
    """
    Скрывает/показывает категорию.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        category_id = _extract_category_id(
            callback_data
        )

        if category_id <= 0:
            await callback.answer(
                "❌ Некорректный ID категории.",
                show_alert=True,
            )
            return

        async with get_session() as session:
            category = await category_service.get_category(
                session=session,
                category_id=category_id,
            )

            if category is None:
                await callback.answer(
                    "❌ Категория не найдена.",
                    show_alert=True,
                )
                return

            category.hidden = not category.hidden
            await session.commit()

            status = (
                "скрыта"
                if category.hidden
                else "показана"
            )

        await callback.answer(
            f"✅ Категория {status}.",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка изменения видимости категории"
        )
        await callback.answer(
            "❌ Не удалось изменить видимость категории.",
            show_alert=True,
        )


@router.callback_query(
    AdminCB.filter(F.action == "category_delete")
)
async def category_delete(
    callback: CallbackQuery,
    callback_data: AdminCB,
) -> None:
    """
    Удаляет категорию.

    Сам сервис обязан проверить наличие дочерних категорий
    и связанных товаров перед удалением.
    """
    try:
        if not _is_admin(callback.from_user.id):
            await callback.answer(
                "⛔ Доступ запрещён.",
                show_alert=True,
            )
            return

        category_id = _extract_category_id(
            callback_data
        )

        if category_id <= 0:
            await callback.answer(
                "❌ Некорректный ID категории.",
                show_alert=True,
            )
            return

        async with get_session() as session:
            await category_service.delete_category(
                session=session,
                category_id=category_id,
            )
            await session.commit()

        await callback.answer(
            "✅ Категория удалена.",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Ошибка удаления категории"
        )
        await callback.answer(
            "❌ Не удалось удалить категорию. "
            "Проверьте, что у неё нет связанных товаров "
            "или дочерних категорий.",
            show_alert=True,
        )


def _extract_category_id(
    callback_data: AdminCB,
) -> int:
    """
    Получает ID категории из callback.
    """
    for field_name in (
        "category_id",
        "item_id",
        "object_id",
        "entity_id",
        "id",
    ):
        value = getattr(
            callback_data,
            field_name,
            0,
        )

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