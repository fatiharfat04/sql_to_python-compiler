"""Intermediate representation: logical plan, lowering, dan optimasi (``SPEC.md`` §11).

``lower(analysis)`` mengubah hasil analisis semantik menjadi pohon operator dengan
urutan **tetap**

``Scan → Join* → Filter(WHERE) → Aggregate → Filter(HAVING) → Sort → Project → Distinct → Limit``

Dua penulisan ulang dilakukan saat lowering:

1. **Alias** (`ORDER BY`/`HAVING`/`GROUP BY` yang menunjuk alias `SELECT`)
   diganti dengan ekspresi aslinya.
2. **Agregat** diekstrak menjadi `__agg0`, `__agg1`, … lalu seluruh kemunculan
   ekspresi agregat yang setara (dibandingkan lewat bentuk kanonik, sehingga
   ``COUNT(*)`` pada ``SELECT`` dan ``HAVING`` berbagi satu nama) diganti menjadi
   kolom internal `__aggN`.

Seluruh kolom pada ekspresi plan sudah dinormalisasi menjadi
``Column(table=None, name="<alias>.<kolom>")`` sesuai ``SPEC.md`` §12, sehingga
codegen cukup memakai ``name`` sebagai label kolom pandas.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Literal as Lit

from .ast_nodes import (
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
    TableRef,
    UnaryOp,
)
from .semantic import (
    AGGREGATE_FUNCTIONS,
    AnalysisResult,
    canonical_key,
)

__all__ = [
    "Aggregate",
    "Distinct",
    "Filter",
    "Join",
    "Limit",
    "Plan",
    "PlanNode",
    "Project",
    "Scan",
    "Sort",
    "fold_constant",
    "format_expr",
    "format_plan",
    "literal_scale",
    "lower",
    "round_half_away",
]

JoinKind = Lit["INNER", "LEFT"]

_ARITHMETIC = frozenset({"+", "-", "*", "/", "DIV", "%"})
_FOLDED_INT = frozenset({"+", "-", "*", "DIV", "%"})

_EMPTY_COLUMN_REFS: dict[Column, str] = {}
_EMPTY_ALIAS_REFS: dict[Column, Expr] = {}


# --------------------------------------------------------------------------- #
# Node IR                                                                      #
# --------------------------------------------------------------------------- #


class PlanNode:
    """Basis seluruh node rencana logis."""


@dataclass(frozen=True)
class Scan(PlanNode):
    """Membaca satu tabel; kolom internal memakai prefiks ``alias`` (``SPEC.md`` §12)."""

    table: str
    alias: str


@dataclass(frozen=True)
class Join(PlanNode):
    """Equi-join; ``keys`` berpasangan (kolom sisi kiri, kolom sisi kanan)."""

    kind: JoinKind
    left: PlanNode
    right: PlanNode
    keys: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Filter(PlanNode):
    """Menyaring baris; ``label`` hanya untuk penampilan (``WHERE``/``ON``/``HAVING``)."""

    child: PlanNode
    predicate: Expr
    label: str = ""


@dataclass(frozen=True)
class Aggregate(PlanNode):
    """`group_keys` berpasangan (nama kolom keluaran, ekspresi kunci)."""

    child: PlanNode
    group_keys: tuple[tuple[str, Expr], ...]
    aggs: tuple[tuple[str, str, Expr | Star], ...]


@dataclass(frozen=True)
class Sort(PlanNode):
    """Pengurutan multi-kunci; ditempatkan sebelum ``Project`` (``SPEC.md`` §11)."""

    child: PlanNode
    keys: tuple[tuple[Expr, bool], ...]


@dataclass(frozen=True)
class Project(PlanNode):
    """Proyeksi; ``items`` berpasangan (ekspresi, nama keluaran)."""

    child: PlanNode
    items: tuple[tuple[Expr, str], ...]


@dataclass(frozen=True)
class Distinct(PlanNode):
    """Menghapus duplikat berdasarkan seluruh kolom keluaran."""

    child: PlanNode


@dataclass(frozen=True)
class Limit(PlanNode):
    """Pembatasan baris; ``n=None`` berarti tanpa batas atas (hanya offset)."""

    child: PlanNode
    n: int | None
    offset: int


@dataclass(frozen=True)
class Plan:
    """Akar rencana logis hasil ``lower``."""

    root: PlanNode


# --------------------------------------------------------------------------- #
# Lipat konstan (``SPEC.md`` §11 - optimasi wajib)                             #
# --------------------------------------------------------------------------- #


def _as_number(node: Expr) -> Literal | None:
    """Kembalikan ``node`` bila berupa literal numerik, selain itu ``None``."""
    if (
        isinstance(node, Literal)
        and node.kind in ("INT", "DOUBLE")
        and isinstance(node.value, (int, float))
    ):
        return node
    return None


def _is_null_literal(node: Expr) -> bool:
    return isinstance(node, Literal) and node.value is None


def _trunc_div(left: int, right: int) -> int:
    """Pembagian bulat dipotong ke arah nol (``SPEC.md`` §10.4)."""
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


def _trunc_mod(left: int, right: int) -> int:
    """Sisa bagi dengan tanda mengikuti pembilang (``SPEC.md`` §10.4)."""
    return left - _trunc_div(left, right) * right


def literal_scale(value: object) -> int:
    """Jumlah digit di belakang koma sebuah literal angka.

    MySQL menghitung skala ``DECIMAL`` dari **teks** literal, sehingga ``4`` → 0,
    ``3.0`` → 1, dan ``1.25`` → 2. Nilai bulat selalu dianggap berskala 0.
    Dipakai untuk menentukan pembulatan hasil bagi: skala operand kiri + 4
    (``div_precision_increment``, ``SPEC.md`` §10.3).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, int):
        return 0
    text = repr(float(value))
    if "e" in text or "E" in text:
        mantissa, _, exponent = text.partition("e")
        return max(0, len(mantissa.partition(".")[2]) - int(exponent))
    return len(text.partition(".")[2])


