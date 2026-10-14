SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim WHERE m.ipk > 3.5 ORDER BY m.nama
