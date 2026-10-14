import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: ORDER BY nim
    df = df.sort_values(by=["mahasiswa.nim"], ascending=[True], na_position="first", kind="stable")
    # SQL: SELECT nim, nama
    out = pd.DataFrame({"nim": df["mahasiswa.nim"], "nama": df["mahasiswa.nama"]})
    # SQL: LIMIT 3 OFFSET 2
    out = out.iloc[2:5]
    return out.reset_index(drop=True)
