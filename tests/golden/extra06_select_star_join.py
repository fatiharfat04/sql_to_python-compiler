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
    # SQL: ORDER BY m.nim, n.kode
    df = df.sort_values(
        by=["n.kode"],
        ascending=[True],
        na_position="first",
        kind="stable",
        key=lambda s: s.str.lower(),
    )
    df = df.sort_values(by=["m.nim"], ascending=[True], na_position="first", kind="stable")
    # SQL: SELECT *
    out = pd.concat([
        pd.DataFrame({"nim": df["m.nim"]}),
        pd.DataFrame({"nama": df["m.nama"]}),
        pd.DataFrame({"jurusan": df["m.jurusan"]}),
        pd.DataFrame({"ipk": df["m.ipk"]}),
        pd.DataFrame({"kota": df["m.kota"]}),
        pd.DataFrame({"nim": df["n.nim"]}),
        pd.DataFrame({"kode": df["n.kode"]}),
        pd.DataFrame({"skor": df["n.skor"]}),
    ], axis=1)
    return out.reset_index(drop=True)
