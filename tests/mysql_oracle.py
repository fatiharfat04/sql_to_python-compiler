"""Harness oracle MySQL 8.4 untuk tes diferensial (``SPEC.md`` §16).

Modul ini memeriksa **lingkungan** oracle (versi, ``sql_mode``, kolasi) dan
menyediakan kembali aturan pembandingan dari :mod:`sqlcompiler.verify` sehingga
tes diferensial, tes konsistensi, dan tab verifikasi UI memakai **satu**
implementasi yang sama: jumlah kolom sama, sel dibandingkan **berdasarkan posisi
kolom**, baris sebagai **multiset** kecuali query ber-``ORDER BY``, ``Decimal``
dikonversikan ke ``float`` dengan toleransi ``1e-9``, dan ``NULL`` sama dengan
``NULL``.

Bukan bagian dari paket ``sqlcompiler``: modul ini hanya dipakai oleh
``tests/test_differential.py`` dan ``tests/test_cli.py`` dan tidak ikut
diperiksa ``mypy --strict src``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlcompiler.verify import (
    DEFAULT_DSN,
    TOLERANCE,
    compare,
    compare_frames,
    compare_query,
    connect,
    fetch,
    frame_columns,
    frame_rows,
)

__all__ = [
    "DEFAULT_DSN",
    "EXPECTED_SQL_MODE",
    "TOLERANCE",
    "ServerFacts",
    "compare",
    "compare_frames",
    "compare_query",
    "fetch",
    "frame_columns",
    "frame_rows",
    "server_facts",
]

#: ``sql_mode`` bawaan MySQL 8.4 sesuai ``SPEC.md`` §2.
EXPECTED_SQL_MODE: frozenset[str] = frozenset(
    {
        "ONLY_FULL_GROUP_BY",
        "STRICT_TRANS_TABLES",
        "NO_ZERO_IN_DATE",
        "NO_ZERO_DATE",
        "ERROR_FOR_DIVISION_BY_ZERO",
        "NO_ENGINE_SUBSTITUTION",
    }
)


@dataclass(frozen=True)
class ServerFacts:
    """Fakta server oracle yang diverifikasi sebelum tes diferensial."""

    version: str
    sql_mode: str
    collation_server: str
    character_set_server: str

    @property
    def sql_mode_set(self) -> frozenset[str]:
        return frozenset(part.strip() for part in self.sql_mode.split(",") if part.strip())


def server_facts(dsn: str = DEFAULT_DSN) -> ServerFacts:
    """Membaca versi, ``sql_mode``, dan kolasi server oracle."""
    connection = connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT VERSION(), @@sql_mode, @@collation_server, @@character_set_server"
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:  # pragma: no cover - SELECT tanpa baris tidak mungkin
        raise RuntimeError("server tidak mengembalikan @@version.")
    return ServerFacts(
        version=str(row[0]),
        sql_mode=str(row[1]),
        collation_server=str(row[2]),
        character_set_server=str(row[3]),
    )
