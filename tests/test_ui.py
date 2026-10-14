"""Tes antarmuka Streamlit (``SPEC.md`` §1, §15; ``PROMPT.md`` Langkah 9).

Empat tes wajib dari ``PROMPT.md`` Langkah 9: aplikasi terbuka tanpa exception,
query valid menampilkan kode, query salah menampilkan pesan error, dan satu tes
berpenanda ``mysql`` yang memastikan penanda "cocok" muncul. Tes memakai
``streamlit.testing.v1.AppTest``, bukan browser sungguhan.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "src" / "sqlcompiler" / "app_streamlit.py"

QUERY = "SELECT nama, ipk FROM mahasiswa WHERE ipk > 3 ORDER BY ipk DESC LIMIT 3"
SALAH = "SELECT namaa FROM mahasiswa"
TABS = ["Token", "AST", "Semantik", "Plan", "Verifikasi"]


def _buka() -> AppTest:
    """Membuka aplikasi Streamlit sekali lalu menjalankannya."""
    return AppTest.from_file(str(APP)).run()


def _kompilasi(query: str) -> AppTest:
    """Membuka aplikasi, memasukkan ``query``, lalu menekan tombol **Kompilasi**."""
    at = _buka()
    at.get_by_key("sumber").set_value(query)
    at.get_by_key("kompilasi").click()
    return at.run()


# --------------------------------------------------------------------------- #
# Wajib (PROMPT.md Langkah 9)                                                  #
# --------------------------------------------------------------------------- #


def test_aplikasi_terbuka_tanpa_exception() -> None:
    """Aplikasi terbuka tanpa exception dan menampilkan kontrol sesuai §1."""
    at = _buka()

    assert not at.exception
    assert not at.error
    # Papan tulis: kotak teks query + tombol Kompilasi, sidebar sumber skema/data.
    assert "sumber" in [item.key for item in at.text_area]
    assert "kompilasi" in [item.key for item in at.button]
    assert [item.label for item in at.sidebar.radio] == ["Skema dari", "Data dari"]
    # Belum dikompilasi: hanya pengingat, belum ada panel kode.
    assert not at.code
    assert any("Kompilasi" in item.value for item in at.info)


def test_query_valid_menampilkan_kode() -> None:
    """Query valid menampilkan kode Python sebagai panel utama + tab pendukung."""
    at = _kompilasi(QUERY)

    assert not at.exception
    # Panel utama = teks kode Python (SPEC.md §1) — elemen ``st.code`` pertama.
    assert at.code[0].value.startswith("import pandas as pd")
    assert "def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:" in at.code[0].value
    # Tombol salin bawaan ``st.code`` + tombol unduh .py.
    assert "unduh" in [item.key for item in at.get("download_button")]
    # Tab pendukung + tab verifikasi.
    assert [item.label for item in at.tabs] == TABS
    # Tab Semantik menampilkan ringkasan tiap item SELECT.
    assert len(at.table) == 1
    assert "mahasiswa.nama" in str(at.table[0].value)


def test_query_salah_menampilkan_error() -> None:
    """Query salah menampilkan pesan error lengkap dengan baris dan penunjuk."""
    at = _kompilasi(SALAH)

    assert not at.exception
    assert at.error
    assert "Kompilasi gagal" in at.error[0].value
    # Satu-satunya kotak kode adalah blok galat: kode + baris + penunjuk ^^^^^.
    galat = at.code[0].value
    assert galat.startswith("SEM002: Kolom 'namaa' tidak ditemukan")
    assert "1 | SELECT namaa FROM mahasiswa" in galat
    assert "^^^^^" in galat
    assert not at.tabs


@pytest.mark.mysql
def test_verifikasi_cocok_dengan_mysql() -> None:
    """Tab Verifikasi menampilkan penanda cocok terhadap MySQL 8.4 sungguhan."""
    at = _kompilasi(QUERY)
    at.get_by_key("jalankan").click()
    at.run()

    assert not at.exception
    assert not at.error
    # Hasil Python berdampingan dengan hasil MySQL (SPEC.md §1).
    assert len(at.dataframe) == 2
    assert any("Cocok dengan MySQL 8.4" in item.value for item in at.success)
    # Tab tetap tersedia setelah verifikasi, dan tab Semantik tetap terisi.
    assert [item.label for item in at.tabs] == TABS
    assert len(at.table) == 1


# --------------------------------------------------------------------------- #
# Tambahan                                                                     #
# --------------------------------------------------------------------------- #


def test_query_kosong_menampilkan_error_bukan_exception() -> None:
    """Query kosong ditolak dengan error kompilasi, bukan exception Streamlit."""
    at = _kompilasi("")

    assert not at.exception
    assert at.error
    assert at.code[0].value.startswith("SYN001:")
    assert not at.tabs


def test_sidebar_bawaan_merujuk_contoh_proyek() -> None:
    """Default sidebar memakai ``examples/`` sehingga aplikasi langsung dapat dipakai."""
    at = _buka()

    path_skema = at.get_by_key("path_skema")
    path_data = at.get_by_key("path_data")
    skema = Path(str(path_skema.value))
    data = Path(str(path_data.value))
    assert skema.name == "schema.json"
    assert skema.parent.name == "examples"
    assert data.name == "examples"
    assert skema.is_file()
    assert data.is_dir()


def test_ganti_sumber_data_ke_mysql_tanpa_exception() -> None:
    """Berpindah sumber data ke MySQL tidak menimbulkan exception saat kompilasi."""
    at = _buka()
    at.get_by_key("mode_data").set_value("MySQL")
    at.get_by_key("sumber").set_value("SELECT nama FROM mahasiswa")
    at.get_by_key("kompilasi").click()
    at.run()

    assert not at.exception
    assert at.code[0].value.startswith("import pandas as pd")
    # Widget direktori CSV disembunyikan; DSN oracle tetap tersedia.
    assert "path_data" not in [item.key for item in at.text_input]
    assert "dsn" in [item.key for item in at.text_input]
