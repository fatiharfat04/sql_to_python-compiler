"""Tes lexer sesuai ``SPEC.md`` §4 dan §9 (Langkah 1)."""

from __future__ import annotations

import random

import pytest

from sqlcompiler.errors import LEX_CODE_MEANING, LexCode, LexError, render_error
from sqlcompiler.lexer import KEYWORDS, Token, TokenType, tokenize


def types(source: str) -> list[TokenType]:
    return [token.type for token in tokenize(source)]


def lexemes(source: str) -> list[str]:
    return [token.lexeme for token in tokenize(source) if token.type is not TokenType.EOF]


def first(source: str) -> Token:
    return tokenize(source)[0]


def error_of(source: str) -> LexError:
    with pytest.raises(LexError) as excinfo:
        tokenize(source)
    return excinfo.value


# --- keyword dan identifier ---------------------------------------------


def test_keyword_huruf_campuran() -> None:
    token = first("SeLeCt")
    assert token.type is TokenType.KEYWORD
    assert token.value == "SELECT"
    assert token.lexeme == "SeLeCt"


def test_keyword_case_insensitive_untuk_semua() -> None:
    for word in KEYWORDS:
        assert first(word.lower()).type is TokenType.KEYWORD
        assert first(word.upper()).type is TokenType.KEYWORD


def test_ident_dinormalisasi_huruf_kecil() -> None:
    token = first("NamaMahasiswa")
    assert token.type is TokenType.IDENT
    assert token.value == "namamahasiswa"
    assert token.lexeme == "NamaMahasiswa"


def test_ident_boleh_mengandung_underscore_dan_dollar() -> None:
    assert lexemes("_a1$2") == ["_a1$2"]
    assert first("_a1$2").value == "_a1$2"


def test_ident_tidak_boleh_diawali_digit() -> None:
    assert types("1abc") == [TokenType.INTEGER, TokenType.IDENT, TokenType.EOF]


def test_qident_backtick() -> None:
    token = first("`Nama Kolom`")
    assert token.type is TokenType.QIDENT
    assert token.value == "nama kolom"
    assert token.lexeme == "`Nama Kolom`"


def test_qident_backtick_ganda_menjadi_satu_backtick() -> None:
    token = first("`a``b`")
    assert token.type is TokenType.QIDENT
    assert token.value == "a`b"


def test_qident_tidak_menjadi_keyword() -> None:
    token = first("`select`")
    assert token.type is TokenType.QIDENT
    assert token.value == "select"


def test_keyword_harus_diberi_backtick_bila_menjadi_nama() -> None:
    assert types("order") == [TokenType.KEYWORD, TokenType.EOF]
    assert types("`order`") == [TokenType.QIDENT, TokenType.EOF]


# --- literal string ------------------------------------------------------


def test_string_kutip_tunggal() -> None:
    token = first("'TI'")
    assert token.type is TokenType.STRING
    assert token.value == "TI"
    assert token.lexeme == "'TI'"


def test_string_kutip_ganda_adalah_string_bukan_identifier() -> None:
    token = first('"TI"')
    assert token.type is TokenType.STRING
    assert token.value == "TI"


def test_string_kutip_digandakan() -> None:
    assert first("'it''s'").value == "it's"
    assert first("'a''''b'").value == "a''b"
    assert first("''").value == ""


def test_string_escape_backslash() -> None:
    assert first(r"'a\\b'").value == "a\\b"


def test_string_escape_kutip_tunggal() -> None:
    assert first(r"'a\'b'").value == "a'b"


def test_string_escape_kutip_ganda() -> None:
    assert first(r"'a\"b'").value == 'a"b'


def test_string_escape_n_t_0() -> None:
    assert first(r"'a\nb'").value == "a\nb"
    assert first(r"'a\tb'").value == "a\tb"
    assert first(r"'a\0b'").value == "a\0b"


def test_string_escape_percent_underscore_dipertahankan() -> None:
    # MySQL mempertahankan backslash pada \% dan \_ (diverifikasi ke server oracle:
    # LENGTH('a\%b') = 4 dan 'a%b' = 'a\%b' bernilai FALSE).
    assert first(r"'a\%b'").value == "a\\%b"
    assert len(str(first(r"'a\%b'").value)) == 4
    assert first(r"'a\_%b'").value == "a\\_%b"