def round_half_away(value: float, scale: int) -> float:
    """Membulatkan ``value`` ke ``scale`` desimal dengan aturan **setengah ke luar**.

    MySQL membulatkan ``DECIMAL`` menjauhi nol (``5/32 = 0.1563``), sedangkan
    ``round()`` bawaan Python/pandas memakai setengah-genap (``0.1562``). Fungsi
    ini memakai aturan MySQL; padanan vektornya ada pada fungsi bantu ``_rdec``
    yang dihasilkan ``codegen``.
    """
    factor = 10.0**scale
    scaled = abs(value) * factor
    if not math.isfinite(scaled):
        return value
    return math.copysign(math.floor(scaled + 0.5) / factor, value)


def fold_constant(node: Expr) -> Expr:
    """Melipat ekspresi aritmetika yang seluruh operandnya konstan.

    Aturan: ``NULL`` pada operand aritmetika → ``NULL``; pembagi nol konstan →
    ``NULL``; hasil bagi dibulatkan ke skala operand kiri + 4 desimal (``1/3`` →
    ``0.3333``, ``4/3.0`` → ``1.3333``); selain itu hasil mengikuti tipe operand
    (``SPEC.md`` §11).
    """
    if isinstance(node, BinaryOp):
        node = replace(node, left=fold_constant(node.left), right=fold_constant(node.right))
        return _fold_binary(node)
    if isinstance(node, UnaryOp):
        node = replace(node, operand=fold_constant(node.operand))
        return _fold_unary(node)
    if isinstance(node, Like):
        return replace(node, operand=fold_constant(node.operand))
    if isinstance(node, InList):
        return replace(
            node,
            operand=fold_constant(node.operand),
            values=[fold_constant(value) for value in node.values],
        )
    if isinstance(node, Between):
        return replace(
            node,
            operand=fold_constant(node.operand),
            low=fold_constant(node.low),
            high=fold_constant(node.high),
        )
    if isinstance(node, IsNull):
        return replace(node, operand=fold_constant(node.operand))
    if isinstance(node, FunctionCall):
        if isinstance(node.arg, Star):
            return node
        return replace(node, arg=fold_constant(node.arg))
    return node


def _fold_unary(node: UnaryOp) -> Expr:
    literal = _as_number(node.operand) if node.op in ("-", "+") else None
    if literal is None:
        return node
    value = literal.value
    assert isinstance(value, (int, float))
    folded = -value if node.op == "-" else value
    kind: Lit["INT", "DOUBLE"] = "INT" if literal.kind == "INT" else "DOUBLE"
    return Literal(folded, kind, node.line, node.column)


