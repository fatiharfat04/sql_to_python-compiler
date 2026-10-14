import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: WHERE ipk > 3.5
    df = df[(df["mahasiswa.ipk"] > 3.5).fillna(False)]
    # SQL: ORDER BY ipk DESC
    df = df.sort_values(by=["mahasiswa.ipk"], ascending=[False], na_position="last", kind="stable")
    # SQL: SELECT nama, ipk
    out = pd.DataFrame({"nama": df["mahasiswa.nama"], "ipk": df["mahasiswa.ipk"]})
    # SQL: LIMIT 5
    out = out.iloc[0:5]
    return out.reset_index(drop=True)
