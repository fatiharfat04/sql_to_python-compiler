# SPEC: Kompiler Query MySQL ke Python (MySQL → pandas), versi 2

Dokumen ini mendefinisikan tujuan, gambaran hasil akhir, ruang lingkup, grammar, semantik, representasi antara, dan strategi pengujian untuk aplikasi "Perancangan dan Implementasi Kompiler Query SQL ke Bahasa Pemrograman Python" dengan **dialek MySQL 8.4** sebagai acuan. Dokumen ini adalah **satu-satunya sumber kebenaran**. Jika kode dan dokumen ini bertentangan, perbaiki salah satunya secara eksplisit; jangan dibiarkan menyimpang.

---

## 1. Tujuan, Gambaran Hasil Akhir, dan Prinsip

**Tujuan.** Menerjemahkan satu pernyataan `SELECT` MySQL (subset, lihat §2) menjadi kode Python berbasis pandas yang, ketika dijalankan pada data yang sama, menghasilkan hasil identik dengan MySQL.

**Gambaran hasil akhir.**

```
┌─ INPUT ─────────────────────────────────────────┐
│  Kotak teks: pengguna mengetik query MySQL      │
│  [ Kompilasi ]                                  │
└─────────────────────────────────────────────────┘
                     │
                     ▼
┌─ OUTPUT UTAMA ──────────────────────────────────┐
│  Kode program Python (pandas) hasil kompilasi   │
│     def run(tables): ...                        │
└─────────────────────────────────────────────────┘
  Tab pendukung : Token | AST | Semantik | Plan
  Tab verifikasi: [ Jalankan ] → hasil kode Python
                  berdampingan dengan hasil MySQL
```

Produk utama adalah **teks kode Python**. Menjalankan kode dan membandingkannya dengan MySQL adalah sarana verifikasi kebenaran kompiler, bukan produknya.

**Prinsip.**
1. **Kebenaran di atas fitur.** Subset kecil yang benar lebih bernilai daripada subset besar yang kadang salah.
2. **MySQL 8.4 adalah oracle.** Perilaku yang ambigu mengikuti MySQL (§10), divalidasi terhadap server MySQL sungguhan.
3. **Boleh lebih ketat, dilarang menyimpang.** Kompiler boleh *menolak* query yang valid di MySQL (dengan error yang jelas). Kompiler **tidak boleh menerima** query lalu menghasilkan hasil yang berbeda dari MySQL.
4. **Fase kompilasi eksplisit.** Lexer, parser, analisis semantik, IR, dan code generator adalah modul terpisah dengan kontrak data yang jelas.
5. **Lexer dan parser ditulis manual** (regex + recursive descent). Tidak memakai Lark/PLY/ANTLR.
6. **Tidak ada fitur di luar dokumen ini** tanpa mengubah dokumen ini terlebih dahulu.

---

## 2. Ruang Lingkup

**Lingkungan acuan.** MySQL 8.4 LTS, `sql_mode` bawaan (`ONLY_FULL_GROUP_BY, STRICT_TRANS_TABLES, NO_ZERO_IN_DATE, NO_ZERO_DATE, ERROR_FOR_DIVISION_BY_ZERO, NO_ENGINE_SUBSTITUTION`), charset `utf8mb4`, collation `utf8mb4_0900_ai_ci`. MariaDB (termasuk bawaan XAMPP) **bukan** acuan; perilakunya berbeda.

### Level 1: Inti (wajib, minimum lulus)
`SELECT`, `*`, kolom, ekspresi aritmetika (`+ - * /`), alias kolom, `FROM` satu tabel (alias opsional), `WHERE` dengan perbandingan (`= <> != < > <= >=`) dan `AND`/`OR`/`NOT`, `ORDER BY` (`ASC`/`DESC`, multi-kolom), `LIMIT`.

### Level 2: Target proyek (REKOMENDASI, dikerjakan penuh)
Semua Level 1 ditambah: `DISTINCT`, `alias.*`, `[INNER] JOIN`, `LEFT [OUTER] JOIN` (hanya equi-join), `LIKE`, `IN (daftar nilai)`, `BETWEEN`, `IS [NOT] NULL`, operator `DIV` dan `%`, `LIMIT offset, n` / `LIMIT n OFFSET offset`, identifier berkutip backtick, string berkutip tunggal/ganda, fungsi agregat `COUNT SUM AVG MIN MAX`, `GROUP BY`, `HAVING`.

### Level 3: Stretch (DILARANG dikerjakan sebelum seluruh kriteria §19 terpenuhi)
Urutan prioritas bila waktu tersisa: (1) `COUNT(DISTINCT x)`; (2) fungsi skalar `IFNULL`, `COALESCE`, `ROUND`, `UPPER`, `LOWER`, `CONCAT`; (3) `CASE WHEN`; (4) `JOIN ... USING`; (5) `IN (SELECT ...)`; (6) `UNION`; (7) literal `TRUE`/`FALSE`, operator `<=>`.

### Di luar scope (ditolak dengan error yang jelas)
CTE (`WITH`), window function, `RIGHT`/`FULL`/`CROSS JOIN`, join gaya koma (`FROM a, b`), non-equi join, subquery, DDL/DML, nama berkualifikasi database (`db.tabel`), `SELECT` tanpa `FROM`, operator `&& || !` (di MySQL `||` berarti OR), `ORDER BY`/`GROUP BY` berdasarkan nomor urut kolom, tipe tanggal/waktu/JSON/blob/enum, banyak statement sekaligus, parameter binding.

---

## 3. Pipeline Kompilasi

