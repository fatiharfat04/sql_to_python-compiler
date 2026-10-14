"""Tes code generator: golden file, konvensi §13, dan semantik wajib §12 (Langkah 6)."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from sqlcompiler.codegen import generate_code
from sqlcompiler.ir import Plan, lower
from sqlcompiler.lexer import tokenize
from sqlcompiler.parser import parse
from sqlcompiler.schema_loader import load_schema_json
from sqlcompiler.semantic import AnalysisResult, analyze

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"
SCHEMA = load_schema_json(ROOT / "examples" / "schema.json")

#: Enam contoh pada ``SPEC.md`` §14.
SPEC_NAMES = [
    "spec01_where_order_limit",
    "spec02_distinct_teks",
    "spec03_join_group_having",
    "spec04_like_in_between",
    "spec05_left_join_isnull",
    "spec06_div_mod",
]

#: Enam kasus wajib lain (``LIMIT a, b``, ``NOT IN``, ``NOT LIKE``, agregat tanpa
#: ``GROUP BY``, ``LEFT JOIN`` + ``WHERE`` sisi kiri, ``SELECT *`` dari join).
EXTRA_NAMES = [
    "extra01_limit_ab",
    "extra02_not_in",
    "extra03_not_like",
    "extra04_agregat_tanpa_group",
    "extra05_left_join_where_kiri",
    "extra06_select_star_join",
]

GOLDEN_NAMES = SPEC_NAMES + EXTRA_NAMES


def analyze_sql(sql: str) -> AnalysisResult:
    """Analisis ``sql`` terhadap ``examples/schema.json``; gagal bila ada error."""
    analysis = analyze(parse(tokenize(sql), sql), SCHEMA)
    assert analysis.ok, f"query tidak valid: {analysis.errors}"
    return analysis


def compile_sql(sql: str) -> str:
    """Kompilasi ``sql`` menjadi teks kode Python (Langkah 6, tanpa ``runtime.py``)."""
    return generate_code(make_plan(sql), analyze_sql(sql))


def make_plan(sql: str) -> Plan:
    """Turunkan ``sql`` menjadi :class:`Plan`."""
    return lower(analyze_sql(sql))


def golden_sql(name: str) -> str:
    return (GOLDEN / f"{name}.sql").read_text(encoding="utf-8").rstrip("\n")


def golden_py(name: str) -> str:
    return (GOLDEN / f"{name}.py").read_text(encoding="utf-8")


def run_code(code: str, tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Eksekusi kode hasil kompilasi (hanya untuk uji kewarasan, lihat PROMPT Langkah 6)."""
    namespace: dict[str, object] = {}
    exec(compile(code, "<golden>", "exec"), namespace)  # noqa: S102
    run = namespace.get("run")
    assert callable(run)
    result = run(tables)
    assert isinstance(result, pd.DataFrame)
    return result


# --------------------------------------------------------------------------- #
# Golden file                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", SPEC_NAMES)
def test_golden_contoh_spesifikasi(name: str) -> None:
    """Enam contoh ``SPEC.md`` §14 menghasilkan kode persis seperti berkas golden."""
    sql = golden_sql(name)
    assert compile_sql(sql) == golden_py(name)


@pytest.mark.parametrize("name", EXTRA_NAMES)
def test_golden_kasus_tambahan(name: str) -> None:
    """Enam kasus wajib lain menghasilkan kode persis seperti berkas golden."""
    sql = golden_sql(name)
    assert compile_sql(sql) == golden_py(name)


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_golden_lolos_compile_python(name: str) -> None:
    """Setiap kode golden valid menurut compiler Python."""
    compile(golden_py(name), f"<{name}>", "exec")


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_golden_deterministik(name: str) -> None:
    """Kompilasi dua kali menghasilkan teks yang identik."""
    sql = golden_sql(name)
    assert compile_sql(sql) == compile_sql(sql)


