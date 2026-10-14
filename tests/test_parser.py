"""Tes parser sesuai ``SPEC.md`` §5 dan §6 (Langkah 2: bagian inti)."""

from __future__ import annotations

import pytest

from sqlcompiler.ast_nodes import (
    Between,
    BinaryOp,
    Column,
    FunctionCall,
    InList,
    IsNull,
    Like,
    Literal,
    SelectItem,
    SelectStatement,
    Star,
    UnaryOp,
)
from sqlcompiler.errors import ParseError, SynCode
from sqlcompiler.lexer import tokenize
from sqlcompiler.parser import parse, pretty_print


def ast(sql: str) -> SelectStatement:
    return parse(tokenize(sql), sql)


def item0(sql: str) -> SelectItem:
    node = ast(sql).items[0]
    assert isinstance(node, SelectItem)
    return node


def expr0(sql: str):
    return item0(sql).expr


def where0(sql: str):
    statement = ast(sql)
    assert statement.where is not None
    return statement.where


def sexpr(node: object) -> str:
    if isinstance(node, BinaryOp):
        return f"({node.op} {sexpr(node.left)} {sexpr(node.right)})"
    if isinstance(node, UnaryOp):
        return f"({node.op} {sexpr(node.operand)})"
    if isinstance(node, Column):
        return f"{node.table}.{node.name}" if node.table else node.name
    if isinstance(node, Literal):
        return repr(node.value)
    if isinstance(node, FunctionCall):
        return f"{node.name}({sexpr(node.arg)})"
    if isinstance(node, Star):
        return f"{node.table}.*" if node.table else "*"
    if isinstance(node, Between):
        prefix = "NOT " if node.negated else ""
        return f"({prefix}BETWEEN {sexpr(node.operand)} {sexpr(node.low)} {sexpr(node.high)})"
    if isinstance(node, Like):
        prefix = "NOT " if node.negated else ""
        return f"({prefix}LIKE {sexpr(node.operand)} {node.pattern!r})"
    if isinstance(node, InList):
        prefix = "NOT " if node.negated else ""
        values = " ".join(sexpr(value) for value in node.values)
        return f"({prefix}IN {sexpr(node.operand)} [{values}])"
    if isinstance(node, IsNull):
        prefix = "NOT " if node.negated else ""
        return f"({prefix}IS NULL {sexpr(node.operand)})"
    raise AssertionError(f"node tak dikenal: {node!r}")


def sql_error(sql: str) -> ParseError:
    with pytest.raises(ParseError) as excinfo:
        ast(sql)
    return excinfo.value


# --- presedensi -----------------------------------------------------------


def test_presedensi_kali_dahulu_ditambah() -> None:
    assert sexpr(expr0("SELECT a + b * c FROM t")) == "(+ a (* b c))"


def test_asosiatif_kiri_pengurangan() -> None:
    assert sexpr(expr0("SELECT a - b - c FROM t")) == "(- (- a b) c)"


def test_asosiatif_kiri_pembagian() -> None:
    assert sexpr(expr0("SELECT a / b / c FROM t")) == "(/ (/ a b) c)"


def test_aritmetika_campuran() -> None:
    assert sexpr(expr0("SELECT 1 + 2 * 3 - 4 / 2 FROM t")) == "(- (+ 1 (* 2 3)) (/ 4 2))"


def test_div_pada_level_sama_dengan_kali() -> None:
    assert sexpr(expr0("SELECT a DIV b % c FROM t")) == "(% (DIV a b) c)"


def test_unary_minus_mengikat_lebih_kuat() -> None:
    assert sexpr(expr0("SELECT -a * b FROM t")) == "(* (- a) b)"


def test_unary_minus_asosiatif_kanan() -> None:
    assert sexpr(expr0("SELECT - - a FROM t")) == "(- (- a))"


def test_kurung_mengubah_presedensi() -> None:
    assert sexpr(expr0("SELECT (a OR b) AND c FROM t")) == "(AND (OR a b) c)"


def test_not_mengikat_lebih_kuat_dari_dan() -> None:
    assert sexpr(expr0("SELECT NOT a AND b OR c FROM t")) == "(OR (AND (NOT a) b) c)"


