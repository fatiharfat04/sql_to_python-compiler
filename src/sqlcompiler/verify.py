"""Eksekusi query pada MySQL dan perbandingan hasil (``SPEC.md`` §1, §15, §16).

Dua tanggung jawab:

- :func:`fetch` — menjalankan satu ``SELECT`` pada server MySQL 8.4;
- :func:`compare` / :func:`compare_query` / :func:`compare_frames` — membandingkan
  hasil oracle dengan hasil kompilasi memakai aturan ``SPEC.md`` §16: jumlah **dan**
  nama kolom sama, sel dibandingkan berdasarkan posisi, baris sebagai **multiset**
  kecuali query ber-``ORDER BY``, ``Decimal`` dikonversikan ke ``float`` dengan
  toleransi ``1e-9``, dan ``NULL`` sama dengan ``NULL``.

Aturan pembandingan sengaja berada di **satu** tempat sehingga tab verifikasi UI
dan harness oracle ``tests/mysql_oracle.py`` tidak pernah berbeda pendapat.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pandas as pd

from .schema_loader import parse_dsn

__all__ = [
    "DEFAULT_DSN",
    "TOLERANCE",
    "compare",
    "compare_frames",
    "compare_query",
    "connect",
    "fetch",
    "frame_columns",
    "frame_rows",
    "plain_cell",
]

#: DSN oracle; dapat diganti lewat variabel lingkungan ``SQLC_MYSQL_DSN``.
DEFAULT_DSN = os.environ.get("SQLC_MYSQL_DSN", "mysql://root:root@localhost:3307/sqlc_test")

#: Toleransi perbandingan bilangan titik koma (``SPEC.md`` §16).
TOLERANCE = 1e-9

#: Batas jumlah pesan kegagalan agar tampilan tetap terbaca.
_MAX_PROBLEMS = 20


def connect(dsn: str) -> Any:
    """Membuka koneksi pymysql dari ``dsn`` (``mysql://user:pass@host:port/db``)."""
    import pymysql

    options = parse_dsn(dsn)
    return pymysql.connect(
        host=str(options["host"]),
        port=int(options["port"]),
        user=str(options["user"]),
        password=str(options["password"]),
        database=str(options["database"]),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
    )


def plain_cell(value: Any) -> Any:
    """Menormalkan satu sel agar dapat dibandingkan lintas engine."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, Decimal):
        return float(value)
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (ValueError, AttributeError):  # pragma: no cover - proteksi defensif
            return value
    return value


def fetch(sql: str, dsn: str = DEFAULT_DSN) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Menjalankan ``sql`` pada MySQL; mengembalikan ``(nama kolom, baris)``."""
    connection = connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = list(cursor.fetchall())
            description = cursor.description or ()
            columns = [str(item[0]) for item in description]
    finally:
        connection.close()
    return columns, [tuple(plain_cell(value) for value in row) for row in rows]


def frame_columns(frame: pd.DataFrame) -> list[str]:
    """Nama kolom hasil kompiler sebagai ``list[str]``."""
    return [str(name) for name in frame.columns]


def frame_rows(frame: pd.DataFrame) -> list[tuple[Any, ...]]:
    """Seluruh sel hasil kompiler dalam bentuk baris ternormalisasi."""
    return [
        tuple(plain_cell(value) for value in row)
        for row in frame.itertuples(index=False, name=None)
    ]


def _same(expected: Any, actual: Any) -> bool:
    """Kesetaraan satu sel: NULL = NULL, teks persis, numerik dengan toleransi."""
    if expected is None:
        return actual is None
    if actual is None:
        return False
    expected_bool = isinstance(expected, bool)
    actual_bool = isinstance(actual, bool)
    if expected_bool or actual_bool:
        return expected_bool and actual_bool and bool(expected) == bool(actual)
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if isinstance(expected, int) and isinstance(actual, int):
            return expected == actual
        return abs(float(expected) - float(actual)) <= TOLERANCE
    return bool(expected == actual)


def _same_row(expected: tuple[Any, ...], actual: tuple[Any, ...]) -> bool:
    return len(expected) == len(actual) and all(
        _same(want, got) for want, got in zip(expected, actual, strict=True)
    )


def _compare_row(index: int, expected: tuple[Any, ...], actual: tuple[Any, ...]) -> list[str]:
    if len(expected) != len(actual):
        return [f"baris ke-{index}: panjang {len(expected)} vs {len(actual)}"]
    return [
        f"baris ke-{index} kolom ke-{column}: oracle {want!r} vs kompiler {got!r}"
        for column, (want, got) in enumerate(zip(expected, actual, strict=True))
        if not _same(want, got)
    ]


def _compare_multiset(
    expected_rows: Sequence[tuple[Any, ...]], actual_rows: Sequence[tuple[Any, ...]]
) -> list[str]:
    remaining = list(range(len(actual_rows)))
    problems: list[str] = []
    for index, expected in enumerate(expected_rows):
        match = next(
            (position for position in remaining if _same_row(expected, actual_rows[position])),
            None,
        )
        if match is None:
            problems.append(f"baris oracle ke-{index} tanpa pasangan: {expected!r}")
        else:
            remaining.remove(match)
    problems.extend(
        f"baris kompiler ke-{position} berlebihan: {actual_rows[position]!r}"
        for position in remaining
    )
    return problems


def compare(
    expected_columns: Sequence[str],
    expected_rows: Sequence[tuple[Any, ...]],
    actual_columns: Sequence[str],
    actual_rows: Sequence[tuple[Any, ...]],
    ordered: bool,
) -> list[str]:
    """Membandingkan hasil oracle dengan hasil kompiler (``SPEC.md`` §16)."""
    if len(expected_columns) != len(actual_columns):
        return [
            "jumlah kolom berbeda: "
            f"oracle {len(expected_columns)} vs kompiler {len(actual_columns)}"
        ]
    problems: list[str] = []
    if list(expected_columns) != list(actual_columns):
        problems.append(
            f"nama kolom berbeda: oracle {list(expected_columns)!r} "
            f"vs kompiler {list(actual_columns)!r}"
        )
    if len(expected_rows) != len(actual_rows):
        problems.append(
            f"jumlah baris berbeda: oracle {len(expected_rows)} vs kompiler {len(actual_rows)}"
        )
        return problems[:_MAX_PROBLEMS]
    if ordered:
        for index, (expected, actual) in enumerate(
            zip(expected_rows, actual_rows, strict=True)
        ):
            problems.extend(_compare_row(index, expected, actual))
    else:
        problems.extend(_compare_multiset(expected_rows, actual_rows))
    return problems[:_MAX_PROBLEMS]


def compare_query(
    oracle_columns: Sequence[str],
    oracle_rows: Sequence[tuple[Any, ...]],
    frame: pd.DataFrame,
    ordered: bool,
) -> list[str]:
    """Membandingkan hasil oracle dengan ``DataFrame`` hasil kompilasi."""
    return compare(
        oracle_columns,
        oracle_rows,
        frame_columns(frame),
        frame_rows(frame),
        ordered,
    )


def compare_frames(
    left: pd.DataFrame, right: pd.DataFrame, ordered: bool = False
) -> list[str]:
    """Membandingkan dua ``DataFrame`` (dipakai tes konsistensi CSV vs MySQL)."""
    return compare(
        frame_columns(left),
        frame_rows(left),
        frame_columns(right),
        frame_rows(right),
        ordered,
    )
