"""Lexer manual (pemindaian karakter) untuk dialek MySQL 8.4 sesuai ``SPEC.md`` §4.

Aturan yang diterapkan:
* keyword dan identifier *case-insensitive*; ``IDENT``/``QIDENT`` dinormalisasi ke huruf kecil;
* backtick ganda di dalam ``QIDENT`` menjadi satu backtick;
* string memakai kutip tunggal atau ganda, kutip yang digandakan menjadi satu kutip;
* escape yang didukung: ``\\ \\ ' \\ " \\n \\t \\0`` serta ``\\%`` dan ``\\_``
  (MySQL mempertahankan backslash pada keduanya, diverifikasi terhadap server oracle);
* ``!=`` dinormalisasi menjadi ``<>``;
* komentar ``-- `` (wajib diikuti spasi/tab/akhir baris), ``# ... EOL``, dan ``/* ... */``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .errors import LexCode, LexError

__all__ = ["KEYWORDS", "Token", "TokenType", "format_tokens", "tokenize"]


class TokenType(StrEnum):
    """Kelas token hasil ``SPEC.md`` §4."""

    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    QIDENT = "QIDENT"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    STRING = "STRING"
    OPERATOR = "OPERATOR"
    PUNCT = "PUNCT"
    EOF = "EOF"


KEYWORDS: frozenset[str] = frozenset(
    {
        "SELECT",
        "DISTINCT",
        "FROM",
        "WHERE",
        "GROUP",
        "BY",
        "HAVING",
        "ORDER",
        "LIMIT",
        "OFFSET",
        "ASC",
        "DESC",
        "AS",
        "JOIN",
        "INNER",
        "LEFT",
        "OUTER",
        "ON",
        "AND",
        "OR",
        "NOT",
        "LIKE",
        "IN",
        "BETWEEN",
        "IS",
        "NULL",
        "DIV",
    }
)


@dataclass(frozen=True)
class Token:
    """Satu token; ``lexeme`` selalu potongan sumber persis, ``value`` sudah dinormalisasi."""

    type: TokenType
    lexeme: str
    value: str | int | float | None
    line: int
    column: int
    offset: int

    def is_keyword(self, *names: str) -> bool:
        """True bila token adalah keyword dengan salah satu ``names`` (huruf besar)."""
        return (
            self.type is TokenType.KEYWORD
            and isinstance(self.value, str)
            and self.value in names
        )

    def is_punct(self, *chars: str) -> bool:
        """True bila token adalah PUNCT atau OPERATOR dengan salah satu ``chars``."""
        return self.type in (TokenType.PUNCT, TokenType.OPERATOR) and self.lexeme in chars

    def name(self) -> str:
        """Nama ternormalisasi untuk ``IDENT``/``QIDENT``."""
        return str(self.value)


_TWO_CHAR_OPERATORS = frozenset({"<=", ">=", "<>", "!="})
_ONE_CHAR_OPERATORS = frozenset({"=", "<", ">", "+", "-", "*", "/", "%"})
_PUNCTUATION = frozenset({"(", ")", ",", ".", ";"})
_SIMPLE_ESCAPES = {
    "\\": "\\",
    "'": "'",
    '"': '"',
    "n": "\n",
    "t": "\t",
    "0": "\0",
}
# MySQL mempertahankan backslash pada \% dan \_; keduanya dipakai sebagai escape LIKE.
_LIKE_ESCAPES = frozenset({"%", "_"})
_DIGITS = frozenset("0123456789")


def _is_ident_start(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalpha())


def _is_ident_part(ch: str) -> bool:
    return ch in ("_", "$") or (ch.isascii() and ch.isalnum())


def tokenize(source: str) -> list[Token]:
    """Mengubah ``source`` menjadi daftar token yang diakhiri ``EOF``.

    Raises:
        LexError: bila ada karakter, string, backtick, escape, atau komentar blok yang tidak sah.
    """
    return _Lexer(source).run()


def format_tokens(tokens: Sequence[Token]) -> str:
    """Merekam daftar token sebagai baris ``baris kolom tipe lexeme``.

    Dipakai perintah ``explain`` (``SPEC.md`` §15) dan tab Token pada UI.
    """
    lines = [f"{'baris':>4} {'kolom':<4} {'tipe':<12} lexeme"]
    for token in tokens:
        lexeme = (
            token.lexeme.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        lines.append(f"{token.line:>4} {token.column:<4} {str(token.type):<12} {lexeme}")
    return "\n".join(lines)


class _Lexer:
    __slots__ = ("_source", "_pos", "_line", "_column")

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0
        self._line = 1
        self._column = 1

    # --- primitif posisi -------------------------------------------------

    def _advance(self) -> None:
        ch = self._source[self._pos]
        self._pos += 1
        if ch == "\n":
            self._line += 1
            self._column = 1
        else:
            self._column += 1

    def _peek(self, distance: int = 0) -> str:
        idx = self._pos + distance
        if idx >= len(self._source):
            return ""
        return self._source[idx]

    def _line_end_offset(self) -> int:
        idx = self._source.find("\n", self._pos)
        return len(self._source) if idx < 0 else idx

    # --- utama -----------------------------------------------------------

    def run(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            self._skip_ignored()
            if self._pos >= len(self._source):
                tokens.append(
                    Token(TokenType.EOF, "", None, self._line, self._column, self._pos)
                )
                return tokens
            tokens.append(self._next_token())

    def _skip_ignored(self) -> None:
        """Melewati whitespace dan seluruh bentuk komentar (``SPEC.md`` §4)."""
        source = self._source
        length = len(source)
        while self._pos < length:
            ch = source[self._pos]
            if ch.isspace():
                self._advance()
                continue
            if ch == "#":
                self._skip_line_comment()
                continue
            if ch == "-" and self._peek(1) == "-":
                following = self._peek(2)
                if following in ("", " ", "\t", "\n", "\r"):
                    self._advance()
                    self._advance()
                    self._skip_line_comment()
                    continue
            if ch == "/" and self._peek(1) == "*":
                self._skip_block_comment()
                continue
            return

    def _skip_line_comment(self) -> None:
        while self._pos < len(self._source) and self._source[self._pos] != "\n":
            self._advance()

    def _skip_block_comment(self) -> None:
        start_pos, start_line, start_column = self._pos, self._line, self._column
        self._advance()
        self._advance()
        while True:
            if self._pos >= len(self._source):
                raise LexError(
                    LexCode.UNTERMINATED_COMMENT,
                    "Komentar blok '/*' tidak ditutup dengan '*/'.",
                    start_line,
                    start_column,
                    max(1, self._line_end_offset() - start_pos),
                    start_pos,
                )
            if self._source[self._pos] == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                return
            self._advance()

    def _next_token(self) -> Token:
        ch = self._source[self._pos]
        if ch in ("'", '"'):
            return self._read_string(ch)
        if ch == "`":
            return self._read_quoted_identifier()
        if ch in "0123456789":
            return self._read_number()
        if _is_ident_start(ch):
            return self._read_word()

        two = self._source[self._pos : self._pos + 2]
        if two in _TWO_CHAR_OPERATORS:
            return self._read_operator(2)
        if ch in _ONE_CHAR_OPERATORS:
            return self._read_operator(1)
        if ch in _PUNCTUATION:
            return self._read_punct()
        if ch in "&|!":
            raise LexError(
                LexCode.UNSUPPORTED_OPERATOR,
                self._unsupported_operator_message(ch),
                self._line,
                self._column,
                2 if two in ("&&", "||") else 1,
                self._pos,
            )
        raise LexError(
            LexCode.UNKNOWN_CHARACTER,
            f"Karakter tidak dikenal: {ch!r}.",
            self._line,
            self._column,
            1,
            self._pos,
        )

    @staticmethod
    def _unsupported_operator_message(ch: str) -> str:
        if ch == "&":
            return "Operator '&' tidak didukung; gunakan 'AND' (di MySQL '&&' berarti AND)."
        if ch == "|":
            return "Operator '|' tidak didukung; gunakan 'OR' (di MySQL '||' berarti OR)."
        return "Operator '!' tidak didukung; gunakan 'NOT' atau '<>' (di MySQL '!' berarti NOT)."

    # --- pembaca per kelas token ----------------------------------------

    def _make(
        self,
        token_type: TokenType,
        lexeme: str,
        value: str | int | float | None,
        start_pos: int,
        start_line: int,
        start_column: int,
    ) -> Token:
        return Token(token_type, lexeme, value, start_line, start_column, start_pos)

    def _read_operator(self, size: int) -> Token:
        start_pos, start_line, start_column = self._pos, self._line, self._column
        lexeme = self._source[self._pos : self._pos + size]
        for _ in range(size):
            self._advance()
        value = "<>" if lexeme == "!=" else lexeme
        return self._make(TokenType.OPERATOR, lexeme, value, start_pos, start_line, start_column)

    def _read_punct(self) -> Token:
        start_pos, start_line, start_column = self._pos, self._line, self._column
        lexeme = self._source[self._pos]
        self._advance()
        return self._make(TokenType.PUNCT, lexeme, lexeme, start_pos, start_line, start_column)

    def _read_number(self) -> Token:
        start_pos, start_line, start_column = self._pos, self._line, self._column
        while self._peek() in _DIGITS:
            self._advance()
        is_float = False
        if self._peek() == "." and self._peek(1) in _DIGITS:
            is_float = True
            self._advance()
            while self._peek() in _DIGITS:
                self._advance()
        lexeme = self._source[start_pos : self._pos]
        token_type = TokenType.FLOAT if is_float else TokenType.INTEGER
        value: int | float = float(lexeme) if is_float else int(lexeme)
        return self._make(token_type, lexeme, value, start_pos, start_line, start_column)

    def _read_word(self) -> Token:
        start_pos, start_line, start_column = self._pos, self._line, self._column
        while self._pos < len(self._source) and _is_ident_part(self._source[self._pos]):
            self._advance()
        lexeme = self._source[start_pos : self._pos]
        upper = lexeme.upper()
        if upper in KEYWORDS:
            return self._make(
                TokenType.KEYWORD, lexeme, upper, start_pos, start_line, start_column
            )
        return self._make(
            TokenType.IDENT, lexeme, lexeme.lower(), start_pos, start_line, start_column
        )

    def _read_quoted_identifier(self) -> Token:
        start_pos, start_line, start_column = self._pos, self._line, self._column
        self._advance()  # backtick pembuka
        buffer: list[str] = []
        while True:
            if self._pos >= len(self._source):
                raise LexError(
                    LexCode.UNTERMINATED_QUOTED_IDENTIFIER,
                    "Identitas berkutip backtick tidak ditutup dengan '`'.",
                    start_line,
                    start_column,
                    max(1, self._line_end_offset() - start_pos),
                    start_pos,
                )
            ch = self._source[self._pos]
            if ch == "`":
                if self._peek(1) == "`":
                    buffer.append("`")
                    self._advance()
                    self._advance()
                    continue
                self._advance()
                break
            buffer.append(ch)
            self._advance()
        lexeme = self._source[start_pos : self._pos]
        value = "".join(buffer).lower()
        return self._make(TokenType.QIDENT, lexeme, value, start_pos, start_line, start_column)

    def _read_string(self, quote: str) -> Token:
        start_pos, start_line, start_column = self._pos, self._line, self._column
        self._advance()  # kutip pembuka
        buffer: list[str] = []
        while True:
            if self._pos >= len(self._source):
                raise LexError(
                    LexCode.UNTERMINATED_STRING,
                    f"Literal string tidak ditutup dengan {quote!r}.",
                    start_line,
                    start_column,
                    max(1, self._line_end_offset() - start_pos),
                    start_pos,
                )
            ch = self._source[self._pos]
            if ch == quote:
                if self._peek(1) == quote:
                    buffer.append(quote)
                    self._advance()
                    self._advance()
                    continue
                self._advance()
                break
            if ch == "\\":
                self._read_escape(quote, buffer)
                continue
            buffer.append(ch)
            self._advance()
        lexeme = self._source[start_pos : self._pos]
        return self._make(
            TokenType.STRING, lexeme, "".join(buffer), start_pos, start_line, start_column
        )

    def _read_escape(self, quote: str, buffer: list[str]) -> None:
        escape_line, escape_column, escape_pos = self._line, self._column, self._pos
        self._advance()  # backslash
        if self._pos >= len(self._source):
            raise LexError(
                LexCode.UNTERMINATED_STRING,
                f"Literal string tidak ditutup dengan {quote!r}.",
                escape_line,
                escape_column,
                1,
                escape_pos,
            )
        char = self._source[self._pos]
        if char in _SIMPLE_ESCAPES:
            buffer.append(_SIMPLE_ESCAPES[char])
        elif char in _LIKE_ESCAPES:
            buffer.append("\\" + char)
        else:
            raise LexError(
                LexCode.UNKNOWN_ESCAPE,
                f"Escape '\\{char}' tidak didukung. "
                "Yang didukung: \\\\ \\' \\\" \\n \\t \\0 \\% \\_.",
                escape_line,
                escape_column,
                2,
                escape_pos,
            )
        self._advance()