def _fold_binary(node: BinaryOp) -> Expr:
    op = node.op
    if op not in _ARITHMETIC:
        return node
    left, right = node.left, node.right
    if _is_null_literal(left) or _is_null_literal(right):
        return Literal(None, "NULL", node.line, node.column)
    right_number = _as_number(right)
    if op in ("/", "DIV", "%") and right_number is not None and right_number.value == 0:
        return Literal(None, "NULL", node.line, node.column)
    left_number = _as_number(left)
    if left_number is None or right_number is None:
        return node
    both_int = left_number.kind == "INT" and right_number.kind == "INT"
    if op in ("DIV", "%") and not both_int:
        # SEM005 menolak operand non-INT; jangan lipat agar tidak salah.
        return node
    a, b = left_number.value, right_number.value
    assert isinstance(a, (int, float)) and isinstance(b, (int, float))
    if op == "+":
        result: int | float = a + b
    elif op == "-":
        result = a - b
    elif op == "*":
        result = a * b
    elif op == "/":
        # MySQL: tanpa operand DOUBLE, hasilnya DECIMAL berskala kiri + 4 desimal,
        # dibulatkan setengah ke luar (bukan setengah-genap seperti ``round()``).
        result = round_half_away(a / b, literal_scale(a) + 4)
    elif op == "DIV":
        result = _trunc_div(int(a), int(b))
    else:
        result = _trunc_mod(int(a), int(b))
    kind: Lit["INT", "DOUBLE"]
    if both_int and op in _FOLDED_INT:
        kind = "INT"
    else:
        kind = "DOUBLE"
    return Literal(result, kind, node.line, node.column)


# --------------------------------------------------------------------------- #
# Penampilan                                                                   #
# --------------------------------------------------------------------------- #


def format_expr(node: Expr | Star) -> str:
    """Merekam ekspresi menjadi satu baris yang mudah dibaca."""
    if isinstance(node, Column):
        return f"{node.table}.{node.name}" if node.table else node.name
    if isinstance(node, Literal):
        return "NULL" if node.value is None else repr(node.value)
    if isinstance(node, UnaryOp):
        return f"({node.op} {format_expr(node.operand)})"
    if isinstance(node, BinaryOp):
        return f"({format_expr(node.left)} {node.op} {format_expr(node.right)})"
    if isinstance(node, Like):
        prefix = "NOT " if node.negated else ""
        return f"{format_expr(node.operand)} {prefix}LIKE {node.pattern!r}"
    if isinstance(node, InList):
        prefix = "NOT " if node.negated else ""
        values = ", ".join(format_expr(value) for value in node.values)
        return f"{format_expr(node.operand)} {prefix}IN ({values})"
    if isinstance(node, Between):
        prefix = "NOT " if node.negated else ""
        return (
            f"{format_expr(node.operand)} {prefix}BETWEEN {format_expr(node.low)} "
            f"AND {format_expr(node.high)}"
        )
    if isinstance(node, IsNull):
        return f"{format_expr(node.operand)} IS {'NOT ' if node.negated else ''}NULL"
    if isinstance(node, FunctionCall):
        return f"{node.name}({format_expr(node.arg)})"
    if isinstance(node, Star):
        return "*"
    return "?"


def _children(node: PlanNode) -> tuple[PlanNode, ...]:
    if isinstance(node, Scan):
        return ()
    if isinstance(node, Join):
        return (node.left, node.right)
    if isinstance(node, (Filter, Aggregate, Sort, Project, Distinct, Limit)):
        return (node.child,)
    return ()


def _describe(node: PlanNode) -> str:
    if isinstance(node, Scan):
        return f"Scan {node.table} AS {node.alias}"
    if isinstance(node, Join):
        keys = ", ".join(f"{left} = {right}" for left, right in node.keys)
        return f"Join {node.kind} ON {keys}"
    if isinstance(node, Filter):
        head = f"Filter [{node.label}]" if node.label else "Filter"
        return f"{head} {format_expr(node.predicate)}"
    if isinstance(node, Aggregate):
        keys = ", ".join(name for name, _ in node.group_keys)
        aggs = ", ".join(
            f"{name} = {func}({format_expr(arg)})" for name, func, arg in node.aggs
        )
        return f"Aggregate GROUP BY [{keys}] AGG [{aggs}]"
    if isinstance(node, Sort):
        keys = ", ".join(
            f"{format_expr(expr)} {'DESC' if descending else 'ASC'}"
            for expr, descending in node.keys
        )
        return f"Sort [{keys}]"
    if isinstance(node, Project):
        items = ", ".join(
            f"{format_expr(expr)} AS {name}" for expr, name in node.items
        )
        return f"Project [{items}]"
    if isinstance(node, Distinct):
        return "Distinct"
    if isinstance(node, Limit):
        return f"Limit n={node.n} offset={node.offset}"
    return "?"  # pragma: no cover


