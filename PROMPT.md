# PROMPT.md: Rangkaian Prompt Pembangunan Kompiler Query MySQL ke Python

Dokumen ini berisi rangkaian prompt bertahap untuk membangun proyek yang didefinisikan dalam `SPEC.md`. Kedua berkas berada di folder yang sama, dan folder itu adalah **akar proyek**.

---

## Bagian A. Untuk Manusia: Cara Memakai

1. **Nama berkas harus persis `SPEC.md` dan `PROMPT.md`** (huruf besar). Seluruh prompt merujuk nama itu, dan di Linux/macOS huruf besar-kecil berpengaruh.
2. **Satu langkah per sesi.** Perintah ke AI untuk memulai setiap langkah:

   > Baca `SPEC.md`, Bagian B ("Aturan Kerja Global") di `PROMPT.md`, dan `PROGRESS.md` (jika ada). Kerjakan LANGKAH N dari `PROMPT.md`, hanya langkah itu, lalu berhenti.

3. **Gerbang antar langkah (wajib, dilakukan olehmu, bukan AI):**
   - Jalankan sendiri perintah di bagian "Verifikasi" langkah tersebut dan lihat hasilnya dengan mata sendiri.
   - Baca diff kode. Kalau ada bagian yang tidak kamu pahami, tanyakan sebelum lanjut.
   - Commit ke git. Baru mulai langkah berikutnya.
4. **Jangan menyuruh AI mengerjakan semua langkah sekaligus.** Itu mengulang masalah awal (AI kehilangan arah dan kesalahan menumpuk tanpa terlihat).
5. Jika AI melaporkan konflik atau ambiguitas pada `SPEC.md`, **kamu yang memutuskan**, ubah `SPEC.md` secara eksplisit, lalu lanjutkan.
6. **Prasyarat mesin:** Python 3.12 atau lebih baru, Docker dengan Docker Compose (untuk MySQL 8.4), Git. XAMPP/MariaDB **tidak memenuhi syarat** sebagai oracle.
7. Template untuk situasi khusus (tes gagal, konflik spesifikasi, sesi terputus, audit independen) ada di Bagian D.

---

## Bagian B. Aturan Kerja Global (wajib dipatuhi AI di setiap langkah)

1. **`SPEC.md` adalah satu-satunya sumber kebenaran.** Jangan mengubahnya tanpa persetujuan eksplisit dari manusia. Jika menemukan kontradiksi, ambiguitas, atau kekurangan: berhenti pada bagian itu, laporkan, usulkan perbaikan, dan tunggu keputusan. Jangan menebak diam-diam.
2. **Kerjakan hanya langkah yang diminta.** Jangan menambah fitur, merapikan modul lain, atau mengerjakan langkah berikutnya. Setelah selesai, **berhenti dan tunggu perintah**.
3. **Dilarang melemahkan tes agar lulus:** tidak boleh mengubah, menghapus, mengurangi toleransi, atau men-*skip* tes untuk menghilangkan kegagalan. Kegagalan berarti ada bug di kode. Jika menurut `SPEC.md` tesnya sendiri yang salah, laporkan dulu dan jangan ubah sebelum disetujui.
4. **Oracle adalah MySQL 8.4 sungguhan.** Dilarang menggantinya dengan SQLite, mock, hasil yang ditulis manual, atau menyalin keluaran kompiler sebagai "hasil yang diharapkan". Tes bertanda `mysql` tidak boleh di-*skip*. Jika server tidak tersedia, **berhenti dan laporkan**, jangan mengakali.
5. **Lexer dan parser ditulis manual.** Dilarang memakai `sqlparse`, `sqlglot`, `lark`, `ply`, `pyparsing`, `antlr`, atau library parsing/SQL sejenis. Dilarang menambah dependensi di luar yang tercantum di `SPEC.md` §17 tanpa izin.
6. **Laporan harus jujur dan berbukti.** Jangan menyatakan "berhasil", "lulus", atau "sudah berjalan" tanpa menjalankan perintahnya. Tempel keluaran asli `pytest`, `ruff`, dan `mypy` pada laporan.
7. **Kualitas kode:** type hints lengkap, `@dataclass(frozen=True)` untuk AST/IR/Token, tanpa state global yang berubah, `ruff check .` dan `mypy --strict src` bersih. Identifier kode memakai bahasa Inggris; komentar, docstring, dan pesan error untuk pengguna memakai bahasa Indonesia.
8. **Tidak melakukan `git commit` atau `git push`.** Manusia yang melakukannya.
9. **Perbarui `PROGRESS.md` di akhir setiap langkah** (format di Lampiran): yang selesai, keputusan desain, masalah terbuka, deviasi dari spesifikasi.
10. **Akar proyek adalah folder tempat `SPEC.md` berada.** Jangan membuat folder pembungkus tambahan bernama `sqlcompiler/`; struktur pada `SPEC.md` §17 dimulai dari akar tersebut.

