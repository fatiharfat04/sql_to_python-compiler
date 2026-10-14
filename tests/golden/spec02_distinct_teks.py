import pandas as pd


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mahasiswa
    df = tables["mahasiswa"].add_prefix("mahasiswa.")
    # SQL: ORDER BY jurusan
    df = df.sort_values(
        by=["mahasiswa.jurusan"],
        ascending=[True],
        na_position="first",
        kind="stable",
        key=lambda s: s.str.lower(),
    )
    # SQL: SELECT jurusan
    out = pd.DataFrame({"jurusan": df["mahasiswa.jurusan"]})
    # SQL: DISTINCT
    out = out[~out["jurusan"].str.lower().duplicated()]
    return out.reset_index(drop=True)
