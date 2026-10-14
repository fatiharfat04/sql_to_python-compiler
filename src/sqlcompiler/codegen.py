"""Code generator: mengubah ``Plan`` menjadi kode Python/pandas (``SPEC.md`` §12, §13).

Konvensi yang dipatuhi:

- satu fungsi ``run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame``;
- setiap blok diawali komentar ``# SQL: <potongan sumber>``;
- fungsi bantu hanya dihasilkan bila dipakai
  (``_idiv``, ``_imod``, ``_rdec``, ``_tmin``, ``_tmax``);
- deterministik: sumber yang sama menghasilkan teks identik;
- kembalian selalu ``.reset_index(drop=True)``.
"""

from __future__ import annotations

import re
from typing import Any

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
    Star,
    TableRef,
    UnaryOp,
)
from .ir import (
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
    format_expr,
    literal_scale,
    round_half_away,
)
from .semantic import AnalysisResult, DataType, Schema

__all__ = ["generate_code"]

#: Lebar baris maksimum kode hasil (selaras dengan ``ruff`` di ``pyproject.toml``).
_MAX_WIDTH = 100

_LITERAL_TO_TYPE: dict[str, DataType] = {
    "INT": DataType.INT,
    "DOUBLE": DataType.DOUBLE,
    "TEXT": DataType.TEXT,
    "NULL": DataType.NULL,
}

_COMPARISON = frozenset({"=", "<>", "<", ">", "<=", ">="})
_PY_COMPARISON = {"=": "==", "<>": "!="}
#: Akhiran yang menyamakan dtype boolean hasil perbandingan (lihat ``_needs_bool_cast``).
_BOOL_CAST = ".astype('boolean')"
_ARITHMETIC = frozenset({"+", "-", "*", "/", "DIV", "%"})
_DIVISIONS = frozenset({"/", "DIV", "%"})


class _NotStatic:
    """Penanda bahwa nilai sebuah ekspresi hanya dapat diketahui saat runtime."""

    def __repr__(self) -> str:  # pragma: no cover - hanya untuk pesan debug
        return "<tidak-statis>"


_NOT_STATIC = _NotStatic()


# --------------------------------------------------------------------------- #
# Fungsi bantu yang dihasilkan ke dalam kode                                   #
# --------------------------------------------------------------------------- #

_HELPERS: dict[str, str] = {
    "_idiv": '''def _idiv(a, b):
    """DIV ala MySQL: hasil dipotong ke arah nol; NULL/pembagi 0 -> NULL."""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.Series([_idiv(x, y) for x, y in zip(a, b)], index=a.index, dtype="Int64")
    if isinstance(a, pd.Series):
        return pd.Series([_idiv(x, b) for x in a], index=a.index, dtype="Int64")
    if isinstance(b, pd.Series):
        return pd.Series([_idiv(a, y) for y in b], index=b.index, dtype="Int64")
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q''',
    "_imod": '''def _imod(a, b):
    """Sisa bagi ala MySQL: tandanya mengikuti pembilang; pembagi 0 -> NULL."""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.Series([_imod(x, y) for x, y in zip(a, b)], index=a.index, dtype="Int64")
    if isinstance(a, pd.Series):
        return pd.Series([_imod(x, b) for x in a], index=a.index, dtype="Int64")
    if isinstance(b, pd.Series):
        return pd.Series([_imod(a, y) for y in b], index=b.index, dtype="Int64")
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    r = abs(a) % abs(b)
    return r if a >= 0 else -r''',
    "_rdec": '''def _rdec(s, scale=4):
    """Pembulatan setengah ke luar (away from zero) ala DECIMAL MySQL."""
    factor = 10 ** scale
    offset = (s >= 0).astype("Float64") * 2 - 1
    return (s * factor + offset * 0.5).astype("Int64") / factor''',
    "_tmin": '''def _tmin(s):
    """MIN kolom teks: tidak membedakan huruf besar/kecil, mengabaikan NULL."""
    best = None
    for value in s:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            continue
        key = str(value).lower()
        if best is None or key < best[0]:
            best = (key, value)
    return None if best is None else best[1]''',
    "_tmax": '''def _tmax(s):
    """MAX kolom teks: tidak membedakan huruf besar/kecil, mengabaikan NULL."""
    best = None
    for value in s:
        if value is None or (not isinstance(value, str) and pd.isna(value)):
            continue
        key = str(value).lower()
        if best is None or key > best[0]:
            best = (key, value)
    return None if best is None else best[1]''',
}

_HELPER_ORDER = ("_idiv", "_imod", "_rdec", "_tmin", "_tmax")


# --------------------------------------------------------------------------- #
# Utilitas                                                                     #
# --------------------------------------------------------------------------- #


def _py_str(value: str) -> str:
    """Literals string Python yang aman (menghindari escape tak terlihat)."""
    if "'" not in value and '"' not in value and "\\" not in value:
        return f'"{value}"'
    return repr(value)


def _py_raw(value: str) -> str:
    """Literals string mentah ``r"..."`` bila aman, selain itu ``repr``."""
    if '"' not in value and not value.endswith("\\"):
        return f'r"{value}"'
    return repr(value)


