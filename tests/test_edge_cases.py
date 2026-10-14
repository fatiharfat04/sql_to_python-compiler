"""Kasus tepi (``PROMPT.md`` Langkah 10, poin 2).

Cakupan yang diminta: string kosong, query hanya spasi/komentar, tabel kosong,
nama kolom sama di dua tabel, alias bertabrakan dengan nama tabel, angka sangat
besar, literal teks panjang, query dengan lima ``JOIN``, dan identifier yang
sama dengan keyword (dengan dan tanpa backtick).

Tiga lapis:

1. ``TOLAK`` — wajib ditolak **tepat** pada baris/kolom tertentu (tanpa MySQL).
2. ``KOMPILASI`` — wajib terkompilasi tanpa error (tanpa MySQL).
3. ``DIFERENSIAL`` — hasilnya identik dengan MySQL 8.4 sungguhan.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mysql_oracle import compare_query, fetch

from sqlcompiler.data_loader import load_tables_csv
from sqlcompiler.runtime import CompileResult, compile_sql, run_compiled
from sqlcompiler.schema_loader import load_schema_json

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = load_schema_json(ROOT / "examples" / "schema.json")
TABLES = load_tables_csv(ROOT / "examples", SCHEMA)

#: ``(id, sql, kode, baris, kolom)`` — wajib ditolak pada posisi persis itu.
TOLAK: list[tuple[str, str, str, int, int]] = [
    ("string_kosong", "", "SYN001", 1, 1),
    ("hanya_spasi", "     \n \t  ", "SYN001", 2, 5),
    ("hanya_komentar", "-- hanya komentar\n# komentar blok #\n", "SYN001", 3, 1),
    ("komentar_tanpa_spasi", "--cari tahu\n", "SYN001", 1, 1),
    (
        "kolom_bare_ambigu_dua_tabel",
        "SELECT nim FROM mahasiswa m JOIN nilai n ON m.nim = n.nim",
        "SEM003",
        1,
        8,
    ),
    (
        "alias_dipakai_dua_kali",
        "SELECT m.nim FROM mahasiswa m JOIN nilai m ON m.nim = m.nim",
        "SEM004",
        1,
        36,
    ),
    (
        "alias_menutupi_tabel_asli",
        "SELECT nilai.nim FROM mahasiswa AS nilai JOIN nilai ON nilai.nim = nilai.nim",
        "SEM004",
        1,
        47,
    ),
    ("keyword_sebagai_kolom", "SELECT order FROM mahasiswa", "SYN002", 1, 8),
    ("keyword_sebagai_alias", "SELECT nama AS order FROM mahasiswa", "SYN001", 1, 16),
    ("keyword_sebagai_tabel", "SELECT * FROM order", "SYN001", 1, 15),
    ("backtick_kolom_tak_ada", "SELECT `order` FROM mahasiswa", "SEM002", 1, 8),
    ("backtick_tabel_tak_ada", "SELECT nim FROM `order`", "SEM001", 1, 17),
    ("limit_negatif", "SELECT nama FROM mahasiswa LIMIT -1", "SYN001", 1, 34),
    ("select_tanpa_from", "SELECT 1 AS satu", "SYN001", 1, 17),
]

#: ``(id, sql)`` — wajib terkompilasi, lalu wajib cocok dengan MySQL 8.4.
DIFERENSIAL: list[tuple[str, str]] = [
    ("tabel_kosong", "SELECT nama FROM mahasiswa WHERE nim > 99999"),
    ("limit_0", "SELECT nama FROM mahasiswa LIMIT 0"),
    ("limit_besar", "SELECT nama FROM mahasiswa LIMIT 9999999999"),
    ("kolom_sama_dua_tabel", "SELECT m.nim, n.nim FROM mahasiswa m JOIN nilai n ON m.nim = n.nim"),
    (
        "lima_join",
        "SELECT a.nim, b.nim, c.nim, d.nim, e.nim FROM mahasiswa a "
        "JOIN nilai b ON a.nim = b.nim "
        "JOIN mahasiswa c ON a.nim = c.nim "
        "JOIN nilai d ON b.nim = d.nim "
        "JOIN mahasiswa e ON c.nim = e.nim",
    ),
    ("self_join", "SELECT a.nim, b.nim FROM mahasiswa a JOIN mahasiswa b ON a.nim = b.nim"),
    ("alias_sama_nama_tabel", "SELECT nama FROM mahasiswa AS nilai WHERE ipk > 3"),
    ("alias_menggantikan_tabel", "SELECT nilai.nim FROM mahasiswa AS nilai"),
    ("angka_24_digit", "SELECT 999999999999999999999999 AS besar FROM mahasiswa LIMIT 1"),
    (
        "desimal_38_digit",
        "SELECT 12345678901234567890.12345678901234567890 AS b FROM mahasiswa LIMIT 1",
    ),
    ("angka_besar_di_where", "SELECT nim FROM mahasiswa WHERE nim > 9223372036854775806 LIMIT 3"),
    (
        "perkalian_besar",
        "SELECT ipk * 1000000000000 AS b FROM mahasiswa WHERE ipk IS NOT NULL LIMIT 3",
    ),
    ("teks_5000_karakter", "SELECT '" + "x" * 5000 + "' AS teks FROM mahasiswa LIMIT 1"),
    (
        "teks_panjang_di_where",
        "SELECT nama FROM mahasiswa WHERE nama = '" + "y" * 2000 + "'",
    ),
    ("backtick_bukan_keyword", "SELECT `nim` FROM `mahasiswa` LIMIT 1"),
    ("backtick_kolom_nyata", "SELECT `kota` FROM mahasiswa LIMIT 1"),
    ("backtick_sebagai_alias", "SELECT nama AS `order` FROM mahasiswa LIMIT 1"),
    ("komentar_blok", "SELECT nama /* blok */ FROM mahasiswa LIMIT 1"),
]


def _hasil(sql: str) -> CompileResult:
    """Mengompilasi ``sql`` dan menggagalkan tes bila ada error."""
    result = compile_sql(sql, SCHEMA)
    assert not result.errors, f"{sql}\n" + "\n".join(
        f"{item.code}: {item.message}" for item in result.errors
    )
    return result


# --------------------------------------------------------------------------- #
# 1. Harus ditolak dengan posisi tepat                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql,kode,baris,kolom",
    [kasus[1:] for kasus in TOLAK],
    ids=[kasus[0] for kasus in TOLAK],
)
def test_ditolak_tepat_pada_posisinya(sql: str, kode: str, baris: int, kolom: int) -> None:
    """Input tak sah menghasilkan satu kode error pada baris/kolom yang benar."""
    result = compile_sql(sql, SCHEMA)

    assert result.errors, f"{sql!r} seharusnya ditolak."
    pertama = result.errors[0]
    assert pertama.code == kode
    assert (pertama.line, pertama.column) == (baris, kolom)
    # Gagal sebelum IR/codegen: tidak pernah ada kode yang bisa dieksekusi.
    assert result.code == ""
    assert result.plan is None


def test_string_kosong_bukan_exception() -> None:
    """String kosong dikembalikan sebagai error kompilasi, bukan di-``raise``."""
    result = compile_sql("", SCHEMA)

    assert isinstance(result, CompileResult)
    assert not result.ast
    assert not result.plan


# --------------------------------------------------------------------------- #
# 2. Harus terkompilasi                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql",
    [kasus[1] for kasus in DIFERENSIAL],
    ids=[kasus[0] for kasus in DIFERENSIAL],
)
def test_terkompilasi_tanpa_error(sql: str) -> None:
    """Kasus tepi yang sah tetap diterima (prinsip "lebih ketat, bukan menyimpang")."""
    result = _hasil(sql)

    assert result.tokens
    assert result.ast is not None
    assert result.plan is not None
    assert result.analysis is not None and result.analysis.ok
    assert result.code.startswith("import pandas as pd")


def test_jumlah_kasus_mencukupi() -> None:
    """Daftar ini benar-benar memuat seluruh kasus yang diminta Langkah 10 poin 2."""
    id_tolak = {kasus[0] for kasus in TOLAK}
    id_jalan = {kasus[0] for kasus in DIFERENSIAL}
    wajib = {
        "string_kosong",
        "hanya_spasi",
        "hanya_komentar",
        "tabel_kosong",
        "kolom_sama_dua_tabel",
        "alias_sama_nama_tabel",
        "angka_24_digit",
        "teks_5000_karakter",
        "lima_join",
        "keyword_sebagai_kolom",
        "backtick_kolom_nyata",
    }
    assert not wajib - (id_tolak | id_jalan), (
        f"belum dicakup: {sorted(wajib - id_tolak - id_jalan)}"
    )


# --------------------------------------------------------------------------- #
# 3. Diferensial terhadap MySQL 8.4 (tidak boleh di-skip)                      #
# --------------------------------------------------------------------------- #


@pytest.mark.mysql
@pytest.mark.parametrize(
    "sql",
    [kasus[1] for kasus in DIFERENSIAL],
    ids=[kasus[0] for kasus in DIFERENSIAL],
)
def test_kasus_tepi_cocok_dengan_mysql(sql: str) -> None:
    """Hasil kode identik dengan MySQL 8.4 untuk kasus tepi tersebut."""
    result = _hasil(sql)
    frame = run_compiled(result.code, TABLES)
    columns, rows = fetch(sql)
    ordered = result.ast is not None and bool(result.ast.order_by)
    problems = compare_query(columns, rows, frame, ordered)

    assert not problems, f"{sql}\n" + "\n".join(problems)