### Format Laporan Standar (akhir setiap langkah)

```
## Laporan Langkah N
1. Ringkasan (maks 5 baris)
2. File dibuat/diubah (daftar)
3. Bukti verifikasi: keluaran asli pytest / ruff / mypy (dan perintah khusus langkah ini)
4. Keputusan desain yang diambil beserta alasannya
5. Deviasi dari SPEC.md, kontradiksi, atau ambiguitas yang ditemukan (kosongkan jika tidak ada)
6. Hal yang perlu ditinjau manusia sebelum lanjut
```

---

## Bagian C. Langkah-langkah

### LANGKAH 0: Kerangka proyek, lingkungan, dan data uji

**Tujuan.** Memahami spesifikasi, menyiapkan kerangka proyek, server MySQL 8.4 untuk oracle, dan data uji. **Tidak ada logika kompiler pada langkah ini.**

**Tugas.**
1. Baca `SPEC.md` secara utuh. Tulis ringkasan pemahamanmu (maks 15 baris) dan **daftar ambiguitas, kontradiksi, atau hal yang belum terdefinisi** yang kamu temukan. Jangan memperbaikinya sendiri.
2. Periksa lingkungan: `python --version` (harus ≥ 3.12), `docker --version`, `docker compose version`, `git --version`. Jika Docker tidak tersedia **dan** variabel `SQLC_MYSQL_DSN` tidak menunjuk server MySQL 8.4 yang valid, **berhenti dan laporkan**.
3. Buat struktur proyek persis seperti `SPEC.md` §17 (layout `src/`), dengan setiap modul berisi docstring tujuan saja (belum ada implementasi).
4. Buat `pyproject.toml`: Python ≥ 3.12; dependensi runtime `pandas`, `pymysql`, `typer`, `rich`, `streamlit`; dependensi dev `pytest`, `pytest-cov`, `ruff`, `mypy`; skrip `sqlc = "sqlcompiler.cli:app"`; konfigurasi `ruff` (line-length 100), `mypy` (strict), `pytest` (daftarkan marker `mysql`), dan `coverage`.
5. Buat `docker-compose.yml`: image `mysql:8.4`, argumen server `--character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci`, port host **3307**, password root `root`, database `sqlc_test`, volume yang memasang `examples/mysql_init.sql` ke `/docker-entrypoint-initdb.d/`, dan `healthcheck`.
6. Buat `examples/mysql_init.sql` sebagai **sumber kebenaran data uji**. Syarat:
   - Tiga tabel sesuai `SPEC.md` §7, termasuk kolom `kota` pada `mahasiswa`. Tipe: `BIGINT` untuk INT, `DOUBLE` untuk DOUBLE, `VARCHAR(100)` untuk TEXT. Semua tabel `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci`. **Tanpa** `PRIMARY KEY`, `FOREIGN KEY`, atau `UNIQUE`.
   - Seluruh teks ASCII.
   - `mahasiswa` ≥ 12 baris: nilai `jurusan` berupa `TI`, `SI`, `MI` (seragam huruf besar) dan beberapa NULL; beberapa `ipk` NULL; nama yang diawali `A`/`a` dengan variasi; kolom `kota` memuat varian huruf (`Jakarta`, `jakarta`, `Bandung`) dan NULL; beberapa mahasiswa **tanpa** baris di `nilai`.
   - `mata_kuliah` ≥ 5 baris dengan `sks` hanya bernilai 2, 3, atau 4 (tanpa NULL; duplikat nilai boleh).
   - `nilai` ≥ 25 baris: beberapa `skor` NULL; **satu atau lebih baris dengan `nim` NULL**; satu grup (jurusan) yang seluruh `skor`-nya NULL; sebagian mahasiswa punya ≥ 3 nilai.
   - Varian huruf besar-kecil **hanya** pada kolom `kota` (lihat `SPEC.md` §16, "Aturan dataset").
7. Buat `examples/schema.json` (sesuai `SPEC.md` §7) dan `examples/mahasiswa.csv`, `examples/mata_kuliah.csv`, `examples/nilai.csv` yang **isinya identik** dengan `mysql_init.sql` (sel kosong = NULL).
8. Buat `.gitignore`, `README.md` kerangka (judul dan cara menjalankan lingkungan), dan `PROGRESS.md`.
9. Siapkan lingkungan: `python -m venv .venv`, aktifkan, `pip install -e ".[dev]"`.