def test_not_mengikat_predikat_penuh() -> None:
    assert sexpr(expr0("SELECT NOT a = b FROM t")) == "(NOT (= a b))"


def test_perbandingan_dahulu_dari_logika() -> None:
    assert sexpr(expr0("SELECT a = b AND c <> d FROM t")) == "(AND (= a b) (<> c d))"


def test_modulo_dahulu_dari_perbandingan() -> None:
    assert sexpr(expr0("SELECT a % b = c FROM t")) == "(= (% a b) c)"


def test_not_equals_dinormalisasi_menjadi_shift() -> None:
    assert sexpr(expr0("SELECT a != b FROM t")) == "(<> a b)"


# --- klausa inti ----------------------------------------------------------


def test_select_dengan_star() -> None:
    assert sexpr(ast("SELECT * FROM mahasiswa").items[0]) == "*"


def test_select_star_dengan_kualifikasi() -> None:
    assert sexpr(ast("SELECT m.* FROM mahasiswa m").items[0]) == "m.*"


def test_select_distinct() -> None:
    statement = ast("SELECT DISTINCT jurusan FROM mahasiswa")
    assert statement.distinct is True
    statement = ast("SELECT jurusan FROM mahasiswa")
    assert statement.distinct is False


def test_banyak_item_dengan_alias() -> None:
    statement = ast("SELECT nama, ipk AS nilai_ipk FROM mahasiswa")
    assert len(statement.items) == 2
    second = statement.items[1]
    assert isinstance(second, SelectItem)
    assert second.alias == "nilai_ipk"


def test_alias_dengan_as() -> None:
    item = item0("SELECT ipk AS i FROM mahasiswa")
    assert item.alias == "i"
    assert item.text == "ipk"


def test_alias_tanpa_as() -> None:
    item = item0("SELECT ipk i FROM mahasiswa")
    assert item.alias == "i"


def test_nama_berkualifikasi() -> None:
    assert sexpr(expr0("SELECT m.nim FROM mahasiswa m")) == "m.nim"


def test_nama_backtick() -> None:
    node = expr0("SELECT `Nama Kolom` FROM t")
    assert isinstance(node, Column)
    assert node.name == "nama kolom"


def test_count_bintang() -> None:
    node = expr0("SELECT COUNT(*) FROM t")
    assert isinstance(node, FunctionCall)
    assert node.name == "count"
    assert isinstance(node.arg, Star)
    assert node.arg.table is None


def test_sum_bintang_diterima_parser() -> None:
    # semantik yang menolak (SEM010), parser tetap menerima
    node = expr0("SELECT SUM(*) FROM t")
    assert isinstance(node, FunctionCall)
    assert node.name == "sum"


def test_literal_string_null_dan_angka() -> None:
    statement = ast("SELECT a, 1, 2.5, 'x', NULL FROM t")
    exprs = [item.expr for item in statement.items if isinstance(item, SelectItem)]
    assert sexpr(exprs[0]) == "a"
    assert [(e.kind, e.value) for e in exprs[1:] if isinstance(e, Literal)] == [
        ("INT", 1),
        ("DOUBLE", 2.5),
        ("TEXT", "x"),
        ("NULL", None),
    ]


def test_select_item_text_spasi_tak_beraturan() -> None:
    sql = "SELECT   a   +    b  FROM t"
    assert item0(sql).text == "a   +    b"


def test_select_item_text_kolom_polos() -> None:
    assert item0("SELECT   nama   FROM mahasiswa").text == "nama"


def test_select_item_text_mempertahankan_huruf_asli() -> None:
    assert item0("SELECT Nama AS nama2 FROM t").text == "Nama"


def test_titik_koma_opsional() -> None:
    plain = ast("SELECT a FROM t")
    semicolon = ast("SELECT a FROM t;")
    assert plain == semicolon


def test_from_dengan_alias() -> None:
    statement = ast("SELECT a FROM mahasiswa m")
    assert statement.from_.name == "mahasiswa"
    assert statement.from_.alias == "m"


