"""Hierarki error kompilasi dan render error bergaya ``SPEC.md`` §9.

Setiap error membawa ``code``, ``message``, ``line``, ``column``, serta ``length``
(lebar teks yang ditandai) dan ``offset`` (posisi karakter pada sumber) agar
``render_error`` dapat menempatkan penunjuk ``^^^^`` dengan tepat.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

__all__ = [
    "LEX_CODE_MEANING",
    "SEM_CODE_MEANING",
    "SYN_CODE_MEANING",
    "CompileError",
    "LexCode",
    "LexError",
    "ParseError",
    "SemCode",
    "SemanticError",
    "SynCode",
    "render_error",
    "render_errors",
]


class LexCode(StrEnum):
    """Kode error leksikal (``SPEC.md`` §4, §9)."""

    UNKNOWN_CHARACTER = "LEX001"
    UNTERMINATED_STRING = "LEX002"
    UNTERMINATED_QUOTED_IDENTIFIER = "LEX003"
    UNTERMINATED_COMMENT = "LEX004"
    UNKNOWN_ESCAPE = "LEX005"
    UNSUPPORTED_OPERATOR = "LEX006"


#: Tabel arti kode ``LEX***``.
LEX_CODE_MEANING: dict[str, str] = {
    LexCode.UNKNOWN_CHARACTER: "Karakter tak dikenal di luar string, identitas, dan komentar.",
    LexCode.UNTERMINATED_STRING: "Literal string tidak ditutup dengan kutip yang sama.",
    LexCode.UNTERMINATED_QUOTED_IDENTIFIER: "Identitas berkutip backtick tidak ditutup.",
    LexCode.UNTERMINATED_COMMENT: "Komentar blok ``/* ... */`` tidak ditutup.",
    LexCode.UNKNOWN_ESCAPE: "Escape backslash yang tidak didukung di dalam literal string.",
    LexCode.UNSUPPORTED_OPERATOR: (
        "Operator yang tidak didukung kompiler, misalnya '&&', '||', dan '!' pada MySQL."
    ),
}


class CompileError(Exception):
    """Basis seluruh error kompilasi."""

    code: str
    message: str
    line: int
    column: int
    length: int
    offset: int

    def __init__(
        self,
        code: str,
        message: str,
        line: int,
        column: int,
        length: int = 1,
        offset: int = 0,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = str(code)
        self.message = message
        self.line = line
        self.column = column
        self.length = max(1, length)
        self.offset = max(0, offset)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class LexError(CompileError):
    """Error yang ditemukan oleh lexer."""


class ParseError(CompileError):
    """Error yang ditemukan oleh parser; memuat daftar token yang diharapkan."""

    expected: tuple[str, ...]

    def __init__(
        self,
        code: str,
        message: str,
        line: int,
        column: int,
        expected: str | Sequence[str] = (),
        length: int = 1,
        offset: int = 0,
    ) -> None:
        super().__init__(code, message, line, column, length, offset)
        if isinstance(expected, str):
            self.expected = (expected,)
        else:
            self.expected = tuple(expected)


class SynCode(StrEnum):
    """Kode error sintaks/parser (``SPEC.md`` §9)."""

    UNEXPECTED_TOKEN = "SYN001"
    INCOMPLETE_EXPRESSION = "SYN002"
    UNBALANCED_PAREN = "SYN003"
    TRAILING_INPUT = "SYN004"
    UNSUPPORTED_FEATURE = "SYN005"


#: Tabel arti kode ``SYN***``.
SYN_CODE_MEANING: dict[str, str] = {
    SynCode.UNEXPECTED_TOKEN: "Token yang ditemukan tidak sesuai grammar pada posisi itu.",
    SynCode.INCOMPLETE_EXPRESSION: "Ekspresi berhenti sebelum lengkap (ekspresi menggantung).",
    SynCode.UNBALANCED_PAREN: "Tanda kurung buka tidak ditutup oleh tanda kurung tutup.",
    SynCode.TRAILING_INPUT: "Masukan masih berlanjut setelah satu pernyataan SELECT.",
    SynCode.UNSUPPORTED_FEATURE: "Fitur di luar ruang lingkup SPEC.md §2 ditolak dengan jelas.",
}


class SemanticError(CompileError):
    """Error yang ditemukan oleh analisis semantik."""


class SemCode(StrEnum):
    """Kode error analisis semantik (``SPEC.md`` §8)."""

    TABLE_NOT_FOUND = "SEM001"
    COLUMN_NOT_FOUND = "SEM002"
    AMBIGUOUS_COLUMN = "SEM003"
    DUPLICATE_ALIAS = "SEM004"
    TYPE_MISMATCH = "SEM005"
    AGGREGATE_NOT_ALLOWED = "SEM006"
    GROUP_BY_REQUIRED = "SEM007"
    HAVING_WITHOUT_GROUP_BY = "SEM008"
    UNKNOWN_FUNCTION = "SEM009"
    INVALID_FUNCTION_ARGUMENT = "SEM010"
    NON_BOOLEAN_CONDITION = "SEM011"
    STAR_WITH_GROUP_BY = "SEM012"
    NON_EQUI_JOIN = "SEM013"
    ORDINAL_NOT_SUPPORTED = "SEM014"
    BOOLEAN_IN_SELECT = "SEM015"
    UNSUPPORTED_COLUMN_TYPE = "SEM016"


#: Tabel arti kode ``SEM***`` (``SPEC.md`` §8).
SEM_CODE_MEANING: dict[str, str] = {
    SemCode.TABLE_NOT_FOUND: "Tabel tidak ada di skema.",
    SemCode.COLUMN_NOT_FOUND: "Kolom tidak ada pada tabel yang dirujuk.",
    SemCode.AMBIGUOUS_COLUMN: "Kolom ditemukan di lebih dari satu tabel atau alias ganda.",
    SemCode.DUPLICATE_ALIAS: "Alias atau nama tabel dipakai dua kali pada FROM/JOIN.",
    SemCode.TYPE_MISMATCH: "Tipe operand tidak cocok pada operator atau perbandingan.",
    SemCode.AGGREGATE_NOT_ALLOWED: "Fungsi agregat dipakai di WHERE, ON, atau GROUP BY.",
    SemCode.GROUP_BY_REQUIRED: (
        "Kolom non-agregat tidak ada di GROUP BY (ONLY_FULL_GROUP_BY)."
    ),
    SemCode.HAVING_WITHOUT_GROUP_BY: "HAVING dipakai tanpa GROUP BY.",
    SemCode.UNKNOWN_FUNCTION: "Fungsi tidak dikenal oleh kompiler.",
    SemCode.INVALID_FUNCTION_ARGUMENT: "Argumen fungsi tidak valid, misal SUM(*) atau AVG(teks).",
    SemCode.NON_BOOLEAN_CONDITION: "Kondisi WHERE/HAVING/ON bukan ekspresi boolean.",
    SemCode.STAR_WITH_GROUP_BY: "* atau alias.* digabung GROUP BY atau agregat.",
    SemCode.NON_EQUI_JOIN: "ON bukan konjungsi equi-join antara sisi kiri dan kanan.",
    SemCode.ORDINAL_NOT_SUPPORTED: "Nomor urut kolom pada ORDER BY/GROUP BY tidak didukung.",
    SemCode.BOOLEAN_IN_SELECT: "Ekspresi boolean berada pada daftar SELECT.",
    SemCode.UNSUPPORTED_COLUMN_TYPE: "Kolom bertipe yang belum didukung (DATE, JSON, ...).",
}


def render_error(error: CompileError, source: str) -> str:
    """Merender ``error`` dengan baris sumber dan penunjuk ``^^^^`` seperti ``SPEC.md`` §9."""
    head = f"{error.code}: {error.message}"
    lines = source.splitlines()
    if not (1 <= error.line <= len(lines)):
        return head

    text = lines[error.line - 1]
    gutter = f"{error.line:>3} | "
    start = min(max(error.column - 1, 0), len(text))
    end = max(start + 1, min(start + error.length, len(text)))
    caret_line = " " * (len(gutter) + start) + "^" * (end - start)
    return "\n".join([head, gutter + text, caret_line])


def render_errors(errors: Sequence[CompileError], source: str) -> str:
    """Merender beberapa error, dipisahkan baris kosong."""
    return "\n\n".join(render_error(error, source) for error in errors)
