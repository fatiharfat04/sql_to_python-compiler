"""Antarmuka Streamlit: editor query, kode hasil, tab pendukung, dan tab verifikasi.

Merujuk ``SPEC.md`` §1 (gambaran hasil akhir) dan §15 (antarmuka). Susunan layarnya:

- editor **Query MySQL** + tombol **Kompilasi**;
- **panel utama** berupa kode Python hasil kompilasi, dengan tombol salin bawaan
  ``st.code`` dan tombol **Unduh .py**;
- tab ``Token | AST | Semantik | Plan`` sebagai tab pendukung;
- tab ``Verifikasi`` yang mengeksekusi kode pada data lalu menampilkan hasil Python
  **berdampingan** dengan hasil MySQL 8.4 beserta penanda cocok/tidak cocok;
- sidebar memilih sumber **skema** (berkas JSON atau introspeksi MySQL) dan sumber
  **data** (direktori CSV atau MySQL), plus DSN oracle.

Catatan penting: berkas ini memakai **impor absolut** (``from sqlcompiler...``)
bukan impor relatif seperti modul lain. Streamlit mengeksekusi skrip sebagai modul
``__main__`` tanpa induk paket, sehingga ``from .data_loader import ...`` akan
melempar ``ImportError: attempted relative import with no known parent package``.

UI **hanya** memanggil fasad :func:`~sqlcompiler.runtime.compile_sql`,
:func:`~sqlcompiler.runtime.run_compiled`, loader, pembanding hasil, dan
:func:`~sqlcompiler.errors.render_errors`; tidak ada logika kompilasi yang
diduplikasi (``SPEC.md`` §15).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from sqlcompiler.ast_nodes import Column, Star
from sqlcompiler.data_loader import load_tables_csv, load_tables_mysql, required_tables
from sqlcompiler.errors import render_errors
from sqlcompiler.ir import format_plan
from sqlcompiler.lexer import format_tokens
from sqlcompiler.parser import pretty_print
from sqlcompiler.runtime import CompileResult, compile_sql, run_compiled
from sqlcompiler.schema_loader import introspect_mysql, load_schema_json
from sqlcompiler.semantic import Schema
from sqlcompiler.verify import DEFAULT_DSN, compare_query, fetch

__all__: list[str] = []

#: Akar repositori, sehingga jalur contoh tetap benar walau ``streamlit run``
#: dipanggil dari direktori lain.
_AKAR = Path(__file__).resolve().parents[2]
_PATH_SKEMA = str(_AKAR / "examples" / "schema.json")
_PATH_DATA = str(_AKAR / "examples")

#: Label pilihan sumber di sidebar.
_MODE_JSON = "Berkas JSON"
_MODE_DSN = "Introspeksi MySQL"
_MODE_CSV = "CSV"
_MODE_MYSQL = "MySQL"


@dataclass(frozen=True)
class Konfigurasi:
    """Pilihan sumber skema dan data yang dibaca dari sidebar."""

    mode_skema: str
    path_skema: str
    mode_data: str
    path_data: str
    dsn: str


@dataclass(frozen=True)
class HasilKompilasi:
    """Hasil menekan tombol **Kompilasi**; disimpan pada ``st.session_state``."""

    sumber: str
    skema: Schema | None
    hasil: CompileResult | None
    galat: str | None


@dataclass(frozen=True)
class HasilVerifikasi:
    """Hasil menekan tombol **Jalankan** pada tab Verifikasi."""

    galat: str | None
    frame: pd.DataFrame | None
    oracle: pd.DataFrame | None
    masalah: list[str]


def _baca_sidebar() -> Konfigurasi:
    """Membaca pilihan sidebar: sumber skema, sumber data, dan DSN oracle."""
    with st.sidebar:
        st.header("Sumber skema")
        mode_skema = st.radio(
            "Skema dari",
            (_MODE_JSON, _MODE_DSN),
            key="mode_skema",
            horizontal=True,
        )
        path_skema = ""
        if mode_skema != _MODE_DSN:
            path_skema = st.text_input("Berkas skema JSON", _PATH_SKEMA, key="path_skema")

        st.header("Sumber data")
        mode_data = st.radio(
            "Data dari",
            (_MODE_CSV, _MODE_MYSQL),
            key="mode_data",
            horizontal=True,
        )
        path_data = ""
        if mode_data != _MODE_MYSQL:
            path_data = st.text_input("Direktori CSV", _PATH_DATA, key="path_data")

        dsn = st.text_input(
            "DSN MySQL (oracle)",
            DEFAULT_DSN,
            key="dsn",
            help=(
                "Dipakai untuk introspeksi skema, membaca data, dan sisi MySQL pada "
                "tab Verifikasi. Default mengikuti variabel lingkungan SQLC_MYSQL_DSN."
            ),
        )
        st.caption("Tab Verifikasi selalu dibandingkan dengan server MySQL 8.4 di DSN itu.")

    return Konfigurasi(
        mode_skema=mode_skema or _MODE_JSON,
        path_skema=path_skema or _PATH_SKEMA,
        mode_data=mode_data or _MODE_CSV,
        path_data=path_data or _PATH_DATA,
        dsn=dsn or DEFAULT_DSN,
    )


def _baca_skema(konfig: Konfigurasi) -> Schema:
    """Membaca skema dari berkas JSON atau dari introspeksi server (pilihan sidebar)."""
    if konfig.mode_skema == _MODE_DSN:
        return introspect_mysql(konfig.dsn)
    return load_schema_json(Path(konfig.path_skema))


def _kompilasi(sumber: str, konfig: Konfigurasi) -> HasilKompilasi:
    """Memanggil fasad :func:`compile_sql`; tidak pernah melempar error ke Streamlit."""
    try:
        skema = _baca_skema(konfig)
    except Exception as exc:
        return HasilKompilasi(
            sumber=sumber,
            skema=None,
            hasil=None,
            galat=f"Skema tidak dapat dibaca: {type(exc).__name__}: {exc}",
        )
    hasil = compile_sql(sumber, skema)
    galat = render_errors(hasil.errors, sumber) if hasil.errors else None
    return HasilKompilasi(sumber=sumber, skema=skema, hasil=hasil, galat=galat)


def _verifikasi(state: HasilKompilasi, konfig: Konfigurasi) -> HasilVerifikasi:
    """Menjalankan kode terkompilasi pada data lalu membandingkannya dengan MySQL."""
    hasil, skema = state.hasil, state.skema
    if hasil is None or skema is None or hasil.ast is None:
        return HasilVerifikasi(
            galat="Tidak ada query terkompilasi untuk diverifikasi.",
            frame=None,
            oracle=None,
            masalah=[],
        )
    try:
        if konfig.mode_data == _MODE_MYSQL:
            tabel = load_tables_mysql(konfig.dsn, required_tables(hasil.ast, skema), skema)
        else:
            tabel = load_tables_csv(Path(konfig.path_data), skema)
        frame = run_compiled(hasil.code, tabel)
    except Exception as exc:
        return HasilVerifikasi(
            galat=f"Eksekusi kode gagal: {type(exc).__name__}: {exc}",
            frame=None,
            oracle=None,
            masalah=[],
        )
    try:
        kolom, baris = fetch(state.sumber, konfig.dsn)
    except Exception as exc:
        return HasilVerifikasi(
            galat=f"Tidak dapat mengambil hasil MySQL: {type(exc).__name__}: {exc}",
            frame=frame,
            oracle=None,
            masalah=[],
        )
    masalah = compare_query(kolom, baris, frame, bool(hasil.ast.order_by))
    return HasilVerifikasi(
        galat=None,
        frame=frame,
        oracle=pd.DataFrame(data=list(baris), columns=list(kolom)),
        masalah=masalah,
    )


def _tampilkan_kode(hasil: CompileResult) -> None:
    """Panel utama sesuai ``SPEC.md`` §1: teks kode Python + salin + unduh .py."""
    st.subheader("Kode Python hasil kompilasi")
    st.code(hasil.code, language="python")
    st.download_button(
        "Unduh .py",
        data=hasil.code,
        file_name="hasil_kompilasi.py",
        mime="text/x-python",
        key="unduh",
        help="Menyimpan kode hasil kompilasi sebagai berkas Python.",
    )
    st.caption(
        "Kotak kode di atas memiliki tombol salin bawaan Streamlit; tombol Unduh .py "
        "menyimpan kode sebagai berkas .py."
    )


def _tampilkan_galat(galat: str) -> None:
    """Galat kompilasi dengan baris, kolom, dan penunjuk ``^^^^`` (``SPEC.md`` §9)."""
    st.error("Kompilasi gagal. Penunjuk di bawah menunjuk baris dan kolom yang bermasalah:")
    st.code(galat, language=None)


def _tab_token(hasil: CompileResult) -> None:
    """Daftar token hasil lexer."""
    st.code(format_tokens(hasil.tokens), language=None)


def _tab_ast(hasil: CompileResult) -> None:
    """Cetak AST memakai perender yang sudah ada (``parser.pretty_print``)."""
    if hasil.ast is None:
        st.info("AST tidak tersedia karena parse gagal.")
        return
    st.code(pretty_print(hasil.ast), language=None)


def _tab_semantik(hasil: CompileResult) -> None:
    """Ringkasan analisis semantik dari ``CompileResult.analysis``."""
    analysis = hasil.analysis
    ast = hasil.ast
    if analysis is None or ast is None:
        st.info("Analisis semantik tidak tersedia untuk query ini.")
        return
    for pesan in analysis.warnings:
        st.warning(pesan)

    rows: list[dict[str, str]] = []
    for item in ast.items:
        if isinstance(item, Star):
            rows.append({"Ekspresi": "*", "Tipe": "-", "Referensi": "-", "Alias": ""})
            continue
        expr = item.expr
        tipe = analysis.types.get(expr)
        referensi = analysis.column_refs.get(expr, "") if isinstance(expr, Column) else ""
        if not referensi and isinstance(expr, Column):
            target = analysis.alias_refs.get(expr)
            referensi = target.name if isinstance(target, Column) else ""
        rows.append(
            {
                "Ekspresi": item.text,
                "Tipe": tipe.value if tipe is not None else "-",
                "Referensi": referensi or "-",
                "Alias": item.alias or "",
            }
        )
    st.table(rows)
    st.caption(
        f"{len(analysis.types)} ekspresi bertipe; {len(analysis.column_refs)} referensi "
        f"kolom; {len(analysis.alias_refs)} alias; {len(analysis.warnings)} peringatan."
    )


def _tab_plan(hasil: CompileResult) -> None:
    """Cetak logical plan (``SPEC.md`` §11)."""
    if hasil.plan is None:
        st.info("Plan tidak tersedia karena kompilasi berhenti sebelum penurunan IR.")
        return
    st.code(format_plan(hasil.plan), language=None)


def _tab_verifikasi(state: HasilKompilasi | None, konfig: Konfigurasi) -> None:
    """Tombol **Jalankan** dan hasil Python berdampingan dengan hasil MySQL."""
    if state is None or state.hasil is None:
        st.info("Kompilasi sebuah query dahulu; baru tab ini dapat dipakai.")
        return
    if st.button("Jalankan", key="jalankan", type="primary"):
        st.session_state["verifikasi"] = _verifikasi(state, konfig)

    verif: HasilVerifikasi | None = st.session_state.get("verifikasi")
    if verif is None:
        st.info(
            "Tekan **Jalankan** untuk mengeksekusi kode pada data lalu "
            "membandingkannya dengan MySQL 8.4."
        )
        return
    if verif.galat is not None:
        st.error(verif.galat)
        return
    if verif.frame is None or verif.oracle is None:
        st.info("Hasil perbandingan tidak tersedia.")
        return

    kolom = st.columns(2)
    with kolom[0]:
        st.caption("Hasil kode Python (pandas)")
        st.dataframe(verif.frame)
    with kolom[1]:
        st.caption("Hasil MySQL 8.4 (oracle)")
        st.dataframe(verif.oracle)

    if verif.masalah:
        st.error(f"Tidak cocok dengan MySQL 8.4: {len(verif.masalah)} masalah.")
        st.code("\n".join(verif.masalah), language=None)
    else:
        st.success(
            f"Cocok dengan MySQL 8.4: {len(verif.frame)} baris dan "
            f"{verif.frame.shape[1]} kolom identik."
        )


st.set_page_config(page_title="sqlc — Kompiler MySQL ke Python", layout="wide")

st.title("sqlc — kompiler query MySQL 8.4 ke Python (pandas)")
st.caption(
    "Ketik satu pernyataan SELECT di bawah, tekan Kompilasi, lalu ambil kode Pythonnya. "
    "Produk utama memang teks kode; tab di bawah hanya sarana verifikasi (SPEC.md §1)."
)

konfig = _baca_sidebar()

sumber = st.text_area(
    "Query MySQL",
    key="sumber",
    height=170,
    placeholder="SELECT nama, ipk FROM mahasiswa WHERE ipk > 3 ORDER BY ipk DESC",
    help="Subset Level 1–2 pada SPEC.md §2; hal di luar itu ditolak dengan error jelas.",
)

if st.button("Kompilasi", key="kompilasi", type="primary"):
    if "verifikasi" in st.session_state:
        del st.session_state["verifikasi"]
    st.session_state["hasil"] = _kompilasi(sumber or "", konfig)

state: HasilKompilasi | None = st.session_state.get("hasil")

if state is None:
    st.info("Tulis sebuah query MySQL lalu tekan **Kompilasi**.")
elif state.galat is not None:
    _tampilkan_galat(state.galat)
elif state.hasil is not None:
    _tampilkan_kode(state.hasil)
    tab_token, tab_ast, tab_semantik, tab_plan, tab_verifikasi = st.tabs(
        ["Token", "AST", "Semantik", "Plan", "Verifikasi"]
    )
    with tab_token:
        _tab_token(state.hasil)
    with tab_ast:
        _tab_ast(state.hasil)
    with tab_semantik:
        _tab_semantik(state.hasil)
    with tab_plan:
        _tab_plan(state.hasil)
    with tab_verifikasi:
        _tab_verifikasi(state, konfig)