def test_where_dengan_kurung() -> None:
    statement = ast("SELECT a FROM t WHERE (a > 1 AND b < 2) OR c = 3")
    assert statement.where is not None
    assert isinstance(statement.where, BinaryOp)
    assert statement.where.op == "OR"


def test_query_huruf_campuran() -> None:
    statement = ast("select Nama from Mahasiswa where ipk > 3")
    assert statement.from_.name == "mahasiswa"
    assert statement.items[0].text == "Nama"


def test_pretty_print_terbaca() -> None:
    text = pretty_print(ast("SELECT nama, COUNT(*) AS j FROM t WHERE a > 1"))
    assert "SelectStatement" in text
    assert "SelectItem" in text
    assert "FunctionCall" in text
    assert "BinaryOp" in text
    assert "Column" in text
    assert "\n" in text


# --- error ----------------------------------------------------------------


def test_error_select_kosong() -> None:
    error = sql_error("SELECT")
    assert error.code == SynCode.INCOMPLETE_EXPRESSION
    assert error.line == 1
    assert error.column == 7
    assert error.expected == ()


def test_error_select_kosong_dengan_klausa() -> None:
    error = sql_error("SELECT FROM t")
    assert error.code == SynCode.INCOMPLETE_EXPRESSION
    assert error.column == 8


def test_error_from_hilang() -> None:
    source = "SELECT a"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "FROM" in error.message
    assert error.expected == ("FROM",)
    assert error.column == len(source) + 1


def test_error_kurung_tak_seimbang() -> None:
    source = "SELECT (a + b FROM t"
    error = sql_error(source)
    assert error.code == SynCode.UNBALANCED_PAREN
    assert error.column == source.index("FROM") + 1
    assert "tidak ditutup" in error.message


def test_error_kurung_fungsi_tak_seimbang() -> None:
    source = "SELECT COUNT(a FROM t"
    error = sql_error(source)
    assert error.code == SynCode.UNBALANCED_PAREN
    assert error.column == source.index("FROM") + 1


def test_error_perbandingan_berantai() -> None:
    source = "SELECT a < b < c FROM t"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert error.column == source.rindex("<") + 1
    assert "FROM" in error.message


def test_error_teks_setelah_titik_koma() -> None:
    source = "SELECT a FROM t; x"
    error = sql_error(source)
    assert error.code == SynCode.TRAILING_INPUT
    assert error.column == source.index("x") + 1


def test_error_ekspresi_menggantung() -> None:
    source = "SELECT a + FROM t"
    error = sql_error(source)
    assert error.code == SynCode.INCOMPLETE_EXPRESSION
    assert error.column == source.index("FROM") + 1
    assert "FROM" in error.message


def test_error_alias_setelah_as_tidak_valid() -> None:
    source = "SELECT a AS FROM t"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "nama alias" in error.message
    assert error.column == source.index("FROM") + 1


def test_error_kualifikasi_berlebih() -> None:
    error = sql_error("SELECT a.b.c FROM t")
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "'.'" in error.message


def test_error_select_tanpa_daftar_kolom_eof() -> None:
    error = sql_error("SELECT , FROM t")
    assert error.code == SynCode.INCOMPLETE_EXPRESSION
    assert error.column == 8


# ======================================================================
# LANGKAH 3: perluasan parser ke seluruh grammar Level 2
# ======================================================================


# --- JOIN ----------------------------------------------------------------


def test_join_tanpa_kata_inner_adalah_inner() -> None:
    statement = ast("SELECT a FROM x JOIN y ON x.id = y.id")
    assert len(statement.joins) == 1
    assert statement.joins[0].kind == "INNER"


def test_inner_join_eksplisit() -> None:
    statement = ast("SELECT a FROM x INNER JOIN y ON x.id = y.id")
    assert statement.joins[0].kind == "INNER"


def test_left_outer_join() -> None:
    statement = ast("SELECT a FROM x LEFT OUTER JOIN y ON x.id = y.id")
    assert statement.joins[0].kind == "LEFT"


