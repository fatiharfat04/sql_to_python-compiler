import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: WHERE ((nama LIKE 'A%' AND jurusan IN ('TI', 'SI')) AND ipk BETWEEN 3.0 AND 4.0) AND ipk
    #      IS NOT NULL
    mask = (
        (df["mahasiswa.nama"].str.match(r"(?is)\AA.*\Z"))
        & (
            (df["mahasiswa.jurusan"].str.lower() == "ti")
            | (df["mahasiswa.jurusan"].str.lower() == "si")
        ).astype('boolean')
        & ((df["mahasiswa.ipk"] >= 3.0) & (df["mahasiswa.ipk"] <= 4.0))
        & (df["mahasiswa.ipk"].notna())
    )
    df = df[mask.fillna(False)]
    # SQL: SELECT nim, nama
    out = pd.DataFrame({"nim": df["mahasiswa.nim"], "nama": df["mahasiswa.nama"]})
    return out.reset_index(drop=True)