**Batasan.** Jangan menulis lexer, parser, atau logika apa pun. Jangan memilih library parsing.

**Verifikasi (jalankan dan tempel keluarannya).**
```
docker compose up -d --wait
docker compose exec -T mysql mysql -uroot -proot -e "SELECT VERSION(), @@sql_mode, @@collation_server, @@character_set_server;"
docker compose exec -T mysql mysql -uroot -proot sqlc_test -e "SELECT COUNT(*) FROM mahasiswa; SELECT COUNT(*) FROM nilai; SELECT COUNT(*) FROM mata_kuliah;"
pytest -q
ruff check .
mypy --strict src
```

**Kriteria selesai.** Server berjalan dengan versi diawali `8.4` dan `sql_mode` sesuai `SPEC.md` §2; ketiga tabel terisi sesuai syarat; `pytest` tidak error (boleh 0 tes), `ruff` dan `mypy` bersih; ringkasan pemahaman dan daftar ambiguitas sudah dilaporkan.

---

### LANGKAH 1: Lexer

**Baca dulu.** `SPEC.md` §4 (leksikal), §9 (format error).

**Tugas.** Implementasikan `src/sqlcompiler/errors.py` dan `src/sqlcompiler/lexer.py`.
1. `errors.py`: hierarki `CompileError(code, message, line, column)` dengan turunan `LexError`, `ParseError` (memuat `expected`), `SemanticError`. Fungsi `render_error(error, source) -> str` yang menghasilkan format dengan penunjuk `^^^^` seperti `SPEC.md` §9. Definisikan kode `LEX001…` dalam sebuah tabel (enum atau konstanta) lengkap dengan arti masing-masing.
2. `lexer.py`: `TokenType` (enum), `Token(type, lexeme, value, line, column, offset)` dan `tokenize(source: str) -> list[Token]` yang diakhiri token `EOF`. Implementasi manual berbasis regex atau pemindaian karakter.
3. Patuhi seluruh aturan `SPEC.md` §4: keyword case-insensitive; `IDENT`/`QIDENT` dinormalisasi huruf kecil; backtick ganda ``` `` ``` di dalam `QIDENT`; `STRING` dengan kutip tunggal atau ganda, kutip digandakan, escape `\\ \' \" \n \t \0` (nilai hasil-dekode disimpan di `value`, escape lain = `LexError`); `INTEGER`/`FLOAT`; operator `!=` dinormalisasi menjadi `<>`; komentar `-- ` (**wajib** diikuti spasi, tab, atau akhir baris; `--x` bukan komentar), `#`, `/* */`.
4. Karakter yang tidak dikenal (termasuk `&`, `|`, `!` yang berdiri sendiri) menghasilkan `LexError` dengan pesan yang membantu, misalnya menjelaskan bahwa operator `&&`, `||`, `!` tidak didukung.

**Tes wajib** (`tests/test_lexer.py`, ≥ 20 kasus): keyword huruf campuran; identifier dan backtick; string dengan tiap escape dan kutip digandakan; kutip ganda sebagai string; angka bulat dan desimal; `!=`; setiap jenis komentar termasuk `--x` (bukan komentar) dan `-- x`; posisi `line/column/offset` untuk teks multi-baris; minimal 5 kasus error (string tak tertutup, backtick tak tertutup, komentar blok tak tertutup, escape tidak dikenal, karakter ilegal). Tambahkan satu tes *fuzz* sederhana: 500 string ASCII acak harus menghasilkan daftar token atau `LexError`, tidak pernah exception lain.

**Batasan.** Jangan menyentuh modul lain.

**Verifikasi.** `pytest tests/test_lexer.py -q`, `ruff check .`, `mypy --strict src`.

**Kriteria selesai.** Semua tes hijau; setiap aturan di `SPEC.md` §4 memiliki minimal satu tes; tabel kode `LEX***` terdokumentasi.

---

### LANGKAH 2: AST dan parser inti

**Baca dulu.** `SPEC.md` §5 (grammar & presedensi), §6 (AST).