def format_plan(plan: Plan) -> str:
    """Merekam plan sebagai pohon teks untuk perintah ``explain``."""
    lines: list[str] = []
    _render(plan.root, lines, 0)
    return "\n".join(lines)


def _render(node: PlanNode, lines: list[str], depth: int) -> None:
    lines.append("  " * depth + _describe(node))
    for child in _children(node):
        _render(child, lines, depth + 1)


# --------------------------------------------------------------------------- #
# Lowering                                                                     #
# --------------------------------------------------------------------------- #


def lower(analysis: AnalysisResult) -> Plan:
    """Menurunkan hasil analisis semantik menjadi :class:`Plan`."""
    if analysis.errors:
        raise analysis.errors[0]
    return _Lowerer(analysis).run()


def _prefix(ref: TableRef) -> str:
    return (ref.alias or ref.name).lower()


def _flatten_and(expr: Expr) -> Iterator[Expr]:
    if isinstance(expr, BinaryOp) and expr.op == "AND":
        yield from _flatten_and(expr.left)
        yield from _flatten_and(expr.right)
    else:
        yield expr


def _norm_key(node: Expr) -> str:
    """Bentuk kanonik ekspresi yang sudah dinormalisasi (kolom tanpa kualifikasi)."""
    return canonical_key(node, _EMPTY_COLUMN_REFS, _EMPTY_ALIAS_REFS)


