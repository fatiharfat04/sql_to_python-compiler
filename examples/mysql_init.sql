-- Sumber kebenaran data uji untuk proyek sqlcompiler.
-- MySQL 8.4, charset utf8mb4, collation utf8mb4_0900_ai_ci.
-- Tanpa PRIMARY KEY / FOREIGN KEY / UNIQUE agar seluruh baris bebas NULL.
-- Seluruh teks berupa karakter ASCII. Varian huruf besar/kecil HANYA pada kolom `kota`.

CREATE TABLE mahasiswa (
    nim     BIGINT,
    nama    VARCHAR(100),
    jurusan VARCHAR(100),
    ipk     DOUBLE,
    kota    VARCHAR(100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE mata_kuliah (
    kode    VARCHAR(100),
    nama_mk VARCHAR(100),
    sks     BIGINT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE nilai (
    nim  BIGINT,
    kode VARCHAR(100),
    skor DOUBLE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO mahasiswa (nim, nama, jurusan, ipk, kota) VALUES
    (22001, 'Andi',    'TI',   3.5,    'Jakarta'),
    (22002, 'Bagus',   'SI',   3.2,    'jakarta'),
    (22003, 'Cahya',   'MI',   NULL,   'Bandung'),
    (22004, 'Dita',    'TI',   3.8,    'Jakarta'),
    (22005, 'Eka',     'SI',   2.9,    NULL),
    (22006, 'Fiona',   'TI',   NULL,   'Bandung'),
    (22007, 'asep',    'MI',   3.1,    'jakarta'),
    (22008, 'Ahmad',   'TI',   3.9,    'Jakarta'),
    (22009, 'Bella',   NULL,   3.4,    'Bandung'),
    (22010, 'Citra',   'SI',   NULL,   'jakarta'),
    (22011, 'Dimas',   'MI',   2.5,    'Jakarta'),
    (22012, 'ani',     'TI',   3.6,    'Bandung'),
    (22013, 'agus',    'SI',   3.3,    NULL),
    (22014, 'Endang',  NULL,   NULL,   'Jakarta');

INSERT INTO mata_kuliah (kode, nama_mk, sks) VALUES
    ('MK001', 'Algoritma',      3),
    ('MK002', 'Basis Data',     4),
    ('MK003', 'Jaringan',       2),
    ('MK004', 'Pemrograman',    3),
    ('MK005', 'Statistika',     4),
    ('MK006', 'Bahasa Inggris', 2);

-- Seluruh baris mahasiswa ber-jurusan 'MI' (22003, 22007, 22011) memiliki skor NULL,
-- sehingga grup 'MI' adalah grup yang seluruh skor-nya NULL.
INSERT INTO nilai (nim, kode, skor) VALUES
    (22001, 'MK001', 85),
    (22001, 'MK002', 90),
    (22001, 'MK003', 78),
    (22001, 'MK004', 88),
    (22002, 'MK001', 76),
    (22002, 'MK002', NULL),
    (22002, 'MK003', 81),
    (22003, 'MK001', NULL),
    (22003, 'MK004', NULL),
    (22004, 'MK001', 92),
    (22004, 'MK002', 87),
    (22004, 'MK005', 79),
    (22005, 'MK002', 68),
    (22005, 'MK003', NULL),
    (22006, 'MK001', 71),
    (22006, 'MK005', NULL),
    (22006, 'MK006', 74),
    (22007, 'MK002', NULL),
    (22007, 'MK005', NULL),
    (22008, 'MK001', 95),
    (22008, 'MK002', 93),
    (22008, 'MK003', 89),
    (22008, 'MK004', 91),
    (22009, 'MK005', 82),
    (22010, 'MK006', NULL),
    (22011, 'MK003', NULL),
    (22011, 'MK006', NULL),
    (NULL,  'MK001', 60),
    (NULL,  'MK002', NULL),
    (22005, 'MK006', 77);
