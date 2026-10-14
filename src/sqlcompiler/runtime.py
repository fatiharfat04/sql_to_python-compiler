"""Fasad kompilasi ``compile_sql`` dan eksekusi ``run_compiled`` (SPEC.md §3, §13).

Pipeline: Lexer → Parser → Semantik → IR → Code generator → Runtime.
CLI dan UI **hanya** memanggil fasad ini sehingga logika kompilasi tidak terduplikasi.
"""

from __future__ import annotations

import builtins
import importlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pandas as pd

from .ast_nodes import SelectStatement
from .codegen import generate_code
from .errors import CompileError, LexError, ParseError
from .ir import Plan, lower
from .lexer import Token, tokenize
from .parser import parse
from .semantic import AnalysisResult, Schema, analyze

__all__ = ["CompileResult", "compile_sql", "run_compiled"]


@dataclass(frozen=True)
class CompileResult:
    """Hasil seluruh pipeline; ``code`` kosong bila terdapat ``errors`` (SPEC.md §3).

    ``analysis`` diisi setelah tahap semantik berjalan (``None`` bila lexer/parser
    gagal) sehingga antarmuka dapat menampilkan tab Semantik tanpa memanggil
    analyzer kedua.
    """

    tokens: tuple[Token, ...]
    ast: SelectStatement | None
    plan: Plan | None
    code: str
    errors: tuple[CompileError, ...]
    analysis: AnalysisResult | None = None


def compile_sql(sql: str, schema: Schema) -> CompileResult:
    """Menjalankan seluruh pipeline pada ``sql`` terhadap ``schema``.

    Fungsi ini **tidak pernah** melempar error kompilasi; seluruh kegagalan
    leksikal, sintaks, dan semantik dikumpulkan pada ``CompileResult.errors``.
    """
    try:
        tokens: list[Token] = tokenize(sql)
    except LexError as exc:
        return CompileResult(tokens=(), ast=None, plan=None, code="", errors=(exc,))

    try:
        ast = parse(tokens, sql)
    except ParseError as exc:
        return CompileResult(tokens=tuple(tokens), ast=None, plan=None, code="", errors=(exc,))

    analysis: AnalysisResult = analyze(ast, schema)
    if analysis.errors:
        return CompileResult(
            tokens=tuple(tokens),
            ast=ast,
            plan=None,
            code="",
            errors=tuple(analysis.errors),
            analysis=analysis,
        )
    plan = lower(analysis)
    return CompileResult(
        tokens=tuple(tokens),
        ast=ast,
        plan=plan,
        code=generate_code(plan, analysis),
        errors=(),
        analysis=analysis,
    )


#: Builtins yang tersedia bagi kode hasil kompilasi (SPEC.md §13: namespace terbatas).
_SAFE_BUILTIN_NAMES: tuple[str, ...] = (
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "filter",
    "float",
    "int",
    "isinstance",
    "len",
    "list",
    "map",
    "max",
    "min",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
)

#: Modul yang boleh diimpor oleh kode hasil kompilasi.
_ALLOWED_IMPORTS: frozenset[str] = frozenset({"pandas"})


def _safe_import(
    name: str,
    globals: dict[str, Any] | None = None,  # noqa: A002
    locals: dict[str, Any] | None = None,  # noqa: A002
    fromlist: Sequence[str] | None = None,  # noqa: A002
    level: int = 0,
) -> ModuleType:
    """Pengganti ``__import__`` yang hanya mengizinkan ``pandas``."""
    root = name.split(".", 1)[0]
    if level != 0 or root not in _ALLOWED_IMPORTS:
        raise ImportError(f"impor '{name}' tidak diizinkan pada runtime sqlcompiler")
    return importlib.import_module(root)


def _build_builtins() -> dict[str, Any]:
    namespace: dict[str, Any] = {
        name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES
    }
    namespace["__import__"] = _safe_import
    return namespace


#: Namespace builtins untuk :func:`run_compiled`.
SAFE_BUILTINS: dict[str, Any] = _build_builtins()


def run_compiled(code: str, tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Mengeksekusi ``code`` (hasil :func:`compile_sql`) pada ``tables`.

    Namespace dibatasi hanya kepada ``pd`` dan daftar builtins aman. Tabel
    builtins di-**salin** pada setiap panggilan, sehingga kode yang dieksekusi
    tidak dapat mengubah builtins yang akan dipakai eksekusi berikutnya
    (semula ``__builtins__`` menunjuk ke ``SAFE_BUILTINS`` yang sama, sehingga
    satu ``__builtins__['len'] = None`` merusak seluruh eksekusi sesudahnya).

    Ini **bukan** sandbox keamanan yang kuat (lihat ``README.md`` dan
    ``SPEC.md`` §13): ``SAFE_BUILTINS`` tetap memuat ``getattr``/``object``, sehingga
    rantai ``object.__subclasses__()`` masih dapat mencapai kelas bebas, dan ``pd``
    adalah modul pandas sungguhan yang dapat membaca berkas. Kode yang dieksekusi
    selalu hasil :func:`compile_sql`, bukan teks yang diberikan pengguna.
    """
    namespace: dict[str, Any] = {"__builtins__": dict(SAFE_BUILTINS), "pd": pd}
    exec(compile(code, "<sqlcompiler>", "exec"), namespace)  # noqa: S102
    runner: Any = namespace.get("run")
    if not callable(runner):
        raise RuntimeError("kode hasil kompilasi tidak mendefinisikan fungsi run().")
    result = runner(tables)
    if not isinstance(result, pd.DataFrame):
        raise TypeError("fungsi run() harus mengembalikan pandas.DataFrame.")
    return result
