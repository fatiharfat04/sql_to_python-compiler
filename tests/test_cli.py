"""Tes CLI ``sqlc`` (``SPEC.md`` §15, ``PROMPT.md`` Langkah 8)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mysql_oracle import DEFAULT_DSN
from typer.testing import CliRunner

from sqlcompiler.cli import EXIT_COMPILE, EXIT_OK, EXIT_RUNTIME, app
from sqlcompiler.runtime import compile_sql
from sqlcompiler.schema_loader import load_schema_json

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
SCHEMA = EXAMPLES / "schema.json"

QUERY = "SELECT nama, ipk FROM mahasiswa WHERE ipk > 3 ORDER BY ipk DESC LIMIT 3"
RUN_QUERY = "SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC"

runner = CliRunner()


def _norm(text: str) -> str:
    """Menyeragamkan akhir baris (Windows menulis CRLF ketika dialihkan)."""
    return text.replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# compile                                                                      #
# --------------------------------------------------------------------------- #


def test_compile_sukses_mencetak_kode_murni() -> None:
    """Keluaran ``compile`` identik dengan kode fasad: dapat langsung ``> hasil.py``."""
    expected = compile_sql(QUERY, load_schema_json(SCHEMA)).code
    result = runner.invoke(app, ["compile", QUERY, "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_OK
    assert _norm(result.stdout) == expected
    assert expected.startswith("import pandas as pd\n")
    assert "def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:" in result.stdout
    assert result.stderr == ""


def test_compile_file(tmp_path: Path) -> None:
    """``--file`` membaca SQL dari berkas dan menghasilkan kode yang sama."""
    sql = "SELECT jurusan FROM mahasiswa GROUP BY jurusan ORDER BY jurusan\n"
    path = tmp_path / "kompilasi.sql"
    path.write_text(sql, encoding="utf-8")
    expected = compile_sql(sql, load_schema_json(SCHEMA)).code

    result = runner.invoke(app, ["compile", "--file", str(path), "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_OK
    assert _norm(result.stdout) == expected


def test_compile_error_semantik_keluar_1_dengan_penunjuk() -> None:
    """Error semantik memakai exit code 1 dan penunjuk ``^^^^`` (``SPEC.md`` §9)."""
    sql = "SELECT nama, ipkk FROM mahasiswa"
    result = runner.invoke(app, ["compile", sql, "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_COMPILE
    assert result.stdout == ""
    lines = result.stderr.splitlines()
    assert lines[0].startswith("SEM002:")
    caret = next(index for index, line in enumerate(lines) if line.lstrip().startswith("^"))
    sumber = lines[caret - 1]
    assert sumber == "  1 | SELECT nama, ipkk FROM mahasiswa"
    assert sumber.index("ipkk") == lines[caret].index("^")
    assert lines[caret].count("^") == len("ipkk")


def test_compile_error_leksikal_keluar_1() -> None:
    """String yang tidak ditutup menjadi ``LEX002`` dengan exit code 1."""
    sql = "SELECT 'tutup FROM mahasiswa"
    result = runner.invoke(app, ["compile", sql, "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_COMPILE
    assert result.stdout == ""
    assert result.stderr.splitlines()[0].startswith("LEX002:")
    assert "^^^^" in result.stderr


def test_compile_error_sintaks_keluar_1() -> None:
    """Tabel tidak dikenal menjadi ``SEM001`` dengan exit code 1."""
    result = runner.invoke(app, ["compile", "SELECT nim FROM tidak_ada", "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_COMPILE
    assert result.stderr.splitlines()[0].startswith("SEM001:")


@pytest.mark.mysql
def test_compile_dsn_memakai_skema_introspeksi() -> None:
    """``--dsn`` mengambil skema dari server; kodenya sama dengan ``--schema``."""
    result = runner.invoke(app, ["compile", QUERY, "--dsn", DEFAULT_DSN])

    assert result.exit_code == EXIT_OK
    assert _norm(result.stdout) == compile_sql(QUERY, load_schema_json(SCHEMA)).code


# --------------------------------------------------------------------------- #
# Sumber SQL dan skema                                                        #
# --------------------------------------------------------------------------- #


def test_sql_dan_file_bersama_keluar_2() -> None:
    args = ["compile", "SELECT 1 FROM mahasiswa", "--file", "x.sql", "--schema", str(SCHEMA)]
    result = runner.invoke(app, args)

    assert result.exit_code == EXIT_RUNTIME
    assert "bukan keduanya" in result.stderr


def test_tanpa_sql_keluar_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SQLC_MYSQL_DSN", raising=False)
    result = runner.invoke(app, ["compile", "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_RUNTIME
    assert "SQL tidak diberikan" in result.stderr


def test_tanpa_sumber_skema_keluar_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SQLC_MYSQL_DSN", raising=False)
    result = runner.invoke(app, ["compile", QUERY])

    assert result.exit_code == EXIT_RUNTIME
    assert "--schema" in result.stderr


def test_berkas_skema_tidak_ada_keluar_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["compile", QUERY, "--schema", str(tmp_path / "hilang.json")])

    assert result.exit_code == EXIT_RUNTIME
    assert "hilang.json" in result.stderr


# --------------------------------------------------------------------------- #
# run                                                                          #
# --------------------------------------------------------------------------- #


def test_run_dengan_data_csv() -> None:
    result = runner.invoke(
        app,
        ["run", RUN_QUERY, "--schema", str(SCHEMA), "--data", str(EXAMPLES)],
    )

    assert result.exit_code == EXIT_OK
    assert "Ahmad" in result.stdout
    assert "3 baris, 2 kolom" in result.stdout


def test_run_null_tampil_sebagai_null() -> None:
    sql = "SELECT nim, ipk FROM mahasiswa WHERE jurusan IS NULL ORDER BY nim"
    result = runner.invoke(app, ["run", sql, "--schema", str(SCHEMA), "--data", str(EXAMPLES)])

    assert result.exit_code == EXIT_OK
    assert "NULL" in result.stdout


def test_run_error_kompilasi_keluar_1() -> None:
    result = runner.invoke(
        app,
        ["run", "SELECT ipkk FROM mahasiswa", "--schema", str(SCHEMA), "--data", str(EXAMPLES)],
    )

    assert result.exit_code == EXIT_COMPILE
    assert result.stdout == ""
    assert result.stderr.splitlines()[0].startswith("SEM002:")


def test_run_tanpa_sumber_data_keluar_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SQLC_MYSQL_DSN", raising=False)
    result = runner.invoke(app, ["run", QUERY, "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_RUNTIME
    assert "sumber data" in result.stderr


def test_run_direktori_csv_tidak_ada_keluar_2(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["run", QUERY, "--schema", str(SCHEMA), "--data", str(tmp_path / "belum-ada")],
    )

    assert result.exit_code == EXIT_RUNTIME
    assert "gagal membaca CSV" in result.stderr


def test_run_tabel_kosong_keluar_2(tmp_path: Path) -> None:
    """Direktori tanpa CSV membuat ``tables[...]`` gagal: exit code 2, bukan crash."""
    result = runner.invoke(
        app,
        ["run", "SELECT nama FROM mahasiswa", "--schema", str(SCHEMA), "--data", str(tmp_path)],
    )

    assert result.exit_code == EXIT_RUNTIME
    assert "galat runtime" in result.stderr


# --------------------------------------------------------------------------- #
# explain                                                                      #
# --------------------------------------------------------------------------- #


def test_explain_menampilkan_empat_bagian() -> None:
    result = runner.invoke(app, ["explain", QUERY, "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_OK
    for judul in ("== TOKEN ==", "== AST ==", "== PLAN ==", "== KODE =="):
        assert judul in result.stdout
    assert "KEYWORD" in result.stdout
    assert "Scan mahasiswa" in result.stdout
    assert "def run(" in result.stdout
    assert result.stderr == ""


def test_explain_error_keluar_1_lalu_tetap_menampilkan_token() -> None:
    sql = "SELECT ipkk FROM mahasiswa"
    result = runner.invoke(app, ["explain", sql, "--schema", str(SCHEMA)])

    assert result.exit_code == EXIT_COMPILE
    assert "== TOKEN ==" in result.stdout
    assert result.stderr.splitlines()[0].startswith("SEM002:")


# --------------------------------------------------------------------------- #
# schema                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.mysql
def test_schema_dsn_menulis_json(tmp_path: Path) -> None:
    out = tmp_path / "keluaran.json"
    result = runner.invoke(app, ["schema", "--dsn", DEFAULT_DSN, "--out", str(out)])

    assert result.exit_code == EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(
        SCHEMA.read_text(encoding="utf-8")
    )


def test_schema_dsn_gagal_keluar_2(tmp_path: Path) -> None:
    args = [
        "schema",
        "--dsn",
        "mysql://root:salah@127.0.0.1:1/sqlc_test",
        "--out",
        str(tmp_path / "x.json"),
    ]
    result = runner.invoke(app, args)

    assert result.exit_code == EXIT_RUNTIME
    assert "gagal mengintrospeksi" in result.stderr


# --------------------------------------------------------------------------- #
# Tes MySQL wajib (tidak boleh di-skip)                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.mysql
def test_run_dsn_mysql_sama_dengan_csv() -> None:
    """``run --dsn`` memakai data dan skema MySQL; hasilnya sama dengan jalur CSV."""
    dari_mysql = runner.invoke(app, ["run", RUN_QUERY, "--dsn", DEFAULT_DSN])
    dari_csv = runner.invoke(
        app, ["run", RUN_QUERY, "--schema", str(SCHEMA), "--data", str(EXAMPLES)]
    )

    assert dari_mysql.exit_code == EXIT_OK
    assert dari_csv.exit_code == EXIT_OK
    assert dari_mysql.stdout == dari_csv.stdout
