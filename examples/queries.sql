-- Contoh query untuk dijalankan lewat CLI/UI.
SELECT nama, ipk FROM mahasiswa WHERE ipk > 3.5 ORDER BY ipk DESC LIMIT 5;

SELECT DISTINCT jurusan FROM mahasiswa ORDER BY jurusan;

SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata
FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim
GROUP BY m.jurusan
HAVING COUNT(*) > 2
ORDER BY rata DESC;

SELECT nim, nama FROM mahasiswa
WHERE nama LIKE 'A%' AND jurusan IN ('TI', 'SI')
  AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL;

SELECT m.nama FROM mahasiswa m LEFT JOIN nilai n ON m.nim = n.nim WHERE n.nim IS NULL;

SELECT sks, sks / 3 AS bagi, sks DIV 3 AS bulat, sks % 3 AS sisa FROM mata_kuliah ORDER BY sks;