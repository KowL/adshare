"""Verify K-line data quality inside the worker container."""

import duckdb
from pathlib import Path

root = Path("/app/data").resolve()
con = duckdb.connect(":memory:")

for p, v in [("daily", "v_kline_day"), ("weekly", "v_kline_week"), ("monthly", "v_kline_month")]:
    con.execute(f"""
        CREATE VIEW {v} AS
        SELECT
            regexp_extract(filename, '.*[\\\\/]([^\\\\/]+)\\.parquet$', 1) AS code,
            date, open, high, low, close, volume, amount, adj_factor, is_suspended, sync_at
        FROM read_parquet('{root}/A_share/{p}/*.parquet', filename=1)
    """)

print("=== 最终数据质量检查 ===")
for v, n in [("v_kline_day", "daily"), ("v_kline_week", "weekly"), ("v_kline_month", "monthly")]:
    bad = con.execute(
        f"SELECT COUNT(*) FROM {v} WHERE (close<=0 OR low<=0 OR high<=0 OR open<=0) AND NOT is_suspended"
    ).fetchone()[0]
    dup = con.execute(
        f"SELECT COUNT(*) FROM (SELECT code, date FROM {v} GROUP BY code, date HAVING COUNT(*)>1)"
    ).fetchone()[0]
    mx = con.execute(f"SELECT MAX(date) FROM {v}").fetchone()[0]
    codes = con.execute(f"SELECT COUNT(DISTINCT code) FROM {v}").fetchone()[0]
    rows = con.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
    print(f"{n}: codes={codes}, rows={rows}, max_date={mx}, bad_rows={bad}, duplicates={dup}")

print("\n=== 周K/月K最新日期覆盖 ===")
for v, n in [("v_kline_week", "weekly"), ("v_kline_month", "monthly")]:
    mx = con.execute(f"SELECT MAX(date) FROM {v}").fetchone()[0]
    has = con.execute(f"SELECT COUNT(DISTINCT code) FROM {v} WHERE date = {mx}").fetchone()[0]
    total = con.execute(f"SELECT COUNT(DISTINCT code) FROM {v}").fetchone()[0]
    print(f"{n}: max_date={mx}, codes_with_max_date={has}/{total}")

print("\n=== 月K六月条检查 ===")
multi_jun = con.execute("""
    SELECT COUNT(*) FROM (
        SELECT code, COUNT(*) as cnt FROM v_kline_month
        WHERE date >= 20260601 AND date <= 20260630
        GROUP BY code HAVING cnt > 1
    )
""").fetchone()[0]
print(f"multi-june codes: {multi_jun}")