```
SQL MySQL (str)
  │  Lexer           lexer.py        → list[Token]           (LexError)
  ▼  Parser          parser.py       → SelectStatement (AST)  (ParseError)
  ▼  Semantic        semantic.py     → AST teranotasi tipe    (list[SemanticError])
  ▼  IR lowering     ir.py           → Logical Plan
  ▼  Optimizer       ir.py           → Logical Plan (constant folding; pushdown opsional)
  ▼  Code generator  codegen.py      → str (kode Python/pandas)   ← OUTPUT UTAMA
  ▼  Runtime         runtime.py      → pandas.DataFrame           ← verifikasi
```

Fungsi fasad: `compile_sql(sql: str, schema: Schema) -> CompileResult` dengan field `tokens`, `ast`, `plan`, `code`, `errors`. CLI dan UI hanya memanggil fasad ini; tidak boleh menduplikasi logika kompilasi.

**Sumber skema:** (a) berkas JSON, atau (b) introspeksi server MySQL lewat `information_schema.COLUMNS` (`schema_loader.py`).
**Sumber data (untuk menjalankan/verifikasi):** CSV, atau dibaca langsung dari MySQL menjadi `dict[str, DataFrame]` (`data_loader.py`).

---

## 4. Spesifikasi Leksikal

**Aturan umum.** Keyword dan identifier *case-insensitive*; identifier dinormalisasi ke huruf kecil (kompiler memperlakukan nama tabel dan kolom tanpa membedakan huruf besar/kecil; ini lebih longgar dari MySQL di Linux untuk nama tabel, dan dicatat sebagai keterbatasan). Whitespace diabaikan. Setiap token membawa `line`, `column` (basis 1), dan `offset` (posisi karakter) untuk mengambil potongan teks sumber.

| Kelas token | Pola | Catatan |
|---|---|---|
| KEYWORD | `SELECT DISTINCT FROM WHERE GROUP BY HAVING ORDER LIMIT OFFSET ASC DESC AS JOIN INNER LEFT OUTER ON AND OR NOT LIKE IN BETWEEN IS NULL DIV` | Hanya dapat dipakai sebagai identifier bila diberi backtick |
| IDENT | `[A-Za-z_][A-Za-z0-9_$]*` | Nama agregat (`COUNT`, dst.) adalah IDENT; parser mengenalinya bila diikuti `(` |
| QIDENT | `` `...` `` | Backtick di dalam nama ditulis ganda ``` `` ```; dinormalisasi seperti IDENT |
| INTEGER | `[0-9]+` | Tanpa tanda; tanda ditangani sebagai operator unary |
| FLOAT | `[0-9]+\.[0-9]+` | Tanpa eksponen |
| STRING | `'...'` atau `"..."` | Kutip ganda di MySQL adalah **string**, bukan identifier. Kutip yang sama digandakan (`''`, `""`) = satu kutip. Escape backslash yang didukung: `\\ \' \" \n \t \0`; lainnya = `LexError` |
| OPERATOR | `= <> != < > <= >= + - * / %` | `!=` dinormalisasi menjadi `<>` |
| PUNCT | `( ) , . ;` | `*` di `SELECT *` dan `COUNT(*)` adalah token operator yang sama |
| EOF | | Token terakhir |

**Komentar (diabaikan):** `-- ` (dua tanda minus **diikuti spasi/tab/akhir baris**; `--x` bukan komentar), `# sampai akhir baris`, `/* ... */`.

**Error leksikal** (`LexError`): karakter tidak dikenal, string/backtick/komentar blok tidak ditutup, escape tidak dikenal.

---

## 5. Grammar (EBNF)

Notasi: `[ ]` opsional, `{ }` pengulangan nol atau lebih, `|` alternatif, huruf kapital = token. `ident` berarti `IDENT | QIDENT`.

```ebnf
query        = select_stmt [ ";" ] EOF ;

select_stmt  = "SELECT" [ "DISTINCT" ] select_list
               "FROM" table_ref { join_clause }
               [ "WHERE" expr ]
               [ "GROUP" "BY" expr { "," expr } ]
               [ "HAVING" expr ]
               [ "ORDER" "BY" order_item { "," order_item } ]
               [ limit_clause ] ;

select_list  = select_item { "," select_item } ;
select_item  = "*"
             | ident "." "*"
             | expr [ [ "AS" ] ident ] ;           (* AS opsional, seperti MySQL *)

table_ref    = ident [ [ "AS" ] ident ] ;
join_clause  = [ "INNER" | "LEFT" [ "OUTER" ] ] "JOIN" table_ref "ON" expr ;

order_item   = expr [ "ASC" | "DESC" ] ;           (* default ASC *)
limit_clause = "LIMIT" INTEGER [ ( "," | "OFFSET" ) INTEGER ] ;
             (* "LIMIT a, b"  = lewati a, ambil b
                "LIMIT a OFFSET b" = ambil a, lewati b *)

expr         = or_expr ;
or_expr      = and_expr { "OR" and_expr } ;
and_expr     = not_expr { "AND" not_expr } ;
not_expr     = "NOT" not_expr | predicate ;

predicate    = additive [ comp_op additive
                        | [ "NOT" ] "LIKE" STRING
                        | [ "NOT" ] "IN" "(" expr { "," expr } ")"
                        | [ "NOT" ] "BETWEEN" additive "AND" additive
                        | "IS" [ "NOT" ] "NULL" ] ;

comp_op      = "=" | "<>" | "<" | ">" | "<=" | ">=" ;

additive     = term { ( "+" | "-" ) term } ;
term         = unary { ( "*" | "/" | "DIV" | "%" ) unary } ;
unary        = ( "-" | "+" ) unary | primary ;

primary      = INTEGER | FLOAT | STRING | "NULL"
             | func_call
             | column_ref
             | "(" expr ")" ;

func_call    = IDENT "(" ( "*" | expr ) ")" ;      (* "*" hanya valid untuk COUNT *)
column_ref   = [ ident "." ] ident ;
```

