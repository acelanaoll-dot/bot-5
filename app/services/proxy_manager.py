from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from app.config import settings


@dataclass(slots=True)
class ProxyState:
    """
    Состояние одного прокси.
    """

    url: str
    failures: int = 0
    healthy: bool = True


class ProxyManager:
    """
    Менеджер пула прокси.

    Используется для внешних HTTP-запросов,
    прежде всего для криптоплатёжных API.

    Формат proxies.txt:

        http://user:password@host:port
        socks5://user:password@host:port

    Пустые строки и строки с # игнорируются.
    """

    def __init__(
        self,
        proxy_file: str | Path | None = None,
    ) -> None:
        self.proxy_file = Path(
            proxy_file
            or settings.proxy_pool_file
        )

        self._proxies: list[ProxyState] = []
        self._current_index = 0

        self.load()

    def load(self) -> None:
        """
        Загружает прокси из файла.
        """

        self._proxies.clear()
        self._current_index = 0

        if not self.proxy_file.exists():
            logger.warning(
                "Файл прокси не найден: {}",
                self.proxy_file,
            )
            return

        try:
            lines = self.proxy_file.read_text(
                encoding="utf-8",
            ).splitlines()

        except OSError:
            logger.exception(
                "Не удалось прочитать файл прокси: {}",
                self.proxy_file,
            )
            return

        for raw_line in lines:
            line = raw_line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            self._proxies.append(
                ProxyState(
                    url=line,
                )
            )

        logger.info(
            "Загружено прокси: {}",
            len(self._proxies),
        )

    @property
    def count(self) -> int:
        """
        Количество загруженных прокси.
        """

        return len(self._proxies)

    @property
    def enabled(self) -> bool:
        """
        Активен ли пул прокси.
        """

        return (
            settings.proxy_rotation_enabled
            and bool(self._proxies)
        )

    def current(self) -> str | None:
        """
        Возвращает текущее здоровое прокси.
        """

        if not self._proxies:
            return None

        total = len(self._proxies)

        for offset in range(total):
            index = (
                self._current_index
                + offset
            ) % total

            proxy = self._proxies[index]

            if proxy.healthy:
                self._current_index = index
                return proxy.url

        logger.warning(
            "Все прокси помечены как неработающие."
        )

        return None

    def rotate(self) -> str | None:
        """
        Переключает текущий индекс на следующее прокси.
        """

        if not self._proxies:
            return None

        self._current_index = (
            self._current_index + 1
        ) % len(self._proxies)

        result = self.current()

        logger.info(
            "Ротация прокси выполнена: {}",
            result,
        )

        return result

    def report_success(
        self,
        proxy_url: str,
    ) -> None:
        """
        Отмечает прокси как рабочее.
        """

        for proxy in self._proxies:
            if proxy.url != proxy_url:
                continue

            proxy.failures = 0
            proxy.healthy = True

            logger.debug(
                "Прокси восстановлено: {}",
                proxy_url,
            )

            return

    def report_failure(
        self,
        proxy_url: str,
    ) -> None:
        """
        Регистрирует ошибку прокси.
        """

        for proxy in self._proxies:
            if proxy.url != proxy_url:
                continue

            proxy.failures += 1

            if (
                proxy.failures
                >= settings.proxy_failure_threshold
            ):
                proxy.healthy = False

                logger.warning(
                    "Прокси отключено после {} ошибок: {}",
                    proxy.failures,
                    proxy_url,
                )

            return

    def reset_proxy(
        self,
        proxy_url: str,
    ) -> None:
        """
        Принудительно восстанавливает прокси.
        """

        self.report_success(proxy_url)

    def reset_all(self) -> None:
        """
        Восстанавливает все прокси.
        """

        for proxy in self._proxies:
            proxy.failures = 0
            proxy.healthy = True

        self._current_index = 0

        logger.info(
            "Состояние всех прокси сброшено."
        )

    def get_httpx_kwargs(
        self,
    ) -> dict[str, object]:
        """
        Возвращает параметры для HTTP-клиента.

        Если прокси отключены или пул пуст,
        возвращается пустой словарь.
        """

        if not settings.crypto_proxy_enabled:
            return {}

        proxy = self.current()

        if not proxy:
            return {}

        return {
            "proxy": proxy,
        }

    def should_rotate_for_status(
        self,
        status_code: int,
    ) -> bool:
        """
        Определяет, нужно ли менять прокси
        после указанного HTTP-кода.
        """

        return (
            status_code
            in settings.proxy_rotate_status_codes
        )

    async def health_check(
        self,
        url: str | None = None,
    ) -> bool:
        """
        Проверяет доступность одного прокси.
        """

        proxy_url = (
            url
            or self.current()
        )

        if not proxy_url:
            return False

        try:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=(
                    settings.proxy_healthcheck_timeout
                ),
            ) as client:
                response = await client.get(
                    settings.proxy_healthcheck_url,
                )

            if response.is_success:
                self.report_success(
                    proxy_url
                )

                return True

            self.report_failure(
                proxy_url
            )

            return False

        except Exception:
            logger.exception(
                "Ошибка health-check прокси: {}",
                proxy_url,
            )

            self.report_failure(
                proxy_url
            )

            return False

    async def health_check_all(
        self,
    ) -> dict[str, bool]:
        """
        Проверяет все прокси из пула.
        """

        results: dict[str, bool] = {}

        for proxy in self._proxies:
            results[proxy.url] = (
                await self.health_check(
                    proxy.url
                )
            )

        return results

    def get_states(self) -> list[ProxyState]:
        """
        Возвращает копии состояний прокси
        для административного мониторинга.
        """

        return [
            ProxyState(
                url=proxy.url,
                failures=proxy.failures,
                healthy=proxy.healthy,
            )
            for proxy in self._proxies
        ]


proxy_manager = ProxyManager()


__all__ = [
    "ProxyState",
    "ProxyManager",
    "proxy_manager",
]