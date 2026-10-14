"""Audit pengerasan ``exec`` dan larangan pustaka parser (``PROMPT.md`` Langkah 10).

Poin 3 — meninjau keamanan eksekusi kode hasil kompilasi, menguji bahwa namespace
benar-benar terbatas, dan mengunci perbaikannya (salinan ``__builtins__`` per
eksekusi) dengan tes regresi.

Poin 4 — memastikan tidak ada pustaka parser SQL terlarang (Aturan Kerja Global
``PROMPT.md`` #5: ``sqlparse``, ``sqlglot``, ``lark``, ``ply``, ``pyparsing``,
``antlr4``) baik pada impor di dalam kode maupun pada dependensi proyek.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from sqlcompiler.runtime import SAFE_BUILTINS, run_compiled

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "sqlcompiler"

#: Pustaka parser SQL yang dilarang dipakai (Aturan Kerja Global #5).
PUSTAKA_PARSER_TERLARANG = frozenset(
    {"sqlparse", "sqlglot", "lark", "ply", "pyparsing", "antlr4", "antlr4-python3-runtime"}
)

#: Dependensi runtime yang diizinkan ``SPEC.md`` §17.
DEPENDENSI_IZIN = frozenset({"pandas", "pymysql", "typer", "rich", "streamlit"})


def _akar_impor() -> set[str]:
    """Seluruh nama modul tingkat atas yang diimpor oleh kode di ``src/sqlcompiler``."""
    akar: set[str] = set()
    for path in sorted(SRC.glob("*.py")):
        pohon = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(pohon):
            if isinstance(node, ast.Import):
                akar.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                akar.add(node.module.split(".", 1)[0])
    return akar


# --------------------------------------------------------------------------- #
# Poin 3: namespace eksekusi                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import sys",
        "import subprocess",
        "__import__('os')",
        "from os import path",
        "from pathlib import Path",
    ],
    ids=["import_os", "import_sys", "import_subprocess", "dunder_import", "from_os", "pathlib"],
)
def test_impor_di_luar_pandas_ditolak(code: str) -> None:
    """``_safe_import`` menolak seluruh akar di luar ``pandas``."""
    with pytest.raises(ImportError, match="tidak diizinkan"):
        run_compiled(code, {})


def test_impor_relatif_ditolak() -> None:
    """Tingkat impor relatif (``level != 0``) ikut ditolak."""
    with pytest.raises(ImportError, match="tidak diizinkan"):
        run_compiled("from . import os", {})


def test_run_compiled_tanpa_fungsi_run() -> None:
    """Kode yang tidak mendefinisikan ``run`` memberi ``RuntimeError`` yang jelas."""
    with pytest.raises(RuntimeError, match="fungsi run"):
        run_compiled("x = 1", {})


def test_run_compiled_mengembalikan_bukan_dataframe() -> None:
    """Keluaran ``run`` yang bukan ``DataFrame`` memberi ``TypeError``."""
    with pytest.raises(TypeError, match="pandas.DataFrame"):
        run_compiled("def run(tables):\n    return 1", {})


def test_mutasi_builtins_tidak_bocor_ke_eksekusi_berikutnya() -> None:
    """Salinan ``__builtins__`` per eksekusi: satu run tak boleh merusak run berikut.

    Regresi Langkah 10: semula ``namespace['__builtins__']`` menunjuk ke
    ``SAFE_BUILTINS`` yang sama, sehingga ``__builtins__['len'] = None`` pada satu
    eksekusi membuat ``len`` hilang untuk **seluruh** eksekusi sesudahnya.
    """
    merusak = (
        "def run(tables):\n"
        "    __builtins__['len'] = None\n"
        "    return __import__('pandas').DataFrame({'a': [1]})"
    )
    run_compiled(merusak, {})
    assert SAFE_BUILTINS["len"] is len, "tabel builtins ikut termodifikasi."

    biasa = (
        "import pandas as pd\n"
        "def run(tables):\n"
        "    return pd.DataFrame({'n': [len([1, 2])]})"
    )
    frame = run_compiled(biasa, {})
    assert frame.to_dict("list") == {"n": [2]}


def test_namespace_tidak_menyimpan_state_antar_eksekusi() -> None:
    """Hasil bergantung hanya pada ``tables``, bukan pada eksekusi sebelumnya."""
    kode = "import pandas as pd\ndef run(tables):\n    return pd.DataFrame({'n': [1]})"
    pertama = run_compiled(kode, {})
    kedua = run_compiled(kode, {})
    assert pertama.to_dict("list") == kedua.to_dict("list") == {"n": [1]}


def test_builtins_aman_hanya_daftar_putih() -> None:
    """``SAFE_BUILTINS`` tidak memuat akses berkas/jaringan langsung."""
    for terlarang in ("open", "eval", "exec", "compile", "input", "breakpoint"):
        assert terlarang not in SAFE_BUILTINS, f"{terlarang!r} tidak boleh ada di builtins aman."
    assert "pandas" not in SAFE_BUILTINS
    assert SAFE_BUILTINS["__import__"].__name__ == "_safe_import"


# --------------------------------------------------------------------------- #
# Poin 4: tidak ada pustaka parser terlarang                                   #
# --------------------------------------------------------------------------- #


def test_tidak_mengimpor_pustaka_parser_terlarang() -> None:
    """Kode sumber tidak mengimpor ``sqlparse``/``sqlglot``/``lark``/``ply``/dst."""
    akar = _akar_impor()
    assert not akar & PUSTAKA_PARSER_TERLARANG, (
        f"pustaka parser terlarang terimpor: {sorted(akar & PUSTAKA_PARSER_TERLARANG)}"
    )
    # Lexer/parser memang ditulis manual; nama berkasnya tetap ada.
    assert (SRC / "lexer.py").is_file()
    assert (SRC / "parser.py").is_file()


def test_tidak_memasang_pustaka_parser_terlarang() -> None:
    """Tidak ada distribusi terpasang yang namanya masuk daftar terlarang."""
    import importlib.metadata as metadata

    terpasang = {
        nama.lower()
        for dist in metadata.distributions()
        if isinstance((nama := dist.metadata.get("Name")), str)
    }
    selisih = terpasang & PUSTAKA_PARSER_TERLARANG
    assert not selisih, f"terpasang: {sorted(selisih)}"


def _nama_bersih(derivasi: list[str]) -> set[str]:
    """Nama paket dasar (huruf kecil) dari spesifikasi dependensi PEP 508."""
    hasil: set[str] = set()
    for item in derivasi:
        polos = item.split(";")[0].strip().split("[")[0]
        for penanda in ("===", "==", ">=", "<=", "~=", "!=", ">", "<", "="):
            polos = polos.split(penanda)[0]
        if polos.strip():
            hasil.add(polos.strip().lower())
    return hasil


def test_dependensi_terbatas_pada_daftar_spec() -> None:
    """``pyproject.toml`` hanya memuat dependensi yang diizinkan ``SPEC.md`` §17."""
    with (ROOT / "pyproject.toml").open("rb") as berkas:
        proyek = tomllib.load(berkas)["project"]

    runtime = _nama_bersih(proyek["dependencies"])
    assert runtime <= {nama.lower() for nama in DEPENDENSI_IZIN}, (
        f"dependensi runtime di luar SPEC.md §17: {sorted(runtime - DEPENDENSI_IZIN)}"
    )

    dev = _nama_bersih(proyek["optional-dependencies"]["dev"])
    izin_dev = {nama.lower() for nama in DEPENDENSI_IZIN} | {
        "pytest",
        "pytest-cov",
        "ruff",
        "mypy",
        "types-pymysql",
        "pandas-stubs",
    }
    assert dev <= izin_dev, f"dependensi dev di luar daftar: {sorted(dev - izin_dev)}"
