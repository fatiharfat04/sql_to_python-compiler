"""Parser recursive descent manual sesuai grammar EBNF ``SPEC.md`` §5.

Presedensi (tinggi → rendah): literal/kolom/fungsi, unary ``+ -``,
``* / DIV %``, ``+ -``, perbandingan/``LIKE``/``IN``/``BETWEEN``/``IS NULL``,
``NOT``, ``AND``, ``OR``. Perbandingan tidak berantai: ``a < b < c`` adalah
error sintaks.

Fitur di luar ruang lingkup ``SPEC.md`` §2 (RIGHT/FULL/CROSS JOIN, join gaya
koma, subquery, ``UNION``, ``WITH``, nama berkualifikasi database, ``SELECT``
tanpa ``FROM``, ``COUNT(DISTINCT ...)``, DDL/DML) ditolak dengan pesan khusus
berkode ``SYN005``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from typing import cast

from .ast_nodes import (
    Between,
    BinaryOp,
    Column,
    Expr,
    FunctionCall,
    InList,
    IsNull,
    Join,
    JoinKind,
    Like,
    Literal,
    Node,
    OrderItem,
    SelectItem,
    SelectStatement,
    Star,
    TableRef,
    UnaryOp,
)
from .errors import ParseError, SynCode
from .lexer import Token, TokenType

__all__ = ["parse", "pretty_print"]

_COMPARISON_OPS = frozenset({"=", "<>", "<", ">", "<=", ">="})
_TERM_OPS = frozenset({"*", "/", "%"})
_ARITH_OPS = frozenset({"+", "-"})
_UNSUPPORTED_JOIN_KINDS = frozenset({"right", "full", "cross"})
_SET_OPERATORS = frozenset({"union", "except", "intersect"})
_NON_SELECT_STATEMENTS = frozenset(
    {
        "create",
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "truncate",
        "replace",
        "show",
        "explain",
        "describe",
        "set",
        "use",
        "grant",
        "revoke",
        "begin",
        "commit",
        "rollback",
        "call",
        "load",
        "optimize",
        "analyze",
    }
)


def parse(tokens: Sequence[Token], source: str) -> SelectStatement:
    """Membangun AST ``SelectStatement`` dari ``tokens``.

    Raises:
        ParseError: bila masukan tidak sesuai grammar ``SPEC.md`` §5 atau
            memakai fitur di luar ruang lingkup ``SPEC.md`` §2.
    """
    return _Parser(list(tokens), source).parse()


class _Parser:
    def __init__(self, tokens: list[Token], source: str) -> None:
        self._tokens = tokens
        self._source = source
        self._i = 0

    # --- primitif ---------------------------------------------------------

    def _peek(self, distance: int = 0) -> Token:
        index = min(self._i + distance, len(self._tokens) - 1)
        return self._tokens[index]

    def _at_end(self) -> bool:
        return self._peek().type is TokenType.EOF

    def _advance(self) -> Token:
        token = self._tokens[self._i]
        if token.type is not TokenType.EOF:
            self._i += 1
        return token

    def _previous(self) -> Token:
        return self._tokens[max(self._i - 1, 0)]

    @staticmethod
    def _is_ident(token: Token) -> bool:
        return token.type in (TokenType.IDENT, TokenType.QIDENT)

    @staticmethod
    def _describe(token: Token) -> str:
        if token.type is TokenType.EOF:
            return "'akhir berkas'"
        return f"'{token.lexeme}'"

    def _error(
        self,
        code: SynCode,
        message: str,
        token: Token | None = None,
        expected: str | Sequence[str] = (),
    ) -> ParseError:
        target = token if token is not None else self._peek()
        length = len(target.lexeme) if target.lexeme else 1
        return ParseError(
            str(code),
            message,
            target.line,
            target.column,
            expected,
            max(1, length),
            target.offset,
        )

    def _unexpected(self, expected: str, token: Token | None = None) -> ParseError:
        target = token if token is not None else self._peek()
        return self._error(
            SynCode.UNEXPECTED_TOKEN,
            f"Diharapkan {expected}, ditemukan {self._describe(target)}.",
            target,
            expected,
        )

    def _unsupported(self, message: str, token: Token | None = None) -> ParseError:
        return self._error(SynCode.UNSUPPORTED_FEATURE, message, token)

    def _match_keyword(self, *names: str) -> Token | None:
        if self._peek().is_keyword(*names):
            return self._advance()
        return None

    def _check_keyword(self, *names: str) -> bool:
        return self._peek().is_keyword(*names)

    def _match_punct(self, *chars: str) -> Token | None:
        if self._peek().is_punct(*chars):
            return self._advance()
        return None

    def _check_punct(self, *chars: str) -> bool:
        return self._peek().is_punct(*chars)

    def _expect_keyword(self, name: str) -> Token:
        token = self._match_keyword(name)
        if token is None:
            raise self._unexpected(name)
        return token

    def _expect_integer(self, description: str) -> int:
        token = self._peek()
        if token.type is not TokenType.INTEGER:
            raise self._unexpected(description)
        self._advance()
        return cast(int, token.value)

    # --- pernyataan -------------------------------------------------------

    def parse(self) -> SelectStatement:
        self._reject_unsupported_start()
        statement = self._parse_select()
        if self._at_end():
            return statement
        token = self._peek()
        if self._is_ident(token) and token.name() in _SET_OPERATORS:
            raise self._unsupported(
                f"Operator '{token.name().upper()}' tidak didukung; "
                "hanya satu pernyataan SELECT yang diizinkan.",
                token,
            )
        if self._match_punct(";"):
            if not self._at_end():
                raise self._error(
                    SynCode.TRAILING_INPUT,
                    "Teks setelah ';' tidak didukung; hanya satu pernyataan SELECT.",
                )
            return statement
        raise self._unexpected("akhir berkas")

    def _reject_unsupported_start(self) -> None:
        token = self._peek()
        if token.is_keyword("SELECT"):
            return
        if self._is_ident(token):
            name = token.name()
            if name == "with":
                raise self._unsupported(
                    "CTE (WITH) tidak didukung; hanya satu pernyataan SELECT.", token
                )
            if name in _NON_SELECT_STATEMENTS:
                raise self._unsupported(
                    f"Hanya pernyataan SELECT yang didukung; ditemukan "
                    f"{self._describe(token)}.",
                    token,
                )
        raise self._unexpected("SELECT", token)

    def _parse_select(self) -> SelectStatement:
        start = self._expect_keyword("SELECT")
        distinct = self._match_keyword("DISTINCT") is not None
        items: list[SelectItem | Star] = [self._parse_select_item()]
        while self._match_punct(","):
            items.append(self._parse_select_item())
        if not self._check_keyword("FROM"):
            token = self._peek()
            note = ""
            if token.type is TokenType.EOF or token.is_punct(";"):
                note = " Pernyataan SELECT tanpa FROM tidak didukung (SPEC.md §2)."
            raise self._error(
                SynCode.UNEXPECTED_TOKEN,
                f"Diharapkan FROM, ditemukan {self._describe(token)}.{note}",
                token,
                "FROM",
            )
        self._advance()  # FROM
        from_ = self._parse_table_ref()
        if self._check_punct(","):
            raise self._unsupported(
                "Join gaya koma ('FROM a, b') tidak didukung; gunakan JOIN eksplisit."
            )

        joins: list[Join] = []
        while self._check_keyword("JOIN", "INNER", "LEFT"):
            joins.append(self._parse_join())

        where: Expr | None = None
        if self._match_keyword("WHERE"):
            where = self._parse_expr()

        group_by: list[Expr] = []
        if self._match_keyword("GROUP"):
            self._expect_keyword("BY")
            group_by.append(self._parse_expr())
            while self._match_punct(","):
                group_by.append(self._parse_expr())

        having: Expr | None = None
        if self._match_keyword("HAVING"):
            having = self._parse_expr()

        order_by: list[OrderItem] = []
        if self._match_keyword("ORDER"):
            self._expect_keyword("BY")
            order_by.append(self._parse_order_item())
            while self._match_punct(","):
                order_by.append(self._parse_order_item())

        limit: int | None = None
        offset = 0
        if self._match_keyword("LIMIT"):
            limit, offset = self._parse_limit()

        return SelectStatement(
            distinct=distinct,
            items=items,
            from_=from_,
            joins=joins,
            where=where,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=limit,
            offset=offset,
            line=start.line,
            column=start.column,
        )

    def _parse_join(self) -> Join:
        start = self._peek()
        kind: JoinKind = "INNER"
        if self._match_keyword("LEFT"):
            kind = "LEFT"
            self._match_keyword("OUTER")
        elif self._match_keyword("INNER"):
            if self._check_keyword("OUTER"):
                raise self._unexpected("JOIN setelah INNER")
        self._expect_keyword("JOIN")
        table = self._parse_table_ref()
        self._expect_keyword("ON")
        on = self._parse_expr()
        return Join(kind=kind, table=table, on=on, line=start.line, column=start.column)

    def _parse_order_item(self) -> OrderItem:
        start = self._peek()
        expr = self._parse_expr()
        descending = False
        if self._match_keyword("DESC"):
            descending = True
        else:
            self._match_keyword("ASC")
        return OrderItem(
            expr=expr, descending=descending, line=start.line, column=start.column
        )

    def _parse_limit(self) -> tuple[int | None, int]:
        first = self._expect_integer("angka bulat setelah LIMIT")
        if self._match_punct(","):
            second = self._expect_integer("angka bulat setelah ',' pada LIMIT")
            return second, first
        if self._match_keyword("OFFSET"):
            second = self._expect_integer("angka bulat setelah OFFSET")
            return first, second
        return first, 0

    # --- daftar SELECT dan tabel -----------------------------------------

    def _parse_select_item(self) -> SelectItem | Star:
        if self._check_punct("*"):
            token = self._advance()
            return Star(table=None, line=token.line, column=token.column)
        if (
            self._is_ident(self._peek())
            and self._peek(1).is_punct(".")
            and self._peek(2).is_punct("*")
        ):
            qualifier = self._advance()
            self._advance()
            star = self._advance()
            return Star(table=qualifier.name(), line=star.line, column=star.column)

        start = self._peek()
        expr = self._parse_expr()
        last = self._previous()
        text = self._source[start.offset : last.offset + len(last.lexeme)]
        alias = self._parse_optional_alias()
        return SelectItem(
            expr=expr, alias=alias, text=text, line=start.line, column=start.column
        )

    def _parse_optional_alias(self) -> str | None:
        if self._match_keyword("AS"):
            token = self._peek()
            if not self._is_ident(token):
                raise self._unexpected("nama alias setelah AS")
            self._advance()
            return token.name()
        token = self._peek()
        if self._is_ident(token) and token.name() not in _SET_OPERATORS:
            self._advance()
            return token.name()
        return None

    def _parse_table_ref(self) -> TableRef:
        token = self._peek()
        if token.is_punct("("):
            raise self._unsupported(
                "Subquery pada klausa FROM tidak didukung; hanya satu tabel dasar.", token
            )
        if not self._is_ident(token):
            raise self._unexpected("nama tabel")
        self._advance()
        if self._check_punct("."):
            raise self._unsupported(
                "Nama tabel berkualifikasi database ('db.tabel') tidak didukung.",
                self._peek(),
            )
        candidate = self._peek()
        if (
            self._is_ident(candidate)
            and candidate.name() in _UNSUPPORTED_JOIN_KINDS
            and self._peek(1).is_keyword("JOIN", "OUTER")
        ):
            suffix = " OUTER JOIN" if self._peek(1).is_keyword("OUTER") else " JOIN"
            raise self._unsupported(
                f"'{candidate.lexeme.upper()}{suffix}' tidak didukung; "
                "hanya INNER JOIN dan LEFT JOIN.",
                candidate,
            )
        alias = self._parse_optional_alias()
        return TableRef(name=token.name(), alias=alias, line=token.line, column=token.column)

    # --- ekspresi ---------------------------------------------------------

    def _parse_expr(self) -> Expr:
        return self._parse_or()

    def _parse_or(self) -> Expr:
        left = self._parse_and()
        while self._match_keyword("OR"):
            right = self._parse_and()
            left = BinaryOp(
                op="OR", left=left, right=right, line=left.line, column=left.column
            )
        return left

    def _parse_and(self) -> Expr:
        left = self._parse_not()
        while self._match_keyword("AND"):
            right = self._parse_not()
            left = BinaryOp(
                op="AND", left=left, right=right, line=left.line, column=left.column
            )
        return left

    def _parse_not(self) -> Expr:
        token = self._match_keyword("NOT")
        if token is not None:
            operand = self._parse_not()
            return UnaryOp(op="NOT", operand=operand, line=token.line, column=token.column)
        return self._parse_predicate()

    def _parse_predicate(self) -> Expr:
        left = self._parse_additive()
        token = self._peek()
        if token.type is TokenType.OPERATOR and str(token.value) in _COMPARISON_OPS:
            self._advance()
            right = self._parse_additive()
            return BinaryOp(
                op=str(token.value), left=left, right=right, line=left.line, column=left.column
            )
        return self._parse_predicate_suffix(left)

    def _parse_predicate_suffix(self, left: Expr) -> Expr:
        negated = False
        token = self._peek()
        if token.is_keyword("NOT") and self._peek(1).is_keyword("LIKE", "IN", "BETWEEN"):
            negated = True
            self._advance()
            token = self._peek()

        if token.is_keyword("LIKE"):
            self._advance()
            return self._parse_like(left, negated)
        if token.is_keyword("IN"):
            self._advance()
            return self._parse_in(left, negated)
        if token.is_keyword("BETWEEN"):
            self._advance()
            return self._parse_between(left, negated)
        if not negated and token.is_keyword("IS"):
            self._advance()
            inner_negated = self._match_keyword("NOT") is not None
            self._expect_keyword("NULL")
            return IsNull(
                operand=left,
                negated=inner_negated,
                line=left.line,
                column=left.column,
            )
        return left

    def _parse_like(self, operand: Expr, negated: bool) -> Expr:
        pattern_token = self._peek()
        if pattern_token.type is not TokenType.STRING:
            raise self._error(
                SynCode.UNEXPECTED_TOKEN,
                "Operand kanan LIKE harus berupa literal string (misal 'A%'); "
                f"ditemukan {self._describe(pattern_token)}.",
                pattern_token,
                "literal string",
            )
        self._advance()
        return Like(
            operand=operand,
            pattern=cast(str, pattern_token.value),
            negated=negated,
            line=operand.line,
            column=operand.column,
        )

    def _parse_in(self, operand: Expr, negated: bool) -> Expr:
        self._expect_punct_opening("(")
        if self._check_keyword("SELECT"):
            raise self._unsupported(
                "Subquery pada klausa IN ('IN (SELECT ...)') tidak didukung."
            )
        values: list[Expr] = [self._parse_expr()]
        while self._match_punct(","):
            values.append(self._parse_expr())
        self._expect_punct_close(")")
        return InList(
            operand=operand,
            values=values,
            negated=negated,
            line=operand.line,
            column=operand.column,
        )

    def _parse_between(self, operand: Expr, negated: bool) -> Expr:
        low = self._parse_additive()
        self._expect_keyword("AND")
        high = self._parse_additive()
        return Between(
            operand=operand,
            low=low,
            high=high,
            negated=negated,
            line=operand.line,
            column=operand.column,
        )

    def _expect_punct_opening(self, char: str) -> Token:
        token = self._match_punct(char)
        if token is None:
            raise self._unexpected(f"'{char}'")
        return token

    def _expect_punct_close(self, char: str) -> Token:
        token = self._match_punct(char)
        if token is None:
            raise self._error(
                SynCode.UNBALANCED_PAREN,
                f"Tanda kurung tidak ditutup; ditemukan {self._describe(self._peek())}.",
            )
        return token

    def _parse_additive(self) -> Expr:
        left = self._parse_term()
        while True:
            token = self._peek()
            if token.type is not TokenType.OPERATOR or str(token.value) not in _ARITH_OPS:
                return left
            self._advance()
            right = self._parse_term()
            left = BinaryOp(
                op=str(token.value), left=left, right=right, line=left.line, column=left.column
            )

    def _parse_term(self) -> Expr:
        left = self._parse_unary()
        while True:
            token = self._peek()
            if token.type is TokenType.OPERATOR and str(token.value) in _TERM_OPS:
                self._advance()
            elif token.is_keyword("DIV"):
                self._advance()
            else:
                return left
            right = self._parse_unary()
            left = BinaryOp(
                op=str(token.value), left=left, right=right, line=left.line, column=left.column
            )

    def _parse_unary(self) -> Expr:
        token = self._peek()
        if token.type is TokenType.OPERATOR and str(token.value) in _ARITH_OPS:
            self._advance()
            operand = self._parse_unary()
            return UnaryOp(
                op="-" if str(token.value) == "-" else "+",
                operand=operand,
                line=token.line,
                column=token.column,
            )
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        token = self._peek()
        if token.type is TokenType.INTEGER:
            self._advance()
            return Literal(
                value=cast(int, token.value), kind="INT", line=token.line, column=token.column
            )
        if token.type is TokenType.FLOAT:
            self._advance()
            return Literal(
                value=cast(float, token.value),
                kind="DOUBLE",
                line=token.line,
                column=token.column,
            )
        if token.type is TokenType.STRING:
            self._advance()
            return Literal(
                value=cast(str, token.value),
                kind="TEXT",
                line=token.line,
                column=token.column,
            )
        if token.is_keyword("NULL"):
            self._advance()
            return Literal(value=None, kind="NULL", line=token.line, column=token.column)
        if token.is_punct("("):
            return self._parse_group()
        if self._is_ident(token):
            if self._peek(1).is_punct("("):
                return self._parse_function_call()
            return self._parse_column_ref()
        raise self._error(
            SynCode.INCOMPLETE_EXPRESSION,
            f"Ekspresi tidak lengkap; diharapkan literal, kolom, fungsi, atau '(' "
            f"tetapi ditemukan {self._describe(token)}.",
            token,
        )

    def _parse_group(self) -> Expr:
        opening = self._advance()
        if self._check_keyword("SELECT"):
            raise self._unsupported(
                "Subquery ('(SELECT ...)') tidak didukung; hanya satu pernyataan SELECT.",
                self._peek(),
            )
        expr = self._parse_expr()
        if not self._check_punct(")"):
            raise self._error(
                SynCode.UNBALANCED_PAREN,
                f"Tanda kurung '(' pada baris {opening.line} kolom {opening.column} "
                f"tidak ditutup; ditemukan {self._describe(self._peek())}.",
            )
        self._advance()
        return expr

    def _parse_function_call(self) -> Expr:
        name_token = self._advance()
        self._advance()  # '('
        if self._check_keyword("DISTINCT"):
            raise self._unsupported(
                f"DISTINCT di dalam fungsi '{name_token.lexeme}' belum didukung "
                "(fitur Level 3).",
                self._peek(),
            )
        if self._check_punct("*"):
            star = self._advance()
            arg: Expr | Star = Star(table=None, line=star.line, column=star.column)
        else:
            arg = self._parse_expr()
        if not self._check_punct(")"):
            raise self._error(
                SynCode.UNBALANCED_PAREN,
                f"Pemanggilan fungsi '{name_token.lexeme}' pada baris {name_token.line} "
                f"tidak ditutup; ditemukan {self._describe(self._peek())}.",
            )
        self._advance()
        return FunctionCall(
            name=name_token.name(),
            arg=arg,
            line=name_token.line,
            column=name_token.column,
        )

    def _parse_column_ref(self) -> Expr:
        first = self._advance()
        if self._check_punct("."):
            self._advance()
            second = self._peek()
            if not self._is_ident(second):
                raise self._unexpected("nama kolom")
            self._advance()
            return Column(
                table=first.name(), name=second.name(), line=first.line, column=first.column
            )
        return Column(table=None, name=first.name(), line=first.line, column=first.column)


# --- pretty print ---------------------------------------------------------


def _scalar(value: object) -> str:
    if isinstance(value, str):
        return repr(value)
    if value is None:
        return "None"
    return str(value)


def _render(value: object, depth: int) -> str:
    indent = "  " * depth
    if isinstance(value, Node) and is_dataclass(value):
        parts = [f"{indent}{type(value).__name__}"]
        for field in dataclass_fields(value):
            item: object = getattr(value, field.name)
            label = f"{indent}  {field.name}"
            if isinstance(item, Node):
                parts.append(f"{label}:")
                parts.append(_render(item, depth + 2))
            elif isinstance(item, list):
                if not item:
                    parts.append(f"{label}: []")
                else:
                    parts.append(f"{label}:")
                    for element in item:
                        parts.append(_render(element, depth + 2))
            else:
                parts.append(f"{label}: {_scalar(item)}")
        return "\n".join(parts)
    return f"{indent}{_scalar(value)}"


def pretty_print(ast: Node) -> str:
    """Menghasilkan pohon AST berindentasi untuk debugging dan UI (``SPEC.md`` §6)."""
    return _render(ast, 0)
