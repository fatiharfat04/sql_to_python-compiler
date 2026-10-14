import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: WHERE nama NOT LIKE 'A%'
    df = df[(~df["mahasiswa.nama"].str.match(r"(?is)\AA.*\Z")).fillna(False)]
    # SQL: ORDER BY nama
    df = df.sort_values(
        by=["mahasiswa.nama"],
        ascending=[True],
        na_position="first",
        kind="stable",
        key=lambda s: s.str.lower(),
    )
    # SQL: SELECT nama
    out = pd.DataFrame({"nama": df["mahasiswa.nama"]})
    return out.reset_index(drop=True)