**Tugas.** Implementasikan `ast_nodes.py` dan `parser.py` untuk **bagian inti saja**:
`SELECT [DISTINCT] daftar item`, `FROM satu tabel [alias]`, `WHERE`, ekspresi lengkap tingkat dasar (literal, kolom berkualifikasi, `+ - * / DIV %`, unary, perbandingan, `AND OR NOT`, tanda kurung, pemanggilan fungsi termasuk `COUNT(*)`), dan alias kolom dengan atau tanpa `AS`.
1. Semua node AST sesuai tabel `SPEC.md` §6 sebagai `@dataclass(frozen=True)`. Node untuk fitur yang baru dikerjakan di Langkah 3 boleh sudah didefinisikan.
2. `SelectItem.text` diisi dengan potongan teks sumber persis (gunakan `offset` token awal dan akhir ekspresi).
3. Parser recursive descent dengan presedensi persis `SPEC.md` §5. `ParseError(code, message, line, column, expected)` dengan pesan membantu, misalnya `Diharapkan FROM, ditemukan ','`.
4. `parse(tokens, source) -> SelectStatement` dan `pretty_print(ast) -> str`.

**Tes wajib** (`tests/test_parser.py`, ≥ 25 kasus): presedensi (`a + b * c`, `a - b - c` asosiatif kiri, `NOT a AND b OR c`, `a DIV b % c`, `-a * b`), alias dengan dan tanpa `AS`, `SELECT DISTINCT`, nama berkualifikasi, `COUNT(*)` vs `SUM(*)` (parser menerima; semantik yang menolak), `SelectItem.text` untuk ekspresi dengan spasi tak beraturan, serta ≥ 6 kasus error dengan posisi yang tepat (`SELECT` kosong, `FROM` hilang, kurung tak seimbang, `a < b < c`, teks setelah `;`, ekspresi menggantung).

**Batasan.** `JOIN`, `GROUP BY`, `HAVING`, `ORDER BY`, `LIMIT`, `LIKE`, `IN`, `BETWEEN`, `IS NULL` **belum** dikerjakan; query yang memakainya boleh menghasilkan `ParseError`.

**Verifikasi.** `pytest -q`, `ruff check .`, `mypy --strict src`.

**Kriteria selesai.** Semua tes (termasuk lexer) hijau; `pretty_print` menghasilkan pohon yang terbaca untuk query contoh.

---

### LANGKAH 3: Perluasan parser

**Baca dulu.** `SPEC.md` §5, §2 (bagian "Di luar scope").

**Tugas.** Perluas parser dan AST yang sudah ada (**jangan menulis ulang dari nol**) untuk seluruh grammar Level 2:
1. `[INNER] JOIN` dan `LEFT [OUTER] JOIN ... ON`, banyak join berurutan.
2. `GROUP BY`, `HAVING`, `ORDER BY` (`ASC`/`DESC`, multi-kolom), dan ketiga bentuk `LIMIT` (`LIMIT n`, `LIMIT a, b`, `LIMIT n OFFSET a`) dengan normalisasi ke `offset` dan `limit`.
3. `LIKE` (hanya literal `STRING` di sisi kanan), `IN (...)`, `BETWEEN ... AND ...`, `IS [NOT] NULL`, serta variasi `NOT LIKE/IN/BETWEEN`.
4. `alias.*` dan `*` di daftar `SELECT`.
5. **Menolak fitur di luar scope dengan pesan yang jelas** (bukan pesan generik): `RIGHT/FULL/CROSS JOIN`, join gaya koma (`FROM a, b`), subquery (`(SELECT ...)`), `UNION`, `WITH`, nama berkualifikasi database (`db.tabel`), `SELECT` tanpa `FROM`, `COUNT(DISTINCT ...)`, dan kata kunci DDL/DML.

**Tes wajib.** Tambahkan ≥ 25 tes baru sehingga total `tests/test_parser.py` ≥ 45: setiap klausa dan kombinasinya, query gabungan kompleks, `AND` dalam `BETWEEN` tidak dikira operator logika, tiga bentuk `LIMIT`, serta ≥ 10 kasus error termasuk satu kasus untuk **setiap** fitur di luar scope pada poin 5 (periksa pesan dan posisi).

**Batasan.** Semua tes lama harus tetap hijau tanpa dimodifikasi. Tambahkan node baru tanpa mengubah kontrak node lama.

**Verifikasi.** `pytest -q`, `ruff check .`, `mypy --strict src`.

**Kriteria selesai.** Seluruh grammar `SPEC.md` §5 dapat di-*parse*; semua fitur di luar scope menghasilkan error informatif; semua tes hijau.

---

### LANGKAH 4: Analisis semantik dan pemuat skema

**Baca dulu.** `SPEC.md` §7 (tipe), §8 (aturan & kode error), §9.

