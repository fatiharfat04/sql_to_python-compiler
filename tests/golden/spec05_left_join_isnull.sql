SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim WHERE n.nim IS NULL
