from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ============================================================
# Константы
# ============================================================

MONEY_PRECISION = 18
MONEY_SCALE = 8


# ============================================================
# Base
# ============================================================

class Base(DeclarativeBase):
    """Базовый класс всех SQLAlchemy-моделей."""

    pass


class TimestampMixin:
    """Общие временные поля."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ============================================================
# Enums
# ============================================================

class UserStatus(StrEnum):
    ACTIVE = "active"
    BANNED = "banned"


class OrderStatus(StrEnum):
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    REFUNDED = "refunded"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentGateway(StrEnum):
    CRYPTOPAY = "cryptopay"
    NOWPAYMENTS = "nowpayments"


class BalanceTransactionType(StrEnum):
    TOPUP = "topup"
    PURCHASE = "purchase"
    REFUND = "refund"
    REFERRAL = "referral"
    PROMO = "promo"
    ADMIN_CREDIT = "admin_credit"
    ADMIN_DEBIT = "admin_debit"
    ADJUSTMENT = "adjustment"


class TopupStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    EXPIRED = "expired"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GuideContentType(StrEnum):
    TEXT = "text"
    PHOTO = "photo"


class AdminAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    VIEW = "view"
    BAN = "ban"
    UNBAN = "unban"
    CREDIT = "credit"
    DEBIT = "debit"
    REFUND = "refund"
    DELIVER = "deliver"
    CANCEL = "cancel"
    BROADCAST = "broadcast"
    BACKUP = "backup"
    LOGIN = "login"
    SETTING_CHANGE = "setting_change"


# ============================================================
# User
# ============================================================

class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    telegram_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        index=True,
    )

    username: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    first_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    last_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    language_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="ru",
        server_default="ru",
    )

    status: Mapped[UserStatus] = mapped_column(
        String(32),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
        index=True,
    )

    balance_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    total_spent_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    total_topup_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    purchases_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    referral_code: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        unique=True,
        index=True,
    )

    referred_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )

    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    # --------------------------------------------------------
    # Связи
    # --------------------------------------------------------

    orders: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    balance_transactions: Mapped[list["BalanceTransaction"]] = relationship(
        "BalanceTransaction",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    topups: Mapped[list["TopupTransaction"]] = relationship(
        "TopupTransaction",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    referred_users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="referrer",
        foreign_keys=[referred_by_id],
    )

    referrer: Mapped["User | None"] = relationship(
        "User",
        back_populates="referred_users",
        remote_side=[id],
        foreign_keys=[referred_by_id],
    )

    promo_usages: Mapped[list["PromoCodeUsage"]] = relationship(
        "PromoCodeUsage",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    cart_items: Mapped[list["CartItem"]] = relationship(
        "CartItem",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    referrals_as_referrer: Mapped[list["Referral"]] = relationship(
        "Referral",
        foreign_keys="Referral.referrer_id",
        back_populates="referrer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    referrals_as_referred: Mapped[list["Referral"]] = relationship(
        "Referral",
        foreign_keys="Referral.referred_id",
        back_populates="referred",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    admin: Mapped["Admin | None"] = relationship(
        "Admin",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "balance_usd >= 0",
            name="ck_user_balance_non_negative",
        ),
        CheckConstraint(
            "total_spent_usd >= 0",
            name="ck_user_total_spent_non_negative",
        ),
        CheckConstraint(
            "total_topup_usd >= 0",
            name="ck_user_total_topup_non_negative",
        ),
        CheckConstraint(
            "purchases_count >= 0",
            name="ck_user_purchases_count_non_negative",
        ),
    )


# ============================================================
# Category
# ============================================================

class Category(Base, TimestampMixin):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    photo_file_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    # Нужны сервисному слою для скрытия категории без её удаления.
    is_hidden: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )

    # Дополнительные данные категории:
    # служебные параметры, будущие настройки и т.п.
    extra_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    parent: Mapped["Category | None"] = relationship(
        "Category",
        back_populates="children",
        remote_side=[id],
        foreign_keys=[parent_id],
    )

    children: Mapped[list["Category"]] = relationship(
        "Category",
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    products: Mapped[list["Product"]] = relationship(
        "Product",
        back_populates="category",
    )


# ============================================================
# Product
# ============================================================

class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    slug: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    price_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    stock: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    is_hidden: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )

    allow_purchase: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )

    extra_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    category: Mapped["Category"] = relationship(
        "Category",
        back_populates="products",
    )

    photos: Mapped[list["ProductPhoto"]] = relationship(
        "ProductPhoto",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProductPhoto.sort_order",
    )

    order_items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="product",
    )

    cart_items: Mapped[list["CartItem"]] = relationship(
        "CartItem",
        back_populates="product",
        passive_deletes=True,
    )

    media_groups: Mapped[list["ProductMediaGroup"]] = relationship(
        "ProductMediaGroup",
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ProductMediaGroup.sort_order",
    )

    __table_args__ = (
        CheckConstraint(
            "price_usd >= 0",
            name="ck_product_price_non_negative",
        ),
        CheckConstraint(
            "stock >= 0",
            name="ck_product_stock_non_negative",
        ),
    )


# ============================================================
# Product photos
# ============================================================

class ProductPhoto(Base, TimestampMixin):
    __tablename__ = "product_photos"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    file_id: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    file_unique_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    is_cover: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
        index=True,
    )

    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="photos",
    )


# ============================================================
# Cart
# ============================================================

class CartItem(Base, TimestampMixin):
    """
    Позиция корзины пользователя.

    Корзина хранится в БД, а не в FSM:
    пользователь не теряет корзину после перезапуска бота.
    """

    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="cart_items",
    )

    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="cart_items",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "product_id",
            name="uq_cart_item_user_product",
        ),
        CheckConstraint(
            "quantity > 0",
            name="ck_cart_item_quantity_positive",
        ),
    )


# ============================================================
# Orders
# ============================================================

class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    order_number: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[OrderStatus] = mapped_column(
        String(32),
        nullable=False,
        default=OrderStatus.PAYMENT_PENDING,
        server_default=OrderStatus.PAYMENT_PENDING.value,
        index=True,
    )

    subtotal_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    discount_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    total_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    balance_paid_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    crypto_paid_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    promo_code_id: Mapped[int | None] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    payment_idempotency_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
        index=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    delivery_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="orders",
    )

    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    payments: Mapped[list["PaymentInvoice"]] = relationship(
        "PaymentInvoice",
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    promo_code: Mapped["PromoCode | None"] = relationship(
        "PromoCode",
        back_populates="orders",
    )

    balance_transactions: Mapped[list["BalanceTransaction"]] = relationship(
        "BalanceTransaction",
        back_populates="order",
    )

    referrals: Mapped[list["Referral"]] = relationship(
        "Referral",
        back_populates="order",
    )

    __table_args__ = (
        CheckConstraint(
            "subtotal_usd >= 0",
            name="ck_order_subtotal_non_negative",
        ),
        CheckConstraint(
            "discount_usd >= 0",
            name="ck_order_discount_non_negative",
        ),
        CheckConstraint(
            "total_usd >= 0",
            name="ck_order_total_non_negative",
        ),
        CheckConstraint(
            "balance_paid_usd >= 0",
            name="ck_order_balance_paid_non_negative",
        ),
        CheckConstraint(
            "crypto_paid_usd >= 0",
            name="ck_order_crypto_paid_non_negative",
        ),
        CheckConstraint(
            "balance_paid_usd + crypto_paid_usd <= total_usd",
            name="ck_order_paid_not_exceed_total",
        ),
    )


# ============================================================
# Order items
# ============================================================

class OrderItem(Base, TimestampMixin):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    product_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    unit_price_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    total_price_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    delivery_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    order: Mapped["Order"] = relationship(
        "Order",
        back_populates="items",
    )

    product: Mapped["Product | None"] = relationship(
        "Product",
        back_populates="order_items",
    )

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="ck_order_item_quantity_positive",
        ),
        CheckConstraint(
            "unit_price_usd >= 0",
            name="ck_order_item_unit_price_non_negative",
        ),
        CheckConstraint(
            "total_price_usd >= 0",
            name="ck_order_item_total_non_negative",
        ),
    )


# ============================================================
# Payment invoices
# ============================================================

class PaymentInvoice(Base, TimestampMixin):
    __tablename__ = "payment_invoices"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    gateway: Mapped[PaymentGateway] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    external_invoice_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    external_payment_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    status: Mapped[PaymentStatus] = mapped_column(
        String(32),
        nullable=False,
        default=PaymentStatus.PENDING,
        server_default=PaymentStatus.PENDING.value,
        index=True,
    )

    amount_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    crypto_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=True,
    )

    crypto_currency: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=True,
    )

    payment_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    tx_hash: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        index=True,
    )

    raw_response: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    webhook_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    order: Mapped["Order | None"] = relationship(
        "Order",
        back_populates="payments",
    )

    user: Mapped["User"] = relationship(
        "User",
    )

    __table_args__ = (
        CheckConstraint(
            "amount_usd > 0",
            name="ck_payment_invoice_amount_positive",
        ),
        Index(
            "ix_payment_external_gateway",
            "gateway",
            "external_invoice_id",
        ),
    )


# ============================================================
# Topup transactions
# ============================================================

class TopupTransaction(Base, TimestampMixin):
    __tablename__ = "topup_transactions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    gateway: Mapped[PaymentGateway] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    external_invoice_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    external_payment_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )

    status: Mapped[TopupStatus] = mapped_column(
        String(32),
        nullable=False,
        default=TopupStatus.PENDING,
        server_default=TopupStatus.PENDING.value,
        index=True,
    )

    amount_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    crypto_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=True,
    )

    crypto_currency: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    exchange_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=True,
    )

    tx_hash: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
        index=True,
    )

    payment_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    raw_response: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="topups",
    )

    balance_transactions: Mapped[list["BalanceTransaction"]] = relationship(
        "BalanceTransaction",
        back_populates="topup",
    )

    __table_args__ = (
        CheckConstraint(
            "amount_usd > 0",
            name="ck_topup_amount_positive",
        ),
    )


# ============================================================
# Balance transactions
# ============================================================

class BalanceTransaction(Base, TimestampMixin):
    __tablename__ = "balance_transactions"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    type: Mapped[BalanceTransactionType] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )

    amount_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    balance_before_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    balance_after_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    topup_id: Mapped[int | None] = mapped_column(
        ForeignKey("topup_transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="balance_transactions",
    )

    order: Mapped["Order | None"] = relationship(
        "Order",
        back_populates="balance_transactions",
    )

    topup: Mapped["TopupTransaction | None"] = relationship(
        "TopupTransaction",
        back_populates="balance_transactions",
    )

    __table_args__ = (
        CheckConstraint(
            "balance_after_usd >= 0",
            name="ck_balance_tx_after_non_negative",
        ),
    )


# ============================================================
# Promo codes
# ============================================================

class PromoCode(Base, TimestampMixin):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    code: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )

    discount_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(7, 4),
        nullable=True,
    )

    discount_amount_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=True,
    )

    max_uses: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    min_order_amount_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
        default=Decimal("0"),
        server_default="0",
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    usages: Mapped[list["PromoCodeUsage"]] = relationship(
        "PromoCodeUsage",
        back_populates="promo_code",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    orders: Mapped[list["Order"]] = relationship(
        "Order",
        back_populates="promo_code",
    )

    __table_args__ = (
        CheckConstraint(
            """
            (
                discount_percent IS NOT NULL
                AND discount_amount_usd IS NULL
            )
            OR
            (
                discount_percent IS NULL
                AND discount_amount_usd IS NOT NULL
            )
            """,
            name="ck_promo_has_discount",
        ),
        CheckConstraint(
            "discount_percent IS NULL OR discount_percent >= 0",
            name="ck_promo_percent_non_negative",
        ),
        CheckConstraint(
            "discount_percent IS NULL OR discount_percent <= 100",
            name="ck_promo_percent_max",
        ),
        CheckConstraint(
            "discount_amount_usd IS NULL OR discount_amount_usd >= 0",
            name="ck_promo_amount_non_negative",
        ),
        CheckConstraint(
            "min_order_amount_usd >= 0",
            name="ck_promo_min_order_non_negative",
        ),
    )


# ============================================================
# Promo usage
# ============================================================

class PromoCodeUsage(Base, TimestampMixin):
    __tablename__ = "promo_code_usages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    promo_code_id: Mapped[int] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    discount_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    promo_code: Mapped["PromoCode"] = relationship(
        "PromoCode",
        back_populates="usages",
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="promo_usages",
    )

    order: Mapped["Order"] = relationship(
        "Order",
    )

    __table_args__ = (
        UniqueConstraint(
            "promo_code_id",
            "user_id",
            name="uq_promo_usage_code_user",
        ),
        UniqueConstraint(
            "promo_code_id",
            "order_id",
            name="uq_promo_usage_code_order",
        ),
    )


# ============================================================
# Referrals
# ============================================================

class Referral(Base, TimestampMixin):
    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    referrer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    referred_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    reward_percent: Mapped[Decimal] = mapped_column(
        Numeric(7, 4),
        nullable=False,
    )

    reward_amount_usd: Mapped[Decimal] = mapped_column(
        Numeric(MONEY_PRECISION, MONEY_SCALE),
        nullable=False,
    )

    referrer: Mapped["User"] = relationship(
        "User",
        foreign_keys=[referrer_id],
        back_populates="referrals_as_referrer",
    )

    referred: Mapped["User"] = relationship(
        "User",
        foreign_keys=[referred_id],
        back_populates="referrals_as_referred",
    )

    order: Mapped["Order | None"] = relationship(
        "Order",
        back_populates="referrals",
    )

    __table_args__ = (
        CheckConstraint(
            "reward_percent >= 0",
            name="ck_referral_percent_non_negative",
        ),
        CheckConstraint(
            "reward_percent <= 100",
            name="ck_referral_percent_max",
        ),
        CheckConstraint(
            "reward_amount_usd >= 0",
            name="ck_referral_amount_non_negative",
        ),
        UniqueConstraint(
            "order_id",
            name="uq_referral_order_id",
        ),
        Index(
            "ix_referral_referrer_referred",
            "referrer_id",
            "referred_id",
        ),
    )


# ============================================================
# Admins
# ============================================================

class Admin(Base, TimestampMixin):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="admin",
        server_default="admin",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        back_populates="admin",
    )

    logs: Mapped[list["AdminLog"]] = relationship(
        "AdminLog",
        back_populates="admin",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


# ============================================================
# Admin logs
# ============================================================

class AdminLog(Base, TimestampMixin):
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    admin_id: Mapped[int] = mapped_column(
        ForeignKey("admins.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    action: Mapped[AdminAction] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    target_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    target_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )

    admin: Mapped["Admin"] = relationship(
        "Admin",
        back_populates="logs",
    )


# ============================================================
# Shop settings
# ============================================================

class ShopSetting(Base, TimestampMixin):
    __tablename__ = "shop_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    value_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="string",
        server_default="string",
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    is_public: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )


# ============================================================
# P2P guide
# ============================================================

class P2PGuideItem(Base, TimestampMixin):
    __tablename__ = "p2p_guide_items"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    language: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
    )

    content_type: Mapped[GuideContentType] = mapped_column(
        String(32),
        nullable=False,
        default=GuideContentType.TEXT,
        server_default=GuideContentType.TEXT.value,
    )

    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    photo_file_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
        index=True,
    )

    __table_args__ = (
        Index(
            "ix_p2p_guide_language_order",
            "language",
            "sort_order",
        ),
    )


# ============================================================
# Payment gateway settings
# ============================================================

class PaymentGatewaySetting(Base, TimestampMixin):
    __tablename__ = "payment_gateway_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    gateway: Mapped[PaymentGateway] = mapped_column(
        String(32),
        nullable=False,
        unique=True,
        index=True,
    )

    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )

    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
        index=True,
    )

    settings_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )


# ============================================================
# Broadcasts
# ============================================================

class Broadcast(Base, TimestampMixin):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    admin_id: Mapped[int] = mapped_column(
        ForeignKey("admins.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    media_type: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    media_file_id: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    button_text: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    button_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="draft",
        server_default="draft",
        index=True,
    )

    total_users: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    sent_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    blocked_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )


# ============================================================
# Product media groups
# ============================================================

class ProductMediaGroup(Base, TimestampMixin):
    __tablename__ = "product_media_groups"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    media_group_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    media_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    file_id: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )

    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    product: Mapped["Product"] = relationship(
        "Product",
        back_populates="media_groups",
    )

    __table_args__ = (
        Index(
            "ix_product_media_group_product_group",
            "product_id",
            "media_group_id",
        ),
    )


# ============================================================
# Экспорт
# ============================================================

__all__ = [
    "Base",
    "TimestampMixin",
    "MONEY_PRECISION",
    "MONEY_SCALE",
    "UserStatus",
    "OrderStatus",
    "PaymentStatus",
    "PaymentGateway",
    "BalanceTransactionType",
    "TopupStatus",
    "GuideContentType",
    "AdminAction",
    "User",
    "Category",
    "Product",
    "ProductPhoto",
    "CartItem",
    "Order",
    "OrderItem",
    "PaymentInvoice",
    "TopupTransaction",
    "BalanceTransaction",
    "PromoCode",
    "PromoCodeUsage",
    "Referral",
    "Admin",
    "AdminLog",
    "ShopSetting",
    "P2PGuideItem",
    "PaymentGatewaySetting",
    "Broadcast",
    "ProductMediaGroup",
]