import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: WHERE jurusan NOT IN ('TI', 'SI')
    mask = (
        ~(
            (df["mahasiswa.jurusan"].str.lower() == "ti")
            | (df["mahasiswa.jurusan"].str.lower() == "si")
        )
    )
    df = df[mask.fillna(False)]
    # SQL: SELECT nama
    out = pd.DataFrame({"nama": df["mahasiswa.nama"]})
    return out.reset_index(drop=True)
