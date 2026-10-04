"""Единая точка экспорта сервисного слоя приложения."""

from app.services.backup import (
    BackupError,
    BackupValidationError,
    BackupNotFoundError,
    BackupCreationError,
    BackupRestoreError,
    BackupInfo,
    BackupResult,
    BackupService,
    backup_service,
)

from app.services.balance import (
    BalanceError,
    InsufficientBalanceError,
    InvalidBalanceAmountError,
    BalanceIdempotencyConflictError,
    BalanceOperationResult,
    BalanceService,
    balance_service,
)

from app.services.broadcast import (
    BroadcastError,
    BroadcastNotFoundError,
    BroadcastValidationError,
    BroadcastAlreadyStartedError,
    BroadcastCancelledError,
    BroadcastSendError,
    BroadcastResult,
    BroadcastPreview,
    BroadcastService,
    broadcast_service,
)

from app.services.cart import (
    CartServiceError,
    CartItemNotFoundError,
    CartValidationError,
    CartStockError,
    CartProductUnavailableError,
    CartTotals,
    CartService,
    cart_service,
)

from app.services.categories import (
    CategoryServiceError,
    CategoryNotFoundError,
    CategoryAlreadyExistsError,
    CategoryValidationError,
    CategoryHierarchyError,
    CategoryService,
    category_service,
)

from app.services.crypto_pay import (
    CryptoPayError,
    CryptoPayDisabledError,
    CryptoPayAPIError,
    CryptoPayRateLimitError,
    CryptoPayInvoice,
    CryptoPayUser,
    CryptoPayService,
    crypto_pay_service,
)

from app.services.exchange_rate import (
    ExchangeRate,
    ExchangeRateService,
    exchange_rate_service,
)

from app.services.media import (
    MediaServiceError,
    MediaValidationError,
    MediaNotFoundError,
    MediaProductNotFoundError,
    MediaLimitError,
    MediaAlreadyExistsError,
    ProductPhotoInfo,
    ProductMediaInfo,
    MediaGroupInfo,
    MediaStats,
    MediaService,
    media_service,
)

from app.services.nowpayments import (
    NowPaymentsError,
    NowPaymentsDisabledError,
    NowPaymentsAPIError,
    NowPaymentsRateLimitError,
    NowPaymentsPayment,
    NowPaymentsEstimate,
    NowPaymentsService,
    nowpayments_service,
)

from app.services.orders import (
    OrderServiceError,
    OrderNotFoundError,
    CartEmptyError,
    ProductNotFoundError,
    ProductInactiveError,
    InsufficientStockError,
    InvalidOrderAmountError,
    OrderAlreadyPaidError,
    OrderCancelledError,
    OrderExpiredError,
    InvalidOrderStateError,
    PaymentAmountExceededError,
    PaymentAlreadyAppliedError,
    OrderTotals,
    PaymentApplicationResult,
    OrderCreateResult,
    OrderService,
    order_service,
)

from app.services.p2p_guide import (
    P2PGuideError,
    P2PGuideItemNotFoundError,
    P2PGuideValidationError,
    P2PGuideAlreadyExistsError,
    P2PGuideItemData,
    P2PGuideService,
    p2p_guide_service,
)

from app.services.products import (
    ProductServiceError,
    ProductNotFoundError,
    ProductAlreadyExistsError,
    ProductValidationError,
    ProductUnavailableError,
    ProductStockError,
    ProductPhotoError,
    ProductService,
    product_service,
)

from app.services.promo import (
    PromoError,
    PromoNotFoundError,
    PromoInactiveError,
    PromoExpiredError,
    PromoUsageLimitError,
    PromoAlreadyUsedError,
    PromoMinimumOrderError,
    PromoValidationError,
    PromoAlreadyExistsError,
    PromoDiscountError,
    PromoDiscountResult,
    PromoApplyResult,
    PromoStats,
    PromoService,
    promo_service,
)

from app.services.proxy_manager import (
    ProxyState,
    ProxyManager,
    proxy_manager,
)

from app.services.referrals import (
    ReferralError,
    ReferralDisabledError,
    ReferralUserNotFoundError,
    ReferralAlreadyExistsError,
    InvalidReferralError,
    ReferralInfo,
    ReferralRewardResult,
    ReferralService,
    referral_service,
)


