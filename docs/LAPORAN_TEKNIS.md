# LAPORAN TEKNIS

## Perancangan dan Implementasi Kompiler Query SQL ke Bahasa Pemrograman Python
### Dialek MySQL 8.4 → pandas

| | |
|---|---|
| Dokumen | `docs/LAPORAN_TEKNIS.md` |
| Spesifikasi | `SPEC.md` (satu-satunya sumber kebenaran) |
| Tanggal penyusunan | 4 Oktober 2026 |
| Lingkungan acuan | Python 3.12, pandas 3.0, MySQL 8.4.11 (Docker, port 3307) |

Seluruh angka dalam dokumen ini diambil dari keluaran perintah yang dijalankan
pada saat penyusunan (bagian 8). Seluruh klaim perilaku mengacu pada kode yang
ada di repositori ini; bagian 10 mencantumkan bagian yang akurasinya masih perlu
ditinjau manusia.

---

## 1. Latar Belakang dan Tujuan

### 1.1 Latar belakang

Relasi kebenaran hasil query pada sistem yang berbeda sering tidak diuji
secara sungguhan. Query yang tampak sederhana dapat menghasilkan hasil berbeda
antara basis data dan proses analitik ketika menyangkut perilaku halus: kolasi
teks, pembulatan pembagian, penanganan `NULL`, dan urutan tanpa `ORDER BY`.
Pendekatan yang lazim — membandingkan hasil secara manual atau menyalin keluaran
sebagai "hasil yang diharapkan" — tidak memberikan kebenaran, hanya kesan
kebenaran.

Dokumen ini merancang dan mengimplementasikan kompiler yang menerjemahkan
pernyataan `SELECT` MySQL 8.4 menjadi kode Python berbasis pandas, di mana
**MySQL 8.4 yang sungguhan adalah satu-satunya oracle**. Kebenaran bukan klaim,
melainkan hasil pengukuran berulang terhadap server yang berjalan.

### 1.2 Tujuan

1. Menerjemahkan satu pernyataan `SELECT` (subset Level 1–2 `SPEC.md` §2) menjadi
   kode Python berbasis pandas yang, ketika dijalankan pada data yang sama,
   menghasilkan hasil **identik** dengan MySQL 8.4.
2. Menjalankan pipeline kompilasi dalam fase yang eksplisit dan terpisah
   (lexer, parser, semantik, IR, code generator) dengan kontrak data yang jelas.
3. Memverifikasi kebenaran kompiler secara **diferensial** terhadap server MySQL
   8.4 sungguhan, bukan terhadap keluaran yang ditulis tangan.
4. Menyediakan dua antarmuka — CLI `sqlc` dan UI Streamlit — yang keduanya hanya
   memanggil fasad kompilator yang sama.

### 1.3 Prinsip yang memandu seluruh implementasi

Prinsip diambil apa adanya dari `SPEC.md` §1 dan Aturan Kerja Global
`PROMPT.md`:

- **Kebenaran di atas fitur.** Subset kecil yang benar lebih bernilai daripada
  subset besar yang kadang salah.
- **MySQL 8.4 adalah oracle.** Perilaku yang ambigu mengikuti MySQL 8.4.
- **Boleh lebih ketat, dilarang menyimpang.** Kompiler boleh *menolak* query
  yang valid di MySQL, dengan error yang jelas. Kompiler tidak boleh menerima
  query lalu menghasilkan hasil berbeda.
- **Fase kompilasi eksplisit.** Modul terpisah dengan kontrak data jelas.
- **Lexer dan parser ditulis manual** (regex + recursive descent); tanpa
  Lark/PLY/ANTLR/`sqlparse`/`sqlglot`.

---

## 2. Arsitektur dan Diagram Alir Fase

### 2.1 Susunan modul

```
src/sqlcompiler/
├── errors.py          LexError, ParseError, SemanticError, render_error()
├── lexer.py           tokenisasi manual (regex + state machine ringan)
├── ast_nodes.py       seluruh node AST (dataclass frozen)
├── parser.py          recursive descent, pretty_print()
├── schema_loader.py   skema JSON + introspeksi information_schema
├── semantic.py        Schema, DataType, AnalysisResult (SEM001–SEM016)
├── ir.py              Logical Plan, lower(), fold_constant(), format_plan()
├── codegen.py         generate_code() → str
├── data_loader.py     CSV / MySQL → dict[str, DataFrame] bertipe nullable
├── runtime.py         compile_sql(), run_compiled()  ← fasad
├── verify.py          fetch(), compare_query(), compare_frames()
├── cli.py             perintah sqlc
└── app_streamlit.py   antarmuka Streamlit
```

### 2.2 Diagram alir pipeline kompilasi

```mermaid
flowchart TD
    SRC["Teks query MySQL<br/>(str)"] --> LEX

    LEX["<b>Lexer</b><br/>lexer.py — regex manual"] --> TOK["list[Token]<br/>line, column, offset, lexeme"]
    LEX -. "LexError" .-> ERR

    TOK --> PAR["<b>Parser</b><br/>parser.py — recursive descent"]
    PAR --> AST["SelectStatement (AST)<br/>ast_nodes.py"]
    PAR -. "ParseError (SYN001..)" .-> ERR

    AST --> SEM["<b>Analisis Semantik</b><br/>semantic.py"]
    SCHEMA[("Schema<br/>JSON / introspeksi MySQL")] --> SEM
    SEM --> ANA["AnalysisResult<br/>types, column_refs, alias_refs"]
    SEM -. "SemanticError (SEM001..016)" .-> ERR

    ANA --> LOW["<b>IR Lowering</b><br/>ir.py: lower()"]
    LOW --> FOLD["<b>Optimizer</b><br/>ir.py: fold_constant()"]
    FOLD --> PLAN["Logical Plan (pohon operator)"]
    PLAN --> GEN["<b>Code Generator</b><br/>codegen.py: generate_code()"]

    GEN --> OUT["<b>OUTPUT UTAMA</b><br/>str — def run(tables) -> DataFrame"]

    OUT --> RUN["<b>Runtime</b><br/>runtime.py: run_compiled()"]
    DATA[("dict[str, DataFrame]<br/>CSV / MySQL")] --> RUN
    RUN --> DF["pandas.DataFrame"]
    DF --> VER["<b>Verifikasi</b><br/>verify.py: compare_query()<br/>vs hasil MySQL 8.4"]

    ERR["<b>Perender galat</b><br/>errors.py: render_error()<br/>baris + kolom + penunjuk ^^^^"]

    classDef out fill:#d9f2d9,stroke:#2d7a2d,stroke-width:2px
    classDef err fill:#f8d7da,stroke:#a12a32
    class OUT,DF out
    class ERR err
```