def test_string_boleh_multibaris() -> None:
    tokens = tokenize("'a\nb'")
    assert tokens[0].value == "a\nb"
    assert tokens[1].type is TokenType.EOF
    assert tokens[1].line == 2


# --- angka ----------------------------------------------------------------


def test_integer() -> None:
    token = first("123")
    assert token.type is TokenType.INTEGER
    assert token.value == 123


def test_float() -> None:
    token = first("3.50")
    assert token.type is TokenType.FLOAT
    assert token.value == 3.5


def test_angka_diikuti_titik_bukan_float() -> None:
    assert types("1.5.5") == [
        TokenType.FLOAT,
        TokenType.PUNCT,
        TokenType.INTEGER,
        TokenType.EOF,
    ]


def test_angka_tanpa_tanda() -> None:
    assert types("-7") == [TokenType.OPERATOR, TokenType.INTEGER, TokenType.EOF]


# --- operator dan tanda baca --------------------------------------------


def test_not_equals_dinormalisasi_menjadi_shift_kiri_kanan() -> None:
    token = first("!=")
    assert token.type is TokenType.OPERATOR
    assert token.lexeme == "!="
    assert token.value == "<>"


def test_seluruh_operator() -> None:
    assert lexemes("= <> < > <= >= + - * / %") == [
        "=",
        "<>",
        "<",
        ">",
        "<=",
        ">=",
        "+",
        "-",
        "*",
        "/",
        "%",
    ]


def test_seluruh_punct() -> None:
    tokens = tokenize("( ) , . ;")
    assert [token.type for token in tokens[:-1]] == [TokenType.PUNCT] * 5
    assert tokens[-1].type is TokenType.EOF


def test_star_direpresentasikan_sebagai_operator() -> None:
    assert types("SELECT *") == [
        TokenType.KEYWORD,
        TokenType.OPERATOR,
        TokenType.EOF,
    ]


# --- komentar -------------------------------------------------------------


def test_komentar_dash_spasi() -> None:
    assert lexemes("SELECT -- komentar\nnama") == ["SELECT", "nama"]


def test_komentar_dash_tab() -> None:
    # tab setelah '--' membuat sisa baris menjadi komentar
    assert lexemes("SELECT --\tkom") == ["SELECT"]
    assert lexemes("SELECT --\tkom\nipk") == ["SELECT", "ipk"]


def test_komentar_dash_akhir_baris() -> None:
    assert lexemes("nama --") == ["nama"]
    assert lexemes("nama --\n") == ["nama"]


def test_dash_dash_x_bukan_komentar() -> None:
    assert lexemes("--x") == ["-", "-", "x"]
    assert types("--x") == [
        TokenType.OPERATOR,
        TokenType.OPERATOR,
        TokenType.IDENT,
        TokenType.EOF,
    ]


def test_komentar_dash_dash_spasi_vs_dash_dash_x() -> None:
    assert lexemes("-- x") == []
    assert lexemes("--x") == ["-", "-", "x"]


def test_komentar_hash() -> None:
    assert lexemes("nama # sampai akhir baris\nipk") == ["nama", "ipk"]


def test_komentar_hash_di_akhir_input() -> None:
    assert lexemes("nama # tanpa newline") == ["nama"]


def test_komentar_blok() -> None:
    assert lexemes("a /* multi\nbaris */ b") == ["a", "b"]


def test_komentar_blok_kosong() -> None:
    assert lexemes("/**/a") == ["a"]


def test_komentar_blok_bertanda_versi_mysql_diperlakukan_sebagai_komentar() -> None:
    assert lexemes("a /*!40101 SET @x = 1 */ b") == ["a", "b"]


# --- posisi ---------------------------------------------------------------


def test_posisi_line_column_offset_multibaris() -> None:
    tokens = tokenize("SELECT a\nFROM b")
    positions = [(t.line, t.column, t.offset) for t in tokens]
    assert positions == [
        (1, 1, 0),
        (1, 8, 7),
        (2, 1, 9),
        (2, 6, 14),
        (2, 7, 15),
    ]


def test_posisi_dalam_string_multibaris() -> None:
    tokens = tokenize("SELECT 'x\ny' z")
    assert tokens[1].line == 1
    assert tokens[2].line == 2
    assert tokens[2].lexeme == "z"


