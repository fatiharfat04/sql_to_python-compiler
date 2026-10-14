"""Pemuat skema dari berkas JSON dan introspeksi MySQL (``SPEC.md`` §7).

Dua sumber skema didukung:

1. ``load_schema_json(path)`` - berkas ``{"tabel": {"kolom": "TIPE"}, ...}``.
2. ``introspect_mysql(dsn)`` - ``information_schema.COLUMNS`` milik server MySQL.

``dump_schema_json`` membalik prosesnya sehingga ``sqlc schema --dsn ... --out ...``
menghasilkan berkas yang bisa dibaca kembali oleh ``load_schema_json``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlparse

from .semantic import ColumnDef, DataType, Schema, Table

__all__ = [
    "dump_schema_json",
    "introspect_mysql",
    "load_schema_json",
    "mysql_type_to_data_type",
    "parse_dsn",
    "schema_from_mapping",
]

#: Nama tipe MySQL yang diterima oleh ``schema_from_mapping``.
_JSON_TYPES: dict[str, DataType] = {
    "INT": DataType.INT,
    "DOUBLE": DataType.DOUBLE,
    "TEXT": DataType.TEXT,
    "BOOLEAN": DataType.BOOLEAN,
    "NULL": DataType.NULL,
    "UNSUPPORTED": DataType.UNSUPPORTED,
}

_INT_TYPES = frozenset({"tinyint", "smallint", "mediumint", "int", "integer", "bigint"})
_DOUBLE_TYPES = frozenset({"float", "double", "real"})
_DECIMAL_TYPES = frozenset({"decimal", "numeric"})
_TEXT_TYPES = frozenset({"char", "varchar", "tinytext", "text", "mediumtext", "longtext"})


def mysql_type_to_data_type(column_type: str) -> tuple[DataType, str | None]:
    """Memetakan ``COLUMN_TYPE`` MySQL ke tipe kompiler (``SPEC.md`` §7).

    Returns:
        ``(tipe, peringatan)``; ``peringatan`` ``None`` bila tidak ada.
    """
    raw = column_type.strip().lower()
    base = raw.split("(", 1)[0].split()[0] if raw else ""
    if base in _INT_TYPES or base in {"bool", "boolean"}:
        return DataType.INT, None
    if base in _DOUBLE_TYPES:
        return DataType.DOUBLE, None
    if base in _DECIMAL_TYPES:
        return DataType.DOUBLE, f"{column_type}: presisi desimal hilang"
    if base in _TEXT_TYPES:
        return DataType.TEXT, None
    return DataType.UNSUPPORTED, None


def schema_from_mapping(mapping: Mapping[str, Mapping[str, str]]) -> Schema:
    """Membangun :class:`Schema` dari struktur dict seperti ``schema.json``."""
    tables: list[Table] = []
    for table_name, columns in mapping.items():
        if not isinstance(columns, Mapping):
            raise ValueError(
                f"Tabel '{table_name}' harus berupa objek {{kolom: tipe}}, "
                f"ditemukan {type(columns).__name__}."
            )
        defs: list[ColumnDef] = []
        for column_name, type_name in columns.items():
            key = str(type_name).strip().upper()
            if key not in _JSON_TYPES:
                raise ValueError(
                    f"Tipe '{type_name}' pada '{table_name}.{column_name}' tidak dikenal; "
                    f"gunakan salah satu dari {sorted(_JSON_TYPES)}."
                )
            defs.append(ColumnDef(name=str(column_name).lower(), type=_JSON_TYPES[key]))
        tables.append(Table(name=str(table_name).lower(), columns=tuple(defs)))
    return Schema(tables=tuple(tables))


def load_schema_json(path: str | Path) -> Schema:
    """Membaca skema dari berkas JSON."""
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Berkas skema {file_path} bukan JSON valid: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Berkas skema {file_path} harus berisi objek JSON di tingkat atas.")
    return schema_from_mapping(payload)


def dump_schema_json(schema: Schema, path: str | Path) -> None:
    """Menulis ``schema`` ke berkas JSON dengan format yang didukung pembaca."""
    payload: dict[str, dict[str, str]] = {}
    for table in schema.tables:
        payload[table.name] = {column.name: str(column.type) for column in table.columns}
    Path(path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def parse_dsn(dsn: str) -> dict[str, str | int]:
    """Murai DSN ``mysql://user:pass@host:port/db`` menjadi argumen koneksi."""
    parsed = urlparse(dsn)
    if parsed.scheme and parsed.scheme not in {"mysql", "mysql2", "tcp"}:
        raise ValueError(f"Skema DSN '{parsed.scheme}' tidak didukung; gunakan mysql://.")
    if not parsed.path or parsed.path == "/":
        raise ValueError("DSN harus memuat nama basis data, mis. mysql://root:root@host/db.")
    options: dict[str, str | int] = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username) if parsed.username else "root",
        "password": unquote(parsed.password) if parsed.password else "",
        "database": unquote(parsed.path.lstrip("/")),
    }
    return options


def introspect_mysql(dsn: str) -> Schema:
    """Membaca skema dari ``information_schema.COLUMNS`` server MySQL."""
    import pymysql

    options = parse_dsn(dsn)
    connection = pymysql.connect(
        host=str(options["host"]),
        port=int(options["port"]),
        user=str(options["user"]),
        password=str(options["password"]),
        database=str(options["database"]),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME AS tbl, COLUMN_NAME AS col, COLUMN_TYPE AS ctype "
                "FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
                (str(options["database"]),),
            )
            rows = list(cursor.fetchall())
    finally:
        connection.close()

    grouped: dict[str, list[ColumnDef]] = {}
    for row in rows:
        table_name = str(row["tbl"]).lower()
        column_name = str(row["col"]).lower()
        column_type = str(row["ctype"])
        tipe, warning = mysql_type_to_data_type(column_type)
        grouped.setdefault(table_name, []).append(
            ColumnDef(
                name=column_name,
                type=tipe,
                mysql_type=column_type,
                warning=None if warning is None else f"{table_name}.{column_name}: {warning}",
            )
        )
    tables = tuple(
        Table(name=name, columns=tuple(columns)) for name, columns in sorted(grouped.items())
    )
    return Schema(tables=tables)