@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_golden_satu_fungsi_run_bertipe(name: str) -> None:
    """``SPEC.md`` §13: tepat satu fungsi ``run(tables: dict[str, pd.DataFrame])``."""
    code = golden_py(name)
    signatures = re.findall(r"^def (\w+)\(", code, flags=re.MULTILINE)
    assert "run" in signatures
    assert "def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:" in code
    assert code.rstrip().endswith("return out.reset_index(drop=True)")


# --------------------------------------------------------------------------- #
# Konvensi SPEC.md §13                                                         #
# --------------------------------------------------------------------------- #


def test_komentar_sql_per_blok() -> None:
    """Setiap blok diawali komentar ``# SQL: ...`` yang merepotkan sumber."""
    code = compile_sql(golden_sql("spec01_where_order_limit"))
    for expected in (
        "# SQL: FROM mahasiswa",
        "# SQL: WHERE ipk > 3.5",
        "# SQL: ORDER BY ipk DESC",
        "# SQL: SELECT nama, ipk",
        "# SQL: LIMIT 5",
    ):
        assert expected in code


def test_komentar_join_dan_group_by() -> None:
    code = compile_sql(golden_sql("spec03_join_group_having"))
    assert "# SQL: INNER JOIN nilai n ON m.nim = n.nim" in code
    assert "# SQL: GROUP BY m.jurusan" in code
    assert "# SQL: HAVING COUNT(*) > 2" in code
    assert "# SQL: ORDER BY rata DESC" in code


def test_komentar_potongan_panjang_dibungkus() -> None:
    """Komentar yang melebihi 100 kolom dipecah agar lolos ``ruff``."""
    code = compile_sql(golden_sql("extra04_agregat_tanpa_group"))
    for line in code.splitlines():
        assert len(line) <= 100, line


def test_lebar_baris_maksimum_100() -> None:
    """Semua kode golden berisi baris ≤ 100 karakter (ruff ``E501``)."""
    for name in GOLDEN_NAMES:
        for line in golden_py(name).splitlines():
            assert len(line) <= 100, f"{name}: {line}"


def test_fungsi_bantu_hanya_dihasilkan_bila_dipakai() -> None:
    """Fungsi bantu dihasilkan hanya bila dipakai (``SPEC.md`` §13)."""
    with_helper = compile_sql(golden_sql("spec06_div_mod"))
    assert "def _idiv(" in with_helper
    assert "def _imod(" in with_helper
    assert "def _tmin(" not in with_helper
    assert "def _tmax(" not in with_helper

    without = compile_sql(golden_sql("spec01_where_order_limit"))
    assert "def _idiv(" not in without
    assert "def _imod(" not in without


def test_fungsi_bantu_teks_min_max() -> None:
    sql = "SELECT MIN(kota) AS kota_min, MAX(kota) AS kota_max FROM mahasiswa"
    code = compile_sql(sql)
    assert "def _tmin(" in code
    assert "def _tmax(" in code
    assert "_tmin(df[\"mahasiswa.kota\"])" in code


def test_fungsi_bantu_apabila_dipakai_didefinisikan_di_atas_run() -> None:
    code = compile_sql(golden_sql("spec06_div_mod"))
    assert code.index("def _idiv(") < code.index("def run(")
    assert code.index("def _imod(") < code.index("def run(")


# --------------------------------------------------------------------------- #
# Semantik wajib SPEC.md §12                                                   #
# --------------------------------------------------------------------------- #


def test_fillna_pada_setiap_filter() -> None:
    """Predikat selalu dibungkus ``.fillna(False)`` (logika tiga nilai)."""
    sql = "SELECT nama FROM mahasiswa WHERE ipk > 3.5"
    code = compile_sql(sql)
    assert 'df = df[(df["mahasiswa.ipk"] > 3.5).fillna(False)]' in code


def test_predikat_statis_true_tanpa_filter() -> None:
    """Predikat yang selalu benar tidak memunculkan baris filter."""
    code = compile_sql("SELECT nama FROM mahasiswa WHERE 1 = 1")
    assert "fillna(False)" not in code
    assert "iloc[0:0]" not in code