**Catatan grammar.**
- `AND` di dalam `BETWEEN a AND b` milik `BETWEEN`.
- Operand kanan `LIKE` dibatasi literal `STRING` agar pola dapat dikompilasi ke regex saat kompilasi.
- Perbandingan tidak berantai: `a < b < c` adalah error sintaks.
- `JOIN` tanpa `INNER`/`LEFT` = `INNER JOIN` (seperti MySQL).
- Teks setelah `;` adalah error sintaks.

### Presedensi (tinggi → rendah)
1. `( )`, literal, kolom, pemanggilan fungsi
2. unary `+ -`
3. `* / DIV %`
4. `+ -`
5. `= <> < > <= >=`, `LIKE`, `IN`, `BETWEEN`, `IS NULL`
6. `NOT`
7. `AND`
8. `OR`

---

## 6. AST

Semua node adalah `@dataclass(frozen=True)` dengan field `line`, `column`.

| Node | Field |
|---|---|
| `SelectStatement` | `distinct: bool`, `items: list[SelectItem \| Star]`, `from_: TableRef`, `joins: list[Join]`, `where: Expr \| None`, `group_by: list[Expr]`, `having: Expr \| None`, `order_by: list[OrderItem]`, `limit: int \| None`, `offset: int` (default 0) |
| `SelectItem` | `expr: Expr`, `alias: str \| None`, `text: str` (potongan teks sumber persis, dipakai sebagai nama kolom hasil) |
| `Star` | `table: str \| None` (`None` = semua tabel; selain itu `alias.*`) |
| `TableRef` | `name: str`, `alias: str \| None` |
| `Join` | `kind: "INNER" \| "LEFT"`, `table: TableRef`, `on: Expr` |
| `OrderItem` | `expr: Expr`, `descending: bool` |
| `Column` | `table: str \| None`, `name: str` |
| `Literal` | `value: int \| float \| str \| None`, `kind: "INT" \| "DOUBLE" \| "TEXT" \| "NULL"` |
| `UnaryOp` | `op: "-" \| "+" \| "NOT"`, `operand: Expr` |
| `BinaryOp` | `op: str` (`+ - * / DIV % = <> < > <= >= AND OR`), `left: Expr`, `right: Expr` |
| `Like` | `operand: Expr`, `pattern: str`, `negated: bool` |
| `InList` | `operand: Expr`, `values: list[Expr]`, `negated: bool` |
| `Between` | `operand: Expr`, `low: Expr`, `high: Expr`, `negated: bool` |
| `IsNull` | `operand: Expr`, `negated: bool` |
| `FunctionCall` | `name: str`, `arg: Expr \| Star` |

Parser menyediakan `pretty_print(ast) -> str` (pohon berindentasi) untuk debugging dan UI. `LIMIT a, b` dinormalisasi menjadi `offset=a, limit=b`.

---

## 7. Skema dan Sistem Tipe

**Tipe kolom:** `INT`, `DOUBLE`, `TEXT`. Ekspresi boolean hanya ada di dalam `WHERE`/`HAVING`/`ON`.

**Skema JSON:**

```json
{
  "mahasiswa":   {"nim": "INT", "nama": "TEXT", "jurusan": "TEXT", "ipk": "DOUBLE", "kota": "TEXT"},
  "mata_kuliah": {"kode": "TEXT", "nama_mk": "TEXT", "sks": "INT"},
  "nilai":       {"nim": "INT", "kode": "TEXT", "skor": "DOUBLE"}
}
```

**Pemetaan tipe MySQL → tipe kompiler** (introspeksi):

| Tipe MySQL | Tipe kompiler |
|---|---|
| `TINYINT SMALLINT MEDIUMINT INT BIGINT` (termasuk `UNSIGNED`) | `INT` |
| `FLOAT DOUBLE` | `DOUBLE` |
| `DECIMAL(p,s)` | `DOUBLE` + peringatan "presisi desimal hilang" |
| `CHAR VARCHAR TINYTEXT TEXT MEDIUMTEXT LONGTEXT` | `TEXT` |
| lainnya (`DATE`, `DATETIME`, `JSON`, `BLOB`, `ENUM`, …) | `UNSUPPORTED` (error SEM016 bila direferensikan) |

**Pemetaan dtype pandas (nullable, wajib):** `INT → "Int64"`, `DOUBLE → "Float64"`, `TEXT → "string"`. NULL = `pd.NA`; sel kosong pada CSV = NULL. Dilarang memakai `int64`/`float64`/`object` untuk data tabel.

**Aturan tipe ekspresi.**

| Ekspresi | Aturan | Tipe hasil |
|---|---|---|
| `+ - *` | Kedua operand numerik | INT jika keduanya INT; selain itu DOUBLE |
| `/` | Kedua operand numerik | DOUBLE (INT/INT dibulatkan 4 desimal, lihat §10.4) |
| `DIV`, `%` | Kedua operand INT | INT |
| unary `- +` | Numerik | tipe operand |
| `= <> < > <= >=` | Kedua operand numerik, atau keduanya TEXT | boolean |
| `LIKE` | Operand TEXT | boolean |
| `IN`, `BETWEEN` | Operand dan nilai-nilainya sepadan seperti perbandingan | boolean |
| `IS NULL` | Operand apa pun | boolean |
| `AND OR NOT` | Operand boolean | boolean |
| `COUNT(x)`, `COUNT(*)` | `x` tipe apa pun | INT |
| `SUM(x)` | `x` numerik | tipe `x` |
| `AVG(x)` | `x` numerik | DOUBLE (dibulatkan 4 desimal bila `x` INT) |
| `MIN(x)`, `MAX(x)` | `x` numerik atau TEXT | tipe `x` |
| Literal `NULL` | Cocok dengan tipe apa pun dalam konteksnya | NULL |

