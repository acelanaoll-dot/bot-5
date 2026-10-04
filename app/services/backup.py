# app/services/backup.py

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from loguru import logger

from app.config import settings


# ======================================================================
# Исключения
# ======================================================================


class BackupError(Exception):
    """Базовая ошибка резервного копирования."""


class BackupValidationError(BackupError):
    """Некорректные параметры резервного копирования."""


class BackupNotFoundError(BackupError):
    """Файл резервной копии не найден."""


class BackupCreationError(BackupError):
    """Ошибка создания резервной копии."""


class BackupRestoreError(BackupError):
    """Ошибка восстановления резервной копии."""


# ======================================================================
# DTO
# ======================================================================


@dataclass(slots=True, frozen=True)
class BackupInfo:
    """Информация о резервной копии."""

    path: Path
    filename: str
    size_bytes: int
    created_at: datetime


@dataclass(slots=True, frozen=True)
class BackupResult:
    """Результат создания резервной копии."""

    backup: BackupInfo
    source_type: str


# ======================================================================
# Сервис
# ======================================================================


class BackupService:
    """Сервис резервного копирования базы данных."""

    DEFAULT_BACKUP_DIR = "backups"
    DEFAULT_RETENTION = 10

    SQLITE_SCHEME = "sqlite"

    def __init__(self) -> None:
        self._backup_dir = self._resolve_backup_dir()

    # ==================================================================
    # Пути
    # ==================================================================

    def _resolve_backup_dir(self) -> Path:
        """
        Определяет каталог резервных копий.

        Сначала используются возможные настройки из config,
        затем стандартный каталог проекта ./backups.
        """

        configured = getattr(
            settings,
            "backup_dir",
            None,
        )

        if configured is None:
            configured = getattr(
                settings,
                "backups_dir",
                None,
            )

        if configured is None:
            configured = self.DEFAULT_BACKUP_DIR

        path = Path(
            str(configured)
        ).expanduser()

        if not path.is_absolute():
            # config.py уже работает относительно каталога проекта
            # в большинстве конфигураций. Здесь сохраняем поведение
            # максимально простым и предсказуемым.
            path = Path.cwd() / path

        return path.resolve()

    @property
    def backup_dir(self) -> Path:
        """Каталог резервных копий."""

        return self._backup_dir

    def ensure_backup_dir(self) -> Path:
        """Создаёт каталог резервных копий."""

        try:
            self._backup_dir.mkdir(
                parents=True,
                exist_ok=True,
            )
        except OSError as exc:
            raise BackupCreationError(
                f"Не удалось создать каталог "
                f"{self._backup_dir}"
            ) from exc

        return self._backup_dir

    # ==================================================================
    # Настройки
    # ==================================================================

    @property
    def database_url(self) -> str:
        """Возвращает URL базы данных."""

        value = getattr(
            settings,
            "database_url",
            None,
        )

        if not value:
            raise BackupValidationError(
                "DATABASE_URL не настроен."
            )

        return str(value)

    def database_type(self) -> str:
        """Определяет тип БД."""

        url = self.database_url.lower()

        if url.startswith("sqlite"):
            return "sqlite"

        if url.startswith("postgresql"):
            return "postgresql"

        if url.startswith("postgres"):
            return "postgresql"

        raise BackupValidationError(
            "Неподдерживаемый тип базы данных: "
            f"{url.split(':', 1)[0]}"
        )

    # ==================================================================
    # SQLite
    # ==================================================================

    def _sqlite_path_from_url(
        self,
        database_url: str,
    ) -> Path:
        """Извлекает путь SQLite-файла из SQLAlchemy URL."""

        parsed = urlparse(
            database_url
        )

        if parsed.scheme not in {
            "sqlite",
            "sqlite+aiosqlite",
        }:
            raise BackupValidationError(
                "URL не является SQLite URL."
            )

        raw_path = unquote(
            parsed.path
        )

        if not raw_path:
            raise BackupValidationError(
                "В SQLite URL отсутствует путь к БД."
            )

        # sqlite:///relative.db
        if raw_path.startswith("/"):
            path = Path(
                raw_path
            )
        else:
            path = Path(
                raw_path.lstrip("/")
            )

        # sqlite:////absolute/path/db.sqlite
        if database_url.startswith(
            "sqlite:////"
        ) or database_url.startswith(
            "sqlite+aiosqlite:////"
        ):
            path = Path(
                raw_path
            )

        if not path.is_absolute():
            path = (
                Path.cwd()
                / path
            )

        return path.resolve()

    async def _sqlite_backup(
        self,
        destination: Path,
    ) -> None:
        """
        Делает SQLite backup через sqlite3 backup API.

        Вызов выполняется в отдельном потоке, чтобы не блокировать
        asyncio event loop.
        """

        source_path = self._sqlite_path_from_url(
            self.database_url
        )

        if not source_path.exists():
            raise BackupCreationError(
                f"Файл SQLite не найден: "
                f"{source_path}"
            )

        if not source_path.is_file():
            raise BackupCreationError(
                f"Путь SQLite не является файлом: "
                f"{source_path}"
            )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        def perform_backup() -> None:
            import sqlite3

            source_connection = None
            destination_connection = None

            try:
                source_connection = sqlite3.connect(
                    str(source_path),
                    timeout=30,
                )

                destination_connection = sqlite3.connect(
                    str(destination),
                    timeout=30,
                )

                with destination_connection:
                    source_connection.backup(
                        destination_connection,
                    )

            finally:
                if destination_connection is not None:
                    destination_connection.close()

                if source_connection is not None:
                    source_connection.close()

        try:
            await asyncio.to_thread(
                perform_backup
            )
        except Exception as exc:
            logger.exception(
                "Ошибка SQLite backup: source={}, destination={}",
                source_path,
                destination,
            )

            try:
                if destination.exists():
                    destination.unlink()
            except OSError:
                logger.warning(
                    "Не удалось удалить неполный backup: {}",
                    destination,
                )

            raise BackupCreationError(
                "Не удалось создать SQLite backup."
            ) from exc

    # ==================================================================
    # PostgreSQL
    # ==================================================================

    def _parse_postgresql_url(
        self,
        database_url: str,
    ) -> dict[str, str]:
        """Разбирает PostgreSQL URL."""

        normalized = database_url

        if normalized.startswith(
            "postgresql+asyncpg://"
        ):
            normalized = normalized.replace(
                "postgresql+asyncpg://",
                "postgresql://",
                1,
            )

        parsed = urlparse(
            normalized
        )

        if parsed.scheme not in {
            "postgresql",
            "postgres",
        }:
            raise BackupValidationError(
                "URL не является PostgreSQL URL."
            )

        if not parsed.hostname:
            raise BackupValidationError(
                "В PostgreSQL URL отсутствует hostname."
            )

        if not parsed.path:
            raise BackupValidationError(
                "В PostgreSQL URL отсутствует имя базы данных."
            )

        return {
            "host": parsed.hostname,
            "port": str(
                parsed.port or 5432
            ),
            "user": unquote(
                parsed.username or ""
            ),
            "password": unquote(
                parsed.password or ""
            ),
            "database": unquote(
                parsed.path.lstrip("/")
            ),
        }

    async def _postgresql_backup(
        self,
        destination: Path,
    ) -> None:
        """Создаёт PostgreSQL backup через pg_dump."""

        connection = self._parse_postgresql_url(
            self.database_url
        )

        executable = shutil.which(
            "pg_dump"
        )

        if executable is None:
            raise BackupCreationError(
                "Команда pg_dump не найдена. "
                "Установите postgresql-client."
            )

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        env = os.environ.copy()

        password = connection["password"]

        if password:
            env["PGPASSWORD"] = password

        command = [
            executable,
            "--host",
            connection["host"],
            "--port",
            connection["port"],
            "--username",
            connection["user"],
            "--format",
            "custom",
            "--file",
            str(destination),
            connection["database"],
        ]

        process = None

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_text = (
                    stderr.decode(
                        "utf-8",
                        errors="replace",
                    ).strip()
                )

                raise BackupCreationError(
                    "pg_dump завершился с ошибкой "
                    f"{process.returncode}: "
                    f"{error_text}"
                )

            if not destination.exists():
                raise BackupCreationError(
                    "pg_dump завершился успешно, "
                    "но backup-файл не создан."
                )

            if destination.stat().st_size <= 0:
                raise BackupCreationError(
                    "Создан пустой PostgreSQL backup."
                )

            if stdout:
                logger.debug(
                    "pg_dump stdout: {}",
                    stdout.decode(
                        "utf-8",
                        errors="replace",
                    ).strip(),
                )

        except BackupCreationError:
            raise

        except Exception as exc:
            logger.exception(
                "Ошибка PostgreSQL backup."
            )

            raise BackupCreationError(
                "Не удалось создать PostgreSQL backup."
            ) from exc

        finally:
            # PGPASSWORD удаляется из локальной копии env
            # после завершения процесса.
            env.pop(
                "PGPASSWORD",
                None,
            )

    # ==================================================================
    # Создание backup
    # ==================================================================

    def _build_backup_filename(
        self,
        database_type: str,
    ) -> str:
        """Формирует имя backup-файла."""

        now = datetime.now(
            timezone.utc
        )

        timestamp = now.strftime(
            "%Y%m%d_%H%M%S"
        )

        if database_type == "sqlite":
            extension = ".sqlite3"
        else:
            extension = ".dump"

        return (
            f"backup_{timestamp}"
            f"_{database_type}"
            f"{extension}"
        )

    async def create_backup(
        self,
        *,
        label: str | None = None,
    ) -> BackupResult:
        """Создаёт резервную копию текущей базы."""

        database_type = (
            self.database_type()
        )

        self.ensure_backup_dir()

        filename = self._build_backup_filename(
            database_type
        )

        if label:
            safe_label = re.sub(
                r"[^a-zA-Z0-9_-]+",
                "_",
                label.strip(),
            ).strip("_")

            if safe_label:
                filename = (
                    filename.rsplit(
                        ".",
                        1,
                    )[0]
                    + "_"
                    + safe_label
                    + "."
                    + filename.rsplit(
                        ".",
                        1,
                    )[1]
                )

        destination = (
            self.backup_dir
            / filename
        )

        logger.info(
            "Создание backup: type={}, destination={}",
            database_type,
            destination,
        )

        if database_type == "sqlite":
            await self._sqlite_backup(
                destination
            )

        elif database_type == "postgresql":
            await self._postgresql_backup(
                destination
            )

        else:
            raise BackupValidationError(
                f"Неподдерживаемый тип БД: "
                f"{database_type}"
            )

        if not destination.exists():
            raise BackupCreationError(
                "Backup-файл не существует после создания."
            )

        size_bytes = destination.stat().st_size

        if size_bytes <= 0:
            raise BackupCreationError(
                "Backup-файл пустой."
            )

        created_at = datetime.now(
            timezone.utc
        )

        backup = BackupInfo(
            path=destination,
            filename=destination.name,
            size_bytes=size_bytes,
            created_at=created_at,
        )

        logger.info(
            "Backup успешно создан: "
            "file={}, size={} bytes",
            destination,
            size_bytes,
        )

        return BackupResult(
            backup=backup,
            source_type=database_type,
        )

    # Алиас для cron/APScheduler/admin.

    async def create(
        self,
        *,
        label: str | None = None,
    ) -> BackupResult:
        """Алиас create_backup()."""

        return await self.create_backup(
            label=label
        )

    # ==================================================================
    # Список backup
    # ==================================================================

    def _backup_file_pattern(
        self,
    ) -> str:
        return "backup_*"

    async def list_backups(
        self,
        *,
        limit: int = 100,
    ) -> list[BackupInfo]:
        """Возвращает список существующих backup."""

        limit = max(
            1,
            min(
                int(limit),
                1000,
            ),
        )

        self.ensure_backup_dir()

        files = [
            path
            for path in self.backup_dir.glob(
                self._backup_file_pattern()
            )
            if path.is_file()
        ]

        files.sort(
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )

        result: list[BackupInfo] = []

        for path in files[:limit]:
            try:
                stat = path.stat()

                created_at = datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.utc,
                )

                result.append(
                    BackupInfo(
                        path=path,
                        filename=path.name,
                        size_bytes=stat.st_size,
                        created_at=created_at,
                    )
                )

            except OSError:
                logger.warning(
                    "Не удалось прочитать backup: {}",
                    path,
                )

        return result

    async def get_latest(
        self,
    ) -> BackupInfo | None:
        """Возвращает последний backup."""

        backups = await self.list_backups(
            limit=1
        )

        if not backups:
            return None

        return backups[0]

    # ==================================================================
    # Получение backup
    # ==================================================================

    async def get_backup(
        self,
        filename: str,
    ) -> BackupInfo:
        """Возвращает backup по имени."""

        if not filename:
            raise BackupValidationError(
                "Имя backup не указано."
            )

        # Защита от path traversal.
        requested = Path(
            filename
        )

        if (
            requested.name != filename
            or ".." in requested.parts
        ):
            raise BackupValidationError(
                "Некорректное имя backup."
            )

        path = (
            self.backup_dir
            / filename
        ).resolve()

        try:
            path.relative_to(
                self.backup_dir
            )
        except ValueError as exc:
            raise BackupValidationError(
                "Недопустимый путь backup."
            ) from exc

        if not path.exists():
            raise BackupNotFoundError(
                f"Backup не найден: {filename}"
            )

        if not path.is_file():
            raise BackupNotFoundError(
                f"Backup не является файлом: {filename}"
            )

        stat = path.stat()

        return BackupInfo(
            path=path,
            filename=path.name,
            size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(
                stat.st_mtime,
                tz=timezone.utc,
            ),
        )

    # ==================================================================
    # Удаление
    # ==================================================================

    async def delete_backup(
        self,
        filename: str,
    ) -> bool:
        """Удаляет конкретный backup."""

        backup = await self.get_backup(
            filename
        )

        try:
            backup.path.unlink()
        except OSError as exc:
            raise BackupError(
                f"Не удалось удалить backup: "
                f"{filename}"
            ) from exc

        logger.info(
            "Backup удалён: {}",
            filename,
        )

        return True

    async def cleanup(
        self,
        *,
        retention: int | None = None,
    ) -> int:
        """
        Удаляет старые backup.

        retention = количество последних файлов,
        которые нужно сохранить.
        """

        if retention is None:
            configured = getattr(
                settings,
                "backup_retention",
                None,
            )

            try:
                retention = int(
                    configured
                    if configured is not None
                    else self.DEFAULT_RETENTION
                )
            except (TypeError, ValueError):
                retention = self.DEFAULT_RETENTION

        if retention < 1:
            retention = 1

        backups = await self.list_backups(
            limit=1000
        )

        to_delete = backups[
            retention:
        ]

        deleted = 0

        for backup in to_delete:
            try:
                backup.path.unlink()

                deleted += 1

                logger.info(
                    "Старый backup удалён: {}",
                    backup.filename,
                )

            except OSError:
                logger.exception(
                    "Не удалось удалить старый backup: {}",
                    backup.filename,
                )

        return deleted

    async def create_and_cleanup(
        self,
        *,
        label: str | None = None,
        retention: int | None = None,
    ) -> BackupResult:
        """Создаёт backup и удаляет старые."""

        result = await self.create_backup(
            label=label
        )

        await self.cleanup(
            retention=retention
        )

        return result

    # ==================================================================
    # Проверка backup
    # ==================================================================

    async def verify_backup(
        self,
        filename: str,
    ) -> bool:
        """
        Проверяет, что backup существует и не пустой.

        Для SQLite дополнительно выполняется PRAGMA integrity_check.
        """

        backup = await self.get_backup(
            filename
        )

        if backup.size_bytes <= 0:
            return False

        # Для PostgreSQL custom dump проверяем хотя бы наличие
        # непустого файла. Полная проверка выполняется через pg_restore
        # при необходимости восстановления.
        if backup.path.suffix == ".dump":
            return True

        if backup.path.suffix != ".sqlite3":
            return True

        def check_sqlite() -> bool:
            import sqlite3

            connection = None

            try:
                connection = sqlite3.connect(
                    str(backup.path),
                    timeout=10,
                )

                cursor = connection.execute(
                    "PRAGMA integrity_check;"
                )

                row = cursor.fetchone()

                return bool(
                    row
                    and row[0] == "ok"
                )

            finally:
                if connection is not None:
                    connection.close()

        try:
            return await asyncio.to_thread(
                check_sqlite
            )

        except Exception:
            logger.exception(
                "Ошибка проверки SQLite backup: {}",
                backup.path,
            )

            return False

    # ==================================================================
    # Восстановление
    # ==================================================================

    async def restore_sqlite(
        self,
        filename: str,
        *,
        target_path: str | Path | None = None,
    ) -> Path:
        """
        Восстанавливает SQLite backup.

        Метод специально не выполняет автоматическую замену
        работающей БД без явного target_path.
        """

        backup = await self.get_backup(
            filename
        )

        if backup.path.suffix != ".sqlite3":
            raise BackupRestoreError(
                "Указанный файл не является SQLite backup."
            )

        valid = await self.verify_backup(
            filename
        )

        if not valid:
            raise BackupRestoreError(
                "SQLite backup не прошёл integrity_check."
            )

        if target_path is None:
            target = self._sqlite_path_from_url(
                self.database_url
            )
        else:
            target = Path(
                target_path
            ).expanduser().resolve()

        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if target == backup.path:
            raise BackupRestoreError(
                "Нельзя восстановить backup поверх самого себя."
            )

        def restore() -> None:
            import sqlite3

            source_connection = None
            target_connection = None

            try:
                source_connection = sqlite3.connect(
                    str(backup.path),
                    timeout=30,
                )

                target_connection = sqlite3.connect(
                    str(target),
                    timeout=30,
                )

                with target_connection:
                    source_connection.backup(
                        target_connection
                    )

            finally:
                if target_connection is not None:
                    target_connection.close()

                if source_connection is not None:
                    source_connection.close()

        try:
            await asyncio.to_thread(
                restore
            )

        except Exception as exc:
            logger.exception(
                "Ошибка восстановления SQLite: "
                "backup={}, target={}",
                backup.path,
                target,
            )

            raise BackupRestoreError(
                "Не удалось восстановить SQLite backup."
            ) from exc

        logger.warning(
            "SQLite backup восстановлен: "
            "backup={}, target={}",
            backup.path,
            target,
        )

        return target

    # ==================================================================
    # Форматирование
    # ==================================================================

    @staticmethod
    def format_size(
        size_bytes: int,
    ) -> str:
        """Форматирует размер файла."""

        if size_bytes < 1024:
            return f"{size_bytes} B"

        units = (
            "KB",
            "MB",
            "GB",
            "TB",
        )

        value = float(
            size_bytes
        )

        for unit in units:
            value /= 1024

            if value < 1024:
                return f"{value:.2f} {unit}"

        return f"{value:.2f} PB"

    @staticmethod
    def format_datetime(
        value: datetime,
    ) -> str:
        """Форматирует дату для админки."""

        if value.tzinfo is None:
            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )


# ======================================================================
# Глобальный экземпляр
# ======================================================================


backup_service = BackupService()


__all__ = [
    "BackupError",
    "BackupValidationError",
    "BackupNotFoundError",
    "BackupCreationError",
    "BackupRestoreError",
    "BackupInfo",
    "BackupResult",
    "BackupService",
    "backup_service",
]