import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa m
    m = tables["mahasiswa"].add_prefix("m.")
    n = tables["nilai"].add_prefix("n.")
    # SQL: LEFT JOIN nilai n ON m.nim = n.nim
    df = m.merge(n.dropna(subset=["n.nim"]), how="left", left_on=["m.nim"], right_on=["n.nim"])
    # SQL: WHERE m.ipk > 3.5
    df = df[(df["m.ipk"] > 3.5).fillna(False)]
    # SQL: ORDER BY m.nama
    df = df.sort_values(
        by=["m.nama"],
        ascending=[True],
        na_position="first",
        kind="stable",
        key=lambda s: s.str.lower(),
    )
    # SQL: SELECT m.nama
    out = pd.DataFrame({"nama": df["m.nama"]})
    return out.reset_index(drop=True)