> Kompiler **menolak** perbandingan lintas tipe (misal `nim = '123'`) yang di MySQL diselesaikan lewat konversi implisit. Ini penerapan Prinsip 3.

---

## 8. Aturan Semantik dan Kode Error

Analisis semantik **mengumpulkan semua error** (tidak berhenti di yang pertama).

| Kode | Kondisi | Contoh pesan |
|---|---|---|
| SEM001 | Tabel tidak ada di skema | `Tabel 'mahasisw' tidak ditemukan. Maksudmu 'mahasiswa'?` |
| SEM002 | Kolom tidak ada | `Kolom 'ipkk' tidak ditemukan di 'mahasiswa'.` |
| SEM003 | Kolom ambigu (ada di >1 tabel pada FROM/JOIN, atau alias SELECT ganda yang direferensikan) | `Kolom 'nim' ambigu: ada di m dan n.` |
| SEM004 | Alias/nama tabel duplikat | `Alias 'm' dipakai dua kali.` |
| SEM005 | Tipe tidak cocok pada operator/perbandingan | `Tidak bisa membandingkan TEXT dengan INT.` |
| SEM006 | Fungsi agregat di `WHERE`, `ON`, atau `GROUP BY` | `Agregat tidak boleh dipakai di WHERE.` |
| SEM007 | Kolom non-agregat di `SELECT`/`HAVING`/`ORDER BY` yang tidak ada di `GROUP BY` (`ONLY_FULL_GROUP_BY`) | `Kolom 'nama' harus ada di GROUP BY atau dibungkus agregat.` |
| SEM008 | `HAVING` tanpa `GROUP BY` | `HAVING membutuhkan GROUP BY.` |
| SEM009 | Fungsi tidak dikenal | `Fungsi 'median' tidak didukung.` |
| SEM010 | Argumen fungsi tidak valid (`SUM(*)`, `AVG(teks)`) | `AVG membutuhkan argumen numerik.` |
| SEM011 | `WHERE`/`HAVING`/`ON` bukan ekspresi boolean (MySQL mengizinkan `WHERE ipk`; kompiler menolak) | `Kondisi WHERE harus boolean.` |
| SEM012 | `*` atau `alias.*` digabung `GROUP BY`/agregat | `SELECT * tidak boleh dengan GROUP BY.` |
| SEM013 | `ON` bukan konjungsi equi-join antara sisi kiri dan kanan | `ON hanya mendukung a.x = b.y [AND ...].` |
| SEM014 | Bilangan bulat literal di `ORDER BY`/`GROUP BY` (di MySQL berarti nomor urut kolom) | `Nomor urut kolom tidak didukung; tulis nama kolom.` |
| SEM015 | Ekspresi boolean di daftar `SELECT` (`SELECT ipk > 3`) | `Ekspresi boolean di SELECT belum didukung.` |
| SEM016 | Kolom bertipe `UNSUPPORTED` direferensikan | `Kolom 'tgl_lahir' bertipe DATE belum didukung.` |

**Resolusi nama (mengikuti MySQL).**
- Kolom tanpa kualifikasi dicari di semua tabel pada FROM/JOIN; ditemukan di >1 tabel → SEM003.
- Jika tabel memiliki alias, nama asli tabel **tidak** boleh dipakai sebagai kualifikasi.
- **`ORDER BY`:** nama tanpa kualifikasi dicari **dulu di alias `SELECT`**, baru di kolom tabel.
- **`GROUP BY` dan `HAVING`:** dicari **dulu di kolom tabel**, baru di alias `SELECT`.
- Alias yang ditemukan di-*rewrite* menjadi ekspresi aslinya saat lowering.
- Query ber-`DISTINCT`: ekspresi `ORDER BY` harus ada di daftar `SELECT` (aturan MySQL 8, error 3065). Pelanggaran dilaporkan sebagai SEM002.

---

## 9. Format Error

Semua error memiliki `code`, `message`, `line`, `column`, dan dirender dengan penunjuk:

```
SEM002: Kolom 'ipkk' tidak ditemukan di 'mahasiswa'.
  1 | SELECT nama, ipkk FROM mahasiswa
                     ^^^^
```

Kode: `LEX001..`, `SYN001..` (memuat `expected`), `SEM001..SEM016`.

---

## 10. Semantik Runtime (mengikuti MySQL 8.4)

Bagian ini adalah tempat sebagian besar bug akan muncul. Setiap butir **wajib** punya minimal satu tes diferensial terhadap server MySQL.

1. **Logika tiga nilai (Kleene).** `NULL AND FALSE = FALSE`, `NULL OR TRUE = TRUE`, `NOT NULL = NULL`. Dtype `boolean` pandas menerapkan ini pada `& | ~`.
2. **Filter.** `WHERE`, `HAVING`, `ON` hanya meloloskan baris bernilai **TRUE** (`.fillna(False)`).
3. **NULL menular.** Aritmetika dan perbandingan dengan NULL menghasilkan NULL.
4. **Pembagian.**
   - `/` **tidak pernah** pembagian bulat. `INT / INT` menghasilkan DECIMAL dengan 4 desimal (`7/2 = 3.5000`, `1/3 = 0.3333`, `2/3 = 0.6667`): kompiler menghasilkan DOUBLE yang `.round(4)`. Jika salah satu operand DOUBLE, hasilnya DOUBLE tanpa pembulatan.
   - `DIV` = pembagian bulat dipotong ke arah nol (`-7 DIV 2 = -3`), hasil INT.
   - `%` = sisa bagi, tanda mengikuti yang dibagi (`-7 % 3 = -1`).
   - Pembagi nol pada `/`, `DIV`, `%` menghasilkan **NULL** pada `SELECT` (bukan error).
   - Catatan: pembulatan pandas adalah half-even pada representasi biner, MySQL half-up pada desimal. Dapat berbeda pada kasus *tie* eksak; tes menghindari kasus tersebut dan hal ini dicatat sebagai keterbatasan.
