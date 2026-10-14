"""Tes analisis semantik ``SPEC.md`` §7-§8 dan pemuat skema (Langkah 4)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sqlcompiler.ast_nodes import Column, FunctionCall, Literal, SelectItem, Star
from sqlcompiler.errors import render_error
from sqlcompiler.lexer import tokenize
from sqlcompiler.parser import parse
from sqlcompiler.schema_loader import (
    dump_schema_json,
    introspect_mysql,
    load_schema_json,
    mysql_type_to_data_type,
    parse_dsn,
    schema_from_mapping,
)
from sqlcompiler.semantic import (
    AnalysisResult,
    ColumnDef,
    DataType,
    Schema,
    Table,
    analyze,
)

ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("SQLC_MYSQL_DSN", "mysql://root:root@localhost:3307/sqlc_test")

#: Skema acuan ``SPEC.md`` §7.
SCHEMA: Schema = load_schema_json(ROOT / "examples" / "schema.json")

#: Skema dengan satu kolom bertipe tak didukung (untuk SEM016).
HR: Schema = Schema(
    tables=(
        Table(
            name="karyawan",
            columns=(
                ColumnDef("nik", DataType.INT, "int"),
                ColumnDef("nama", DataType.TEXT, "varchar(100)"),
                ColumnDef("tgl_lahir", DataType.UNSUPPORTED, "date"),
            ),
        ),
    )
)

#: Enam contoh ``SPEC.md`` §14 (= ``examples/queries.sql``).
SPEC_EXAMPLES = [
    "SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC LIMIT 5",
    "SELECT DISTINCT jurusan FROM mahasiswa ORDER BY jurusan",
    "SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata "
    "FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim "
    "GROUP BY m.jurusan HAVING COUNT(*) > 2 ORDER BY rata DESC",
    "SELECT nim, nama FROM mahasiswa "
    "WHERE nama LIKE 'A%' AND jurusan IN ('TI', 'SI') "
    "AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL",
    "SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim "
    "WHERE n.nim IS NULL",
    "SELECT sks, sks / 3 AS bagi, sks DIV 3 AS bulat, sks % 3 AS sisa "
    "FROM mata_kuliah ORDER BY sks",
]


def check(sql: str, schema: Schema | None = None) -> AnalysisResult:
    """Analisis ``sql`` dan kembalikan hasilnya."""
    return analyze(parse(tokenize(sql), sql), SCHEMA if schema is None else schema)


def codes(sql: str, schema: Schema | None = None) -> list[str]:
    """Kode error ``SEM***`` untuk ``sql``."""
    return [error.code for error in check(sql, schema).errors]


def messages(sql: str, schema: Schema | None = None) -> list[str]:
    return [error.message for error in check(sql, schema).errors]


def select_expr(sql: str):
    item = check(sql).ast.items[0]
    assert isinstance(item, SelectItem)
    return item.expr


# --------------------------------------------------------------------------- #
# Query valid                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT nama, ipk FROM mahasiswa",
        "SELECT * FROM mahasiswa",
        "SELECT m.nama, n.skor FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim",
        "SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim "
        "WHERE n.nim IS NULL",
        "SELECT jurusan, COUNT(*) AS jumlah FROM mahasiswa GROUP BY jurusan "
        "HAVING COUNT(*) > 2 ORDER BY jumlah DESC LIMIT 3",
        "SELECT DISTINCT jurusan FROM mahasiswa ORDER BY jurusan",
        "SELECT nim, nama FROM mahasiswa WHERE nama LIKE 'A%' "
        "AND jurusan IN ('TI', 'SI') AND ipk BETWEEN 3.0 AND 4.0 "
        "AND ipk IS NOT NULL",
        "SELECT sks, sks / 3 AS bagi, sks DIV 3 AS bulat, sks % 3 AS sisa "
        "FROM mata_kuliah ORDER BY sks",
    ],
)
def test_query_valid(sql: str) -> None:
    assert check(sql).ok, check(sql).errors


@pytest.mark.parametrize("sql", SPEC_EXAMPLES)
def test_spec_contoh_14_valid(sql: str) -> None:
    assert check(sql).ok, check(sql).errors


def test_analysis_result_keeps_inputs() -> None:
    ast = parse(tokenize("SELECT nama FROM mahasiswa"), "SELECT nama FROM mahasiswa")
    result = analyze(ast, SCHEMA)
    assert isinstance(result, AnalysisResult)
    assert result.ast is ast
    assert result.schema is SCHEMA
    assert result.ok
    assert result.errors == []
    assert result.warnings == []


def test_aggregate_without_group_by_is_valid() -> None:
    assert check("SELECT COUNT(*) FROM mahasiswa").ok


# --------------------------------------------------------------------------- #
# SEM001 - tabel tidak ada                                                     #
# --------------------------------------------------------------------------- #


def test_sem001_tabel_tidak_ditemukan() -> None:
    assert codes("SELECT * FROM mahasisw") == ["SEM001"]
    assert messages("SELECT * FROM mahasisw") == [
        "Tabel 'mahasisw' tidak ditemukan. Maksudmu 'mahasiswa'?"
    ]


def test_sem001_pada_tabel_join() -> None:
    result = check("SELECT m.nama FROM mahasiswa m JOIN buku b ON m.nim = b.nim")
    assert "SEM001" in [error.code for error in result.errors]


def test_sem001_tanpa_saran_bila_tak_mirip() -> None:
    assert messages("SELECT * FROM tabel_ini_tidak_ada") == [
        "Tabel 'tabel_ini_tidak_ada' tidak ditemukan."
    ]


# --------------------------------------------------------------------------- #
# SEM002 - kolom tidak ada                                                     #
# --------------------------------------------------------------------------- #


def test_sem002_kolom_tidak_ditemukan() -> None:
    assert codes("SELECT nama, ipkk FROM mahasiswa") == ["SEM002"]
    assert messages("SELECT nama, ipkk FROM mahasiswa") == [
        "Kolom 'ipkk' tidak ditemukan di 'mahasiswa'. Maksudmu 'ipk'?"
    ]


def test_sem002_kolom_terkualifikasi_tidak_ada() -> None:
    assert messages("SELECT m.ipek FROM mahasiswa m") == [
        "Kolom 'ipek' tidak ditemukan di 'm'. Maksudmu 'ipk'?"
    ]


def test_sem002_nama_asli_tabel_setelah_alias() -> None:
    assert messages("SELECT mahasiswa.ipk FROM mahasiswa m") == [
        "Tabel 'mahasiswa' memiliki alias 'm'; gunakan 'm.ipk' sebagai gantinya."
    ]


def test_sem002_kualifikasi_tidak_dikenal() -> None:
    assert messages("SELECT m2.nim FROM mahasiswa m")[0].startswith(
        "Referensi 'm2.nim' tidak ditemukan;"
    )


def test_sem002_kolom_pada_banyak_tabel() -> None:
    sql = "SELECT ipkk FROM mahasiswa m JOIN nilai n ON m.nim = n.nim"
    assert messages(sql) == [
        "Kolom 'ipkk' tidak ditemukan di FROM/JOIN. Maksudmu 'ipk'?"
    ]


def test_sem002_kolom_hanya_pada_salah_satu_tabel_tetap_valid() -> None:
    assert check("SELECT ipk FROM mahasiswa m JOIN nilai n ON m.nim = n.nim").ok


def test_sem002_distinct_order_by_di_luar_select() -> None:
    assert codes("SELECT DISTINCT jurusan FROM mahasiswa ORDER BY ipk") == ["SEM002"]
    assert "DISTINCT" in messages("SELECT DISTINCT jurusan FROM mahasiswa ORDER BY ipk")[0]


def test_sem002_kualifikasi_star_tidak_dikenal() -> None:
    assert codes("SELECT x.* FROM mahasiswa") == ["SEM002"]


# --------------------------------------------------------------------------- #
# SEM003 - kolom ambigu                                                        #
# --------------------------------------------------------------------------- #


def test_sem003_kolom_ada_di_dua_tabel() -> None:
    sql = "SELECT nim FROM nilai n JOIN mahasiswa m ON n.nim = m.nim"
    assert messages(sql) == ["Kolom 'nim' ambigu: ada di m dan n."]


def test_sem003_alias_select_ganda() -> None:
    sql = "SELECT nama AS n, ipk AS n FROM mahasiswa ORDER BY n"
    assert messages(sql) == [
        "Kolom 'n' ambigu: alias SELECT 'n' didefinisikan 2 kali."
    ]


# --------------------------------------------------------------------------- #
# SEM004 - alias duplikat                                                      #
# --------------------------------------------------------------------------- #


def test_sem004_alias_duplikat() -> None:
    sql = "SELECT nim FROM mahasiswa m JOIN nilai m ON m.nim = m.nim"
    assert codes(sql) == ["SEM004"]
    assert messages(sql) == ["Alias 'm' dipakai dua kali."]


# --------------------------------------------------------------------------- #
# SEM005 - tipe tidak cocok                                                    #
# --------------------------------------------------------------------------- #


def test_sem005_banding_teks_dengan_int() -> None:
    assert messages("SELECT nama FROM mahasiswa WHERE nama = 5") == [
        "Tidak bisa membandingkan TEXT dengan INT."
    ]


def test_sem005_and_operand_bukan_boolean() -> None:
    errors = check("SELECT nama FROM mahasiswa WHERE ipk AND nama").errors
    assert [error.code for error in errors] == ["SEM005", "SEM005"]
    assert errors[0].message == "Operator 'AND' membutuhkan operand boolean; ditemukan DOUBLE."


def test_sem005_like_operand_bukan_teks() -> None:
    assert messages("SELECT nama FROM mahasiswa WHERE ipk LIKE '3%'") == [
        "LIKE membutuhkan operand TEXT; ditemukan DOUBLE."
    ]


def test_sem005_aritmetika_operand_bukan_numerik() -> None:
    assert messages("SELECT nim + nama FROM mahasiswa") == [
        "Operator '+' membutuhkan operand numerik; ditemukan TEXT."
    ]


def test_sem005_div_operand_bukan_int() -> None:
    assert messages("SELECT nama DIV 3 FROM mahasiswa") == [
        "Operator 'DIV' membutuhkan operand INT; ditemukan TEXT."
    ]


def test_sem005_not_operand_bukan_boolean() -> None:
    assert messages("SELECT nama FROM mahasiswa WHERE NOT ipk") == [
        "Operator 'NOT' membutuhkan operand boolean; ditemukan DOUBLE."
    ]


# --------------------------------------------------------------------------- #
# SEM006 - agregat di WHERE/ON/GROUP BY                                        #
# --------------------------------------------------------------------------- #


def test_sem006_agregat_di_where() -> None:
    assert messages("SELECT nama FROM mahasiswa WHERE COUNT(*) > 1") == [
        "Agregat tidak boleh dipakai di WHERE."
    ]


def test_sem006_agregat_di_group_by() -> None:
    sql = "SELECT nim FROM mahasiswa GROUP BY COUNT(*)"
    assert "SEM006" in codes(sql)
    assert "Agregat tidak boleh dipakai di GROUP BY." in messages(sql)


def test_sem006_agregat_di_on() -> None:
    sql = "SELECT nama FROM mahasiswa m JOIN nilai n ON m.nim = COUNT(*)"
    assert "SEM006" in codes(sql)
    assert "Agregat tidak boleh dipakai di ON." in messages(sql)


# --------------------------------------------------------------------------- #
# SEM007 - kolom non-agregat tanpa GROUP BY                                    #
# --------------------------------------------------------------------------- #


def test_sem007_kolom_tidak_dibungkus() -> None:
    assert messages("SELECT nama, COUNT(*) FROM mahasiswa GROUP BY jurusan") == [
        "Kolom 'nama' harus ada di GROUP BY atau dibungkus agregat."
    ]


def test_sem007_pada_having() -> None:
    sql = "SELECT jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan HAVING nama > 1"
    assert "SEM007" in codes(sql)
    assert "Kolom 'nama' harus ada di GROUP BY atau dibungkus agregat." in messages(sql)


def test_sem007_pada_order_by() -> None:
    sql = "SELECT jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan ORDER BY ipk"
    assert "SEM007" in codes(sql)
    assert any("Kolom 'ipk'" in message for message in messages(sql))


def test_sem007_tanpa_group_by_tanpa_agregat() -> None:
    assert check("SELECT nama, ipk FROM mahasiswa").ok


def test_sem007_bungkus_agregat_diperbolehkan() -> None:
    assert check("SELECT jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan").ok


# --------------------------------------------------------------------------- #
# SEM008 - HAVING tanpa GROUP BY                                               #
# --------------------------------------------------------------------------- #


def test_sem008_having_tanpa_group_by() -> None:
    assert "SEM008" in codes("SELECT COUNT(*) FROM mahasiswa HAVING COUNT(*) > 2")


# --------------------------------------------------------------------------- #
# SEM009 - fungsi tidak dikenal                                                #
# --------------------------------------------------------------------------- #


def test_sem009_fungsi_tidak_dikenal() -> None:
    assert messages("SELECT median(ipk) FROM mahasiswa") == [
        "Fungsi 'median' tidak didukung."
    ]


# --------------------------------------------------------------------------- #
# SEM010 - argumen fungsi tidak valid                                          #
# --------------------------------------------------------------------------- #


def test_sem010_sum_bintang() -> None:
    assert messages("SELECT SUM(*) FROM mahasiswa") == [
        "SUM(*) tidak didukung; hanya COUNT(*) yang boleh memakai '*'."
    ]


def test_sem010_avg_teks() -> None:
    assert messages("SELECT AVG(nama) FROM mahasiswa") == [
        "AVG membutuhkan argumen numerik."
    ]


def test_sem010_sum_teks() -> None:
    assert messages("SELECT SUM(nama) FROM mahasiswa") == [
        "SUM membutuhkan argumen numerik."
    ]


# --------------------------------------------------------------------------- #
# SEM011 - kondisi bukan boolean                                               #
# --------------------------------------------------------------------------- #


def test_sem011_where_bukan_boolean() -> None:
    assert messages("SELECT COUNT(*) FROM mahasiswa WHERE ipk") == [
        "Kondisi WHERE harus boolean."
    ]


def test_sem011_having_bukan_boolean() -> None:
    sql = "SELECT jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan HAVING jurusan"
    assert "SEM011" in codes(sql)
    assert "Kondisi HAVING harus boolean." in messages(sql)


# --------------------------------------------------------------------------- #
# SEM012 - star bersama GROUP BY/agregat                                      #
# --------------------------------------------------------------------------- #


def test_sem012_star_dengan_group_by() -> None:
    assert codes("SELECT * FROM mahasiswa GROUP BY jurusan") == ["SEM012"]


def test_sem012_star_dengan_agregat() -> None:
    assert codes("SELECT *, COUNT(*) FROM mahasiswa") == ["SEM012"]


def test_sem012_star_terkualifikasi_dengan_group_by() -> None:
    assert codes("SELECT m.* FROM mahasiswa m GROUP BY jurusan") == ["SEM012"]


# --------------------------------------------------------------------------- #
# SEM013 - ON bukan equi-join                                                  #
# --------------------------------------------------------------------------- #


def test_sem013_operator_bukan_equality() -> None:
    sql = "SELECT nama FROM mahasiswa m JOIN nilai n ON m.nim > n.nim"
    assert messages(sql)[0] == "ON hanya mendukung a.x = b.y [AND ...]."


def test_sem013_sisi_bukan_kolom() -> None:
    sql = "SELECT nama FROM mahasiswa m JOIN nilai n ON m.nim = 5"
    assert "SEM013" in codes(sql)


def test_sem013_sisi_bukan_lawan_sisi() -> None:
    """``ON`` harus menyandingkan sisi kiri dan sisi kanan (``SPEC.md`` §8)."""
    sql = "SELECT nama FROM mahasiswa m JOIN nilai n ON m.nim = m.ipk"
    assert messages(sql) == ["ON hanya mendukung a.x = b.y [AND ...]."]


def test_sem013_konjungsi_equi_valid() -> None:
    sql = (
        "SELECT m.nama FROM mahasiswa m JOIN nilai n "
        "ON m.nim = n.nim AND m.nim = n.nim"
    )
    assert check(sql).ok, check(sql).errors


# --------------------------------------------------------------------------- #
# SEM014 - nomor urut kolom                                                    #
# --------------------------------------------------------------------------- #


def test_sem014_order_by_ordinal() -> None:
    assert codes("SELECT jurusan FROM mahasiswa ORDER BY 1") == ["SEM014"]


def test_sem014_group_by_ordinal() -> None:
    assert codes("SELECT jurusan FROM mahasiswa GROUP BY 1") == ["SEM014"]


# --------------------------------------------------------------------------- #
# SEM015 - ekspresi boolean di SELECT                                          #
# --------------------------------------------------------------------------- #


def test_sem015_boolean_di_select() -> None:
    assert messages("SELECT ipk > 3 FROM mahasiswa") == [
        "Ekspresi boolean di SELECT belum didukung."
    ]


# --------------------------------------------------------------------------- #
# SEM016 - kolom bertipe tak didukung                                          #
# --------------------------------------------------------------------------- #


def test_sem016_kolom_date() -> None:
    sql = "SELECT nik, tgl_lahir FROM karyawan"
    result = check(sql, HR)
    assert [error.code for error in result.errors] == ["SEM016"]
    assert result.errors[0].message == "Kolom 'tgl_lahir' bertipe DATE belum didukung."


def test_sem016_dengan_alias_tabel() -> None:
    sql = "SELECT k.tgl_lahir FROM karyawan k"
    assert messages(sql, HR) == ["Kolom 'tgl_lahir' bertipe DATE belum didukung."]


def test_sem016_lewat_star() -> None:
    sql = "SELECT * FROM karyawan"
    assert messages(sql, HR) == ["Kolom 'tgl_lahir' bertipe DATE belum didukung."]


def test_sem016_kolom_lain_tetap_valid() -> None:
    assert check("SELECT nik, nama FROM karyawan", HR).ok


# --------------------------------------------------------------------------- #
# Pengumpulan dan posisi error                                                 #
# --------------------------------------------------------------------------- #


def test_semua_error_dikumpulkan_dan_urut() -> None:
    sql = "SELECT ipkk, nama, COUNT(*) FROM mahasiswa GROUP BY jurusan"
    errors = check(sql).errors
    assert [error.code for error in errors] == ["SEM002", "SEM007"]
    assert errors[0].column < errors[1].column


def test_render_error_semantik_memakai_caret() -> None:
    sql = "SELECT nama, ipkk FROM mahasiswa"
    error = check(sql).errors[0]
    rendered = render_error(error, sql)
    lines = rendered.splitlines()
    assert lines[0].startswith("SEM002: Kolom 'ipkk' tidak ditemukan di 'mahasiswa'.")
    assert lines[1] == "  1 | SELECT nama, ipkk FROM mahasiswa"
    assert lines[2] == " " * (len("  1 | ") + error.column - 1) + "^" * error.length
    assert error.length == 4


# --------------------------------------------------------------------------- #
# Resolusi nama dan alias                                                      #
# --------------------------------------------------------------------------- #


def test_column_refs_memakai_prefix_internal() -> None:
    result = check("SELECT m.ipk FROM mahasiswa m")
    assert set(result.column_refs.values()) == {"m.ipk"}


def test_order_by_mendahulukan_alias_select() -> None:
    sql = "SELECT ipk AS jurusan, jurusan FROM mahasiswa ORDER BY jurusan"
    result = check(sql)
    order_column = result.ast.order_by[0].expr
    assert isinstance(order_column, Column)
    assert order_column in result.alias_refs
    assert order_column not in result.column_refs


def test_group_by_mendahulukan_kolom_tabel() -> None:
    sql = "SELECT ipk AS jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan"
    result = check(sql)
    group_column = result.ast.group_by[0]
    assert isinstance(group_column, Column)
    assert result.column_refs.get(group_column) == "mahasiswa.jurusan"
    assert group_column not in result.alias_refs


def test_having_mendahulukan_kolom_tabel() -> None:
    sql = "SELECT ipk AS jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan HAVING jurusan"
    result = check(sql)
    having_column = result.ast.having
    assert isinstance(having_column, Column)
    assert result.column_refs.get(having_column) == "mahasiswa.jurusan"


def test_having_jatuh_ke_alias_bila_kolom_tabel_tak_ada() -> None:
    sql = "SELECT ipk AS total, COUNT(*) FROM mahasiswa GROUP BY jurusan HAVING total"
    result = check(sql)
    having_column = result.ast.having
    assert isinstance(having_column, Column)
    assert result.alias_refs.get(having_column) is not None


def test_select_dan_where_tidak_memakai_alias_select() -> None:
    sql = "SELECT ipk AS jumlah FROM mahasiswa WHERE jumlah IS NOT NULL"
    assert messages(sql) == ["Kolom 'jumlah' tidak ditemukan di 'mahasiswa'."]


def test_alias_direwrite_ke_ekspresi_asli_pada_order_by() -> None:
    sql = "SELECT ipk + 1 AS x FROM mahasiswa ORDER BY x"
    result = check(sql)
    assert result.ok, result.errors
    order_column = result.ast.order_by[0].expr
    assert isinstance(order_column, Column)
    assert order_column in result.alias_refs


# --------------------------------------------------------------------------- #
# Inferensi tipe                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1 FROM mahasiswa", DataType.INT),
        ("SELECT 1.5 FROM mahasiswa", DataType.DOUBLE),
        ("SELECT 'x' FROM mahasiswa", DataType.TEXT),
        ("SELECT NULL FROM mahasiswa", DataType.NULL),
        ("SELECT COUNT(*) FROM mahasiswa", DataType.INT),
        ("SELECT COUNT(nim) FROM mahasiswa", DataType.INT),
        ("SELECT AVG(ipk) FROM mahasiswa", DataType.DOUBLE),
        ("SELECT SUM(sks) FROM mata_kuliah", DataType.INT),
        ("SELECT MIN(nama) FROM mahasiswa", DataType.TEXT),
        ("SELECT MAX(ipk) FROM mahasiswa", DataType.DOUBLE),
        ("SELECT sks + 1 FROM mata_kuliah", DataType.INT),
        ("SELECT sks / 3 FROM mata_kuliah", DataType.DOUBLE),
        ("SELECT sks DIV 3 FROM mata_kuliah", DataType.INT),
        ("SELECT sks % 3 FROM mata_kuliah", DataType.INT),
        ("SELECT ipk + 1 FROM mahasiswa", DataType.DOUBLE),
        ("SELECT -sks FROM mata_kuliah", DataType.INT),
    ],
)
def test_inferensi_tipe(sql: str, expected: DataType) -> None:
    result = check(sql)
    assert result.ok, result.errors
    item = result.ast.items[0]
    assert isinstance(item, SelectItem)
    assert result.types[item.expr] is expected


def test_tipe_perbandingan_boolean() -> None:
    """Ekspresi boolean bertipe BOOLEAN meski SEM015 menolaknya di SELECT."""
    result = check("SELECT 1 = 1 FROM mahasiswa")
    item = result.ast.items[0]
    assert isinstance(item, SelectItem)
    assert result.types[item.expr] is DataType.BOOLEAN
    assert [error.code for error in result.errors] == ["SEM015"]


def test_tipe_kolom_diambil_dari_skema() -> None:
    result = check("SELECT m.ipk FROM mahasiswa m")
    column = result.ast.items[0]
    assert isinstance(column, SelectItem)
    assert result.types[column.expr] is DataType.DOUBLE


def test_tipe_where_boolean() -> None:
    result = check("SELECT 1 FROM mahasiswa WHERE ipk > 3")
    assert result.ast.where is not None
    assert result.types[result.ast.where] is DataType.BOOLEAN


# --------------------------------------------------------------------------- #
# schema_loader                                                                #
# --------------------------------------------------------------------------- #


def test_load_schema_json_sesuai_spec() -> None:
    assert SCHEMA.table_names == ("mahasiswa", "mata_kuliah", "nilai")
    mahasiswa = SCHEMA.get("mahasiswa")
    assert mahasiswa is not None
    assert mahasiswa.column_names() == ("nim", "nama", "jurusan", "ipk", "kota")
    assert mahasiswa.get("IPK") is not None
    assert SCHEMA.get("MAHASISWA") is not None


def test_dump_schema_json_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "schema.json"
    dump_schema_json(SCHEMA, target)
    assert load_schema_json(target).tables == SCHEMA.tables


def test_schema_from_mapping_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="tidak dikenal"):
        schema_from_mapping({"t": {"kolom": "VARCHAR2"}})


def test_schema_from_mapping_rejects_non_object_table() -> None:
    with pytest.raises(ValueError, match="objek"):
        schema_from_mapping({"t": ["a"]})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    ("mysql_type", "expected", "warned"),
    [
        ("int", DataType.INT, False),
        ("bigint unsigned", DataType.INT, False),
        ("tinyint(1)", DataType.INT, False),
        ("double", DataType.DOUBLE, False),
        ("float", DataType.DOUBLE, False),
        ("decimal(10,2)", DataType.DOUBLE, True),
        ("varchar(50)", DataType.TEXT, False),
        ("char(2)", DataType.TEXT, False),
        ("longtext", DataType.TEXT, False),
        ("date", DataType.UNSUPPORTED, False),
        ("datetime", DataType.UNSUPPORTED, False),
        ("json", DataType.UNSUPPORTED, False),
        ("enum('a','b')", DataType.UNSUPPORTED, False),
        ("blob", DataType.UNSUPPORTED, False),
    ],
)
def test_pemetaan_tipe_mysql(mysql_type: str, expected: DataType, warned: bool) -> None:
    tipe, warning = mysql_type_to_data_type(mysql_type)
    assert tipe is expected
    assert (warning is not None) is warned


def test_parse_dsn_lengkap() -> None:
    options = parse_dsn("mysql://user:p%40ss@db.example:3307/mydb")
    assert options == {
        "host": "db.example",
        "port": 3307,
        "user": "user",
        "password": "p@ss",
        "database": "mydb",
    }


def test_parse_dsn_default_port() -> None:
    options = parse_dsn("mysql://root:root@localhost/sqlc_test")
    assert options["port"] == 3306
    assert options["database"] == "sqlc_test"


def test_parse_dsn_skema_salah() -> None:
    with pytest.raises(ValueError, match="tidak didukung"):
        parse_dsn("postgres://root@localhost/db")


def test_parse_dsn_tanpa_basis_data() -> None:
    with pytest.raises(ValueError, match="basis data"):
        parse_dsn("mysql://root@localhost")


@pytest.mark.mysql
def test_introspeksi_mysql_sama_dengan_schema_json() -> None:
    introspected = introspect_mysql(DSN)
    assert introspected.table_names == SCHEMA.table_names
    for expected in SCHEMA.tables:
        actual = introspected.get(expected.name)
        assert actual is not None, expected.name
        assert actual.column_names() == expected.column_names()
        for expected_column, actual_column in zip(expected.columns, actual.columns):
            assert actual_column.type is expected_column.type, actual_column.name


@pytest.mark.mysql
def test_introspeksi_mysql_menghasilkan_skema_pakai() -> None:
    schema = introspect_mysql(DSN)
    assert check("SELECT m.nama, n.skor FROM mahasiswa m "
                 "JOIN nilai n ON m.nim = n.nim", schema).ok


def test_star_node_is_select_item_operand() -> None:
    """``Star`` bukan ``SelectItem``; dipakai untuk membedakan ``*`` dari ekspresi."""
    ast = parse(tokenize("SELECT * FROM mahasiswa"), "SELECT * FROM mahasiswa")
    assert isinstance(ast.items[0], Star)
    assert not isinstance(ast.items[0], SelectItem)


def test_aggregate_function_node_dikenali() -> None:
    expr = select_expr("SELECT COUNT(*) FROM mahasiswa")
    assert isinstance(expr, FunctionCall)
    assert expr.name == "count"
    literal = select_expr("SELECT 7 FROM mahasiswa")
    assert isinstance(literal, Literal)
    assert literal.kind == "INT"