**Tugas.** Implementasikan `semantic.py` dan `schema_loader.py`.
1. `DataType` (`INT`, `DOUBLE`, `TEXT`, plus penanda `UNSUPPORTED`), `Table`, `Schema`. `schema_loader.py`: (a) `load_schema_json(path)`; (b) `introspect_mysql(dsn)` yang membaca `information_schema.COLUMNS` memakai `pymysql` dan memetakan tipe sesuai tabel `SPEC.md` §7 (`DECIMAL` → `DOUBLE` dengan peringatan). Urai DSN `mysql://user:pass@host:port/db` dengan `urllib.parse` (tanpa SQLAlchemy).
2. Penganalisis semantik yang mengimplementasikan **seluruh** SEM001–SEM016 dan aturan resolusi nama MySQL pada `SPEC.md` §8 (ORDER BY: alias dulu, lalu kolom; GROUP BY/HAVING: kolom dulu, lalu alias). AST bersifat *immutable*: hasilnya berupa struktur terpisah (misal `AnalysisResult` berisi AST, peta node → tipe, peta referensi kolom ke `alias.kolom`, daftar `SemanticError`, daftar peringatan) yang dapat dipakai langsung oleh tahap IR.
3. **Kumpulkan semua error**, jangan berhenti di error pertama. Pesan membantu, termasuk saran "Maksudmu '...'?" memakai `difflib.get_close_matches`.
4. Inferensi tipe sesuai tabel aturan tipe `SPEC.md` §7.

**Tes wajib** (`tests/test_semantic.py`, ≥ 30 kasus): setiap kode SEM001–SEM016 minimal sekali; resolusi alias (alias bernama sama dengan kolom pada `ORDER BY` vs `GROUP BY`); kolom ambigu pada join; `ONLY_FULL_GROUP_BY`; `ON` non-equi; `ORDER BY 1`; beberapa error sekaligus dalam satu query; query valid untuk seluruh contoh `SPEC.md` §14. Satu tes bertanda `mysql` yang membandingkan hasil `introspect_mysql` terhadap `examples/schema.json`.

**Batasan.** Jangan menyentuh parser kecuali menemukan bug (laporkan bila iya).

**Verifikasi.** `pytest -q` (termasuk tes `mysql`), `ruff check .`, `mypy --strict src`.

**Kriteria selesai.** Semua tes hijau; tidak ada kode SEM yang belum teruji.

---

### LANGKAH 5: Intermediate Representation

**Baca dulu.** `SPEC.md` §11, §10 (butir 4 dan 5 terkait folding dan teks).

**Tugas.** Implementasikan `ir.py`.
1. Dataclass operator IR sesuai `SPEC.md` §11 dan fungsi `lower(analysis) -> Plan` yang menghasilkan urutan **tetap** `Scan → Join* → Filter → Aggregate → Filter(HAVING) → Sort → Project → Distinct → Limit`.
2. Ekstraksi agregat menjadi `__aggN`, dan penulisan ulang referensi `SELECT`/`HAVING`/`ORDER BY` yang memakai agregat atau alias; `ORDER BY` alias di-*rewrite* ke ekspresi aslinya.
3. *Constant folding* sesuai aturan `SPEC.md` §11 (NULL, pembagi nol, pembulatan 4 desimal untuk `INT / INT`).
4. `format_plan(plan) -> str` (pohon teks) untuk `explain`.

**Tes wajib** (`tests/test_ir.py`, ≥ 15 kasus): urutan operator untuk berbagai kombinasi klausa; ekstraksi agregat berganda; rewrite alias; folding yang benar (`1 + 2`, `7 / 2` → `3.5`, `NULL + 1` → `NULL`, `x / 0` tidak dilipat salah); join dengan banyak kondisi `ON`.

**Batasan.** **Jangan** mengimplementasikan *predicate pushdown* pada langkah ini (opsional, hanya setelah Kriteria Selesai proyek terpenuhi).

**Verifikasi.** `pytest -q`, `ruff check .`, `mypy --strict src`.

**Kriteria selesai.** Semua tes hijau; `format_plan` terbaca untuk seluruh contoh `SPEC.md` §14.

---

### LANGKAH 6: Code generator

**Baca dulu.** `SPEC.md` §12, §13, §14, §10.

**Tugas.** Implementasikan `codegen.py` yang mengubah `Plan` menjadi string kode Python (pandas).
1. Patuhi pemetaan `SPEC.md` §12 dan konvensi §13: satu fungsi `run(tables)`, komentar `# SQL: ...` per blok, deterministik, fungsi bantu (misal pembagian bulat `DIV`/`%`) hanya dihasilkan bila dipakai.
2. Semantik wajib: logika tiga nilai dan `.fillna(False)` pada filter; `IN` sebagai rantai `OR`; `BETWEEN`; `LIKE` menjadi regex `(?is)\A...\Z` dengan semua karakter khusus di-*escape* dan dukungan `\%` `\_`; perbandingan/kunci teks tidak membedakan huruf (`.str.lower()`, literal dikecilkan saat kompilasi); `/` dibulatkan 4 desimal untuk `INT / INT`, pembagi nol → NULL; kunci NULL dibuang sebelum `merge`; `GROUP BY` memakai kunci bayangan `__k0…`; agregat tanpa `GROUP BY` selalu satu baris; urutan NULL (ASC awal, DESC akhir); `Project` memakai `pd.concat` bila nama keluaran tidak unik.
3. Enam contoh pada `SPEC.md` §14 menjadi *golden file* di `tests/golden/` (query `.sql` + keluaran `.py`).