5. **Perbandingan teks tidak membedakan huruf besar/kecil.** Kolasi bawaan `utf8mb4_0900_ai_ci`: `'TI' = 'ti'` bernilai TRUE. Kompiler mengemulasikannya untuk **karakter ASCII** dengan `.str.lower()` pada: `= <> < <= > >=`, `BETWEEN`, `IN`, `LIKE`, `ORDER BY`, `MIN/MAX`, `GROUP BY`, `DISTINCT`, dan kunci `JOIN`. Spasi di akhir string bermakna (kolasi `0900` adalah `NO PAD`). Sensitivitas aksen (`é` = `e`) **tidak** diemulasikan; data tes hanya ASCII. Pada `GROUP BY`/`DISTINCT`, nilai wakil grup yang ditampilkan adalah kemunculan pertama (di MySQL tidak ditentukan; tes tidak memakai kolom dengan varian huruf).
6. **LIKE.** `%` = nol atau lebih karakter, `_` = tepat satu karakter, `\` = karakter escape bawaan (`\%`, `\_` berarti literal). Mengikuti butir 5 (tidak membedakan huruf). Operand NULL → NULL. Pola dikompilasi menjadi regex dengan semua karakter khusus di-*escape*: `(?is)\A...\Z`.
7. **IN.** Di-*lower* menjadi rantai `OR` dari `=`; `NOT IN` menjadi `NOT (...)`. Menjaga logika tiga nilai (`x NOT IN (1, NULL)` tidak pernah TRUE). Dilarang memakai `Series.isin` langsung.
8. **BETWEEN.** `x BETWEEN a AND b` ≡ `x >= a AND x <= b`; `NOT BETWEEN` ≡ `NOT (...)`.
9. **Agregat.** `COUNT(*)` menghitung semua baris; `COUNT(x)` hanya `x` bukan NULL. `SUM/AVG/MIN/MAX` mengabaikan NULL; jika tidak ada nilai non-NULL hasilnya NULL (bukan 0). `COUNT` pada himpunan kosong = 0. `SUM(INT)` tetap INT; `AVG(INT)` dibulatkan 4 desimal (butir 4); `SUM/AVG(DOUBLE)` tanpa pembulatan.
10. **Agregat tanpa GROUP BY** selalu menghasilkan **tepat satu baris**, termasuk saat input kosong.
11. **GROUP BY.** Semua NULL pada kunci membentuk **satu** grup (`dropna=False`). Urutan grup tanpa `ORDER BY` tidak ditentukan.
12. **JOIN.** Hanya equi-join. **Kunci NULL tidak pernah cocok**: pandas `merge` mencocokkan NaN dengan NaN, sehingga baris berkunci NULL wajib dibuang dari sisi yang dipasangkan sebelum `merge` (pada `LEFT JOIN`, baris kiri berkunci NULL tetap dipertahankan). `LEFT JOIN` mempertahankan seluruh baris kiri; kolom kanan yang tak cocok bernilai NULL. Kunci teks mengikuti butir 5.
13. **ORDER BY.** NULL dianggap lebih kecil dari nilai apa pun: `ASC` → NULL di awal, `DESC` → NULL di akhir. Pengurutan stabil. Multi-kunci = rangkaian `sort_values` stabil dari kunci terakhir ke pertama. Urutan antar baris yang kuncinya sama tidak ditentukan oleh MySQL; tes selalu menambahkan kunci pembeda (misal `nim`).
14. **DISTINCT.** Dua NULL dianggap sama; mengikuti butir 5; mempertahankan kemunculan pertama.
15. **LIMIT/OFFSET.** `LIMIT 0` → hasil kosong dengan kolom tetap ada. `OFFSET` melebihi jumlah baris → kosong.
16. **Urutan baris tanpa ORDER BY** tidak dijamin; tes membandingkan sebagai multiset.
17. **Nama kolom hasil.** Alias bila ada; kolom polos → nama kolom menurut skema; selain itu `SelectItem.text` (potongan sumber persis). Hasil boleh memiliki nama kolom ganda (misal `SELECT *` dari join); perbandingan dengan MySQL dilakukan **berdasarkan posisi kolom**, bukan nama.

---

## 11. Intermediate Representation (Logical Plan)

Pohon operator; dibangun dengan **urutan tetap**:

```
Scan → Join* → Filter(WHERE) → Aggregate → Filter(HAVING) → Sort → Project → Distinct → Limit
```

> `Sort` sebelum `Project` agar `ORDER BY` dapat merujuk kolom sumber dan alias (di-*rewrite*). `Distinct` setelah `Sort` aman karena penghapusan duplikat mempertahankan kemunculan pertama.

| Operator | Parameter |
|---|---|
| `Scan` | `table`, `alias` |
| `Join` | `kind` (INNER/LEFT), `left`, `right`, `keys: list[(kolom_kiri, kolom_kanan)]` |
| `Filter` | `child`, `predicate` |
| `Aggregate` | `child`, `group_keys`, `aggs: list[(nama_internal, fungsi, argumen)]`; nama internal `__agg0`, `__agg1`, … |
| `Sort` | `child`, `keys: list[(ekspresi, descending)]` |
| `Project` | `child`, `items: list[(ekspresi, nama_keluaran)]` |
| `Distinct` | `child` |
| `Limit` | `child`, `n`, `offset` |

**Optimasi.**
- **Wajib:** *constant folding* (`1 + 2` → `3`). Tidak boleh melipat ekspresi yang mengandung NULL atau pembagi nol secara salah (`NULL + 1` → `NULL`; `x / 0` → `NULL`). Aturan butir 4 (`/` INT/INT dibulatkan 4 desimal) berlaku juga saat dilipat.
- **Opsional (hanya bila semua tes hijau):** *predicate pushdown* untuk konjungsi `WHERE` yang hanya menyentuh satu tabel, hanya ke: kedua sisi `INNER JOIN`, atau sisi **kiri** `LEFT JOIN`. **Dilarang** ke sisi kanan `LEFT JOIN`.

Plan dapat dicetak sebagai pohon teks (`explain`).

---

## 12. Pemetaan Code Generator

| Operator IR | Kode pandas |
|---|---|
| `Scan(t, a)` | `a = tables["t"].add_prefix("a.")` (kolom internal `alias.kolom`, huruf kecil) |
| `Join(INNER)` | `kiri.dropna(subset=kunci_kiri).merge(kanan.dropna(subset=kunci_kanan), how="inner", left_on=[...], right_on=[...])` |
| `Join(LEFT)` | `kiri.merge(kanan.dropna(subset=kunci_kanan), how="left", left_on=[...], right_on=[...])` |
| `Filter(p)` | `df = df[(<mask>).fillna(False)]` |
| `Aggregate` | `df.assign(__one=1, __k0=<kunci teks>.str.lower(), ...)`; `groupby(["__k0", ...], dropna=False, sort=False).agg(...)`; nilai wakil kunci diambil dengan `"first"`; `reset_index(drop=True)`. Tanpa `GROUP BY`: bangun satu baris secara eksplisit |
| `Sort` | rangkaian `sort_values(by=[k], ascending=[...], na_position=..., kind="stable", key=...)` dari kunci terakhir ke pertama; `key=lambda s: s.str.lower()` untuk kolom teks |
| `Project` | `pd.DataFrame({...})` bila nama keluaran unik; `pd.concat([...], axis=1)` bila ada nama ganda |
| `Distinct` | tahan baris dengan `~kunci.duplicated()`, `kunci` = kolom keluaran dengan kolom teks di-`.str.lower()` |
| `Limit(n, offset)` | `out.iloc[offset:offset + n]` |

**Kunci teks.** Perbandingan `=`/`<>`/`IN` pada TEXT dikompilasi menjadi `kolom.str.lower() == "literal_huruf_kecil"` (literal dikecilkan saat kompilasi); antar-kolom: kedua sisi di-`.str.lower()`.

**Agregat → pandas:** `COUNT(*)` → `("__one", "count")`; `COUNT(x)` → `(x, "count")`; `SUM` → `lambda s: s.sum(min_count=1)`; `AVG` → `"mean"` (+ `.round(4)` bila argumen INT); `MIN`/`MAX` → `"min"`/`"max"` (untuk TEXT: ambil berdasarkan urutan huruf kecil).

---

## 13. Konvensi Kode Hasil

- Selalu satu fungsi: `def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:`, diawali `import pandas as pd`. Fungsi bantu kecil (misal pembagian bulat) dihasilkan di atas `run` **hanya bila dipakai**, sehingga kode tetap mandiri.
- Tidak ada efek samping, tidak ada I/O, tidak mengubah `tables`.
- Setiap blok diberi komentar `# SQL: <potongan SQL asal>`.
- Kembalian selalu `.reset_index(drop=True)`.
- Deterministik: sumber yang sama menghasilkan teks yang identik (untuk *golden test*).
- Runtime mengeksekusi kode dengan namespace terbatas (hanya `pd`, builtins dibatasi). Ini **bukan** sandbox keamanan yang kuat; wajib dicatat di README.

