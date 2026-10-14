import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: SELECT COUNT(*) AS jumlah, SUM(ipk) AS total, AVG(ipk) AS rata, MIN(ipk) AS terkecil,
    #      MAX(ipk) AS terbesar
    df = pd.DataFrame({
        "__agg0": pd.array([len(df)], dtype="Int64"),
        "__agg1": pd.array([df["mahasiswa.ipk"].sum(min_count=1)], dtype="Float64"),
        "__agg2": pd.array([df["mahasiswa.ipk"].mean()], dtype="Float64"),
        "__agg3": pd.array([df["mahasiswa.ipk"].min()], dtype="Float64"),
        "__agg4": pd.array([df["mahasiswa.ipk"].max()], dtype="Float64"),
    })
    out = pd.DataFrame({
        "jumlah": df["__agg0"],
        "total": df["__agg1"],
        "rata": df["__agg2"],
        "terkecil": df["__agg3"],
        "terbesar": df["__agg4"],
    })
    return out.reset_index(drop=True)