**Tes wajib** (`tests/test_codegen.py`): ≥ 12 *golden test* (6 contoh spesifikasi + ≥ 6 lainnya yang mencakup `LIMIT a, b`, `NOT IN`, `NOT LIKE`, agregat tanpa `GROUP BY`, `LEFT JOIN` + `WHERE` pada sisi kiri, `SELECT *` dari join); setiap kode hasil harus lolos `compile()` Python; determinisme (dua kali kompilasi → teks identik).

**Batasan.** Tidak menulis `runtime.py` pada langkah ini. Boleh memakai `exec` sederhana **hanya di dalam tes** untuk uji kewarasan.

**Verifikasi.** `pytest -q`, `ruff check .`, `mypy --strict src`.

**Kriteria selesai.** Semua tes hijau; keluaran enam contoh spesifikasi cocok secara semantik dengan `SPEC.md` §14 (jika berbeda, jelaskan di laporan; jangan ubah `SPEC.md`).

---

### LANGKAH 7: Runtime, pemuat data, dan tes diferensial MySQL

**Baca dulu.** `SPEC.md` §3, §10 (seluruh butir), §13, §16.

**Tugas.**
1. `data_loader.py`: `load_tables_csv(dir)` dan `load_tables_mysql(dsn, table_names)` yang menghasilkan `dict[str, DataFrame]` dengan dtype **nullable** (`Int64`, `Float64`, `string`) sesuai skema; sel kosong/NULL → `pd.NA`.
2. `runtime.py`: `compile_sql(sql, schema) -> CompileResult` (field `tokens`, `ast`, `plan`, `code`, `errors`) dan `run_compiled(code, tables) -> DataFrame` yang mengeksekusi kode dengan namespace terbatas (hanya `pd`, builtins dibatasi).
3. **Harness oracle** (`tests/mysql_oracle.py`): menjalankan query yang sama di MySQL via `pymysql`, mengonversi `Decimal` ke `float`, dan **membandingkan berdasarkan posisi kolom**: baris sebagai multiset (kecuali ada `ORDER BY` → urutan dibandingkan), float toleransi `1e-9`, NULL = NULL.
4. `tests/test_differential.py`: **≥ 45 query** yang mencakup daftar cakupan di bawah. Sebelum semua tes diferensial, satu tes memverifikasi lingkungan (versi server diawali `8.4`, `sql_mode` sesuai `SPEC.md` §2); jika tidak sesuai, **gagal** (bukan skip). Tambahkan tes konsistensi: isi CSV identik dengan isi tabel MySQL.

**Daftar cakupan minimal (berdasarkan butir `SPEC.md` §10):**
- Butir 1–2: `AND/OR/NOT` dengan operand NULL, `WHERE` meloloskan hanya TRUE.
- Butir 3–4: aritmetika dengan NULL; `/` pada INT/INT (hasil 4 desimal, termasuk 1/3 dan 2/3), INT/DOUBLE, `DIV` dan `%` dengan operand negatif, pembagi nol → NULL.
- Butir 5: `=`, `<>`, `IN`, `BETWEEN`, `LIKE` pada `kota` (`Jakarta` vs `jakarta`); `ORDER BY` teks; `MIN/MAX` teks; **self-join** `mahasiswa a JOIN mahasiswa b ON a.kota = b.kota`.
- Butir 6: `%`, `_`, `\%`, `NOT LIKE`, operand NULL.
- Butir 7–8: `IN`/`NOT IN` yang memuat NULL, `BETWEEN`/`NOT BETWEEN`.
- Butir 9–10: seluruh lima agregat, grup yang seluruh nilainya NULL, agregat tanpa `GROUP BY` termasuk pada input kosong (misal `WHERE 1 = 0`-setara dengan kondisi yang tidak pernah benar).
- Butir 11: `GROUP BY` dengan kunci NULL.
- Butir 12: `INNER` dan `LEFT JOIN` dengan kunci NULL di `nilai.nim`, `ON` multi-kondisi, tiga tabel.
- Butir 13–15: `ORDER BY` ASC/DESC multi-kunci dengan NULL (selalu dengan kunci pembeda), `DISTINCT` satu dan multi-kolom dengan NULL, `LIMIT n`, `LIMIT a, b`, `LIMIT n OFFSET a`, `LIMIT 0`, offset melebihi jumlah baris.
- Butir 17: `SELECT *` dan `alias.*` dari join (nama kolom ganda, dibandingkan per posisi).
- Aturan resolusi alias `SPEC.md` §8: alias di `ORDER BY`, `HAVING`, dan `GROUP BY`.

