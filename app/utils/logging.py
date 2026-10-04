from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from app.config import settings


def setup_logging() -> None:
    """
    Настраивает централизованное логирование приложения.

    Логи:
    - выводятся в консоль;
    - сохраняются в bot.log;
    - ошибки сохраняются отдельно в errors.log.
    """

    log_dir = Path(settings.log_dir)

    log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    bot_log = log_dir / "bot.log"
    errors_log = log_dir / "errors.log"

    # Удаляем стандартный обработчик Loguru.
    logger.remove()

    # --------------------------------------------------------
    # CONSOLE
    # --------------------------------------------------------

    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:"
            "<cyan>{function}</cyan>:"
            "<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=settings.log_backtrace,
        diagnose=settings.log_diagnose,
    )

    # --------------------------------------------------------
    # ОСНОВНОЙ ЛОГ
    # --------------------------------------------------------

    logger.add(
        str(bot_log),
        level=settings.log_level,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        compression=settings.log_compression,
        encoding="utf-8",
        enqueue=True,
        backtrace=settings.log_backtrace,
        diagnose=settings.log_diagnose,
    )

    # --------------------------------------------------------
    # ERROR LOG
    # --------------------------------------------------------

    logger.add(
        str(errors_log),
        level="ERROR",
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        compression=settings.log_compression,
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=settings.log_diagnose,
    )

    logger.info(
        "Логирование инициализировано"
    )


__all__ = [
    "setup_logging",
]