### 2.3 Kontrak data antar-fase

| Fase | Masukan | Keluaran | Modul |
|---|---|---|---|
| Lexer | `str` | `tuple[Token, ...]` | `lexer.py` |
| Parser | `tuple[Token, ...]` + sumber | `SelectStatement \| None` | `parser.py` |
| Semantik | AST + `Schema` | `AnalysisResult` (AST diotomatisasi tipe) | `semantic.py` |
| IR lowering | `AnalysisResult` | `Plan` | `ir.py` |
| Optimasi | `Plan` | `Plan` | `ir.py` |
| Code generator | `Plan` + `AnalysisResult` | `str` | `codegen.py` |
| Runtime | `str` + `dict[str, DataFrame]` | `pd.DataFrame` | `runtime.py` |

Fasad tunggalnya adalah:

```python
compile_sql(sql: str, schema: Schema) -> CompileResult
# CompileResult: tokens, ast, plan, code, errors, analysis
```

CLI (`cli.py`) dan UI (`app_streamlit.py`) **hanya** memanggil fasad ini beserta
loader skema/data; tidak ada logika kompilasi yang diduplikasi di lapisan
antarmuka (`SPEC.md` §15).

---

## 3. Grammar EBNF Final

Grammar berikut adalah grammar yang diimplementasikan oleh `parser.py`
(recursive descent, satu fungsi `_parse_*` per nonterminal). Notasi: `[ ]`
opsional, `{ }` pengulangan nol atau lebih, `|` alternatif, huruf kapital = token,
`ident` = `IDENT | QIDENT`.

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
               (* "LIMIT a, b"   = lewati a, ambil b
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

func_call    = IDENT "(" ( "*" | expr ) ")" ;      (* "*" hanya sah untuk COUNT *)
column_ref   = [ ident "." ] ident ;
```

**Spesifikasi leksikal** (`SPEC.md` §4, diimplementasikan `lexer.py`):

| Kelas | Pola | Catatan |
|---|---|---|
| KEYWORD | `SELECT DISTINCT FROM WHERE GROUP BY HAVING ORDER LIMIT OFFSET ASC DESC AS JOIN INNER LEFT OUTER ON AND OR NOT LIKE IN BETWEEN IS NULL DIV` | case-insensitive; hanya dapat dipakai sebagai identifier bila diberi backtick |
| IDENT | `[A-Za-z_][A-Za-z0-9_$]*` | dinormalisasi ke huruf kecil |
| QIDENT | `` `...` `` | backtick ganda ``` `` ``` di dalam nama |
| INTEGER | `[0-9]+` | tanpa tanda; tanda ditangani sebagai operator unary |
| FLOAT | `[0-9]+\.[0-9]+` | tanpa eksponen |
| STRING | `'...'` atau `"..."` | kutip ganda = **string** di MySQL; kutip kembar digandakan = satu kutip |
| OPERATOR | `= <> != < > <= >= + - * / %` | `!=` dinormalisasi menjadi `<>` |
| PUNCT | `( ) , . ;` | |
| EOF | | token terakhir |

Komentar yang diabaikan: `-- ` (dua minus **diikuti spasi/tab/akhir baris**;
`--x` bukan komentar — diverifikasi terhadap MySQL), `# ...` sampai akhir baris,
dan `/* ... */`.

**Presedensi** (tinggi → rendah): `( )`/literal/kolom/fungsi → unary `+ -` →
`* / DIV %` → `+ -` → perbandingan/`LIKE`/`IN`/`BETWEEN`/`IS NULL` → `NOT` →
`AND` → `OR`. Perbandingan tidak berantai: `a < b < c` adalah error sintaks.

---

## 4. Rancangan Tiap Fase

### 4.1 Lexer (`lexer.py`, 429 baris)

Pendekatan: **regex terpadu satu kali** untuk kelas-kelas yang tidak ambigu
(pita pola dengan *longest match*, lalu *tag* hasilnya), dilanjuti state machine
ringan untuk token yang butuh *scan* berkelanjutan: string, identifier berkutip
backtick, komentar blok, dan escape.

Keputusan rancangan:

- Setiap `Token` membawa `line`, `column` (basis 1), dan `offset` (basis 0).
  `offset` dipakai untuk mengambil potongan teks sumber apa adanya, sehingga
  `SelectItem.text` (nama kolom hasil) dan penunjuk galat selalu persis dengan
  yang diketik pengguna.
- Keyword dikenali setelah normalisasi huruf, sehingga `SeLeCt` diterima seperti
  `SELECT`. Identifier dinormalisasi ke huruf kecil karena kompiler memperlakukan
  nama tanpa membedakan huruf besar/kecil.
- `\%` dan `\_` dipertahankan sebagai dua karakter (tidak didekode) mengikuti
  `SPEC.md` §10.6, sedangkan `SPEC.md` §4 hanya mendaftar enam escape lain.
  Escape lain tetap `LexError`. Keputusan ini diverifikasi langsung terhadap
  oracle: `LENGTH('a\%b') = 4` dan `'a\%b' = 'a%b'` bernilai 0.
- `LexError` dibangkitkan untuk: karakter tidak dikenal, string/backtick/komentar
  blok tidak tertutup, dan escape tidak dikenal.

### 4.2 Parser (`parser.py`, 707 baris)

Recursive descent murni pada kelas `_Parser`, satu metode per nonterminal
(`_parse_select`, `_parse_join`, `_parse_predicate_suffix`, `_parse_additive`,
…). Faktanya 64 metode, tanpa tabel konflik LALR.

- **Presedensi** diekspresikan sebagai rantai fungsi (or → and → not → predicate
  → additive → term → unary → primary), sehingga tabel presedensi tidak perlu
  dibentuk dari tabel konflik.
- **`_reject_unsupported_start()`** menangani prinsip "lebih ketat, dilarang
  menyimpang": token pembuka di luar scope (`WITH`, `INSERT`, `UPDATE`, `DELETE`,
  `CREATE`, `(` sebagai query bersarang, dst.) langsung menghasilkan `SYN005`
  dengan pesan yang menjelaskan apa yang didukung.
- **`_unsupported()`** melaporkan konstruksi yang dikenali tetapi di luar scope
  (`RIGHT JOIN`, `CROSS JOIN`, comma join, subquery, window function,
  `GROUP BY 1`, `SELECT` tanpa `FROM`) sebagai `SYN005`, bukan sebagai galat
  sintaks generik — pengguna mendapat pesan yang dapat ditindaklanjuti.