**Aturan keras.** Jika hasil berbeda dari MySQL, **perbaiki kompiler**, bukan tesnya, dan bukan harness-nya agar lebih longgar. Laporkan tabel: query yang awalnya gagal | penyebab | modul yang diperbaiki.

**Verifikasi.** `docker compose up -d --wait`, `pytest -q` (seluruh tes, tanpa skip: tempel baris ringkasan `passed/skipped`), `ruff check .`, `mypy --strict src`.

**Kriteria selesai.** ≥ 45 query diferensial hijau dan **0 tes di-skip**; tabel kegagalan awal dilaporkan.

---

### LANGKAH 8: CLI

**Baca dulu.** `SPEC.md` §15, §9.

**Tugas.** Implementasikan `cli.py` (`typer` + `rich`): perintah `compile`, `run`, `explain`, `schema` sesuai `SPEC.md` §15, opsi `--schema`, `--dsn` (default dari variabel `SQLC_MYSQL_DSN`), `--data`, `--file`, dan exit code 0/1/2. Error leksikal/sintaks/semantik dirender dengan baris, kolom, dan penunjuk `^^^^` memakai `render_error`. `compile` mencetak kode Python tanpa hiasan tambahan agar dapat dialihkan ke berkas (`> hasil.py`).

**Tes wajib** (`tests/test_cli.py`, memakai `typer.testing.CliRunner`): setiap perintah pada jalur sukses; exit code untuk error kompilasi dan error runtime; format error dengan penunjuk; `--file`; satu tes bertanda `mysql` untuk `schema --dsn` dan `run --dsn`.

**Batasan.** CLI hanya memanggil `compile_sql`/`run_compiled`/loader; tidak menduplikasi logika kompilasi.

**Verifikasi.** `pytest -q`, `ruff check .`, `mypy --strict src`, dan contoh nyata: `sqlc compile "SELECT nama FROM mahasiswa WHERE ipk > 3" --schema examples/schema.json` (tempel keluarannya).

**Kriteria selesai.** Semua tes hijau; keluaran contoh terlampir.

---

### LANGKAH 9: Antarmuka Streamlit

**Baca dulu.** `SPEC.md` §1 (gambaran hasil akhir), §15.

**Tugas.** Implementasikan `app_streamlit.py` sesuai diagram `SPEC.md` §1:
- Kotak teks query MySQL dan tombol **Kompilasi**.
- **Panel utama = kode Python hasil kompilasi**, dengan tombol salin dan unduh `.py`.
- Tab pendukung: Token | AST | Semantik | Plan.
- Error ditampilkan dengan baris/kolom dan penunjuk.
- Tab verifikasi: tombol **Jalankan** menjalankan kode pada data (CSV atau MySQL, dipilih di sidebar) dan menampilkan hasil Python **berdampingan** dengan hasil MySQL beserta penanda cocok/tidak cocok.
- Skema dari berkas JSON atau introspeksi MySQL, dipilih di sidebar.

**Batasan.** UI hanya memanggil fasad `compile_sql` dan loader; tanpa CSS kustom berlebihan.

**Tes wajib.** `tests/test_ui.py` memakai `streamlit.testing.v1.AppTest`: aplikasi terbuka tanpa exception; memasukkan query valid menampilkan kode; query salah menampilkan pesan error; perbandingan "cocok" muncul untuk satu query contoh (tes `mysql`).

**Verifikasi.** `pytest -q`, `ruff check .`, `mypy --strict src`, dan `streamlit run src/sqlcompiler/app_streamlit.py --server.headless true` dijalankan beberapa detik tanpa error (tempel log awalnya).

**Kriteria selesai.** Semua tes hijau; aplikasi dapat dijalankan dari repositori bersih.

---

### LANGKAH 10: Audit dan pengerasan