def test_banyak_join_berurutan() -> None:
    sql = (
        "SELECT m.nim FROM mahasiswa m "
        "JOIN nilai n ON m.nim = n.nim "
        "LEFT JOIN mata_kuliah k ON n.kode = k.kode"
    )
    statement = ast(sql)
    assert [join.kind for join in statement.joins] == ["INNER", "LEFT"]
    assert statement.joins[1].table.name == "mata_kuliah"
    assert statement.joins[1].table.alias == "k"


def test_join_on_dengan_banyak_kondisi() -> None:
    statement = ast("SELECT a FROM x JOIN y ON x.a = y.a AND x.b = y.b")
    on = statement.joins[0].on
    assert isinstance(on, BinaryOp)
    assert on.op == "AND"


def test_join_pertama_setelah_tabel_pertama() -> None:
    statement = ast("SELECT a FROM x JOIN y ON x.i = y.i JOIN z ON y.i = z.i")
    assert [join.table.name for join in statement.joins] == ["y", "z"]


# --- GROUP BY / HAVING / ORDER BY / LIMIT --------------------------------


def test_group_by_tunggal() -> None:
    statement = ast("SELECT jurusan, COUNT(*) FROM mahasiswa GROUP BY jurusan")
    assert len(statement.group_by) == 1
    assert sexpr(statement.group_by[0]) == "jurusan"


def test_group_by_banyak() -> None:
    statement = ast("SELECT a, b, COUNT(*) FROM t GROUP BY a, b")
    assert [sexpr(expr) for expr in statement.group_by] == ["a", "b"]


def test_having() -> None:
    statement = ast("SELECT a, COUNT(*) FROM t GROUP BY a HAVING COUNT(*) > 2")
    assert statement.having is not None
    assert sexpr(statement.having) == "(> count(*) 2)"


def test_group_by_having_order_limit() -> None:
    sql = (
        "SELECT m.jurusan, COUNT(*) AS jumlah FROM mahasiswa m "
        "JOIN nilai n ON m.nim = n.nim GROUP BY m.jurusan "
        "HAVING COUNT(*) > 2 ORDER BY jumlah DESC LIMIT 10 OFFSET 5"
    )
    statement = ast(sql)
    assert len(statement.joins) == 1
    assert len(statement.group_by) == 1
    assert statement.having is not None
    assert len(statement.order_by) == 1
    assert statement.order_by[0].descending is True
    assert statement.limit == 10
    assert statement.offset == 5


def test_order_by_default_asc() -> None:
    statement = ast("SELECT a FROM t ORDER BY a")
    assert statement.order_by[0].descending is False


def test_order_by_desc() -> None:
    statement = ast("SELECT a FROM t ORDER BY a DESC")
    assert statement.order_by[0].descending is True


def test_order_by_asc_eksplisit() -> None:
    statement = ast("SELECT a FROM t ORDER BY a ASC")
    assert statement.order_by[0].descending is False


def test_order_by_multi_kunci() -> None:
    statement = ast("SELECT a FROM t ORDER BY a DESC, b, c ASC")
    assert [item.descending for item in statement.order_by] == [True, False, False]


def test_order_by_ekspresi() -> None:
    statement = ast("SELECT a FROM t ORDER BY ipk + 1 DESC")
    assert sexpr(statement.order_by[0].expr) == "(+ ipk 1)"


def test_limit_satu_angka() -> None:
    statement = ast("SELECT a FROM t LIMIT 5")
    assert statement.limit == 5
    assert statement.offset == 0


def test_limit_koma() -> None:
    statement = ast("SELECT a FROM t LIMIT 5, 10")
    assert statement.limit == 10
    assert statement.offset == 5


def test_limit_offset() -> None:
    statement = ast("SELECT a FROM t LIMIT 10 OFFSET 5")
    assert statement.limit == 10
    assert statement.offset == 5


def test_limit_nol() -> None:
    statement = ast("SELECT a FROM t LIMIT 0")
    assert statement.limit == 0


# --- LIKE / IN / BETWEEN / IS NULL ---------------------------------------


def test_like() -> None:
    assert sexpr(where0("SELECT a FROM t WHERE nama LIKE 'A%'")) == "(LIKE nama 'A%')"


