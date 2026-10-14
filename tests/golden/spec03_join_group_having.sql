SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata
FROM mahasiswa m INNER JOIN nilai n ON m.nim = n.nim
GROUP BY m.jurusan
HAVING COUNT(*) > 2
ORDER BY rata DESC
