"""Skema, sistem tipe, dan analisis semantik sesuai ``SPEC.md`` §7 dan §8.

Analyzer **mengumpulkan seluruh** error ``SEM001..SEM016`` (bukan gagal di error
pertama), memetakan setiap node ``Column`` ke nama internal ``alias.kolom``,
memetakan referensi alias ``ORDER BY`` ke ekspresi aslinya, serta menyimpan tipe
setiap node ekspresi. Seluruh hasil diletakkan pada ``AnalysisResult`` yang
terpisah dari AST (AST beku/tak bisa diubah).
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

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
    Node,
    SelectItem,
    SelectStatement,
    Star,
    TableRef,
    UnaryOp,
)
from .errors import SemanticError, SemCode

__all__ = [
    "AGGREGATE_FUNCTIONS",
    "AnalysisResult",
    "ColumnDef",
    "DataType",
    "Schema",
    "Table",
    "analyze",
    "canonical_key",
]


class DataType(StrEnum):
    """Tipe kompilator (``SPEC.md`` §7).

    ``BOOLEAN`` dipakai sebagai tipe hasil perbandingan/predikat dan ``NULL``
    sebagai tipe literal ``NULL`` sesuai kolom "Tipe hasil" pada tabel §7.
    ``UNSUPPORTED`` adalah penanda untuk tipe MySQL yang belum didukung.
    """

    INT = "INT"
    DOUBLE = "DOUBLE"
    TEXT = "TEXT"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"
    UNSUPPORTED = "UNSUPPORTED"


#: Fungsi agregat Level 2 (``SPEC.md`` §2).
AGGREGATE_FUNCTIONS: frozenset[str] = frozenset({"count", "sum", "avg", "min", "max"})

_NUMERIC = frozenset({DataType.INT, DataType.DOUBLE})
_TEXTUAL = frozenset({DataType.TEXT})


@dataclass(frozen=True)
class ColumnDef:
    """Definisi satu kolom pada tabel skema."""

    name: str
    type: DataType
    mysql_type: str = ""
    warning: str | None = None


@dataclass(frozen=True)
class Table:
    """Satu tabel beserta urutan kolomnya."""

    name: str
    columns: tuple[ColumnDef, ...]

    def get(self, name: str) -> ColumnDef | None:
        """Mencari kolom tanpa membedakan huruf besar/kecil."""
        lowered = name.lower()
        for column in self.columns:
            if column.name == lowered:
                return column
        return None

    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


@dataclass(frozen=True)
class Schema:
    """Kumpulan tabel yang tersedia bagi query."""

    tables: tuple[Table, ...]

    def get(self, name: str) -> Table | None:
        """Mencari tabel tanpa membedakan huruf besar/kecil."""
        lowered = name.lower()
        for table in self.tables:
            if table.name == lowered:
                return table
        return None

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(table.name for table in self.tables)


@dataclass(frozen=True)
class AnalysisResult:
    """Hasil analisis semantik; struktur terpisah dari AST yang beku."""

    ast: SelectStatement
    schema: Schema
    types: dict[Expr, DataType]
    column_refs: dict[Column, str]
    alias_refs: dict[Column, Expr]
    errors: list[SemanticError]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def analyze(ast: SelectStatement, schema: Schema) -> AnalysisResult:
    """Menganalisis ``ast`` terhadap ``schema`` dan mengumpulkan seluruh error."""
    return _Analyzer(ast, schema).run()


def canonical_key(
    node: Expr,
    column_refs: Mapping[Column, str],
    alias_refs: Mapping[Column, Expr],
) -> str:
    """Bentuk kanonik posisi-independen dari ``node``.

    Dipakai (a) untuk mencocokkan kunci ``GROUP BY``/``DISTINCT`` dan (b) oleh
    tahap IR untuk mendeduplikasi ekspresi agregat yang bertulisan sama namun
    berada di posisi berbeda (mis. ``COUNT(*)`` pada ``SELECT`` dan ``HAVING``).
    Referensi kolom dipetakan ke nama internal dan alias di-*rewrite* ke
    ekspresi aslinya sehingga dua node berbeda yang bermakna sama menghasilkan
    string yang sama.
    """
    if isinstance(node, Column):
        if node in alias_refs:
            return canonical_key(alias_refs[node], column_refs, alias_refs)
        ref = column_refs.get(node)
        if ref:
            return ref
        return f"{node.table or ''}.{node.name}"
    if isinstance(node, Literal):
        return f"{node.kind}:{node.value!r}"
    if isinstance(node, UnaryOp):
        return f"({node.op} {canonical_key(node.operand, column_refs, alias_refs)})"
    if isinstance(node, BinaryOp):
        left = canonical_key(node.left, column_refs, alias_refs)
        right = canonical_key(node.right, column_refs, alias_refs)
        return f"({node.op} {left} {right})"
    if isinstance(node, Like):
        prefix = "NOT " if node.negated else ""
        operand = canonical_key(node.operand, column_refs, alias_refs)
        return f"({prefix}LIKE {operand} {node.pattern!r})"
    if isinstance(node, InList):
        prefix = "NOT " if node.negated else ""
        operand = canonical_key(node.operand, column_refs, alias_refs)
        values = " ".join(
            canonical_key(value, column_refs, alias_refs) for value in node.values
        )
        return f"({prefix}IN {operand} [{values}])"
    if isinstance(node, Between):
        prefix = "NOT " if node.negated else ""
        operand = canonical_key(node.operand, column_refs, alias_refs)
        low = canonical_key(node.low, column_refs, alias_refs)
        high = canonical_key(node.high, column_refs, alias_refs)
        return f"({prefix}BETWEEN {operand} {low} {high})"
    if isinstance(node, IsNull):
        prefix = "NOT " if node.negated else ""
        operand = canonical_key(node.operand, column_refs, alias_refs)
        return f"({prefix}IS NULL {operand})"
    if isinstance(node, FunctionCall):
        if isinstance(node.arg, Star):
            return f"{node.name}(*)"
        return f"{node.name}({canonical_key(node.arg, column_refs, alias_refs)})"
    if isinstance(node, Star):
        return "*"
    return "?"


def _suggest(word: str, candidates: list[str] | tuple[str, ...]) -> str:
    matches = difflib.get_close_matches(word.lower(), list(candidates), n=1, cutoff=0.6)
    if not matches:
        return ""
    return f" Maksudmu '{matches[0]}'?"


class _Analyzer:
    def __init__(self, ast: SelectStatement, schema: Schema) -> None:
        self._ast = ast
        self._schema = schema
        self.types: dict[Expr, DataType] = {}
        self.column_refs: dict[Column, str] = {}
        self.alias_refs: dict[Column, Expr] = {}
        self.errors: list[SemanticError] = []
        self.warnings: list[str] = []
        self._scope: dict[str, Table] = {}
        self._original_to_prefix: dict[str, str] = {}
        self._alias_index: dict[str, list[int]] = {}
        self._item_types: list[DataType | None] = []
        self._keys: set[tuple[str, int, int, str]] = set()

    # --- pelaporan --------------------------------------------------------

    def _err(
        self, code: SemCode, message: str, line: int, column: int, length: int = 1
    ) -> None:
        key = (str(code), line, column, message)
        if key in self._keys:
            return
        self._keys.add(key)
        self.errors.append(
            SemanticError(str(code), message, line, column, max(1, length), 0)
        )

    @staticmethod
    def _node_length(node: Node) -> int:
        if isinstance(node, Column):
            return max(1, len(node.name))
        if isinstance(node, FunctionCall):
            return max(1, len(node.name))
        if isinstance(node, Literal) and isinstance(node.value, str):
            return max(1, len(node.value) + 2)
        return 1

    def _err_node(self, code: SemCode, message: str, node: Node) -> None:
        self._err(code, message, node.line, node.column, self._node_length(node))

    # --- tahap 1: ruang nama ---------------------------------------------

    def _build_scope(self) -> None:
        refs = [self._ast.from_] + [join.table for join in self._ast.joins]
        for ref in refs:
            table = self._schema.get(ref.name)
            if table is None:
                self._err(
                    SemCode.TABLE_NOT_FOUND,
                    f"Tabel '{ref.name}' tidak ditemukan."
                    + _suggest(ref.name, self._schema.table_names),
                    ref.line,
                    ref.column,
                    max(1, len(ref.name)),
                )
                continue
            prefix = (ref.alias or ref.name).lower()
            if prefix in self._scope:
                self._err(
                    SemCode.DUPLICATE_ALIAS,
                    f"Alias '{prefix}' dipakai dua kali.",
                    ref.line,
                    ref.column,
                    max(1, len(prefix)),
                )
                continue
            self._scope[prefix] = table
            if ref.alias:
                self._original_to_prefix[ref.name.lower()] = prefix

    # --- tahap 2: JOIN ----------------------------------------------------

    @staticmethod
    def _flatten_and(expr: Expr) -> list[Expr]:
        if isinstance(expr, BinaryOp) and expr.op == "AND":
            return _Analyzer._flatten_and(expr.left) + _Analyzer._flatten_and(expr.right)
        return [expr]

    @staticmethod
    def _is_equi(expr: Expr) -> bool:
        return (
            isinstance(expr, BinaryOp)
            and expr.op == "="
            and isinstance(expr.left, Column)
            and isinstance(expr.right, Column)
        )

    def _process_joins(self) -> None:
        left_prefixes = {self._prefix_of(self._ast.from_)}
        for join in self._ast.joins:
            right_prefixes = {self._prefix_of(join.table)}
            self._visit(join.on, mode="TABLE", allow_aggregate=False, clause="ON")
            conjuncts = self._flatten_and(join.on)
            if not all(self._is_equi(item) for item in conjuncts):
                self._err_node(
                    SemCode.NON_EQUI_JOIN,
                    "ON hanya mendukung a.x = b.y [AND ...].",
                    join.on,
                )
            else:
                for item in conjuncts:
                    if not self._crosses_sides(item, left_prefixes, right_prefixes):
                        self._err_node(
                            SemCode.NON_EQUI_JOIN,
                            "ON hanya mendukung a.x = b.y [AND ...].",
                            item,
                        )
            left_prefixes |= right_prefixes

    @staticmethod
    def _prefix_of(ref: TableRef) -> str:
        return (ref.alias or ref.name).lower()

    def _ref_prefix(self, column: Column) -> str | None:
        ref = self.column_refs.get(column)
        if ref is not None:
            return ref.split(".", 1)[0]
        target = self.alias_refs.get(column)
        if isinstance(target, Column):
            nested = self.column_refs.get(target)
            if nested is not None:
                return nested.split(".", 1)[0]
        return None

    def _crosses_sides(self, item: Expr, left: set[str], right: set[str]) -> bool:
        if not (
            isinstance(item, BinaryOp)
            and isinstance(item.left, Column)
            and isinstance(item.right, Column)
        ):
            return False
        a = self._ref_prefix(item.left)
        b = self._ref_prefix(item.right)
        if a is None or b is None:
            # Kolom tak terpecahkan; SEM002/SEM003 sudah dilaporkan lebih dulu.
            return True
        return (a in left and b in right) or (b in left and a in right)

    def _validate_star(self, star: Star) -> None:
        """Memvalidasi kualifier ``alias.*`` dan menangkap kolom bertipe UNSUPPORTED."""
        if star.table is None:
            targets = list(self._scope.values())
        else:
            table = self._scope.get(star.table)
            if table is None:
                self._err(
                    SemCode.COLUMN_NOT_FOUND,
                    f"Kualifikasi '{star.table}' tidak ditemukan di FROM/JOIN."
                    + _suggest(star.table, list(self._scope)),
                    star.line,
                    star.column,
                    max(1, len(star.table)),
                )
                return
            targets = [table]
        for table in targets:
            for column in table.columns:
                if column.type is DataType.UNSUPPORTED:
                    label = (column.mysql_type or "UNSUPPORTED").upper()
                    self._err(
                        SemCode.UNSUPPORTED_COLUMN_TYPE,
                        f"Kolom '{column.name}' bertipe {label} belum didukung.",
                        star.line,
                        star.column,
                        max(1, len(star.table) + 2 if star.table else 1),
                    )

    # --- tahap 3: daftar SELECT ------------------------------------------

    def _process_select_items(self) -> None:
        for index, item in enumerate(self._ast.items):
            if isinstance(item, Star):
                self._validate_star(item)
                self._item_types.append(None)
                continue
            if item.alias:
                self._alias_index.setdefault(item.alias, []).append(index)
            item_type = self._visit(
                item.expr, mode="TABLE", allow_aggregate=True, clause="SELECT"
            )
            self._item_types.append(item_type)
            if item_type is DataType.BOOLEAN:
                self._err(
                    SemCode.BOOLEAN_IN_SELECT,
                    "Ekspresi boolean di SELECT belum didukung.",
                    item.line,
                    item.column,
                    max(1, len(item.text)),
                )

    # --- tahap 4: WHERE ---------------------------------------------------

    def _process_where(self) -> None:
        where = self._ast.where
        if where is None:
            return
        where_type = self._visit(
            where, mode="TABLE", allow_aggregate=False, clause="WHERE"
        )
        if where_type is not DataType.BOOLEAN:
            self._err(
                SemCode.NON_BOOLEAN_CONDITION,
                "Kondisi WHERE harus boolean.",
                where.line,
                where.column,
                self._node_length(where),
            )

    # --- tahap 5: GROUP BY ------------------------------------------------

    def _process_group_by(self) -> None:
        for expr in self._ast.group_by:
            if isinstance(expr, Literal) and expr.kind == "INT":
                self._err_node(
                    SemCode.ORDINAL_NOT_SUPPORTED,
                    "Nomor urut kolom tidak didukung; tulis nama kolom.",
                    expr,
                )
            self._visit(expr, mode="GROUP", allow_aggregate=False, clause="GROUP BY")

    # --- tahap 6: HAVING --------------------------------------------------

    def _process_having(self) -> None:
        having = self._ast.having
        if having is None:
            return
        if not self._ast.group_by:
            self._err(
                SemCode.HAVING_WITHOUT_GROUP_BY,
                "HAVING membutuhkan GROUP BY.",
                having.line,
                having.column,
                1,
            )
        having_type = self._visit(
            having, mode="GROUP", allow_aggregate=True, clause="HAVING"
        )
        if having_type is not DataType.BOOLEAN:
            self._err(
                SemCode.NON_BOOLEAN_CONDITION,
                "Kondisi HAVING harus boolean.",
                having.line,
                having.column,
                self._node_length(having),
            )

    # --- tahap 7: ORDER BY ------------------------------------------------

    def _process_order_by(self) -> None:
        for item in self._ast.order_by:
            if isinstance(item.expr, Literal) and item.expr.kind == "INT":
                self._err_node(
                    SemCode.ORDINAL_NOT_SUPPORTED,
                    "Nomor urut kolom tidak didukung; tulis nama kolom.",
                    item.expr,
                )
            self._visit(
                item.expr, mode="ORDER", allow_aggregate=True, clause="ORDER BY"
            )
        self._check_distinct_order()

    def _check_distinct_order(self) -> None:
        if not (self._ast.distinct and self._ast.order_by):
            return
        allowed = self._select_canonicals()
        for item in self._ast.order_by:
            if self._canonical(item.expr) not in allowed:
                self._err(
                    SemCode.COLUMN_NOT_FOUND,
                    "Ekspresi ORDER BY harus ada pada daftar SELECT saat memakai "
                    "DISTINCT (MySQL 8 error 3065).",
                    item.expr.line,
                    item.expr.column,
                    self._node_length(item.expr),
                )

    def _select_canonicals(self) -> set[str]:
        out: set[str] = set()
        for item in self._ast.items:
            if isinstance(item, Star):
                prefixes = [item.table] if item.table else list(self._scope)
                for prefix in prefixes:
                    table = self._scope.get(prefix)
                    if table is None:
                        continue
                    for column in table.columns:
                        out.add(f"{prefix}.{column.name}")
            else:
                out.add(self._canonical(item.expr))
        return out

    def _canonical(self, node: Expr) -> str:
        return canonical_key(node, self.column_refs, self.alias_refs)

    # --- tahap 8: aturan GROUP BY dan * ----------------------------------

    @staticmethod
    def _has_aggregate(node: Expr) -> bool:
        if isinstance(node, FunctionCall):
            if node.name in AGGREGATE_FUNCTIONS:
                return True
            return not isinstance(node.arg, Star) and _Analyzer._has_aggregate(node.arg)
        if isinstance(node, UnaryOp):
            return _Analyzer._has_aggregate(node.operand)
        if isinstance(node, BinaryOp):
            return _Analyzer._has_aggregate(node.left) or _Analyzer._has_aggregate(
                node.right
            )
        if isinstance(node, Like):
            return _Analyzer._has_aggregate(node.operand)
        if isinstance(node, InList):
            return any(_Analyzer._has_aggregate(value) for value in [node.operand, *node.values])
        if isinstance(node, Between):
            return any(
                _Analyzer._has_aggregate(part) for part in (node.operand, node.low, node.high)
            )
        if isinstance(node, IsNull):
            return _Analyzer._has_aggregate(node.operand)
        return False

    def _bare_columns(self, node: Expr | Star, inside: bool = False) -> list[Column]:
        """Kolom yang direferensikan ``node`` di luar agregat (untuk SEM007)."""
        if isinstance(node, Star):
            return []
        if isinstance(node, Column):
            if inside:
                return []
            target = self.alias_refs.get(node)
            if target is not None:
                return self._bare_columns(target, inside)
            return [node]
        if isinstance(node, Literal):
            return []
        if isinstance(node, UnaryOp):
            return self._bare_columns(node.operand, inside)
        if isinstance(node, BinaryOp):
            return self._bare_columns(node.left, inside) + self._bare_columns(
                node.right, inside
            )
        if isinstance(node, Like):
            return self._bare_columns(node.operand, inside)
        if isinstance(node, InList):
            out = self._bare_columns(node.operand, inside)
            for value in node.values:
                out += self._bare_columns(value, inside)
            return out
        if isinstance(node, Between):
            out = self._bare_columns(node.operand, inside)
            out += self._bare_columns(node.low, inside)
            out += self._bare_columns(node.high, inside)
            return out
        if isinstance(node, IsNull):
            return self._bare_columns(node.operand, inside)
        if isinstance(node, FunctionCall):
            if isinstance(node.arg, Star):
                return []
            return self._bare_columns(node.arg, inside or node.name in AGGREGATE_FUNCTIONS)
        return []

    def _check_group_by_rule(self) -> None:
        has_star = any(isinstance(item, Star) for item in self._ast.items)
        candidates: list[Expr] = [
            item.expr for item in self._ast.items if isinstance(item, SelectItem)
        ]
        if self._ast.having is not None:
            candidates.append(self._ast.having)
        candidates += [item.expr for item in self._ast.order_by]
        has_aggregate = any(self._has_aggregate(expr) for expr in candidates)
        enforce = bool(self._ast.group_by) or has_aggregate

        if has_star and enforce:
            star = next(item for item in self._ast.items if isinstance(item, Star))
            self._err(
                SemCode.STAR_WITH_GROUP_BY,
                "SELECT * tidak boleh dengan GROUP BY.",
                star.line,
                star.column,
                1,
            )
        if not enforce:
            return
        if any(
            isinstance(expr, Literal) and expr.kind == "INT" for expr in self._ast.group_by
        ):
            # SEM014 sudah dilaporkan; kunci ordinal tak bisa dicocokkan dengan kolom.
            return

        group_keys = {self._canonical(expr) for expr in self._ast.group_by}
        for expr in candidates:
            if self._canonical(expr) in group_keys:
                # Ekspresi itu sendiri adalah kunci GROUP BY, mis.
                # `SELECT sks + 1 ... GROUP BY sks + 1` (MySQL mengizinkannya).
                continue
            for column in self._bare_columns(expr):
                if not self._is_resolved(column):
                    continue
                if self._canonical(column) in group_keys:
                    continue
                self._err_node(
                    SemCode.GROUP_BY_REQUIRED,
                    f"Kolom '{column.name}' harus ada di GROUP BY atau dibungkus agregat.",
                    column,
                )

    def _is_resolved(self, column: Column) -> bool:
        return column in self.column_refs or column in self.alias_refs

    # --- resolusi nama ----------------------------------------------------

    def _resolve_qualified(self, col: Column) -> str | None:
        assert col.table is not None
        prefix = col.table
        table = self._scope.get(prefix)
        if table is not None:
            if table.get(col.name) is None:
                self._err(
                    SemCode.COLUMN_NOT_FOUND,
                    f"Kolom '{col.name}' tidak ditemukan di '{prefix}'."
                    + _suggest(col.name, table.column_names()),
                    col.line,
                    col.column,
                    max(1, len(col.name)),
                )
                return None
            return f"{prefix}.{col.name}"
        if prefix in self._original_to_prefix:
            owner = self._original_to_prefix[prefix]
            self._err(
                SemCode.COLUMN_NOT_FOUND,
                f"Tabel '{prefix}' memiliki alias '{owner}'; gunakan "
                f"'{owner}.{col.name}' sebagai gantinya.",
                col.line,
                col.column,
                max(1, len(col.name)),
            )
            return None
        self._err(
            SemCode.COLUMN_NOT_FOUND,
            f"Referensi '{prefix}.{col.name}' tidak ditemukan; '{prefix}' bukan tabel "
            f"atau alias pada query ini."
            + _suggest(prefix, list(self._scope)),
            col.line,
            col.column,
            max(1, len(prefix)),
        )
        return None

    def _resolve_alias(self, col: Column) -> tuple[bool, str | None]:
        """Mencoba merujuk kolom ke alias SELECT.

        Returns:
            ``(handled, ref)``; ``handled=False`` berarti tidak ada alias yang
            cocok dan pencarian harus dilanjutkan ke kolom tabel.
        """
        indices = self._alias_index.get(col.name, [])
        if not indices:
            return False, None
        if len(indices) > 1:
            self._err(
                SemCode.AMBIGUOUS_COLUMN,
                f"Kolom '{col.name}' ambigu: alias SELECT '{col.name}' didefinisikan "
                f"{len(indices)} kali.",
                col.line,
                col.column,
                max(1, len(col.name)),
            )
            return True, None
        index = indices[0]
        item = self._ast.items[index]
        assert isinstance(item, SelectItem)
        self.alias_refs[col] = item.expr
        return True, f"alias:{index}"

    def _resolve_unqualified(self, col: Column, mode: str) -> str | None:
        if not self._scope:
            return None
        name = col.name
        if mode == "ORDER":
            handled, ref = self._resolve_alias(col)
            if handled:
                return ref

        found = [prefix for prefix, table in self._scope.items() if table.get(name)]
        if len(found) > 1:
            self._err(
                SemCode.AMBIGUOUS_COLUMN,
                f"Kolom '{name}' ambigu: ada di {' dan '.join(sorted(found))}.",
                col.line,
                col.column,
                max(1, len(name)),
            )
            return None
        if len(found) == 1:
            return f"{found[0]}.{name}"

        if mode == "GROUP":
            handled, ref = self._resolve_alias(col)
            if handled:
                return ref

        if len(self._scope) == 1:
            prefix, table = next(iter(self._scope.items()))
            message = f"Kolom '{name}' tidak ditemukan di '{prefix}'." + _suggest(
                name, table.column_names()
            )
        else:
            all_names: list[str] = []
            for table in self._scope.values():
                all_names.extend(table.column_names())
            message = f"Kolom '{name}' tidak ditemukan di FROM/JOIN." + _suggest(
                name, all_names
            )
        self._err(SemCode.COLUMN_NOT_FOUND, message, col.line, col.column, max(1, len(name)))
        return None

    # --- inferensi tipe ---------------------------------------------------

    @staticmethod
    def _is_numeric(tipe: DataType) -> bool:
        return tipe in _NUMERIC

    @staticmethod
    def _comparable(left: DataType, right: DataType) -> bool:
        if left is DataType.NULL or right is DataType.NULL:
            return True
        if DataType.UNSUPPORTED in (left, right):
            return True
        if left in _NUMERIC and right in _NUMERIC:
            return True
        return left in _TEXTUAL and right in _TEXTUAL

    def _visit(
        self, node: Expr, *, mode: str, allow_aggregate: bool, clause: str
    ) -> DataType:
        if node in self.types:
            return self.types[node]
        if isinstance(node, Literal):
            tipe = _LITERAL_TYPES[node.kind]
        elif isinstance(node, Column):
            tipe = self._visit_column(node, mode)
        elif isinstance(node, UnaryOp):
            tipe = self._visit_unary(node, mode, allow_aggregate, clause)
        elif isinstance(node, BinaryOp):
            tipe = self._visit_binary(node, mode, allow_aggregate, clause)
        elif isinstance(node, Like):
            tipe = self._visit_like(node, mode, allow_aggregate, clause)
        elif isinstance(node, InList):
            tipe = self._visit_in(node, mode, allow_aggregate, clause)
        elif isinstance(node, Between):
            tipe = self._visit_between(node, mode, allow_aggregate, clause)
        elif isinstance(node, IsNull):
            self._visit(node.operand, mode=mode, allow_aggregate=allow_aggregate, clause=clause)
            tipe = DataType.BOOLEAN
        elif isinstance(node, FunctionCall):
            tipe = self._visit_function(node, mode, allow_aggregate, clause)
        else:  # pragma: no cover - node tak dikenal
            tipe = DataType.INT
        self.types[node] = tipe
        return tipe

    def _visit_column(self, col: Column, mode: str) -> DataType:
        if not self._scope:
            return DataType.INT
        if col.table is not None:
            ref = self._resolve_qualified(col)
        else:
            ref = self._resolve_unqualified(col, mode)
        if ref is None:
            return DataType.INT
        if ref.startswith("alias:"):
            index = int(ref.split(":", 1)[1])
            item_type = self._item_types[index]
            return DataType.INT if item_type is None else item_type
        self.column_refs[col] = ref
        prefix, _, name = ref.partition(".")
        table = self._scope[prefix]
        column = table.get(name)
        if column is None:  # pragma: no cover - tak mungkin terjadi
            return DataType.INT
        if column.type is DataType.UNSUPPORTED:
            label = (column.mysql_type or "UNSUPPORTED").upper()
            self._err_node(
                SemCode.UNSUPPORTED_COLUMN_TYPE,
                f"Kolom '{column.name}' bertipe {label} belum didukung.",
                col,
            )
            return DataType.UNSUPPORTED
        return column.type

    def _child(self, node: Expr, mode: str, allow_aggregate: bool, clause: str) -> DataType:
        return self._visit(node, mode=mode, allow_aggregate=allow_aggregate, clause=clause)

    def _visit_unary(
        self, node: UnaryOp, mode: str, allow_aggregate: bool, clause: str
    ) -> DataType:
        tipe = self._child(node.operand, mode, allow_aggregate, clause)
        if node.op == "NOT":
            if tipe not in (DataType.BOOLEAN, DataType.NULL, DataType.UNSUPPORTED):
                self._err_node(
                    SemCode.TYPE_MISMATCH,
                    f"Operator 'NOT' membutuhkan operand boolean; ditemukan {tipe}.",
                    node.operand,
                )
            return DataType.BOOLEAN
        if not self._is_numeric(tipe) and tipe not in (
            DataType.NULL,
            DataType.UNSUPPORTED,
        ):
            self._err_node(
                SemCode.TYPE_MISMATCH,
                f"Operator unary '{node.op}' membutuhkan operand numerik; "
                f"ditemukan {tipe}.",
                node.operand,
            )
            return DataType.INT
        return tipe

    def _visit_binary(
        self, node: BinaryOp, mode: str, allow_aggregate: bool, clause: str
    ) -> DataType:
        left = self._child(node.left, mode, allow_aggregate, clause)
        right = self._child(node.right, mode, allow_aggregate, clause)
        op = node.op

        if op in ("AND", "OR"):
            for side, value in ((node.left, left), (node.right, right)):
                if value not in (DataType.BOOLEAN, DataType.NULL, DataType.UNSUPPORTED):
                    self._err_node(
                        SemCode.TYPE_MISMATCH,
                        f"Operator '{op}' membutuhkan operand boolean; ditemukan {value}.",
                        side,
                    )
            return DataType.BOOLEAN

        if op in ("=", "<>", "<", ">", "<=", ">="):
            if not self._comparable(left, right):
                self._err_node(
                    SemCode.TYPE_MISMATCH,
                    f"Tidak bisa membandingkan {left} dengan {right}.",
                    node,
                )
            return DataType.BOOLEAN

        if op in ("+", "-", "*"):
            for side, value in ((node.left, left), (node.right, right)):
                if not self._is_numeric(value) and value not in (
                    DataType.NULL,
                    DataType.UNSUPPORTED,
                ):
                    self._err_node(
                        SemCode.TYPE_MISMATCH,
                        f"Operator '{op}' membutuhkan operand numerik; ditemukan {value}.",
                        side,
                    )
                    return DataType.INT
            if DataType.UNSUPPORTED in (left, right):
                return DataType.UNSUPPORTED
            defined = [side for side in (left, right) if side is not DataType.NULL]
            if not defined:
                return DataType.NULL
            if len(defined) == 1:
                return defined[0]
            if defined[0] is DataType.INT and defined[1] is DataType.INT:
                return DataType.INT
            return DataType.DOUBLE

        if op == "/":
            for side, value in ((node.left, left), (node.right, right)):
                if not self._is_numeric(value) and value not in (
                    DataType.NULL,
                    DataType.UNSUPPORTED,
                ):
                    self._err_node(
                        SemCode.TYPE_MISMATCH,
                        f"Operator '/' membutuhkan operand numerik; ditemukan {value}.",
                        side,
                    )
                    return DataType.DOUBLE
            if DataType.UNSUPPORTED in (left, right):
                return DataType.UNSUPPORTED
            return DataType.DOUBLE

        # DIV dan %
        for side, value in ((node.left, left), (node.right, right)):
            if value is not DataType.INT and value not in (
                DataType.NULL,
                DataType.UNSUPPORTED,
            ):
                self._err_node(
                    SemCode.TYPE_MISMATCH,
                    f"Operator '{op}' membutuhkan operand INT; ditemukan {value}.",
                    side,
                )
                return DataType.INT
        if DataType.UNSUPPORTED in (left, right):
            return DataType.UNSUPPORTED
        return DataType.INT

    def _visit_like(
        self, node: Like, mode: str, allow_aggregate: bool, clause: str
    ) -> DataType:
        tipe = self._child(node.operand, mode, allow_aggregate, clause)
        if tipe not in (DataType.TEXT, DataType.NULL, DataType.UNSUPPORTED):
            self._err_node(
                SemCode.TYPE_MISMATCH,
                f"LIKE membutuhkan operand TEXT; ditemukan {tipe}.",
                node.operand,
            )
        return DataType.BOOLEAN

    def _visit_in(
        self, node: InList, mode: str, allow_aggregate: bool, clause: str
    ) -> DataType:
        operand = self._child(node.operand, mode, allow_aggregate, clause)
        for value in node.values:
            tipe = self._child(value, mode, allow_aggregate, clause)
            if not self._comparable(operand, tipe):
                self._err_node(
                    SemCode.TYPE_MISMATCH,
                    f"Tidak bisa membandingkan {operand} dengan {tipe}.",
                    value,
                )
        return DataType.BOOLEAN

    def _visit_between(
        self, node: Between, mode: str, allow_aggregate: bool, clause: str
    ) -> DataType:
        operand = self._child(node.operand, mode, allow_aggregate, clause)
        for bound in (node.low, node.high):
            tipe = self._child(bound, mode, allow_aggregate, clause)
            if not self._comparable(operand, tipe):
                self._err_node(
                    SemCode.TYPE_MISMATCH,
                    f"Tidak bisa membandingkan {operand} dengan {tipe}.",
                    bound,
                )
        return DataType.BOOLEAN

    def _visit_function(
        self, node: FunctionCall, mode: str, allow_aggregate: bool, clause: str
    ) -> DataType:
        name = node.name
        if name not in AGGREGATE_FUNCTIONS:
            if not isinstance(node.arg, Star):
                self._child(node.arg, mode, allow_aggregate, clause)
            self._err_node(
                SemCode.UNKNOWN_FUNCTION,
                f"Fungsi '{name}' tidak didukung.",
                node,
            )
            return DataType.INT

        if not allow_aggregate:
            self._err_node(
                SemCode.AGGREGATE_NOT_ALLOWED,
                f"Agregat tidak boleh dipakai di {clause}.",
                node,
            )

        if isinstance(node.arg, Star):
            if name != "count":
                self._err_node(
                    SemCode.INVALID_FUNCTION_ARGUMENT,
                    f"{name.upper()}(*) tidak didukung; hanya COUNT(*) yang boleh "
                    f"memakai '*'.",
                    node,
                )
            return DataType.INT

        arg_type = self._child(node.arg, mode, allow_aggregate, clause)

        if name == "count":
            return DataType.INT
        if name == "sum":
            if not self._is_numeric(arg_type):
                if arg_type not in (DataType.NULL, DataType.UNSUPPORTED):
                    self._err_node(
                        SemCode.INVALID_FUNCTION_ARGUMENT,
                        "SUM membutuhkan argumen numerik.",
                        node,
                    )
                return DataType.INT
            return arg_type
        if name == "avg":
            if not self._is_numeric(arg_type):
                if arg_type not in (DataType.NULL, DataType.UNSUPPORTED):
                    self._err_node(
                        SemCode.INVALID_FUNCTION_ARGUMENT,
                        "AVG membutuhkan argumen numerik.",
                        node,
                    )
                return DataType.DOUBLE
            return DataType.DOUBLE
        # min / max
        if not self._is_numeric(arg_type) and arg_type not in (
            DataType.TEXT,
            DataType.NULL,
            DataType.UNSUPPORTED,
        ):
            self._err_node(
                SemCode.INVALID_FUNCTION_ARGUMENT,
                f"{name.upper()} membutuhkan argumen numerik atau TEXT.",
                node,
            )
            return DataType.INT
        return arg_type

    # --- eksekusi ---------------------------------------------------------

    def run(self) -> AnalysisResult:
        self._build_scope()
        self._process_joins()
        self._process_select_items()
        self._process_where()
        self._process_group_by()
        self._process_having()
        self._process_order_by()
        self._check_group_by_rule()
        self.errors.sort(key=lambda error: (error.line, error.column, error.code))
        return AnalysisResult(
            ast=self._ast,
            schema=self._schema,
            types=self.types,
            column_refs=self.column_refs,
            alias_refs=self.alias_refs,
            errors=self.errors,
            warnings=self.warnings,
        )


_LITERAL_TYPES: dict[str, DataType] = {
    "INT": DataType.INT,
    "DOUBLE": DataType.DOUBLE,
    "TEXT": DataType.TEXT,
    "NULL": DataType.NULL,
}