- `LIMIT a, b` dinormalisasi saat parsing menjadi `offset=a, limit=b`, sehingga
  seluruh tahap berikutnya hanya mengenal satu bentuk.
- Parser menyediakan `pretty_print(ast)` yang menghasilkan pohon berindentasi;
  dipakai bersama oleh perintah `explain` dan tab AST pada UI.
- Galat memuat `expected` pada `SYN001`, sehingga pesannya seperti
  `Diharapkan FROM, ditemukan 'akhir berkas'.`

### 4.3 Analisis Semantik (`semantic.py`, 1043 baris)

Fase terbesar karena menggabungkan resolusi nama, sistem tipe, dan seluruh
aturan MySQL yang dikompilasi menjadi pemeriksaan.

- **Mengumpulkan semua error**, tidak berhenti di yang pertama
  (`SPEC.md` §8). Hasilnya berupa `tuple[CompileError, ...]`.
- `AnalysisResult` memuat `types: dict[Expr, DataType]`,
  `column_refs: dict[Column, str]` (nama internal setelah kualifikasi),
  `alias_refs`, `errors`, `warnings`, dan `ok`. Field ini dipakai kembali oleh
  tab Semantik pada UI, sehingga UI tidak perlu menjalankan analyzer kedua.
- **Resolusi nama** mengikuti MySQL: kolom tanpa kualifikasi dicari di seluruh
  tabel pada `FROM`/`JOIN`, ditemukan di lebih dari satu → `SEM003`. Bila tabel
  memakai alias, nama asli tabel tidak boleh dipakai sebagai kualifikasi.
- **Urutan pencarian alias berbeda per klausa** — ini perilaku MySQL:
  `ORDER BY` mencari **dulu di alias `SELECT`** lalu di kolom tabel; `GROUP BY`
  dan `HAVING` mencari **dulu di kolom tabel** lalu di alias `SELECT`.
- `Schema` dinormalisasi huruf kecil pada `get()`, sehingga nama tabel dari CSV
  (nama berkas) dan dari MySQL (dari `information_schema`) selalu cocok di
  sistem berkas yang sensitif huruf.
- Sumber skema: (a) berkas `schema.json`, (b) introspeksi
  `information_schema.COLUMNS` lewat DSN. Keduanya diuji menghasilkan `Schema`
  yang setara pada `tests/test_semantic.py`.

### 4.4 Intermediate Representation (`ir.py`, 646 baris)

`lower(analysis) -> Plan` membangun pohon operator dengan **urutan tetap**:

```
Scan → Join* → Filter(WHERE) → Aggregate → Filter(HAVING) → Sort → Project → Distinct → Limit
```

`Sort` ditempatkan **sebelum** `Project` agar `ORDER BY` masih dapat merujuk
kolom sumber dan alias (alias di-*rewrite* menjadi ekspresi aslinya).
`Distinct` ditempatkan setelah `Sort`; penghapusan duplikat mempertahankan
kemunculan pertama sehingga urutannya tidak terganggu.

**Optimasi yang diimplementasikan: `fold_constant`.** Pelipatan konstanta
dilakukan pada IR dengan aturan yang hati-hati terhadap semantik MySQL:

- `1 + 2` → `3`
- ekspresi yang mengandung `NULL` tidak boleh dilipat menjadi salah
  (`NULL + 1` → `NULL`, `x / 0` → `NULL`)
- aturan pembagian `INT/INT` (dibulatkan 4 desimal) berlaku juga saat pelipatan

**Optimasi yang tidak diimplementasikan:** *predicate pushdown* di luar
`SPEC.md` §11 ditandai opsional dan "hanya bila semua tes hijau". Setelah audit
Langkah 10, pushdown memang **belum ada** dalam kode (`ir.py` tidak memuat
simbol pushdown apa pun). Laporan ini tidak mengklaim fitur tersebut.

Pohon plan dapat dicetak sebagai teks (`format_plan`), dipakai oleh perintah
`explain` dan tab Plan pada UI.

### 4.5 Code Generator (`codegen.py`, 1266 baris)

`generate_code(plan, analysis) -> str`. Hasilnya berupa satu fungsi mandiri:

```python
import pandas as pd

def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: <potongan SQL asal>
    ...
    return out.reset_index(drop=True)
```

Keputusan rancangan:

- **Fungsi bantu hanya muncul bila dipakai** (`_HELPER_ORDER` =
  `_idiv`, `_imod`, `_rdec`, `_tmin`, `_tmax`). Kode tetap mandiri dan dapat
  disalin ke berkas `.py` tanpa dependensi lain.
- **Deterministik.** Sumber yang sama selalu menghasilkan teks yang identik —
  prasyarat untuk *golden test*.
- **Komentar `# SQL: ...`** ditempelkan pada setiap blok sehingga kode hasil
  dapat ditelusuri kembali ke query asal.
- **PEP 8** dipatuhi: dua baris kosong sebelum `def` tingkat atas dan garis
  ≤ 100 kolom (kode yang panjang dipecah per argumen). Konsekuensinya beberapa
  contoh pada `SPEC.md` §14 berbeda *gaya* dari berkas golden; perbedaan itu
  didokumentasikan sebagai deviasi gaya di `PROGRESS.md`, bukan perbedaan
  semantik.
- Evaluasi ekspresi statis (`_static_operator`, `_static_value`, …) memakai
  tabel Kleene yang sama dengan pandas, sehingga pelipatan konstanta dan
  eksekusi dinamis tidak pernah berbeda hasil.

### 4.6 Runtime (`runtime.py`, 165 baris)

```python
compile_sql(sql, schema) -> CompileResult
run_compiled(code, tables) -> pd.DataFrame
```

`run_compiled` mengeksekusi kode hasil kompilasi dengan **namespace terbatas**:

- `__builtins__` adalah daftar putih kecil (tanpa `open`, `eval`, `exec`,
  `compile`, `input`, `breakpoint`), dan **disalin pada setiap panggilan**;
- `__import__` diganti `_safe_import` sehingga **hanya `pandas`** yang boleh
  diimpor dan impor relatif selalu ditolak;
- hanya `pd` yang tersedia sebagai modul.

Keputusan ini lahir dari temuan audit Langkah 10: semula `__builtins__`
menunjuk ke tabel bersama, sehingga satu eksekusi dapat merusak seluruh
eksekusi berikutnya (`__builtins__['len'] = None` membuat `len` hilang untuk
semua pemanggil sesudahnya). Perbaikannya diregresikan oleh tes
`test_mutasi_builtins_tidak_bocor_ke_eksekusi_berikutnya`.

Keluaran `run` diverifikasi bertipe `pandas.DataFrame`, dan ketiadaan fungsi
`run` memberi `RuntimeError` yang jelas. **Ini bukan sandbox keamanan yang
kuat** — lihat bagian 9.