__all__ = [
    # Backup
    "BackupError",
    "BackupValidationError",
    "BackupNotFoundError",
    "BackupCreationError",
    "BackupRestoreError",
    "BackupInfo",
    "BackupResult",
    "BackupService",
    "backup_service",

    # Balance
    "BalanceError",
    "InsufficientBalanceError",
    "InvalidBalanceAmountError",
    "BalanceIdempotencyConflictError",
    "BalanceOperationResult",
    "BalanceService",
    "balance_service",

    # Broadcast
    "BroadcastError",
    "BroadcastNotFoundError",
    "BroadcastValidationError",
    "BroadcastAlreadyStartedError",
    "BroadcastCancelledError",
    "BroadcastSendError",
    "BroadcastResult",
    "BroadcastPreview",
    "BroadcastService",
    "broadcast_service",

    # Cart
    "CartServiceError",
    "CartItemNotFoundError",
    "CartValidationError",
    "CartStockError",
    "CartProductUnavailableError",
    "CartTotals",
    "CartService",
    "cart_service",

    # Categories
    "CategoryServiceError",
    "CategoryNotFoundError",
    "CategoryAlreadyExistsError",
    "CategoryValidationError",
    "CategoryHierarchyError",
    "CategoryService",
    "category_service",

    # Crypto Pay
    "CryptoPayError",
    "CryptoPayDisabledError",
    "CryptoPayAPIError",
    "CryptoPayRateLimitError",
    "CryptoPayInvoice",
    "CryptoPayUser",
    "CryptoPayService",
    "crypto_pay_service",

    # Exchange rate
    "ExchangeRate",
    "ExchangeRateService",
    "exchange_rate_service",

    # Media
    "MediaServiceError",
    "MediaValidationError",
    "MediaNotFoundError",
    "MediaProductNotFoundError",
    "MediaLimitError",
    "MediaAlreadyExistsError",
    "ProductPhotoInfo",
    "ProductMediaInfo",
    "MediaGroupInfo",
    "MediaStats",
    "MediaService",
    "media_service",

    # NOWPayments
    "NowPaymentsError",
    "NowPaymentsDisabledError",
    "NowPaymentsAPIError",
    "NowPaymentsRateLimitError",
    "NowPaymentsPayment",
    "NowPaymentsEstimate",
    "NowPaymentsService",
    "nowpayments_service",

    # Orders
    "OrderServiceError",
    "OrderNotFoundError",
    "CartEmptyError",
    "ProductNotFoundError",
    "ProductInactiveError",
    "InsufficientStockError",
    "InvalidOrderAmountError",
    "OrderAlreadyPaidError",
    "OrderCancelledError",
    "OrderExpiredError",
    "InvalidOrderStateError",
    "PaymentAmountExceededError",
    "PaymentAlreadyAppliedError",
    "OrderTotals",
    "PaymentApplicationResult",
    "OrderCreateResult",
    "OrderService",
    "order_service",

    # P2P guide
    "P2PGuideError",
    "P2PGuideItemNotFoundError",
    "P2PGuideValidationError",
    "P2PGuideAlreadyExistsError",
    "P2PGuideItemData",
    "P2PGuideService",
    "p2p_guide_service",

    # Products
    "ProductServiceError",
    "ProductNotFoundError",
    "ProductAlreadyExistsError",
    "ProductValidationError",
    "ProductUnavailableError",
    "ProductStockError",
    "ProductPhotoError",
    "ProductService",
    "product_service",

    # Promo
    "PromoError",
    "PromoNotFoundError",
    "PromoInactiveError",
    "PromoExpiredError",
    "PromoUsageLimitError",
    "PromoAlreadyUsedError",
    "PromoMinimumOrderError",
    "PromoValidationError",
    "PromoAlreadyExistsError",
    "PromoDiscountError",
    "PromoDiscountResult",
    "PromoApplyResult",
    "PromoStats",
    "PromoService",
    "promo_service",

    # Proxy
    "ProxyState",
    "ProxyManager",
    "proxy_manager",

    # Referrals
    "ReferralError",
    "ReferralDisabledError",
    "ReferralUserNotFoundError",
    "ReferralAlreadyExistsError",
    "InvalidReferralError",
    "ReferralInfo",
    "ReferralRewardResult",
    "ReferralService",
    "referral_service",
]