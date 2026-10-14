"""Tes diferensial terhadap oracle MySQL 8.4 (``SPEC.md`` §16).

Setiap query dijalankan dua kali: pada server MySQL 8.4 sebagai **oracle** dan
pada kode hasil :func:`sqlcompiler.runtime.compile_sql`. Keduanya dibandingkan
memakai aturan ``tests/mysql_oracle.py``: jumlah dan nama kolom sama, sel
dibandingkan berdasarkan posisi kolom, baris sebagai **multiset** kecuali query
ber-``ORDER BY`` (urutan dibandingkan), toleransi ``1e-9`` untuk ``float``, dan
``NULL`` sama dengan ``NULL``.

**Tidak boleh di-skip.** Bila server oracle berhenti, tidak terpasang, atau
tidak sesuai ``SPEC.md`` §2, tes di bawah harus **gagal** — bukan dilewati.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mysql_oracle import (
    DEFAULT_DSN,
    EXPECTED_SQL_MODE,
    compare_frames,
    compare_query,
    fetch,
    server_facts,
)

from sqlcompiler.data_loader import load_tables_csv, load_tables_mysql
from sqlcompiler.runtime import compile_sql, run_compiled
from sqlcompiler.schema_loader import load_schema_json

#: Akar proyek: berkas ``examples/*.csv`` dan ``examples/schema.json`` ada di sana.
ROOT = Path(__file__).resolve().parent.parent
SCHEMA = load_schema_json(ROOT / "examples" / "schema.json")
TABLES = load_tables_csv(ROOT / "examples", SCHEMA)

#: Tabel yang dibandingkan antara CSV dan MySQL pada tes konsistensi.
TABLE_NAMES = ["mahasiswa", "mata_kuliah", "nilai"]

#: Seluruh query diferensial: mencakup butir 1–15 dan 17 ``SPEC.md`` §10
#: ditambah aturan resolusi alias ``SPEC.md`` §8.
QUERIES: list[str] = [
    "SELECT nim FROM mahasiswa WHERE ipk > 3.0 AND jurusan = 'TI' ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk > 3.0 OR jurusan = 'TI' ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE NOT (ipk > 3.0) ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk > 3.0 AND jurusan = NULL ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk IS NULL OR jurusan IS NULL ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE NOT (ipk IS NULL) AND kota = 'jakarta' ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE kota = 'jakarta' AND ipk > 3.0 OR jurusan = 'MI' ORDER BY nim",
    (
        "SELECT nim FROM mahasiswa WHERE (kota = 'jakarta' OR kota = 'bandung') AND ipk > 3.0 "
        "ORDER BY nim"
    ),
    "SELECT nim FROM mahasiswa WHERE NOT (jurusan = 'TI' OR jurusan = 'SI') ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk = NULL OR nim > 0 ORDER BY nim",
    "SELECT nim, skor + 1 AS satu FROM nilai ORDER BY nim, kode",
    "SELECT kode, sks / 3 AS bagi FROM mata_kuliah ORDER BY kode",
    "SELECT kode, sks / 3.0 AS bagi_desimal FROM mata_kuliah ORDER BY kode",
    "SELECT nama, ipk / 3 AS tiga_per FROM mahasiswa ORDER BY nim",
    "SELECT nama, ipk / 2.0 AS dua_per FROM mahasiswa ORDER BY nim",
    "SELECT nim, skor FROM nilai WHERE skor / 2 > 40 ORDER BY nim, kode",
    "SELECT nim, ipk FROM mahasiswa WHERE ipk / 2 >= 1.5 ORDER BY nim",
    "SELECT 7 / 2.0 AS a, 4 / 3.0 AS b, 1 / 3 AS c, 2 / 3 AS d FROM mahasiswa LIMIT 1",
    "SELECT 5 / 32 AS a, 1 / 32 AS b, -1 / 32 AS c, 3 / 32 AS d FROM mahasiswa LIMIT 1",
    "SELECT kode, sks / 32 AS a, sks / 16 AS b, sks / 7 AS c FROM mata_kuliah ORDER BY kode",
    "SELECT kode, sks / 2.0 AS bagi2 FROM mata_kuliah ORDER BY kode",
    "SELECT kode, sks * 1.5 AS kali FROM mata_kuliah ORDER BY kode",
    "SELECT kode, sks - 4 AS kurang, sks + 100 AS tambah FROM mata_kuliah ORDER BY kode",
    "SELECT kode, sks DIV 2 AS bulat, sks % 2 AS sisa FROM mata_kuliah ORDER BY kode",
    "SELECT kode, sks DIV -2 AS bulat, sks % -2 AS sisa FROM mata_kuliah ORDER BY kode",
    "SELECT kode, -sks AS negasi, 0 - sks AS nol_kurang FROM mata_kuliah ORDER BY kode",
    (
        "SELECT kode, sks AS s, 7 / 2 AS tujuh_dua, 1 / 3 AS sepertiga, 2 / 3 AS duapertiga FROM "
        "mata_kuliah ORDER BY kode"
    ),
    "SELECT kode, sks AS s FROM mata_kuliah WHERE 1 = 1 ORDER BY kode",
    "SELECT nama, ipk + 1 AS ipk1, ipk * 2 AS ipk2 FROM mahasiswa ORDER BY nim",
    "SELECT nama FROM mahasiswa WHERE ipk + 1 > 4.5 ORDER BY nim",
    "SELECT nim, kota FROM mahasiswa WHERE kota = 'Jakarta' ORDER BY nim",
    "SELECT nim, kota FROM mahasiswa WHERE kota = 'jakarta' ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE kota <> 'Jakarta' ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE kota IN ('Jakarta', 'Bandung') ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE kota BETWEEN 'Bandung' AND 'Jakarta' ORDER BY nim",
    "SELECT DISTINCT kode FROM nilai ORDER BY kode",
    "SELECT nama FROM mahasiswa ORDER BY nama",
    "SELECT nama FROM mahasiswa ORDER BY nama DESC",
    "SELECT MIN(jurusan) AS min_jurusan, MAX(jurusan) AS max_jurusan FROM mahasiswa",
    "SELECT MIN(nama) AS min_nama, MAX(nama) AS max_nama FROM mahasiswa",
    (
        "SELECT a.nim, b.nim AS pasangan FROM mahasiswa a INNER JOIN mahasiswa b ON a.kota = "
        "b.kota WHERE a.nim = 22004 ORDER BY b.nim"
    ),
    (
        "SELECT a.nim, b.nim AS pasangan FROM mahasiswa a INNER JOIN mahasiswa b ON a.jurusan = "
        "b.jurusan WHERE a.nim = 22001 ORDER BY b.nim"
    ),
    "SELECT nama FROM mahasiswa WHERE nama LIKE 'A%' ORDER BY nama",
    "SELECT nama FROM mahasiswa WHERE nama NOT LIKE 'A%' ORDER BY nama",
    "SELECT nama FROM mahasiswa WHERE nama LIKE '%a%' ORDER BY nama",
    "SELECT nama FROM mahasiswa WHERE nama LIKE '_hi%' ORDER BY nama",
    "SELECT nama FROM mahasiswa WHERE kota LIKE 'Jak%' ORDER BY nama",
    "SELECT kode, nama_mk AS x FROM mata_kuliah WHERE nama_mk LIKE 'B%g_s' ORDER BY kode",
    "SELECT nim FROM mahasiswa WHERE ipk IN (3.5, 3.8, 3.9) ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk IN (NULL, 3.5) ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE jurusan NOT IN ('TI', 'SI') ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE jurusan NOT IN (NULL, 'TI') ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk BETWEEN 3.0 AND 3.5 ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk NOT BETWEEN 3.0 AND 3.5 ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE ipk BETWEEN NULL AND 3.5 ORDER BY nim",
    "SELECT nama FROM mahasiswa WHERE nama BETWEEN 'B' AND 'D' ORDER BY nama",
    (
        "SELECT COUNT(*) AS c, SUM(ipk) AS s, AVG(ipk) AS a, MIN(ipk) AS mn, MAX(ipk) AS mx FROM "
        "mahasiswa"
    ),
    "SELECT COUNT(ipk) AS c2, COUNT(*) AS c FROM mahasiswa",
    (
        "SELECT jurusan, COUNT(*) AS c, AVG(ipk) AS a, MIN(nama) AS mn, MAX(nama) AS mx FROM "
        "mahasiswa GROUP BY jurusan ORDER BY jurusan"
    ),
    (
        "SELECT nim, COUNT(*) AS c, SUM(skor) AS s, AVG(skor) AS a, MIN(skor) AS mn, MAX(skor) AS "
        "mx FROM nilai GROUP BY nim ORDER BY nim"
    ),
    (
        "SELECT COUNT(*) AS c, SUM(skor) AS s, AVG(skor) AS a, MIN(skor) AS mn, MAX(skor) AS mx "
        "FROM nilai"
    ),
    (
        "SELECT COUNT(*) AS c, SUM(ipk) AS s, AVG(ipk) AS a, MIN(ipk) AS mn, MAX(ipk) AS mx FROM "
        "mahasiswa WHERE 1 = 0"
    ),
    "SELECT COUNT(*) AS c, SUM(ipk) AS s FROM mahasiswa WHERE nama = 'Tidak Ada'",
    "SELECT COUNT(*) AS c FROM mahasiswa WHERE ipk IS NULL",
    "SELECT kode, COUNT(*) AS c FROM nilai GROUP BY kode ORDER BY kode",
    "SELECT jurusan, COUNT(*) AS c FROM mahasiswa GROUP BY jurusan ORDER BY jurusan",
    (
        "SELECT m.nim, m.nama, n.kode, n.skor FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim "
        "ORDER BY m.nim, n.kode"
    ),
    (
        "SELECT m.nim, m.nama, n.kode FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim ORDER BY "
        "m.nim, n.kode"
    ),
    (
        "SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim WHERE n.nim IS NULL "
        "ORDER BY m.nama"
    ),
    (
        "SELECT a.nim, b.nim AS pasangan FROM mahasiswa a INNER JOIN mahasiswa b ON a.jurusan = "
        "b.jurusan AND a.kota = b.kota ORDER BY a.nim, b.nim"
    ),
    (
        "SELECT m.nama, n.kode, k.nama_mk FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim "
        "INNER JOIN mata_kuliah k ON n.kode = k.kode ORDER BY m.nim, n.kode"
    ),
    (
        "SELECT n.nim, n.kode, m.nama FROM nilai n LEFT JOIN mahasiswa m ON n.nim = m.nim ORDER BY "
        "n.nim, n.kode"
    ),
    "SELECT nim, nama, ipk FROM mahasiswa ORDER BY ipk DESC, nim",
    "SELECT nim, ipk, jurusan FROM mahasiswa ORDER BY jurusan, ipk DESC, nim",
    "SELECT DISTINCT jurusan FROM mahasiswa ORDER BY jurusan",
    "SELECT DISTINCT nim, kode FROM nilai ORDER BY nim, kode",
    "SELECT DISTINCT skor FROM nilai ORDER BY skor",
    "SELECT nim, nama FROM mahasiswa ORDER BY nim LIMIT 5",
    "SELECT nim, nama FROM mahasiswa ORDER BY nim LIMIT 3, 4",
    "SELECT nim, nama FROM mahasiswa ORDER BY nim LIMIT 5 OFFSET 3",
    "SELECT nim, nama FROM mahasiswa ORDER BY nim LIMIT 0",
    "SELECT nim, nama FROM mahasiswa ORDER BY nim LIMIT 100 OFFSET 20",
    "SELECT * FROM mahasiswa ORDER BY nim",
    "SELECT k.* FROM mata_kuliah k ORDER BY k.kode",
    (
        "SELECT m.*, n.skor FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim ORDER BY m.nim, "
        "n.kode"
    ),
    "SELECT * FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim ORDER BY m.nim, n.kode",
    (
        "SELECT jurusan AS j, COUNT(*) AS jumlah FROM mahasiswa GROUP BY jurusan HAVING jumlah > 1 "
        "ORDER BY jumlah DESC, j"
    ),
    "SELECT jurusan AS j, COUNT(*) AS jumlah FROM mahasiswa GROUP BY j ORDER BY j",
    "SELECT nim, ipk * 2 AS ganda FROM mahasiswa ORDER BY ganda DESC, nim",
    (
        "SELECT m.jurusan AS j, COUNT(*) AS jumlah, AVG(n.skor) AS rata FROM mahasiswa m INNER "
        "JOIN nilai n ON m.nim = n.nim GROUP BY m.jurusan HAVING jumlah > 2 ORDER BY rata DESC, j"
    ),
    (
        "SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata FROM mahasiswa m INNER JOIN "
        "nilai n ON m.nim = n.nim GROUP BY m.jurusan HAVING COUNT(*) > 2 ORDER BY rata DESC, "
        "m.jurusan"
    ),
    "SELECT nim, skor FROM nilai WHERE skor > 80 AND kode = 'MK001' ORDER BY nim",
    "SELECT nama FROM mahasiswa WHERE ipk IS NOT NULL AND kota IS NOT NULL ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE 'a%' LIKE 'a\\%' AND 'a_b' LIKE 'a\\_b' ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE 'axb' LIKE 'a\\_b' AND 'a' LIKE '_' ORDER BY nim",
    "SELECT nim FROM mahasiswa WHERE NOT ('a' LIKE 'a\\%') ORDER BY nim",
    "SELECT kode FROM mata_kuliah WHERE kode LIKE 'MK00\\_1' ORDER BY kode",
    "SELECT kode FROM mata_kuliah WHERE kode LIKE 'MK00__' ORDER BY kode",
    (
        "SELECT kode, sks / 0 AS nol, sks DIV 0 AS bulat, sks % 0 AS sisa FROM mata_kuliah ORDER "
        "BY kode"
    ),
    "SELECT nim, nama FROM mahasiswa WHERE ipk > 3.0 ORDER BY ipk DESC, nim LIMIT 3",
    (
        "SELECT m.jurusan AS j, COUNT(*) AS jml FROM mahasiswa m GROUP BY j HAVING jml > 2 ORDER "
        "BY jml DESC, j"
    ),
    "SELECT nim, nama FROM mahasiswa WHERE nama LIKE 'A%' AND ipk IS NULL ORDER BY nim",
    "SELECT nim, nama FROM mahasiswa WHERE jurusan IS NULL OR kota IS NULL ORDER BY nim",
    "SELECT COUNT(*) AS c FROM mahasiswa WHERE ipk BETWEEN 3.0 AND 4.0 AND jurusan IN ('TI', 'MI')",
    "SELECT nim, skor + 1 AS naik FROM nilai WHERE skor IS NOT NULL ORDER BY nim, kode LIMIT 5",
    ]


@pytest.mark.mysql
def test_lingkungan_oracle_mysql_84() -> None:
    """Versi server diawali ``8.4`` dan ``sql_mode`` persis seperti ``SPEC.md`` §2."""
    facts = server_facts()
    assert facts.version.startswith("8.4"), f"versi server {facts.version!r} bukan 8.4."
    assert facts.sql_mode_set == EXPECTED_SQL_MODE, (
        f"@@sql_mode {facts.sql_mode!r} berbeda dari SPEC.md §2: {sorted(EXPECTED_SQL_MODE)}."
    )
    assert facts.collation_server == "utf8mb4_0900_ai_ci"
    assert facts.character_set_server == "utf8mb4"


@pytest.mark.mysql
def test_data_csv_identik_dengan_tabel_mysql() -> None:
    """Isi ``examples/*.csv`` identik dengan isi tabel MySQL (baris sebagai multiset)."""
    from_mysql = load_tables_mysql(DEFAULT_DSN, TABLE_NAMES, SCHEMA)
    for name in TABLE_NAMES:
        assert name in from_mysql, f"tabel {name!r} tidak ada pada server oracle."
        problems = compare_frames(TABLES[name], from_mysql[name])
        assert not problems, f"{name}: " + "; ".join(problems)


@pytest.mark.mysql
def test_jumlah_query_memenuhi_cakupan_minimal() -> None:
    """Daftar cakupan ``SPEC.md`` §10 membutuhkan minimal 45 query diferensial."""
    assert len(QUERIES) >= 45, f"hanya ada {len(QUERIES)} query diferensial."


@pytest.mark.mysql
@pytest.mark.parametrize(
    "sql",
    QUERIES,
    ids=[f"q{position:03d}" for position in range(1, len(QUERIES) + 1)],
)
def test_diferensial_mysql(sql: str) -> None:
    """Hasil kompilator sama dengan hasil MySQL 8.4 untuk ``sql``."""
    result = compile_sql(sql, SCHEMA)
    assert not result.errors, f"{sql}\n" + "\n".join(str(item) for item in result.errors)
    frame = run_compiled(result.code, TABLES)
    columns, rows = fetch(sql)
    ordered = result.ast is not None and bool(result.ast.order_by)
    problems = compare_query(columns, rows, frame, ordered)
    assert not problems, f"{sql}\n" + "\n".join(problems)