def test_predikat_statis_false_menghasilkan_kosong() -> None:
    """Predikat yang selalu salah menghasilkan ``iloc[0:0]``."""
    code = compile_sql("SELECT nama FROM mahasiswa WHERE 1 = 0")
    assert "df = df.iloc[0:0]" in code


def test_like_menjadi_regex_asis() -> None:
    """``SPEC.md`` §10.6: ``LIKE`` menjadi regex ``(?is)\\A...\\Z``."""
    code = compile_sql(golden_sql("extra03_not_like"))
    assert r'str.match(r"(?is)\AA.*\Z")' in code


def test_like_escape_karakter_khusus() -> None:
    """Karakter regex di-escape; ``\\%`` dan ``\\_`` dipertahankan sebagai joker."""
    code = compile_sql(r"SELECT nama FROM mahasiswa WHERE nama LIKE 'a.b%c'")
    assert r"(?is)\Aa\.b.*c\Z" in code

    code = compile_sql(r"SELECT nama FROM mahasiswa WHERE nama LIKE 'a\%b'")
    assert r"(?is)\Aa%b\Z" in code

    code = compile_sql(r"SELECT nama FROM mahasiswa WHERE nama LIKE 'a\_b'")
    assert r"(?is)\Aa_b\Z" in code


def test_in_menjadi_rantai_or() -> None:
    """``SPEC.md`` §12: ``IN`` diterjemahkan menjadi rantai ``OR``."""
    code = compile_sql(golden_sql("extra02_not_in"))
    assert " isin(" not in code
    assert code.count('| (df["mahasiswa.jurusan"].str.lower() == "si")') == 1
    assert 'df["mahasiswa.jurusan"].str.lower() == "ti"' in code


def test_in_pendek_menjadi_satu_baris() -> None:
    """Rantai ``OR`` yang pendek tetap ditulis sebagai satu baris."""
    sql = "SELECT nama FROM mahasiswa WHERE ipk IN (3.0, 4.0)"
    code = compile_sql(sql)
    assert 'df = df[((df["mahasiswa.ipk"] == 3.0) | (df["mahasiswa.ipk"] == 4.0))' in code


def test_in_menggunakan_lower_pada_operand_teks() -> None:
    code = compile_sql("SELECT nama FROM mahasiswa WHERE jurusan IN ('TI', 'SI')")
    assert 'df["mahasiswa.jurusan"].str.lower() == "ti"' in code
    assert '"ti"' in code
    assert '"TI"' not in code


def test_between_menghasilkan_dua_perbandingan() -> None:
    code = compile_sql(golden_sql("spec04_like_in_between"))
    assert 'df["mahasiswa.ipk"] >= 3.0' in code
    assert 'df["mahasiswa.ipk"] <= 4.0' in code


def test_is_not_null_menghasilkan_notna() -> None:
    code = compile_sql(golden_sql("spec04_like_in_between"))
    assert 'df["mahasiswa.ipk"].notna()' in code


def test_pembulatan_4_desimal_untuk_int_div_int() -> None:
    """``SPEC.md`` §10.4: ``INT / INT`` dibulatkan 4 desimal setengah ke luar."""
    code = compile_sql("SELECT sks, sks / 3 AS hasil FROM mata_kuliah")
    assert '_rdec((df["mata_kuliah.sks"] / (3)), 4)' in code
    assert "def _rdec(s, scale=4):" in code


def test_pembagi_nol_statis_menghasilkan_null() -> None:
    """Pembagi literal 0 menyetel seluruh ekspresi ke NULL (lipat konstan)."""
    code = compile_sql("SELECT sks, sks / 0 AS hasil FROM mata_kuliah")
    assert "pd.NA" in code


