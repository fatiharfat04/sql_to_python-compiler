import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa m
    m = tables["mahasiswa"].add_prefix("m.")
    n = tables["nilai"].add_prefix("n.")
    # SQL: INNER JOIN nilai n ON m.nim = n.nim
    df = m.dropna(subset=["m.nim"]).merge(
        n.dropna(subset=["n.nim"]),
        how="inner",
        left_on=["m.nim"],
        right_on=["n.nim"],
    )
    # SQL: GROUP BY m.jurusan
    df = df.assign(__one=1, __k0=df["m.jurusan"].str.lower())
    df = df.groupby(["__k0"], dropna=False, sort=False).agg(
        **{
            "m.jurusan": ("m.jurusan", "first"),
        },
        __agg0=("__one", "count"),
        __agg1=("n.skor", "mean"),
    ).reset_index(drop=True)
    # SQL: HAVING COUNT(*) > 2
    df = df[(df["__agg0"] > 2).fillna(False)]
    # SQL: ORDER BY rata DESC
    df = df.sort_values(by=["__agg1"], ascending=[False], na_position="last", kind="stable")
    # SQL: SELECT m.jurusan, COUNT(*) AS jumlah, AVG(n.skor) AS rata
    out = pd.DataFrame({"jurusan": df["m.jurusan"], "jumlah": df["__agg0"], "rata": df["__agg1"]})
    return out.reset_index(drop=True)