---

## 14. Contoh (kontrak perilaku)

Dataset acuan: `mahasiswa`, `mata_kuliah`, `nilai` seperti §7, mengandung NULL di `ipk`, `jurusan`, dan `skor`, mahasiswa tanpa baris nilai, serta kolom `kota` yang sengaja memuat varian huruf (`'Jakarta'`, `'jakarta'`) khusus untuk menguji butir 5. Kode di bawah ilustrasi gaya dan semantik; berkas *golden* final ditetapkan saat langkah code generator selesai.

### Contoh 1: filter, urut, batas
```sql
SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC LIMIT 5
```
```python
import pandas as pd

def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: WHERE ipk > 3.5
    df = df[(df["mahasiswa.ipk"] > 3.5).fillna(False)]
    # SQL: ORDER BY ipk DESC  (NULL di akhir untuk DESC)
    df = df.sort_values(by=["mahasiswa.ipk"], ascending=[False], na_position="last", kind="stable")
    # SQL: SELECT nama, ipk
    out = pd.DataFrame({"nama": df["mahasiswa.nama"], "ipk": df["mahasiswa.ipk"]})
    # SQL: LIMIT 5
    out = out.iloc[0:5]
    return out.reset_index(drop=True)
```

### Contoh 2: DISTINCT, ORDER BY teks, NULL
```sql
SELECT DISTINCT jurusan FROM mahasiswa ORDER BY jurusan
```
```python
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: ORDER BY jurusan  (ASC: NULL di awal; teks tidak membedakan huruf)
    df = df.sort_values(by=["mahasiswa.jurusan"], ascending=[True], na_position="first",
                        kind="stable", key=lambda s: s.str.lower())
    out = pd.DataFrame({"jurusan": df["mahasiswa.jurusan"]})
    # SQL: DISTINCT  (NULL dianggap sama; teks tidak membedakan huruf)
    out = out[~out["jurusan"].str.lower().duplicated()]
```
Perilaku wajib: NULL pada `jurusan` muncul **sekali** dan **paling awal**.