---

## 5. Desain AST dan IR

### 5.1 AST (`ast_nodes.py`, 225 baris)

Seluruh node adalah `@dataclass(frozen=True)` dan membawa `line` serta `column`.
Keuntungannya: AST dapat dijadikan kunci `dict` (dipakai `AnalysisResult.types`)
tanpa masalah *hash*, tidak dapat diubah diam-diam oleh fase berikutnya, dan
kesalahan referensi memunculkan galat lebih awal.

| Node | Field utama |
|---|---|
| `SelectStatement` | `distinct`, `items`, `from_`, `joins`, `where`, `group_by`, `having`, `order_by`, `limit`, `offset` |
| `SelectItem` | `expr`, `alias`, `text` (potongan sumber persis → nama kolom hasil) |
| `Star` | `table \| None` (`None` = `*` murni) |
| `TableRef` | `name`, `alias` |
| `Join` | `kind` (`"INNER" \| "LEFT"`), `table`, `on` |
| `OrderItem` | `expr`, `descending` |
| `Column` | `table`, `name` |
| `Literal` | `value`, `kind` (`"INT" \| "DOUBLE" \| "TEXT" \| "NULL"`) |
| `UnaryOp` | `op`, `operand` |
| `BinaryOp` | `op`, `left`, `right` |
| `Like` / `InList` / `Between` / `IsNull` | `operand` + `negated` |
| `FunctionCall` | `name`, `arg` (`Expr \| Star`) |

Desain penting: `Star` adalah subclass dari `SelectItem` sehingga
`items: list[SelectItem | Star]` tetap dapat diperlakukan seragam oleh konsumen.

### 5.2 IR (`ir.py`)

IR adalah pohon operator yang dibangun dengan urutan tetap (bagian 4.4). Seluruh
operator turunan dari `PlanNode`:

| Operator | Parameter |
|---|---|
| `Scan` | `table`, `alias` |
| `Join` | `kind`, `left`, `right`, `keys: list[(kolom_kiri, kolom_kanan)]` |
| `Filter` | `child`, `predicate` |
| `Aggregate` | `child`, `group_keys`, `aggs: list[(nama_internal, fungsi, argumen)]` |
| `Sort` | `child`, `keys: list[(ekspresi, descending)]` |
| `Project` | `child`, `items: list[(ekspresi, nama_keluaran)]` |
| `Distinct` | `child` |
| `Limit` | `child`, `n`, `offset` |

Nama internal agregat menggunakan pola `__agg0`, `__agg1`, … dan kunci grup
`__k0`, `__k1`, … — ruang nama yang mustahil dihasilkan parser, sehingga tidak
pernah bertabrakan dengan nama kolom pengguna.

### 5.3 Pemisahan tanggung jawab AST vs IR vs kode

- **AST** mewakili *apa yang ditulis pengguna* — posisi, teks sumber, alias,
  struktur klausa. Karena itu `SelectItem.text` dipertahankan apa adanya.
- **IR** mewakili *urutan eksekusi* — bergabung, menyaring, mengelompokkan,
  mengurutkan, memproyeksikan. Di tahap ini alias sudah di-*rewrite* dan nama
  internal sudah dipasang.
- **Kode hasil** mewakili *bagaimana pandas melakukannya* — satu-satunya tahap
  yang mengetahui API pandas.

Pemisahan ini membuat setiap tahap dapat diuji secara terpisah (100 tes parser,
63 tes IR, 98 tes codegen) dan membuat galat terisolasi pada tahap yang benar.

---

## 6. Strategi Pemetaan MySQL ke pandas

### 6.1 Pemetaan tipe

| Tipe MySQL | Tipe kompiler | dtype pandas |
|---|---|---|
| `TINYINT SMALLINT MEDIUMINT INT BIGINT` (termasuk `UNSIGNED`) | `INT` | `"Int64"` |
| `FLOAT DOUBLE` | `DOUBLE` | `"Float64"` |
| `DECIMAL(p,s)` | `DOUBLE` + peringatan presisi hilang | `"Float64"` |
| `CHAR VARCHAR TINYTEXT TEXT MEDIUMTEXT LONGTEXT` | `TEXT` | `"string"` |
| lainnya (`DATE`, `DATETIME`, `JSON`, `BLOB`, `ENUM`, …) | `UNSUPPORTED` → `SEM016` | — |

Seluruh dtype **nullable**: `NULL` = `pd.NA`, sel kosong pada CSV = `NULL`.
Dipakai `Int64`/`Float64`/`string`, bukan `int64`/`float64`/`object`, karena
ketiganya mendukung `pd.NA` secara bawaan. Konsekuensinya perbandingan teks
menghasilkan `boolean[pyarrow]` dan masker di `.fillna(False)` tetap aman.

### 6.2 Pemetaan operator IR → pandas

| Operator IR | Kode pandas |
|---|---|
| `Scan(t, a)` | `a = tables["t"].add_prefix("a.")` (kolom internal `alias.kolom`) |
| `Join(INNER)` | `kiri.dropna(subset=kunci_kiri).merge(kanan.dropna(subset=kunci_kanan), how="inner", left_on=[...], right_on=[...])` |
| `Join(LEFT)` | `kiri.merge(kanan.dropna(subset=kunci_kanan), how="left", left_on=[...], right_on=[...])` |
| `Filter(p)` | `df = df[(<mask>).fillna(False)]` |
| `Aggregate` | `df.assign(__one=1, __k0=...)` → `groupby([...], dropna=False, sort=False).agg(...)` → `reset_index(drop=True)` |
| `Sort` | rangkaian `sort_values(..., kind="stable", key=lambda s: s.str.lower())` dari kunci terakhir ke pertama |
| `Project` | `pd.DataFrame({...})`; `pd.concat([...], axis=1)` bila ada nama keluaran ganda |
| `Distinct` | `~kunci.duplicated()` dengan kunci teks di-`.str.lower()` |
| `Limit(n, offset)` | `out.iloc[offset:offset + n]` |

### 6.3 Pemetaan agregat

| Agregat MySQL | pandas | Catatan |
|---|---|---|
| `COUNT(*)` | `("__one", "count")` | menghitung seluruh baris |
| `COUNT(x)` | `(x, "count")` | hanya `x` bukan NULL |
| `SUM(x)` | `lambda s: s.sum(min_count=1)` | `min_count=1` agar himpunan kosong → NULL, bukan 0 |
| `AVG(x)` | `"mean"` + `.round(4)` bila `x` INT | |
| `MIN`/`MAX` | `"min"`/`"max"` | untuk TEXT diurutkan menurut huruf kecil |
| agregat tanpa `GROUP BY` | dibangun sebagai **satu baris** secara eksplisit | termasuk saat input kosong |

