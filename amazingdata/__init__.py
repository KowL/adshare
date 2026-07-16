"""AmazingData subsystem for adshare.

Two cooperating services share one Linux/amd64 host (AmazingData SDK
requirement):

* :mod:`amazingdata.realtime` — 盘中模式: realtime subscription -> Redis
* :mod:`amazingdata.batch`    — 盘后模式: APScheduler -> L3 warehouse (Parquet/DuckDB)

Both depend on the same :mod:`amazingdata.adapters` SDK wrapper.
"""

__all__ = ["adapters", "realtime", "batch"]