def test_div_dan_mod_memakai_fungsi_bantu() -> None:
    code = compile_sql(golden_sql("spec06_div_mod"))
    assert "_idiv(df[\"mata_kuliah.sks\"], 3)" in code
    assert "_imod(df[\"mata_kuliah.sks\"], 3)" in code


def test_kunci_teks_lower_sebelum_merge() -> None:
    """Perbandingan kunci teks tidak membedakan huruf besar/kecil."""
    sql = (
        "SELECT a.nama FROM mahasiswa a INNER JOIN mahasiswa b ON a.kota = b.kota "
        "WHERE a.nim <> b.nim"
    )
    code = compile_sql(sql)
    assert "__jl0" in code
    assert "__jr0" in code
    assert ".assign(__jl0=a[\"a.kota\"].str.lower())" in code
    assert ".assign(__jr0=b[\"b.kota\"].str.lower())" in code


def test_kunci_null_dibuang_sebelum_merge_inner() -> None:
    code = compile_sql(golden_sql("spec03_join_group_having"))
    assert 'm.dropna(subset=["m.nim"])' in code
    assert 'n.dropna(subset=["n.nim"])' in code


def test_left_join_tidak_membuang_kunci_kiri() -> None:
    code = compile_sql(golden_sql("spec05_left_join_isnull"))
    assert 'how="left"' in code
    assert 'm.merge(n.dropna(subset=["n.nim"])' in code


def test_group_by_memakai_kunci_bayangan() -> None:
    """``SPEC.md`` §12: ``GROUP BY`` memakai kunci bayangan ``__k0``."""
    code = compile_sql(golden_sql("spec03_join_group_having"))
    assert "__k0=" in code
    assert 'groupby(["__k0"], dropna=False, sort=False)' in code


def test_agregat_tanpa_group_by_selalu_satu_baris() -> None:
    """``SPEC.md`` §10.9: agregat tanpa ``GROUP BY`` selalu menghasilkan satu baris."""
    code = compile_sql(golden_sql("extra04_agregat_tanpa_group"))
    assert "pd.DataFrame({" in code
    assert "len(df)" in code
    assert "dropna=False" not in code


def test_agregat_menggunakan_min_count_untuk_sum() -> None:
    """``SUM`` kosong/semua-NULL menghasilkan NULL, bukan 0."""
    code = compile_sql(golden_sql("extra04_agregat_tanpa_group"))
    assert ".sum(min_count=1)" in code


def test_avg_int_dibulatkan_4_desimal() -> None:
    sql = "SELECT AVG(sks) AS rata FROM mata_kuliah GROUP BY kode"
    code = compile_sql(sql)
    assert '.round(4)' in code


def test_avg_double_tidak_dibulatkan() -> None:
    code = compile_sql(golden_sql("extra04_agregat_tanpa_group"))
    assert "__agg2\"].round(4)" not in code


def test_urutan_null_asc_awal_desc_akhir() -> None:
    asc = compile_sql("SELECT nama FROM mahasiswa ORDER BY ipk")
    desc = compile_sql("SELECT nama FROM mahasiswa ORDER BY ipk DESC")
    assert 'na_position="first"' in asc
    assert 'ascending=[True]' in asc
    assert 'na_position="last"' in desc
    assert 'ascending=[False]' in desc


def test_sort_stable_multi_kunci() -> None:
    """``SPEC.md`` §10.13: kunci terakhir diurutkan lebih dulu (stable)."""
    code = compile_sql(golden_sql("extra06_select_star_join"))
    sorted_lines = [
        line.strip()
        for line in code.splitlines()
        if "sort_values" in line or line.strip().startswith("by=[")
    ]
    assert any(line.startswith('by=["n.kode"]') for line in sorted_lines)
    assert 'by=["m.nim"]' in code
    assert code.index('by=["n.kode"]') < code.index('by=["m.nim"]')