def _like_to_regex(pattern: str) -> str:
    """Terjemahkan pola LIKE MySQL menjadi regex ``(?is)\\A...\\Z`` (``SPEC.md`` §10.6)."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            out.append(re.escape(pattern[index + 1]))
            index += 2
            continue
        if char == "%":
            out.append(".*")
        elif char == "_":
            out.append(".")
        else:
            out.append(re.escape(char))
        index += 1
    return "(?is)\\A" + "".join(out) + "\\Z"


def _strip_parens(text: str) -> str:
    """Hapus sepasang tanda kurung pembungkus bila seluruhnya membungkus."""
    if not text.startswith("(") or not text.endswith(")"):
        return text
    depth = 0
    for position, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and position != len(text) - 1:
                return text
    return text[1:-1] if depth == 0 else text


def _sql_text(expr: Expr | Star) -> str:
    """Bentuk sumber yang lebih mirip SQL untuk komentar (``SPEC.md`` §13)."""
    text = _strip_parens(format_expr(expr))
    return re.sub(
        r"\b(count|sum|avg|min|max)\(",
        lambda match: match.group(1).upper() + "(",
        text,
    )


def _is_wrapped(text: str) -> bool:
    """True bila ``text`` seluruhnya dibungkus satu pasang tanda kurung."""
    if not text.startswith("("):
        return False
    depth = 0
    for position, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return position == len(text) - 1
    return False


def _split_top(text: str, operator: str) -> list[str]:
    """Pecah ``text`` pada ``operator`` di tingkat kurung/kuotasi terluar."""
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == operator and depth == 0:
            parts.append(text[start:index].strip())
            start = index + len(operator)
        index += 1
    if not parts:
        return []
    parts.append(text[start:].strip())
    return parts


def _as_boolean(text: str) -> str:
    """Bungkus ``text`` agar bertipe ``boolean`` ter-mask tanpa mengubah nilai."""
    return f"{text}{_BOOL_CAST}" if _is_wrapped(text) else f"({text}){_BOOL_CAST}"


def _breakable(term: str) -> tuple[str, str, str, list[str]] | None:
    """Bongkar pembungkus ``term`` menjadi ``(buka, tutup, operator, bagian)`` bila memungkinkan."""
    cast = ""
    without_cast = term[: -len(_BOOL_CAST)] if term.endswith(_BOOL_CAST) else ""
    if without_cast and _is_wrapped(without_cast):
        # Akhiran penyatuan dtype melekat pada kurung pembungkus, bukan pada
        # bagian terakhir, sehingga `close_text` yang harus membawanya.
        core, cast = _strip_parens(without_cast), _BOOL_CAST
    else:
        core = _strip_parens(term)
    open_text, close_text = "(", ")"
    if core.startswith("~(") and core.endswith(")"):
        open_text, close_text = "~(", ")"
        core = core[2:-1]
    for operator in ("|", "&"):
        parts = _split_top(core, operator)
        if len(parts) >= 2:
            return open_text, close_text + cast, operator, parts
    return None


def _truthy(value: Any) -> Any:
    """Konversi nilai statis ke boolean Kleene (``None`` = NULL)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _NOT_STATIC