### 6.4 Kunci teks (kolasi)

Seluruh perbandingan teks diturunkan ke huruf kecil **saat kompilasi**:

- literal dikecilkan sekali (`'TI'` → `"ti"`) sehingga tidak membayar
  `.str.lower()` pada literal;
- kolom di-`.str.lower()` sekali per ekspresi;
- antar-kolom: kedua sisi di-`.str.lower()`;
- `ORDER BY` memakai `key=lambda s: s.str.lower()`;
- `GROUP BY` memakai `__kN = kolom.str.lower()`;
- `DISTINCT` memakai `~kolom.str.lower().duplicated()`.

### 6.5 Sumber data

`data_loader.py` menghasilkan `dict[str, DataFrame]` bertipe nullable dari dua
sumber yang memakai **satu** fungsi `coerce_frame` sehingga dtypes identik:

- **CSV**: `dtype=str, keep_default_na=False, na_values=[""]` agar tidak ada
  tipe yang ditebak, lalu dikonversi mengikuti skema.
- **MySQL**: `load_tables_mysql(dsn, table_names, schema)` mengambil **hanya
  tabel yang benar-benar dibaca query** (`required_tables` menurut urut
  kemunculan pada `FROM` dan `JOIN`), sehingga nama tabel selalu dapat dicocokkan
  dengan `schema.get()` di sistem berkas sensitif huruf.

---

## 7. Perbedaan Semantik MySQL yang Ditangani dan Alasannya

Bagian ini adalah tempat sebagian besar bug kompiler semacam ini muncul.
Setiap butir wajib tercakup tes diferensial.

### 7.1 Kolasi (`utf8mb4_0900_ai_ci`)

**Perilaku MySQL.** Kolasi bawaan bersifat *case-insensitive*:
`'TI' = 'ti'` bernilai TRUE. Kolasi `0900` juga `NO PAD`, sehingga spasi di
akhir string bermakna.

**Alasan ditangani.** Tanpa emulasi, filter `WHERE jurusan = 'TI'` akan
mengembalikan nol baris di kompiler dan beberapa baris di MySQL — perbedaan
hasil yang paling mudah ditemui dalam data nyata.

**Implementasi.** `.str.lower()` diterapkan pada seluruh titik perbandingan
teks: `= <> < <= > >=`, `BETWEEN`, `IN`, `LIKE`, `ORDER BY`, `MIN`/`MAX`,
`GROUP BY`, `DISTINCT`, dan kunci `JOIN`.

**Batas emulasi.** Hanya karakter ASCII. Sensitivitas aksen (`é` = `e`) tidak
diemulasikan; data uji sengaja hanya ASCII. `SPEC.md` §20 mencatat ini sebagai
keterbatasan yang diakui, dan `PROGRESS.md` mengulanginya.

**Bukti.** Kolom `kota` pada dataset berisi varian `'Jakarta'` dan `'jakarta'`
khusus untuk menguji butir ini, dengan aturan: varian huruf hanya dipakai di
`WHERE`/`IN`/`LIKE`/`JOIN`, tidak pernah di `GROUP BY`/`DISTINCT`/pembeda
urutan (karena MySQL tidak menentukan nilai wakil grup).

### 7.2 Pembagian

**Perilaku MySQL.** `/` **tidak pernah** pembagian bulat. `INT / INT`
menghasilkan `DECIMAL` dengan 4 desimal (`7/2 = 3.5000`, `1/3 = 0.3333`,
`2/3 = 0.6667`). Jika salah satu operand `DOUBLE`, hasilnya `DOUBLE` tanpa
pembulatan. `DIV` memotong ke arah nol (`-7 DIV 2 = -3`). `%` mengambil sisa
dengan tanda pembagi (`-7 % 3 = -1`). Pembagi nol pada `/`, `DIV`, `%` pada
`SELECT` menghasilkan **NULL**, bukan error.

**Alasan ditangani.** Menerapkan pembagian Python/NumPy apa adanya akan
menghasilkan pembagian floating point (`7/2 = 3.5`) dan pembagian bulat Python
yang membulat ke arah minus tak hingga — keduanya berbeda dari MySQL.

**Implementasi.** Empat lapis:

1. Tipe `/` diturunkan ke `DOUBLE`; jika **tidak ada** operand `DOUBLE`
   sungguhan, skala desimal diturunkan menjadi *skala operand kiri + 4*.
2. Pembulatan memakai `ir.round_half_away` (untuk nilai statis) dan pemanggilan
   helper `_rdec(s, scale)` (untuk Series) — **half-away-from-zero**, meniru
   `DECIMAL` MySQL, bukan half-even biner bawaan `round()` Python.
3. `DIV` dan `%` diturunkan ke helper `_idiv`/`_imod` yang memotong ke arah nol.
4. Pembagi nol ditangani diam-diam menjadi `NULL` melalui `pd.NA`.

**Temuan audit Langkah 10.** `SPEC.md` §10.3 menyebut "kompiler menghasilkan
DOUBLE yang `.round(4)`", tetapi `round()` Python memakai half-even sedangkan
`DECIMAL` MySQL memakai half-up. Karena itu implementasi memakai
`round_half_away`; angka-angka contoh pada spec tetap sama
(`5/32 → 0.1563`, bukan `0.1562`). Perbedaan semantik ini tercatat sebagai
deviasi di `PROGRESS.md` — `SPEC.md` tidak diubah.

**Bukti.** Contoh `SPEC.md` §14.6 diuji langsung terhadap MySQL: `sks` = 2, 3, 4
menghasilkan `0.6667 / 1.0000 / 1.3333`, dan `sks DIV 3` menghasilkan `0 / 1 / 1`.

### 7.3 NULL

**Perilaku MySQL.** `NULL` bukan nilai tetapi status ketiadaan nilai. Ada tiga
aturan yang harus dipegang bersama:

1. **Logika tiga nilai (Kleene).** `NULL AND FALSE = FALSE`,
   `NULL OR TRUE = TRUE`, `NOT NULL = NULL`.
2. **Hanya TRUE yang lolos.** `WHERE`, `HAVING`, dan `ON` hanya meneruskan baris
   bernilai TRUE — `NULL` dan `FALSE` sama-sama ditolak.
3. **NULL menular.** Aritmetika dan perbandingan dengan `NULL` menghasilkan
   `NULL`.