def test_project_memakai_concat_bila_nama_tidak_unik() -> None:
    """``SPEC.md`` §12: ``pd.concat`` bila nama keluaran tidak unik."""
    code = compile_sql(golden_sql("extra06_select_star_join"))
    assert "pd.concat(" in code
    assert 'pd.DataFrame({"nim": df["m.nim"]})' in code
    assert 'pd.DataFrame({"nim": df["n.nim"]})' in code

    unique = compile_sql(golden_sql("spec01_where_order_limit"))
    assert "pd.concat(" not in unique


def test_distinct_multi_kolom_memakai_rename_tmp() -> None:
    """``DISTINCT`` multi-kolom memakai ``rename`` agar nama keluaran tetap utuh."""
    code = compile_sql("SELECT DISTINCT jurusan, kota FROM mahasiswa")
    assert 'rename("c0")' in code
    assert 'rename("c1")' in code
    assert "out[~__d.duplicated()]" in code


def test_limit_menggunakan_iloc() -> None:
    code = compile_sql(golden_sql("spec01_where_order_limit"))
    assert "out = out.iloc[0:5]" in code


def test_limit_ab_menggunakan_offset_dan_hitungan() -> None:
    """``LIMIT a, b`` = ``LIMIT b OFFSET a``."""
    code = compile_sql(golden_sql("extra01_limit_ab"))
    assert "out = out.iloc[2:5]" in code
    assert "# SQL: LIMIT 3 OFFSET 2" in code


def test_distinct_menggunakan_duplicated() -> None:
    code = compile_sql(golden_sql("spec02_distinct_teks"))
    assert 'out = out[~out["jurusan"].str.lower().duplicated()]' in code


def test_distinct_tanpa_kunci_teks() -> None:
    code = compile_sql("SELECT DISTINCT ipk FROM mahasiswa")
    assert 'out = out[~out["ipk"].duplicated()]' in code


def test_select_bintang_dari_join_menggunakan_concat() -> None:
    code = compile_sql("SELECT * FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim")
    assert "pd.concat(" in code
    assert 'pd.DataFrame({"nim": df["m.nim"]})' in code
    assert 'pd.DataFrame({"nim": df["n.nim"]})' in code


def test_select_bintang_tunggal_memakai_dict() -> None:
    code = compile_sql("SELECT * FROM mahasiswa")
    assert "pd.concat(" not in code
    assert '"nim": df["mahasiswa.nim"]' in code


def test_alias_substitusi_pada_order_by() -> None:
    """``SPEC.md`` §8: alias dapat dipakai pada ``ORDER BY``."""
    code = compile_sql("SELECT ipk * 2 AS ganda FROM mahasiswa ORDER BY ganda DESC")
    assert "__sort0=" in code
    assert 'by=["__sort0"]' in code


# --------------------------------------------------------------------------- #
# Uji kewarasan terhadap data                                                  #
# --------------------------------------------------------------------------- #


def _tables() -> dict[str, pd.DataFrame]:
    mahasiswa = pd.DataFrame(
        {
            "nim": pd.array(["21001", "21002", "21003", "21004"], dtype="string"),
            "nama": pd.array(["Andi", "Budi", "Citra", "Dewi"], dtype="string"),
            "jurusan": pd.array(["TI", "SI", "TI", None], dtype="string"),
            "ipk": pd.array([3.8, 3.4, None, 3.9], dtype="Float64"),
            "kota": pd.array(["Jakarta", "jakarta", "Bandung", None], dtype="string"),
        }
    )
    mata_kuliah = pd.DataFrame(
        {
            "kode": pd.array(["MK1", "MK2"], dtype="string"),
            "sks": pd.array([2, 3], dtype="Int64"),
        }
    )
    return {"mahasiswa": mahasiswa, "mata_kuliah": mata_kuliah}


def test_exec_contoh1_where_order_limit() -> None:
    out = run_code(compile_sql(golden_sql("spec01_where_order_limit")), _tables())
    assert list(out.columns) == ["nama", "ipk"]
    assert list(out["nama"]) == ["Dewi", "Andi"]


