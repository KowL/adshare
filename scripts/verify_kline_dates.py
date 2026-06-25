"""Check latest weekly/monthly kline date distribution."""
import duckdb
from pathlib import Path

root = Path("/app/data").resolve()
con = duckdb.connect(":memory:")

for p, v in [("weekly", "v_kline_week"), ("monthly", "v_kline_month")]:
    con.execute(f"""
        CREATE VIEW {v} AS
        SELECT
            regexp_extract(filename, '.*[\\\\/]([^\\\\/]+)\\.parquet$', 1) AS code,
            date
        FROM read_parquet('{root}/A_share/{p}/*.parquet', filename=1)
    """)

print("=== weekly latest dates ===")
print(con.execute("""
    SELECT date, COUNT(DISTINCT code) as cnt FROM v_kline_week
    GROUP BY date ORDER BY date DESC LIMIT 10
""").fetchdf().to_string(index=False))

print("\n=== monthly latest dates ===")
print(con.execute("""
    SELECT date, COUNT(DISTINCT code) as cnt FROM v_kline_month
    GROUP BY date ORDER BY date DESC LIMIT 10
""").fetchdf().to_string(index=False))