def test_eof_adalah_token_terakhir() -> None:
    tokens = tokenize("")
    assert len(tokens) == 1
    assert tokens[0].type is TokenType.EOF
    assert tokenize("SELECT")[-1].type is TokenType.EOF


# --- error ----------------------------------------------------------------


def test_error_string_tidak_ditutup() -> None:
    error = error_of("SELECT 'abc")
    assert error.code == LexCode.UNTERMINATED_STRING
    assert error.line == 1
    assert error.column == 8


def test_error_backtick_tidak_ditutup() -> None:
    error = error_of("SELECT `abc")
    assert error.code == LexCode.UNTERMINATED_QUOTED_IDENTIFIER
    assert error.column == 8


def test_error_komentar_blok_tidak_ditutup() -> None:
    error = error_of("SELECT a /* belum selesai")
    assert error.code == LexCode.UNTERMINATED_COMMENT
    assert error.column == 10


def test_error_escape_tidak_dikenal() -> None:
    error = error_of(r"SELECT 'a\qb'")
    assert error.code == LexCode.UNKNOWN_ESCAPE
    assert error.line == 1
    assert error.column == 10
    assert error.length == 2


def test_error_karakter_illegal() -> None:
    error = error_of("SELECT @x")
    assert error.code == LexCode.UNKNOWN_CHARACTER
    assert error.column == 8
    assert "tidak dikenal" in error.message


def test_error_operator_ampersand_dengan_pesan_membantu() -> None:
    error = error_of("WHERE a && b")
    assert error.code == LexCode.UNSUPPORTED_OPERATOR
    assert error.column == 9
    assert error.length == 2
    assert "AND" in error.message


def test_error_operator_pipe_dengan_pesan_membantu() -> None:
    error = error_of("WHERE a || b")
    assert error.code == LexCode.UNSUPPORTED_OPERATOR
    assert "OR" in error.message


def test_error_operator_bang_sendiri() -> None:
    error = error_of("WHERE !a")
    assert error.code == LexCode.UNSUPPORTED_OPERATOR
    assert "NOT" in error.message


def test_bang_sama_dengan_sama_dengan_bukan_error() -> None:
    assert first("!=").value == "<>"


def test_error_pada_baris_kedua() -> None:
    error = error_of("SELECT a\nFROM @")
    assert error.line == 2
    assert error.column == 6


# --- render error ---------------------------------------------------------


def test_render_error_format_sesuai_spec() -> None:
    source = "SELECT @nama FROM mahasiswa"
    error = error_of(source)
    rendered = render_error(error, source).splitlines()
    assert rendered[0] == "LEX001: Karakter tidak dikenal: '@'."
    assert rendered[1] == "  1 | SELECT @nama FROM mahasiswa"
    assert rendered[2].index("^") == rendered[1].index("@")
    assert rendered[2].count("^") == 1


def test_render_error_penunjuk_sejajar_dengan_token() -> None:
    source = "SELECT nama, ipkk FROM mahasiswa"
    from sqlcompiler.errors import SemanticError

    error = SemanticError("SEM002", "Kolom 'ipkk' tidak ditemukan di 'mahasiswa'.", 1, 14, 4)
    rendered = render_error(error, source).splitlines()
    assert rendered[0] == "SEM002: Kolom 'ipkk' tidak ditemukan di 'mahasiswa'."
    assert rendered[1] == "  1 | SELECT nama, ipkk FROM mahasiswa"
    assert rendered[2].index("^") == rendered[1].index("ipkk")
    assert rendered[2].count("^") == 4


# --- dokumentasi kode dan fuzz -------------------------------------------


def test_seluruh_kode_lex_terdokumentasi() -> None:
    for code in LexCode:
        assert code in LEX_CODE_MEANING
        assert LEX_CODE_MEANING[code]


def test_fuzz_500_string_ascii_acak() -> None:
    rng = random.Random(20261004)
    alphabet = (
        "abcXYZ019 _$ \t\n'\"`\\;,()/*#-+=<>%!|&"
        + "".join(chr(i) for i in range(32, 127))
    )
    for _ in range(500):
        length = rng.randint(0, 40)
        text = "".join(rng.choice(alphabet) for _ in range(length))
        try:
            tokens = tokenize(text)
        except LexError:
            continue
        assert tokens
        assert tokens[-1].type is TokenType.EOF
        assert all(isinstance(token, Token) for token in tokens)
