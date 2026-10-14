# sqlcompiler — Kompiler Query MySQL ke Python (pandas)

Aplikasi ini **menerjemahkan query SQL MySQL menjadi kode Python (pandas)**
yang hasilnya **sama persis** dengan MySQL.

> **Contoh singkat.** Query ini
>
> ```sql
> SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC;
> ```
>
> keluar sebagai kode Python ini (keluaran asli, bukan tulisan tangan) —
> siap disalin, disimpan, atau dijalankan:
>
> ```python
> import pandas as pd
>
>
> def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
>     # SQL: FROM mahasiswa
>     df = tables["mahasiswa"].add_prefix("mahasiswa.")
>     # SQL: WHERE ipk > 3.5
>     df = df[(df["mahasiswa.ipk"] > 3.5).fillna(False)]
>     # SQL: ORDER BY ipk DESC
>     df = df.sort_values(by=["mahasiswa.ipk"], ascending=[False], na_position="last", kind="stable")
>     # SQL: SELECT nama, ipk
>     out = pd.DataFrame({"nama": df["mahasiswa.nama"], "ipk": df["mahasiswa.ipk"]})
>     return out.reset_index(drop=True)
> ```

**Isi dokumen ini**

| Bagian | Isi |
|---|---|
| [1. Cara kerja](#1-cara-kerja-aplikasi-ini) | apa yang dilakukan aplikasi, dari teks SQL sampai tabel hasil |
| [2. Prasyarat](#2-prasyarat) | apa yang harus terpasang di komputer |
| [3. Langkah 1 — nyalakan Docker](#3-langkah-1--nyalakan-docker-wajib-dijalankan-pertama) | **wajib**, menyalakan MySQL (3307) + Adminer (8081) |
| [4. Langkah 2 — pasang](#4-langkah-2--pasang-aplikasinya) | membuat *virtual environment* + memasang aplikasi |
| [5. Langkah 3 — jalankan (UI)](#5-langkah-3--jalankan-aplikasi-antarmuka-web) | cara paling mudah: klik-klik di browser |
| [6. Langkah 4 — jalankan (CLI)](#6-langkah-4--jalankan-lewat-terminal) | lewat terminal/perintah |
| [7. Langkah 5 — tes](#7-langkah-5--jalankan-tes-untuk-pengembang) | memastikan semuanya masih berjalan benar |
| [8. Fitur](#8-fitur-yang-didukung) | query seperti apa yang boleh dikompilasi |
| [9. Keterbatasan](#9-keterbatasan-yang-diketahui) | batas yang disengaja |
| [10. Struktur & dokumen](#10-struktur-proyek-dan-dokumen-lain) | letak berkas dan dokumen lain |

---

## 1. Cara kerja aplikasi ini

Bayangkan aplikasi ini seperti **penerjemah bahasa**. Query adalah bahasa
MySQL, keluarannya adalah bahasa Python. Prosesnya berjalan dalam enam tahap,
masing-masing satu berkas di `src/sqlcompiler/`:

```
   Query MySQL (teks yang Anda ketik)
        │
        │  [1] LEXER        lexer.py
        │       memecah teks menjadi potongan kecil (token):
        │       "SELECT", "nama", "FROM", "mahasiswa", ...
        ▼
   [2] PARSER        parser.py
        menyusun token menjadi pohon struktur (AST),
        sekaligus mengecek tata bahasa: kurung, koma, urutan klausa
        ▼
   [3] SEMANTIK      semantic.py  +  skema tabel
        mencocokkan nama kolom/tabel dengan skema yang nyata,
        menentukan tipe tiap ekspresi, menangkap salah ketik
        (mis. "namaa" → SEM002: Maksudmu 'nama'?)
        ▼
   [4] IR            ir.py
        menyusun rencana eksekusi: pindai → saring → kelompokkan
        → urutkan → proyeksikan → batasi
        ▼
   [5] CODE GENERATOR  codegen.py
        menerjemahkan rencana itu menjadi kode Python (pandas)
        ─────────────────────────────────────────────────────
        ★  INI PRODUK UTAMANYA: teks kode Python  ★
        ─────────────────────────────────────────────────────
        ▼
   [6] RUNTIME       runtime.py
        menjalankan kode itu → menghasilkan tabel (pandas.DataFrame)
        ▼
   Dibandingkan dengan hasil MySQL asli  →  COCOK / TIDAK COCOK
```

**Yang penting dipahami:**

1. **Produknya adalah teks kode Python.** Menjalankan kode dan membandingkannya
   dengan MySQL hanyalah *cara membuktikan* bahwa terjemahannya benar
   (`SPEC.md` §1).
2. **MySQL di sini berperan sebagai penguji (oracle).** Aplikasi tidak menebak
   apakah hasilnya benar — ia menjalankan query yang sama di MySQL 8.4 yang
   sungguhan, lalu membandingkan baris demi baris. Karena itu **Docker wajib
   dinyalakan lebih dulu** (Langkah 1).
3. **Aplikasi lebih ketat daripada MySQL.** Query yang tidak didukung
   **ditolak dengan pesan dan posisi yang jelas**, bukan diterima lalu
   menghasilkan hasil yang salah.

**Dua hal yang dibutuhkan aplikasi:**

| | Isi | Sumber |
|---|---|---|
| **Skema** | daftar tabel + kolom + tipenya | berkas `examples/schema.json` **atau** dibaca langsung dari MySQL |
| **Data** | isi tabelnya | folder berkas CSV di `examples/` **atau** dibaca langsung dari MySQL |

Keduanya bisa dipilih di *sidebar* pada antarmuka web.

---

## 2. Prasyarat

| | Versi | Kegunaan |
|---|---|---|
| **Python** | 3.12 atau lebih baru | menjalankan aplikasi |
| **Docker Desktop** (dengan Compose) | apa pun | menjalankan MySQL 8.4 dan Adminer |
| **Git** | opsional | mengambil repositori |

Cek dari terminal:

```powershell
python --version      # harus 3.12 ke atas
docker --version
docker compose version
```

> **Penting.** Oracle-nya hanya **MySQL 8.4**. XAMPP atau MariaDB **tidak
> memenuhi syarat** karena perilakunya berbeda (kolasi bawaan,
> `ONLY_FULL_GROUP_BY`, aturan `DECIMAL`) — hasilnya bisa beda padahal query-nya
> sama.

---

## 3. Langkah 1 — Nyalakan Docker (wajib, dijalankan pertama)

Sebelum memakai aplikasi, nyalakan **dua layanan** sekaligus:

| Layanan | Port | Fungsinya |
|---|---|---|
| **MySQL 8.4** (container `sqlc-mysql`) | **3307** | menyimpan tabel uji **dan** menjadi penguji kebenaran hasil |
| **Adminer** (container `sqlc-adminer`) | **8081** | melihat isi database lewat browser |

### 3.1 Nyalakan

Buka terminal **di dalam folder proyek** (tempat berkas `docker-compose.yml`
berada), lalu jalankan satu perintah ini:

```powershell
docker compose up -d --wait
```

Perintah itu akan mengunduh gambar bila belum ada, lalu menunggu sampai
kedua container benar-benar siap.

### 3.2 Pastikan sudah jalan

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Yang harus muncul (keduanya `Up`; `sqlc-mysql` juga menampilkan `(healthy)`):

```
NAMES          STATUS                    PORTS
sqlc-adminer   Up ...                    0.0.0.0:8081->8080/tcp, [::]:8081->8080/tcp
sqlc-mysql     Up ... (healthy)          0.0.0.0:3307->3306/tcp, [::]:3307->3306/tcp
```

### 3.3 (Opsional) Lihat data lewat browser — Adminer

Buka **<http://localhost:8081>**, lalu isi formulir login:

| Isian | Nilai |
|---|---|
| System | `MySQL / MariaDB` |
| Server | `mysql` — *atau* `host.docker.internal:3307` (dua-duanya sudah diuji) | 
| Username | `root` |
| Password | `root` |
| Database | `sqlc_test` |

Tekan **Login**. Anda akan melihat tabel `mahasiswa`, `mata_kuliah`, dan
`nilai`. Ini berguna untuk melihat langsung data yang dipakai sebagai bahan uji.

### 3.4 Konfigurasi server

| | |
|---|---|
| host / port | `localhost` / **3307** |
| user / password | `root` / `root` |
| database | `sqlc_test` |
| DSN | `mysql://root:root@localhost:3307/sqlc_test` |
| charset / collation | `utf8mb4` / `utf8mb4_0900_ai_ci` |
| isi awal tabel | dibuat dari `examples/mysql_init.sql` |

DSN tersebut adalah **nilai bawaan**; aplikasi, CLI, dan seluruh tes memakainya
tanpa perlu mengatur apa pun. Bila Anda ingin memakai DSN lain, setel variabel
lingkungan `SQLC_MYSQL_DSN`:

```powershell
# Windows (PowerShell)
$env:SQLC_MYSQL_DSN = "mysql://root:root@localhost:3307/sqlc_test"
```

```bash
# Linux / macOS
export SQLC_MYSQL_DSN="mysql://root:root@localhost:3307/sqlc_test"
```

### 3.5 Berhenti / ulangi

```powershell
docker compose logs -f mysql      # lihat log MySQL (Ctrl+C untuk keluar)
docker compose down               # hentikan kedua container
docker compose up -d --wait       # nyalakan lagi
```

> Saat dijalankan ulang, isi database **dibuat ulang otomatis** dari
> `examples/mysql_init.sql`, sehingga data selalu konsisten.

### 3.6 Bila ada masalah

| Gejala | Penyebab | Solusi |
|---|---|---|
| `error during connect ... docker desktop is not running` | Docker Desktop belum dibuka | buka Docker Desktop, tunggu ikon biru |
| port sudah dipakai (`port is already allocated`) | sisa container lama | `docker compose down` lalu ulangi |
| container `Created` tapi tidak `Up` | menunggu *healthcheck* | tunggu ±30 detik, cek lagi `docker ps` |
| tab Verifikasi gagal / 132 tes `mysql` gagal | server mati | `docker compose up -d --wait` |

> **Catatan.** Tes bertanda `mysql` memang **gagal, bukan dilewati**, bila
> server berhenti. Oracle yang hilang harus terlihat, bukan tersamar
> (`PROMPT.md` Langkah 7).

---

## 4. Langkah 2 — Pasang aplikasinya

Dijalankan **sekali saja** saat pertama kali.

### Windows (PowerShell)

```powershell
cd C:\laragon\www\sql-compiler_to-python

python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"
```

### Linux / macOS

```bash
cd /path/to/sql-compiler_to-python

python -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"
```

**Penjelasan singkat:**

- `python -m venv .venv` membuat ruang terpisah khusus proyek ini (disebut
  *virtual environment*) supaya pustaka yang diunduh tidak bercampur dengan
  Python lain di komputer Anda.
- `Activate.ps1` / `source .../activate` **mengaktifkan** ruang itu. Setiap
  kali membuka terminal baru untuk proyek ini, langkah ini harus diulang.
- `pip install -e ".[dev]"` memasang aplikasi beserta seluruh pustakanya
  (pandas, PyMySQL, typer, rich, streamlit) plus alat bantu pengujian
  (pytest, ruff, mypy).

**Pastikan berhasil:**

```powershell
sqlc --help
```

Bila muncul daftar perintah seperti di bawah, pemasangan berhasil:

```
 Usage: sqlc [OPTIONS] COMMAND [ARGS]...

 Kompiler query MySQL 8.4 ke Python (pandas).

 Commands
  compile  Kompilasi query lalu cetak kode Python apa adanya.
  run      Kompilasi query, jalankan kodenya pada data, lalu cetak tabel hasil.
  explain  Tampilkan token, AST, plan, dan kode hasil kompilasi.
  schema   Introspeksi skema dari server MySQL lalu tulis ke berkas JSON.
```

### Bila ada masalah

| Gejala | Solusi |
|---|---|
| `'python' is not recognized` | pakai `py -3.12 -m venv .venv`, atau pasang Python dari python.org |
| `sqlc` tidak dikenal | aktifkan kembali venv: `.\.venv\Scripts\Activate.ps1` |
| menjalankan `Activate.ps1` ditolak (*execution policy*) | jalankan `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, buka terminal baru, ulangi |
| `streamlit` tidak dikenal | pastikan venv aktif, lalu `pip install -e ".[dev]"` lagi |

---

## 5. Langkah 3 — Jalankan aplikasi (antarmuka web)

Ini **cara paling mudah** memakai aplikasi ini.

```powershell
streamlit run src\sqlcompiler\app_streamlit.py --server.headless true
```

> Linux/macOS: `streamlit run src/sqlcompiler/app_streamlit.py --server.headless true`

Lalu buka **<http://localhost:8501>** di browser.
Tekan `Ctrl+C` di terminal untuk mematikannya.

### Yang harus diklik

1. **Isi query** pada kotak berjudul **Query MySQL**. Contoh siap tempel:

   ```sql
   SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC LIMIT 5;
   ```

2. Tekan tombol **Kompilasi**.
3. **Panel utama** menampilkan **kode Python hasil kompilasi**. Di situ ada
   tombol **salin** bawaan (ikon 📋 pada kotak kode) dan tombol
   **Unduh .py** bila ingin menyimpannya sebagai berkas.
4. Lihat tab bawah untuk tahap-tahap internal: **Token | AST | Semantik | Plan**.
   Tab ini menjelaskan *mengapa* kode dihasilkan seperti itu.
5. Buka tab **Verifikasi**, lalu tekan **Jalankan**. Hasil Python dan hasil
   MySQL 8.4 ditampilkan **berdampingan** lengkap dengan penanda
   **COCOK** / **TIDAK COCOK**.

### Sidebar (penyetelan)

| Setelan | Nilai bawaan | Keterangan |
|---|---|---|
| **Skema dari** | `Berkas JSON` | ganti ke `Introspeksi MySQL` untuk membaca struktur tabel langsung dari server |
| **Berkas skema JSON** | `examples/schema.json` | |
| **Data dari** | `CSV` | ganti ke `MySQL` untuk membaca isi tabel langsung dari server |
| **Direktori CSV** | `examples` | |
| **DSN MySQL (oracle)** | `mysql://root:root@localhost:3307/sqlc_test` | dipakai untuk introspeksi, data, dan sisi MySQL pada tab Verifikasi |

> Karena setelan bawaan menunjuk ke `examples/`, aplikasi langsung bisa
> dipakai tanpa mengubah apa pun — **asal Docker sudah menyala**.

### Contoh query lain

Banyak contoh siap pakai ada di [`examples/queries.sql`](examples/queries.sql):

```sql
-- agregat + GROUP BY + HAVING
SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata
FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim
GROUP BY m.jurusan
HAVING COUNT(*) > 2
ORDER BY rata DESC;

-- LEFT JOIN untuk mencari yang tidak punya nilai
SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim
WHERE n.nim IS NULL;

-- LIKE, IN, BETWEEN, IS NOT NULL
SELECT nim, nama FROM mahasiswa
WHERE nama LIKE 'A%' AND jurusan IN ('TI', 'SI')
  AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL;

-- pembagian, DIV, dan sisa
SELECT sks, sks / 3 AS bagi, sks DIV 3 AS bulat, sks % 3 AS sisa
FROM mata_kuliah ORDER BY sks;
```

### Bila ada masalah

| Gejala | Solusi |
|---|---|
| halaman kosong / gagal memuat | pastikan perintah `streamlit run` masih berjalan di terminal |
| muncul merah berisi `SEM...` / `SYN...` / `LEX...` | query ditolak — pesannya menjelaskan baris dan kolomnya; itu perilaku yang benar |
| tab Verifikasi menampilkan galat koneksi | Docker belum nyala → ulangi Langkah 1 |
| `port 8501 sudah dipakai` | tambahkan `--server.port 8502` |

---

## 6. Langkah 4 — Jalankan lewat terminal

Semua perintah memakai format:

```
sqlc <perintah> "<QUERY ANDA>" --schema examples/schema.json
```

| Perintah | Fungsinya |
|---|---|
| `sqlc compile "<SQL>"` | mencetak kode Python hasil kompilasi |
| `sqlc run "<SQL>"` | mengompilasi **lalu** menjalankannya, mencetak tabel hasil |
| `sqlc explain "<SQL>"` | menampilkan token, AST, plan, dan kode |
| `sqlc schema --out schema.json` | menyimpan struktur tabel dari server ke berkas JSON |

**Pilihan umum:**

| Pilihan | Fungsi |
|---|---|
| `--schema <berkas.json>` | sumber skema: berkas JSON |
| `--dsn <dsn>` | sumber skema: langsung dari server (atau pakai `SQLC_MYSQL_DSN`) |
| `--data <folder>` | data untuk `run`: baca dari folder CSV |
| `--dsn <dsn>` (pada `run`) | data untuk `run`: baca langsung dari MySQL |
| `--file` / `-f <berkas.sql>` | baca query dari berkas, bukan dari argumen |

### 6.1 Mengompilasi saja

```powershell
sqlc compile "SELECT nama FROM mahasiswa WHERE ipk > 3" --schema examples/schema.json
```

```python
import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: WHERE ipk > 3
    df = df[(df["mahasiswa.ipk"] > 3).fillna(False)]
    # SQL: SELECT nama
    out = pd.DataFrame({"nama": df["mahasiswa.nama"]})
    return out.reset_index(drop=True)
```

Karena kode dicetak ke stdout, hasilnya bisa langsung disimpan ke berkas:

```powershell
sqlc compile "SELECT nama FROM mahasiswa" --schema examples/schema.json > hasil.py
```

### 6.2 Mengompilasi sekaligus menjalankan

```powershell
sqlc run "SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC LIMIT 3" --schema examples/schema.json --data examples
```

```
+-------------+
| nama  | ipk |
|-------+-----|
| Ahmad | 3.9 |
| Dita  | 3.8 |
| ani   | 3.6 |
+-------------+
3 baris, 2 kolom
```

Untuk membaca data langsung dari Docker (tanpa CSV), ganti `--data examples`
menjadi `--dsn mysql://root:root@localhost:3307/sqlc_test`.

### 6.3 Melihat tahap dalam (token, AST, plan)

```powershell
sqlc explain "SELECT DISTINCT jurusan FROM mahasiswa WHERE ipk > 3.5 ORDER BY jurusan" --schema examples/schema.json
```

```
== TOKEN ==
baris kolom tipe         lexeme
   1 1    KEYWORD      SELECT
   1 8    KEYWORD      DISTINCT
   1 17   IDENT        jurusan
   1 25   KEYWORD      FROM
   ...
== AST ==
SelectStatement
  distinct: True
  items:
    SelectItem
      ...
```

### 6.4 Melihat galat

```powershell
sqlc compile "SELECT namaa FROM mahasiswa" --schema examples/schema.json
```

```
SEM002: Kolom 'namaa' tidak ditemukan di 'mahasiswa'. Maksudmu 'nama'?
  1 | SELECT namaa FROM mahasiswa
             ^^^^^
```

**Kode keluaran (exit code):**

| Kode | Arti |
|---|---|
| `0` | sukses |
| `1` | ada error kompilasi (galat ditampilkan di **stderr**) |
| `2` | error runtime / pemakaian |

Berguna untuk skrip: `sqlc compile "..." ; if ($LASTEXITCODE -eq 0) { ... }`

### 6.5 Menyimpan skema dari server

```powershell
sqlc schema --out examples/schema.json
```

Berkas ini yang dipakai saat memilih **Skema dari → Berkas JSON**.

---

## 7. Langkah 5 — Jalankan tes (untuk pengembang)

```powershell
pytest -q                     # seluruh tes (perlu Docker menyala)
pytest -q -m "not mysql"      # tes tanpa MySQL (504 tes) — bisa tanpa Docker
pytest -q -m mysql            # hanya tes oracle (132 tes) — Docker wajib
ruff check .                  # pengecekan gaya kode
mypy --strict src             # pengecekan tipe
pytest --cov=sqlcompiler --cov-report=term-missing    # cakupan kode
```

**Hasil terakhir:** `636 passed, 0 gagal, 0 dilewati` · `All checks passed!` ·
`Success: no issues found in 14 source files` · cakupan **86%**
(target ≥ 85%).

Struktur pengujian (`SPEC.md` §16): lexer, parser, semantik, IR, **12 berkas
golden** codegen, **tes diferensial** terhadap MySQL 8.4 sungguhan, CLI, UI
(AppTest), kasus tepi, dan audit pengerasan `exec`.

> **Catatan pengukuran cakupan.** `--cov=sqlcompiler` menelusuri modul berdasarkan
> *nama modul* yang dapat diimpor, sedangkan skrip Streamlit dijalankan sebagai
> `__main__`. Karena itu baris `app_streamlit.py` tidak ikut tercatat pada
> perintah itu; jalankan juga dengan sumber berupa path bila angka per berkas
> dibutuhkan: `pytest --cov=src/sqlcompiler --cov-report=term-missing`
> (hasilnya **91%**, `app_streamlit.py` 84%).

---

## 8. Fitur yang didukung

**Level 1 (inti)** — `SELECT`, `*`, kolom, ekspresi aritmetika `+ - * /`, alias kolom,
`FROM` satu tabel (alias opsional), `WHERE` dengan perbandingan
`= <> != < > <= >=` serta `AND`/`OR`/`NOT`, `ORDER BY` (multi-kolom, `ASC`/`DESC`),
`LIMIT`.

**Level 2 (target proyek)** — di atas, plus `DISTINCT`, `alias.*`,
`[INNER] JOIN`, `LEFT [OUTER] JOIN` (hanya *equi-join*), `LIKE`, `IN (daftar nilai)`,
`BETWEEN`, `IS [NOT] NULL`, `DIV` dan `%`, `LIMIT offset, n` serta
`LIMIT n OFFSET offset`, identifikasi berkutip backtick, string berkutip
tunggal/ganda, fungsi agregat `COUNT` `SUM` `AVG` `MIN` `MAX`, `GROUP BY`, `HAVING`,
dan komentar `-- `, `#`, `/* blok */`.

**Kode error** — `LEX001..` (lexer), `SYN001..` (parser, termasuk penolakan fitur
luar scope dengan `SYN005`), `SEM001..SEM016` (semantik). Setiap galat selalu
memuat kode, pesan, baris, kolom, dan penunjuk `^^^^`.

**Contoh query** ada di [`examples/queries.sql`](examples/queries.sql).

**Yang dijaga tetap sama dengan MySQL** (diaudit per titik, lihat
`docs/LAPORAN_TEKNIS.md` §7): kolasi teks *case-insensitive*, aturan pembagian
`/` `DIV` `%` termasuk pembulatan 4 desimal, dan logika tiga nilai `NULL`
(termasuk kunci `NULL` yang tidak pernah cocok saat `JOIN`).

---

## 9. Keterbatasan yang diketahui

Daftar lengkap ada di **[`SPEC.md` §20 — Keterbatasan yang Diakui](SPEC.md)**. Ringkasnya:

- Hanya satu pernyataan `SELECT`; tidak ada transaksi, indeks, atau optimasi biaya.
  Seluruh data dimuat ke memori.
- Acuan perilaku adalah **MySQL 8.4 dengan `sql_mode` bawaan**. MariaDB dan versi
  MySQL lain dapat berbeda.
- Emulasi kolasi `utf8mb4_0900_ai_ci` hanya untuk ASCII; sensitivitas aksen dan
  aturan Unicode tidak diemulasikan.
- Kolom `DECIMAL` dipetakan ke `DOUBLE` (presisi hilang); tipe tanggal/waktu,
  JSON, blob, dan enum belum didukung.
- Pembulatan 4 desimal memakai *half-away-from-zero* pada representasi biner,
  sedangkan MySQL memakai *half-up* desimal — dapat berbeda pada kasus *tie* eksak.
- Kompiler **lebih ketat** daripada MySQL pada konversi tipe implisit, `WHERE`
  non-boolean, `ON` non-*equi*, dan fungsi ketergantungan fungsional pada
  `GROUP BY`: query yang tidak didukung **ditolak dengan error yang jelas**, bukan
  diterima lalu menghasilkan hasil berbeda.
- Nama tabel diperlakukan tanpa membedakan huruf besar/kecil (di MySQL Linux
  bersifat sensitif).
- **Di luar scope dan selalu ditolak** (`SYN005`): CTE, window function,
  `RIGHT`/`FULL`/`CROSS JOIN`, *comma join*, subquery, DDL/DML, banyak pernyataan
  sekaligus, `SELECT` tanpa `FROM`, dan lain-lain — lihat `SPEC.md` §2.

### Catatan sandbox eksekusi kode

`run_compiled` mengeksekusi kode hasil kompilasi lewat `exec` dengan namespace
terbatas: `__builtins__` hanyalah daftar putih fungsi aman (tanpa `open`, `eval`,
`exec`, `compile`, `input`), `__import__` diganti sehingga **hanya `pandas`** yang
boleh diimpor, dan hanya `pd` yang tersedia sebagai modul. Tabel builtins
di-**salin** pada setiap eksekusi sehingga satu eksekusi tidak dapat merusak
eksekusi berikutnya.

**Ini bukan sandbox keamanan yang kuat** (`SPEC.md` §13, §20):

- `SAFE_BUILTINS` tetap memuat `getattr` dan `object`, sehingga rantai
  `object.__subclasses__()` masih dapat mencapai kelas bebas — pelarian standar
  dari "sandbox" Python.
- `pd` adalah modul pandas sungguhan, yang dapat membaca dan menulis berkas.
- Kode yang dieksekusi **selalu** keluaran `codegen` sendiri, bukan teks yang
  diberikan pengguna. Jangan mengeksekusi kode hasil pihak ketiga melalui
  fungsi ini.

---

## 10. Struktur proyek dan dokumen lain

```
sqlcompiler/
├── README.md                  ← berkas ini
├── docker-compose.yml         ← MySQL 8.4 (3307) + Adminer (8081)
├── pyproject.toml             ← dependensi + perintah `sqlc`
├── SPEC.md                    ← spesifikasi teknis (satu-satunya sumber kebenaran)
├── PROMPT.md                  ← rangkaian prompt pembangunan
├── PROGRESS.md                ← kemajuan, keputusan desain, deviasi
├── src/sqlcompiler/
│   ├── errors.py              ← kode error + perender galat
│   ├── lexer.py               ← tokenisasi (ditulis manual)
│   ├── ast_nodes.py           ← node AST (dataclass beku)
│   ├── parser.py              ← recursive descent (ditulis manual)
│   ├── schema_loader.py       ← skema JSON + introspeksi MySQL
│   ├── semantic.py            ← SEM001–SEM016, sistem tipe
│   ├── ir.py                  ← rencana eksekusi + pelipatan konstanta
│   ├── codegen.py             ← penerjemah ke kode Python (pandas)
│   ├── data_loader.py         ← CSV / MySQL → dict[str, DataFrame]
│   ├── runtime.py             ← compile_sql(), run_compiled()  ← fasad
│   ├── verify.py              ← aturan perbandingan hasil
│   ├── cli.py                 ← perintah `sqlc`
│   └── app_streamlit.py       ← antarmuka web
├── examples/                  ← schema.json, mysql_init.sql, *.csv, queries.sql
├── tests/                     ← 636 tes (+ tests/golden/)
└── docs/LAPORAN_TEKNIS.md     ← laporan teknis lengkap
```

| Dokumen | Isi |
|---|---|
| [`SPEC.md`](SPEC.md) | spesifikasi: ruang lingkup, grammar, semantik, IR, strategi uji |
| [`PROMPT.md`](PROMPT.md) | rangkaian prompt pembangunan langkah demi langkah |
| [`PROGRESS.md`](PROGRESS.md) | status tiap langkah, keputusan desain, masalah terbuka, deviasi |
| [`docs/LAPORAN_TEKNIS.md`](docs/LAPORAN_TEKNIS.md) | laporan teknis: arsitektur, EBNF, rancangan tiap fase, hasil uji |

---

## Ringkasan satu halaman

```powershell
# 0. sekali saja: pasang
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 1. setiap kali mulai kerja: nyalakan Docker (3307 + 8081)
docker compose up -d --wait
docker ps

# 2. jalankan antarmuka web
streamlit run src\sqlcompiler\app_streamlit.py --server.headless true
#    lalu buka http://localhost:8501

# 3. atau pakai terminal
sqlc run "SELECT nama, ipk FROM mahasiswa ORDER BY ipk DESC LIMIT 3" --schema examples/schema.json --data examples

# 4. saat selesai
docker compose down
```
