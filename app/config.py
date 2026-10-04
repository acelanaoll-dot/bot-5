from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    SecretStr,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================================
# Типы
# ============================================================

Environment = Literal["development", "production"]

LogLevel = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]


class Settings(BaseSettings):
    """
    Центральная конфигурация приложения.

    Все значения берутся из переменных окружения и/или .env.
    Секретные значения представлены через SecretStr, чтобы
    случайно не попасть в repr/logging.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # ========================================================
    # APPLICATION
    # ========================================================

    app_env: Environment = Field(
        default="production",
        alias="APP_ENV",
    )

    app_name: str = Field(
        default="telegram-shop-bot",
        alias="APP_NAME",
        min_length=1,
        max_length=100,
    )

    timezone: str = Field(
        default="UTC",
        alias="TIMEZONE",
        min_length=1,
        max_length=64,
    )

    default_language: str = Field(
        default="ru",
        alias="DEFAULT_LANGUAGE",
        min_length=2,
        max_length=5,
    )

    log_level: LogLevel = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )

    log_dir: Path = Field(
        default=Path("./logs"),
        alias="LOG_DIR",
    )

    backup_dir: Path = Field(
        default=Path("./backups"),
        alias="BACKUP_DIR",
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    bot_token: SecretStr = Field(
        alias="BOT_TOKEN",
    )

    admin_ids: set[int] = Field(
        default_factory=set,
        alias="ADMIN_IDS",
    )

    bot_username: str | None = Field(
        default=None,
        alias="BOT_USERNAME",
    )

    telegram_proxy_url: str | None = Field(
        default=None,
        alias="TELEGRAM_PROXY_URL",
    )

    telegram_request_timeout: float = Field(
        default=30.0,
        alias="TELEGRAM_REQUEST_TIMEOUT",
        gt=0,
        le=300,
    )

    # ========================================================
    # DATABASE
    # ========================================================

    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/bot.db",
        alias="DATABASE_URL",
    )

    database_echo: bool = Field(
        default=False,
        alias="DATABASE_ECHO",
    )

    database_pool_size: int = Field(
        default=5,
        alias="DATABASE_POOL_SIZE",
        ge=1,
        le=100,
    )

    database_max_overflow: int = Field(
        default=10,
        alias="DATABASE_MAX_OVERFLOW",
        ge=0,
        le=100,
    )

    database_pool_timeout: float = Field(
        default=30.0,
        alias="DATABASE_POOL_TIMEOUT",
        gt=0,
        le=300,
    )

    database_pool_recycle: int = Field(
        default=1800,
        alias="DATABASE_POOL_RECYCLE",
        ge=0,
    )

    # ========================================================
    # CRYPTOPAY
    # ========================================================

    cryptopay_api_token: SecretStr | None = Field(
        default=None,
        alias="CRYPTOPAY_API_TOKEN",
    )

    cryptopay_api_url: str = Field(
        default="https://pay.crypt.bot/api",
        alias="CRYPTOPAY_API_URL",
    )

    cryptopay_enabled: bool = Field(
        default=True,
        alias="CRYPTOPAY_ENABLED",
    )

    cryptopay_priority: int = Field(
        default=1,
        alias="CRYPTOPAY_PRIORITY",
        ge=1,
        le=100,
    )

    cryptopay_webhook_secret: SecretStr | None = Field(
        default=None,
        alias="CRYPTOPAY_WEBHOOK_SECRET",
    )

    invoice_ttl_seconds: int = Field(
        default=1800,
        alias="INVOICE_TTL_SECONDS",
        ge=60,
        le=86400,
    )

    invoice_poll_interval_seconds: int = Field(
        default=15,
        alias="INVOICE_POLL_INTERVAL_SECONDS",
        ge=5,
        le=300,
    )

    invoice_rate_limit_seconds: int = Field(
        default=30,
        alias="INVOICE_RATE_LIMIT_SECONDS",
        ge=1,
        le=3600,
    )

    # ========================================================
    # NOWPAYMENTS
    # ========================================================

    nowpayments_api_key: SecretStr | None = Field(
        default=None,
        alias="NOWPAYMENTS_API_KEY",
    )

    nowpayments_ipn_secret: SecretStr | None = Field(
        default=None,
        alias="NOWPAYMENTS_IPN_SECRET",
    )

    nowpayments_api_url: str = Field(
        default="https://api.nowpayments.io",
        alias="NOWPAYMENTS_API_URL",
    )

    nowpayments_enabled: bool = Field(
        default=False,
        alias="NOWPAYMENTS_ENABLED",
    )

    nowpayments_priority: int = Field(
        default=2,
        alias="NOWPAYMENTS_PRIORITY",
        ge=1,
        le=100,
    )

    # ========================================================
    # CRYPTO
    # ========================================================

    supported_currencies: list[str] = Field(
        default_factory=lambda: [
            "USDTTRC20",
            "USDTTON",
            "TON",
            "BTC",
            "ETH",
        ],
        alias="SUPPORTED_CURRENCIES",
    )

    # ========================================================
    # PROXY
    # ========================================================

    global_proxy_url: str | None = Field(
        default=None,
        alias="GLOBAL_PROXY_URL",
    )

    crypto_proxy_enabled: bool = Field(
        default=True,
        alias="CRYPTO_PROXY_ENABLED",
    )

    use_global_proxy_for_telegram: bool = Field(
        default=False,
        alias="USE_GLOBAL_PROXY_FOR_TELEGRAM",
    )

    proxy_pool_file: Path = Field(
        default=Path("./proxies.txt"),
        alias="PROXY_POOL_FILE",
    )

    proxy_rotation_enabled: bool = Field(
        default=False,
        alias="PROXY_ROTATION_ENABLED",
    )

    proxy_healthcheck_timeout: float = Field(
        default=10.0,
        alias="PROXY_HEALTHCHECK_TIMEOUT",
        gt=0,
        le=120,
    )

    proxy_healthcheck_url: str = Field(
        default="https://api.telegram.org",
        alias="PROXY_HEALTHCHECK_URL",
        min_length=1,
    )

    proxy_failure_threshold: int = Field(
        default=3,
        alias="PROXY_FAILURE_THRESHOLD",
        ge=1,
        le=100,
    )

    proxy_rotate_on_status_codes: set[int] = Field(
        default_factory=lambda: {403, 429},
        alias="PROXY_ROTATE_ON_STATUS_CODES",
    )

    # ========================================================
    # HTTP
    # ========================================================

    http_connect_timeout: float = Field(
        default=10.0,
        alias="HTTP_CONNECT_TIMEOUT",
        gt=0,
        le=120,
    )

    http_read_timeout: float = Field(
        default=30.0,
        alias="HTTP_READ_TIMEOUT",
        gt=0,
        le=300,
    )

    http_write_timeout: float = Field(
        default=30.0,
        alias="HTTP_WRITE_TIMEOUT",
        gt=0,
        le=300,
    )

    http_pool_timeout: float = Field(
        default=10.0,
        alias="HTTP_POOL_TIMEOUT",
        gt=0,
        le=120,
    )

    http_max_connections: int = Field(
        default=50,
        alias="HTTP_MAX_CONNECTIONS",
        ge=1,
        le=1000,
    )

    http_max_keepalive_connections: int = Field(
        default=20,
        alias="HTTP_MAX_KEEPALIVE_CONNECTIONS",
        ge=1,
        le=1000,
    )

    # ========================================================
    # SHOP
    # ========================================================

    default_min_topup_usd: float = Field(
        default=5.0,
        alias="DEFAULT_MIN_TOPUP_USD",
        gt=0,
        le=1_000_000,
    )

    shop_currency: str = Field(
        default="USD",
        alias="SHOP_CURRENCY",
        min_length=3,
        max_length=3,
    )

    money_decimal_places: int = Field(
        default=2,
        alias="MONEY_DECIMAL_PLACES",
        ge=0,
        le=8,
    )

    shop_enabled: bool = Field(
        default=True,
        alias="SHOP_ENABLED",
    )

    maintenance_mode: bool = Field(
        default=False,
        alias="MAINTENANCE_MODE",
    )

    subscription_required: bool = Field(
        default=False,
        alias="SUBSCRIPTION_REQUIRED",
    )

    required_channel_id: int | None = Field(
        default=None,
        alias="REQUIRED_CHANNEL_ID",
    )

    required_channel_username: str | None = Field(
        default=None,
        alias="REQUIRED_CHANNEL_USERNAME",
    )

    # ========================================================
    # EXCHANGE RATE
    # ========================================================

    exchange_rate_api_url: str | None = Field(
        default=None,
        alias="EXCHANGE_RATE_API_URL",
    )

    exchange_rate_api_key: SecretStr | None = Field(
        default=None,
        alias="EXCHANGE_RATE_API_KEY",
    )

    default_usd_rub_rate: float = Field(
        default=100.0,
        alias="DEFAULT_USD_RUB_RATE",
        gt=0,
    )

    exchange_rate_max_age_seconds: int = Field(
        default=900,
        alias="EXCHANGE_RATE_MAX_AGE_SECONDS",
        ge=30,
        le=86400,
    )

    # ========================================================
    # REFERRALS
    # ========================================================

    referrals_enabled: bool = Field(
        default=True,
        alias="REFERRALS_ENABLED",
    )

    default_referral_percent: float = Field(
        default=5.0,
        alias="DEFAULT_REFERRAL_PERCENT",
        ge=0,
        le=100,
    )

    referral_min_order_usd: float = Field(
        default=1.0,
        alias="REFERRAL_MIN_ORDER_USD",
        ge=0,
    )

    # ========================================================
    # BROADCAST
    # ========================================================

    broadcast_messages_per_second: int = Field(
        default=20,
        alias="BROADCAST_MESSAGES_PER_SECOND",
        ge=1,
        le=100,
    )

    broadcast_batch_size: int = Field(
        default=20,
        alias="BROADCAST_BATCH_SIZE",
        ge=1,
        le=1000,
    )

    broadcast_batch_delay_seconds: float = Field(
        default=1.0,
        alias="BROADCAST_BATCH_DELAY_SECONDS",
        ge=0,
        le=60,
    )

    # ========================================================
    # ANTI-FLOOD
    # ========================================================

    throttle_rate: int = Field(
        default=5,
        alias="THROTTLE_RATE",
        ge=1,
        le=1000,
    )

    throttle_period_seconds: float = Field(
        default=2.0,
        alias="THROTTLE_PERIOD_SECONDS",
        gt=0,
        le=3600,
    )

    throttle_block_seconds: float = Field(
        default=10.0,
        alias="THROTTLE_BLOCK_SECONDS",
        gt=0,
        le=3600,
    )

    # ========================================================
    # BACKUPS
    # ========================================================

    backup_enabled: bool = Field(
        default=True,
        alias="BACKUP_ENABLED",
    )

    backup_interval_hours: int = Field(
        default=24,
        alias="BACKUP_INTERVAL_HOURS",
        ge=1,
        le=168,
    )

    backup_retention_days: int = Field(
        default=14,
        alias="BACKUP_RETENTION_DAYS",
        ge=1,
        le=3650,
    )

    backup_download_enabled: bool = Field(
        default=True,
        alias="BACKUP_DOWNLOAD_ENABLED",
    )

    # ========================================================
    # CLEANUP
    # ========================================================

    invoice_cleanup_enabled: bool = Field(
        default=True,
        alias="INVOICE_CLEANUP_ENABLED",
    )

    invoice_cleanup_hours: int = Field(
        default=24,
        alias="INVOICE_CLEANUP_HOURS",
        ge=1,
        le=8760,
    )

    cleanup_interval_minutes: int = Field(
        default=60,
        alias="CLEANUP_INTERVAL_MINUTES",
        ge=1,
        le=1440,
    )

    # ========================================================
    # HEALTHCHECK
    # ========================================================

    healthcheck_host: str = Field(
        default="127.0.0.1",
        alias="HEALTHCHECK_HOST",
    )

    healthcheck_port: int = Field(
        default=8080,
        alias="HEALTHCHECK_PORT",
        ge=1,
        le=65535,
    )

    healthcheck_path: str = Field(
        default="/health",
        alias="HEALTHCHECK_PATH",
    )

    # ========================================================
    # P2P GUIDE
    # ========================================================

    p2p_guide_enabled: bool = Field(
        default=True,
        alias="P2P_GUIDE_ENABLED",
    )

    support_username: str | None = Field(
        default=None,
        alias="SUPPORT_USERNAME",
    )

    support_url: str | None = Field(
        default=None,
        alias="SUPPORT_URL",
    )

    p2p_guide_title_ru: str = Field(
        default="Как пополнить баланс",
        alias="P2P_GUIDE_TITLE_RU",
    )

    p2p_guide_title_en: str = Field(
        default="How to top up your balance",
        alias="P2P_GUIDE_TITLE_EN",
    )

    # ========================================================
    # ADMIN NOTIFICATIONS
    # ========================================================

    admin_notifications_enabled: bool = Field(
        default=True,
        alias="ADMIN_NOTIFICATIONS_ENABLED",
    )

    notify_new_order: bool = Field(
        default=True,
        alias="NOTIFY_NEW_ORDER",
    )

    notify_payment: bool = Field(
        default=True,
        alias="NOTIFY_PAYMENT",
    )

    notify_errors: bool = Field(
        default=True,
        alias="NOTIFY_ERRORS",
    )

    notify_gateway_errors: bool = Field(
        default=True,
        alias="NOTIFY_GATEWAY_ERRORS",
    )

    # ========================================================
    # CSV
    # ========================================================

    csv_encoding: str = Field(
        default="utf-8-sig",
        alias="CSV_ENCODING",
    )

    csv_delimiter: str = Field(
        default=";",
        alias="CSV_DELIMITER",
        min_length=1,
        max_length=1,
    )

    # ========================================================
    # LOCALIZATION
    # ========================================================

    locales_dir: Path = Field(
        default=Path("./locales"),
        alias="LOCALES_DIR",
    )

    supported_languages: list[str] = Field(
        default_factory=lambda: ["ru", "en"],
        alias="SUPPORTED_LANGUAGES",
    )

    # ========================================================
    # SECURITY
    # ========================================================

    max_text_length: int = Field(
        default=4000,
        alias="MAX_TEXT_LENGTH",
        ge=100,
        le=100_000,
    )

    max_product_description_length: int = Field(
        default=10_000,
        alias="MAX_PRODUCT_DESCRIPTION_LENGTH",
        ge=100,
        le=100_000,
    )

    max_product_name_length: int = Field(
        default=255,
        alias="MAX_PRODUCT_NAME_LENGTH",
        ge=1,
        le=1000,
    )

    max_category_name_length: int = Field(
        default=255,
        alias="MAX_CATEGORY_NAME_LENGTH",
        ge=1,
        le=1000,
    )

    max_promo_code_length: int = Field(
        default=64,
        alias="MAX_PROMO_CODE_LENGTH",
        ge=1,
        le=255,
    )

    allow_html_in_product_description: bool = Field(
        default=False,
        alias="ALLOW_HTML_IN_PRODUCT_DESCRIPTION",
    )

    app_secret_key: SecretStr = Field(
        alias="APP_SECRET_KEY",
    )

    # ========================================================
    # LOGGING
    # ========================================================

    log_rotation: str = Field(
        default="10 MB",
        alias="LOG_ROTATION",
    )

    log_retention: str = Field(
        default="30 days",
        alias="LOG_RETENTION",
    )

    log_compression: str = Field(
        default="zip",
        alias="LOG_COMPRESSION",
    )

    log_backtrace: bool = Field(
        default=True,
        alias="LOG_BACKTRACE",
    )

    log_diagnose: bool = Field(
        default=False,
        alias="LOG_DIAGNOSE",
    )

    # ========================================================
    # VALIDATORS
    # ========================================================

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(
        cls,
        value: object,
    ) -> set[int]:
        if value is None:
            return set()

        if isinstance(value, set):
            return {int(item) for item in value}

        if isinstance(value, (list, tuple)):
            return {int(item) for item in value}

        if isinstance(value, int):
            return {value}

        if isinstance(value, str):
            raw = value.strip()

            if not raw:
                return set()

            result: set[int] = set()

            for item in raw.split(","):
                item = item.strip()

                if not item:
                    continue

                try:
                    result.add(int(item))
                except ValueError as exc:
                    raise ValueError(
                        f"Некорректный Telegram admin ID: {item!r}"
                    ) from exc

            return result

        raise TypeError(
            "ADMIN_IDS должен быть строкой, списком или множеством ID"
        )

    @field_validator("supported_currencies", mode="before")
    @classmethod
    def parse_supported_currencies(
        cls,
        value: object,
    ) -> list[str]:
        if value is None:
            return []

        if isinstance(value, str):
            return [
                item.strip().upper()
                for item in value.split(",")
                if item.strip()
            ]

        if isinstance(value, (list, tuple, set)):
            return [
                str(item).strip().upper()
                for item in value
                if str(item).strip()
            ]

        raise TypeError(
            "SUPPORTED_CURRENCIES должен быть строкой или списком"
        )

    @field_validator("supported_languages", mode="before")
    @classmethod
    def parse_supported_languages(
        cls,
        value: object,
    ) -> list[str]:
        if value is None:
            return ["ru", "en"]

        if isinstance(value, str):
            languages = [
                item.strip().lower()
                for item in value.split(",")
                if item.strip()
            ]
        elif isinstance(value, (list, tuple, set)):
            languages = [
                str(item).strip().lower()
                for item in value
                if str(item).strip()
            ]
        else:
            raise TypeError(
                "SUPPORTED_LANGUAGES должен быть строкой или списком"
            )

        if not languages:
            raise ValueError(
                "Должен быть указан хотя бы один поддерживаемый язык"
            )

        return languages

    @field_validator("proxy_rotate_on_status_codes", mode="before")
    @classmethod
    def parse_proxy_status_codes(
        cls,
        value: object,
    ) -> set[int]:
        if value is None:
            return {403, 429}

        if isinstance(value, str):
            result: set[int] = set()

            for item in value.split(","):
                item = item.strip()

                if not item:
                    continue

                try:
                    status_code = int(item)
                except ValueError as exc:
                    raise ValueError(
                        f"Некорректный HTTP status code: {item!r}"
                    ) from exc

                if not 100 <= status_code <= 599:
                    raise ValueError(
                        f"HTTP status code вне диапазона 100-599: "
                        f"{status_code}"
                    )

                result.add(status_code)

            return result

        if isinstance(value, (list, tuple, set)):
            return {int(item) for item in value}

        raise TypeError(
            "PROXY_ROTATE_ON_STATUS_CODES должен быть строкой "
            "или коллекцией чисел"
        )

    @field_validator("bot_username")
    @classmethod
    def normalize_bot_username(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        if not value:
            return None

        return value.lstrip("@")

    @field_validator("required_channel_username")
    @classmethod
    def normalize_channel_username(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        if not value:
            return None

        return value.lstrip("@")

    @field_validator("support_username")
    @classmethod
    def normalize_support_username(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()

        if not value:
            return None

        return value.lstrip("@")

    @field_validator("database_url")
    @classmethod
    def validate_database_url(
        cls,
        value: str,
    ) -> str:
        value = value.strip()

        allowed_prefixes = (
            "sqlite+aiosqlite:///",
            "postgresql+asyncpg://",
        )

        if not value.startswith(allowed_prefixes):
            raise ValueError(
                "DATABASE_URL должен использовать "
                "sqlite+aiosqlite или postgresql+asyncpg"
            )

        return value

    @field_validator("shop_currency")
    @classmethod
    def normalize_shop_currency(
        cls,
        value: str,
    ) -> str:
        value = value.strip().upper()

        if len(value) != 3:
            raise ValueError(
                "SHOP_CURRENCY должен состоять из 3 символов"
            )

        return value

    @field_validator("healthcheck_path")
    @classmethod
    def normalize_healthcheck_path(
        cls,
        value: str,
    ) -> str:
        value = value.strip()

        if not value:
            return "/health"

        if not value.startswith("/"):
            value = f"/{value}"

        return value

    # ========================================================
    # HELPERS
    # ========================================================

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith(
            "sqlite+aiosqlite:///"
        )

    @property
    def is_postgresql(self) -> bool:
        return self.database_url.startswith(
            "postgresql+asyncpg://"
        )

    @property
    def invoice_poll_interval(self) -> int:
        return self.invoice_poll_interval_seconds

    @property
    def cryptopay_invoice_rate_limit(self) -> int:
        return self.invoice_rate_limit_seconds

    @property
    def http_timeout(self) -> float:
        return max(
            self.http_connect_timeout,
            self.http_read_timeout,
            self.http_write_timeout,
        )

    @property
    def exchange_rate_url(self) -> str | None:
        return self.exchange_rate_api_url

    @property
    def exchange_rate_max_age(self) -> int:
        return self.exchange_rate_max_age_seconds

    @property
    def exchange_rate_default_usd_rub(self) -> float:
        return self.default_usd_rub_rate

    @property
    def localization_dir(self) -> Path:
        return self.locales_dir

    @property
    def min_topup_usd(self) -> float:
        return self.default_min_topup_usd

    @property
    def max_topup_usd(self) -> float | None:
        return 10000.0  # Установлен лимит по умолчанию (можно изменить)

    @property
    def supported_crypto_currencies(self) -> list[str]:
        return self.supported_currencies

    @property
    def referral_enabled(self) -> bool:
        return self.referrals_enabled

    @property
    def referrals_default_percent(self) -> float:
        return self.default_referral_percent

    @property
    def referrals_min_order_usd(self) -> float:
        return self.referral_min_order_usd

    @property
    def security_max_text_length(self) -> int:
        return self.max_text_length

    @property
    def broadcast_enabled(self) -> bool:
        return True

    @property
    def support_text(self) -> str:
        if self.support_username:
            return f"Поддержка: @{self.support_username.lstrip('@')}"
        if self.support_url:
            return f"Поддержка: {self.support_url}"
        return "Обратитесь в поддержку магазина."

    @property
    def proxy_rotate_status_codes(self) -> set[int]:
        return self.proxy_rotate_on_status_codes

    @property
    def telegram_proxy(self) -> str | None:
        if self.telegram_proxy_url:
            return self.telegram_proxy_url

        if self.use_global_proxy_for_telegram:
            return self.global_proxy_url

        return None

    @property
    def crypto_proxy(self) -> str | None:
        if not self.crypto_proxy_enabled:
            return None

        return self.global_proxy_url

    @property
    def has_cryptopay(self) -> bool:
        return (
            self.cryptopay_enabled
            and self.cryptopay_api_token is not None
            and bool(self.cryptopay_api_token.get_secret_value().strip())
        )

    @property
    def has_nowpayments(self) -> bool:
        return (
            self.nowpayments_enabled
            and self.nowpayments_api_key is not None
            and bool(self.nowpayments_api_key.get_secret_value().strip())
        )

    @property
    def enabled_payment_gateways(self) -> list[str]:
        gateways: list[tuple[int, str]] = []

        if self.has_cryptopay:
            gateways.append(
                (
                    self.cryptopay_priority,
                    "cryptopay",
                )
            )

        if self.has_nowpayments:
            gateways.append(
                (
                    self.nowpayments_priority,
                    "nowpayments",
                )
            )

        gateways.sort(key=lambda item: item[0])

        return [name for _, name in gateways]

    def ensure_directories(self) -> None:
        self.log_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.backup_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        if self.is_sqlite:
            database_path = self.database_url.removeprefix(
                "sqlite+aiosqlite:///"
            )

            database_file = Path(database_path)

            if database_file.parent != Path("."):
                database_file.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

    def validate_production_security(self) -> None:
        if not self.is_production:
            return

        app_secret = self.app_secret_key.get_secret_value().strip()

        if not app_secret or app_secret == "CHANGE_ME_TO_RANDOM_LONG_SECRET":
            raise ValueError(
                "В production необходимо задать APP_SECRET_KEY"
            )

        bot_token = self.bot_token.get_secret_value().strip()

        if not bot_token or bot_token == "CHANGE_ME":
            raise ValueError(
                "В production необходимо задать BOT_TOKEN"
            )

        if not self.admin_ids:
            raise ValueError(
                "В production необходимо задать хотя бы один ADMIN_IDS"
            )

        if self.default_referral_percent > 100:
            raise ValueError(
                "DEFAULT_REFERRAL_PERCENT не может быть больше 100"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()

    settings.ensure_directories()
    settings.validate_production_security()

    return settings

settings = get_settings()