**Tugas.** Audit seluruh repositori. **Jangan menambah fitur.**
1. Jalankan `ruff check .`, `mypy --strict src`, `pytest --cov=sqlcompiler --cov-report=term-missing` dan perbaiki semua temuan; cakupan harus ≥ 85%.
2. Cari bug pada kasus tepi dan tambahkan tesnya: string kosong, query hanya spasi/komentar, tabel kosong, nama kolom sama di dua tabel, alias bertabrakan dengan nama tabel, angka sangat besar, literal teks panjang, query dengan 5 join, identifier yang sama dengan keyword (dengan dan tanpa backtick).
3. Tinjau keamanan eksekusi kode hasil (`exec`) dan perkuat namespace terbatas; dokumentasikan batasannya (ini **bukan** sandbox keamanan yang kuat).
4. Verifikasi bahwa tidak ada library parsing terlarang (Aturan Kerja Global #5) di dependensi maupun impor.
5. Lengkapi `README.md`: cara memasang, `docker compose up`, cara menjalankan CLI/UI/tes, daftar fitur yang didukung, dan **keterbatasan yang diketahui** (sebut `SPEC.md` §20 dan catatan sandbox).
6. Laporkan temuan dalam tabel: masalah | lokasi | dampak | perbaikan.

**Verifikasi.** Keluaran asli dari seluruh perintah pada poin 1, ditambah ringkasan jumlah tes `passed/skipped/failed`.

**Kriteria selesai.** Lint, tipe, dan tes bersih; cakupan ≥ 85%; 0 tes di-skip; README lengkap.

---

### LANGKAH 11: Laporan teknis

**Tugas.** Buat `docs/LAPORAN_TEKNIS.md` dalam bahasa Indonesia formal, berisi: latar belakang dan tujuan; arsitektur dan diagram alir fase kompilasi (Mermaid); grammar EBNF final; rancangan tiap fase (lexer, parser, semantik, IR, code generator, runtime); desain AST dan IR; strategi pemetaan MySQL ke pandas; perbedaan semantik MySQL yang ditangani dan alasannya (kolasi, pembagian, NULL); strategi pengujian dan hasil uji diferensial (angka asli dari `pytest`); keterbatasan dan pengembangan lanjutan.

**Batasan.** Semua klaim harus sesuai kode yang ada; jangan menulis fitur yang belum diimplementasikan; semua angka (jumlah tes, cakupan) berasal dari keluaran perintah yang dijalankan saat itu.

**Kriteria selesai.** Dokumen lengkap, konsisten dengan `SPEC.md` dan kode; daftar bagian yang diragukan akurasinya dilaporkan.

---

## Bagian D. Template Prompt Tambahan

### D1. Tes diferensial gagal

> Tes diferensial berikut gagal: `<nama tes / query>`. Keluaran MySQL: `<...>`. Keluaran kompiler: `<...>`. Ikuti Aturan Kerja Global (terutama #3 dan #4). Temukan akar masalah, perbaiki di modul yang tepat, tambahkan tes regresi, dan laporkan: penyebab, perubahan, serta bukti `pytest` penuh.

### D2. Konflik atau ambiguitas pada SPEC

> Berhenti dan jangan menebak. Jelaskan konfliknya: kutipan bagian `SPEC.md` yang terkait (nomor bagian), dua atau lebih interpretasi yang mungkin, dampak tiap interpretasi pada kode dan tes, serta rekomendasimu. Tunggu keputusan sebelum melanjutkan.

### D3. Melanjutkan sesi yang terputus

> Baca `SPEC.md`, Bagian B `PROMPT.md`, dan `PROGRESS.md`. Periksa kondisi repositori (`git status`, `git diff --stat`, `pytest -q`). Laporkan apa yang sudah selesai dan apa yang setengah jadi untuk LANGKAH N. Tunggu konfirmasi sebelum melanjutkan.

### D4. Audit independen (sesi baru, tanpa konteks sebelumnya)

> Kamu adalah peninjau independen. Baca `SPEC.md` lalu tinjau kode di `src/` dan `tests/`. Cari: (a) perilaku yang menyimpang dari `SPEC.md` (sebut nomor bagian), (b) tes yang lemah, ter-*skip*, atau tidak benar-benar memverifikasi hasil terhadap MySQL, (c) query yang menurutmu akan menghasilkan hasil berbeda dari MySQL 8.4 beserta alasannya. Jangan memperbaiki apa pun; laporkan temuan dalam tabel: temuan | bukti | tingkat keparahan | saran.

---

## Lampiran: Templat PROGRESS.md

```
# PROGRESS

## Status langkah
| Langkah | Status | Tanggal | Catatan |
|---|---|---|---|
| 0 | belum | | |
| 1 | belum | | |
| ... | | | |

## Keputusan desain
- (langkah, keputusan, alasan)

## Deviasi / ambiguitas SPEC.md yang dilaporkan
- (bagian, isu, keputusan manusia)

## Masalah terbuka
- (deskripsi)
```
