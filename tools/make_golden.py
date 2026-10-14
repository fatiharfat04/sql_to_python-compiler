"""Skrip bantu: memperbarui berkas golden di tests/golden (Langkah 6).

Pemakaian: .venv/Scripts/python.exe tools/make_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlcompiler.codegen import generate_code  # noqa: E402
from sqlcompiler.ir import lower  # noqa: E402
from sqlcompiler.lexer import tokenize  # noqa: E402
from sqlcompiler.parser import parse  # noqa: E402
from sqlcompiler.schema_loader import load_schema_json  # noqa: E402
from sqlcompiler.semantic import analyze  # noqa: E402

GOLDEN = ROOT / "tests" / "golden"

QUERIES: list[tuple[str, str]] = [
    (
        "spec01_where_order_limit",
        "SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC LIMIT 5",
    ),
    (
        "spec02_distinct_teks",
        "SELECT DISTINCT jurusan FROM mahasiswa ORDER BY jurusan",
    ),
    (
        "spec03_join_group_having",
        "SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata\n"
        "FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim\n"
        "GROUP BY m.jurusan\n"
        "HAVING COUNT(*) > 2\n"
        "ORDER BY rata DESC",
    ),
    (
        "spec04_like_in_between",
        "SELECT nim, nama FROM mahasiswa\n"
        "WHERE nama LIKE 'A%' AND jurusan IN ('TI', 'SI')\n"
        "  AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL",
    ),
    (
        "spec05_left_join_isnull",
        "SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim "
        "WHERE n.nim IS NULL",
    ),
    (
        "spec06_div_mod",
        "SELECT sks, sks / 3 AS bagi, sks DIV 3 AS bulat, sks % 3 AS sisa "
        "FROM mata_kuliah ORDER BY sks",
    ),
    (
        "extra01_limit_ab",
        "SELECT nim, nama FROM mahasiswa ORDER BY nim LIMIT 2, 3",
    ),
    (
        "extra02_not_in",
        "SELECT nama FROM mahasiswa WHERE jurusan NOT IN ('TI', 'SI')",
    ),
    (
        "extra03_not_like",
        "SELECT nama FROM mahasiswa WHERE nama NOT LIKE 'A%' ORDER BY nama",
    ),
    (
        "extra04_agregat_tanpa_group",
        "SELECT COUNT(*) AS jumlah, SUM(ipk) AS total, AVG(ipk) AS rata, "
        "MIN(ipk) AS terkecil, MAX(ipk) AS terbesar FROM mahasiswa",
    ),
    (
        "extra05_left_join_where_kiri",
        "SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim "
        "WHERE m.ipk > 3.5 ORDER BY m.nama",
    ),
    (
        "extra06_select_star_join",
        "SELECT * FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim "
        "ORDER BY m.nim, n.kode",
    ),
]


def main() -> None:
    schema = load_schema_json(ROOT / "examples" / "schema.json")
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, sql in QUERIES:
        analysis = analyze(parse(tokenize(sql), sql), schema)
        if not analysis.ok:  # pragma: no cover - pemberitahuan ke penulis
            raise SystemExit(f"{name}: {analysis.errors}")
        code = generate_code(lower(analysis), analysis)
        compile(code, f"<{name}>", "exec")
        (GOLDEN / f"{name}.sql").write_text(sql + "\n", encoding="utf-8")
        (GOLDEN / f"{name}.py").write_text(code, encoding="utf-8", newline="\n")
        print(f"{name}: {len(code.splitlines())} baris")


if __name__ == "__main__":
    main()
