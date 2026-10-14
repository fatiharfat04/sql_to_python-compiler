SELECT nim, nama FROM mahasiswa
WHERE nama LIKE 'A%' AND jurusan IN ('TI', 'SI')
  AND ipk BETWEEN 3.0 AND 4.0 AND ipk IS NOT NULL