def test_not_like() -> None:
    assert sexpr(where0("SELECT a FROM t WHERE nama NOT LIKE 'A%'")) == (
        "(NOT LIKE nama 'A%')"
    )


def test_in() -> None:
    sql = "SELECT a FROM t WHERE jurusan IN ('TI', 'SI')"
    assert sexpr(where0(sql)) == "(IN jurusan ['TI' 'SI'])"


def test_not_in() -> None:
    sql = "SELECT a FROM t WHERE jurusan NOT IN ('TI')"
    assert sexpr(where0(sql)) == "(NOT IN jurusan ['TI'])"


def test_between() -> None:
    sql = "SELECT a FROM t WHERE ipk BETWEEN 3.0 AND 4.0"
    assert sexpr(where0(sql)) == "(BETWEEN ipk 3.0 4.0)"


def test_not_between() -> None:
    sql = "SELECT a FROM t WHERE ipk NOT BETWEEN 3.0 AND 4.0"
    assert sexpr(where0(sql)) == "(NOT BETWEEN ipk 3.0 4.0)"


def test_is_null() -> None:
    assert sexpr(where0("SELECT a FROM t WHERE ipk IS NULL")) == "(IS NULL ipk)"


def test_is_not_null() -> None:
    assert sexpr(where0("SELECT a FROM t WHERE ipk IS NOT NULL")) == "(NOT IS NULL ipk)"


def test_and_di_dalam_between_bukan_operator_logika() -> None:
    sql = "SELECT a FROM t WHERE ipk BETWEEN 1 AND 5 AND jurusan = 'TI'"
    assert sexpr(where0(sql)) == "(AND (BETWEEN ipk 1 5) (= jurusan 'TI'))"


def test_not_in_mempertahankan_not_luar() -> None:
    sql = "SELECT a FROM t WHERE NOT jurusan IN ('TI')"
    assert sexpr(where0(sql)) == "(NOT (IN jurusan ['TI']))"


def test_kombinasi_predikat_lengkap() -> None:
    sql = (
        "SELECT nim, nama FROM mahasiswa "
        "WHERE nama LIKE 'A%' AND jurusan IN ('TI', 'SI') "
        "AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL"
    )
    assert sexpr(where0(sql)).startswith("(AND (AND (AND (LIKE nama 'A%')")


def test_query_lengkap_level_2() -> None:
    sql = (
        "SELECT DISTINCT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata "
        "FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim "
        "WHERE n.skor IS NOT NULL "
        "GROUP BY m.jurusan HAVING COUNT(*) > 2 "
        "ORDER BY rata DESC LIMIT 5, 10"
    )
    statement = ast(sql)
    assert statement.distinct is True
    assert len(statement.joins) == 1
    assert statement.where is not None
    assert len(statement.group_by) == 1
    assert statement.having is not None
    assert statement.order_by[0].descending is True
    assert statement.limit == 10
    assert statement.offset == 5


def test_select_star_dari_join_dengan_alias() -> None:
    statement = ast("SELECT m.*, n.skor FROM mahasiswa m JOIN nilai n ON m.nim = n.nim")
    assert sexpr(statement.items[0]) == "m.*"
    second = statement.items[1]
    assert isinstance(second, SelectItem)
    assert sexpr(second.expr) == "n.skor"


def test_pretty_print_memuat_node_level_2() -> None:
    sql = (
        "SELECT a FROM x LEFT JOIN y ON x.i = y.i WHERE a BETWEEN 1 AND 2 "
        "GROUP BY a HAVING COUNT(*) > 1 ORDER BY a DESC LIMIT 3"
    )
    text = pretty_print(ast(sql))
    for name in ("Join", "Between", "OrderItem"):
        assert name in text
    assert "limit: 3" in text


# --- penolakan fitur di luar scope (SPEC.md §2) --------------------------


def test_error_right_join() -> None:
    source = "SELECT a FROM x RIGHT JOIN y ON x.i = y.i"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "RIGHT JOIN" in error.message
    assert error.column == source.index("RIGHT") + 1