class _Lowerer:
    def __init__(self, analysis: AnalysisResult) -> None:
        self.analysis = analysis
        self.ast = analysis.ast
        self.agg_names: dict[str, str] = {}
        self.aggs: list[tuple[str, str, Expr | Star]] = []
        self.group_keys: list[tuple[str, Expr]] = []
        self.group_key_names: dict[str, str] = {}
        self.prefixes: dict[str, TableRef] = {}
        for ref in [self.ast.from_, *(join.table for join in self.ast.joins)]:
            self.prefixes[_prefix(ref)] = ref

    # --- normalisasi ------------------------------------------------------

    def _normalize(self, node: Expr) -> Expr:
        """Ganti kolom dengan nama internal, alias dengan ekspresinya, agregat dengan ``__aggN``."""
        if isinstance(node, Column):
            target = self.analysis.alias_refs.get(node)
            if target is not None:
                return self._normalize(target)
            ref = self.analysis.column_refs.get(node)
            if ref is None:
                raise KeyError(f"kolom tak terpecahkan: {node}")
            return Column(None, ref, node.line, node.column)
        if isinstance(node, Literal):
            return node
        if isinstance(node, UnaryOp):
            return replace(node, operand=self._normalize(node.operand))
        if isinstance(node, BinaryOp):
            return replace(
                node,
                left=self._normalize(node.left),
                right=self._normalize(node.right),
            )
        if isinstance(node, Like):
            return replace(node, operand=self._normalize(node.operand))
        if isinstance(node, InList):
            return replace(
                node,
                operand=self._normalize(node.operand),
                values=[self._normalize(value) for value in node.values],
            )
        if isinstance(node, Between):
            return replace(
                node,
                operand=self._normalize(node.operand),
                low=self._normalize(node.low),
                high=self._normalize(node.high),
            )
        if isinstance(node, IsNull):
            return replace(node, operand=self._normalize(node.operand))
        if isinstance(node, FunctionCall):
            if node.name in AGGREGATE_FUNCTIONS:
                return self._register_aggregate(node)
            if isinstance(node.arg, Star):  # pragma: no cover - SEM009 menolak lebih dulu
                return node
            return replace(node, arg=self._normalize(node.arg))
        raise TypeError(f"node ekspresi tak dikenal: {node!r}")  # pragma: no cover

    def _register_aggregate(self, node: FunctionCall) -> Expr:
        key = canonical_key(
            node, self.analysis.column_refs, self.analysis.alias_refs
        )
        name = self.agg_names.get(key)
        if name is None:
            name = f"__agg{len(self.aggs)}"
            self.agg_names[key] = name
            arg: Expr | Star = (
                node.arg
                if isinstance(node.arg, Star)
                else self._normalize(node.arg)
            )
            self.aggs.append((name, node.name, arg))
        return Column(None, name, node.line, node.column)

    def _finalize(self, node: Expr, *, check_group: bool) -> Expr:
        """Normalisasi + lipat konstan + substitusi kunci ``GROUP BY``."""
        normalized = fold_constant(self._normalize(node))
        if check_group:
            name = self.group_key_names.get(_norm_key(normalized))
            if name is not None:
                return Column(None, name, node.line, node.column)
        return normalized

    # --- ekspansi ``*`` ----------------------------------------------------

    def _expand_star(self, star: Star) -> list[tuple[Expr, str]]:
        if star.table is None:
            names: Sequence[str] = tuple(self.prefixes)
        else:
            names = (star.table,)
        items: list[tuple[Expr, str]] = []
        for name in names:
            ref = self.prefixes.get(name)
            if ref is None:  # pragma: no cover - SEM002 menangkap lebih dulu
                continue
            table = self.analysis.schema.get(ref.name)
            if table is None:  # pragma: no cover - SEM001 menangkap lebih dulu
                continue
            for column in table.columns:
                internal = f"{name}.{column.name}"
                items.append(
                    (
                        Column(None, internal, star.line, star.column),
                        column.name,
                    )
                )
        return items

    def _project_items(self) -> list[tuple[Expr, str]]:
        items: list[tuple[Expr, str]] = []
        for item in self.ast.items:
            if isinstance(item, Star):
                items.extend(self._expand_star(item))
                continue
            items.append((self._finalize(item.expr, check_group=True), self._name(item)))
        return items

    @staticmethod
    def _name(item: SelectItem) -> str:
        if item.alias:
            return item.alias
        if isinstance(item.expr, Column):
            return item.expr.name
        return item.text

    # --- pembangunan plan --------------------------------------------------

    def _scan(self, ref: TableRef) -> Scan:
        return Scan(ref.name, _prefix(ref))

    def _join_keys(
        self, on: Expr, left: set[str], right: set[str]
    ) -> tuple[tuple[str, str], ...]:
        keys: list[tuple[str, str]] = []
        for conjunct in _flatten_and(on):
            if not (
                isinstance(conjunct, BinaryOp)
                and conjunct.op == "="
                and isinstance(conjunct.left, Column)
                and isinstance(conjunct.right, Column)
            ):
                raise ValueError(  # pragma: no cover - SEM013 menangkap lebih dulu
                    f"ON bukan konjungsi equi-join: {format_expr(conjunct)}"
                )
            a, b = conjunct.left.name, conjunct.right.name
            if a.split(".", 1)[0] in right and b.split(".", 1)[0] in left:
                a, b = b, a
            keys.append((a, b))
        return tuple(keys)

    def run(self) -> Plan:
        ast = self.ast

        where = (
            None if ast.where is None else self._finalize(ast.where, check_group=False)
        )
        on_exprs = [
            self._finalize(join.on, check_group=False) for join in ast.joins
        ]

        # Kunci GROUP BY harus dinormalisasi lebih dulu agar dapat disubstitusikan.
        for expr in ast.group_by:
            normalized = fold_constant(self._normalize(expr))
            key = _norm_key(normalized)
            if key in self.group_key_names:
                continue
            if isinstance(normalized, Column):
                name = normalized.name
            else:
                name = f"__g{len(self.group_keys)}"
            self.group_key_names[key] = name
            self.group_keys.append((name, normalized))

        # Urutan ekstraksi agregat: SELECT lalu HAVING lalu ORDER BY.
        project_items = self._project_items()
        having = (
            None
            if ast.having is None
            else self._finalize(ast.having, check_group=True)
        )
        sort_keys = tuple(
            (self._finalize(item.expr, check_group=True), item.descending)
            for item in ast.order_by
        )

        root: PlanNode = self._scan(ast.from_)
        left = {_prefix(ast.from_)}
        for join, on in zip(ast.joins, on_exprs):
            right = {_prefix(join.table)}
            keys = self._join_keys(on, left, right)
            root = Join(join.kind, root, self._scan(join.table), keys)
            left |= right

        if where is not None:
            root = Filter(root, where, "WHERE")
        if ast.group_by or self.aggs:
            root = Aggregate(root, tuple(self.group_keys), tuple(self.aggs))
        if having is not None:
            root = Filter(root, having, "HAVING")
        if sort_keys:
            root = Sort(root, sort_keys)
        root = Project(root, tuple(project_items))
        if ast.distinct:
            root = Distinct(root)
        if ast.limit is not None or ast.offset:
            root = Limit(root, ast.limit, ast.offset)
        return Plan(root)
