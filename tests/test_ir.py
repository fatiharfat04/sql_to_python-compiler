"""Tes IR: urutan operator, ekstraksi agregat, rewrite alias, dan lipat konstan (Langkah 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sqlcompiler.ast_nodes import (
    Between,
    BinaryOp,
    Column,
    Expr,
    FunctionCall,
    InList,
    IsNull,
    Like,
    Literal,
    SelectItem,
    Star,
    UnaryOp,
)
from sqlcompiler.errors import SemanticError
from sqlcompiler.ir import (
    Aggregate,
    Distinct,
    Filter,
    Join,
    Limit,
    Plan,
    PlanNode,
    Project,
    Scan,
    Sort,
    fold_constant,
    format_expr,
    format_plan,
    lower,
)
from sqlcompiler.lexer import tokenize
from sqlcompiler.parser import parse
from sqlcompiler.schema_loader import load_schema_json
from sqlcompiler.semantic import AnalysisResult, analyze

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = load_schema_json(ROOT / "examples" / "schema.json")

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


def check(sql: str) -> AnalysisResult:
    return analyze(parse(tokenize(sql), sql), SCHEMA)


def plan(sql: str) -> Plan:
    result = check(sql)
    assert result.ok, result.errors
    return lower(result)


def children(node: PlanNode) -> tuple[PlanNode, ...]:
    if isinstance(node, Scan):
        return ()
    if isinstance(node, Join):
        return (node.left, node.right)
    if isinstance(node, (Filter, Aggregate, Sort, Project, Distinct, Limit)):
        return (node.child,)
    raise TypeError(node)  # pragma: no cover


def shape(node: PlanNode) -> list[str]:
    """Nama kelas setiap node dalam urutan praperjalanan (pre-order)."""
    out = [type(node).__name__]
    for child in children(node):
        out.extend(shape(child))
    return out


def find(node: PlanNode, kind: type) -> PlanNode | None:
    if isinstance(node, kind):
        return node
    for child in children(node):
        found = find(child, kind)
        if found is not None:
            return found
    return None


def folded(expr_sql: str, table: str = "mahasiswa") -> Expr:
    """Lipat konstan ekspresi ``expr_sql`` setelah melewati analisis semantik."""
    sql = f"SELECT {expr_sql} FROM {table}"
    result = check(sql)
    assert result.ok, result.errors
    item = result.ast.items[0]
    assert isinstance(item, SelectItem)
    return fold_constant(item.expr)


def expr_columns(expr: Expr | Star) -> list[Column]:
    if isinstance(expr, Column):
        return [expr]
    if isinstance(expr, (Literal, Star)):
        return []
    if isinstance(expr, UnaryOp):
        return expr_columns(expr.operand)
    if isinstance(expr, BinaryOp):
        return expr_columns(expr.left) + expr_columns(expr.right)
    if isinstance(expr, Like):
        return expr_columns(expr.operand)
    if isinstance(expr, InList):
        out = expr_columns(expr.operand)
        for value in expr.values:
            out += expr_columns(value)
        return out
    if isinstance(expr, Between):
        return (
            expr_columns(expr.operand)
            + expr_columns(expr.low)
            + expr_columns(expr.high)
        )
    if isinstance(expr, IsNull):
        return expr_columns(expr.operand)
    if isinstance(expr, FunctionCall):
        return [] if isinstance(expr.arg, Star) else expr_columns(expr.arg)
    raise TypeError(expr)  # pragma: no cover


def plan_columns(node: PlanNode) -> list[Column]:
    """Seluruh referensi kolom yang dipakai oleh ekspresi-elemen plan."""
    if isinstance(node, Scan):
        return []
    if isinstance(node, Join):
        return plan_columns(node.left) + plan_columns(node.right)
    if isinstance(node, Filter):
        return expr_columns(node.predicate) + plan_columns(node.child)
    if isinstance(node, Aggregate):
        out: list[Column] = []
        for _, expr in node.group_keys:
            out += expr_columns(expr)
        for _, _, arg in node.aggs:
            out += expr_columns(arg)
        return out + plan_columns(node.child)
    if isinstance(node, Sort):
        out = []
        for expr, _ in node.keys:
            out += expr_columns(expr)
        return out + plan_columns(node.child)
    if isinstance(node, Project):
        out = []
        for expr, _ in node.items:
            out += expr_columns(expr)
        return out + plan_columns(node.child)
    if isinstance(node, (Distinct, Limit)):
        return plan_columns(node.child)
    raise TypeError(node)  # pragma: no cover


def assert_literal(expr: Expr, value: object, kind: str) -> None:
    assert isinstance(expr, Literal), expr
    assert expr.value == value, expr
    assert expr.kind == kind, expr


# --------------------------------------------------------------------------- #
# Urutan operator                                                              #
# --------------------------------------------------------------------------- #


def test_urutan_operator_minimal() -> None:
    assert shape(plan("SELECT nama FROM mahasiswa").root) == ["Project", "Scan"]


def test_urutan_operator_dengan_where() -> None:
    built = shape(plan("SELECT nama FROM mahasiswa WHERE ipk > 3").root)
    assert built == ["Project", "Filter", "Scan"]


def test_urutan_operator_penuh() -> None:
    sql = (
        "SELECT jurusan, COUNT(*) AS j FROM mahasiswa WHERE ipk > 3 "
        "GROUP BY jurusan HAVING COUNT(*) > 1 ORDER BY j DESC LIMIT 5"
    )
    assert shape(plan(sql).root) == [
        "Limit",
        "Project",
        "Sort",
        "Filter",
        "Aggregate",
        "Filter",
        "Scan",
    ]


def test_urutan_operator_distinct_setelah_project() -> None:
    assert shape(plan("SELECT DISTINCT jurusan FROM mahasiswa").root) == [
        "Distinct",
        "Project",
        "Scan",
    ]


def test_urutan_operator_join() -> None:
    sql = (
        "SELECT m.nama FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim "
        "INNER JOIN mata_kuliah k ON n.kode = k.kode WHERE m.ipk > 3"
    )
    assert shape(plan(sql).root) == [
        "Project",
        "Filter",
        "Join",
        "Join",
        "Scan",
        "Scan",
        "Scan",
    ]


def test_tanpa_agregat_tanpa_group_by_tidak_membuat_operator_aggregate() -> None:
    assert find(plan("SELECT nama FROM mahasiswa").root, Aggregate) is None


def test_group_by_tanpa_agregat_tetap_membuat_operator_aggregate() -> None:
    node = find(plan("SELECT jurusan FROM mahasiswa GROUP BY jurusan").root, Aggregate)
    assert isinstance(node, Aggregate)
    assert node.aggs == ()
    assert [name for name, _ in node.group_keys] == ["mahasiswa.jurusan"]


def test_agregat_tanpa_group_by_menghasilkan_operator_tanpa_kunci() -> None:
    node = find(plan("SELECT COUNT(*) FROM mahasiswa").root, Aggregate)
    assert isinstance(node, Aggregate)
    assert node.group_keys == ()
    assert [name for name, _, _ in node.aggs] == ["__agg0"]


# --------------------------------------------------------------------------- #
# Ekstraksi agregat                                                            #
# --------------------------------------------------------------------------- #


def test_ekstraksi_agregat_berganda() -> None:
    sql = (
        "SELECT jurusan, COUNT(*) AS a, AVG(ipk) AS b, SUM(ipk) AS c "
        "FROM mahasiswa GROUP BY jurusan"
    )
    node = find(plan(sql).root, Aggregate)
    assert isinstance(node, Aggregate)
    assert [(name, func) for name, func, _ in node.aggs] == [
        ("__agg0", "count"),
        ("__agg1", "avg"),
        ("__agg2", "sum"),
    ]


def test_agregat_sama_pada_select_dan_having_berbagi_nama() -> None:
    sql = (
        "SELECT jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan "
        "HAVING COUNT(*) > 2"
    )
    built = plan(sql)
    node = find(built.root, Aggregate)
    assert isinstance(node, Aggregate)
    assert [name for name, _, _ in node.aggs] == ["__agg0"]
    having = find(built.root, Filter)
    assert isinstance(having, Filter)
    assert having.label == "HAVING"
    assert format_expr(having.predicate) == "(__agg0 > 2)"


def test_agregat_berbeda_tidak_berbagi_nama() -> None:
    sql = (
        "SELECT jurusan, COUNT(*), COUNT(ipk) FROM mahasiswa GROUP BY jurusan "
        "HAVING COUNT(*) <> COUNT(ipk)"
    )
    node = find(plan(sql).root, Aggregate)
    assert isinstance(node, Aggregate)
    assert [name for name, _, _ in node.aggs] == ["__agg0", "__agg1"]


def test_argumen_agregat_dinormalisasi() -> None:
    node = find(plan("SELECT COUNT(m.nim) FROM mahasiswa m").root, Aggregate)
    assert isinstance(node, Aggregate)
    arg = node.aggs[0][2]
    assert isinstance(arg, Column)
    assert arg.table is None
    assert arg.name == "m.nim"


def test_count_bintang_argumen_tetap_star() -> None:
    node = find(plan("SELECT COUNT(*) FROM mahasiswa").root, Aggregate)
    assert isinstance(node, Aggregate)
    assert isinstance(node.aggs[0][2], Star)


# --------------------------------------------------------------------------- #
# Rewrite alias                                                                #
# --------------------------------------------------------------------------- #


def test_rewrite_alias_pada_order_by() -> None:
    node = find(plan("SELECT ipk AS p FROM mahasiswa ORDER BY p DESC").root, Sort)
    assert isinstance(node, Sort)
    expr, descending = node.keys[0]
    assert descending is True
    assert isinstance(expr, Column)
    assert expr.table is None
    assert expr.name == "mahasiswa.ipk"


def test_rewrite_alias_agregat_pada_order_by() -> None:
    sql = (
        "SELECT jurusan, COUNT(*) AS jumlah FROM mahasiswa GROUP BY jurusan "
        "ORDER BY jumlah DESC"
    )
    node = find(plan(sql).root, Sort)
    assert isinstance(node, Sort)
    expr, descending = node.keys[0]
    assert descending is True
    assert isinstance(expr, Column)
    assert expr.name == "__agg0"


def test_rewrite_alias_pada_group_by() -> None:
    """``GROUP BY`` mencari kolom tabel lebih dulu, baru alias; keduanya ditulis ulang."""
    sql = "SELECT ipk AS total, COUNT(*) FROM mahasiswa GROUP BY total"
    built = plan(sql)
    node = find(built.root, Aggregate)
    assert isinstance(node, Aggregate)
    name, expr = node.group_keys[0]
    assert name == "mahasiswa.ipk"
    assert isinstance(expr, Column)
    assert expr.name == "mahasiswa.ipk"
    project = find(built.root, Project)
    assert isinstance(project, Project)
    first, first_name = project.items[0]
    assert first_name == "total"
    assert isinstance(first, Column)
    assert first.name == "mahasiswa.ipk"


def test_sort_berada_di_bawah_project() -> None:
    """Sort harus bisa memakai kolom sumber sehingga ditempatkan sebelum Project."""
    root = plan("SELECT nama FROM mahasiswa ORDER BY ipk").root
    assert isinstance(root, Project)
    assert isinstance(root.child, Sort)
    assert isinstance(root.child.child, Scan)


def test_kunci_group_by_kompleks_dipindahkan_ke_kolom_khusus() -> None:
    sql = "SELECT sks + 1 FROM mata_kuliah GROUP BY sks + 1"
    built = plan(sql)
    node = find(built.root, Aggregate)
    assert isinstance(node, Aggregate)
    name, expr = node.group_keys[0]
    assert name == "__g0"
    assert not isinstance(expr, Column)
    project = find(built.root, Project)
    assert isinstance(project, Project)
    out_expr, output = project.items[0]
    assert isinstance(out_expr, Column)
    assert out_expr.name == "__g0"
    assert output == "sks + 1"


# --------------------------------------------------------------------------- #
# Normalisasi kolom                                                            #
# --------------------------------------------------------------------------- #


def test_seluruh_kolom_plan_memakai_prefiks_internal() -> None:
    sql = (
        "SELECT m.nama, n.skor FROM mahasiswa m JOIN nilai n ON m.nim = n.nim "
        "WHERE n.skor > 3 ORDER BY m.nama"
    )
    columns = plan_columns(plan(sql).root)
    assert columns
    for column in columns:
        assert column.table is None
        assert "." in column.name


def test_scan_memakai_alias_query() -> None:
    node = find(plan("SELECT m.nim FROM mahasiswa m").root, Scan)
    assert isinstance(node, Scan)
    assert (node.table, node.alias) == ("mahasiswa", "m")


def test_scan_tanpa_alias_memakai_nama_tabel() -> None:
    node = find(plan("SELECT nim FROM nilai").root, Scan)
    assert isinstance(node, Scan)
    assert (node.table, node.alias) == ("nilai", "nilai")


# --------------------------------------------------------------------------- #
# JOIN                                                                         #
# --------------------------------------------------------------------------- #


def test_kunci_join_tunggal() -> None:
    node = find(
        plan("SELECT m.nama FROM mahasiswa m JOIN nilai n ON m.nim = n.nim").root,
        Join,
    )
    assert isinstance(node, Join)
    assert node.kind == "INNER"
    assert node.keys == (("m.nim", "n.nim"),)


def test_kunci_join_banyak_kondisi() -> None:
    sql = (
        "SELECT m.nama FROM mahasiswa m JOIN nilai n "
        "ON m.nim = n.nim AND m.nim = n.nim"
    )
    node = find(plan(sql).root, Join)
    assert isinstance(node, Join)
    assert node.keys == (("m.nim", "n.nim"), ("m.nim", "n.nim"))


def test_kunci_join_left_dengan_urutan_terbalik() -> None:
    node = find(
        plan("SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON n.nim = m.nim").root,
        Join,
    )
    assert isinstance(node, Join)
    assert node.kind == "LEFT"
    assert node.keys == (("m.nim", "n.nim"),)


def test_join_rantai_tiga_tabel() -> None:
    sql = (
        "SELECT m.nama FROM mahasiswa m "
        "JOIN nilai n ON m.nim = n.nim "
        "JOIN mata_kuliah k ON n.kode = k.kode"
    )
    joins: list[PlanNode] = []

    def collect(node: PlanNode) -> None:
        if isinstance(node, Join):
            joins.append(node)
        for child in children(node):
            collect(child)

    collect(plan(sql).root)
    assert len(joins) == 2
    # Praperjalanan: akar (join rantai kedua) dikunjungi lebih dulu.
    assert joins[0].keys == (("n.kode", "k.kode"),)
    assert joins[1].keys == (("m.nim", "n.nim"),)


# --------------------------------------------------------------------------- #
# Ekspansi ``*`` dan nama keluaran                                             #
# --------------------------------------------------------------------------- #


def test_star_diekspansikan_sesuai_urutan_tabel() -> None:
    node = find(plan("SELECT * FROM mahasiswa").root, Project)
    assert isinstance(node, Project)
    assert [name for _, name in node.items] == [
        "nim",
        "nama",
        "jurusan",
        "ipk",
        "kota",
    ]


def test_star_join_diekspansikan_berurutan() -> None:
    sql = "SELECT * FROM mahasiswa m JOIN nilai n ON m.nim = n.nim"
    node = find(plan(sql).root, Project)
    assert isinstance(node, Project)
    assert [name for _, name in node.items] == [
        "nim",
        "nama",
        "jurusan",
        "ipk",
        "kota",
        "nim",
        "kode",
        "skor",
    ]


def test_alias_star_diekspansikan_hanya_satu_tabel() -> None:
    sql = "SELECT n.*, m.nama FROM mahasiswa m JOIN nilai n ON m.nim = n.nim"
    node = find(plan(sql).root, Project)
    assert isinstance(node, Project)
    assert [name for _, name in node.items] == ["nim", "kode", "skor", "nama"]


def test_nama_keluaran_dari_alias_kolom_dan_teks() -> None:
    sql = "SELECT nama, m.ipk AS p, ipk + 1 FROM mahasiswa m"
    node = find(plan(sql).root, Project)
    assert isinstance(node, Project)
    assert [name for _, name in node.items] == ["nama", "p", "ipk + 1"]


# --------------------------------------------------------------------------- #
# LIMIT                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT nama FROM mahasiswa LIMIT 5", (5, 0)),
        ("SELECT nama FROM mahasiswa LIMIT 2, 5", (5, 2)),
        ("SELECT nama FROM mahasiswa LIMIT 5 OFFSET 2", (5, 2)),
        ("SELECT nama FROM mahasiswa LIMIT 0", (0, 0)),
    ],
)
def test_bentuk_limit(sql: str, expected: tuple[int, int]) -> None:
    node = find(plan(sql).root, Limit)
    assert isinstance(node, Limit)
    assert (node.n, node.offset) == expected


def test_tanpa_limit_tidak_membuat_operator_limit() -> None:
    assert find(plan("SELECT nama FROM mahasiswa").root, Limit) is None


# --------------------------------------------------------------------------- #
# Lipat konstan                                                                #
# --------------------------------------------------------------------------- #


def test_fold_penjumlahan() -> None:
    assert_literal(folded("1 + 2"), 3, "INT")


def test_fold_pembagian_bulat_desimal() -> None:
    assert_literal(folded("7 / 2"), 3.5, "DOUBLE")


def test_fold_pembagian_desimal_dibulatkan_empat() -> None:
    assert_literal(folded("1 / 3"), 0.3333, "DOUBLE")
    assert_literal(folded("2 / 3"), 0.6667, "DOUBLE")


def test_fold_null_ditambahkan_satu() -> None:
    assert_literal(folded("NULL + 1"), None, "NULL")


def test_fold_pembagi_nol_pada_kolom() -> None:
    assert_literal(folded("ipk / 0"), None, "NULL")


def test_fold_modulo_pembagi_nol() -> None:
    assert_literal(folded("sks % 0", "mata_kuliah"), None, "NULL")


def test_fold_div_pembagi_nol() -> None:
    assert_literal(folded("sks DIV 0", "mata_kuliah"), None, "NULL")


def test_fold_tidak_menghapus_kolom() -> None:
    assert not isinstance(folded("ipk + 1"), Literal)


def test_fold_div_negatif() -> None:
    assert_literal(folded("-7 DIV 2"), -3, "INT")
    assert_literal(folded("7 DIV -2"), -3, "INT")


def test_fold_modulo_tanda_mengikuti_pembilang() -> None:
    assert_literal(folded("-7 % 3"), -1, "INT")
    assert_literal(folded("7 % -3"), 1, "INT")


def test_fold_unary_negatif() -> None:
    assert_literal(folded("-5"), -5, "INT")


def test_fold_bukan_aritmetika_dibiarkan() -> None:
    result = check("SELECT nama FROM mahasiswa WHERE ipk > 3")
    assert result.ok, result.errors
    assert result.ast.where is not None
    assert not isinstance(fold_constant(result.ast.where), Literal)


def test_fold_melekat_pada_kolom_dalam_ekspresi_kompleks() -> None:
    """``1 + 2`` di dalam ekspresi kolom ikut dilipat menjadi konstan."""
    node = folded("ipk + (1 + 2)")
    assert isinstance(node, BinaryOp)
    assert_literal(node.right, 3, "INT")


def test_fold_constant_tidak_pernah_melempar_pembagian_nol() -> None:
    sql = "SELECT sks / 0, sks DIV 0, sks % 0 FROM mata_kuliah"
    result = check(sql)
    assert result.ok, result.errors
    for item in result.ast.items:
        assert isinstance(item, SelectItem)
        assert_literal(fold_constant(item.expr), None, "NULL")


# --------------------------------------------------------------------------- #
# format_plan                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("sql", SPEC_EXAMPLES)
def test_format_plan_contoh_spec_terbaca(sql: str) -> None:
    text = format_plan(plan(sql))
    lines = text.splitlines()
    assert lines
    assert "Scan" in text
    assert "?" not in text
    for line in lines:
        assert line.strip()
        assert line == line.rstrip()


def test_format_plan_membedakan_where_dan_having() -> None:
    sql = (
        "SELECT jurusan, COUNT(*) FROM mahasiswa WHERE ipk > 3 GROUP BY jurusan "
        "HAVING COUNT(*) > 1"
    )
    text = format_plan(plan(sql))
    assert "Filter [WHERE]" in text
    assert "Filter [HAVING]" in text


def test_format_plan_menampilkan_kunci_join_dan_agregat() -> None:
    sql = (
        "SELECT m.jurusan, COUNT(*) AS j FROM mahasiswa m "
        "JOIN nilai n ON m.nim = n.nim GROUP BY m.jurusan"
    )
    text = format_plan(plan(sql))
    assert "Join INNER ON m.nim = n.nim" in text
    assert "__agg0 = count(*)" in text


def test_format_plan_menampilkan_limit_dan_distinct() -> None:
    text = format_plan(plan("SELECT DISTINCT nama FROM mahasiswa LIMIT 3 OFFSET 1"))
    assert "Limit n=3 offset=1" in text
    assert "Distinct" in text
    assert text.splitlines()[0].startswith("Limit")


def test_format_expr_membaca_seluruh_jenis_node() -> None:
    sql = (
        "SELECT nama FROM mahasiswa WHERE nama LIKE 'A%' AND nim IN (1, 2) "
        "AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL"
    )
    result = check(sql)
    assert result.ok, result.errors
    assert result.ast.where is not None
    text = format_expr(result.ast.where)
    assert "LIKE 'A%'" in text
    assert "nim IN (1, 2)" in text
    assert "ipk BETWEEN 3.0 AND 4.0" in text
    assert "ipk IS NOT NULL" in text


def test_format_expr_unary_dan_kolom_bertitik() -> None:
    result = check("SELECT -m.ipk FROM mahasiswa m")
    assert result.ok, result.errors
    item = result.ast.items[0]
    assert isinstance(item, SelectItem)
    assert format_expr(item.expr) == "(- m.ipk)"


# --------------------------------------------------------------------------- #
# Lain-lain                                                                    #
# --------------------------------------------------------------------------- #


def test_lower_mengangkat_error_semantik() -> None:
    result = check("SELECT ipkk FROM mahasiswa")
    assert not result.ok
    with pytest.raises(SemanticError):
        lower(result)


def test_lower_deterministik() -> None:
    sql = (
        "SELECT m.jurusan, COUNT(*) AS jumlah FROM mahasiswa m "
        "JOIN nilai n ON m.nim = n.nim GROUP BY m.jurusan ORDER BY jumlah"
    )
    assert lower(check(sql)) == lower(check(sql))


def test_plan_membungkus_akar() -> None:
    built = lower(check("SELECT nama FROM mahasiswa"))
    assert isinstance(built, Plan)
    assert isinstance(built.root, Project)


def test_seluruh_contoh_spec_14_menjadi_plan() -> None:
    for sql in SPEC_EXAMPLES:
        assert "Scan" in format_plan(plan(sql))