### Contoh 3: JOIN, GROUP BY, HAVING, agregat, alias
```sql
SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata
FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim
GROUP BY m.jurusan
HAVING COUNT(*) > 2
ORDER BY rata DESC
```
```python
import pandas as pd

def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    m = tables["mahasiswa"].add_prefix("m.")
    n = tables["nilai"].add_prefix("n.")
    # SQL: INNER JOIN nilai n ON m.nim = n.nim  (kunci NULL tidak pernah cocok)
    df = m.dropna(subset=["m.nim"]).merge(
        n.dropna(subset=["n.nim"]), how="inner", left_on=["m.nim"], right_on=["n.nim"]
    )
    # SQL: GROUP BY m.jurusan  (kunci teks tidak membedakan huruf)
    df = df.assign(__one=1, __k0=df["m.jurusan"].str.lower())
    df = df.groupby(["__k0"], dropna=False, sort=False).agg(
        **{"m.jurusan": ("m.jurusan", "first")},
        __agg0=("__one", "count"),     # COUNT(*)
        __agg1=("n.skor", "mean"),     # AVG(n.skor), skor bertipe DOUBLE: tanpa pembulatan
    ).reset_index(drop=True)
    # SQL: HAVING COUNT(*) > 2
    df = df[(df["__agg0"] > 2).fillna(False)]
    # SQL: ORDER BY rata DESC  (alias rata -> AVG(n.skor))
    df = df.sort_values(by=["__agg1"], ascending=[False], na_position="last", kind="stable")
    # SQL: SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata
    out = pd.DataFrame({"jurusan": df["m.jurusan"], "jumlah": df["__agg0"], "rata": df["__agg1"]})
    return out.reset_index(drop=True)
```

### Contoh 4: LIKE, IN, BETWEEN, IS NOT NULL
```sql
SELECT nim, nama FROM mahasiswa
WHERE nama LIKE 'A%' AND jurusan IN ('TI', 'SI')
  AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL
```
```python
    mask = (
        df["mahasiswa.nama"].str.match(r"(?is)\AA.*\Z")
        & ((df["mahasiswa.jurusan"].str.lower() == "ti") | (df["mahasiswa.jurusan"].str.lower() == "si"))
        & ((df["mahasiswa.ipk"] >= 3.0) & (df["mahasiswa.ipk"] <= 4.0))
        & df["mahasiswa.ipk"].notna()
    )
    df = df[mask.fillna(False)]
```

### Contoh 5: LEFT JOIN untuk data tanpa pasangan
```sql
SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim WHERE n.nim IS NULL
```
```python
    df = m.merge(n.dropna(subset=["n.nim"]), how="left", left_on=["m.nim"], right_on=["n.nim"])
    df = df[df["n.nim"].isna()]
    out = pd.DataFrame({"nama": df["m.nama"]})
```

### Contoh 6: pembagian khas MySQL (kontrak nilai)
```sql
SELECT sks, sks / 3 AS bagi, sks DIV 3 AS bulat, sks % 3 AS sisa FROM mata_kuliah ORDER BY sks
```
Hasil wajib untuk `sks` = 2, 3, 4:

| sks | bagi | bulat | sisa |
|---|---|---|---|
| 2 | 0.6667 | 0 | 2 |
| 3 | 1.0000 | 1 | 0 |
| 4 | 1.3333 | 1 | 1 |

Dan `SELECT sks / 0 FROM mata_kuliah` menghasilkan NULL untuk semua baris (tanpa error).

---

## 15. Antarmuka

**CLI** (`typer` + `rich`):
- `sqlc compile "<SQL>" --schema schema.json` → cetak kode Python hasil.
- `sqlc compile "<SQL>" --dsn mysql://user:pass@host:3306/db` → skema diambil dari server.
- `sqlc run "<SQL>" --dsn ...` atau `--data examples/` → kompilasi, jalankan, tampilkan tabel hasil.
- `sqlc explain "<SQL>"` → token, AST, plan, kode.
- `sqlc schema --dsn ... --out schema.json` → ekspor skema hasil introspeksi.
- Opsi `--file` membaca SQL dari berkas. Exit code: 0 sukses, 1 error kompilasi, 2 error runtime.

**UI** (`streamlit`): sesuai diagram §1. Editor query MySQL; panel utama menampilkan **kode Python** dengan tombol salin/unduh `.py`; tab Token | AST | Semantik | Plan; tab verifikasi menjalankan kode pada data dan menampilkan hasil Python berdampingan dengan hasil MySQL beserta penanda cocok/tidak cocok. UI hanya memanggil `compile_sql`.

---

## 16. Strategi Pengujian

| Lapisan | Berkas | Minimal |
|---|---|---|
| Lexer | `tests/test_lexer.py` | 20 kasus (backtick, kutip ganda, komentar `-- ` vs `--x`, `#`, escape, 5 error) |
| Parser | `tests/test_parser.py` | 45 kasus (presedensi, semua klausa, `LIMIT a, b`, 10 error dengan posisi tepat) |
| Semantik | `tests/test_semantic.py` | 30 kasus (SEM001–SEM016 masing-masing ≥1, termasuk aturan resolusi alias §8) |
| IR | `tests/test_ir.py` | urutan operator, constant folding, (pushdown bila dikerjakan) |
| Codegen | `tests/test_codegen.py` | 12 *golden file* di `tests/golden/` |
| **Diferensial (MySQL)** | `tests/test_differential.py` | **≥ 45 query**, setiap butir §10 tercakup |

