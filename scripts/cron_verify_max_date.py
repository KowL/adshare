from adshare.historical.warehouse import get_warehouse
from adshare.core.config import get_settings
settings = get_settings()
wh = get_warehouse(settings)
wh.refresh_views()
row = wh.connection.execute("SELECT MAX(date) FROM v_kline_day").fetchone()
print(f"warehouse max date: {row[0]}")