def test_error_full_join() -> None:
    source = "SELECT a FROM x FULL JOIN y ON x.i = y.i"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "FULL JOIN" in error.message


def test_error_full_outer_join() -> None:
    source = "SELECT a FROM x FULL OUTER JOIN y ON x.i = y.i"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "FULL OUTER JOIN" in error.message


def test_error_cross_join() -> None:
    source = "SELECT a FROM x CROSS JOIN y ON x.i = y.i"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "CROSS JOIN" in error.message


def test_error_join_gaya_koma() -> None:
    source = "SELECT a FROM x, y"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "koma" in error.message
    assert error.column == source.index(",") + 1


def test_error_subquery_pada_ekspresi() -> None:
    source = "SELECT a FROM t WHERE a = (SELECT MAX(b) FROM u)"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "Subquery" in error.message
    assert error.column == source.index("SELECT", 3) + 1


def test_error_subquery_pada_from() -> None:
    source = "SELECT a FROM (SELECT b FROM u) x"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "Subquery" in error.message
    assert error.column == source.index("(") + 1


def test_error_subquery_pada_in() -> None:
    source = "SELECT a FROM t WHERE a IN (SELECT b FROM u)"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "IN (SELECT" in error.message


def test_error_union() -> None:
    source = "SELECT a FROM t UNION SELECT b FROM u"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "UNION" in error.message
    assert error.column == source.index("UNION") + 1


def test_error_cte_with() -> None:
    source = "WITH cte AS (SELECT 1) SELECT a FROM t"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "CTE" in error.message
    assert error.column == 1


def test_error_nama_tabel_berkualifikasi_database() -> None:
    source = "SELECT a FROM db.tabel"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "db.tabel" in error.message
    assert error.column == source.index(".") + 1


def test_error_select_tanpa_from() -> None:
    source = "SELECT 1 + 2"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "FROM" in error.message
    assert "tanpa FROM" in error.message
    assert error.column == len(source) + 1


def test_error_count_distinct() -> None:
    source = "SELECT COUNT(DISTINCT nim) FROM mahasiswa"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "DISTINCT" in error.message
    assert error.column == source.index("DISTINCT") + 1


def test_error_ddl_ditolak() -> None:
    source = "CREATE TABLE t (a INT)"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "SELECT" in error.message
    assert error.column == 1


def test_error_dml_ditolak() -> None:
    source = "DELETE FROM mahasiswa"
    error = sql_error(source)
    assert error.code == SynCode.UNSUPPORTED_FEATURE
    assert "DELETE" in error.message


# --- error grammar Level 2 ----------------------------------------------


def test_error_group_by_tanpa_by() -> None:
    source = "SELECT a FROM t GROUP jurusan"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert error.column == source.index("jurusan") + 1


def test_error_order_by_tanpa_by() -> None:
    source = "SELECT a FROM t ORDER jurusan"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "BY" in error.message


def test_error_limit_bukan_angka() -> None:
    source = "SELECT a FROM t LIMIT nama"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert error.column == source.index("nama") + 1


def test_error_like_bukan_literal_string() -> None:
    source = "SELECT a FROM t WHERE nama LIKE pola"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "literal string" in error.message
    assert error.column == source.index("pola") + 1


def test_error_join_tanpa_on() -> None:
    source = "SELECT a FROM x JOIN y"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "ON" in error.message


def test_error_between_tanpa_and() -> None:
    source = "SELECT a FROM t WHERE ipk BETWEEN 1 5"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "AND" in error.message


def test_error_is_tanpa_null() -> None:
    source = "SELECT a FROM t WHERE ipk IS NOT"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "NULL" in error.message


def test_error_in_tanpa_buka_kurung() -> None:
    source = "SELECT a FROM t WHERE jurusan IN 'TI'"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "'('" in error.message


def test_error_klausa_tanpa_urutan_benar() -> None:
    source = "SELECT a FROM t ORDER BY a WHERE b = 1"
    error = sql_error(source)
    assert error.code == SynCode.UNEXPECTED_TOKEN
    assert "'WHERE'" in error.message
