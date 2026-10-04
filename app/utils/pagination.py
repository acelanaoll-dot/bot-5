from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pagination:
    """
    Параметры пагинации.
    """

    page: int
    per_page: int
    total: int

    @property
    def offset(self) -> int:
        return (
            self.page - 1
        ) * self.per_page

    @property
    def pages(self) -> int:
        if self.total <= 0:
            return 1

        return (
            self.total + self.per_page - 1
        ) // self.per_page

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def build_pagination(
    page: int,
    per_page: int,
    total: int,
) -> Pagination:
    """
    Создаёт безопасную пагинацию.
    """

    if per_page <= 0:
        raise ValueError(
            "per_page должен быть больше нуля."
        )

    if total < 0:
        raise ValueError(
            "total не может быть отрицательным."
        )

    pages = max(
        1,
        (total + per_page - 1) // per_page,
    )

    page = max(
        1,
        min(page, pages),
    )

    return Pagination(
        page=page,
        per_page=per_page,
        total=total,
    )


__all__ = [
    "Pagination",
    "build_pagination",
]