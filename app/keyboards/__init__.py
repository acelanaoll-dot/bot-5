from __future__ import annotations

from app.keyboards.admin import (
    admin_main_keyboard,
    admin_back_keyboard,
    admin_product_keyboard,
    admin_product_list_keyboard,
    admin_category_keyboard,
    admin_category_list_keyboard,
    admin_order_keyboard,
    admin_order_list_keyboard,
    admin_user_keyboard,
    admin_stats_keyboard,
    admin_finance_keyboard,
    admin_broadcast_keyboard,
    admin_promo_keyboard,
    admin_topup_keyboard,
    admin_gateway_keyboard,
    admin_settings_keyboard,
    admin_backup_keyboard,
    admin_log_keyboard,
    admin_text_keyboard,
)

from app.keyboards.cart import (
    cart_keyboard,
    cart_item_keyboard,
    empty_cart_keyboard,
    cart_clear_confirm_keyboard,
)

from app.keyboards.catalog import (
    catalog_categories_keyboard,
    catalog_category_keyboard,
    catalog_product_keyboard,
    catalog_search_keyboard,
)

from app.keyboards.checkout import (
    checkout_keyboard,
    payment_methods_keyboard,
    checkout_back_keyboard,
)

from app.keyboards.payment import (
    invoice_keyboard,
    payment_status_keyboard,
    topup_menu_keyboard,
    topup_amount_keyboard,
    topup_asset_keyboard,
    topup_payment_keyboard,
    topup_invoice_keyboard,
    topup_history_keyboard,
    topup_support_keyboard,
    p2p_guide_keyboard,
    admin_payment_keyboard,
)

from app.keyboards.user import (
    main_menu_keyboard,
    profile_keyboard,
    balance_keyboard,
    referral_keyboard,
    language_keyboard,
    support_keyboard,
)

__all__ = [
    # Пользователь
    "main_menu_keyboard",
    "profile_keyboard",
    "balance_keyboard",
    "referral_keyboard",
    "language_keyboard",
    "support_keyboard",

    # Каталог
    "catalog_categories_keyboard",
    "catalog_category_keyboard",
    "catalog_product_keyboard",
    "catalog_search_keyboard",

    # Корзина
    "cart_keyboard",
    "cart_item_keyboard",
    "empty_cart_keyboard",
    "cart_clear_confirm_keyboard",

    # Оформление заказа
    "checkout_keyboard",
    "payment_methods_keyboard",
    "checkout_back_keyboard",

    # Платежи и пополнение
    "invoice_keyboard",
    "payment_status_keyboard",
    "topup_menu_keyboard",
    "topup_amount_keyboard",
    "topup_asset_keyboard",
    "topup_payment_keyboard",
    "topup_invoice_keyboard",
    "topup_history_keyboard",
    "topup_support_keyboard",
    "p2p_guide_keyboard",
    "admin_payment_keyboard",

    # Админка
    "admin_main_keyboard",
    "admin_back_keyboard",
    "admin_product_keyboard",
    "admin_product_list_keyboard",
    "admin_category_keyboard",
    "admin_category_list_keyboard",
    "admin_order_keyboard",
    "admin_order_list_keyboard",
    "admin_user_keyboard",
    "admin_stats_keyboard",
    "admin_finance_keyboard",
    "admin_broadcast_keyboard",
    "admin_promo_keyboard",
    "admin_topup_keyboard",
    "admin_gateway_keyboard",
    "admin_settings_keyboard",
    "admin_backup_keyboard",
    "admin_log_keyboard",
    "admin_text_keyboard",
]