**Alasan ditangani.** pandas menerapkan aturan yang berbeda: `NaN != NaN`
adalah TRUE, `fillna(False)` butuh aturan Kleene agar tidak salah, dan
`merge` **mencocokkan NaN dengan NaN** — yang langsung melanggar aturan bahwa
kunci `NULL` tidak pernah cocok.

**Implementasi.**

- `boolean` nullable pandas menerapkan aturan Kleene pada `&`, `|`, `~`.
- Setiap filter ditulis `df[(<mask>).fillna(False)]`, sehingga `NULL` (yang
  menjadi `NA`) ditolak bersama `FALSE`.
- Aritmetika dan perbandingan memakai operator pandas pada `Int64`/`Float64`/
  `string`, yang menghasilkan `NA` secara alami; literal `NULL` memakai
  `pd.NA`.
- **JOIN**: baris berkunci `NULL` dibuang dari sisi yang dipasangkan **sebelum**
  `merge` (`dropna(subset=...)`) karena pandas akan mencocokkan NaN dengan NaN.
  Pada `LEFT JOIN`, baris kiri berkunci `NULL` tetap dipertahankan.
- **`IN`** diturunkan menjadi rantai `OR` dari perbandingan `=`, bukan
  `Series.isin`, sehingga `x NOT IN (1, NULL)` tidak pernah TRUE. Dilarang
  memakai `isin` langsung karena `isin` mengabaikan logika tiga nilai.
- **`IS [NOT] NULL`** → `notna()`/`isna()` yang menghasilkan `boolean` tanpa NA.
- **`COUNT(*)` vs `COUNT(x)`**: `__one` = 1 untuk `COUNT(*)`, sedangkan
  `COUNT(x)` memakai `"count"` yang melewatkan `NA`.
- **`SUM`/`AVG`/`MIN`/`MAX`** mengabaikan `NA`; `SUM` memakai `min_count=1`
  agar himpunan kosong menghasilkan `NULL`, bukan `0`.
- **`GROUP BY`** memakai `dropna=False`, sehingga seluruh `NA` pada kunci
  membentuk **satu** grup, seperti MySQL.
- **`ORDER BY`** memakai `na_position="first"` untuk `ASC` dan `"last"` untuk
  `DESC`, sesuai aturan bahwa `NULL` dianggap lebih kecil dari nilai apa pun.
- **`DISTINCT`** menganggap dua `NA` sama dan mempertahankan kemunculan pertama.

### 7.4 Perbedaan semantik lain yang ditangani

| Perilaku MySQL | Alasan ditangani | Implementasi |
|---|---|---|
| Urutan baris tanpa `ORDER BY` tidak dijamin | membandingkan urutan akan memberi kegagalan semu | perbandingan sebagai **multiset** |
| Urutan antar baris berkunci sama tidak ditentukan | `sort_values` tidak stabil terhadap itu | tes selalu menambahkan kunci pembeda; `kind="stable"` |
| `LEFT JOIN` mempertahankan seluruh baris kiri | pandas default `how="inner"` | `how="left"` eksplisit; sisi kanan tetap `dropna` |
| `LIMIT 0` → hasil kosong dengan kolom tetap ada | `.iloc[:0]` memang mempertahankan kolom | diverifikasi terhadap oracle |
| `DISTINCT` + `ORDER BY` harus ada di daftar `SELECT` (error 3065) | prinsip "lebih ketat" | dilaporkan sebagai `SEM002` |
| Konversi tipe implisit (`nim = '123'`) | menolak berarti menolak, bukan menebak | `SEM005`, kompiler lebih ketat |
| `WHERE` non-boolean (`WHERE ipk`) diizinkan MySQL | | `SEM011`, kompiler lebih ketat |
| Nama tabel sensitif huruf di MySQL Linux | | kompiler memperlakukan tanpa membedakan huruf — **lebih longgar**, dicatat sebagai keterbatasan |

---

## 8. Strategi Pengujian dan Hasil Uji Diferensial

### 8.1 Strategi

Pengujian berlapis, satu lapis per fase, ditambah satu lapis **diferensial**
yang menghubungkan seluruh pipeline dengan oracle:

| Lapis | Berkas | Jumlah tes | Peran |
|---|---|---:|---|
| Lexer | `tests/test_lexer.py` | 53 | tokenisasi, escape, komentar, 5 kelas galat |
| Parser | `tests/test_parser.py` | 100 | presedensi, seluruh klausa, `LIMIT a, b`, galat + posisi tepat |
| Semantik + skema | `tests/test_semantic.py` | 119 | `SEM001`–`SEM016`, resolusi alias, introspeksi vs JSON |
| IR | `tests/test_ir.py` | 63 | urutan operator, `fold_constant`, `format_plan` |
| Code generator | `tests/test_codegen.py` | 98 | **12 berkas golden**, konvensi §13, semantik wajib §12 |
| **Diferensial** | `tests/test_differential.py` | **108** | 105 query diferensial + tes lingkungan oracle + konsistensi CSV↔MySQL |
| Kasus tepi | `tests/test_edge_cases.py` | 52 | 14 wajib ditolak + 17 diferensial + jumlah cakupan |
| CLI | `tests/test_cli.py` | 21 | exit code, stdout/stderr, opsi |
| Audit | `tests/test_audit.py` | 15 | namespace `exec`, larangan pustaka parser |
| UI | `tests/test_ui.py` | 7 | `streamlit.testing.v1.AppTest` |
| **Total** | | **636** | |

Berkas golden berjumlah 12 pasang (`.sql` + `.py`): `spec01`–`spec06`
mengikuti enam contoh `SPEC.md` §14, `extra01`–`extra06` melengkapi kasus yang
tidak tercakup contoh.

### 8.2 Aturan perbandingan diferensial

Aturan yang sama dipakai oleh harness `tests/mysql_oracle.py` **dan** oleh tab
Verifikasi pada UI, lewat satu modul `verify.py` — sehingga keduanya tidak
pernah berbeda pendapat:

1. Jumlah kolom dan nama kolom harus sama.
2. Sel dibandingkan **berdasarkan posisi kolom**, bukan nama (hasil boleh
   memiliki nama kolom ganda).
3. Baris dibandingkan sebagai **multiset**, kecuali query ber-`ORDER BY` lalu
   urutan ikut dibandingkan.
4. `Decimal` dari MySQL dikonversi ke `float`; `float` dengan toleransi `1e-9`.
5. `NULL` sama dengan `NULL`.
6. Masalah dibatasi 20 baris agar pesan tetap terbaca.

Bila berbeda, **perbaiki kompiler, bukan tesnya**.

### 8.3 Lingkungan oracle

- MySQL 8.4.11 lewat Docker (`docker-compose.yml`, image `mysql:8.4`),
  host port **3307**, database `sqlc_test`, `root`/`root`.
