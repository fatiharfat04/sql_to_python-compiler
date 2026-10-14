"""Pemuat data: CSV dan MySQL menjadi ``dict[str, DataFrame]`` bertipe nullable (SPEC.md §3).

Sumber data dipakai untuk *menjalankan* dan *memverifikasi* kode hasil kompilasi.
Seluruh kolom memakai tipe pandas yang menerima ``pd.NA`` sehingga ``NULL`` MySQL
dan sel kosong pada CSV direpresentasikan dengan cara yang sama:

=============== ====================
Skema           dtype pandas
=============== ====================
``INT``         ``Int64``
``DOUBLE``      ``Float64``
``TEXT``/``NULL`` ``string``
``BOOLEAN``     ``boolean``
``UNSUPPORTED`` ``string``
=============== ====================

Berkas ``examples/*.csv`` dibaca sebagai teks lalu dikonversi mengikuti skema,
sehingga ``load_tables_csv(dir, schema)`` dan ``load_tables_mysql(dsn, names)``
menghasilkan isi yang identik baris demi baris.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import pandas as pd

from .ast_nodes import SelectStatement
from .schema_loader import introspect_mysql, parse_dsn
from .semantic import DataType, Schema

__all__ = ["coerce_frame", "load_tables_csv", "load_tables_mysql", "required_tables"]

#: Pemetaan tipe skema ke dtype pandas nullable.
_PANDAS_DTYPE: dict[DataType, str] = {
    DataType.INT: "Int64",
    DataType.DOUBLE: "Float64",
    DataType.TEXT: "string",
    DataType.BOOLEAN: "boolean",
    DataType.NULL: "string",
    DataType.UNSUPPORTED: "string",
}

#: Nilai teks yang diterima pada kolom bertipe ``BOOLEAN`` pada CSV.
_BOOLEAN_TEXT: dict[str, bool] = {
    "1": True,
    "0": False,
    "true": True,
    "false": False,
    "t": True,
    "f": False,
    "yes": True,
    "no": False,
}


def _dtype_for(data_type: DataType) -> str:
    """Dtype pandas untuk satu tipe skema."""
    return _PANDAS_DTYPE.get(data_type, "string")


def _empty(series: pd.Series, dtype: str) -> pd.Series:
    """Kolom kosong dengan dtype target (``to_numeric`` tidak stabil pada 0 baris)."""
    return pd.Series(index=series.index, dtype=dtype, name=series.name)


def _coerce_series(series: pd.Series, data_type: DataType) -> pd.Series:
    """Mengubah satu kolom mentah menjadi dtype nullable sesuai skema."""
    dtype = _dtype_for(data_type)
    if len(series) == 0:
        return _empty(series, dtype)
    if data_type is DataType.INT:
        return pd.to_numeric(series, errors="coerce").astype("Int64")
    if data_type is DataType.DOUBLE:
        return pd.to_numeric(series, errors="coerce").astype("Float64")
    if data_type is DataType.BOOLEAN:
        lowered = series.astype("string").str.lower()
        mapped = lowered.map(_BOOLEAN_TEXT)
        return pd.Series(mapped, dtype="boolean", index=series.index)
    return series.astype("string")


def _infer_dtype(series: pd.Series) -> Literal["Int64", "Float64", "string"]:
    """Menebak dtype bila skema tidak tersedia (dipakai hanya sebagai cadangan)."""
    values = series.dropna()
    if len(values) == 0:
        return "string"
    numeric = pd.to_numeric(values, errors="coerce")
    if not bool(numeric.notna().all()):
        return "string"
    if all(float(value).is_integer() for value in numeric.astype("float64")):
        return "Int64"
    return "Float64"


def coerce_frame(frame: pd.DataFrame, table_name: str, schema: Schema | None) -> pd.DataFrame:
    """Menormalkan ``frame`` mentah agar dtype-nya nullable dan urutannya sesuai skema.

    Kolom skema yang tidak ada pada sumber diisi ``pd.NA``; kolom di luar skema
    tetap dipertahankan sebagai ``string`` di belakang kolom skema.
    """
    table = None if schema is None else schema.get(table_name)
    if schema is not None and table is None:
        available = ", ".join(schema.table_names)
        raise ValueError(f"Tabel '{table_name}' tidak ada di skema (tersedia: {available}).")

    columns = [str(column) for column in frame.columns]
    coerced: dict[str, pd.Series] = {}
    if table is None:
        for column in columns:
            source = frame[column]
            coerced[column] = source.astype(_infer_dtype(source))
        return pd.DataFrame(coerced)

    known = {column.name: column for column in table.columns}
    for column in columns:
        spec = known.get(column.lower())
        source = frame[column]
        coerced[column] = (
            source.astype("string") if spec is None else _coerce_series(source, spec.type)
        )
    present = {column.lower() for column in columns}
    for spec in table.columns:
        if spec.name not in present:
            coerced[spec.name] = pd.Series(
                [pd.NA] * len(frame), dtype=_dtype_for(spec.type), name=spec.name
            )

    ordered: dict[str, pd.Series] = {}
    for spec in table.columns:
        if spec.name in coerced:
            ordered[spec.name] = coerced.pop(spec.name)
    ordered.update(coerced)
    if not ordered:
        return pd.DataFrame(index=frame.index)
    return pd.DataFrame(ordered)


def required_tables(ast: SelectStatement, schema: Schema) -> tuple[str, ...]:
    """Nama tabel (huruf kecil) yang dibaca ``ast``, menurut urut kemunculan.

    Dipakai bersama :func:`load_tables_mysql` agar hanya tabel yang benar-benar
    dipakai query yang diambil dari server, dan namanya selalu cocok di Linux
    (``schema.get`` menormalkan huruf besar/kecil).
    """
    names: dict[str, None] = {}
    for ref in (ast.from_, *(join.table for join in ast.joins)):
        table = schema.get(ref.name)
        names.setdefault(table.name if table is not None else ref.name.lower(), None)
    return tuple(names)


def load_tables_csv(
    directory: str | Path, schema: Schema | None = None
) -> dict[str, pd.DataFrame]:
    """Membaca seluruh ``*.csv`` di ``directory`` menjadi frame bertipe nullable.

    Sel kosong menjadi ``pd.NA``. Dengan ``schema`` dtypes mengikuti skema dan
    nama tabel adalah nama berkas tanpa ekstensi (huruf kecil); tanpa ``schema``
    dtypes ditebak dari isi kolom.
    """
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"'{root}' bukan direktori.")
    tables: dict[str, pd.DataFrame] = {}
    for path in sorted(root.glob("*.csv")):
        name = path.stem.lower()
        raw = pd.read_csv(
            path, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8"
        )
        tables[name] = coerce_frame(raw, name, schema)
    return tables


def load_tables_mysql(
    dsn: str,
    table_names: Sequence[str],
    schema: Schema | None = None,
) -> dict[str, pd.DataFrame]:
    """Membaca ``table_names`` dari server MySQL menjadi frame bertipe nullable.

    ``schema`` diintrospeksi dari ``dsn`` bila tidak diberikan. Hasilnya identik
    dengan :func:`load_tables_csv` untuk data yang sama.
    """
    import pymysql

    options = parse_dsn(dsn)
    resolved = introspect_mysql(dsn) if schema is None else schema
    connection = pymysql.connect(
        host=str(options["host"]),
        port=int(options["port"]),
        user=str(options["user"]),
        password=str(options["password"]),
        database=str(options["database"]),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
    )
    tables: dict[str, pd.DataFrame] = {}
    try:
        with connection.cursor() as cursor:
            for name in table_names:
                quoted = str(name).replace("`", "``")
                cursor.execute(f"SELECT * FROM `{quoted}`")
                rows = list(cursor.fetchall())
                description = cursor.description or ()
                columns = [str(item[0]) for item in description]
                raw = pd.DataFrame(rows, columns=columns)
                tables[str(name).lower()] = coerce_frame(raw, str(name).lower(), resolved)
    finally:
        connection.close()
    return tables
