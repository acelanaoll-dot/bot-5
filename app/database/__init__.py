"""
Слой работы с базой данных.

Экспортирует:
    - ORM-модели;
    - async SQLAlchemy engine;
    - фабрику AsyncSession;
    - контекстные менеджеры сессий и транзакций;
    - проверки подключения и корректное завершение engine.
"""

from app.database.models import (
    Admin,
    AdminAction,
    AdminLog,
    BalanceTransaction,
    BalanceTransactionType,
    Base,
    Broadcast,
    Category,
    GuideContentType,
    MONEY_PRECISION,
    MONEY_SCALE,
    Order,
    OrderItem,
    OrderStatus,
    PaymentGateway,
    PaymentGatewaySetting,
    PaymentInvoice,
    PaymentStatus,
    P2PGuideItem,
    Product,
    ProductMediaGroup,
    ProductPhoto,
    PromoCode,
    PromoCodeUsage,
    Referral,
    ShopSetting,
    TimestampMixin,
    TopupStatus,
    TopupTransaction,
    User,
    UserStatus,
)
from app.database.session import (
    SessionLocal,
    check_database_connection,
    dispose_database,
    engine,
    get_session,
    init_database,
    write_transaction,
)

__all__ = [
    # Base / constants
    "Base",
    "TimestampMixin",
    "MONEY_PRECISION",
    "MONEY_SCALE",

    # Enums
    "UserStatus",
    "OrderStatus",
    "PaymentStatus",
    "PaymentGateway",
    "BalanceTransactionType",
    "TopupStatus",
    "GuideContentType",
    "AdminAction",

    # Users
    "User",

    # Catalog
    "Category",
    "Product",
    "ProductPhoto",
    "ProductMediaGroup",

    # Orders / payments
    "Order",
    "OrderItem",
    "PaymentInvoice",
    "TopupTransaction",
    "BalanceTransaction",

    # Promotions / referrals
    "PromoCode",
    "PromoCodeUsage",
    "Referral",

    # Administration
    "Admin",
    "AdminLog",
    "Broadcast",

    # Settings / guides
    "ShopSetting",
    "P2PGuideItem",
    "PaymentGatewaySetting",

    # SQLAlchemy
    "engine",
    "SessionLocal",
    "get_session",
    "write_transaction",
    "check_database_connection",
    "init_database",
    "dispose_database",
]