def test_exec_distinct_null_muncul_sekali_dan_paling_awal() -> None:
    """``SPEC.md`` §14 Contoh 2: NULL pada ``jurusan`` muncul sekali dan paling awal."""
    out = run_code(compile_sql(golden_sql("spec02_distinct_teks")), _tables())
    values = [None if pd.isna(value) else value for value in out["jurusan"]]
    assert values.count(None) == 1
    assert values[0] is None
    assert len(values) == 3


def test_exec_div_mod() -> None:
    out = run_code(compile_sql(golden_sql("spec06_div_mod")), _tables())
    assert list(out["sks"]) == [2, 3]
    assert [float(value) for value in out["bagi"]] == [0.6667, 1.0]
    assert list(out["bulat"]) == [0, 1]
    assert list(out["sisa"]) == [2, 0]


def test_exec_agregat_kosong_satu_baris() -> None:
    """``SPEC.md`` §10.9: input kosong → tepat satu baris; COUNT = 0; lainnya NULL."""
    tables = _tables()
    tables["mahasiswa"] = tables["mahasiswa"].iloc[0:0]
    out = run_code(compile_sql(golden_sql("extra04_agregat_tanpa_group")), tables)
    assert len(out) == 1
    assert int(out.loc[0, "jumlah"]) == 0
    for column in ("total", "rata", "terkecil", "terbesar"):
        assert pd.isna(out.loc[0, column])


def test_exec_agregat_count_pada_input_penuh() -> None:
    out = run_code(compile_sql(golden_sql("extra04_agregat_tanpa_group")), _tables())
    assert int(out.loc[0, "jumlah"]) == 4
    assert float(out.loc[0, "total"]) == pytest.approx(11.1)


def test_exec_limit_ab() -> None:
    out = run_code(compile_sql(golden_sql("extra01_limit_ab")), _tables())
    assert list(out["nim"]) == ["21003", "21004"]


def test_exec_not_in_dengan_null_pada_operand() -> None:
    """``SPEC.md`` §10.7: ``NOT IN`` dengan operand NULL tidak pernah TRUE."""
    out = run_code(compile_sql(golden_sql("extra02_not_in")), _tables())
    # 'TI' dan 'SI' gugur; jurusan NULL menghasilkan NULL sehingga baris tidak lolos.
    assert len(out) == 0


def test_exec_not_in_bila_ada_nilai_di_luar_daftar() -> None:
    out = run_code(
        compile_sql("SELECT nama FROM mahasiswa WHERE jurusan NOT IN ('TI')"),
        _tables(),
    )
    assert list(out["nama"]) == ["Budi"]


def test_exec_not_like() -> None:
    out = run_code(compile_sql(golden_sql("extra03_not_like")), _tables())
    assert list(out["nama"]) == ["Budi", "Citra", "Dewi"]


def test_exec_select_star_join_nama_ganda_dibandingkan_per_posisi() -> None:
    tables = _tables()
    tables["nilai"] = pd.DataFrame(
        {
            "nim": pd.array(["21001", "21001"], dtype="string"),
            "kode": pd.array(["MK1", "MK2"], dtype="string"),
            "skor": pd.array([80.5, 90.0], dtype="Float64"),
        }
    )
    out = run_code(compile_sql(golden_sql("extra06_select_star_join")), tables)
    assert list(out.columns) == ["nim", "nama", "jurusan", "ipk", "kota", "nim", "kode", "skor"]
    # Nama keluaran ganda dibandingkan berdasarkan posisi kolom.
    assert list(out.iloc[:, 0]) == ["21001", "21001"]
    assert list(out.iloc[:, 5]) == ["21001", "21001"]
    assert [float(value) for value in out.iloc[:, 7]] == [80.5, 90.0]


def test_exec_predikat_statis_false() -> None:
    out = run_code(compile_sql("SELECT nama FROM mahasiswa WHERE 1 = 0"), _tables())
    assert len(out) == 0