**Lingkungan oracle.** Server MySQL 8.4 lewat Docker (`docker-compose.yml`: image `mysql:8.4`, `--character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci`). `examples/mysql_init.sql` membuat tabel dan data. Driver: `PyMySQL`. Koneksi diatur lewat variabel lingkungan `SQLC_MYSQL_DSN`. Tes diberi penanda `@pytest.mark.mysql`; sebelum tes berjalan, harness memverifikasi versi server dimulai dengan `8.4` dan `sql_mode` sesuai §2, bila tidak, gagal (bukan di-*skip*).

**Tes diferensial** memuat data yang sama ke MySQL dan ke DataFrame, menjalankan query pada keduanya, lalu membandingkan: jumlah kolom sama, kolom dibandingkan **berdasarkan posisi**, baris sebagai multiset (kecuali ada `ORDER BY`, lalu urutan dibandingkan; query ber-`ORDER BY` wajib punya kunci pembeda), `Decimal` dari MySQL dikonversi ke `float`, float dengan toleransi `1e-9`, NULL sama dengan NULL. Jika hasil berbeda, **perbaiki kompiler, bukan tesnya**.

**Aturan dataset.** Varian huruf besar/kecil (`'Jakarta'`/`'jakarta'`) hanya pada kolom `kota` dan hanya dipakai di `WHERE`/`IN`/`LIKE`/`JOIN`, tidak pernah di `GROUP BY`/`DISTINCT`/pembeda urutan.

Tambahan: `ruff` bersih, `mypy --strict` bersih, cakupan kode ≥ 85%.

---

## 17. Struktur Proyek

```
sqlcompiler/
├── pyproject.toml
├── docker-compose.yml        # MySQL 8.4 untuk oracle
├── README.md
├── SPEC.md
├── src/sqlcompiler/
│   ├── errors.py             # LexError, ParseError, SemanticError, kode error
│   ├── lexer.py
│   ├── ast_nodes.py
│   ├── parser.py
│   ├── semantic.py           # Schema, DataType, analisis
│   ├── schema_loader.py      # JSON + introspeksi information_schema MySQL
│   ├── data_loader.py        # CSV / MySQL → dict[str, DataFrame] bertipe nullable
│   ├── ir.py                 # Logical plan, lowering, optimasi
│   ├── codegen.py
│   ├── runtime.py            # compile_sql, run_compiled
│   ├── cli.py
│   └── app_streamlit.py
├── examples/                 # schema.json, mysql_init.sql, *.csv, queries.sql
├── tests/
│   └── golden/
└── docs/LAPORAN_TEKNIS.md
```

Dependensi: `pandas`, `pymysql`, `typer`, `rich`, `streamlit`. Dev: `pytest`, `pytest-cov`, `ruff`, `mypy`.

---

## 18. Urutan Pengerjaan

0. Kerangka proyek + dokumen ini + `docker-compose.yml` dan data uji → 1. Lexer → 2. AST + parser inti → 3. Parser lanjutan → 4. Semantik + `schema_loader` → 5. IR → 6. Codegen → 7. Runtime + `data_loader` + tes diferensial MySQL → 8. CLI → 9. UI → 10. Audit → 11. Laporan teknis.

Aturan: **tidak lanjut ke langkah berikutnya selama `pytest` langkah sekarang belum hijau.**

---

## 19. Kriteria Selesai

Proyek dinyatakan selesai (dan barulah Level 3 boleh disentuh) bila:
1. Seluruh grammar Level 1–2 terimplementasi; tes lexer/parser/semantik/IR/codegen hijau.
2. Tes diferensial ≥ 45 query **berjalan terhadap server MySQL 8.4 asli** (tidak di-*skip*) dan hijau, mencakup seluruh butir §10 (terutama pembagian, kolasi, NULL, urutan NULL, kunci JOIN NULL, agregat himpunan kosong).
3. `ruff` dan `mypy --strict` bersih; cakupan ≥ 85%.
4. CLI dan UI berjalan dari repositori bersih mengikuti README, termasuk `docker compose up` untuk oracle.
5. README memuat keterbatasan yang diketahui (§20 dan catatan sandbox §13).
6. Pemilik proyek dapat menjelaskan setiap fase dan setiap keputusan semantik di §10 tanpa melihat kode.

---

## 20. Keterbatasan yang Diakui

- Hanya satu pernyataan `SELECT`; tidak ada transaksi, indeks, atau optimasi biaya. Seluruh data dimuat ke memori.
- Acuan perilaku adalah MySQL 8.4 dengan `sql_mode` bawaan. MariaDB dan MySQL versi lain dapat berbeda (kolasi bawaan, `ONLY_FULL_GROUP_BY`, aturan DECIMAL).
- Emulasi kolasi `utf8mb4_0900_ai_ci` hanya untuk ASCII; sensitivitas aksen dan aturan Unicode tidak diemulasikan.
- Kolom `DECIMAL` dipetakan ke DOUBLE (presisi hilang); tipe tanggal/waktu belum didukung.
- Pembulatan 4 desimal memakai half-even biner, MySQL memakai half-up desimal.
- Kompiler lebih ketat daripada MySQL pada konversi tipe implisit, `WHERE` non-boolean, `ON` non-equi, dan fungsi ketergantungan fungsional pada `GROUP BY`.
- Nama tabel diperlakukan tanpa membedakan huruf besar/kecil (di MySQL Linux bersifat sensitif).
- Eksekusi kode hasil tidak aman terhadap masukan yang sengaja berbahaya.