def _predicate_truth(value: Any) -> bool | None:
    """Bandingkan nilai statis predikat: hanya ``True`` yang meloloskan baris."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value != ""
    return None


def _kleene_and(left: Any, right: Any) -> Any:
    if left is False or right is False:
        return False
    if left is None or right is None:
        return None
    if left is _NOT_STATIC or right is _NOT_STATIC:
        return _NOT_STATIC
    return bool(left) and bool(right)


def _kleene_or(left: Any, right: Any) -> Any:
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    if left is _NOT_STATIC or right is _NOT_STATIC:
        return _NOT_STATIC
    return bool(left) or bool(right)


def _static_operator(op: str, left: Any, right: Any) -> Any:
    """Hitung hasil operator pada dua nilai statis, atau ``_NOT_STATIC``."""
    if left is None or right is None:
        return None
    if op in _ARITHMETIC:
        if isinstance(left, bool) or isinstance(right, bool):
            return _NOT_STATIC
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            return _NOT_STATIC
        if op in _DIVISIONS and right == 0:
            return None
        if op in ("DIV", "%") and not (isinstance(left, int) and isinstance(right, int)):
            return _NOT_STATIC
        try:
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                # MySQL: DECIMAL/DECIMAL → skala operand kiri + 4 desimal,
                # dibulatkan setengah ke luar (bukan setengah-genap).
                return round_half_away(left / right, literal_scale(left) + 4)
            if op == "DIV":
                quotient = abs(left) // abs(right)
                return -quotient if (left < 0) != (right < 0) else quotient
            remainder = abs(left) % abs(right)
            return remainder if left >= 0 else -remainder
        except (TypeError, ValueError, ZeroDivisionError):  # pragma: no cover
            return _NOT_STATIC
    if op in _COMPARISON:
        if isinstance(left, str) or isinstance(right, str):
            if not (isinstance(left, str) and isinstance(right, str)):
                return _NOT_STATIC
            left, right = left.lower(), right.lower()
        try:
            if op == "=":
                return left == right
            if op == "<>":
                return left != right
            if op == "<":
                return left < right
            if op == ">":
                return left > right
            if op == "<=":
                return left <= right
            return left >= right
        except TypeError:  # pragma: no cover
            return _NOT_STATIC
    return _NOT_STATIC


def _static_value(expr: Expr) -> Any:
    """Hitung nilai ``expr`` bila seluruh daunnya literal, selain itu ``_NOT_STATIC``."""
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, Column):
        return _NOT_STATIC
    if isinstance(expr, UnaryOp):
        operand = _static_value(expr.operand)
        if operand is _NOT_STATIC:
            return _NOT_STATIC
        if expr.op == "NOT":
            truth = _truthy(operand)
            if truth is _NOT_STATIC:
                return _NOT_STATIC
            return None if truth is None else not truth
        if operand is None or isinstance(operand, bool):
            return _NOT_STATIC
        if not isinstance(operand, (int, float)):
            return _NOT_STATIC
        return -operand if expr.op == "-" else operand
    if isinstance(expr, BinaryOp):
        left = _static_value(expr.left)
        if left is _NOT_STATIC:
            return _NOT_STATIC
        if expr.op == "AND":
            return _kleene_and(_truthy(left), _truthy(_static_value(expr.right)))
        if expr.op == "OR":
            return _kleene_or(_truthy(left), _truthy(_static_value(expr.right)))
        right = _static_value(expr.right)
        if right is _NOT_STATIC:
            return _NOT_STATIC
        return _static_operator(expr.op, left, right)
    if isinstance(expr, IsNull):
        operand = _static_value(expr.operand)
        if operand is _NOT_STATIC:
            return _NOT_STATIC
        found = operand is None
        return (not found) if expr.negated else found
    if isinstance(expr, Like):
        operand = _static_value(expr.operand)
        if not isinstance(operand, str):
            return _NOT_STATIC
        matched = re.match(_like_to_regex(expr.pattern), operand) is not None
        return (not matched) if expr.negated else matched
    if isinstance(expr, InList):
        return _static_in(expr)
    if isinstance(expr, Between):
        return _static_between(expr)
    return _NOT_STATIC


def _static_in(expr: InList) -> Any:
    operand = _static_value(expr.operand)
    if operand is _NOT_STATIC:
        return _NOT_STATIC
    result: Any = False
    for value in expr.values:
        static = _static_value(value)
        if static is _NOT_STATIC:
            return _NOT_STATIC
        if operand is None or static is None:
            member: Any = None
        else:
            equal = _static_operator("=", operand, static)
            if equal is _NOT_STATIC:
                return _NOT_STATIC
            member = None if equal is None else bool(equal)
        if member is True:
            result = True
        elif member is None and result is not True:
            result = None
    if result is None:
        return None
    return (not result) if expr.negated else result


def _static_between(expr: Between) -> Any:
    operand = _static_value(expr.operand)
    low = _static_value(expr.low)
    high = _static_value(expr.high)
    if operand is _NOT_STATIC or low is _NOT_STATIC or high is _NOT_STATIC:
        return _NOT_STATIC
    if operand is None or low is None or high is None:
        return None
    above = _static_operator(">=", operand, low)
    below = _static_operator("<=", operand, high)
    if above is _NOT_STATIC or below is _NOT_STATIC:
        return _NOT_STATIC
    if above is None or below is None:
        return None
    inside = bool(above) and bool(below)
    if inside:
        return not expr.negated
    return expr.negated


def _is_scalar(expr: Expr) -> bool:
    """True bila ekspresi bernilai skalar Python (bukan ``Series``)."""
    value = _static_value(expr)
    return value is not _NOT_STATIC and value is not None


def _flatten_and(expr: Expr) -> list[Expr]:
    if isinstance(expr, BinaryOp) and expr.op == "AND":
        return _flatten_and(expr.left) + _flatten_and(expr.right)
    return [expr]


def _dtype_name(data_type: DataType | None) -> str | None:
    if data_type is DataType.INT:
        return "Int64"
    if data_type is DataType.DOUBLE:
        return "Float64"
    if data_type is DataType.TEXT:
        return "string"
    return None


# --------------------------------------------------------------------------- #
# Generator                                                                    #
# --------------------------------------------------------------------------- #


def generate_code(plan: Plan, analysis: AnalysisResult) -> str:
    """Kompilasi ``plan`` menjadi kode Python yang mandiri (``SPEC.md`` §13)."""
    return _Generator(plan, analysis).generate()


class _Generator:
    def __init__(self, plan: Plan, analysis: AnalysisResult) -> None:
        self.plan = plan
        self.analysis = analysis
        self.ast = analysis.ast
        self.schema: Schema = analysis.schema
        self.lines: list[str] = []
        self.helpers: set[str] = set()
        self.join_index = 0
        self.refs: list[TableRef] = [self.ast.from_]
        self.refs.extend(join.table for join in self.ast.joins)
        self.prefix_table: dict[str, str] = {
            (ref.alias or ref.name).lower(): ref.name for ref in self.refs
        }
        self.agg_types: dict[str, DataType] = {}
        self.group_types: dict[str, DataType] = {}
        self.project_items: list[tuple[Expr, str]] = []
        self._last_comment: str | None = None
        aggregate = _find(plan.root, Aggregate)
        if isinstance(aggregate, Aggregate):
            for name, expr in aggregate.group_keys:
                self.group_types[name] = self._type_of(expr)
            for name, func, arg in aggregate.aggs:
                if func == "count" or isinstance(arg, Star):
                    self.agg_types[name] = DataType.INT
                elif func == "avg":
                    self.agg_types[name] = DataType.DOUBLE
                else:
                    self.agg_types[name] = self._type_of(arg)

    # --- tipe ---------------------------------------------------------------

    def _column_type(self, name: str) -> DataType:
        if name in self.agg_types:
            return self.agg_types[name]
        if name in self.group_types:
            return self.group_types[name]
        prefix, dot, column = name.partition(".")
        if not dot:
            return DataType.UNSUPPORTED
        table_name = self.prefix_table.get(prefix)
        if table_name is None:
            return DataType.UNSUPPORTED
        table = self.schema.get(table_name)
        if table is None:
            return DataType.UNSUPPORTED
        found = table.get(column)
        return found.type if found is not None else DataType.UNSUPPORTED

    def _type_of(self, expr: Expr) -> DataType:
        if isinstance(expr, Literal):
            return _LITERAL_TO_TYPE[expr.kind]
        if isinstance(expr, Column):
            return self._column_type(expr.name)
        if isinstance(expr, UnaryOp):
            return DataType.BOOLEAN if expr.op == "NOT" else self._type_of(expr.operand)
        if isinstance(expr, (IsNull, Like, Between, InList)):
            return DataType.BOOLEAN
        if isinstance(expr, FunctionCall):
            return DataType.INT
        if isinstance(expr, BinaryOp):
            op = expr.op
            if op in _COMPARISON or op in ("AND", "OR"):
                return DataType.BOOLEAN
            if op in ("DIV", "%"):
                return DataType.INT
            if op == "/":
                return DataType.DOUBLE
            left = self._type_of(expr.left)
            right = self._type_of(expr.right)
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
        return DataType.UNSUPPORTED

    # --- ekspresi -----------------------------------------------------------

    def _null(self, var: str) -> str:
        return f"pd.Series([pd.NA] * len({var}), index={var}.index)"

    def _plain(self, expr: Expr, var: str) -> str:
        """Sumber ekspresi untuk diakses atributnya (``.isna()``, ``.str``)."""
        source = self._expr(expr, var)
        if isinstance(expr, Column) or _is_wrapped(source):
            return source
        return f"({source})"

    def _text_operand(self, expr: Expr, var: str) -> str:
        if isinstance(expr, Literal) and isinstance(expr.value, str):
            return _py_str(expr.value.lower())
        return f"{self._plain(expr, var)}.str.lower()"

    def _comparison(self, node: BinaryOp, var: str) -> str:
        if _static_value(node.left) is None or _static_value(node.right) is None:
            return self._null(var)
        left_type = self._type_of(node.left)
        right_type = self._type_of(node.right)
        if left_type is DataType.TEXT or right_type is DataType.TEXT:
            left = self._text_operand(node.left, var)
            right = self._text_operand(node.right, var)
        else:
            left = self._expr(node.left, var)
            right = self._expr(node.right, var)
        operator = _PY_COMPARISON.get(node.op, node.op)
        return f"({left} {operator} {right})"

    def _division(self, node: BinaryOp, var: str) -> str:
        left = self._expr(node.left, var)
        right = self._expr(node.right, var)
        static = _static_value(node.right)
        divisor = f"({right})"
        if not (isinstance(static, (int, float)) and not isinstance(static, bool)
                and static != 0):
            divisor = f"({right}).where({right} != 0)"
        result = f"({left} / {divisor})"
        if not self._is_double_expr(node.left) and not self._is_double_expr(node.right):
            # MySQL: tanpa operand DOUBLE sungguhan, hasilnya DECIMAL berskala
            # kiri + 4 desimal dibulatkan setengah ke luar (`sks / 32` → 0.0938,
            # `sks / 3.0` → 1.3333, bukan 1.3333333333333333).
            self.helpers.add("_rdec")
            result = f"_rdec({result}, {self._decimal_scale(node.left) + 4})"
        return result

    def _is_double_expr(self, expr: Expr) -> bool:
        """True bila ``expr`` membawa tipe DOUBLE sungguhan, bukan literal desimal.

        MySQL membedakan ``DECIMAL`` (literal seperti ``3.0``) dan ``DOUBLE``
        (kolom ``DOUBLE``); hanya yang kedua menghasilkan pembagian presisi penuh
        tanpa pembulatan (``SPEC.md`` §10.3).
        """
        if isinstance(expr, Literal):
            return False
        if isinstance(expr, UnaryOp):
            return self._is_double_expr(expr.operand)
        if isinstance(expr, BinaryOp) and expr.op in _ARITHMETIC:
            return any(self._is_double_expr(side) for side in (expr.left, expr.right))
        return self._type_of(expr) is DataType.DOUBLE

    def _decimal_scale(self, expr: Expr) -> int:
        """Digit di belakang koma hasil ``expr`` menurut aturan ``DECIMAL`` MySQL."""
        if isinstance(expr, Literal):
            return literal_scale(expr.value)
        if isinstance(expr, UnaryOp):
            return self._decimal_scale(expr.operand)
        if isinstance(expr, BinaryOp) and expr.op in _ARITHMETIC:
            left = self._decimal_scale(expr.left)
            right = self._decimal_scale(expr.right)
            if expr.op in ("+", "-"):
                return max(left, right)
            if expr.op == "*":
                return left + right
            if expr.op == "/":
                return left + 4
            return 0  # DIV dan % selalu bulat
        return 0

    def _in_list(self, expr: InList, var: str) -> str:
        if self._type_of(expr.operand) is DataType.TEXT:
            operand = self._text_operand(expr.operand, var)
        else:
            operand = self._expr(expr.operand, var)
        parts: list[str] = []
        comparisons: list[bool] = []
        for value in expr.values:
            if _static_value(value) is None:
                # `x = NULL` bernilai NULL, bukan perbandingan (§10.1).
                parts.append(self._null_bool(var))
                comparisons.append(False)
                continue
            if isinstance(value, Literal) and isinstance(value.value, str):
                right = _py_str(value.value.lower())
            else:
                right = self._expr(value, var)
            parts.append(f"({operand} == {right})")
            comparisons.append(True)
        if not all(comparisons):
            # Gabungan dengan Series boolean seluruh-NA harus memakai dtype yang sama;
            # tanpa ini pandas 3.0 melempar "boolean value of NA is ambiguous".
            parts = [
                _as_boolean(part) if is_comparison else part
                for part, is_comparison in zip(parts, comparisons, strict=True)
            ]
        body = f"({' | '.join(parts)})"
        return f"(~{body})" if expr.negated else body

    def _between(self, expr: Between, var: str) -> str:
        if _static_value(expr.low) is None or _static_value(expr.high) is None:
            # Batas NULL membuat seluruh rentang NULL untuk setiap baris.
            null = self._null_bool(var)
            return f"(~{null})" if expr.negated else null
        if self._type_of(expr.operand) is DataType.TEXT:
            operand = self._text_operand(expr.operand, var)
            low = self._text_operand(expr.low, var)
            high = self._text_operand(expr.high, var)
        else:
            operand = self._expr(expr.operand, var)
            low = self._expr(expr.low, var)
            high = self._expr(expr.high, var)
        body = f"(({operand} >= {low}) & ({operand} <= {high}))"
        return f"(~{body})" if expr.negated else body

    def _null_bool(self, var: str) -> str:
        """Series ``boolean`` seluruh-NA: nilai NULL pada posisi boolean (``SPEC.md`` §10.1)."""
        return f"pd.Series([pd.NA] * len({var}), index={var}.index, dtype='boolean')"

    # --- penyatuan boolean --------------------------------------------------

    def _needs_bool_cast(self, expr: Expr) -> bool:
        """True bila ``expr`` dapat menghasilkan dtype selain ``boolean`` ter-mask.

        Pandas 3.0 mengembalikan ``bool[pyarrow]`` untuk perbandingan kolom teks
        dan ``object`` untuk ekspresi yang salah satunya statis ``NULL``, sedangkan
        kolom ``Int64``/``Float64`` menghasilkan ``boolean`` ter-mask. Menggabungkan
        dtype tersebut dengan ``&``/``|`` saat memuat ``NA`` melempar ``TypeError``;
        ``astype("boolean")`` mempertahankan ``NA`` sehingga logika tiga nilai
        ``SPEC.md`` §10.1 tetap terpenuhi.
        """
        static = _static_value(expr)
        if static is not _NOT_STATIC:
            # Literal bool diekspor sebagai "True"/"False"; NULL diekspor object Series.
            return static is None
        # `NOT` diseragamkan oleh ``_expr`` pada operandnya sehingga hasilnya sudah
        # berdtype ``boolean`` dan tidak perlu disatukan lagi di sini.
        if isinstance(expr, BinaryOp) and expr.op in _COMPARISON:
            if (
                self._type_of(expr.left) is DataType.TEXT
                or self._type_of(expr.right) is DataType.TEXT
            ):
                return True
            return _static_value(expr.left) is None or _static_value(expr.right) is None
        if isinstance(expr, (InList, Between)):
            return self._type_of(expr.operand) is DataType.TEXT
        return False

    def _bool_operand(self, expr: Expr, var: str) -> str:
        """Ekspresi boolean yang aman digabung dengan ``&``/``|``."""
        text = self._expr(expr, var)
        if not self._needs_bool_cast(expr):
            return text
        return _as_boolean(text)

    def _expr(self, expr: Expr, var: str) -> str:
        static = _static_value(expr)
        if static is not _NOT_STATIC:
            if static is None:
                return self._null(var)
            if isinstance(static, bool):
                return "True" if static else "False"
            return repr(static)
        if isinstance(expr, Column):
            return f'{var}["{expr.name}"]'
        if isinstance(expr, UnaryOp):
            operand = self._expr(expr.operand, var)
            if expr.op == "NOT":
                if self._needs_bool_cast(expr.operand):
                    operand = _as_boolean(operand)
                return f"(~{operand})"
            return f"({expr.op}{operand})"
        if isinstance(expr, BinaryOp):
            return self._binary(expr, var)
        if isinstance(expr, Like):
            regex = _like_to_regex(expr.pattern)
            body = f"{self._plain(expr.operand, var)}.str.match({_py_raw(regex)})"
            return f"(~{body})" if expr.negated else body
        if isinstance(expr, InList):
            return self._in_list(expr, var)
        if isinstance(expr, Between):
            return self._between(expr, var)
        if isinstance(expr, IsNull):
            test = "notna" if expr.negated else "isna"
            return f"{self._plain(expr.operand, var)}.{test}()"
        if isinstance(expr, FunctionCall):  # pragma: no cover - SEM009 menolak
            if isinstance(expr.arg, Star):
                return f"{expr.name}(*)"
            return f"{expr.name}({self._expr(expr.arg, var)})"
        raise TypeError(f"node ekspresi tak dikenal: {expr!r}")  # pragma: no cover

    def _binary(self, node: BinaryOp, var: str) -> str:
        op = node.op
        if op in _ARITHMETIC and (
            _static_value(node.left) is None or _static_value(node.right) is None
        ):
            return self._null(var)
        if op in ("AND", "OR"):
            operator = "&" if op == "AND" else "|"
            left = self._bool_operand(node.left, var)
            right = self._bool_operand(node.right, var)
            return f"({left} {operator} {right})"
        if op in _COMPARISON:
            return self._comparison(node, var)
        if op == "/":
            return self._division(node, var)
        if op in ("DIV", "%"):
            helper = "_idiv" if op == "DIV" else "_imod"
            self.helpers.add(helper)
            left = self._expr(node.left, var)
            right = self._expr(node.right, var)
            return f"{helper}({left}, {right})"
        left = self._expr(node.left, var)
        right = self._expr(node.right, var)
        return f"({left} {op} {right})"

    # --- komentar -----------------------------------------------------------

    def _select_comment(self) -> str:
        parts: list[str] = []
        for item in self.ast.items:
            if isinstance(item, Star):
                parts.append(f"{item.table}.*" if item.table else "*")
            elif item.alias:
                parts.append(f"{_sql_text(item.expr)} AS {item.alias}")
            else:
                parts.append(_sql_text(item.expr))
        return "SELECT " + ", ".join(parts)

    def _order_comment(self) -> str:
        parts: list[str] = []
        for item in self.ast.order_by:
            text = _sql_text(item.expr)
            parts.append(f"{text} DESC" if item.descending else text)
        return "ORDER BY " + ", ".join(parts)

    def _filter_comment(self, node: Filter) -> str:
        if node.label == "HAVING":
            source = self.ast.having
            return "HAVING " + _sql_text(source) if source is not None else "HAVING"
        source = self.ast.where
        return "WHERE " + _sql_text(source) if source is not None else "WHERE"

    def _comment(self, text: str) -> None:
        if text == self._last_comment:
            return
        self._last_comment = text
        prefix = "    # SQL: "
        line = prefix + text
        while len(line) > _MAX_WIDTH:
            cut = line.rfind(" ", len(prefix), _MAX_WIDTH)
            if cut <= len(prefix):
                cut = _MAX_WIDTH
            self.lines.append(line[:cut])
            prefix = "    #      "
            line = prefix + line[cut + 1 :].lstrip()
        self.lines.append(line)

    # --- operator -----------------------------------------------------------

    def _emit(self, node: PlanNode, var: str) -> str:
        if isinstance(node, Scan):
            return self._scan(node)
        if isinstance(node, Join):
            return self._join(node, var)
        if isinstance(node, Filter):
            return self._filter(node, var)
        if isinstance(node, Aggregate):
            return self._aggregate(node, var)
        if isinstance(node, Sort):
            return self._sort(node, var)
        if isinstance(node, Project):
            return self._project(node, var)
        if isinstance(node, Distinct):
            return self._distinct(node, var)
        if isinstance(node, Limit):
            return self._limit(node, var)
        raise TypeError(f"operator tak dikenal: {node!r}")  # pragma: no cover

    def _ref_for(self, node: Scan) -> TableRef:
        for ref in self.refs:
            if ref.name == node.table and (ref.alias or ref.name).lower() == node.alias:
                return ref
        return self.ast.from_

    def _scan(self, node: Scan) -> str:
        ref = self._ref_for(node)
        if ref is self.ast.from_:
            suffix = f" {ref.alias}" if ref.alias else ""
            self._comment(f"FROM {ref.name}{suffix}")
        name = node.alias
        if not self.ast.joins and name == node.table:
            name = "df"
        self.lines.append(f'    {name} = tables["{node.table}"].add_prefix("{node.alias}.")')
        return name

    def _join(self, node: Join, var: str) -> str:
        left_var = self._emit(node.left, var)
        index = self.join_index
        self.join_index += 1
        right_var = self._emit(node.right, var)
        if index < len(self.ast.joins):
            join = self.ast.joins[index]
            ref = join.table
            suffix = f" {ref.alias}" if ref.alias else ""
            self._comment(f"{join.kind} JOIN {ref.name}{suffix} ON {_sql_text(join.on)}")

        left_keys: list[str] = []
        right_keys: list[str] = []
        left_pairs: list[str] = []
        right_pairs: list[str] = []
        for position, (left_key, right_key) in enumerate(node.keys):
            if (
                self._column_type(left_key) is DataType.TEXT
                or self._column_type(right_key) is DataType.TEXT
            ):
                left_name = f"__jl{position}"
                right_name = f"__jr{position}"
                left_pairs.append(f'{left_name}={left_var}["{left_key}"].str.lower()')
                right_pairs.append(f'{right_name}={right_var}["{right_key}"].str.lower()')
                left_keys.append(left_name)
                right_keys.append(right_name)
            else:
                left_keys.append(left_key)
                right_keys.append(right_key)
        if left_pairs:
            self.lines.append(f"    {left_var} = {left_var}.assign({', '.join(left_pairs)})")
        if right_pairs:
            self.lines.append(f"    {right_var} = {right_var}.assign({', '.join(right_pairs)})")

        left_list = ", ".join(_py_str(key) for key in left_keys)
        right_list = ", ".join(_py_str(key) for key in right_keys)
        if node.kind == "INNER":
            head = f"    df = {left_var}.dropna(subset=[{left_list}]).merge("
            args = [
                f"{right_var}.dropna(subset=[{right_list}])",
                'how="inner"',
                f"left_on=[{left_list}]",
                f"right_on=[{right_list}]",
            ]
        else:
            head = f"    df = {left_var}.merge("
            args = [
                f"{right_var}.dropna(subset=[{right_list}])",
                'how="left"',
                f"left_on=[{left_list}]",
                f"right_on=[{right_list}]",
            ]
        self._wrap_call(head, args)
        return "df"

    def _filter(self, node: Filter, var: str) -> str:
        child_var = self._emit(node.child, var)
        self._comment(self._filter_comment(node))
        value = _static_value(node.predicate)
        if value is not _NOT_STATIC:
            if _predicate_truth(value) is True:
                return child_var
            self.lines.append(f"    df = {child_var}.iloc[0:0]")
            return "df"
        predicates = list(_flatten_and(node.predicate))
        texts = [self._expr(term, child_var) for term in predicates]
        # Satu predikat tidak perlu disatukan sehingga bentuknya tidak berubah;
        # dengan dua predikat atau lebih, dtype boolean diseragamkan dulu.
        cast = len(predicates) > 1
        wrapped: list[str] = []
        for term, text in zip(predicates, texts, strict=True):
            if cast and self._needs_bool_cast(term):
                wrapped.append(_as_boolean(text))
            elif _is_wrapped(text):
                wrapped.append(text)
            else:
                wrapped.append(f"({text})")
        one_line = f"    df = {child_var}[{' & '.join(wrapped)}.fillna(False)]"
        if len(one_line) <= _MAX_WIDTH:
            self.lines.append(one_line)
        else:
            self.lines.append("    mask = (")
            self._emit_mask(wrapped, 8)
            self.lines.append("    )")
            self.lines.append(f"    df = {child_var}[mask.fillna(False)]")
        return "df"

    def _emit_mask(self, terms: list[str], indent: int) -> None:
        """Tulis konjungsi pada ``indent``; konjungsi terlalu panjang dipecah jadi satu blok."""
        bare = len(terms) == 1
        for position, term in enumerate(terms):
            prefix = " " * indent + ("" if position == 0 else "& ")
            if len(prefix) + len(term) <= _MAX_WIDTH:
                self.lines.append(prefix + term)
                continue
            broken = _breakable(term)
            if broken is None:
                self.lines.append(prefix + term)
                continue
            open_text, close_text, operator, parts = broken
            if bare and open_text == "(" and close_text == ")":
                # Kurung pembuka ``mask = (`` sudah cukup sebagai pembungkus.
                for index, part in enumerate(parts):
                    part_prefix = " " * indent + ("" if index == 0 else f"{operator} ")
                    self.lines.append(part_prefix + part)
                continue
            self.lines.append(prefix + open_text)
            for index, part in enumerate(parts):
                part_prefix = " " * (indent + 4) + ("" if index == 0 else f"{operator} ")
                self.lines.append(part_prefix + part)
            self.lines.append(" " * indent + close_text)

    def _aggregate(self, node: Aggregate, var: str) -> str:
        child_var = self._emit(node.child, var)
        if self.ast.group_by:
            texts = ", ".join(_sql_text(expr) for expr in self.ast.group_by)
            self._comment("GROUP BY " + texts)
        else:
            self._comment(self._select_comment())

        need_star = any(func == "count" and isinstance(arg, Star) for _, func, arg in node.aggs)
        head: list[str] = ["__one=1"] if need_star else []
        tail: list[str] = []
        agg_columns: dict[str, str] = {}
        for position, (name, _, arg) in enumerate(node.aggs):
            if isinstance(arg, Star):
                agg_columns[name] = "__one"
            elif isinstance(arg, Column):
                agg_columns[name] = arg.name
            else:
                temp = f"__ain{position}"
                head.append(f"{temp}={self._expr(arg, child_var)}")
                agg_columns[name] = temp

        group_columns: list[str] = []
        representatives: list[str] = []
        for position, (name, expr) in enumerate(node.group_keys):
            if isinstance(expr, Column):
                source = f'{child_var}["{expr.name}"]'
                direct = True
            else:
                head.append(f"{name}={self._expr(expr, child_var)}")
                source = f'{child_var}["{name}"]'
                direct = False
            representatives.append(name)
            if self.group_types.get(name) is DataType.TEXT:
                key_name = f"__k{position}"
                if direct:
                    head.append(f"{key_name}={source}.str.lower()")
                else:
                    tail.append(f"{key_name}={source}.str.lower()")
                group_columns.append(key_name)
            else:
                group_columns.append(name)

        if not group_columns:
            self._aggregate_frame(node, child_var)
        else:
            if head:
                self.lines.append(f"    {child_var} = {child_var}.assign({', '.join(head)})")
            if tail:
                self.lines.append(f"    {child_var} = {child_var}.assign({', '.join(tail)})")
            self._aggregate_groupby(node, child_var, group_columns, representatives, agg_columns)
        self._round_avg(node)
        return "df"

    def _aggregate_groupby(
        self,
        node: Aggregate,
        child_var: str,
        group_columns: list[str],
        representatives: list[str],
        agg_columns: dict[str, str],
    ) -> None:
        groups = ", ".join(_py_str(column) for column in group_columns)
        head = f"    df = {child_var}.groupby([{groups}], dropna=False, sort=False).agg("
        self.lines.append(head)
        self.lines.append("        **{")
        for name in representatives:
            self.lines.append(f'            {_py_str(name)}: ({_py_str(name)}, "first"),')
        self.lines.append("        },")
        for name, func, arg in node.aggs:
            spec = self._agg_spec(func, arg, agg_columns[name])
            self.lines.append(f"        {name}={spec},")
        self.lines.append("    ).reset_index(drop=True)")

    def _aggregate_frame(self, node: Aggregate, child_var: str) -> None:
        entries: list[str] = []
        for name, func, arg in node.aggs:
            value = self._agg_value(func, arg, child_var)
            dtype = _dtype_name(self.agg_types.get(name))
            source = f"pd.array([{value}], dtype={_py_str(dtype)})" if dtype else f"[{value}]"
            entries.append(f"{_py_str(name)}: {source}")
        one_line = f"    df = pd.DataFrame({{{', '.join(entries)}}})"
        if len(one_line) <= _MAX_WIDTH:
            self.lines.append(one_line)
            return
        self.lines.append("    df = pd.DataFrame({")
        for entry in entries:
            self.lines.append(f"        {entry},")
        self.lines.append("    })")

    def _round_avg(self, node: Aggregate) -> None:
        for name, func, arg in node.aggs:
            if func != "avg" or isinstance(arg, Star):
                continue
            if self.agg_types.get(name) is DataType.DOUBLE and self._type_of(arg) is DataType.INT:
                self.lines.append(f'    df["{name}"] = df["{name}"].round(4)')

    def _agg_spec(self, func: str, arg: Expr | Star, column: str) -> str:
        if func == "count":
            return f'({_py_str(column)}, "count")'
        if func == "sum":
            return f"({_py_str(column)}, lambda s: s.sum(min_count=1))"
        if func == "avg":
            return f'({_py_str(column)}, "mean")'
        if func in ("min", "max"):
            if isinstance(arg, Star) or self._type_of(arg) is not DataType.TEXT:
                return f'({_py_str(column)}, "{func}")'
            helper = "_tmin" if func == "min" else "_tmax"
            self.helpers.add(helper)
            return f"({_py_str(column)}, {helper})"
        raise TypeError(f"fungsi agregat tak dikenal: {func}")  # pragma: no cover

    def _agg_value(self, func: str, arg: Expr | Star, var: str) -> str:
        if isinstance(arg, Star):
            return f"len({var})"
        column = self._expr(arg, var)
        if func == "count":
            return f"{column}.count()"
        if func == "sum":
            return f"{column}.sum(min_count=1)"
        if func == "avg":
            return f"{column}.mean()"
        if func in ("min", "max"):
            if self._type_of(arg) is DataType.TEXT:
                helper = "_tmin" if func == "min" else "_tmax"
                self.helpers.add(helper)
                return f"{helper}({column})"
            return f"{column}.{func}()"
        raise TypeError(f"fungsi agregat tak dikenal: {func}")  # pragma: no cover

    def _sort(self, node: Sort, var: str) -> str:
        child_var = self._emit(node.child, var)
        self._comment(self._order_comment())
        columns: list[str] = []
        temps: list[str] = []
        for position, (expr, _) in enumerate(node.keys):
            if isinstance(expr, Column):
                columns.append(expr.name)
            else:
                name = f"__sort{position}"
                temps.append(f"{name}={self._expr(expr, child_var)}")
                columns.append(name)
        if temps:
            self.lines.append(f"    {child_var} = {child_var}.assign({', '.join(temps)})")
        source = child_var
        for position in range(len(node.keys) - 1, -1, -1):
            _, descending = node.keys[position]
            args = [
                f"by=[{_py_str(columns[position])}]",
                "ascending=[False]" if descending else "ascending=[True]",
                f'na_position="{"last" if descending else "first"}"',
                'kind="stable"',
            ]
            if self._type_of(node.keys[position][0]) is DataType.TEXT:
                args.append("key=lambda s: s.str.lower()")
            self._wrap_call(f"    df = {source}.sort_values(", args)
            source = "df"
        return "df"

    def _project(self, node: Project, var: str) -> str:
        child_var = self._emit(node.child, var)
        self.project_items = list(node.items)
        self._comment(self._select_comment())
        names = [name for _, name in node.items]
        sources = [self._expr(expr, child_var) for expr, _ in node.items]
        entries = [f"{_py_str(name)}: {source}" for name, source in zip(names, sources)]
        index_arg = (
            f", index={child_var}.index"
            if any(_is_scalar(expr) for expr, _ in node.items)
            else ""
        )
        if len(set(names)) == len(names):
            one_line = f"    out = pd.DataFrame({{{', '.join(entries)}}}{index_arg})"
            if len(one_line) <= _MAX_WIDTH:
                self.lines.append(one_line)
                return "out"
            self.lines.append("    out = pd.DataFrame({")
            for entry in entries:
                self.lines.append(f"        {entry},")
            self.lines.append(f"    }}{index_arg})")
            return "out"
        frames = [f"pd.DataFrame({{{entry}}}{index_arg})" for entry in entries]
        self._wrap_call("    out = pd.concat([", frames, "], axis=1)")
        return "out"

    def _distinct(self, node: Distinct, var: str) -> str:
        child_var = self._emit(node.child, var)
        self._comment("DISTINCT")
        items = list(self.project_items)
        if not items:  # pragma: no cover - selalu ada Project di bawah Distinct
            return child_var
        names = [name for _, name in items]
        unique = len(set(names)) == len(names)
        if len(items) == 1:
            expr, name = items[0]
            ref = f'{child_var}["{name}"]' if unique else f"{child_var}.iloc[:, 0]"
            if self._type_of(expr) is DataType.TEXT:
                ref = f"{ref}.str.lower()"
            self.lines.append(f"    {child_var} = {child_var}[~{ref}.duplicated()]")
            return child_var
        parts: list[str] = []
        for position, (expr, name) in enumerate(items):
            ref = f'{child_var}["{name}"]' if unique else f"{child_var}.iloc[:, {position}]"
            if self._type_of(expr) is DataType.TEXT:
                ref = f"{ref}.str.lower()"
            parts.append(f'{ref}.rename("c{position}")')
        self._wrap_call("    __d = pd.concat([", parts, "], axis=1)")
        self.lines.append(f"    {child_var} = {child_var}[~__d.duplicated()]")
        return child_var

    def _limit(self, node: Limit, var: str) -> str:
        child_var = self._emit(node.child, var)
        if node.n is None:
            self._comment(f"OFFSET {node.offset}")
            target = f"{child_var}.iloc[{node.offset}:]"
        else:
            self._comment(
                f"LIMIT {node.n} OFFSET {node.offset}" if node.offset else f"LIMIT {node.n}"
            )
            target = f"{child_var}.iloc[{node.offset}:{node.offset + node.n}]"
        self.lines.append(f"    out = {target}")
        return "out"

    # --- penyusunan ---------------------------------------------------------

    def _wrap_call(self, head: str, args: list[str], tail: str = ")") -> None:
        one_line = f"{head}{', '.join(args)}{tail}"
        if len(one_line) <= _MAX_WIDTH:
            self.lines.append(one_line)
            return
        base = len(head) - len(head.lstrip(" "))
        indent = base + 4
        self.lines.append(head)
        for arg in args:
            self.lines.append(" " * indent + arg + ",")
        self.lines.append(" " * base + tail)

    def generate(self) -> str:
        result_var = self._emit(self.plan.root, "df")
        self.lines.append(f"    return {result_var}.reset_index(drop=True)")
        parts = ["import pandas as pd"]
        for helper in _HELPER_ORDER:
            if helper in self.helpers:
                parts.extend(["", "", _HELPERS[helper]])
        parts.extend(["", "", "def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:"])
        parts.extend(self.lines)
        return "\n".join(parts) + "\n"


def _children(node: PlanNode) -> tuple[PlanNode, ...]:
    if isinstance(node, Scan):
        return ()
    if isinstance(node, Join):
        return (node.left, node.right)
    if isinstance(node, (Filter, Aggregate, Sort, Project, Distinct, Limit)):
        return (node.child,)
    return ()


def _find(node: PlanNode, kind: type) -> PlanNode | None:
    if isinstance(node, kind):
        return node
    for child in _children(node):
        found = _find(child, kind)
        if found is not None:
            return found
    return None