- `--character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci`.
- `@@sql_mode` diverifikasi sama dengan enam mode `SPEC.md` §2 sebelum tes
  berjalan; bila berbeda, tes **gagal**.
- DSN diatur lewat `SQLC_MYSQL_DSN`; nilai bawaan
  `mysql://root:root@localhost:3307/sqlc_test`.
- Tes bertanda `@pytest.mark.mysql` **tidak boleh di-skip**. Bila container
  berhenti, tes gagal — bukan dilewati.

### 8.4 Hasil uji (angka asli)

Dijalankan pada tanggal 4 Oktober 2026.

```
$ .venv/Scripts/python.exe -m pytest -q
636 passed in 20.82s

$ .venv/Scripts/python.exe -m pytest -q -m mysql
132 passed, 504 deselected in 16.74s

$ .venv/Scripts/python.exe -m pytest -q -m "not mysql"
504 passed, 132 deselected in 6.40s

$ .venv/Scripts/ruff.exe check .
All checks passed!

$ .venv/Scripts/mypy.exe --strict src
Success: no issues found in 14 source files
```

**Cakupan kode** (perintah yang disyaratkan `SPEC.md` §16 dan `PROMPT.md`
Langkah 10):

```
$ .venv/Scripts/python.exe -m pytest --cov=sqlcompiler --cov-report=term-missing -q
Name                               Stmts   Miss  Cover
src\sqlcompiler\__init__.py            1      0   100%
src\sqlcompiler\app_streamlit.py     182    182     0%   (*)
src\sqlcompiler\ast_nodes.py         117      0   100%
src\sqlcompiler\cli.py               140      9    94%
src\sqlcompiler\codegen.py           903    146    84%
src\sqlcompiler\data_loader.py       101     22    78%
src\sqlcompiler\errors.py             76      1    99%
src\sqlcompiler\ir.py                350     18    95%
src\sqlcompiler\lexer.py             236      0   100%
src\sqlcompiler\parser.py            400      4    99%
src\sqlcompiler\runtime.py            60      0   100%
src\sqlcompiler\schema_loader.py      77      2    97%
src\sqlcompiler\semantic.py          579     63    89%
src\sqlcompiler\verify.py             94     11    88%
----------------------------------------------------------------
TOTAL                               3316    458    86%
636 passed in 27.32s
```

**Ringkasan angka:**

| Metrik | Nilai |
|---|---:|
| Tes total | **636** |
| Tes lulus | **636** |
| Tes gagal | **0** |
| Tes di-skip | **0** |
| Tes bertanda `mysql` | **132** |
| Query diferensial | **105** |
| Cakupan (perintah baku `--cov=sqlcompiler`) | **86%** |
| Cakupan (sumber path `--cov=src/sqlcompiler`) | **91%** |

`SPEC.md` §16 mensyaratkan ≥ 45 query diferensial (tercapai: **105**) dan
`SPEC.md` §19 mensyaratkan cakupan ≥ 85% (tercapai: **86%** dan **91%**).

**Catatan (*) pada `app_streamlit.py`.** Streamlit mengeksekusi skrip sebagai
modul `__main__`, dan `coverage` menurunkan nama modulnya menjadi
`app_streamlit` (lewat `inspect.getmodulename`), yang tidak cocok dengan
`--source sqlcompiler` — `coverage` menolaknya dengan alasan
`"module 'app_streamlit' falls outside the --source spec"`. Bentuk
`--cov=<nama-modul>` memang tidak dapat menelusuri skrip `__main__`. Pada
perintah dengan sumber berupa path, modul yang sama tercatat **84%** dan
seluruh berkas tercatat **91%**. Ini cacat alat, bukan tes yang kurang; tidak
ada tes yang diubah untuk menaikkan angka.

### 8.5 Hasil uji diferensial

Seluruh 105 query pada `tests/test_differential.py` dijalankan dua kali — pada
server MySQL 8.4 sebagai **oracle**, dan pada kode hasil `compile_sql` — lalu
dibandingkan menurut bagian 8.2. **105/105 cocok**, tanpa satu pun kegagalan
yang tersisa pada akhir proyek. Empat bug kompiler nyata ditemukan melalui
jalur ini dan diperbaiki pada modulnya masing-masing (tercatat di
`PROGRESS.md`).

Selain itu, 17 query kasus tepi (`tests/test_edge_cases.py`) juga dijalankan
secara diferensial dan seluruhnya cocok: tabel kosong, 5 `JOIN`, `self-join`,
alias yang menimpa nama tabel, literal 24 digit dan desimal 38 digit, teks 5000
karakter, backtick, komentar blok, `LIMIT` 0/besar.

---

## 9. Keterbatasan dan Pengembangan Lanjutan

### 9.1 Keterbatasan yang diakui

Daftar lengkap ada di `SPEC.md` §20. Ringkasan yang paling berdampak:

**Ruang lingkup.**
- Hanya satu pernyataan `SELECT`; tidak ada transaksi, indeks, atau optimasi
  biaya. Seluruh data dimuat ke memori.
- Di luar scope dan selalu ditolak dengan `SYN005`: CTE, window function,
  `RIGHT`/`FULL`/`CROSS JOIN`, comma join, non-equi join, subquery, DDL/DML,
  `db.tabel`, `SELECT` tanpa `FROM`, `&& || !`, nomor urut kolom pada
  `ORDER BY`/`GROUP BY`, tipe tanggal/waktu/JSON/blob/enum, banyak statement,
  parameter binding.
- Level 3 `SPEC.md` §2 (`COUNT(DISTINCT x)`, fungsi skalar, `CASE WHEN`,
  `JOIN ... USING`, `IN (SELECT ...)`, `UNION`, `TRUE`/`FALSE`, `<=>`)
  **tidak** diimplementasikan.

**Kebenaran.**
- Acuan perilaku hanya MySQL 8.4 dengan `sql_mode` bawaan. MariaDB dan versi
  MySQL lain dapat berbeda (kolasi bawaan, `ONLY_FULL_GROUP_BY`, aturan
  `DECIMAL`).
- Emulasi kolasi `utf8mb4_0900_ai_ci` hanya untuk karakter ASCII;
  sensitivitas aksen tidak diemulasikan.
- Kolom `DECIMAL` dipetakan ke `DOUBLE`; presisi desimal hilang (peringatan
  diberikan saat introspeksi).
- Pembulatan memakai half-away-from-zero pada representasi biner; MySQL
  memakai half-up pada desimal. Dapat berbeda pada kasus *tie* eksak.
