from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Mapping


def rows_to_csv(
    rows: Iterable[Mapping[str, object]],
    *,
    delimiter: str = ";",
    encoding: str = "utf-8-sig",
) -> bytes:
    """
    Преобразует набор словарей в CSV.
    """

    rows = list(rows)

    if not rows:
        return b""

    fieldnames = list(rows[0].keys())

    buffer = io.StringIO(
        newline="",
    )

    writer = csv.DictWriter(
        buffer,
        fieldnames=fieldnames,
        delimiter=delimiter,
        extrasaction="ignore",
    )

    writer.writeheader()
    writer.writerows(rows)

    return buffer.getvalue().encode(
        encoding,
    )


__all__ = [
    "rows_to_csv",
]