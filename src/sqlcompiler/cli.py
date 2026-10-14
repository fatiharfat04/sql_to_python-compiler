"""CLI ``sqlc`` berbasis typer + rich (``SPEC.md`` §15).

Empat perintah: ``compile`` (cetak kode Python), ``run`` (kompilasi, eksekusi, dan
cetak tabel hasil), ``explain`` (token, AST, plan, kode), serta ``schema`` (ekspor
skema hasil introspeksi). Seluruhnya hanya memanggil fasad
:func:`~sqlcompiler.runtime.compile_sql` / :func:`~sqlcompiler.runtime.run_compiled`
dan loader; logika kompilasi tidak diduplikasi.

Exit code: ``0`` sukses, ``1`` error kompilasi, ``2`` error runtime/pemakaian.
Error dirender memakai :func:`~sqlcompiler.errors.render_errors` sehingga
mempunyai baris, kolom, dan penunjuk ``^^^^`` (``SPEC.md`` §9).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Any, Never

import pandas as pd
import typer
from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .ast_nodes import SelectStatement
from .data_loader import load_tables_csv, load_tables_mysql, required_tables
from .errors import render_errors
from .ir import format_plan
from .lexer import format_tokens
from .parser import pretty_print
from .runtime import CompileResult, compile_sql, run_compiled
from .schema_loader import dump_schema_json, introspect_mysql, load_schema_json
from .semantic import Schema

__all__ = ["EXIT_COMPILE", "EXIT_OK", "EXIT_RUNTIME", "app"]

EXIT_OK = 0
EXIT_COMPILE = 1
EXIT_RUNTIME = 2

#: Keluaran biasa (bukan kode hasil kompilasi); markup dimatikan agar teks query aman.
_OUT = Console(markup=False, soft_wrap=True)
#: Keluaran galat; lebar tidak dibatasi sehingga penunjuk ``^^^^`` tidak terpotong.
_ERR = Console(stderr=True, markup=False, soft_wrap=True)


class CliError(Exception):
    """Kesalahan pemakaian atau runtime yang menghasilkan exit code 2."""


app = typer.Typer(
    name="sqlc",
    help="Kompiler query MySQL 8.4 ke Python (pandas).",
    no_args_is_help=True,
    add_completion=False,
)

SqlArgument = Annotated[
    str | None,
    typer.Argument(help="Pernyataan SELECT; boleh kosong bila memakai --file."),
]
FileOption = Annotated[
    Path | None,
    typer.Option("--file", "-f", help="Baca SQL dari berkas."),
]
SchemaOption = Annotated[
    Path | None,
    typer.Option("--schema", help="Berkas skema JSON (alternatif --dsn)."),
]
DsnOption = Annotated[
    str | None,
    typer.Option(
        "--dsn",
        envvar="SQLC_MYSQL_DSN",
        help="DSN MySQL, mis. mysql://root:root@localhost:3307/sqlc_test "
        "(default dari variabel SQLC_MYSQL_DSN).",
    ),
]
DataOption = Annotated[
    Path | None,
    typer.Option("--data", help="Direktori berisi berkas CSV tabel."),
]


# --------------------------------------------------------------------------- #
# Pembantu internal                                                            #
# --------------------------------------------------------------------------- #


def _runtime_error(message: str) -> Never:
    """Melaporkan galat runtime ke stderr lalu keluar dengan kode 2."""
    _ERR.print(f"Error: {message}")
    raise typer.Exit(EXIT_RUNTIME)


def _read_sql(sql: str | None, file: Path | None) -> str:
    """Mengambil sumber SQL dari argumen posisional atau ``--file``."""
    if sql is not None and file is not None:
        raise CliError("beri SQL sebagai argumen ATAU --file, bukan keduanya.")
    if file is not None:
        try:
            return file.read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(f"tidak dapat membaca {file}: {exc}") from exc
    if sql is not None:
        return sql
    raise CliError("SQL tidak diberikan; tulis query sebagai argumen atau pakai --file.")


def _resolve_schema(schema: Path | None, dsn: str | None) -> Schema:
    """Membaca skema dari ``--schema``, atau mengintrospeksi ``--dsn`` bila tidak ada."""
    if schema is not None:
        try:
            return load_schema_json(schema)
        except (OSError, ValueError) as exc:
            raise CliError(str(exc)) from exc
    if dsn:
        try:
            return introspect_mysql(dsn)
        except Exception as exc:
            raise CliError(f"gagal mengintrospeksi skema dari {dsn}: {exc}") from exc
    raise CliError("sumber skema tidak diberikan; pakai --schema <berkas> atau --dsn <mysql://...>.")


def _prepare(
    sql: str | None,
    file: Path | None,
    schema: Path | None,
    dsn: str | None,
) -> tuple[str, Schema]:
    """Membaca sumber SQL dan skema sekaligus; kegagalan menjadi exit code 2."""
    try:
        source = _read_sql(sql, file)
        return source, _resolve_schema(schema, dsn)
    except CliError as exc:
        _runtime_error(str(exc))


def _compile_or_exit(source: str, schema: Schema) -> CompileResult:
    """Memanggil fasad ``compile_sql``; galat kompilasi dirender dengan penunjuk (kode 1)."""
    result = compile_sql(source, schema)
    if result.errors:
        _ERR.print(render_errors(result.errors, source))
        raise typer.Exit(EXIT_COMPILE)
    return result


def _load_tables(
    schema: Schema,
    ast: SelectStatement,
    dsn: str | None,
    data: Path | None,
) -> dict[str, pd.DataFrame]:
    """Menyiapkan tabel dari MySQL (``--dsn``) atau CSV (``--data``)."""
    if dsn:
        try:
            return load_tables_mysql(dsn, required_tables(ast, schema), schema)
        except Exception as exc:
            raise CliError(f"gagal membaca data dari {dsn}: {exc}") from exc
    if data is not None:
        try:
            return load_tables_csv(data, schema)
        except Exception as exc:
            raise CliError(f"gagal membaca CSV dari {data}: {exc}") from exc
    raise CliError("sumber data tidak diberikan; pakai --dsn <mysql://...> atau --data <dir>.")


def _section(console: Console, title: str, body: str) -> None:
    """Mencetak judul bagian lalu isinya tanpa pembungkusan ulang."""
    console.print(Text(f"== {title} ==", style="bold"))
    console.print(body if body else "-")


def _cell(value: Any) -> str:
    """Merekam satu sel tabel hasil; nilai hilang tampil sebagai ``NULL``."""
    if value is None or value is pd.NA:
        return "NULL"
    if isinstance(value, float) and math.isnan(value):
        return "NULL"
    return str(value)


def _print_frame(frame: pd.DataFrame) -> None:
    """Mencetak ``frame`` sebagai tabel rich (huruf ASCII agar aman di konsol apa pun)."""
    table = Table(box=box.SIMPLE, header_style="bold")
    for column in frame.columns:
        table.add_column(str(column))
    for row in frame.itertuples(index=False, name=None):
        table.add_row(*[_cell(value) for value in row])
    _OUT.print(table)
    _OUT.print(f"{len(frame)} baris, {len(frame.columns)} kolom")


# --------------------------------------------------------------------------- #
# Perintah                                                                     #
# --------------------------------------------------------------------------- #


@app.command()
def compile(  # nama perintah ditentukan SPEC.md §15
    sql: SqlArgument = None,
    file: FileOption = None,
    schema: SchemaOption = None,
    dsn: DsnOption = None,
) -> None:
    """Kompilasi query lalu cetak kode Python apa adanya (dapat dialihkan ke berkas)."""
    source, skema = _prepare(sql, file, schema, dsn)
    result = _compile_or_exit(source, skema)
    typer.echo(result.code, nl=False)


@app.command()
def run(
    sql: SqlArgument = None,
    file: FileOption = None,
    schema: SchemaOption = None,
    dsn: DsnOption = None,
    data: DataOption = None,
) -> None:
    """Kompilasi query, jalankan kodenya pada data, lalu cetak tabel hasil."""
    source, skema = _prepare(sql, file, schema, dsn)
    result = _compile_or_exit(source, skema)
    assert result.ast is not None  # dijamin oleh errors yang kosong
    try:
        tables = _load_tables(skema, result.ast, dsn, data)
    except CliError as exc:
        _runtime_error(str(exc))
    try:
        frame = run_compiled(result.code, tables)
    except Exception as exc:
        _runtime_error(f"galat runtime: {type(exc).__name__}: {exc}")
    _print_frame(frame)


@app.command()
def explain(
    sql: SqlArgument = None,
    file: FileOption = None,
    schema: SchemaOption = None,
    dsn: DsnOption = None,
) -> None:
    """Tampilkan token, AST, plan, dan kode hasil kompilasi (SPEC.md §15)."""
    source, skema = _prepare(sql, file, schema, dsn)
    result = compile_sql(source, skema)
    _section(_OUT, "TOKEN", format_tokens(result.tokens))
    _section(_OUT, "AST", pretty_print(result.ast) if result.ast is not None else "")
    _section(_OUT, "PLAN", format_plan(result.plan) if result.plan is not None else "")
    _section(_OUT, "KODE", result.code)
    if result.errors:
        _ERR.print(render_errors(result.errors, source))
        raise typer.Exit(EXIT_COMPILE)


@app.command()
def schema(  # nama perintah ditentukan SPEC.md §15
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            envvar="SQLC_MYSQL_DSN",
            help="DSN MySQL sumber skema (default dari SQLC_MYSQL_DSN).",
        ),
    ],
    out: Annotated[Path, typer.Option("--out", help="Berkas JSON tujuan.")],
) -> None:
    """Introspeksi skema dari server MySQL lalu tulis ke berkas JSON."""
    try:
        skema = introspect_mysql(dsn)
    except Exception as exc:
        _runtime_error(f"gagal mengintrospeksi skema dari {dsn}: {exc}")
    try:
        dump_schema_json(skema, out)
    except OSError as exc:
        _runtime_error(f"gagal menulis {out}: {exc}")
    _OUT.print(f"{len(skema.tables)} tabel ditulis ke {out}")