- Nama tabel diperlakukan tanpa membedakan huruf besar/kecil; di MySQL pada
  Linux bersifat sensitif — kompiler **lebih longgar** di titik ini.
- Kompiler lebih ketat daripada MySQL pada konversi tipe implisit, `WHERE`
  non-boolean, `ON` non-equi, dan fungsi ketergantungan fungsional pada
  `GROUP BY`.

**Pengukuran.**
- Baris `app_streamlit.py` tidak tercatat oleh `pytest --cov=sqlcompiler`
  (lihat bagian 8.4).

### 9.2 Bukan sandbox keamanan

`run_compiled` membatasi namespace, tetapi **bukan sandbox keamanan yang kuat**.
Keterbatasannya eksplisit:

- `SAFE_BUILTINS` tetap memuat `getattr` dan `object`, sehingga rantai
  `object.__subclasses__()` masih dapat mencapai kelas bebas — pelarian standar
  dari "sandbox" Python.
- `pd` adalah modul pandas sungguhan, yang dapat membaca dan menulis berkas.
- Kode yang dieksekusi **selalu** keluaran `codegen` sendiri, bukan teks yang
  diberikan pengguna. Jangan mengeksekusi kode hasil pihak ketiga melalui
  fungsi ini.

Yang dicapai oleh pembatasan adalah mencegah kekeliruan tak disengaja (impor
salah, akses berkas tanpa sengaja) dan menjaga setiap eksekusi terisolasi dari
eksekusi lain (salinan `__builtins__` per panggilan) — bukan melindungi dari
kode yang memang berniat jahat. Hal ini tercatat pada `SPEC.md` §13, §20, dan
`README.md`.

### 9.3 Pengembangan lanjutan yang disarankan

Disarankan berdasarkan urutan prioritas yang tersisa, bukan klaim tentang kode
yang ada:

1. **Predicate pushdown** (`SPEC.md` §11, opsional, belum diimplementasikan).
   Syaratnya ketat: hanya konjungsi `WHERE` yang menyentuh satu tabel, hanya ke
   kedua sisi `INNER JOIN` atau sisi kiri `LEFT JOIN`, dan **dilarang** ke sisi
   kanan `LEFT JOIN`.
2. **Empat kasus tertunda dari audit Langkah 10** — didokumentasikan di
   `PROGRESS.md` sebagai masalah terbuka:
   aritmetika dengan operand `NULL` statis di dalam perbandingan
   (`(ipk + NULL) > 3`), overflow kelipatan `10^skala` pada `_rdec` untuk
   pembagi sangat kecil, dua perbedaan pembulatan yang sudah tercatat, dan
   pengukuran cakupan per berkas untuk `app_streamlit.py`.
3. **Level 3 `SPEC.md` §2**, sesuai urutan prioritas yang tercantum di sana.
4. **Dukungan `SELECT` tanpa `FROM`** — saat ini ditolak dengan `SYN001` dan
   pesan eksplisit, sehingga pengguna mendapat penjelasan, tetapi menambahkannya
   tidak mengubah pipeline.
5. **Tipe tanggal/waktu dan `DECIMAL` sejati** — keduanya memerlukan keputusan
   dtype pandas baru dan menyangkut seluruh aturan kolasi/urutan.

---

## 10. Bagian yang Diragukan Akurasinya

Bagian ini dilaporkan sesuai kriteria penyelesaian `PROMPT.md` Langkah 11.
Kelasnya dibedakan agar tinjauan manusia dapat difokuskan.

| # | Bagian | Keraguan | Yang sudah diperiksa | Saran tinjauan |
|---|---|---|---|---|
| 1 | 3 (grammar EBNF) | Grammar disalin dari `SPEC.md` §5 dan dihubungkan ke nama metode `parser.py`; **tidak** diverifikasi otomatis bahwa setiap produksi tepat setara rantai metode | 100 tes parser termasuk presedensi dan 10 galat dengan posisi tepat; seluruh 105 query diferensial lolos | cocokkan produksi per produksi terhadap `_parse_*` bila ingin keyakinan penuh |
| 2 | 4.1–4.6 (penjelasan tiap fase) | Deskripsi struktur disusun dari membaca kode, bukan dari diagram rancangan asli yang sudah divalidasi | seluruh klaim perilaku punya tes; tidak ada klaim fitur yang tidak ada | — |
| 3 | 7.1 (kolasi) | Klaim "hanya ASCII" diambil dari `SPEC.md` §10.5/§20 dan dari kenyataan bahwa data uji hanya ASCII; **tidak** diuji bahwa kolom berisi `é` memang salah | filter/kelompokkan/pengurutan teks teruji penuh terhadap oracle | jika nanti ada data non-ASCII, uji dulu sebelum percaya |
| 4 | 8.4 (angka) | **Rendah** — seluruh angka berasal dari keluaran perintah yang dijalankan saat penyusunan dan dicantumkan apa adanya | 636/0 gagal/0 lewat, 132 `mysql`, 105 query, 86%/91% | jalankan ulang bila jumlah tes berubah |
| 5 | 9.3 (pengembangan lanjutan) | Merupakan **saran**, bukan laporan implementasi; tidak ada satupun yang diklaim sudah ada | klaim "belum diimplementasikan" diverifikasi dengan pencarian di kode | — |
| 6 | Selisih `SPEC.md` §15 vs implementasi UI | `SPEC.md` §15 menyebut "UI hanya memanggil `compile_sql`", sedangkan tab Verifikasi (`SPEC.md` §1) wajib mengeksekusi kode. Implementasi memanggil `compile_sql` **dan** `run_compiled`, keduanya dari `runtime.py` | tidak ada logika kompilasi yang diduplikasi; tercatat sebagai ambiguitas di `PROGRESS.md` | putuskan interpretasi mana yang diikat |
| 7 | Susunan `SPEC.md` §17 vs `verify.py` | `verify.py` tidak tercantum pada daftar struktur `SPEC.md` §17, tetapi dipakai oleh harness dan UI | modul hanya menambah, tidak mengubah kontrak modul yang tercantum | putuskan apakah `SPEC.md` akan disesuaikan |
| 8 | Jumlah baris modul | Dihitung dengan `grep -c ""` per berkas dan akan bergeser setiap ada perubahan | — | anggap ilustratif, bukan kontrak |

Bagian yang **tidak** diragukan: seluruh angka tes dan cakupan (diukur),
seluruh perilaku yang dinyatakan di bagian 6 dan 7 (tertutup tes diferensial
terhadap MySQL 8.4), serta klaim bahwa fitur di luar scope memang ditolak
(tertutup tes penolakan pada `tests/test_parser.py` dan
`tests/test_edge_cases.py`).
