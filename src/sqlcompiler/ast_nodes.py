"""Node AST beku (``@dataclass(frozen=True)``) sesuai ``SPEC.md`` §6.

Seluruh node memuat ``line`` dan ``column`` (basis 1) yang menunjuk token pertama
yang membentuknya. Node untuk fitur Level 2 (``JOIN``, ``GROUP BY``, ``LIKE``,
``IN``, ``BETWEEN``, ``IS NULL``, ``ORDER BY``, ``LIMIT``) sudah didefinisikan di
sini meskipun baru dipakai mulai Langkah 3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal as Lit

__all__ = [
    "Between",
    "BinaryOp",
    "Column",
    "Expr",
    "FunctionCall",
    "InList",
    "IsNull",
    "Join",
    "Like",
    "Literal",
    "LiteralKind",
    "Node",
    "OrderItem",
    "SelectItem",
    "SelectStatement",
    "Star",
    "TableRef",
    "UnaryOp",
]

LiteralKind = Lit["INT", "DOUBLE", "TEXT", "NULL"]
JoinKind = Lit["INNER", "LEFT"]
UnaryOpKind = Lit["-", "+", "NOT"]


class Node:
    """Basis seluruh node AST; hanya mendeklarasikan posisi sumber."""

    line: int
    column: int


class Expr(Node):
    """Basis seluruh ekspresi."""


@dataclass(frozen=True)
class Star(Node):
    """``*`` atau ``alias.*`` pada daftar ``SELECT`` dan argumen fungsi."""

    table: str | None
    line: int
    column: int


@dataclass(frozen=True)
class Literal(Expr):
    """Literal ``INTEGER``, ``FLOAT``, ``STRING``, atau ``NULL``."""

    value: int | float | str | None
    kind: LiteralKind
    line: int
    column: int


@dataclass(frozen=True)
class Column(Expr):
    """Referensi kolom, dengan kualifikasi tabel/alias yang opsional."""

    table: str | None
    name: str
    line: int
    column: int


@dataclass(frozen=True)
class UnaryOp(Expr):
    """Operator unary ``-``, ``+``, atau ``NOT``."""

    op: UnaryOpKind
    operand: Expr
    line: int
    column: int


@dataclass(frozen=True)
class BinaryOp(Expr):
    """Operator biner aritmetika, perbandingan, dan logika."""

    op: str
    left: Expr
    right: Expr
    line: int
    column: int


@dataclass(frozen=True)
class Like(Expr):
    """``x [NOT] LIKE '<pola>'``; pola selalu literal string (``SPEC.md`` §5)."""

    operand: Expr
    pattern: str
    negated: bool
    line: int
    column: int


@dataclass(frozen=True)
class InList(Expr):
    """``x [NOT] IN (a, b, ...)``."""

    operand: Expr
    values: list[Expr]
    negated: bool
    line: int
    column: int

    def __hash__(self) -> int:
        """Hash struktural; ``@dataclass(frozen=True)`` tak dapat meng-hash ``list``."""
        return hash(
            (
                type(self).__name__,
                self.operand,
                tuple(self.values),
                self.negated,
                self.line,
                self.column,
            )
        )


@dataclass(frozen=True)
class Between(Expr):
    """``x [NOT] BETWEEN a AND b``."""

    operand: Expr
    low: Expr
    high: Expr
    negated: bool
    line: int
    column: int


@dataclass(frozen=True)
class IsNull(Expr):
    """``x IS [NOT] NULL``."""

    operand: Expr
    negated: bool
    line: int
    column: int


@dataclass(frozen=True)
class FunctionCall(Expr):
    """Pemanggilan fungsi agregat; ``arg`` berupa ``Star`` hanya untuk ``COUNT(*)``."""

    name: str
    arg: Expr | Star
    line: int
    column: int


@dataclass(frozen=True)
class SelectItem(Node):
    """Satu item pada daftar ``SELECT``; ``text`` adalah potongan sumber persis."""

    expr: Expr
    alias: str | None
    text: str
    line: int
    column: int


@dataclass(frozen=True)
class TableRef(Node):
    """Referensi tabel dengan alias yang opsional."""

    name: str
    alias: str | None
    line: int
    column: int


@dataclass(frozen=True)
class Join(Node):
    """``[INNER|LEFT [OUTER]] JOIN tabel ON ekspresi``."""

    kind: JoinKind
    table: TableRef
    on: Expr
    line: int
    column: int


@dataclass(frozen=True)
class OrderItem(Node):
    """Satu kunci ``ORDER BY``; default ``ASC``."""

    expr: Expr
    descending: bool
    line: int
    column: int


@dataclass(frozen=True)
class SelectStatement(Node):
    """Akar AST satu pernyataan ``SELECT`` (``SPEC.md`` §6)."""

    distinct: bool
    items: list[SelectItem | Star]
    from_: TableRef
    joins: list[Join]
    where: Expr | None
    group_by: list[Expr]
    having: Expr | None
    order_by: list[OrderItem]
    limit: int | None
    offset: int
    line: int
    column: int
