import pandas as pd


def _idiv(a, b):
    """DIV ala MySQL: hasil dipotong ke arah nol; NULL/pembagi 0 -> NULL."""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.Series([_idiv(x, y) for x, y in zip(a, b)], index=a.index, dtype="Int64")
    if isinstance(a, pd.Series):
        return pd.Series([_idiv(x, b) for x in a], index=a.index, dtype="Int64")
    if isinstance(b, pd.Series):
        return pd.Series([_idiv(a, y) for y in b], index=b.index, dtype="Int64")
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _imod(a, b):
    """Sisa bagi ala MySQL: tandanya mengikuti pembilang; pembagi 0 -> NULL."""
    if isinstance(a, pd.Series) and isinstance(b, pd.Series):
        return pd.Series([_imod(x, y) for x, y in zip(a, b)], index=a.index, dtype="Int64")
    if isinstance(a, pd.Series):
        return pd.Series([_imod(x, b) for x in a], index=a.index, dtype="Int64")
    if isinstance(b, pd.Series):
        return pd.Series([_imod(a, y) for y in b], index=b.index, dtype="Int64")
    if pd.isna(a) or pd.isna(b) or b == 0:
        return None
    r = abs(a) % abs(b)
    return r if a >= 0 else -r


def _rdec(s, scale=4):
    """Pembulatan setengah ke luar (away from zero) ala DECIMAL MySQL."""
    factor = 10 ** scale
    offset = (s >= 0).astype("Float64") * 2 - 1
    return (s * factor + offset * 0.5).astype("Int64") / factor


def run(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    # SQL: FROM mata_kuliah
    df = tables["mata_kuliah"].add_prefix("mata_kuliah.")
    # SQL: ORDER BY sks
    df = df.sort_values(
        by=["mata_kuliah.sks"],
        ascending=[True],
        na_position="first",
        kind="stable",
    )
    # SQL: SELECT sks, sks / 3 AS bagi, sks DIV 3 AS bulat, sks % 3 AS sisa
    out = pd.DataFrame({
        "sks": df["mata_kuliah.sks"],
        "bagi": _rdec((df["mata_kuliah.sks"] / (3)), 4),
        "bulat": _idiv(df["mata_kuliah.sks"], 3),
        "sisa": _imod(df["mata_kuliah.sks"], 3),
    })
    return out.reset_index(drop=True)
